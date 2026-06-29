from __future__ import annotations

from typing import Any

import pygame

from wildmagic.rendering.layout import WINDOW_HEIGHT, WINDOW_WIDTH
from wildmagic.ui_theme import (
    ACCENT,
    GOLD,
    MODE_GREEN,
    MUTED,
    PANEL,
    PANEL_EDGE,
    TEXT,
)


QUEUE_STATUS_STYLE = {
    "done": ("done", MODE_GREEN),
    "running": ("generating", GOLD),
    "queued": ("queued", ACCENT),
    "pending": ("waiting", MUTED),
    "far": ("(too far)", (110, 110, 120)),
}


def draw_queue_debug(host: Any) -> None:
    """Draw the F7 background-generation queue overlay."""
    snap = host.session.canon_queue_snapshot()

    overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 180))
    host.screen.blit(overlay, (0, 0))

    box_w = 780
    box_h = min(700, WINDOW_HEIGHT - 60)
    bx = (WINDOW_WIDTH - box_w) // 2
    by = (WINDOW_HEIGHT - box_h) // 2
    pad = 22
    row_h = host.small_font.get_linesize()
    pygame.draw.rect(host.screen, PANEL, (bx, by, box_w, box_h), border_radius=6)
    pygame.draw.rect(
        host.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6
    )

    cx = bx + pad
    cy = by + pad

    def emit(text: str, color, font=None) -> None:
        nonlocal cy
        font = font or host.small_font
        surf = font.render(text, True, color)
        host.screen.blit(surf, (cx, cy))
        cy += font.get_linesize()

    emit("Generation Queue", GOLD, host.ui_font)
    cy += 2
    emit("F7 or Esc to close · arrows / PgUp / PgDn / wheel to scroll", MUTED)
    cy += 6

    flags = (
        f"titles {'on' if snap['titles_enabled'] else 'off'}   "
        f"saturation {'on' if snap['saturation_enabled'] else 'off'}   "
        f"depth {snap['limit']}"
    )
    emit(flags, TEXT)
    emit(
        f"in flight: {snap['pending_canon']} canon · "
        f"{snap['pending_lore']} lore · {snap['pending_flesh']} flesh",
        MUTED,
    )
    cy += 8

    emit("Worker — now & next", ACCENT)
    if snap["now_next"]:
        for job in snap["now_next"]:
            label_text, color = QUEUE_STATUS_STYLE.get(
                job["status"], (job["status"], TEXT)
            )
            kind = {"book_title": "title", "book": "pages"}.get(
                job["kind"], job["kind"]
            )
            name = job["label"]
            if len(name) > 48:
                name = name[:45] + "..."
            emit(f"  [{label_text}] {kind}: {name}", color)
    else:
        emit("  idle — nothing queued", MUTED)
    cy += 8

    books = snap["books"]
    emit(f"Books in zone ({len(books)}) — nearest first", ACCENT)
    cy += 2

    # Columns for the scrollable book table.
    col_dist = cx + 4
    col_name = col_dist + 56
    col_title = bx + box_w - 260
    col_pages = bx + box_w - 130
    header_y = cy
    for label, x in (
        ("dist", col_dist),
        ("book", col_name),
        ("title", col_title),
        ("pages", col_pages),
    ):
        host.screen.blit(host.small_font.render(label, True, MUTED), (x, header_y))
    cy += row_h + 2

    list_bottom = by + box_h - pad - row_h
    capacity = max(1, (list_bottom - cy) // row_h)
    host._queue_debug_max_scroll = max(0, len(books) - capacity)
    host.queue_debug_scroll = max(
        0, min(host.queue_debug_scroll, host._queue_debug_max_scroll)
    )
    start = host.queue_debug_scroll
    visible = books[start : start + capacity]

    name_chars = max(8, (col_title - col_name) // 8 - 1)
    for book in visible:
        host.screen.blit(
            host.small_font.render(f"d{book['distance']}", True, MUTED),
            (col_dist, cy),
        )
        name = book["name"]
        if len(name) > name_chars:
            name = name[: name_chars - 1] + "…"
        host.screen.blit(host.small_font.render(name, True, TEXT), (col_name, cy))
        for key, x in (("title", col_title), ("pages", col_pages)):
            label_text, color = QUEUE_STATUS_STYLE.get(book[key], (book[key], TEXT))
            host.screen.blit(host.small_font.render(label_text, True, color), (x, cy))
        cy += row_h

    if host._queue_debug_max_scroll > 0:
        shown_end = start + len(visible)
        footer = (
            f"showing {start + 1}–{shown_end} of {len(books)}  "
            f"({'more below' if shown_end < len(books) else 'end'})"
        )
        host.screen.blit(
            host.small_font.render(footer, True, MUTED),
            (cx, by + box_h - pad - row_h + 4),
        )
