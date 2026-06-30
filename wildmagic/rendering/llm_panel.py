from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any

import pygame

from wildmagic.config import audit_dir
from wildmagic.game_data import _TOWN_GEN_TIMEOUT
from wildmagic.normalize import normalize_id
from wildmagic.rendering.layout import LLM_PANEL_WIDTH, WINDOW_HEIGHT
from wildmagic.rendering.theme import (
    ACCENT,
    DANGER,
    GOLD,
    MANA,
    MODE_GREEN,
    MODE_ORANGE,
    MODE_PURPLE,
    MODE_YELLOW,
    MUTED,
    PANEL,
    PANEL_EDGE,
    SELECTED,
    TEXT,
    wrap_text,
)


LLM_AUDIT_FILES = (
    "wild_magic_audit.jsonl",
    "dialogue_audit.jsonl",
    "trade_audit.jsonl",
    "town_audit.jsonl",
    "prop_audit.jsonl",
    "canon_audit.jsonl",
    "lore_audit.jsonl",
    "flesh_audit.jsonl",
    "deed_interp_audit.jsonl",
)


LLM_CALL_COLORS = {
    "spell": MODE_PURPLE,
    "dialogue": MODE_YELLOW,
    "trade": MODE_ORANGE,
    "town": GOLD,
    "canon": ACCENT,
    "lore": MANA,
    "flesh": MODE_GREEN,
}


def draw_panel(host: Any) -> None:
    x = 0
    panel_width = int(getattr(host, "llm_panel_width", LLM_PANEL_WIDTH))
    panel_height = int(getattr(host, "llm_panel_height", WINDOW_HEIGHT))
    pygame.draw.rect(host.screen, PANEL, (x, 0, panel_width, panel_height))
    pygame.draw.line(
        host.screen,
        PANEL_EDGE,
        (panel_width - 1, 0),
        (panel_width - 1, panel_height),
        2,
    )
    cursor_y = host.draw_text("LLM Debug", x + 16, 16, host.ui_font, ACCENT)
    buttons_bottom = draw_call_buttons(host, x + 16, cursor_y + 12, panel_width - 32)
    divider_y = max(cursor_y + 10, buttons_bottom + 8)
    pygame.draw.line(
        host.screen,
        PANEL_EDGE,
        (x + 16, divider_y),
        (panel_width - 16, divider_y),
        1,
    )
    content_y = divider_y + 10
    content_height = panel_height - content_y - 16
    draw_content(host, x + 16, content_y, panel_width - 32, content_height)


