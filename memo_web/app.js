const instruction = document.querySelector('#instruction');
const sendButton = document.querySelector('#send');
const statusTip = document.querySelector('#status');
const welcomeCard = document.querySelector('#welcome-card');
const replyCard = document.querySelector('#reply-card');
const reply = document.querySelector('#reply');
const copyButton = document.querySelector('#copy-btn');
const clearButton = document.querySelector('#clear-btn');
const loadingIndicator = document.querySelector('#loading-indicator');
const chatContainer = document.querySelector('#chat-container');
const toastEl = document.querySelector('#toast');
const chips = document.querySelectorAll('.chip');
const userQueryBox = document.querySelector('#user-query-box');
const userQueryText = document.querySelector('#user-query-text');

let lastReplyText = '';
let toastTimer = null;

// 动态自适应调整 Textarea 高度
function autoResizeTextarea() {
  instruction.style.height = 'auto';
  const newHeight = Math.min(instruction.scrollHeight, 120);
  instruction.style.height = `${Math.max(newHeight, 24)}px`;
}

instruction.addEventListener('input', autoResizeTextarea);

function setBusy(busy) {
  sendButton.disabled = busy;
  instruction.disabled = busy;
  chips.forEach(chip => chip.disabled = busy);
  if (busy) {
    loadingIndicator.hidden = false;
    statusTip.textContent = 'DeepSeek Harness 正在查询…';
    statusTip.classList.add('busy');
    scrollToBottom();
  } else {
    loadingIndicator.hidden = true;
    statusTip.textContent = '准备好了';
    statusTip.classList.remove('busy');
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    chatContainer.scrollTop = chatContainer.scrollHeight;
  });
}

function showToast(message) {
  if (toastTimer) clearTimeout(toastTimer);
  toastEl.textContent = message;
  toastEl.hidden = false;
  // 触觉反馈（移动端震动）
  if (navigator.vibrate) {
    try {
      navigator.vibrate(40);
    } catch (_) {}
  }
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, 2000);
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
  lastReplyText = cleanedText;
  if (window.marked) {
    reply.innerHTML = marked.parse(cleanedText || 'Memo 没有返回文字。');
  } else {
    reply.textContent = cleanedText || 'Memo 没有返回文字。';
  }
  scrollToBottom();
}

function showError(message) {
  replyCard.hidden = false;
  welcomeCard.hidden = true;
  reply.textContent = message;
  reply.classList.add('error');
  scrollToBottom();
}

async function sendInstruction(customText) {
  const text = (customText !== undefined ? customText : instruction.value).trim();
  if (!text || sendButton.disabled) return;

  welcomeCard.hidden = true;
  replyCard.hidden = true;
  reply.classList.remove('error');
  if (userQueryBox && userQueryText) {
    userQueryText.textContent = text;
    userQueryBox.hidden = false;
  }
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
    statusTip.textContent = '检索完成';
    statusTip.classList.remove('busy');
    // 发送成功后清空输入框并重置高度
    instruction.value = '';
    autoResizeTextarea();
  } catch (error) {
    showError(error instanceof Error ? error.message : '请求失败，请稍后重试。');
    statusTip.textContent = '请求失败';
    statusTip.classList.remove('busy');
  } finally {
    setBusy(false);
  }
}

// 绑定快捷气泡点击一键发送
chips.forEach(chip => {
  chip.addEventListener('click', () => {
    const query = chip.textContent.trim();
    if (query) {
      instruction.value = query;
      autoResizeTextarea();
      sendInstruction(query);
    }
  });
});

// 发送按钮与回车事件
sendButton.addEventListener('click', () => sendInstruction());

instruction.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    // 桌面/外接键盘：Enter 或 ⌘/Ctrl+Enter 发送；手机端虚拟键盘：若为非换行习惯，Enter 直接发送
    if (!event.shiftKey) {
      event.preventDefault();
      sendInstruction();
    }
  }
});

// 一键复制
if (copyButton) {
  copyButton.addEventListener('click', async () => {
    if (!lastReplyText) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(lastReplyText);
      } else {
        const temp = document.createElement('textarea');
        temp.value = lastReplyText;
        document.body.appendChild(temp);
        temp.select();
        document.execCommand('copy');
        document.body.removeChild(temp);
      }
      showToast('已复制到剪贴板');
    } catch (_) {
      showToast('复制失败，请手动选择');
    }
  });
}

// 清空对话
if (clearButton) {
  clearButton.addEventListener('click', () => {
    replyCard.hidden = true;
    if (userQueryBox) userQueryBox.hidden = true;
    welcomeCard.hidden = false;
    lastReplyText = '';
    instruction.value = '';
    autoResizeTextarea();
    statusTip.textContent = '已清空';
    showToast('已重置');
  });
}

// 获取版本号并展示在 Header
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
  } catch (_) {}
}

loadAppVersion();

// 软键盘弹起优化 (iOS & Android visualViewport)
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', () => {
    scrollToBottom();
  });
}
