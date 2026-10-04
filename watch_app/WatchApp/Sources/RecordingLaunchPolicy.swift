/// 后台重新进入前台时开始新录音；前台内的暗屏/亮屏不产生新请求。
struct RecordingLaunchPolicy {
    private var needsRecording = true

    mutating func didEnterBackground() {
        needsRecording = true
    }

    mutating func consumeActivation() -> Bool {
        guard needsRecording else { return false }
        needsRecording = false
        return true
    }
}
