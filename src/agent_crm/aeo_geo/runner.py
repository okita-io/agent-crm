"""AEO/GEO extractability signal extraction and issue engine.

Detection is local (Firecrawl markdown + metadata). This module never patches
a live site or claims live citation results.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_crm.seo.runner import PageSignals, extract_page_signals

_QUESTION_HEADING_RE = re.compile(
    r"^#{1,3}\s+(.+\?)\s*$", re.MULTILINE | re.IGNORECASE
)
_FAQ_HEADING_RE = re.compile(
    r"^#{1,4}\s+.*\b(faq|frequently asked|questions)\b.*$", re.MULTILINE | re.IGNORECASE
)
_STAT_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*%|\b\d{4}\b|\$\d+|\b\d+\s+(?:users|customers|companies|plants|sites)\b",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r'[""][^""]{20,}[""]|"[^"]{20,}"')
_TABLE_RE = re.compile(r"^\|.+\|$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^[-*]\s+.+$", re.MULTILINE)
_SAME_AS_RE = re.compile(
    r"sameAs|same_as|wikipedia\.org|linkedin\.com|crunchbase\.com", re.IGNORECASE
)
_ORG_SCHEMA_RE = re.compile(
    r'"@type"\s*:\s*"(Organization|Person|Corporation)"', re.IGNORECASE
)

SEVERITY_PENALTY = {"critical": 18, "high": 10, "medium": 5, "low": 2}


@dataclass(frozen=True)
class ExtractabilitySignals:
    """AEO/GEO signals derived from one scraped page."""

    url: str
    question_headings: tuple[str, ...] = ()
    has_faq_section: bool = False
    answer_first_paragraph_words: int = 0
    statistics_count: int = 0
    quotation_count: int = 0
    table_row_count: int = 0
    list_item_count: int = 0
    has_org_person_schema: bool = False
    has_same_as_hints: bool = False
    word_count: int = 0
    h1_text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["question_headings"] = list(self.question_headings)
        return payload


@dataclass(frozen=True)
class AeoGeoIssue:
    """One AEO/GEO finding with copy-pasteable remediation for a human implementer."""

    issue_id: str
    severity: str
    title: str
    explanation: str
    how_to_fix: str
    evidence: str
    url: str
    lever: str  # access | entity | quotable | fanout | measure

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AeoGeoBundle:
    """Signals and issues for one or more pages on a target."""

    pages: list[ExtractabilitySignals] = field(default_factory=list)
    seo_pages: list[PageSignals] = field(default_factory=list)
    issues: list[AeoGeoIssue] = field(default_factory=list)
    score: int = 100
    crawled: bool = True
    crawl_error: str | None = None

    def as_evidence(self) -> dict[str, Any]:
        return {
            "pages": [page.as_dict() for page in self.pages],
            "seo_pages": [page.as_dict() for page in self.seo_pages],
            "score": self.score,
            "crawled": self.crawled,
            "crawl_error": self.crawl_error,
            "issue_count": len(self.issues),
        }


def extract_extractability_signals(
    url: str,
    *,
    markdown: str,
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> ExtractabilitySignals:
    """Derive AEO/GEO signals from one page's scrape."""
    meta = metadata or {}
    text = markdown or ""
    seo = extract_page_signals(url, markdown=text, metadata=meta, title=title)

    question_headings = tuple(
        match.group(1).strip() for match in _QUESTION_HEADING_RE.finditer(text)
    )
    has_faq = bool(_FAQ_HEADING_RE.search(text))

    # Answer-first: words in first paragraph after first H1
    answer_words = 0
    h1_text = seo.h1_text[0] if seo.h1_text else None
    after_h1 = text
    if h1_text:
        h1_pattern = re.compile(
            rf"^#\s+{re.escape(h1_text)}\s*$", re.MULTILINE | re.IGNORECASE
        )
        match = h1_pattern.search(text)
        if match:
            after_h1 = text[match.end() :]
    first_block = re.split(r"\n\s*\n", after_h1.strip(), maxsplit=1)[0]
    first_block = re.sub(r"^#+\s+.*$", "", first_block, flags=re.MULTILINE).strip()
    answer_words = len(re.findall(r"\b\w+\b", first_block))

    meta_blob = str(meta.get("jsonld") or meta) + text[:4000]
    has_org = bool(_ORG_SCHEMA_RE.search(meta_blob))
    has_same_as = bool(_SAME_AS_RE.search(meta_blob))

    return ExtractabilitySignals(
        url=url,
        question_headings=question_headings,
        has_faq_section=has_faq,
        answer_first_paragraph_words=answer_words,
        statistics_count=len(_STAT_RE.findall(text)),
        quotation_count=len(_QUOTE_RE.findall(text)),
        table_row_count=len(_TABLE_RE.findall(text)),
        list_item_count=len(_LIST_ITEM_RE.findall(text)),
        has_org_person_schema=has_org or seo.has_json_ld,
        has_same_as_hints=has_same_as,
        word_count=seo.word_count,
        h1_text=h1_text,
    )


