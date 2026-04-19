let mediaRecorder;
let audioChunks = [];
let isRecording = false;

const recordBtn = document.getElementById('record-btn');
const statusText = document.getElementById('status-text');
const statusIndicator = document.getElementById('status-indicator');
const recordingAnimation = document.getElementById('recording-animation');
const chatHistory = document.getElementById('chat-history');

// 获取麦克风权限
async function setupRecorder() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.ondataavailable = (event) => {
            audioChunks.push(event.data);
        };

        mediaRecorder.onstop = async () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
            audioChunks = [];
            await sendAudioToBackend(audioBlob);
        };
    } catch (err) {
        console.error('无法访问麦克风:', err);
        addMessage('system', '无法访问麦克风，请确保已授予权限。');
    }
}

function addMessage(role, text) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.innerText = text;
    
    messageDiv.appendChild(bubble);
    chatHistory.appendChild(messageDiv);
    
    // 滚动到底部
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function sendAudioToBackend(blob) {
    updateStatus('识别中...', true);
    
    const formData = new FormData();
    formData.append('audio', blob, 'recording.wav');

    try {
        const response = await fetch('/process', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.status === 'success') {
            if (data.transcript) {
                addMessage('user', data.transcript);
            }
            if (data.reply) {
                updateStatus('思考中...', true);
                addMessage('assistant', data.reply);
            }
            updateStatus('就绪', false);
        } else {
            addMessage('system', '出错了: ' + (data.error || '未知错误'));
            updateStatus('就绪', false);
        }
    } catch (err) {
        console.error('发送失败:', err);
        addMessage('system', '网络请求失败，请检查后端服务是否运行。');
        updateStatus('就绪', false);
    }
}

function updateStatus(text, showLoading) {
    statusText.innerText = text;
    recordingAnimation.style.display = showLoading ? 'flex' : 'none';
}

function startRecording() {
    if (!mediaRecorder) return;
    
    audioChunks = [];
    mediaRecorder.start();
    isRecording = true;
    recordBtn.classList.add('recording');
    updateStatus('录音中...', true);
}

function stopRecording() {
    if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
    
    mediaRecorder.stop();
    isRecording = false;
    recordBtn.classList.remove('recording');
    updateStatus('识别中...', true);
}

// 绑定事件
recordBtn.addEventListener('mousedown', startRecording);
recordBtn.addEventListener('mouseup', stopRecording);
recordBtn.addEventListener('mouseleave', () => {
    if (isRecording) stopRecording();
});

// 适配触摸设备
recordBtn.addEventListener('touchstart', (e) => {
    e.preventDefault();
    startRecording();
});
recordBtn.addEventListener('touchend', (e) => {
    e.preventDefault();
    stopRecording();
});

// 初始化
setupRecorder();
addMessage('system', '欢迎回来，大王！');
