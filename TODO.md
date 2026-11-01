# 见声会议助手：后续待办

更新：2026-10-31。目标是交付可在桌面电脑与会议设备运行、能可靠获取会议双方声音、能用 CLI/GUI 录制和中文转写的产品。优先级按当前最大技术风险排序；勾选仅表示已有证据支持。

## 当前基线

- [x] macOS/Windows/Linux 录音 CLI 源码，以及统一 JSON 命令外壳；桌面 GUI、分段实时转写、本地/可选线上模型和纪要功能已有实现。**跨平台真实会议应用的双向录音尚未形成完整实机验收。**
- [x] Android 10+ 原生试装 APK：设备麦克风/USB 输入、WAV、可选每 15 秒云端转写与纪要、横屏 UI。Android 15 模拟器已验收安装和文件格式，厂商设备尚未实装。
- [x] 已整理 [设备安装和音频约束](docs/meeting-device-compatibility.md)。Android 普通应用不能承诺在同机会议中同时取得本地麦克风与远端通话音频。

## P0：先跑通完整会议闭环

先按 [虚拟机测试计划](docs/vm-validation.md)验证安装、CLI、GUI、音频文件与故障恢复；虚拟机通过后再做实体会议设备的双向声音验收。虚拟机测试期间最多运行一台、内存配置不超过 4 GB，不与 Android 模拟器并行。

| 编号 | 待办 | 完成标准 | 依赖 |
| --- | --- | --- | --- |
| P0-0 | 用 Android 模拟器、Windows VM、Linux VM 分别验证包安装、CLI/GUI、合成音频回环与录音文件；控制内存占用。 | VM 测试报告列明通过项、模拟声源及不能外推到实体会议板的结论；Windows VM 自动化先解决客户机代理问题。 | 虚拟化环境、客户机代理。 |
| P0-1 | 选定首批样机并记录型号、固件、系统、会议模式、安装策略；优先一台 Windows OPS 会议板和一台可安装 APK 的 Android 会议板。 | 样机表可复现安装路径，明确是否能取得设备管理员权限；不得把腾讯 Rooms 认证等同于本应用认证。 | 实体设备、管理员协助。 |
| P0-2 | 在 Windows OPS 上把桌面 GUI、CLI、Python 运行时及转录依赖打成可安装包，加入签名与升级/回滚方案。 | 干净 Windows OPS 不预装 Python 时可安装、启动、卸载；CLI `doctor` 和 GUI 可找到同一录音核心。 | Windows 构建环境、发布证书。 |
| P0-3 | 用真实会议验证 Windows 声音回环、麦克风与按应用录音；保留独立轨道。 | 30 分钟会议中分别核听本地与远端发言，验证静音、切换扬声器、共享内容声音；WAV、轨道、时间轴与转写都能回放核对。记录失败模式与设备路由。 | P0-1、P0-2。 |
| P0-4 | 在 Android 样机试装并测麦克风并发、USB/Line-In 路由、后台录制、锁屏和系统回收。 | 输出逐型号测试记录；若会议应用使录音静音，设备页和 GUI 显示不支持同机录会，不把此机型标为“双方声音可录”。 | P0-1、厂商允许侧载。 |
| P0-5 | 线上 Qwen ASR/纪要端到端验收，覆盖权限、超时、限流、断网与重试。 | 用真实账号和测试会议核对中文术语、段序和错误恢复；失败时完整 WAV 保留；API Key 不进入日志或会议文件。 | 测试账号、预算与网络。 |

**P0 发布门槛：**至少一个实际会议设备方案完成安装、30 分钟双向录音、中文转写、导出与恢复测试；明确列出已支持的型号/固件/会议模式。仅通过模拟器或合成音频不能过此门槛。

## P1：完善录音与 AI 体验

