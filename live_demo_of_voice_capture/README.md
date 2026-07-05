# Voice Capture & Cloning Demo

A browser demo that runs two modules from a **single** FastAPI server:

- **Module 1 — Identity & Voice Capture**: the browser records a guided clip (a head turn for
  liveness + 5 short emotion prompts), uploads it, and the server checks liveness and voice quality
  and picks voice anchors. The **left** panel captures using *this device's* camera (a phone's camera
  on a phone, a webcam on a PC); the **right** panel streams each step and the final pass/fail result.
- **Module 2 — Voice Cloning**: takes a captured session and clones that voice with a TTS model
  (VoxCPM2), generating a fixed set of test sentences you can compare by ear at `/module2`.

---

## Quick start (get it running in 3 steps)

```bash
# 1. system dependency: ffmpeg (used to normalize uploaded video)
sudo apt-get update && sudo apt-get install -y ffmpeg

# 2. install Python deps (from this folder)
cd live_demo_of_voice_capture
python -m venv venv && source venv/bin/activate     # optional but recommended
pip install -r requirements.txt

# 3. run the server
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then open:

| URL                              | Page                                        |
|----------------------------------|---------------------------------------------|
| <http://localhost:8000>          | **Module 1** — capture & analyze            |
| <http://localhost:8000/module2>  | **Module 2** — run cloning & compare clips  |

That's it. The first time analysis runs, the ~3.7 MB MediaPipe `face_landmarker.task` model
auto-downloads to the repo root — no manual step needed.

### Requirements notes

- **Python 3.10+** and **ffmpeg** are required.
- `requirements.txt` includes **both** Module 1 (lightweight) **and** Module 2 (heavy: `torch`,
  `voxcpm`) dependencies, so the one `pip install` above sets up everything.
- **Only want the light capture app (no cloning)?** Comment out the Module 2 block at the bottom of
  `requirements.txt` before installing — Module 1 does not need `torch`/`voxcpm`.
- **Have a GPU?** Voice cloning is much faster on GPU (it falls back to CPU otherwise). Install the
  CUDA build of PyTorch matching your setup from <https://pytorch.org> instead of the plain `torch`
  wheel, e.g. `pip install torch --index-url https://download.pytorch.org/whl/cu121`.

---

## Using Module 1 — capture & analyze

On <http://localhost:8000>:

1. Type a **name** for the recording (it labels the session folder so you can find it later).
2. Either:
   - click **Open Camera & Start**, grant camera/mic, and follow the on-screen prompts (live capture), **or**
   - pick a video file and click **Upload & Analyze** to run the same analysis on a pre-recorded clip.
3. The right panel streams each step and ends with a combined **pass/fail** result.

**Auto-chaining to Module 2 (enabled by default):** if a capture **passes**, cloning starts
automatically with the default model and a banner links you to `/module2` to watch it. A **failed**
Module 1 result is *not* sent to Module 2. Turn auto-chaining off with `MODULE2_AUTOCLONE=0`; pick
the default model with `MODULE2_DEFAULT_MODEL=voxcpm`.

### How the analysis works

1. **Liveness (1A)** — MediaPipe yaw over the first 8s, thresholded for a genuine left→right turn.
2. **Voice (1B)** — each 7s emotion window is cut → denoised → quality-checked.
3. **Anchors** — `normal` becomes the calm baseline; the highest-RMS clip becomes the expressive peak.
4. **Result** — combined pass/fail JSON.

The recorded clip's `t=0` is the start of step 1, so it lines up with the fixed analysis windows:
liveness 0–8s, then normal/loud/happy/angry/sad at 7s each (≈43s).

---

## Using Module 2 — voice cloning

### From the browser (`/module2`)

Pick a **session** and a **model**, then click **Run cloning**. Progress (model load, then each
clip) streams live; when it finishes, the generated clips appear grouped by sentence with an audio
player each, next to the **original reference clip**. Use **Load existing outputs** to listen to a
previous run without re-generating.

> To trigger cloning, the server must run in the environment where the Module 2 deps (torch +
> voxcpm) are installed. If `torch.cuda.is_available()` is False in the server process it falls back
> to CPU (much slower, but still works).

### From the command line (batch, at any time)

```bash
cd live_demo_of_voice_capture
python -m module2.run --list-models          # -> voxcpm
python -m module2.run --list-sessions        # sessions under output/ that have clips
python -m module2.run --session <id> --model voxcpm
```

