import Darwin
import Foundation

enum SessionState: String, Codable {
    case starting
    case recording
    case stopping
    case done
    case failed

    var isActive: Bool { self == .starting || self == .recording || self == .stopping }
}

struct SessionRecord: Codable {
    var schemaVersion = 1
    var id: String
    var state: SessionState = .starting
    var pid: Int32?
    var output: String
    var source: String
    var app: String?
    var createdAt: String
    var startedAt: String?
    var endedAt: String?
    var durationSeconds: Double?
    var tracks: [String]?
    var error: String?
    var logPath: String
    var arguments: [String]

    init(id: String, output: String, source: String, app: String?, logPath: String, arguments: [String]) {
        self.id = id
        self.output = output
        self.source = source
        self.app = app
        self.createdAt = ISO8601DateFormatter().string(from: Date())
        self.logPath = logPath
        self.arguments = arguments
    }

    func jsonObject() throws -> [String: Any] {
        let data = try JSONEncoder().encode(self)
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }
}

enum ControlError: LocalizedError {
    case badID
    case missingSession(String)
    case activeSession(String)
    case noActiveSession
    case sessionFailed(String, String)
    case stopTimeout(String)

    var errorDescription: String? {
        switch self {
        case .badID: return "Session ID must be a UUID"
        case .missingSession(let id): return "Session not found: \(id)"
        case .activeSession(let id): return "A recording is already active: \(id)"
        case .noActiveSession: return "No active recording; run 'audio-recorder status'"
        case .sessionFailed(let id, let reason): return "Session \(id) failed: \(reason)"
        case .stopTimeout(let id): return "Stop requested for \(id), but finalization did not finish within 20 seconds; check status"
        }
    }

    var code: String {
        switch self {
        case .badID: return "INVALID_SESSION_ID"
        case .missingSession: return "SESSION_NOT_FOUND"
        case .activeSession: return "ALREADY_RECORDING"
        case .noActiveSession: return "NO_ACTIVE_SESSION"
        case .sessionFailed: return "CAPTURE_FAILED"
        case .stopTimeout: return "STOP_TIMEOUT"
        }
    }
}

enum JSONReply {
    static func write(command: String, data: [String: Any]) {
        writeObject(["schemaVersion": 1, "ok": true, "command": command, "data": data])
    }

    static func fail(command: String, code: String, message: String) {
        writeObject(["schemaVersion": 1, "ok": false, "command": command,
                     "error": ["code": code, "message": message]])
    }

    private static func writeObject(_ object: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
              let line = String(data: data, encoding: .utf8) else {
            fputs("{\"schemaVersion\":1,\"ok\":false,\"error\":{\"code\":\"JSON_ENCODING_FAILED\"}}\n", stdout)
            return
        }
        print(line)
    }
}

final class SessionRegistry {
    let root: URL

    init() throws {
        if let override = ProcessInfo.processInfo.environment["AUDIO_RECORDER_STATE_DIR"], !override.isEmpty {
            root = URL(fileURLWithPath: (override as NSString).expandingTildeInPath).standardizedFileURL
        } else {
            root = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/AudioRecorder/sessions", isDirectory: true)
        }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
    }

    private func path(for id: String) throws -> URL {
        guard let uuid = UUID(uuidString: id) else { throw ControlError.badID }
        return root.appendingPathComponent("\(uuid.uuidString).json")
    }

    private func withLock<T>(_ body: () throws -> T) throws -> T {
        let descriptor = open(root.appendingPathComponent(".lock").path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { throw POSIXError(.EACCES) }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else { throw POSIXError(.EIO) }
        defer { flock(descriptor, LOCK_UN) }
        return try body()
    }

    private func readUnlocked(_ id: String) throws -> SessionRecord {
        let url = try path(for: id)
        guard FileManager.default.fileExists(atPath: url.path) else { throw ControlError.missingSession(id) }
        return try JSONDecoder().decode(SessionRecord.self, from: Data(contentsOf: url))
    }

    private func writeUnlocked(_ record: SessionRecord) throws {
        let url = try path(for: record.id)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(record).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private func listUnlocked() throws -> [SessionRecord] {
        let urls = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
        return try urls.filter { $0.pathExtension == "json" }.compactMap { url in
            guard UUID(uuidString: url.deletingPathExtension().lastPathComponent) != nil else { return nil }
            return try JSONDecoder().decode(SessionRecord.self, from: Data(contentsOf: url))
        }.sorted { $0.createdAt > $1.createdAt }
    }

    private func refreshUnlocked(_ record: SessionRecord) throws -> SessionRecord {
        guard record.state.isActive else { return record }
        if let pid = record.pid {
            if kill(pid, 0) == 0 || errno == EPERM { return record }
        } else if let created = ISO8601DateFormatter().date(from: record.createdAt),
                  Date().timeIntervalSince(created) < 60 {
            return record
        }
        var failed = record
        failed.state = .failed
        failed.error = "Recorder process did not start or exited before finalizing; inspect log and temporary tracks"
        failed.endedAt = ISO8601DateFormatter().string(from: Date())
        try writeUnlocked(failed)
        return failed
    }

    func create(_ record: SessionRecord) throws {
        try withLock {
            for existing in try listUnlocked() {
                let current = try refreshUnlocked(existing)
                if current.state.isActive { throw ControlError.activeSession(current.id) }
            }
            try writeUnlocked(record)
        }
    }

    func read(_ id: String) throws -> SessionRecord {
        try withLock { try refreshUnlocked(readUnlocked(id)) }
    }

    func list() throws -> [SessionRecord] {
        try withLock { try listUnlocked().map { try refreshUnlocked($0) } }
    }

    @discardableResult
    func update(_ id: String, _ change: (inout SessionRecord) throws -> Void) throws -> SessionRecord {
        try withLock {
            var record = try readUnlocked(id)
            try change(&record)
            try writeUnlocked(record)
            return record
        }
    }
}
