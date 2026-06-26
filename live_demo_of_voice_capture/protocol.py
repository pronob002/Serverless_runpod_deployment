"""
Shared capture protocol — single source of truth for timing, prompts, overlay drawing
and microphone recording.

Everything here is lifted verbatim (in behaviour) from `record_test_video.py` and Section 0
of `Module1_Identity_Voice_Capture_with_new.ipynb`, so the live demo, the CLI recorder, and the
notebook all agree on the exact same fixed-timing protocol. If you change a timing number, change
it in the notebook too — the analysis cuts the saved video using these same numbers.
"""

import queue
import threading

import cv2
import soundfile as sf


# ── FIXED TIMING — must match the notebook's Section 0 exactly ────────────────
LIVENESS_WINDOW_SEC = 8     # Step 1: look forward, turn left, turn right
EMOTION_WINDOW_SEC = 7      # Steps 2-6: one sentence each
EMOTION_ORDER = ["normal", "loud", "happy", "angry", "sad"]

# Computed boundaries — derived from the two constants above, exactly like notebook Cell 2.
SEGMENT_BOUNDARIES = {"liveness": (0, LIVENESS_WINDOW_SEC)}
_cursor = LIVENESS_WINDOW_SEC
for _tag in EMOTION_ORDER:
    SEGMENT_BOUNDARIES[_tag] = (_cursor, _cursor + EMOTION_WINDOW_SEC)
    _cursor += EMOTION_WINDOW_SEC
TOTAL_EXPECTED_DURATION_SEC = _cursor


# Unique sentences tailored to help the user express each specific emotion
EMOTION_SENTENCES = {
    "normal": "The weather today is absolutely wonderful, and I can't wait to go outside.",
    "loud":   "Listen carefully, we need to finalize the plan before the deadline tonight!",
    "happy":  "I am so incredibly excited because everything is finally working out perfectly!",
    "angry":  "This is completely unacceptable and I want this issue resolved immediately!",
    "sad":    "It's hard to believe that they are really gone, and things will never be the same.",
}

DEFAULT_SENTENCE = "The weather today is absolutely wonderful, and I can't wait to go outside."

# Display text per emotion (shown big, on screen, during that step)
EMOTION_PROMPTS = {
    "normal": "Read in a NORMAL tone",
    "loud":   "Read LOUDLY",
    "happy":  "Read sounding HAPPY",
    "angry":  "Read sounding ANGRY",
    "sad":    "Read sounding SAD",
}

# Colors (BGR, since OpenCV) — the brand palette, reused by the web UI too (see style.css).
COLOR_BG = (24, 21, 26)        # near-black
COLOR_ACCENT = (42, 71, 184)   # the brand vermilion, in BGR
COLOR_TEXT = (230, 239, 244)   # cream
COLOR_DIM = (140, 131, 138)    # muted grey


class AudioRecorder:
    """Background microphone recorder writing a 16kHz mono PCM WAV (copied from record_test_video.py)."""

    # Record at 44.1kHz (the near-universal native rate) rather than forcing 16kHz —
    # many ALSA/PortAudio inputs reject 16kHz outright. The analysis re-extracts audio
    # from the merged video at 16kHz via ffmpeg, so the capture rate doesn't matter.
    def __init__(self, filename, samplerate=44100, channels=1, device=None):
        self.filename = filename
        self.samplerate = samplerate
        self.channels = channels
        self.device = device
        self.queue = queue.Queue()
        self.recording = False
        self.thread = None
        self.error = None              # set if the input stream fails to open
        self.started = threading.Event()  # set once the stream opens OR errors

    def _record_loop(self):
        # Imported lazily: the deployable web app captures audio in the browser, so the
        # server doesn't need PortAudio/sounddevice unless this legacy recorder is used.
        import sounddevice as sd
        try:
            with sf.SoundFile(self.filename, mode='w', samplerate=self.samplerate,
                              channels=self.channels, subtype='PCM_16') as file:
                with sd.InputStream(samplerate=self.samplerate, channels=self.channels,
                                     device=self.device, callback=self._callback):
                    self.started.set()
                    while self.recording:
                        try:
                            data = self.queue.get(timeout=0.1)
                            file.write(data)
                        except queue.Empty:
                            continue
        except Exception as e:
            self.error = e
            self.started.set()

    def _callback(self, indata, frames, time, status):
        if status:
            print(f"Audio input status warning: {status}", flush=True)
        self.queue.put(indata.copy())

    def start(self):
        """Starts recording; returns True only once the input stream actually opened."""
        self.recording = True
        self.thread = threading.Thread(target=self._record_loop)
        self.thread.start()
        # Wait for the stream to open (or fail) so the caller knows before recording begins.
        self.started.wait(timeout=2.0)
        return self.error is None

    def stop(self):
        self.recording = False
        if self.thread:
            self.thread.join()


