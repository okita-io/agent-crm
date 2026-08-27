"""Tests for Lead / Contact Verifier (mocked DNS and HTTP)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_crm.api import app
from agent_crm.config import get_settings
from agent_crm.db import init_db, reset_engine
from agent_crm.enums import (
    ContactKind,
    ContactVerificationStatus,
    LeadSource,
    LeadStatus,
)
from agent_crm.models import Lead
from agent_crm.schemas import LeadCreate
from agent_crm.tooling import CRMToolkit
from agent_crm.verifier import (
    check_email,
    check_url,
    extract_contacts,
    list_verifications,
    verify_batch_unverified,
    verify_lead,
)


class DnsError(Exception):
    pass


class Nxdomain(DnsError):
    pass


class NoAnswer(DnsError):
    pass


@dataclass
class FakeMx:
    exchange: str
    preference: int


class FakeResolver:
    """Injectable DNS resolver for tests."""

    def __init__(self, records: dict[tuple[str, str], list]) -> None:
        self._records = records

    def resolve(self, qname: str, rdtype: str) -> list:
        key = (qname.lower(), rdtype.upper())
        if key not in self._records:
            if rdtype.upper() == "MX":
                raise NoAnswer("no answer")
            raise Nxdomain("nxdomain")
        return self._records[key]


def _valid_mx_resolver() -> FakeResolver:
    return FakeResolver(
        {
            ("acme.com", "A"): ["192.0.2.1"],
            ("acme.com", "MX"): [FakeMx("mail.acme.com.", 10)],
            ("acme.com", "TXT"): ['"v=spf1 include:_spf.google.com ~all"'],
        }
    )


def test_check_email_valid_mx() -> None:
    result = check_email("jane@acme.com", resolver=_valid_mx_resolver())
    assert result.status == ContactVerificationStatus.VALID
    assert any("MX present" in r for r in result.reasons)
    assert any("mailbox deliverability not verified" in r for r in result.reasons)


def test_check_email_null_mx() -> None:
    resolver = FakeResolver(
        {
            ("reject.testcorp", "A"): ["192.0.2.1"],
            ("reject.testcorp", "MX"): [FakeMx(".", 0)],
        }
    )
    result = check_email("user@reject.testcorp", resolver=resolver)
    assert result.status == ContactVerificationStatus.INVALID
    assert any("null MX" in r for r in result.reasons)


def test_check_email_nxdomain() -> None:
    resolver = FakeResolver({})
    result = check_email("user@ghost-domain.nx", resolver=resolver)
    assert result.status == ContactVerificationStatus.INVALID
    assert any("NXDOMAIN" in r or "does not exist" in r for r in result.reasons)


def test_check_email_no_mx() -> None:
    resolver = FakeResolver({("exists.testcorp", "A"): ["192.0.2.1"]})
    result = check_email("user@exists.testcorp", resolver=resolver)
    assert result.status == ContactVerificationStatus.INVALID
    assert any("no mx" in r.lower() for r in result.reasons)


def test_check_email_disposable() -> None:
    resolver = FakeResolver(
        {
            ("mailinator.com", "A"): ["192.0.2.1"],
            ("mailinator.com", "MX"): [FakeMx("mail.mailinator.com.", 10)],
        }
    )
    result = check_email("throwaway@mailinator.com", resolver=resolver)
    assert result.status == ContactVerificationStatus.RISKY
    assert any("disposable" in r for r in result.reasons)


def test_check_email_placeholder_domain() -> None:
    result = check_email("test@example.com", resolver=FakeResolver({}))
    assert result.status == ContactVerificationStatus.INVALID
    assert any("placeholder" in r for r in result.reasons)


@pytest.mark.parametrize(
    "email",
    [
        "info@icarmenia.am",
        "hello@x.com",
        "careers@crescendo.ai",
        "noreply@acme.com",
        "our.team@vocabulary.com",
        "team.of.english.language.specialists@vocabulary.com",
    ],
)
def test_check_email_role_inbox_invalid_despite_mx(email: str) -> None:
    result = check_email(email, resolver=_valid_mx_resolver())
    assert result.status == ContactVerificationStatus.INVALID
    assert result.status != ContactVerificationStatus.VALID
    assert any("role" in r.lower() or "shared inbox" in r.lower() for r in result.reasons)


def test_check_email_info_at_example_domain_is_invalid() -> None:
    result = check_email("info@example.com", resolver=_valid_mx_resolver())
    assert result.status == ContactVerificationStatus.INVALID
    assert result.status != ContactVerificationStatus.VALID


def test_check_email_placeholder_local_invalid_despite_mx() -> None:
    result = check_email("name@domain.com", resolver=_valid_mx_resolver())
    assert result.status == ContactVerificationStatus.INVALID
    assert any("placeholder" in r.lower() for r in result.reasons)


def test_check_email_person_local_can_be_valid_with_mx() -> None:
    result = check_email("jane.doe@acme.com", resolver=_valid_mx_resolver())
    assert result.status == ContactVerificationStatus.VALID


def test_check_email_role_address() -> None:
    resolver = FakeResolver(
        {
            ("acme.com", "A"): ["192.0.2.1"],
            ("acme.com", "MX"): [FakeMx("mail.acme.com.", 10)],
        }
    )
    result = check_email("noreply@acme.com", resolver=resolver)
    assert result.status == ContactVerificationStatus.INVALID
    assert any("role" in r.lower() or "shared inbox" in r.lower() for r in result.reasons)


def test_check_url_dead_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    resolver = FakeResolver({("dead.example", "A"): ["192.0.2.1"]})
    result = check_url("https://dead.example/gone", client=client, resolver=resolver)
    assert result.status == ContactVerificationStatus.INVALID
    assert result.http_status == 404
    assert any("gone" in r.lower() or "404" in r for r in result.reasons)
    client.close()


def test_check_url_reachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><title>Acme Corp</title><body>Hello</body></html>",
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    resolver = FakeResolver({("acme.com", "A"): ["192.0.2.1"]})
    result = check_url("https://acme.com", client=client, resolver=resolver)
    assert result.status == ContactVerificationStatus.VALID
    assert result.http_status == 200
    client.close()


def test_extract_contacts_does_not_invent_email(db_url) -> None:
    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(
            name="No Email Co",
            company="No Email Co",
            source=LeadSource.HUNTER,
            raw_payload={"url": "https://noemail.example/page", "search_snippet": "A studio"},
        )
    )
    from agent_crm.db import session_scope

    with session_scope() as session:
        orm_lead = session.get(Lead, lead.id)
        contacts = extract_contacts(orm_lead)
    kinds = {kind for _, kind in contacts}
    emails = [c for c, k in contacts if k == ContactKind.EMAIL]
    urls = [c for c, k in contacts if k == ContactKind.URL]
    assert ContactKind.URL in kinds
    assert "https://noemail.example/page" in urls
    assert emails == []


def test_extract_contacts_from_email_and_payload(db_url) -> None:
    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(
            email="Contact@Acme.COM",
            source=LeadSource.HUNTER,
            raw_payload={
                "url": "https://acme.com",
                "page_text": "Reach us at support@acme.com for help.",
            },
        )
    )
    from agent_crm.db import session_scope

    with session_scope() as session:
        orm_lead = session.get(Lead, lead.id)
        contacts = extract_contacts(orm_lead)
    emails = sorted(c for c, k in contacts if k == ContactKind.EMAIL)
    assert "contact@acme.com" in emails
    assert "support@acme.com" in emails


def test_verify_lead_persists_and_notes(db_url) -> None:
    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(
            email="jane@acme.com",
            source=LeadSource.HUNTER,
            raw_payload={"url": "https://acme.com"},
        )
    )

    with patch("agent_crm.verifier._default_dns_resolver", return_value=_valid_mx_resolver()):
        with patch("agent_crm.verifier.check_url") as mock_url:
            from agent_crm.verifier import UrlCheckResult

            mock_url.return_value = UrlCheckResult(
                status=ContactVerificationStatus.VALID,
                reasons=["HTTP 200 — reachable"],
                http_status=200,
            )
            results = verify_lead(lead.id, resolver=_valid_mx_resolver())

    assert len(results) == 2
    stored = list_verifications(lead.id)
    assert len(stored) == 2
    activities = crm.list_activities(lead.id)
    assert any(a.type.value == "verified" for a in activities)


def test_verify_lead_disqualifies_dead_contacts(db_url) -> None:
    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(
            email="user@ghost-domain.nx",
            source=LeadSource.HUNTER,
        )
    )
    resolver = FakeResolver({})
    results = verify_lead(lead.id, resolver=resolver)
    assert results[0].status == ContactVerificationStatus.INVALID
    updated = crm.get_lead(lead.id)
    assert updated.status == LeadStatus.DISQUALIFIED


def test_verify_batch_unverified(db_url) -> None:
    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(email="jane@acme.com", source=LeadSource.HUNTER)
    )
    crm.create_lead(LeadCreate(email="other@form.example", source=LeadSource.FORM))

    with patch("agent_crm.verifier.verify_lead") as mock_verify:
        from agent_crm.schemas import ContactVerificationOut
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        mock_verify.return_value = [
            ContactVerificationOut(
                id=1,
                lead_id=lead.id,
                contact="jane@acme.com",
                contact_kind=ContactKind.EMAIL,
                status=ContactVerificationStatus.VALID,
                reasons=["ok"],
                checked_at=now,
                dns_summary=None,
                mx_summary=None,
                http_status=None,
                created_at=now,
                updated_at=now,
            )
        ]
        result = verify_batch_unverified(limit=10)

    assert result.leads_processed == 1
    assert lead.id in result.lead_ids


def test_verify_lead_api(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "verify-api.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(email="jane@acme.com", source=LeadSource.HUNTER)
    )

    client = TestClient(app)
    with patch("agent_crm.api.verify_lead") as mock_verify:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        from agent_crm.schemas import ContactVerificationOut

        mock_verify.return_value = [
            ContactVerificationOut(
                id=1,
                lead_id=lead.id,
                contact="jane@acme.com",
                contact_kind=ContactKind.EMAIL,
                status=ContactVerificationStatus.VALID,
                reasons=["ok"],
                checked_at=now,
                dns_summary=None,
                mx_summary=None,
                http_status=None,
                created_at=now,
                updated_at=now,
            )
        ]
        response = client.post(f"/leads/{lead.id}/verify")
    assert response.status_code == 200
    assert response.json()[0]["status"] == "valid"
    reset_engine()
    get_settings.cache_clear()


def test_verify_batch_api(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "verify-batch.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    client = TestClient(app)
    with patch("agent_crm.api.verify_batch_unverified") as mock_batch:
        from agent_crm.schemas import BatchVerifyResult

        mock_batch.return_value = BatchVerifyResult(
            leads_processed=2,
            contacts_verified=3,
            lead_ids=[1, 2],
            errors=[],
        )
        response = client.post("/verify/batch", json={"limit": 10})
    assert response.status_code == 200
    assert response.json()["leads_processed"] == 2
    reset_engine()
    get_settings.cache_clear()


def test_get_verifications_api(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "verify-get.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()
    reset_engine()
    init_db()

    crm = CRMToolkit(actor="lead_intake")
    lead = crm.create_lead(
        LeadCreate(email="jane@acme.com", source=LeadSource.HUNTER)
    )

    with patch("agent_crm.verifier._default_dns_resolver", return_value=_valid_mx_resolver()):
        verify_lead(lead.id, resolver=_valid_mx_resolver())

    client = TestClient(app)
    response = client.get(f"/leads/{lead.id}/verifications")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["contact"] == "jane@acme.com"
    reset_engine()
    get_settings.cache_clear()
