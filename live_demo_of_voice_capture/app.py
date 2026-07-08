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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import protocol
from session import AnalysisSession, RECORDINGS_DIR
from module2 import service as m2
from schemas import (
    ConfigResponse, ErrorResponse, HealthResponse, Module2Manifest, Module2ModelsResponse,
    Module2ReferenceInfo, Module2ResultResponse, Module2RunRequest,
    Module2RunResponse, Module2SessionsResponse, Module2StatusResponse,
    UploadResponse,
)

app = FastAPI(
    title="Voice Capture & Cloning API",
    description=(
        "Module 1 (identity/voice capture + liveness analysis) and Module 2 (voice cloning) "
        "backend. Long-running work (capture analysis, cloning) streams progress over "
        "Server-Sent Events (`/events`, `/module2/events`) rather than a single response."
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "Module 1", "description": "Capture upload + liveness/voice analysis."},
        {"name": "Module 2", "description": "Voice cloning: run models, list results, compare clips."},
    ],
)

# Frontend team: if the UI is served from a different origin than this API (e.g. a separate dev
# server), the browser needs CORS allowed here. Comma-separated list of allowed origins; "*" (the
# default) allows any origin, which is fine for demos/dev but should be locked down for a real
# deployment (see README → Deployment).
_allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allowed_origins == "*" else [o.strip() for o in _allowed_origins.split(",")],
    allow_credentials=_allowed_origins != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/health", tags=["Ops"], summary="Liveness/readiness probe", response_model=HealthResponse)
def health():
    """Lightweight status for load balancers / container orchestrators / uptime checks. Does not
    do any real work, so it's safe to poll frequently."""
    try:
        import torch
        cuda_available = torch.cuda.is_available()
    except ImportError:
        # torch isn't installed (Module 1-only deployment) — not an error, just no GPU to report.
        cuda_available = False
    return JSONResponse({
        "status": "ok",
        "module1_active": bool(session is not None and session.active),
        "module2_active": bool(cloning_run is not None and cloning_run.active),
        "cuda_available": cuda_available,
    })


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/config", tags=["Module 1"], summary="Get the capture protocol",
         response_model=ConfigResponse)
def config():
    """The capture protocol (timing/prompts) the browser overlay drives itself from, so the
    client guided timeline can't drift from the fixed windows the server analyzes by."""
    return JSONResponse(protocol.web_protocol())


@app.post("/upload", tags=["Module 1"], summary="Upload a clip and start analysis",
          response_model=UploadResponse, status_code=202,
          responses={409: {"model": ErrorResponse, "description": "A session is already running."}})
async def upload(file: UploadFile = File(..., description="The recorded/uploaded video clip."),
                  name: str = Form("", description="Optional user-typed label for the recording.")):
    """Receive a recorded clip (from live browser capture or a pre-recorded file) and analyze it.

    The blob is saved, then handed to AnalysisSession, which normalizes it to a constant-frame-rate
    MP4 before running pipeline.run_analysis() — browser MediaRecorder output (WebM/MP4) has
    unreliable fps/duration metadata that would otherwise break the frame-index / timestamp cutting.

    `name` is an optional user-typed label for the recording. It's slugged and prefixed onto the
    session id so the folder is recognisable later (e.g. in the Module 2 compare dropdown) — a way
    to record a clip now and come back to test cloning on it another time.

    Progress and the final pass/fail result are **not** in this response — connect to `GET /events`
    (Server-Sent Events) right after this call to stream them.
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


@app.get("/events", tags=["Module 1"], summary="Stream Module 1 analysis progress (SSE)",
         responses={200: {"content": {"text/event-stream": {}}, "description": (
             "Server-Sent Events. Each `data:` line is a JSON object with a `type` field: "
             "`log` ({type, text}), `step` (per-stage progress), `result` (the final pass/fail "
             "payload), or `done` ({type, status}) which ends the stream. Connect immediately "
             "after POST /upload."
         )}})
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

@app.get("/module2", include_in_schema=False)
def module2_page():
    return FileResponse(os.path.join(STATIC_DIR, "module2.html"))


@app.get("/module2/models", tags=["Module 2"], summary="List available cloning models",
         response_model=Module2ModelsResponse)
def module2_models():
    return JSONResponse({"models": m2.list_models()})


@app.get("/module2/sessions", tags=["Module 2"], summary="List sessions eligible for cloning",
         response_model=Module2SessionsResponse)
def module2_sessions():
    """Only Module 1 sessions that captured usable voice anchors are listed."""
    return JSONResponse({"sessions": m2.list_sessions(OUTPUT_ROOT)})


@app.get("/module2/status", tags=["Module 2"], summary="Current cloning run status",
         response_model=Module2StatusResponse)
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


@app.get("/module2/result", tags=["Module 2"], summary="Get a completed cloning run's manifest",
         response_model=Module2ResultResponse,
         responses={404: {"model": ErrorResponse, "description": "No outputs for this session/model yet."}})
def module2_result(session: str, model: str):
    """Manifest + reference clip for an already-generated (session, model) pair. Poll this after
    `/module2/events` emits `done`, or to load a previous run without re-generating."""
    manifest = m2.load_manifest(OUTPUT_ROOT, session, model)
    if manifest is None:
        return JSONResponse({"error": "no outputs for this session/model yet"}, status_code=404)
    try:
        ref = m2.reference_info(OUTPUT_ROOT, session)
    except Exception:
        ref = None
    return JSONResponse({"manifest": manifest, "reference": ref})


@app.post("/module2/run", tags=["Module 2"], summary="Start a cloning run",
          response_model=Module2RunResponse, status_code=202,
          responses={
              400: {"model": ErrorResponse, "description": "Unknown model."},
              404: {"model": ErrorResponse, "description": "Session not found."},
              409: {"model": ErrorResponse, "description": "A cloning run is already in progress."},
          })
async def module2_run(body: Module2RunRequest):
    """Start a cloning run for `{session_id, model}` on a background thread (GPU-bound — only one
    run at a time across the whole server). Connect to `GET /module2/events` right after this call
    to stream progress, then `GET /module2/result` for the final manifest."""
    global cloning_run
    if cloning_run is not None and cloning_run.active:
        return JSONResponse({"error": "a cloning run is already in progress"}, status_code=409)

    session_id = body.session_id
    model = body.model
    if model not in m2.list_models():
        return JSONResponse({"error": f"unknown model '{model}'"}, status_code=400)

    session_dir = os.path.join(OUTPUT_ROOT, session_id)
    if not os.path.isdir(session_dir):
        return JSONResponse({"error": f"session '{session_id}' not found"}, status_code=404)

    cloning_run = m2.CloningRun(session_dir, model)
    cloning_run.start()
    return JSONResponse({"status": "running", "session_id": session_id, "model": model},
                        status_code=202)


@app.get("/module2/events", tags=["Module 2"], summary="Stream cloning progress (SSE)",
         responses={200: {"content": {"text/event-stream": {}}, "description": (
             "Server-Sent Events. Each `data:` line is a JSON object with a `type` field: "
             "`log` ({type, text}), `status` ({type, stage: loading|generating|done, detail}), "
             "`clip` (one manifest clip entry as each is generated), `result` ({type, manifest}), "
             "or `done` ({type, status}) which ends the stream. Connect immediately after "
             "POST /module2/run."
         )}})
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
