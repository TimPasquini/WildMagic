from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.normalize import normalize_trigger_name


def _engine_with_enemy(hp: int = 10):
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "brute", "B", player.x + 1, player.y, hp, 0, 0, "enemy", None
    )
    return engine, player, enemy


def test_trigger_when_hp_below_gates_until_condition_is_true() -> None:
    engine, player, enemy = _engine_with_enemy(hp=10)
    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_damaged",
            "target": enemy.id,
            "charges": 1,
            "duration": 5,
            "when": {"hp_below": 0.5},
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

    engine.damage_entity(enemy, 3, "arcane", source=player)
    assert enemy.hp == 7
    assert "bleeding" not in enemy.statuses
    assert len(engine.state.triggers) == 1

    engine.damage_entity(enemy, 3, "arcane", source=player)
    assert enemy.hp == 4
    assert enemy.statuses.get("bleeding") == 3
    assert engine.state.triggers == []


def test_step_multiple_predicate_uses_player_step_counter() -> None:
    engine, player, _enemy = _engine_with_enemy()
    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_player_move",
            "target": "player",
            "charges": 1,
            "duration": 5,
            "when": {"step_multiple": 2},
            "effects": [
                {
                    "type": "add_status",
                    "target": "trigger_target",
                    "status": "hasted",
                    "duration": 2,
                }
            ],
        }
    )

    engine.state.player_steps = 1
    engine._fire_triggers("on_player_move", {"target": player, "source": player})
    assert "hasted" not in player.statuses
    assert len(engine.state.triggers) == 1

    engine.state.player_steps = 2
    engine._fire_triggers("on_player_move", {"target": player, "source": player})
    assert player.statuses.get("hasted") == 2
    assert engine.state.triggers == []


def test_on_curse_gained_trigger_fires_from_curse_cost() -> None:
    engine, player, enemy = _engine_with_enemy()
    player.mana = 0
    player.max_mana = 5
    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_curse_gained",
            "target": "player",
            "charges": 1,
            "duration": 5,
            "effects": [
                {
                    "type": "damage",
                    "target": enemy.id,
                    "amount": 3,
                    "damage_type": "arcane",
                }
            ],
        }
    )

    hp_before = enemy.hp
    engine._apply_cost(
        {
            "type": "curse",
            "id": "test_curse",
            "name": "Test Curse",
            "description": "A test curse.",
        }
    )

    assert enemy.hp == hp_before - 3
    assert "test_curse" in engine.state.curses
    assert engine.state.triggers == []


def test_on_lethal_damage_can_make_same_blow_survivable() -> None:
    engine, player, enemy = _engine_with_enemy(hp=10)
    enemy.hp = 3
    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_lethal_damage",
            "target": enemy.id,
            "charges": 1,
            "duration": 5,
            "effects": [
                {"type": "heal", "target": "trigger_target", "amount": 5},
            ],
        }
    )

    engine.damage_entity(enemy, 5, "arcane", source=player)

    assert enemy.hp == 3
    assert enemy.alive
    assert enemy.blocks
    assert engine.state.triggers == []
    assert engine.state.stats.enemies_killed == 0


def test_on_lethal_damage_passes_through_when_payload_does_not_save_target() -> None:
    engine, player, enemy = _engine_with_enemy(hp=10)
    enemy.hp = 3
    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_lethal_damage",
            "target": enemy.id,
            "charges": 1,
            "duration": 5,
            "effects": [{"type": "message", "text": "Too late."}],
        }
    )

    engine.damage_entity(enemy, 5, "arcane", source=player)

    assert enemy.hp == 0
    assert not enemy.blocks
    assert engine.state.stats.enemies_killed == 1


def test_on_enters_sight_fires_once_when_entity_becomes_visible() -> None:
    engine, player, _enemy = _engine_with_enemy()
    engine.state.fov_radius = 3
    hidden = engine.spawn_actor("distant brute", "D", 2, 2, 10, 0, 0, "enemy", None)
    engine.update_fov()
    assert hidden.id not in engine.state.visible_entity_ids

    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_enters_sight",
            "target": "enemy",
            "charges": 1,
            "duration": 5,
            "effects": [
                {
                    "type": "add_status",
                    "target": "trigger_target",
                    "status": "frozen",
                    "duration": 2,
                }
            ],
        }
    )

    hidden.x = player.x
    hidden.y = player.y - 3
    engine.update_fov()
    assert hidden.statuses.get("frozen") == 2
    assert engine.state.triggers == []

    hidden.statuses.pop("frozen")
    engine.update_fov()
    assert "frozen" not in hidden.statuses


def test_on_enters_sight_does_not_fire_for_already_visible_entity() -> None:
    engine, player, _enemy = _engine_with_enemy()
    engine.state.fov_radius = 4
    visible_enemy = engine.spawn_actor(
        "visible brute", "V", player.x, player.y - 3, 10, 0, 0, "enemy", None
    )
    engine.update_fov()
    assert visible_enemy.id in engine.state.visible_entity_ids

    engine._apply_effect(
        {
            "type": "create_trigger",
            "trigger": "on_enters_sight",
            "target": "enemy",
            "charges": 1,
            "duration": 5,
            "effects": [
                {
                    "type": "add_status",
                    "target": "trigger_target",
                    "status": "frozen",
                    "duration": 2,
                }
            ],
        }
    )

    engine.update_fov()
    assert "frozen" not in visible_enemy.statuses
    assert len(engine.state.triggers) == 1


def test_new_trigger_aliases_normalize() -> None:
    assert normalize_trigger_name("before_death") == "on_lethal_damage"
    assert normalize_trigger_name("when_cursed") == "on_curse_gained"
    assert normalize_trigger_name("when_seen") == "on_enters_sight"