def draw_call_buttons(host: Any, x: int, y: int, width: int) -> int:
    refresh_debug_entries(host)
    host.llm_call_button_rects = []
    recent = recent_call_indices(host)
    if not recent:
        return y - 8
    gap = 6
    button_h = 24
    button_w = max(40, (width - gap * 4) // 5)
    for slot, entry_index in enumerate(recent):
        entry = host.llm_debug_entries[entry_index]
        col = slot % 5
        row = slot // 5
        rect = pygame.Rect(
            x + col * (button_w + gap),
            y + row * (button_h + gap),
            button_w,
            button_h,
        )
        kind = call_kind(entry)
        color = LLM_CALL_COLORS.get(kind, PANEL_EDGE)
        fill = tuple(max(0, int(channel * 0.28)) for channel in color)
        pygame.draw.rect(host.screen, fill, rect, border_radius=5)
        border = TEXT if entry_index == host.llm_selected_call_index else color
        pygame.draw.rect(host.screen, border, rect, 1, border_radius=5)
        label = fit_text(kind, host.small_font, rect.width - 10)
        label_surf = host.small_font.render(label, True, TEXT)
        host.screen.blit(
            label_surf,
            (rect.x + 5, rect.y + (rect.height - label_surf.get_height()) // 2),
        )
        host.llm_call_button_rects.append((rect, entry_index))
    rows = 1 + (len(recent) - 1) // 5
    return y + rows * button_h + (rows - 1) * gap


def draw_content(host: Any, x: int, y: int, width: int, height: int) -> None:
    scrollbar_width = 10
    text_width = max(20, width - scrollbar_width - 6)
    host.llm_content_rect = pygame.Rect(x, y, width, height)

    char_width = max(1, host.small_font.size("M")[0])
    wrap_chars = max(10, text_width // char_width)
    if host._llm_lines_cache is None:
        host._llm_lines_cache = build_lines(host, wrap_chars)
    else:
        now_sec = int(time.monotonic())
        if now_sec != getattr(host, "_llm_cache_sec", -1):
            host._llm_cache_sec = now_sec
            host._llm_lines_cache = build_lines(host, wrap_chars)
    lines = host._llm_lines_cache

    line_height = host.small_font.get_linesize() + 1
    max_visible = max(1, height // line_height)
    host._llm_max_scroll = max(0, len(lines) - max_visible)
    if host.llm_autoscroll:
        host.llm_scroll_offset = host._llm_max_scroll
    host.llm_scroll_offset = max(0, min(host.llm_scroll_offset, host._llm_max_scroll))

    sel_lo, sel_hi = None, None
    if host.llm_selection_anchor is not None and host.llm_selection_focus is not None:
        sel_lo = min(host.llm_selection_anchor, host.llm_selection_focus)
        sel_hi = max(host.llm_selection_anchor, host.llm_selection_focus)

    clip = host.screen.get_clip()
    host.screen.set_clip(pygame.Rect(x, y, width, height))
    host.llm_line_rects = []
    line_y = y
    visible_slice = lines[
        host.llm_scroll_offset : host.llm_scroll_offset + max_visible + 1
    ]
    for offset, (text, color) in enumerate(visible_slice):
        abs_index = host.llm_scroll_offset + offset
        rect = pygame.Rect(x - 4, line_y - 1, width - scrollbar_width - 2, line_height)
        if sel_lo is not None and sel_lo <= abs_index <= sel_hi:
            pygame.draw.rect(host.screen, SELECTED, rect, border_radius=3)
        if text:
            host.draw_text(text, x, line_y, host.small_font, color)
        host.llm_line_rects.append((rect, abs_index))
        line_y += line_height
    host.screen.set_clip(clip)

    draw_scrollbar(
        host,
        x + width - scrollbar_width,
        y,
        scrollbar_width,
        height,
        len(lines),
        max_visible,
    )


def draw_scrollbar(
    host: Any,
    x: int,
    y: int,
    width: int,
    height: int,
    total_lines: int,
    visible_lines: int,
) -> None:
    track = pygame.Rect(x, y, width, height)
    pygame.draw.rect(host.screen, (20, 22, 27), track, border_radius=4)
    if total_lines <= visible_lines or host._llm_max_scroll <= 0:
        host.llm_scrollbar_track_rect = None
        host.llm_scrollbar_thumb_rect = None
        return
    thumb_height = max(28, int(height * (visible_lines / total_lines)))
    usable = max(1, height - thumb_height)
    thumb_y = y + int(usable * (host.llm_scroll_offset / host._llm_max_scroll))
    thumb = pygame.Rect(x, thumb_y, width, thumb_height)
    thumb_color = ACCENT if host.llm_dragging_scrollbar else PANEL_EDGE
    pygame.draw.rect(host.screen, thumb_color, thumb, border_radius=4)
    host.llm_scrollbar_track_rect = track
    host.llm_scrollbar_thumb_rect = thumb


def line_index_at(host: Any, pos: tuple[int, int]) -> int | None:
    if not host.llm_content_rect.collidepoint(pos):
        return None
    x, y = pos
    for rect, abs_index in host.llm_line_rects:
        expanded = rect.inflate(0, 4)
        expanded.x = host.llm_content_rect.x
        expanded.width = host.llm_content_rect.width
        if expanded.collidepoint(x, y):
            return abs_index
    if host.llm_line_rects:
        if y < host.llm_line_rects[0][0].top:
            return host.llm_line_rects[0][1]
        if y > host.llm_line_rects[-1][0].bottom:
            return host.llm_line_rects[-1][1]
    return None


def selected_lines(host: Any) -> list[str]:
    if (
        host.llm_selection_anchor is None
        or host.llm_selection_focus is None
        or not host._llm_lines_cache
    ):
        return []
    start = max(0, min(host.llm_selection_anchor, host.llm_selection_focus))
    end = min(
        len(host._llm_lines_cache) - 1,
        max(host.llm_selection_anchor, host.llm_selection_focus),
    )
    if start > end:
        return []
    return [text for text, _color in host._llm_lines_cache[start : end + 1]]


def block_index_for_line(host: Any, line_index: int) -> int | None:
    if host._llm_lines_cache is None:
        host._llm_lines_cache = build_lines(host, 80)
    for index, (start, end) in enumerate(host.llm_block_ranges):
        if start <= line_index <= end:
            return index
    return None


def select_block(host: Any, block_index: int) -> bool:
    if host._llm_lines_cache is None:
        host._llm_lines_cache = build_lines(host, 80)
    if not host.llm_block_ranges:
        return False
    block_index = max(0, min(block_index, len(host.llm_block_ranges) - 1))
    start, end = host.llm_block_ranges[block_index]
    host.llm_selection_anchor = start
    host.llm_selection_focus = end
    visible_lines = max(
        1, host.llm_content_rect.height // (host.small_font.get_linesize() + 1)
    )
    host.llm_scroll_offset = max(0, min(start, max(0, end - visible_lines + 1)))
    host.llm_autoscroll = False
    return True


def move_block_selection(host: Any, direction: int) -> bool:
    if host._llm_lines_cache is None:
        host._llm_lines_cache = build_lines(host, 80)
    if not host.llm_block_ranges:
        return False
    focus = (
        host.llm_selection_focus
        if host.llm_selection_focus is not None
        else host.llm_selection_anchor
    )
    if focus is None:
        return False
    current = block_index_for_line(host, focus)
    if current is None:
        return False
    return select_block(host, current + direction)


def recent_call_indices(host: Any) -> list[int]:
    count = len(host.llm_debug_entries)
    start = max(0, count - 10)
    return list(range(count - 1, start - 1, -1))


def call_kind(entry: dict[str, Any]) -> str:
    raw = normalize_id(str(entry.get("call_type") or "llm"))
    if raw in {"wild_magic", "wild magic"}:
        return "spell"
    if raw in {"dialogue", "trade", "town", "canon", "lore", "flesh"}:
        return raw
    return raw.replace("_", " ") or "llm"


def fit_text(text: str, font: pygame.font.Font, max_width: int) -> str:
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "..."
    result = text
    while result and font.size(result + ellipsis)[0] > max_width:
        result = result[:-1]
    return (result + ellipsis) if result else ellipsis


def activate_call_button(host: Any, entry_index: int) -> bool:
    if host.llm_selected_call_index == entry_index:
        part = "response" if host.llm_selected_call_part == "prompt" else "prompt"
    else:
        part = "prompt"
    return select_entry_part(host, entry_index, part)


def select_entry_part(host: Any, entry_index: int, part: str) -> bool:
    if host._llm_lines_cache is None:
        host._llm_lines_cache = build_lines(host, 80)
    ranges = host.llm_entry_block_ranges.get(entry_index)
    if not ranges or part not in ranges:
        return False
    start, end = ranges[part]
    host.llm_selection_anchor = start
    host.llm_selection_focus = end
    visible_lines = max(
        1, host.llm_content_rect.height // (host.small_font.get_linesize() + 1)
    )
    host.llm_scroll_offset = max(0, min(start, max(0, end - visible_lines + 1)))
    host.llm_autoscroll = False
    host.llm_selected_call_index = entry_index
    host.llm_selected_call_part = part
    return True


def refresh_debug_entries(host: Any) -> None:
    entries_changed = False
    base_dir = audit_dir()
    for filename in LLM_AUDIT_FILES:
        path = base_dir / filename
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    key = f"{path}:{line_no}"
                    if key in host.llm_debug_seen:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = parse_audit_timestamp(record.get("timestamp"))
                    if timestamp is not None and timestamp < host.llm_debug_started_at:
                        host.llm_debug_seen.add(key)
                        continue
                    host.llm_debug_seen.add(key)
                    host.llm_debug_entries.append(
                        audit_record_to_debug_entry(filename, record)
                    )
                    entries_changed = True
        except OSError:
            continue
    if entries_changed:
        host.llm_debug_entries.sort(key=lambda entry: entry.get("timestamp") or "")
        host._llm_lines_cache = None


def parse_audit_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def audit_record_to_debug_entry(
    filename: str, record: dict[str, Any]
) -> dict[str, Any]:
    call_type = filename.removesuffix("_audit.jsonl").replace("_", " ")
    if filename == "wild_magic_audit.jsonl":
        call_type = "wild magic"
    return {
        "timestamp": str(record.get("timestamp") or ""),
        "call_type": call_type,
        "provider": str(
            record.get("provider") or record.get("provider_requested") or ""
        ),
        "model": str(record.get("model") or ""),
        "technical_failure": bool(record.get("technical_failure")),
        "error": record.get("error"),
        "prompt": format_audit_prompt(record),
        "response": format_audit_response(record),
    }


def format_audit_prompt(record: dict[str, Any]) -> str:
    prompt = record.get("prompt")
    if isinstance(prompt, dict):
        messages = prompt.get("messages")
        if isinstance(messages, list) and messages:
            parts: list[str] = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "message").upper()
                content = str(message.get("content") or "")
                parts.append(f"{role}:\n{content}")
            if parts:
                return "\n\n".join(parts)
        if "context" in prompt:
            return json.dumps(prompt["context"], indent=2, ensure_ascii=False)
    if "context" in record:
        return json.dumps(record["context"], indent=2, ensure_ascii=False)
    return "(prompt unavailable)"


def format_audit_response(record: dict[str, Any]) -> str:
    raw = record.get("raw_response")
    if raw is not None:
        return str(raw)
    for key in ("parsed_resolution", "reply", "claims", "record", "flesh", "town"):
        if record.get(key) is not None:
            return json.dumps(record[key], indent=2, ensure_ascii=False)
    return "(no response captured)"


def build_lines(host: Any, wrap_chars: int) -> list[tuple[str, tuple[int, int, int]]]:
    refresh_debug_entries(host)
    lines: list[tuple[str, tuple[int, int, int]]] = []
    block_ranges: list[tuple[int, int]] = []
    entry_block_ranges: dict[int, dict[str, tuple[int, int]]] = {}

    def emit(text: str, color: tuple[int, int, int]) -> None:
        for raw_line in text.splitlines() or [""]:
            for wrapped in wrap_text(raw_line, wrap_chars):
                lines.append((wrapped, color))

    def emit_block(
        label: str, text: str, color: tuple[int, int, int]
    ) -> tuple[int, int]:
        start = len(lines)
        emit(label, ACCENT)
        emit(text or "(empty)", color)
        block_range = (start, max(start, len(lines) - 1))
        block_ranges.append(block_range)
        lines.append(("", MUTED))
        return block_range

    pending = getattr(host.engine, "_pending_towns", {})
    if pending:
        emit("Town generation in progress", GOLD)
        now = time.monotonic()
        for key in pending:
            ctx = getattr(host.engine, "_pending_town_contexts", {}).get(key, {})
            start = getattr(host.engine, "_pending_town_start_times", {}).get(key, now)
            remaining = max(0.0, _TOWN_GEN_TIMEOUT - (now - start))
            zx, zy = key
            emit(f"  Zone ({zx}, {zy}) - {remaining:.0f}s remaining", MODE_ORANGE)
            if ctx.get("settlement_type"):
                emit(f"  Type: {ctx['settlement_type']}", TEXT)
            if ctx.get("location"):
                emit(f"  Location: {ctx['location']}", TEXT)
            if ctx.get("defining_trait"):
                emit(f"  Trait: {ctx['defining_trait']}", TEXT)
            if ctx.get("current_situation"):
                emit(f"  Situation: {ctx['current_situation']}", TEXT)
            lines.append(("", MUTED))

    if not host.llm_debug_entries:
        lines.append(("", MUTED))
        emit("No LLM calls captured yet.", MUTED)
        host.llm_block_ranges = []
        host.llm_entry_block_ranges = {}
        return lines

    for entry_index, entry in enumerate(host.llm_debug_entries):
        lines.append(("", MUTED))
        header_bits = [entry.get("call_type") or "llm"]
        if entry.get("provider"):
            header_bits.append(f"provider {entry['provider']}")
        if entry.get("model"):
            header_bits.append(str(entry["model"]))
        if entry.get("timestamp"):
            header_bits.append(str(entry["timestamp"]))
        emit(" | ".join(header_bits), DANGER if entry["technical_failure"] else GOLD)
        if entry.get("error"):
            emit(f"error: {entry['error']}", DANGER)
        entry_block_ranges[entry_index] = {
            "prompt": emit_block("Prompt", str(entry.get("prompt") or ""), TEXT),
            "response": emit_block("Response", str(entry.get("response") or ""), TEXT),
        }

    host.llm_block_ranges = block_ranges
    host.llm_entry_block_ranges = entry_block_ranges
    return lines


def scroll_to_fraction(host: Any, fraction: float) -> None:
    if host._llm_max_scroll <= 0:
        return
    fraction = max(0.0, min(1.0, fraction))
    host.llm_scroll_offset = int(round(fraction * host._llm_max_scroll))
    host.llm_autoscroll = host.llm_scroll_offset >= host._llm_max_scroll


def scrollbar_fraction_at(host: Any, mouse_y: int) -> float | None:
    track = host.llm_scrollbar_track_rect
    thumb = host.llm_scrollbar_thumb_rect
    if track is None or thumb is None:
        return None
    usable = track.height - thumb.height
    if usable <= 0:
        return None
    target_thumb_y = mouse_y - host.llm_drag_grab_dy
    return (target_thumb_y - track.y) / usable
