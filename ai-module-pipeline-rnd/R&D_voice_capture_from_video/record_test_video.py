"""
Module 1 — Guided Test Recorder
=================================
Records ONE video matching the exact fixed-timing protocol used by
Module1_Identity_Voice_Capture.ipynb:

    Step 1: LIVENESS   (look forward -> turn LEFT -> turn RIGHT -> center)   8s
    Step 2: NORMAL      (read the sentence in a normal tone)                 7s
    Step 3: LOUD        (read the same sentence loudly)                     7s
    Step 4: HAPPY       (read it sounding happy)                            7s
    Step 5: ANGRY       (read it sounding angry)                            7s
    Step 6: SAD         (read it sounding sad)                              7s
                                                                  Total: 43s

It shows the current instruction and a live countdown directly on the camera
preview, automatically advances through all 6 steps, and saves one .mp4 file
when done — ready to drop straight into the notebook's test_videos/ folder.

Usage:
    python record_test_video.py
    python record_test_video.py --output test_videos/enrollment_session.mp4
    python record_test_video.py --camera 1          # use a different camera index
    python record_test_video.py --sentence "Your own sentence here"

Requirements:
    sudo apt-get update && sudo apt-get install -y libportaudio2
    pip install opencv-python
"""

import cv2
import time
import argparse
import os   
import queue
import threading
import subprocess
import sounddevice as sd
import soundfile as sf

class AudioRecorder:
    def __init__(self, filename, samplerate=16000, channels=1):
        self.filename = filename
        self.samplerate = samplerate
        self.channels = channels
        self.queue = queue.Queue()
        self.recording = False
        self.thread = None

    def _record_loop(self):
        try:
            with sf.SoundFile(self.filename, mode='w', samplerate=self.samplerate,
                              channels=self.channels, subtype='PCM_16') as file:
                with sd.InputStream(samplerate=self.samplerate, channels=self.channels,
                                     callback=self._callback):
                    while self.recording:
                        try:
                            # non-blocking wait with a short timeout
                            data = self.queue.get(timeout=0.1)
                            file.write(data)
                        except queue.Empty:
                            continue
        except Exception as e:
            print(f"⚠️  Audio writer thread error: {e}")

    def _callback(self, indata, frames, time, status):
        if status:
            print(f"⚠️  Audio input status warning: {status}", flush=True)
        self.queue.put(indata.copy())

    def start(self):
        self.recording = True
        self.thread = threading.Thread(target=self._record_loop)
        self.thread.start()

    def stop(self):
        self.recording = False
        if self.thread:
            self.thread.join()

# ── THESE MUST MATCH THE NOTEBOOK'S SECTION 0 EXACTLY ──────────────────────
# If you change timing in the notebook, change it here too — they must agree,
# since the notebook cuts the video using these same numbers.
LIVENESS_WINDOW_SEC = 8
EMOTION_WINDOW_SEC = 7
EMOTION_ORDER = ["normal", "loud", "happy", "angry", "sad"]


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

# Colors (BGR, since OpenCV)
COLOR_BG = (24, 21, 26)        # near-black
COLOR_ACCENT = (42, 71, 184)   # the brand vermilion, in BGR
COLOR_TEXT = (230, 239, 244)   # cream
COLOR_DIM = (140, 131, 138)    # muted grey


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

    # Wrap the subtext to fit the frame width, and grow the banner height if it needs 2 lines
    font = cv2.FONT_HERSHEY_SIMPLEX
    sub_lines = wrap_text(subtext, font, 0.6, 1, w - 48)
    banner_height = 130 + max(0, len(sub_lines) - 1) * 26

    # Semi-transparent banner at the top for text legibility over any background
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, banner_height), COLOR_BG, -1)
    frame = cv2.addWeighted(overlay, 0.75, frame, 0.25, 0)

    # Headline (which step / emotion)
    cv2.putText(frame, headline, (24, 45), font, 1.0, COLOR_TEXT, 2, cv2.LINE_AA)

    # Subtext (instruction or sentence to read), one or more lines
    for i, line in enumerate(sub_lines):
        cv2.putText(frame, line, (24, 80 + i * 26), font, 0.6, COLOR_DIM, 1, cv2.LINE_AA)

    # Step indicator dots (1..total_steps), filled = done, outline = upcoming, accent = current
    dot_y = banner_height - 20
    for i in range(total_steps):
        cx = 30 + i * 28
        if i < step_idx:
            cv2.circle(frame, (cx, dot_y), 6, COLOR_DIM, -1)
        elif i == step_idx:
            cv2.circle(frame, (cx, dot_y), 7, COLOR_ACCENT, -1)
        else:
            cv2.circle(frame, (cx, dot_y), 6, COLOR_DIM, 1)

    # Countdown bar, draining left to right
    bar_x, bar_y, bar_w, bar_h = 24, banner_height - 30, w - 48, 6
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), COLOR_DIM, 1)
    progress = max(0.0, min(1.0, seconds_left / total_seconds))
    fill_w = int(bar_w * progress)
    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), COLOR_ACCENT, -1)

    # Big countdown number, bottom-right
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


