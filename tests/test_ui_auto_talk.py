from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.ui import GameUI


def _ui(engine: GameEngine) -> GameUI:
    ui = GameUI.__new__(GameUI)
    ui.engine = engine
    return ui


def test_auto_talk_selects_adjacent_neutral_npc_when_calm() -> None:
    engine = GameEngine(seed=1, scenario="test_chamber")
    player = engine.state.player
    npc = engine.spawn_npc(
        "quiet villager",
        "v",
        player.x + 1,
        player.y,
        role="townsfolk",
        backstory="keeps to themself",
    )

    assert _ui(engine)._auto_talk_target() is npc


def test_auto_talk_stays_spell_when_enemy_visible() -> None:
    engine = GameEngine(seed=1, scenario="test_chamber")
    player = engine.state.player
    engine.spawn_npc(
        "quiet villager",
        "v",
        player.x + 1,
        player.y,
        role="townsfolk",
        backstory="keeps to themself",
    )
    foe = engine.spawn_actor(
        "legionary",
        "l",
        player.x + 2,
        player.y,
        8,
        3,
        0,
        "enemy",
        "melee",
        tags={"empire"},
        role="soldier",
    )
    engine.state.visible.add(engine.tile_key(foe.x, foe.y))

    assert _ui(engine)._auto_talk_target() is None


def test_auto_talk_does_not_select_talk_to_anyone_targets() -> None:
    engine = GameEngine(seed=1, scenario="test_chamber")
    player = engine.state.player
    foe = engine.spawn_actor(
        "snarling brigand",
        "b",
        player.x + 1,
        player.y,
        8,
        3,
        0,
        "enemy",
        "melee",
        tags={"humanoid"},
    )

    assert engine.find_talk_target() is foe
    assert _ui(engine)._auto_talk_target() is None
