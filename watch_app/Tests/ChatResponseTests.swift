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

        // Test makeEndpointURL
        let url1 = APIClient.makeEndpointURL(serverBase: "https://va.soj.myds.me:1443", endpointPath: "/v1/chat")
        assert(url1?.absoluteString == "https://va.soj.myds.me:1443/v1/chat")

        let url2 = APIClient.makeEndpointURL(serverBase: "http://docker.home:10031/v1", endpointPath: "/v1/chat")
        assert(url2?.absoluteString == "http://docker.home:10031/v1/chat")

        let url3 = APIClient.makeEndpointURL(serverBase: "http://docker.home:10031/v1/", endpointPath: "/v1/transcribe")
        assert(url3?.absoluteString == "http://docker.home:10031/v1/transcribe")

        print("ChatResponse: spoken preference and legacy fallback passed")
    }
}
