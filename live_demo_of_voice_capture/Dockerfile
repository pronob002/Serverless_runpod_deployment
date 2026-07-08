FROM python:3.11-slim

# System deps:
#  - ffmpeg: normalizes uploaded clips (session.py) before analysis
#  - libgl1/libglib2.0-0/libsm6/libxext6/libxrender1: runtime libs opencv-python/mediapipe need
#  - libsndfile1: soundfile backend used by librosa/noisereduce
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# App lives at /app/live_demo_of_voice_capture so pipeline.py's
# dirname(dirname(__file__)) (the face_landmarker.task download location) resolves to /app.
WORKDIR /app/live_demo_of_voice_capture

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p recordings output

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
