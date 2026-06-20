from __future__ import annotations

from wildmagic.engine import BLOOD_FEUD_KILLS, GameEngine


def _engine() -> GameEngine:
    return GameEngine(seed=1, scenario="test_chamber")


def _imperial_soldier(engine: GameEngine, dx: int = 1):
    p = engine.state.player
    return engine.spawn_actor(
        "legionary",
        "i",
        p.x + dx,
        p.y,
        20,
        1,
        0,
        "neutral",
        "legion",
        tags={"soldier"},
        identity=["imperial"],
        role="soldier",
    )


def test_imperial_spawns_neutral_until_provoked_or_exposed() -> None:
    engine = _engine()
    soldier = _imperial_soldier(engine)
    # Politically situated, not hostile-on-sight.
    assert soldier.faction == "neutral"
    assert not engine.is_hostile_to(soldier, engine.state.player)


def test_witnessed_wild_magic_alerts_the_empire() -> None:
    engine = _engine()
    soldier = _imperial_soldier(engine)
    engine._expose_wild_magic_to_witnesses()
    # The file opens (a one-time deed), and the Empire's soldiers now hunt you on sight.
    assert engine.state.flags.get("wild_magic_exposed")
    assert any(
        d.type == "witnessed_forbidden_magic" for d in engine.state.deed_ledger.deeds
    )
    assert engine.is_hostile_to(soldier, engine.state.player)


def test_imperial_clerk_fears_and_reports_but_does_not_fight() -> None:
    engine = _engine()
    p = engine.state.player
    clerk = engine.spawn_npc(
        "tax-clerk",
        "c",
        p.x + 1,
        p.y,
        role="clerk",
        backstory="files reports",
        identity=["imperial"],
    )
    engine._expose_wild_magic_to_witnesses()
    assert "afraid" in clerk.traits  # recoils and carries word
    assert not engine.is_hostile_to(clerk, p)  # a non-combatant never draws


def test_wild_magic_does_not_expose_you_to_non_imperials() -> None:
    engine = _engine()
    p = engine.state.player
    local = engine.spawn_actor(
        "malcontent",
        "r",
        p.x + 1,
        p.y,
        10,
        2,
        0,
        "neutral",
        "simple",
        tags={"human"},
        identity=["rebel"],
        role="partisan",
    )
    engine._expose_wild_magic_to_witnesses()
    # No imperial saw you, so you stay unexposed; a non-imperial doesn't care that you cast.
    assert not engine.state.flags.get("wild_magic_exposed")
    assert not engine.is_hostile_to(local, p)


def test_attacking_a_neutral_combatant_provokes_it() -> None:
    engine = _engine()
    p = engine.state.player
    p.attack = 5
    soldier = _imperial_soldier(engine)
    assert not engine.is_hostile_to(soldier, p)
    engine.attack(p, soldier)  # high hp: survives and turns on you
    assert soldier.faction == "enemy"
    assert engine.is_hostile_to(soldier, p)


def test_attacking_a_townsperson_does_not_make_them_fight() -> None:
    engine = _engine()
    p = engine.state.player
    p.attack = 1
    weaver = engine.spawn_npc(
        "weaver", "p", p.x + 1, p.y, role="townsfolk", backstory="weaves cloth"
    )
    weaver.hp = 20
    engine.attack(p, weaver)
    assert weaver.faction == "neutral"  # a non-combatant flees, never aggroes
    assert not engine.is_hostile_to(weaver, p)


def test_blood_feud_makes_a_faction_hostile_on_sight() -> None:
    engine = _engine()
    p = engine.state.player
    p.attack = 999
    for _ in range(BLOOD_FEUD_KILLS):
        foe = engine.spawn_actor(
            "legionary",
            "l",
            p.x,
            p.y + 1,
            1,
            1,
            0,
            "enemy",
            "melee",
            tags={"empire"},
            role="soldier",
        )
        engine.attack(p, foe)
    assert "empire" in engine.feuding_factions()
    # A fresh, neutral imperial is now hostile on sight — the blood feud, not exposure.
    fresh = _imperial_soldier(engine, dx=2)
    assert engine.is_hostile_to(fresh, p)
