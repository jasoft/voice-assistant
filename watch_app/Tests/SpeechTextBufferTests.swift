// swiftc WatchApp/Sources/SpeechTextBuffer.swift Tests/SpeechTextBufferTests.swift -o /tmp/watch-speech-tests
@main
struct SpeechTextBufferTests {
    static func main() {
        var buffer = SpeechTextBuffer()
        assert(buffer.append("先看结").isEmpty)
        assert(buffer.append("论。\n第二句！未完") == ["先看结论。", "第二句！"])
        assert(buffer.flush() == "未完")
        assert(buffer.flush() == nil)
        assert(buffer.append(String(repeating: "字", count: 130)).map(\.count) == [60, 60])
        assert(buffer.flush()?.count == 10)
        print("SpeechTextBuffer: segmentation and no duplicate tails passed")
    }
}
