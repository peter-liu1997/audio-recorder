# 国内会议设备兼容与设备内安装

更新：2026-10-31。此页区分 **运行平台、第三方应用安装许可、可获得的音频**。腾讯会议认证名单证明相应型号运行腾讯 Rooms，并不自动证明本应用已获该设备认证或可以侧载。具体型号、固件、管理模式仍需现场验收。

## 已交付的设备版

- `android/` 是原生 Android 应用，最低 Android 10（API 29），可在允许安装第三方 APK 的会议平板上试装。支持板载/USB 输入设备选择、前台麦克风录音、16/48 kHz 单声道 WAV、连续全零静音提醒、每 15 秒可选 Qwen3-ASR-Flash 中文转写、可选 Qwen-Plus 中文纪要，以及系统文件选择器导出录音和转写。云端接口与密钥由操作者填写；密钥只保留在本次应用进程中。转写按音频块完成，不是词级流式 ASR。
- 现有 `windows/` 与 `gui/` 可用于 Windows OPS，Windows 录音核心支持麦克风与 WASAPI 系统/进程回环。Windows GUI 当前需要 Python 3/Tkinter，必须在实际 OPS 的 Windows 版本、音频设备和会议软件上验证。该项目目前没有厂商签名、商店上架或设备厂商认证。
- 华为 HarmonyOS 主系统、封闭式 SIP/H.323 终端以及 Android 10 以下的型号尚无可直接安装的本项目原生包；可先用 Windows OPS、BYOD 主机或厂商 SDK 接入。

## 优先适配矩阵

| 设备族及例子 | 官方平台或安装证据 | 推荐落地路线 | 录取同机远端声音 |
| --- | --- | --- | --- |
| 亿联 Yealink MeetingBoard Pro | 官方明确：第三方 APK 安装仅 General Mode/BYOM，Teams/Zoom 模式无此功能；Pro Windows 可加 MCore OPS。 | General Mode 试装 Android APK；Windows OPS 用桌面版。 | Android 通用 API 无保证；Windows OPS 需要会议声音确实路由到该 Windows 系统。 |
| MAXHUB 会议平板及腾讯 Rooms 认证的 PG/CH 系列 | 腾讯认证名单列出多款 Windows 型号；MAXHUB Pivot Plus 官方资料支持批量安装/管理 Windows 和 Android 应用。 | Windows 型号优先 OPS/Windows 版；Android 型号经管理员开放安装后试装 APK。 | Windows 用回环实测；Android 需厂商开放混音或物理输入。 |
| 华为 IdeaHub Board 3 Base / Board 3 Pro | Base 官方列 Android、Windows OPS；Pro 为 HarmonyOS/Windows OPS，官方列 Device Management SDK、Meeting SDK 和音视频 API 类别。腾讯认证名单中的 IHB3 特定 Rooms 版本标 Android。 | Android 变体可试装 APK；HarmonyOS 主系统走厂商 SDK/伙伴接入或 Windows OPS。 | 需厂商验证具体会议混音 API；公开资料仅能证明 SDK 类别，不能证明第三方可直接读通话 PCM。 |
| 海信 MR6E、海康威视 DS-D5D、皓丽 M7G2、TCL NXTHUB 等 | 腾讯认证名单列有 Android 型号与固件。 | 逐台核对 Android API、侧载策略、输入设备和驻留策略后试装 APK。 | 未验证；认证名单不能作为录音能力证明。 |
| Windows 会议室主机、OPS、BYOD PC | 腾讯 Rooms 官方支持 Windows 安装；亿联 BYOD 可把会议板麦克风作为 PC 外设。 | 将桌面版装在实际运行会议的 Windows 主机；同时验证播放回环与麦克风轨。 | 条件性可行：音频须经过该 Windows 主机；外设本身不会把远端混音自动送入麦克风。 |

