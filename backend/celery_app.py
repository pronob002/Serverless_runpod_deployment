import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("CELERY_BROKER_URL")

celery_app = Celery(
    "audiobook_tasks",
    broker=redis_url,
    backend=redis_url
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_use_ssl={"ssl_cert_reqs": "none"},
    redis_backend_use_ssl={"ssl_cert_reqs": "none"},
    worker_prefetch_multiplier=1, 
)