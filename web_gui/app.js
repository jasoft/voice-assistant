let mediaRecorder;
let audioChunks = [];
let isRecording = false;
let recordTimer;
let startTime;

const micBtn = document.getElementById('mic-btn');
const statusBar = document.getElementById('status-bar');
const orb = document.getElementById('orb');
const cardsContainer = document.getElementById('cards-container');
const mainContent = document.getElementById('main-content');

// 初始化录音器
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
            
            // 如果录音时间太短（可能是误触），则忽略
            const duration = Date.now() - startTime;
            if (duration < 500) {
                console.log('录音时间太短，忽略');
                updateStatus('就绪', 'idle');
                return;
            }
            
            await sendAudioToBackend(audioBlob);
        };
    } catch (err) {
        console.error('无法访问麦克风:', err);
        statusBar.innerText = '无法访问麦克风';
    }
}

function updateStatus(text, state) {
    statusBar.innerText = text;
    orb.className = `orb ${state}`;
    micBtn.className = `mic-btn ${state === 'recording' ? 'recording' : ''}`;
}

function clearCards() {
    cardsContainer.innerHTML = '';
}

function addTranscriptCard(text) {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
        <div class="card-header">
            <svg viewBox="0 0 24 24" width="12" height="12"><path fill="currentColor" d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>
            实时转写
        </div>
        <div class="card-content">${text}</div>
    `;
    cardsContainer.appendChild(card);
    mainContent.scrollTop = mainContent.scrollHeight;
}

function addResponseCard() {
    const card = document.createElement('div');
    card.id = 'response-card';
    card.className = 'card';
    card.innerHTML = `
        <div class="card-header">
            <svg viewBox="0 0 24 24" width="12" height="12"><path fill="currentColor" d="M7 2v11h3v9l7-12h-4l4-8z"/></svg>
            智能回答
        </div>
        <div class="card-footer" id="response-footer">
            <div class="thinking-dots">
                <span class="active"></span><span class="active"></span><span class="active"></span>
            </div>
            正在准备回答
        </div>
        <div class="card-content" id="response-content" style="display:none;"></div>
    `;
    cardsContainer.appendChild(card);
    mainContent.scrollTop = mainContent.scrollHeight;
    return card;
}

function updateResponseCard(reply) {
    const content = document.getElementById('response-content');
    const footer = document.getElementById('response-footer');
    if (content && footer) {
        content.innerText = reply;
        content.style.display = 'block';
        footer.innerHTML = '回答完成';
        footer.style.color = '#128540';
    }
    mainContent.scrollTop = mainContent.scrollHeight;
}

async function sendAudioToBackend(blob) {
    updateStatus('识别中...', 'thinking');
    
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
                addTranscriptCard(data.transcript);
            }
            
            // 显示正在思考
            addResponseCard();
            updateStatus('思考中...', 'thinking');

            if (data.reply) {
                updateResponseCard(data.reply);
                updateStatus('正在回答', 'speaking');
                
                // 播放语音
                if (data.audio_url) {
                    const audio = new Audio(data.audio_url);
                    audio.onended = () => {
                        updateStatus('就绪', 'idle');
                    };
                    audio.play();
                } else {
                    updateStatus('就绪', 'idle');
                }
            } else {
                updateStatus('就绪', 'idle');
            }
        } else {
            updateStatus('出错了', 'idle');
            statusBar.innerText = data.error || '未知错误';
        }
    } catch (err) {
        console.error('发送失败:', err);
        updateStatus('就绪', 'idle');
        statusBar.innerText = '网络请求失败';
    }
}

function startRecording() {
    if (!mediaRecorder || isRecording) return;
    
    clearCards();
    audioChunks = [];
    startTime = Date.now();
    mediaRecorder.start();
    isRecording = true;
    updateStatus('录音中...', 'recording');
}

function stopRecording() {
    if (!mediaRecorder || !isRecording) return;
    
    mediaRecorder.stop();
    isRecording = false;
}

// 按钮交互逻辑
let isPointerDown = false;
let isLongPress = false;

micBtn.addEventListener('mousedown', (e) => {
    isPointerDown = true;
    isLongPress = false;
    
    // 设置长按检测
    recordTimer = setTimeout(() => {
        if (isPointerDown) {
            isLongPress = true;
            startRecording();
        }
    }, 200);
});

micBtn.addEventListener('mouseup', (e) => {
    isPointerDown = false;
    clearTimeout(recordTimer);
    
    if (isLongPress) {
        // 长按结束，停止录音
        stopRecording();
    } else {
        // 短点击切换
        if (!isRecording) {
            startRecording();
        } else {
            stopRecording();
        }
    }
});

micBtn.addEventListener('mouseleave', () => {
    if (isPointerDown && isLongPress) {
        stopRecording();
    }
    isPointerDown = false;
    clearTimeout(recordTimer);
});

// 触摸支持
micBtn.addEventListener('touchstart', (e) => {
    e.preventDefault();
    isPointerDown = true;
    isLongPress = false;
    recordTimer = setTimeout(() => {
        if (isPointerDown) {
            isLongPress = true;
            startRecording();
        }
    }, 200);
});

micBtn.addEventListener('touchend', (e) => {
    e.preventDefault();
    isPointerDown = false;
    clearTimeout(recordTimer);
    if (isLongPress) {
        stopRecording();
    } else {
        if (!isRecording) {
            startRecording();
        } else {
            stopRecording();
        }
    }
});

// 初始化
setupRecorder();
