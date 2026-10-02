import Foundation

/// 用户可覆盖的客户端配置；空值回退到 gen-secrets.sh 生成的默认值。
enum AppPrefs {
    private static let serverURLKey = "va.serverURL"
    private static let apiKeyKey = "va.apiKey"

    static var serverURL: String {
        get {
            let stored = UserDefaults.standard.string(forKey: serverURLKey)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return stored.isEmpty ? Secrets.defaultServerURL : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: serverURLKey) }
    }

    static var apiKey: String {
        get {
            let stored = UserDefaults.standard.string(forKey: apiKeyKey)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return stored.isEmpty ? Secrets.defaultAPIKey : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: apiKeyKey) }
    }
}
