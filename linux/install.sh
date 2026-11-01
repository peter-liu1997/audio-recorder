#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PREFIX=${PREFIX:-"$HOME/.local"}
mkdir -p "$PREFIX/bin" "$PREFIX/lib/audio-recorder/gui"
install -m 755 "$ROOT/linux/audio-recorder" "$PREFIX/bin/audio-recorder"
install -m 644 "$ROOT/scripts/transcribe.py" "$PREFIX/lib/audio-recorder/transcribe.py"
install -m 644 "$ROOT/scripts/live.py" "$PREFIX/lib/audio-recorder/live.py"
for name in meeting_assistant.py meeting_service.py meeting_core.py; do
    install -m 644 "$ROOT/gui/$name" "$PREFIX/lib/audio-recorder/gui/$name"
done
install -m 755 "$ROOT/linux/meeting-assistant" "$PREFIX/bin/meeting-assistant"
printf 'Installed %s/bin/audio-recorder and meeting-assistant\n' "$PREFIX"
