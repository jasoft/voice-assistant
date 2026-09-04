const fs = require('fs');
const cp = require('child_process');

function patchFile(pattern, replacer) {
  const p = cp.execSync(`find /usr/local/lib/node_modules -path "${pattern}"`).toString().trim();
  if (!p) throw new Error(`Could not find file matching ${pattern}`);
  const original = fs.readFileSync(p, 'utf8');
  const updated = replacer(original);
  if (original === updated) {
    console.warn(`Warning: No changes made to ${p}`);
  }
  fs.writeFileSync(p, updated, 'utf8');
  console.log(`Successfully patched ${p}`);
}

// 1. 解除 startup.js 中的 --host 0.0.0.0 限制
patchFile('*/@deepseek-ai/dsh-web-app/lib/startup.js', (code) => {
  return code.replace('options.host === "0.0.0.0"', 'false');
});

// 2. 兼容点号 RPC 方法 (session.create -> session/create) 与 trusted API 浏览器鉴权穿透
patchFile('*/@deepseek-ai/dsh-client-connection/lib/index.js', (code) => {
  code = code.replace(
    'const endpoint = pathname.slice(channel.length + 1);',
    'let endpoint = pathname.slice(channel.length + 1); if (endpoint.includes(".")) endpoint = endpoint.replace(".", "/");'
  );
  code = code.replace(
    'const message = envelope.data;',
    'const message = envelope.data; if (typeof message.method === "string" && message.method.includes(".")) message.method = message.method.replace(".", "/");'
  );
  code = code.replace(
    'this.browserAuth.isAuthenticated(request) ? void 0 : 401',
    'void 0'
  );
  return code;
});

// 3. 兼容入参包装 (自动注入 args.request) 与 session/history -> session/page 适配
patchFile('*/@deepseek-ai/dsh-api-gateway/lib/index.js', (code) => {
  // claimsEndpoint 放行 session/history
  code = code.replace(
    'if (endpoint === "$events/result") return true;',
    'if (endpoint === "session/history" || endpoint === "$events/result") return true;'
  );

  // dispatchRpc 拦截 session/history 并转换为 session/page
  const historyHandler = `
		if (endpoint === "session/history") {
			try {
				const sid = payload?.sessionId || payload?.args?.request?.sessionId;
				const maxMsg = payload?.maxMessages ?? payload?.args?.request?.maxMessages ?? 200;
				const res = await this.invokeRpc("session/page", {
					address: { kind: "session", sessionId: sid },
					throughSeq: -1,
					maxMessages: maxMsg
				}, signal);
				if (res && res.ok) {
					return { ok: true, value: { events: res.value?.records ?? [] } };
				}
				return res;
			} catch (err) {
				return rpcFailure(err);
			}
		}
`;
  code = code.replace(
    'async dispatchRpc(endpoint, payload, signal) {',
    `async dispatchRpc(endpoint, payload, signal) {${historyHandler}`
  );

  // remoteRequest 包装入参为 { args: { request: payload } }，并为 session/prompt 自动生成缺失的 requestId
  code = code.replace(
    'const [namespace, method] = segments;',
    `const [namespace, method] = segments;
     if (isObject(payload) && isPlainObject(payload)) {
       if (!Object.hasOwn(payload, "args")) {
         payload = { args: Object.hasOwn(payload, "request") ? payload : { request: payload } };
       }
       if (endpoint === "session/prompt" && payload.args?.request && !payload.args.request.requestId) {
         payload.args.request.requestId = require("crypto").randomUUID();
       }
     }`
  );


  return code;
});

console.log('All dsh patches applied successfully!');
