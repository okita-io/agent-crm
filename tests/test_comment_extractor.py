"""Tests for comment-thread people extraction and persistence."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_crm.comment_extractor import extract_comment_people, is_valid_comment_handle
from agent_crm.comment_people_store import (
    count_comment_people,
    list_comment_people,
    process_scraped_page_comment_people,
    upsert_comment_person,
)
from agent_crm.contact_extractor import extract_contacts
from agent_crm.db import init_db, reset_engine, session_scope
from agent_crm.enums import Brand, ContactAudience
from agent_crm.hunt_feedback import (
    HuntFeedbackBudget,
    enqueue_handle_terms,
    handle_search_terms,
)
from agent_crm.hunt_store import HuntStore
from agent_crm.models import CommentPerson
from sqlalchemy import select


REDDIT_THREAD_MARKDOWN = """# Best indie games of 2026

Posted by u/article_author

## Comments

**u/curious_reader** • 2 points • 3 hours ago
I loved this list, especially the roguelikes.

[u/helpful_dev](/u/helpful_dev) wrote:
Author of a small Unity toolkit here — happy to share notes.

**u/AutoModerator** • stickied
Welcome! Read the rules.

**u/[deleted]** • 1 point
[removed]

**u/verified_creator** • 42 points
Verified author of Pixel Drift. Check out my YouTube channel for devlogs.
https://youtube.com/watch?v=example
"""

BLOG_COMMENTS_HTML = """
<article>
  <p class="byline">By Jane Articlewriter</p>
  <p>Main article body about product launches.</p>
</article>
<section class="comments">
  <div class="comment">
    <span class="comment-author">Sam Commenter</span>
    <p>Great post, thanks for sharing.</p>
  </div>
  <div class="comment">
    <cite>Maria Reader</cite> said:
    <p>This helped me choose a stack.</p>
  </div>
</section>
"""

BLOG_COMMENTS_MARKDOWN = """
# Product launch recap

By Jane Articlewriter

## Comments

