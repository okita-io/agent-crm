#!/usr/bin/env bash
# Container entrypoint. First arg selects the role:
#   api        -> run migrations, then the FastAPI service
#   dashboard  -> run the Streamlit dashboard
#   migrate    -> run Alembic migrations and exit
#   <other>    -> exec the args verbatim (escape hatch)
set -euo pipefail

run_migrations() {
  echo "[entrypoint] Running database migrations..."
  # Wait briefly for Postgres if we're pointed at it.
  if [[ "${CRM_DATABASE_URL:-}" == postgresql* ]]; then
    echo "[entrypoint] Waiting for Postgres..."
    for _ in $(seq 1 30); do
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
        break
      fi
      sleep 1
    done
  fi
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
    echo "[entrypoint] Starting dashboard on :${CRM_DASHBOARD_PORT:-8501}"
    exec streamlit run src/agent_crm/dashboard.py \
      --server.address 0.0.0.0 \
      --server.port "${CRM_DASHBOARD_PORT:-8501}" \
      --server.headless true
    ;;
  migrate)
    run_migrations
    ;;
  *)
    exec "$@"
    ;;
esac
