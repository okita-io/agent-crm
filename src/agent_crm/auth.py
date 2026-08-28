"""Shared ranch API authentication helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from .config import get_settings

KNOWN_AGENT_ROSTER: frozenset[str] = frozenset(
    {
        "lead_intake",
        "outbound_hunter",
        "engagement",
        "research",
        "job-dispatcher",
        "orchestrator",
        "contact-qualifier",
        "lead_verifier",
        "crm_manager",
        "hermes",
        "dashboard",
    }
)


def require_api_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_crm_token: str | None = Header(default=None, alias="X-CRM-Token"),
) -> None:
    """Require Bearer / X-CRM-Token when ``CRM_API_TOKEN`` is configured.

    Empty token disables auth so existing TestClient suites keep working.
    ``GET /health`` is always open.
    """
    if request.url.path.rstrip("/") == "/health":
        return
    expected = get_settings().api_token.strip()
    if not expected:
        return
    provided = (x_crm_token or "").strip()
    if not provided and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = value.strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing CRM API token",
        )


def require_known_agent(agent_name: str) -> None:
    """Reject heartbeat spoofing for unknown actor names when auth is enabled."""
    if not get_settings().api_token.strip():
        return
    if agent_name not in KNOWN_AGENT_ROSTER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown agent_name: {agent_name}",
        )
