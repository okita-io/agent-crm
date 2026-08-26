"""Deterministic hunt-loop feedback: communities and people → search queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import unquote

from .config import get_settings
from .contact_extractor import _looks_like_name
from .enums import Brand, ContactAudience, HuntResourceKind
from .hunt_seeds import origin_with_audience
from .hunt_store import HuntStore
from .hunt_utils import ResourceClassification, normalize_query

_SKIP_PERSON_TOKENS = frozenset(
    {
        "admin",
        "administrator",
        "contact",
        "editor",
        "info",
        "moderator",
        "noreply",
        "owner",
        "staff",
        "support",
        "team",
        "webmaster",
    }
)

_EMAIL_LOCAL_GARBAGE_RE = re.compile(
    r"^[a-z0-9]+([._+\-][a-z0-9]+)*$",
    re.IGNORECASE,
)


@dataclass
class HuntFeedbackBudget:
    """Per-run caps on deterministic community/person query enqueue."""

    community_terms_remaining: int
    person_terms_remaining: int

    @classmethod
    def from_settings(cls) -> HuntFeedbackBudget:
        settings = get_settings()
        return cls(
            community_terms_remaining=settings.hunter_community_terms_per_run,
            person_terms_remaining=settings.hunter_person_terms_per_run,
        )


def is_valid_hunt_person_name(name: str | None) -> bool:
    """True when a contact name is worth public discovery queries."""
    if not name:
        return False
    text = re.sub(r"\s+", " ", name.strip())
    if not text or not _looks_like_name(text):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    if len(words) < 2:
        return False
    if len(words) == 1 and words[0].lower() in _SKIP_PERSON_TOKENS:
        return False
    if any(word.lower() in _SKIP_PERSON_TOKENS for word in words) and len(words) < 3:
        return False
    if "@" in text:
        return False
    if len(words) == 1 and (
        "." in text or "_" in text or "+" in text or _EMAIL_LOCAL_GARBAGE_RE.fullmatch(text)
    ):
        return False
    return True


def community_search_terms(
    classification: ResourceClassification,
    *,
    title: str | None = None,
    max_terms: int = 4,
) -> list[str]:
    """Build 2–4 bounded search queries for a newly discovered community."""
    terms: list[str] = []
    slug = classification.community_slug
    platform = classification.platform
    label = classification.community_label or title

    if platform == "reddit" and slug:
        terms.extend(
            [
                f"site:reddit.com/r/{slug}",
                f'"{slug}" community',
                f"{slug} discord",
                f"{slug} facebook group",
            ]
        )
    elif platform == "facebook" and slug:
        terms.extend(
            [
                f"site:facebook.com/groups/{slug}",
                f"{slug} facebook group",
                f'"{slug}" community',
            ]
        )
    elif platform == "discord" and slug:
        terms.extend(
            [
                f"{slug} discord",
                f"site:discord.com {slug}",
                f'"{slug}" discord server',
            ]
        )
    elif platform == "google_groups" and slug:
        terms.extend(
            [
                f"site:groups.google.com {slug}",
                f'"{slug}" google group',
            ]
        )
    elif platform == "meetup" and slug:
        terms.extend(
            [
                f"site:meetup.com {slug}",
                f"{slug} meetup group",
            ]
        )
    elif platform in {"lemmy", "discourse", "lobsters"} and slug:
        terms.extend(
            [
                f"{slug} {platform} community",
                f'"{slug}" forum',
            ]
        )
    elif platform == "slack" and slug:
        terms.extend(
            [
                f"{slug} slack community",
                f'"{slug}" slack workspace',
            ]
        )

    if label and len(terms) < max_terms:
        quoted = f'"{label.strip()}"'
        if quoted not in terms:
            terms.append(f"{quoted} community")

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = normalize_query(term)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(term.strip())
        if len(deduped) >= max(2, min(max_terms, 4)):
            break
    return deduped[:max_terms]


def person_search_terms(name: str, *, max_terms: int = 4) -> list[str]:
    """Build brand-scoped person discovery queries (never raw emails)."""
    clean = re.sub(r"\s+", " ", name.strip())
    if not is_valid_hunt_person_name(clean):
        return []
    quoted = f'"{clean}"'
    templates = [
        f"{quoted} reddit",
        f"{quoted} site:reddit.com",
        f"{quoted} discord",
        f"{quoted} facebook group",
    ]
    return templates[:max_terms]


def _origin_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "unknown"


def enqueue_community_terms(
    store: HuntStore,
    *,
    classification: ResourceClassification,
    title: str | None,
    brand: Brand,
    run_id: str | None,
    budget: HuntFeedbackBudget,
    audience: ContactAudience | None = None,
) -> int:
    """Enqueue community-derived queries when budget allows."""
    if classification.kind not in {
        HuntResourceKind.COMMUNITY,
        HuntResourceKind.FORUM,
    }:
        return 0

    slug = classification.community_slug or classification.platform or "community"
    origin = origin_with_audience(
        f"community:{classification.platform or 'web'}/{_origin_slug(slug)}",
        audience,
    )
    enqueued = 0
    for term in community_search_terms(classification, title=title):
        if budget.community_terms_remaining <= 0:
            break
        if store.enqueue_query(
            query=term,
            brand=brand,
            origin=origin,
            params=None,
            run_id=run_id,
        ):
            budget.community_terms_remaining -= 1
            enqueued += 1
    return enqueued


def enqueue_person_terms(
    store: HuntStore,
    *,
    name: str,
    brand: Brand,
    run_id: str | None,
    budget: HuntFeedbackBudget,
    audience: ContactAudience | None = None,
) -> int:
    """Enqueue person-derived queries for a validated contact name."""
    if not is_valid_hunt_person_name(name):
        return 0

    origin = origin_with_audience(f"person:{_origin_slug(name)}", audience)
    enqueued = 0
    for term in person_search_terms(name):
        if budget.person_terms_remaining <= 0:
            break
        if "@" in term:
            continue
        if store.enqueue_query(
            query=term,
            brand=brand,
            origin=origin,
            params=None,
            run_id=run_id,
        ):
            budget.person_terms_remaining -= 1
            enqueued += 1
    return enqueued


def parse_community_notes(notes: str | None) -> dict | None:
    """Return community metadata stored in hunt_resources.notes, if present."""
    if not notes:
        return None
    text = notes.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("community"):
        return payload
    return None
