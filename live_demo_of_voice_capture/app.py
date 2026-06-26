"""
FastAPI app for the Module 1 capture demo.

Capture happens **in the browser** on the user's own device (phone camera on a phone, webcam in a
PC browser) via getUserMedia + MediaRecorder. The recorded clip is uploaded here; the server runs
the analysis pipeline (MediaPipe / librosa / ffmpeg) and streams step/log/result events back over
Server-Sent Events (/events). This is the deployable split: the client captures, the server analyzes
— a cloud server needs no camera or microphone of its own.

Run from this folder:   uvicorn app:app --host 0.0.0.0 --port 8000
Then open:              http://localhost:8000   (HTTPS required for camera on other devices — use ngrok)
"""

import os
import json
import time
import uuid
import shutil
import asyncio

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import protocol
from session import AnalysisSession, RECORDINGS_DIR

app = FastAPI(title="Module 1 — Identity & Voice Capture")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
OUTPUT_ROOT = os.path.join(HERE, "output")  # per-session subfolders go here

# Single active analysis at a time (one client attempt drives the shared SSE stream).
session: AnalysisSession | None = None


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/config")
def config():
    """The capture protocol (timing/prompts) the browser overlay drives itself from, so the
    client guided timeline can't drift from the fixed windows the server analyzes by."""
    return JSONResponse(protocol.web_protocol())


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Receive a recorded clip (from live browser capture or a pre-recorded file) and analyze it.

    The blob is saved, then handed to AnalysisSession, which normalizes it to a constant-frame-rate
    MP4 before running pipeline.run_analysis() — browser MediaRecorder output (WebM/MP4) has
    unreliable fps/duration metadata that would otherwise break the frame-index / timestamp cutting.
    """
    global session
    if session is not None and session.active:
        return JSONResponse({"error": "a session is already running"}, status_code=409)

    # Unique id per attempt (no user accounts yet, so timestamp + short random suffix). It keys
    # both the saved clip and a per-session output folder so nothing from a previous run is
    # overwritten — the analysis WAVs (normal_clean.wav, …) would otherwise clobber each other.
    session_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".webm"
    dest = os.path.join(RECORDINGS_DIR, f"capture_{session_id}{ext}")
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        await file.close()

    output_dir = os.path.join(OUTPUT_ROOT, session_id)
    session = AnalysisSession(dest, output_dir=output_dir)
    session.start()
    return JSONResponse({"status": "analyzing", "session_id": session_id,
                         "file": os.path.basename(dest)}, status_code=202)


@app.get("/events")
async def events(request: Request):
    """SSE stream draining the active session's event queue."""
    async def event_stream():
        # Wait briefly for a session to exist if the client connected first.
        for _ in range(50):
            if session is not None:
                break
            await asyncio.sleep(0.1)
        if session is None:
            yield "data: {\"type\": \"log\", \"text\": \"No active session.\"}\n\n"
            return

        loop = asyncio.get_event_loop()
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await loop.run_in_executor(None, session.events.get, True, 0.5)
            except Exception:
                # queue.Empty (timeout) — keep the connection alive
                yield ": keep-alive\n\n"
                if not session.active and session.events.empty():
                    break
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
