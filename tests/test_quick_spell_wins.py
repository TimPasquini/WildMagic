from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.models import STAIRS_DOWN, WALL


def test_sight_shrouded_status_reduces_and_restores_fov() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    engine.state.fov_radius = 6
    engine.update_fov()
    far_key = next(
        key
        for key in engine.state.visible
        if abs(int(key.split(",")[0]) - player.x)
        + abs(int(key.split(",")[1]) - player.y)
        > 3
    )
    assert far_key in engine.state.visible

    engine._apply_effect(
        {
            "type": "add_status",
            "target": "player",
            "status": "sight_shrouded",
            "sight_radius": 1,
            "duration": 2,
        }
    )

    assert engine.effective_fov_radius() == 1
    assert far_key not in engine.state.visible

    engine._apply_effect(
        {"type": "remove_status", "target": "player", "status": "sight_shrouded"}
    )

    assert engine.effective_fov_radius() == 6
    assert far_key in engine.state.visible


def test_blind_alias_applies_sight_shrouded() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")

    engine._apply_effect(
        {
            "type": "add_status",
            "target": "player",
            "status": "blind",
            "radius": 0,
            "duration": 2,
        }
    )

    assert "sight_shrouded" in engine.state.player.statuses
    assert engine.effective_fov_radius() == 0


def test_seal_stairs_flag_blocks_vertical_travel() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    assert engine.tile_at(engine.state.player.x, engine.state.player.y) == STAIRS_DOWN

    engine._apply_effect({"type": "set_flag", "flag": "seal_stairs", "value": True})

    assert engine.descend_stairs() is False
    assert engine.state.depth == 1
    assert engine.state.messages[-1] == "The stairs are sealed by magic."


def test_spell_created_walls_cannot_box_player_in() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    for dx, dy in ((1, 0), (-1, 0), (0, 1)):
        engine.set_tile(player.x + dx, player.y + dy, WALL)

    messages = engine._apply_effect(
        {
            "type": "create_tiles",
            "tiles": [{"x": player.x, "y": player.y - 1, "tile": "wall"}],
        }
    )

    assert messages == ["Terrain changes to wall on 0 tile(s)."]
    assert engine.tile_at(player.x, player.y - 1) != WALL


def test_deceptive_message_is_log_only() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")

    messages = engine._apply_effect(
        {"type": "message", "text": "You gain a moonlit crown.", "spoof": True}
    )

    assert messages == ["You gain a moonlit crown."]
    assert "moonlit crown" not in engine.state.inventory
