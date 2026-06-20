from __future__ import annotations

import random

from wildmagic.engine import GameEngine
from wildmagic.populations import denizen_plan


def test_denizen_plan_conquered_mixes_occupiers_and_locals() -> None:
    plan = denizen_plan("conquered", "stalnaz", random.Random(1))
    identities = [tuple(ident) for _denizen, ident in plan]
    assert ("imperial",) in identities  # the garrison
    assert ("stalnaz",) in identities  # the locals


def test_rival_zone_fields_its_own_people_not_imperials() -> None:
    plan = denizen_plan("rival", "brall", random.Random(1))
    identities = {tuple(ident) for _denizen, ident in plan}
    assert ("brall",) in identities
    assert ("imperial",) not in identities  # the free rival is not occupied


def _conquered_placement(engine: GameEngine):
    return next(
        pl
        for pl in sorted(
            engine.state.world_map.placements.values(), key=lambda p: p.realm_id
        )
        if pl.role == "conquered"
    )


def test_conquered_zone_populates_neutral_occupiers_and_locals() -> None:
    engine = GameEngine(seed=1, scenario="frontier")
    placement = _conquered_placement(engine)
    # Inspect only the realm denizens by clearing the start zone's wild inhabitants first.
    player_id = engine.state.player_id
    for eid in [e for e in list(engine.state.entities) if e != player_id]:
        del engine.state.entities[eid]

    engine._populate_realm_denizens(random.Random(7), [], set(), placement)

    people = [
        e
        for e in engine.state.entities.values()
        if e.id != player_id and e.kind in {"npc", "actor"}
    ]
    assert people
    # Every denizen enters as a politically situated person — neutral, not hostile-on-sight.
    assert all(e.faction == "neutral" for e in people)
    # A mix of imperial occupiers and locals, all typed.
    assert any("imperial" in e.identity for e in people)
    assert any(placement.realm_id in e.identity for e in people)
    # Soldiers are combatant actors; townsfolk/merchants are talkable personas.
    assert any(e.kind == "actor" and e.role in {"soldier", "officer"} for e in people)
    assert any(e.kind == "npc" and e.role in {"townsfolk", "merchant"} for e in people)
