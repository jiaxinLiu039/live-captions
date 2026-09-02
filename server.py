"""
Realtime EN→ZH subtitle server (V2).

Pipeline:
  Browser mic (16kHz PCM)
    -> WebSocket
    -> Aliyun Paraformer realtime (English ASR)
    -> DeepSeek API (EN -> ZH streaming translation)
    -> WebSocket back to browser

Protocol V2 features: config handshake, structured transcript messages,
translation_stream, diff mode, end-of-audio signal. V1 messages are still
sent for backwards compatibility with older frontends.
"""

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiohttp
import dashscope
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dashscope.audio.asr import Recognition

from config import settings, BASE_DIR
from session import Session
from asr_bridge import ASRBridge
from protocol import make_config, make_stats, make_ready_to_stop

from routes.health import router as health_router
from routes.models import router as models_router
from routes.transcriptions import router as transcriptions_router

# Set API key for dashscope
dashscope.api_key = settings.dashscope_api_key

# Track active sessions for graceful shutdown
active_sessions: set[Session] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — cleanup on shutdown."""
    yield
    # Graceful shutdown: mark all sessions as closed
    for session in active_sessions:
        session.closed = True


app = FastAPI(lifespan=lifespan)

# Mount routes
app.include_router(health_router)
app.include_router(models_router)
app.include_router(transcriptions_router)

# Downloads directory for saved sessions
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


# ===== Hotwords API =====
HOTWORDS_DIR = BASE_DIR / "hotwords"
HOTWORDS_DIR.mkdir(exist_ok=True)


@app.get("/api/hotwords/list")
async def list_hotwords():
    """List all hotword files."""
    files = []
    for f in sorted(HOTWORDS_DIR.glob("*.txt")):
        files.append({"name": f.stem, "filename": f.name, "size": f.stat().st_size})
    return {"files": files}


@app.get("/api/hotwords/{filename}")
async def get_hotwords(filename: str):
    """Read a hotword file content."""
    filepath = HOTWORDS_DIR / filename
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": "文件不存在"})
    return {"filename": filename, "content": filepath.read_text(encoding="utf-8")}


@app.post("/api/hotwords/save")
async def save_hotwords(request: Request):
    """Save/create a hotword file."""
    body = await request.json()
    name = body.get("name", "").strip()
    content = body.get("content", "")
    if not name:
        return JSONResponse(status_code=400, content={"error": "名称不能为空"})
    # Sanitize
    name = "".join(c for c in name if c.isalnum() or c in ('-', '_', ' ', '（', '）')).strip()
    if not name:
        name = f"hotwords-{int(time.time())}"
    filename = name + ".txt"
    filepath = HOTWORDS_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return {"ok": True, "filename": filename}


@app.delete("/api/hotwords/{filename}")
async def delete_hotwords(filename: str):
    """Delete a hotword file."""
    filepath = HOTWORDS_DIR / filename
    if filepath.exists():
        filepath.unlink()
    return {"ok": True}


@app.post("/api/hotwords/upload")
async def upload_hotwords(file: UploadFile = File(...)):
    """Upload a hotword file (.txt)."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    name = Path(file.filename or "imported.txt").stem
    filename = name + ".txt"
    filepath = HOTWORDS_DIR / filename
    filepath.write_text(text, encoding="utf-8")
    return {"ok": True, "filename": filename, "content": text}


@app.post("/api/sessions/save")
async def save_session(request: Request):
    """Save a session to downloads folder as JSON."""
    body = await request.json()
    filename = body.get("filename", "").strip()
    if not filename:
        filename = f"session-{int(time.time())}"
    # Sanitize filename
    filename = "".join(c for c in filename if c.isalnum() or c in ('-', '_', ' ', '.', '（', '）', '(', ')')).strip()
    if not filename:
        filename = f"session-{int(time.time())}"
    if not filename.endswith(".json"):
        filename += ".json"

    import json as json_lib
    filepath = DOWNLOADS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json_lib.dump(body.get("data", {}), f, ensure_ascii=False, indent=2)

    return {"ok": True, "filename": filename, "path": str(filepath)}


@app.get("/api/sessions/list")
async def list_sessions():
    """List saved session files in downloads folder."""
    files = []
    for f in sorted(DOWNLOADS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        files.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        })
    return {"files": files}


@app.get("/api/sessions/{filename}")
async def load_session(filename: str):
    """Load a saved session file."""
    import json as json_lib
    filepath = DOWNLOADS_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        return JSONResponse(status_code=404, content={"error": "文件不存在"})
    with open(filepath, "r", encoding="utf-8") as f:
        data = json_lib.load(f)
    return data


@app.delete("/api/sessions/{filename}")
async def delete_session(filename: str):
    """Delete a saved session file."""
    filepath = DOWNLOADS_DIR / filename
    if filepath.exists() and filepath.is_file():
        filepath.unlink()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/chat")
async def chat_page():
    """Serve the AI chat page."""
    return FileResponse(BASE_DIR / "static" / "chat.html")


@app.get("/listen")
async def listen_page():
    """Serve the listening practice page."""
    return FileResponse(BASE_DIR / "static" / "listen.html")


