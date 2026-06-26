"""
CaptureSession — one live recording attempt, end-to-end.

Mirrors `record_test_video.record()` but is **non-blocking** and **event-emitting** instead of
driving an OpenCV window: it runs on a background thread, exposes the current annotated frame for
the MJPEG stream, and pushes step/log/result events onto a thread-safe queue the SSE endpoint drains.

Flow:  pre-roll → 6 guided recording steps (writing a clean .mp4 + mic .wav) → FFmpeg merge →
        pipeline.run_analysis() → final result.  All progress is emitted as events.
"""

import os
import time
import queue
import threading
import subprocess

import cv2

import protocol
import pipeline

PRE_ROLL_SEC = 3
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")


class CaptureSession:
    def __init__(self, camera_index=0, sentence=None, output_dir="output"):
        self.camera_index = camera_index
        self.sentence = sentence
        self.output_dir = output_dir

        self.steps = protocol.build_steps(sentence)
        self.total_steps = len(self.steps)

        self.events = queue.Queue()
        self._cancel = threading.Event()
        self._thread = None
        self.active = False

        # Latest annotated frame (JPEG bytes) for the MJPEG stream
        self._frame_lock = threading.Lock()
        self._latest_jpeg = None

    # ── public API ────────────────────────────────────────────────────────────
    def start(self):
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def latest_jpeg(self):
        with self._frame_lock:
            return self._latest_jpeg

    # ── internals ─────────────────────────────────────────────────────────────
    def _emit(self, event):
        self.events.put(event)

    def _log(self, text):
        self._emit({"type": "log", "text": text})

    def _set_frame(self, frame, headline, subtext, seconds_left, total_seconds, step_idx):
        display = protocol.draw_overlay(frame.copy(), headline, subtext, seconds_left,
                                        total_seconds, step_idx, self.total_steps)
        ok, buf = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._frame_lock:
                self._latest_jpeg = buf.tobytes()

    def _finish(self, status="done", message=""):
        self.active = False
        self._emit({"type": "done", "status": status, "message": message})

    def _run(self):
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(RECORDINGS_DIR, f"session_{ts}.mp4")
        temp_audio_path = output_path.replace(".mp4", "_temp.wav")
        temp_video_path = output_path.replace(".mp4", "_temp.mp4")

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            self._emit({"type": "stage", "stage": "camera", "status": "fail",
                        "detail": f"camera index {self.camera_index} unavailable"})
            self._log(f"Could not open camera index {self.camera_index}.")
            self._finish("error", "camera unavailable")
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(temp_video_path, fourcc, fps, (width, height))

        # Warm-up: webcams return near-black frames for the first ~1s while
        # auto-exposure settles. Discard those so the first visible frame is lit.
        for _ in range(15):
            cap.read()

        self._log(f"Camera opened ({width}x{height} @ {fps:.0f}fps). "
                  f"Protocol: {self.total_steps} steps, "
                  f"{protocol.TOTAL_EXPECTED_DURATION_SEC:.0f}s total.")

        # ── Pre-roll ──────────────────────────────────────────────────────────
        self._emit({"type": "stage", "stage": "preroll", "status": "running", "detail": "Get ready…"})
        pre_start = time.time()
        while time.time() - pre_start < PRE_ROLL_SEC and not self._cancel.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            remaining = PRE_ROLL_SEC - (time.time() - pre_start)
            self._set_frame(frame, "GET READY", "Recording starts in...", remaining,
                            PRE_ROLL_SEC, -1)
        self._emit({"type": "stage", "stage": "preroll", "status": "pass", "detail": "Recording!"})

        # ── Microphone (non-fatal: if it fails we continue video-only) ─────────
        # Optional overrides for tricky audio setups:
        #   AUDIO_DEVICE=8   pick a specific input (e.g. onboard mic, away from a USB webcam)
        #   AUDIO_SR=48000   capture sample rate (default 44100)
        recorder = None
        self._emit({"type": "stage", "stage": "microphone", "status": "running",
                    "detail": "Opening mic…"})
        dev_env = os.environ.get("AUDIO_DEVICE")
        audio_device = int(dev_env) if dev_env not in (None, "") else None
        audio_sr = int(os.environ.get("AUDIO_SR", "44100"))
        try:
            recorder = protocol.AudioRecorder(temp_audio_path, samplerate=audio_sr,
                                              device=audio_device)
            if recorder.start() and recorder.error is None:
                self._log(f"Microphone recording started "
                          f"(device={audio_device if audio_device is not None else 'default'}, "
                          f"{audio_sr} Hz).")
                self._emit({"type": "stage", "stage": "microphone", "status": "pass",
                            "detail": f"{audio_sr} Hz"})
            else:
                self._log(f"Microphone unavailable ({recorder.error}); recording video only. "
                          f"Tip: set AUDIO_DEVICE / AUDIO_SR to pick a working input.")
                self._emit({"type": "stage", "stage": "microphone", "status": "fail",
                            "detail": "video-only"})
                recorder.stop()
                recorder = None
        except Exception as e:
            self._log(f"Could not start microphone ({e}); recording video only.")
            self._emit({"type": "stage", "stage": "microphone", "status": "fail",
                        "detail": "video-only"})
            recorder = None

        # ── Guided recording steps ────────────────────────────────────────────
        success = True
        for step_idx, (headline, subtext, duration) in enumerate(self.steps):
            if self._cancel.is_set():
                success = False
                break
            self._emit({"type": "stage", "stage": f"record_{step_idx}", "status": "running",
                        "detail": headline})
            step_start = time.time()
            while True:
                if self._cancel.is_set():
                    success = False
                    break
                elapsed = time.time() - step_start
                remaining = duration - elapsed
                if remaining <= 0:
                    break
                ret, frame = cap.read()
                if not ret:
                    self._log("Camera read failed mid-recording.")
                    success = False
                    break
                writer.write(frame)  # clean frame, no overlay burned in
                self._set_frame(frame, headline, subtext, remaining, duration, step_idx)
            self._emit({"type": "stage", "stage": f"record_{step_idx}",
                        "status": "pass" if success else "fail", "detail": ""})
            if not success:
                break

        writer.release()
        cap.release()
        if recorder:
            recorder.stop()
            self._log("Microphone recording stopped.")

        if not success:
            for p in (temp_video_path, temp_audio_path):
                if os.path.exists(p):
                    os.remove(p)
            self._log("Recording cancelled — partial files discarded.")
            self._finish("cancelled", "recording cancelled")
            return

        # ── Merge audio + video ───────────────────────────────────────────────
        self._emit({"type": "stage", "stage": "merge", "status": "running",
                    "detail": "Merging audio + video…"})
        merged = False
        if recorder and os.path.exists(temp_audio_path):
            cmd = ["ffmpeg", "-y", "-i", temp_video_path, "-i", temp_audio_path,
                   "-c:v", "copy", "-c:a", "aac", output_path]
            res = subprocess.run(cmd, capture_output=True)
            merged = res.returncode == 0
            if not merged:
                self._log("FFmpeg merge failed; saving raw video only.")
        if not merged:
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(temp_video_path, output_path)
            if os.path.exists(temp_audio_path):  # stray/empty wav from a failed mic
                os.remove(temp_audio_path)
        else:
            if os.path.exists(temp_video_path):
                os.remove(temp_video_path)
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
        self._emit({"type": "stage", "stage": "merge", "status": "pass",
                    "detail": os.path.basename(output_path)})
        self._log(f"Saved recording: {output_path}")

        # ── Analysis pipeline ─────────────────────────────────────────────────
        try:
            pipeline.run_analysis(output_path, self._emit, output_dir=self.output_dir)
        except Exception as e:
            self._log(f"Analysis error: {e}")
            self._emit({"type": "stage", "stage": "result", "status": "fail", "detail": str(e)})
            self._finish("error", str(e))
            return

        self._finish("done", "complete")


