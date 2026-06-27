from __future__ import annotations

import pygame

from wildmagic.rendering.layout import (
    MAP_OFFSET_X,
    MAP_PIXEL_HEIGHT,
    MAP_PIXEL_WIDTH,
    WINDOW_HEIGHT,
)
from wildmagic.ui_theme import ACCENT, PANEL_EDGE, TEXT, wrap_text


def draw_resolving_indicator(
    screen: pygame.Surface,
    font: pygame.font.Font,
    command_label: str,
) -> None:
    """Draw the busy banner shown while an urgent command resolves."""
    label = command_label or "the wild magic"
    if len(label) > 48:
        label = label[:45] + "..."
    text = f"Resolving: {label}"
    surface = font.render(text, True, TEXT)
    pad = 10
    width = surface.get_width() + pad * 2
    height = surface.get_height() + pad * 2
    x = MAP_OFFSET_X + (MAP_PIXEL_WIDTH - width) // 2
    y = 14
    box = pygame.Surface((width, height), pygame.SRCALPHA)
    box.fill((20, 22, 28, 235))
    screen.blit(box, (x, y))
    pygame.draw.rect(screen, ACCENT, (x, y, width, height), width=1, border_radius=6)
    screen.blit(surface, (x + pad, y + pad))


def draw_autoplay_overlay(
    screen: pygame.Surface,
    font: pygame.font.Font,
    lines: list[tuple[str, tuple[int, int, int]]],
) -> None:
    """Draw the AI watch overlay over the lower map area."""
    if not lines:
        return
    wrapped: list[tuple[str, tuple[int, int, int]]] = []
    for text, color in lines:
        for line in wrap_text(text, 62):
            wrapped.append((line, color))
    line_height = font.get_linesize() + 2
    width = MAP_PIXEL_WIDTH - 24
    height = 16 + len(wrapped) * line_height
    x = MAP_OFFSET_X + 12
    y = MAP_PIXEL_HEIGHT + 10
    if y + height > WINDOW_HEIGHT - 10:
        y = WINDOW_HEIGHT - height - 10
    overlay = pygame.Surface((width, height), pygame.SRCALPHA)
    overlay.fill((17, 19, 24, 222))
    screen.blit(overlay, (x, y))
    pygame.draw.rect(
        screen, PANEL_EDGE, (x, y, width, height), width=1, border_radius=6
    )
    cursor_y = y + 8
    for text, color in wrapped:
        surface = font.render(text, True, color)
        screen.blit(surface, (x + 10, cursor_y))
        cursor_y += line_height
