#!/usr/bin/env python3
"""Cross-platform transcription backend for the native audio-recorder CLIs."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib import error as urlerror
from urllib import request
from urllib.parse import urlparse
import wave


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


SCHEMA_VERSION = 1
LOCAL_MODELS = ("small", "medium", "large-v3", "large-v3-turbo")
CHUNK_SECONDS = 180  # 16 kHz mono PCM stays below Qwen's 10 MB data-URI limit.
GGUF_MODELS = ("qwen3-asr-0.6b", "qwen3-asr-1.7b", "sensevoice-small")


class TranscriptionError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 6):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise TranscriptionError("INVALID_ARGUMENT", message, 2)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = Parser(description="Transcribe with local Whisper/GGUF models or optional Qwen ASR cloud API")
    parser.add_argument("--input", required=True, help="Audio file to transcribe")
    parser.add_argument("--output", help="Transcript .txt path; default: INPUT.transcript.txt")
    parser.add_argument("--backend", choices=("local", "local-gguf", "qwen"), default="local")
    parser.add_argument("--model", help="Whisper size, GGUF model family, or cloud model name")
    parser.add_argument("--model-path", help="Explicit ggml model path for local Whisper")
    parser.add_argument("--model-dir", default=os.environ.get("AUDIO_RECORDER_MODEL_DIR", "models"))
    parser.add_argument("--language", default="zh", help="zh, yue, en, or auto; default zh")
    parser.add_argument("--ffmpeg", default=os.environ.get("AUDIO_RECORDER_FFMPEG", "ffmpeg"))
    parser.add_argument("--whisper-cli", default=os.environ.get("AUDIO_RECORDER_WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--transcribe-cli", default=os.environ.get("AUDIO_RECORDER_TRANSCRIBE_CLI", "transcribe-cli"))
    parser.add_argument("--api-url", default=os.environ.get("QWEN_ASR_API_URL"),
                        help="Qwen OpenAI-compatible /chat/completions URL")
    return parser.parse_args(argv)


def executable(value: str, label: str) -> str:
    found = shutil.which(value)
    if found:
        return found
    if Path(value).is_file():
        return str(Path(value).resolve())
    raise TranscriptionError("DEPENDENCY_MISSING", f"{label} not found: {value}", 5)


def run_process(command: list[str], label: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise TranscriptionError("TRANSCRIPTION_FAILED", f"{label} failed (exit {completed.returncode}): {detail}")


def split_audio(source: Path, directory: Path, ffmpeg: str, chunk_seconds: int) -> list[tuple[Path, float, float]]:
    pattern = directory / "chunk-%05d.wav"
    run_process([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source),
        "-map", "0:a:0", "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
        str(pattern),
    ], "ffmpeg")
    chunks = sorted(directory.glob("chunk-*.wav"))
    if not chunks:
        raise TranscriptionError("INVALID_AUDIO", "No audio stream or empty recording", 5)
    result = []
    position = 0.0
    for chunk in chunks:
        try:
            with wave.open(str(chunk), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                    raise ValueError("unexpected chunk format")
                duration = wav.getnframes() / wav.getframerate()
        except (wave.Error, ValueError) as exc:
            raise TranscriptionError("INVALID_AUDIO", f"Invalid normalized audio: {exc}", 5) from exc
        if duration <= 0:
            continue
        result.append((chunk, position, position + duration))
        position += duration
    if not result:
        raise TranscriptionError("INVALID_AUDIO", "Recording has no audio frames", 5)
    return result


def local_text(chunk: Path, index: int, directory: Path, args: argparse.Namespace,
               whisper: str, model_path: Path) -> str:
    prefix = directory / f"whisper-{index:05d}"
    run_process([
        whisper, "-m", str(model_path), "-f", str(chunk), "-l", args.language,
        "-otxt", "-of", str(prefix), "-np",
    ], "whisper-cli")
    transcript = prefix.with_suffix(".txt")
    if not transcript.is_file():
        raise TranscriptionError("TRANSCRIPTION_FAILED", "whisper-cli produced no transcript file")
    return transcript.read_text(encoding="utf-8-sig").strip()


def gguf_text(chunk: Path, args: argparse.Namespace, binary: str, model_path: Path) -> str:
    command = [binary, "-m", str(model_path)]
    if args.language != "auto":
        command += ["--language", args.language]
    command.append(str(chunk))
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise TranscriptionError("TRANSCRIPTION_FAILED", f"transcribe-cli failed (exit {completed.returncode}): {detail}")
    for line in completed.stdout.splitlines():
        if line.startswith("text:"):
            return line.removeprefix("text:").strip()
    raise TranscriptionError("TRANSCRIPTION_FAILED", "transcribe-cli produced no text: line")


def qwen_text(chunk: Path, args: argparse.Namespace, api_key: str) -> str:
    raw = chunk.read_bytes()
    data_uri = "data:audio/wav;base64," + base64.b64encode(raw).decode("ascii")
    if len(data_uri) >= 10_000_000:
        raise TranscriptionError("AUDIO_TOO_LARGE", "Normalized chunk exceeds Qwen's 10 MB input limit")
    asr_options = {"enable_itn": False}
    if args.language != "auto":
        asr_options["language"] = args.language
    body = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}]}],
        "stream": False,
        "asr_options": asr_options,
    }).encode("utf-8")
    req = request.Request(args.api_url, data=body, headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
    }, method="POST")
    try:
        with request.urlopen(req, timeout=180) as response:
            result = json.load(response)
    except urlerror.HTTPError as exc:
        raise TranscriptionError("CLOUD_API_ERROR", f"Qwen API returned HTTP {exc.code}") from exc
    except (urlerror.URLError, TimeoutError) as exc:
        raise TranscriptionError("CLOUD_API_ERROR", f"Qwen API connection failed: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
    try:
        choice = result["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise TranscriptionError("CLOUD_API_ERROR", f"Qwen ASR response ended with {choice['finish_reason']}")
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("content is not text")
        return text.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise TranscriptionError("CLOUD_API_ERROR", "Qwen API response has no transcript text") from exc


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".audio-recorder-", delete=False) as temp:
        temp.write(content)
        temp_path = Path(temp.name)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def prepare_engine(args: argparse.Namespace) -> dict:
    args.model = args.model or ({"local": "medium", "local-gguf": "qwen3-asr-0.6b",
                                "qwen": "qwen3-asr-flash"}[args.backend])
    ffmpeg = executable(args.ffmpeg, "ffmpeg")
    whisper = None
    gguf_binary = None
    model_path = None
    api_key = None
    if args.backend == "local":
        whisper = executable(args.whisper_cli, "whisper-cli")
        if args.model_path:
            model_path = Path(args.model_path).expanduser().resolve()
        else:
            if args.model not in LOCAL_MODELS:
                raise TranscriptionError("INVALID_ARGUMENT", f"Local model must be one of {', '.join(LOCAL_MODELS)}", 2)
            model_path = (Path(args.model_dir).expanduser() / f"ggml-{args.model}.bin").resolve()
        if not model_path.is_file():
            raise TranscriptionError("MODEL_NOT_FOUND", f"Whisper model not found: {model_path}", 5)
    elif args.backend == "local-gguf":
        if args.model not in GGUF_MODELS:
            raise TranscriptionError("INVALID_ARGUMENT", f"GGUF model must be one of {', '.join(GGUF_MODELS)}", 2)
        gguf_binary = executable(args.transcribe_cli, "transcribe-cli")
        if not args.model_path:
            raise TranscriptionError("MODEL_NOT_FOUND", "--model-path is required for local GGUF models", 5)
        model_path = Path(args.model_path).expanduser().resolve()
        if not model_path.is_file():
            raise TranscriptionError("MODEL_NOT_FOUND", f"GGUF model not found: {model_path}", 5)
    else:
        if args.model_path:
            raise TranscriptionError("INVALID_ARGUMENT", "--model-path is only for local models", 2)
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise TranscriptionError("CLOUD_NOT_CONFIGURED", "Set DASHSCOPE_API_KEY for Qwen ASR", 5)
        if not args.api_url:
            raise TranscriptionError("CLOUD_NOT_CONFIGURED", "Set QWEN_ASR_API_URL or --api-url to the workspace /chat/completions endpoint", 5)
        parsed = urlparse(args.api_url)
        if parsed.scheme != "https" or not parsed.netloc or not parsed.path.endswith("/chat/completions"):
            raise TranscriptionError("INVALID_ARGUMENT", "Qwen API URL must be an HTTPS /chat/completions endpoint", 2)
    return {"ffmpeg": ffmpeg, "whisper": whisper, "gguf_binary": gguf_binary,
            "model_path": model_path, "api_key": api_key}


def recognize_chunk(chunk: Path, index: int, directory: Path,
                    args: argparse.Namespace, engine: dict) -> str:
    if args.backend == "local":
        return local_text(chunk, index, directory, args, engine["whisper"], engine["model_path"])
    if args.backend == "local-gguf":
        return gguf_text(chunk, args, engine["gguf_binary"], engine["model_path"])
    return qwen_text(chunk, args, engine["api_key"])


def transcribe(args: argparse.Namespace) -> dict:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise TranscriptionError("INPUT_NOT_FOUND", f"Audio file not found: {source}", 5)
    output = (Path(args.output).expanduser() if args.output else source.with_suffix(".transcript.txt")).resolve()
    metadata = output.with_suffix(".json")
    if output == source or output.exists() or metadata.exists():
        raise TranscriptionError("OUTPUT_EXISTS", f"Output already exists: {output} or {metadata}", 4)
    engine = prepare_engine(args)
    segments = []
    with tempfile.TemporaryDirectory(prefix="audio-recorder-asr-") as work:
        directory = Path(work)
        chunk_seconds = 25 if args.backend == "local-gguf" and args.model == "sensevoice-small" else CHUNK_SECONDS
        chunks = split_audio(source, directory, engine["ffmpeg"], chunk_seconds)
        for index, (chunk, start, end) in enumerate(chunks):
            recognized = recognize_chunk(chunk, index, directory, args, engine)
            segments.append({"startSeconds": round(start, 3), "endSeconds": round(end, 3), "text": recognized})
    combined = "\n".join(segment["text"] for segment in segments if segment["text"])
    result = {
        "input": str(source), "output": str(output), "metadata": str(metadata),
        "backend": args.backend, "model": args.model, "language": args.language,
        "uploaded": args.backend == "qwen", "timestampPrecision": "chunk",
        "durationSeconds": round(segments[-1]["endSeconds"], 3), "segments": segments,
    }
    write_atomic(output, combined + ("\n" if combined else ""))
    write_atomic(metadata, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main(argv: list[str]) -> int:
    json_mode = "--json" in argv
    argv = [arg for arg in argv if arg != "--json"]
    try:
        args = parse_args(argv)
        result = transcribe(args)
        if json_mode:
            print(json.dumps({"schemaVersion": SCHEMA_VERSION, "ok": True,
                              "command": "transcribe", "data": result}, ensure_ascii=False))
        else:
            print(f"Saved {result['output']} ({result['backend']}: {result['model']})")
        return 0
    except TranscriptionError as exc:
        if json_mode:
            print(json.dumps({"schemaVersion": SCHEMA_VERSION, "ok": False,
                              "command": "transcribe", "error": {"code": exc.code, "message": str(exc)}},
                             ensure_ascii=False))
        else:
            print(f"audio-recorder: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        if json_mode:
            print(json.dumps({"schemaVersion": SCHEMA_VERSION, "ok": False,
                              "command": "transcribe", "error": {"code": "IO_ERROR", "message": str(exc)}},
                             ensure_ascii=False))
        else:
            print(f"audio-recorder: {exc}", file=sys.stderr)
        return 7


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
