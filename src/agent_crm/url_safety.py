"""URL safety helpers — block private/link-local/metadata targets (SSRF guard)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe for outbound fetch."""


def _hostname_is_blocked(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return True
    if host in {"localhost", "metadata.google.internal"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolved_addresses_are_safe(hostname: str) -> bool:
    """Resolve DNS and reject if any answer is a private/special address."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {hostname}") from exc
    if not infos:
        raise UnsafeURLError(f"no DNS answers for {hostname}")
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def is_public_http_url(url: str, *, resolve_dns: bool = True) -> bool:
    """Return True when ``url`` is http(s) and not aimed at private networks."""
    try:
        assert_public_http_url(url, resolve_dns=resolve_dns)
        return True
    except UnsafeURLError:
        return False


def assert_public_http_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate and return a cleaned public http(s) URL, or raise UnsafeURLError."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("empty URL")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("only http/https URLs are allowed")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL missing hostname")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with credentials are not allowed")
    if _hostname_is_blocked(hostname):
        raise UnsafeURLError(f"blocked host: {hostname}")
    if resolve_dns and not _resolved_addresses_are_safe(hostname):
        raise UnsafeURLError(f"resolved address for {hostname} is not public")
    return url.strip()
