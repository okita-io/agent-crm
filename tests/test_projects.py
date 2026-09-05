"""Tests for the YAML-backed Projects catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_crm.enums import Brand
from agent_crm.errors import ConflictError, NotFoundError, ValidationAppError
from agent_crm.projects.channel_flags import (
    active_brands_for,
    clear_channel_cache,
    project_prompt_for,
)
from agent_crm.projects.schema import ProjectDocument, ProjectStatus
from agent_crm.projects.store import (
    create_project,
    get_project,
    patch_project,
    projects_stats,
    update_channels,
    update_prompts,
)


REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"


@pytest.fixture()
def projects_tmpdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_PROJECTS_DIR", str(tmp_path))
    # Clear cached settings + channel cache
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    clear_channel_cache()
    yield tmp_path
    get_settings.cache_clear()
    clear_channel_cache()


def test_shipped_yaml_files_parse() -> None:
    paths = sorted(REPO_PROJECTS.glob("*.yaml"))
    assert len(paths) >= 5
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc = ProjectDocument.model_validate(raw)
        assert doc.slug == path.stem


def test_heybuddy_seo_disarmed_in_repo() -> None:
    doc = ProjectDocument.model_validate(
        yaml.safe_load((REPO_PROJECTS / "heybuddy.yaml").read_text(encoding="utf-8"))
    )
    assert doc.channels["seo"].armed is False
    assert doc.channels["research"].armed is True
    assert doc.status == ProjectStatus.PRE_LAUNCH


def test_active_brands_for_respects_yaml(projects_tmpdir: Path) -> None:
    # Copy heybuddy + midnightsatin from repo
    for slug in ("heybuddy", "midnightsatin"):
        src = REPO_PROJECTS / f"{slug}.yaml"
        (projects_tmpdir / f"{slug}.yaml").write_text(src.read_text(encoding="utf-8"))
    clear_channel_cache()
    seo = active_brands_for("seo")
    research = active_brands_for("research")
    assert Brand.HEYBUDDY not in seo
    assert Brand.MIDNIGHTSATIN in seo
    assert Brand.HEYBUDDY in research
    assert Brand.MIDNIGHTSATIN in research


def test_create_patch_channels_round_trip(projects_tmpdir: Path) -> None:
    doc = create_project(
        slug="demo-project",
        name="Demo",
        site="https://example.com",
        origin_prompt="Primary goal here.",
    )
    assert (projects_tmpdir / "demo-project.yaml").is_file()
    from agent_crm.projects.store import brand_for_slug

    assert brand_for_slug(doc.slug) is None  # not a Brand enum member

    updated = patch_project("demo-project", origin_prompt="Updated goal.")
    assert updated.origin_prompt == "Updated goal."

    ch = update_channels("demo-project", {"research": True, "seo": False})
    assert ch.channels["research"].armed is True
    assert ch.channels["seo"].armed is False

    prompted = update_prompts(
        "demo-project",
        channel_prompts={"research": "Research task prompt"},
    )
    assert prompted.channels["research"].prompt == "Research task prompt"

    reloaded = get_project("demo-project")
    raw = yaml.safe_load((projects_tmpdir / "demo-project.yaml").read_text(encoding="utf-8"))
    assert reloaded.origin_prompt == "Updated goal."
    assert raw["channels"]["research"]["armed"] is True


def test_best_biryani_brand_enum() -> None:
    assert Brand.BEST_BIRYANI.value == "best-biryani"
    assert Brand("best-biryani") is Brand.BEST_BIRYANI


def test_atomic_write_uses_literal_blocks(projects_tmpdir: Path) -> None:
    create_project(
        slug="blocky",
        name="Blocky",
        origin_prompt="Line one.\nLine two.\n",
    )
    update_prompts("blocky", channel_prompts={"research": "Task A.\nTask B.\n"})
    text = (projects_tmpdir / "blocky.yaml").read_text(encoding="utf-8")
    assert "origin_prompt: |" in text
    assert "prompt: |" in text

    create_project(slug="once", name="Once")
    with pytest.raises(ConflictError):
        create_project(slug="once", name="Twice")


def test_invalid_channel_rejected(projects_tmpdir: Path) -> None:
    create_project(slug="ok", name="Ok")
    with pytest.raises(ValidationAppError):
        update_channels("ok", {"not_a_channel": True})  # type: ignore[dict-item]


def test_missing_project_raises(projects_tmpdir: Path) -> None:
    with pytest.raises(NotFoundError):
        get_project("nope")


def test_projects_stats(projects_tmpdir: Path) -> None:
    create_project(slug="a", name="A", site="https://a.example", status=ProjectStatus.LIVE)
    create_project(slug="b", name="B", status=ProjectStatus.PRE_LAUNCH)
    update_channels("a", {"research": True, "hunter": True})
    stats = projects_stats()
    assert stats["projects"] == 2
    assert stats["live_sites"] == 1
    assert stats["pre_launch"] == 1
    assert stats["channels_armed"] >= 2


def test_project_prompt_for_includes_origin(projects_tmpdir: Path) -> None:
    (projects_tmpdir / "heybuddy.yaml").write_text(
        (REPO_PROJECTS / "heybuddy.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    clear_channel_cache()
    text = project_prompt_for(Brand.HEYBUDDY, "research", max_chars=2000)
    assert "HeyBuddy" in text or "loneliness" in text.lower()


def test_fail_open_without_projects_dir(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("CRM_PROJECTS_DIR", str(missing))
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    clear_channel_cache()
    brands = active_brands_for("research")
    assert Brand.HEYBUDDY in brands
    assert Brand.TACTIC_STUDIO in brands
    get_settings.cache_clear()
    clear_channel_cache()


def test_list_projects_api(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from agent_crm.api import app
    from agent_crm.config import get_settings
    from agent_crm.db import init_db, reset_engine

    db_path = tmp_path / "projects_api.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CRM_API_TOKEN", "")
    monkeypatch.setenv("CRM_PROJECTS_DIR", str(REPO_PROJECTS))
    get_settings.cache_clear()
    clear_channel_cache()
    reset_engine()
    init_db()
    try:
        client = TestClient(app)
        response = client.get("/projects")
        assert response.status_code == 200
        payload = response.json()
        assert payload["stats"]["projects"] >= 5
        slugs = {row["slug"] for row in payload["projects"]}
        assert "heybuddy" in slugs
        hey = next(row for row in payload["projects"] if row["slug"] == "heybuddy")
        assert hey["channels"]["seo"]["armed"] is False
    finally:
        reset_engine()
        get_settings.cache_clear()
        clear_channel_cache()
