from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from celery import Celery

from app.config import get_settings

settings = get_settings()


def _normalize_redis_url(url: str) -> str:
    """
    Celery's redis backend requires `ssl_cert_reqs` in the query string when
    using TLS (`rediss://`). Managed Redis providers (Upstash, etc.) don't
    include it — append a safe default so the worker can start.
    """
    if not url.startswith("rediss://"):
        return url
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "ssl_cert_reqs" not in params:
        params["ssl_cert_reqs"] = ["CERT_NONE"]
        new_query = urlencode(params, doseq=True)
        parsed = parsed._replace(query=new_query)
    return urlunparse(parsed)


broker_url = _normalize_redis_url(settings.redis_url)
backend_url = _normalize_redis_url(settings.redis_url)

celery_app = Celery(
    "minddrift",
    broker=broker_url,
    backend=backend_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.workers.tasks.ingest_fragment": {"queue": "ingest"},
        "app.workers.tasks.run_drift_for_user": {"queue": "drift"},
        "app.workers.tasks.run_drift_for_drift": {"queue": "drift"},
        "app.workers.tasks.run_drift_all_users": {"queue": "drift"},
        "app.workers.tasks.update_user_memory_from_synthesis": {"queue": "drift"},
        "app.workers.tasks.generate_lens_task": {"queue": "lenses"},
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.workers"])

# Import beat schedule
from app.workers.beat_scheduler import setup_beat_schedule  # noqa: E402

setup_beat_schedule(celery_app)
