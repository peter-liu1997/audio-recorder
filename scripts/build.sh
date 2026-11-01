#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

mkdir -p .build-cache/clang .build-cache/xdg
export CLANG_MODULE_CACHE_PATH="$ROOT/.build-cache/clang"
export SWIFTPM_MODULECACHE_OVERRIDE="$ROOT/.build-cache/clang"
export XDG_CACHE_HOME="$ROOT/.build-cache/xdg"

# This Mac's default 26.5 SDK and command-line Swift toolchain are out of sync.
# The 15.4 SDK contains every API this macOS 15+ CLI uses.
if [ -n "${AUDIO_RECORDER_SDK:-}" ]; then
    SDK="$AUDIO_RECORDER_SDK"
elif [ -d /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk ]; then
    SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk
else
    SDK=$(xcrun --show-sdk-path)
fi

swift build --disable-sandbox --sdk "$SDK" -c release --product audio-recorder
BIN_DIR=$(swift build --disable-sandbox --sdk "$SDK" -c release --show-bin-path)
APP="$ROOT/dist/AudioRecorder.app"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"
cp "$BIN_DIR/audio-recorder" "$APP/Contents/MacOS/audio-recorder"
cp "$ROOT/Resources/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/scripts/transcribe.py" "$APP/Contents/Resources/transcribe.py"
cp "$ROOT/scripts/live.py" "$APP/Contents/Resources/live.py"
codesign --force --sign - --timestamp=none "$APP"
echo "$APP/Contents/MacOS/audio-recorder"
