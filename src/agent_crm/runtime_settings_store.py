"""Dashboard runtime settings merged over environment defaults."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .db import session_scope
from .models import AgencySetting
from .spark_queue.config import get_spark_queue_settings

logger = logging.getLogger(__name__)

RUNTIME_SETTING_KEYS: tuple[str, ...] = (
    "spark_upstream_base_url",
    "spark_model",
    "spark_max_concurrency",
    "searxng_url",
    "firecrawl_url",
    "observer_live_refresh_seconds",
    "observer_refresh_seconds",
    "llm_input_usd_per_million",
    "llm_output_usd_per_million",
)

_SETTING_LABELS: dict[str, str] = {
    "spark_upstream_base_url": "Spark SGLang base URL",
    "spark_model": "Spark model id",
    "spark_max_concurrency": "Spark global session cap",
    "searxng_url": "SearXNG base URL",
    "firecrawl_url": "Firecrawl base URL",
    "observer_live_refresh_seconds": "Live Agents refresh (seconds)",
    "observer_refresh_seconds": "Token totals cache (seconds)",
    "llm_input_usd_per_million": "Est. cloud input $/M tokens",
    "llm_output_usd_per_million": "Est. cloud output $/M tokens",
}


def _env_defaults() -> dict[str, Any]:
    crm = get_settings()
    spark = get_spark_queue_settings()
    return {
        "spark_upstream_base_url": spark.base_url,
        "spark_model": spark.model,
        "spark_max_concurrency": spark.max_concurrency,
        "searxng_url": crm.searxng_url,
        "firecrawl_url": crm.firecrawl_url,
        "observer_live_refresh_seconds": crm.observer_live_refresh_seconds,
        "observer_refresh_seconds": crm.observer_refresh_seconds,
        "llm_input_usd_per_million": crm.llm_input_usd_per_million,
        "llm_output_usd_per_million": crm.llm_output_usd_per_million,
    }


def _load_overrides() -> dict[str, Any]:
    try:
        with session_scope() as session:
            rows = list(session.scalars(select(AgencySetting)))
        return {row.key: row.value for row in rows}
    except OperationalError:
        return {}


def get_runtime_settings() -> dict[str, Any]:
    """Effective values: env defaults merged with dashboard overrides."""
    merged = _env_defaults()
    merged.update(_load_overrides())
    return merged


def get_runtime_setting(key: str, default: Any = None) -> Any:
    overrides = _load_overrides()
    if key in overrides:
        return overrides[key]
    env = _env_defaults()
    if key in env:
        return env[key]
    return default


def list_runtime_settings_meta() -> list[dict[str, Any]]:
    defaults = _env_defaults()
    overrides = _load_overrides()
    rows: list[dict[str, Any]] = []
    for key in RUNTIME_SETTING_KEYS:
        rows.append(
            {
                "key": key,
                "label": _SETTING_LABELS.get(key, key),
                "value": overrides.get(key, defaults[key]),
                "default": defaults[key],
                "overridden": key in overrides,
            }
        )
    return rows


def _normalize_base_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("URL is required")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url!r}")
    return cleaned.rstrip("/")


def _validate_updates(updates: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, raw in updates.items():
        if key not in RUNTIME_SETTING_KEYS:
            continue
        if key.endswith("_url") or key == "spark_upstream_base_url":
            validated[key] = _normalize_base_url(str(raw))
        elif key == "spark_model":
            model = str(raw).strip()
            if not model:
                raise ValueError("Spark model id is required")
            validated[key] = model
        elif key == "spark_max_concurrency":
            value = int(raw)
            if value < 1 or value > 32:
                raise ValueError("Spark session cap must be between 1 and 32")
            validated[key] = value
        elif key.endswith("_seconds"):
            value = int(raw)
            if value < 2:
                raise ValueError(f"{key} must be at least 2")
            validated[key] = value
        elif key.endswith("_usd_per_million"):
            value = float(raw)
            if value < 0:
                raise ValueError(f"{key} must be non-negative")
            validated[key] = value
        else:
            validated[key] = raw
    return validated


def update_runtime_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist dashboard overrides and return the merged effective settings."""
    validated = _validate_updates(updates)
    now = datetime.now(UTC)
    try:
        with session_scope() as session:
            for key, value in validated.items():
                row = session.get(AgencySetting, key)
                if row is None:
                    row = AgencySetting(key=key, value=value, updated_at=now)
                    session.add(row)
                else:
                    row.value = value
                    row.updated_at = now
    except OperationalError as exc:
        raise RuntimeError(
            "agency_settings table is missing — run alembic upgrade head"
        ) from exc
    return get_runtime_settings()


def clear_runtime_setting(key: str) -> dict[str, Any]:
    if key not in RUNTIME_SETTING_KEYS:
        raise ValueError(f"Unknown setting {key!r}")
    with session_scope() as session:
        row = session.get(AgencySetting, key)
        if row is not None:
            session.delete(row)
    return get_runtime_settings()


def spark_origin_url() -> str:
    """Spark HTTP origin without the ``/v1`` OpenAI prefix."""
    base = str(get_runtime_setting("spark_upstream_base_url")).rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def probe_spark_upstream(url: str | None = None, *, timeout: float = 8.0) -> dict[str, Any]:
    """Test reachability of the Spark SGLang OpenAI API."""
    try:
        target = _normalize_base_url(url or str(get_runtime_setting("spark_upstream_base_url")))
    except ValueError as exc:
        return {
            "ok": False,
            "url": str(url or ""),
            "status_code": None,
            "models": [],
            "detail": str(exc),
        }
    models_url = f"{target}/models"
    try:
        response = httpx.get(models_url, timeout=timeout, follow_redirects=True)
        ok = response.status_code == 200
        detail = response.text[:500] if not ok else "ok"
        models: list[str] = []
        if ok:
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                models = [
                    str(item.get("id"))
                    for item in data
                    if isinstance(item, dict) and item.get("id")
                ]
        return {
            "ok": ok,
            "url": target,
            "status_code": response.status_code,
            "models": models,
            "detail": detail,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "url": target,
            "status_code": None,
            "models": [],
            "detail": str(exc),
        }


def docker_spark_host_hint() -> str | None:
    """Host from the Spark URL for docker-compose extra_hosts notes."""
    parsed = urlparse(str(get_runtime_setting("spark_upstream_base_url")))
    host = parsed.hostname
    if not host or host in {"spark", "localhost", "127.0.0.1"}:
        return None
    return host
