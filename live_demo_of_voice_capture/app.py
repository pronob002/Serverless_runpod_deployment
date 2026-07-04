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
import re
import json
import time
import uuid
import shutil
import asyncio

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import protocol
from session import AnalysisSession, RECORDINGS_DIR
from module2 import service as m2

app = FastAPI(title="Module 1 — Identity & Voice Capture")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
OUTPUT_ROOT = os.path.join(HERE, "output")  # per-session subfolders go here
os.makedirs(OUTPUT_ROOT, exist_ok=True)

# Single active analysis at a time (one client attempt drives the shared SSE stream).
session: AnalysisSession | None = None

# Single active Module 2 cloning run at a time (heavy, GPU-bound).
cloning_run: m2.CloningRun | None = None

# ── Option B — auto-chain Module 2 after a Module 1 capture ────────────────────
# When enabled, a successful capture immediately kicks off a cloning run with DEFAULT_MODEL, so the
# user doesn't have to trigger it by hand. Option A (manual runs from /module2) always stays available.
# Toggle off with MODULE2_AUTOCLONE=0; pick the model with MODULE2_DEFAULT_MODEL.
AUTO_CLONE = os.environ.get("MODULE2_AUTOCLONE", "1").lower() not in ("0", "false", "no", "")
DEFAULT_MODEL = os.environ.get("MODULE2_DEFAULT_MODEL", "voxcpm")


def _safe_name(name: str) -> str:
    """Turn a user-typed recording name into a filesystem-safe slug (or '' if unusable)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:40]


def _maybe_autoclone(session_id: str, output_dir: str, result: dict | None) -> dict | None:
    """Start a default cloning run for a just-finished capture. Returns an event dict for the
    Module 1 stream describing what happened, or None if auto-clone is off.

    Only a *passing* Module 1 result proceeds to Module 2 — a failed capture (bad liveness or a
    failed voice clip) is not worth cloning, so it's skipped."""
    global cloning_run
    if not AUTO_CLONE:
        return None
    if not (result and result.get("overall_result") == "pass"):
        return {"type": "autoclone", "status": "skipped",
                "reason": "Module 1 did not pass", "session_id": session_id}
    if DEFAULT_MODEL not in m2.list_models():
        return {"type": "autoclone", "status": "skipped",
                "reason": f"unknown default model '{DEFAULT_MODEL}'", "session_id": session_id}
    if not m2.reference.has_anchors(output_dir):
        return {"type": "autoclone", "status": "skipped",
                "reason": "no voice anchors captured", "session_id": session_id}
    if cloning_run is not None and cloning_run.active:
        return {"type": "autoclone", "status": "skipped",
                "reason": "another cloning run is in progress", "session_id": session_id}
    cloning_run = m2.CloningRun(output_dir, DEFAULT_MODEL)
    cloning_run.start()
    return {"type": "autoclone", "status": "started",
            "session_id": session_id, "model": DEFAULT_MODEL}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/config")
def config():
    """The capture protocol (timing/prompts) the browser overlay drives itself from, so the
    client guided timeline can't drift from the fixed windows the server analyzes by."""
    return JSONResponse(protocol.web_protocol())


@app.post("/upload")
async def upload(file: UploadFile = File(...), name: str = Form("")):
    """Receive a recorded clip (from live browser capture or a pre-recorded file) and analyze it.

    The blob is saved, then handed to AnalysisSession, which normalizes it to a constant-frame-rate
    MP4 before running pipeline.run_analysis() — browser MediaRecorder output (WebM/MP4) has
    unreliable fps/duration metadata that would otherwise break the frame-index / timestamp cutting.

    `name` is an optional user-typed label for the recording. It's slugged and prefixed onto the
    session id so the folder is recognisable later (e.g. in the Module 2 compare dropdown) — a way
    to record a clip now and come back to test cloning on it another time.
    """
    global session
    if session is not None and session.active:
        return JSONResponse({"error": "a session is already running"}, status_code=409)

    # Unique id per attempt. A short timestamp + random suffix guarantees no two runs collide (the
    # analysis WAVs would otherwise clobber each other); the optional name is prefixed for humans.
    stamp = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    safe = _safe_name(name)
    session_id = f"{safe}_{stamp}" if safe else stamp

    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".webm"
    dest = os.path.join(RECORDINGS_DIR, f"capture_{session_id}{ext}")
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        await file.close()

    output_dir = os.path.join(OUTPUT_ROOT, session_id)
    session = AnalysisSession(
        dest, output_dir=output_dir,
        on_complete=lambda result: _maybe_autoclone(session_id, output_dir, result),
    )
    session.start()
    return JSONResponse({"status": "analyzing", "session_id": session_id,
                         "name": safe, "file": os.path.basename(dest),
                         "auto_clone": AUTO_CLONE}, status_code=202)


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


