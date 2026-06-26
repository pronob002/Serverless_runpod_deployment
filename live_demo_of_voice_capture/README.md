# Module 1 — Identity & Voice Capture (demo UI)

A browser demo for the **Module 1** pipeline. The **left** panel captures a guided clip using
**this device's own camera** (the phone's camera on a phone, the webcam in a PC browser); the
**right** panel shows each step (done / running / pending) and a streaming log, ending with the
final Module 1 result JSON.

## Architecture (capture in the browser, analysis on the server)

- **Capture is client-side.** The browser uses `getUserMedia` + `MediaRecorder` to record mic +
  camera together, drawing the guided overlay (head-turn + 5 emotion prompts, with a countdown) over
  a `<video>` element. This is why it uses whatever device opens the page — and why it works after
  deployment, where the **server has no camera**.
- **Analysis is server-side.** The recorded clip is uploaded; the server runs the same pipeline
  (MediaPipe yaw liveness, librosa/noisereduce audio quality, anchor selection) and streams
  step/log/result events back over Server-Sent Events.

The analysis functions are lifted, unchanged in behaviour, from
`../Module1_Identity_Voice_Capture_with_new.ipynb` (now in `pipeline.py`); the protocol timing comes
from `protocol.py` and is served to the browser via `GET /config` so the JS overlay can never drift
from the windows the server analyzes by.

### Upload normalization (important)

`MediaRecorder` output (WebM on Chrome/Firefox, MP4 on iOS Safari) often has unreliable/variable fps
and duration metadata, which would break the pipeline's frame-index seeking and fixed-timestamp audio
cuts. So every upload is first **re-encoded to a constant-frame-rate MP4** (`ffmpeg -r 30 libx264 …`)
with rewritten timestamps, then analyzed. This makes the analysis codec-agnostic.

## Where files are stored (per session)

There are no user accounts yet, so each attempt gets a unique **session id**
(`<timestamp>_<6-hex>`, returned by `/upload`) and nothing is overwritten across runs:

- `recordings/capture_<session_id>.<ext>` — the uploaded clip, and `…_norm.mp4` its normalized copy.
- `output/<session_id>/<emotion>_raw.wav` and `…_clean.wav` — the cut + denoised audio per emotion.

When real user identity is added later, swap the timestamp id for the user/enrollment id (the
`output_dir` passed to `AnalysisSession` is the only thing that needs to change).

## How it works

1. **Liveness (1A)** — MediaPipe yaw over the first 8s, thresholded for a genuine left→right turn.
2. **Voice (1B)** — each 7s emotion window is cut → denoised → quality-checked.
3. **Anchors** — `normal` becomes the calm baseline; the highest-RMS clip becomes the expressive peak.
4. **Result** — combined pass/fail JSON, identical in shape to the notebook's `build_module1_result`.

The recorded clip's `t=0` is the start of step 1 (the 3s pre-roll is not recorded), so it lines up
with the fixed analysis windows: liveness 0–8s, then normal/loud/happy/angry/sad at 7s each (≈43s).

## Run (local, PC webcam)

```bash
# system deps (Debian/Ubuntu): ffmpeg for normalization + audio cut
sudo apt-get update && sudo apt-get install -y ffmpeg

cd live_demo
pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>, then either:
- click **Open Camera & Start**, grant camera/mic, and follow the prompts (live capture), or
- pick a video and click **Upload & Analyze** to run the same analysis on an already-recorded clip.

## Remote testing from a phone (HTTPS via ngrok)

Browsers only allow camera access in a **secure context** (HTTPS or `localhost`), so testing from a
phone needs an HTTPS URL. ngrok provides one:

```bash
# terminal 1
uvicorn app:app --host 0.0.0.0 --port 8000
# terminal 2
ngrok http 8000
```

Open the printed `https://<random>.ngrok-free.app` URL on the phone → **Open Camera & Start** now uses
the **phone's** front camera. (For real deployment, use a proper TLS certificate instead of ngrok.)

## Cross-check against the notebook

Point the notebook's `TEST_VIDEO_PATH` at a saved `recordings/*_norm.mp4` and run it top to bottom —
the liveness pass/fail, per-emotion quality, and chosen anchors should match the UI.

## Anti-spoofing (future)

`pipeline.run_analysis()` has a clearly-marked hook after the yaw check where a MiniFASNetV2 ONNX
check would slot in. It is disabled by default (`ANTISPOOF_MODEL_PATH = None`).

## Files

| File          | Role                                                               |
|---------------|--------------------------------------------------------------------|
| `app.py`      | FastAPI routes: `/`, `/config` (protocol for the browser), `/upload`, `/events` (SSE) |
| `session.py`  | `AnalysisSession` — normalizes an uploaded clip, runs the pipeline, feeds the event queue. `CaptureSession` (legacy server-side recorder) is kept but no longer wired to the UI |
| `pipeline.py` | Analysis functions lifted from the notebook + `run_analysis()`      |
| `protocol.py` | Shared timing constants + `web_protocol()` (served via `/config`)  |
| `static/`     | `index.html` (`<video>` + overlay), `app.js` (browser capture), `style.css` |
