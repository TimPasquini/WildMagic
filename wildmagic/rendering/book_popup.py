from __future__ import annotations

from typing import Any

import pygame

from wildmagic.rendering.layout import WINDOW_HEIGHT, WINDOW_WIDTH
from wildmagic.ui_theme import wrap_text


def draw_book_popup(host: Any) -> None:
    """Draw the modal parchment reader for materialized book text."""
    assert host.book_popup is not None
    title = str(host.book_popup["title"])
    author = str(host.book_popup["author"])
    text = str(host.book_popup["text"])

    overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
    overlay.fill((10, 8, 4, 180))
    host.screen.blit(overlay, (0, 0))

    parchment = (233, 222, 196)
    parchment_edge = (140, 112, 72)
    ink = (58, 44, 30)
    faded_ink = (120, 100, 72)

    box_w = 600
    box_h = min(640, WINDOW_HEIGHT - 60)
    pad = 36
    wrap_width = 58
    bx = (WINDOW_WIDTH - box_w) // 2
    by = (WINDOW_HEIGHT - box_h) // 2
    title_h = host.book_title_font.get_linesize()
    body_h = host.book_font.get_linesize()
    small_h = host.book_small_font.get_linesize()

    # Body lines with paragraph spacing preserved as blank lines.
    body_lines: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            if body_lines and body_lines[-1] != "":
                body_lines.append("")
            continue
        body_lines.extend(wrap_text(paragraph, wrap_width))
        body_lines.append("")
    while body_lines and body_lines[-1] == "":
        body_lines.pop()

    title_lines = wrap_text(title, 42)
    header_h = title_h * len(title_lines) + (small_h + 6 if author else 0) + 18
    footer_h = small_h + 18
    first_capacity = max(4, (box_h - pad * 2 - header_h - footer_h) // body_h)
    rest_capacity = max(4, (box_h - pad * 2 - footer_h) // body_h)

    pages: list[list[str]] = []
    remaining = list(body_lines)
    capacity = first_capacity
    while True:
        page_lines = remaining[:capacity]
        pages.append(page_lines)
        remaining = remaining[capacity:]
        while remaining and remaining[0] == "":
            remaining = remaining[1:]
        if not remaining:
            break
        capacity = rest_capacity
    host.book_popup["page_count"] = len(pages)
    page = max(0, min(int(host.book_popup.get("page", 0)), len(pages) - 1))
    host.book_popup["page"] = page

    pygame.draw.rect(host.screen, parchment, (bx, by, box_w, box_h), border_radius=4)
    pygame.draw.rect(
        host.screen, parchment_edge, (bx, by, box_w, box_h), 2, border_radius=4
    )
    pygame.draw.rect(
        host.screen,
        parchment_edge,
        (bx + 6, by + 6, box_w - 12, box_h - 12),
        1,
        border_radius=3,
    )

    cy = by + pad
    if page == 0:
        for line in title_lines:
            surf = host.book_title_font.render(line, True, ink)
            host.screen.blit(surf, (bx + (box_w - surf.get_width()) // 2, cy))
            cy += title_h
        if author:
            surf = host.book_small_font.render(f"— {author}", True, faded_ink)
            host.screen.blit(surf, (bx + (box_w - surf.get_width()) // 2, cy + 2))
            cy += small_h + 6
        cy += 8
        pygame.draw.line(
            host.screen,
            parchment_edge,
            (bx + box_w // 2 - 60, cy),
            (bx + box_w // 2 + 60, cy),
            1,
        )
        cy += 10

    for line in pages[page]:
        if line:
            surf = host.book_font.render(line, True, ink)
            host.screen.blit(surf, (bx + pad, cy))
        cy += body_h

    if len(pages) > 1:
        marker = host.book_small_font.render(
            f"— {page + 1} of {len(pages)} —", True, faded_ink
        )
        host.screen.blit(
            marker,
            (
                bx + (box_w - marker.get_width()) // 2,
                by + box_h - pad // 2 - small_h - 14,
            ),
        )
    last_page = page + 1 >= len(pages)
    hint_text = (
        "Esc closes · click or arrows turn the page"
        if not last_page
        else "Esc or click to close the book"
    )
    hint = host.book_small_font.render(hint_text, True, faded_ink)
    host.screen.blit(
        hint,
        (bx + (box_w - hint.get_width()) // 2, by + box_h - pad // 2 - small_h + 2),
    )
