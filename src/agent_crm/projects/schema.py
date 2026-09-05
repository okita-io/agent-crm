"""Pydantic schema for on-disk project YAML files."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ProjectChannelName = Literal[
    "research",
    "hunter",
    "seo",
    "aeo_geo",
    "engage",
    "publish",
]

CHANNEL_NAMES: tuple[ProjectChannelName, ...] = (
    "research",
    "hunter",
    "seo",
    "aeo_geo",
    "engage",
    "publish",
)


class ProjectStatus(str, Enum):
    LIVE = "live"
    PRE_LAUNCH = "pre_launch"
    RENAMED = "renamed"
    PAUSED = "paused"


class ProjectChannel(BaseModel):
    armed: bool = True
    prompt: str = ""


class ProjectDocument(BaseModel):
    """One ``projects/{slug}.yaml`` file."""

    slug: str
    name: str
    status: ProjectStatus = ProjectStatus.LIVE
    enabled: bool = True
    site: str | None = None
    alias: str | None = None
    context_file: str | None = None
    origin_prompt: str = ""
    channels: dict[str, ProjectChannel] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _slug_ok(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned or "/" in cleaned or "\\" in cleaned or ".." in cleaned:
            raise ValueError("invalid project slug")
        if not all(ch.isalnum() or ch in "-_" for ch in cleaned):
            raise ValueError("slug must be alphanumeric with - or _")
        return cleaned

    @model_validator(mode="after")
    def _channels_complete(self) -> ProjectDocument:
        unknown = set(self.channels) - set(CHANNEL_NAMES)
        if unknown:
            raise ValueError(f"unknown channels: {sorted(unknown)}")
        for name in CHANNEL_NAMES:
            if name not in self.channels:
                self.channels[name] = ProjectChannel(armed=False, prompt="")
        return self

    def armed_channels(self) -> list[str]:
        return [name for name in CHANNEL_NAMES if self.channels[name].armed]

    def seeded_loops(self) -> list[str]:
        mapping = {
            "research": "research-loop",
            "hunter": "hunt-loop",
            "seo": "seo-loop",
            "aeo_geo": "aeo-geo-loop",
            "engage": "engagement-loop",
            "publish": "publisher",
        }
        return [mapping[name] for name in self.armed_channels() if self.enabled]

    def to_yaml_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "status": self.status.value,
            "enabled": self.enabled,
            "site": self.site,
            "alias": self.alias,
            "context_file": self.context_file,
            "origin_prompt": self.origin_prompt,
            "channels": {
                name: {
                    "armed": self.channels[name].armed,
                    "prompt": self.channels[name].prompt,
                }
                for name in CHANNEL_NAMES
            },
        }


def default_channels(*, all_armed: bool = False) -> dict[str, ProjectChannel]:
    return {
        name: ProjectChannel(armed=all_armed, prompt="")
        for name in CHANNEL_NAMES
    }
