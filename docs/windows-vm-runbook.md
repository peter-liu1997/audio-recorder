# Windows 虚机运行与测试

当前本机为 Apple Silicon、16 GB 内存；现有 UTM `Windows` 虚机为 Windows ARM、4 GB 内存，已停止。Windows 虚机尚未完成程序实测。以下命令在 macOS 终端运行，PowerShell 命令在虚机内运行。

## 1. 控制宿主机内存

先退出 Android 模拟器和其他虚机，再检查：

```sh
memory_pressure -Q
/Applications/UTM.app/Contents/MacOS/utmctl status Windows
```

保持 UTM 虚机配置为 4 GB，不在虚机内安装 Visual Studio 或运行 Android 模拟器。优先在 UTM 界面启动，以便观察 Windows 桌面与音频设备。若只做一次性测试，可用：

```sh
/Applications/UTM.app/Contents/MacOS/utmctl start --disposable Windows
```

测试中定期运行 `memory_pressure -Q`；空闲比例低于 20% 或持续换页时结束测试。结束时先在 Windows 中正常关机；必要时从宿主机请求关机并核对状态：

```sh
/Applications/UTM.app/Contents/MacOS/utmctl stop Windows --request
/Applications/UTM.app/Contents/MacOS/utmctl status Windows
```

`--disposable` 会丢弃本次虚机内安装与配置。需要保留 Guest Tools、Python 或测试软件时，应先为现有 VM 建快照，再正常启动。

## 2. 准备程序并送入虚机

2026-11-01 已生成交叉编译测试包：`dist/audio-recorder-windows-arm64.zip`（现有 Windows ARM 虚机优先使用）、`dist/audio-recorder-windows-x64.zip`（Intel/AMD PC 和 OPS）。包内含 EXE、GUI、脚本、冒烟检查和构建信息；无需在虚机中安装 Visual Studio 即可开始运行测试。交叉编译不替代 Windows 运行验收。

在虚机中运行 `winver`，确认系统版本。项目当前生成 x64 可执行文件；Windows 11 ARM 可通过 x64 模拟运行。4 GB VM 建议只运行程序，在 Windows x64 构建机或 GitHub Actions 的 `Windows build` 工作流中执行 `windows/build.ps1`。该脚本会先运行无设备冒烟测试，然后生成 `windows/build/Release/`。CI 上传整个 `Release` 目录，包含 CLI、GUI 启动脚本及 Python 源码。工作流需要仓库已推送并成功执行；当前项目目录本身不是 Git 仓库，不能假定已有可下载产物。

把整个 `Release` 目录复制到虚机，例如 `C:\audio-recorder\Release`。可用 UTM 文件共享（需 Windows Guest Tools）、USB 或虚机内下载。当前虚机的 QEMU Guest Agent 未运行，故 `utmctl exec` 暂不可用；这不妨碍在虚机桌面手动运行程序。若需要共享目录或宿主机自动执行，在 UTM 窗口选择“Install Windows Guest Tools…”，在 Windows 虚拟光驱中运行安装程序并重启。一次性启动下安装不会持久保留。

## 3. 在 Windows 中测试

打开 PowerShell：

```powershell
cd C:\audio-recorder\Release
.\audio-recorder.exe doctor --json
.\audio-recorder.exe devices --json
.\audio-recorder.exe apps --json
.\audio-recorder.exe __selftest --json
```

`doctor` 与 `__selftest` 仅检查环境及合成混音，不能证明真实设备采集。若复制了完整项目源码，还可运行自动检查：

```powershell
powershell -ExecutionPolicy Bypass -File C:\audio-recorder\windows\smoke.ps1 -Exe C:\audio-recorder\Release\audio-recorder.exe
```

确保 Windows 默认输出设备是 UTM 虚拟声卡，在虚机内播放一段可听音频，同时执行：

```powershell
.\audio-recorder.exe record --source system --duration 10 --output "$env:TEMP\vm-system.wav" --json
Get-Item "$env:TEMP\vm-system.wav" | Select-Object FullName,Length
```

播放应用出现在 `apps` 中时，按其列出的进程名或 PID 测按应用采集：

```powershell
.\audio-recorder.exe record --source system --app 1234 --duration 10 --output "$env:TEMP\vm-app.wav" --json
```

把 `1234` 替换为实际 PID。分别试听 WAV，确认长度、采样内容及无明显失真。麦克风测试必须先确认 `devices` 列出可用输入设备，并在 Windows 隐私设置中允许桌面应用访问麦克风；UTM 虚机没有输入设备时，麦克风失败属于环境限制，不算产品通过或失败。

GUI 测试在虚机中安装带 Tkinter 的 Python 3 后运行 `meeting-assistant.cmd`，观察设备选择、开始/停止、历史记录和导出。FFmpeg、转写执行程序与模型需单独安装；可先验证录音，再测本地或线上转写，避免把缺失依赖误判为录音故障。

## 4. 验收边界

记录 Windows 版本、CLI JSON、WAV 时长/试听结果、GUI 截图和宿主机内存变化。Windows ARM 虚机中的 x64 模拟与虚拟声卡测试，不能替代 Windows x64 OPS 和具体会议设备的声卡、驱动、麦克风阵列及会议软件实测。

参考：[UTM Windows Guest Tools](https://docs.getutm.app/guest-support/windows/)、[UTM 目录共享](https://docs.getutm.app/guest-support/sharing/directory/)、[Microsoft Windows on Arm x64 模拟](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation)。
