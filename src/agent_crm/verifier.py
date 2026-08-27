"""Lead / Contact Verifier: defensive DNS, MX, and HTTP checks without sending mail.

Checks whether hunter-sourced contacts look live and current. MX-up means the
*domain* can receive mail — not that the local-part is a live person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import session_scope
from .contact_quality import (
    is_dummy_documentation_email,
    is_filename_as_email,
    is_placeholder_email,
    is_relevant_contact,
    is_role_inbox_email,
)
from .enums import (
    ActivityType,
    AgentStatus,
    ContactKind,
    ContactVerificationStatus,
    LeadSource,
    LeadStatus,
)
from .errors import NotFoundError
from .models import Account, ContactVerification, Lead
from .schemas import (
    BatchVerifyResult,
    ContactVerificationOut,
    VerifyRawRequest,
    VerifyRawResult,
)
from .tooling import CRMToolkit

ACTOR = "lead_verifier"

# RFC-ish local@domain — not a full RFC 5322 parser; good enough for CRM hygiene.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
_EMAIL_EXTRACT_RE = re.compile(
    r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+"
)
_URL_EXTRACT_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_PLACEHOLDER_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "invalid",
        "localhost",
        "test",
        "test.com",
    }
)
_DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "tempmail.com",
        "throwaway.email",
        "yopmail.com",
        "10minutemail.com",
        "trashmail.com",
        "sharklasers.com",
        "getnada.com",
    }
)
_ROLE_LOCAL_PARTS = frozenset(
    {
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "abuse",
        "postmaster",
        "mailer-daemon",
        "bounce",
        "unsubscribe",
    }
)
_DEAD_HTTP_STATUSES = frozenset({404, 410})
_INTERSTITIAL_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "checking your browser",
    "cloudflare",
    "access denied",
    "please wait",
)


class DnsResolver(Protocol):
  def resolve(self, qname: str, rdtype: str) -> list[Any]: ...


@dataclass
class EmailCheckResult:
    status: ContactVerificationStatus
    reasons: list[str] = field(default_factory=list)
    dns_summary: dict[str, Any] | None = None
    mx_summary: dict[str, Any] | None = None


@dataclass
class UrlCheckResult:
    status: ContactVerificationStatus
    reasons: list[str] = field(default_factory=list)
    http_status: int | None = None
    dns_summary: dict[str, Any] | None = None


def _default_dns_resolver() -> DnsResolver:
    import dns.resolver

    return dns.resolver.Resolver()


def extract_contacts(
    lead: Lead,
    *,
    account_website: str | None = None,
) -> list[tuple[str, ContactKind]]:
    """Pull emails and contact URLs from a lead row — never invent addresses."""
    emails: set[str] = set()
    urls: set[str] = set()

    if lead.email:
        normalized = lead.email.strip().lower()
        if normalized:
            emails.add(normalized)

    payload = lead.raw_payload if isinstance(lead.raw_payload, dict) else {}
    for key in ("url", "website", "contact_url", "page_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            urls.add(value.strip())

    if account_website:
        urls.add(account_website.strip())

    page_text_parts: list[str] = []
    for key in ("page_text", "markdown", "body", "search_snippet"):
        value = payload.get(key)
        if isinstance(value, str):
            page_text_parts.append(value)
    if lead.enrichment_summary:
        page_text_parts.append(lead.enrichment_summary)

    combined_text = "\n".join(page_text_parts)
    for match in _EMAIL_EXTRACT_RE.findall(combined_text):
        emails.add(match.lower())
    for match in _URL_EXTRACT_RE.findall(combined_text):
        urls.add(match.rstrip(".,;"))

    contacts: list[tuple[str, ContactKind]] = []
    for email in sorted(emails):
        contacts.append((email, ContactKind.EMAIL))
    for url in sorted(urls):
        contacts.append((url, ContactKind.URL))
    return contacts


def check_email_syntax(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def check_email(
    email: str,
    *,
    resolver: DnsResolver | None = None,
) -> EmailCheckResult:
    """Defensive email checks: syntax, DNS, MX — no SMTP or RCPT TO."""
    reasons: list[str] = []
    email = email.strip().lower()
    local, _, domain = email.partition("@")

    if not check_email_syntax(email):
        return EmailCheckResult(
            status=ContactVerificationStatus.INVALID,
            reasons=["email syntax invalid"],
        )

    if domain in _PLACEHOLDER_DOMAINS:
        return EmailCheckResult(
            status=ContactVerificationStatus.INVALID,
            reasons=[f"placeholder domain: {domain}"],
        )

    if is_dummy_documentation_email(email):
        return EmailCheckResult(
            status=ContactVerificationStatus.INVALID,
            reasons=["documentation dummy email local-part (not a prospect)"],
        )

    if local in _ROLE_LOCAL_PARTS:
        reasons.append("role or no-reply local-part — risky for outreach")

    if domain in _DISPOSABLE_DOMAINS:
        reasons.append(f"disposable provider: {domain}")

    dns_summary: dict[str, Any] = {"domain": domain}
    mx_summary: dict[str, Any] | None = None

    if resolver is None:
        resolver = _default_dns_resolver()

    has_address = False
    for rdtype in ("A", "AAAA", "CNAME"):
        try:
            answers = resolver.resolve(domain, rdtype)
            if answers:
                has_address = True
                dns_summary[rdtype.lower()] = [str(r) for r in answers][:5]
                break
        except Exception:
            continue

    if not has_address:
        try:
            resolver.resolve(domain, "A")
        except Exception as exc:
            exc_name = type(exc).__name__.lower()
            if "nxdomain" in exc_name or "noanswer" in exc_name:
                return EmailCheckResult(
                    status=ContactVerificationStatus.INVALID,
                    reasons=["domain does not exist (NXDOMAIN)"],
                    dns_summary={"domain": domain, "error": type(exc).__name__},
                )
            return EmailCheckResult(
                status=ContactVerificationStatus.UNKNOWN,
                reasons=[f"DNS lookup failed: {type(exc).__name__}"],
                dns_summary={"domain": domain, "error": type(exc).__name__},
            )

    try:
        mx_answers = resolver.resolve(domain, "MX")
        mx_records = []
        null_mx = False
        for rdata in mx_answers:
            exchange = str(rdata.exchange).rstrip(".")
            preference = int(rdata.preference)
            mx_records.append({"host": exchange, "preference": preference})
            if preference == 0 and exchange == "":
                null_mx = True
        mx_summary = {"records": mx_records}

        if null_mx or any(r["host"] == "." for r in mx_records):
            return EmailCheckResult(
                status=ContactVerificationStatus.INVALID,
                reasons=[
                    "null MX (RFC 7505) — domain does not accept mail",
                ],
                dns_summary=dns_summary,
                mx_summary=mx_summary,
            )

        if not mx_records:
            return EmailCheckResult(
                status=ContactVerificationStatus.INVALID,
                reasons=["no MX records — domain cannot receive mail"],
                dns_summary=dns_summary,
                mx_summary=mx_summary,
            )

        mx_hosts = ", ".join(r["host"] for r in mx_records[:3])
        reasons.append(
            f"MX present ({mx_hosts}) — domain can receive mail; "
            "mailbox deliverability not verified (no SMTP probe)"
        )

    except Exception as exc:
        exc_name = type(exc).__name__.lower()
        if "noanswer" in exc_name or "nxdomain" in exc_name:
            return EmailCheckResult(
                status=ContactVerificationStatus.INVALID,
                reasons=["no MX records — domain cannot receive mail"],
                dns_summary=dns_summary,
                mx_summary={"error": type(exc).__name__},
            )
        return EmailCheckResult(
            status=ContactVerificationStatus.UNKNOWN,
            reasons=[f"MX lookup failed: {type(exc).__name__}"],
            dns_summary=dns_summary,
            mx_summary={"error": type(exc).__name__},
        )

    for txt_type in ("SPF", "DMARC"):
        try:
            txt_answers = resolver.resolve(domain, "TXT")
            for rdata in txt_answers:
                text = str(rdata).strip('"')
                if txt_type == "SPF" and text.lower().startswith("v=spf1"):
                    reasons.append("SPF record present (informational)")
                    break
                if txt_type == "DMARC" and text.lower().startswith("v=dmarc1"):
                    reasons.append("DMARC record present (informational)")
                    break
        except Exception:
            pass

    if domain in _DISPOSABLE_DOMAINS or local in _ROLE_LOCAL_PARTS:
        return EmailCheckResult(
            status=ContactVerificationStatus.RISKY,
            reasons=reasons,
            dns_summary=dns_summary,
            mx_summary=mx_summary,
        )

    return EmailCheckResult(
        status=ContactVerificationStatus.VALID,
        reasons=reasons,
        dns_summary=dns_summary,
        mx_summary=mx_summary,
    )


def check_url(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolver: DnsResolver | None = None,
) -> UrlCheckResult:
    """HTTP(S) HEAD/GET with redirect follow — flag dead links and interstitials."""
    reasons: list[str] = []
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return UrlCheckResult(
            status=ContactVerificationStatus.INVALID,
            reasons=["invalid or unsupported URL scheme"],
        )

    domain = parsed.netloc.split(":")[0].lower()
    dns_summary: dict[str, Any] = {"domain": domain}

    if resolver is None:
        resolver = _default_dns_resolver()

    has_address = False
    for rdtype in ("A", "AAAA", "CNAME"):
        try:
            answers = resolver.resolve(domain, rdtype)
            if answers:
                has_address = True
                dns_summary[rdtype.lower()] = [str(r) for r in answers][:3]
                break
        except Exception as exc:
            exc_name = type(exc).__name__
            if "NXDOMAIN" in exc_name:
                return UrlCheckResult(
                    status=ContactVerificationStatus.INVALID,
                    reasons=["domain does not exist (NXDOMAIN)"],
                    dns_summary={"domain": domain, "error": exc_name},
                )

    if not has_address:
        reasons.append("domain DNS resolution uncertain")

    owns_client = client is None
    if owns_client:
        client = httpx.Client(follow_redirects=True, timeout=15.0)

    try:
        response = client.request("HEAD", url)
        if response.status_code >= 400 or response.status_code == 405:
            response = client.get(url)
        http_status = response.status_code
        body = ""
        if response.status_code < 400:
            # Some servers return empty on HEAD; GET for title/body checks.
            if response.request.method == "HEAD":
                get_resp = client.get(url)
                http_status = get_resp.status_code
                body = get_resp.text[:4000]
            else:
                body = response.text[:4000]

        title = _extract_html_title(body) if body else None

        if http_status in _DEAD_HTTP_STATUSES:
            return UrlCheckResult(
                status=ContactVerificationStatus.INVALID,
                reasons=[f"HTTP {http_status} — page gone"],
                http_status=http_status,
                dns_summary=dns_summary,
            )

        if http_status >= 500:
            return UrlCheckResult(
                status=ContactVerificationStatus.RISKY,
                reasons=[f"HTTP {http_status} — server error"],
                http_status=http_status,
                dns_summary=dns_summary,
            )

        if http_status >= 400:
            return UrlCheckResult(
                status=ContactVerificationStatus.RISKY,
                reasons=[f"HTTP {http_status} — client error"],
                http_status=http_status,
                dns_summary=dns_summary,
            )

        if title and _is_interstitial_title(title):
            return UrlCheckResult(
                status=ContactVerificationStatus.RISKY,
                reasons=[f"interstitial or bot-check page: {title[:80]}"],
                http_status=http_status,
                dns_summary=dns_summary,
            )

        if body is not None and len(body.strip()) == 0 and http_status == 200:
            return UrlCheckResult(
                status=ContactVerificationStatus.RISKY,
                reasons=["empty response body"],
                http_status=http_status,
                dns_summary=dns_summary,
            )

        reasons.append(f"HTTP {http_status} — reachable")
        return UrlCheckResult(
            status=ContactVerificationStatus.VALID,
            reasons=reasons,
            http_status=http_status,
            dns_summary=dns_summary,
        )

    except httpx.ConnectError:
        return UrlCheckResult(
            status=ContactVerificationStatus.INVALID,
            reasons=["connection failed — host unreachable"],
            dns_summary=dns_summary,
        )
    except httpx.TimeoutException:
        return UrlCheckResult(
            status=ContactVerificationStatus.UNKNOWN,
            reasons=["HTTP request timed out"],
            dns_summary=dns_summary,
        )
    except httpx.HTTPError as exc:
        return UrlCheckResult(
            status=ContactVerificationStatus.UNKNOWN,
            reasons=[f"HTTP error: {exc}"],
            dns_summary=dns_summary,
        )
    finally:
        if owns_client and client is not None:
            client.close()


def _extract_html_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _is_interstitial_title(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in _INTERSTITIAL_TITLE_MARKERS)


def _upsert_verification(
    session: Session,
    *,
    lead_id: int,
    contact: str,
    contact_kind: ContactKind,
    status: ContactVerificationStatus,
    reasons: list[str],
    checked_at: datetime,
    dns_summary: dict[str, Any] | None,
    mx_summary: dict[str, Any] | None,
    http_status: int | None,
) -> ContactVerification:
    row = session.scalar(
        select(ContactVerification).where(
            ContactVerification.lead_id == lead_id,
            ContactVerification.contact == contact,
        )
    )
    if row is None:
        row = ContactVerification(
            lead_id=lead_id,
            contact=contact,
            contact_kind=contact_kind,
        )
        session.add(row)
    row.status = status
    row.reasons = reasons
    row.checked_at = checked_at
    row.dns_summary = dns_summary
    row.mx_summary = mx_summary
    row.http_status = http_status
    session.flush()
    return row


def _should_disqualify(results: list[ContactVerification]) -> bool:
    if not results:
        return False
    clearly_dead_reasons = (
        "no mx records",
        "null mx",
        "nxdomain",
        "domain does not exist",
        "page gone",
        "connection failed",
        "placeholder domain",
        "documentation dummy email",
        "email syntax invalid",
    )
    all_invalid = all(r.status == ContactVerificationStatus.INVALID for r in results)
    if not all_invalid:
        return False
    for row in results:
        reason_text = " ".join(row.reasons or []).lower()
        if any(marker in reason_text for marker in clearly_dead_reasons):
            return True
    return False


def verify_lead(
    lead_id: int,
    *,
    http_client: httpx.Client | None = None,
    resolver: DnsResolver | None = None,
) -> list[ContactVerificationOut]:
    """Verify all extractable contacts on a lead and persist results."""
    crm = CRMToolkit(actor=ACTOR)
    crm.record_heartbeat(
        status=AgentStatus.THINKING,
        task=f"extracting contacts for lead {lead_id}",
    )

    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            crm.record_heartbeat(status=AgentStatus.IDLE)
            raise NotFoundError(f"Lead {lead_id} not found")

        account_website: str | None = None
        if lead.account_id:
            account = session.get(Account, lead.account_id)
            if account and account.website:
                account_website = account.website

        contacts = extract_contacts(lead, account_website=account_website)
        lead_source = lead.source
        lead_payload = dict(lead.raw_payload or {}) if isinstance(lead.raw_payload, dict) else {}
        if not contacts:
            crm.record_heartbeat(status=AgentStatus.IDLE)
            crm.log_note(
                f"No contacts to verify on lead {lead_id}",
                lead_id=lead_id,
                type=ActivityType.VERIFIED,
                payload={"contacts_checked": 0},
            )
            return []

    results: list[ContactVerificationOut] = []
    checked_at = datetime.now(UTC)

    for contact, kind in contacts:
        crm.record_heartbeat(
            status=AgentStatus.WORKING,
            task=f"checking {kind.value}: {contact[:60]}",
        )
        if kind == ContactKind.EMAIL:
            if lead_source == LeadSource.CONTACT:
                found_on = lead_payload.get("found_on")
                source_urls = (
                    [url for url in found_on if isinstance(url, str)]
                    if isinstance(found_on, list)
                    else []
                )
                if not is_relevant_contact(contact, source_urls):
                    check = EmailCheckResult(
                        status=ContactVerificationStatus.INVALID,
                        reasons=[
                            "contact failed source-context quality filter "
                            "(irrelevant page or generic support identity)",
                        ],
                    )
                    dns_summary = None
                    mx_summary = None
                    http_status = None
                else:
                    check = check_email(contact, resolver=resolver)
                    dns_summary = check.dns_summary
                    mx_summary = check.mx_summary
                    http_status = None
            else:
                check = check_email(contact, resolver=resolver)
                dns_summary = check.dns_summary
                mx_summary = check.mx_summary
                http_status = None
        else:
            check = check_url(contact, client=http_client, resolver=resolver)
            dns_summary = check.dns_summary
            mx_summary = None
            http_status = check.http_status

        with session_scope() as session:
            row = _upsert_verification(
                session,
                lead_id=lead_id,
                contact=contact,
                contact_kind=kind,
                status=check.status,
                reasons=check.reasons,
                checked_at=checked_at,
                dns_summary=dns_summary,
                mx_summary=mx_summary,
                http_status=http_status,
            )
            results.append(ContactVerificationOut.model_validate(row))

    summary_parts = [f"{r.contact_kind.value} {r.contact}: {r.status.value}" for r in results]
    crm.log_note(
        f"Verified {len(results)} contact(s) on lead {lead_id}: "
        + "; ".join(summary_parts[:5]),
        lead_id=lead_id,
        type=ActivityType.VERIFIED,
        payload={
            "contacts_checked": len(results),
            "statuses": {r.contact: r.status.value for r in results},
        },
    )

    if _should_disqualify(
        [
            ContactVerification(
                lead_id=lead_id,
                contact=r.contact,
                contact_kind=r.contact_kind,
                status=r.status,
                reasons=r.reasons,
                checked_at=r.checked_at,
            )
            for r in results
        ]
    ):
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead and lead.status != LeadStatus.DISQUALIFIED:
                lead.status = LeadStatus.DISQUALIFIED
        crm.log_note(
            f"Lead {lead_id} disqualified — all contacts clearly dead",
            lead_id=lead_id,
            type=ActivityType.NOTE,
        )

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return results


def count_unverified_hunter_leads() -> int:
    """Count hunter leads that have no contact verification rows yet."""
    with session_scope() as session:
        verified_lead_ids = select(ContactVerification.lead_id).distinct()
        stmt = (
            select(func.count())
            .select_from(Lead)
            .where(Lead.source == LeadSource.HUNTER)
            .where(Lead.id.not_in(verified_lead_ids))
        )
        return int(session.scalar(stmt) or 0)


def seed_verify_jobs_for_unverified(*, limit: int = 50) -> int:
    """Enqueue verify_lead jobs for email leads lacking verification rows."""
    from .job_store import enqueue_verify_lead_job

    if limit <= 0:
        return 0

    with session_scope() as session:
        verified_lead_ids = select(ContactVerification.lead_id).distinct()
        stmt = (
            select(Lead.id, Lead.email)
            .where(Lead.email.is_not(None))
            .where(func.length(func.trim(Lead.email)) > 0)
            .where(Lead.id.not_in(verified_lead_ids))
            .order_by(Lead.created_at.asc(), Lead.id.asc())
            .limit(limit * 3)
        )
        candidates = list(session.execute(stmt))

    enqueued = 0
    for lead_id, email in candidates:
        if enqueued >= limit:
            break
        if not email:
            continue
        normalized = email.strip().lower()
        if is_role_inbox_email(normalized):
            continue
        if is_placeholder_email(normalized):
            continue
        if is_filename_as_email(normalized):
            continue
        if is_dummy_documentation_email(normalized):
            continue
        if enqueue_verify_lead_job(lead_id):
            enqueued += 1
    return enqueued


def verify_batch_unverified(
    *,
    limit: int = 50,
    http_client: httpx.Client | None = None,
    resolver: DnsResolver | None = None,
) -> BatchVerifyResult:
    """Verify hunter leads that have no verification records yet."""
    crm = CRMToolkit(actor=ACTOR)
    crm.record_heartbeat(
        status=AgentStatus.THINKING,
        task=f"batch verify up to {limit} unverified hunter leads",
    )

    lead_ids: list[int] = []
    with session_scope() as session:
        verified_lead_ids = select(ContactVerification.lead_id).distinct()
        stmt = (
            select(Lead.id)
            .where(Lead.source == LeadSource.HUNTER)
            .where(Lead.id.not_in(verified_lead_ids))
            .order_by(Lead.created_at.desc())
            .limit(limit)
        )
        lead_ids = list(session.scalars(stmt))

    verified: list[int] = []
    errors: list[str] = []
    total_contacts = 0

    for lead_id in lead_ids:
        try:
            results = verify_lead(lead_id, http_client=http_client, resolver=resolver)
            verified.append(lead_id)
            total_contacts += len(results)
        except NotFoundError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — batch must continue
            errors.append(f"lead {lead_id}: {exc}")

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return BatchVerifyResult(
        leads_processed=len(verified),
        contacts_verified=total_contacts,
        lead_ids=verified,
        errors=errors,
    )


def verify_raw(
    payload: VerifyRawRequest,
    *,
    http_client: httpx.Client | None = None,
    resolver: DnsResolver | None = None,
) -> VerifyRawResult:
    """Verify a raw email or URL without a lead record."""
    crm = CRMToolkit(actor=ACTOR)
    checked_at = datetime.now(UTC)
    results: list[ContactVerificationOut] = []

    if payload.email:
        crm.record_heartbeat(status=AgentStatus.WORKING, task=f"checking email: {payload.email}")
        check = check_email(payload.email.strip().lower(), resolver=resolver)
        results.append(
            ContactVerificationOut(
                id=0,
                lead_id=None,
                contact=payload.email.strip().lower(),
                contact_kind=ContactKind.EMAIL,
                status=check.status,
                reasons=check.reasons,
                checked_at=checked_at,
                dns_summary=check.dns_summary,
                mx_summary=check.mx_summary,
                http_status=None,
                created_at=checked_at,
                updated_at=checked_at,
            )
        )

    if payload.url:
        crm.record_heartbeat(status=AgentStatus.WORKING, task=f"checking url: {payload.url}")
        check = check_url(payload.url.strip(), client=http_client, resolver=resolver)
        results.append(
            ContactVerificationOut(
                id=0,
                lead_id=None,
                contact=payload.url.strip(),
                contact_kind=ContactKind.URL,
                status=check.status,
                reasons=check.reasons,
                checked_at=checked_at,
                dns_summary=check.dns_summary,
                mx_summary=None,
                http_status=check.http_status,
                created_at=checked_at,
                updated_at=checked_at,
            )
        )

    crm.record_heartbeat(status=AgentStatus.IDLE)
    return VerifyRawResult(results=results)


def list_verifications(lead_id: int) -> list[ContactVerificationOut]:
    """Return stored verification records for a lead."""
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise NotFoundError(f"Lead {lead_id} not found")
        stmt = (
            select(ContactVerification)
            .where(ContactVerification.lead_id == lead_id)
            .order_by(ContactVerification.checked_at.desc())
        )
        return [ContactVerificationOut.model_validate(row) for row in session.scalars(stmt)]