@app.post("/api/chat")
async def chat_api(request: Request):
    """Stream chat completion from DeepSeek."""
    from starlette.responses import StreamingResponse
    import json as json_lib

    body = await request.json()
    messages = body.get("messages", [])
    transcript_context = body.get("transcript", "")

    # Build system prompt
    system_prompt = (
        "你是一个专业的语音内容分析助手。你可以帮助用户：\n"
        "1. 总结和归纳语音转录/翻译的内容要点\n"
        "2. 提取关键信息、术语、人名、数字\n"
        "3. 回答关于转录内容的问题\n"
        "4. 生成会议纪要、课堂笔记\n"
        "5. 纠正可能的 ASR 识别错误\n"
        "6. 翻译和解释专业术语\n\n"
        "回答要简洁、准确、使用中文。如果用户提供了转录文本，基于该内容回答。"
    )

    api_messages = [{"role": "system", "content": system_prompt}]

    # If transcript context provided, inject it
    if transcript_context:
        api_messages.append({
            "role": "system",
            "content": f"以下是当前的语音转录内容（供参考）：\n\n{transcript_context[:4000]}"
        })

    api_messages.extend(messages)

    async def generate():
        async with aiohttp.ClientSession() as http:
            payload = {
                "model": settings.translation_model,
                "messages": api_messages,
                "temperature": 0.7,
                "stream": True,
                "max_tokens": 2048,
            }
            headers = {
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            }
            try:
                async with http.post(
                    "https://api.deepseek.com/chat/completions",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(sock_connect=10, sock_read=60),
                ) as resp:
                    if resp.status != 200:
                        error_body = await resp.text()
                        yield f"data: {json_lib.dumps({'error': error_body[:200]})}\n\n"
                        return
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            yield line + "\n\n"
                            if "[DONE]" in line:
                                break
            except Exception as e:
                yield f"data: {json_lib.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    # Connection limit check
    if len(active_sessions) >= settings.max_connections:
        await websocket.close(code=1013, reason="服务器已满，请稍后再试")
        return

    # Token authentication (if configured)
    if settings.auth_token:
        params = websocket.query_params
        token = params.get("token", "")
        if token != settings.auth_token:
            await websocket.close(code=4001, reason="认证失败")
            return

    await websocket.accept()
    loop = asyncio.get_running_loop()

    # Parse query params
    params = websocket.query_params
    mode = params.get("mode", "full")

    # Create session
    session = Session(mode=mode)
    active_sessions.add(session)

    # Stats push task
    stats_task = None

    async def push_stats():
        while not session.closed:
            await asyncio.sleep(settings.stats_interval)
            try:
                await websocket.send_json(
                    make_stats(
                        audio_seconds=session.audio_seconds,
                        translate_count=session.translate_count,
                        asr_cost=session.asr_cost,
                        translate_cost=session.translate_cost,
                        session_duration=session.session_duration,
                    )
                )
            except Exception:
                break

    async def ws_send_json(payload: dict):
        """Wrapper for sending JSON that respects session.closed."""
        if session.closed:
            return
        await websocket.send_json(payload)

    async with aiohttp.ClientSession() as http:
        bridge = ASRBridge(session, ws_send_json, loop, http)

        recognizer = Recognition(
            model=settings.asr_model,
            format=settings.asr_format,
            sample_rate=settings.asr_sample_rate,
            callback=bridge,
        )
        recognizer.start()

        # Send config message (V2)
        await websocket.send_json(make_config(session.session_id, mode))

        stats_task = asyncio.ensure_future(push_stats())

        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.receive":
                    if "bytes" in msg and msg["bytes"] is not None:
                        data = msg["bytes"]
                        # Empty frame = end-of-audio signal
                        if not data:
                            break
                        session.add_audio_bytes(len(data))
                        recognizer.send_audio_frame(data)
                    elif "text" in msg and msg["text"] is not None:
                        # Text frame = control message (JSON)
                        import json as json_mod
                        try:
                            ctrl = json_mod.loads(msg["text"])
                            if ctrl.get("type") == "hotwords":
                                session.hotwords = ctrl.get("data", "")
                            elif ctrl.get("type") == "settings":
                                bridge.update_settings(**(ctrl.get("data") or {}))
                        except Exception:
                            pass
                elif msg["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            try:
                await websocket.send_json(
                    {"type": "error", "code": "internal", "msg": str(e), "recoverable": False}
                )
            except Exception:
                pass
        finally:
            active_sessions.discard(session)

            if stats_task:
                stats_task.cancel()
                try:
                    await stats_task
                except asyncio.CancelledError:
                    pass

            # Stop recognizer BEFORE marking closed — allows final callbacks to arrive
            try:
                recognizer.stop()
            except Exception:
                pass

            # Brief wait for final ASR callbacks to flush
            await asyncio.sleep(0.3)

            # Now mark session as closed — no more sends
            session.closed = True

            # Send ready_to_stop (V2)
            try:
                await websocket.send_json(make_ready_to_stop(session.total_lines))
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port, loop="asyncio")