# ── Module 2 — Voice Cloning (compare page) ───────────────────────────────────
# Heavy GPU work: this only works if uvicorn runs in an environment that has torch + voxcpm
# (`pip install -r module2/requirements.txt`). Listing sessions/models works without them; the
# actual /module2/run will report an import error over the event stream if they're missing.

@app.get("/module2")
def module2_page():
    return FileResponse(os.path.join(STATIC_DIR, "module2.html"))


@app.get("/module2/models")
def module2_models():
    return JSONResponse({"models": m2.list_models()})


@app.get("/module2/sessions")
def module2_sessions():
    return JSONResponse({"sessions": m2.list_sessions(OUTPUT_ROOT)})


@app.get("/module2/status")
def module2_status():
    """Whether a cloning run is active and for which session/model — lets the compare page attach
    to a run that was auto-started by a capture (Option B) and show its live progress."""
    if cloning_run is None:
        return JSONResponse({"active": False})
    return JSONResponse({
        "active": cloning_run.active,
        "session_id": os.path.basename(cloning_run.session_dir),
        "model": cloning_run.model_name,
    })


@app.get("/module2/result")
def module2_result(session: str, model: str):
    """Manifest + reference clip for an already-generated (session, model) pair."""
    manifest = m2.load_manifest(OUTPUT_ROOT, session, model)
    if manifest is None:
        return JSONResponse({"error": "no outputs for this session/model yet"}, status_code=404)
    try:
        ref = m2.reference_info(OUTPUT_ROOT, session)
    except Exception:
        ref = None
    return JSONResponse({"manifest": manifest, "reference": ref})


@app.post("/module2/run")
async def module2_run(request: Request):
    """Start a cloning run for {session_id, model} on a background thread."""
    global cloning_run
    if cloning_run is not None and cloning_run.active:
        return JSONResponse({"error": "a cloning run is already in progress"}, status_code=409)

    body = await request.json()
    session_id = body.get("session_id")
    model = body.get("model")
    if not session_id or not model:
        return JSONResponse({"error": "session_id and model are required"}, status_code=400)
    if model not in m2.list_models():
        return JSONResponse({"error": f"unknown model '{model}'"}, status_code=400)

    session_dir = os.path.join(OUTPUT_ROOT, session_id)
    if not os.path.isdir(session_dir):
        return JSONResponse({"error": f"session '{session_id}' not found"}, status_code=404)

    cloning_run = m2.CloningRun(session_dir, model)
    cloning_run.start()
    return JSONResponse({"status": "running", "session_id": session_id, "model": model},
                        status_code=202)


@app.get("/module2/events")
async def module2_events(request: Request):
    """SSE stream draining the active cloning run's event queue."""
    async def event_stream():
        for _ in range(50):
            if cloning_run is not None:
                break
            await asyncio.sleep(0.1)
        if cloning_run is None:
            yield "data: {\"type\": \"log\", \"text\": \"No active cloning run.\"}\n\n"
            return

        loop = asyncio.get_event_loop()
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await loop.run_in_executor(None, cloning_run.events.get, True, 0.5)
            except Exception:
                yield ": keep-alive\n\n"
                if not cloning_run.active and cloning_run.events.empty():
                    break
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Serve generated audio (and the Module 1 clips) for in-browser playback.
app.mount("/output", StaticFiles(directory=OUTPUT_ROOT), name="output")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
