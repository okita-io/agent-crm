#!/usr/bin/env bash
# Container entrypoint. First arg selects the role:
#   api        -> run migrations, then the FastAPI service
#   dashboard  -> run the Streamlit dashboard
#   migrate    -> run Alembic migrations and exit
#   <other>    -> exec the args verbatim (escape hatch)
set -euo pipefail

wait_for_postgres() {
  if [[ "${CRM_DATABASE_URL:-}" != postgresql* ]]; then
    return 0
  fi
  echo "[entrypoint] Waiting for Postgres..."
  local attempt=1
  while true; do
    if python -c "
import sys
from sqlalchemy import create_engine, text
from agent_crm.config import get_settings
try:
    e = create_engine(get_settings().database_url)
    with e.connect() as c:
        c.execute(text('SELECT 1'))
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      echo "[entrypoint] Postgres is ready."
      return 0
    fi
    if (( attempt >= 120 )); then
      echo "[entrypoint] Postgres not ready after ${attempt} attempts; exiting." >&2
      return 1
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
}

run_migrations() {
  echo "[entrypoint] Running database migrations..."
  wait_for_postgres
  alembic upgrade head
}

role="${1:-api}"
case "$role" in
  api)
    run_migrations
    echo "[entrypoint] Starting API on ${CRM_API_HOST:-0.0.0.0}:${CRM_API_PORT:-8000}"
    exec uvicorn agent_crm.api:app \
      --host "${CRM_API_HOST:-0.0.0.0}" \
      --port "${CRM_API_PORT:-8000}"
    ;;
  dashboard)
    wait_for_postgres
    echo "[entrypoint] Starting dashboard on :${CRM_DASHBOARD_PORT:-8501}"
    exec streamlit run src/agent_crm/dashboard.py \
      --server.address 0.0.0.0 \
      --server.port "${CRM_DASHBOARD_PORT:-8501}" \
      --server.headless true
    ;;
  spark-queue)
    echo "[entrypoint] Starting Spark queue on ${SPARK_LLM_HOST:-0.0.0.0}:${SPARK_LLM_PORT:-8088}"
    exec uvicorn agent_crm.spark_queue.app:app \
      --host "${SPARK_LLM_HOST:-0.0.0.0}" \
      --port "${SPARK_LLM_PORT:-8088}"
    ;;
  migrate)
    run_migrations
    ;;
  *)
    wait_for_postgres
    exec "$@"
    ;;
esac
