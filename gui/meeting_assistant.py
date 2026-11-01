#!/usr/bin/env python3
"""Native Tk desktop front end for Audio Recorder meetings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from meeting_core import DEFAULT_RECORDINGS, MeetingError
from meeting_service import MeetingService


SOURCES = {"麦克风 + 会议声音": "both", "仅麦克风": "mic", "仅会议声音": "system"}
ASR = {"本地 Qwen3-ASR / SenseVoice": "local-gguf", "本地 Whisper": "local", "线上 Qwen ASR": "qwen"}
SUMMARY = {"本地 Ollama": "ollama", "线上 Qwen": "qwen", "暂不生成": "none"}
STATES = {"starting": "启动中", "recording": "录制中", "stopping": "正在保存", "recorded": "已录制",
          "summarizing": "生成纪要中", "done": "已完成", "failed": "失败", "summary_failed": "纪要失败"}
TRACKS = {"microphone": "我方麦克风", "system": "会议声音", "mixed": "混合音轨"}


class MeetingAssistant:
    def __init__(self, window: tk.Tk, service: MeetingService):
        self.window = window
        self.service = service
        self.cursor = 0
        self.selected_id: str | None = None
        self.current: dict | None = None
        self.app_lookup: dict[str, str] = {}
        self.closing = False
        self.title = tk.StringVar(value="项目会议")
        self.root_path = tk.StringVar(value=str(service.root))
        self.source = tk.StringVar(value=next(iter(SOURCES)))
        self.app_choice = tk.StringVar(value="全部系统声音")
        self.app_manual = tk.StringVar()
        self.mic_choice = tk.StringVar(value="系统默认麦克风")
        self.mic_lookup: dict[str, str] = {}
        self.asr_backend = tk.StringVar(value=next(iter(ASR)))
        self.asr_model = tk.StringVar(value="qwen3-asr-0.6b")
        self.asr_model_path = tk.StringVar()
        self.asr_url = tk.StringVar(value=os.environ.get("QWEN_ASR_API_URL", ""))
        self.language = tk.StringVar(value="zh")
        self.chunk_seconds = tk.StringVar(value="5")
        self.summary_backend = tk.StringVar(value=next(iter(SUMMARY)))
        self.summary_model = tk.StringVar(value="qwen3:8b")
        self.summary_url = tk.StringVar(value="http://127.0.0.1:11434/api/chat")
        self.message = tk.StringVar(value="正在检查本地录音器…")
        self.state_text = tk.StringVar(value="待命")
        self.session_title = tk.StringVar(value="尚未开始会议")
        self.session_path = tk.StringVar(value="选择录音来源和模型，然后开始会议。")
        self.elapsed = tk.StringVar(value="00:00")
        self._style()
        self._build()
        self.refresh_history()
        self.refresh_sources()
        self.message.set("已连接本地录音器" if service.cli else "未找到 audio-recorder CLI")
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
        self.window.after(200, self._poll)
        self.window.after(1000, self._clock)

    def _style(self):
        self.window.title("AI 会议助手 · Audio Recorder")
        self.window.geometry("1280x830")
        self.window.minsize(900, 620)
        self.window.configure(bg="#edf3f6")
        style = ttk.Style(self.window)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("App.TFrame", background="#edf3f6")
        style.configure("Sidebar.TFrame", background="#142d43")
        style.configure("Sidebar.TLabel", background="#142d43", foreground="#d7e7ef", font=("Helvetica", 10))
        style.configure("Brand.TLabel", background="#142d43", foreground="#ffffff", font=("Helvetica", 17, "bold"))
        style.configure("Section.TLabel", background="#142d43", foreground="#57ceb9", font=("Helvetica", 10, "bold"))
        style.configure("Title.TLabel", background="#edf3f6", foreground="#18364a", font=("Helvetica", 21, "bold"))
        style.configure("Body.TLabel", background="#edf3f6", foreground="#637d8d", font=("Helvetica", 10))
        style.configure("State.TLabel", background="#ffffff", foreground="#1e695d", font=("Helvetica", 11, "bold"))
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabel", background="#ffffff", foreground="#25465c", font=("Helvetica", 11))
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#193f52", font=("Helvetica", 12, "bold"))
        style.configure("Primary.TButton", padding=(13, 9), background="#41bda9", foreground="#092c31", font=("Helvetica", 11, "bold"))
        style.map("Primary.TButton", background=[("active", "#67d7c5")])
        style.configure("Stop.TButton", padding=(12, 9), background="#e1e9ec", foreground="#365464")
        style.configure("TButton", padding=(9, 6))
        style.configure("TEntry", padding=5)
        style.configure("TCombobox", padding=4)
        style.configure("Treeview", rowheight=27, font=("Helvetica", 10))
        style.configure("Treeview.Heading", font=("Helvetica", 10, "bold"))

    def _build(self):
        shell = ttk.Frame(self.window, style="App.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(shell, width=350, style="Sidebar.TFrame")
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.rowconfigure(1, weight=1)
        ttk.Label(sidebar, text="◉  Audio Recorder\n     AI 会议助手", style="Brand.TLabel").grid(
            row=0, column=0, sticky="ew", padx=24, pady=(23, 18))
        tabs = ttk.Notebook(sidebar)
        tabs.grid(row=1, column=0, sticky="nsew")
        meeting_tab = ttk.Frame(tabs, style="Sidebar.TFrame")
        audio_tab = ttk.Frame(tabs, style="Sidebar.TFrame")
        asr_tab = ttk.Frame(tabs, style="Sidebar.TFrame")
        summary_tab = ttk.Frame(tabs, style="Sidebar.TFrame")
        for tab, title in [(meeting_tab, "会议"), (audio_tab, "录音"),
                           (asr_tab, "转写"), (summary_tab, "纪要")]:
            tabs.add(tab, text=title)

        self._section(meeting_tab, "01  新会议")
        self._field(meeting_tab, "会议名称", self.title)
        self._field(meeting_tab, "保存目录", self.root_path, browse=lambda: self._choose_dir(self.root_path))
        ttk.Label(meeting_tab, text="开始前请在其他标签页选择声音来源和模型。",
                  style="Sidebar.TLabel", wraplength=290).pack(anchor="w", padx=23, pady=(14, 0))
        self._section(audio_tab, "02  录音")
        self._select(audio_tab, "声音来源", self.source, list(SOURCES))
        self._select(audio_tab, "会议应用", self.app_choice, ["全部系统声音"])
        self.app_combo = self.last_combo
        self._field(audio_tab, "或输入应用名、PID、播放流 ID", self.app_manual)
        self._select(audio_tab, "麦克风", self.mic_choice, ["系统默认麦克风"])
        self.mic_combo = self.last_combo
        ttk.Button(audio_tab, text="刷新设备与应用", command=self.refresh_sources).pack(fill="x", padx=23, pady=(8, 0))
        self._section(asr_tab, "03  实时转写")
        self._select(asr_tab, "转写后端", self.asr_backend, list(ASR), self._asr_changed)
        self._field(asr_tab, "模型名称", self.asr_model)
        self._field(asr_tab, "本地模型文件", self.asr_model_path, browse=lambda: self._choose_file(self.asr_model_path))
        self._field(asr_tab, "线上 ASR 地址", self.asr_url)
        self._field(asr_tab, "语言", self.language)
        self._field(asr_tab, "转写分块时长（秒）", self.chunk_seconds)
        self._section(summary_tab, "04  AI 纪要")
        self._select(summary_tab, "生成方式", self.summary_backend, list(SUMMARY), self._summary_changed)
        self._field(summary_tab, "模型名称", self.summary_model)
        self._field(summary_tab, "模型地址", self.summary_url)
        ttk.Label(summary_tab, text="本地数据默认留在电脑；选择线上模型后才发送音频或文字。",
                  style="Sidebar.TLabel", wraplength=290).pack(anchor="w", padx=23, pady=(12, 23))

        buttons = ttk.Frame(sidebar, style="Sidebar.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", padx=18, pady=16)
        buttons.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(buttons, text="●  开始会议", style="Primary.TButton", command=self.on_start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_button = ttk.Button(buttons, text="■  结束录音", style="Stop.TButton", command=self.on_stop)
        self.stop_button.grid(row=0, column=1, sticky="ew")

        main = ttk.Frame(shell, style="App.TFrame", padding=(26, 22))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=3)
        main.rowconfigure(5, weight=2)
        ttk.Label(main, text="让每场讨论都留下清晰记录", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="录音、实时转写和可追溯的会后纪要，保存在同一个本地文件夹。",
                  style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 18))
        session = ttk.Frame(main, style="Card.TFrame", padding=16)
        session.grid(row=2, column=0, sticky="new", pady=(0, 16))
        session.columnconfigure(0, weight=1)
        ttk.Label(session, textvariable=self.session_title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(session, textvariable=self.state_text, style="State.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Label(session, textvariable=self.session_path, style="Card.TLabel", wraplength=580).grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Label(session, textvariable=self.elapsed, style="CardTitle.TLabel").grid(row=1, column=1, sticky="e")

        columns = ttk.Frame(main, style="App.TFrame")
        columns.grid(row=3, column=0, sticky="nsew")
        columns.columnconfigure(0, weight=1)
        columns.columnconfigure(1, weight=1)
        columns.rowconfigure(0, weight=1)
        transcript_card = self._card(columns, 0, "实时转写")
        summary_card = self._card(columns, 1, "AI 会议纪要")
        self.transcript = self._text(transcript_card)
        self.summary = self._text(summary_card)
        ttk.Button(summary_card, text="生成 / 重试纪要", command=self.on_summarize).pack(anchor="e", padx=12, pady=(0, 10))

        files = ttk.Frame(main, style="Card.TFrame", padding=12)
        files.grid(row=4, column=0, sticky="ew", pady=(15, 0))
        ttk.Label(files, text="会议文件", style="CardTitle.TLabel").pack(side="left", padx=(0, 12))
        for label, key in [("播放录音", "audio"), ("打开转写", "transcript"),
                           ("打开纪要", "summary"), ("打开目录", "folder")]:
            ttk.Button(files, text=label, command=lambda item=key: self.open_artifact(item)).pack(side="left", padx=3)

        history_card = ttk.Frame(main, style="Card.TFrame", padding=12)
        history_card.grid(row=5, column=0, sticky="nsew", pady=(15, 0))
        history_card.columnconfigure(0, weight=1)
        history_card.rowconfigure(1, weight=1)
        head = ttk.Frame(history_card, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        ttk.Label(head, text="历史会议", style="CardTitle.TLabel").pack(side="left")
        ttk.Button(head, text="刷新列表", command=self.refresh_history).pack(side="right")
        self.history = ttk.Treeview(history_card, columns=("date", "title", "state"), show="headings", height=5)
        for column, title, width in [("date", "时间", 150), ("title", "会议", 260), ("state", "状态", 90)]:
            self.history.heading(column, text=title)
            self.history.column(column, width=width, anchor="w")
        self.history.grid(row=1, column=0, sticky="nsew")
        self.history.bind("<<TreeviewSelect>>", self._history_selected)
        ttk.Label(main, textvariable=self.message, style="Body.TLabel").grid(row=6, column=0, sticky="w", pady=(13, 0))
        self._update_session()

    def _section(self, parent, title):
        ttk.Label(parent, text=title, style="Section.TLabel").pack(anchor="w", padx=23, pady=(24, 8))

    def _field(self, parent, label, variable, browse=None):
        ttk.Label(parent, text=label, style="Sidebar.TLabel").pack(anchor="w", padx=23, pady=(8, 4))
        row = ttk.Frame(parent, style="Sidebar.TFrame")
        row.pack(fill="x", padx=23)
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
        if browse:
            ttk.Button(row, text="选择", command=browse).pack(side="left", padx=(5, 0))

    def _select(self, parent, label, variable, values, changed=None):
        ttk.Label(parent, text=label, style="Sidebar.TLabel").pack(anchor="w", padx=23, pady=(8, 4))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.pack(fill="x", padx=23)
        if changed:
            combo.bind("<<ComboboxSelected>>", lambda _: changed())
        self.last_combo = combo

    def _card(self, parent, column, title):
        card = ttk.Frame(parent, style="Card.TFrame")
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else (8, 0))
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", padx=14, pady=(14, 10))
        return card

    def _text(self, parent):
        text = tk.Text(parent, wrap="word", width=24, height=9, bg="#ffffff", fg="#28485a", relief="flat", font=("Helvetica", 11),
                       padx=14, pady=10, spacing2=4, state="disabled", highlightthickness=0)
        text.pack(fill="both", expand=True, padx=2, pady=(0, 10))
        return text

    def _write_text(self, widget, content, append=False):
        widget.configure(state="normal")
        if not append:
            widget.delete("1.0", "end")
        widget.insert("end", content)
        widget.configure(state="disabled")
        widget.see("end")

    def _choose_dir(self, variable):
        selected = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()))
        if selected:
            variable.set(selected)
            try:
                self.service.set_root(selected)
                self.selected_id = None
                self.refresh_history()
                self._write_text(self.transcript, "")
                self._write_text(self.summary, "")
            except (MeetingError, OSError) as exc:
                self._error(exc)

    def _choose_file(self, variable):
        selected = filedialog.askopenfilename(title="选择本地转写模型")
        if selected:
            variable.set(selected)

    def _asr_changed(self):
        backend = ASR[self.asr_backend.get()]
        self.asr_model.set("qwen3-asr-0.6b" if backend == "local-gguf" else
                           "medium" if backend == "local" else "qwen3-asr-flash")

    def _summary_changed(self):
        backend = SUMMARY[self.summary_backend.get()]
        if backend == "ollama":
            self.summary_model.set("qwen3:8b")
            self.summary_url.set("http://127.0.0.1:11434/api/chat")
        elif backend == "qwen":
            self.summary_model.set("qwen-plus")
            self.summary_url.set(os.environ.get("QWEN_SUMMARY_API_URL", os.environ.get("QWEN_ASR_API_URL", "")))

    def refresh_sources(self):
        self.message.set("正在查找麦克风和会议应用…")
        def work():
            try:
                devices, apps = self.service.devices(), self.service.apps()
                if not self.closing:
                    self.window.after(0, lambda: self._set_sources(devices, apps))
            except (MeetingError, OSError) as exc:
                if not self.closing:
                    self.window.after(0, lambda message=str(exc): self.message.set("来源查询失败：" + message))
        threading.Thread(target=work, daemon=True).start()

    def _set_sources(self, devices, apps):
        self.mic_lookup = {f"{item.get('name') or item.get('id')} · {item.get('id')}": str(item['id'])
                           for item in devices if item.get("id") is not None}
        self.app_lookup = {f"{item.get('name') or '应用'} · {item.get('id', item.get('pid'))}":
                           str(item.get("id", item.get("pid"))) for item in apps
                           if item.get("id", item.get("pid")) is not None}
        self.mic_combo.configure(values=["系统默认麦克风", *self.mic_lookup])
        self.app_combo.configure(values=["全部系统声音", *self.app_lookup])
        self.message.set(f"发现 {len(devices)} 个麦克风、{len(apps)} 个可选应用")

    def _form(self):
        return {"title": self.title.get().strip(), "root": self.root_path.get().strip(),
                "source": SOURCES[self.source.get()],
                "app": self.app_manual.get().strip() or self.app_lookup.get(self.app_choice.get(), ""),
                "micDevice": self.mic_lookup.get(self.mic_choice.get(), ""),
                "asrBackend": ASR[self.asr_backend.get()], "asrModel": self.asr_model.get().strip(),
                "asrModelPath": self.asr_model_path.get().strip(), "asrApiUrl": self.asr_url.get().strip(),
                "language": self.language.get().strip() or "zh", "chunkSeconds": self.chunk_seconds.get().strip(),
                "summaryBackend": SUMMARY[self.summary_backend.get()],
                "summaryModel": self.summary_model.get().strip(), "summaryUrl": self.summary_url.get().strip()}

    def on_start(self):
        try:
            item = self.service.start(self._form())
            self.current = item
            self.selected_id = item["id"]
            self.refresh_history()
            self._view(item["id"])
            self.message.set("会议录音正在启动…")
            self._update_session()
        except (MeetingError, OSError, ValueError) as exc:
            self._error(exc)

    def on_stop(self):
        try:
            self.service.stop()
            self.message.set("正在结束录音并保存剩余音频…")
            self._update_session()
        except MeetingError as exc:
            self._error(exc)

    def on_summarize(self):
        if not self.selected_id:
            self._error("请先选择一场会议")
            return
        backend = SUMMARY[self.summary_backend.get()]
        if backend == "none":
            self._error("请先选择本地 Ollama 或线上 Qwen")
            return
        try:
            self.service.summarize(self.selected_id, backend, self.summary_model.get().strip(),
                                   self.summary_url.get().strip())
            self.message.set("正在生成 AI 纪要…")
            self.refresh_history()
        except (MeetingError, OSError) as exc:
            self._error(exc)

    def refresh_history(self):
        current = self.selected_id
        for child in self.history.get_children():
            self.history.delete(child)
        for item in self.service.meetings():
            try:
                date = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
            except (ValueError, KeyError):
                date = ""
            self.history.insert("", "end", iid=item["id"], values=(date, item.get("title", ""),
                                                                 STATES.get(item.get("state"), "待命")))
        if current and self.history.exists(current):
            self.history.selection_set(current)

    def _history_selected(self, _):
        chosen = self.history.selection()
        if chosen:
            self._view(chosen[0])

    def _view(self, identifier):
        try:
            detail = self.service.meeting_detail(identifier)
        except (MeetingError, OSError) as exc:
            self._error(exc)
            return
        self.selected_id = identifier
        self._write_text(self.transcript, detail["transcript"] or "等待实时转写…")
        self._write_text(self.summary, detail["summary"] or "录音结束后可生成会议纪要。\n\nAI 草稿需要以原始录音和转写核对。")

    def _update_session(self):
        status = self.service.status()
        self.current = status["current"]
        item = self.current
        self.session_title.set(item["title"] if item else "尚未开始会议")
        self.session_path.set(item["folder"] if item else "选择录音来源和模型，然后开始会议。")
        self.state_text.set(STATES.get(item["state"], "待命") if item else "待命")
        self.start_button.configure(state="disabled" if status["recording"] or self.closing else "normal")
        self.stop_button.configure(state="normal" if status["recording"] and
                                   item and item["state"] != "stopping" else "disabled")

    def _clock(self):
        item = self.current
        seconds = 0
        if item:
            if item.get("durationSeconds") is not None:
                seconds = float(item["durationSeconds"])
            elif self.service.status()["recording"]:
                try:
                    created = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00"))
                    seconds = (datetime.now(timezone.utc) - created).total_seconds()
                except (ValueError, KeyError):
                    pass
        seconds = max(0, int(seconds))
        self.elapsed.set(f"{seconds // 60:02d}:{seconds % 60:02d}")
        if not self.closing:
            self.window.after(1000, self._clock)

    def _poll(self):
        result = self.service.events_after(self.cursor, timeout=0)
        self.cursor = result["cursor"]
        if result["missed"] and self.selected_id:
            self._view(self.selected_id)
        refresh = False
        for event in result["events"]:
            kind, data = event["event"], event.get("data", {})
            if kind == "live":
                payload = data.get("payload", {})
                if payload.get("event") == "segment" and self.selected_id == data.get("meetingId"):
                    segment = payload.get("data", {})
                    current = self.transcript.get("1.0", "end").strip()
                    if current == "等待实时转写…":
                        self._write_text(self.transcript, "")
                    self._write_text(self.transcript,
                                     f"[{segment.get('startSeconds', 0):.1f}-{segment.get('endSeconds', 0):.1f}] "
                                     f"[{TRACKS.get(segment.get('track'), segment.get('track', '音轨'))}] "
                                     f"{segment.get('text', '')}\n", append=True)
                elif payload.get("event") in ("started", "completed", "error"):
                    refresh = True
                    if payload["event"] == "completed":
                        self.message.set("录音和实时转写已完成")
                        if self.selected_id == data.get("meetingId"):
                            self._view(self.selected_id)
                    elif payload["event"] == "error":
                        self.message.set(payload.get("error", {}).get("message", "录音失败"))
            elif kind in ("summaryStarted", "summaryCompleted", "summaryError", "meeting", "processExit", "root"):
                refresh = True
                if kind == "summaryCompleted":
                    self.message.set("AI 纪要已保存")
                    if self.selected_id == data.get("meetingId"):
                        self._view(self.selected_id)
                elif kind == "summaryError":
                    self.message.set("纪要失败：" + data.get("message", "未知错误"))
            elif kind == "log" and data.get("message"):
                self.message.set(data["message"])
        if refresh:
            self._update_session()
            self.refresh_history()
        if self.closing:
            self._finish_close()
        if self.window.winfo_exists():
            self.window.after(200, self._poll)

    def open_artifact(self, kind):
        if not self.selected_id:
            self._error("请先选择一场会议")
            return
        try:
            item = self.service.meeting(self.selected_id)
            path = Path(item["folder"] if kind == "folder" else item.get(kind) or "")
            if kind != "folder" and path.resolve().parent != Path(item["folder"]):
                raise MeetingError("会议文件路径无效")
            if not path.exists():
                raise MeetingError("文件尚未生成")
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except (MeetingError, OSError) as exc:
            self._error(exc)

    def _error(self, error):
        self.message.set(str(error))
        messagebox.showerror("AI 会议助手", str(error), parent=self.window)

    def on_close(self):
        self.closing = True
        self.message.set("正在保存会议并等待纪要完成…")
        if self.service.status()["recording"]:
            try:
                if self.service.process and self.service.process.poll() is None:
                    self.service.stop()
            except MeetingError as exc:
                self.message.set(str(exc))
                self.closing = False
                return
        self._finish_close()

    def _finish_close(self):
        status = self.service.status()
        if not status["recording"] and not status["summaryBusy"]:
            self.window.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Native AI meeting assistant desktop GUI")
    parser.add_argument("--root", default=str(DEFAULT_RECORDINGS), help="Meeting recordings directory")
    parser.add_argument("--smoke", action="store_true", help="Open and close the native window for validation")
    args = parser.parse_args(argv)
    window = tk.Tk()
    MeetingAssistant(window, MeetingService(Path(args.root)))
    if args.smoke:
        window.after(700, window.destroy)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
