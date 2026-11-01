import AppKit
import Darwin
import Foundation
import SwiftUI

private struct Meeting: Identifiable, Hashable, Sendable {
    let id: String
    let title: String
    let createdAt: String
    let state: String
    let folder: String
    let audio: String
    let transcript: String
    let events: String
    let summary: String?

    init?(_ value: [String: Any]) {
        guard let id = value["id"] as? String,
              let title = value["title"] as? String,
              let createdAt = value["createdAt"] as? String,
              let state = value["state"] as? String,
              let folder = value["folder"] as? String,
              let audio = value["audio"] as? String,
              let transcript = value["transcript"] as? String,
              let events = value["events"] as? String else { return nil }
        self.id = id
        self.title = title
        self.createdAt = createdAt
        self.state = state
        self.folder = folder
        self.audio = audio
        self.transcript = transcript
        self.events = events
        self.summary = value["summary"] as? String
    }
}

@MainActor
private final class MeetingModel: ObservableObject {
    @Published var title = "项目会议"
    @Published var root = (NSHomeDirectory() as NSString).appendingPathComponent("Recordings/Audio Recorder")
    @Published var source = "both"
    @Published var application = ""
    @Published var manualApplication = ""
    @Published var micDevice = ""
    @Published var asrBackend = "local-gguf"
    @Published var asrModel = "qwen3-asr-0.6b"
    @Published var asrModelPath = ""
    @Published var asrURL = ProcessInfo.processInfo.environment["QWEN_ASR_API_URL"] ?? ""
    @Published var language = "zh"
    @Published var chunkSeconds = "5"
    @Published var summaryBackend = "ollama"
    @Published var summaryModel = "qwen3:8b"
    @Published var summaryURL = "http://127.0.0.1:11434/api/chat"
    @Published var devices: [(String, String)] = []
    @Published var applications: [(String, String)] = []
    @Published var meetings: [Meeting] = []
    @Published var selectedID: String?
    @Published var currentID: String?
    @Published var transcriptText = "等待实时转写…"
    @Published var summaryText = "录音结束后可生成会议纪要。"
    @Published var status = "就绪"
    @Published var state = "待命"
    @Published var isRecording = false
    @Published var isSummarizing = false
    @Published var elapsed = "00:00"

    private var recordingProcess: Process?
    private var summaryProcess: Process?
    private var currentFolder: URL?
    private var stdoutBuffer = Data()
    private var startedAt: Date?
    private let fileManager = FileManager.default

    var selected: Meeting? { meetings.first { $0.id == selectedID } }
    var current: Meeting? { meetings.first { $0.id == currentID } }

    init() {
        refreshMeetings()
        refreshSources()
    }

    private var cli: URL {
        Bundle.main.executableURL!.deletingLastPathComponent().appendingPathComponent("audio-recorder")
    }

    private var resources: URL { Bundle.main.resourceURL! }

    private var python: String { ProcessInfo.processInfo.environment["AUDIO_RECORDER_PYTHON"] ?? "python3" }

