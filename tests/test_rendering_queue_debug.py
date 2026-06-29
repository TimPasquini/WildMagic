from __future__ import annotations

from types import SimpleNamespace

import pygame

from wildmagic.rendering.queue_debug import draw_queue_debug


class FakeRenderedText:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_width(self) -> int:
        return len(self.text) * 8


class FakeFont:
    def __init__(self, linesize: int = 14) -> None:
        self.linesize = linesize
        self.rendered: list[str] = []

    def get_linesize(self) -> int:
        return self.linesize

    def render(
        self, text: str, _antialias: bool, _color: tuple[int, int, int]
    ) -> FakeRenderedText:
        self.rendered.append(text)
        return FakeRenderedText(text)


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


def _snapshot(book_count: int = 0) -> dict:
    return {
        "titles_enabled": True,
        "saturation_enabled": False,
        "limit": 2,
        "pending_canon": 1,
        "pending_lore": 0,
        "pending_flesh": 3,
        "now_next": [
            {
                "status": "running",
                "kind": "book",
                "label": "A very long generated book title that needs truncation here",
            }
        ],
        "books": [
            {
                "distance": index,
                "name": f"Book {index}",
                "title": "done",
                "pages": "queued",
            }
            for index in range(book_count)
        ],
    }


def _host(book_count: int = 0, scroll: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        screen=FakeScreen(),
        small_font=FakeFont(),
        ui_font=FakeFont(17),
        queue_debug_scroll=scroll,
        _queue_debug_max_scroll=0,
        session=SimpleNamespace(canon_queue_snapshot=lambda: _snapshot(book_count)),
    )


def test_draw_queue_debug_renders_summary_and_worker(monkeypatch) -> None:
    surfaces: list[FakeSurface] = []

    def surface(size: tuple[int, int], _flags: int) -> FakeSurface:
        result = FakeSurface(size)
        surfaces.append(result)
        return result

    monkeypatch.setattr(pygame, "Surface", surface)
    monkeypatch.setattr(pygame.draw, "rect", lambda *_args, **_kwargs: None)
    host = _host()

    draw_queue_debug(host)

    rendered = host.small_font.rendered + host.ui_font.rendered
    assert surfaces[0].fills == [(0, 0, 0, 180)]
    assert "Generation Queue" in rendered
    assert "titles on   saturation off   depth 2" in rendered
    assert any(text.startswith("  [generating] pages:") for text in rendered)
    assert host._queue_debug_max_scroll == 0


def test_draw_queue_debug_clamps_scroll_and_draws_footer(monkeypatch) -> None:
    monkeypatch.setattr(pygame, "Surface", lambda size, _flags: FakeSurface(size))
    monkeypatch.setattr(pygame.draw, "rect", lambda *_args, **_kwargs: None)
    host = _host(book_count=80, scroll=999)

    draw_queue_debug(host)

    assert host._queue_debug_max_scroll > 0
    assert host.queue_debug_scroll == host._queue_debug_max_scroll
    assert any(text.startswith("showing ") for text in host.small_font.rendered)
