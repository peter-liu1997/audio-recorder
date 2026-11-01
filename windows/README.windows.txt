AI 会议助手 Windows 测试包

架构：x64 用于 Intel/AMD Windows PC、OPS；ARM64 用于 Windows ARM 设备和本机 UTM Windows ARM 虚机。
建议使用 Windows 11。按应用采集要求系统 build 20348 或更高。

解压整个目录，在此目录打开 PowerShell：
  .\audio-recorder.exe doctor --json
  .\audio-recorder.exe devices --json
  .\audio-recorder.exe apps --json
  .\audio-recorder.exe __selftest --json

在 Windows 内播放音频，同时录制默认播放设备：
  .\audio-recorder.exe record --source system --duration 10 --output test-system.wav --json
录制默认输入设备：
  .\audio-recorder.exe record --source mic --duration 10 --output test-mic.wav --json
输出文件已存在时会拒绝覆盖。请试听 WAV 确认实际内容。

GUI：安装带 Tkinter 的 Python 3.10+，双击 meeting-assistant.cmd。
GUI 使用原生 Tkinter 窗口。Python、FFmpeg、本地模型不包含在测试包内。
CLI 的录音功能无需 Python；转写、实时转写和 GUI 需要 Python。
如果系统没有 python 命令，可设置 AUDIO_RECORDER_PYTHON 为 Python 可执行文件的完整路径。

安装 Python 后运行无设备冒烟检查：
  powershell -ExecutionPolicy Bypass -File .\smoke.ps1 -Exe .\audio-recorder.exe

本包在 macOS 使用 LLVM-MinGW 交叉编译，C++ 运行库静态链接。
Windows 运行、音频采集、转写效果须另外验证；构建成功不能视为录音验收。
编译器版本和文件校验值见 build-info.json；第三方许可见 licenses/。

目录中的 README.md 提供模型、线上后端、实时转写等详细配置说明。
