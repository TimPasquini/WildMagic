from __future__ import annotations

import pygame

from wildmagic.rendering.theme import ACCENT, PANEL_EDGE

SCROLLBAR_TRACK = (20, 22, 27)


def draw_vertical_scrollbar(
    screen: pygame.Surface,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    total_items: int,
    visible_items: int,
    offset: int,
    max_offset: int,
    dragging: bool = False,
    reverse: bool = False,
) -> tuple[pygame.Rect, pygame.Rect | None]:
    """Draw a standard vertical scrollbar and return its track/thumb rects.

    ``reverse`` maps offset zero to the bottom of the track, matching the game log
    where offset zero means "newest messages visible".
    """

    track = pygame.Rect(x, y, width, height)
    pygame.draw.rect(screen, SCROLLBAR_TRACK, track, border_radius=4)
    if total_items <= visible_items or max_offset <= 0:
        return track, None

    thumb_height = max(28, int(height * (visible_items / total_items)))
    usable = max(1, height - thumb_height)
    offset_fraction = offset / max_offset
    if reverse:
        thumb_y = y + usable - int(usable * offset_fraction)
    else:
        thumb_y = y + int(usable * offset_fraction)
    thumb = pygame.Rect(x, thumb_y, width, thumb_height)
    thumb_color = ACCENT if dragging else PANEL_EDGE
    pygame.draw.rect(screen, thumb_color, thumb, border_radius=4)
    return track, thumb
