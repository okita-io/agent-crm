"""Persistence and orchestration for contact profiles."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select

from .config import get_settings
from .contact_extractor import ExtractedContact, extract_contacts
from .contact_social_lookup import lookup_social_profiles
from .db import session_scope
from .enums import Brand, LeadSource, LeadStatus
from .models import ContactProfile, Lead, Opportunity
from .schemas import ContactProfileOut

logger = logging.getLogger(__name__)


@dataclass
class ContactExtractionBudget:
    """Per-run cap on how many profiles get SearXNG social lookup."""

    social_lookups_remaining: int

    @classmethod
    def from_settings(cls) -> ContactExtractionBudget:
        return cls(social_lookups_remaining=get_settings().contact_social_lookups_per_run)

    def consume_profile_lookup(self) -> bool:
        if self.social_lookups_remaining <= 0:
            return False
        self.social_lookups_remaining -= 1
        return True


def merge_socials(
    existing: dict | None,
    incoming: dict | None,
) -> dict | None:
    if not incoming:
        return existing
    merged = dict(existing or {})
    for key, value in incoming.items():
        if key == "other":
            current = merged.get("other")
            if isinstance(current, list):
                extras = [item for item in value if item not in current] if isinstance(value, list) else []
                merged["other"] = current + extras
            elif isinstance(value, list):
                merged["other"] = value
            continue
        if key not in merged or not merged[key]:
            merged[key] = value
    return merged or None


def merge_source_urls(existing: list | None, source_url: str) -> list[str]:
    urls = list(existing or [])
    if source_url not in urls:
        urls.append(source_url)
    return urls


def _find_lead_by_email(session, email: str) -> Lead | None:
    return session.scalar(
        select(Lead).where(func.lower(Lead.email) == email.lower()).limit(1)
    )


def _upsert_lead_for_contact(
    session,
    *,
    email: str,
    name: str | None,
    brand: Brand,
    source_url: str,
    socials: dict | None,
) -> Lead:
    lead = _find_lead_by_email(session, email)
    payload_fragment = {
        "found_on": [source_url],
        "socials": socials or {},
    }
    if lead is None:
        lead = Lead(
            name=name,
            email=email,
            source=LeadSource.CONTACT,
            brand=brand,
            status=LeadStatus.NEW,
            raw_payload=payload_fragment,
        )
        session.add(lead)
        session.flush()
        session.add(Opportunity(lead_id=lead.id, brand=brand))
        return lead

    if name and not lead.name:
        lead.name = name
    if brand != Brand.UNASSIGNED and lead.brand == Brand.UNASSIGNED:
        lead.brand = brand

    raw = dict(lead.raw_payload or {})
    found_on = list(raw.get("found_on") or [])
    if source_url not in found_on:
        found_on.append(source_url)
    raw["found_on"] = found_on
    raw["socials"] = merge_socials(raw.get("socials"), socials)
    lead.raw_payload = raw
    return lead


def upsert_contact_profile(
    *,
    email: str,
    name: str | None,
    brand: Brand,
    source_url: str,
    socials: dict | None = None,
) -> ContactProfileOut:
    """Insert or merge a contact profile and link an email-keyed lead."""
    normalized_email = email.strip().lower()
    with session_scope() as session:
        row = session.scalar(
            select(ContactProfile).where(ContactProfile.email == normalized_email)
        )
        lead = _upsert_lead_for_contact(
            session,
            email=normalized_email,
            name=name,
            brand=brand,
            source_url=source_url,
            socials=socials,
        )

        if row is None:
            row = ContactProfile(
                email=normalized_email,
                name=name,
                brand=brand,
                socials=socials,
                source_urls=[source_url],
                lead_id=lead.id,
            )
            session.add(row)
        else:
            if name and not row.name:
                row.name = name
            if brand != Brand.UNASSIGNED and row.brand == Brand.UNASSIGNED:
                row.brand = brand
            row.socials = merge_socials(row.socials, socials)
            row.source_urls = merge_source_urls(row.source_urls, source_url)
            row.lead_id = lead.id

        session.flush()
        return ContactProfileOut.model_validate(row)


def list_contact_profiles(
    *,
    brand: Brand | None = None,
    email: str | None = None,
    limit: int = 500,
) -> list[ContactProfileOut]:
    with session_scope() as session:
        stmt = select(ContactProfile).order_by(ContactProfile.updated_at.desc())
        if brand is not None:
            stmt = stmt.where(ContactProfile.brand == brand)
        if email is not None:
            stmt = stmt.where(ContactProfile.email == email.strip().lower())
        stmt = stmt.limit(limit)
        return [ContactProfileOut.model_validate(row) for row in session.scalars(stmt)]


def process_scraped_page_contacts(
    *,
    markdown: str | None,
    source_url: str,
    brand: Brand,
    html: str | None = None,
    searx_client: httpx.Client | None = None,
    budget: ContactExtractionBudget | None = None,
) -> list[ContactProfileOut]:
    """Extract contacts from a scraped page, upsert profiles, optionally run social lookup."""
    try:
        extracted = extract_contacts(markdown=markdown, html=html)
    except Exception:  # noqa: BLE001
        logger.exception("Contact extraction failed for %s", source_url)
        return []

    if not extracted:
        return []

    budget = budget or ContactExtractionBudget.from_settings()
    profiles: list[ContactProfileOut] = []

    for contact in extracted:
        try:
            profile = upsert_contact_profile(
                email=contact.email,
                name=contact.name,
                brand=brand,
                source_url=source_url,
                socials=contact.socials or None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to upsert contact profile for %s", contact.email)
            continue

        page_had_socials = bool(contact.socials)
        needs_lookup = not profile.socials
        if needs_lookup and not page_had_socials and budget.consume_profile_lookup():
            try:
                socials, _queries_used = lookup_social_profiles(
                    email=contact.email,
                    name=contact.name,
                    client=searx_client,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Social lookup failed for %s", contact.email)
                profiles.append(profile)
                continue

            if socials:
                profile = upsert_contact_profile(
                    email=contact.email,
                    name=contact.name,
                    brand=brand,
                    source_url=source_url,
                    socials=socials,
                )

        profiles.append(profile)

    return profiles
