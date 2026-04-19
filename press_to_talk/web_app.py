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
from .utils.logging import log, init_session_log
from .utils.env import env_path, DEFAULT_LOG_DIR

from fastapi.staticfiles import StaticFiles

# 初始化环境
load_env_files()

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
app.mount("/gui", StaticFiles(directory="web_gui", html=True), name="gui")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/process")
async def process_audio(audio: UploadFile = File(...)):
    session_id = uuid.uuid4().hex
    # 初始化日志（可选，为了调试方便）
    init_session_log(env_path("PTT_LOG_DIR", DEFAULT_LOG_DIR), session_id=session_id)
    
    # 创建临时目录存放音频
    temp_dir = Path("tmp") / "web" / session_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temp_dir / "input.wav"
    
    try:
        # 保存上传的文件
        with audio_path.open("wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)
        
        # 构造配置
        # 我们默认关闭 TTS 和 GUI 事件，只返回结果文本
        cfg = parse_args(["--no-tts"])
        
        # 1. 语音转文字
        log(f"Web API: transcribing {audio_path}")
        # run_stt 是同步的，目前没看到它内部有用 asyncio.run
        transcript = run_stt(cfg.stt_url, cfg.stt_token, str(audio_path))
        
        if not transcript:
            return JSONResponse(
                status_code=200,
                content={"error": "No speech detected", "transcript": "", "reply": ""}
            )
        
        # 2. 执行逻辑流
        log(f"Web API: executing transcript: {transcript}")
        reply = await execute_transcript_async(cfg, transcript)
        
        return {
            "session_id": session_id,
            "transcript": transcript,
            "reply": reply,
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
