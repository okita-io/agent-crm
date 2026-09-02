"""Interpret operator commands and dispatch work to standing agents."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .request_store import (
    claim_next_pending_agency_request,
    count_pending_agency_requests,
    list_agency_requests,
    mark_agency_request_completed,
    mark_agency_request_failed,
)
from agent_crm.agent_control import (
    ENQUEUE_ACTION_AGENTS,
    allowed_enqueue_actions,
    enabled_work_agents,
    is_agent_enabled,
    is_focused_roster,
    list_agent_enabled,
    set_agent_enabled,
)
from agent_crm.agents.registry import KNOWN_AGENT_ROSTER, toggleable_agents
from agent_crm.config import get_settings
from agent_crm.engagement.query_store import EngagementQueryStore
from agent_crm.enums import AgentStatus, Brand, ResearchFindingKind, SeoQueryKind
from agent_crm.heartbeat import record_heartbeat
from agent_crm.hunt.store import HuntStore
from agent_crm.llm_client import chat_completions
from agent_crm.llm_text import extract_json_object
from agent_crm.models import AgencyRequest
from agent_crm.research.query_store import ResearchQueryStore
from agent_crm.seo.query_store import SeoQueryStore

logger = logging.getLogger(__name__)

ACTOR = "orchestrator"
ORIGIN = "explicit"

TOGGLEABLE_AGENTS: tuple[str, ...] = toggleable_agents()

_ACTION_TYPES = frozenset({"set_agent_enabled", *ENQUEUE_ACTION_AGENTS})

_AGENT_ALIASES: dict[str, str] = {
    "outbound hunter": "outbound_hunter",
    "outbound_hunter": "outbound_hunter",
    "hunter": "outbound_hunter",
    "outbound": "outbound_hunter",
    "research": "research",
    "engagement": "engagement",
    "seo": "seo",
    "aeo geo": "aeo-geo",
    "aeo/geo": "aeo-geo",
    "aeo-geo": "aeo-geo",
    "aeo": "aeo-geo",
    "geo": "aeo-geo",
    "queue review": "queue-review",
    "queue-review": "queue-review",
    "queue": "queue-review",
    "job dispatcher": "job-dispatcher",
    "job-dispatcher": "job-dispatcher",
    "dispatcher": "job-dispatcher",
    "orchestrator": "orchestrator",
}

_DISABLE_PHRASES = ("pause", "stop", "disable", "turn off", "switch off", "shut off")
_ENABLE_PHRASES = ("enable", "start", "resume", "turn on", "switch on", "unpause")


def _normalize_message(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _find_agents_in_message(message: str) -> list[str]:
    normalized = _normalize_message(message)
    if not normalized:
        return []
    if "all agents" in normalized or normalized in {"pause all", "stop all", "enable all"}:
        return list(TOGGLEABLE_AGENTS)
    if "everyone" in normalized and any(
        phrase.replace(" ", "") in normalized.replace(" ", "")
        for phrase in _DISABLE_PHRASES + _ENABLE_PHRASES
    ):
        return list(TOGGLEABLE_AGENTS)

    found: list[str] = []
    for alias, key in sorted(_AGENT_ALIASES.items(), key=lambda item: -len(item[0])):
        alias_norm = _normalize_message(alias)
        if not alias_norm:
            continue
        if alias_norm in normalized and key not in found:
            found.append(key)
    return found


def _toggle_intent(message: str) -> bool | None:
    normalized = _normalize_message(message)
    if not normalized:
        return None
    padded = f" {normalized} "
    disable = any(f" {phrase} " in padded for phrase in _DISABLE_PHRASES)
    enable = any(f" {phrase} " in padded for phrase in _ENABLE_PHRASES)
    if disable and not enable:
        return False
    if enable and not disable:
        return True
    return None


def try_rule_based_toggle(message: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Handle pause/resume phrasing without Spark when the message is unambiguous."""
    enabled = _toggle_intent(message)
    if enabled is None:
        return None
    agents = _find_agents_in_message(message)
    if not agents:
        return None

    actions = [
        {"type": "set_agent_enabled", "agent": agent, "enabled": enabled}
        for agent in agents
    ]
    results = execute_actions(actions)
    labels = _toggleable_agent_labels()
    verb = "Enabled" if enabled else "Paused"
    names = ", ".join(labels.get(agent, agent) for agent in agents)
    reply = f"{verb}: {names}."
    if not all(result.get("ok") for result in results):
        reply += " Some switches could not be applied — check agent names."
    return reply, actions, results


