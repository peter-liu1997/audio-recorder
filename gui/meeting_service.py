#!/usr/bin/env python3
"""Meeting lifecycle shared by the native desktop GUI."""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading

from meeting_core import (MeetingError, MeetingSettings,
                          create_meeting, generate_summary, list_meetings,
                          live_command, save_meeting, save_summary,
                          validate_summary_endpoint)


def find_recorder() -> Path | None:
    override = os.environ.get("AUDIO_RECORDER_CLI")
    if override:
        candidate = Path(override).expanduser().resolve()
        return candidate if candidate.is_file() else None
    script = Path(__file__).resolve()
    for parent in script.parents:
        if parent.suffix == ".app":
            candidate = parent / "Contents/MacOS/audio-recorder"
            if candidate.is_file():
                return candidate
    repo = script.parents[1]
    candidates = (
        [repo / "dist/AudioRecorder.app/Contents/MacOS/audio-recorder"] if sys.platform == "darwin" else
        [repo / "windows/build/Release/audio-recorder.exe"] if os.name == "nt" else
        [Path.home() / ".local/bin/audio-recorder", repo / "linux/audio-recorder"]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    located = shutil.which("audio-recorder")
    return Path(located) if located else None


def settings_from_json(payload: dict, root: Path) -> MeetingSettings:
    try:
        return MeetingSettings(
            title=str(payload.get("title", "")).strip(), root=root,
            source=str(payload.get("source", "both")),
            app=str(payload.get("app", "")).strip(),
            mic_device=str(payload.get("micDevice", "")).strip(),
            asr_backend=str(payload.get("asrBackend", "local-gguf")),
            asr_model=str(payload.get("asrModel", "qwen3-asr-0.6b")).strip(),
            asr_model_path=str(payload.get("asrModelPath", "")).strip(),
            asr_api_url=str(payload.get("asrApiUrl", "")).strip(),
            asr_language=str(payload.get("language", "zh")),
            chunk_seconds=float(payload.get("chunkSeconds", 5)),
            summary_backend=str(payload.get("summaryBackend", "ollama")),
            summary_model=str(payload.get("summaryModel", "qwen3:8b")).strip(),
            summary_url=str(payload.get("summaryUrl", "http://127.0.0.1:11434/api/chat")).strip(),
        )
    except (TypeError, ValueError) as exc:
        raise MeetingError("会议配置格式错误") from exc


class MeetingService:
    def __init__(self, root: Path, cli: Path | None = None):
        self.root = root.expanduser().resolve()
        self.cli = cli or find_recorder()
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.events: deque[dict] = deque(maxlen=1000)
        self.sequence = 0
        self.current: dict | None = None
        self.settings: MeetingSettings | None = None
        self.process: subprocess.Popen | None = None
        self.summary_jobs: set[str] = set()
        self.stop_sent = False

    def publish(self, event: dict) -> None:
        with self.condition:
            self.sequence += 1
            self.events.append({"seq": self.sequence, **event})
            self.condition.notify_all()

    def events_after(self, cursor: int, timeout: float = 15) -> dict:
        with self.condition:
            if cursor >= self.sequence:
                self.condition.wait(timeout=timeout)
            first = self.events[0]["seq"] if self.events else self.sequence + 1
            return {"cursor": self.sequence, "missed": cursor < first - 1,
                    "events": [item for item in self.events if item["seq"] > cursor]}

    def status(self) -> dict:
        with self.lock:
            return {"root": str(self.root), "cli": str(self.cli) if self.cli else None,
                    "asrUrl": os.environ.get("QWEN_ASR_API_URL", ""),
                    "summaryUrl": os.environ.get("QWEN_SUMMARY_API_URL", os.environ.get("QWEN_ASR_API_URL", "")),
                    "current": self.current.copy() if self.current else None,
                    "recording": self.process is not None,
                    "summaryBusy": bool(self.summary_jobs)}

    def set_root(self, root: str) -> dict:
        with self.lock:
            if self.process is not None:
                raise MeetingError("录音期间不能切换保存目录")
            if not root.strip():
                raise MeetingError("保存目录不能为空")
            target = Path(root).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            self.root = target
            self.current = None
        self.publish({"event": "root", "data": {"root": str(target)}})
        return self.status()

    def _command_json(self, name: str) -> dict:
        if not self.cli:
            raise MeetingError("未找到 audio-recorder CLI，请先构建或设置 AUDIO_RECORDER_CLI")
        try:
            done = subprocess.run([str(self.cli), name, "--json"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=15)
            result = json.loads(done.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise MeetingError(f"查询 {name} 失败：{exc}") from exc
        if not result.get("ok"):
            raise MeetingError(result.get("error", {}).get("message", f"查询 {name} 失败"))
        return result["data"]

    def devices(self) -> list[dict]:
        return self._command_json("devices").get("devices", [])

    def apps(self) -> list[dict]:
        return self._command_json("apps").get("apps", [])

    def meetings(self) -> list[dict]:
        return list_meetings(self.root)

    def meeting(self, identifier: str) -> dict:
        if not identifier or "/" in identifier or "\\" in identifier or identifier in (".", ".."):
            raise MeetingError("会议 ID 无效")
        folder = (self.root / identifier).resolve()
        if folder.parent != self.root:
            raise MeetingError("会议 ID 无效")
        path = folder / "meeting.json"
        if not path.is_file():
            raise MeetingError("会议不存在")
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("folder") != str(folder):
            raise MeetingError("会议路径不匹配")
        for key in ("audio", "transcript", "events", "summary"):
            if item.get(key) and Path(item[key]).resolve().parent != folder:
                raise MeetingError("会议文件路径不匹配")
        return item

    def meeting_detail(self, identifier: str) -> dict:
        item = self.meeting(identifier)
        transcript = Path(item["transcript"])
        summary = Path(item["summary"]) if item.get("summary") else None
        return {"meeting": item,
                "transcript": transcript.read_text(encoding="utf-8") if transcript.is_file() else "",
                "summary": summary.read_text(encoding="utf-8") if summary and summary.is_file() else ""}

    def start(self, payload: dict) -> dict:
        with self.lock:
            if self.process is not None:
                raise MeetingError("已有会议正在录音，请先结束")
            if not self.cli:
                raise MeetingError("未找到 audio-recorder CLI，请先构建或设置 AUDIO_RECORDER_CLI")
            root = Path(str(payload.get("root") or self.root)).expanduser().resolve()
            settings = settings_from_json(payload, root)
            if settings.asr_backend == "local-gguf" and not Path(settings.asr_model_path).expanduser().is_file():
                raise MeetingError("请选择已下载的 Qwen3-ASR / SenseVoice 模型文件")
            if settings.summary_backend not in ("none", "ollama", "qwen"):
                raise MeetingError("纪要模型类型无效")
            if settings.summary_backend != "none":
                validate_summary_endpoint(settings.summary_backend, settings.summary_url)
            live_command(settings, {"audio": "audio.wav", "transcript": "transcript.txt"}, self.cli)
            self.root = root
            item = create_meeting(settings)
            command = live_command(settings, item, self.cli)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding="utf-8", errors="replace", bufsize=1,
                                           creationflags=flags, start_new_session=os.name != "nt")
            except OSError as exc:
                item["state"] = "failed"
                item["error"] = str(exc)
                save_meeting(item)
                raise MeetingError(f"启动录音失败：{exc}") from exc
            self.current = item
            self.settings = settings
            self.process = process
            self.stop_sent = False
            threading.Thread(target=self._read_stdout, args=(process, item["id"]), daemon=True).start()
            threading.Thread(target=self._read_stderr, args=(process,), daemon=True).start()
        self.publish({"event": "meeting", "data": {"meeting": item}})
        return item

    def _read_stdout(self, process: subprocess.Popen, identifier: str) -> None:
        with process.stdout:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    self.publish({"event": "log", "data": {"message": line.strip()}})
                    continue
                self._on_live_event(identifier, event)
        code = process.wait()
        with self.lock:
            if self.current and self.current["id"] == identifier:
                if self.current["state"] in ("starting", "recording", "stopping"):
                    self.current["state"] = "failed"
                    self.current["error"] = f"录音进程退出，状态码 {code}；请查看事件日志"
                    save_meeting(self.current)
                    self.publish({"event": "error", "data": {"message": self.current["error"]}})
                self.process = None
        self.publish({"event": "processExit", "data": {"code": code, "meetingId": identifier}})

    def _read_stderr(self, process: subprocess.Popen) -> None:
        with process.stderr:
            for line in process.stderr:
                if line.strip():
                    self.publish({"event": "log", "data": {"message": line.strip()[:500]}})

    def _on_live_event(self, identifier: str, event: dict) -> None:
        with self.lock:
            if not self.current or self.current["id"] != identifier:
                return
            kind = event.get("event")
            if kind == "started":
                self.current["state"] = "recording"
                save_meeting(self.current)
            elif kind == "completed":
                self.current["state"] = "recorded"
                self.current["durationSeconds"] = event.get("data", {}).get("durationSeconds")
                save_meeting(self.current)
                if self.settings and self.settings.summary_backend != "none":
                    self.summarize(self.current["id"], self.settings.summary_backend,
                                   self.settings.summary_model, self.settings.summary_url)
                else:
                    self.current["state"] = "done"
                    save_meeting(self.current)
            elif kind == "error":
                self.current["state"] = "failed"
                self.current["error"] = event.get("error", {}).get("message", "录音或转写失败")
                if event.get("recoveryPath"):
                    self.current["recoveryPath"] = event["recoveryPath"]
                save_meeting(self.current)
        self.publish({"event": "live", "data": {"meetingId": identifier, "payload": event}})

    def stop(self) -> dict:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise MeetingError("没有正在录制的会议")
            if self.stop_sent:
                return self.status()
            self.stop_sent = True
            self.current["state"] = "stopping"
            save_meeting(self.current)
            try:
                if os.name == "nt":
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(self.process.pid, signal.SIGINT)
            except OSError as exc:
                self.stop_sent = False
                raise MeetingError(f"请求停止录音失败：{exc}") from exc
        self.publish({"event": "meeting", "data": {"meeting": self.current.copy()}})
        return self.status()

    def summarize(self, identifier: str, backend: str, model: str, url: str) -> dict:
        with self.lock:
            item = self.meeting(identifier)
            if item["state"] in ("starting", "recording", "stopping"):
                raise MeetingError("请在录音结束后生成纪要")
            if identifier in self.summary_jobs:
                raise MeetingError("该会议正在生成纪要")
            validate_summary_endpoint(backend, url)
            self.summary_jobs.add(identifier)
            item["state"] = "summarizing"
            save_meeting(item)
            if self.current and self.current["id"] == identifier:
                self.current = item
            threading.Thread(target=self._summary_worker,
                             args=(item, backend, model, url), daemon=True).start()
        self.publish({"event": "summaryStarted", "data": {"meetingId": identifier}})
        return item

    def _summary_worker(self, item: dict, backend: str, model: str, url: str) -> None:
        try:
            transcript = Path(item["transcript"]).read_text(encoding="utf-8")
            body = generate_summary(item["title"], transcript, backend, model, url)
            target = save_summary(item, body, backend, model)
            self.publish({"event": "summaryCompleted", "data": {"meetingId": item["id"],
                                                               "summary": str(target)}})
        except (MeetingError, OSError, ValueError) as exc:
            item["state"] = "summary_failed"
            item["summaryError"] = str(exc)
            save_meeting(item)
            self.publish({"event": "summaryError", "data": {"meetingId": item["id"],
                                                           "message": str(exc)}})
        finally:
            with self.lock:
                self.summary_jobs.discard(item["id"])
                if self.current and self.current["id"] == item["id"]:
                    self.current = item

    def open_folder(self, identifier: str) -> None:
        folder = Path(self.meeting(identifier)["folder"])
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif os.name == "nt":
            os.startfile(str(folder))
        else:
            subprocess.Popen(["xdg-open", str(folder)])

