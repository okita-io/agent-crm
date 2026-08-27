"""Tests for deferred review-fix items (budget, decode, uncertain, account dedupe)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_crm.contact_store import ContactExtractionBudget, process_scraped_page_contacts
from agent_crm.db import session_scope
from agent_crm.enums import AgentJobKind, Brand, TopicalRelevanceVerdict
from agent_crm.hunt_loop import _filter_relevant_hunt_results
from agent_crm.job_dispatcher import execute_job
from agent_crm.job_store import enqueue_decode_email_job
from agent_crm.models import Account, AgentJob
from agent_crm.research import _maybe_write_account_note
from agent_crm.searxng_client import SearchResult
from agent_crm.tooling import CRMToolkit
from sqlalchemy import func, select


def test_process_scraped_page_respects_enrichment_budget(db_url) -> None:
    markdown = (
        "Jane Doe jane@novastudio.com\n"
        "Bob Smith bob@novastudio.com\n"
    )
    budget = ContactExtractionBudget(
        social_lookups_remaining=0,
        enrichments_remaining=1,
        spark_enrichments_remaining=0,
        spark_decode_remaining=0,
    )
    profiles = process_scraped_page_contacts(
        markdown=markdown,
        source_url="https://novastudio.com/team",
        brand=Brand.TACTIC_STUDIO,
        budget=budget,
    )
    assert len(profiles) == 2
    assert budget.enrichments_remaining == 0

    with session_scope() as session:
        enrich_jobs = list(
            session.scalars(
                select(AgentJob).where(AgentJob.kind == AgentJobKind.ENRICH_CONTACT)
            )
        )
    assert len(enrich_jobs) == 1


def test_uncertain_hunt_results_are_not_kept_for_scrape(db_url) -> None:
    hits = [
        SearchResult(
            title="Maybe",
            url="https://maybe.example/post",
            snippet="unclear",
        )
    ]
    assessment = MagicMock()
    assessment.verdict = TopicalRelevanceVerdict.UNCERTAIN
    assessment.reason = "unclear"
    with patch(
        "agent_crm.hunt_loop.assess_topical_relevance",
        return_value=assessment,
    ), patch("agent_crm.hunt_loop.upsert_url_topic_relevance") as upsert, patch(
        "agent_crm.hunt_loop.enqueue_topical_relevance_job",
        return_value=True,
    ) as enqueue:
        kept = _filter_relevant_hunt_results(
            hits,
            brand=Brand.MIDNIGHTSATIN,
            query="romance forums",
        )
    assert kept == []
    upsert.assert_called_once()
    enqueue.assert_called_once()


def test_decode_email_job_upserts_decoded_contact(db_url) -> None:
    assert enqueue_decode_email_job(
        source_url="https://novastudio.com/contact",
        obfuscation_span="jane at novastudio dot com",
    )
    with session_scope() as session:
        job = session.scalar(select(AgentJob).where(AgentJob.kind == AgentJobKind.DECODE_EMAIL))
        assert job is not None
        job_id = job.id
        payload = dict(job.payload or {})

    with patch(
        "agent_crm.contact_extractor.decode_obfuscated_emails_spark",
        return_value=[("jane@novastudio.com", "Jane")],
    ):
        execute_job(job_id, AgentJobKind.DECODE_EMAIL, payload)

    from agent_crm.models import ContactProfile

    with session_scope() as session:
        row = session.scalar(
            select(ContactProfile).where(ContactProfile.email == "jane@novastudio.com")
        )
        assert row is not None
        assert row.name == "Jane"


def test_research_account_note_dedupes_by_website(db_url) -> None:
    crm = CRMToolkit(actor="test")
    url = "https://partners.example.org/about"
    _maybe_write_account_note(
        crm,
        url,
        "Partners Org",
        "First summary about the nonprofit.",
        {"org_name": "Partners Org", "mission": "Help people"},
    )
    _maybe_write_account_note(
        crm,
        url,
        "Partners Org",
        "Updated summary about the nonprofit partnership.",
        {"org_name": "Partners Org", "mission": "Help people more"},
    )
    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(Account))
        assert count == 1
        row = session.scalar(select(Account))
        assert row is not None
        assert "Updated summary" in (row.notes or "")
