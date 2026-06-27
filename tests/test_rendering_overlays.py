from __future__ import annotations

from types import SimpleNamespace

import pygame

from wildmagic.rendering.overlays import (
    draw_autoplay_overlay,
    draw_resolving_indicator,
)


class FakeRenderedText:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_width(self) -> int:
        return len(self.text) * 8

    def get_height(self) -> int:
        return 12


class FakeFont:
    def __init__(self) -> None:
        self.rendered: list[tuple[str, tuple[int, int, int]]] = []

    def render(
        self, text: str, _antialias: bool, color: tuple[int, int, int]
    ) -> FakeRenderedText:
        self.rendered.append((text, color))
        return FakeRenderedText(text)

    def get_linesize(self) -> int:
        return 14


class FakeScreen:
    def __init__(self) -> None:
        self.blits: list[tuple[object, tuple[int, int]]] = []

    def blit(self, surface: object, pos: tuple[int, int]) -> None:
        self.blits.append((surface, pos))


class FakeSurface:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.fills: list[tuple[int, int, int, int]] = []

    def fill(self, color: tuple[int, int, int, int]) -> None:
        self.fills.append(color)


def test_draw_resolving_indicator_truncates_long_command(monkeypatch) -> None:
    surfaces: list[FakeSurface] = []
    rects: list[tuple[object, object]] = []

    def surface(size: tuple[int, int], _flags: int) -> FakeSurface:
        result = FakeSurface(size)
        surfaces.append(result)
        return result

    monkeypatch.setattr(pygame, "Surface", surface)
    monkeypatch.setattr(
        pygame.draw,
        "rect",
        lambda _screen, color, rect, **_kwargs: rects.append((color, rect)),
    )
    screen = FakeScreen()
    font = FakeFont()

    draw_resolving_indicator(screen, font, "x" * 80)

    assert font.rendered[0][0] == f"Resolving: {'x' * 45}..."
    assert surfaces[0].fills == [(20, 22, 28, 235)]
    assert len(screen.blits) == 2
    assert rects


def test_draw_autoplay_overlay_wraps_lines_and_draws_text(monkeypatch) -> None:
    surfaces: list[FakeSurface] = []
    rects: list[tuple[object, object]] = []

    def surface(size: tuple[int, int], _flags: int) -> FakeSurface:
        result = FakeSurface(size)
        surfaces.append(result)
        return result

    monkeypatch.setattr(pygame, "Surface", surface)
    monkeypatch.setattr(
        pygame.draw,
        "rect",
        lambda _screen, color, rect, **_kwargs: rects.append((color, rect)),
    )
    screen = FakeScreen()
    font = FakeFont()

    draw_autoplay_overlay(
        screen,
        font,
        [("one two three four five six seven eight nine ten eleven twelve", (1, 2, 3))],
    )

    assert surfaces[0].fills == [(17, 19, 24, 222)]
    assert rects
    assert [text for text, _color in font.rendered]
    assert all(len(text) <= 62 for text, _color in font.rendered)


def test_draw_autoplay_overlay_ignores_empty_lines(monkeypatch) -> None:
    monkeypatch.setattr(
        pygame,
        "Surface",
        lambda *_args, **_kwargs: SimpleNamespace(fill=lambda _color: None),
    )
    screen = FakeScreen()
    font = FakeFont()

    draw_autoplay_overlay(screen, font, [])

    assert screen.blits == []
    assert font.rendered == []
