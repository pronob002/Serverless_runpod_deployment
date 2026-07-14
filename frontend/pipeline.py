"""
Module 1 analysis pipeline — lifted verbatim (in behaviour) from
`Module1_Identity_Voice_Capture_with_new.ipynb` so it runs outside Jupyter.

Two sub-modules:
  1A  Human detection (liveness) — MediaPipe yaw over the first LIVENESS_WINDOW_SEC seconds,
      thresholded to confirm a genuine left-then-right head turn.
  1B  Voice collection — cut each fixed-position emotion window from the same recording, denoise,
      quality-check; then choose the two emotion anchors Module 2 needs.

`run_analysis(video_path, emit)` runs every stage in order, calling
`emit(event_dict)` after each so the UI can show live progress, and returns the final result dict
(identical shape to the notebook's `build_module1_result`).
"""

import os
import subprocess
import urllib.request

import cv2
import numpy as np
import librosa
import soundfile as sf
import noisereduce as nr
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from protocol import SEGMENT_BOUNDARIES, EMOTION_ORDER


# ── Tunables (same starting points as the notebook) ───────────────────────────
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "face_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
YAW_THRESHOLD_DEGREES = 25.0


def ensure_face_landmarker_model(model_path=MODEL_PATH, log=print):
    """Download the MediaPipe face_landmarker model (~3.7MB) if it isn't already present.

    The notebook downloads this one-time; the pipeline mirrors that so a fresh checkout doesn't
    fail liveness with 'face_landmarker.task not found'. Returns True if the model is available.
    """
    if os.path.exists(model_path):
        return True
    log(f"Downloading face_landmarker.task (one-time, ~3.7MB) → {model_path}")
    try:
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, model_path)
        log("Face landmarker model downloaded.")
        return True
    except Exception as e:
        log(f"Could not download face_landmarker.task ({e}). "
            f"Download it manually from {MODEL_URL} and place it at {model_path}.")
        return False

MIN_DURATION_SEC = 0.8
MIN_RMS_LOUDNESS = 0.01
SILENCE_CHECK_TOP_DB = 30

# Anti-spoofing hook (future): set to a MiniFASNetV2 ONNX path to enable. Off by default.
ANTISPOOF_MODEL_PATH = None


# ── Sub-module 1A: liveness via yaw ───────────────────────────────────────────
def create_face_landmarker(model_path=MODEL_PATH):
    """Creates a MediaPipe FaceLandmarker configured for single-frame IMAGE mode."""
    base_options = mp_python.BaseOptions(model_asset_path=model_path)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        output_facial_transformation_matrixes=True,
    )
    return mp_vision.FaceLandmarker.create_from_options(options)


def yaw_from_transform_matrix(matrix):
    """Extracts yaw (left/right rotation, degrees) from a 4x4 facial transformation matrix."""
    r = matrix[:3, :3]
    sy = np.sqrt(r[0, 0] ** 2 + r[1, 0] ** 2)
    yaw = np.degrees(np.arctan2(-r[2, 0], sy))
    return yaw


def estimate_yaw_for_frame(frame, landmarker):
    """Returns yaw angle in degrees for one BGR frame, or None if no face was detected."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)
    if not result.facial_transformation_matrixes:
        return None
    matrix = np.array(result.facial_transformation_matrixes[0]).reshape(4, 4)
    return yaw_from_transform_matrix(matrix)


def analyze_video_yaw(video_path, start_sec=0, end_sec=None, sample_every_n_frames=2):
    """Runs yaw estimation across a time window, returning [(timestamp_sec, yaw_degrees), ...]."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    start_frame = int(start_sec * fps)
    end_frame = int(end_sec * fps) if end_sec is not None else None

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    results = []

    landmarker = create_face_landmarker()
    try:
        frame_idx = start_frame
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if end_frame is not None and frame_idx >= end_frame:
                break
            if frame_idx % sample_every_n_frames == 0:
                yaw = estimate_yaw_for_frame(frame, landmarker)
                timestamp = frame_idx / fps
                results.append((timestamp, yaw))
            frame_idx += 1
    finally:
        landmarker.close()
        cap.release()
    return results


