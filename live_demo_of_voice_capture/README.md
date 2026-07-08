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

## Running with Docker (recommended)

The whole app (Module 1 + Module 2, including the ffmpeg/system deps) is packaged into one image
via the included `Dockerfile` and `docker-compose.yml`.

```bash
cd live_demo_of_voice_capture
./run.sh          # auto-detects a usable NVIDIA GPU and enables it; CPU-only otherwise
# or, without GPU auto-detection:
docker compose up --build
```

Then open <http://localhost:8000> (and <http://localhost:8000/module2>) exactly as in the manual
Quick start above.

Notes:

- **First build is slow / large.** `requirements.txt` pulls in `torch` + `voxcpm` (~5GB), so the
  first `docker compose up --build` takes a while and the image is large. Subsequent builds reuse
  Docker's layer cache as long as `requirements.txt` doesn't change.
- **Persistent data.** `recordings/` and `output/` are bind-mounted onto the host (same folders the
  manual run uses), so captured clips and generated clones survive `docker compose down` /
  rebuilds. VoxCPM2's Hugging Face weights are cached in the `hf_cache` named volume so they aren't
  re-downloaded on every rebuild.
- **GPU support (auto-detected).** `docker-compose.yml` is GPU-free by default so it runs on any
  host. `docker-compose.gpu.yml` adds the NVIDIA device reservation as an *overlay* — Compose has no
  built-in "use GPU if present" switch, so `./run.sh` does the detection for you: it checks for
  `nvidia-smi` and that Docker can actually hand a GPU to a container, and only then layers in
  `docker-compose.gpu.yml`. Use `./run.sh` instead of `docker compose up --build` to get this
  automatically; run it plain for CPU-only, or manually with
  `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build` once you're sure the
  host has a GPU. Either way, this requires the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed on the host first — without it, the GPU overlay makes `docker compose up` fail rather
  than falling back to CPU.
- **`buildx`/Bake error on `--build`.** Compose 2.39+ tries to build with `docker buildx bake` by
  default; if the `buildx` plugin isn't installed this fails with a garbled
  `unable to prepare context: path ".../.-" not found` error. `run.sh` already sets
  `COMPOSE_BAKE=false` to avoid it; if you run `docker compose` directly, do the same:
  `COMPOSE_BAKE=false docker compose up --build`.
- **Config via environment.** `MODULE2_AUTOCLONE` and `MODULE2_DEFAULT_MODEL` (same meaning as the
  manual run — see below) can be set in a `.env` file next to `docker-compose.yml` or exported
  before `docker compose up`.
- **Stop / rebuild:**
  ```bash
  docker compose down          # stop
  docker compose up --build    # rebuild after code/dependency changes
  ```
- **Testing from a phone over Docker:** the container still only serves plain HTTP, so you need
  ngrok (or a reverse proxy with TLS) pointed at port 8000, same as the non-Docker case (see
  "Testing from a phone" below).
- **Only want Module 1 (no cloning)?** Comment out the Module 2 block in `requirements.txt` before
  building — this shrinks the image significantly since `torch`/`voxcpm` won't be installed.

### Enabling GPU access for Docker (NVIDIA Container Toolkit)

`nvidia-smi` working on the host only proves the **driver** is installed — it does not mean a
Docker container can use the GPU. Those are two separate pieces of software. Containers are
isolated from the host by default (no GPU, no camera, nothing extra); the **NVIDIA Container
Toolkit** is what lets Docker pass a GPU device through to a container. Without it, `run.sh`
will (correctly) detect no usable GPU and fall back to CPU even on a machine with a good card
sitting idle.

Install it (Ubuntu/Debian host):

```bash
# 1. add the NVIDIA container toolkit apt repo
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 2. install
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# 3. wire it into the Docker daemon and restart
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 4. verify a container can see the GPU
docker run --rm --gpus all busybox true && echo "GPU is usable by Docker"
```

If step 4 prints the confirmation line, `./run.sh` will detect the GPU on the next run and layer
in `docker-compose.gpu.yml` automatically. If it fails, re-check steps 2-3 completed without error
and that `docker info | grep -i runtime` lists `nvidia`.

**`docker compose build` fails with `unable to prepare context: path ".../.-" not found`.** This is
an unrelated Docker Compose bug, not a GPU problem — Compose 2.39+ defaults to building through
`docker buildx bake`, and if the `buildx` plugin isn't installed, that path breaks and mangles the
build context argument. Two ways to fix it, either is fine:

