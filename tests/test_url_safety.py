"""Tests for URL safety / SSRF guards."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_crm.url_safety import UnsafeURLError, assert_public_http_url, is_public_http_url


def test_blocks_literal_private_and_loopback_ips() -> None:
    assert not is_public_http_url("http://127.0.0.1/admin", resolve_dns=False)
    assert not is_public_http_url("http://10.0.1.9:8888/v1", resolve_dns=False)
    assert not is_public_http_url("http://192.168.1.1/", resolve_dns=False)
    assert not is_public_http_url("http://169.254.169.254/latest/meta-data/", resolve_dns=False)
    assert not is_public_http_url("http://[::1]/", resolve_dns=False)
    assert not is_public_http_url("http://localhost/x", resolve_dns=False)


def test_allows_public_http_urls_without_dns() -> None:
    assert is_public_http_url("https://example.com/path", resolve_dns=False)
    assert is_public_http_url("http://novastudio.com/team", resolve_dns=False)


def test_rejects_non_http_schemes() -> None:
    assert not is_public_http_url("ftp://example.com/file", resolve_dns=False)
    assert not is_public_http_url("file:///etc/passwd", resolve_dns=False)


def test_dns_resolution_blocks_private_answers() -> None:
    with patch("agent_crm.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ]
        with pytest.raises(UnsafeURLError, match="not public"):
            assert_public_http_url("https://evil.example", resolve_dns=True)


def test_dns_resolution_allows_public_answers() -> None:
    with patch("agent_crm.url_safety.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]
        assert assert_public_http_url("https://example.com", resolve_dns=True) == (
            "https://example.com"
        )
