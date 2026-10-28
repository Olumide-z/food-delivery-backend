from celery import Celery
from app.core.config import settings

# docker-compose restart celery_worker

celery_app = Celery(
    'food_delivery',
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.email_tasks",
        "app.tasks.auth_tasks"
    ]
)


celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,        # adds a STARTED state
    result_expires=3600,            # keep results for 1 hour
    task_acks_late=True,            # ack after the task finishes (safer)
    worker_prefetch_multiplier=1,   # fair dispatch for long tasks
)