Output lands in `output/<session_id>/module2/<model>/`: one WAV per (test sentence × style
variation) plus a `manifest.json` with per-clip real-time-factor. Each model writes to its own
subfolder, so running a second model later never clobbers the first — you end up with sibling
`module2/voxcpm/`, `module2/dots_tts/`, … folders holding the same sentences to A/B by ear.

### Adding another cloning model

Models are pluggable: write one adapter file in `module2/adapters/` (a `CloningAdapter` subclass)
and add one line to `module2/registry.py`. `runner.py`, `reference.py`, and `run.py` stay untouched.
The adapter interface already carries the flags that differ between models
(`requires_reference_text`, `supports_style_prompt`) — e.g. a model that needs the reference
transcript gets it from Module 1's fixed enrollment sentences (`protocol.EMOTION_SENTENCES`) with no
speech-to-text step.

---

## Testing from a phone (HTTPS via ngrok)

Browsers only allow camera access over **HTTPS or `localhost`**, so capturing from a phone needs an
HTTPS URL. ngrok provides one:

```bash
# terminal 1
uvicorn app:app --host 0.0.0.0 --port 8000
# terminal 2
ngrok http 8000
```

Open the printed `https://<random>.ngrok-free.app` URL on the phone → **Open Camera & Start** now
uses the **phone's** camera. (For real deployment, use a proper TLS certificate instead of ngrok.)

---

## Architecture — capture in the browser, analysis on the server

- **Capture is client-side.** The browser uses `getUserMedia` + `MediaRecorder` to record mic +
  camera together, drawing the guided overlay (head-turn + 5 emotion prompts, with a countdown) over
  a `<video>` element. This is why it uses whatever device opens the page — and why it works after
  deployment, where the **server has no camera**.
- **Analysis is server-side.** The recorded clip is uploaded; the server runs the pipeline
  (MediaPipe yaw liveness, librosa/noisereduce audio quality, anchor selection) and streams
  step/log/result events back over Server-Sent Events (`/events`).
- **Upload normalization.** `MediaRecorder` output (WebM on Chrome/Firefox, MP4 on iOS Safari) often
  has unreliable fps/duration metadata that would break frame-index seeking and timestamp cuts, so
  every upload is first re-encoded to a **constant-frame-rate MP4** (`ffmpeg -r 30 libx264 …`), then
  analyzed. This makes analysis codec-agnostic.

### Where files are stored (per session)

Each attempt gets a unique **session id** (`<name>_<timestamp>_<hex>`, returned by `/upload`) and
nothing is overwritten across runs:

- `recordings/capture_<session_id>.<ext>` — the uploaded clip (`…_norm.mp4` is its normalized copy).
- `output/<session_id>/<emotion>_raw.wav` and `…_clean.wav` — the cut + denoised audio per emotion.
- `output/<session_id>/module2/<model>/` — Module 2's generated clips + `manifest.json`.

---

## Project layout

| Path              | Role                                                                        |
|-------------------|-----------------------------------------------------------------------------|
| `app.py`          | FastAPI routes: `/`, `/config`, `/upload`, `/events` (Module 1) + `/module2*` (Module 2) |
| `session.py`      | `AnalysisSession` — normalizes an uploaded clip, runs the pipeline, feeds the event queue |
| `pipeline.py`     | Analysis functions + `run_analysis()`; auto-downloads the face-landmarker model |
| `protocol.py`     | Shared timing constants + `web_protocol()` (served via `/config`)           |
| `static/`         | `index.html`/`app.js`/`style.css` (Module 1) and `module2.html`/`.js`/`.css` (compare page) |
| `module2/`        | Pluggable voice-cloning package: `adapters/`, `registry.py`, `runner.py`, `service.py`, `run.py` |
| `requirements.txt`| All dependencies (Module 1 + Module 2)                                       |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg: command not found` | Install ffmpeg (Quick start step 1). |
| `face_landmarker.task not found` | It auto-downloads on first analysis; check network access to `storage.googleapis.com`. |
| Cloning runs on CPU / very slow | `torch.cuda.is_available()` is False in the server — install the CUDA torch build and make sure the GPU is free and visible to the server process. |
| `a cloning run is already in progress` | Only one cloning run at a time (GPU-bound). Wait for it to finish. |
| Camera won't open on another device | You need HTTPS — use ngrok (see phone testing above). |
