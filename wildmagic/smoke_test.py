from __future__ import annotations

from pathlib import Path
import tempfile

from .actions import GameSession
from .replay import run_replay, save_replay
from .wild_magic import MockWildMagicProvider


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
    assert any(enemy.name == "debt collector" for enemy in rich_session.engine.living_enemies())

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
