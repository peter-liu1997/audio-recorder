import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import RecorderCore
import ScreenCaptureKit

private enum RecorderError: LocalizedError {
    case usage(String)
    case permission(String)
    case noDisplay
    case noApplication(String)
    case ambiguousApplication(String)
    case noMicrophone(String)
    case captureStopped(String)

    var errorDescription: String? {
        switch self {
        case .usage(let detail): return detail
        case .permission(let detail): return detail
        case .noDisplay: return "No display is available for system audio capture"
        case .noApplication(let query): return "No running audio app matches '\(query)'; run 'audio-recorder apps'"
        case .ambiguousApplication(let query): return "Multiple apps match '\(query)'; use a bundle ID or PID from 'audio-recorder apps'"
        case .noMicrophone(let id): return "No microphone with device ID '\(id)'; run 'audio-recorder devices'"
        case .captureStopped(let reason): return "Capture stopped unexpectedly: \(reason)"
        }
    }
}

private enum Source: String {
    case mic, system, both
    var needsMic: Bool { self == .mic || self == .both }
    var needsSystem: Bool { self == .system || self == .both }
}

private struct RecordOptions {
    var source: Source = .both
    var app: String?
    var micDevice: String?
    var output: URL?
    var liveDir: URL?
    var duration: Double?
    var keepTracks = false
    var micGain: Float = 1
    var systemGain: Float = 1

    static func parse(_ arguments: [String]) throws -> Self {
        var options = Self()
        var index = 0
        while index < arguments.count {
            let flag = arguments[index]
            if flag == "--keep-tracks" {
                options.keepTracks = true
                index += 1
                continue
            }
            guard index + 1 < arguments.count else {
                throw RecorderError.usage("Missing value for \(flag)")
            }
            let value = arguments[index + 1]
            switch flag {
            case "--source":
                guard let source = Source(rawValue: value) else {
                    throw RecorderError.usage("--source must be mic, system, or both")
                }
                options.source = source
            case "--app": options.app = value
            case "--mic-device": options.micDevice = value
            case "--output": options.output = URL(fileURLWithPath: (value as NSString).expandingTildeInPath).standardizedFileURL
            case "--live-dir": options.liveDir = URL(fileURLWithPath: (value as NSString).expandingTildeInPath).standardizedFileURL
            case "--duration":
                guard let duration = Double(value), duration.isFinite, duration > 0 else {
                    throw RecorderError.usage("--duration must be a positive number of seconds")
                }
                options.duration = duration
            case "--mic-gain":
                guard let gain = Float(value), gain.isFinite, gain >= 0, gain <= 4 else {
                    throw RecorderError.usage("--mic-gain must be between 0 and 4")
                }
                options.micGain = gain
            case "--system-gain":
                guard let gain = Float(value), gain.isFinite, gain >= 0, gain <= 4 else {
                    throw RecorderError.usage("--system-gain must be between 0 and 4")
                }
                options.systemGain = gain
            default: throw RecorderError.usage("Unknown option: \(flag)")
            }
            index += 2
        }
        if options.app != nil && !options.source.needsSystem {
            throw RecorderError.usage("--app requires --source system or both")
        }
        if options.micDevice != nil && !options.source.needsMic {
            throw RecorderError.usage("--mic-device requires --source mic or both")
        }
        return options
    }
}

