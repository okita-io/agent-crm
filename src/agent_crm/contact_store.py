"""Persistence and orchestration for contact profiles."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select

from .config import get_settings
from .contact_extractor import ExtractedContact, extract_contacts
from .contact_quality import (
    ContactBackfillResult,
    clean_contact_data,
    filter_socials,
    is_relevant_contact,
    is_role_inbox_email,
    scrub_notes_value,
)
from .contact_people_enrichment import (
    build_enrichment_metadata,
    enrich_contact_person,
    PeopleEnrichmentResult,
)
from .contact_social_lookup import lookup_social_profiles
from .db import session_scope
from .enums import Brand, ContactAudience, LeadSource, LeadStatus
from .models import ContactProfile, HuntResource, Lead, Opportunity
from .schemas import (
    ContactBackfillResultOut,
    ContactEnrichDetailOut,
    ContactEnrichResultOut,
    ContactProfileOut,
    ContactQualityCleanupOut,
)

logger = logging.getLogger(__name__)


@dataclass
class ContactExtractionBudget:
    """Per-run cap on SearXNG social lookup and people-enrichment."""

    social_lookups_remaining: int
    enrichments_remaining: int
    spark_enrichments_remaining: int

    @classmethod
    def from_settings(cls) -> ContactExtractionBudget:
        settings = get_settings()
        return cls(
            social_lookups_remaining=settings.contact_social_lookups_per_run,
            enrichments_remaining=settings.contact_enrichments_per_run,
            spark_enrichments_remaining=settings.contact_enrichment_spark_per_run,
        )

    def consume_profile_lookup(self) -> bool:
        if self.social_lookups_remaining <= 0:
            return False
        self.social_lookups_remaining -= 1
        return True

    def consume_enrichment(self) -> bool:
        if self.enrichments_remaining <= 0:
            return False
        self.enrichments_remaining -= 1
        return True

    def consume_spark(self) -> bool:
        if self.spark_enrichments_remaining <= 0:
            return False
        self.spark_enrichments_remaining -= 1
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
    audience: ContactAudience | None = None,
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
            audience=audience,
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
    if audience is not None and lead.audience is None:
        lead.audience = audience

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
    audience: ContactAudience | None = None,
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
            audience=audience,
        )

        if row is None:
            row = ContactProfile(
                email=normalized_email,
                name=name,
                brand=brand,
                audience=audience,
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
            if audience is not None and row.audience is None:
                row.audience = audience
            row.socials = merge_socials(row.socials, socials)
            row.source_urls = merge_source_urls(row.source_urls, source_url)
            row.lead_id = lead.id

        session.flush()
        return ContactProfileOut.model_validate(row)


def _persist_enrichment(
    *,
    email: str,
    result: PeopleEnrichmentResult,
) -> ContactProfileOut:
    """Write enrichment fields and evidence onto an existing contact profile."""
    normalized_email = email.strip().lower()
    fields = result.fields
    enrichment_meta = build_enrichment_metadata(result)

    with session_scope() as session:
        row = session.scalar(
            select(ContactProfile).where(ContactProfile.email == normalized_email)
        )
        if row is None:
            raise ValueError(f"contact profile not found for {normalized_email}")

        if fields.name and not row.name:
            row.name = fields.name
        if fields.title and not row.title:
            row.title = fields.title
        if fields.organization and not row.organization:
            row.organization = fields.organization
        if fields.location and not row.location:
            row.location = fields.location
        if fields.bio and not row.bio:
            row.bio = fields.bio
        if fields.socials:
            row.socials = merge_socials(row.socials, fields.socials)
        row.enrichment = enrichment_meta

        if row.lead_id and fields.name:
            lead = session.get(Lead, row.lead_id)
            if lead is not None and not lead.name:
                lead.name = fields.name

        session.flush()
        return ContactProfileOut.model_validate(row)


def _needs_people_enrichment(profile: ContactProfileOut) -> bool:
    return (
        profile.enrichment is None
        and profile.title is None
        and profile.organization is None
    )


def _maybe_enrich_contact(
    *,
    profile: ContactProfileOut,
    name: str | None,
    searx_client: httpx.Client | None,
    budget: ContactExtractionBudget,
) -> ContactProfileOut:
    if is_role_inbox_email(profile.email):
        return profile
    if not _needs_people_enrichment(profile):
        return profile
    if not budget.consume_enrichment():
        return profile

    allow_spark = budget.spark_enrichments_remaining > 0
    try:
        result = enrich_contact_person(
            email=profile.email,
            name=name or profile.name,
            searx_client=searx_client,
            allow_spark=allow_spark,
        )
    except Exception:  # noqa: BLE001
        logger.exception("People enrichment failed for %s", profile.email)
        return profile

    if result is None:
        return profile

    if result.spark_used and allow_spark:
        budget.consume_spark()

    try:
        return _persist_enrichment(email=profile.email, result=result)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to persist enrichment for %s", profile.email)
        return profile


def _apply_contact_profile_filters(
    stmt,
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    email: str | None = None,
):
    if brand is not None:
        stmt = stmt.where(ContactProfile.brand == brand)
    if audience is not None:
        stmt = stmt.where(ContactProfile.audience == audience)
    if email is not None:
        stmt = stmt.where(ContactProfile.email == email.strip().lower())
    return stmt


def count_contact_profiles(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    email: str | None = None,
) -> int:
    """Count contact profiles matching optional brand/audience/email filters."""
    with session_scope() as session:
        stmt = select(func.count()).select_from(ContactProfile)
        stmt = _apply_contact_profile_filters(
            stmt,
            brand=brand,
            audience=audience,
            email=email,
        )
        return int(session.scalar(stmt) or 0)


def count_contact_profiles_by_brand(
    *,
    audience: ContactAudience | None = None,
) -> list[dict]:
    """Count contact profiles grouped by brand, optionally filtered by audience."""
    with session_scope() as session:
        stmt = (
            select(ContactProfile.brand, func.count())
            .group_by(ContactProfile.brand)
            .order_by(ContactProfile.brand.asc())
        )
        if audience is not None:
            stmt = stmt.where(ContactProfile.audience == audience)
        return [
            {"brand": brand.value, "count": count}
            for brand, count in session.execute(stmt)
        ]


def count_contact_emails_by_brand_audience() -> list[dict]:
    """Count contact profiles with non-empty email, grouped by brand and audience."""
    with session_scope() as session:
        stmt = (
            select(
                ContactProfile.brand,
                ContactProfile.audience,
                func.count(),
            )
            .where(ContactProfile.email.is_not(None))
            .where(func.length(func.trim(ContactProfile.email)) > 0)
            .group_by(ContactProfile.brand, ContactProfile.audience)
            .order_by(ContactProfile.brand.asc(), ContactProfile.audience.asc())
        )
        return [
            {
                "brand": brand.value,
                "audience": audience.value if audience else None,
                "count": count,
            }
            for brand, audience, count in session.execute(stmt)
        ]


def list_contact_profiles(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    email: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> list[ContactProfileOut]:
    with session_scope() as session:
        stmt = select(ContactProfile).order_by(ContactProfile.updated_at.desc())
        stmt = _apply_contact_profile_filters(
            stmt,
            brand=brand,
            audience=audience,
            email=email,
        )
        stmt = stmt.offset(max(offset, 0)).limit(limit)
        return [ContactProfileOut.model_validate(row) for row in session.scalars(stmt)]


def process_scraped_page_contacts(
    *,
    markdown: str | None,
    source_url: str,
    brand: Brand,
    html: str | None = None,
    searx_client: httpx.Client | None = None,
    budget: ContactExtractionBudget | None = None,
    audience: ContactAudience | None = None,
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
        if not is_relevant_contact(contact.email, [source_url]):
            logger.debug(
                "Skipping irrelevant contact %s from %s",
                contact.email,
                source_url,
            )
            continue
        cleaned_socials = filter_socials(contact.socials or None, email=contact.email)
        try:
            profile = upsert_contact_profile(
                email=contact.email,
                name=contact.name,
                brand=brand,
                source_url=source_url,
                socials=cleaned_socials,
                audience=audience,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to upsert contact profile for %s", contact.email)
            continue

        page_had_socials = bool(contact.socials)
        profile = _maybe_enrich_contact(
            profile=profile,
            name=contact.name,
            searx_client=searx_client,
            budget=budget,
        )

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
                cleaned_lookup_socials = filter_socials(socials, email=contact.email)
                if cleaned_lookup_socials:
                    profile = upsert_contact_profile(
                        email=contact.email,
                        name=contact.name,
                        brand=brand,
                        source_url=source_url,
                        socials=cleaned_lookup_socials,
                        audience=audience,
                    )

        profiles.append(profile)

    return profiles


def backfill_contact_quality(
    *,
    limit: int = 500,
    dry_run: bool = False,
) -> ContactBackfillResultOut:
    """Re-apply contact-quality filters to existing profiles and related notes."""
    result = ContactBackfillResult()

    with session_scope() as session:
        profiles = list(
            session.scalars(
                select(ContactProfile).order_by(ContactProfile.updated_at.desc()).limit(limit)
            )
        )
        result.profiles_scanned = len(profiles)

        for row in profiles:
            try:
                cleaned_socials, kept_urls, keep, cleanup = clean_contact_data(
                    email=row.email,
                    socials=row.socials,
                    source_urls=row.source_urls,
                )
                changed = (
                    cleaned_socials != row.socials
                    or kept_urls != (row.source_urls or [])
                )
                result.details.append(
                    ContactQualityCleanupOut(
                        email=cleanup.email,
                        kept=cleanup.kept,
                        removed_source_urls=cleanup.removed_source_urls,
                        stripped_social_keys=cleanup.stripped_social_keys,
                        reasons=cleanup.reasons,
                    )
                )

                if not keep:
                    if not dry_run:
                        lead = session.get(Lead, row.lead_id) if row.lead_id else None
                        if lead and lead.status != LeadStatus.DISQUALIFIED:
                            lead.status = LeadStatus.DISQUALIFIED
                            result.leads_disqualified += 1
                        session.delete(row)
                    result.profiles_removed += 1
                    continue

                if changed and not dry_run:
                    row.socials = cleaned_socials
                    row.source_urls = kept_urls
                    if row.lead_id:
                        lead = session.get(Lead, row.lead_id)
                        if lead is not None:
                            raw = dict(lead.raw_payload or {})
                            raw["socials"] = cleaned_socials
                            raw["found_on"] = kept_urls
                            lead.raw_payload = raw
                    result.profiles_updated += 1
                elif changed:
                    result.profiles_updated += 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{row.email}: {exc}")

        if not dry_run:
            resources = list(session.scalars(select(HuntResource)))
            for resource in resources:
                cleaned_notes = scrub_notes_value(resource.notes)
                if cleaned_notes != resource.notes:
                    resource.notes = cleaned_notes
                    result.resource_notes_scrubbed += 1

    return ContactBackfillResultOut(
        profiles_scanned=result.profiles_scanned,
        profiles_updated=result.profiles_updated,
        profiles_removed=result.profiles_removed,
        leads_disqualified=result.leads_disqualified,
        resource_notes_scrubbed=result.resource_notes_scrubbed,
        details=result.details,
        errors=result.errors,
    )


def backfill_contact_enrichment(
    *,
    limit: int = 500,
    dry_run: bool = False,
) -> ContactEnrichResultOut:
    """Backfill public people-enrichment for profiles missing enrichment data."""
    profiles_scanned = 0
    profiles_enriched = 0
    spark_calls = 0
    details: list[ContactEnrichDetailOut] = []
    errors: list[str] = []

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ContactProfile)
                .where(
                    ContactProfile.enrichment.is_(None),
                )
                .order_by(ContactProfile.updated_at.desc())
                .limit(limit)
            )
        )

    for row in rows:
        profiles_scanned += 1
        if is_role_inbox_email(row.email):
            details.append(
                ContactEnrichDetailOut(email=row.email, enriched=False, fields_filled=[])
            )
            continue

        try:
            result = enrich_contact_person(
                email=row.email,
                name=row.name,
                allow_spark=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row.email}: {exc}")
            continue

        if result is None:
            details.append(
                ContactEnrichDetailOut(email=row.email, enriched=False, fields_filled=[])
            )
            continue

        fields_filled: list[str] = []
        fields = result.fields
        if fields.name and not row.name:
            fields_filled.append("name")
        if fields.title:
            fields_filled.append("title")
        if fields.organization:
            fields_filled.append("organization")
        if fields.location:
            fields_filled.append("location")
        if fields.bio:
            fields_filled.append("bio")
        if fields.socials:
            fields_filled.append("socials")

        if not fields_filled and not dry_run:
            details.append(
                ContactEnrichDetailOut(email=row.email, enriched=False, fields_filled=[])
            )
            continue

        if dry_run:
            profiles_enriched += 1
            if result.spark_used:
                spark_calls += 1
            details.append(
                ContactEnrichDetailOut(
                    email=row.email,
                    enriched=True,
                    spark_used=result.spark_used,
                    fields_filled=fields_filled,
                )
            )
            continue

        try:
            _persist_enrichment(email=row.email, result=result)
            profiles_enriched += 1
            if result.spark_used:
                spark_calls += 1
            details.append(
                ContactEnrichDetailOut(
                    email=row.email,
                    enriched=True,
                    spark_used=result.spark_used,
                    fields_filled=fields_filled,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row.email}: {exc}")

    return ContactEnrichResultOut(
        profiles_scanned=profiles_scanned,
        profiles_enriched=profiles_enriched,
        spark_calls=spark_calls,
        details=details,
        errors=errors,
    )
