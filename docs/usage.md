# Audio Recorder CLI（macOS / Windows / Linux）

后续开发顺序和验收标准见 [TODO.md](../TODO.md)。

## 会议设备安装版

新增原生 Android 会议平板应用，源码在 [`android/`](../android/)，调试 APK 构建命令为 `cd android && ./gradlew assembleDebug`（Windows 用 `gradlew.bat`）。支持板载/USB 麦克风录音、可选 Qwen 中文分段实时转写和纪要，以及录音/转写导出。Windows OPS 可使用下方桌面版。具体型号、侧载政策和同机会议声音限制见 [国内会议设备兼容与设备内安装](meeting-device-compatibility.md)。Android 通用权限不能保证获取会议应用的远端通话音频；设备试装版尚未完成厂商实机认证。

原生命令行录音与转录工具，支持麦克风、系统播放声音，以及按运行中应用筛选的声音。输出 48 kHz、双声道、16-bit PCM WAV；无需虚拟声卡。macOS 版本使用 Swift 和 ScreenCaptureKit；Windows 版本使用 C++、WASAPI 与进程回环采集接口；Linux 版本使用 PulseAudio 兼容接口上的 `pactl`、`parec` 和 FFmpeg。`transcribe` 转录已有文件；`live` 在录音期间按时间片持续转写，均可选本地模型或显式启用的线上模型。

## AI 会议助手 GUI

会议助手使用原生桌面窗口：macOS 为 SwiftUI，Windows/Linux 为 Tkinter，不启动浏览器或 HTTP 服务。后台使用同一套录音与实时转写逻辑。可选择麦克风、系统声音或某个应用，查看逐段实时转写，录音结束后生成中文会议纪要；历史会议可回看，录音、转写和纪要可用系统默认程序打开。每场会议存于独立文件夹，包含 `audio.wav`、`transcript.txt`、`audio.live.jsonl`、`meeting.json`，生成纪要后还包含 `summary.md`。重复生成纪要会保存为 `summary-v2.md` 等新文件，不覆盖旧版。

Windows/Linux GUI 使用 Python 3 标准库中的 Tkinter；Linux 发行版通常需另装 `python3-tk`。macOS GUI 是 SwiftUI 应用，也通过 Python 3 执行实时转写与纪要脚本。**录音与转写模型仍需要按本页“构建”和“中文转录”安装。** 默认本地 Qwen3-ASR 需要选择已下载的 GGUF 文件并安装 `transcribe-cli`；也可切换本地 Whisper 或线上 Qwen ASR。默认纪要调用本机 Ollama 的 `qwen3:8b`，需先安装 Ollama 并拉取模型；可选择“暂不生成”，之后对历史会议手动生成或重试。只有选择线上 Qwen ASR 时会上传音频块；只有选择线上 Qwen 纪要时会发送转写文本。两者均需 `DASHSCOPE_API_KEY` 和对应 HTTPS 兼容端点。麦克风/系统标签只区分声源，不代表说话人身份；纪要是待核对草稿。

### macOS 启动

```sh
sh gui/build-macos.sh
open dist/MeetingAssistant.app
```

也可直接执行 `dist/MeetingAssistant.app/Contents/MacOS/meeting-assistant`。该应用使用 SwiftUI，不依赖 Tkinter；如需指定转写脚本使用的 Python，设置 `AUDIO_RECORDER_PYTHON`。首次采集会议应用或系统声音时，在“系统设置 → 隐私与安全性 → 屏幕与系统音频录制”中允许 **AI 会议助手**，然后重新打开应用；它与 CLI 的 AudioRecorder 应用包是两项独立权限。录音期间关闭窗口会请求停止并等待录音文件收尾，随后可在历史会议中手动生成纪要。

### Windows 启动

运行 `windows/build.ps1` 后，双击 `windows/build/Release/meeting-assistant.cmd`；需要带 Tkinter 的 Python 3 和系统的 `py -3` 命令，也可设置 `AUDIO_RECORDER_PYTHON` 指向 Python 可执行文件。构建目录须一起保留 `gui/`、`live.py`、`transcribe.py` 和 `audio-recorder.exe`。Windows GUI 尚未在 Windows 实机上验收。

### Linux 启动

```sh
sh linux/install.sh
~/.local/bin/meeting-assistant
```

自定义 `PREFIX` 时，启动 `$PREFIX/bin/meeting-assistant`。安装脚本会将 GUI 文件复制到 `$PREFIX/lib/audio-recorder/gui/`。Linux GUI 尚未在真实音频会话上验收。

## 构建

### macOS

