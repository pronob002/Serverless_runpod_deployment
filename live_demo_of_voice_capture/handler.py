import sys
import traceback
print(">>> BOOTING RUNPOD WORKER <<<", flush=True)

try:
    import os
    import json
    import shutil
    import boto3
    from supabase import create_client, Client
    import runpod
except Exception as e:
    print(f"CRITICAL INIT ERROR: {e}", flush=True)
    traceback.print_exc(file=sys.stdout)
    sys.exit(1)

# Initialize environment variables from .env if present (useful for local development/testing)
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SUPABASE_STORAGE_ACCESS_KEY = os.environ.get("SUPABASE_STORAGE_ACCESS_KEY")
SUPABASE_STORAGE_SECRET_KEY = os.environ.get("SUPABASE_STORAGE_SECRET_KEY")
SUPABASE_STORAGE_ENDPOINT_URL = os.environ.get("SUPABASE_STORAGE_ENDPOINT_URL")
SUPABASE_STORAGE_BUCKET_NAME = os.environ.get("SUPABASE_STORAGE_BUCKET_NAME", "audiobooks")

# Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Initialize S3 Client for Supabase Storage
s3_client = boto3.client(
    "s3",
    endpoint_url=SUPABASE_STORAGE_ENDPOINT_URL,
    aws_access_key_id=SUPABASE_STORAGE_ACCESS_KEY,
    aws_secret_access_key=SUPABASE_STORAGE_SECRET_KEY,
    config=boto3.session.Config(signature_version="s3v4")
)

def handler(job):
    """
    RunPod Serverless handler for voice cloning.
    """
    job_input = job.get("input", {})
    session_id = job_input.get("session_id")
    model = job_input.get("model", "voxcpm2")

    if not session_id:
        return {"status": "error", "error": "Missing session_id in job input"}

    session_dir = f"/tmp/output/{session_id}"
    os.makedirs(session_dir, exist_ok=True)

    try:
        # 1. Download normal_clean.wav from Supabase Storage
        print(f"Downloading normal_clean.wav for session_id '{session_id}'...")
        local_ref_path = os.path.join(session_dir, "normal_clean.wav")
        s3_key = f"{session_id}/normal_clean.wav"
        
        s3_client.download_file(
            Bucket=SUPABASE_STORAGE_BUCKET_NAME,
            Key=s3_key,
            Filename=local_ref_path
        )
        print("Download complete.")

        # 2. Execute module2.runner.run_model() using session_dir
        print(f"Executing voice cloning using model '{model}'...")
        from module2.runner import run_model
        
        manifest = run_model(session_dir=session_dir, model_name=model)
        print("Voice cloning generation complete.")

        # 3. Scan generated clips and upload to Supabase Storage, generating signed URLs
        out_dir = os.path.join(session_dir, "module2", model)
        clip_mappings = {}

        if os.path.exists(out_dir):
            for filename in os.listdir(out_dir):
                if filename.endswith(".wav"):
                    local_filepath = os.path.join(out_dir, filename)
                    s3_dest_key = f"{session_id}/module2/{model}/{filename}"

                    print(f"Uploading clip '{filename}' to Supabase Storage...")
                    s3_client.upload_file(
                        Filename=local_filepath,
                        Bucket=SUPABASE_STORAGE_BUCKET_NAME,
                        Key=s3_dest_key
                    )

                    # Generate a signed URL for 7 days
                    signed_url = s3_client.generate_presigned_url(
                        ClientMethod="get_object",
                        Params={
                            "Bucket": SUPABASE_STORAGE_BUCKET_NAME,
                            "Key": s3_dest_key
                        },
                        ExpiresIn=604800  # 7 days
                    )
                    clip_mappings[filename] = signed_url
            
            # Upload the generated manifest.json as well for metadata archival
            manifest_local_path = os.path.join(out_dir, "manifest.json")
            if os.path.exists(manifest_local_path):
                s3_manifest_key = f"{session_id}/module2/{model}/manifest.json"
                print("Uploading manifest.json to Supabase Storage...")
                s3_client.upload_file(
                    Filename=manifest_local_path,
                    Bucket=SUPABASE_STORAGE_BUCKET_NAME,
                    Key=s3_manifest_key
                )

        # 4. Insert database record into 'cloning_runs'
        print("Inserting record into 'cloning_runs' table...")
        db_record = {
            "session_id": session_id,
            "model_name": model,
            "device": manifest.get("device"),
            "sample_rate": manifest.get("sample_rate"),
            "adapter_params": manifest.get("adapter_params", {}),
            "clip_mappings": clip_mappings
        }
        
        supabase.table("cloning_runs").insert(db_record).execute()
        print("Database insertion complete.")

        return {
            "status": "success",
            "session_id": session_id,
            "model": model,
            "clip_mappings": clip_mappings
        }

    except Exception as e:
        print(f"Exception occurred in handler: {e}")
        return {"status": "error", "error": str(e)}

    finally:
        # Clean up the local session directory to prevent disk/memory leaks
        if os.path.exists(session_dir):
            print(f"Cleaning up temporary directory '{session_dir}'...")
            shutil.rmtree(session_dir)

# Start the serverless listener
runpod.serverless.start({"handler": handler})
