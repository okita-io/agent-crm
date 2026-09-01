"""Dashboard UI contracts that do not require a Streamlit runtime."""

from __future__ import annotations

from pathlib import Path

DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "src" / "agent_crm" / "dashboard.py"


def test_dashboard_disables_streamlit_stale_fade() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    assert "opacity: 1 !important" in source
    assert "transition: none !important" in source
    main_at = source.index("def main() -> None:")
    config_at = source.index("st.set_page_config", main_at)
    fade_at = source.index("_disable_stale_fade()", main_at)
    assert fade_at > config_at
    assert '"SEO"' in source
    assert '"AEO / GEO"' in source
    assert "_render_seo_tab" in source
    assert "_render_aeo_geo_tab" in source
    assert "_pick_seo_document" in source
    assert 'page_title="The Agency"' in source
    assert 'st.title("The Agency")' in source
    assert "_render_agent_roster" in source
    assert "cols[0].toggle(" in source
    assert "agent_enabled_" in source
    assert "_on_agent_enabled_change" in source


def test_seo_document_pickers_are_outside_expanders() -> None:
    """Selectboxes inside expanders clip their dropdowns; pickers must stay visible."""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    seo = source.split("def _render_seo_tab")[1].split("def _render_aeo_geo_tab")[0]
    aeo_geo = source.split("def _render_aeo_geo_tab")[1].split("def _render_contacts_tab")[0]
    assert 'expander("Open a plan document")' not in seo
    assert 'expander("Open a review document")' not in seo
    assert 'label="Which plan to open"' in seo
    assert 'label="Which review to open"' in seo
    assert "_seo_download_buttons" in seo
    assert "Download review" in source
    assert "Download plan" in source
    assert "Export all reviews (.zip)" in source
    assert "Export all plans (.zip)" in source
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
