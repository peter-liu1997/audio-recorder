import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gui"))
from meeting_core import (MeetingError, MeetingSettings, create_meeting,
                          generate_summary, live_command, list_meetings,
                          save_summary, validate_summary_endpoint)
from meeting_service import MeetingService


class MeetingGUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="meeting-gui-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_meeting_folder_and_live_command(self):
        model = self.root / "model.gguf"
        model.write_bytes(b"fixture")
        settings = MeetingSettings(title="客户 / 方案评审", root=self.root,
                                   asr_model_path=str(model), summary_backend="none")
        meeting = create_meeting(settings)
        command = live_command(settings, meeting, Path("/usr/bin/audio-recorder"))
        self.assertEqual(command[1], "live")
        self.assertIn("--json", command)
        self.assertEqual(command[command.index("--model-path") + 1], str(model))
        self.assertEqual(list_meetings(self.root)[0]["title"], settings.title)
        self.assertNotIn("key", (Path(meeting["folder"]) / "meeting.json").read_text().lower())
        self.assertEqual(Path(meeting["folder"]).name.count("/"), 0)

    def test_local_summary_and_versioned_save(self):
        requests = []

        def respond(req, timeout):
            requests.append(json.loads(req.data))
            return io.BytesIO(json.dumps({"message": {"content": "## 核心结论\n同意试点。"}}).encode())

        result = generate_summary("试点会", "[0-5] [system] 同意试点。", "ollama", "qwen3:8b",
                                  "http://127.0.0.1:11434/api/chat", respond)
        self.assertIn("同意试点", result)
        self.assertFalse(requests[0]["stream"])
        meeting = create_meeting(MeetingSettings(title="试点会", root=self.root))
        first = save_summary(meeting, result, "ollama", "qwen3:8b")
        second = save_summary(meeting, result, "ollama", "qwen3:8b")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file() and second.is_file())
        self.assertFalse(meeting["summaryUploaded"])

    def test_cloud_summary_is_explicit_and_long_transcript_is_not_dropped(self):
        calls = []

        def respond(req, timeout):
            calls.append((req.full_url, req.get_header("Authorization"), json.loads(req.data)))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "压缩后的事实与纪要"}}]}).encode())

        url = "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
        with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}):
            result = generate_summary("长会", "[00:00] 讨论方案\n" * 3000,
                                      "qwen", "qwen-plus", url, respond)
        self.assertEqual(result, "压缩后的事实与纪要")
        self.assertGreater(len(calls), 2)
        self.assertTrue(all(call[0] == url and call[1] == "Bearer test-key" for call in calls))
        with self.assertRaises(MeetingError):
            validate_summary_endpoint("ollama", "https://example.com/api/chat")

    def test_native_gui_service_recording_lifecycle(self):
        cli = self.root / "fake-recorder"
        cli.write_text(f"#!{sys.executable}\n" + '''import json, pathlib, signal, sys, time
if sys.argv[1] in ("devices", "apps"):
    print(json.dumps({"ok": True, "data": {sys.argv[1]: []}})); sys.exit()
output = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
transcript = pathlib.Path(sys.argv[sys.argv.index("--transcript") + 1])
def emit(event, data):
    print(json.dumps({"event": event, "data": data}), flush=True)
def finish(*_):
    output.write_bytes(b"RIFFtest")
    transcript.write_text("[0.00-1.00] [microphone] 同意试点。\\n", encoding="utf-8")
    emit("segment", {"startSeconds": 0, "endSeconds": 1, "track": "microphone", "text": "同意试点。"})
    emit("completed", {"durationSeconds": 1})
    sys.exit(0)
signal.signal(signal.SIGINT, finish)
emit("started", {})
while True: time.sleep(0.05)
''', encoding="utf-8")
        cli.chmod(0o755)
        service = MeetingService(self.root, cli)
        try:
            item = service.start({"title": "方案会", "asrBackend": "local",
                                  "asrModel": "medium", "summaryBackend": "none"})
            deadline = time.monotonic() + 5
            while service.meeting(item["id"])["state"] != "recording" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(service.meeting(item["id"])["state"], "recording")
            service.stop()
            while service.meeting(item["id"])["state"] != "done" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(service.meeting(item["id"])["state"], "done")
            self.assertIn("同意试点", service.meeting_detail(item["id"])["transcript"])
            self.assertEqual(Path(item["audio"]).read_bytes(), b"RIFFtest")
            self.assertTrue(service.events_after(0, 0)["events"])
        finally:
            if service.process and service.process.poll() is None:
                service.stop()


if __name__ == "__main__":
    unittest.main()
