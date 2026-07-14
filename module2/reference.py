"""
Bridge from Module 1's output to Module 2's input.

Module 1 writes `output/<session_id>/<tag>_clean.wav` for each emotion tag and (in the notebook /
`pipeline.select_anchors`) chooses two anchors: the calm baseline (`normal`) and the expressive
peak (the highest-RMS of the rest). Module 2 needs those two clips as reference voices — plus, for
models that require a transcript, the exact words spoken in each. Because Module 1 prompts fixed
enrollment sentences (`protocol.EMOTION_SENTENCES`), we already know each clip's transcript with no
speech-to-text step.

This module re-derives the anchors directly from the WAVs present (mirroring
`pipeline.select_anchors`) so Module 2 depends only on the audio files on disk, not on a saved
result object from a Module 1 run.
"""

import os

import numpy as np
import soundfile as sf

import protocol  # top-level module of the live demo (cwd is on sys.path when run as `python -m`)

CALM_BASELINE_TAG = "normal"


def _rms(path: str) -> float:
    y, _ = sf.read(path)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return float(np.sqrt(np.mean(y.astype(np.float64) ** 2))) if len(y) else 0.0


def _clean_wav_path(session_dir: str, tag: str) -> str:
    return os.path.join(session_dir, f"{tag}_clean.wav")


def has_anchors(session_dir: str) -> bool:
    """True if the session has at least the calm baseline clip Module 2 needs to run."""
    return os.path.exists(_clean_wav_path(session_dir, CALM_BASELINE_TAG))


def resolve_anchors(session_dir: str) -> dict:
    """
    Return the two reference anchors for `session_dir`:

        {
          "calm":       {"tag": "normal", "wav": <path>, "text": <transcript>},
          "expressive": {"tag": <tag>,    "wav": <path>, "text": <transcript>},
        }

    "calm" is always `normal`; "expressive" is the highest-RMS of the other emotion clips present
    (mirroring pipeline.select_anchors). Raises if the calm baseline clip is missing.
    """
    calm_path = _clean_wav_path(session_dir, CALM_BASELINE_TAG)
    if not os.path.exists(calm_path):
        raise FileNotFoundError(
            f"No calm baseline clip at '{calm_path}'. Run Module 1 for this session first."
        )

    anchors = {
        "calm": {
            "tag": CALM_BASELINE_TAG,
            "wav": calm_path,
            "text": protocol.EMOTION_SENTENCES[CALM_BASELINE_TAG],
        }
    }

    candidates = {}
    for tag in protocol.EMOTION_ORDER:
        if tag == CALM_BASELINE_TAG:
            continue
        path = _clean_wav_path(session_dir, tag)
        if os.path.exists(path):
            candidates[tag] = path

    if candidates:
        peak_tag = max(candidates, key=lambda t: _rms(candidates[t]))
        anchors["expressive"] = {
            "tag": peak_tag,
            "wav": candidates[peak_tag],
            "text": protocol.EMOTION_SENTENCES[peak_tag],
        }
    else:
        # Only the baseline exists — fall back to it so single-clip sessions still run.
        anchors["expressive"] = dict(anchors["calm"])

    return anchors
