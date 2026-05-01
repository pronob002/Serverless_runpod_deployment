import os
import uuid
from fastapi import FastAPI, UploadFile, File, HTTPException
import firebase_admin
from firebase_admin import credentials, storage
from celery.result import AsyncResult
from celery_app import celery_app
from dotenv import load_dotenv

load_dotenv()

cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred, {
    'storageBucket': os.getenv("FIREBASE_BUCKET_NAME")
})

app = FastAPI(title="Audiobook MVP Backend")

@app.post("/generate")
async def generate_audiobook(
    pdf: UploadFile = File(...), 
    mp3: UploadFile = File(...)
):
    try:
        task_id = str(uuid.uuid4())
        bucket = storage.bucket()
        
        pdf_blob_path = f"inputs/{task_id}/{pdf.filename}"
        mp3_blob_path = f"inputs/{task_id}/{mp3.filename}"

        pdf_blob = bucket.blob(pdf_blob_path)
        pdf_blob.upload_from_file(pdf.file, content_type="application/pdf")

        mp3_blob = bucket.blob(mp3_blob_path)
        mp3_blob.upload_from_file(mp3.file, content_type="audio/mpeg")
        celery_app.send_task(
            "worker.process_audiobook",
            args=[task_id, pdf_blob_path, mp3_blob_path],
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