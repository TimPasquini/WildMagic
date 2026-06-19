"""File-backed authored lore cards.

Lore files live under ``content/lore`` as readable Markdown with fenced TOML metadata.
This module parses them into neutral section records; ``lore_cards.py`` adapts those
records into the live ``LoreCard`` registry so the existing gate/router path stays intact.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LORE_ROOT = Path(__file__).resolve().parents[1] / "content" / "lore"

_LORE_BLOCK_RE = re.compile(r"```toml\s+lore\s*\n(.*?)\n```", re.DOTALL)
_META_BLOCK_RE = re.compile(r"```toml\s+meta\s*\n(.*?)\n```", re.DOTALL)
_LEVEL_HEADING_RE = re.compile(r"(?m)^##\s+Level\s+([0-4])(?:\s*[:\-]\s*(.+?))?\s*$")
_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")


@dataclass(frozen=True)
class FileLoreSection:
    name: str
    topic: str
    title: str
    level: int
    tags: tuple[str, ...]
    triggers: tuple[str, ...]
    subjects: tuple[str, ...]
    description: str
    text: str
    source: Path
    version: int = 1
    draft: bool = False

    @property
    def threshold(self) -> int:
        return self.level

    @property
    def source_label(self) -> str:
        try:
            return str(self.source.relative_to(DEFAULT_LORE_ROOT.parents[1]))
        except ValueError:
            return str(self.source)


class FileLoreError(ValueError):
    """Raised when a file-backed lore card is malformed."""


def parse_lore_file(path: Path) -> tuple[FileLoreSection, ...]:
    """Parse one ``content/lore/*.md`` topic file."""
    raw = path.read_text(encoding="utf-8")
    title = _parse_title(raw, path)
    lore_meta = _parse_lore_metadata(raw, path)
    topic = _required_string(lore_meta, "id", path)
    tags = _string_tuple(lore_meta.get("tags"), "tags", path, required=True)

    matches = list(_LEVEL_HEADING_RE.finditer(raw))
    if not matches:
        raise FileLoreError(f"{path} must contain at least one '## Level N' section")

    sections: list[FileLoreSection] = []
    for index, match in enumerate(matches):
        level = int(match.group(1))
        section_title = (match.group(2) or "").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        section_raw = raw[start:end].strip()
        sections.append(
            _parse_level_section(
                path,
                topic=topic,
                title=section_title or title,
                level=level,
                file_tags=tags,
                section_raw=section_raw,
            )
        )

    names = [section.name for section in sections]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise FileLoreError(f"{path} has duplicate lore section names: {duplicates}")
    return tuple(sections)


def load_file_lore_sections(
    root: Path | None = None, *, include_drafts: bool = False
) -> tuple[FileLoreSection, ...]:
    """Load every Markdown lore file under ``root`` in deterministic order."""
    base = root or DEFAULT_LORE_ROOT
    if not base.exists():
        return ()
    sections: list[FileLoreSection] = []
    for path in sorted(base.glob("*.md")):
        for section in parse_lore_file(path):
            if include_drafts or not section.draft:
                sections.append(section)

    names = [section.name for section in sections]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise FileLoreError(f"duplicate lore section names across files: {duplicates}")
    return tuple(sections)


def _parse_title(raw: str, path: Path) -> str:
    match = _TITLE_RE.search(raw)
    if not match:
        raise FileLoreError(f"{path} must start with a '# Title' heading")
    return match.group(1).strip()


def _parse_lore_metadata(raw: str, path: Path) -> dict[str, Any]:
    match = _LORE_BLOCK_RE.search(raw)
    if not match:
        raise FileLoreError(f"{path} must contain a fenced 'toml lore' metadata block")
    return _loads_toml(match.group(1), path, "lore")


def _parse_level_section(
    path: Path,
    *,
    topic: str,
    title: str,
    level: int,
    file_tags: tuple[str, ...],
    section_raw: str,
) -> FileLoreSection:
    match = _META_BLOCK_RE.search(section_raw)
    if not match:
        raise FileLoreError(f"{path} Level {level} must contain a 'toml meta' block")
    meta = _loads_toml(match.group(1), path, f"Level {level} meta")
    description = _required_string(meta, "description", path)
    name = str(meta.get("name") or f"{topic}:{level}").strip()
    if not name:
        raise FileLoreError(f"{path} Level {level} has an empty name")
    tags = _string_tuple(meta.get("tags"), "tags", path, required=False) or file_tags
    triggers = _string_tuple(meta.get("triggers"), "triggers", path, required=False)
    subjects = _string_tuple(meta.get("subjects"), "subjects", path, required=False)
    version = _positive_int(meta.get("version", 1), "version", path)
    draft = _bool_value(meta.get("draft", False), "draft", path)
    body = (section_raw[: match.start()] + section_raw[match.end() :]).strip()
    if not body:
        raise FileLoreError(f"{path} Level {level} must contain body text")
    return FileLoreSection(
        name=name,
        topic=topic,
        title=title,
        level=level,
        tags=tags,
        triggers=tuple(dict.fromkeys((*triggers, *subjects))),
        subjects=subjects,
        description=description,
        text=body,
        source=path,
        version=version,
        draft=draft,
    )


def _loads_toml(raw: str, path: Path, label: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise FileLoreError(f"{path} has malformed {label} TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise FileLoreError(f"{path} {label} metadata must be a TOML table")
    return data


def _required_string(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FileLoreError(f"{path} metadata must include non-empty string '{key}'")
    return value.strip().lower() if key == "id" else value.strip()


def _string_tuple(
    value: Any, key: str, path: Path, *, required: bool
) -> tuple[str, ...]:
    if value is None:
        if required:
            raise FileLoreError(f"{path} metadata must include non-empty list '{key}'")
        return ()
    if not isinstance(value, list) or not value:
        raise FileLoreError(f"{path} metadata field '{key}' must be a non-empty list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise FileLoreError(f"{path} metadata field '{key}' contains a bad value")
        out.append(item.strip().lower())
    return tuple(dict.fromkeys(out))


def _positive_int(value: Any, key: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise FileLoreError(f"{path} metadata field '{key}' must be a positive integer")
    return value


def _bool_value(value: Any, key: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise FileLoreError(f"{path} metadata field '{key}' must be a boolean")
    return value


def known_file_lore_tags(sections: Iterable[FileLoreSection]) -> frozenset[str]:
    return frozenset(tag for section in sections for tag in section.tags)
