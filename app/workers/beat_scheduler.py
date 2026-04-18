from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()


def setup_beat_schedule(app: Celery) -> None:
    app.conf.beat_schedule = {
        # Run drift detection + synthesis for all users every N hours
        "drift-all-users": {
            "task": "app.workers.tasks.run_drift_all_users",
            "schedule": crontab(minute=0, hour=f"*/{settings.drift_interval_hours}"),
            "options": {"queue": "drift"},
        },
        # Lightweight health ping every 5 minutes
        "health-ping": {
            "task": "app.workers.tasks.health_ping",
            "schedule": 300.0,  # seconds
        },
    }
