from __future__ import annotations

import random

from wildmagic.engine import GameEngine
from wildmagic.populations import roll_concern


def _local_with_concern(engine: GameEngine, dx: int = 1):
    p = engine.state.player
    npc = engine.spawn_npc(
        "weaver", "p", p.x + dx, p.y, role="townsfolk", backstory="weaves cloth"
    )
    engine.state.npc_profiles[npc.id].concern = {
        "type": "slay",
        "subject": "an imperial tormentor",
        "victim_faction": "empire",
        "reward_gold": 20,
    }
    return npc


def test_roll_concern_gives_occupied_locals_a_slay_plight() -> None:
    found = None
    for seed in range(30):
        concern = roll_concern("townsfolk", "conquered", random.Random(seed))
        if concern:
            found = concern
            break
    assert found is not None
    assert found["type"] == "slay"
    assert found["victim_faction"] == "empire"


def test_rival_and_proxy_locals_differ_from_conquered() -> None:
    # A proxy (Threen) local has no slay-the-occupier plight (it is not occupied).
    assert roll_concern("townsfolk", "proxy", random.Random(1)) is None


def test_concern_opens_as_a_quiet_lead_then_accepts_to_active() -> None:
    engine = GameEngine(seed=1, scenario="test_chamber")
    npc = _local_with_concern(engine)
    quest = engine.register_heard_concern(npc.id)
    assert quest is not None and quest.status == "lead"
    accepted = engine.accept_quest(quest.subject)
    assert accepted is quest and quest.status == "active"


def test_decline_marks_a_lead_declined_without_deleting_it() -> None:
    engine = GameEngine(seed=1, scenario="test_chamber")
    npc = _local_with_concern(engine)
    quest = engine.register_heard_concern(npc.id)
    engine.decline_quest(quest.subject)
    assert quest.status == "declined"
    assert quest in engine.state.promises  # the world fact stands


def test_a_lead_still_closes_from_a_deed_without_accepting() -> None:
    engine = GameEngine(seed=1, scenario="test_chamber")
    player = engine.state.player
    player.attack = 999
    npc = _local_with_concern(engine, dx=-1)
    quest = engine.register_heard_concern(npc.id)
    assert quest.status == "lead"
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
    engine.attack(player, foe)
    # Solving the plight without formally accepting still closes it (the giver can react later).
    assert quest.status == "objective_met"
