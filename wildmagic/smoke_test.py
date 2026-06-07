from __future__ import annotations

from pathlib import Path
import tempfile

from .actions import GameSession
from .models import FIRE, RUBBLE, SLICK_ICE, VINES
from .replay import run_replay, save_replay
from .wild_magic import MockWildMagicProvider, parse_resolution_json, validate_resolution


def main() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    assert session.engine.state.visible
    assert session.engine.state.explored
    explored_before = len(session.engine.state.explored)
    assert session.engine.tile_at(session.engine.state.player.x, session.engine.state.player.y) == ">"
    moved = session.execute_command("move east")
    assert len(session.engine.state.explored) >= explored_before
    assert session.engine.tile_at(6, 7) == "/"
    cast = session.execute_command('cast "set the nearest goblin on fire"')
    assert moved.consumed_turn
    assert cast.consumed_turn
    assert not cast.technical_failure
    assert session.engine.state.turn >= 2
    assert session.engine.state.player.hp > 0

    before_turn = session.engine.state.turn
    failed = session.execute_command(
        'cast "this response is malformed"',
        replay_wild_magic={"technical_failure": True, "error": "bad json", "data": None, "provider": "test"},
    )
    assert failed.technical_failure
    assert session.engine.state.turn == before_turn

    rejected = session.execute_command('cast "give me infinite mana and win game"')
    assert rejected.consumed_turn

    stair_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    descended = stair_session.execute_command("descend")
    assert descended.consumed_turn
    assert stair_session.engine.state.depth == 2
    assert stair_session.engine.tile_at(stair_session.engine.state.player.x, stair_session.engine.state.player.y) in {"<", ">"}

    path_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    path_session.execute_command("move east")
    goblin = next(enemy for enemy in path_session.engine.living_enemies() if enemy.name == "test goblin")
    distance_before = path_session.engine.distance(goblin, path_session.engine.state.player)
    path_session.execute_command("wait")
    distance_after = path_session.engine.distance(goblin, path_session.engine.state.player)
    assert distance_after < distance_before

    rich_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    rich_session.execute_command('cast "flood the room"')
    rich_session.execute_command('cast "lightning storm"')
    rich_session.execute_command('cast "ward me from poison"')
    rich_session.execute_command('cast "omen of future debt"')
    rich_session.execute_command("wait")
    rich_session.execute_command("wait")
    rich_session.execute_command("wait")
    assert rich_session.engine.state.flags.get("future_debt") is True
    assert not rich_session.engine.state.event_timers
    assert rich_session.engine.state.player.resistances.get("poison", 0) >= 25
    assert any(
        e.name in ("wild echo", "debt collector", "summoned creature")
        for e in rich_session.engine.state.entities.values()
        if e.kind == "actor" and e.hp > 0
    )

    conjure_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    teeth = conjure_session.execute_command('cast "the goblin teeth turn to glass and fall out"')
    ants = conjure_session.execute_command('cast "an army of ants crawls out of the walls"')
    assert teeth.messages and teeth.messages[0].startswith("*>")
    assert ants.messages and ants.messages[0].startswith("*>")
    assert any(
        item.kind == "item" and item.name == "glass teeth" and item.material == "glass"
        for item in conjure_session.engine.state.entities.values()
    )
    ant_swarms = [
        enemy
        for enemy in conjure_session.engine.living_enemies()
        if enemy.name == "ant swarm" and "ant" in enemy.tags
    ]
    assert len(ant_swarms) >= 1

    behavior_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    behavior_engine = behavior_session.engine
    behavior_player = behavior_engine.state.player
    behavior_goblin = next(enemy for enemy in behavior_engine.living_enemies() if enemy.name == "test goblin")
    ward = behavior_engine.spawn_actor(
        "burning ward",
        "w",
        behavior_goblin.x - 1,
        behavior_goblin.y,
        hp=5,
        attack=0,
        defense=1,
        faction="ally",
        ai=None,
        tags=set(),
    )
    assert "stationary" in ward.tags
    assert "aura_burn_2" in ward.tags
    assert "pacifist" in ward.tags
    behavior_engine._process_entity_behaviors()
    assert "burning" in behavior_goblin.statuses

    enemy_font = behavior_engine.spawn_actor(
        "poison font",
        "p",
        behavior_player.x + 1,
        behavior_player.y,
        hp=5,
        attack=0,
        defense=1,
        faction="enemy",
        ai="simple",
        tags=set(),
    )
    assert "stationary" in enemy_font.tags
    assert "aura_poison_2" in enemy_font.tags
    assert "pacifist" in enemy_font.tags
    behavior_engine._process_entity_behaviors()
    assert "poisoned" in behavior_player.statuses

    line_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    line_engine = line_session.engine
    line_engine.apply_wild_magic_resolution(
        {
            "accepted": True,
            "severity": "minor",
            "outcome_text": "Ice draws a straight answer.",
            "effects": [
                {
                    "type": "create_tiles",
                    "shape": "line",
                    "origin": "player",
                    "target": "nearest_enemy",
                    "tile": "slick_ice",
                    "duration": 5,
                }
            ],
            "costs": [],
            "rejected_reason": None,
        }
    )
    assert line_engine.tile_at(6, 7) == SLICK_ICE

    shape_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    shape_engine = shape_session.engine
    shape_engine.apply_wild_magic_resolution(
        {
            "accepted": True,
            "severity": "minor",
            "outcome_text": "Shapes learn to hold terrain.",
            "effects": [
                {"type": "create_tiles", "shape": "wall", "target": "nearest_enemy", "tile": "rubble", "radius": 2},
                {"type": "create_tiles", "shape": "cone", "origin": "player", "target": "nearest_enemy", "tile": "fire", "radius": 3},
                {"type": "create_tiles", "shape": "scatter", "target": "nearest_enemy", "tile": "vines", "radius": 4, "count": 3},
            ],
            "costs": [],
            "rejected_reason": None,
        }
    )
    flattened_tiles = [tile for row in shape_engine.state.tiles for tile in row]
    assert flattened_tiles.count(RUBBLE) >= 1
    assert flattened_tiles.count(FIRE) >= 1
    assert flattened_tiles.count(VINES) >= 1

    trigger_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    trigger_engine = trigger_session.engine
    trigger_goblin = next(enemy for enemy in trigger_engine.living_enemies() if enemy.name == "test goblin")
    trigger_engine.apply_wild_magic_resolution(
        {
            "accepted": True,
            "severity": "moderate",
            "outcome_text": "The next wound will answer.",
            "effects": [
                {
                    "type": "create_trigger",
                    "name": "thorn-blood answer",
                    "trigger": "on_player_hit",
                    "target": "player",
                    "charges": 1,
                    "duration": 6,
                    "effects": [
                        {"type": "damage", "target": "trigger_source", "amount": 4, "damage_type": "physical"},
                        {"type": "add_status", "target": "trigger_source", "status": "bleeding", "duration": 2},
                    ],
                }
            ],
            "costs": [],
            "rejected_reason": None,
        }
    )
    assert trigger_engine.state.triggers
    hp_before_trigger = trigger_goblin.hp
    trigger_engine.attack(trigger_goblin, trigger_engine.state.player)
    assert trigger_goblin.hp < hp_before_trigger
    assert "bleeding" in trigger_goblin.statuses
    assert not trigger_engine.state.triggers

    trigger_action_resolution = parse_resolution_json(
        '{"accepted": true, "severity": "minor", "outcome_text": "x", '
        '"effect": "create_trigger", "trigger": "on_player_hit", "target": "player", '
        '"action": "retaliate with fire", "cost": {"mana": 1}}'
    )
    assert validate_resolution(trigger_action_resolution) is None
    trigger_action_effect = trigger_action_resolution["effects"][0]
    assert trigger_action_effect["type"] == "create_trigger"
    assert trigger_action_effect["effects"][0]["damage_type"] == "fire"
    empty_trigger_resolution = parse_resolution_json(
        '{"accepted": true, "severity": "minor", "outcome_text": "x", '
        '"effects": [{"type": "create_trigger", "trigger": "on_player_hit", "target": "player"}], '
        '"costs": [{"type": "mana", "amount": 1}]}'
    )
    assert validate_resolution(empty_trigger_resolution) == "create_trigger effects must be a non-empty list"

    terrain_alias_resolution = parse_resolution_json(
        '{"accepted": true, "severity": "minor", "outcome_text": "x", '
        '"effects": [{"type": "create_tiles", "name": "smoke curtain", '
        '"target": "player", "radius": 2, "tile": "?"}], '
        '"costs": [{"type": "mana", "amount": 1}]}'
    )
    assert terrain_alias_resolution["effects"][0]["tile"] == "mist"

    with tempfile.TemporaryDirectory() as temp_dir:
        replay_path = Path(temp_dir) / "smoke_replay.json"
        save_replay(conjure_session, replay_path)
        replay_result = run_replay(replay_path)
        assert replay_result.matched

    print(f"moved={moved.success}")
    print(f"turn={session.engine.state.turn}")
    print(f"player_hp={session.engine.state.player.hp}")
    print(f"player_mana={session.engine.state.player.mana}")
    print("recent_log:")
    for message in session.engine.state.messages[-8:]:
        print(f"- {message}")


if __name__ == "__main__":
    main()