private let help = """
audio-recorder — record microphone and application/system audio on macOS 15+

Usage:
  audio-recorder doctor [--json]
  audio-recorder devices [--json]
  audio-recorder apps [--json]
  audio-recorder record [options] [--json]
  audio-recorder start [options] [--json]
  audio-recorder status [--id UUID] [--json]
  audio-recorder stop [--id UUID] [--json]
  audio-recorder list [--json]
  audio-recorder transcribe --input FILE [--backend local|local-gguf|qwen] [options] [--json]
  audio-recorder live [record and model options] [--json]

Record options:
  --source mic|system|both  Source, default both
  --app NAME|BUNDLE_ID|PID Capture only a running app's output audio
  --mic-device ID           Microphone device ID from 'devices'
  --output FILE.wav         Output path, default recording-YYYYMMDD-HHMMSS.wav
  --duration SECONDS        Stop automatically; otherwise press Ctrl-C
  --mic-gain 0..4           Microphone level in mixed WAV, default 1
  --system-gain 0..4        System level in mixed WAV, default 1
  --keep-tracks             Keep separate microphone/system CAF files

Agent commands:
  start                    Launch one background recording; returns session ID
  status                   Show active session, or most recent completed one
  stop                     Request a clean stop and wait for finalization
  list                     List saved session records
  --json                   Print one machine-readable JSON object

Transcription:
  --backend NAME            local (Whisper), local-gguf (Qwen3/SenseVoice), qwen (cloud)
  --model NAME              Whisper: small, medium, large-v3, large-v3-turbo
                            GGUF: qwen3-asr-0.6b, qwen3-asr-1.7b, sensevoice-small
  --model-path FILE         Local model path; required for local-gguf
  --language zh|yue|en|auto Spoken language; default zh
  --output FILE.txt         Transcript path; writes adjacent .json metadata
  --api-url URL             Qwen workspace /chat/completions URL

Exit codes: 0 success, 2 usage, 3 permission, 4 session conflict,
            5 device/app missing, 6 capture failure, 7 filesystem error

Examples:
  audio-recorder record --source mic --duration 10 --output mic.wav
  audio-recorder record --source both --app com.microsoft.teams2 --output meeting.wav
  audio-recorder record --source system --app Zoom --duration 60
"""

private final class StopFlag {
    private let lock = NSLock()
    private var stopped = false
    func stop() { lock.lock(); stopped = true; lock.unlock() }
    var isStopped: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
}

private struct RecordOutcome {
    let output: URL
    let duration: Double
    let tracks: [URL]

    var json: [String: Any] {
        ["output": output.path, "durationSeconds": duration,
         "tracks": tracks.map(\.path), "sampleRate": 48_000,
         "channels": 2, "format": "wav-pcm16"]
    }
}

private final class StreamReceiver: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    let queue = DispatchQueue(label: "audio-recorder.sample-writer")
    private let microphone: TimestampedAudioWriter?
    private let system: TimestampedAudioWriter?
    private let origin: Double
    private let stopFlag: StopFlag
    private(set) var failure: Error?

    init(microphone: TimestampedAudioWriter?, system: TimestampedAudioWriter?, origin: Double, stopFlag: StopFlag) {
        self.microphone = microphone
        self.system = system
        self.origin = origin
        self.stopFlag = stopFlag
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard CMSampleBufferIsValid(sampleBuffer), CMSampleBufferGetNumSamples(sampleBuffer) > 0 else { return }
        let writer: TimestampedAudioWriter?
        switch type {
        case .audio: writer = system
        case .microphone: writer = microphone
        default: return
        }
        guard let writer else { return }
        do {
            let buffer = try pcmBuffer(from: sampleBuffer)
            let timestamp = CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds - origin
            try writer.append(buffer, at: timestamp)
        } catch {
            failure = error
            stopFlag.stop()
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        queue.async {
            self.failure = error
            self.stopFlag.stop()
        }
    }

    func capturedFailure() -> Error? { queue.sync { failure } }
}

