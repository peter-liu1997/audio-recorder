#!/usr/bin/env python3
"""Read-only, JSON-first Android meeting-device inventory via adb."""

import argparse
import json
import subprocess
import sys


PACKAGE = "cn.meetingassistant.device"


def adb(serial, *parts):
    command = ["adb"] + (["-s", serial] if serial else []) + list(parts)
    result = subprocess.run(command, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "adb failed")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="Inspect a connected Android meeting device")
    parser.add_argument("--serial", help="adb serial when more than one device is connected")
    args = parser.parse_args()
    devices = [line.split("\t", 1)[0] for line in adb(None, "devices").splitlines()[1:]
               if line.endswith("\tdevice")]
    if not args.serial and len(devices) != 1:
        raise RuntimeError(f"Expected one authorized adb device, found {len(devices)}; use --serial")
    serial = args.serial or devices[0]
    if serial not in devices:
        raise RuntimeError("Selected device is not connected and authorized")
    prop = lambda name: adb(serial, "shell", "getprop", name)
    sdk = int(prop("ro.build.version.sdk"))
    installed = ("package:" + PACKAGE) in adb(serial, "shell", "pm", "list", "packages", PACKAGE).splitlines()
    payload = {
        "schemaVersion": 1,
        "serial": serial,
        "manufacturer": prop("ro.product.manufacturer"),
        "model": prop("ro.product.model"),
        "androidRelease": prop("ro.build.version.release"),
        "androidSdk": sdk,
        "primaryAbi": prop("ro.product.cpu.abi"),
        "appInstalled": installed,
        "apkMinSdkMet": sdk >= 29,
        "sideloadPolicy": "not_tested",
        "meetingAppAudioCapture": "not_tested",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if sdk >= 29 else 5


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, FileNotFoundError) as error:
        print(json.dumps({"schemaVersion": 1, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(5)
