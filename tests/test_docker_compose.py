"""Sanity checks for docker-compose self-organizing worker services."""

from __future__ import annotations

from pathlib import Path

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_compose_declares_standing_workers() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    assert 'command: ["agent-crm", "jobs"]' in content
    assert 'command: ["agent-crm", "hunt-loop"]' in content
    assert 'command: ["agent-crm", "research-loop"]' in content


def test_compose_workers_restart_unless_stopped() -> None:
    content = COMPOSE_PATH.read_text(encoding="utf-8")
    for service in ("contact-worker", "hunt-loop", "research-loop"):
        marker = f"  {service}:"
        start = content.index(marker)
        end = content.index("\n\n", start)
        block = content[start:end]
        assert "restart: unless-stopped" in block, f"{service} missing restart policy"
