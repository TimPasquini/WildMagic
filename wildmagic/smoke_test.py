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
    
    # 1. Drop a unique item in dungeon floor 1 (depth 2)
    stair_session.engine.spawn_item("blood moss", ",", stair_session.engine.state.player.x + 1, stair_session.engine.state.player.y, "blood moss")
    assert any(item.name == "blood moss" for item in stair_session.engine.state.entities.values() if item.kind == "item")
    
    # 2. Descend to dungeon floor 2 (depth 3)
    stairs_down_pos = None
    for y, row in enumerate(stair_session.engine.state.tiles):
        for x, tile in enumerate(row):
            if tile == ">":
                stairs_down_pos = (x, y)
                break
        if stairs_down_pos:
            break
    assert stairs_down_pos is not None
    stair_session.engine.state.player.x, stair_session.engine.state.player.y = stairs_down_pos
    descended2 = stair_session.execute_command("descend")
    assert descended2.consumed_turn
    assert stair_session.engine.state.depth == 3
    
    # 3. Ascend back to dungeon floor 1 (depth 2)
    ascended = stair_session.execute_command("ascend")
    assert ascended.consumed_turn
    assert stair_session.engine.state.depth == 2
    # Verify the dropped item is still present (dungeon level persistence)
    assert any(item.name == "blood moss" for item in stair_session.engine.state.entities.values() if item.kind == "item")
    
    # 4. Ascend back to the surface (test chamber)
    stairs_up_pos = None
    for y, row in enumerate(stair_session.engine.state.tiles):
        for x, tile in enumerate(row):
            if tile == "<":
                stairs_up_pos = (x, y)
                break
        if stairs_up_pos:
            break
    assert stairs_up_pos is not None
    stair_session.engine.state.player.x, stair_session.engine.state.player.y = stairs_up_pos
    ascended2 = stair_session.execute_command("ascend")
    assert ascended2.consumed_turn
    assert stair_session.engine.state.depth == 1
    # Verify we are back in the original surface zone and the test goblin is still alive (surface zone persistence)
    assert any(enemy.name == "test goblin" for enemy in stair_session.engine.living_enemies())

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

    invalid_apply_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    invalid_apply_engine = invalid_apply_session.engine
    invalid_turn = invalid_apply_engine.state.turn
    invalid_outcome = invalid_apply_engine.apply_wild_magic_resolution(
        {
            "accepted": True,
            "severity": "minor",
            "outcome_text": "This should not stick.",
            "effects": [{"type": "unsupported_magic", "target": "player"}],
            "costs": [],
            "rejected_reason": None,
        }
    )
    assert invalid_outcome.technical_failure
    assert invalid_apply_engine.state.turn == invalid_turn
    assert not invalid_apply_engine.validate_state()

    trade_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    trade_engine = trade_session.engine
    tx, ty = trade_engine.find_open_tile_near(trade_engine.state.player.x, trade_engine.state.player.y)
    trader = trade_engine.spawn_npc(
        "test trader",
        "@",
        tx,
        ty,
        role="peddler",
        backstory="Trades only what is in the pack.",
        wares={"smoke vial": 1},
    )
    trade_engine.apply_dialogue_exchange(
        trader,
        "Can I have a special drink?",
        "I might have something.",
        {
            "trade_proposed": True,
            "initiator": "npc",
            "npc_gives": [{"item": "special drink", "quantity": 1}],
            "npc_wants": [],
            "proposal_text": "Here is a special drink.",
            "rejected_reason": None,
        },
    )
    assert trade_engine.state.pending_trade is None
    assert "special drink" not in trade_engine.state.inventory
    assert trade_engine.state.turn == 1

    with tempfile.TemporaryDirectory() as temp_dir:
        replay_path = Path(temp_dir) / "smoke_replay.json"
        save_replay(conjure_session, replay_path)
        replay_result = run_replay(replay_path)
        assert replay_result.matched

    # Test player damage flag and UI coloring logic
    from .ui import is_player_damage_message
    from .engine import LogMessage
    
    # 1. Test LogMessage attribute check
    msg_danger = LogMessage("You take some damage.", is_danger=True)
    msg_safe = LogMessage("You take the key.", is_danger=False)
    assert is_player_damage_message(msg_danger) is True
    assert is_player_damage_message(msg_safe) is False
    
    # 2. Test refined substring checks (fallback behavior)
    assert is_player_damage_message("You take 5 physical damage.") is True
    assert is_player_damage_message("You take the golden key.") is False
    assert is_player_damage_message("You lose 3 health.") is True
    assert is_player_damage_message("You lose your lockpick.") is False
    assert is_player_damage_message("Cost: 3 health.") is True
    assert is_player_damage_message("Cost: 3 mana.") is False
    assert is_player_damage_message("You suffer 4 fire damage.") is True
    assert is_player_damage_message("You die.") is True
    
    # 3. Test that real combat damage log applies correct flags
    combat_session = GameSession(seed=7, scenario="test_chamber", provider=MockWildMagicProvider())
    p = combat_session.engine.state.player
    g = next(enemy for enemy in combat_session.engine.living_enemies() if enemy.name == "test goblin")
    
    # Player hits goblin - should not be danger
    combat_session.engine.attack(p, g)
    hit_msg = combat_session.engine.state.messages[-1]
    assert "You hit test goblin" in hit_msg
    assert getattr(hit_msg, "is_danger", False) is False
    
    # Goblin hits player - should be danger
    combat_session.engine.attack(g, p)
    hit_msg2 = combat_session.engine.state.messages[-1]
    assert "test goblin hits You" in hit_msg2
    assert getattr(hit_msg2, "is_danger", False) is True

    print(f"moved={moved.success}")
    print(f"turn={session.engine.state.turn}")
    print(f"player_hp={session.engine.state.player.hp}")
    print(f"player_mana={session.engine.state.player.mana}")
    print("recent_log:")
    for message in session.engine.state.messages[-8:]:
        print(f"- {message}")


if __name__ == "__main__":
    main()
