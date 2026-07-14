# 🎙️ Serverless RunPod Voice Cloning Worker

This branch is optimized for **RunPod Serverless** deployment. It contains only the core voice cloning logic from Module 2 (VoxCPM2) and the RunPod serverless handler, with all frontend assets and liveness verification code pruned to minimize build time and container size.

---

## 🛠️ Step 1: Supabase Setup

Before deploying, make sure your Supabase instance is ready:
1. **Database Table**: In the Supabase **SQL Editor**, run the SQL queries in `supabase_schema.sql` to initialize the `cloning_runs` database table.
2. **Storage Bucket**: Create a public storage bucket named `audiobooks` (or use your custom name). Make sure your bucket allows uploads/downloads via policies or API access.

## 🚀 Step 2: Deploying on RunPod Serverless (Via GitHub Integration)

Instead of building and pushing Docker images manually, you can configure RunPod to build your container directly from your GitHub repository:

### 1. Grant RunPod Access to Your GitHub Repository
1. In the **RunPod Console**, go to **Serverless** → **Endpoints** and click **+ New Endpoint**.
2. Under **Select a source**, select **GitHub**.
3. If your repository (`Serverless_runpod_deployment`) is not in the list, click the **Edit connection** button in the top right.
4. On the GitHub authorization page, configure the RunPod app to have access to your repository `pronob002/Serverless_runpod_deployment` (or choose *All repositories*), click **Save**, and refresh the RunPod page.

### 2. Configure the Endpoint Builder
1. Select the **`pronob002/Serverless_runpod_deployment`** repository from the list.
2. Select your branch: **`main`** (or `runpod-serverless`).
3. Set **Dockerfile path** to: `/Dockerfile` (it should display a green `Dockerfile found` checkmark).
4. Click **Next** in the bottom right. *(If a warning pops up saying `Could not find runpod.serverless.start() in your repo`, click **Continue anyway**. This is a UI warning and the endpoint functions perfectly since `handler.py` runs the listener).*

### 3. Configure Resources & Environment Variables
1. **GPU Selection**: Select **RTX 3090/4090** or **NVIDIA L4** (NVIDIA L4 is cost-effective and recommended).
2. **Workers**: Set **Min Workers** to `0` (saves cost by scaling down to zero when idle) and **Max Workers** to `3` (or your preferred scale limit).
3. **Environment Variables**: Click **Environment Variables** (or **Manage** → **Edit Configuration** on an existing endpoint) and add the following keys and values:
   * `SUPABASE_URL` = `https://eeewcayqxhmgucyprptm.supabase.co`
   * `SUPABASE_SERVICE_KEY` = *Your Supabase Service Key*
   * `SUPABASE_STORAGE_ACCESS_KEY` = `bd45c06f30a1ed85fdd8ab7c027b2505`
   * `SUPABASE_STORAGE_SECRET_KEY` = `607502687d773289dcfba0b0cb91fb3f6ede103b59a9fc1223bec3d3fc021e32`
   * `SUPABASE_STORAGE_ENDPOINT_URL` = `https://eeewcayqxhmgucyprptm.storage.supabase.co/storage/v1/s3`
   * `SUPABASE_STORAGE_BUCKET_NAME` = `audiobooks`
4. Click **Deploy** (or **Update Endpoint**).

RunPod will pull your repository, build the image using the root `Dockerfile` automatically in the cloud, and deploy the worker! You will see the build status change to `Completed` under the **Builds** tab, and your endpoint status will change to `Ready` (Green).

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

