#!/usr/bin/env bash
set -euo pipefail

CMD="${1:-api}"

case "$CMD" in
  api)
    echo "🚀 Starting FastAPI server..."
    uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    ;;
  worker)
    echo "⚙️  Starting Celery worker..."
    uv run celery -A app.workers.celery_app worker --loglevel=info --concurrency=4
    ;;
  beat)
    echo "🕐 Starting Celery Beat scheduler..."
    uv run celery -A app.workers.celery_app beat --loglevel=info
    ;;
  infra)
    echo "🐳 Starting Redis + Qdrant..."
    docker compose up -d
    ;;
  migrate)
    echo "📦 Running Alembic migrations..."
    uv run alembic upgrade head
    ;;
  all)
    echo "🐳 Starting infra..."
    docker compose up -d
    sleep 2
    echo "🚀 Starting API + Worker + Beat..."
    trap 'kill 0' EXIT
    uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
    uv run celery -A app.workers.celery_app worker --loglevel=info --concurrency=2 &
    uv run celery -A app.workers.celery_app beat --loglevel=info &
    wait
    ;;
  *)
    echo "Usage: ./run.sh {api|worker|beat|infra|migrate|all}"
    exit 1
    ;;
esac
