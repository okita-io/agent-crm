"""Sanity checks for docker-compose self-organizing worker services."""

from __future__ import annotations

from pathlib import Path

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_compose_declares_standing_workers() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'command: ["agent-crm", "jobs"]' in content
    assert 'command: ["agent-crm", "hunt-loop"]' in content
    assert "research-loop" in content
    assert 'command: ["agent-crm", "orchestrate"]' in content


def test_compose_research_loop_is_bounded() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    start = content.index("  research-loop:")
    end = content.index("\n\n", start)
    block = content[start:end]
    assert 'CRM_RESEARCH_MAX_QUERIES_DEFAULT: "20"' in block
    assert 'CRM_RESEARCH_MAX_PAGES_PER_RUN: "200"' in block
    assert 'CRM_RESEARCH_MAX_MINUTES_DEFAULT: "60"' in block
    assert '"20"' in block and '"200"' in block and '"60"' in block
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

    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for service in ("contact-worker", "hunt-loop", "research-loop", "orchestrator"):
        marker = f"  {service}:"
        start = content.index(marker)
        end = content.index("\n\n", start)
        block = content[start:end]
        assert "restart: unless-stopped" in block, f"{service} missing restart policy"
