"""Dashboard UI module: tabs.contacts."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent_crm.contacts.comment_people_store import count_comment_people, list_comment_people
from agent_crm.contacts.store import count_contact_profiles, count_contact_profiles_by_brand, count_contact_profiles_by_quality, list_contact_profiles
from agent_crm.enums import AgencyRequestStatus, AgentStatus, Brand, ContactAudience, ContactVerificationStatus, HuntResourceKind, LeadSource, ResearchFindingKind, SeoPlanKind, SeoReviewKind, Stage

from agent_crm.dashboard_ui.common import (
    _export_filename,
    _render_full_csv_export,
)

def _contact_profile_rows(
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
    quality: str,
    limit: int | None = 100,
    offset: int = 0,
) -> pd.DataFrame:
    profiles = list_contact_profiles(
        brand=brand,
        audience=audience,
        quality=quality,
        limit=limit,
        offset=offset,
    )
    if not profiles:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "name": row.name,
                "title": row.title,
                "organization": row.organization,
                "location": row.location,
                "email": row.email,
                "brand": row.brand.value,
                "audience": row.audience.value if row.audience else None,
                "socials": json.dumps(row.socials) if row.socials else None,
                "source pages": ", ".join(row.source_urls or []),
                "lead_id": row.lead_id,
                "updated": row.updated_at,
            }
            for row in profiles
        ]
    )


def _comment_people_rows(
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
    limit: int | None = 100,
    offset: int = 0,
) -> pd.DataFrame:
    people = list_comment_people(
        brand=brand,
        audience=audience,
        limit=limit,
        offset=offset,
    )
    if not people:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "platform": row.platform,
                "handle": row.handle,
                "display name": row.display_name,
                "profile": row.profile_url,
                "brand": row.brand.value,
                "audience": row.audience.value if row.audience else None,
                "source pages": ", ".join(row.source_urls or []),
                "snippet": (
                    (row.comment_snippets or [{}])[-1].get("snippet")
                    if row.comment_snippets
                    else None
                ),
                "updated": row.updated_at,
            }
            for row in people
        ]
    )


def _render_contacts_tab() -> None:
    st.subheader("Contact profiles")
    st.caption(
        "People found on scraped hunter/research pages. Email contacts are keyed by address; "
        "comment authors are keyed by platform + handle (no email invented). "
        "Title, organization, and location on email rows come from public-web enrichment."
    )

    CONTACTS_PAGE_SIZE = 100

    view_filter = st.selectbox(
        "View",
        options=["emails", "commenters", "all"],
        index=0,
        key="contacts_view",
    )

    brand_filter = st.selectbox(
        "Brand filter",
        options=["all"] + [b.value for b in Brand if b != Brand.UNASSIGNED],
        key="contacts_brand",
    )
    audience_filter = st.selectbox(
        "Audience filter",
        options=[
            "all",
            "marketing",
            "influencer",
            "end_user",
            "b2b",
            "client",
            "user",
        ],
        key="contacts_audience",
    )
    quality_filter = st.selectbox(
        "Email quality",
        options=["person", "role", "all"],
        index=0,
        key="contacts_quality",
    )
    brand = None if brand_filter == "all" else Brand(brand_filter)
    audience = (
        None
        if audience_filter == "all"
        else ContactAudience(audience_filter)
    )
    quality = quality_filter

    filter_key = f"{view_filter}:{brand_filter}:{audience_filter}:{quality_filter}"
    if st.session_state.get("contacts_filter_key") != filter_key:
        st.session_state.contacts_filter_key = filter_key
        st.session_state.contacts_page = 0
    if "contacts_page" not in st.session_state:
        st.session_state.contacts_page = 0

    if view_filter in {"commenters", "all"}:
        comment_total = count_comment_people(brand=brand, audience=audience)
        st.caption(f"Comment authors (handles): **{comment_total}**")

    if view_filter == "commenters":
        _render_comment_people_table(
            brand=brand,
            audience=audience,
            page_size=CONTACTS_PAGE_SIZE,
        )
        return

    quality_counts = count_contact_profiles_by_quality(brand=brand, audience=audience)
    total = count_contact_profiles(brand=brand, audience=audience, quality=quality)
    st.caption(
        f"person **{quality_counts['person']}** / "
        f"role **{quality_counts['role']}** / "
        f"total **{quality_counts['total']}**"
    )

    if quality_counts["total"] == 0:
        st.info("No contact profiles yet. Run hunter or research scrapes to extract emails.")
        return

    if brand is None and quality == "all":
        by_brand = count_contact_profiles_by_brand(audience=audience)
        brand_cols = st.columns(max(len(by_brand), 1))
        for col, row in zip(brand_cols, by_brand, strict=False):
            col.metric(row["brand"], row["count"])
    else:
        st.metric("Profiles", total)

    page = st.session_state.contacts_page
    max_page = max((total - 1) // CONTACTS_PAGE_SIZE, 0)
    page = min(page, max_page)
    st.session_state.contacts_page = page
    offset = page * CONTACTS_PAGE_SIZE

    profiles = list_contact_profiles(
        brand=brand,
        audience=audience,
        quality=quality,
        limit=CONTACTS_PAGE_SIZE,
        offset=offset,
    )

    if profiles:
        showing_start = offset + 1
        showing_end = offset + len(profiles)
        st.caption(f"Showing {showing_start}–{showing_end} of {total} matching")
    else:
        st.caption(f"Showing 0 of {total} matching")

    _render_full_csv_export(
        key="contact_profiles",
        filename=_export_filename(
            "contacts",
            brand.value if brand else None,
            audience.value if audience else None,
            None if quality == "all" else quality,
        ),
        fetch_all=lambda: _contact_profile_rows(
            brand=brand,
            audience=audience,
            quality=quality,
            limit=None,
        ),
        preview_count=len(profiles),
        filter_key=filter_key,
        caption=(
            f"Table shows {CONTACTS_PAGE_SIZE} per page. "
            f"Full export includes all {total} matching rows."
        ),
    )

    nav_prev, nav_next, _ = st.columns([1, 1, 6])
    if nav_prev.button("Previous", disabled=page <= 0, key="contacts_prev"):
        st.session_state.contacts_page = max(page - 1, 0)
        st.rerun()
    if nav_next.button("Next", disabled=page >= max_page, key="contacts_next"):
        st.session_state.contacts_page = min(page + 1, max_page)
        st.rerun()

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "name": row.name,
                    "title": row.title,
                    "organization": row.organization,
                    "location": row.location,
                    "email": row.email,
                    "brand": row.brand.value,
                    "audience": row.audience.value if row.audience else None,
                    "socials": json.dumps(row.socials) if row.socials else None,
                    "source pages": ", ".join(row.source_urls or []),
                    "lead_id": row.lead_id,
                    "updated": row.updated_at,
                }
                for row in profiles
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    if view_filter == "all":
        st.divider()
        st.subheader("Comment authors")
        _render_comment_people_table(
            brand=brand,
            audience=audience,
            page_size=CONTACTS_PAGE_SIZE,
            key_prefix="all_",
        )


def _render_comment_people_table(
    *,
    brand: Brand | None,
    audience: ContactAudience | None,
    page_size: int,
    key_prefix: str = "",
) -> None:
    total = count_comment_people(brand=brand, audience=audience)
    if total == 0:
        st.info("No comment authors yet. Run hunter or research scrapes on threads/articles.")
        return

    page_key = f"{key_prefix}comment_people_page"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0
    page = st.session_state[page_key]
    max_page = max((total - 1) // page_size, 0)
    page = min(page, max_page)
    st.session_state[page_key] = page
    offset = page * page_size

    people = list_comment_people(
        brand=brand,
        audience=audience,
        limit=page_size,
        offset=offset,
    )
    if people:
        showing_start = offset + 1
        showing_end = offset + len(people)
        st.caption(f"Showing {showing_start}–{showing_end} of {total} comment authors")
    else:
        st.caption(f"Showing 0 of {total} comment authors")

    _render_full_csv_export(
        key=f"{key_prefix}comment_people",
        filename=_export_filename(
            "comment-authors",
            brand.value if brand else None,
            audience.value if audience else None,
        ),
        fetch_all=lambda: _comment_people_rows(
            brand=brand, audience=audience, limit=None
        ),
        preview_count=len(people),
        filter_key=(
            brand.value if brand else "all",
            audience.value if audience else "all",
            key_prefix,
        ),
        caption=(
            f"Table shows {page_size} per page. "
            f"Full export includes all {total} comment authors."
        ),
    )

    nav_prev, nav_next, _ = st.columns([1, 1, 6])
    if nav_prev.button(
        "Previous",
        disabled=page <= 0,
        key=f"{key_prefix}comment_people_prev",
    ):
        st.session_state[page_key] = max(page - 1, 0)
        st.rerun()
    if nav_next.button(
        "Next",
        disabled=page >= max_page,
        key=f"{key_prefix}comment_people_next",
    ):
        st.session_state[page_key] = min(page + 1, max_page)
        st.rerun()

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "platform": row.platform,
                    "handle": row.handle,
                    "display name": row.display_name,
                    "profile": row.profile_url,
                    "brand": row.brand.value,
                    "audience": row.audience.value if row.audience else None,
                    "source pages": ", ".join(row.source_urls or []),
                    "snippet": (
                        (row.comment_snippets or [{}])[-1].get("snippet")
                        if row.comment_snippets
                        else None
                    ),
                    "updated": row.updated_at,
                }
                for row in people
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