private final class MicrophoneReceiver: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    let queue = DispatchQueue(label: "audio-recorder.mic-writer")
    let session = AVCaptureSession()
    let writer: TimestampedAudioWriter
    let stopFlag: StopFlag
    private var firstTimestamp: Double?
    private(set) var failure: Error?

    init(writer: TimestampedAudioWriter, stopFlag: StopFlag, deviceID: String?) throws {
        self.writer = writer
        self.stopFlag = stopFlag
        super.init()
        let device: AVCaptureDevice?
        if let deviceID { device = audioDevices().first { $0.uniqueID == deviceID } }
        else { device = AVCaptureDevice.default(for: .audio) }
        guard let device else { throw RecorderError.noMicrophone(deviceID ?? "default") }
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else { throw AudioPipelineError.invalidAudioFormat }
        session.addInput(input)
        let output = AVCaptureAudioDataOutput()
        guard session.canAddOutput(output) else { throw AudioPipelineError.invalidAudioFormat }
        session.addOutput(output)
        output.setSampleBufferDelegate(self, queue: queue)
    }

    func start() throws {
        session.startRunning()
        guard session.isRunning else { throw RecorderError.captureStopped("Microphone session could not start") }
    }

    func stop() {
        session.stopRunning()
        queue.sync { writer.finish() }
    }

    func capturedFailure() -> Error? { queue.sync { failure } }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard CMSampleBufferIsValid(sampleBuffer), CMSampleBufferGetNumSamples(sampleBuffer) > 0 else { return }
        do {
            let timestamp = CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds
            if firstTimestamp == nil { firstTimestamp = timestamp }
            let buffer = try pcmBuffer(from: sampleBuffer)
            try writer.append(buffer, at: timestamp - (firstTimestamp ?? timestamp))
        } catch {
            failure = error
            stopFlag.stop()
        }
    }
}

private func pcmBuffer(from sampleBuffer: CMSampleBuffer) throws -> AVAudioPCMBuffer {
    guard let description = CMSampleBufferGetFormatDescription(sampleBuffer),
          let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description),
          let format = AVAudioFormat(streamDescription: asbd) else {
        throw AudioPipelineError.invalidAudioFormat
    }
    let count = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
    guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count) else {
        throw AudioPipelineError.invalidAudioFormat
    }
    buffer.frameLength = count
    let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sampleBuffer,
                                                              at: 0,
                                                              frameCount: Int32(count),
                                                              into: buffer.mutableAudioBufferList)
    guard status == noErr else {
        throw RecorderError.captureStopped("PCM copy error \(status)")
    }
    return buffer
}

private func audioDevices() -> [AVCaptureDevice] {
    AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external],
                                     mediaType: .audio,
                                     position: .unspecified).devices
}

@main
private struct App {
    static func main() async {
        let raw = Array(CommandLine.arguments.dropFirst())
        let json = raw.contains("--json")
        let command = raw.first ?? "help"
        do {
            try await run(raw.filter { $0 != "--json" }, json: json)
        } catch {
            let (code, status) = classify(error)
            if json { JSONReply.fail(command: command, code: code, message: error.localizedDescription) }
            else { fputs("audio-recorder: \(error.localizedDescription)\n", stderr) }
            exit(status)
        }
    }

    private static func classify(_ error: Error) -> (String, Int32) {
        if let control = error as? ControlError {
            if case .stopTimeout = control { return (control.code, 6) }
            if case .sessionFailed = control { return (control.code, 6) }
            return (control.code, 4)
        }
        if let recorder = error as? RecorderError {
            switch recorder {
            case .usage: return ("INVALID_ARGUMENT", 2)
            case .permission: return ("PERMISSION_REQUIRED", 3)
            case .noDisplay, .noApplication, .ambiguousApplication, .noMicrophone:
                return ("SOURCE_NOT_FOUND", 5)
            case .captureStopped: return ("CAPTURE_FAILED", 6)
            }
        }
        if let pipeline = error as? AudioPipelineError {
            switch pipeline {
            case .outputExists: return ("OUTPUT_EXISTS", 4)
            case .emptyRecording, .conversionFailed, .invalidAudioFormat, .recordingTooLong:
                return ("AUDIO_PROCESSING_FAILED", 6)
            }
        }
        if error is CocoaError || error is POSIXError { return ("IO_ERROR", 7) }
        return ("INTERNAL_ERROR", 1)
    }

