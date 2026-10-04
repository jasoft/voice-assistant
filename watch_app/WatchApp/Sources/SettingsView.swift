import SwiftUI

struct SettingsView: View {
    @State private var serverURL = AppPrefs.serverURL
    @State private var apiKey = AppPrefs.apiKey
    @State private var showAdvanced = false
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("自动播报", isOn: Binding(get: { AppPrefs.autoPlay }, set: { AppPrefs.autoPlay = $0 }))
                } footer: {
                    Text("回答开始显示时准备语音，边生成边播报。")
                }
                Section {
                    Button("连接设置", systemImage: "network") { showAdvanced.toggle() }
                    if showAdvanced {
                        TextField("服务器地址", text: $serverURL)
                        SecureField("访问密钥", text: $apiKey)
                        Button("保存连接") {
                            let value = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard let url = URL(string: value), ["http", "https"].contains(url.scheme ?? ""), url.host != nil else {
                                error = "请输入完整的 http 或 https 地址"
                                return
                            }
                            AppPrefs.serverURL = value
                            AppPrefs.apiKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
                            dismiss()
                        }
                        if let error { Text(error).font(.caption).foregroundStyle(.orange) }
                    }
                } footer: {
                    Text("日常使用无需修改连接。")
                }
            }.navigationTitle("设置")
        }
    }
}