def _toggleable_agent_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for key in TOGGLEABLE_AGENTS:
        labels[key] = KNOWN_AGENT_ROSTER.get(key, key.replace("_", " ").title())
    return labels


def format_llm_error(exc: BaseException) -> str:
    """Turn Spark/queue failures into operator-facing text."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status >= 500 or status == 503:
            return (
                "Spark LLM is unavailable right now (spark-queue could not reach the GPU "
                f"upstream — HTTP {status}). Pause/resume commands still work without Spark; "
                "queueing hunt/research/SEO work needs Spark back online. "
                "Check that Spark SGLang is running on the ranch and spark-queue can reach it."
            )
        return f"Spark queue rejected the request (HTTP {status})."
    if isinstance(exc, httpx.HTTPError):
        return (
            "Could not reach spark-queue for LLM interpretation. "
            "Pause/resume commands still work without Spark; complex queue instructions "
            "need spark-queue and the GPU online."
        )
    return str(exc)


def _operator_context() -> dict[str, Any]:
    stored = list_agent_enabled()
    agents = []
    for key in TOGGLEABLE_AGENTS:
        agents.append(
            {
                "key": key,
                "label": _toggleable_agent_labels()[key],
                "enabled": stored.get(key, True),
            }
        )
    brands = [brand.value for brand in Brand if brand != Brand.UNASSIGNED]
    research_kinds = [kind.value for kind in ResearchFindingKind]
    seo_kinds = [kind.value for kind in SeoQueryKind]
    enabled = enabled_work_agents()
    return {
        "agents": agents,
        "brands": brands,
        "research_kinds": research_kinds,
        "seo_kinds": seo_kinds,
        "enabled_work_agents": enabled,
        "allowed_enqueue_actions": allowed_enqueue_actions(enabled),
        "focused": is_focused_roster(enabled),
    }


def _build_system_prompt(ctx: dict[str, Any]) -> str:
    agent_lines = "\n".join(
        f"- {row['key']}: {row['label']} (enabled={row['enabled']})"
        for row in ctx["agents"]
    )
    enabled = list(ctx.get("enabled_work_agents") or [])
    allowed = list(ctx.get("allowed_enqueue_actions") or [])
    allowed_text = ", ".join(allowed) if allowed else "(none — do not enqueue work)"
    if not enabled:
        focus_rules = (
            "- No work agents are enabled. Do not enqueue hunt/research/engagement/SEO/"
            "AEO work. You may enable agents only if the operator explicitly asks.\n"
        )
    elif ctx.get("focused"):
        focus_rules = (
            f"- Focused roster: only {len(enabled)} work agent(s) enabled "
            f"({', '.join(enabled)}). Restrict tasking and task types to those agents. "
            f"Allowed enqueue actions: {allowed_text}. "
            "Do not enqueue work for paused agents. Do not enable additional agents "
            "unless the operator explicitly asks to turn them on.\n"
        )
    else:
        focus_rules = (
            f"- Allowed enqueue actions for currently enabled agents: {allowed_text}. "
            "Do not enqueue work for paused agents. Do not enable paused agents unless "
            "the operator explicitly asks.\n"
        )
    return (
        "You are the orchestrator for The Agency CRM. Operators send short commands. "
        "Respond with JSON only (no markdown fences) using this schema:\n"
        '{"reply":"natural language summary for the operator",'
        '"actions":[{"type":"set_agent_enabled","agent":"research","enabled":false},'
        '{"type":"enqueue_hunt","query":"astrology forums","brand":"tactic-studio"},'
        '{"type":"enqueue_research","query":"competitor pricing","brand":"midnightsatin",'
        '"kind":"competitor"},'
        '{"type":"enqueue_engagement","query":"reddit astrology","brand":"tactic-studio"},'
        '{"type":"enqueue_seo","query":"homepage audit","brand":"heybuddy","kind":"site_audit"},'
        '{"type":"enqueue_aeo_geo","query":"llms.txt review","brand":"celestial-nexus"}]}\n'
        "Rules:\n"
        "- Use action types only from: set_agent_enabled, enqueue_hunt, enqueue_research, "
        "enqueue_engagement, enqueue_seo, enqueue_aeo_geo.\n"
        "- Agent keys for toggles: "
        + ", ".join(TOGGLEABLE_AGENTS)
        + ".\n"
        "- Brands: "
        + ", ".join(ctx["brands"])
        + ".\n"
        "- Research kinds: "
        + ", ".join(ctx["research_kinds"])
        + ". SEO kinds: "
        + ", ".join(ctx["seo_kinds"])
        + ".\n"
        "- enqueue_aeo_geo uses kind aeo_geo internally; do not pass kind on that action.\n"
        "- If the operator only asks a question, return actions=[] and answer in reply.\n"
        "- Observe Live Agents enable/disable switches when assigning work.\n"
        + focus_rules
        + "\nToggleable agents now:\n"
        + agent_lines
    )


def _format_history(rows: list[AgencyRequest], *, exclude_id: int | None = None) -> str:
    lines: list[str] = []
    for row in rows:
        if exclude_id is not None and row.id == exclude_id:
            continue
        if row.status.value == "completed" and row.reply:
            lines.append(f"Operator: {row.message}")
            lines.append(f"Orchestrator: {row.reply}")
        elif row.status.value == "failed":
            lines.append(f"Operator: {row.message}")
            lines.append(f"Orchestrator (failed): {row.error_message or 'error'}")
    return "\n".join(lines[-20:])


def _parse_brand(value: str | None) -> Brand | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    for brand in Brand:
        if brand.value == cleaned:
            return brand
    return None


def _parse_research_kind(value: str | None) -> ResearchFindingKind:
    if not value:
        return ResearchFindingKind.OTHER
    cleaned = value.strip().lower()
    for kind in ResearchFindingKind:
        if kind.value == cleaned:
            return kind
    return ResearchFindingKind.OTHER


def _parse_seo_kind(value: str | None) -> SeoQueryKind:
    if not value:
        return SeoQueryKind.SITE_AUDIT
    cleaned = value.strip().lower()
    for kind in SeoQueryKind:
        if kind.value == cleaned:
            return kind
    return SeoQueryKind.SITE_AUDIT


def execute_action(action: dict[str, Any]) -> dict[str, Any]:
    """Run one parsed action and return a result record."""
    action_type = str(action.get("type") or "").strip()
    if action_type not in _ACTION_TYPES:
        return {
            "type": action_type or "unknown",
            "ok": False,
            "detail": "unsupported action type",
        }

    if action_type == "set_agent_enabled":
        agent = str(action.get("agent") or "").strip()
        if agent not in TOGGLEABLE_AGENTS:
            return {
                "type": action_type,
                "ok": False,
                "detail": f"unknown agent {agent!r}",
            }
        enabled = action.get("enabled")
        if not isinstance(enabled, bool):
            return {
                "type": action_type,
                "ok": False,
                "detail": "enabled must be boolean",
            }
        stored = set_agent_enabled(agent, enabled)
        return {
            "type": action_type,
            "ok": True,
            "agent": agent,
            "enabled": stored,
        }

    owner = ENQUEUE_ACTION_AGENTS.get(action_type)
    if owner is not None and not is_agent_enabled(owner):
        return {
            "type": action_type,
            "ok": False,
            "agent": owner,
            "detail": f"{owner} is paused; enqueue skipped",
        }

    brand = _parse_brand(str(action.get("brand") or ""))
    if brand is None:
        return {
            "type": action_type,
            "ok": False,
            "detail": "brand is required and must be a known brand slug",
        }
    query = str(action.get("query") or "").strip()
    if not query:
        return {
            "type": action_type,
            "ok": False,
            "detail": "query is required",
        }

    if action_type == "enqueue_hunt":
        enqueued = HuntStore().enqueue_query(
            query=query,
            brand=brand,
            origin=ORIGIN,
        )
        return {
            "type": action_type,
            "ok": True,
            "enqueued": enqueued,
            "brand": brand.value,
            "query": query,
        }

    if action_type == "enqueue_research":
        kind = _parse_research_kind(str(action.get("kind") or ""))
        enqueued = ResearchQueryStore().enqueue_query(
            query=query,
            brand=brand,
            kind=kind,
            origin=ORIGIN,
        )
        return {
            "type": action_type,
            "ok": True,
            "enqueued": enqueued,
            "brand": brand.value,
            "kind": kind.value,
            "query": query,
        }

    if action_type == "enqueue_engagement":
        enqueued = EngagementQueryStore().enqueue_query(
            query=query,
            brand=brand,
            origin=ORIGIN,
        )
        return {
            "type": action_type,
            "ok": True,
            "enqueued": enqueued,
            "brand": brand.value,
            "query": query,
        }

    if action_type == "enqueue_seo":
        kind = _parse_seo_kind(str(action.get("kind") or ""))
        enqueued = SeoQueryStore().enqueue_query(
            query=query,
            brand=brand,
            kind=kind,
            origin=ORIGIN,
        )
        return {
            "type": action_type,
            "ok": True,
            "enqueued": enqueued,
            "brand": brand.value,
            "kind": kind.value,
            "query": query,
        }

    enqueued = SeoQueryStore().enqueue_query(
        query=query,
        brand=brand,
        kind=SeoQueryKind.AEO_GEO,
        origin=ORIGIN,
    )
    return {
        "type": action_type,
        "ok": True,
        "enqueued": enqueued,
        "brand": brand.value,
        "kind": SeoQueryKind.AEO_GEO.value,
        "query": query,
    }


def execute_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply enable/disable toggles first so a batch can enable then enqueue."""
    results: list[dict[str, Any] | None] = [None] * len(actions)
    for index, action in enumerate(actions):
        if str(action.get("type") or "").strip() == "set_agent_enabled":
            results[index] = execute_action(action)
    for index, action in enumerate(actions):
        if results[index] is None:
            results[index] = execute_action(action)
    return [row for row in results if row is not None]


