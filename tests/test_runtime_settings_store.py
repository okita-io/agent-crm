"""Tests for dashboard runtime settings store."""

from __future__ import annotations

from agent_crm.runtime_settings_store import (
    get_runtime_settings,
    probe_spark_upstream,
    update_runtime_settings,
)


def test_update_runtime_settings_spark_url(db_url) -> None:
    merged = update_runtime_settings(
        {"spark_upstream_base_url": "http://10.0.1.3:8888/v1"}
    )
    assert merged["spark_upstream_base_url"] == "http://10.0.1.3:8888/v1"
    again = get_runtime_settings()
    assert again["spark_upstream_base_url"] == "http://10.0.1.3:8888/v1"


def test_probe_spark_upstream_invalid_url(db_url) -> None:
    result = probe_spark_upstream("not-a-url")
    assert result["ok"] is False
