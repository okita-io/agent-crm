"""Sanity checks for docker-compose self-organizing worker services."""

from __future__ import annotations

from pathlib import Path

from agent_crm.agents.registry import compose_services

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_compose_declares_standing_workers() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'command: ["agent-crm", "jobs"]' in content
    assert "- hunt-loop\n      - --watch" in content
    assert "research-loop" in content
    assert "engagement-loop" in content
    assert "seo-loop" in content
    assert "aeo-geo-loop" in content
    assert "queue-review" in content
    assert 'command: ["agent-crm", "orchestrate"]' in content
    assert "  web:" in content
    assert "context: ./frontend" in content
    for service in compose_services():
        assert f"  {service}:" in content, f"registry compose_service missing: {service}"


def test_compose_research_loop_is_bounded() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  research-loop:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert "--watch" in block
    assert 'CRM_RESEARCH_MAX_QUERIES_DEFAULT: "20"' in block
    assert 'CRM_RESEARCH_MAX_PAGES_PER_RUN: "200"' in block
    assert 'CRM_RESEARCH_MAX_MINUTES_DEFAULT: "60"' in block
    assert '"20"' in block and '"200"' in block and '"60"' in block
    assert "TREG_API_TOKEN: ${TREG_API_TOKEN:-}" in block
    assert 'CRM_RESEARCH_MAX_QUERIES_DEFAULT: "0"' not in block
    assert 'CRM_RESEARCH_MAX_PAGES_PER_RUN: "0"' not in block
    assert 'CRM_RESEARCH_MAX_MINUTES_DEFAULT: "0"' not in block


def test_compose_hunt_loop_stays_unbounded() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  hunt-loop:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert 'CRM_HUNTER_MAX_QUERIES_DEFAULT: "0"' in block
    assert 'CRM_HUNTER_MAX_MINUTES_DEFAULT: "0"' in block
    assert "--watch" in block
    assert "TREG_API_TOKEN: ${TREG_API_TOKEN:-}" in block

    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for service in compose_services():
        marker = f"  {service}:"
        start = content.index(marker)
        end = content.index("\n\n", start)
        block = content[start:end]
        assert "restart: unless-stopped" in block, f"{service} missing restart policy"


def test_compose_publish_loop_is_dry_run_by_default() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  publish-loop:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert "--watch" in block
    assert "CRM_PUBLISH_DRY_RUN: ${CRM_PUBLISH_DRY_RUN:-true}" in block
    assert 'CRM_PUBLISH_MAX_JOBS_PER_CYCLE: "5"' in block
    assert "spark-queue:" not in block[block.index("depends_on:") :]


def test_compose_engagement_loop_is_bounded() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  engagement-loop:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert "--watch" in block
    assert 'CRM_ENGAGEMENT_MAX_VENUES_PER_RUN: "10"' in block
    assert 'CRM_ENGAGEMENT_MAX_PAGES_PER_VENUE: "15"' in block
    assert 'CRM_ENGAGEMENT_MAX_MINUTES_DEFAULT: "45"' in block
    assert 'CRM_ENGAGEMENT_MAX_VENUES_PER_RUN: "0"' not in block


def test_compose_seo_loop_runs_daily_at_noon() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  seo-loop:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert "--watch" in block
    assert '--max-targets\n      - "0"' in block or '- --max-targets\n      - "0"' in block
    assert 'CRM_SEO_MAX_TARGETS_PER_RUN: "0"' in block
    assert 'CRM_SEO_MAX_PAGES_PER_TARGET: "4"' in block
    assert 'CRM_SEO_MAX_MINUTES_DEFAULT: "0"' in block
    assert 'CRM_SEO_REVIEW_HOUR: "12"' in block
    assert 'CRM_SEO_REVIEW_TIMEZONE: "America/Los_Angeles"' in block
    assert "CRM_SEO_REVIEW_INTERVAL_HOURS" not in block


def test_compose_queue_review_watches_backlog() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  queue-review:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert "--watch" in block
    assert "spark-queue:" in block[block.index("depends_on:") :]
    assert 'CRM_QUEUE_REVIEW_MAX_QUERIES: "40"' in block


def test_contact_worker_and_orchestrator_do_not_require_spark_queue() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for service in ("contact-worker", "orchestrator"):
        start = content.index(f"  {service}:")
        end = content.index("\n\n", start)
        block = content[start:end]
        depends_start = block.index("depends_on:")
        depends_block = block[depends_start:]
        assert "spark-queue:" not in depends_block, (
            f"{service} should not depend on spark-queue to start verify jobs"
        )


def test_compose_dashboard_uses_ten_minute_observer_refresh() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  dashboard:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert 'CRM_OBSERVER_LIVE_REFRESH_SECONDS: "5"' in block
    assert 'CRM_OBSERVER_REFRESH_SECONDS: "600"' in block
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  spark-queue:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert "CRM_DATABASE_URL: postgresql+psycopg://crm:" in block
    assert "db:" in block[block.index("depends_on:") :]


def test_compose_binds_sensitive_ports_to_localhost() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert '"127.0.0.1:5432:5432"' in content
    assert '"127.0.0.1:8088:8088"' in content
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-crm}" in content
    assert "CRM_API_TOKEN: ${CRM_API_TOKEN:-changeme}" in content
    assert "CRM_DASHBOARD_PASSWORD: ${CRM_DASHBOARD_PASSWORD:-}" in content


def test_compose_publishes_dashboards_on_all_interfaces() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert '"0.0.0.0:8000:8000"' in content
    assert '"0.0.0.0:8501:8501"' in content
    assert '"0.0.0.0:3000:80"' in content


def test_workers_wait_for_api_migrations() -> None:
    """Standing workers must start after api so Alembic finishes before init_db."""
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for service in compose_services():
        start = content.index(f"  {service}:")
        end = content.index("\n\n", start)
        block = content[start:end]
        depends_start = block.index("depends_on:")
        depends_block = block[depends_start:]
        assert "api:" in depends_block, f"{service} should depend on api migrations"


def test_compose_spark_depends_match_registry() -> None:
    from agent_crm.agents.registry import AGENT_SPECS

    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for spec in AGENT_SPECS:
        if spec.compose_service is None:
            continue
        start = content.index(f"  {spec.compose_service}:")
        end = content.index("\n\n", start)
        block = content[start:end]
        depends_block = block[block.index("depends_on:") :]
        if spec.spark_required:
            assert "spark-queue:" in depends_block, (
                f"{spec.compose_service} is spark_required but does not depend on spark-queue"
            )
        else:
            assert "spark-queue:" not in depends_block, (
                f"{spec.compose_service} should not depend on spark-queue"
            )

