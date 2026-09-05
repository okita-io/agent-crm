"""File-backed project origins (YAML catalog)."""

from __future__ import annotations

from .channel_flags import PROJECT_CHANNELS, active_brands_for, project_prompt_for
from .schema import ProjectChannel, ProjectChannelName, ProjectDocument, ProjectStatus
from .store import (
    create_project,
    get_project,
    list_projects,
    patch_project,
    projects_stats,
    reload_context,
    update_channels,
    update_prompts,
)

__all__ = [
    "PROJECT_CHANNELS",
    "ProjectChannel",
    "ProjectChannelName",
    "ProjectDocument",
    "ProjectStatus",
    "active_brands_for",
    "create_project",
    "get_project",
    "list_projects",
    "patch_project",
    "project_prompt_for",
    "projects_stats",
    "reload_context",
    "update_channels",
    "update_prompts",
]
