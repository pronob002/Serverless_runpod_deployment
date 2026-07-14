# 🎙️ Serverless RunPod Voice Cloning Worker

This branch is optimized for **RunPod Serverless** deployment. It contains only the core voice cloning logic from Module 2 (VoxCPM2) and the RunPod serverless handler, with all frontend assets and liveness verification code pruned to minimize build time and container size.

---

## 🛠️ Step 1: Supabase Setup

Before deploying, make sure your Supabase instance is ready:
1. **Database Table**: In the Supabase **SQL Editor**, run the SQL queries in `supabase_schema.sql` to initialize the `cloning_runs` database table.
2. **Storage Bucket**: Create a public storage bucket named `audiobooks` (or use your custom name). Make sure your bucket allows uploads/downloads via policies or API access.

---

## 🐳 Step 2: Build & Push the Docker Image

Build and push the container image to your container registry (e.g., Docker Hub):

```bash
# 1. Build the Docker image from the root of the repository
docker build -t your-dockerhub-username/voice-cloner-serverless:latest .

# 2. Push the built image to your registry
docker push your-dockerhub-username/voice-cloner-serverless:latest
```

> [!TIP]
> To compile the image on a GPU machine for CUDA support, ensure the host machine has the NVIDIA Container Toolkit installed so that Docker can use GPU during build if needed.

---

## 🚀 Step 3: Deploying on RunPod Serverless (Latest UI)

Follow these steps on the [RunPod Console](https://www.runpod.io/console):

### 1. Create a Serverless Template
1. In the left sidebar, click on **Templates**.
2. Click the **+ New Template** button in the top right.
3. Configure the following fields in the modal:
   * **Template Name**: `Voice-Cloner-Serverless`
   * **Container Image**: `your-dockerhub-username/voice-cloner-serverless:latest` (or your registry URI)
   * **Container Disk**: Set to `15 GB` (to accommodate VoxCPM model weights caching).
4. Expand **Environment Variables** and add the following keys from your `.env`:
   * `SUPABASE_URL` = *Your Supabase Project URL*
   * `SUPABASE_SERVICE_KEY` = *Your Supabase Service/Anon Key*
   * `SUPABASE_STORAGE_ACCESS_KEY` = *Your S3 Storage Access Key ID*
   * `SUPABASE_STORAGE_SECRET_KEY` = *Your S3 Storage Secret Access Key*
   * `SUPABASE_STORAGE_ENDPOINT_URL` = *Your S3 endpoint URL (e.g., `https://xxxx.supabase.co/storage/v1/s3`)*
   * `SUPABASE_STORAGE_BUCKET_NAME` = `audiobooks` (or your custom bucket name)
5. Click **Save Template**.

### 2. Create the Serverless Endpoint
1. In the left sidebar, click on **Serverless** → **Endpoints**.
2. Click the **+ New Endpoint** button.
3. Configure the endpoint settings:
   * **Endpoint Name**: `voice-cloning-endpoint`
   * **Select Template**: Choose `Voice-Cloner-Serverless` (created in the previous step).
   * **Active GPU Type**: Select **RTX 3090/4090** or **NVIDIA L4** (NVIDIA L4 is cost-effective and highly recommended for zero-shot cloning).
   * **Min Workers**: `0` (scales down to 0 to save costs when idle).
   * **Max Workers**: `3` (or higher depending on your concurrency needs).
   * **Idle Timeout**: `60` seconds.
4. Click **Create Endpoint**.

Once created, you will get an **Endpoint ID** (e.g., `https://api.runpod.ai/v2/YOUR-ENDPOINT-ID/run`).

---

## ⚡ Step 4: Invoking the Endpoint

To invoke the serverless worker, send a POST request to your RunPod endpoint.

### Request Body Format:
```json
{
  "input": {
    "session_id": "your-unique-session-id",
    "model": "voxcpm"
  }
}
```

* **`session_id`**: The folder name in your Supabase storage bucket where the user's enrolled normal clip is stored (expected at `{session_id}/normal_clean.wav`).
* **`model`**: The target voice cloning model to run (defaults to `voxcpm`).

### Response Format:
On success, the handler generates emotional clone clips, uploads them to your Supabase bucket, registers them in the database, and returns the signed URLs:
```json
{
  "status": "success",
  "session_id": "your-unique-session-id",
  "model": "voxcpm",
  "clip_mappings": {
    "normal_normal.wav": "https://...",
    "happy_happy.wav": "https://...",
    "sad_sad.wav": "https://...",
    "angry_angry.wav": "https://...",
    "loud_loud.wav": "https://..."
  }
}
```

---

## 💻 Step 5: Local Frontend Web Client (No GPU Required!)

You can run the web capture and voice comparison frontend locally on your laptop without needing a local GPU. The local FastAPI server handles the camera capture, uploads it to Supabase, and proxies the heavy voice cloning execution to your RunPod Serverless worker.

### 1. Install System Dependencies
Make sure you have **Python 3.10+** and **ffmpeg** installed on your local machine.

### 2. Install Python Packages
Navigate to the `frontend/` directory and install the requirements:
```bash
# Navigate to the frontend folder
cd frontend

# Create a virtual environment (optional but recommended)
python -m venv venv
# Activate it:
# - On Windows: venv\Scripts\activate
# - On macOS/Linux: source venv/bin/activate

# Install the dependencies
pip install -r requirements.txt
```

### 3. Set Up Local Environment Variables
1. Copy the template environment file:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in your Supabase credentials, RunPod Endpoint ID, and RunPod API Key.

### 4. Launch the Web App
Start the local FastAPI development server:
```bash
python -m uvicorn app:app --port 8000
```

### 5. Access the Web Interface
1. Open **`http://localhost:8000`** in your browser.
2. Complete **Module 1 (Identity & Voice Capture)**:
   * Perform the head-turn liveness check in front of your camera.
   * Read the 5 emotional prompts.
   * Click **Upload & Validate**.
3. Once the capture passes validation, the local server will **automatically invoke your RunPod Serverless GPU worker** to clone the voice.
4. Go to **`http://localhost:8000/module2`** to listen to and compare your original voice clips against the cloned variations side-by-side!