```bash
# Option A — install buildx (recommended; also fixes other buildx-dependent features)
sudo apt-get install -y docker-buildx
docker buildx version   # should now print a version instead of "unknown command"

# Option B — force the older non-Bake builder instead
COMPOSE_BAKE=false docker compose up --build
```

`run.sh` already sets `COMPOSE_BAKE=false` for you, so this only matters if you invoke
`docker compose` directly.

---

## API documentation (Swagger / OpenAPI)

The FastAPI server generates interactive API docs automatically from typed request/response models
in `schemas.py` — this is what the frontend team should integrate against.

| URL                              | What it is                                              |
|-----------------------------------|----------------------------------------------------------|
| `http://<host>:8000/docs`         | **Swagger UI** — browse every endpoint, see request/response schemas, and call them directly from the browser. |
| `http://<host>:8000/redoc`        | **ReDoc** — a read-only, more document-like view of the same spec. |
| `http://<host>:8000/openapi.json` | The raw OpenAPI 3 spec — feed this into a codegen tool (`openapi-typescript`, `orval`, etc.) to generate a typed frontend client instead of hand-writing fetch calls. |

### Endpoint summary

| Method & path | Tag | Purpose |
|---|---|---|
| `GET /health` | Ops | Liveness/readiness probe — also reports whether the process can see a GPU. |
| `GET /config` | Module 1 | The capture protocol (step timings/prompts) the UI overlay drives itself from. |
| `POST /upload` | Module 1 | Upload a recorded/pre-recorded clip; starts analysis; returns a `session_id`. |
| `GET /events` | Module 1 | SSE stream of analysis progress + final pass/fail result for the active session. |
| `GET /module2/models` | Module 2 | Cloning model keys accepted by `/module2/run`. |
| `GET /module2/sessions` | Module 2 | Module 1 sessions that have usable voice anchors, with models already cloned per session. |
| `POST /module2/run` | Module 2 | Start a cloning run for `{session_id, model}`; GPU-bound, one at a time server-wide. |
| `GET /module2/events` | Module 2 | SSE stream of cloning progress (model load → per-clip → final manifest). |
| `GET /module2/result` | Module 2 | Manifest + reference clip info for an already-completed `(session, model)` run. |
| `GET /module2/status` | Module 2 | Whether a cloning run is currently active (e.g. one auto-started after a passing capture). |

Both `/events` and `/module2/events` are **Server-Sent Events**, not plain JSON responses — the
frontend should use `EventSource` (browser) or an SSE client, not a one-shot `fetch`/`axios` call.
The event `type` field distinguishes payload shapes (`log`, `status`, `clip`, `result`, `done`); see
each endpoint's description in `/docs` for the exact shape.

### Cross-origin requests (CORS)

If the frontend is served from a different origin than this API (e.g. a separate Vite/Next.js dev
server), the browser needs CORS enabled. It's on by default (`CORS_ALLOWED_ORIGINS=*`, permissive,
fine for local dev). For a real deployment, set it to the frontend's actual origin(s):

```bash
export CORS_ALLOWED_ORIGINS="https://app.example.com,https://staging.example.com"
```

(comma-separated; also settable in Docker Compose — see Deployment below).

---

## Deployment

This section covers running the app as a persistent, GPU-accelerated service — not just a local
demo. It builds on the Docker setup above; read that first if you haven't.

### 1. Provision the host

- A Linux server (Ubuntu 22.04/24.04 tested) with an **NVIDIA GPU**. VoxCPM2 is a ~2B-parameter
  model — 8GB+ VRAM is comfortable; it also runs on CPU (much slower) if no GPU is available.
- Docker Engine + Docker Compose plugin, and the **NVIDIA Container Toolkit** so containers can see
  the GPU (see "Running with Docker" above for the exact install commands). Verify with:
  ```bash
  docker run --rm --gpus all busybox true && echo "GPU is usable by Docker"
  ```
- Enough disk for: the image + build cache (~10-15GB), the Hugging Face model cache (~5GB,
  `hf_cache` volume), and growing `recordings/`/`output/` data (each capture attempt is tens of MB).

### 2. Get the code onto the host and configure it

```bash
git clone <this-repo-url>
cd <repo>/live_demo_of_voice_capture
```

Set production config via a `.env` file next to `docker-compose.yml` (Compose reads it
automatically) or exported environment variables:

```bash
# .env
MODULE2_AUTOCLONE=1
MODULE2_DEFAULT_MODEL=voxcpm
CORS_ALLOWED_ORIGINS=https://app.example.com
```

