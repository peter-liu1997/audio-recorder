#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
sh "$ROOT/scripts/build.sh"
cd "$ROOT"
mkdir -p .build-cache/clang .build-cache/xdg
export CLANG_MODULE_CACHE_PATH="$ROOT/.build-cache/clang"
export SWIFTPM_MODULECACHE_OVERRIDE="$ROOT/.build-cache/clang"
export XDG_CACHE_HOME="$ROOT/.build-cache/xdg"
if [ -n "${AUDIO_RECORDER_SDK:-}" ]; then
    SDK="$AUDIO_RECORDER_SDK"
elif [ -d /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk ]; then
    SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk
else
    SDK=$(xcrun --show-sdk-path)
fi
swift build --disable-sandbox --sdk "$SDK" -c release --product meeting-assistant
BIN_DIR=$(swift build --disable-sandbox --sdk "$SDK" -c release --show-bin-path)
CLI_APP="$ROOT/dist/AudioRecorder.app"
APP="$ROOT/dist/MeetingAssistant.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/gui"
cp "$CLI_APP/Contents/MacOS/audio-recorder" "$APP/Contents/MacOS/audio-recorder"
cp "$BIN_DIR/meeting-assistant" "$APP/Contents/MacOS/meeting-assistant"
cp "$ROOT/scripts/transcribe.py" "$ROOT/scripts/live.py" "$APP/Contents/Resources/"
for name in meeting_summary.py meeting_core.py; do
    cp "$ROOT/gui/$name" "$APP/Contents/Resources/gui/$name"
done
cp "$ROOT/gui/macOS-Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/Resources/MeetingAssistant.icns" "$APP/Contents/Resources/MeetingAssistant.icns"
codesign --force --deep --sign - --timestamp=none "$APP"
echo "$APP"
