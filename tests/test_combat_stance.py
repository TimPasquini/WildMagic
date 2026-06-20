from __future__ import annotations

from wildmagic.engine import GameEngine


def _enemy_pair_engine() -> GameEngine:
    # test_chamber keeps the phase-0 scaffold, where empire and rebellion are at open war.
    return GameEngine(seed=1, scenario="test_chamber")


def test_warring_factions_fight_while_neutral_to_player() -> None:
    engine = _enemy_pair_engine()
    assert engine.state.faction_ledger.are_hostile("empire", "rebellion")
    imperial = engine.spawn_actor(
        "imperial soldier",
        "i",
        1,
        1,
        hp=10,
        attack=3,
        defense=0,
        faction="neutral",
        ai="melee",
        identity=["imperial"],
        role="soldier",
    )
    rebel = engine.spawn_actor(
        "rebel fighter",
        "r",
        2,
        2,
        hp=10,
        attack=3,
        defense=0,
        faction="neutral",
        ai="melee",
        identity=["rebel"],
        role="fighter",
    )
    # Two characters both neutral to the player are hostile to *each other* (their factions
    # are at war) — the case a stored ally/enemy/neutral flag cannot express.
    assert engine.is_hostile_to(imperial, rebel)
    assert engine.is_hostile_to(rebel, imperial)
    player = engine.state.player
    assert not engine.is_hostile_to(imperial, player)
    assert not engine.is_hostile_to(rebel, player)


def test_same_faction_members_not_at_war() -> None:
    engine = _enemy_pair_engine()
    a = engine.spawn_actor(
        "imperial a",
        "i",
        1,
        1,
        hp=10,
        attack=3,
        defense=0,
        faction="neutral",
        ai="melee",
        identity=["imperial"],
        role="soldier",
    )
    b = engine.spawn_actor(
        "imperial b",
        "j",
        2,
        2,
        hp=10,
        attack=3,
        defense=0,
        faction="neutral",
        ai="melee",
        identity=["imperial"],
        role="soldier",
    )
    assert not engine.is_hostile_to(a, b)


def test_enemy_faction_noncombatant_will_not_fight() -> None:
    engine = _enemy_pair_engine()
    clerk = engine.spawn_actor(
        "imperial clerk",
        "c",
        1,
        1,
        hp=8,
        attack=1,
        defense=0,
        faction="enemy",
        ai="melee",
        identity=["imperial"],
        role="clerk",
    )
    # A member of a faction hostile to the player who is *just a clerk* does not draw.
    assert not engine.is_hostile_to(clerk, engine.state.player)


def test_roleless_enemy_still_fights_the_player() -> None:
    engine = _enemy_pair_engine()
    brute = engine.spawn_actor(
        "snarling brute",
        "b",
        1,
        1,
        hp=12,
        attack=4,
        defense=1,
        faction="enemy",
        ai="melee",
    )
    # No regression: an ordinary enemy with no pacifist role still fights.
    assert engine.is_hostile_to(brute, engine.state.player)


def test_townsperson_does_not_initiate_combat() -> None:
    engine = _enemy_pair_engine()
    weaver = engine.spawn_npc(
        "weaver", "w", 1, 1, role="townsfolk", backstory="weaves cloth"
    )
    imperial = engine.spawn_actor(
        "imperial soldier",
        "i",
        2,
        2,
        hp=10,
        attack=3,
        defense=0,
        faction="enemy",
        ai="melee",
        identity=["imperial"],
        role="soldier",
    )
    # The townsperson flees rather than fights — they never initiate, even against the Empire.
    assert not engine.is_hostile_to(weaver, imperial)
