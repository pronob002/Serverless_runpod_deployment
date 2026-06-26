// Front-end controller.
//
// Capture happens HERE, in the browser, using this device's own camera (getUserMedia +
// MediaRecorder). We draw the guided overlay over a <video> element, drive the protocol
// timeline in JS, then upload the recorded clip to the server, which analyzes it and streams
// step/log/result events back over SSE. (A pre-recorded file can be uploaded instead.)

const ICON = { pending: "○", running: "◉", pass: "✓", fail: "✗" };

// Ordered step definitions. `id` matches the `stage` field emitted by the backend (analysis)
// or set directly in JS (recording). `live: true` rows only exist in the live-capture flow and
// are hidden when analyzing an uploaded file.
const STEP_DEFS = [
  { group: "Recording", live: true },
  { id: "record_0", label: "Step 1 · Liveness (turn left → right)", live: true },
  { id: "record_1", label: "Step 2 · Normal voice", live: true },
  { id: "record_2", label: "Step 3 · Loud voice", live: true },
  { id: "record_3", label: "Step 4 · Happy voice", live: true },
  { id: "record_4", label: "Step 5 · Angry voice", live: true },
  { id: "record_5", label: "Step 6 · Sad voice", live: true },
  { id: "upload",   label: "Upload & normalize", live: true },
  { group: "Analysis" },
  { id: "liveness",      label: "Liveness — head-turn check" },
  { id: "audio_normal",  label: "Audio quality — normal" },
  { id: "audio_loud",    label: "Audio quality — loud" },
  { id: "audio_happy",   label: "Audio quality — happy" },
  { id: "audio_angry",   label: "Audio quality — angry" },
  { id: "audio_sad",     label: "Audio quality — sad" },
  { id: "anchors",       label: "Select emotion anchors" },
  { id: "result",        label: "Final Module 1 result" },
];

const feed       = document.getElementById("feed");
const overlay    = document.getElementById("overlay");
const ovHeadline = document.getElementById("ovHeadline");
const ovSubtext  = document.getElementById("ovSubtext");
const ovBarFill  = document.getElementById("ovBarFill");
const ovCountdown = document.getElementById("ovCountdown");
const placeholder = document.getElementById("placeholder");
const startBtn   = document.getElementById("startBtn");
const stopBtn    = document.getElementById("stopBtn");
const fileInput  = document.getElementById("fileInput");
const uploadBtn  = document.getElementById("uploadBtn");
const stepsEl    = document.getElementById("steps");
const logEl      = document.getElementById("log");
const badgeEl    = document.getElementById("overallBadge");
const resultBox  = document.getElementById("resultBox");
const resultJson = document.getElementById("resultJson");

let eventSource = null;
const stepRows = {}; // id -> { row, icon, detail }

let PROTOCOL = null;          // fetched from /config
let mediaStream = null;
let mediaRecorder = null;
let recordedChunks = [];
let cancelRequested = false;  // set by Stop, checked by the timeline
let uploadAfterStop = false;  // true = recording finished normally → upload; false = cancelled

// ── Steps + log rendering ────────────────────────────────────────────────────
function buildSteps(mode = "live") {
  stepsEl.innerHTML = "";
  for (const k in stepRows) delete stepRows[k];
  for (const def of STEP_DEFS) {
    if (mode === "upload" && def.live) continue; // skip recording steps for file uploads
    const li = document.createElement("li");
    if (def.group) {
      li.className = "step group-label";
      li.textContent = def.group;
      stepsEl.appendChild(li);
      continue;
    }
    li.className = "step pending";
    li.innerHTML =
      `<span class="icon">${ICON.pending}</span>` +
      `<span class="label">${def.label}</span>` +
      `<span class="detail"></span>`;
    stepsEl.appendChild(li);
    stepRows[def.id] = {
      row: li,
      icon: li.querySelector(".icon"),
      detail: li.querySelector(".detail"),
    };
  }
}

function setStep(id, status, detail) {
  const r = stepRows[id];
  if (!r) return;
  r.row.className = `step ${status}`;
  r.icon.textContent = ICON[status] || ICON.pending;
  if (detail !== undefined && detail !== "") r.detail.textContent = detail;
}

