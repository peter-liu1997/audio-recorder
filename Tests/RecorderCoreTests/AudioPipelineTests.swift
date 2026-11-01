import AVFoundation
import Foundation
import RecorderCore

@main
struct AudioPipelineTests {
    static func main() throws {
        try timestampGapsAndMixedWAV()
        try monoResamplingAndOverlap()
        try emptySecondaryTrack()
        try rightChannelAndDurationLimit()
        try liveRawTap()
        print("Audio pipeline smoke checks passed")
    }

    static func timestampGapsAndMixedWAV() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let micURL = root.appendingPathComponent("mic.caf")
        let systemURL = root.appendingPathComponent("system.caf")
        let wavURL = root.appendingPathComponent("result.wav")
        let format = AudioFormat.processing
        func samples(_ value: Float, count: Int) -> AVAudioPCMBuffer {
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count))!
            buffer.frameLength = AVAudioFrameCount(count)
            for channel in 0..<2 {
                buffer.floatChannelData![channel].initialize(repeating: value, count: count)
            }
            return buffer
        }
        let mic = try TimestampedAudioWriter(url: micURL)
        let system = try TimestampedAudioWriter(url: systemURL)
        try mic.append(samples(0.25, count: 480), at: 0)
        try system.append(samples(0.5, count: 480), at: 0.005)
        mic.finish()
        system.finish()
        try AudioMixer.export(tracks: [micURL, systemURL], to: wavURL, duration: 0.02)
        let file = try AVAudioFile(forReading: wavURL)
        assert(file.fileFormat.sampleRate == 48_000)
        assert(file.fileFormat.channelCount == 2)
        assert(file.length == 960)
        let result = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 960)!
        try file.read(into: result)
        assert(abs(result.floatChannelData![0][0] - 0.25) < 0.002)
        assert(abs(result.floatChannelData![0][300] - 0.75) < 0.002)
        assert(abs(result.floatChannelData![0][800]) < 0.002)
    }

    static func monoResamplingAndOverlap() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let trackURL = root.appendingPathComponent("resample.caf")
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
        let sample = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 160)!
        sample.frameLength = 160
        sample.floatChannelData![0].initialize(repeating: 0.2, count: 160)
        let track = try TimestampedAudioWriter(url: trackURL)
        try track.append(sample, at: 0)
        try track.append(sample, at: 0.005)
        track.finish()
        assert(track.frameCount >= 700)
        assert(track.frameCount <= 730)
        let file = try AVAudioFile(forReading: trackURL)
        assert(file.fileFormat.sampleRate == 48_000)
        assert(file.fileFormat.channelCount == 2)
    }

    static func emptySecondaryTrack() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let activeURL = root.appendingPathComponent("active.caf")
        let silentURL = root.appendingPathComponent("silent.caf")
        let outputURL = root.appendingPathComponent("result.wav")
        let sample = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing, frameCapacity: 480)!
        sample.frameLength = 480
        for channel in 0..<2 { sample.floatChannelData![channel].initialize(repeating: 0.1, count: 480) }
        let active = try TimestampedAudioWriter(url: activeURL)
        let silent = try TimestampedAudioWriter(url: silentURL)
        try active.append(sample, at: 0)
        active.finish()
        silent.finish()
        try AudioMixer.export(tracks: [activeURL, silentURL], to: outputURL, duration: 0.01)
        let output = try AVAudioFile(forReading: outputURL)
        assert(output.length == 480)
    }

    static func rightChannelAndDurationLimit() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let trackURL = root.appendingPathComponent("right.caf")
        let outputURL = root.appendingPathComponent("right.wav")
        let sample = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing, frameCapacity: 4800)!
        sample.frameLength = 4800
        sample.floatChannelData![0].initialize(repeating: 0, count: 4800)
        sample.floatChannelData![1].initialize(repeating: 0.4, count: 4800)
        let writer = try TimestampedAudioWriter(url: trackURL)
        try writer.append(sample, at: 0)
        writer.finish()
        try AudioMixer.export(tracks: [trackURL], to: outputURL,
                              duration: 0.01, limitToDuration: true)
        let file = try AVAudioFile(forReading: outputURL)
        assert(file.length == 480)
        let result = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 480)!
        try file.read(into: result)
        assert(abs(result.floatChannelData![0][240]) < 0.002)
        assert(abs(result.floatChannelData![1][240] - 0.4) < 0.002)
    }

    static func liveRawTap() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let rawURL = root.appendingPathComponent("microphone.raw")
        let sample = AVAudioPCMBuffer(pcmFormat: AudioFormat.processing, frameCapacity: 480)!
        sample.frameLength = 480
        for channel in 0..<2 { sample.floatChannelData![channel].initialize(repeating: 0.25, count: 480) }
        let writer = try TimestampedAudioWriter(url: root.appendingPathComponent("track.caf"),
                                                liveRawURL: rawURL)
        try writer.append(sample, at: 0.01)
        let raw = try Data(contentsOf: rawURL)
        assert(raw.count == 960 * 4)
        assert(raw.prefix(480 * 4).allSatisfy { $0 == 0 })
        let firstAudio = raw.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: 480 * 4, as: Int16.self) }
        assert(abs(Int(firstAudio) - 8192) <= 1)
        writer.finish()
    }
}
