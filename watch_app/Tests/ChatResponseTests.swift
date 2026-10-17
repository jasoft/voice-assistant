import Foundation

@main
struct ChatResponseTests {
    static func main() throws {
        let decoder = JSONDecoder()
        let response = try decoder.decode(ChatResponse.self, from: Data("""
        {"reply":"**南京**：22℃，湿度85%","spoken_reply":" 南京现在二十二度，湿度百分之八十五。 "}
        """.utf8))
        assert(response.reply == "**南京**：22℃，湿度85%")
        assert(response.speechText == "南京现在二十二度，湿度百分之八十五。")
        for spoken in [nil, NSNull(), "  \n"] as [Any?] {
            var payload: [String: Any] = ["reply": "兼容旧服务"]
            payload["spoken_reply"] = spoken
            let data = try JSONSerialization.data(withJSONObject: payload)
            let legacy = try decoder.decode(ChatResponse.self, from: data)
            assert(legacy.speechText == "兼容旧服务")
        }
        print("ChatResponse: spoken preference and legacy fallback passed")
    }
}