def interpret_operator_message(
    message: str,
    *,
    history_rows: list[AgencyRequest] | None = None,
    exclude_id: int | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse the operator message and execute returned actions."""
    rule_based = try_rule_based_toggle(message)
    if rule_based is not None:
        return rule_based

    ctx = _operator_context()
    history = _format_history(history_rows or [], exclude_id=exclude_id)
    user_content = message.strip()
    if history:
        user_content = f"Recent conversation:\n{history}\n\nNew operator message:\n{message}"

    settings = get_settings()
    record_heartbeat(
        ACTOR,
        status=AgentStatus.THINKING,
        task="interpreting operator command",
        resource=f"Spark queue ({settings.llm_base_url})",
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": _build_system_prompt(ctx)},
                    {"role": "user", "content": user_content},
                ],
            },
            timeout=120.0,
            actor=ACTOR,
            task="operator command",
        )
    except httpx.HTTPError as exc:
        raise ValueError(format_llm_error(exc)) from exc
    finally:
        record_heartbeat(ACTOR, status=AgentStatus.IDLE, task=None, resource=None)

    content = ""
    choices = response.get("choices") or []
    if choices:
        content = str((choices[0].get("message") or {}).get("content") or "")

    parsed = extract_json_object(content)
    if parsed is None:
        raise ValueError("orchestrator model did not return parseable JSON")

    reply = str(parsed.get("reply") or "").strip()
    if not reply:
        reply = "Command processed."

    raw_actions = parsed.get("actions")
    actions: list[dict[str, Any]] = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if isinstance(item, dict):
                actions.append(item)

    results = execute_actions(actions)
    return reply, actions, results


def process_pending_agency_requests(*, max_requests: int = 1) -> int:
    """Claim and process up to ``max_requests`` pending operator commands."""
    processed = 0
    history = list_agency_requests(limit=20)
    for _ in range(max(1, max_requests)):
        row = claim_next_pending_agency_request()
        if row is None:
            break
        try:
            reply, actions, results = interpret_operator_message(
                row.message,
                history_rows=history,
                exclude_id=row.id,
            )
            mark_agency_request_completed(
                row.id,
                reply=reply,
                actions=[{"planned": actions, "results": results}],
            )
            processed += 1
            history = list_agency_requests(limit=20)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agency request %s failed", row.id)
            if isinstance(exc, ValueError):
                message = str(exc)
            elif isinstance(exc, httpx.HTTPError):
                message = format_llm_error(exc)
            else:
                message = str(exc)
            mark_agency_request_failed(row.id, message)
            processed += 1
            history = list_agency_requests(limit=20)
    return processed


def has_pending_agency_requests() -> bool:
    return count_pending_agency_requests() > 0
