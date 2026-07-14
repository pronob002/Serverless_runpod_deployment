FROM pytorch/pytorch:2.2.1-cuda12.1-cudnn8-runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --default-timeout=1000 --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Command to execute the RunPod serverless handler
CMD ["python", "-u", "handler.py"]
