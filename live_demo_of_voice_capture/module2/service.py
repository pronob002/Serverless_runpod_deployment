"""
Web-facing glue for the Module 2 batch runner.

`CloningRun` runs `runner.run_model` on a background thread and pushes structured events onto a
thread-safe queue, exactly like `session.AnalysisSession` does for Module 1 — so the FastAPI layer
can stream cloning progress over SSE with the same pattern. Heavy model imports (torch/voxcpm)
happen inside the thread (via the adapter's `load()`), so importing this module stays cheap.

Discovery helpers (`list_models`, `list_sessions`, `list_outputs`) are import-light and used to
populate the compare page's dropdowns without touching the GPU stack.
"""

import os
import json
import queue
import threading

from . import registry
from . import reference
from .runner import run_model


class CloningRun:
    def __init__(self, session_dir: str, model_name: str):
        self.session_dir = session_dir
        self.model_name = model_name
        self.events = queue.Queue()
        self.active = False
        self._thread = None

    def start(self):
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _emit(self, event):
        self.events.put(event)

    def _log(self, text):
        self._emit({"type": "log", "text": text})

    def _run(self):
        try:
            run_model(self.session_dir, self.model_name, log=self._log, emit=self._emit)
            self.active = False
            self._emit({"type": "done", "status": "done", "message": "complete"})
        except Exception as e:
            self.active = False
            self._emit({"type": "log", "text": f"Cloning error: {e}"})
            self._emit({"type": "done", "status": "error", "message": str(e)})


def list_models() -> list[str]:
    return registry.available()


def list_sessions(output_root: str) -> list[dict]:
    """Sessions under output_root that have clips ready, with the models already generated for each."""
    if not os.path.isdir(output_root):
        return []
    sessions = []
    for name in sorted(os.listdir(output_root), reverse=True):
        session_dir = os.path.join(output_root, name)
        if not (os.path.isdir(session_dir) and reference.has_anchors(session_dir)):
            continue
        m2_dir = os.path.join(session_dir, "module2")
        done_models = (
            sorted(d for d in os.listdir(m2_dir)
                   if os.path.isdir(os.path.join(m2_dir, d)))
            if os.path.isdir(m2_dir) else []
        )
        sessions.append({"session_id": name, "models_done": done_models})
    return sessions


def load_manifest(output_root: str, session_id: str, model_name: str) -> dict | None:
    path = os.path.join(output_root, session_id, "module2", model_name, "manifest.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def reference_info(output_root: str, session_id: str) -> dict:
    """The reference (calm) clip path/tag for a session, for the 'original voice' player."""
    anchors = reference.resolve_anchors(os.path.join(output_root, session_id))
    return {"calm_tag": anchors["calm"]["tag"],
            "calm_file": os.path.basename(anchors["calm"]["wav"])}
