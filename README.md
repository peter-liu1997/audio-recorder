# Audio Recorder · AI 会议助手与音频录制 CLI

**跨平台录音 · 中文实时转写 · AI 会议纪要 · Agent-friendly CLI**

从麦克风、系统声音到指定应用录音，把会议声音转成文字与纪要。提供原生桌面 GUI、自动化 CLI 和 Android 会议设备试装版。

`Audio recording` · `Meeting assistant` · `Real-time transcription` · `Speech-to-text / ASR`

## 主要能力

- **会议录音，多路声源**：采集麦克风、系统播放与指定应用声音，支持混音和独立音轨。
- **边录边转写，中文优先**：分段实时转写与文件转录；支持 Qwen3-ASR、SenseVoice、Whisper 等可选本地后端，以及线上 Qwen ASR。
- **AI 会议纪要**：根据转写文本生成中文摘要，支持本地 Ollama 或可选线上 Qwen，保留历史会议与导出文件。
- **为 AI Agent 和自动化准备**：JSON / JSONL 输出、明确错误码、后台录音会话，以及 `start / status / stop` 控制。
- **原生 GUI，多平台使用**：macOS 使用 SwiftUI，Windows / Linux 使用 Tkinter；Android 提供原生录音与会议助手应用。
- **本地处理，可选上云**：录音在本地保存；可选本地转写，显式启用线上后端时才发送对应音频或文本。

## 下载测试版

[查看全部构建产物与 SHA-256 校验文件](https://github.com/peter-liu1997/audio-recorder/releases/tag/v0.1.0-alpha.1)

| 平台 | 下载 | 说明 |
| --- | --- | --- |
| Windows x64 | [桌面与 CLI 测试包](https://github.com/peter-liu1997/audio-recorder/releases/download/v0.1.0-alpha.1/audio-recorder-windows-x64.zip) | Intel / AMD 电脑、OPS |
| Windows ARM64 | [桌面与 CLI 测试包](https://github.com/peter-liu1997/audio-recorder/releases/download/v0.1.0-alpha.1/audio-recorder-windows-arm64.zip) | Windows ARM 设备、ARM 虚机 |
| macOS Apple Silicon | [AI 会议助手 GUI](https://github.com/peter-liu1997/audio-recorder/releases/download/v0.1.0-alpha.1/meeting-assistant-macos-arm64.zip) · [录音 CLI](https://github.com/peter-liu1997/audio-recorder/releases/download/v0.1.0-alpha.1/audio-recorder-macos-arm64.zip) | macOS 15+，本机签名、未公证测试包 |
| Android | [会议助手 APK](https://github.com/peter-liu1997/audio-recorder/releases/download/v0.1.0-alpha.1/meeting-assistant-android-debug.apk) | Android 10+，调试签名试装版 |
| Linux | [CLI 与 GUI 脚本包](https://github.com/peter-liu1997/audio-recorder/releases/download/v0.1.0-alpha.1/audio-recorder-linux.tar.gz) | Python 3.10+，需配置音频依赖 |

Windows 录音 CLI 可直接运行；GUI 需要 Python 3 + Tkinter。转写需另装 FFmpeg、推理程序与模型，或配置线上接口。Windows 按应用录音要求系统 build 20348+。Android 会议设备的安装策略及通话音频采集能力需按型号验证。

## 快速使用

在 Windows 测试包目录打开 PowerShell：

```powershell
.\audio-recorder.exe devices --json
.\audio-recorder.exe record --source both --duration 60 --output meeting.wav --keep-tracks --json
.\audio-recorder.exe start --source system --output background.wav --json
.\audio-recorder.exe stop --json
```

Windows GUI：安装 Python 后双击 `meeting-assistant.cmd`。macOS 解压后打开 `MeetingAssistant.app`。Linux 解压后运行 `sh linux/install.sh`。

## 文档与进度

- [安装、构建、模型配置与完整 CLI 用法](docs/usage.md)
- [会议设备兼容与设备内安装](docs/meeting-device-compatibility.md)
- [Windows 功能测试报告](docs/windows-functional-test.md)
- [开发计划与验收标准](TODO.md)

当前为 Alpha 测试版：Windows ARM 虚机已通过 13 项录音 CLI 检查，Windows x64 CI 构建与冒烟测试通过。GUI、真实模型转写、实际会议软件及厂商会议设备的完整验收仍在进行。
