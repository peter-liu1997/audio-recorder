import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "transcribe.py"
spec = importlib.util.spec_from_file_location("audio_recorder_transcribe", SCRIPT)
transcriber = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transcriber)


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="audio-recorder-test-")
        self.root = Path(self.directory.name)
        self.audio = self.root / "会议录音.wav"
        with wave.open(str(self.audio), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(b"\x00\x00\x00\x00" * 4800)
        self.model = self.root / "model.gguf"
        self.model.write_bytes(b"model fixture")

    def tearDown(self):
        self.directory.cleanup()

    def fake_cli(self, name, body):
        path = self.root / name
        path.write_text("#!" + sys.executable + "\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_local_whisper_json_and_no_overwrite(self):
        cli = self.fake_cli("whisper-cli", """import pathlib, sys
args = sys.argv[1:]
pathlib.Path(args[args.index('-of') + 1] + '.txt').write_text('你好，世界\\n', encoding='utf-8')
""")
        command = [sys.executable, str(SCRIPT), "--input", str(self.audio),
                   "--model-path", str(self.model), "--whisper-cli", str(cli), "--json"]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        reply = json.loads(first.stdout)
        self.assertEqual(reply["data"]["segments"][0]["text"], "你好，世界")
        self.assertFalse(reply["data"]["uploaded"])
        self.assertEqual(Path(reply["data"]["output"]).read_text(encoding="utf-8"), "你好，世界\n")
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 4)
        self.assertEqual(json.loads(second.stdout)["error"]["code"], "OUTPUT_EXISTS")

    def test_local_gguf_routes_qwen_model(self):
        cli = self.fake_cli("transcribe-cli", "print('backend: CPU\\ntext: 早上好\\nrealtime: 10x')\n")
        args = transcriber.parse_args(["--input", str(self.audio), "--backend", "local-gguf",
                                       "--model", "qwen3-asr-0.6b", "--model-path", str(self.model),
                                       "--transcribe-cli", str(cli)])
        result = transcriber.transcribe(args)
        self.assertEqual(result["model"], "qwen3-asr-0.6b")
        self.assertEqual(result["segments"][0]["text"], "早上好")

    def test_qwen_request_is_explicit_and_records_upload(self):
        args = transcriber.parse_args(["--input", str(self.audio), "--backend", "qwen",
                                       "--api-url", "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"])
        calls = []

        def respond(req, timeout):
            calls.append((req, timeout))
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop",
                                                    "message": {"content": "这是线上转录。"}}]}).encode())

        with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}), \
             mock.patch.object(transcriber.request, "urlopen", side_effect=respond):
            result = transcriber.transcribe(args)
        self.assertTrue(result["uploaded"])
        self.assertEqual(result["segments"][0]["text"], "这是线上转录。")
        self.assertEqual(len(calls), 1)
        request_body = json.loads(calls[0][0].data)
        self.assertEqual(request_body["model"], "qwen3-asr-flash")
        self.assertEqual(request_body["asr_options"]["language"], "zh")
        self.assertTrue(request_body["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,"))


if __name__ == "__main__":
    unittest.main()
