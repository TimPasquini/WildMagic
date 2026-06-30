from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pygame


TILE_SIZE = 18
MAP_PIXEL_WIDTH = 42 * TILE_SIZE
MAP_PIXEL_HEIGHT = 28 * TILE_SIZE
PANEL_WIDTH = 430
LLM_PANEL_WIDTH = 520
MAP_OFFSET_X = LLM_PANEL_WIDTH
WINDOW_WIDTH = LLM_PANEL_WIDTH + MAP_PIXEL_WIDTH + PANEL_WIDTH
WINDOW_HEIGHT = 800

# Largest integer UI scale the toggle offers. Auto-detection only selects it when
# the desktop can actually fit the scaled-up window.
MAX_UI_SCALE = 2


@dataclass(frozen=True)
class WindowLayout:
    width: int = WINDOW_WIDTH
    height: int = WINDOW_HEIGHT
    max_ui_scale: int = MAX_UI_SCALE

    def scaled_size(self, ui_scale: int) -> tuple[int, int]:
        return self.width * ui_scale, self.height * ui_scale


DEFAULT_WINDOW_LAYOUT = WindowLayout()


@dataclass(frozen=True)
class Viewport:
    """How a logical rectangle is presented inside the physical OS window."""

    source: pygame.Rect
    dest: pygame.Rect


def auto_ui_scale(layout: WindowLayout = DEFAULT_WINDOW_LAYOUT) -> int:
    """Pick the largest integer UI scale that fits the primary desktop.

    The calculation leaves headroom for the taskbar and title bar. A 4K display
    can usually fit the 2x window; 1080p/1440p displays generally stay at 1x and
    let the user opt into 2x via the toggle.

    Requires pygame.display to be initialised; falls back to 1x if unavailable.
    """
    try:
        desktop_w, desktop_h = pygame.display.get_desktop_sizes()[0]
    except (pygame.error, IndexError, AttributeError):
        return 1
    usable_h = desktop_h - 80
    scale = 1
    for candidate in range(2, layout.max_ui_scale + 1):
        candidate_w, candidate_h = layout.scaled_size(candidate)
        if candidate_w <= desktop_w and candidate_h <= usable_h:
            scale = candidate
    return scale


def desktop_size(layout: WindowLayout = DEFAULT_WINDOW_LAYOUT) -> tuple[int, int]:
    """Return the primary desktop size, falling back to the logical game size."""
    try:
        width, height = pygame.display.get_desktop_sizes()[0]
    except (pygame.error, IndexError, AttributeError):
        return layout.width, layout.height
    return max(1, width), max(1, height)


def usable_desktop_size(
    layout: WindowLayout = DEFAULT_WINDOW_LAYOUT, *, vertical_headroom: int = 80
) -> tuple[int, int]:
    """Desktop size with a little room for title bars and taskbars in windowed mode."""
    width, height = desktop_size(layout)
    return width, max(1, height - vertical_headroom)


def fit_size(
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    *,
    max_scale: float | None = None,
) -> tuple[int, int]:
    """Scale source_size to fit target_size while preserving aspect ratio."""
    source_w, source_h = max(1, source_size[0]), max(1, source_size[1])
    target_w, target_h = max(1, target_size[0]), max(1, target_size[1])
    scale = min(target_w / source_w, target_h / source_h)
    if max_scale is not None:
        scale = min(scale, max_scale)
    scale = max(0.05, scale)
    return max(1, int(source_w * scale)), max(1, int(source_h * scale))


def windowed_fit_size(
    view_size: tuple[int, int],
    ui_scale: int,
    layout: WindowLayout = DEFAULT_WINDOW_LAYOUT,
) -> tuple[int, int]:
    """Windowed physical size for a logical view.

    If the preferred integer scale does not fit the monitor, downscale just enough
    to keep the whole game visible instead of letting the window bleed offscreen.
    """
    desired = (max(1, view_size[0]) * ui_scale, max(1, view_size[1]) * ui_scale)
    usable = usable_desktop_size(layout)
    if desired[0] <= usable[0] and desired[1] <= usable[1]:
        return desired
    return fit_size(view_size, usable, max_scale=float(max(1, ui_scale)))


def centered_viewport(source: pygame.Rect, display_size: tuple[int, int]) -> Viewport:
    """Return the physical destination rect for source inside display_size."""
    dest_w, dest_h = fit_size(source.size, display_size)
    display_w, display_h = max(1, display_size[0]), max(1, display_size[1])
    return Viewport(
        source=source.copy(),
        dest=pygame.Rect(
            (display_w - dest_w) // 2,
            (display_h - dest_h) // 2,
            dest_w,
            dest_h,
        ),
    )


def toggled_ui_scale(
    current_scale: int, layout: WindowLayout = DEFAULT_WINDOW_LAYOUT
) -> int:
    return 1 if current_scale >= layout.max_ui_scale else layout.max_ui_scale


def logical_mouse_event(event: pygame.event.Event, ui_scale: int) -> pygame.event.Event:
    if not hasattr(event, "pos"):
        return event
    attributes: dict[str, Any] = event.dict.copy()
    attributes["pos"] = tuple(coordinate // ui_scale for coordinate in event.pos)
    if "rel" in attributes:
        attributes["rel"] = tuple(
            coordinate / ui_scale for coordinate in attributes["rel"]
        )
    return pygame.event.Event(event.type, attributes)


def logical_mouse_pos(ui_scale: int) -> tuple[int, int]:
    return tuple(coordinate // ui_scale for coordinate in pygame.mouse.get_pos())


def viewport_mouse_pos(
    physical_pos: tuple[int, int], viewport: Viewport
) -> tuple[int, int]:
    """Map a physical window coordinate into logical canvas coordinates."""
    px, py = physical_pos
    dest = viewport.dest
    if dest.width <= 0 or dest.height <= 0 or not dest.collidepoint(px, py):
        return (-1, -1)
    sx = viewport.source.x + int((px - dest.x) * viewport.source.width / dest.width)
    sy = viewport.source.y + int((py - dest.y) * viewport.source.height / dest.height)
    return sx, sy


def logical_mouse_event_for_view(
    event: pygame.event.Event, viewport: Viewport
) -> pygame.event.Event:
    if not hasattr(event, "pos"):
        return event
    attributes: dict[str, Any] = event.dict.copy()
    attributes["pos"] = viewport_mouse_pos(event.pos, viewport)
    if "rel" in attributes:
        attributes["rel"] = (
            attributes["rel"][0] * viewport.source.width / max(1, viewport.dest.width),
            attributes["rel"][1]
            * viewport.source.height
            / max(1, viewport.dest.height),
        )
    return pygame.event.Event(event.type, attributes)
