from __future__ import annotations

from wildmagic import capabilities as cap
from wildmagic.engine import GameEngine
from wildmagic.models import WALL
from wildmagic.spell_contract import SUPPORTED_EFFECTS, validate_resolution


def _engine_with_enemy():
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "brute", "B", player.x + 2, player.y, 12, 3, 0, "enemy", None
    )
    return engine, player, enemy


def test_create_flow_moves_entity_one_tile_per_environment_tick() -> None:
    engine, _player, enemy = _engine_with_enemy()
    start = (enemy.x, enemy.y)

    engine._apply_effect(
        {
            "type": "create_flow",
            "target": enemy.id,
            "direction": "east",
            "duration": 3,
        }
    )
    engine._tick_environment()

    assert (enemy.x, enemy.y) == (start[0] + 1, start[1])
    assert enemy.details["last_move_delta"] == [1, 0]


def test_create_flow_respects_blocking_tiles() -> None:
    engine, _player, enemy = _engine_with_enemy()
    engine.set_tile(enemy.x + 1, enemy.y, WALL)

    engine._apply_effect(
        {
            "type": "create_flow",
            "target": enemy.id,
            "direction": "east",
            "duration": 3,
        }
    )
    engine._tick_environment()

    assert (enemy.x, enemy.y) == (engine.state.player.x + 2, engine.state.player.y)


def test_create_flow_expires_after_duration_tick() -> None:
    engine, _player, enemy = _engine_with_enemy()
    engine._apply_effect(
        {
            "type": "create_flow",
            "target": enemy.id,
            "direction": "east",
            "duration": 1,
        }
    )
    assert engine.state.tile_flows

    engine._tick_environment()
    engine._tick_tile_durations()

    assert engine.state.tile_flows == {}


def test_inward_flow_pulls_toward_center() -> None:
    engine, player, _enemy = _engine_with_enemy()
    center_x = player.x + 4
    center_y = player.y
    pulled = engine.spawn_actor(
        "loose brute", "L", center_x + 2, center_y, 12, 3, 0, "enemy", None
    )

    engine._apply_effect(
        {
            "type": "create_flow",
            "x": center_x,
            "y": center_y,
            "radius": 3,
            "mode": "inward",
            "duration": 3,
        }
    )
    engine._tick_environment()

    assert (pulled.x, pulled.y) == (center_x + 1, center_y)


def test_flow_movement_order_is_deterministic_by_entity_id() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    first = engine.spawn_actor(
        "first", "f", player.x + 2, player.y, 10, 1, 0, "enemy", None
    )
    second = engine.spawn_actor(
        "second", "s", player.x + 3, player.y, 10, 1, 0, "enemy", None
    )

    engine._apply_effect(
        {
            "type": "create_flow",
            "tiles": [
                {"x": first.x, "y": first.y},
                {"x": second.x, "y": second.y},
            ],
            "direction": "east",
            "duration": 3,
        }
    )
    engine._tick_environment()

    assert (first.x, first.y) == (player.x + 2, player.y)
    assert (second.x, second.y) == (player.x + 4, player.y)


def test_create_flow_contract_and_routing_are_live() -> None:
    assert "create_flow" in SUPPORTED_EFFECTS
    err = validate_resolution(
        {
            "accepted": True,
            "severity": "moderate",
            "outcome_text": "The floor starts moving east.",
            "effects": [
                {
                    "type": "create_flow",
                    "target": "nearest_enemy",
                    "direction": "east",
                    "radius": 2,
                    "duration": 3,
                }
            ],
            "costs": [],
            "rejected_reason": None,
        }
    )
    assert err is None
    assert "environment_flow" in {
        card.name
        for card in cap.select_cards("make a conveyor current under the enemy")
    }
