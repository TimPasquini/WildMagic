from __future__ import annotations

from typing import Any

import pygame

from wildmagic.rendering import llm_panel
from wildmagic.rendering.layout import LLM_PANEL_WIDTH, WINDOW_HEIGHT


class LlmDebugWindow:
    """Independent OS window for the LLM debug panel.

    Pygame's classic display module owns only one window; the SDL2 API supplies
    the second native window while the panel itself still renders to a normal
    pygame Surface.
    """

    def __init__(
        self, width: int = LLM_PANEL_WIDTH, height: int = WINDOW_HEIGHT
    ) -> None:
        from pygame._sdl2.video import Renderer, Window

        self.window = Window(
            "Wild Magic - LLM Debug",
            size=(width, height),
            resizable=True,
        )
        self.renderer = Renderer(self.window)
        self.surface = pygame.Surface((width, height))
        self.window_id = int(self.window.id)
        self.open = True

    def close(self) -> None:
        if not self.open:
            return
        self.open = False
        try:
            self.window.destroy()
        except Exception:
            pass

    def owns_event(self, event: pygame.event.Event) -> bool:
        event_window = _event_window_id(event)
        return event_window is not None and event_window == self.window_id

    def draw(self, host: Any) -> None:
        if not self.open:
            return
        width, height = self._window_size()
        if self.surface.get_size() != (width, height):
            self.surface = pygame.Surface((width, height))

        old_screen = host.screen
        old_width = getattr(host, "llm_panel_width", None)
        old_height = getattr(host, "llm_panel_height", None)
        host.screen = self.surface
        host.llm_panel_width = width
        host.llm_panel_height = height
        try:
            llm_panel.draw_panel(host)
        finally:
            host.screen = old_screen
            if old_width is None:
                delattr(host, "llm_panel_width")
            else:
                host.llm_panel_width = old_width
            if old_height is None:
                delattr(host, "llm_panel_height")
            else:
                host.llm_panel_height = old_height

        self.renderer.clear()
        texture = self._texture_from_surface()
        texture.draw()
        self.renderer.present()

    def _window_size(self) -> tuple[int, int]:
        width, height = self.window.size
        return max(320, int(width)), max(240, int(height))

    def _texture_from_surface(self) -> Any:
        from pygame._sdl2.video import Texture

        return Texture.from_surface(self.renderer, self.surface)


def _event_window_id(event: pygame.event.Event) -> int | None:
    event_window = getattr(event, "window", None)
    if event_window is None:
        return None
    if isinstance(event_window, int):
        return event_window
    try:
        return int(event_window.id)
    except Exception:
        return None
