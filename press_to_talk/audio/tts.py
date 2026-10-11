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
    # 0. 剥离可能遗留的 <spoken> 标签本身（包括未闭合的 <spoken 碎片）
    text = re.sub(r"<\s*/?spoken\b.*?(?:>|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?spoken>", "", text, flags=re.IGNORECASE)

    # 1. 基础清理：去掉代码块、链接、来源附录等
    text = re.sub(r"`{1,3}.*?`{1,3}", "", text, flags=re.DOTALL)
    text = re.sub(r"\[(.*?)\]\((https?://[^\s)]+)\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"（?数据来源[：:][^）\n]+）?", "", text)
    text = re.sub(r"\b[a-zA-Z0-9-]+\.(in|com|cn|org|net|io)\b", "", text)

    # 2. 特殊符号与单位口语化
    text = re.sub(r"℃", "度", text)
    text = re.sub(r"[～~]", "到", text)
    text = re.sub(r"(?i)\bkm/h\b", "公里每小时", text)
    text = re.sub(r"(\d+)%", r"百分之\1", text)
    text = re.sub(r"%", "百分之", text)

    # 3. Markdown 格式清理
    text = re.sub(r"[*_#~]", "", text)  # 去掉粗体、斜体、标题符号、删除线

    # 4. 结构化清理与自然停顿
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"-{3,}", line):  # 去掉分割线
            continue

        # 处理列表项：把 - 或 * 开头的列表换成自然表达
        line = re.sub(r"^\s*[-*+]\s+", "，", line)
        line = re.sub(r"^\s*\d+\.\s+", "，", line)

        # 去掉冒号前后的空隙或冒号本身转换为自然停顿
        line = re.sub(r"：\s*", "，", line)

        # 去掉括号内的辅助信息（如：(播报)、(语音)等）
        line = re.sub(r"\([^)]*(语音|回复|播报|记录)[^)]*\)", "", line)

        if not line.strip():
            continue
        lines.append(line)

    text = "。".join(lines)

    # 5. 去掉表情符号
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+", "", text)
    text = re.sub(r"[\uFE0E\uFE0F]", "", text)

    # 6. 最终标点与空白整理，杜绝畸形标点组合
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。\.]{2,}", "。", text)
    text = re.sub(r"[，,][。.]|[。.][，,]", "。", text)
    text = re.sub(r"：[，。]", "。", text)
    text = text.strip("，。 ") + "。"

    return text.strip()


def split_visual_and_spoken(text: str) -> tuple[str, str]:
    """将模型产出拆分为屏幕展示版本（visual）与语音朗读版本（spoken）。

    若文本中包含 <spoken>...</spoken>（或未闭合的 <spoken>...），
    则视觉版本为标签前/外的排版正文，口语版本为标签内的内容。
    若未包含标签，则视觉版本为原样文本，口语版本回退为经过口语规范化的文本。
    """
    raw = (text or "").strip()
    if not raw:
        return "", ""

    spoken_match = re.search(r"<\s*spoken\b[^>]*>?(.*?)(?:</spoken>|\Z)", raw, flags=re.DOTALL | re.IGNORECASE)
    if spoken_match:
        spoken_part = spoken_match.group(1).strip()
        visual_part = (raw[:spoken_match.start()] + raw[spoken_match.end():]).strip()
        # 清理口语版中可能残留的标点或换行
        spoken_clean = re.sub(r"\s+", " ", spoken_part).strip()
        return visual_part or spoken_clean, spoken_clean or sanitize_for_tts(visual_part)

    return raw, sanitize_for_tts(raw)

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

def _run_tts_process(text: str, is_async: bool = False) -> subprocess.Popen | subprocess.CompletedProcess:
    """统一的 TTS 执行入口"""
    qwen_tts = ensure_bin("qwen-tts")
    cmd = [qwen_tts, text]

    if is_async:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    else:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )


def speak_text(text: str) -> bool:
    clean_text = sanitize_for_tts(text)
    if not clean_text:
        raise RuntimeError("tts text became empty after sanitize")

    log(f"speaking reply with: qwen-tts \"{clean_text[:20]}...\"")
    consume_tts_stop_request()

    proc = _run_tts_process(clean_text, is_async=True)
    assert isinstance(proc, subprocess.Popen)

    try:
        while True:
            # Check if parent process (the GUI or launcher) has died. 
            if consume_tts_stop_request() or os.getppid() == 1:
                log("received GUI stop request or parent process exited for qwen-tts")
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

    try:
        while True:
            # Check if parent process (the GUI or launcher) has died. 
            # On macOS/Unix, orphaned processes are adopted by PID 1.
            if consume_tts_stop_request() or os.getppid() == 1:
                log("received GUI stop request or parent process exited for qwen-tts")
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
