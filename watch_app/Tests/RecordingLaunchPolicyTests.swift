// swiftc WatchApp/Sources/RecordingLaunchPolicy.swift Tests/RecordingLaunchPolicyTests.swift -o /tmp/watch-launch-tests
@main
enum RecordingLaunchPolicyTests {
    static func main() {
        var policy = RecordingLaunchPolicy()
        assert(policy.consumeActivation(), "首次打开应自动录音")
        assert(!policy.consumeActivation(), "onAppear 和 active 不应重复录音")
        // 暗屏仅 inactive，不发送 didEnterBackground；再次 active 保留回答。
        assert(!policy.consumeActivation(), "暗屏亮屏不应重录")
        assert(!policy.consumeActivation(), "关闭设置不应重录")
        policy.didEnterBackground()
        assert(policy.consumeActivation(), "离开后重新点图标应录音")
        assert(!policy.consumeActivation(), "每次进入只录音一次")
        policy.didEnterBackground()
        policy.didEnterBackground()
        assert(policy.consumeActivation(), "多次后台通知仍可重新进入")
        assert(!policy.consumeActivation(), "重复后台通知不应累积请求")
        print("RecordingLaunchPolicy: 8 checks passed")
    }
}