```sh
cd audio-recorder
sh scripts/build.sh
```

构建结果为 `dist/AudioRecorder.app/Contents/MacOS/audio-recorder`。这是带有录音用途说明的本地应用包，首次运行录音时 macOS 会申请相应权限。重新构建后如果系统权限失效，请在“系统设置 → 隐私与安全性”中检查 AudioRecorder 的权限。

### Windows

在 Windows 上安装 Visual Studio 2022 的“使用 C++ 的桌面开发”工作负载和 Windows SDK，然后在 PowerShell 中运行：

```powershell
cd audio-recorder
powershell -ExecutionPolicy Bypass -File windows/build.ps1
```

构建结果为 `windows/build/Release/audio-recorder.exe`，同目录带有转录脚本 `transcribe.py`。构建脚本会运行不访问录音设备的 JSON 命令检查。Windows 11 支持麦克风、默认播放设备回环和按进程录音；按进程录音要求 Windows build 20348 或更高。较旧的 Windows 10 可用麦克风和默认播放设备回环，但不支持按应用筛选。Windows 端已在 macOS 交叉编译生成 x64、ARM64 测试包；ARM64 版已在 Windows ARM 虚机通过 13 项录音 CLI 功能检查，详见 [Windows 功能测试报告](windows-functional-test.md)。GUI、转写、实际会议软件及 x64 真机运行仍待验收。

