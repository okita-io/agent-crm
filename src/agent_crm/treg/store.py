"""Persist treg catalog rows and the paid-tool allowlist."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from agent_crm.db import session_scope
from agent_crm.models import TregTool
from .catalog import CATALOG_SEARCHES, CatalogTool, collect_catalog_tools
from .client import TregClient, TregError, treg_configured


@dataclass(frozen=True)
class TregSyncResult:
    fetched: int
    upserted: int
    free: int
    paid_selectable: int
    auto_allowed_free: int
    errors: list[str]


def upsert_catalog_tools(tools: list[CatalogTool]) -> int:
    """Insert or update catalog rows. Preserves ``allowed`` / ``queued_at``."""
    now = datetime.now(UTC)
    upserted = 0
    with session_scope() as session:
        for tool in tools:
            row = session.get(TregTool, tool.endpoint_id)
            if row is None:
                row = TregTool(endpoint_id=tool.endpoint_id)
                session.add(row)
            row.title = tool.title
            row.summary = tool.summary
            row.provider = tool.provider
            row.capability = tool.capability
            row.platform = tool.platform
            row.method = tool.method
            row.path = tool.path
            row.kind = tool.kind
            row.queue_as = tool.queue_as
            row.estimated_cost_usd = tool.estimated_cost_usd
            row.cost_type = tool.cost_type
            row.cost_note = tool.cost_note
            row.is_free = tool.is_free
            row.is_routed = tool.is_routed
            row.selectable = tool.selectable
            row.input_schema = tool.input_schema
            row.last_synced_at = now
            if tool.is_free:
                row.allowed = True
            upserted += 1
    return upserted


def sync_treg_catalog(*, client: TregClient | None = None) -> TregSyncResult:
    """Search the live treg catalog and upsert CRM-relevant endpoints."""
    if not treg_configured() and client is None:
        raise TregError("TREG_API_TOKEN is not set")
    owns = client is None
    http = client or TregClient()
    errors: list[str] = []
    payloads: list[tuple[str, dict[str, Any]]] = []
    try:
        for hint, query in CATALOG_SEARCHES:
            try:
                payloads.append((hint, http.catalog_search(query, limit=50)))
            except TregError as exc:
                errors.append(f"{query}: {exc}")
    finally:
        if owns:
            http.close()
    tools = collect_catalog_tools(payloads)
    upserted = upsert_catalog_tools(tools)
    free = sum(1 for tool in tools if tool.is_free)
    paid_selectable = sum(1 for tool in tools if not tool.is_free and tool.selectable)
    auto_allowed = sum(1 for tool in tools if tool.is_free)
    return TregSyncResult(
        fetched=len(tools),
        upserted=upserted,
        free=free,
        paid_selectable=paid_selectable,
        auto_allowed_free=auto_allowed,
        errors=errors,
    )


def get_treg_tool(endpoint_id: str) -> TregTool | None:
    with session_scope() as session:
        return session.get(TregTool, endpoint_id)


def list_treg_tools(
    *,
    paid: bool | None = None,
    selectable: bool | None = None,
    allowed: bool | None = None,
    queue_as: str | None = None,
) -> list[TregTool]:
    with session_scope() as session:
        stmt = select(TregTool).order_by(
            TregTool.queue_as.asc(),
            TregTool.endpoint_id.asc(),
        )
        if paid is True:
            stmt = stmt.where(TregTool.is_free.is_(False))
        elif paid is False:
            stmt = stmt.where(TregTool.is_free.is_(True))
        if selectable is not None:
            stmt = stmt.where(TregTool.selectable.is_(selectable))
        if allowed is not None:
            stmt = stmt.where(TregTool.allowed.is_(allowed))
        if queue_as is not None:
            stmt = stmt.where(TregTool.queue_as == queue_as)
        return list(session.scalars(stmt))


def set_treg_tools_allowed(endpoint_ids: list[str], *, allowed: bool = True) -> list[str]:
    """Flip the allowlist. Returns ids that were updated."""
    updated: list[str] = []
    now = datetime.now(UTC)
    with session_scope() as session:
        for endpoint_id in endpoint_ids:
            row = session.get(TregTool, endpoint_id)
            if row is None:
                continue
            if row.is_free:
                row.allowed = True
                updated.append(endpoint_id)
                continue
            row.allowed = allowed
            if allowed:
                row.queued_at = now
            updated.append(endpoint_id)
    return updated


def treg_tool_allowed(endpoint_id: str) -> bool:
    row = get_treg_tool(endpoint_id)
    if row is None:
        return False
    return bool(row.is_free or row.allowed)


def treg_counts() -> dict[str, int]:
    tools = list_treg_tools()
    return {
        "total": len(tools),
        "free": sum(1 for row in tools if row.is_free),
        "paid": sum(1 for row in tools if not row.is_free),
        "paid_selectable": sum(1 for row in tools if not row.is_free and row.selectable),
        "allowed_paid": sum(
            1 for row in tools if not row.is_free and row.allowed
        ),
    }