class AnalysisSession:
    """
    Runs the analysis pipeline on an already-recorded video: a clip captured live in the browser
    (getUserMedia + MediaRecorder) or a pre-recorded file the user uploaded. Exposes the same
    interface the SSE endpoint expects (`events`, `active`, `latest_jpeg`, `cancel`).

    Browser MediaRecorder output (WebM on Chrome/Firefox, MP4 on iOS) often has unreliable/variable
    fps and duration metadata, which breaks the pipeline's frame-index seeking and `-ss/-to` audio
    cutting. So the upload is first normalized to a constant-frame-rate MP4 with rewritten timestamps,
    then analyzed.
    """

    def __init__(self, video_path, output_dir="output"):
        self.video_path = video_path
        self.output_dir = output_dir
        self.events = queue.Queue()
        self.active = False
        self._thread = None
        self._preview_jpeg = None  # first frame of the clip, for the preview pane

    def start(self):
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        # Analysis is short and non-interactive; nothing to interrupt.
        pass

    def latest_jpeg(self):
        return self._preview_jpeg

    def _emit(self, event):
        self.events.put(event)

    def _normalize(self, src_path):
        """Re-encode to a clean constant-frame-rate MP4 so frame-index/timestamp cutting is reliable.
        Returns the normalized path, or the original if ffmpeg is unavailable/fails."""
        norm_path = os.path.splitext(src_path)[0] + "_norm.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart",
            norm_path,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            self._emit({"type": "log", "text": "ffmpeg not found — analyzing the raw upload as-is."})
            return src_path
        if res.returncode != 0 or not os.path.exists(norm_path):
            self._emit({"type": "log",
                        "text": "Normalization failed — analyzing the raw upload as-is."})
            return src_path
        return norm_path

    def _run(self):
        self._emit({"type": "log",
                    "text": f"Received clip: {os.path.basename(self.video_path)}. Normalizing…"})
        video_path = self._normalize(self.video_path)

        # Show the clip's first frame in the preview pane while analysing.
        try:
            cap = cv2.VideoCapture(video_path)
            ret, frame = cap.read()
            cap.release()
            if ret:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    self._preview_jpeg = buf.tobytes()
        except Exception:
            pass

        try:
            pipeline.run_analysis(video_path, self._emit, output_dir=self.output_dir)
        except Exception as e:
            self._emit({"type": "log", "text": f"Analysis error: {e}"})
            self._emit({"type": "stage", "stage": "result", "status": "fail", "detail": str(e)})
            self.active = False
            self._emit({"type": "done", "status": "error", "message": str(e)})
            return

        self.active = False
        self._emit({"type": "done", "status": "done", "message": "complete"})
