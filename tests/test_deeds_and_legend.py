"""Phase A.1 — deterministic deed breadth, legend, and causal compression.

Emission sites describe *what happened*; the declarative DEED_RULES table interprets it
into multi-axis standing + legend tags (strategy §5.1: "one deed produces different
consequences along different axes"); the daily tick applies it and the legend becomes the
connective tissue dialogue and readouts read. Story beats compress clusters of deeds.

See docs/EMERGENT_WORLD_IMPLEMENTATION.md §3 (Phase A.1).
"""

from __future__ import annotations

from wildmagic.deeds import DEED_RULES, Deed, DeedLedger, interpret_deed_rules
from wildmagic.engine import GameEngine
from wildmagic.factions import resolve_faction, seed_phase0_factions
from wildmagic.legend import LegendLedger


def _spawn_imperial(engine: GameEngine, x: int, y: int):
    return engine.spawn_actor(
        "legion spearman", "l", x, y, 1, 1, 0, "enemy", "melee", tags={"empire"}
    )


# --- one deed, many axes ---------------------------------------------------------


def test_imperial_kill_splits_across_axes_and_earns_legend() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    engine.attack(player, foe)
    engine.run_world_tick()

    empire = engine.state.faction_ledger.get("empire")
    rebellion = engine.state.faction_ledger.get("rebellion")
    # Distinct consequences on distinct axes — not one flat score.
    assert empire.standing_of("imperial_threat") > 0
    assert empire.standing_of("fear") > 0
    assert rebellion.standing_of("gratitude") > 0
    assert rebellion.standing_of("notoriety") > 0
    assert rebellion.standing_of("legitimacy") > 0
    # And a legend tag.
    assert "defiant" in engine.legend_words(engine.state.player_soul_id)


def test_spark_kill_is_attributed_and_records_a_deed() -> None:
    """A ranged standard spell (spark) must attribute the kill to the player's soul, so
    it produces a deed exactly like a melee kill. Regression: spark/frost/item damage
    once omitted ``source=player``, so the most common attack silently bypassed the whole
    emergent loop (no deed, no standing, no legend)."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.mana = 10
    foe = _spawn_imperial(engine, player.x + 1, player.y)
    foe.hp = 1
    assert engine.cast_standard_bolt()  # spark the nearest foe
    assert not foe.alive
    engine.run_world_tick()

    empire = engine.state.faction_ledger.get("empire")
    assert empire.standing_of("imperial_threat") > 0
    assert "defiant" in engine.legend_words(engine.state.player_soul_id)


def test_killing_imperial_near_civilian_also_defends_the_folk() -> None:
    """One act, two deeds: cutting down an imperial standing over a townsperson reads as
    both a strike on the Empire and a defense of the folk — so the protector legend (and the
    'people's champion' arc) can actually arise in play, not just on paper."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    engine.spawn_npc(
        "weaver", "n", player.x + 1, player.y + 1, role="weaver", backstory=""
    )
    foe = _spawn_imperial(engine, player.x + 1, player.y)
    engine.attack(player, foe)
    types = [d.type for d in engine.state.deed_ledger.deeds]
    assert "killed_imperials" in types
    assert "defended_townsfolk" in types
    engine.run_world_tick()
    assert "protector" in engine.legend_words(engine.state.player_soul_id)


def test_killing_imperial_with_no_civilian_near_does_not_defend() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x + 1, player.y)
    engine.attack(player, foe)
    types = [d.type for d in engine.state.deed_ledger.deeds]
    assert types == ["killed_imperials"]


def test_killing_civilians_costs_legitimacy_and_brands_a_butcher() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    victim = engine.spawn_npc(
        "weaver", "n", player.x, player.y + 1, role="weaver", backstory=""
    )
    engine.attack(player, victim)
    assert not victim.alive
    engine.run_world_tick()

    rebellion = engine.state.faction_ledger.get("rebellion")
    assert rebellion.standing_of("gratitude") < 0  # the people disown you
    assert rebellion.standing_of("legitimacy") < 0
    assert "butcher" in engine.legend_words(engine.state.player_soul_id)