Sam Commenter said: Great post, thanks for sharing.
"""


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    db_path = tmp_path / "comment_people.db"
    monkeypatch.setenv("CRM_DATABASE_URL", f"sqlite:///{db_path}")
    from agent_crm.config import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield db_path
    reset_engine()
    get_settings.cache_clear()


def test_reddit_thread_extracts_unique_handles_skips_bots() -> None:
    people = extract_comment_people(
        markdown=REDDIT_THREAD_MARKDOWN,
        source_url="https://www.reddit.com/r/indiegaming/comments/abc123/thread/",
    )
    handles = {person.handle.lower() for person in people}
    assert "curious_reader" in handles
    assert "helpful_dev" in handles
    assert "verified_creator" in handles
    assert "automoderator" not in handles
    assert "deleted" not in handles
    assert "removed" not in handles
    assert len(handles) == len(people)


def test_reddit_skips_article_author_as_commenter() -> None:
    people = extract_comment_people(
        markdown=REDDIT_THREAD_MARKDOWN,
        source_url="https://www.reddit.com/r/indiegaming/comments/abc123/thread/",
    )
    handles = {person.handle.lower() for person in people}
    assert "article_author" not in handles


def test_influencer_heuristic_on_reddit_comment() -> None:
    people = extract_comment_people(
        markdown=REDDIT_THREAD_MARKDOWN,
        source_url="https://www.reddit.com/r/indiegaming/comments/abc123/thread/",
    )
    creator = next(person for person in people if person.handle == "verified_creator")
    assert creator.audience == ContactAudience.INFLUENCER


def test_blog_comment_html_authors_captured() -> None:
    people = extract_comment_people(
        markdown=BLOG_COMMENTS_MARKDOWN,
        html=BLOG_COMMENTS_HTML,
        source_url="https://blog.example.com/launch-recap",
    )
    handles = {person.handle for person in people}
    assert "samcommenter" in handles
    assert "mariareader" in handles
    assert "janearticlewriter" not in handles


def test_no_email_invented_for_comment_people(db_url) -> None:
    profiles = process_scraped_page_comment_people(
        markdown=REDDIT_THREAD_MARKDOWN,
        source_url="https://www.reddit.com/r/indiegaming/comments/abc123/thread/",
        brand=Brand.MIDNIGHTSATIN,
    )
    assert profiles
    with session_scope() as session:
        rows = list(session.scalars(select(CommentPerson)))
    assert rows
    for row in rows:
        assert "@" not in row.handle


def test_hunt_loop_email_extraction_still_works() -> None:
    markdown = "Ada Vega <ada.vega@romancebooks.test> moderates the community."
    emails = extract_contacts(markdown=markdown)
    assert len(emails) == 1
    assert emails[0].email == "ada.vega@romancebooks.test"


def test_upsert_comment_person_merges_sources(db_url) -> None:
    first = upsert_comment_person(
        platform="reddit",
        handle="curious_reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://reddit.com/r/test/comments/1/a",
        comment_snippet="first comment",
        audience=ContactAudience.USER,
    )
    second = upsert_comment_person(
        platform="reddit",
        handle="curious_reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://reddit.com/r/test/comments/2/b",
        comment_snippet="second comment",
        audience=ContactAudience.USER,
    )
    assert first.id == second.id
    assert len(second.source_urls or []) == 2
    assert count_comment_people() == 1


def test_handle_search_terms_for_reddit() -> None:
    terms = handle_search_terms("reddit", "curious_reader")
    assert any("site:reddit.com/u/curious_reader" in term for term in terms)
    assert all("@" not in term for term in terms)


def test_enqueue_handle_terms(db_url) -> None:
    store = HuntStore()
    budget = HuntFeedbackBudget(
        community_terms_remaining=0,
        person_terms_remaining=0,
        handle_terms_remaining=5,
        engagement_terms_remaining=0,
    )
    enqueued = enqueue_handle_terms(
        store,
        platform="reddit",
        handle="curious_reader",
        brand=Brand.MIDNIGHTSATIN,
        run_id="test-run",
        budget=budget,
    )
    assert enqueued >= 2
    queries = store.list_feedback_queries(brand=Brand.MIDNIGHTSATIN, limit=20)
    handle_queries = [row for row in queries if row.origin.startswith("handle:")]
    assert handle_queries
    assert all("@" not in row.query for row in handle_queries)


def test_is_valid_comment_handle_rejects_deleted() -> None:
    assert not is_valid_comment_handle("reddit", "[deleted]")
    assert not is_valid_comment_handle("reddit", "AutoModerator")
    assert is_valid_comment_handle("reddit", "curious_reader")


def test_list_comment_people_api(db_url) -> None:
    upsert_comment_person(
        platform="reddit",
        handle="curious_reader",
        brand=Brand.MIDNIGHTSATIN,
        source_url="https://reddit.com/r/test",
    )
    rows = list_comment_people(brand=Brand.MIDNIGHTSATIN)
    assert len(rows) == 1
    assert rows[0].handle == "curious_reader"


def test_spark_comment_extraction_uses_budget_when_few_handles() -> None:
    from agent_crm.contact_store import ContactExtractionBudget

    markdown = "## Comments\n\nSome thread with obfuscated author mentions."
    budget = ContactExtractionBudget(
        social_lookups_remaining=0,
        enrichments_remaining=0,
        spark_enrichments_remaining=0,
        spark_decode_remaining=1,
    )
    with patch("agent_crm.llm_client.chat_completions") as mock_llm:
        mock_llm.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"people":[{"platform":"reddit","handle":"spark_user",'
                            '"display_name":null,"comment_snippet":"hello"}]}'
                        )
                    }
                }
            ]
        }
        people = extract_comment_people(
            markdown=markdown,
            source_url="https://www.reddit.com/r/test/comments/abc/thread/",
            budget=budget,
        )
    assert any(person.handle == "spark_user" for person in people)
