// swiftc -parse-as-library WatchApp/Sources/{SpeechPlayback,SpeechTextBuffer}.swift Tests/SpeechPlaybackTests.swift -o /tmp/watch-playback-tests
import Foundation

// Compile the real UI controller against a slow audio boundary and controlled PCM stream.
actor PCMStreamPlayer {
    private(set) var scheduledBytes = 0
    private(set) var playedBytes: [Int] = []
    private(set) var operations: [String] = []
    private(set) var pendingBuffers = 0

    func start() throws {
        operations.append("start")
        scheduledBytes = 0
    }
    func schedule(_ pcm: Data) { scheduledBytes += pcm.count }
    func play() { playedBytes.append(scheduledBytes) }
    func stop() {
        Thread.sleep(forTimeInterval: 0.35)
        operations.append("stop")
    }
}

final class APIClient {
    var streams: [AsyncThrowingStream<Data, Error>.Continuation] = []
    func streamSpeech(text: String, serverBase: String, apiKey: String) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { streams.append($0) }
    }
}
enum AppPrefs {
    static let serverURL = "fixture"
    static let apiKey = "fixture"
}
enum APIError: Error { case transport(String) }

@main
struct SpeechPlaybackTests {
    @MainActor static func waitUntil(_ condition: () async -> Bool) async {
        for _ in 0..<300 {
            if await condition() { return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        fatalError("Timed out waiting for playback state")
    }

    @MainActor static func main() async {
        let player = PCMStreamPlayer()
        let api = APIClient()
        let speech = SpeechPlayback(player: player, api: api)
        speech.begin(); speech.accept("长语音。"); speech.finishInput()
        await waitUntil { api.streams.count == 1 }
        for _ in 0..<49 { api.streams[0].yield(Data(count: 4800)) }
        await waitUntil { await player.scheduledBytes == 235_200 }
        assert(speech.status == .preparing)
        let earlyPlays = await player.playedBytes
        assert(earlyPlays.isEmpty, "4.9 seconds must remain silent")
        api.streams[0].yield(Data(count: 4800))
        await waitUntil { speech.status == .playing }
        let plays = await player.playedBytes
        assert(plays == [240_000], "Start only after 5 seconds of 24kHz/16-bit mono PCM")

        let clock = ContinuousClock()
        let before = clock.now
        speech.stop()
        assert(speech.status == .stopped)
        assert(before.duration(to: clock.now) < .milliseconds(100), "Stop must not wait for audio teardown")
        try? await Task.sleep(nanoseconds: 30_000_000)
        assert(before.duration(to: clock.now) < .milliseconds(200), "Main actor must tick while audio teardown blocks")
        await speech.waitForAudioSessionRelease()

        // Short replies flush at EOF, without waiting to accumulate 5 seconds.
        speech.begin(); speech.accept("短语音。"); speech.finishInput()
        await waitUntil { api.streams.count == 2 }
        api.streams[1].yield(Data(count: 24_000))
        await waitUntil { await player.scheduledBytes == 24_000 }
        assert(speech.status == .preparing)
        api.streams[1].finish()
        await waitUntil { speech.status == .stopped }
        await speech.waitForAudioSessionRelease()
        let shortPlays = await player.playedBytes
        assert(shortPlays == [240_000, 24_000])

        // Cancel while prebuffering, then immediately replay; stale audio cannot restart playback.
        speech.begin(); speech.accept("取消这段。"); speech.finishInput()
        await waitUntil { api.streams.count == 3 }
        api.streams[2].yield(Data(count: 48_000))
        await waitUntil { await player.scheduledBytes == 48_000 }
        speech.stop()
        speech.begin(); speech.accept("重新播放。"); speech.finishInput()
        api.streams[2].yield(Data(count: 240_000)); api.streams[2].finish()
        try? await Task.sleep(nanoseconds: 30_000_000)
        assert(speech.status == .preparing && api.streams.count == 3, "UI remains responsive while restart waits for teardown")
        await waitUntil { api.streams.count == 4 }
        api.streams[3].yield(Data(count: 9600)); api.streams[3].finish()
        await waitUntil { speech.status == .stopped }
        await speech.waitForAudioSessionRelease()
        let replayPlays = await player.playedBytes
        assert(replayPlays == [240_000, 24_000, 9600], "Unexpected replay history: \(replayPlays)")
        let operations = await player.operations
        assert(operations == ["start", "stop", "start", "stop", "start", "stop", "start", "stop"])

        speech.begin(); speech.accept("空音频。"); speech.finishInput()
        await waitUntil { api.streams.count == 5 }
        api.streams[4].finish()
        await waitUntil { speech.status == .stopped }
        assert(speech.error != nil, "Empty TTS must report failure")
        await speech.waitForAudioSessionRelease()
        print("SpeechPlayback: 5s prebuffer, short EOF, immediate stop, serialized replay and empty audio passed")
    }
}
