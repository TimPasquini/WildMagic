from __future__ import annotations

from types import SimpleNamespace

import wildmagic.rendering as rendering
from wildmagic.rendering.frame import draw_game_frame
from wildmagic.rendering.theme import BACKGROUND


class FakeScreen:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.fills: list[tuple[int, int, int]] = []

    def fill(self, color: tuple[int, int, int]) -> None:
        self.calls.append("fill")
        self.fills.append(color)


class FakeHost:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.screen = FakeScreen(self.calls)
        self.inspect_tile = None
        self.menu_active = False
        self.book_popup = None
        self.queue_debug_active = False
        self.awaiting_command = False
        self.scene = None
        self.tile_font = object()
        self.small_font = object()
        self.engine = object()
        self._command_label = "cast"
        self.autoplay = SimpleNamespace(overlay_lines=lambda: ["watching"])

    def _active_scene(self):
        return self.scene

    def _awaiting_command(self) -> bool:
        return self.awaiting_command

    def _llm_debug_embedded(self) -> bool:
        return True

    def draw_menu(self) -> None:
        self.calls.append("menu")


def _spy_frame_rendering(monkeypatch, host: FakeHost) -> None:
    monkeypatch.setattr(
        rendering, "draw_llm_panel", lambda target: target.calls.append("llm")
    )
    monkeypatch.setattr(
        rendering,
        "draw_map_layer",
        lambda context: (
            host.calls.append("map")
            if context.screen is host.screen
            and context.tile_font is host.tile_font
            and context.engine is host.engine
            else None
        ),
    )
    monkeypatch.setattr(
        rendering, "draw_hud_panel", lambda target: target.calls.append("panel")
    )
    monkeypatch.setattr(
        rendering,
        "draw_autoplay_overlay_layer",
        lambda context: (
            host.calls.append("autoplay")
            if context.screen is host.screen
            and context.small_font is host.small_font
            and context.autoplay_overlay_lines == ["watching"]
            else None
        ),
    )
    monkeypatch.setattr(
        rendering,
        "draw_resolving_indicator_layer",
        lambda context: (
            host.calls.append("resolving")
            if context.screen is host.screen
            and context.small_font is host.small_font
            and context.command_label == "cast"
            else None
        ),
    )
    monkeypatch.setattr(
        rendering,
        "draw_inspect_tooltip",
        lambda target: target.calls.append("inspect"),
    )
    monkeypatch.setattr(
        rendering, "draw_curse_tooltip", lambda target: target.calls.append("curse")
    )
    monkeypatch.setattr(
        rendering, "draw_book_popup", lambda target: target.calls.append("book")
    )
    monkeypatch.setattr(
        rendering, "draw_queue_debug", lambda target: target.calls.append("queue")
    )


def test_draw_game_frame_composes_base_view_in_order(monkeypatch) -> None:
    host = FakeHost()
    _spy_frame_rendering(monkeypatch, host)

    draw_game_frame(host)

    assert host.screen.fills == [BACKGROUND]
    assert host.calls == [
        "fill",
        "llm",
        "map",
        "panel",
        "autoplay",
        "curse",
    ]


def test_draw_game_frame_skips_embedded_llm_panel_when_debug_is_external(
    monkeypatch,
) -> None:
    host = FakeHost()
    host._llm_debug_embedded = lambda: False  # type: ignore[method-assign]
    _spy_frame_rendering(monkeypatch, host)

    draw_game_frame(host)

    assert host.calls == [
        "fill",
        "map",
        "panel",
        "autoplay",
        "curse",
    ]


def test_draw_game_frame_draws_active_overlays_after_base_view(monkeypatch) -> None:
    host = FakeHost()
    _spy_frame_rendering(monkeypatch, host)
    host.awaiting_command = True
    host.inspect_tile = (1, 2)
    host.menu_active = True
    host.book_popup = {"title": "Book"}
    host.queue_debug_active = True

    draw_game_frame(host)

    assert host.calls == [
        "fill",
        "llm",
        "map",
        "panel",
        "autoplay",
        "resolving",
        "inspect",
        "curse",
        "menu",
        "book",
        "queue",
    ]


def test_draw_game_frame_delegates_to_active_scene() -> None:
    host = FakeHost()
    scene_calls: list[str] = []
    host.scene = SimpleNamespace(draw=lambda: scene_calls.append("scene"))

    draw_game_frame(host)

    assert scene_calls == ["scene"]
    assert host.calls == []
