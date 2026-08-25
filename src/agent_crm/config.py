"""Runtime configuration, loaded from environment / .env.

Every component (API, dashboard, agent tooling, migrations) reads the same
settings object so they agree on which store to talk to.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings. Prefix every var with ``CRM_``."""

    model_config = SettingsConfigDict(
        env_prefix="CRM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite:///./data/agent_crm.db"
    sql_echo: bool = False

    # API service
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Dashboard
    dashboard_port: int = 8501

    # Behavior
    hot_lead_threshold: int = 80

    # Outbound Hunter integrations
    searxng_base_url: str = "http://localhost:8080"
    firecrawl_base_url: str = "http://localhost:3002"
    llm_base_url: str = "http://localhost:8001/v1"
    llm_model: str = "qwen3.8-27b-sglang"
    hunter_max_pages_per_run: int = 8
    hunter_max_queries_default: int = 20
    hunter_max_minutes_default: int = 25
    hunter_max_branch_terms: int = 5
    hunter_enable_llm: bool = True
    hunter_request_timeout: float = 60.0

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