def wrap_text(text, font, font_scale, thickness, max_width):
    """Breaks `text` into multiple lines so each line fits within max_width pixels."""
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        (w, _), _ = cv2.getTextSize(candidate, font, font_scale, thickness)
        if w > max_width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def draw_overlay(frame, headline, subtext, seconds_left, total_seconds, step_idx, total_steps):
    """Draws the instruction headline, subtext, countdown bar, and step indicator onto the frame."""
    h, w = frame.shape[:2]

    font = cv2.FONT_HERSHEY_SIMPLEX
    sub_lines = wrap_text(subtext, font, 0.6, 1, w - 48)
    banner_height = 130 + max(0, len(sub_lines) - 1) * 26

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, banner_height), COLOR_BG, -1)
    frame = cv2.addWeighted(overlay, 0.75, frame, 0.25, 0)

    cv2.putText(frame, headline, (24, 45), font, 1.0, COLOR_TEXT, 2, cv2.LINE_AA)

    for i, line in enumerate(sub_lines):
        cv2.putText(frame, line, (24, 80 + i * 26), font, 0.6, COLOR_DIM, 1, cv2.LINE_AA)

    dot_y = banner_height - 20
    for i in range(total_steps):
        cx = 30 + i * 28
        if i < step_idx:
            cv2.circle(frame, (cx, dot_y), 6, COLOR_DIM, -1)
        elif i == step_idx:
            cv2.circle(frame, (cx, dot_y), 7, COLOR_ACCENT, -1)
        else:
            cv2.circle(frame, (cx, dot_y), 6, COLOR_DIM, 1)

    bar_x, bar_y, bar_w, bar_h = 24, banner_height - 30, w - 48, 6
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), COLOR_DIM, 1)
    progress = max(0.0, min(1.0, seconds_left / total_seconds)) if total_seconds else 0.0
    fill_w = int(bar_w * progress)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), COLOR_ACCENT, -1)

    cv2.putText(frame, f"{seconds_left:0.1f}s", (w - 110, h - 24),
                font, 1.0, COLOR_TEXT, 2, cv2.LINE_AA)

    return frame


def build_steps(sentence=None):
    """Builds the ordered list of (headline, subtext, duration) for all 6 steps."""
    steps = [
        ("STEP 1 / 6 - IDENTITY CHECK",
         "Look at the camera, then turn your head LEFT, then RIGHT, then back to center.",
         LIVENESS_WINDOW_SEC),
    ]
    for i, tag in enumerate(EMOTION_ORDER, start=2):
        text = sentence if sentence else EMOTION_SENTENCES[tag]
        steps.append((
            f"STEP {i} / 6 - {tag.upper()}",
            f'{EMOTION_PROMPTS[tag]}:  "{text}"',
            EMOTION_WINDOW_SEC
        ))
    return steps


PRE_ROLL_SEC = 3  # countdown before recording starts (not part of the recorded clip)


def web_protocol():
    """The capture protocol as JSON-serializable data for the browser overlay.

    The browser drives its own guided timeline + countdown from this, so the JS can never
    drift from the timing the server analyzes by. `id` matches the `record_<n>` step ids the
    front-end's step list already uses; `t=0` of the recorded clip is the start of step 0,
    so these durations line up with `SEGMENT_BOUNDARIES` (pre-roll is excluded from the clip).
    """
    steps = []
    for idx, (headline, subtext, duration) in enumerate(build_steps()):
        steps.append({
            "id": f"record_{idx}",
            "headline": headline,
            "subtext": subtext,
            "durationSec": duration,
        })
    return {
        "preRollSec": PRE_ROLL_SEC,
        "totalSec": TOTAL_EXPECTED_DURATION_SEC,
        "steps": steps,
    }