### 3. Put TLS in front of it

Browsers only grant camera/microphone access on **HTTPS or `localhost`** — this is not optional for
a real deployment (ngrok, used earlier for phone testing, is dev-only). Put a reverse proxy in front
of the container that terminates TLS and forwards to port 8000:

- **Simplest**: [Caddy](https://caddyserver.com/) with a one-line Caddyfile (`app.example.com {
  reverse_proxy localhost:8000 }`) — it gets and renews a Let's Encrypt cert automatically.
- **Alternative**: nginx + `certbot`, or your cloud provider's managed load balancer with a TLS
  cert attached (e.g. an AWS ALB, GCP HTTPS LB).

Either way: only expose 443 (and 80 for the ACME challenge, if using Caddy/certbot) externally; keep
port 8000 bound to localhost/internal network only, not the public internet directly. **SSE
streams (`/events`, `/module2/events`) must pass through the proxy unbuffered** — Caddy does this by
default; for nginx add `proxy_buffering off;` and `proxy_read_timeout 3600s;` on those two locations
(the analysis/cloning run can take minutes, and a buffering proxy will otherwise hold back events or
time out the connection).

### 4. Start it with GPU access, detached

```bash
./run.sh -d
# or, explicitly:
COMPOSE_BAKE=false docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

`restart: unless-stopped` (already set in `docker-compose.yml`) brings the container back up after a
crash or host reboot, as long as the Docker daemon itself starts on boot (`systemctl enable docker`,
enabled by default on most distros).

Confirm the container actually has GPU access and the app sees it:

```bash
docker compose exec voice-capture nvidia-smi        # GPU visible inside the container?
curl -s http://localhost:8000/health | python3 -m json.tool   # cuda_available: true?
```

### 5. Updating a live deployment

```bash
git pull
COMPOSE_BAKE=false docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

This is a brief-downtime redeploy (single container, replaced in place) — there's no rolling update
here, which is fine given the single-GPU/single-active-run design (see the scaling note below).

### Important constraints to know before deploying

- **Not horizontally scalable as-is.** `app.py` keeps the active Module 1 session and Module 2
  cloning run in **process-global variables** (`session`, `cloning_run`), and cloning is GPU-bound
  to one run at a time. Do not run multiple uvicorn workers or multiple container replicas behind a
  load balancer — requests could land on different processes that don't share that state, breaking
  the SSE progress stream and the "one run at a time" guarantee. Run exactly **one** container/worker
  per GPU.
- **No authentication.** `/upload` and `/module2/run` are open to anyone who can reach the server —
  fine behind a private network or for an internal demo, not fine on the open internet without adding
  auth (e.g. an API key checked in a `Depends()`, or auth at the reverse-proxy layer) in front of it.
- **Storage growth.** Nothing prunes `recordings/`/`output/` automatically. For long-running
  deployments, plan a retention job (cron deleting old session folders) or move them to
  object storage periodically.

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
| `app.py`          | FastAPI routes: `/health`, `/config`, `/upload`, `/events` (Module 1) + `/module2*` (Module 2) |
| `schemas.py`      | Pydantic request/response models — source of the `/docs` Swagger schema                     |
| `session.py`      | `AnalysisSession` — normalizes an uploaded clip, runs the pipeline, feeds the event queue |
| `pipeline.py`     | Analysis functions + `run_analysis()`; auto-downloads the face-landmarker model |
| `protocol.py`     | Shared timing constants + `web_protocol()` (served via `/config`)           |
| `static/`         | `index.html`/`app.js`/`style.css` (Module 1) and `module2.html`/`.js`/`.css` (compare page) |
| `module2/`        | Pluggable voice-cloning package: `adapters/`, `registry.py`, `runner.py`, `service.py`, `run.py` |
| `requirements.txt`| All dependencies (Module 1 + Module 2)                                       |
| `Dockerfile`      | Container image: system deps (ffmpeg, opencv/mediapipe libs) + `pip install -r requirements.txt` |
| `docker-compose.yml` | One-command run (`docker compose up --build`); mounts `recordings/`, `output/`, and the HF weights cache |
| `docker-compose.gpu.yml` | GPU overlay (adds the NVIDIA device reservation) — applied automatically by `run.sh` |
| `run.sh`          | Detects a usable NVIDIA GPU and layers in `docker-compose.gpu.yml` if found, else runs CPU-only |


