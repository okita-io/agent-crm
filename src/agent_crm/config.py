"""Runtime configuration, loaded from environment / .env.

Every component (API, dashboard, agent tooling, migrations) reads the same
settings object so they agree on which store to talk to.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings. Prefix every var with ``CRM_``."""

    model_config = SettingsConfigDict(
        env_prefix="CRM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Database
    database_url: str = "sqlite:///./data/agent_crm.db"
    sql_echo: bool = False

    # Project YAML catalog (prompt origins + channel switches). Empty = repo projects/.
    projects_dir: str = ""

    # API service
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Shared ranch token. Empty disables auth (tests/local). Compose must set one.
    api_token: str = ""

    # Dashboard
    dashboard_port: int = 8501
    dashboard_password: str = ""
    # Browser origins for the Vite dashboard (comma-separated). Empty uses localhost defaults.
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    # LLM (Spark queue proxy — never point agents at Spark directly)
    llm_base_url: str = "http://spark-queue:8088/v1"
    llm_queue_token: str = ""
    # Cloud-equivalent rates used to estimate spend avoided by the local GPU.
    llm_input_usd_per_million: float = 2.0
    llm_output_usd_per_million: float = 10.0

    # Local ranch search + scrape (SearXNG + Firecrawl on the host)
    searxng_url: str = "http://host.docker.internal:8080"
    firecrawl_url: str = "http://host.docker.internal:3002"

    # treg.to catalog (people/link lookups). Also reads TREG_API_TOKEN / TREG_TOKEN.
    treg_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("CRM_TREG_API_TOKEN", "TREG_API_TOKEN", "TREG_TOKEN"),
    )
    treg_base_url: str = "https://treg.to"
    treg_org: str = "okita-2"

    # Outbound Hunter defaults
    hunter_max_pages_per_run: int = 50
    hunter_search_result_limit: int = 50
    hunter_max_queries_default: int = 0
    hunter_max_minutes_default: int = 0
    hunter_max_branch_terms: int = 5
    hunter_community_terms_per_run: int = 30
    hunter_person_terms_per_run: int = 20
    hunter_handle_terms_per_run: int = 20
    hunter_engagement_terms_per_run: int = 20
    hunter_request_timeout: float = 60.0

    # Agent engagement (comment-draft arm; publish via publisher after schedule)
    engagement_max_venues_per_run: int = 10
    engagement_max_pages_per_venue: int = 15
    engagement_max_minutes_default: int = 45
    engagement_scan_interval_hours: int = 24
    engagement_popularity_threshold: int = 40
    engagement_draft_threshold: int = 55
    engagement_max_branch_terms: int = 8

    # Publisher (human-scheduled outbound; dry-run by default)
    publish_dry_run: bool = True
    publish_poll_seconds: int = 30
    publish_max_jobs_per_cycle: int = 5
    publish_reddit_daily_cap: int = 3
    publish_reddit_min_interval_minutes: int = 240
    publish_allow_tactic_studio: bool = False
    postiz_base_url: str = ""
    postiz_api_key: str = ""
    # Default Reddit script app (override per account via CRM_SOCIAL_{KEY}_*)
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""
    reddit_password: str = ""
    reddit_user_agent: str = "agent-crm/publisher"

    # SEO documents (reviews + plans for humans; never applied to live sites)
    seo_max_targets_per_run: int = 8
    seo_max_pages_per_target: int = 4
    seo_max_minutes_default: int = 45
    seo_review_hour: int = 12
    seo_review_timezone: str = "America/Los_Angeles"
    seo_search_result_limit: int = 15

    # Research agent defaults
    research_max_queries_default: int = 20
    research_max_pages_per_run: int = 200
    research_max_minutes_default: int = 60
    research_search_result_limit: int = 50
    research_max_branch_terms: int = 8

    # Observer / presence
    api_base_url: str = "http://api:8000"
    # Spark slots + heartbeat/status/task on Live Agents.
    observer_live_refresh_seconds: int = 5
    # Token totals / hunt-loop snapshot. 10 minutes avoids extra Postgres load.
    observer_refresh_seconds: int = 600

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
    job_dispatcher_idle_verify_limit: int = 50

    # Orchestrator self-learning loop
    orchestrator_poll_seconds: int = 180

    # Queue-review agent (keep/toss queued search terms; auto-on when queues grow)
    queue_review_poll_seconds: int = 20
    queue_review_max_queries: int = 40
    queue_review_spark_per_cycle: int = 8

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def resolved_projects_dir(self) -> Path:
        """Directory of ``{slug}.yaml`` project origins."""
        if self.projects_dir.strip():
            return Path(self.projects_dir).expanduser().resolve()
        docker = Path("/app/projects")
        if docker.is_dir():
            return docker
        repo_root = Path(__file__).resolve().parents[2]
        return repo_root / "projects"

    @property
    def resolved_treg_api_token(self) -> str:
        """CRM_TREG_API_TOKEN, else a bare TREG_API_TOKEN / TREG_TOKEN in the environment."""
        import os

        return (
            self.treg_api_token
            or os.environ.get("TREG_API_TOKEN")
            or os.environ.get("TREG_TOKEN")
            or ""
        ).strip()


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
