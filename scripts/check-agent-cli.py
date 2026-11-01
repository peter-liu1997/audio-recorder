#!/usr/bin/env python3
"""Permission-free smoke check for the agent-facing CLI contract."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def main() -> None:
    executable = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/AudioRecorder.app/Contents/MacOS/audio-recorder").resolve()
    assert executable.is_file(), executable
    with tempfile.TemporaryDirectory(prefix="audio-recorder-agent-") as state_dir:
        env = dict(os.environ, AUDIO_RECORDER_STATE_DIR=state_dir)

        def call(*args: str, exit_code: int = 0) -> dict:
            result = subprocess.run([str(executable), *args, "--json"], env=env, text=True,
                                    capture_output=True, timeout=15, check=False)
            assert result.returncode == exit_code, (args, result.returncode, result.stdout, result.stderr)
            lines = result.stdout.splitlines()
            assert len(lines) == 1, (args, result.stdout, result.stderr)
            reply = json.loads(lines[0])
            assert reply["schemaVersion"] == 1 and isinstance(reply["ok"], bool), reply
            return reply

        permissions = call("doctor")["data"]
        assert permissions["microphone"]
        if permissions["microphone"] != "granted":
            denied = call("start", "--source", "mic", exit_code=3)
            assert denied["error"]["code"] == "PERMISSION_REQUIRED"
        if permissions["systemAudio"] != "granted":
            denied = call("apps", exit_code=3)
            assert denied["error"]["code"] == "PERMISSION_REQUIRED"
        assert call("list")["data"]["sessions"] == []
        assert call("status")["data"]["session"] is None

        bad = call("record", "--source", "invalid", exit_code=2)
        assert bad["error"]["code"] == "INVALID_ARGUMENT"
        bad = call("status", "--id", "invalid", exit_code=4)
        assert bad["error"]["code"] == "INVALID_SESSION_ID"
        bad = call("stop", exit_code=4)
        assert bad["error"]["code"] == "NO_ACTIVE_SESSION"

        done_id = str(uuid.uuid4()).upper()
        done = {"schemaVersion": 1, "id": done_id, "state": "done", "output": "/tmp/agent-check.wav",
                "source": "mic", "createdAt": "2026-01-01T00:00:00Z", "logPath": "/tmp/agent-check.log",
                "arguments": ["--source", "mic"]}
        Path(state_dir, f"{done_id}.json").write_text(json.dumps(done))
        assert call("status", "--id", done_id.lower())["data"]["session"]["state"] == "done"
        assert call("stop", "--id", done_id)["data"]["session"]["id"] == done_id
        assert len(call("list")["data"]["sessions"]) == 1

        stale_id = str(uuid.uuid4()).upper()
        stale = dict(done, id=stale_id, state="recording", pid=99999999)
        Path(state_dir, f"{stale_id}.json").write_text(json.dumps(stale))
        session = call("status", "--id", stale_id)["data"]["session"]
        assert session["state"] == "failed" and session["error"], session
        assert call("stop", "--id", stale_id, exit_code=6)["error"]["code"] == "CAPTURE_FAILED"
        assert json.loads(Path(state_dir, f"{stale_id}.json").read_text())["state"] == "failed"
        print("agent CLI JSON and session checks passed")


if __name__ == "__main__":
    main()
