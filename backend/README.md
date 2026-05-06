<h1>🎧 Distributed AI Audiobook Generator</h1>

<p>A production-ready microservices architecture that converts PDFs into high-quality, emotionally directed audiobooks using AI voice cloning.</p>

<p>To overcome local hardware limitations, this project splits the workload: a lightweight local FastAPI server handles user requests and file storage, while a remote Google Colab T4 GPU instance acts as a dedicated background worker to process the heavy Machine Learning pipeline.</p>

<hr>

## 🏗️ Architecture Visualization

This system strictly separates the **Data Path** (heavy media files) from the **Control Path** (lightweight task queues). 

*   **Supabase (The Warehouse):** Stores the massive PDFs and MP3s.
*   **Upstash Redis (The Message Broker):** Stores tiny JSON "tickets" telling the GPU worker what to do.

![Audiobook System Architecture](architecture.png)

<hr>

<h2>📂 Project Structure</h2>

<pre><code>audiobook-backend/
├── __pycache__/
├── venv/
├── .env                        # Secret API keys and URLs
├── .gitignore                  # Keeps secrets out of version control
├── audiobook_backend.ipynb     # The Jupyter Notebook to run on Google Colab
├── celery_app.py               # Redis connection and Celery rules
├── main.py                     # Local FastAPI server endpoints
├── README.md                   # You are here
└── requirements.txt            # Local Python dependencies</code></pre>

<hr>

<h2>🛠️ Prerequisites</h2>

<p>To run this architecture, you need free accounts for the following services:</p>
<ol>
    <li><strong><a href="https://supabase.com/" target="_blank">Supabase</a>:</strong> For cloud file storage (Bucket name: <code>audiobooks</code>).</li>
    <li><strong><a href="https://upstash.com/" target="_blank">Upstash</a>:</strong> For serverless Redis (Message Broker).</li>
    <li><strong><a href="https://groq.com/" target="_blank">Groq</a>:</strong> For lightning-fast LLM inference (Llama 3.3).</li>
    <li><strong><a href="https://colab.research.google.com/" target="_blank">Google Colab</a>:</strong> For the free T4 GPU worker.</li>
</ol>

<hr>

<h2>🚀 Step 1: Environment Setup</h2>

<p>Create a <code>.env</code> file in the root of your local project and add your credentials:</p>

<pre><code class="language-env"># Upstash Redis (Ensure it starts with rediss:// for SSL)
CELERY_BROKER_URL="rediss://default:YOUR_PASSWORD@YOUR_ENDPOINT:PORT"

# Groq
GROQ_API_KEY="gsk_your_groq_api_key"

# Supabase Storage
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_KEY="your-anon-public-key"
SUPABASE_BUCKET="audiobooks"</code></pre>

<hr>

<h2>🗄️ Step 2: Supabase Storage Setup</h2>

<ol>
    <li>Create a public bucket in Supabase named <code>audiobooks</code>.</li>
    <li>Go to the <strong>SQL Editor</strong> in Supabase and run the following commands to allow your FastAPI server and Colab worker to read and write files without complex authentication:</li>
</ol>

<pre><code class="language-sql">-- Allow Public Downloads
CREATE POLICY "Allow Public Downloads" ON storage.objects FOR SELECT USING ( bucket_id = 'audiobooks' );

-- Allow Public Uploads
CREATE POLICY "Allow Public Uploads" ON storage.objects FOR INSERT WITH CHECK ( bucket_id = 'audiobooks' );</code></pre>

<hr>

<h2>💻 Step 3: Start the Local Dispatcher</h2>

<p>Install the dependencies and start the FastAPI server on your local machine.</p>

<pre><code class="language-bash">python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
pip install -r requirements.txt

uvicorn main:app --reload</code></pre>

<p><em>Your local server is now running at <code>http://localhost:8000/docs</code>. It is ready to receive files and dispatch tickets.</em></p>

<hr>

<h2>🧠 Step 4: Start the GPU Worker (Google Colab)</h2>

<ol>
    <li>Upload <code>audiobook_backend.ipynb</code> and your <code>.env</code> file to a Google Colab instance.</li>
    <li>Change the Runtime to <strong>T4 GPU</strong>.</li>
    <li>Run the setup cells to install dependencies and warm up the ML models.</li>
    <li>Run the final cell to launch the Celery worker.</li>
</ol>

<blockquote>
    <p><strong>⚠️ Critical Worker Command:</strong></p>
    <p>The launch command utilizes strict multiprocessing limits (<code>--pool=prefork --concurrency=1 --max-tasks-per-child=1</code>) to prevent PyTorch from causing <code>CUDA Out of Memory</code> crashes after multiple generations. It forces a totally clean VRAM slate for every audiobook.</p>
</blockquote>

<hr>

<h2>🎧 Usage Instructions</h2>

<ol>
    <li>Open <strong><code>http://localhost:8000/docs</code></strong> (Swagger UI) in your browser.</li>
    <li>Use the <strong><code>POST /generate</code></strong> endpoint to upload a PDF and a short (~10 second) MP3 voice sample.</li>
    <li>Copy the <code>task_id</code> returned in the response.</li>
    <li>Watch the logs in your Google Colab tab to see the GPU worker processing your files in real-time.</li>
    <li>Use the <strong><code>GET /status/{task_id}</code></strong> endpoint with progressive backoff (wait 10-15 seconds between checks) to retrieve your final public Audiobook URL!</li>
</ol>