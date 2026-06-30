from __future__ import annotations

from types import SimpleNamespace

import pygame

from wildmagic.rendering.layout import WindowLayout
from wildmagic.rendering.window import GameWindow


class DisplaySurface:
    def __init__(self, size: tuple[int, int]) -> None:
        self._size = size
        self.fills: list[tuple[int, int, int]] = []
        self.blits: list[tuple[object, object]] = []

    def get_size(self) -> tuple[int, int]:
        return self._size

    def fill(self, color: tuple[int, int, int]) -> None:
        self.fills.append(color)

    def blit(self, source: object, dest: object) -> None:
        self.blits.append((source, dest))


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

    def set_mode(size: tuple[int, int], flags: int = 0) -> DisplaySurface:
        calls.setdefault("display_size", size)
        calls.setdefault("flags", flags)
        return DisplaySurface(size)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    monkeypatch.setattr(pygame, "Surface", lambda size: SimpleNamespace(size=size))
    monkeypatch.setattr(pygame.time, "Clock", Clock)

    window = GameWindow.create("Wild Magic", layout)

    assert calls["init"] is True
    assert calls["repeat"] is True
    assert calls["caption"] == "Wild Magic"
    assert calls["display_size"] == (200, 100)
    assert calls["flags"] == pygame.RESIZABLE
    assert window.ui_scale == 2
    assert window.screen.size == (100, 50)


def test_game_window_toggle_scale_rebuilds_display(monkeypatch) -> None:
    layout = WindowLayout(width=100, height=50, max_ui_scale=2)
    sizes: list[tuple[tuple[int, int], int]] = []

    def set_mode(size: tuple[int, int], flags: int = 0) -> DisplaySurface:
        sizes.append((size, flags))
        return DisplaySurface(size)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [(240, 240)])
    window = GameWindow(
        display=DisplaySurface((100, 50)),
        screen=SimpleNamespace(),
        clock=Clock(),
        ui_scale=1,
        layout=layout,
    )

    window.toggle_scale()

    assert window.ui_scale == 2
    assert sizes == [((200, 100), pygame.RESIZABLE)]
    assert window.display.get_size() == (200, 100)


def test_game_window_present_scales_flips_and_ticks(monkeypatch) -> None:
    clock = Clock()
    window = GameWindow(
        display=DisplaySurface((200, 100)),
        screen=pygame.Surface((100, 50)),
        clock=clock,
        ui_scale=2,
        layout=WindowLayout(width=100, height=50, max_ui_scale=2),
    )
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        pygame.transform,
        "scale",
        lambda screen, size: calls.append(("scale", size)) or SimpleNamespace(),
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
        display=DisplaySurface((300, 150)),
        screen=SimpleNamespace(),
        clock=Clock(),
        ui_scale=3,
        layout=WindowLayout(width=100, height=50, max_ui_scale=3),
        base_view_rect=pygame.Rect(0, 0, 100, 50),
        active_view_rect=pygame.Rect(0, 0, 100, 50),
    )

    logical = window.logical_mouse_event(event)

    assert logical.pos == (10, 6)
    assert logical.button == 1


def test_game_window_create_downscales_to_fit_desktop(monkeypatch) -> None:
    layout = WindowLayout(width=200, height=100, max_ui_scale=2)
    calls: dict[str, object] = {}

    monkeypatch.setattr(pygame, "init", lambda: None)
    monkeypatch.setattr(pygame.key, "set_repeat", lambda: None)
    monkeypatch.setattr(pygame.display, "set_caption", lambda _caption: None)
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [(150, 160)])

    def set_mode(size: tuple[int, int], flags: int = 0) -> DisplaySurface:
        calls["display_size"] = size
        calls["flags"] = flags
        return DisplaySurface(size)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    monkeypatch.setattr(pygame, "Surface", lambda size: SimpleNamespace(size=size))
    monkeypatch.setattr(pygame.time, "Clock", Clock)

    GameWindow.create("Wild Magic", layout)

    assert calls["display_size"] == (150, 75)
    assert calls["flags"] == pygame.RESIZABLE


def test_game_window_fullscreen_uses_desktop_size(monkeypatch) -> None:
    layout = WindowLayout(width=100, height=50, max_ui_scale=2)
    calls: dict[str, object] = {}

    monkeypatch.setattr(pygame, "init", lambda: None)
    monkeypatch.setattr(pygame.key, "set_repeat", lambda: None)
    monkeypatch.setattr(pygame.display, "set_caption", lambda _caption: None)
    monkeypatch.setattr(pygame.display, "get_desktop_sizes", lambda: [(1920, 1080)])

    def set_mode(size: tuple[int, int], flags: int = 0) -> DisplaySurface:
        calls["display_size"] = size
        calls["flags"] = flags
        return DisplaySurface(size)

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    monkeypatch.setattr(pygame, "Surface", lambda size: SimpleNamespace(size=size))
    monkeypatch.setattr(pygame.time, "Clock", Clock)

    window = GameWindow.create("Wild Magic", layout, fullscreen=True)

    assert window.fullscreen is True
    assert calls["display_size"] == (1920, 1080)
    assert calls["flags"] == pygame.FULLSCREEN