参考资料：[腾讯会议认证硬件名单](https://meeting.tencent.com/support/topic/994/)、[腾讯 Rooms 安装说明](https://meeting.tencent.com/support/topic/1931/)、[亿联 MeetingBoard Pro 第三方应用管理](https://support.yealink.com/docs/meetingboard-pro/application/004fc3c98f094db89eeab914a8d1747f)、[亿联 Windows OPS 设置](https://support.yealink.com/docs/meetingboard-pro-windows/quick-setup/c5c9116e83a3404994c970ad5b9a094a)、[亿联 BYOD 音视频外设](https://support.yealink.com/docs/meetingboard/wired-byod-mode/dedc237ce2744c3ba952600604cd2499)、[MAXHUB Pivot Plus](https://sgp-cstore-pub.maxhub.com/maxhub_global_public/upload/mljpe4sd/MAXHUB_Pivot_Plus_Brochure_20260131_v7.pdf)、[华为 Board 3 Base](https://ekit.huawei.com/ekit/front/ssr/en/product/1876264994965816278)、[华为 IdeaHub SDK 概览](https://e.huawei.com/marketingcloud/pep/asset/20000001/Material/b27ba3d8a6a94a1eac8dd33e6d5fd8df/M3T1A669N1170050532881821887/HUAWEI%20IdeaHub%20Series%20Datasheet--For%20Industry_Commercial%20Market.pdf)。

## 音频能力边界

1. Android 的普通应用播放采集只允许 `USAGE_UNKNOWN/GAME/MEDIA` 等用途，并要求来源应用允许采集。会议通话通常使用 `USAGE_VOICE_COMMUNICATION`，不能靠普通 MediaProjection 取得。参见 [Android AudioPlaybackCaptureConfiguration](https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration)。
2. Android 官方的麦克风共享规则说明两个普通应用不能同时取得麦克风音频；当会议应用正在使用麦克风，本应用可能获得静音。参见 [Android Sharing audio input](https://developer.android.com/media/platform/sharing-audio-input)。因此 **Android APK 现阶段是独立录制和接入测试版，不能承诺同机开会时完整录制双方声音**。
3. 设备内稳定录全会的优先路径：会议应用本身的合规录制/转写接口、设备厂商提供的可授权混音 PCM/媒体 SDK、会议音频物理输出到独立 USB 音频输入，或让会议运行在 Windows OPS 并使用同机回环。每种方式都应测远端、本地、共享内容以及静音状态。
4. Android 麦克风前台服务必须由可见界面发起并取得权限；当前应用采用这种方式。参见 [Android 前台服务限制](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start)。

## 试装和验收

```sh
cd android
./gradlew assembleDebug
python3 device_doctor.py --serial <ADB序列号>
adb -s <ADB序列号> install -r app/build/outputs/apk/debug/app-debug.apk
```

`device_doctor.py` 只读，输出 JSON；`apkMinSdkMet` 仅判断 Android 版本，`sideloadPolicy`、`meetingAppAudioCapture` 保持 `not_tested`。没有 ADB 的会议板可用厂商允许的 U 盘、网页或管理平台安装。调试 APK 供样机试装；正式部署需用组织证书签名、固化包名/升级策略，并让设备管理员纳管。

录音保存在应用专属的 `Android/data/cn.meetingassistant.device/files/Meetings/` 下；卸载应用前须先在界面中导出需要保留的文件。

每个型号至少做这 8 项验收：① 安装和升级；② 前台和后台持续录制；③ 会议软件占用麦克风时是否静音；④ 远端/本地双向音频是否都入轨；⑤ USB/Line-In 设备路由；⑥ 锁屏、待机、重启和 MDM 策略；⑦ 转写网络断线后 WAV 是否完整；⑧ 设备实际固件版本、Rooms 模式和厂商授权。只有通过第 ③④ 项的组合才能标为“会议双方声音可录”。

## 当前验证记录

- Android 15 / API 35 ARM64 模拟器：APK 安装、启动、纵屏/横屏 UI、麦克风权限、前台录音服务、停止保存和历史会议列表均已通过。
- 模拟器无真实音频输入，录得 WAV 为静音；录音流程触发了连续全零提示。正常结束后用 FFprobe 读取为 16 kHz、单声道、PCM s16le、34.75 秒。此结果只验证文件格式和流程。
- 线上 Qwen 调用请求结构参照阿里云 [Qwen-ASR 兼容接口文档](https://help.aliyun.com/en/model-studio/qwen-asr-api-reference) 实现，尚未用真实 API Key 验收。亿联、MAXHUB、华为及其他厂商实体设备尚未实装实录。

## 商业接入建议

初期覆盖面最大的是 Windows OPS 与允许侧载的 Android General Mode 设备。对 HarmonyOS/封闭会议终端，下一阶段应与华为、亿联、MAXHUB 分别谈 OEM/SDK、企业应用分发和音频流授权；这里需要设备厂商或会议平台的合作，不能只靠 APK 权限。市场覆盖优先级不能由腾讯认证名单推断成销量排名，需另查装机量与目标客户设备清单。
