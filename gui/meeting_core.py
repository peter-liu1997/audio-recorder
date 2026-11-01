"""Meeting folders, recorder commands, and evidence-based AI summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib import error as urlerror
from urllib import request
from urllib.parse import urlparse


DEFAULT_RECORDINGS = Path.home() / "Recordings" / "Audio Recorder"
SUMMARY_CHARS = 14000


class MeetingError(Exception):
    pass


@dataclass(frozen=True)
class MeetingSettings:
    title: str
    root: Path
    source: str = "both"
    app: str = ""
    mic_device: str = ""
    asr_backend: str = "local-gguf"
    asr_model: str = "qwen3-asr-0.6b"
    asr_model_path: str = ""
    asr_api_url: str = ""
    asr_language: str = "zh"
    chunk_seconds: float = 5.0
    summary_backend: str = "ollama"
    summary_model: str = "qwen3:8b"
    summary_url: str = "http://127.0.0.1:11434/api/chat"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(title: str) -> str:
    clean = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title.strip(), flags=re.UNICODE).strip("-_ ")
    return clean[:48] or "会议"


def create_meeting(settings: MeetingSettings) -> dict:
    if not settings.title.strip():
        raise MeetingError("请填写会议名称")
    root = settings.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = f"{stamp}_{safe_name(settings.title)}"
    folder = root / name
    counter = 2
    while folder.exists():
        folder = root / f"{name}_{counter}"
        counter += 1
    folder.mkdir(mode=0o700)
    item = {
        "schemaVersion": 1,
        "id": folder.name,
        "title": settings.title.strip(),
        "createdAt": utc_now(),
        "state": "starting",
        "folder": str(folder),
        "audio": str(folder / "audio.wav"),
        "transcript": str(folder / "transcript.txt"),
        "events": str(folder / "audio.live.jsonl"),
        "summary": None,
        "source": settings.source,
        "app": settings.app or None,
        "asrBackend": settings.asr_backend,
        "asrModel": settings.asr_model,
        "summaryBackend": settings.summary_backend,
        "summaryModel": settings.summary_model,
    }
    save_meeting(item)
    return item


def save_meeting(item: dict) -> None:
    path = Path(item["folder"]) / "meeting.json"
    temporary = path.with_name(".meeting.json.tmp")
    temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def list_meetings(root: Path) -> list[dict]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []
    meetings = []
    for path in root.glob("*/meeting.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("folder") == str(path.parent):
                meetings.append(item)
        except (OSError, ValueError):
            continue
    return sorted(meetings, key=lambda item: item.get("createdAt", ""), reverse=True)


def live_command(settings: MeetingSettings, meeting: dict, cli: Path) -> list[str]:
    if settings.source not in ("mic", "system", "both"):
        raise MeetingError("录音来源无效")
    if settings.asr_backend not in ("local", "local-gguf", "qwen"):
        raise MeetingError("转写模型类型无效")
    if settings.app and settings.source == "mic":
        raise MeetingError("按应用录音需要包含系统声音")
    if settings.mic_device and settings.source == "system":
        raise MeetingError("指定麦克风需要包含麦克风声音")
    if not 1 <= settings.chunk_seconds <= 25:
        raise MeetingError("转写间隔需要在 1～25 秒之间")
    if settings.asr_backend == "local-gguf" and not settings.asr_model_path:
        raise MeetingError("本地 Qwen3-ASR / SenseVoice 需要选择模型文件")
    command = [str(cli), "live", "--source", settings.source,
               "--output", meeting["audio"], "--transcript", meeting["transcript"],
               "--backend", settings.asr_backend, "--model", settings.asr_model,
               "--language", settings.asr_language,
               "--chunk-seconds", str(settings.chunk_seconds), "--json"]
    if settings.app:
        command += ["--app", settings.app]
    if settings.mic_device:
        command += ["--mic-device", settings.mic_device]
    if settings.asr_model_path:
        command += ["--model-path", settings.asr_model_path]
    if settings.asr_backend == "qwen" and settings.asr_api_url:
        command += ["--api-url", settings.asr_api_url]
    return command


def summary_chunks(transcript: str, limit: int = SUMMARY_CHARS) -> list[str]:
    if not transcript.strip():
        raise MeetingError("转写文本为空，无法生成纪要")
    if limit < 100:
        raise MeetingError("摘要分块上限太小")
    chunks = []
    current = ""
    for line in transcript.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


SYSTEM_PROMPT = (
    "你是严谨的中文会议纪要助手。只能根据提供的转写文本作答，不补造事实。"
    "保留重要数字、专有名词和时间；无法确认的内容写‘待确认’。"
    "麦克风/系统音轨标签只表示声源，不等同于说话人身份。"
    "行动项只有在转写明确出现负责人或期限时才填写，否则写‘未提及’。"
)


def validate_summary_endpoint(backend: str, url: str) -> str:
    parsed = urlparse(url)
    if backend == "ollama":
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") \
                or parsed.path != "/api/chat":
            raise MeetingError("本地 Ollama 地址必须是 localhost/127.0.0.1 的 /api/chat")
    elif backend == "qwen":
        if parsed.scheme != "https" or not parsed.netloc or not parsed.path.endswith("/chat/completions"):
            raise MeetingError("线上纪要地址必须是 HTTPS /chat/completions")
    else:
        raise MeetingError("纪要模型类型无效")
    return url


def call_summary_model(backend: str, model: str, url: str, system: str,
                       user: str, opener=request.urlopen) -> str:
    validate_summary_endpoint(backend, url)
    if not model.strip():
        raise MeetingError("请填写纪要模型名称")
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}], "stream": False}
    headers = {"Content-Type": "application/json"}
    if backend == "qwen":
        key = os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            raise MeetingError("线上纪要需要环境变量 DASHSCOPE_API_KEY")
        headers["Authorization"] = f"Bearer {key}"
    incoming = request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                               headers=headers, method="POST")
    try:
        with opener(incoming, timeout=180) as response:
            payload = json.load(response)
    except urlerror.HTTPError as exc:
        raise MeetingError(f"纪要模型返回 HTTP {exc.code}") from exc
    except (urlerror.URLError, TimeoutError) as exc:
        raise MeetingError(f"纪要模型连接失败：{exc}") from exc
    try:
        content = (payload["message"]["content"] if backend == "ollama" else
                   payload["choices"][0]["message"]["content"])
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty content")
        return content.strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MeetingError("纪要模型没有返回有效文本") from exc


def generate_summary(title: str, transcript: str, backend: str, model: str,
                     url: str, opener=request.urlopen) -> str:
    parts = summary_chunks(transcript)
    if len(parts) == 1:
        source = parts[0]
    else:
        notes = []
        for index, part in enumerate(parts, 1):
            note = call_summary_model(
                backend, model, url, SYSTEM_PROMPT,
                f"会议《{title}》的第 {index}/{len(parts)} 段转写。只提取可核对的事实、决定、行动项和未决问题，"
                f"尽量保留时间戳，控制在 1200 字以内：\n\n{part}", opener)
            notes.append(f"[第 {index} 段]\n{note}")
        source = "\n\n".join(notes)
        rounds = 0
        while len(source) > SUMMARY_CHARS:
            rounds += 1
            if rounds > 4:
                raise MeetingError("会议过长，分段摘要仍超出模型上下文，请拆分会议后重试")
            reduced = []
            for index, part in enumerate(summary_chunks(source), 1):
                reduced.append(call_summary_model(
                    backend, model, url, SYSTEM_PROMPT,
                    f"压缩第 {index} 段会议事实，保留决定、负责人、期限、重要数字和时间戳，"
                    f"控制在 800 字以内：\n\n{part}", opener))
            next_source = "\n\n".join(reduced)
            if len(next_source) >= len(source):
                raise MeetingError("模型未能压缩长会议摘要，请拆分会议后重试")
            source = next_source
    result = call_summary_model(
        backend, model, url, SYSTEM_PROMPT,
        f"请根据以下会议《{title}》转写/分段事实，形成中文会议纪要。使用这些标题："
        "## 会议主题、## 核心结论、## 关键讨论、## 决定、## 行动项、## 待确认问题。"
        "不要把推测写成决定。对每项决定和行动项尽可能引用已有的时间戳；没有依据时写‘未提及’。"
        f"\n\n{source}", opener)
    return result


def save_summary(meeting: dict, body: str, backend: str, model: str) -> Path:
    folder = Path(meeting["folder"])
    target = folder / "summary.md"
    number = 2
    while target.exists():
        target = folder / f"summary-v{number}.md"
        number += 1
    header = (f"# {meeting['title']} · AI 纪要\n\n"
              f"> 草稿；请以原始转写和录音核对。生成时间：{utc_now()}；模型：{backend}/{model}。\n\n")
    with target.open("x", encoding="utf-8") as output:
        output.write(header + body.strip() + "\n")
    meeting["summary"] = str(target)
    meeting["summaryBackend"] = backend
    meeting["summaryModel"] = model
    meeting["summaryUploaded"] = backend == "qwen"
    meeting["state"] = "done"
    save_meeting(meeting)
    return target
