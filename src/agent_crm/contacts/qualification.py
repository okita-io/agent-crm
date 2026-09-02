"""Contact/lead qualification: ingest-time inference and public-web backfill."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .quality import (
    has_contact_context_path,
    is_community_platform,
    is_placeholder_email,
    is_relevant_source_url,
    is_role_inbox_email,
    prepare_contact_for_ingest,
)
from agent_crm.db import session_scope
from agent_crm.enums import Brand, ContactAudience
from agent_crm.jobs.store import enqueue_verify_lead_job
from agent_crm.llm_client import chat_completions
from agent_crm.llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, wrap_untrusted
from agent_crm.models import CommentPerson, ContactProfile, Lead
from .pipeline_leads import normalize_audience
from agent_crm.searxng_client import search

logger = logging.getLogger(__name__)

ACTOR = "contact-qualifier"

PROMO_MEDIA_PATH_FRAGMENTS = (
    "/press",
    "/press-kit",
    "/presskit",
    "/media-kit",
    "/mediakit",
    "/media",
    "/newsroom",
    "/brand-assets",
    "/campaign",
    "/promo",
    "/marketing",
)

INDUSTRIAL_PATH_FRAGMENTS = (
    "/industrial",
    "/manufacturing",
    "/enterprise",
    "/b2b",
    "/solutions",
    "/corporate",
)

CLIENT_PATH_FRAGMENTS = (
    "/clients",
    "/case-studies",
    "/case-study",
    "/portfolio",
    "/customer-stories",
    "/testimonials",
)

INFLUENCER_HOST_FRAGMENTS = (
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "twitch.tv",
)

INFLUENCER_SNIPPET_RE = re.compile(
    r"(followers|subscribers|verified creator|content creator|"
    r"youtube\.com|youtu\.be|tiktok\.com|my channel|check out my)",
    re.IGNORECASE,
)

FOLLOWER_COUNT_RE = re.compile(
    r"(\d[\d,.]*)\s*(k|m)?\s*(followers|subscriber|subscribers)",
    re.IGNORECASE,
)

VALID_QUALIFICATIONS = frozenset(
    {
        ContactAudience.END_USER.value,
        ContactAudience.INFLUENCER.value,
        ContactAudience.B2B.value,
        ContactAudience.CLIENT.value,
        ContactAudience.MARKETING.value,
        ContactAudience.USER.value,
    }
)


@dataclass
class QualificationResult:
    audience: ContactAudience
    evidence: list[str] = field(default_factory=list)
    confidence: str = "rule"
    discovered_email: str | None = None
    spark_used: bool = False


def _path_lower(url: str) -> str:
    return urlparse(url).path.lower()


def _host_lower(url: str) -> str:
    return urlparse(url).netloc.lower()


def _has_path_fragment(url: str, fragments: tuple[str, ...]) -> bool:
    path = _path_lower(url)
    return any(fragment in path for fragment in fragments)


def infer_audience_from_ingest(
    *,
    source_url: str,
    email: str | None = None,
    name: str | None = None,
    socials: dict | None = None,
    hunt_audience: ContactAudience | None = None,
    comment_snippet: str | None = None,
    is_comment_person: bool = False,
) -> ContactAudience | None:
    """Deterministic qualification from page context at scrape/ingest time."""
    if hunt_audience is not None:
        return normalize_audience(hunt_audience)

    if is_comment_person:
        snippet = comment_snippet or ""
        if INFLUENCER_SNIPPET_RE.search(snippet):
            return ContactAudience.INFLUENCER
        if is_community_platform(source_url):
            return ContactAudience.END_USER
        return ContactAudience.END_USER

    if email and socials:
        for platform, value in socials.items():
            if platform in {"x", "instagram", "youtube", "tiktok"} and value:
                host_text = str(value).lower()
                if any(fragment in host_text for fragment in INFLUENCER_HOST_FRAGMENTS):
                    return ContactAudience.INFLUENCER

    if _has_path_fragment(source_url, PROMO_MEDIA_PATH_FRAGMENTS):
        return ContactAudience.MARKETING

    if _has_path_fragment(source_url, CLIENT_PATH_FRAGMENTS):
        return ContactAudience.CLIENT

    if _has_path_fragment(source_url, INDUSTRIAL_PATH_FRAGMENTS):
        return ContactAudience.B2B

    if email and has_contact_context_path(source_url):
        if is_relevant_source_url(source_url, email):
            local = email.split("@", 1)[0].lower()
            if any(
                token in local
                for token in ("marketing", "brand", "media", "press", "pr", "campaign")
            ):
                return ContactAudience.MARKETING
            if any(
                token in local
                for token in ("sales", "bizdev", "partnerships", "enterprise")
            ):
                return ContactAudience.B2B
        return ContactAudience.B2B

    if is_community_platform(source_url):
        return ContactAudience.END_USER

    if name:
        lowered = name.lower()
        if any(
            token in lowered
            for token in (
                "marketing",
                "brand manager",
                "brand management",
                "brand",
                "media",
                "pr",
                "cmo",
            )
        ):
            return ContactAudience.MARKETING

    return hunt_audience


def is_weakly_qualified(audience: ContactAudience | None) -> bool:
    """Rows eligible for the qualify backfill worker (null or generic end-user)."""
    if audience is None:
        return True
    if audience == ContactAudience.USER:
        return True
    normalized = normalize_audience(audience)
    return normalized == ContactAudience.END_USER


def _qualification_meta(result: QualificationResult) -> dict[str, Any]:
    return {
        "audience": result.audience.value,
        "evidence": result.evidence,
        "confidence": result.confidence,
        "classified_at": datetime.now(UTC).isoformat(),
        "spark_used": result.spark_used,
        "discovered_email": result.discovered_email,
    }


def _merge_enrichment_qualification(
    existing: dict | None,
    meta: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged["qualification"] = meta
    return merged


def _persist_qualification_on_profile(
    profile_id: int,
    result: QualificationResult,
) -> None:
    with session_scope() as session:
        row = session.get(ContactProfile, profile_id)
        if row is None:
            return
        row.audience = result.audience
        row.enrichment = _merge_enrichment_qualification(
            row.enrichment if isinstance(row.enrichment, dict) else None,
            _qualification_meta(result),
        )
        if row.lead_id:
            lead = session.get(Lead, row.lead_id)
            if lead is not None:
                lead.audience = result.audience
                raw = dict(lead.raw_payload or {})
                raw["qualification"] = _qualification_meta(result)
                lead.raw_payload = raw
                if result.discovered_email and not lead.email:
                    prepared = prepare_contact_for_ingest(result.discovered_email, None)
                    if prepared is not None:
                        lead.email = prepared[0]


def _persist_qualification_on_comment_person(
    comment_person_id: int,
    result: QualificationResult,
) -> None:
    with session_scope() as session:
        row = session.get(CommentPerson, comment_person_id)
        if row is None:
            return
        row.audience = result.audience
        snippets = list(row.comment_snippets or [])
        meta_entry = {
            "qualification": _qualification_meta(result),
            "source_url": (row.source_urls or [""])[-1],
        }
        if snippets and isinstance(snippets[-1], dict):
            snippets[-1] = {**snippets[-1], **meta_entry}
        else:
            snippets.append(meta_entry)
        row.comment_snippets = snippets[-10:]


def _rule_based_qualification(
    *,
    email: str | None,
    name: str | None,
    source_urls: list[str],
    socials: dict | None,
    comment_snippet: str | None,
    platform: str | None,
    handle: str | None,
) -> QualificationResult | None:
    source_url = source_urls[-1] if source_urls else ""
    snippet = comment_snippet or ""

    for url in source_urls:
        if _has_path_fragment(url, PROMO_MEDIA_PATH_FRAGMENTS):
            return QualificationResult(
                audience=ContactAudience.MARKETING,
                evidence=[f"promo/media page: {url}"],
                confidence="rule",
            )

    if _has_path_fragment(source_url, CLIENT_PATH_FRAGMENTS):
        return QualificationResult(
            audience=ContactAudience.CLIENT,
            evidence=[f"client/case-study page: {source_url}"],
            confidence="rule",
        )

    if _has_path_fragment(source_url, INDUSTRIAL_PATH_FRAGMENTS):
        return QualificationResult(
            audience=ContactAudience.B2B,
            evidence=[f"industrial/enterprise page: {source_url}"],
            confidence="rule",
        )

    if platform and "tiktok" in (platform + " " + (handle or "")).lower():
        if FOLLOWER_COUNT_RE.search(snippet):
            return QualificationResult(
                audience=ContactAudience.INFLUENCER,
                evidence=["tiktok handle with follower signal in snippet"],
                confidence="rule",
            )

    if snippet and INFLUENCER_SNIPPET_RE.search(snippet):
        return QualificationResult(
            audience=ContactAudience.INFLUENCER,
            evidence=["comment snippet suggests creator/influencer"],
            confidence="rule",
        )

    inferred = infer_audience_from_ingest(
        source_url=source_url,
        email=email,
        name=name,
        socials=socials,
        comment_snippet=comment_snippet,
        is_comment_person=platform is not None and handle is not None and not email,
    )
    if inferred is None:
        return None
    if not is_weakly_qualified(inferred):
        return QualificationResult(
            audience=inferred,
            evidence=[f"ingest context: {source_url or platform}"],
            confidence="rule",
        )

    if snippet and is_community_platform(source_url):
        return QualificationResult(
            audience=ContactAudience.END_USER,
            evidence=["article/forum commenter"],
            confidence="rule",
        )

    return None


def _parse_spark_qualification(content: str) -> dict[str, Any] | None:
    from agent_crm.llm_text import extract_json_object

    return extract_json_object(content)


def _spark_qualification(
    *,
    email: str | None,
    name: str | None,
    handle: str | None,
    platform: str | None,
    serp_snippets: list[str],
) -> QualificationResult | None:
    subject_parts = []
    if email:
        subject_parts.append(f"email: {email}")
    if name:
        subject_parts.append(f"name: {name}")
    if handle:
        subject_parts.append(f"handle: {platform or 'web'}/{handle}")
    if not subject_parts:
        return None

    evidence_block = "\n".join(
        wrap_untrusted(f"serp_{idx}", line, max_chars=400)
        for idx, line in enumerate(serp_snippets[:8], start=1)
    )
    prompt = (
        "Classify this public contact for a CRM pipeline. "
        "Return JSON only: "
        '{"audience":"end_user|influencer|b2b|client|marketing",'
        '"evidence":["short reason"],"public_email":null}. '
        "Use marketing for press kits, brand media, promotional contacts, "
        "and marketing/brand leadership (VP of marketing, brand manager, "
        "marketing manager). "
        "Use influencer for creators with followers/subscribers. "
        "Use end_user for commenters and community participants. "
        "Use b2b for company/industrial prospects. "
        "Use client for known customers or case-study subjects. "
        "Only include public_email if clearly associated with this person; "
        "never invent addresses.\n\n"
        f"Subject: {', '.join(subject_parts)}\n\n"
        f"Public web snippets:\n{evidence_block or '(none)'}"
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You classify CRM contacts. Output JSON only."
                            + UNTRUSTED_DATA_SYSTEM_SUFFIX
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
            },
            timeout=90.0,
            actor=ACTOR,
            task="qualify contact",
        )
        content = response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        logger.exception("Spark qualification failed")
        return None

    parsed = _parse_spark_qualification(content)
    if not parsed:
        return None
    audience_raw = str(parsed.get("audience", "")).strip().lower()
    if audience_raw == "user":
        audience_raw = ContactAudience.END_USER.value
    if audience_raw not in VALID_QUALIFICATIONS:
        return None
    audience = ContactAudience(audience_raw)
    evidence = parsed.get("evidence")
    evidence_list = (
        [str(item) for item in evidence[:5]]
        if isinstance(evidence, list)
        else [str(evidence)]
        if evidence
        else ["spark classification"]
    )
    public_email = parsed.get("public_email")
    discovered = None
    if isinstance(public_email, str) and "@" in public_email:
        prepared = prepare_contact_for_ingest(public_email, None)
        if prepared is not None and not is_role_inbox_email(prepared[0]) and not is_placeholder_email(
            prepared[0]
        ):
            discovered = prepared[0]
    return QualificationResult(
        audience=normalize_audience(audience) or audience,
        evidence=evidence_list,
        confidence="spark",
        discovered_email=discovered,
        spark_used=True,
    )


def qualify_contact_profile(
    profile_id: int,
    *,
    allow_spark: bool = True,
    client: httpx.Client | None = None,
) -> QualificationResult | None:
    """Research and write qualification for an email contact profile."""
    with session_scope() as session:
        row = session.get(ContactProfile, profile_id)
        if row is None:
            raise ValueError(f"contact profile {profile_id} not found")
        email = row.email
        name = row.name
        source_urls = list(row.source_urls or [])
        socials = row.socials if isinstance(row.socials, dict) else None
        lead_id = row.lead_id

    rule_result = _rule_based_qualification(
        email=email,
        name=name,
        source_urls=source_urls,
        socials=socials,
        comment_snippet=None,
        platform=None,
        handle=None,
    )
    if rule_result is not None and not is_weakly_qualified(rule_result.audience):
        _persist_qualification_on_profile(profile_id, rule_result)
        return rule_result

    query = email
    if name:
        query = f"{name} {email}"
    serp_snippets: list[str] = []
    try:
        results = search(query, limit=5, client=client)
        serp_snippets = [
            f"{hit.title}: {hit.snippet} ({hit.url})"
            for hit in results
        ]
    except Exception:  # noqa: BLE001
        logger.exception("SERP failed for qualification of %s", email)

    if rule_result is not None:
        _persist_qualification_on_profile(profile_id, rule_result)
        return rule_result

    if not allow_spark:
        return None

    spark_result = _spark_qualification(
        email=email,
        name=name,
        handle=None,
        platform=None,
        serp_snippets=serp_snippets,
    )
    if spark_result is None:
        return None

    _persist_qualification_on_profile(profile_id, spark_result)
    if spark_result.discovered_email and lead_id:
        enqueue_verify_lead_job(lead_id)
    return spark_result


def qualify_comment_person(
    comment_person_id: int,
    *,
    allow_spark: bool = True,
    client: httpx.Client | None = None,
) -> QualificationResult | None:
    """Research and write qualification for a handle-only comment author."""
    with session_scope() as session:
        row = session.get(CommentPerson, comment_person_id)
        if row is None:
            raise ValueError(f"comment person {comment_person_id} not found")
        platform = row.platform
        handle = row.handle
        display_name = row.display_name
        source_urls = list(row.source_urls or [])
        snippet = None
        if row.comment_snippets:
            last = row.comment_snippets[-1]
            if isinstance(last, dict):
                snippet = last.get("snippet")

    rule_result = _rule_based_qualification(
        email=None,
        name=display_name,
        source_urls=source_urls,
        socials=None,
        comment_snippet=snippet,
        platform=platform,
        handle=handle,
    )
    if rule_result is not None and not is_weakly_qualified(rule_result.audience):
        _persist_qualification_on_comment_person(comment_person_id, rule_result)
        return rule_result

    query = f"{platform} {handle}"
    if display_name:
        query = f"{display_name} {query}"
    serp_snippets: list[str] = []
    try:
        results = search(query, limit=5, client=client)
        serp_snippets = [
            f"{hit.title}: {hit.snippet} ({hit.url})"
            for hit in results
        ]
    except Exception:  # noqa: BLE001
        logger.exception("SERP failed for comment person %s/%s", platform, handle)

    if rule_result is not None:
        _persist_qualification_on_comment_person(comment_person_id, rule_result)
        return rule_result

    if not allow_spark:
        return None

    spark_result = _spark_qualification(
        email=None,
        name=display_name,
        handle=handle,
        platform=platform,
        serp_snippets=serp_snippets,
    )
    if spark_result is None:
        return None

    _persist_qualification_on_comment_person(comment_person_id, spark_result)
    return spark_result


def count_unqualified_contacts() -> int:
    """Count profiles and comment people needing qualification backfill."""
    from sqlalchemy import select

    total = 0
    with session_scope() as session:
        for audience in session.scalars(select(ContactProfile.audience)):
            if is_weakly_qualified(audience):
                total += 1
        for audience in session.scalars(select(CommentPerson.audience)):
            if is_weakly_qualified(audience):
                total += 1
    return total


def seed_qualify_jobs_for_unqualified(*, limit: int = 50) -> int:
    """Enqueue qualify_contact jobs for weakly-qualified rows."""
    from agent_crm.jobs.store import enqueue_qualify_contact_job

    if limit <= 0:
        return 0

    from sqlalchemy import select

    enqueued = 0
    with session_scope() as session:
        profile_candidates = list(
            session.execute(
                select(ContactProfile.id, ContactProfile.audience)
                .order_by(ContactProfile.updated_at.asc(), ContactProfile.id.asc())
                .limit(limit * 3)
            )
        )
        comment_candidates = list(
            session.execute(
                select(CommentPerson.id, CommentPerson.audience)
                .order_by(CommentPerson.updated_at.asc(), CommentPerson.id.asc())
                .limit(limit * 3)
            )
        )

    for profile_id, audience in profile_candidates:
        if enqueued >= limit:
            break
        if not is_weakly_qualified(audience):
            continue
        if enqueue_qualify_contact_job(contact_profile_id=profile_id):
            enqueued += 1

    for person_id, audience in comment_candidates:
        if enqueued >= limit:
            break
        if not is_weakly_qualified(audience):
            continue
        if enqueue_qualify_contact_job(comment_person_id=person_id):
            enqueued += 1

    return enqueued
