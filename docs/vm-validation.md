# 虚拟机先行测试计划

Windows 虚机的实际操作步骤见 [Windows 虚机运行与测试](windows-vm-runbook.md)。

更新：2026-10-31。虚拟机用于提前验证安装、命令、UI、音频文件与错误处理；设备厂商的音频路由、会议软件并发麦克风和远端通话混音，仍须在实体设备上验证。

## 本机资源与现状

- 主机：Apple Silicon，16 GB 内存。已发现 UTM 的 Windows ARM 虚拟机，配置 4 GB 内存；Android 15 ARM 模拟器已可用；Ubuntu 24.04 ARM 镜像已存在。
- Windows VM 于 2026-10-31 完成启动、账户登录和系统音频设备检查。Windows 版本为 `10.0.26200.9711`；播放设备为默认扬声器 `High Definition Audio Device`，录音设备为默认线路输入 `High Definition Audio Device`。设备存在不能证明宿主麦克风已经接通或 WAV 采集成功。
- 本轮 disposable 启动后虚机自动停止，随后通过 UTM 界面正常启动成功；始终只有一台虚机运行。4 GB 配置下，已观察到的宿主机最低空闲内存比例为 32%，未触发 20% 停止线。测试后已请求正常关机，状态为 `stopped`，无 QEMULauncher、qemu-system 或 Android emulator 进程；停止后空闲比例为 50%。
- 2026-11-01 已在 Windows ARM 虚机跑过 ARM64 包的 13 项录音 CLI 功能检查，修复后台录音继承输出管道的问题，并重新构建两种架构。GUI、转写、实际会议软件和 x64 真机验收仍未完成。详细证据见 [Windows 功能测试报告](windows-functional-test.md)。本轮测试后已关机、确认无虚机进程，主机空闲内存恢复到 56%。
- Android 模拟器已验证试装 APK、界面、录音服务和 WAV 文件结构；上次使用 `-no-audio`，录音内容是静音，不能证明真实麦克风效果。

## 资源护栏

1. 一次只启动一台 VM 或 Android 模拟器；Windows VM 内存上限 4 GB，Linux VM 初始不超过 2 GB。优先用 UTM disposable/快照或隔离的 Lima 实例，不改变现有 Windows VM 的持久磁盘。
2. 启动前与运行中检查 `memory_pressure -Q`，若空闲比例低于 20% 或出现明显持续换页，立即停止 VM。每次测试结束确认 `utmctl status` 和 QEMU 进程状态。
3. Windows VM 当前客户机代理不可用，先通过 UTM 界面确认系统状态；修复代理或建立共享目录之后再执行自动化测试。不要在这台 VM 中启动 Android 模拟器。

## 测试顺序和完成证据

| 环境 | 先测什么 | 证据 | 不能推出的结论 |
| --- | --- | --- | --- |
| Android 15 模拟器 | APK 安装、权限、横竖屏、录音开始/停止、WAV 头和导出、云端接口错误处理。若要测宿主麦克风，明确开启模拟器 Host Microphone Access。 | 安装结果、截图、WAV/FFprobe、日志。 | 厂商侧载、板载麦阵、USB/Line-In、同机会议双方声音。 |
| Windows ARM VM | 编译/安装 Windows 包，`doctor/list/start/status/stop` JSON，GUI、WASAPI 虚拟扬声器回环、合成播放音频与麦克风设备缺失处理。 | 构建日志、CLI JSON、虚拟声源 WAV、转写事件。 | Windows x64 OPS 的驱动兼容、厂商设备声卡路由和真实会议软件完整混音。 |
| Ubuntu ARM VM | 安装脚本、`pactl`/`parec`/FFmpeg、PipeWire/PulseAudio 兼容、虚拟 sink/source、分轨与实时事件。 | CLI JSON、音频流列表、WAV、事件 JSONL。 | 具体发行版、声卡、会议应用在实机上的音频路由。 |

**升级到真机验收的条件：** VM 中安装、录音文件、转写和故障恢复通过；随后在选定的亿联/MAXHUB/华为型号和 Windows OPS 上测试本地及远端双向发言、共享声音、静音、后台和升级。VM 成功不标记为“设备兼容”。

依据：[Android 模拟器麦克风默认关闭及开启方法](https://developer.android.com/studio/run/emulator-extended-controls)、[Android 播放采集限制](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration)、[Android 麦克风共享规则](https://developer.android.com/media/platform/sharing-audio-input)、[Microsoft Windows 11 ARM 虚拟机说明](https://learn.microsoft.com/en-gb/windows/arm/iso)。
