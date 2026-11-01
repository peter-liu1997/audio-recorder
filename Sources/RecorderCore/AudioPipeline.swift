import AVFoundation
import AudioToolbox
import Foundation

public enum AudioPipelineError: LocalizedError {
    case invalidAudioFormat
    case conversionFailed
    case emptyRecording
    case recordingTooLong
    case outputExists

    public var errorDescription: String? {
        switch self {
        case .invalidAudioFormat: return "Unsupported input audio format"
        case .conversionFailed: return "Audio format conversion failed"
        case .emptyRecording: return "No audio samples were captured"
        case .recordingTooLong: return "The WAV would exceed the 4 GiB format limit"
        case .outputExists: return "Output file already exists"
        }
    }
}

public enum AudioFormat {
    public static let sampleRate: Double = 48_000
    public static let channels: AVAudioChannelCount = 2
    public static var processing: AVAudioFormat {
        AVAudioFormat(commonFormat: .pcmFormatFloat32,
                      sampleRate: sampleRate,
                      channels: channels,
                      interleaved: false)!
    }
}

/// All timestamps are seconds since the same session start. Call from one serial queue.
public final class TimestampedAudioWriter {
    public let url: URL
    public private(set) var frameCount: AVAudioFramePosition = 0
    public private(set) var receivedBuffers = 0
    public private(set) var receivedFrames = 0
    private var file: AVAudioFile?
    private var liveRaw: FileHandle?
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?

    public init(url: URL, liveRawURL: URL? = nil) throws {
        self.url = url
        if let liveRawURL {
            guard !FileManager.default.fileExists(atPath: liveRawURL.path),
                  FileManager.default.createFile(atPath: liveRawURL.path, contents: nil) else {
                throw AudioPipelineError.outputExists
            }
            liveRaw = try FileHandle(forWritingTo: liveRawURL)
        }
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: AudioFormat.sampleRate,
            AVNumberOfChannelsKey: Int(AudioFormat.channels),
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsBigEndianKey: false
        ]
        file = try AVAudioFile(forWriting: url,
                               settings: settings,
                               commonFormat: .pcmFormatFloat32,
                               interleaved: false)
    }

    public func append(_ input: AVAudioPCMBuffer, at seconds: Double) throws {
        guard seconds.isFinite, input.frameLength > 0 else { return }
        guard input.format.channelCount > 0, input.format.sampleRate > 0 else {
            throw AudioPipelineError.invalidAudioFormat
        }
        let sourceFrames = Int(input.frameLength)
        let converted = try convert(input)
        var start = AVAudioFramePosition((seconds * AudioFormat.sampleRate).rounded())
        var drop: AVAudioFrameCount = 0
        if start < frameCount {
            let overlap = frameCount - start
            if overlap >= converted.frameLength { return }
            drop = AVAudioFrameCount(overlap)
            start = frameCount
        }
        try pad(to: start)
        if drop == 0 {
            try file?.write(from: converted)
            try writeLiveRaw(converted)
        } else {
            let length = converted.frameLength - drop
            guard let trimmed = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing,
                                                  frameCapacity: length),
                  let source = converted.floatChannelData,
                  let target = trimmed.floatChannelData else {
                throw AudioPipelineError.invalidAudioFormat
            }
            trimmed.frameLength = length
            for channel in 0..<Int(AudioFormat.channels) {
                target[channel].update(from: source[channel].advanced(by: Int(drop)),
                                       count: Int(length))
            }
            try file?.write(from: trimmed)
            try writeLiveRaw(trimmed)
        }
        frameCount += AVAudioFramePosition(converted.frameLength - drop)
        receivedBuffers += 1
        receivedFrames += sourceFrames
    }

    private func convert(_ input: AVAudioPCMBuffer) throws -> AVAudioPCMBuffer {
        if input.format == AudioFormat.processing { return input }
        if inputFormat != input.format {
            inputFormat = input.format
            converter = AVAudioConverter(from: input.format, to: AudioFormat.processing)
        }
        guard let converter else { throw AudioPipelineError.conversionFailed }
        let estimate = Double(input.frameLength) * AudioFormat.sampleRate / input.format.sampleRate
        let capacity = AVAudioFrameCount(max(1, Int(ceil(estimate)) + 1024))
        guard let output = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing,
                                            frameCapacity: capacity) else {
            throw AudioPipelineError.invalidAudioFormat
        }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            if supplied {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return input
        }
        guard error == nil, status != .error, output.frameLength > 0 else {
            throw error ?? AudioPipelineError.conversionFailed
        }
        return output
    }

    private func pad(to target: AVAudioFramePosition) throws {
        guard target > frameCount else { return }
        guard let blank = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing,
                                           frameCapacity: 8192),
              let channels = blank.floatChannelData else {
            throw AudioPipelineError.invalidAudioFormat
        }
        while frameCount < target {
            let count = AVAudioFrameCount(min(8192, target - frameCount))
            blank.frameLength = count
            for channel in 0..<Int(AudioFormat.channels) {
                channels[channel].initialize(repeating: 0, count: Int(count))
            }
            try file?.write(from: blank)
            try writeLiveRaw(blank)
            frameCount += AVAudioFramePosition(count)
        }
    }

    private func writeLiveRaw(_ buffer: AVAudioPCMBuffer) throws {
        guard let liveRaw, let channels = buffer.floatChannelData else { return }
        let frames = Int(buffer.frameLength)
        var values = [Int16](repeating: 0, count: frames * Int(AudioFormat.channels))
        for frame in 0..<frames {
            for channel in 0..<Int(AudioFormat.channels) {
                let sample = max(-1, min(1, channels[channel][frame]))
                values[frame * Int(AudioFormat.channels) + channel] =
                    Int16(clamping: Int((sample * 32767).rounded())).littleEndian
            }
        }
        try values.withUnsafeBytes { try liveRaw.write(contentsOf: Data($0)) }
    }

    public func finish() {
        file = nil
        try? liveRaw?.close()
        liveRaw = nil
    }
}