    private static func run(_ args: [String], json: Bool) async throws {
        guard let command = args.first else {
            if json { JSONReply.write(command: "help", data: ["text": help]) }
            else { print(help) }
            return
        }
        switch command {
        case "help", "--help", "-h":
            if json { JSONReply.write(command: "help", data: ["text": help]) }
            else { print(help) }
        case "doctor":
            guard args.count == 1 else { throw RecorderError.usage("doctor takes no options") }
            let mic = permissionName(AVCaptureDevice.authorizationStatus(for: .audio))
            let system = CGPreflightScreenCaptureAccess() ? "granted" : "not granted"
            if json {
                JSONReply.write(command: "doctor", data: ["macOS": ProcessInfo.processInfo.operatingSystemVersionString,
                                                           "microphone": mic, "systemAudio": system])
            } else {
                print("macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
                print("Microphone: \(mic)")
                print("Screen & System Audio Recording: \(system)")
                print("If access is denied, open System Settings > Privacy & Security and grant it to AudioRecorder (or the terminal running the command).")
            }
        case "devices":
            guard args.count == 1 else { throw RecorderError.usage("devices takes no options") }
            let devices = audioDevices()
            if json {
                JSONReply.write(command: "devices", data: ["devices": devices.map {
                    ["id": $0.uniqueID, "name": $0.localizedName]
                }])
            } else {
                if devices.isEmpty {
                    print("No microphones visible. Check Microphone permission with 'audio-recorder doctor'.")
                }
                for device in devices { print("\(device.uniqueID)\t\(device.localizedName)") }
            }
        case "apps":
            guard args.count == 1 else { throw RecorderError.usage("apps takes no options") }
            if json && !CGPreflightScreenCaptureAccess() {
                throw RecorderError.permission("Screen & System Audio Recording access is not granted; run 'audio-recorder doctor'")
            }
            let content = try await SCShareableContent.current
            let apps = content.applications.sorted { $0.applicationName.localizedStandardCompare($1.applicationName) == .orderedAscending }
            if json {
                JSONReply.write(command: "apps", data: ["apps": apps.map {
                    ["pid": Int($0.processID), "bundleID": $0.bundleIdentifier, "name": $0.applicationName] as [String: Any]
                }])
            } else {
                for app in apps { print("\(app.processID)\t\(app.bundleIdentifier)\t\(app.applicationName)") }
            }
        case "record":
            let options = try RecordOptions.parse(Array(args.dropFirst()))
            let result = try await record(options, quiet: json)
            if json { JSONReply.write(command: "record", data: result.json) }
        case "start":
            let options = try RecordOptions.parse(Array(args.dropFirst()))
            try await start(options, arguments: Array(args.dropFirst()), json: json)
        case "status": try status(Array(args.dropFirst()), json: json)
        case "stop": try await stop(Array(args.dropFirst()), json: json)
        case "list": try list(Array(args.dropFirst()), json: json)
        case "transcribe": try transcribe(Array(args.dropFirst()), json: json)
        case "live": try live(Array(args.dropFirst()), json: json)
        case "__worker":
            guard args.count == 2 else { throw RecorderError.usage("Invalid worker invocation") }
            try await worker(args[1])
        default: throw RecorderError.usage("Unknown command: \(command)\n\n\(help)")
        }
    }

    private static func transcribe(_ args: [String], json: Bool) throws {
        let manager = FileManager.default
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let candidates = [
            Bundle.main.resourceURL?.appendingPathComponent("transcribe.py"),
            executable.deletingLastPathComponent().appendingPathComponent("transcribe.py"),
            URL(fileURLWithPath: manager.currentDirectoryPath).appendingPathComponent("scripts/transcribe.py")
        ].compactMap { $0 }
        guard let script = candidates.first(where: { manager.fileExists(atPath: $0.path) }) else {
            throw RecorderError.usage("Transcription backend not found; rebuild the app with scripts/transcribe.py")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [ProcessInfo.processInfo.environment["AUDIO_RECORDER_PYTHON"] ?? "python3",
                             script.path] + args + (json ? ["--json"] : [])
        try process.run()
        process.waitUntilExit()
        exit(process.terminationStatus)
    }

    private static func live(_ args: [String], json: Bool) throws {
        let manager = FileManager.default
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let candidates = [
            Bundle.main.resourceURL?.appendingPathComponent("live.py"),
            executable.deletingLastPathComponent().appendingPathComponent("live.py"),
            URL(fileURLWithPath: manager.currentDirectoryPath).appendingPathComponent("scripts/live.py")
        ].compactMap { $0 }
        guard let script = candidates.first(where: { manager.fileExists(atPath: $0.path) }) else {
            throw RecorderError.usage("Live transcription backend not found; rebuild with scripts/live.py")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [ProcessInfo.processInfo.environment["AUDIO_RECORDER_PYTHON"] ?? "python3",
                             script.path, "--recorder-cli", executable.path] + args + (json ? ["--json"] : [])
        try process.run()
        signal(SIGINT, SIG_IGN)
        process.waitUntilExit()
        exit(process.terminationStatus)
    }

    private static func permissionName(_ status: AVAuthorizationStatus) -> String {
        switch status {
        case .authorized: return "granted"
        case .denied, .restricted: return "denied"
        case .notDetermined: return "not requested"
        @unknown default: return "unknown"
        }
    }

    private static func defaultOutput() -> URL {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("recording-\(formatter.string(from: Date())).wav")
    }

    private static func sessionID(from arguments: [String]) throws -> String? {
        if arguments.isEmpty { return nil }
        guard arguments.count == 2, arguments[0] == "--id" else {
            throw RecorderError.usage("Expected --id UUID")
        }
        guard UUID(uuidString: arguments[1]) != nil else { throw ControlError.badID }
        return arguments[1]
    }

    private static func show(_ session: SessionRecord, command: String, json: Bool) throws {
        if json { JSONReply.write(command: command, data: ["session": try session.jsonObject()]) }
        else {
            print("\(session.id)\t\(session.state.rawValue)\t\(session.output)")
            if let error = session.error { print("Error: \(error)") }
        }
    }

    private static func start(_ options: RecordOptions, arguments: [String], json: Bool) async throws {
        if options.liveDir != nil {
            throw RecorderError.usage("--live-dir is only valid with foreground record")
        }
        // Background invocations never trigger macOS permission dialogs.
        if options.source.needsMic && AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
            throw RecorderError.permission("Microphone access is not granted; run 'audio-recorder doctor' and grant it in System Settings")
        }
        if options.source.needsSystem && !CGPreflightScreenCaptureAccess() {
            throw RecorderError.permission("Screen & System Audio Recording access is not granted; run 'audio-recorder doctor' and grant it in System Settings")
        }
        if let id = options.micDevice, !audioDevices().contains(where: { $0.uniqueID == id }) {
            throw RecorderError.noMicrophone(id)
        }
        let output = options.output ?? defaultOutput()
        guard output.pathExtension.lowercased() == "wav" else { throw RecorderError.usage("--output must have a .wav extension") }
        guard FileManager.default.fileExists(atPath: output.deletingLastPathComponent().path) else {
            throw RecorderError.usage("Output directory does not exist: \(output.deletingLastPathComponent().path)")
        }
        guard !FileManager.default.fileExists(atPath: output.path) else { throw AudioPipelineError.outputExists }
        if options.keepTracks {
            for name in ["microphone", "system"] {
                let target = output.deletingPathExtension().appendingPathExtension("\(name).caf")
                guard !FileManager.default.fileExists(atPath: target.path) else {
                    throw AudioPipelineError.outputExists
                }
            }
        }

        let registry = try SessionRegistry()
        let id = UUID().uuidString
        let log = registry.root.appendingPathComponent("\(id).log")
        let record = SessionRecord(id: id, output: output.path, source: options.source.rawValue,
                                   app: options.app, logPath: log.path,
                                   arguments: arguments + ["--output", output.path])
        try registry.create(record)
        guard FileManager.default.createFile(atPath: log.path, contents: nil,
                                              attributes: [.posixPermissions: 0o600]) else {
            _ = try? registry.update(id) { $0.state = .failed; $0.error = "Could not create worker log" }
            throw POSIXError(.EIO)
        }
        let process = Process()
        process.executableURL = Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])
        process.arguments = ["__worker", id]
        process.standardInput = FileHandle.nullDevice
        let logHandle: FileHandle
        do { logHandle = try FileHandle(forWritingTo: log) }
        catch {
            _ = try? registry.update(id) { $0.state = .failed; $0.error = error.localizedDescription }
            throw error
        }
        defer { try? logHandle.close() }
        process.standardOutput = logHandle
        process.standardError = logHandle
        do { try process.run() }
        catch {
            _ = try? registry.update(id) { $0.state = .failed; $0.error = error.localizedDescription }
            throw error
        }
        try registry.update(id) { $0.pid = process.processIdentifier }

        // Return the actual state. Slow permission or device setup remains "starting".
        let deadline = Date().addingTimeInterval(8)
        var current = try registry.read(id)
        while current.state == .starting && Date() < deadline {
            try await Task.sleep(nanoseconds: 100_000_000)
            current = try registry.read(id)
        }
        if current.state == .failed {
            throw RecorderError.captureStopped("Session \(id) failed: \(current.error ?? "see \(log.path)")")
        }
        try show(current, command: "start", json: json)
    }

