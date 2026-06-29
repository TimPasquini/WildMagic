from __future__ import annotations

from types import SimpleNamespace

import pygame

from wildmagic.rendering.book_popup import draw_book_popup


class FakeRenderedText:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_width(self) -> int:
        return len(self.text) * 8

    def get_height(self) -> int:
        return 12


class FakeFont:
    def __init__(self, linesize: int) -> None:
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


def _host(text: str, page: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        screen=FakeScreen(),
        book_title_font=FakeFont(20),
        book_font=FakeFont(16),
        book_small_font=FakeFont(12),
        book_popup={
            "title": "A Very Important Book",
            "author": "Archivist",
            "text": text,
            "page": page,
            "page_count": 1,
        },
    )


def test_draw_book_popup_updates_page_count_and_draws_last_page_hint(
    monkeypatch,
) -> None:
    surfaces: list[FakeSurface] = []

    def surface(size: tuple[int, int], _flags: int) -> FakeSurface:
        result = FakeSurface(size)
        surfaces.append(result)
        return result

    monkeypatch.setattr(pygame, "Surface", surface)
    monkeypatch.setattr(pygame.draw, "rect", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pygame.draw, "line", lambda *_args, **_kwargs: None)
    host = _host("short text")

    draw_book_popup(host)

    assert host.book_popup["page"] == 0
    assert host.book_popup["page_count"] == 1
    assert surfaces[0].fills == [(10, 8, 4, 180)]
    assert "Esc or click to close the book" in host.book_small_font.rendered


def test_draw_book_popup_clamps_page_and_draws_page_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        pygame,
        "Surface",
        lambda size, _flags: FakeSurface(size),
    )
    monkeypatch.setattr(pygame.draw, "rect", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pygame.draw, "line", lambda *_args, **_kwargs: None)
    text = " ".join(f"word{i}" for i in range(900))
    host = _host(text, page=999)

    draw_book_popup(host)

    assert host.book_popup["page_count"] > 1
    assert host.book_popup["page"] == host.book_popup["page_count"] - 1
    assert any(
        rendered.startswith("— ") and rendered.endswith(" —")
        for rendered in host.book_small_font.rendered
    )
