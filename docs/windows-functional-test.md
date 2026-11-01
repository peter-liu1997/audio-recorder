# Windows ARM 虚机功能测试

日期：2026-11-01。环境为现有 UTM Windows ARM 虚机，4 GB 内存，Windows build 26200，PowerShell 5.1，测试 ARM64 交叉编译包。仅启动这台虚机一次；在同一次启动中修复问题并回归。

## 结果

录音 CLI 13 项检查全部通过。原始结果见 [windows-functional-results.json](windows-functional-results.json)。GUI 依赖检查仅收集环境信息，不计入功能通过项。

| 检查 | 结果与证据 |
| --- | --- |
| doctor / devices / apps | 均返回可解析的单行 JSON、schemaVersion 1；枚举到播放和输入设备 |
| 合成混音 | `__selftest` 返回 audioMix passed |
| 无效参数 | 返回退出码 2、INVALID_ARGUMENT |
| 隔离会话目录 | list 为空，status 无当前会话 |
| 无会话停止 | 返回退出码 4、NO_ACTIVE_SESSION |
| 系统回环 | 录制 3 秒 WAV，48 kHz、双声道、PCM16，采集到测试音频 |
| 应用回环 | 以播放测试音频的 PowerShell PID 为目标，录制 3 秒 WAV，采集到测试音频 |
| 默认输入采集 | 录制 2 秒 WAV，存在非零采样；输入为虚拟声卡线路输入，未验证物理麦克风语音质量 |
| 混音及独立音轨 | 生成 2 秒混音、microphone 与 system 三个 WAV，均通过结构/时长检查，混音与系统轨包含测试音频 |
| 后台启动/状态/停止 | PowerShell 捕获 start 输出后可继续执行；状态 recording，停止后 done，生成约 2.14 秒非静音 WAV |
| 拒绝覆盖文件 | 返回退出码 4，保留原录音 |

## 发现并修复的问题

后台录音创建子进程时曾继承 PowerShell 的 stdout 管道。即使 start 已退出，调用方仍等不到 EOF，导致 Agent 或 PowerShell 的输出捕获一直等待。

现已使用 STARTUPINFOEX 和 PROC_THREAD_ATTRIBUTE_HANDLE_LIST，只传递日志与 NUL 输入句柄。修复后重新构建 x64、ARM64，使用修复版 ARM64 完整跑过上述检查。修复前因手动停止旧进程而产生的失败结果不计入通过结果。

测试脚本初版还曾将函数命名为 Cli，与 PowerShell Clear-Item 的别名冲突；已改为 Invoke-Recorder。该脚本错误发生在实际产品调用之前。

## 尚未验收

- GUI：虚机没有 py 启动器，python 指向 WindowsApps 商店占位入口。尝试从 Python 官方下载测试运行环境，连接两次超时，未能启动 Tkinter GUI。
- 转写及实时转写：未具备 Python、FFmpeg 和本地模型/线上凭据，未验证真实模型与线上接口。
- 会议软件：尚未使用腾讯会议、飞书、Teams 等实际会议软件进行双方发言、共享声音和应用隔离测试。
- x64：已重新构建，尚未在 Windows x64 真机上运行；不能从 ARM64 结果推导 OPS 驱动兼容。
- 物理会议设备、麦克风阵列、长时间录制、音画时钟漂移与中断恢复仍待真机验收。

## 内存与收尾

启动前主机空闲内存比例 46%，运行期间观察到最低 34%，未达到 20% 停止线。已请求正常关机，确认状态为 stopped，无 QEMULauncher、qemu-system 或 Android emulator 进程；关机后主机空闲内存比例恢复到 56%。临时结果回传服务也已停止。
