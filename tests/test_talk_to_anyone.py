from __future__ import annotations

from wildmagic.engine import GameEngine


def _engine() -> GameEngine:
    return GameEngine(seed=1, scenario="test_chamber")


def test_soul_id_assigned_at_spawn() -> None:
    engine = _engine()
    npc = engine.spawn_npc(
        "merchant", "m", 1, 1, role="merchant", backstory="sells wares"
    )
    actor = engine.spawn_actor("bandit", "b", 2, 2, 8, 3, 0, "enemy", "melee")
    assert npc.soul_id
    assert actor.soul_id
    assert npc.soul_id != actor.soul_id
    # The persona mirrors the body's soul (specific-person quests bind to it).
    assert engine.state.npc_profiles[npc.id].soul_id == npc.soul_id


def test_kill_deed_carries_victim_soul() -> None:
    engine = _engine()
    player = engine.state.player
    player.attack = 999
    foe = engine.spawn_actor(
        "legionary",
        "l",
        player.x,
        player.y + 1,
        1,
        1,
        0,
        "enemy",
        "melee",
        tags={"empire"},
        role="soldier",
    )
    soul = foe.soul_id
    engine.attack(player, foe)
    kill_deeds = [d for d in engine.state.deed_ledger.deeds if d.subject_refs]
    assert kill_deeds
    assert soul in kill_deeds[0].subject_refs


def test_can_talk_to_an_adjacent_enemy() -> None:
    engine = _engine()
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
    # NPCs and enemies are one talkable kind — you can address the enemy beside you.
    assert engine.find_talk_target() is foe


def test_can_call_out_to_a_visible_target_at_range() -> None:
    engine = _engine()
    player = engine.state.player
    foe = engine.spawn_actor(
        "distant sentry",
        "s",
        player.x + 3,
        player.y,
        8,
        3,
        0,
        "enemy",
        "melee",
        tags={"humanoid"},
    )
    engine.state.visible.add(engine.tile_key(foe.x, foe.y))
    assert engine.find_talk_target() is foe
    # Out of talk range → not reachable.
    assert engine.find_talk_target(max_range=1) is None


def test_selector_targets_by_name() -> None:
    engine = _engine()
    player = engine.state.player
    guard = engine.spawn_actor(
        "stone guard",
        "g",
        player.x + 2,
        player.y,
        8,
        3,
        0,
        "enemy",
        "melee",
        tags={"humanoid"},
    )
    engine.spawn_actor(
        "alley cat",
        "c",
        player.x + 1,
        player.y,
        4,
        1,
        0,
        "neutral",
        "melee",
        tags={"humanoid"},
    )
    for entity in (guard,):
        engine.state.visible.add(engine.tile_key(entity.x, entity.y))
    assert engine.find_talk_target(selector="guard") is guard


def test_beast_cannot_converse() -> None:
    engine = _engine()
    player = engine.state.player
    beast = engine.spawn_actor(
        "dire wolf",
        "w",
        player.x + 1,
        player.y,
        10,
        4,
        0,
        "enemy",
        "melee",
        tags={"beast"},
    )
    assert not engine.can_converse_with(beast)
    assert engine.find_talk_target() is None


def test_ensure_persona_creates_for_enemy_and_context_works() -> None:
    engine = _engine()
    player = engine.state.player
    foe = engine.spawn_actor(
        "imperial sergeant",
        "i",
        player.x + 1,
        player.y,
        12,
        4,
        1,
        "enemy",
        "melee",
        tags={"humanoid", "empire"},
        role="soldier",
    )
    assert engine.state.npc_profiles.get(foe.id) is None
    context = engine.dialogue_context_for_llm(foe, "stand down")
    # A persona was created lazily, and the context carries situational awareness.
    assert engine.state.npc_profiles.get(foe.id) is not None
    situation = context["situation"]
    assert situation["player_is_adjacent"] is True
    assert situation["i_am_hostile_to_the_player"] is True
    assert situation["distance_to_player"] >= 1
