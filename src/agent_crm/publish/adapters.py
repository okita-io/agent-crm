"""Outbound adapters for the publisher worker."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from agent_crm.config import get_settings
from agent_crm.enums import SocialPlatform
from agent_crm.models import PublishJob, SocialAccount

logger = logging.getLogger(__name__)

_REDDIT_COMMENT_RE = re.compile(
    r"/comments/(?P<subid>[a-z0-9]+)",
    re.IGNORECASE,
)
_REDDIT_THING_RE = re.compile(
    r"/comments/[a-z0-9]+/[^/]+/(?P<comment>[a-z0-9]+)",
    re.IGNORECASE,
)


class RateLimitedError(Exception):
    """Platform asked us to wait before retrying."""

    def __init__(self, message: str, *, retry_after_seconds: int = 300) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(retry_after_seconds, 60)


class PublishAdapterError(Exception):
    """Non-retryable adapter failure."""


@dataclass(frozen=True)
class PublishResult:
    posted_url: str | None
    platform_post_id: str | None
    dry_run: bool = False


class PublishAdapter(Protocol):
    def publish(self, job: PublishJob, account: SocialAccount) -> PublishResult: ...


def reddit_thing_id(target_url: str) -> str:
    """Parse a Reddit submission or comment thing id from a permalink."""
    path = urlparse(target_url).path or ""
    comment = _REDDIT_THING_RE.search(path)
    if comment:
        return f"t1_{comment.group('comment')}"
    submission = _REDDIT_COMMENT_RE.search(path)
    if submission:
        return f"t3_{submission.group('subid')}"
    raise PublishAdapterError(f"cannot parse Reddit thing id from {target_url!r}")


class DryRunAdapter:
    """Logs would-post and returns a synthetic id. Default for phase 1."""

    def publish(self, job: PublishJob, account: SocialAccount) -> PublishResult:
        logger.info(
            "publish dry-run brand=%s platform=%s account=%s target=%s body_chars=%s",
            job.brand.value,
            job.platform.value,
            account.handle,
            job.target_url,
            len(job.body),
        )
        return PublishResult(
            posted_url=job.target_url,
            platform_post_id=f"dry-run-{job.id}",
            dry_run=True,
        )


def _credential_env(prefix: str, name: str) -> str:
    return os.environ.get(f"CRM_SOCIAL_{prefix}_{name}", "").strip()


def _reddit_credentials(account: SocialAccount) -> dict[str, str]:
    settings = get_settings()
    key = (account.credential_key or "").strip().upper()
    if key:
        creds = {
            "client_id": _credential_env(key, "CLIENT_ID"),
            "client_secret": _credential_env(key, "CLIENT_SECRET"),
            "username": _credential_env(key, "USERNAME"),
            "password": _credential_env(key, "PASSWORD"),
            "user_agent": _credential_env(key, "USER_AGENT")
            or f"agent-crm/publisher by /u/{account.handle}",
        }
    else:
        creds = {
            "client_id": settings.reddit_client_id,
            "client_secret": settings.reddit_client_secret,
            "username": settings.reddit_username,
            "password": settings.reddit_password,
            "user_agent": settings.reddit_user_agent
            or f"agent-crm/publisher by /u/{account.handle}",
        }
    missing = [name for name, value in creds.items() if name != "user_agent" and not value]
    if missing:
        raise PublishAdapterError(
            f"missing Reddit credentials for account {account.id}: {', '.join(missing)}"
        )
    return creds


class RedditCommentAdapter:
    """Post a comment on an existing Reddit thread via official OAuth."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def _access_token(self, creds: dict[str, str]) -> str:
        auth = (creds["client_id"], creds["client_secret"])
        data = {
            "grant_type": "password",
            "username": creds["username"],
            "password": creds["password"],
        }
        headers = {"User-Agent": creds["user_agent"]}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                "https://www.reddit.com/api/v1/access_token",
                data=data,
                auth=auth,
                headers=headers,
            )
        if response.status_code == 429:
            raise RateLimitedError("Reddit auth rate limited", retry_after_seconds=300)
        if response.status_code >= 400:
            raise PublishAdapterError(
                f"Reddit auth failed ({response.status_code}): {response.text[:300]}"
            )
        token = (response.json() or {}).get("access_token")
        if not token:
            raise PublishAdapterError("Reddit auth returned no access_token")
        return str(token)

    def publish(self, job: PublishJob, account: SocialAccount) -> PublishResult:
        if not job.target_url:
            raise PublishAdapterError("Reddit comment jobs require target_url")
        thing_id = reddit_thing_id(job.target_url)
        creds = _reddit_credentials(account)
        token = self._access_token(creds)
        headers = {
            "Authorization": f"bearer {token}",
            "User-Agent": creds["user_agent"],
        }
        data = {"thing_id": thing_id, "text": job.body, "api_type": "json"}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                "https://oauth.reddit.com/api/comment",
                data=data,
                headers=headers,
            )
        if response.status_code == 429:
            raise RateLimitedError("Reddit comment rate limited", retry_after_seconds=600)
        payload = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}
        errors = (payload.get("json") or {}).get("errors") or []
        if errors:
            flat = " ".join(str(part) for err in errors for part in err)
            if "RATELIMIT" in flat.upper() or "try again" in flat.lower():
                raise RateLimitedError(flat, retry_after_seconds=600)
            raise PublishAdapterError(f"Reddit comment rejected: {flat[:500]}")
        if response.status_code >= 400:
            raise PublishAdapterError(
                f"Reddit comment failed ({response.status_code}): {response.text[:300]}"
            )
        things = (
            ((payload.get("json") or {}).get("data") or {}).get("things") or []
        )
        comment_data = (things[0] or {}).get("data") if things else {}
        comment_id = comment_data.get("id") or comment_data.get("name")
        permalink = comment_data.get("permalink")
        posted_url = (
            f"https://www.reddit.com{permalink}"
            if permalink
            else job.target_url
        )
        return PublishResult(
            posted_url=posted_url,
            platform_post_id=str(comment_id) if comment_id else None,
            dry_run=False,
        )


