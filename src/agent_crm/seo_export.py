"""Export SEO and AEO/GEO review/plan documents as markdown or zip."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from .enums import SeoPlanKind, SeoReviewKind

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_domain(domain: str) -> str:
    slug = _SLUG_RE.sub("-", (domain or "unknown").lower()).strip("-")
    return slug or "unknown"


def _document_kind_prefix(row: Any) -> str:
    kind = getattr(row, "kind", None)
    if kind in (SeoReviewKind.GEO, SeoPlanKind.GEO):
        return "geo"
    return "seo"


def seo_export_filename(row: Any, *, doc: str = "review") -> str:
    """Build a stable markdown filename such as ``geo-review-example-com-12.md``."""
    prefix = _document_kind_prefix(row)
    domain_slug = _slug_domain(getattr(row, "domain", "") or "")
    row_id = getattr(row, "id", 0)
    return f"{prefix}-{doc}-{domain_slug}-{row_id}.md"


def seo_document_markdown(row: Any) -> str:
    """Return document body, or a title/url/one_thing fallback."""
    body = (getattr(row, "body", None) or "").strip()
    if body:
        return body

    title = (getattr(row, "title", None) or "").strip()
    url = (getattr(row, "url", None) or "").strip()
    one_thing = (getattr(row, "one_thing", None) or "").strip()
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    if url:
        lines.append("")
        lines.append(f"URL: {url}")
    if one_thing:
        lines.append("")
        lines.append("## One thing")
        lines.append("")
        lines.append(one_thing)
    if lines:
        return "\n".join(lines).strip() + "\n"
    return f"# Document {getattr(row, 'id', '')}\n"


def zip_seo_documents(rows: list[Any], *, doc: str = "review") -> bytes:
    """Zip markdown exports for every row in ``rows``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            archive.writestr(
                seo_export_filename(row, doc=doc),
                seo_document_markdown(row),
            )
    return buffer.getvalue()
