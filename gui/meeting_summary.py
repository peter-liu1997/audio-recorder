#!/usr/bin/env python3
"""Summarize a saved meeting for the native macOS application."""

import argparse
import json
from pathlib import Path
import sys

from meeting_core import MeetingError, generate_summary, save_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting", required=True)
    parser.add_argument("--backend", choices=("ollama", "qwen"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    manifest = Path(args.meeting).expanduser().resolve()
    item = json.loads(manifest.read_text(encoding="utf-8"))
    folder = manifest.parent
    if item.get("folder") != str(folder) or Path(item["transcript"]).resolve().parent != folder:
        raise MeetingError("会议文件路径无效")
    transcript = Path(item["transcript"]).read_text(encoding="utf-8")
    body = generate_summary(item["title"], transcript, args.backend, args.model, args.url)
    target = save_summary(item, body, args.backend, args.model)
    print(json.dumps({"summary": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (MeetingError, OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
