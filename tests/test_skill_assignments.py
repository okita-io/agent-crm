"""Skill catalog discovery and per-agent assignment persistence."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_crm.agency.commands import execute_action
from agent_crm.api import app
from agent_crm.skill_catalog import BRAND_CONTEXT_ID, list_catalog
from agent_crm.skill_runtime import has_skill, research_competitor_guidance, uses_skill
from agent_crm.skill_store import (
    assign_skill,
    list_agent_skills,
    list_assignments_by_agent,
    unassign_skill,
    unassign_skill_everywhere,
)
from agent_crm.tooling import CRMToolkit


def test_catalog_includes_packs_and_modules() -> None:
    by_id = {record.id: record for record in list_catalog()}
    assert "marketing-agi" in by_id
    assert by_id["marketing-agi"].kind == "pack"
    assert "marketing-agi/positioning" in by_id
    assert by_id["marketing-agi/positioning"].kind == "module"
    assert "open-seo" in by_id
    assert "social-media/voice" in by_id
    assert "aeo-geo" in by_id
    assert BRAND_CONTEXT_ID in by_id
    assert by_id[BRAND_CONTEXT_ID].virtual is True


def test_pack_only_enables_modules() -> None:
    assigned = {"open-seo"}
    assert uses_skill(assigned, "open-seo")
    assert uses_skill(assigned, "open-seo/site-audit")
    assert not uses_skill(assigned, "marketing-agi/hooks")


def test_module_whitelist_does_not_enable_other_modules() -> None:
    assigned = {"marketing-agi", "marketing-agi/positioning"}
    assert uses_skill(assigned, "marketing-agi/positioning")
    assert not uses_skill(assigned, "marketing-agi/competitive")


def test_defaults_seed_once_and_empty_list_is_sticky(db_url) -> None:
    skills = list_agent_skills("research")
    assert "marketing-agi" in skills
    assert "marketing-agi/competitive" in skills
    assert BRAND_CONTEXT_ID in skills

    for skill_id in list(skills):
        unassign_skill("research", skill_id)
    assert list_agent_skills("research") == []

    listed = list_assignments_by_agent()
    assert listed["research"] == []


def test_assign_and_unassign_round_trip(db_url) -> None:
    assign_skill("seo", "marketing-agi/hooks")
    skills = list_agent_skills("seo")
    assert "marketing-agi/hooks" in skills
    assert "open-seo" in skills
    remaining = unassign_skill("seo", "open-seo")
    assert "open-seo" not in remaining
    assert "marketing-agi/hooks" in remaining


def test_unassign_everywhere_leaves_files(db_url) -> None:
    removed = unassign_skill_everywhere("marketing-agi")
    assert removed >= 1
    by_agent = list_assignments_by_agent()
    assert all("marketing-agi" not in skills for skills in by_agent.values())
    from agent_crm.marketing_skill import marketing_skill_root

    assert (marketing_skill_root() / "SKILL.md").is_file()


def test_skills_api_round_trip(db_url) -> None:
    client = TestClient(app)
    catalog = client.get("/skills")
    assert catalog.status_code == 200
    ids = {row["id"] for row in catalog.json()["skills"]}
    assert "marketing-agi" in ids
    assert "marketing-agi/positioning" in ids

    agents = {row["name"]: row for row in client.get("/agents").json()}
    assert "marketing-agi" in agents["research"]["skills"]

    added = client.post(
        "/agents/research/skills",
        json={"skill_id": "social-media/newsletter"},
    )
    assert added.status_code == 200
    assert "social-media/newsletter" in added.json()["skills"]

    removed = client.delete(
        "/agents/research/skills",
        params={"skill_id": "social-media/newsletter"},
    )
    assert removed.status_code == 200
    assert "social-media/newsletter" not in removed.json()["skills"]

    gone = client.delete(
        "/skills/assignments",
        params={"skill_id": "marketing-agi/competitive"},
    )
    assert gone.status_code == 200
    assert gone.json()["removed"] >= 1
    research = client.get("/agents/research/skills").json()
    assert "marketing-agi/competitive" not in research["skills"]


def test_unknown_skill_or_agent_is_404(db_url) -> None:
    client = TestClient(app)
    missing_agent = client.post(
        "/agents/not-a-real-agent/skills",
        json={"skill_id": "marketing-agi"},
    )
    assert missing_agent.status_code == 404
    missing_skill = client.post(
        "/agents/research/skills",
        json={"skill_id": "no-such-pack"},
    )
    assert missing_skill.status_code == 404


def test_toolkit_assign_skill(db_url) -> None:
    crm = CRMToolkit(actor="orchestrator")
    skills = crm.assign_skill("social-media/voice", agent_name="research")
    assert "social-media/voice" in skills
    assert "social-media/voice" in crm.list_agent_skills("research")
    remaining = crm.unassign_skill("social-media/voice", agent_name="research")
    assert "social-media/voice" not in remaining
    catalog = crm.list_skills()
    assert any(item["id"] == "open-seo" for item in catalog)


def test_execute_assign_skill_action(db_url) -> None:
    result = execute_action(
        {
            "type": "assign_skill",
            "agent": "queue-review",
            "skill_id": "open-seo",
        }
    )
    assert result["ok"] is True
    assert "open-seo" in result["skills"]
    removed = execute_action(
        {
            "type": "unassign_skill",
            "agent": "queue-review",
            "skill_id": "open-seo",
        }
    )
    assert removed["ok"] is True
    assert "open-seo" not in removed["skills"]


def test_has_skill_respects_unassign(db_url) -> None:
    assert has_skill("research", "marketing-agi/competitive") is True
    unassign_skill("research", "marketing-agi/competitive")
    assert has_skill("research", "marketing-agi/competitive") is False
    guidance = research_competitor_guidance()
    assert "competitive.md" not in guidance
    assert "positioning.md" in guidance
