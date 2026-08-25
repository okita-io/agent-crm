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

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