function addLog(text) {
  const line = document.createElement("div");
  line.className = "line";
  const t = new Date().toLocaleTimeString();
  line.textContent = `[${t}] ${text}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setBadge(state, text) {
  badgeEl.className = `badge badge-${state}`;
  badgeEl.textContent = text;
}

function resetUI(mode) {
  buildSteps(mode);
  logEl.innerHTML = "";
  resultBox.classList.add("hidden");
  resultJson.textContent = "";
  setBadge("running", mode === "upload" ? "Analyzing" : "Recording");
}

function setBusy(busy) {
  startBtn.disabled = busy;
  uploadBtn.disabled = busy;
  fileInput.disabled = busy;
  stopBtn.disabled = !busy;
}

// ── Camera teardown / terminal cleanup ───────────────────────────────────────
function stopTracks() {
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
  }
}

function finish() {
  setBusy(false);
  stopTracks();
  feed.srcObject = null;
  overlay.classList.add("hidden");
  placeholder.classList.remove("hidden");
  if (eventSource) { eventSource.close(); eventSource = null; }
}

// ── SSE: analysis events from the server ─────────────────────────────────────
function subscribeEvents() {
  stopBtn.disabled = true; // analysis is server-side; nothing to cancel from here
  eventSource = new EventSource("/events");
  eventSource.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    handleEvent(ev);
  };
  eventSource.onerror = () => { /* keep-alives may trip this; ignore unless closed */ };
}

function handleEvent(ev) {
  switch (ev.type) {
    case "stage":
      setStep(ev.stage, ev.status, ev.detail);
      break;
    case "log":
      addLog(ev.text);
      break;
    case "result":
      resultBox.classList.remove("hidden");
      resultJson.textContent = JSON.stringify(ev.result, null, 2);
      const pass = ev.result.overall_result === "pass";
      setBadge(pass ? "pass" : "fail", pass ? "Pass" : "Fail");
      break;
    case "done":
      if (ev.status === "cancelled") setBadge("idle", "Cancelled");
      else if (ev.status === "error") setBadge("fail", "Error");
      addLog("Session " + ev.status + (ev.message ? `: ${ev.message}` : ""));
      finish();
      break;
  }
}

// ── Live browser capture ─────────────────────────────────────────────────────
function pickMimeType() {
  const prefs = ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"];
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
  for (const m of prefs) if (MediaRecorder.isTypeSupported(m)) return m;
  return "";
}

// Animate one timed phase; rejects if Stop was pressed. `stepId` (optional) is the step row to
// mark running → pass. `headline`/`subtext` fill the overlay.
function runPhase(headline, subtext, durationSec, stepId) {
  if (stepId) setStep(stepId, "running");
  ovHeadline.textContent = headline;
  ovSubtext.textContent = subtext;
  const endAt = performance.now() + durationSec * 1000;
  return new Promise((resolve, reject) => {
    function tick() {
      if (cancelRequested) { reject(new Error("cancelled")); return; }
      const remaining = Math.max(0, (endAt - performance.now()) / 1000);
      ovCountdown.textContent = remaining.toFixed(1) + "s";
      ovBarFill.style.width = (100 * remaining / durationSec) + "%";
      if (remaining <= 0) {
        if (stepId) setStep(stepId, "pass");
        resolve();
        return;
      }
      requestAnimationFrame(tick);
    }
    tick();
  });
}

async function start() {
  if (!PROTOCOL) { addLog("Protocol not loaded yet — try again in a moment."); return; }

  setBusy(true);
  resetUI("live");
  cancelRequested = false;
  uploadAfterStop = false;
  recordedChunks = [];

  // 1. Acquire THIS device's camera + mic.
  addLog("Requesting camera & microphone…");
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" },
      audio: true,
    });
  } catch (e) {
    addLog("Camera/mic unavailable or denied: " + e.message);
    addLog("Note: camera access needs HTTPS (or localhost). On a phone, use the ngrok HTTPS URL.");
    setBadge("fail", "Error");
    finish();
    return;
  }
  feed.srcObject = mediaStream;
  placeholder.classList.add("hidden");
  overlay.classList.remove("hidden");

  // 2. Set up the recorder (started later so the clip's t=0 aligns with step 0).
  const mimeType = pickMimeType();
  try {
    mediaRecorder = mimeType
      ? new MediaRecorder(mediaStream, { mimeType })
      : new MediaRecorder(mediaStream);
  } catch (e) {
    addLog("MediaRecorder unsupported on this browser: " + e.message);
    setBadge("fail", "Error");
    finish();
    return;
  }
  mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) recordedChunks.push(e.data); };
  mediaRecorder.onstop = onRecordingStopped;

  // 3. Pre-roll (not recorded), then record while running the guided steps.
  try {
    await runPhase("GET READY", "Recording starts in…", PROTOCOL.preRollSec, null);
    await new Promise((resolve) => { mediaRecorder.onstart = resolve; mediaRecorder.start(); });
    addLog("Recording started.");
    for (const s of PROTOCOL.steps) {
      await runPhase(s.headline, s.subtext, s.durationSec, s.id);
    }
  } catch {
    // Stop was pressed: stop the recorder without uploading.
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      uploadAfterStop = false;
      mediaRecorder.stop();
    } else {
      addLog("Capture cancelled.");
      setBadge("idle", "Cancelled");
      finish();
    }
    return;
  }

  // 4. Finished cleanly → stop; onstop uploads the clip.
  uploadAfterStop = true;
  ovHeadline.textContent = "DONE";
  ovSubtext.textContent = "Uploading & analyzing…";
  ovCountdown.textContent = "";
  ovBarFill.style.width = "0%";
  mediaRecorder.stop();
}

async function onRecordingStopped() {
  // Free the camera as soon as recording ends.
  stopTracks();
  feed.srcObject = null;
  overlay.classList.add("hidden");

  if (!uploadAfterStop) {
    addLog("Capture cancelled — clip discarded.");
    setBadge("idle", "Cancelled");
    finish();
    return;
  }

  const type = (mediaRecorder && mediaRecorder.mimeType) || "video/webm";
  const ext = type.includes("mp4") ? "mp4" : "webm";
  const blob = new Blob(recordedChunks, { type });

  setStep("upload", "running");
  addLog(`Captured ${(blob.size / 1e6).toFixed(1)} MB — uploading…`);

  const form = new FormData();
  form.append("file", blob, `capture.${ext}`);
  let res;
  try {
    res = await fetch("/upload", { method: "POST", body: form });
  } catch (e) {
    addLog("Upload failed: " + e.message);
    setStep("upload", "fail");
    setBadge("fail", "Error");
    finish();
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    addLog("Upload failed: " + (body.error || res.status));
    setStep("upload", "fail");
    setBadge("fail", "Error");
    finish();
    return;
  }
  setStep("upload", "pass");
  addLog("Upload complete — analyzing…");
  subscribeEvents();
}

// ── Upload a pre-recorded file (no camera) ───────────────────────────────────
async function uploadAndAnalyze() {
  const file = fileInput.files[0];
  if (!file) { addLog("Pick a video file first."); return; }

  setBusy(true);
  resetUI("upload");
  placeholder.classList.remove("hidden");
  addLog(`Uploading ${file.name} (${(file.size / 1e6).toFixed(1)} MB)…`);

  const form = new FormData();
  form.append("file", file);
  let res;
  try {
    res = await fetch("/upload", { method: "POST", body: form });
  } catch (e) {
    addLog("Upload failed: " + e.message);
    setBadge("fail", "Error");
    finish();
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    addLog("Upload failed: " + (body.error || res.status));
    setBadge("fail", "Error");
    finish();
    return;
  }
  addLog("Upload complete — analyzing…");
  subscribeEvents();
}

function stop() {
  // Requests cancellation of the in-progress capture timeline.
  cancelRequested = true;
  stopBtn.disabled = true;
  addLog("Stopping…");
}

// ── Init ─────────────────────────────────────────────────────────────────────
async function loadConfig() {
  try {
    PROTOCOL = await (await fetch("/config")).json();
  } catch (e) {
    addLog("Could not load protocol config: " + e.message);
  }
}

startBtn.addEventListener("click", start);
stopBtn.addEventListener("click", stop);
uploadBtn.addEventListener("click", uploadAndAnalyze);
buildSteps();
loadConfig();