    func chooseRoot() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        if panel.runModal() == .OK, let url = panel.url {
            root = url.path
            refreshMeetings()
            selectedID = nil
            transcriptText = "等待实时转写…"
            summaryText = "录音结束后可生成会议纪要。"
        }
    }

    func chooseModel() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url { asrModelPath = url.path }
    }

    func asrChanged() {
        asrModel = asrBackend == "local-gguf" ? "qwen3-asr-0.6b" :
                   asrBackend == "local" ? "medium" : "qwen3-asr-flash"
    }

    func summaryChanged() {
        if summaryBackend == "ollama" {
            summaryModel = "qwen3:8b"
            summaryURL = "http://127.0.0.1:11434/api/chat"
        } else if summaryBackend == "qwen" {
            summaryModel = "qwen-plus"
            summaryURL = ProcessInfo.processInfo.environment["QWEN_SUMMARY_API_URL"] ?? asrURL
        }
    }

    private func runJSON(_ command: String) throws -> [String: Any] {
        let process = Process()
        process.executableURL = cli
        process.arguments = [command, "--json"]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()
        try process.run()
        let bytes = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard let outer = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
            throw NSError(domain: "MeetingAssistant", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "查询\(command)没有返回有效 JSON"])
        }
        guard outer["ok"] as? Bool == true, let data = outer["data"] as? [String: Any] else {
            let detail = outer["error"] as? [String: Any] ?? [:]
            let explanation = detail["code"] as? String == "PERMISSION_REQUIRED"
                ? "请在系统设置 → 隐私与安全性 → 屏幕与系统音频录制中允许 AI 会议助手，然后重新打开应用"
                : (detail["message"] as? String ?? "查询\(command)失败")
            throw NSError(domain: "MeetingAssistant", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: explanation])
        }
        return data
    }

    func refreshSources() {
        guard fileManager.isExecutableFile(atPath: cli.path) else {
            status = "未找到 audio-recorder 可执行文件"
            return
        }
        do {
            let deviceData = try runJSON("devices")
            devices = (deviceData["devices"] as? [[String: Any]] ?? []).compactMap { item in
                guard let id = item["id"] as? String else { return nil }
                return (id, "\(item["name"] as? String ?? id) · \(id)")
            }
        } catch {
            status = "麦克风查询失败：\(error.localizedDescription)"
        }
        do {
            let appData = try runJSON("apps")
            applications = (appData["apps"] as? [[String: Any]] ?? []).compactMap { item in
                let id = item["id"] as? String ?? (item["pid"] as? NSNumber)?.stringValue
                guard let id else { return nil }
                return (id, "\(item["name"] as? String ?? "应用") · \(id)")
            }
            status = "发现 \(devices.count) 个麦克风、\(applications.count) 个可选应用"
        } catch {
            status = "应用列表查询失败：\(error.localizedDescription)；可手动输入应用名或 PID"
        }
    }

    func refreshMeetings() {
        let folder = URL(fileURLWithPath: (root as NSString).expandingTildeInPath, isDirectory: true)
        let children = (try? fileManager.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        meetings = children.compactMap { child -> Meeting? in
            let manifest = child.appendingPathComponent("meeting.json")
            guard let bytes = try? Data(contentsOf: manifest),
                  let value = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                  value["folder"] as? String == child.path else { return nil }
            return Meeting(value)
        }.sorted { $0.createdAt > $1.createdAt }
    }

    func select(_ meeting: Meeting) {
        selectedID = meeting.id
        transcriptText = (try? String(contentsOfFile: meeting.transcript, encoding: .utf8)) ?? "等待实时转写…"
        summaryText = meeting.summary.flatMap { try? String(contentsOfFile: $0, encoding: .utf8) }
            ?? "录音结束后可生成会议纪要。"
    }

    private func saveManifest(_ folder: URL, changes: [String: Any]) {
        let path = folder.appendingPathComponent("meeting.json")
        guard let original = try? Data(contentsOf: path),
              var item = try? JSONSerialization.jsonObject(with: original) as? [String: Any] else { return }
        for (key, value) in changes { item[key] = value }
        if let bytes = try? JSONSerialization.data(withJSONObject: item, options: [.prettyPrinted, .sortedKeys]) {
            try? bytes.write(to: path, options: .atomic)
        }
        refreshMeetings()
    }

    func start() {
        guard !isRecording else { return }
        guard !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { status = "请填写会议名称"; return }
        guard let chunk = Double(chunkSeconds), (1...25).contains(chunk) else { status = "分块时长需为 1～25 秒"; return }
        if asrBackend == "local-gguf" && !fileManager.fileExists(atPath: asrModelPath) {
            status = "请选择已下载的 Qwen3-ASR / SenseVoice 模型文件"
            return
        }
        if asrBackend == "qwen" && asrURL.isEmpty { status = "请填写线上 ASR 地址"; return }
        guard fileManager.isExecutableFile(atPath: cli.path) else { status = "未找到录音 CLI"; return }
        let base = URL(fileURLWithPath: (root as NSString).expandingTildeInPath, isDirectory: true)
        do {
            try fileManager.createDirectory(at: base, withIntermediateDirectories: true)
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd_HHmmss"
            let clean = String(title.prefix(40)).replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
            let stem = "\(formatter.string(from: Date()))_\(clean)"
            var folder = base.appendingPathComponent(stem, isDirectory: true)
            var suffix = 2
            while fileManager.fileExists(atPath: folder.path) {
                folder = base.appendingPathComponent("\(stem)_\(suffix)", isDirectory: true)
                suffix += 1
            }
            try fileManager.createDirectory(at: folder, withIntermediateDirectories: false,
                                            attributes: [.posixPermissions: 0o700])
            let audio = folder.appendingPathComponent("audio.wav")
            let transcript = folder.appendingPathComponent("transcript.txt")
            let events = folder.appendingPathComponent("audio.live.jsonl")
            let item: [String: Any] = ["schemaVersion": 1, "id": folder.lastPathComponent, "title": title,
                                        "createdAt": ISO8601DateFormatter().string(from: Date()), "state": "starting",
                                        "folder": folder.path, "audio": audio.path, "transcript": transcript.path,
                                        "events": events.path, "summary": NSNull(), "source": source,
                                        "app": manualApplication.isEmpty ? application : manualApplication,
                                        "asrBackend": asrBackend, "asrModel": asrModel,
                                        "summaryBackend": summaryBackend, "summaryModel": summaryModel]
            let encoded = try JSONSerialization.data(withJSONObject: item, options: [.prettyPrinted, .sortedKeys])
            try encoded.write(to: folder.appendingPathComponent("meeting.json"), options: .atomic)
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            var arguments = [python, resources.appendingPathComponent("live.py").path,
                             "--recorder-cli", cli.path, "--source", source,
                             "--output", audio.path, "--transcript", transcript.path,
                             "--backend", asrBackend, "--model", asrModel,
                             "--language", language, "--chunk-seconds", String(chunk), "--json"]
            let app = manualApplication.isEmpty ? application : manualApplication
            if !app.isEmpty { arguments += ["--app", app] }
            if !micDevice.isEmpty { arguments += ["--mic-device", micDevice] }
            if !asrModelPath.isEmpty { arguments += ["--model-path", asrModelPath] }
            if asrBackend == "qwen" { arguments += ["--api-url", asrURL] }
            process.arguments = arguments
            let out = Pipe()
            let err = Pipe()
            process.standardOutput = out
            process.standardError = err
            stdoutBuffer = Data()
            out.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let bytes = handle.availableData
                guard !bytes.isEmpty else { handle.readabilityHandler = nil; return }
                Task { @MainActor [weak self] in self?.consume(bytes) }
            }
            err.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let bytes = handle.availableData
                guard !bytes.isEmpty else { handle.readabilityHandler = nil; return }
                let message = String(decoding: bytes, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                if !message.isEmpty { Task { @MainActor [weak self] in self?.status = message } }
            }
            let folderPath = folder.path
            process.terminationHandler = { [weak self] finished in
                let code = finished.terminationStatus
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    self.isRecording = false
                    self.recordingProcess = nil
                    if let current = self.current, ["starting", "recording", "stopping"].contains(current.state) {
                        self.saveManifest(URL(fileURLWithPath: folderPath),
                                          changes: ["state": "failed", "error": "录音进程退出，状态码 \(code)"])
                        self.state = "失败"
                    }
                    self.refreshMeetings()
                }
            }
            try process.run()
            recordingProcess = process
            currentFolder = folder
            currentID = folder.lastPathComponent
            selectedID = currentID
            transcriptText = "等待实时转写…"
            summaryText = "录音结束后可生成会议纪要。"
            startedAt = Date()
            isRecording = true
            state = "启动中"
            status = "会议录音正在启动…"
            refreshMeetings()
        } catch {
            status = "启动失败：\(error.localizedDescription)"
        }
    }

    private func consume(_ bytes: Data) {
        stdoutBuffer.append(bytes)
        while let newline = stdoutBuffer.firstIndex(of: 10) {
            let line = Data(stdoutBuffer[..<newline])
            stdoutBuffer.removeSubrange(...newline)
            guard let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            handleEvent(event)
        }
    }

    private func handleEvent(_ event: [String: Any]) {
        guard let kind = event["event"] as? String, let folder = currentFolder else { return }
        let data = event["data"] as? [String: Any] ?? [:]
        switch kind {
        case "started":
            state = "录制中"
            status = "录音与实时转写已开始"
            saveManifest(folder, changes: ["state": "recording"])
        case "segment":
            if selectedID == currentID {
                if transcriptText == "等待实时转写…" { transcriptText = "" }
                let begin = (data["startSeconds"] as? NSNumber)?.doubleValue ?? 0
                let end = (data["endSeconds"] as? NSNumber)?.doubleValue ?? 0
                let track = data["track"] as? String ?? "mixed"
                let label = track == "microphone" ? "我方麦克风" : track == "system" ? "会议声音" : "混合音轨"
                transcriptText += String(format: "[%.1f-%.1f] [%@] %@\n", begin, end, label,
                                         data["text"] as? String ?? "")
            }
        case "completed":
            let duration = (data["durationSeconds"] as? NSNumber)?.doubleValue ?? 0
            saveManifest(folder, changes: ["state": "recorded", "durationSeconds": duration])
            isRecording = false
            state = "已录制"
            status = "录音和转写已完成"
            if let current = current { select(current) }
            if summaryBackend == "none" {
                saveManifest(folder, changes: ["state": "done"])
                state = "已完成"
            } else {
                summarize(meetingID: currentID)
            }
        case "error":
            let detail = event["error"] as? [String: Any] ?? [:]
            let message = detail["message"] as? String ?? "录音或转写失败"
            saveManifest(folder, changes: ["state": "failed", "error": message])
            isRecording = false
            state = "失败"
            status = message
        default: break
        }
    }

    func stop() {
        guard let process = recordingProcess, process.isRunning else { return }
        Darwin.kill(process.processIdentifier, SIGINT)
        state = "正在保存"
        status = "正在结束录音并处理剩余音频…"
        if let currentFolder { saveManifest(currentFolder, changes: ["state": "stopping"]) }
    }

    func summarize(meetingID: String? = nil) {
        guard !isSummarizing, summaryBackend != "none" else { return }
        guard let target = meetings.first(where: { $0.id == (meetingID ?? selectedID) }) else {
            status = "请先选择一场会议"
            return
        }
        guard !["starting", "recording", "stopping"].contains(target.state) else {
            status = "请在录音结束后生成纪要"
            return
        }
        let folder = URL(fileURLWithPath: target.folder, isDirectory: true)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [python, resources.appendingPathComponent("gui/meeting_summary.py").path,
                             "--meeting", folder.appendingPathComponent("meeting.json").path,
                             "--backend", summaryBackend, "--model", summaryModel,
                             "--url", summaryURL]
        let err = Pipe()
        process.standardOutput = Pipe()
        process.standardError = err
        let folderPath = folder.path
        process.terminationHandler = { [weak self] finished in
            let code = finished.terminationStatus
            let message = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.isSummarizing = false
                self.summaryProcess = nil
                if code == 0 {
                    self.status = "AI 纪要已保存"
                    self.state = "已完成"
                    self.refreshMeetings()
                    if let item = self.meetings.first(where: { $0.id == target.id }) { self.select(item) }
                } else {
                    self.status = "纪要失败：\(message.isEmpty ? "模型调用失败" : message)"
                    self.state = "纪要失败"
                    self.saveManifest(URL(fileURLWithPath: folderPath),
                                      changes: ["state": "summary_failed", "summaryError": self.status])
                }
            }
        }
        do {
            try process.run()
            summaryProcess = process
            isSummarizing = true
            state = "生成纪要中"
            status = "正在生成 AI 纪要…"
            saveManifest(folder, changes: ["state": "summarizing"])
        } catch {
            status = "纪要启动失败：\(error.localizedDescription)"
        }
    }

    func openArtifact(_ key: String) {
        guard let selected else { status = "请先选择一场会议"; return }
        let path: String?
        switch key {
        case "folder": path = selected.folder
        case "audio": path = selected.audio
        case "transcript": path = selected.transcript
        case "summary": path = selected.summary
        default: path = nil
        }
        guard let path, fileManager.fileExists(atPath: path) else { status = "文件尚未生成"; return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    func tick() {
        guard let startedAt, isRecording else { return }
        let seconds = max(0, Int(Date().timeIntervalSince(startedAt)))
        elapsed = String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    func finishOnClose() {
        if let process = recordingProcess, process.isRunning {
            stop()
            process.waitUntilExit()
            if let folder = currentFolder {
                let eventsPath = folder.appendingPathComponent("audio.live.jsonl")
                let events = (try? String(contentsOf: eventsPath, encoding: .utf8)) ?? ""
                let completed = events.split(separator: "\n").reversed().compactMap { line -> [String: Any]? in
                    guard let bytes = String(line).data(using: .utf8) else { return nil }
                    return try? JSONSerialization.jsonObject(with: bytes) as? [String: Any]
                }.first { $0["event"] as? String == "completed" }
                if let data = completed?["data"] as? [String: Any] {
                    saveManifest(folder, changes: ["state": "recorded",
                                                   "durationSeconds": data["durationSeconds"] ?? 0])
                } else {
                    saveManifest(folder, changes: ["state": "failed",
                                                   "error": "窗口关闭后录音未能正常完成；请检查保留的原始音轨"])
                }
            }
        }
        if let process = summaryProcess, process.isRunning { process.waitUntilExit() }
    }
}

private struct AssistantView: View {
    @StateObject private var model = MeetingModel()
    private let accent = Color(red: 0.19, green: 0.67, blue: 0.60)

    var body: some View {
        HStack(spacing: 0) {
            settings
                .frame(width: 338)
                .background(Color(red: 0.07, green: 0.16, blue: 0.24))
                .environment(\.colorScheme, .dark)
            dashboard
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color(red: 0.94, green: 0.96, blue: 0.97))
        }
        .frame(minWidth: 1060, minHeight: 710)
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in model.tick() }
        .onDisappear { model.finishOnClose() }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 3) {
                Text("◉  Audio Recorder").font(.title3.bold()).foregroundStyle(.white)
                Text("AI 会议助手").font(.caption).foregroundStyle(.white.opacity(0.65))
            }.padding(22)
            Divider().overlay(.white.opacity(0.15))
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    section("01  新会议") {
                        field("会议名称", text: $model.title)
                        HStack { field("保存目录", text: $model.root); Button("选择") { model.chooseRoot() } }
                    }
                    section("02  录音") {
                        Picker("声音来源", selection: $model.source) {
                            Text("麦克风 + 会议声音").tag("both")
                            Text("仅麦克风").tag("mic")
                            Text("仅会议声音").tag("system")
                        }
                        Picker("会议应用", selection: $model.application) {
                            Text("全部系统声音").tag("")
                            ForEach(model.applications, id: \.0) { item in Text(item.1).tag(item.0) }
                        }
                        field("应用名 / PID（可选）", text: $model.manualApplication)
                        Picker("麦克风", selection: $model.micDevice) {
                            Text("系统默认麦克风").tag("")
                            ForEach(model.devices, id: \.0) { item in Text(item.1).tag(item.0) }
                        }
                        Button("刷新设备与应用") { model.refreshSources() }
                    }
                    section("03  实时转写") {
                        Picker("转写后端", selection: $model.asrBackend) {
                            Text("本地 Qwen3-ASR / SenseVoice").tag("local-gguf")
                            Text("本地 Whisper").tag("local")
                            Text("线上 Qwen ASR").tag("qwen")
                        }.onChange(of: model.asrBackend) { _, _ in model.asrChanged() }
                        field("模型名称", text: $model.asrModel)
                        HStack { field("本地模型文件", text: $model.asrModelPath); Button("选择") { model.chooseModel() } }
                        field("线上 ASR 地址", text: $model.asrURL)
                        HStack { field("语言", text: $model.language); field("分块秒数", text: $model.chunkSeconds) }
                    }
                    section("04  AI 纪要") {
                        Picker("生成方式", selection: $model.summaryBackend) {
                            Text("本地 Ollama").tag("ollama")
                            Text("线上 Qwen").tag("qwen")
                            Text("暂不生成").tag("none")
                        }.onChange(of: model.summaryBackend) { _, _ in model.summaryChanged() }
                        field("模型名称", text: $model.summaryModel)
                        field("模型地址", text: $model.summaryURL)
                        Text("只有选择线上模型后才发送音频或转写文本。")
                            .font(.caption2).foregroundStyle(.white.opacity(0.6))
                    }
                }.padding(20)
            }
            HStack {
                Button("●  开始会议") { model.start() }
                    .buttonStyle(.borderedProminent).tint(accent).disabled(model.isRecording)
                Button("■  结束录音") { model.stop() }
                    .disabled(!model.isRecording)
            }.padding(16)
        }.foregroundStyle(.white)
    }

    private func section<Content: View>(_ heading: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(heading).font(.caption.bold()).foregroundStyle(accent).padding(.top, 5)
            content()
        }
    }

    private func field(_ heading: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(heading).font(.caption)
            TextField(heading, text: text).textFieldStyle(.roundedBorder).foregroundStyle(.primary)
        }
    }

    private var dashboard: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("让每场讨论都留下清晰记录").font(.title.bold())
            Text("录音、实时转写和可追溯的会后纪要，保存在同一个本地文件夹。")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.current?.title ?? "尚未开始会议").font(.headline)
                    Text(model.current?.folder ?? "选择录音来源和模型，然后开始会议。")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Text(model.state).foregroundStyle(accent).font(.subheadline.bold())
                Text(model.elapsed).monospacedDigit().font(.title3.bold())
            }.padding(15).background(.white, in: RoundedRectangle(cornerRadius: 12))
            HStack(spacing: 14) {
                textCard("实时转写", content: model.transcriptText, action: nil)
                textCard("AI 会议纪要", content: model.summaryText,
                         action: { model.summarize() })
            }.frame(maxHeight: .infinity)
            HStack(spacing: 9) {
                Text("会议文件").font(.headline)
                Spacer()
                Button("播放录音") { model.openArtifact("audio") }.disabled(model.selected == nil)
                Button("打开转写") { model.openArtifact("transcript") }.disabled(model.selected == nil)
                Button("打开纪要") { model.openArtifact("summary") }.disabled(model.selected == nil)
                Button("打开目录") { model.openArtifact("folder") }.disabled(model.selected == nil)
            }.padding(13).background(.white, in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("历史会议").font(.headline)
                    Spacer()
                    Button("刷新") { model.refreshMeetings() }
                }
                if model.meetings.isEmpty {
                    VStack(spacing: 7) {
                        Image(systemName: "calendar.badge.plus").font(.title2).foregroundStyle(accent)
                        Text("这里会保存每一场会议").font(.subheadline.bold())
                        Text("开始录音后，录音、转写与纪要将自动归档。")
                            .font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, minHeight: 130)
                } else {
                    List(model.meetings) { meeting in
                        Button {
                            model.select(meeting)
                        } label: {
                            HStack {
                                Text(meeting.title).lineLimit(1)
                                Spacer()
                                Text(meeting.createdAt.prefix(16).replacingOccurrences(of: "T", with: " "))
                                Text(meeting.state).foregroundStyle(.secondary)
                            }
                        }.buttonStyle(.plain)
                    }.frame(minHeight: 130)
                }
            }.padding(13).background(.white, in: RoundedRectangle(cornerRadius: 12))
            Text(model.status).font(.caption)
                .foregroundStyle(model.status.contains("失败") ? Color.red : Color.secondary).lineLimit(2)
        }.padding(24)
    }

    private func textCard(_ heading: String, content: String, action: (() -> Void)?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(heading).font(.headline)
                Spacer()
                if let action { Button("生成 / 重试") { action() }
                        .disabled(model.isSummarizing || model.selected == nil) }
            }
            Divider()
            if content == "等待实时转写…" || content == "录音结束后可生成会议纪要。" {
                VStack(spacing: 10) {
                    Image(systemName: action == nil ? "waveform" : "sparkles")
                        .font(.system(size: 31)).foregroundStyle(accent.opacity(0.75))
                    Text(content).font(.subheadline.bold()).foregroundStyle(.secondary)
                    Text(action == nil ? "声音会按时间片逐段显示在这里。" : "决定、行动项与待确认问题会归纳在这里。")
                        .font(.caption).foregroundStyle(.tertiary)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    Text(content).font(.system(size: 12)).frame(maxWidth: .infinity, alignment: .topLeading)
                        .textSelection(.enabled)
                }
            }
            if action != nil {
                Text("AI 草稿需以原始录音和转写核对。")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }.padding(15).frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(.white, in: RoundedRectangle(cornerRadius: 12))
    }
}

@main
private struct MeetingAssistantApp: App {
    var body: some Scene {
        WindowGroup("AI 会议助手") { AssistantView() }
    }
}
