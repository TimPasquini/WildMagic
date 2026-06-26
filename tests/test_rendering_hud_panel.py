from __future__ import annotations

from types import SimpleNamespace

from wildmagic.engine import LogMessage
from wildmagic.rendering.hud_panel import input_line_slices, is_player_damage_message


def _host(text: str) -> SimpleNamespace:
    return SimpleNamespace(input_text=text)


def test_input_line_slices_preserves_empty_input_as_clickable_line() -> None:
    assert input_line_slices(_host("")) == [(" ", 0, 0)]


def test_input_line_slices_wraps_on_word_boundary() -> None:
    assert input_line_slices(_host("cast a very bright light"), wrap_chars=12) == [
        ("cast a very", 0, 11),
        ("bright light", 12, 24),
    ]


def test_input_line_slices_splits_long_word_when_no_boundary_exists() -> None:
    assert input_line_slices(_host("abcdef"), wrap_chars=3) == [
        ("abc", 0, 3),
        ("def", 3, 6),
    ]


def test_is_player_damage_message_respects_explicit_log_flag() -> None:
    assert is_player_damage_message(LogMessage("You take the key.", is_danger=True))
    assert not is_player_damage_message(
        LogMessage("You take the key.", is_danger=False)
    )


def test_is_player_damage_message_detects_damage_but_not_item_pickups() -> None:
    assert is_player_damage_message("You take 5 physical damage.")
    assert is_player_damage_message("You lose 3 health.")
    assert is_player_damage_message("Cost: 3 health.")
    assert is_player_damage_message("You die.")
    assert not is_player_damage_message("You take the golden key.")
    assert not is_player_damage_message("Cost: 3 mana.")
