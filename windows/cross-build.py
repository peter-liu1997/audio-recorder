#!/usr/bin/env python3
"""Build Windows CLI packages on macOS/Linux using an unpacked LLVM-MinGW."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", required=True, type=Path,
                        help="Root of an unpacked LLVM-MinGW toolchain")
    parser.add_argument("--arch", choices=("x64", "arm64", "all"), default="all")
    args = parser.parse_args()
    args.toolchain = args.toolchain.expanduser().resolve()
    root = Path(__file__).resolve().parent.parent
    version = subprocess.check_output(
        [str(args.toolchain / "bin/clang"), "--version"], text=True).splitlines()[0]
    targets = {"x64": "x86_64", "arm64": "aarch64"}
    for arch, target in targets.items():
        if args.arch not in (arch, "all"):
            continue
        output = root / "dist" / f"audio-recorder-windows-{arch}"
        output.mkdir(parents=True, exist_ok=True)
        exe = output / "audio-recorder.exe"
        command = [str(args.toolchain / f"bin/{target}-w64-mingw32-clang++"),
                   "-std=c++17", "-O2", "-Wall", "-Wextra", "-municode", "-static",
                   "-DUNICODE", "-D_UNICODE", "-DWIN32_LEAN_AND_MEAN", "-DNOMINMAX",
                   "-D_WIN32_WINNT=0x0A00", str(root / "windows/windows_recorder.cpp"),
                   "-o", str(exe), "-lole32", "-luuid", "-lmmdevapi", "-lpropsys"]
        print(f"Building Windows {arch} with {version}", flush=True)
        subprocess.run(command, check=True)
        for name in ("transcribe.py", "live.py"):
            shutil.copy2(root / "scripts" / name, output / name)
        (output / "gui").mkdir(exist_ok=True)
        for name in ("meeting_assistant.py", "meeting_service.py", "meeting_core.py"):
            shutil.copy2(root / "gui" / name, output / "gui" / name)
        for name in ("meeting-assistant.cmd", "smoke.ps1"):
            shutil.copy2(root / "windows" / name, output / name)
        shutil.copy2(root / "README.md", output / "README.md")
        shutil.copy2(root / "windows/README.windows.txt", output / "README.windows.txt")
        shutil.copytree(root / "windows/licenses", output / "licenses", dirs_exist_ok=True)
        shutil.copy2(args.toolchain / "LICENSE.TXT", output / "licenses/LLVM-LICENSE.TXT")
        manifest = {
            "architecture": arch, "compiler": version,
            "sourceSha256": hashlib.sha256((root / "windows/windows_recorder.cpp").read_bytes()).hexdigest(),
            "loopbackHeaderSha256": hashlib.sha256((root / "windows/loopback_compat.h").read_bytes()).hexdigest(),
            "exeSha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
            "validation": "Cross-compiled; Windows runtime tests must be run separately.",
            "dependencies": "GUI: Python 3 with Tkinter. Transcription: FFmpeg and configured model/backend."
        }
        (output / "build-info.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive = output.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for item in sorted(output.rglob("*")):
                if item.is_file():
                    package.write(item, item.relative_to(output.parent))
        print(f"Built {archive}", flush=True)


if __name__ == "__main__":
    main()
