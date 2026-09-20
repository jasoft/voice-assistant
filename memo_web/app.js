const instruction = document.querySelector('#instruction');
const sendButton = document.querySelector('#send');
const status = document.querySelector('#status');
const replyCard = document.querySelector('#reply-card');
const reply = document.querySelector('#reply');

function setBusy(busy) {
  sendButton.disabled = busy;
  instruction.disabled = busy;
  sendButton.textContent = busy ? '等待中…' : '发送';
  status.textContent = busy ? 'Memo 正在处理…' : '准备好了';
  status.classList.toggle('busy', busy);
}

function hideMemoIds(text) {
  if (!text) return '';
  let cleaned = String(text);
  // 1. 匹配带括号包围的 ID：(ID: memos/xxx), (id: memos/xxx), [ID: memos/xxx], (memos/xxx), 【ID: memos/xxx】等
  cleaned = cleaned.replace(/[\(（\[【]\s*(?:ID[:：]\s*)?(?:memos\/[A-Za-z0-9_-]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*[\)）\]】]/gi, '');
  // 2. 匹配如 ID: memos/xxx, 编号: memos/xxx
  cleaned = cleaned.replace(/(?:ID|编号|id)[:：]\s*(?:memos\/[A-Za-z0-9_-]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/gi, '');
  // 3. 匹配裸露的 memos/xxx
  cleaned = cleaned.replace(/\bmemos\/[A-Za-z0-9_-]+\b/g, '');
  // 4. 清理多余空括号
  cleaned = cleaned.replace(/[\(（]\s*[\)）]/g, '');
  // 5. 清理每行尾部多余空白
  cleaned = cleaned.split('\n').map(line => line.replace(/[ \t]+$/, '')).join('\n').trim();
  return cleaned;
}

function renderReply(rawText) {
  const cleanedText = hideMemoIds(rawText);
  if (window.marked) {
    reply.innerHTML = marked.parse(cleanedText || 'Memo 没有返回文字。');
  } else {
    reply.textContent = cleanedText || 'Memo 没有返回文字。';
  }
}

function showError(message) {
  replyCard.hidden = false;
  reply.textContent = message;
  reply.classList.add('error');
}

async function sendInstruction() {
  const text = instruction.value.trim();
  if (!text || sendButton.disabled) return;

  replyCard.hidden = true;
  reply.classList.remove('error');
  setBusy(true);

  try {
    const response = await fetch('/api/query', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({instruction: text}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `请求失败（${response.status}）`);
    }
    replyCard.hidden = false;
    renderReply(payload.reply || '');
    status.textContent = '已完成';
    status.classList.remove('busy');
  } catch (error) {
    showError(error instanceof Error ? error.message : '请求失败，请稍后重试。');
    status.textContent = '请求失败';
    status.classList.remove('busy');
  } finally {
    sendButton.disabled = false;
    instruction.disabled = false;
    sendButton.textContent = '发送';
  }
}

sendButton.addEventListener('click', sendInstruction);
instruction.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault();
    sendInstruction();
  }
});

async function loadAppVersion() {
  const versionEl = document.querySelector('#app-version');
  if (!versionEl) return;
  try {
    const res = await fetch('/api/version');
    if (res.ok) {
      const data = await res.json();
      if (data && data.version) {
        versionEl.textContent = `v${data.version}`;
      }
    }
  } catch (_) {
    // 忽略版本加载错误
  }
}

loadAppVersion();

