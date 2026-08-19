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
    reply.textContent = payload.reply || 'Memo 没有返回文字。';
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
