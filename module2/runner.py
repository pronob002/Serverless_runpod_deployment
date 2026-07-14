"""
Run one cloning model over one Module 1 session and write comparable output.

`run_model(session_dir, model_name)`:
  1. resolve the session's reference anchors (reference.resolve_anchors),
  2. load the model's adapter,
  3. generate every (test sentence × style) into `module2/<model_name>/<sentence>_<style>.wav`,
  4. write a `manifest.json` recording what was run and how fast, then unload the model.

Outputs are namespaced per model under the session, so running a second model at a later time lands
in a sibling folder and never clobbers the first — exactly what a later side-by-side listen needs.
"""

import os
import json
import time
from datetime import datetime, timezone

import soundfile as sf

from . import reference
from . import registry
from .test_sentences import TEST_SENTENCES, STYLE_VARIATIONS


def run_model(session_dir: str, model_name: str, log=print, emit=None) -> dict:
    """
    Generate the comparison set for `model_name` on `session_dir`. Returns the manifest dict.

    `log(text)` receives human-readable progress lines (defaults to print). `emit(event_dict)`, if
    given, receives structured events the web UI streams over SSE:
        {"type": "status", "stage": "loading"|"generating"|"done", "detail": ...}
        {"type": "clip", ...one manifest clip entry...}
        {"type": "result", "manifest": {...}}
    """
    def _emit(event):
        if emit is not None:
            emit(event)

    anchors = reference.resolve_anchors(session_dir)
    ref = anchors["calm"]  # the calm baseline is the reference voice, per the notebook's Section 3/4
    log(f"Reference voice: {ref['tag']} ({os.path.basename(ref['wav'])})")

    adapter = registry.get_adapter(model_name)
    if adapter.requires_reference_text and not ref.get("text"):
        raise ValueError(
            f"Model '{model_name}' requires the reference transcript but none is available "
            f"for tag '{ref['tag']}'."
        )

    log(f"Loading model '{model_name}' (this can take a while on first run)…")
    _emit({"type": "status", "stage": "loading", "detail": f"Loading {model_name}…"})
    t0 = time.time()
    adapter.load()
    log(f"Model loaded in {time.time() - t0:.1f}s (device={getattr(adapter, 'device', 'n/a')}).")
    sr = adapter.sample_rate
    _emit({"type": "status", "stage": "generating",
           "detail": f"Model loaded ({time.time() - t0:.0f}s). Generating…"})

    out_dir = os.path.join(session_dir, "module2", model_name)
    os.makedirs(out_dir, exist_ok=True)

    clips = []
    try:
        for sentence_id, text in TEST_SENTENCES:
            for style_id, style in STYLE_VARIATIONS:
                # Only pass a style to models that understand one, so an unsupported model isn't
                # handed an instruction it would read aloud as literal text.
                effective_style = style if (style and adapter.supports_style_prompt) else None

                log(f"  generating [{sentence_id}/{style_id}]…")
                g0 = time.time()
                wav = adapter.generate(
                    text=text,
                    reference_wav_path=ref["wav"],
                    reference_text=ref.get("text"),
                    style=effective_style,
                )
                elapsed = time.time() - g0

                fname = f"{sentence_id}_{style_id}.wav"
                sf.write(os.path.join(out_dir, fname), wav, sr)

                audio_sec = len(wav) / sr if sr else 0.0
                clip = {
                    "file": fname,
                    "sentence_id": sentence_id,
                    "style_id": style_id,
                    "style_applied": effective_style is not None,
                    "text": text,
                    "duration_sec": round(audio_sec, 2),
                    "gen_sec": round(elapsed, 2),
                    "rtf": round(elapsed / audio_sec, 3) if audio_sec else None,
                }
                clips.append(clip)
                _emit({"type": "clip", **clip})
    finally:
        adapter.unload()

    manifest = {
        "model": model_name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": getattr(adapter, "device", None),
        "sample_rate": sr,
        "reference": {
            "calm_tag": anchors["calm"]["tag"],
            "expressive_tag": anchors["expressive"]["tag"],
            "reference_used": ref["tag"],
        },
        "adapter_params": {
            k: getattr(adapter, k) for k in ("cfg_value", "inference_timesteps")
            if hasattr(adapter, k)
        },
        "clips": clips,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    log(f"Done — {len(clips)} clips + manifest.json in {out_dir}")
    _emit({"type": "result", "manifest": manifest})
    return manifest
