import Foundation

/// 用户可覆盖的客户端配置；空值回退到 gen-secrets.sh 生成的默认值。
enum AppPrefs {
    private static let serverURLKey = "va.serverURL"
    private static let apiKeyKey = "va.apiKey"

    static var serverURL: String {
        get {
            #if DEBUG
            if let value = ProcessInfo.processInfo.environment["VA_TEST_SERVER"] { return value }
            #endif
            let stored = UserDefaults.standard.string(forKey: serverURLKey)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return stored.isEmpty ? Secrets.defaultServerURL : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: serverURLKey) }
    }

    static var apiKey: String {
        get {
            #if DEBUG
            if let value = ProcessInfo.processInfo.environment["VA_TEST_KEY"] { return value }
            #endif
            let stored = UserDefaults.standard.string(forKey: apiKeyKey)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return stored.isEmpty ? Secrets.defaultAPIKey : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: apiKeyKey) }
    }

    private static let autoPlayKey = "va.autoPlay"

    /// 首段文字到达后自动合成并播放；新安装默认开启，尊重已有设置。
    static var autoPlay: Bool {
        get {
            #if DEBUG
            if ProcessInfo.processInfo.environment["VA_TEST_AUTOPLAY"] == "1" { return true }
            #endif
            return UserDefaults.standard.object(forKey: autoPlayKey) == nil ? true : UserDefaults.standard.bool(forKey: autoPlayKey)
        }
        set { UserDefaults.standard.set(newValue, forKey: autoPlayKey) }
    }
}
