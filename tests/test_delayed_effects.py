from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.models import FIRE
from wildmagic.spell_contract import SUPPORTED_EFFECTS, validate_resolution


def _engine_with_enemy():
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "brute", "B", player.x + 1, player.y, 30, 0, 0, "enemy", None
    )
    return engine, player, enemy


def test_delayed_damage_resists_and_triggers_only_on_release() -> None:
    engine, player, enemy = _engine_with_enemy()
    enemy.resistances["arcane"] = 50
    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_damaged",
            "target": enemy.id,
            "charges": 1,
            "duration": 5,
            "effects": [
                {
                    "type": "add_status",
                    "target": "trigger_target",
                    "status": "bleeding",
                    "duration": 3,
                }
            ],
        }
    )
    engine._apply_effect({"type": "delay_incoming", "target": enemy.id, "turns": 2})

    hp_before = enemy.hp
    assert engine.damage_entity(enemy, 10, "arcane", source=player) == 0
    assert enemy.hp == hp_before
    assert "bleeding" not in enemy.statuses

    engine.finish_player_turn()
    assert enemy.hp == hp_before
    assert "bleeding" not in enemy.statuses

    engine.finish_player_turn()
    assert enemy.hp == hp_before - 5
    assert enemy.statuses.get("bleeding") == 3
    assert "delayed_sink" not in enemy.statuses
    assert "delayed_damage" not in enemy.details


def test_schedule_event_runs_effects_and_costs_payload() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.mana = 5
    player.max_mana = 10

    engine._apply_effect(
        {
            "type": "schedule_event",
            "turns": 1,
            "text": "The borrowed hour rings.",
            "effects": [{"type": "restore_mana", "target": "player", "amount": 4}],
            "costs": [{"type": "mana", "amount": 3}],
        }
    )

    engine.finish_player_turn()

    assert player.mana == 6
    assert any("borrowed hour" in message for message in engine.state.messages)
    assert engine.state.event_timers == []


def test_accelerate_status_bursts_remaining_damage_ticks() -> None:
    engine, _player, enemy = _engine_with_enemy()
    enemy.statuses["poisoned"] = 3

    hp_before = enemy.hp
    engine._apply_effect(
        {"type": "accelerate_status", "target": enemy.id, "status": "poisoned"}
    )

    assert enemy.hp == hp_before - 3
    assert "poisoned" not in enemy.statuses


def test_accelerate_burning_does_not_leave_burning_behind() -> None:
    engine, _player, enemy = _engine_with_enemy()
    enemy.statuses["burning"] = 2

    engine._apply_effect(
        {"type": "accelerate_status", "target": enemy.id, "status": "burning"}
    )

    assert "burning" not in enemy.statuses


def test_stasis_pauses_timers_tiles_and_statuses_for_whole_turn() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.statuses["stasis"] = 2
    player.statuses["poisoned"] = 3
    engine.set_tile(player.x + 1, player.y, FIRE, duration=2)
    tile_key = engine.tile_key(player.x + 1, player.y)
    engine._apply_effect(
        {
            "type": "schedule_event",
            "turns": 1,
            "effects": [{"type": "restore_mana", "target": "player", "amount": 1}],
        }
    )

    mana_before = player.mana
    engine.finish_player_turn()
    assert player.statuses["stasis"] == 1
    assert player.statuses["poisoned"] == 3
    assert engine.state.tile_durations[tile_key] == 2
    assert engine.state.event_timers[0]["turns"] == 1
    assert player.mana == mana_before

    engine.finish_player_turn()
    assert "stasis" not in player.statuses
    assert player.statuses["poisoned"] == 3
    assert engine.state.tile_durations[tile_key] == 2
    assert engine.state.event_timers[0]["turns"] == 1
    assert player.mana == mana_before

    engine.finish_player_turn()
    assert player.statuses["poisoned"] == 2
    assert engine.state.tile_durations[tile_key] == 1
    assert engine.state.event_timers == []


def test_delayed_effects_are_registered_in_the_contract() -> None:
    assert {"delay_incoming", "accelerate_status"} <= SUPPORTED_EFFECTS
    assert (
        validate_resolution(
            {
                "accepted": True,
                "severity": "moderate",
                "outcome_text": "Pain waits outside the door.",
                "effects": [
                    {"type": "delay_incoming", "target": "player", "turns": 3},
                    {
                        "type": "accelerate_status",
                        "target": "nearest_enemy",
                        "status": "poisoned",
                    },
                ],
                "costs": [{"type": "mana", "amount": 3}],
                "rejected_reason": None,
            }
        )
        is None
    )