    private static func status(_ arguments: [String], json: Bool) throws {
        let id = try sessionID(from: arguments)
        let registry = try SessionRegistry()
        let current: SessionRecord?
        if let id { current = try registry.read(id) }
        else {
            let sessions = try registry.list()
            current = sessions.first(where: { $0.state.isActive }) ?? sessions.first
        }
        if let current { try show(current, command: "status", json: json) }
        else if json { JSONReply.write(command: "status", data: ["session": NSNull()]) }
        else { print("No sessions") }
    }

    private static func list(_ arguments: [String], json: Bool) throws {
        guard arguments.isEmpty else { throw RecorderError.usage("list takes no options") }
        let sessions = try SessionRegistry().list()
        if json { JSONReply.write(command: "list", data: ["sessions": try sessions.map { try $0.jsonObject() }]) }
        else {
            if sessions.isEmpty { print("No sessions") }
            for session in sessions { print("\(session.id)\t\(session.state.rawValue)\t\(session.output)") }
        }
    }

    private static func stop(_ arguments: [String], json: Bool) async throws {
        let id = try sessionID(from: arguments)
        let registry = try SessionRegistry()
        let target: SessionRecord
        if let id { target = try registry.read(id) }
        else {
            guard let active = try registry.list().first(where: { $0.state.isActive }) else {
                throw ControlError.noActiveSession
            }
            target = active
        }
        let updated = try registry.update(target.id) {
            if $0.state == .starting || $0.state == .recording { $0.state = .stopping }
        }
        if updated.state == .failed {
            throw ControlError.sessionFailed(updated.id, updated.error ?? "inspect log")
        }
        if updated.state == .done {
            try show(updated, command: "stop", json: json)
            return
        }
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            let current = try registry.read(target.id)
            if current.state == .failed {
                throw ControlError.sessionFailed(current.id, current.error ?? "inspect log")
            }
            if current.state == .done {
                try show(current, command: "stop", json: json)
                return
            }
            try await Task.sleep(nanoseconds: 150_000_000)
        }
        throw ControlError.stopTimeout(target.id)
    }

    private static func worker(_ id: String) async throws {
        let registry = try SessionRegistry()
        let session = try registry.update(id) { $0.pid = getpid() }
        do {
            let options = try RecordOptions.parse(session.arguments)
            let outcome = try await record(options, quiet: true,
                                           onStarted: {
                                               try registry.update(id) {
                                                   if $0.state != .stopping { $0.state = .recording }
                                                   $0.startedAt = ISO8601DateFormatter().string(from: Date())
                                               }
                                           },
                                           shouldStop: { (try? registry.read(id).state) == .stopping })
            try registry.update(id) {
                $0.state = .done
                $0.endedAt = ISO8601DateFormatter().string(from: Date())
                $0.durationSeconds = outcome.duration
                $0.tracks = outcome.tracks.map(\.path)
            }
        } catch {
            _ = try? registry.update(id) {
                $0.state = .failed
                $0.endedAt = ISO8601DateFormatter().string(from: Date())
                $0.error = error.localizedDescription
            }
            throw error
        }
    }

    private static func authorize(_ options: RecordOptions, allowPrompt: Bool) async throws {
        if options.source.needsMic {
            let status = AVCaptureDevice.authorizationStatus(for: .audio)
            let granted: Bool
            if status == .authorized { granted = true }
            else if status == .notDetermined && allowPrompt { granted = await AVCaptureDevice.requestAccess(for: .audio) }
            else { granted = false }
            guard granted else {
                throw RecorderError.permission("Microphone permission is required. Grant access in System Settings > Privacy & Security > Microphone.")
            }
        }
        if options.source.needsSystem && !CGPreflightScreenCaptureAccess() {
            guard allowPrompt && CGRequestScreenCaptureAccess() else {
                throw RecorderError.permission("Screen & System Audio Recording permission is required. Grant access in System Settings > Privacy & Security, then restart the command.")
            }
        }
    }

    private static func selectApp(_ query: String, in apps: [SCRunningApplication]) throws -> SCRunningApplication {
        let exact = apps.filter {
            $0.bundleIdentifier.caseInsensitiveCompare(query) == .orderedSame ||
            $0.applicationName.caseInsensitiveCompare(query) == .orderedSame ||
            String($0.processID) == query
        }
        if exact.count == 1 { return exact[0] }
        if exact.count > 1 { throw RecorderError.ambiguousApplication(query) }
        let partial = apps.filter {
            $0.bundleIdentifier.localizedCaseInsensitiveContains(query) ||
            $0.applicationName.localizedCaseInsensitiveContains(query)
        }
        if partial.count == 1 { return partial[0] }
        if partial.isEmpty { throw RecorderError.noApplication(query) }
        throw RecorderError.ambiguousApplication(query)
    }

    private static func record(_ options: RecordOptions,
                               quiet: Bool = false,
                               onStarted: (() throws -> Void)? = nil,
                               shouldStop: (() -> Bool)? = nil) async throws -> RecordOutcome {
        try await authorize(options, allowPrompt: !quiet)
        if let id = options.micDevice, !audioDevices().contains(where: { $0.uniqueID == id }) {
            throw RecorderError.noMicrophone(id)
        }
        let output = options.output ?? defaultOutput()
        guard output.pathExtension.lowercased() == "wav" else {
            throw RecorderError.usage("--output must have a .wav extension")
        }
        guard FileManager.default.fileExists(atPath: output.deletingLastPathComponent().path) else {
            throw RecorderError.usage("Output directory does not exist: \(output.deletingLastPathComponent().path)")
        }
        guard !FileManager.default.fileExists(atPath: output.path) else {
            throw AudioPipelineError.outputExists
        }
        if options.keepTracks {
            for name in ["microphone", "system"] {
                let target = output.deletingPathExtension().appendingPathExtension("\(name).caf")
                guard !FileManager.default.fileExists(atPath: target.path) else {
                    throw AudioPipelineError.outputExists
                }
            }
        }
        let work = output.deletingLastPathComponent().appendingPathComponent(".audio-recorder-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: false)
        if let liveDir = options.liveDir {
            try FileManager.default.createDirectory(at: liveDir, withIntermediateDirectories: true)
        }
        var completed = false
        defer {
            if completed { try? FileManager.default.removeItem(at: work) }
            else { fputs("Temporary tracks kept for recovery (if audio arrived): \(work.path)\n", stderr) }
        }
        let micTrackURL = work.appendingPathComponent("microphone.caf")
        let systemTrackURL = work.appendingPathComponent("system.caf")
        let micTrack = try options.source.needsMic ? TimestampedAudioWriter(
            url: micTrackURL, liveRawURL: options.liveDir?.appendingPathComponent("microphone.raw")) : nil
        let systemTrack = try options.source.needsSystem ? TimestampedAudioWriter(
            url: systemTrackURL, liveRawURL: options.liveDir?.appendingPathComponent("system.raw")) : nil
        let stopped = StopFlag()
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        sigint.setEventHandler { stopped.stop() }
        sigterm.setEventHandler { stopped.stop() }
        sigint.resume()
        sigterm.resume()
        defer { sigint.cancel(); sigterm.cancel() }

        var stream: SCStream?
        var receiver: StreamReceiver?
        var microphone: MicrophoneReceiver?
        if options.source.needsSystem {
            let content = try await SCShareableContent.current
            guard let display = content.displays.first else { throw RecorderError.noDisplay }
            let filter: SCContentFilter
            if let query = options.app {
                let app = try selectApp(query, in: content.applications)
                filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
                if !quiet { print("Application: \(app.applicationName) (\(app.bundleIdentifier), PID \(app.processID))") }
            } else {
                filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
                if !quiet { print("Application: all system output") }
            }
            let config = SCStreamConfiguration()
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.queueDepth = 3
            config.capturesAudio = true
            config.sampleRate = Int(AudioFormat.sampleRate)
            config.channelCount = Int(AudioFormat.channels)
            config.excludesCurrentProcessAudio = true
            if options.source == .both {
                config.captureMicrophone = true
                config.microphoneCaptureDeviceID = options.micDevice
            }
            let origin = CMClockGetTime(CMClockGetHostTimeClock()).seconds
            let handler = StreamReceiver(microphone: micTrack, system: systemTrack, origin: origin, stopFlag: stopped)
            let capture = SCStream(filter: filter, configuration: config, delegate: handler)
            try capture.addStreamOutput(handler, type: .audio, sampleHandlerQueue: handler.queue)
            if options.source == .both {
                try capture.addStreamOutput(handler, type: .microphone, sampleHandlerQueue: handler.queue)
            }
            receiver = handler
            stream = capture
        } else if let micTrack {
            microphone = try MicrophoneReceiver(writer: micTrack, stopFlag: stopped, deviceID: options.micDevice)
        }

        let start = Date()
        do {
            if let stream { try await stream.startCapture() }
            if let microphone { try microphone.start() }
            try onStarted?()
            if !quiet { print("Recording to \(output.path). Press Ctrl-C to stop.") }
            while !stopped.isStopped {
                if shouldStop?() == true { break }
                if let duration = options.duration, Date().timeIntervalSince(start) >= duration { break }
                try await Task.sleep(nanoseconds: 150_000_000)
            }
        } catch {
            if let stream { try? await stream.stopCapture() }
            microphone?.stop()
            receiver?.queue.sync { micTrack?.finish(); systemTrack?.finish() }
            throw error
        }
        if let stream {
            do { try await stream.stopCapture() }
            catch {
                receiver?.queue.sync { micTrack?.finish(); systemTrack?.finish() }
                throw error
            }
        }
        microphone?.stop()
        receiver?.queue.sync { micTrack?.finish(); systemTrack?.finish() }
        if let failure = receiver?.capturedFailure() ?? microphone?.capturedFailure() { throw failure }
        let elapsed = Date().timeIntervalSince(start)
        let sources: [(URL, Float, String, TimestampedAudioWriter?)] = [
            (micTrackURL, options.micGain, "microphone", micTrack),
            (systemTrackURL, options.systemGain, "system", systemTrack)
        ].filter { $0.3 != nil }
        guard sources.contains(where: { ($0.3?.receivedBuffers ?? 0) > 0 }) else {
            throw RecorderError.captureStopped("No audio arrived. Check permissions and whether the selected app is playing audio.")
        }
        try AudioMixer.export(tracks: sources.map(\.0), to: output,
                              duration: min(options.duration ?? elapsed, elapsed),
                              gains: sources.map(\.1),
                              limitToDuration: options.duration != nil)
        var retainedTracks: [URL] = []
        if options.keepTracks {
            for source in sources {
                let target = output.deletingPathExtension().appendingPathExtension("\(source.2).caf")
                try FileManager.default.copyItem(at: source.0, to: target)
                retainedTracks.append(target)
                if !quiet { print("Track: \(target.path)") }
            }
        }
        completed = true
        if !quiet { print("Saved \(output.path) (\(String(format: "%.1f", elapsed)) s, 48 kHz stereo WAV)") }
        return RecordOutcome(output: output, duration: min(options.duration ?? elapsed, elapsed), tracks: retainedTracks)
    }
}
