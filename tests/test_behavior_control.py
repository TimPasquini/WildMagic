from __future__ import annotations

from wildmagic import capabilities as cap
from wildmagic.behaviors import active_behavior
from wildmagic.engine import GameEngine
from wildmagic.spell_contract import SUPPORTED_EFFECTS, validate_resolution


def _engine_with_enemy(hp: int = 10, attack: int = 4):
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "brute", "B", player.x + 1, player.y, hp, attack, 0, "enemy", None
    )
    return engine, player, enemy


def test_set_behavior_dance_moves_without_attacking() -> None:
    engine, player, enemy = _engine_with_enemy(attack=6)
    player_hp = player.hp
    start = (enemy.x, enemy.y)

    engine._apply_effect(
        {
            "type": "set_behavior",
            "target": enemy.id,
            "behavior": "dance",
            "duration": 2,
        }
    )
    engine._enemy_single_action(enemy, player)

    assert player.hp == player_hp
    assert (enemy.x, enemy.y) != start


def test_set_behavior_duel_ignores_adjacent_player() -> None:
    engine, player, enemy = _engine_with_enemy(attack=6)
    rival = engine.spawn_actor(
        "ogre", "O", player.x + 3, player.y, 18, 3, 0, "enemy", None
    )
    player_hp = player.hp

    engine._apply_effect(
        {
            "type": "set_behavior",
            "target": enemy.id,
            "behavior": "duel",
            "behavior_target": rival.id,
            "duration": 3,
        }
    )
    engine._enemy_single_action(enemy, player)

    assert player.hp == player_hp
    assert engine.distance(enemy, rival) < 2


def test_set_behavior_coward_flees_visible_blood() -> None:
    engine, player, enemy = _engine_with_enemy(attack=6)
    player.statuses["bleeding"] = 3
    before_distance = engine.distance(enemy, player)
    player_hp = player.hp

    engine._apply_effect(
        {
            "type": "set_behavior",
            "target": enemy.id,
            "behavior": "coward",
            "duration": 3,
        }
    )
    engine._enemy_single_action(enemy, player)

    assert player.hp == player_hp
    assert engine.distance(enemy, player) > before_distance


def test_set_behavior_lowest_hp_retargets_to_weakest_visible_creature() -> None:
    engine, player, enemy = _engine_with_enemy(hp=20)
    weak_ally = engine.spawn_actor(
        "wounded hound", "h", player.x + 2, player.y, 3, 1, 0, "ally", None
    )
    weak_ally.hp = 2

    engine._apply_effect(
        {
            "type": "set_behavior",
            "target": enemy.id,
            "behavior": "lowest_hp",
            "duration": 3,
        }
    )

    assert engine._select_target(enemy, player) is weak_ally


def test_set_behavior_mimic_copies_focus_last_move() -> None:
    engine, player, enemy = _engine_with_enemy()
    enemy.x = player.x + 2
    enemy.y = player.y + 1
    player.details["last_move_delta"] = [1, 0]

    engine._apply_effect(
        {
            "type": "set_behavior",
            "target": enemy.id,
            "behavior": "mimic",
            "mimic_target": "player",
            "duration": 3,
        }
    )
    start = (enemy.x, enemy.y)
    engine._enemy_single_action(enemy, player)

    assert (enemy.x, enemy.y) == (start[0] + 1, start[1])


def test_behavior_modifiers_expire_after_their_action_window() -> None:
    engine, player, enemy = _engine_with_enemy()
    engine._apply_effect(
        {
            "type": "set_behavior",
            "target": enemy.id,
            "behavior": "freeze_dread",
            "duration": 1,
        }
    )

    assert active_behavior(enemy, "freeze_dread") is not None
    engine._enemy_single_action(enemy, player)
    engine._tick_behavior_modifiers()

    assert active_behavior(enemy, "freeze_dread") is None


def test_set_behavior_contract_and_routing_are_live() -> None:
    assert "set_behavior" in SUPPORTED_EFFECTS
    err = validate_resolution(
        {
            "accepted": True,
            "severity": "moderate",
            "outcome_text": "The brute starts dancing.",
            "effects": [
                {
                    "type": "set_behavior",
                    "target": "nearest_enemy",
                    "behavior": "dance",
                    "duration": 3,
                }
            ],
            "costs": [],
            "rejected_reason": None,
        }
    )
    assert err is None
    assert "behavior_control" in {
        card.name for card in cap.select_cards("make the brute dance instead of attack")
    }
