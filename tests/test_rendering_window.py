from __future__ import annotations

from types import SimpleNamespace

import pygame

from wildmagic.rendering.layout import WindowLayout
from wildmagic.rendering.window import GameWindow


class DisplaySurface:
    def __init__(self, size: tuple[int, int]) -> None:
        self._size = size

    def get_size(self) -> tuple[int, int]:
        return self._size


class Clock:
    def __init__(self) -> None:
        self.ticks: list[int] = []

    def tick(self, fps: int) -> None:
        self.ticks.append(fps)


def test_game_window_create_sets_caption_and_scaled_display(monkeypatch) -> None:
    layout = WindowLayout(width=100, height=50, max_ui_scale=2)
    calls: dict[str, object] = {}

    monkeypatch.setattr(pygame, "init", lambda: calls.setdefault("init", True))
    monkeypatch.setattr(
        pygame.key, "set_repeat", lambda: calls.setdefault("repeat", True)
    )
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [(240, 240)])
    monkeypatch.setattr(
        pygame.display,
        "set_caption",
        lambda caption: calls.setdefault("caption", caption),
    )
    monkeypatch.setattr(
        pygame.display,
        "set_mode",
        lambda size: calls.setdefault("display_size", size) or DisplaySurface(size),
    )
    monkeypatch.setattr(pygame, "Surface", lambda size: SimpleNamespace(size=size))
    monkeypatch.setattr(pygame.time, "Clock", Clock)

    window = GameWindow.create("Wild Magic", layout)

    assert calls["init"] is True
    assert calls["repeat"] is True
    assert calls["caption"] == "Wild Magic"
    assert calls["display_size"] == (200, 100)
    assert window.ui_scale == 2
    assert window.screen.size == (100, 50)


def test_game_window_toggle_scale_rebuilds_display(monkeypatch) -> None:
    layout = WindowLayout(width=100, height=50, max_ui_scale=2)
    sizes: list[tuple[int, int]] = []

    def set_mode(size: tuple[int, int]) -> DisplaySurface:
        sizes.append(size)
        return DisplaySurface(size)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    window = GameWindow(
        display=DisplaySurface((100, 50)),
        screen=SimpleNamespace(),
        clock=Clock(),
        ui_scale=1,
        layout=layout,
    )

    window.toggle_scale()

    assert window.ui_scale == 2
    assert sizes == [(200, 100)]
    assert window.display.get_size() == (200, 100)


def test_game_window_present_scales_flips_and_ticks(monkeypatch) -> None:
    clock = Clock()
    window = GameWindow(
        display=DisplaySurface((200, 100)),
        screen=SimpleNamespace(),
        clock=clock,
        ui_scale=2,
    )
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        pygame.transform,
        "scale",
        lambda screen, size, display: calls.append(("scale", size)),
    )
    monkeypatch.setattr(pygame.display, "flip", lambda: calls.append(("flip", None)))

    window.present(fps=42)

    assert calls == [("scale", (200, 100)), ("flip", None)]
    assert clock.ticks == [42]


def test_game_window_close_quits_pygame(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(pygame, "quit", lambda: calls.append("quit"))
    window = GameWindow(
        display=DisplaySurface((200, 100)),
        screen=SimpleNamespace(),
        clock=Clock(),
        ui_scale=2,
    )

    window.close()

    assert calls == ["quit"]


def test_game_window_converts_mouse_events() -> None:
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (30, 18), "button": 1})
    window = GameWindow(
        display=DisplaySurface((200, 100)),
        screen=SimpleNamespace(),
        clock=Clock(),
        ui_scale=3,
    )

    logical = window.logical_mouse_event(event)

    assert logical.pos == (10, 6)
    assert logical.button == 1
