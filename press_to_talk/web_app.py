import os
import uuid
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .execution import execute_transcript_async
from .core import run_stt, load_env_files, parse_args
from .audio.tts import generate_tts_wav
from .utils.logging import log, init_session_log, set_global_log_level
from .utils.env import env_path, DEFAULT_LOG_DIR

from fastapi.staticfiles import StaticFiles

# 1. 初始化环境与日志
load_env_files()
# Web GUI 默认开启 DEBUG，并初始化全局日志文件
log_dir = env_path("PTT_LOG_DIR", DEFAULT_LOG_DIR)
log_path = init_session_log(log_dir, session_id="web-server")
set_global_log_level("DEBUG")

log(f"Web API Server starting. Log file: {log_path}")

app = FastAPI(title="Voice Assistant Web API")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
frontend_path = Path("web_gui")
frontend_path.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory="web_gui", html=True), name="gui")

# 挂载临时音频目录用于播放
audio_tmp_dir = Path("tmp") / "web_audio"
audio_tmp_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(audio_tmp_dir)), name="audio")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    log(f"Web API [STT] request received. session={session_id}", level="debug")
    temp_dir = Path("tmp") / "web" / session_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temp_dir / "input.wav"
    
    try:
        with audio_path.open("wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)
        
        cfg = parse_args(["--no-tts"])
        log(f"Web API: transcribing {audio_path}", level="debug")
        transcript = run_stt(cfg.stt_url, cfg.stt_token, str(audio_path))
        log(f"Web API: transcript result: '{transcript}'", level="debug")
        
        return {
            "session_id": session_id,
            "transcript": transcript,
            "status": "success"
        }
    except Exception as e:
        log(f"Web API STT Error: {str(e)}", level="error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/execute")
async def execute_text(transcript: str, session_id: str):
    log(f"Web API [Execute] session={session_id} transcript='{transcript}'", level="debug")
    try:
        cfg = parse_args(["--no-tts"])
        reply = await execute_transcript_async(cfg, transcript)
        log(f"Web API: execution reply ready: '{reply[:50]}...'", level="debug")
        
        # Web 端不再生成 TTS，直接返回文字，追求极速
        return {
            "session_id": session_id,
            "reply": reply,
            "audio_url": None,
            "status": "success"
        }
    except Exception as e:
        log(f"Web API Execute Error: {str(e)}", level="error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process")
async def process_audio(audio: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    log(f"Web API [Process] full flow request. session={session_id}", level="debug")
    
    # 创建临时目录
    temp_dir = Path("tmp") / "web" / session_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temp_dir / "input.wav"
    
    try:
        # 保存上传的文件
        with audio_path.open("wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)
        
        # 构造配置
        cfg = parse_args(["--no-tts"])
        
        # 1. 语音转文字
        log(f"Web API: transcribing {audio_path}", level="debug")
        transcript = run_stt(cfg.stt_url, cfg.stt_token, str(audio_path))
        
        if not transcript:
            log(f"Web API: no speech detected for session {session_id}", level="warn")
            return JSONResponse(
                status_code=200,
                content={"error": "No speech detected", "transcript": "", "reply": ""}
            )
        
        log(f"Web API: transcript: '{transcript}'", level="debug")
        
        # 2. 执行逻辑流
        log(f"Web API: executing transcript...", level="debug")
        reply = await execute_transcript_async(cfg, transcript)
        log(f"Web API: reply: '{reply[:50]}...'", level="debug")
        
        # Web 端不再生成 TTS，直接返回文字
        return {
            "session_id": session_id,
            "transcript": transcript,
            "reply": reply,
            "audio_url": None,
            "status": "success"
        }
    except Exception as e:
        log(f"Web API Error: {str(e)}", level="error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 清理临时文件（可选，如果想保留可以注释掉）
        # if temp_dir.exists():
        #     shutil.rmtree(temp_dir)
        pass

if __name__ == "__main__":
    import uvicorn
    # 开启 reload=True 即可实现热重载
    # 注意：使用 reload 时，建议传入 import 字符串字符串形式以提高稳定性
    uvicorn.run("press_to_talk.web_app:app", host="0.0.0.0", port=10021, reload=True)
