"""Pipeline & Leads query helpers — only VALID-verified email contacts."""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_crm.db import session_scope
from agent_crm.enums import (
    Brand,
    ContactAudience,
    ContactKind,
    ContactVerificationStatus,
    LeadStatus,
)
from agent_crm.models import ContactVerification, Lead
from agent_crm.schemas import LeadOut
from agent_crm.topic_relevance_store import lead_is_topically_visible

PIPELINE_LEAD_CSV_FIELDS = (
    "id",
    "name",
    "email",
    "company",
    "source",
    "score",
    "priority",
    "brand",
    "qualification",
    "status",
    "verified",
    "created",
)


def normalize_audience(audience: ContactAudience | None) -> ContactAudience | None:
    """Map legacy ``user`` to ``end_user`` for display and filtering."""
    from agent_crm.enums import CONTACT_AUDIENCE_ALIASES

    if audience is None:
        return None
    return CONTACT_AUDIENCE_ALIASES.get(audience, audience)


def lead_email_is_pipeline_visible(
    session: Session,
    lead: Lead,
) -> bool:
    """Return True when the lead's primary email has a VALID verification row."""
    if lead.status == LeadStatus.DISQUALIFIED:
        return False
    email = (lead.email or "").strip().lower()
    if not email:
        return False
    row = session.scalar(
        select(ContactVerification.status)
        .where(ContactVerification.lead_id == lead.id)
        .where(ContactVerification.contact == email)
        .where(ContactVerification.contact_kind == ContactKind.EMAIL)
        .order_by(ContactVerification.checked_at.desc())
        .limit(1)
    )
    return row == ContactVerificationStatus.VALID


def list_pipeline_leads(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    limit: int | None = 500,
) -> list[LeadOut]:
    """Leads whose primary email is DNS/MX verified VALID (not disqualified).

    Pass ``limit=None`` to return every matching row (CSV full export).
    """
    with session_scope() as session:
        email_verification = (
            select(ContactVerification.lead_id)
            .where(ContactVerification.contact_kind == ContactKind.EMAIL)
            .where(ContactVerification.status == ContactVerificationStatus.VALID)
            .where(
                func.lower(func.trim(ContactVerification.contact))
                == func.lower(func.trim(Lead.email))
            )
            .correlate(Lead)
            .exists()
        )
        stmt = (
            select(Lead)
            .where(Lead.email.is_not(None))
            .where(func.length(func.trim(Lead.email)) > 0)
            .where(Lead.status != LeadStatus.DISQUALIFIED)
            .where(email_verification)
            .order_by(Lead.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        if brand is not None:
            stmt = stmt.where(Lead.brand == brand)
        if audience is not None:
            normalized = normalize_audience(audience)
            legacy = (
                ContactAudience.USER
                if normalized == ContactAudience.END_USER
                else None
            )
            if legacy is not None:
                stmt = stmt.where(Lead.audience.in_([normalized, legacy]))
            else:
                stmt = stmt.where(Lead.audience == normalized)
        leads = [row for row in session.scalars(stmt) if lead_is_topically_visible(row)]
        return [LeadOut.model_validate(row) for row in leads]


def pipeline_lead_records(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
    limit: int | None = 500,
) -> list[dict[str, Any]]:
    """Display/export rows for the Pipeline & leads table."""
    records: list[dict[str, Any]] = []
    for lead in list_pipeline_leads(audience=audience, brand=brand, limit=limit):
        created = lead.created_at.isoformat() if lead.created_at else None
        records.append(
            {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "company": lead.company,
                "source": lead.source.value,
                "score": lead.score,
                "priority": lead.priority.value if lead.priority else None,
                "brand": lead.brand.value,
                "qualification": (
                    normalize_audience(lead.audience).value
                    if lead.audience
                    else None
                ),
                "status": lead.status.value,
                "verified": "valid",
                "created": created,
            }
        )
    return records


def pipeline_leads_csv(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
) -> tuple[bytes, int]:
    """UTF-8 CSV (Excel BOM) of every matching pipeline lead."""
    records = pipeline_lead_records(brand=brand, audience=audience, limit=None)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PIPELINE_LEAD_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue().encode("utf-8-sig"), len(records)


def pipeline_leads_export_filename(
    *,
    brand: Brand | None = None,
    audience: ContactAudience | None = None,
) -> str:
    parts = ["pipeline-leads"]
    if brand is not None:
        parts.append(brand.value)
    if audience is not None:
        normalized = normalize_audience(audience)
        if normalized is not None:
            parts.append(normalized.value)
    return "-".join(parts) + ".csv"