macOS/Linux 也可使用解压后的 [LLVM-MinGW](https://github.com/mstorsjo/llvm-mingw/releases) 工具链交叉编译，无需启动 Windows 虚机：

```sh
python3 windows/cross-build.py --toolchain /path/to/llvm-mingw
```

默认依次生成 x64 和 ARM64 的 `dist/audio-recorder-windows-<架构>.zip`；也可用 `--arch x64` 或 `--arch arm64` 单独构建。包内包含 EXE、GUI、实时转写脚本、`smoke.ps1` 和记录编译器/校验值的 `build-info.json`。CLI 静态链接 C++ 运行库；GUI 仍需带 Tkinter 的 Python 3，转写需另配 FFmpeg 和模型/后端。在 Windows 解压后运行 `powershell -ExecutionPolicy Bypass -File .\smoke.ps1 -Exe .\audio-recorder.exe` 做无设备检查（需要 Python 3）。交叉编译成功不代表 Windows 音频采集已经验收。

### Linux

需要 Python 3.10+、`pactl`、`parec`、FFmpeg，以及可访问的 PulseAudio 或提供 PulseAudio 兼容接口的 PipeWire 会话。在常见发行版中，`pactl` 和 `parec` 通常由 PulseAudio 工具包提供；具体包名以发行版为准。直接运行或安装到 `~/.local/bin`：

```sh
python3 linux/audio-recorder doctor --json
sh linux/install.sh
~/.local/bin/audio-recorder doctor --json
```

`PREFIX=/custom/prefix sh linux/install.sh` 可指定安装目录。Linux 版是 Python CLI，无需编译；安装脚本会同时复制转录后端。将 `~/.local/bin` 加入 `PATH` 后可直接使用 `audio-recorder`。`doctor` 会报告录音依赖和声音服务器连接状态；若应用运行在容器或远程会话中，需要该会话能访问用户的音频服务器。

## 使用

```sh
CLI=./dist/AudioRecorder.app/Contents/MacOS/audio-recorder
$CLI doctor
$CLI devices
$CLI apps
$CLI record --source mic --duration 10 --output mic.wav
$CLI record --source system --app Zoom --duration 60 --output zoom.wav
$CLI record --source both --app com.microsoft.teams2 --output meeting.wav --keep-tracks
```

Windows 命令形式相同，只需将 `CLI` 改为 `windows/build/Release/audio-recorder.exe`。Windows 的 `--app` 使用 `apps` 命令列出的进程名或 PID，例如 `--app Teams.exe` 或 `--app 1234`；同名进程超过一个时请使用 PID。Windows 的 `--keep-tracks` 生成 `*.microphone.wav` 和 `*.system.wav`，macOS 则生成 CAF 轨。

Linux 命令形式也相同。`apps` 列出当前**正在播放声音的流**，`--app` 接受流 ID、进程 PID 或应用名；同一应用存在多个播放流时请选择流 ID。`system` 录默认输出设备的 monitor；`--app` 借助 PulseAudio 的 `parec --monitor-stream` 监听选定播放流，不会移动或静音应用。Linux `--keep-tracks` 生成独立的 `*.microphone.wav`、`*.system.wav`。

`--source` 可选 `mic`、`system`、`both`，默认 `both`。`--app` 的标识方式按平台而异，见上文。`--mic-device` 可指定 `devices` 列出的设备 ID。`--duration` 省略时按 Ctrl-C 结束。`--mic-gain`、`--system-gain` 可在 0～4 之间设置混音增益。`--keep-tracks` 额外保存独立音轨，文件格式按平台而异。输出文件已存在时会拒绝覆盖。

## Agent 调用

`start` 在后台启动录音并返回会话 ID；`status`、`list` 查询状态；`stop` 请求正常结束并等待 WAV 导出。除 `live` 外，命令加 `--json` 后标准输出只包含**一行 JSON**；`live --json` 在录音期间输出多行 JSONL 事件。macOS 首次录音可能弹出系统授权对话框；请在交互终端完成授权，再运行后台任务。

```sh
CLI=./dist/AudioRecorder.app/Contents/MacOS/audio-recorder
$CLI doctor --json
$CLI start --source both --app com.microsoft.teams2 --output /tmp/meeting.wav --json
$CLI status --id <start返回的UUID> --json
$CLI stop --id <start返回的UUID> --json
$CLI list --json
```

成功响应格式为 `{"schemaVersion":1,"ok":true,"command":"start","data":{"session":{...}}}`；失败为 `{"schemaVersion":1,"ok":false,"command":"start","error":{"code":"PERMISSION_REQUIRED","message":"..."}}`。会话包含 `id`、`state`、`pid`、`output`、`source`、`createdAt`、`startedAt`、`endedAt`、`durationSeconds`、`tracks`、`error`、`logPath`；尚无值的可选字段会省略。状态按 `starting → recording → stopping → done` 变化，故障时为 `failed`。`status` 不指定 ID 时返回活动会话或最近一次会话；没有会话时 `data.session` 为 `null`。`stop` 不指定 ID 时停止当前活动会话，重复停止已完成的指定会话可查询最终结果。

退出码：`0` 成功；`2` 参数错误；`3` 缺少授权；`4` 会话冲突或输出文件已存在；`5` 设备、应用、模型或依赖不存在；`6` 采集、转录、导出或停止超时；`7` 文件系统错误。失败时应检查 `error.code`，不要靠匹配英文报错。会话记录在 macOS 默认存于 `~/Library/Application Support/AudioRecorder/sessions`，在 Linux 默认存于 `~/.local/state/audio-recorder/sessions`（可用 `XDG_STATE_HOME` 更改）；可用 `AUDIO_RECORDER_STATE_DIR` 指定独立目录。音频输出与会话记录均用绝对路径，`logPath` 可用于诊断子进程故障。

## 实时转写

`live` 使用现有录音后端持续采集，在录音未结束时按默认 5 秒音频块调用 ASR。麦克风与系统声音默认分别转写，事件中的 `track` 为 `microphone` 或 `system`；这只是声源标记，不是说话人识别。`--track-mode mix` 可转写混合音频。`--chunk-seconds` 可设为 1～25 秒。块长加模型计算时间决定可见延迟，若模型速度低于录音速度，事件会积压；这不是词级流式解码。与 [Amanu 的独立直播转写和保留会话文件](https://github.com/gsamat/amanu) 类似，本工具保留最终 WAV、逐段文本和 JSONL 事件；模型与数据格式沿用现有转录后端。OSTT 的[命令模式](https://ostt.ai/guide/commands)可作 CLI 交互参考；其官方说明指出转写在录音结束后开始，并不提供录音中的部分文字。

```sh
CLI=./dist/AudioRecorder.app/Contents/MacOS/audio-recorder
$CLI live --source both --app Zoom --output meeting.wav \
  --backend local-gguf --model qwen3-asr-0.6b \
  --model-path /path/to/qwen3-asr-0.6b.gguf --chunk-seconds 5 --json
```

Linux 将 `CLI` 换成 `~/.local/bin/audio-recorder`；Windows 使用 `windows/build/Release/audio-recorder.exe`。所选本地模型和执行程序需按下文安装。`--backend qwen` 会把每个音频块上传到已配置的线上接口；其他后端在本地运行。省略 `--duration` 时按 Ctrl-C 正常结束。

`live --json` 每行都有 `schemaVersion:1`、`command:"live"`、`ok`、`event`。事件为 `started`、`segment`、`completed`，失败时为 `error`。`segment.data` 包含块起止时间、音轨、文本、模型和估计积压时长；时间戳是**音频块边界**，不代表词级时间。运行期间持续写出 `meeting.live.jsonl` 和 `meeting.transcript.txt`；完成后写出 `meeting.transcript.json`。转写中途失败时，已写出的文本与事件保留，原始 PCM 轨道保留在输出目录中的 `.audio-recorder-live-<UUID>` 目录。`live` 为前台事件流命令；现有 `start/status/stop` 管理的是纯录音会话。

## 中文转录

录音完成后，用 `transcribe` 转录已有音频文件。该命令不会自动上传录音；只有显式指定 `--backend qwen` 才会调用线上 API。Windows 和 Linux 上需要 Python 3；三个平台都需要 FFmpeg。模型和推理程序由用户独立安装，不随录音应用包分发。`AUDIO_RECORDER_PYTHON` 可在 macOS/Windows 指定 Python 可执行文件，`AUDIO_RECORDER_FFMPEG` 可指定 FFmpeg 路径。

### 本地 Whisper

安装 [whisper.cpp](https://github.com/ggml-org/whisper.cpp) 的 `whisper-cli`，并从其[模型说明](https://github.com/ggml-org/whisper.cpp/tree/master/models)获取**多语言** GGML 模型。支持 `small`、`medium`、`large-v3`、`large-v3-turbo`；默认 `medium`。模型文件默认从当前目录的 `models/ggml-<模型名>.bin` 读取，也可指定 `--model-path`。不要使用 `.en` 英语专用模型转录中文。

```sh
audio-recorder transcribe --input meeting.wav --backend local \
  --model large-v3 --model-path /path/to/ggml-large-v3.bin --language zh --json
```

### 本地 Qwen3-ASR / SenseVoice

安装 [transcribe.cpp](https://github.com/handy-computer/transcribe.cpp) 的 `transcribe-cli`，下载对应 GGUF 模型。支持 `qwen3-asr-0.6b`、`qwen3-asr-1.7b` 和 `sensevoice-small`；需要显式传入模型文件路径。`AUDIO_RECORDER_TRANSCRIBE_CLI` 可指定程序路径。SenseVoice 单次输入上限 30 秒，因此本工具按 25 秒切块；其他模型和线上 API 按 180 秒切块。详情见 [Qwen3-ASR GGUF](https://github.com/handy-computer/transcribe.cpp/blob/main/docs/models/qwen3-asr.md) 与 [SenseVoice GGUF](https://github.com/handy-computer/transcribe.cpp/blob/main/docs/models/sensevoice-small.md)。

```sh
audio-recorder transcribe --input meeting.wav --backend local-gguf \
  --model qwen3-asr-0.6b --model-path /path/to/qwen3-asr-0.6b-Q8_0.gguf --json
```

### 可选线上 Qwen ASR

在阿里云百炼获取 API Key 和工作空间 ID，配置工作空间专属的 OpenAI 兼容端点。`QWEN_ASR_API_URL` 必须是完整的 HTTPS `/chat/completions` 地址。音频先在本地转为 16 kHz 单声道，按 180 秒切块，以 Base64 Data URI 逐块发送。Qwen3-ASR-Flash 的[接口说明](https://help.aliyun.com/en/model-studio/qwen-asr-api-reference)给出 5 分钟时长和 10 MB 输入限制；切块用于满足这两个限制。

```sh
export DASHSCOPE_API_KEY=你的密钥
export QWEN_ASR_API_URL=https://你的工作空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
audio-recorder transcribe --input meeting.wav --backend qwen --model qwen3-asr-flash --json
```

Windows PowerShell 设置环境变量时使用 `$env:DASHSCOPE_API_KEY = "..."` 和 `$env:QWEN_ASR_API_URL = "..."`。密钥不写入命令行或转录文件。

成功后生成 `meeting.transcript.txt` 和 `meeting.transcript.json`；重复执行不会覆盖现有结果。JSON 响应延续 `schemaVersion: 1`，包含 `backend`、`model`、`uploaded`、`durationSeconds` 和逐块文本。时间戳仅为切块边界（`timestampPrecision: "chunk"`），不代表句子或词的准确起止时间；线上 Qwen3-ASR-Flash 的兼容接口本身也[不返回时间戳](https://help.aliyun.com/en/model-studio/qwen-asr-api-reference)。目前不进行说话人分离。混合音轨会转录为一份文本；若需分别处理麦克风和应用声音，录音时加 `--keep-tracks`，再分别传入对应轨道文件。

指定 `--duration` 时，最终 WAV 会裁剪到指定时长。macOS 加 `--keep-tracks` 时，原始 CAF 轨可能多保留停止过程中的少量音频；采集或导出失败时，CAF 文件会留在输出目录下的 `.audio-recorder-<ID>` 文件夹。Linux 失败时会在该文件夹中保留原始 PCM 轨和 `capture.log` 以供恢复。标准 WAV 有约 4 GiB 的数据上限，长时间录制前请估算文件大小。

系统与应用音频由 ScreenCaptureKit 采集；麦克风单独录制使用 AVFoundation，混合录制使用 ScreenCaptureKit 的麦克风输出。按应用筛选的是**该应用整个进程**的输出：例如选择浏览器时，可能一并录下其他标签页声音。部分受保护内容不会由系统提供可录音频。录音权限由 macOS 管理，首次授权后若系统要求重启应用，请重新运行命令。

Windows 的 `--source system` 采集**默认播放设备**的回环声音；输出到其他播放设备的声音可能不在其中。`--app` 使用进程回环，包含目标进程及其子进程的音频，且不依赖单个播放设备。Windows 录音权限由“设置 → 隐私和安全性 → 麦克风”控制，企业策略也可能限制桌面应用。`doctor` 只报告设备和系统版本，不能证明实际录音权限；需要用短时实录确认。

Linux 的应用筛选针对**一个当前播放流**，同一个会议应用的多个流需要分别录制。选择浏览器流时也可能包含该流内的其他标签页声音。麦克风和播放流由独立进程采集，混音按首个收到的音频缓冲区近似对齐，不保证样本级同步；有严格同步要求时请保留独立轨道并在后期校准。PipeWire 模式依赖 `pipewire-pulse` 兼容层。此版本已在 macOS 上用模拟 `pactl` / `parec` 和真实 FFmpeg 验证 CLI 与 WAV 处理，尚未在 Linux 音频服务器及真实会议应用上验收。

## 验证

```sh
swift run --disable-sandbox --sdk /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk audio-pipeline-smoke
```

该检查用合成音频验证重采样、时间轴间隙、重叠、右声道、定时裁剪及 WAV 混音，不访问实际麦克风或会议应用。

```sh
python3 scripts/check-agent-cli.py dist/AudioRecorder.app/Contents/MacOS/audio-recorder
```

该检查验证 JSON 响应、退出码、会话查询和失联子进程处理；不需要录音权限。

Windows 构建脚本还会运行 `windows/smoke.ps1`，验证 `doctor`、`list`、`status` 和 JSON 错误码。Windows 后台会话默认存于 `%LOCALAPPDATA%\AudioRecorder\sessions`，也支持 `AUDIO_RECORDER_STATE_DIR` 指定独立目录。Windows 与 macOS 使用相同的主要 Agent 命令和 `schemaVersion: 1` 响应外壳；平台相关的 `doctor` 字段与轨道文件格式有所区别。

转录集成检查：

```sh
PYTHONPYCACHEPREFIX=/tmp/audio-recorder-pycache python3 -m unittest discover -s Tests/TranscriptionTests -p 'test_*.py' -v
```

该检查用合成 WAV、真实 FFmpeg 和模拟推理程序/API 验证本地模型路由、线上请求结构、输出文件及 JSON；Linux 用模拟 `pactl` / `parec` 验证应用流选择、录音、后台会话和**录音结束前的转写事件**。Mac 音频管线检查也验证实时 PCM 轨道在写入时可读取。这些测试不代表真实模型精度、线上账户或 Linux/Windows 实机音频服务器已验收。

## API 依据

- Apple [ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit) 与 [应用过滤器](https://developer.apple.com/documentation/screencapturekit/sccontentfilter/init%28display%3Aincluding%3Aexceptingwindows%3A%29)
- Apple [ScreenCaptureKit 麦克风采集](https://developer.apple.com/videos/play/wwdc2024/10088/)
- Apple [麦克风访问说明](https://developer.apple.com/documentation/bundleresources/information-property-list/nsmicrophoneusagedescription) 与 [系统音频访问说明](https://developer.apple.com/documentation/bundleresources/information-property-list/nsaudiocaptureusagedescription)
- Microsoft [WASAPI 回环采集](https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording)、[应用进程回环示例](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) 与 [采集时间戳](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudiocaptureclient-getbuffer)
- PulseAudio [parec 录制参数](https://github.com/pulseaudio/pulseaudio/blob/master/src/utils/pacat.c)、[按播放流监听 API](https://freedesktop.org/software/pulseaudio/doxygen/stream_8h.html) 与 [pactl JSON](https://manpages.debian.org/unstable/pulseaudio-utils/pactl.1.en.html)
- PipeWire [`pipewire-pulse` 兼容层](https://docs.pipewire.org/page_man_pipewire-pulse_1.html)
