import SwiftUI

struct SettingsView: View {
    @State private var serverURL: String = AppPrefs.serverURL
    @State private var apiKey: String = AppPrefs.apiKey
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://…", text: $serverURL)
                    Text("局域网可用 http://docker.home:10031")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("服务器地址")
                }
                Section {
                    SecureField("PTT_API_KEY", text: $apiKey)
                } header: {
                    Text("API Key")
                }
                Button("保存") {
                    AppPrefs.serverURL = serverURL
                    AppPrefs.apiKey = apiKey
                    dismiss()
                }
            }
            .navigationTitle("设置")
        }
    }
}