/// Reads a small chunk from each CAF track and writes a 16-bit stereo WAV.
public enum AudioMixer {
    public static func export(tracks urls: [URL],
                              to output: URL,
                              duration: Double,
                              gains: [Float] = [],
                              limitToDuration: Bool = false) throws {
        guard !FileManager.default.fileExists(atPath: output.path) else {
            throw AudioPipelineError.outputExists
        }
        let files = try urls.map { try AVAudioFile(forReading: $0) }
        guard !files.isEmpty, files.allSatisfy({ $0.processingFormat.sampleRate == AudioFormat.sampleRate && $0.processingFormat.channelCount == AudioFormat.channels }) else {
            throw AudioPipelineError.invalidAudioFormat
        }
        let recordedFrames = files.map(\.length).max() ?? 0
        let requestedFrames = AVAudioFramePosition(max(0, (duration * AudioFormat.sampleRate).rounded()))
        let totalFrames = limitToDuration ? requestedFrames : max(recordedFrames, requestedFrames)
        guard totalFrames > 0 else { throw AudioPipelineError.emptyRecording }
        // RIFF/WAV stores its data size in a UInt32; stereo PCM16 uses four bytes per frame.
        guard totalFrames <= AVAudioFramePosition((UInt32.max - 256) / 4) else {
            throw AudioPipelineError.recordingTooLong
        }
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: AudioFormat.sampleRate,
            AVNumberOfChannelsKey: Int(AudioFormat.channels),
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false
        ]
        let temporary = output.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString)-\(output.lastPathComponent)")
        do {
            var destination: AVAudioFile? = try AVAudioFile(forWriting: temporary,
                                                            settings: settings,
                                                            commonFormat: .pcmFormatFloat32,
                                                            interleaved: false)
            var position: AVAudioFramePosition = 0
            while position < totalFrames {
                let count = AVAudioFrameCount(min(8192, totalFrames - position))
                guard let mixed = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing,
                                                    frameCapacity: count),
                      let result = mixed.floatChannelData else {
                    throw AudioPipelineError.invalidAudioFormat
                }
                mixed.frameLength = count
                for channel in 0..<Int(AudioFormat.channels) {
                    result[channel].initialize(repeating: 0, count: Int(count))
                }
                for (index, source) in files.enumerated() {
                    guard source.framePosition < source.length else { continue }
                    guard let chunk = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing,
                                                       frameCapacity: count) else {
                        throw AudioPipelineError.invalidAudioFormat
                    }
                    try source.read(into: chunk, frameCount: count)
                    guard let input = chunk.floatChannelData else { continue }
                    let gain = index < gains.count ? gains[index] : 1
                    for channel in 0..<Int(AudioFormat.channels) {
                        for sample in 0..<Int(chunk.frameLength) {
                            result[channel][sample] += input[channel][sample] * gain
                        }
                    }
                }
                for channel in 0..<Int(AudioFormat.channels) {
                    for sample in 0..<Int(count) {
                        result[channel][sample] = min(1, max(-1, result[channel][sample]))
                    }
                }
                try destination?.write(from: mixed)
                position += AVAudioFramePosition(count)
            }
            destination = nil
            try FileManager.default.moveItem(at: temporary, to: output)
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            throw error
        }
    }
}
