import os
import uuid
from fastapi import FastAPI, UploadFile, File, HTTPException
from supabase import create_client, Client
from celery.result import AsyncResult
from celery_app import celery_app
from dotenv import load_dotenv

load_dotenv()

# Initialize Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
bucket_name = os.getenv("SUPABASE_BUCKET", "audiobooks")

app = FastAPI(title="Audiobook MVP Backend")

@app.post("/generate")
async def generate_audiobook(
    pdf: UploadFile = File(...), 
    mp3: UploadFile = File(...)
):
    try:
        task_id = str(uuid.uuid4())

        # Define Storage paths
        pdf_path = f"inputs/{task_id}/{pdf.filename}"
        mp3_path = f"inputs/{task_id}/{mp3.filename}"

        # Read files into memory
        pdf_bytes = await pdf.read()
        mp3_bytes = await mp3.read()

        # Upload to Supabase
        supabase.storage.from_(bucket_name).upload(
            path=pdf_path, 
            file=pdf_bytes, 
            file_options={"content-type": "application/pdf"}
        )
        supabase.storage.from_(bucket_name).upload(
            path=mp3_path, 
            file=mp3_bytes, 
            file_options={"content-type": "audio/mpeg"}
        )

        # Push task payload to Celery
        celery_app.send_task(
            "worker.process_audiobook",
            args=[task_id, pdf_path, mp3_path],
            task_id=task_id
        )

        return {"task_id": task_id, "status": "PENDING"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    return {
        "task_id": task_id,
        "status": res.state,
        "result": res.result if res.state == 'SUCCESS' else None,
        "error": str(res.info) if res.state == 'FAILURE' else None
    }