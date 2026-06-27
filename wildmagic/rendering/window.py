from __future__ import annotations

from dataclasses import dataclass

import pygame

from wildmagic.rendering.layout import (
    DEFAULT_WINDOW_LAYOUT,
    WindowLayout,
    auto_ui_scale,
    logical_mouse_event,
    logical_mouse_pos,
    toggled_ui_scale,
)


@dataclass
class GameWindow:
    display: pygame.Surface
    screen: pygame.Surface
    clock: pygame.time.Clock
    ui_scale: int
    layout: WindowLayout = DEFAULT_WINDOW_LAYOUT

    @classmethod
    def create(
        cls, caption: str, layout: WindowLayout = DEFAULT_WINDOW_LAYOUT
    ) -> "GameWindow":
        pygame.init()
        pygame.key.set_repeat()
        pygame.display.set_caption(caption)
        ui_scale = auto_ui_scale(layout)
        display = pygame.display.set_mode(layout.scaled_size(ui_scale))
        screen = pygame.Surface((layout.width, layout.height))
        clock = pygame.time.Clock()
        return cls(display, screen, clock, ui_scale, layout)

    def logical_mouse_event(self, event: pygame.event.Event) -> pygame.event.Event:
        return logical_mouse_event(event, self.ui_scale)

    def logical_mouse_pos(self) -> tuple[int, int]:
        return logical_mouse_pos(self.ui_scale)

    def toggle_scale(self) -> None:
        self.ui_scale = toggled_ui_scale(self.ui_scale, self.layout)
        self.display = pygame.display.set_mode(self.layout.scaled_size(self.ui_scale))

    def present(self, fps: int = 30) -> None:
        pygame.transform.scale(self.screen, self.display.get_size(), self.display)
        pygame.display.flip()
        self.clock.tick(fps)

    def close(self) -> None:
        pygame.quit()
