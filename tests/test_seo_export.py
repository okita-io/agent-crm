"""Tests for SEO/AEO markdown export helpers."""

from __future__ import annotations

import zipfile
from io import BytesIO
from types import SimpleNamespace

from agent_crm.enums import SeoReviewKind
from agent_crm.seo_export import (
    seo_document_markdown,
    seo_export_filename,
    zip_seo_documents,
)


def test_seo_export_filename_geo_review() -> None:
    row = SimpleNamespace(id=12, domain="example.com", kind=SeoReviewKind.GEO)
    assert seo_export_filename(row, doc="review") == "geo-review-example-com-12.md"


def test_seo_document_markdown_prefers_body() -> None:
    row = SimpleNamespace(body="# Hello", title="T", url="https://x", one_thing="fix")
    assert seo_document_markdown(row) == "# Hello"


def test_seo_document_markdown_fallback() -> None:
    row = SimpleNamespace(body="", title="Title", url="https://example.com", one_thing="Do one thing")
    text = seo_document_markdown(row)
    assert "# Title" in text
    assert "https://example.com" in text
    assert "Do one thing" in text


def test_zip_seo_documents_contains_markdown_files() -> None:
    rows = [
        SimpleNamespace(
            id=1,
            domain="a.com",
            kind=SeoReviewKind.GEO,
            body="one",
            title="",
            url="",
            one_thing=None,
        ),
        SimpleNamespace(
            id=2,
            domain="b.com",
            kind=SeoReviewKind.GEO,
            body="two",
            title="",
            url="",
            one_thing=None,
        ),
    ]
    payload = zip_seo_documents(rows, doc="review")
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = sorted(archive.namelist())
    assert names == [
        "geo-review-a-com-1.md",
        "geo-review-b-com-2.md",
    ]
