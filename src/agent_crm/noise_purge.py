"""One-shot cleanup of deny-listed hunt resources and contacts sourced from them."""

from __future__ import annotations

from sqlalchemy import select

from .contact_quality import is_share_link_social_url
from .db import session_scope
from .enums import ContactEmailKind
from .hunt_relevance import is_obvious_off_topic_url
from .models import ContactProfile, HuntResource


def purge_denied_ingest(*, dry_run: bool = True) -> dict[str, int]:
    """Drop mozilla/docker/aggregator hunt rows and mark sourced contacts junk."""
    resource_ids: list[int] = []
    contact_ids: list[int] = []
    with session_scope() as session:
        for row in session.scalars(select(HuntResource)):
            if is_obvious_off_topic_url(row.url):
                resource_ids.append(row.id)
                if not dry_run:
                    session.delete(row)
        for profile in session.scalars(select(ContactProfile)):
            urls = [
                url
                for url in (profile.source_urls or [])
                if isinstance(url, str) and url.strip()
            ]
            denied_source = any(is_obvious_off_topic_url(url) for url in urls)
            socials = profile.socials if isinstance(profile.socials, dict) else {}
            only_share_links = bool(socials) and all(
                is_share_link_social_url(str(value))
                for value in socials.values()
                if value
            )
            if denied_source or only_share_links:
                contact_ids.append(profile.id)
                if not dry_run:
                    profile.email_kind = ContactEmailKind.JUNK
    return {
        "resources_matched": len(resource_ids),
        "contacts_matched": len(contact_ids),
        "dry_run": int(dry_run),
    }