def detect_aeo_geo_issues(signals: ExtractabilitySignals) -> list[AeoGeoIssue]:
    """Return AEO/GEO issues for one page."""
    issues: list[AeoGeoIssue] = []
    url = signals.url

    if signals.answer_first_paragraph_words < 25 and signals.word_count >= 80:
        issues.append(
            AeoGeoIssue(
                issue_id="buried-answer",
                severity="high",
                title="Answer is not up front",
                explanation=(
                    "AEO needs a self-contained answer in the first 2–3 sentences under "
                    "the main heading. The opening block is thin."
                ),
                how_to_fix=(
                    "Rewrite the lead paragraph to directly answer the page's main "
                    "question in plain language before any preamble."
                ),
                evidence=f"~{signals.answer_first_paragraph_words} words in first block after H1",
                url=url,
                lever="quotable",
            )
        )

    if not signals.question_headings and signals.word_count >= 150:
        issues.append(
            AeoGeoIssue(
                issue_id="missing-question-headings",
                severity="medium",
                title="No question-shaped headings",
                explanation=(
                    "H2/H3 phrased as questions help both AEO snippets and GEO extraction."
                ),
                how_to_fix=(
                    "Add H2s that match how people ask ChatGPT (e.g. "
                    "'What is WebAR training for plants?')."
                ),
                evidence="0 question-shaped headings in scrape",
                url=url,
                lever="fanout",
            )
        )

    if not signals.has_faq_section and signals.word_count >= 200:
        issues.append(
            AeoGeoIssue(
                issue_id="missing-visible-faq",
                severity="medium",
                title="No visible FAQ section",
                explanation=(
                    "Visible FAQ blocks are easy lift targets for snippets and AI answers."
                ),
                how_to_fix=(
                    "Add a short FAQ with 3–5 real customer questions and direct answers "
                    "in HTML text."
                ),
                evidence="No FAQ heading detected in markdown",
                url=url,
                lever="quotable",
            )
        )

    if signals.statistics_count == 0 and signals.quotation_count == 0 and signals.word_count >= 200:
        issues.append(
            AeoGeoIssue(
                issue_id="no-quotable-evidence",
                severity="high",
                title="No quotable statistics or quotations",
                explanation=(
                    "GEO research (Aggarwal et al., KDD 2024) found statistics and "
                    "quotations help generative citation; generic prose does not."
                ),
                how_to_fix=(
                    "Add named numbers with source/date, or a short attributed quote "
                    "from a customer or study."
                ),
                evidence="0 statistics and 0 quotations detected in scrape",
                url=url,
                lever="quotable",
            )
        )

    if not signals.has_org_person_schema and signals.word_count >= 100:
        issues.append(
            AeoGeoIssue(
                issue_id="missing-entity-schema",
                severity="medium",
                title="No Organization/Person structured data hints",
                explanation=(
                    "Entity clarity (JSON-LD, bios, sameAs) helps GEO disambiguate the brand."
                ),
                how_to_fix=(
                    "Add Organization or Person JSON-LD with name, url, logo, and sameAs "
                    "links to official profiles."
                ),
                evidence="No Organization/Person @type in scrape metadata",
                url=url,
                lever="entity",
            )
        )

    if not signals.has_same_as_hints and signals.word_count >= 100:
        issues.append(
            AeoGeoIssue(
                issue_id="missing-sameas",
                severity="low",
                title="No sameAs / corroboration profile links",
                explanation=(
                    "Consistent entity references across the open web support GEO corroboration."
                ),
                how_to_fix=(
                    "Link official LinkedIn, Crunchbase, or Wikipedia (only if legitimate) "
                    "from About page and JSON-LD sameAs."
                ),
                evidence="No sameAs or major profile URLs in scrape",
                url=url,
                lever="entity",
            )
        )

    if signals.table_row_count == 0 and signals.list_item_count < 3 and signals.word_count >= 250:
        issues.append(
            AeoGeoIssue(
                issue_id="low-extractable-structure",
                severity="medium",
                title="Few tables or lists for extractable facts",
                explanation=(
                    "Tables and bullet lists are easier for models to lift than dense prose."
                ),
                how_to_fix=(
                    "Convert comparisons or feature lists into a table or bullet list with "
                    "explicit facts."
                ),
                evidence=(
                    f"{signals.table_row_count} table rows, "
                    f"{signals.list_item_count} list items"
                ),
                url=url,
                lever="quotable",
            )
        )

    if signals.word_count < 120:
        issues.append(
            AeoGeoIssue(
                issue_id="thin-extractable-content",
                severity="high",
                title="Thin page content for AEO/GEO",
                explanation="Very little crawlable text for models to extract or cite.",
                how_to_fix=(
                    "Expand with answer-first copy, FAQ, and specific facts a model "
                    "cannot invent."
                ),
                evidence=f"~{signals.word_count} words in scrape",
                url=url,
                lever="quotable",
            )
        )

    return issues


def score_aeo_geo_issues(issues: list[AeoGeoIssue]) -> int:
    score = 100
    for issue in issues:
        score -= SEVERITY_PENALTY.get(issue.severity, 3)
    return max(0, min(100, score))


def pick_one_aeo_geo_thing(issues: list[AeoGeoIssue], *, owned: bool) -> str:
    if not issues:
        if owned:
            return (
                "Run the measurement prompt panel across ChatGPT, Gemini, Perplexity, "
                "and Copilot — record mentions vs citations in a spreadsheet."
            )
        return "Study which competitor pages get cited for the seed questions in chat engines."

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ranked = sorted(issues, key=lambda item: (order.get(item.severity, 9), item.issue_id))
    top = ranked[0]
    return f"{top.how_to_fix} ({top.title} on {top.url})."


def related_paths_for_aeo_geo(page: PageSignals, *, limit: int = 3) -> list[str]:
    """Prefer about, pricing, and FAQ paths for AEO/GEO fan-out review."""
    priority = ("/about", "/faq", "/pricing", "/how", "/vs", "/compare")
    urls = list(page.same_domain_urls)
    picked: list[str] = []
    for fragment in priority:
        for href in urls:
            if fragment in href.lower() and href not in picked:
                picked.append(href)
                if len(picked) >= limit:
                    return picked
    for href in urls:
        if href not in picked:
            picked.append(href)
        if len(picked) >= limit:
            break
    return picked
