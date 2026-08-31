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
    assert "_seo_download_buttons" in source
    assert "set_agent_enabled" in source
    assert "Flip the switch to pause or resume" in source
    assert ".toggle(" in source
    assert 'st.dataframe(table, use_container_width=True, hide_index=True)' not in source.split(
        "def _render_agent_observer"
    )[1].split("def _render_pipeline_tab")[0]
    assert 'page_title="The Agency"' in source
    assert 'st.title("The Agency")' in source


def test_seo_document_pickers_are_outside_expanders() -> None:
    """Selectboxes inside expanders clip their dropdowns; pickers must stay visible."""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    seo = source.split("def _render_seo_tab")[1].split("def _render_aeo_geo_tab")[0]
    aeo_geo = source.split("def _render_aeo_geo_tab")[1].split("def _render_contacts_tab")[0]
    assert 'expander("Open a plan document")' not in seo
    assert 'expander("Open a review document")' not in seo
    assert 'label="Which plan to open"' in seo
    assert 'label="Which review to open"' in seo
    plan_pick = seo.index('label="Which plan to open"')
    plan_expand = seo.index("Plan —", plan_pick)
    review_pick = seo.index('label="Which review to open"')
    review_expand = seo.index("Review —", review_pick)
    assert plan_pick < plan_expand
    assert review_pick < review_expand
    assert "_seo_download_buttons(" in seo
    assert "_seo_download_buttons(" in aeo_geo
    aeo_review_pick = aeo_geo.index('label="Which AEO/GEO review to open"')
    aeo_review_expand = aeo_geo.index("AEO/GEO Review —", aeo_review_pick)
    assert aeo_review_pick < aeo_review_expand
