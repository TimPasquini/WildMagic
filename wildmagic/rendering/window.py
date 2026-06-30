from __future__ import annotations

import warnings
from dataclasses import dataclass

import pygame

from wildmagic.rendering.layout import (
    DEFAULT_WINDOW_LAYOUT,
    Viewport,
    WindowLayout,
    auto_ui_scale,
    centered_viewport,
    desktop_size,
    logical_mouse_event_for_view,
    viewport_mouse_pos,
    toggled_ui_scale,
    usable_desktop_size,
    windowed_fit_size,
)


@dataclass
class GameWindow:
    display: pygame.Surface
    screen: pygame.Surface
    clock: pygame.time.Clock
    ui_scale: int
    layout: WindowLayout = DEFAULT_WINDOW_LAYOUT
    fullscreen: bool = False
    base_view_rect: pygame.Rect | None = None
    active_view_rect: pygame.Rect | None = None
    content_rect: pygame.Rect | None = None
    window_id: int | None = None

    @classmethod
    def create(
        cls,
        caption: str,
        layout: WindowLayout = DEFAULT_WINDOW_LAYOUT,
        *,
        fullscreen: bool = False,
    ) -> "GameWindow":
        pygame.init()
        pygame.key.set_repeat()
        pygame.display.set_caption(caption)
        ui_scale = auto_ui_scale(layout)
        view_rect = pygame.Rect(0, 0, layout.width, layout.height)
        if fullscreen:
            display = pygame.display.set_mode(desktop_size(layout), pygame.FULLSCREEN)
        else:
            display = pygame.display.set_mode(
                windowed_fit_size(view_rect.size, ui_scale, layout), pygame.RESIZABLE
            )
        screen = pygame.Surface((layout.width, layout.height))
        clock = pygame.time.Clock()
        window = cls(
            display,
            screen,
            clock,
            ui_scale,
            layout,
            fullscreen,
            view_rect.copy(),
            view_rect.copy(),
            window_id=_display_window_id(),
        )
        window._refresh_content_rect()
        return window

    def owns_event(self, event: pygame.event.Event) -> bool:
        if self.window_id is None:
            return _event_window_id(event) is None
        event_window = _event_window_id(event)
        return event_window is None or event_window == self.window_id

    def handle_window_event(self, event: pygame.event.Event) -> bool:
        if not self.owns_event(event):
            return False
        resize_events = {
            getattr(pygame, "WINDOWRESIZED", None),
            getattr(pygame, "WINDOWSIZECHANGED", None),
            pygame.VIDEORESIZE,
        }
        if event.type not in resize_events:
            return False
        if self.fullscreen:
            self.display = pygame.display.get_surface() or self.display
            self._refresh_content_rect()
            return True
        size = getattr(event, "size", None) or (
            getattr(event, "w", 0),
            getattr(event, "h", 0),
        )
        if not size or size[0] <= 0 or size[1] <= 0:
            return True
        self.display = pygame.display.get_surface() or self.display
        self.window_id = _display_window_id() or self.window_id
        self._refresh_content_rect()
        return True

    def logical_mouse_event(self, event: pygame.event.Event) -> pygame.event.Event:
        viewport = self._viewport()
        return logical_mouse_event_for_view(event, viewport)

    def logical_mouse_pos(self) -> tuple[int, int]:
        viewport = self._viewport()
        return viewport_mouse_pos(pygame.mouse.get_pos(), viewport)

    def set_base_view_rect(self, rect: pygame.Rect) -> None:
        rect = rect.copy()
        if self.base_view_rect == rect:
            self.active_view_rect = rect.copy()
            self._refresh_content_rect()
            return
        self.base_view_rect = rect
        self.active_view_rect = rect.copy()
        self._refresh_content_rect()

    def set_active_view_rect(self, rect: pygame.Rect | None) -> None:
        self.active_view_rect = (
            rect or self.base_view_rect or self._full_rect()
        ).copy()
        self._refresh_content_rect()

    def toggle_scale(self) -> None:
        self.ui_scale = toggled_ui_scale(self.ui_scale, self.layout)
        if not self.fullscreen:
            view = self.base_view_rect or self._full_rect()
            self.display = pygame.display.set_mode(
                windowed_fit_size(view.size, self.ui_scale, self.layout),
                pygame.RESIZABLE,
            )
            self.window_id = _display_window_id()
        self._refresh_content_rect()

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.display = pygame.display.set_mode(
                desktop_size(self.layout), pygame.FULLSCREEN
            )
        else:
            view = self.base_view_rect or self._full_rect()
            self.display = pygame.display.set_mode(
                windowed_fit_size(view.size, self.ui_scale, self.layout),
                pygame.RESIZABLE,
            )
        self.window_id = _display_window_id()
        self._refresh_content_rect()

    def fits_view_at_1x(self, rect: pygame.Rect) -> bool:
        usable_w, usable_h = usable_desktop_size(self.layout)
        return rect.width <= usable_w and rect.height <= usable_h

    def present(self, fps: int = 30) -> None:
        viewport = self._viewport()
        self.display.fill((0, 0, 0))
        source = self.screen.subsurface(viewport.source)
        if viewport.dest.size == viewport.source.size:
            self.display.blit(source, viewport.dest)
        else:
            scaled = pygame.transform.scale(source, viewport.dest.size)
            self.display.blit(scaled, viewport.dest)
        pygame.display.flip()
        self.clock.tick(fps)

    def close(self) -> None:
        pygame.quit()

    def _full_rect(self) -> pygame.Rect:
        return pygame.Rect(0, 0, self.layout.width, self.layout.height)

    def _viewport(self) -> Viewport:
        view = self.active_view_rect or self.base_view_rect or self._full_rect()
        return centered_viewport(view, self.display.get_size())

    def _refresh_content_rect(self) -> None:
        self.content_rect = self._viewport().dest


def _event_window_id(event: pygame.event.Event) -> int | None:
    """Return a stable SDL window id without comparing SDL2 Window objects directly."""
    event_window = (
        getattr(event, "window", None)
        or getattr(event, "windowID", None)
        or getattr(event, "window_id", None)
    )
    if event_window is None:
        return None
    if isinstance(event_window, int):
        return event_window
    try:
        return int(event_window.id)
    except Exception:
        return None


def _display_window_id() -> int | None:
    get_window_id = getattr(pygame.display, "get_window_id", None)
    if get_window_id is not None:
        try:
            return int(get_window_id())
        except Exception:
            pass
    try:
        from pygame._sdl2.video import Window

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return int(Window.from_display_module().id)
    except Exception:
        return None
