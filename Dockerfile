# Single image that can run either the API or the dashboard.
# Which one it runs is decided by docker-compose command overrides.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# Install the package with the dashboard extra so one image serves both roles.
RUN pip install --upgrade pip && pip install ".[dashboard]"

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Data dir for the SQLite fallback (mounted as a volume in compose).
RUN mkdir -p /app/data

EXPOSE 8000 8088 8501

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]
