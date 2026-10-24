// balatro-recorder: record the Balatro window with ScreenCaptureKit, and make timelapses.
//
//   balatro-recorder record <out.mov> [--app Balatro] [--fps 30] [--wait 60]
//       Records the game window (falls back to the main display) until SIGINT/SIGTERM.
//   balatro-recorder speedup <in.mov> <out.mov> <factor> [--fps 30]
//       Writes a sped-up copy (e.g. factor 4 = 4x faster), resampled to a normal frame rate.
//
// Needs macOS 15+ and Screen Recording permission for the app that runs it
// (System Settings > Privacy & Security > Screen & System Audio Recording).
// Build: swiftc -parse-as-library -O tools/recorder.swift -o tools/bin/balatro-recorder

import AVFoundation
import Foundation
import ScreenCaptureKit

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(("balatro-recorder: " + msg + "\n").data(using: .utf8)!)
    exit(1)
}

func option(_ args: [String], _ name: String, _ fallback: String) -> String {
    if let i = args.firstIndex(of: name), i + 1 < args.count { return args[i + 1] }
    return fallback
}

final class RecordingDelegate: NSObject, SCRecordingOutputDelegate, SCStreamDelegate {
    var finished: CheckedContinuation<Void, Never>?
    var failure: Error?

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        finished?.resume()
        finished = nil
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        failure = error
        finished?.resume()
        finished = nil
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        failure = error
        finished?.resume()
        finished = nil
    }
}

func findTarget(app: String, waitSeconds: Int) async throws -> (SCContentFilter, CGSize) {
    let deadline = Date().addingTimeInterval(TimeInterval(waitSeconds))
    while true {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let match = content.windows.filter { w in
            let owner = w.owningApplication?.applicationName ?? ""
            let title = w.title ?? ""
            return (owner.localizedCaseInsensitiveContains(app) || title.localizedCaseInsensitiveContains(app))
                && w.frame.width > 200 && w.frame.height > 200
        }.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
        if let w = match {
            let scale = NSScreenScale()
            return (SCContentFilter(desktopIndependentWindow: w),
                    CGSize(width: w.frame.width * scale, height: w.frame.height * scale))
        }
        if Date() > deadline, let display = content.displays.first {
            FileHandle.standardError.write("balatro-recorder: no '\(app)' window found, recording the whole display\n".data(using: .utf8)!)
            let scale = NSScreenScale()
            return (SCContentFilter(display: display, excludingWindows: []),
                    CGSize(width: CGFloat(display.width) * scale, height: CGFloat(display.height) * scale))
        }
        try await Task.sleep(nanoseconds: 1_000_000_000)
    }
}

func NSScreenScale() -> CGFloat {
    // Backing scale of the main display (2 on Retina).
    let id = CGMainDisplayID()
    guard let mode = CGDisplayCopyDisplayMode(id) else { return 2 }
    return CGFloat(mode.pixelWidth) / CGFloat(max(1, mode.width))
}

func record(_ args: [String]) async {
    guard args.count >= 1 else { fail("usage: record <out.mov> [--app Balatro] [--fps 30] [--wait 60]") }
    let url = URL(fileURLWithPath: args[0])
    let app = option(args, "--app", "Balatro")
    let fps = Int32(option(args, "--fps", "30")) ?? 30
    let wait = Int(option(args, "--wait", "60")) ?? 60
    try? FileManager.default.removeItem(at: url)
    // Ignore stop signals until capture is running, so an early Ctrl-C can't lose the file.
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)

    do {
        let (filter, size) = try await findTarget(app: app, waitSeconds: wait)
        let config = SCStreamConfiguration()
        config.width = Int(size.width) & ~1
        config.height = Int(size.height) & ~1
        config.minimumFrameInterval = CMTime(value: 1, timescale: fps)
        config.showsCursor = false
        config.capturesAudio = false
        config.queueDepth = 6

        let delegate = RecordingDelegate()
        let stream = SCStream(filter: filter, configuration: config, delegate: delegate)
        let recConfig = SCRecordingOutputConfiguration()
        recConfig.outputURL = url
        recConfig.outputFileType = .mov
        recConfig.videoCodecType = .hevc
        let output = SCRecordingOutput(configuration: recConfig, delegate: delegate)
        try stream.addRecordingOutput(output)
        try await stream.startCapture()
        print("recording \(Int(size.width))x\(Int(size.height)) @\(fps)fps -> \(url.path)")
        fflush(stdout)

        // Wait for Ctrl-C / SIGTERM.
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            var resumed = false
            for sig in [SIGINT, SIGTERM] {
                let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
                src.setEventHandler {
                    if !resumed { resumed = true; cont.resume() }
                }
                src.resume()
                Sources.keep.append(src)
            }
        }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            delegate.finished = cont
            Task { try? await stream.stopCapture() }
        }
        if let err = delegate.failure { fail("recording failed: \(err.localizedDescription)") }
        print("saved \(url.path)")
    } catch {
        fail("\(error.localizedDescription) (grant Screen Recording permission to your terminal app)")
    }
}

enum Sources { static var keep: [DispatchSourceSignal] = [] }

func speedup(_ args: [String]) async {
    guard args.count >= 3, let factor = Double(args[2]), factor > 0 else {
        fail("usage: speedup <in.mov> <out.mov> <factor> [--fps 30]")
    }
    let inURL = URL(fileURLWithPath: args[0])
    let outURL = URL(fileURLWithPath: args[1])
    let fps = Int32(option(args, "--fps", "30")) ?? 30
    try? FileManager.default.removeItem(at: outURL)
    do {
        let asset = AVURLAsset(url: inURL)
        guard let track = try await asset.loadTracks(withMediaType: .video).first else { fail("no video track") }
        let duration = try await asset.load(.duration)
        let comp = AVMutableComposition()
        guard let ct = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            fail("cannot create composition track")
        }
        let range = CMTimeRange(start: .zero, duration: duration)
        try ct.insertTimeRange(range, of: track, at: .zero)
        ct.preferredTransform = try await track.load(.preferredTransform)
        comp.scaleTimeRange(range, toDuration: CMTimeMultiplyByFloat64(duration, multiplier: 1.0 / factor))

        // Resample to a normal frame rate so the output isn't hundreds of fps.
        let size = try await track.load(.naturalSize)
        let video = AVMutableVideoComposition()
        video.renderSize = size
        video.frameDuration = CMTime(value: 1, timescale: fps)
        let instruction = AVMutableVideoCompositionInstruction()
        instruction.timeRange = CMTimeRange(start: .zero, duration: comp.duration)
        instruction.layerInstructions = [AVMutableVideoCompositionLayerInstruction(assetTrack: ct)]
        video.instructions = [instruction]

        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHEVCHighestQuality) else {
            fail("cannot create export session")
        }
        export.videoComposition = video
        try await export.export(to: outURL, as: .mov)
        print("saved \(outURL.path) (\(String(format: "%.1f", duration.seconds / factor))s)")
    } catch {
        fail("speedup failed: \(error.localizedDescription)")
    }
}

@main
struct Main {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        switch args.first {
        case "record": await record(Array(args.dropFirst()))
        case "speedup": await speedup(Array(args.dropFirst()))
        default: fail("usage: balatro-recorder record|speedup ...")
        }
    }
}