def record(output_path, camera_index=1, sentence=None, countdown_before_start=3):
    steps = build_steps(sentence)
    total_steps = len(steps)
    total_duration = sum(d for _, _, d in steps)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Temporary filenames for audio/video splitting
    temp_audio_path = output_path.replace(".mp4", "_temp.wav")
    temp_video_path = output_path.replace(".mp4", "_temp.mp4")

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"❌ Could not open camera index {camera_index}.")
        print("   Try a different --camera index (0, 1, 2...), or check camera permissions.")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    
    # Calculate delay in milliseconds matching the frame rate
    delay_ms = max(1, int(1000 / fps))

    # Write video to the TEMPORARY video path
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(temp_video_path, fourcc, fps, (width, height))

    print(f"Recording protocol: {total_steps} steps, {total_duration}s total.")
    print(f"Output will be saved to: {output_path}")
    print(f"Press 'q' at any time to cancel.\n")

    # Create a large window and force it to the foreground/top-most layer
    cv2.namedWindow("Module 1 — Guided Recorder", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Module 1 — Guided Recorder", 1280, 960)
    cv2.setWindowProperty("Module 1 — Guided Recorder", cv2.WND_PROP_TOPMOST, 1.0)

    # ── Pre-roll countdown so you have time to get in position ─────────────
    pre_start = time.time()
    print("=" * 60)
    print("🚀 GET READY: Starting in 3 seconds...")
    print("=" * 60)
    
    last_printed_sec = -1
    while time.time() - pre_start < countdown_before_start:
        ret, frame = cap.read()
        if not ret:
            break
        remaining = countdown_before_start - (time.time() - pre_start)
        
        # Print countdown seconds to console
        sec_to_print = int(remaining) + 1
        if sec_to_print != last_printed_sec:
            print(f"Starting in {sec_to_print}...")
            last_printed_sec = sec_to_print
            
        frame = draw_overlay(frame, "GET READY", "Recording starts in...", remaining,
                              countdown_before_start, -1, total_steps)
        cv2.imshow("Module 1 — Guided Recorder", frame)
        if cv2.waitKey(delay_ms) & 0xFF == ord('q'):
            cap.release()
            cv2.destroyAllWindows()
            print("Cancelled.")
            # Cleanup temp video if written
            if os.path.exists(temp_video_path):
                os.remove(temp_video_path)
            return False

    # Start audio recording in background
    recorder = None
    try:
        recorder = AudioRecorder(temp_audio_path)
        recorder.start()
        print("🎙️  Microphone recording started...")
    except Exception as e:
        print(f"⚠️  Could not start microphone recording: {e}")
        print("   Recording will proceed with video only.")
        recorder = None

    # ── Main recording loop — steps through the protocol automatically ─────
    session_start = time.time()
    success = True
    for step_idx, (headline, subtext, duration) in enumerate(steps):
        step_start = time.time()
        print("\n" + "=" * 60)
        print(f"🎬 {headline}")
        print(f"👉 {subtext}")
        print(f"⏱️  Duration: {duration}s")
        print("=" * 60)
        
        while True:
            elapsed = time.time() - step_start
            remaining = duration - elapsed
            if remaining <= 0:
                break

            ret, frame = cap.read()
            if not ret:
                print("⚠️  Camera read failed mid-recording.")
                success = False
                break

            writer.write(frame)  # save the CLEAN frame (no overlay burned into the file)

            display_frame = draw_overlay(frame.copy(), headline, subtext, remaining,
                                          duration, step_idx, total_steps)
            cv2.imshow("Module 1 — Guided Recorder", display_frame)

            if cv2.waitKey(delay_ms) & 0xFF == ord('q'):
                print("Cancelled mid-recording — partial files discarded.")
                success = False
                break
        if not success:
            break

    total_elapsed = time.time() - session_start
    writer.release()
    cap.release()
    cv2.destroyAllWindows()

    # Stop the audio recorder
    if recorder:
        recorder.stop()
        print("🎙️  Microphone recording stopped.")

    if success:
        # Merge audio and video using FFmpeg
        if recorder and os.path.exists(temp_audio_path):
            print("🔄 Merging audio and video tracks...")
            cmd = [
                "ffmpeg", "-y",
                "-i", temp_video_path,
                "-i", temp_audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                output_path
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0:
                print(f"\n✅ Recording complete: {output_path}")
                print(f"   Actual recording time: {total_elapsed:.1f}s (expected ~{total_duration}s)")
                print(f"   Point the notebook's TEST_VIDEO_PATH at this file and run it.")
                # Clean up temporary files
                if os.path.exists(temp_video_path):
                    os.remove(temp_video_path)
                if os.path.exists(temp_audio_path):
                    os.remove(temp_audio_path)
                return True
            else:
                print("⚠️  FFmpeg merge failed. Saving raw video only.")
                print(res.stderr.decode()[-800:])
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(temp_video_path, output_path)
                return False
        else:
            # Fallback if audio recorder failed to initialize
            print("\n⚠️  No audio captured. Saving raw video only.")
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(temp_video_path, output_path)
            print(f"✅ Recording complete (video-only): {output_path}")
            return True
    else:
        # Clean up temporary files on failure or cancellation
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Guided recorder for Module 1 test videos.")
    parser.add_argument("--output", default="test_videos/enrollment_session.mp4",
                         help="Where to save the recorded video.")
    parser.add_argument("--camera", type=int, default=0,
                         help="Camera index to use (default 0).")
    parser.add_argument("--sentence", default=None,
                         help="Override all emotional steps with this single sentence.")
    args = parser.parse_args()

    record(args.output, camera_index=args.camera, sentence=args.sentence)
