from __future__ import annotations

import os
import re
import time
import contextlib
import subprocess
from pathlib import Path
from ..utils.logging import log
from ..utils.shell import ensure_bin

TTS_STOP_SIGNAL_FILENAME = "stop_tts"

def sanitize_for_tts(text: str) -> str:
    # 1. 基础清理：去掉代码块、链接等
    text = re.sub(r"`{1,3}.*?`{1,3}", "", text, flags=re.DOTALL)
    text = re.sub(r"\[(.*?)\]\((https?://[^\s)]+)\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    
    # 2. Markdown 格式清理
    text = re.sub(r"[*_#~]", "", text)  # 去掉粗体、斜体、标题符号、删除线
    
    # 3. 结构化清理与自然停顿
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"-{3,}", line): # 去掉分割线
            continue
        
        # 处理列表项：把 - 或 * 开头的列表换成更自然的表达或加上停顿
        # 比如："- 苹果" 变成 "，苹果" 或者是单纯去掉符号
        line = re.sub(r"^\s*[-*+]\s+", "，", line)
        line = re.sub(r"^\s*\d+\.\s+", "，", line)
        
        # 去掉括号内的辅助信息（如：(播报)、(语音)等）
        line = re.sub(r"\([^)]*(语音|回复|播报|记录)[^)]*\)", "", line)
        
        if not line.strip():
            continue
        lines.append(line)
    
    # 用逗号或句号连接行，增加语流的自然感
    text = "。".join(lines)
    
    # 4. 去掉表情符号
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", text)
    text = re.sub(r"[\uFE0E\uFE0F]", "", text)
    
    # 5. 最终清理多余空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[，。]{2,}", "。", text) # 合并重复的标点
    text = text.strip("，。 ") + "。"
    
    return text.strip()

def tts_stop_signal_path() -> Path | None:
    raw = os.environ.get("PTT_GUI_CONTROL_DIR", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser() / TTS_STOP_SIGNAL_FILENAME

def consume_tts_stop_request() -> bool:
    signal_path = tts_stop_signal_path()
    if signal_path is None or not signal_path.exists():
        return False
    with contextlib.suppress(OSError):
        signal_path.unlink()
    return True

def generate_tts_wav(text: str, output_path: Path) -> Path:
    clean_text = sanitize_for_tts(text)
    if not clean_text:
        raise RuntimeError("tts text became empty after sanitize")

    qwen_tts = ensure_bin("qwen-tts")
    log(f"generating tts wav for text: {clean_text[:20]}...")
    
    # qwen-tts 默认生成到 output.wav，或者可以通过参数指定
    # 假设 qwen-tts 支持 --output 参数，如果不支持则需要重命名
    proc = subprocess.run(
        [qwen_tts, clean_text, "--speaker", "serena", "--output", str(output_path), "--no-play"],
        capture_output=True,
        text=True,
    )
    
    if proc.returncode != 0:
        # 如果不支持 --output，尝试默认行为并移动文件
        proc = subprocess.run(
            [qwen_tts, clean_text, "--speaker", "serena", "--no-play"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            default_output = Path("output.wav")
            if default_output.exists():
                shutil.move(str(default_output), str(output_path))
                return output_path
        
        msg = (proc.stderr or proc.stdout or f"tts generation failed with code {proc.returncode}").strip()
        raise RuntimeError(msg)
        
    return output_path

def speak_text(text: str) -> bool:
    clean_text = sanitize_for_tts(text)
    if not clean_text:
        raise RuntimeError("tts text became empty after sanitize")

    qwen_tts = ensure_bin("qwen-tts")
    log("speaking reply with qwen-tts --play --speaker serena --stream")
    consume_tts_stop_request()
    proc = subprocess.Popen(
        [qwen_tts, "--play", clean_text, "--speaker", "serena", "--stream"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while True:
            if consume_tts_stop_request():
                log("received GUI stop request for qwen-tts")
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                return False
            code = proc.poll()
            if code is not None:
                stdout, stderr = proc.communicate()
                if code != 0:
                    msg = (stderr or stdout or f"command failed with code {code}").strip()
                    raise RuntimeError(msg)
                return True
            time.sleep(0.1)
    finally:
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
