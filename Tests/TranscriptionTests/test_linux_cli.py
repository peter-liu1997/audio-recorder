import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import wave


CLI = Path(__file__).resolve().parents[2] / "linux" / "audio-recorder"


class LinuxCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audio-recorder-linux-test-")
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        pactl = self.bin / "pactl"
        pactl.write_text("#!" + sys.executable + "\n" + """import json,sys
args=sys.argv[1:]
if args[-1]=='sources': print(json.dumps([{'name':'alsa_input.test','description':'Test microphone'},{'name':'alsa_output.test.monitor','description':'Monitor'}]))
elif args[-1]=='sinks': print(json.dumps([{'index':2,'name':'alsa_output.test','monitor_source':'alsa_output.test.monitor'}]))
elif args[-1]=='sink-inputs': print(json.dumps([{'index':37,'sink':2,'name':'Zoom','properties':{'application.name':'Zoom','application.process.id':'123'}}]))
elif args[-1]=='get-default-source': print('alsa_input.test')
elif args[-1]=='get-default-sink': print('alsa_output.test')
elif args[-1]=='info': print('Server Name: test-pulse')
else: sys.exit(1)
""")
        pactl.chmod(0o755)
        parec = self.bin / "parec"
        parec.write_text("#!" + sys.executable + "\n" + """import os,signal,sys,time
with open(os.environ['FAKE_PAREC_ARGS'],'a') as log: log.write(' '.join(sys.argv[1:])+'\\n')
signal.signal(signal.SIGINT,lambda *_: sys.exit(0))
sys.stdout.buffer.write(b'\\x01\\x00\\x01\\x00'*48000)
sys.stdout.buffer.flush()
while True: time.sleep(.1)
""")
        parec.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        AUDIO_RECORDER_STATE_DIR=str(self.root / "sessions"),
                        FAKE_PAREC_ARGS=str(self.root / "parec-args.txt"))

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args):
        done = subprocess.run([sys.executable, str(CLI), *args, "--json"], env=self.env,
                              capture_output=True, text=True, timeout=30)
        return done.returncode, json.loads(done.stdout)

    def test_app_capture_and_wav(self):
        code, response = self.command("apps")
        self.assertEqual(code, 0)
        self.assertEqual(response["data"]["apps"][0]["id"], 37)
        output = self.root / "app.wav"
        code, response = self.command("record", "--source", "system", "--app", "37",
                                      "--duration", "0.3", "--output", str(output))
        self.assertEqual(code, 0, response)
        self.assertFalse(response["data"]["tracks"])
        with wave.open(str(output), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (48000, 2, 2))
            self.assertGreater(wav.getnframes(), 0)
        self.assertIn("--monitor-stream=37", (self.root / "parec-args.txt").read_text())

    def test_background_lifecycle(self):
        output = self.root / "meeting.wav"
        code, response = self.command("start", "--source", "both", "--duration", "0.35",
                                      "--output", str(output), "--keep-tracks")
        self.assertEqual(code, 0, response)
        identifier = response["data"]["session"]["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            code, status = self.command("status", "--id", identifier)
            self.assertEqual(code, 0)
            if status["data"]["session"]["state"] in ("done", "failed"):
                break
            time.sleep(.1)
        self.assertEqual(status["data"]["session"]["state"], "done", status)
        self.assertTrue(output.is_file())
        self.assertEqual(len(status["data"]["session"]["tracks"]), 2)
        self.assertTrue(all(Path(path).is_file() for path in status["data"]["session"]["tracks"]))
        with wave.open(str(output), "rb") as wav:
            self.assertEqual(wav.getnframes(), 16800)
            self.assertNotEqual(wav.readframes(100), b"\x00" * 400)
        code, stopped = self.command("stop", "--id", identifier)
        self.assertEqual(code, 0)
        self.assertEqual(stopped["data"]["session"]["state"], "done")

    def test_stop_active_session(self):
        output = self.root / "stopped.wav"
        code, response = self.command("start", "--source", "mic", "--output", str(output))
        self.assertEqual(code, 0, response)
        identifier = response["data"]["session"]["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, status = self.command("status", "--id", identifier)
            if status["data"]["session"]["state"] == "recording":
                break
            time.sleep(.1)
        self.assertEqual(status["data"]["session"]["state"], "recording", status)
        code, stopped = self.command("stop", "--id", identifier)
        self.assertEqual(code, 0, stopped)
        self.assertEqual(stopped["data"]["session"]["state"], "done")
        self.assertTrue(output.is_file())

    def test_live_emits_segments_before_recording_stops(self):
        model = self.root / "model.gguf"
        model.write_bytes(b"fixture")
        whisper = self.bin / "whisper-cli"
        whisper.write_text("#!" + sys.executable + "\n" + """import pathlib,sys
args=sys.argv[1:]
pathlib.Path(args[args.index('-of')+1]+'.txt').write_text('实时中文\\n',encoding='utf-8')
""")
        whisper.chmod(0o755)
        output = self.root / "live.wav"
        process = subprocess.Popen([
            sys.executable, str(CLI), "live", "--source", "both",
            "--output", str(output), "--duration", "1.5", "--chunk-seconds", "1",
            "--model-path", str(model), "--whisper-cli", str(whisper), "--json",
        ], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            started = json.loads(process.stdout.readline())
            microphone = json.loads(process.stdout.readline())
            system = json.loads(process.stdout.readline())
            self.assertEqual(started["event"], "started")
            self.assertEqual({microphone["data"]["track"], system["data"]["track"]},
                             {"microphone", "system"})
            self.assertEqual(microphone["data"]["text"], "实时中文")
            self.assertIsNone(process.poll(), "Segments should be emitted during recording")
            tail, errors = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, errors)
            completed = json.loads(tail.strip().splitlines()[-1])
            self.assertEqual(completed["event"], "completed")
            self.assertTrue(output.is_file())
            self.assertTrue((self.root / "live.transcript.txt").is_file())
            self.assertEqual(len((self.root / "live.live.jsonl").read_text().splitlines()), 4)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_live_keeps_raw_audio_when_model_fails(self):
        model = self.root / "model.gguf"
        model.write_bytes(b"fixture")
        whisper = self.bin / "whisper-cli"
        whisper.write_text("#!" + sys.executable + "\n" + "import sys\nsys.exit(1)\n")
        whisper.chmod(0o755)
        output = self.root / "failed-live.wav"
        done = subprocess.run([
            sys.executable, str(CLI), "live", "--source", "mic",
            "--output", str(output), "--duration", "1.5", "--chunk-seconds", "1",
            "--model-path", str(model), "--whisper-cli", str(whisper), "--json",
        ], env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 6, done.stderr)
        error = json.loads(done.stdout.splitlines()[-1])
        self.assertEqual(error["error"]["code"], "TRANSCRIPTION_FAILED")
        recovery = Path(error["recoveryPath"])
        self.assertTrue((recovery / "microphone.raw").is_file())
        events = [json.loads(line) for line in (self.root / "failed-live.live.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "error")


if __name__ == "__main__":
    unittest.main()
