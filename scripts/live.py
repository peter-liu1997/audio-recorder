#!/usr/bin/env python3
"""Continuous recording with incremental, recoverable ASR events."""

from __future__ import annotations

import argparse
import array
import datetime as dt
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import transcribe as asr


RATE = 48000
FRAME_BYTES = 4


class LiveError(asr.TranscriptionError):
    pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = asr.Parser(description="Record and emit incremental transcription events")
    parser.add_argument("--recorder-cli", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--source", choices=("mic", "system", "both"), default="both")
    parser.add_argument("--app")
    parser.add_argument("--mic-device")
    parser.add_argument("--output", help="Final audio WAV path")
    parser.add_argument("--transcript", help="Incrementally written transcript .txt")
    parser.add_argument("--duration", type=float)
    parser.add_argument("--chunk-seconds", type=float, default=5.0)
    parser.add_argument("--track-mode", choices=("separate", "mix"), default="separate")
    parser.add_argument("--mic-gain", type=float, default=1.0)
    parser.add_argument("--system-gain", type=float, default=1.0)
    parser.add_argument("--keep-tracks", action="store_true")
    parser.add_argument("--backend", choices=("local", "local-gguf", "qwen"), default="local")
    parser.add_argument("--model")
    parser.add_argument("--model-path")
    parser.add_argument("--model-dir", default=os.environ.get("AUDIO_RECORDER_MODEL_DIR", "models"))
    parser.add_argument("--language", default="zh")
    parser.add_argument("--ffmpeg", default=os.environ.get("AUDIO_RECORDER_FFMPEG", "ffmpeg"))
    parser.add_argument("--whisper-cli", default=os.environ.get("AUDIO_RECORDER_WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--transcribe-cli", default=os.environ.get("AUDIO_RECORDER_TRANSCRIBE_CLI", "transcribe-cli"))
    parser.add_argument("--api-url", default=os.environ.get("QWEN_ASR_API_URL"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        raise LiveError("INVALID_ARGUMENT", "--duration must be positive and finite", 2)
    if not math.isfinite(args.chunk_seconds) or not 1 <= args.chunk_seconds <= 25:
        raise LiveError("INVALID_ARGUMENT", "--chunk-seconds must be between 1 and 25", 2)
    if any(not math.isfinite(gain) or gain < 0 or gain > 4
           for gain in (args.mic_gain, args.system_gain)):
        raise LiveError("INVALID_ARGUMENT", "Gains must be between 0 and 4", 2)
    if args.app and args.source == "mic":
        raise LiveError("INVALID_ARGUMENT", "--app requires system or both source", 2)
    if args.mic_device and args.source == "system":
        raise LiveError("INVALID_ARGUMENT", "--mic-device requires mic or both source", 2)
    return args


def read_frames(path: Path, start: int, count: int) -> bytes:
    if not path.is_file():
        return b""
    with path.open("rb") as source:
        source.seek(start * FRAME_BYTES)
        data = source.read(count * FRAME_BYTES)
    return data[:len(data) // FRAME_BYTES * FRAME_BYTES]


def available_frames(paths: dict[str, Path]) -> int:
    return max((path.stat().st_size // FRAME_BYTES if path.is_file() else 0
                for path in paths.values()), default=0)


def mix_pcm(mic: bytes, system: bytes, mic_gain: float, system_gain: float) -> bytes:
    length = max(len(mic), len(system))
    if not length:
        return b""
    mic = mic.ljust(length, b"\x00")
    system = system.ljust(length, b"\x00")
    left = array.array("h")
    right = array.array("h")
    left.frombytes(mic)
    right.frombytes(system)
    if sys.byteorder != "little":
        left.byteswap()
        right.byteswap()
    result = array.array("h", (max(-32768, min(32767, round(a * mic_gain + b * system_gain)))
                                for a, b in zip(left, right)))
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def normalize_pcm(raw: bytes, target: Path, ffmpeg: str) -> None:
    try:
        done = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "pipe:0",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(target),
        ], input=raw, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise LiveError("TRANSCRIPTION_FAILED", "FFmpeg normalization timed out") from exc
    if done.returncode:
        raise LiveError("TRANSCRIPTION_FAILED", "FFmpeg normalization failed: " +
                        done.stderr.decode("utf-8", "replace")[-600:])


def stop_recorder(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    except OSError:
        pass


def emit(event: str, data: dict, json_mode: bool, events_file) -> None:
    item = {"schemaVersion": 1, "ok": True, "command": "live", "event": event, "data": data}
    line = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    events_file.write(line + "\n")
    events_file.flush()
    if json_mode:
        print(line, flush=True)
    elif event == "segment":
        print(f"{data['startSeconds']:.1f}-{data['endSeconds']:.1f}s [{data['track']}] {data['text']}", flush=True)
    elif event == "started":
        print("Recording and transcribing:", data["output"], flush=True)
    elif event == "completed":
        print("Saved", data["output"], "and", data["transcript"], flush=True)


def run_live(args: argparse.Namespace) -> dict:
    recorder = asr.executable(args.recorder_cli, "audio-recorder")
    engine = asr.prepare_engine(args)
    output = (Path(args.output).expanduser() if args.output else
              Path("recording-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".wav")).resolve()
    if output.suffix.lower() != ".wav":
        raise LiveError("INVALID_ARGUMENT", "--output must end in .wav", 2)
    transcript = (Path(args.transcript).expanduser() if args.transcript else
                  output.with_suffix(".transcript.txt")).resolve()
    metadata = transcript.with_suffix(".json")
    events_path = output.with_suffix(".live.jsonl")
    paths = (output, transcript, metadata, events_path)
    if len(set(paths)) != len(paths):
        raise LiveError("INVALID_ARGUMENT", "Audio, transcript, metadata, and event paths must differ", 2)
    if any(path.exists() for path in paths):
        raise LiveError("OUTPUT_EXISTS", "An audio or transcript output already exists", 4)
    output.parent.mkdir(parents=True, exist_ok=True)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    raw_dir = output.parent / (".audio-recorder-live-" + str(uuid.uuid4()))
    raw_dir.mkdir(mode=0o700)
    command = [recorder, "record", "--source", args.source, "--output", str(output),
               "--live-dir", str(raw_dir), "--mic-gain", str(args.mic_gain),
               "--system-gain", str(args.system_gain), "--json"]
    if args.app:
        command += ["--app", args.app]
    if args.mic_device:
        command += ["--mic-device", args.mic_device]
    if args.duration is not None:
        command += ["--duration", str(args.duration)]
    if args.keep_tracks:
        command.append("--keep-tracks")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = None
    success = False
    stop_requested = False
    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)

    def request_stop(*_):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=creationflags,
                                   start_new_session=os.name != "nt")
        tracks = {role: raw_dir / (role + ".raw") for role in
                  (["microphone"] if args.source == "mic" else
                   ["system"] if args.source == "system" else ["microphone", "system"])}
        chunk_frames = round(args.chunk_seconds * RATE)
        cursor = 0
        index = 0
        segments = []
        started = False
        chunk_ready_at = None
        stop_sent_at = None
        started_at = time.monotonic()
        with events_path.open("x", encoding="utf-8", buffering=1) as events_file, \
                transcript.open("x", encoding="utf-8", buffering=1) as text_file, \
                tempfile.TemporaryDirectory(prefix="audio-recorder-live-asr-") as temp:
            directory = Path(temp)
            while True:
                if stop_requested and stop_sent_at is None:
                    stop_recorder(process)
                    stop_sent_at = time.monotonic()
                if stop_sent_at is not None and process.poll() is None and time.monotonic() - stop_sent_at > 30:
                    process.kill()
                    raise LiveError("STOP_TIMEOUT", "Recorder did not stop within 30 seconds")
                if not started and (any(path.exists() for path in tracks.values()) or process.poll() is not None):
                    if process.poll() is None:
                        emit("started", {"output": str(output), "transcript": str(transcript),
                                         "events": str(events_path), "backend": args.backend,
                                         "model": args.model, "uploaded": args.backend == "qwen"},
                             args.json, events_file)
                        started = True
                frames = available_frames(tracks)
                finished = process.poll() is not None
                remaining = frames - cursor
                if remaining >= chunk_frames and chunk_ready_at is None:
                    chunk_ready_at = time.monotonic()
                ready = (remaining >= chunk_frames and chunk_ready_at is not None and
                         time.monotonic() - chunk_ready_at >= 0.2)
                if ready or (finished and remaining > 0):
                    count = min(chunk_frames, remaining)
                    end = cursor + count
                    slices = {role: read_frames(path, cursor, count) for role, path in tracks.items()}
                    if args.track_mode == "mix":
                        slices = {"mixed": mix_pcm(slices.get("microphone", b""),
                                                   slices.get("system", b""),
                                                   args.mic_gain, args.system_gain)}
                    for role, raw in slices.items():
                        if not raw or not any(raw):
                            continue
                        chunk = directory / f"chunk-{index:05d}-{role}.wav"
                        normalize_pcm(raw, chunk, engine["ffmpeg"])
                        recognized = asr.recognize_chunk(chunk, index, directory, args, engine).strip()
                        if not recognized:
                            continue
                        segment = {"index": index, "startSeconds": round(cursor / RATE, 3),
                                   "endSeconds": round((cursor + len(raw) // FRAME_BYTES) / RATE, 3),
                                   "track": role, "text": recognized, "final": True,
                                   "backend": args.backend, "model": args.model,
                                   "latencySeconds": round(max(0, time.monotonic() - started_at - end / RATE), 3)}
                        segments.append(segment)
                        text_file.write(f"[{segment['startSeconds']:.2f}-{segment['endSeconds']:.2f}] "
                                        f"[{role}] {recognized}\n")
                        text_file.flush()
                        emit("segment", segment, args.json, events_file)
                    cursor = end
                    index += 1
                    chunk_ready_at = None
                    continue
                if finished:
                    break
                time.sleep(0.1)
            recorder_stdout, recorder_stderr = process.communicate(timeout=5)
            if process.returncode:
                try:
                    error = json.loads(recorder_stdout)["error"]
                    raise LiveError(error["code"], error["message"], process.returncode)
                except (ValueError, KeyError, TypeError):
                    raise LiveError("CAPTURE_FAILED", recorder_stderr.decode("utf-8", "replace")[-600:] or
                                    "Recorder exited unsuccessfully", process.returncode)
            result = json.loads(recorder_stdout)["data"]
            summary = {"output": str(output), "transcript": str(transcript),
                       "metadata": str(metadata), "events": str(events_path),
                       "backend": args.backend, "model": args.model,
                       "uploaded": args.backend == "qwen", "segments": len(segments),
                       "durationSeconds": result["durationSeconds"], "tracks": result.get("tracks", [])}
            asr.write_atomic(metadata, json.dumps({**summary, "segments": segments,
                                                   "timestampPrecision": "chunk"}, ensure_ascii=False, indent=2) + "\n")
            emit("completed", summary, args.json, events_file)
            success = True
            return summary
    except Exception as exc:
        if isinstance(exc, asr.TranscriptionError):
            exc.recovery_path = str(raw_dir)
        if events_path.is_file():
            code = exc.code if isinstance(exc, asr.TranscriptionError) else "IO_ERROR"
            failure = {"schemaVersion": 1, "ok": False, "command": "live", "event": "error",
                       "error": {"code": code, "message": str(exc)}, "recoveryPath": str(raw_dir)}
            try:
                with events_path.open("a", encoding="utf-8") as events_file:
                    events_file.write(json.dumps(failure, ensure_ascii=False) + "\n")
            except OSError:
                pass
        raise
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        if process is not None and process.poll() is None:
            stop_recorder(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if success:
            shutil.rmtree(raw_dir)
        else:
            print(f"Live raw tracks retained for recovery: {raw_dir}", file=sys.stderr)


def main(argv: list[str]) -> int:
    json_mode = "--json" in argv
    try:
        args = parse_args(argv)
        run_live(args)
        return 0
    except (asr.TranscriptionError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, asr.TranscriptionError) else "IO_ERROR"
        status = exc.exit_code if isinstance(exc, asr.TranscriptionError) else 7
        if json_mode:
            failure = {"schemaVersion": 1, "ok": False, "command": "live",
                       "event": "error", "error": {"code": code, "message": str(exc)}}
            if hasattr(exc, "recovery_path"):
                failure["recoveryPath"] = exc.recovery_path
            print(json.dumps(failure, ensure_ascii=False), flush=True)
        else:
            print(f"audio-recorder: {exc}", file=sys.stderr)
        return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
