"""Engine tests for the general aura mechanic: a standing emanation that re-fires every
turn, always coupled to a concrete effect (damage, or a buff/debuff status). Auras can be
borne by any entity (creature/item/prop) or anchored to a tile, and resolve in
GameEngine._tick_auras."""

from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.models import Entity


def _engine_with_enemy(dx: int = 1):
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "test goblin", "g", player.x + dx, player.y, 14, 2, 0, "enemy", "simple"
    )
    return engine, enemy


# --- standalone aura effect -------------------------------------------------------------


def test_self_aura_damages_adjacent_enemies_each_turn() -> None:
    engine, enemy = _engine_with_enemy()
    engine._apply_effect(
        {
            "type": "aura",
            "target": "player",
            "kind": "damage",
            "amount": 3,
            "damage_type": "fire",
            "radius": 3,
            "affects": "enemies",
            "turns": 2,
        }
    )
    assert engine.state.player.auras, "aura should be anchored on the caster"
    hp0 = enemy.hp
    engine.finish_player_turn()
    assert enemy.hp == hp0 - 3


def test_self_aura_expires_after_its_turns() -> None:
    engine, enemy = _engine_with_enemy()
    # "force" rather than "fire": fire damage also ignites (a lingering burning tick),
    # which would mask whether the aura itself stopped firing.
    engine._apply_effect(
        {"type": "aura", "target": "player", "kind": "damage", "amount": 1,
         "damage_type": "force", "radius": 3, "turns": 2}
    )
    engine.finish_player_turn()  # turn 1 fires, ttl -> 1
    engine.finish_player_turn()  # turn 2 fires, ttl -> 0, pruned
    hp_after_two = enemy.hp
    engine.finish_player_turn()  # nothing left to fire
    assert enemy.hp == hp_after_two
    assert not engine.state.player.auras


def test_aura_affects_enemies_not_allies() -> None:
    engine, enemy = _engine_with_enemy(dx=1)
    player = engine.state.player
    friend = engine.spawn_actor(
        "loyal hound", "h", player.x - 1, player.y, 10, 2, 0, "ally", None
    )
    engine._apply_effect(
        {"type": "aura", "target": "player", "kind": "damage", "amount": 2,
         "damage_type": "force", "radius": 4, "affects": "enemies", "turns": 1}
    )
    friend_hp0 = friend.hp
    enemy_hp0 = enemy.hp
    engine.finish_player_turn()
    assert enemy.hp == enemy_hp0 - 2  # foe burns
    assert friend.hp == friend_hp0    # ally untouched


# --- aura borne by a conjured creature --------------------------------------------------


def test_conjured_creature_carries_status_aura() -> None:
    engine, enemy = _engine_with_enemy(dx=1)
    player = engine.state.player
    engine._apply_effect(
        {
            "type": "conjure_creature",
            "template": "small_beast",
            "faction": "ally",
            "placement": "near_player",
            "aura": {
                "kind": "status",
                "status": "slowed",
                "duration": 2,
                "radius": 5,
                "affects": "enemies",
                "label": "frost haze",
            },
        }
    )
    summoned = [e for e in engine.state.entities.values() if e.faction == "ally" and e.auras]
    assert summoned, "the conjured creature should carry the nested aura"
    engine.finish_player_turn()
    assert enemy.statuses.get("slowed")  # the haze slowed the foe


# --- tile-anchored aura -----------------------------------------------------------------


def test_tile_aura_damages_whoever_stands_on_it() -> None:
    engine, enemy = _engine_with_enemy(dx=1)
    engine._apply_effect(
        {
            "type": "aura",
            "target": "tile",
            "x": enemy.x,
            "y": enemy.y,
            "kind": "damage",
            "amount": 2,
            "damage_type": "acid",
            "radius": 0,
            "affects": "all",
            "turns": 3,
        }
    )
    assert engine.state.tile_auras, "aura should be anchored to the ground"
    hp0 = enemy.hp
    engine.finish_player_turn()
    assert enemy.hp == hp0 - 2


# --- validation: every aura must carry a real mechanic ----------------------------------


def test_aura_without_a_mechanic_is_dropped() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    # No damage fields and no usable status -> nothing to anchor.
    normalized = engine._normalize_auras({"radius": 3, "label": "pretty glow"})
    assert normalized == []
    # And the standalone effect reports the fizzle rather than anchoring an empty aura.
    messages = engine._apply_effect({"type": "aura", "target": "player", "label": "pretty glow"})
    assert messages and "anchor" in messages[0].lower() or "gutter" in messages[0].lower()
    assert not engine.state.player.auras


def test_aura_with_unusable_status_is_dropped() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    normalized = engine._normalize_auras(
        {"kind": "status", "status": "definitely_not_a_real_status"}
    )
    assert normalized == []
