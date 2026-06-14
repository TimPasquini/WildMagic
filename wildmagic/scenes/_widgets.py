"""Shared pygame widgets for full-screen scenes (text fields, gender selector, portrait
panel). Pure draw helpers that take the host GameUI for surface/fonts/draw_text and
return the rects scenes register as click targets. Keeps creation and character-view
rendering consistent and DRY.
"""

from __future__ import annotations

import pygame

from ..ui_theme import (
    ACCENT,
    DANGER,
    MUTED,
    PANEL,
    PANEL_EDGE,
    SELECTED,
    TEXT,
    wrap_text,
)

GENDER_OPTIONS = ("Male", "Female", "Other")


def fit_text(font, text: str, max_px: int) -> str:
    """Trim from the front (keeping the tail visible for typing feedback) until it fits."""
    if font.size(text)[0] <= max_px:
        return text
    out = text
    while out and font.size("…" + out)[0] > max_px:
        out = out[1:]
    return "…" + out


def draw_text_field(
    host,
    label: str,
    value: str,
    x: int,
    y: int,
    w: int,
    focused: bool,
    default: str = "",
) -> tuple[pygame.Rect, int]:
    """A labelled single-line text field. Returns (clickable rect, next y)."""
    y = host.draw_text(label + ":", x, y, host.small_font, ACCENT if focused else MUTED)
    rect = pygame.Rect(x, y, w, 26)
    pygame.draw.rect(
        host.screen, ACCENT if focused else PANEL_EDGE, rect, width=1, border_radius=3
    )
    if value:
        shown, color = value + ("_" if focused else ""), TEXT
    elif focused:
        shown, color = "_", TEXT
    else:
        shown, color = (default or "(none)"), MUTED
    host.draw_text(
        fit_text(host.small_font, shown, w - 12), x + 6, y + 5, host.small_font, color
    )
    return rect, y + 34


def draw_gender_field(
    host, x: int, y: int, focused: bool, mode: int, other_text: str
) -> tuple[dict[int, pygame.Rect], int]:
    """Male/Female/Other selector ('Other' shows the typed custom value). Returns
    ({option_index: rect}, next y)."""
    y = host.draw_text("Gender:", x, y, host.small_font, ACCENT if focused else MUTED)
    rects: dict[int, pygame.Rect] = {}
    ox = x
    for i, opt in enumerate(GENDER_OPTIONS):
        label = opt
        if i == 2 and mode == 2:
            label = (other_text + ("_" if focused else "")) or "Other"
        selected = mode == i
        bw = max(72, host.small_font.size(label)[0] + 18)
        rect = pygame.Rect(ox, y, bw, 26)
        pygame.draw.rect(
            host.screen, SELECTED if selected else PANEL, rect, border_radius=3
        )
        if selected and focused:
            pygame.draw.rect(host.screen, ACCENT, rect, width=1, border_radius=3)
        host.draw_text(
            label, ox + 9, y + 5, host.small_font, ACCENT if selected else TEXT
        )
        rects[i] = rect
        ox += bw + 8
    return rects, y + 36


def draw_portrait_panel(
    host,
    x: int,
    y: int,
    box: int,
    *,
    available: bool,
    status: str | None,
    surface,
    error: str,
    warming: bool,
) -> tuple[pygame.Rect | None, int]:
    """Portrait box + Generate/Regenerate button. Returns (button rect or None when the
    button is disabled/absent, bottom y below the button)."""
    pygame.draw.rect(
        host.screen, PANEL_EDGE, (x, y, box, box), width=1, border_radius=6
    )
    if not available:
        host.draw_text(
            "(portrait generator", x + 12, y + box // 2 - 14, host.small_font, MUTED
        )
        host.draw_text(
            "not installed)", x + 12, y + box // 2 + 2, host.small_font, MUTED
        )
        return None, y + box + 16

    if surface is not None and status != "working":
        scaled = pygame.transform.smoothscale(surface, (box - 4, box - 4))
        host.screen.blit(scaled, (x + 2, y + 2))
    else:
        if status == "working":
            lines = ["Painting your portrait..."]
            if warming:
                lines += ["(loading the model —", "first time is slow)"]
            color = ACCENT
        elif status == "error":
            lines = ["Portrait failed:"] + wrap_text(error or "", 30)
            color = DANGER
        else:
            lines = ["No portrait yet.", "Set a description,", "then generate."]
            color = MUTED
        ty = y + box // 2 - len(lines) * 9
        for line in lines:
            host.draw_text(line, x + 12, ty, host.small_font, color)
            ty += 18

    if status == "working":
        label = "Working..."
    elif surface is not None:
        label = "Regenerate portrait"
    else:
        label = "Generate portrait"
    btn = pygame.Rect(x, y + box + 10, box, 34)
    enabled = status != "working"
    pygame.draw.rect(
        host.screen, ACCENT if enabled else PANEL_EDGE, btn, width=2, border_radius=6
    )
    surf = host.ui_font.render(label, True, ACCENT if enabled else MUTED)
    host.screen.blit(surf, (btn.centerx - surf.get_width() // 2, btn.y + 7))
    return (btn if enabled else None), btn.bottom + 18
