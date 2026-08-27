"""Spark queue service settings (``SPARK_LLM_*`` env vars)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SparkQueueSettings(BaseSettings):
    """Environment-driven settings for the Spark SGLang queue proxy."""

    model_config = SettingsConfigDict(
        env_prefix="SPARK_LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Upstream Spark SGLang OpenAI-compatible base URL (includes /v1).
    base_url: str = "http://10.0.1.9:8888/v1"
    model: str = "qwen3.8-27b-sglang"
    max_concurrency: int = 4
    queue_timeout: float = 600.0
    occupancy_poll_interval: float = 0.25
    host: str = "0.0.0.0"
    port: int = 8088
    # Shared ranch token for /v1/*. Empty disables auth (tests).
    queue_token: str = ""

    @property
    def origin_url(self) -> str:
        """Spark HTTP origin without the ``/v1`` OpenAI prefix."""
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base[:-3]
        return base


@lru_cache
def get_spark_queue_settings() -> SparkQueueSettings:
    """Return a cached SparkQueueSettings instance."""
    return SparkQueueSettings()
