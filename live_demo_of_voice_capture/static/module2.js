// Module 2 — Voice Cloning compare page.
// Talks to the FastAPI endpoints added in app.py: lists sessions/models, triggers a cloning run,
// streams progress over SSE, then renders the generated clips (per model) grouped by sentence so
// they can be compared by ear against the original reference clip.

const sessionSel = document.getElementById("sessionSel");
const modelSel = document.getElementById("modelSel");
const runBtn = document.getElementById("runBtn");
const loadBtn = document.getElementById("loadBtn");
const runBadge = document.getElementById("runBadge");
const logEl = document.getElementById("log");
const refLabel = document.getElementById("refLabel");
const refPlayer = document.getElementById("refPlayer");
const compareEl = document.getElementById("compare");

let sessions = []; // [{session_id, models_done: [...]}]

function log(text) {
  const line = document.createElement("div");
  line.className = "line";
  line.textContent = text;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setBadge(state, text) {
  runBadge.className = "badge badge-" + state;
  runBadge.textContent = text;
}

function currentSession() {
  return sessions.find((s) => s.session_id === sessionSel.value);
}

// A session id may be passed in the URL (?session=…) — e.g. the link from a Module 1 capture that
// auto-started cloning — so this page can preselect it and attach to the run in progress.
const urlSession = new URLSearchParams(location.search).get("session");

async function fetchSessions(preferred) {
  const sRes = await fetch("/module2/sessions").then((r) => r.json());
  sessions = sRes.sessions || [];
  sessionSel.innerHTML = "";
  sessions.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.session_id;
    o.textContent = s.models_done.length
      ? `${s.session_id}  ✓ ${s.models_done.join(", ")}`
      : s.session_id;
    sessionSel.appendChild(o);
  });
  const want = preferred || urlSession;
  if (want && sessions.some((s) => s.session_id === want)) sessionSel.value = want;
}

async function init() {
  try {
    const mRes = await fetch("/module2/models").then((r) => r.json());
    modelSel.innerHTML = "";
    (mRes.models || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = o.textContent = m;
      modelSel.appendChild(o);
    });
    await fetchSessions();
    if (!sessions.length) {
      compareEl.innerHTML =
        '<p class="m2-empty">No capture sessions found. Record one in Module 1 first.</p>';
      return;
    }
    // If a run is already in progress (e.g. auto-started by a capture), attach to it; else show
    // whatever outputs the selected session already has.
    const attached = await attachIfRunning();
    if (!attached) loadExisting();
  } catch (e) {
    log("Failed to load sessions/models: " + e);
  }
}

async function attachIfRunning() {
  let status;
  try {
    status = await fetch("/module2/status").then((r) => r.json());
  } catch { return false; }
  if (!status.active) return false;

  // Focus the running session/model and stream its progress.
  if (sessions.some((s) => s.session_id === status.session_id)) sessionSel.value = status.session_id;
  if ([...modelSel.options].some((o) => o.value === status.model)) modelSel.value = status.model;
  runBtn.disabled = true;
  loadBtn.disabled = true;
  setBadge("running", "Running…");
  log(`Attached to in-progress run: ${status.model} on ${status.session_id}…`);
  streamEvents(status.model);
  return true;
}

runBtn.addEventListener("click", async () => {
  const session_id = sessionSel.value;
  const model = modelSel.value;
  if (!session_id || !model) return;

  runBtn.disabled = true;
  loadBtn.disabled = true;
  logEl.innerHTML = "";
  setBadge("running", "Running…");
  log(`Starting ${model} on ${session_id}…`);

  try {
    const res = await fetch("/module2/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id, model }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      log("Could not start: " + (err.error || res.status));
      finishRun("fail", "Failed");
      return;
    }
  } catch (e) {
    log("Request failed: " + e);
    finishRun("fail", "Failed");
    return;
  }

  streamEvents(model);
});

// Subscribe to the cloning run's SSE stream and reflect progress. On completion, refresh the
// session list (so the just-finished model shows up) and re-render the comparison.
function streamEvents(model) {
  const es = new EventSource("/module2/events");
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }
    if (d.type === "log") log(d.text);
    else if (d.type === "status") log("• " + d.detail);
    else if (d.type === "clip") log(`  ✓ ${d.sentence_id}/${d.style_id}  (rtf ${d.rtf ?? "?"})`);
    else if (d.type === "result") log("Manifest written.");
    else if (d.type === "done") {
      es.close();
      if (d.status === "done") {
        finishRun("pass", "Done");
        const want = sessionSel.value;
        fetchSessions(want).then(loadExisting);
      } else {
        finishRun("fail", "Error");
      }
    }
  };
  es.onerror = () => { es.close(); finishRun("fail", "Stream lost"); };
}

function finishRun(state, text) {
  setBadge(state, text);
  runBtn.disabled = false;
  loadBtn.disabled = false;
}

loadBtn.addEventListener("click", loadExisting);
sessionSel.addEventListener("change", loadExisting);

async function loadExisting() {
  const s = currentSession();
  if (!s) return;
  const models = s.models_done;
  if (!models.length) {
    refLabel.className = "badge badge-idle";
    refLabel.textContent = "no outputs yet";
    refPlayer.innerHTML = "";
    compareEl.innerHTML =
      '<p class="m2-empty">No generated outputs for this session yet — run a model above.</p>';
    return;
  }

  // Fetch each model's manifest (also gives us the reference clip once).
  const results = await Promise.all(
    models.map((m) =>
      fetch(`/module2/result?session=${encodeURIComponent(s.session_id)}&model=${encodeURIComponent(m)}`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
    )
  );

  // Reference (original voice) player.
  const withRef = results.find((r) => r && r.reference);
  if (withRef && withRef.reference) {
    const ref = withRef.reference;
    refLabel.className = "badge badge-pass";
    refLabel.textContent = "reference: " + ref.calm_tag;
    refPlayer.innerHTML = `<div class="ref-card">
      <div>Original voice — <b>${ref.calm_tag}</b> (the reference the models clone from)</div>
      <audio controls src="/output/${s.session_id}/${ref.calm_file}"></audio></div>`;
  }

  // Build sentence -> style -> [{model, file, rtf}] from every model's clips.
  const bySentence = new Map();
  results.forEach((r, i) => {
    if (!r || !r.manifest) return;
    const model = models[i];
    r.manifest.clips.forEach((c) => {
      if (!bySentence.has(c.sentence_id))
        bySentence.set(c.sentence_id, { text: c.text, styles: new Map() });
      const grp = bySentence.get(c.sentence_id);
      if (!grp.styles.has(c.style_id)) grp.styles.set(c.style_id, []);
      grp.styles.get(c.style_id).push({ model, file: c.file, rtf: c.rtf });
    });
  });

  compareEl.innerHTML = "";
  bySentence.forEach((grp, sentenceId) => {
    const g = document.createElement("div");
    g.className = "sentence-group";
    g.innerHTML = `<p class="sentence-text"><span class="sid">${sentenceId}</span>${grp.text}</p>`;
    grp.styles.forEach((entries, styleId) => {
      entries.forEach((e) => {
        const row = document.createElement("div");
        row.className = "clip-row";
        row.innerHTML = `
          <div class="style-tag">${styleId}<span class="model">${e.model}</span></div>
          <audio controls src="/output/${s.session_id}/module2/${e.model}/${e.file}"></audio>
          <div class="rtf">rtf ${e.rtf ?? "?"}</div>`;
        g.appendChild(row);
      });
    });
    compareEl.appendChild(g);
  });
}

init();
