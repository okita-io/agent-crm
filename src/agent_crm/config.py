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

    # LLM (Spark queue proxy — never point agents at Spark directly)
    llm_base_url: str = "http://spark-queue:8088/v1"

    # Local ranch search + scrape (SearXNG + Firecrawl on the host)
    searxng_url: str = "http://host.docker.internal:8080"
    firecrawl_url: str = "http://host.docker.internal:3002"

    # Outbound Hunter defaults
    hunter_max_pages_per_run: int = 50
    hunter_search_result_limit: int = 50
    hunter_max_queries_default: int = 0
    hunter_max_minutes_default: int = 0
    hunter_max_branch_terms: int = 5
    hunter_community_terms_per_run: int = 30
    hunter_person_terms_per_run: int = 20
    hunter_handle_terms_per_run: int = 20
    hunter_request_timeout: float = 60.0

    # Research agent defaults
    research_max_queries_default: int = 20
    research_max_pages_per_run: int = 200
    research_max_minutes_default: int = 60
    research_search_result_limit: int = 50

    # Observer / presence
    api_base_url: str = "http://api:8000"
    observer_refresh_seconds: int = 3

    # Behavior
    hot_lead_threshold: int = 80

    # Contact extraction / social lookup
    contact_social_queries_per_profile: int = 4
    contact_social_lookups_per_run: int = 40
    contact_spark_decode_per_run: int = 20
    contact_spark_decode_per_page: int = 5

    # Contact people-enrichment (SERP + public pages + Spark)
    contact_enrichment_queries_per_profile: int = 6
    contact_enrichments_per_run: int = 20
    contact_enrichment_spark_per_run: int = 10
    comment_people_per_page: int = 40

    # Contact worker drain loops (job dispatcher)
    job_dispatcher_poll_seconds: int = 45
    job_dispatcher_batch_size: int = 20

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
