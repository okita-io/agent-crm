"""Dashboard UI contracts that do not require a Streamlit runtime."""

from __future__ import annotations

from pathlib import Path

from agent_crm.dashboard import TAB_EXPORT_KEYS, TAB_LABELS

DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "src" / "agent_crm" / "dashboard.py"
DASHBOARD_UI = Path(__file__).resolve().parents[1] / "src" / "agent_crm" / "dashboard_ui"
TAB_DIR = DASHBOARD_UI / "tabs"


def test_dashboard_shell_is_thin_and_registers_tabs() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    assert source.count("\n") < 220
    assert 'page_title="The Agency"' in source
    assert 'st.title("The Agency")' in source
    assert "_disable_stale_fade()" in source
    assert list(TAB_LABELS) == [
        "Live agents",
        "Command",
        "Settings",
        "Pipeline & leads",
        "Hunter",
        "Research",
        "Engagement",
        "SEO",
        "AEO / GEO",
        "Contacts",
        "Verifier",
        "Improvement",
    ]
    for label in TAB_LABELS:
        assert f'"{label}"' in source or label in TAB_LABELS


def test_dashboard_disables_streamlit_stale_fade() -> None:
    common = (DASHBOARD_UI / "common.py").read_text(encoding="utf-8")
    assert "opacity: 1 !important" in common
    assert "transition: none !important" in common
    shell = DASHBOARD_PATH.read_text(encoding="utf-8")
    main_at = shell.index("def main() -> None:")
    config_at = shell.index("st.set_page_config", main_at)
    fade_at = shell.index("_disable_stale_fade()", main_at)
    assert fade_at > config_at
    observer = (TAB_DIR / "observer.py").read_text(encoding="utf-8")
    assert "_render_agent_roster" in observer
    assert "cols[0].toggle(" in observer
    assert "agent_enabled_" in observer
    assert "_on_agent_enabled_change" in observer


def test_seo_document_pickers_are_outside_expanders() -> None:
    """Selectboxes inside expanders clip their dropdowns; pickers must stay visible."""
    seo = (TAB_DIR / "seo.py").read_text(encoding="utf-8")
    aeo_geo = (TAB_DIR / "aeo_geo.py").read_text(encoding="utf-8")
    assert 'expander("Open a plan document")' not in seo
    assert 'expander("Open a review document")' not in seo
    assert 'label="Which plan to open"' in seo
    assert 'label="Which review to open"' in seo
    assert "_seo_download_buttons" in seo
    assert "Download review" in seo
    assert "Download plan" in seo
    assert "Export all reviews (.zip)" in seo
    assert "Export all plans (.zip)" in seo
    plan_pick = seo.index('label="Which plan to open"')
    plan_dl = seo.index("_seo_download_buttons", plan_pick)
    plan_expand = seo.index("Plan —", plan_pick)
    review_pick = seo.index('label="Which review to open"')
    review_dl = seo.index("_seo_download_buttons", review_pick)
    review_expand = seo.index("Review —", review_pick)
    assert plan_pick < plan_dl < plan_expand
    assert review_pick < review_dl < review_expand
    aeo_review_pick = aeo_geo.index('label="Which AEO/GEO review to open"')
    aeo_review_dl = aeo_geo.index("_seo_download_buttons", aeo_review_pick)
    aeo_review_expand = aeo_geo.index("AEO/GEO Review —", aeo_review_pick)
    assert aeo_review_pick < aeo_review_dl < aeo_review_expand
    aeo_plan_pick = aeo_geo.index('label="Which AEO/GEO plan to open"')
    aeo_plan_dl = aeo_geo.index("_seo_download_buttons", aeo_plan_pick)
    aeo_plan_expand = aeo_geo.index("AEO/GEO Plan —", aeo_plan_pick)
    assert aeo_plan_pick < aeo_plan_dl < aeo_plan_expand


def test_dashboard_tables_have_full_csv_export() -> None:
    common = (DASHBOARD_UI / "common.py").read_text(encoding="utf-8")
    assert "def _render_full_csv_export" in common
    assert '"Full export"' in common
    tab_files = {
        "pipeline": TAB_DIR / "pipeline.py",
        "hunter": TAB_DIR / "hunter.py",
        "research": TAB_DIR / "research.py",
        "engagement": TAB_DIR / "engagement.py",
        "seo": TAB_DIR / "seo.py",
        "aeo_geo": TAB_DIR / "aeo_geo.py",
        "contacts": TAB_DIR / "contacts.py",
        "verifier": TAB_DIR / "verifier.py",
        "improvement": TAB_DIR / "improvement.py",
    }
    for tab, keys in TAB_EXPORT_KEYS.items():
        if tab == "comment_people":
            body = (TAB_DIR / "contacts.py").read_text(encoding="utf-8")
            assert 'key=f"{key_prefix}comment_people"' in body
            assert "_render_full_csv_export" in body
            continue
        path = tab_files[tab]
        body = path.read_text(encoding="utf-8")
        assert "_render_full_csv_export" in body, f"{tab} missing Full export"
        for key in keys:
            assert f'key="{key}"' in body, f"missing key={key} in {tab}"


def test_pipeline_and_research_selectboxes_have_keys() -> None:
    pipeline = (TAB_DIR / "pipeline.py").read_text(encoding="utf-8")
    research = (TAB_DIR / "research.py").read_text(encoding="utf-8")
    assert 'key="pipeline_lead_pick"' in pipeline
    assert 'key="research_kind"' in research


def test_seo_export_filename_includes_kind_and_domain() -> None:
    from types import SimpleNamespace

    from agent_crm.seo_export import seo_document_markdown, seo_export_filename, zip_seo_documents

    row = SimpleNamespace(
        id=12,
        domain="example.com",
        kind=SimpleNamespace(value="geo"),
        title="GEO Review — example.com",
        url="https://example.com",
        body="# GEO Review\n\nCitation notes.\n",
        one_thing="Add llms.txt",
    )
    assert seo_export_filename(row, doc="review") == "geo-review-example-com-12.md"
    assert seo_document_markdown(row).startswith("# GEO Review")
    payload = zip_seo_documents([row], doc="review")
    assert payload[:2] == b"PK"

    empty = SimpleNamespace(
        id=3,
        domain="heybuddy.app",
        kind=SimpleNamespace(value="technical"),
        title="SEO Plan — heybuddy.app",
        url="https://heybuddy.app",
        body="  ",
        one_thing="Fix title tags",
    )
    fallback = seo_document_markdown(empty)
    assert fallback.startswith("# SEO Plan — heybuddy.app")
    assert "https://heybuddy.app" in fallback
    assert "Fix title tags" in fallback
    assert seo_export_filename(empty, doc="plan") == "technical-plan-heybuddy-app-3.md"
