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
