"""Deterministic extraction of comment-thread authors from scraped markdown/HTML."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from agent_crm.enums import ContactAudience

if TYPE_CHECKING:
    from .store import ContactExtractionBudget

logger = logging.getLogger(__name__)

DEFAULT_MAX_HANDLES_PER_PAGE = 40

SKIP_HANDLES = frozenset(
    {
        "admin",
        "administrator",
        "anonymous",
        "automoderator",
        "bot",
        "deleted",
        "moderator",
        "moderatorbot",
        "newsbot",
        "reddit",
        "removed",
        "support",
        "webmaster",
        "[deleted]",
        "[removed]",
    }
)

INFLUENCER_SNIPPET_RE = re.compile(
    r"(author of|verified|youtube\.com|youtu\.be|my (book|site|blog|channel)|"
    r"check out my|founder of|ceo of|creator of)",
    re.IGNORECASE,
)

REDDIT_USER_RE = re.compile(
    r"(?:https?://(?:www\.|old\.)?reddit\.com)?/?u(?:ser)?/([A-Za-z0-9_-]{3,20})\b",
    re.IGNORECASE,
)
REDDIT_U_PREFIX_RE = re.compile(r"\bu/([A-Za-z0-9_-]{3,20})\b")
REDDIT_MD_USER_RE = re.compile(
    r"\[([A-Za-z0-9_-]{3,20})\]\(\s*/?u(?:ser)?/([A-Za-z0-9_-]{3,20})\s*\)",
    re.IGNORECASE,
)

HTML_COMMENT_AUTHOR_RE = re.compile(
    r'class=["\'][^"\']*comment-author[^"\']*["\'][^>]*>([^<]{1,80})<',
    re.IGNORECASE,
)
HTML_CITE_AUTHOR_RE = re.compile(
    r"<cite[^>]*>([^<]{1,80})</cite>",
    re.IGNORECASE,
)

SAID_WROTE_RE = re.compile(
    r"^([A-Z][A-Za-z'. \-]{1,60}?)\s+(?:said|wrote)\s*:",
    re.MULTILINE,
)

DISQUS_AUTHOR_RE = re.compile(
    r'data-role=["\']author["\'][^>]*>([^<]{1,80})<',
    re.IGNORECASE,
)

FOURCHAN_TRIPCODE_RE = re.compile(r"!!|#\w|Anonymous", re.IGNORECASE)

COMMENT_SECTION_MARKERS = (
    "comments",
    "comment thread",
    "discussion",
    "replies",
    "leave a reply",
    "join the conversation",
)

SPARK_COMMENT_ACTOR = "comment-extractor"


@dataclass
class ExtractedCommentPerson:
    """One public comment author found on a scraped page."""

    platform: str
    handle: str
    display_name: str | None = None
    profile_url: str | None = None
    comment_snippet: str | None = None
    audience: ContactAudience | None = ContactAudience.END_USER
    source_url: str | None = None


@dataclass
class _HandleAccumulator:
    people: dict[tuple[str, str], ExtractedCommentPerson] = field(default_factory=dict)

    def add(
        self,
        *,
        platform: str,
        handle: str,
        display_name: str | None = None,
        profile_url: str | None = None,
        comment_snippet: str | None = None,
        source_url: str | None = None,
        article_author_handles: set[tuple[str, str]] | None = None,
        max_handles: int,
    ) -> bool:
        normalized_handle = _normalize_handle(handle)
        if not normalized_handle:
            return False
        if not is_valid_comment_handle(platform, normalized_handle):
            return False
        key = (platform, normalized_handle)
        if article_author_handles and key in article_author_handles:
            return False
        if len(self.people) >= max_handles and key not in self.people:
            return False

        snippet = (comment_snippet or "").strip()
        audience = _guess_audience(snippet)
        existing = self.people.get(key)
        if existing is None:
            self.people[key] = ExtractedCommentPerson(
                platform=platform,
                handle=normalized_handle,
                display_name=_clean_display_name(display_name),
                profile_url=profile_url or _default_profile_url(platform, normalized_handle),
                comment_snippet=snippet[:240] if snippet else None,
                audience=audience,
                source_url=source_url,
            )
            return True

        if display_name and not existing.display_name:
            existing.display_name = _clean_display_name(display_name)
        if snippet and not existing.comment_snippet:
            existing.comment_snippet = snippet[:240]
        if audience == ContactAudience.INFLUENCER and existing.audience != ContactAudience.INFLUENCER:
            existing.audience = ContactAudience.INFLUENCER
        return False


def _normalize_handle(handle: str) -> str:
    text = handle.strip().strip("/").lstrip("@")
    if text.lower().startswith("u/"):
        text = text[2:]
    return text[:128]


def _clean_display_name(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value.strip())
    if not text or len(text) > 80:
        return None
    if text.lower() in SKIP_HANDLES:
        return None
    if EMAIL_RE.search(text):
        return None
    return text[:255]


EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)


def is_valid_comment_handle(platform: str, handle: str) -> bool:
    """Return True when a handle is worth persisting as a person of interest."""
    text = handle.strip().lower()
    if not text or len(text) < 3 or len(text) > 20:
        return False
    if text in SKIP_HANDLES:
        return False
    if text.startswith("[") or text.endswith("]"):
        return False
    if platform == "4chan":
        return False
    if FOURCHAN_TRIPCODE_RE.search(handle):
        return False
    if text.endswith("bot") and text not in {"robot"}:
        return False
    if not re.fullmatch(r"[a-z0-9_-]+", text):
        return False
    return True


def detect_platform(source_url: str) -> str:
    host = urlparse(source_url).netloc.lower()
    if "reddit.com" in host:
        return "reddit"
    if "disqus.com" in host:
        return "disqus"
    if "wordpress" in host or "wp.com" in host:
        return "wordpress"
    if "4chan" in host:
        return "4chan"
    return "web"


def _default_profile_url(platform: str, handle: str) -> str | None:
    if platform == "reddit":
        return f"https://www.reddit.com/user/{handle}"
    return None


def _guess_audience(snippet: str) -> ContactAudience:
    if snippet and INFLUENCER_SNIPPET_RE.search(snippet):
        return ContactAudience.INFLUENCER
    return ContactAudience.END_USER


def _split_article_and_comments(text: str) -> tuple[str, str]:
    lowered = text.lower()
    best_idx = -1
    for marker in COMMENT_SECTION_MARKERS:
        idx = lowered.find(marker)
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
    if best_idx < 0:
        return text, text
    return text[:best_idx], text[best_idx:]


def _extract_article_author_handles(
    article_text: str,
    *,
    platform: str,
    source_url: str,
) -> set[tuple[str, str]]:
    handles: set[tuple[str, str]] = set()
    if platform == "reddit":
        for match in REDDIT_USER_RE.finditer(article_text):
            handle = _normalize_handle(match.group(1))
            if handle:
                handles.add((platform, handle))
        for match in REDDIT_U_PREFIX_RE.finditer(article_text):
            handle = _normalize_handle(match.group(1))
            if handle:
                handles.add((platform, handle))
    byline = re.search(
        r"(?:by|posted by|written by)\s+([A-Z][A-Za-z'. \-]{1,60})",
        article_text,
        re.IGNORECASE,
    )
    if byline:
        name = byline.group(1).strip()
        slug = re.sub(r"[^a-z0-9]+", "", name.lower())
        if slug:
            handles.add((platform, slug))
    return handles


def _extract_reddit_handles(
    acc: _HandleAccumulator,
    text: str,
    *,
    source_url: str,
    article_author_handles: set[tuple[str, str]],
    max_handles: int,
) -> None:
    for match in REDDIT_MD_USER_RE.finditer(text):
        display_name, handle = match.group(1), match.group(2)
        snippet = text[match.end() : match.end() + 200]
        acc.add(
            platform="reddit",
            handle=handle,
            display_name=display_name,
            comment_snippet=snippet,
            source_url=source_url,
            article_author_handles=article_author_handles,
            max_handles=max_handles,
        )

    for pattern in (REDDIT_USER_RE, REDDIT_U_PREFIX_RE):
        for match in pattern.finditer(text):
            handle = match.group(1)
            start = max(0, match.start() - 20)
            snippet = text[start : match.end() + 180]
            acc.add(
                platform="reddit",
                handle=handle,
                comment_snippet=snippet,
                source_url=source_url,
                article_author_handles=article_author_handles,
                max_handles=max_handles,
            )


def _extract_blog_handles(
    acc: _HandleAccumulator,
    *,
    html: str | None,
    comments_text: str,
    platform: str,
    source_url: str,
    article_author_handles: set[tuple[str, str]],
    max_handles: int,
) -> None:
    if html:
        for pattern in (HTML_COMMENT_AUTHOR_RE, HTML_CITE_AUTHOR_RE, DISQUS_AUTHOR_RE):
            for match in pattern.finditer(html):
                raw = match.group(1).strip()
                handle = _normalize_handle(raw.replace(" ", "").lower())
                if not handle:
                    handle = re.sub(r"[^a-z0-9]+", "", raw.lower())
                if not handle:
                    continue
                acc.add(
                    platform=platform if platform != "web" else "disqus"
                    if "disqus" in (html or "").lower()
                    else "web",
                    handle=handle,
                    display_name=raw,
                    comment_snippet=html[match.end() : match.end() + 200],
                    source_url=source_url,
                    article_author_handles=article_author_handles,
                    max_handles=max_handles,
                )

    for match in SAID_WROTE_RE.finditer(comments_text):
        name = match.group(1).strip()
        handle = re.sub(r"[^a-z0-9]+", "", name.lower())
        if not handle:
            continue
        snippet = comments_text[match.end() : match.end() + 200]
        acc.add(
            platform=platform,
            handle=handle,
            display_name=name,
            comment_snippet=snippet,
            source_url=source_url,
            article_author_handles=article_author_handles,
            max_handles=max_handles,
        )


def _parse_spark_handles_json(content: str) -> list[dict[str, str | None]]:
    from agent_crm.llm_text import extract_json_object

    payload = extract_json_object(content)
    if not payload:
        return []
    people = payload.get("people")
    if not isinstance(people, list):
        return []
    parsed: list[dict[str, str | None]] = []
    for item in people:
        if not isinstance(item, dict):
            continue
        handle = item.get("handle")
        if not isinstance(handle, str):
            continue
        platform = item.get("platform")
        parsed.append(
            {
                "platform": platform if isinstance(platform, str) else "web",
                "handle": handle,
                "display_name": item.get("display_name")
                if isinstance(item.get("display_name"), str)
                else None,
                "comment_snippet": item.get("comment_snippet")
                if isinstance(item.get("comment_snippet"), str)
                else None,
            }
        )
    return parsed


def extract_comment_people_spark(
    text: str,
    *,
    source_url: str,
    platform: str,
    budget: ContactExtractionBudget | None,
    article_author_handles: set[tuple[str, str]],
    max_handles: int,
    acc: _HandleAccumulator,
) -> None:
    """Use Spark (via spark-queue) for leftover comment-author extraction."""
    if budget is None or not budget.consume_spark_decode():
        return
    if not text.strip():
        return

    from agent_crm.llm_client import chat_completions
    from agent_crm.llm_text import UNTRUSTED_DATA_SYSTEM_SUFFIX, wrap_untrusted

    prompt = (
        "Extract public comment author usernames/handles from the thread snippet below. "
        "Return JSON only: "
        '{"people":[{"platform":"reddit","handle":"username","display_name":null,'
        '"comment_snippet":"short quote"}]}. '
        "Skip bots, [deleted], AutoModerator, anonymous 4chan posts, and site accounts. "
        "Do NOT invent emails. Handles only.\n\n"
        f"Source URL: {source_url}\n\n"
        f"{wrap_untrusted('comment_thread', text, max_chars=3000)}"
    )
    try:
        response = chat_completions(
            {
                "model": "crm",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You extract comment usernames. Output JSON only."
                            + UNTRUSTED_DATA_SYSTEM_SUFFIX
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 400,
            },
            timeout=90.0,
            actor=SPARK_COMMENT_ACTOR,
            task="extract comment authors",
        )
        content = response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        logger.exception("Spark comment extraction failed for %s", source_url)
        return

    for item in _parse_spark_handles_json(content):
        acc.add(
            platform=item.get("platform") or platform,
            handle=item["handle"] or "",
            display_name=item.get("display_name"),
            comment_snippet=item.get("comment_snippet"),
            source_url=source_url,
            article_author_handles=article_author_handles,
            max_handles=max_handles,
        )


def extract_comment_people(
    *,
    markdown: str | None = None,
    html: str | None = None,
    source_url: str,
    max_handles: int = DEFAULT_MAX_HANDLES_PER_PAGE,
    budget: ContactExtractionBudget | None = None,
) -> list[ExtractedCommentPerson]:
    """Extract unique comment authors from page markdown/HTML."""
    parts: list[str] = []
    if markdown:
        parts.append(markdown)
    if html:
        parts.append(html)
    if not parts:
        return []

    combined = "\n".join(parts)
    platform = detect_platform(source_url)
    if platform == "4chan":
        return []

    article_text, comments_text = _split_article_and_comments(combined)
    article_author_handles = _extract_article_author_handles(
        article_text,
        platform=platform,
        source_url=source_url,
    )

    acc = _HandleAccumulator()
    if platform == "reddit":
        _extract_reddit_handles(
            acc,
            comments_text,
            source_url=source_url,
            article_author_handles=article_author_handles,
            max_handles=max_handles,
        )
    else:
        _extract_blog_handles(
            acc,
            html=html,
            comments_text=comments_text,
            platform=platform,
            source_url=source_url,
            article_author_handles=article_author_handles,
            max_handles=max_handles,
        )
        if platform != "reddit":
            _extract_reddit_handles(
                acc,
                comments_text,
                source_url=source_url,
                article_author_handles=article_author_handles,
                max_handles=max_handles,
            )

    if len(acc.people) < 3 and budget is not None and budget.spark_decode_remaining > 0:
        extract_comment_people_spark(
            comments_text,
            source_url=source_url,
            platform=platform,
            budget=budget,
            article_author_handles=article_author_handles,
            max_handles=max_handles,
            acc=acc,
        )

    return list(acc.people.values())
