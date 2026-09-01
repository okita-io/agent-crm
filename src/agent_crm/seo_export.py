"""Markdown export helpers for SEO / AEO / GEO review and plan documents."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any


def seo_export_filename(row: Any, *, doc: str) -> str:
    """Stable markdown filename: ``geo-review-example-com-12.md``."""
    domain = str(getattr(row, "domain", "") or "document")
    kind = getattr(getattr(row, "kind", None), "value", None) or doc
    raw = f"{kind}-{doc}-{domain}-{getattr(row, 'id', 'doc')}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return f"{slug or f'{doc}-document'}.md"


def seo_document_markdown(row: Any) -> str:
    """Return the document body, or a short fallback if the body is empty."""
    body = getattr(row, "body", None) or ""
    if body.strip():
        return body if body.endswith("\n") else f"{body}\n"
    title = (getattr(row, "title", None) or "").strip() or "Untitled"
    url = getattr(row, "url", None) or ""
    one_thing = getattr(row, "one_thing", None) or ""
    parts = [f"# {title}\n"]
    if url:
        parts.append(f"Source: {url}\n")
    if one_thing:
        parts.append(f"\n{one_thing}\n")
    return "".join(parts)


def zip_seo_documents(rows: list, *, doc: str) -> bytes:
    """Pack every review or plan in ``rows`` into a zip of markdown files."""
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            name = seo_export_filename(row, doc=doc)
            if name in used:
                stem = name.removesuffix(".md")
                name = f"{stem}-{getattr(row, 'id', 'dup')}.md"
            used.add(name)
            archive.writestr(name, seo_document_markdown(row))
    return buf.getvalue()
