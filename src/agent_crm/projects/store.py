"""Read/write project YAML files under CRM_PROJECTS_DIR."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_crm.config import get_settings
from agent_crm.enums import Brand
from agent_crm.errors import ConflictError, NotFoundError, ValidationAppError
from agent_crm.marketing_skill import brand_context_path

from .schema import (
    CHANNEL_NAMES,
    ProjectChannel,
    ProjectChannelName,
    ProjectDocument,
    ProjectStatus,
    default_channels,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _LiteralStr(str):
    """Marker so PyYAML dumps multiline prompts as ``|`` block scalars."""


def _represent_literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.add_representer(_LiteralStr, _represent_literal_str, Dumper=yaml.SafeDumper)


def _for_yaml(value: object) -> object:
    if isinstance(value, str) and "\n" in value:
        return _LiteralStr(value)
    if isinstance(value, dict):
        return {k: _for_yaml(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_for_yaml(v) for v in value]
    return value


def projects_dir() -> Path:
    return get_settings().resolved_projects_dir


def brand_for_slug(slug: str) -> Brand | None:
    for brand in Brand:
        if brand != Brand.UNASSIGNED and brand.value == slug:
            return brand
    return None


def _path_for(slug: str) -> Path:
    if not _SLUG_RE.match(slug):
        raise ValidationAppError(f"invalid project slug: {slug}")
    return projects_dir() / f"{slug}.yaml"


def _load_file(path: Path) -> ProjectDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise NotFoundError(f"project file unreadable: {path.name}") from exc
    except yaml.YAMLError as exc:
        raise ValidationAppError(f"invalid YAML in {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationAppError(f"project file must be a mapping: {path.name}")
    try:
        doc = ProjectDocument.model_validate(raw)
    except ValidationError as exc:
        raise ValidationAppError(f"invalid project {path.name}: {exc}") from exc
    if doc.slug != path.stem:
        raise ValidationAppError(
            f"slug {doc.slug!r} does not match filename {path.stem!r}"
        )
    return doc


def _atomic_write(path: Path, doc: ProjectDocument) -> ProjectDocument:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.dump(
        _for_yaml(doc.to_yaml_dict()),
        Dumper=yaml.SafeDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    _invalidate_cache()
    return doc


def list_projects() -> list[ProjectDocument]:
    root = projects_dir()
    if not root.is_dir():
        return []
    docs: list[ProjectDocument] = []
    for path in sorted(root.glob("*.yaml")):
        if path.name.startswith("."):
            continue
        try:
            docs.append(_load_file(path))
        except (ValidationAppError, NotFoundError) as exc:
            logger.warning("skipping project file %s: %s", path.name, exc)
    return docs


def get_project(slug: str) -> ProjectDocument:
    path = _path_for(slug)
    if not path.is_file():
        raise NotFoundError(f"project not found: {slug}")
    return _load_file(path)


def create_project(
    *,
    slug: str,
    name: str,
    site: str | None = None,
    origin_prompt: str = "",
    alias: str | None = None,
    status: ProjectStatus = ProjectStatus.LIVE,
    enabled: bool = True,
    context_file: str | None = None,
    channels: dict[str, ProjectChannel] | None = None,
) -> ProjectDocument:
    path = _path_for(slug)
    if path.is_file():
        raise ConflictError(f"project already exists: {slug}")
    brand = brand_for_slug(slug)
    if context_file is None and brand is not None:
        mapped = brand_context_path(brand)
        context_file = mapped.name if mapped else None
    doc = ProjectDocument(
        slug=slug,
        name=name,
        status=status,
        enabled=enabled,
        site=site,
        alias=alias,
        context_file=context_file,
        origin_prompt=origin_prompt,
        channels=channels or default_channels(all_armed=False),
    )
    return _atomic_write(path, doc)


def patch_project(
    slug: str,
    *,
    name: str | None = None,
    site: str | None | object = ...,
    alias: str | None | object = ...,
    status: ProjectStatus | None = None,
    origin_prompt: str | None = None,
    enabled: bool | None = None,
    context_file: str | None | object = ...,
) -> ProjectDocument:
    doc = get_project(slug)
    data = doc.model_dump()
    if name is not None:
        data["name"] = name
    if site is not ...:
        data["site"] = site
    if alias is not ...:
        data["alias"] = alias
    if status is not None:
        data["status"] = status
    if origin_prompt is not None:
        data["origin_prompt"] = origin_prompt
    if enabled is not None:
        data["enabled"] = enabled
    if context_file is not ...:
        data["context_file"] = context_file
    updated = ProjectDocument.model_validate(data)
    return _atomic_write(_path_for(slug), updated)


def update_channels(slug: str, armed: dict[str, bool]) -> ProjectDocument:
    doc = get_project(slug)
    unknown = set(armed) - set(CHANNEL_NAMES)
    if unknown:
        raise ValidationAppError(f"unknown channels: {sorted(unknown)}")
    channels = {name: ch.model_copy() for name, ch in doc.channels.items()}
    for name, value in armed.items():
        channels[name] = ProjectChannel(
            armed=bool(value),
            prompt=channels[name].prompt,
        )
    updated = doc.model_copy(update={"channels": channels})
    return _atomic_write(_path_for(slug), updated)


def update_prompts(
    slug: str,
    *,
    origin_prompt: str | None = None,
    channel_prompts: dict[str, str] | None = None,
) -> ProjectDocument:
    doc = get_project(slug)
    channels = {name: ch.model_copy() for name, ch in doc.channels.items()}
    if channel_prompts:
        unknown = set(channel_prompts) - set(CHANNEL_NAMES)
        if unknown:
            raise ValidationAppError(f"unknown channels: {sorted(unknown)}")
        for name, prompt in channel_prompts.items():
            channels[name] = ProjectChannel(
                armed=channels[name].armed,
                prompt=prompt,
            )
    data = doc.model_dump()
    data["channels"] = channels
    if origin_prompt is not None:
        data["origin_prompt"] = origin_prompt
    updated = ProjectDocument.model_validate(data)
    return _atomic_write(_path_for(slug), updated)


def _product_section_from_md(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.find("## Product")
    if start < 0:
        return text.strip()
    end = len(text)
    next_h2 = text.find("\n## ", start + len("## Product"))
    if next_h2 > start:
        end = next_h2
    return text[start:end].strip()


def reload_context(slug: str) -> ProjectDocument:
    doc = get_project(slug)
    brand = brand_for_slug(slug)
    path: Path | None = None
    if doc.context_file:
        candidate = Path(__file__).resolve().parents[3] / doc.context_file
        if candidate.is_file():
            path = candidate
        docker = Path("/app") / doc.context_file
        if path is None and docker.is_file():
            path = docker
    if path is None and brand is not None:
        path = brand_context_path(brand)
    if path is None or not path.is_file():
        raise NotFoundError(f"brand context not found for {slug}")
    origin = _product_section_from_md(path)
    return update_prompts(slug, origin_prompt=origin)


def projects_stats(docs: list[ProjectDocument] | None = None) -> dict[str, int]:
    items = docs if docs is not None else list_projects()
    live_sites = sum(1 for d in items if d.site)
    pre_launch = sum(1 for d in items if d.status == ProjectStatus.PRE_LAUNCH)
    armed = sum(len(d.armed_channels()) for d in items if d.enabled)
    total = len(CHANNEL_NAMES) * len(items)
    return {
        "projects": len(items),
        "live_sites": live_sites,
        "pre_launch": pre_launch,
        "channels_armed": armed,
        "channels_total": total,
    }


# Cache invalidation hook used by channel_flags.
_cache_generation = 0


def _invalidate_cache() -> None:
    global _cache_generation
    _cache_generation += 1
    from agent_crm.projects.channel_flags import clear_channel_cache

    clear_channel_cache()


def cache_generation() -> int:
    return _cache_generation
