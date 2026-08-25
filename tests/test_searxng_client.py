"""Tests for SearXNG client param forwarding."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_crm.searxng_client import SearxngClient


def test_search_forwards_searxng_params():
    client = SearxngClient()
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_response.raise_for_status = MagicMock()

    with patch("agent_crm.searxng_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client.search(
            "booktok communities",
            categories="social media",
            pageno=2,
            time_range="year",
            language="en",
            engines="google",
        )

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs["params"]
        assert params["q"] == "booktok communities"
        assert params["format"] == "json"
        assert params["categories"] == "social media"
        assert params["pageno"] == 2
        assert params["time_range"] == "year"
        assert params["language"] == "en"
        assert params["engines"] == "google"