class PostizOwnedFeedAdapter:
    """Schedule an owned-feed post through self-hosted Postiz public API."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def publish(self, job: PublishJob, account: SocialAccount) -> PublishResult:
        settings = get_settings()
        base = (settings.postiz_base_url or "").rstrip("/")
        api_key = (settings.postiz_api_key or "").strip()
        integration_id = (account.postiz_integration_id or "").strip()
        if not base or not api_key:
            raise PublishAdapterError("CRM_POSTIZ_BASE_URL and CRM_POSTIZ_API_KEY required")
        if not integration_id:
            raise PublishAdapterError(
                f"social account {account.id} missing postiz_integration_id"
            )

        post_block: dict = {
            "integration": {"id": integration_id},
            "value": [{"content": job.body, "image": []}],
            "settings": {"__type": job.platform.value},
        }
        payload_extra = job.payload_json or {}
        if isinstance(payload_extra.get("settings"), dict):
            post_block["settings"] = {
                **post_block["settings"],
                **payload_extra["settings"],
            }
        if payload_extra.get("media"):
            post_block["value"][0]["image"] = list(payload_extra["media"])

        body = {
            "type": "now" if job.scheduled_at else "schedule",
            "date": job.scheduled_at.isoformat().replace("+00:00", "Z"),
            "shortLink": False,
            "tags": [],
            "posts": [post_block],
        }
        headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{base}/public/v1/posts",
                json=body,
                headers=headers,
            )
        if response.status_code == 429:
            raise RateLimitedError("Postiz rate limited", retry_after_seconds=120)
        if response.status_code >= 400:
            raise PublishAdapterError(
                f"Postiz create failed ({response.status_code}): {response.text[:400]}"
            )
        data = {}
        try:
            data = response.json()
        except Exception:
            data = {}
        post_id = None
        if isinstance(data, dict):
            post_id = data.get("id") or data.get("postId")
            if not post_id and isinstance(data.get("posts"), list) and data["posts"]:
                first = data["posts"][0]
                if isinstance(first, dict):
                    post_id = first.get("id") or first.get("releaseId")
        return PublishResult(
            posted_url=None,
            platform_post_id=str(post_id) if post_id else None,
            dry_run=False,
        )


def adapter_for(
    job: PublishJob,
    account: SocialAccount,
    *,
    force_dry_run: bool = False,
) -> PublishAdapter:
    if force_dry_run or job.dry_run or get_settings().publish_dry_run:
        return DryRunAdapter()
    if (
        job.platform == SocialPlatform.REDDIT
        and job.target_url
        and not account.postiz_integration_id
    ):
        return RedditCommentAdapter()
    if account.postiz_integration_id:
        return PostizOwnedFeedAdapter()
    if job.platform == SocialPlatform.REDDIT:
        return RedditCommentAdapter()
    raise PublishAdapterError(
        f"no adapter for platform={job.platform.value} account={account.id}"
    )
