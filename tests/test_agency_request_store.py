"""Tests for operator command queue."""

from __future__ import annotations

from agent_crm.agency.request_store import (
    claim_next_pending_agency_request,
    create_agency_request,
    list_agency_requests,
    mark_agency_request_completed,
)
from agent_crm.enums import AgencyRequestStatus


def test_create_and_list_agency_request(db_url) -> None:
    row = create_agency_request("Pause research and hunt tactic-studio forums")
    assert row.id > 0
    assert row.status == AgencyRequestStatus.PENDING

    rows = list_agency_requests(limit=10)
    assert rows[-1].message == "Pause research and hunt tactic-studio forums"


def test_claim_and_complete_agency_request(db_url) -> None:
    row = create_agency_request("Enable hunter")
    claimed = claim_next_pending_agency_request()
    assert claimed is not None
    assert claimed.id == row.id
    assert claimed.status == AgencyRequestStatus.PROCESSING

    mark_agency_request_completed(
        claimed.id,
        reply="Hunter enabled.",
        actions=[{"type": "set_agent_enabled", "ok": True}],
    )
    finished = list_agency_requests(limit=1)[0]
    assert finished.status == AgencyRequestStatus.COMPLETED
    assert finished.reply == "Hunter enabled."

    assert claim_next_pending_agency_request() is None