def check_left_right_turn(yaw_series, threshold=YAW_THRESHOLD_DEGREES):
    """Decides whether a left-then-right (or right-then-left) turn happened, with diagnostics."""
    yaws = [y for t, y in yaw_series if y is not None]
    detection_rate = len(yaws) / len(yaw_series) if yaw_series else 0

    if detection_rate < 0.5:
        return {
            "result": "fail",
            "reason": "face_not_consistently_detected",
            "detection_rate": round(detection_rate, 2),
        }

    max_left = float(min(yaws))   # most negative
    max_right = float(max(yaws))  # most positive

    turned_left = bool(max_left <= -threshold)
    turned_right = bool(max_right >= threshold)

    if turned_left and turned_right:
        return {
            "result": "pass",
            "reason": None,
            "max_left_deg": round(max_left, 1),
            "max_right_deg": round(max_right, 1),
            "detection_rate": round(detection_rate, 2),
        }
    return {
        "result": "fail",
        "reason": "insufficient_turn_range",
        "max_left_deg": round(max_left, 1),
        "max_right_deg": round(max_right, 1),
        "detection_rate": round(detection_rate, 2),
    }


# ── Sub-module 1B: voice collection ───────────────────────────────────────────
def extract_audio_range(video_path, output_path, start_sec, end_sec):
    """Cuts a time range of the audio track to 16-bit PCM WAV via FFmpeg's -ss/-to options."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", str(start_sec), "-to", str(end_sec),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFmpeg error:")
        print(result.stderr[-800:])
        return False
    return True


def reduce_noise(y, sr, noise_sample_duration=0.5):
    """Spectral-gating denoise using the first `noise_sample_duration`s of the segment as profile."""
    noise_sample_len = int(noise_sample_duration * sr)
    noise_clip = y[:noise_sample_len] if len(y) > noise_sample_len else y
    cleaned = nr.reduce_noise(y=y, sr=sr, y_noise=noise_clip, stationary=True)
    return cleaned


def contains_speech(y, sr, top_db=SILENCE_CHECK_TOP_DB):
    """Checks whether at least one non-silent region exists in this isolated clip."""
    intervals = librosa.effects.split(y, top_db=top_db)
    return len(intervals) > 0


def quality_check_clip(y, sr, emotion_tag):
    duration_sec = len(y) / sr
    duration_ok = bool(duration_sec >= MIN_DURATION_SEC)
    rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))
    loudness_ok = bool(rms >= MIN_RMS_LOUDNESS)
    speech_ok = bool(contains_speech(y, sr))

    passed = duration_ok and loudness_ok and speech_ok
    return {
        "emotion_tag": emotion_tag,
        "result": "pass" if passed else "fail",
        "duration_sec": round(duration_sec, 2),
        "duration_ok": duration_ok,
        "rms_loudness": round(rms, 4),
        "loudness_ok": loudness_ok,
        "contains_speech": speech_ok,
    }


# ── Section 3: anchor selection ───────────────────────────────────────────────
def select_anchors(emotion_clips):
    if "normal" not in emotion_clips:
        return None
    calm_baseline = emotion_clips["normal"]

    candidates = {tag: data for tag, data in emotion_clips.items() if tag != "normal"}
    if not candidates:
        return None

    peak_tag = max(candidates, key=lambda t: candidates[t]["quality"]["rms_loudness"])
    expressive_peak = candidates[peak_tag]
    validation_tags = [t for t in candidates if t != peak_tag]

    return {
        "calm_baseline_tag": "normal",
        "calm_baseline_rms": calm_baseline["quality"]["rms_loudness"],
        "expressive_peak_tag": peak_tag,
        "expressive_peak_rms": expressive_peak["quality"]["rms_loudness"],
        "validation_clip_tags": validation_tags,
    }


# ── Section 4: combined result ────────────────────────────────────────────────
def build_module1_result(sub1a_result, emotion_clips, anchor_selection):
    all_clips_passed = (
        all(data["quality"]["result"] == "pass" for data in emotion_clips.values())
        if emotion_clips else False
    )
    overall_pass = (
        sub1a_result is not None
        and sub1a_result.get("result") == "pass"
        and all_clips_passed
        and anchor_selection is not None
    )
    return {
        "module": "module_1_identity_voice_capture",
        "overall_result": "pass" if overall_pass else "fail",
        "sub_module_1a_human_detection": sub1a_result,
        "sub_module_1b_voice_collection": {
            "clips": {tag: data["quality"] for tag, data in emotion_clips.items()},
        },
        "emotion_anchors_for_module_2": anchor_selection,
    }


# ── Orchestrator ──────────────────────────────────────────────────────────────
def run_analysis(video_path, emit, output_dir="output"):
    """
    Runs the whole Module 1 analysis on a saved recording.

    `emit(event)` is called with dicts shaped for the UI, e.g.:
        {"type": "stage", "stage": "liveness", "status": "running"|"pass"|"fail", "detail": "..."}
        {"type": "log", "text": "..."}
    Returns the final result dict from `build_module1_result`.
    """
    def log(text):
        emit({"type": "log", "text": text})

    def stage(name, status, detail=""):
        emit({"type": "stage", "stage": name, "status": status, "detail": detail})

    os.makedirs(output_dir, exist_ok=True)

    # ── 1A: liveness ──────────────────────────────────────────────────────────
    stage("liveness", "running", "Measuring head yaw…")
    ensure_face_landmarker_model(log=log)  # fetch the model on first run if it's missing
    if not os.path.exists(MODEL_PATH):
        log(f"Missing model file: {MODEL_PATH}. Cannot run liveness.")
        sub1a_result = {"result": "fail", "reason": "model_missing"}
        stage("liveness", "fail", "face_landmarker.task not found")
    else:
        liveness_start, liveness_end = SEGMENT_BOUNDARIES["liveness"]
        log(f"Analyzing yaw over {liveness_start}-{liveness_end}s…")
        yaw_series = analyze_video_yaw(video_path, start_sec=liveness_start, end_sec=liveness_end)
        detected = [y for t, y in yaw_series if y is not None]
        log(f"Face detected in {len(detected)}/{len(yaw_series)} sampled frames.")
        sub1a_result = check_left_right_turn(yaw_series)

        # ── Anti-spoofing hook (future, disabled) ─────────────────────────────
        # if ANTISPOOF_MODEL_PATH:
        #     spoof = run_antispoof(video_path, liveness_start, liveness_end, ANTISPOOF_MODEL_PATH)
        #     sub1a_result["antispoof"] = spoof
        #     if spoof["result"] != "real": sub1a_result["result"] = "fail"

        if sub1a_result["result"] == "pass":
            stage("liveness", "pass",
                  f"L {sub1a_result['max_left_deg']}° / R {sub1a_result['max_right_deg']}°")
        else:
            stage("liveness", "fail", sub1a_result.get("reason", ""))

    # ── 1B: per-emotion voice clips ───────────────────────────────────────────
    emotion_clips = {}
    for emotion_tag in EMOTION_ORDER:
        stage(f"audio_{emotion_tag}", "running", "Cut → denoise → quality-check…")
        start_sec, end_sec = SEGMENT_BOUNDARIES[emotion_tag]

        raw_audio_path = os.path.join(output_dir, f"{emotion_tag}_raw.wav")
        ok = extract_audio_range(video_path, raw_audio_path, start_sec, end_sec)
        if not ok:
            log(f"[{emotion_tag}] audio extraction failed for window {start_sec}-{end_sec}s.")
            stage(f"audio_{emotion_tag}", "fail", "extraction failed")
            continue

        y, sr = librosa.load(raw_audio_path, sr=None)
        y_clean = reduce_noise(y, sr)
        clean_audio_path = os.path.join(output_dir, f"{emotion_tag}_clean.wav")
        sf.write(clean_audio_path, y_clean, sr)

        quality = quality_check_clip(y_clean, sr, emotion_tag)
        emotion_clips[emotion_tag] = {"audio": y_clean, "sr": sr, "quality": quality}

        log(f"[{emotion_tag}] window={start_sec}-{end_sec}s "
            f"duration={quality['duration_sec']}s rms={quality['rms_loudness']} "
            f"speech={quality['contains_speech']}")
        stage(f"audio_{emotion_tag}", "pass" if quality["result"] == "pass" else "fail",
              f"rms={quality['rms_loudness']}")

    # ── Section 3: anchors ────────────────────────────────────────────────────
    stage("anchors", "running", "Choosing calm baseline + expressive peak…")
    anchor_selection = select_anchors(emotion_clips) if emotion_clips else None
    if anchor_selection:
        stage("anchors", "pass",
              f"baseline={anchor_selection['calm_baseline_tag']}, "
              f"peak={anchor_selection['expressive_peak_tag']}")
        log(f"Anchors → baseline: {anchor_selection['calm_baseline_tag']}, "
            f"expressive peak: {anchor_selection['expressive_peak_tag']}")
    else:
        stage("anchors", "fail", "could not select anchors")

    # ── Section 4: combined result ────────────────────────────────────────────
    final_result = build_module1_result(sub1a_result, emotion_clips, anchor_selection)
    stage("result", "pass" if final_result["overall_result"] == "pass" else "fail",
          final_result["overall_result"].upper())
    emit({"type": "result", "result": final_result})
    return final_result