| 编号 | 待办 | 完成标准 |
| --- | --- | --- |
| P1-1 | 把“分段实时转写”与真正的流式 ASR 分开提供；为线上接入 Qwen-ASR-Realtime WebSocket，并保留本地分段模式。 | GUI 明确显示模式与延迟；断线重连后片段不重排、不重复；目标在合格网络中稳定显示部分结果。Qwen3-ASR-Realtime 官方当前不返回词级时间戳，需要句/词对齐时另选模型。 |
| P1-2 | 评估 Android 本地中文 ASR：Qwen3-ASR 0.6B、SenseVoice Small 等的设备内推理，按内存、速度、功耗选型。 | 在真实会议板上完成 30 分钟性能与中文准确率报告；设备不达标时界面只开放云端/导出模式。桌面已有本地模型调用，Android 仍缺此能力。 |
| P1-3 | 统一会话数据格式与跨设备迁移：WAV、独立音轨、事件 JSONL、转写、纪要和来源证据。 | 同一会议可从设备导出并在 Mac/Windows/Linux 桌面版继续转写或重做纪要；失败后可恢复，不覆盖旧版结果。 |
| P1-4 | 实机回归 macOS、Windows、Linux 的会议应用声音采集与 GUI；优化 Windows/Linux 原生窗口的视觉与触控布局。 | 每个平台有真实会议应用的双向音频样例、安装包、屏幕尺寸适配和回归记录。 |
| P1-5 | 强化 Agent CLI：固定 JSON Schema、`doctor/list/start/status/events/stop/export`、稳定错误码与可恢复会话。 | 用自动化脚本从发现设备到录音、转写和导出全程无需解析界面文字；中断后能查询最终状态。 |

## P2：设备生态与企业交付

| 编号 | 待办 | 完成标准 |
| --- | --- | --- |
| P2-1 | 与亿联、MAXHUB、华为分别确认第三方应用分发、驻留、设备音频混音/PCM 或会议录制接口的授权条件。 | 每家形成书面接口清单、可获得的音频轨道、支持机型/模式、签名与商业条款；没有接口证据前不承诺 Android/HarmonyOS 同机录全会。 |
| P2-2 | 在可行的厂商接口上开发独立适配器；HarmonyOS 版本单独评估，不假定 APK 通用。 | 每个适配器用目标固件完成双向声音、权限、升级和故障恢复验收。 |
| P2-3 | 做企业部署：正式签名、MDM/厂商管理平台分发、密钥托管、留存与删除策略、管理员日志。 | 可批量安装/升级/回滚；录音和转写的上传范围、保留时间及访问权限可配置、可审计。 |

## 商业与竞品并行任务

| 编号 | 待办 | 产出 |
| --- | --- | --- |
| B-1 | 基于同一组中文真实会议，比较 Hark、Amanu、OSTT 与腾讯会议等产品的录音来源、实时性、中文术语、部署方式和价格。 | 可追溯竞品矩阵；事实、体验判断、推断分别标注，不把开源项目的 README 当成实机证据。 |
| B-2 | 访谈目标客户与集成商，收集实际装机品牌/型号、会议平台、合规要求、预算和采购路径。 | 形成优先适配的设备清单及商机规模假设；认证硬件名单只用于抽样，不推断市场份额。 |
| B-3 | 定义两条产品线：桌面/OPS 自助版与厂商集成企业版；评估本地推理成本、云转写成本、设备合作分成。 | 价格与毛利测算、渠道/OEM 合作包、试点转正式部署条件。 |

## 接下来直接执行

1. 建立 `device-validation/` 逐机型记录模板，按 P0-1 收集样机信息和测试音频证据。
2. 完成 P0-2 的 Windows OPS 免 Python 安装包，再按 P0-3 在实际会议中测双向声音。
3. 用 Android 真机执行 P0-4，决定该设备走 APK、OPS、厂商接口还是外接音频输入。

## 本路线图的外部依据

- [Android 播放音频采集限制](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration)、[Android 麦克风并发规则](https://developer.android.com/media/platform/sharing-audio-input)：决定 Android 同机录会必须先验证音频路径。
- [亿联 MeetingBoard Pro 安装模式](https://support.yealink.com/docs/meetingboard-pro/application/004fc3c98f094db89eeab914a8d1747f)、[MAXHUB Pivot Plus](https://sgp-cstore-pub.maxhub.com/maxhub_global_public/upload/mljpe4sd/MAXHUB_Pivot_Plus_Brochure_20260131_v7.pdf)、[华为 IdeaHub SDK 概览](https://e.huawei.com/marketingcloud/pep/asset/20000001/Material/b27ba3d8a6a94a1eac8dd33e6d5fd8df/M3T1A669N1170050532881821887/HUAWEI%20IdeaHub%20Series%20Datasheet--For%20Industry_Commercial%20Market.pdf)：决定设备分发与厂商集成需分路线推进。
- [腾讯会议认证硬件名单](https://meeting.tencent.com/support/topic/994/)：用于样机候选，不代表本应用认证。[阿里云 Qwen-ASR-Realtime](https://help.aliyun.com/en/model-studio/qwen-asr-realtime-interaction-process)、[实时识别模型与时间戳说明](https://help.aliyun.com/en/model-studio/real-time-speech-recognition-user-guide)：用于流式转写方案选型。