def test_killing_a_hostile_npc_is_not_a_civilian_murder() -> None:
    """A foe who was hostile *before* the player struck them is a combatant, not a civilian —
    even when it is a ``kind == "npc"`` entity (the game supports hostile npc-kind actors via
    ``living_enemies``). Regression: ``killed_civilians`` keyed only on ``kind == "npc"``, so
    killing a bandit who attacked you branded you a butcher (and, once quests land, would
    have wrongly satisfied a civilian-protection objective)."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = engine.spawn_npc(
        "marsh bandit", "n", player.x, player.y + 1, role="bandit", backstory=""
    )
    foe.faction = "enemy"  # hostile to the player on its own account, not provoked
    engine.attack(player, foe)
    assert not foe.alive
    types = [d.type for d in engine.state.deed_ledger.deeds]
    assert "killed_civilians" not in types


# --- per-faction kill tally (FACTION_KILL_REPUTATION.md K1/K2) --------------------


def test_resolve_faction_maps_victims_to_factions_and_buckets() -> None:
    ledger = seed_phase0_factions()
    # A faction id tagged directly wins.
    assert resolve_faction({"empire"}, "actor", ledger) == "empire"
    # A tagged *role* resolves to that role's primary faction (resistance -> rebellion).
    assert resolve_faction({"resistance"}, "actor", ledger) == "rebellion"
    # An unaligned person falls to the civilian bucket...
    assert resolve_faction(set(), "npc", ledger) == "civilian"
    assert resolve_faction({"civilian"}, "actor", ledger) == "civilian"
    # ...but an unaligned creature is politics-free and stays tally-exempt.
    assert resolve_faction(set(), "actor", ledger) == ""


def test_imperial_kill_stamps_victim_faction_and_tallies() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    engine.attack(player, foe)
    kill = next(
        d for d in engine.state.deed_ledger.deeds if d.type == "killed_imperials"
    )
    assert kill.victim_faction == "empire"
    assert engine.kills_by_faction() == {"empire": 1}


def test_tally_counts_civilians_and_excludes_hostiles_and_beasts() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    # An innocent townsperson -> civilian bucket.
    weaver = engine.spawn_npc(
        "weaver", "n", player.x, player.y + 1, role="weaver", backstory=""
    )
    engine.attack(player, weaver)
    # A foe hostile before being struck -> no kill deed, so never tallied.
    bandit = engine.spawn_npc(
        "marsh bandit", "n", player.x, player.y - 1, role="bandit", backstory=""
    )
    bandit.faction = "enemy"
    engine.attack(player, bandit)
    # One imperial -> empire bucket.
    foe = _spawn_imperial(engine, player.x + 1, player.y)
    engine.attack(player, foe)

    assert engine.kills_by_faction() == {"civilian": 1, "empire": 1}


def test_kills_by_faction_survives_deed_ledger_round_trip() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    engine.attack(player, foe)
    restored = DeedLedger.from_dict(engine.state.deed_ledger.to_dict())
    assert restored.kills_by_faction() == {"empire": 1}


def test_legend_accumulates_over_repeated_deeds() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    for i in range(3):
        foe = _spawn_imperial(engine, player.x, player.y + 1)
        engine.attack(player, foe)
    engine.run_world_tick()
    tags = engine.state.legend_ledger.tags_for(engine.state.player_soul_id)
    # Three imperial kills at 0.2 magnitude * 1.0 coeff each.
    assert round(tags["defiant"], 4) == 0.6


# --- the rules table itself ------------------------------------------------------


def test_interpret_deed_rules_scales_by_magnitude() -> None:
    deed = Deed(
        id="d",
        turn=0,
        zone=(0, 0),
        type="killed_imperials",
        magnitude=0.5,
        actor="player",
        source="combat",
    )
    interpret_deed_rules(deed)
    assert deed.interpretation_source == "rules"
    # coeff 1.0 on empire imperial_threat * 0.5 magnitude.
    assert deed.standing_deltas["empire"]["imperial_threat"] == 0.5
    assert deed.legend_tags["defiant"] == 0.5


def test_unknown_deed_type_has_no_rule_consequences() -> None:
    # A type the rules don't know is left consequence-free for the LLM interpreter (A.2).
    assert "an_unprecedented_act" not in DEED_RULES
    deed = Deed(
        id="d",
        turn=0,
        zone=(0, 0),
        type="an_unprecedented_act",
        magnitude=0.5,
        actor="player",
        source="spell",
    )
    interpret_deed_rules(deed)
    assert deed.standing_deltas == {}
    assert deed.legend_tags == {}
    assert deed.interpretation_source == "rules"


# --- causal compression ----------------------------------------------------------


def test_compress_creates_a_beat_without_mutating_deeds() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    for _ in range(3):
        foe = _spawn_imperial(engine, player.x, player.y + 1)
        engine.attack(player, foe)
    ledger = engine.state.deed_ledger
    deeds_snapshot = [deed.to_dict() for deed in ledger.deeds]

    engine.run_world_tick()  # applies deeds and runs compress()
    assert len(ledger.beats) == 1
    beat = ledger.beats[0]
    assert len(beat.source_deeds) == 3
    assert "empire" in beat.factions_affected
    # The deed ledger is untouched by compression (additive only).
    assert [deed.to_dict() for deed in ledger.deeds] == [
        {**d, "applied": True} for d in deeds_snapshot
    ]

    # A second compress with no new deeds mints nothing.
    assert ledger.compress() == []


def test_two_deeds_below_threshold_make_no_beat() -> None:
    ledger = DeedLedger()
    for i in range(2):
        deed = Deed(
            id=f"d{i}",
            turn=0,
            zone=(0, 0),
            type="killed_imperials",
            magnitude=0.2,
            actor="player",
            source="combat",
        )
        ledger.record(deed)
    assert ledger.compress() == []
    assert ledger.beats == []


# --- legibility: legend reaches dialogue -----------------------------------------


def test_legend_reaches_dialogue_context() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    engine.attack(player, foe)
    engine.run_world_tick()

    npc = engine.spawn_npc(
        "innkeeper", "n", player.x - 1, player.y, role="innkeeper", backstory=""
    )
    context = engine.dialogue_context_for_llm(npc, "Who are you?")
    assert "defiant" in context["player"].get("legend", [])


# --- serialization ---------------------------------------------------------------


def test_legend_serialization_round_trip() -> None:
    legend = LegendLedger()
    legend.add_tag("player", "defiant", 0.6)
    legend.add_tag("player", "uncanny", 0.2)
    assert LegendLedger.from_dict(legend.to_dict()).to_dict() == legend.to_dict()
