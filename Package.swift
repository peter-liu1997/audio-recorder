// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "audio-recorder",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "audio-recorder", targets: ["AudioRecorder"]),
        .executable(name: "meeting-assistant", targets: ["MeetingAssistant"]),
        .executable(name: "audio-pipeline-smoke", targets: ["AudioPipelineSmoke"])
    ],
    targets: [
        .target(name: "RecorderCore"),
        .executableTarget(name: "AudioRecorder", dependencies: ["RecorderCore"]),
        .executableTarget(name: "MeetingAssistant"),
        .executableTarget(name: "AudioPipelineSmoke", dependencies: ["RecorderCore"], path: "Tests/RecorderCoreTests")
    ]
)
