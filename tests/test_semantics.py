"""Tests for the semantic-effects substrate (wildmagic/semantics.py) and its wiring into the
engine: the shared note/anchor ledger, the add_trait write path, decay, the combat write-back,
and the cross-consumer surfacing that is the whole point -- a fact minted once must reach both
the resolver and the dialogue context through one shared ledger."""

from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.semantics import (
    SemanticLedger,
    SEMANTIC_PREAMBLE,
    entity_anchor,
    faction_anchor,
    place_anchor,
    WORLD_ANCHOR,
)


# --- the ledger in isolation ------------------------------------------------------------


def test_ledger_records_and_retrieves_by_anchor() -> None:
    ledger = SemanticLedger()
    ledger.record("world", "the moon is cracked", turn=0, salience=5)
    notes = ledger.for_anchors(["world"], turn=1)
    assert len(notes) == 1 and notes[0].text == "the moon is cracked"


def test_ledger_dedupes_and_raises_salience() -> None:
    ledger = SemanticLedger()
    ledger.record("entity:a", "hates goblins", turn=0, salience=2)
    ledger.record("entity:a", "hates goblins", turn=5, salience=4)
    notes = ledger.notes["entity:a"]
    assert len(notes) == 1
    assert notes[0].salience == 4  # raised, not duplicated
    assert notes[0].turn_created == 5  # refreshed


def test_ledger_caps_per_anchor_evicting_lowest_salience() -> None:
    ledger = SemanticLedger(per_anchor_cap=3)
    ledger.record("place:0,0", "trivial a", turn=0, salience=1)
    ledger.record("place:0,0", "big thing", turn=0, salience=5)
    ledger.record("place:0,0", "trivial b", turn=0, salience=1)
    ledger.record("place:0,0", "trivial c", turn=0, salience=2)  # pushes out a salience-1
    texts = {n.text for n in ledger.notes["place:0,0"]}
    assert "big thing" in texts
    assert len(ledger.notes["place:0,0"]) == 3


def test_ledger_ranks_by_salience_then_recency() -> None:
    ledger = SemanticLedger()
    ledger.record("world", "minor old", turn=0, salience=2)
    ledger.record("world", "major", turn=1, salience=5)
    ledger.record("world", "minor new", turn=9, salience=2)
    ranked = [n.text for n in ledger.for_anchors(["world"], turn=10, limit=8)]
    assert ranked[0] == "major"
    assert ranked.index("minor new") < ranked.index("minor old")


def test_ledger_decay_drops_expired_notes() -> None:
    ledger = SemanticLedger()
    ledger.record("world", "fleeting", turn=0, salience=3, ttl=2)  # expires at turn 2
    ledger.record("world", "lasting", turn=0, salience=3)
    assert len(ledger.for_anchors(["world"], turn=1)) == 2
    ledger.decay(turn=2)
    remaining = [n.text for n in ledger.for_anchors(["world"], turn=2)]
    assert remaining == ["lasting"]


def test_anchor_helpers() -> None:
    assert entity_anchor("actor_3") == "entity:actor_3"
    assert place_anchor(4, 7) == "place:4,7"
    assert faction_anchor("Empire") == "faction:empire"
    assert WORLD_ANCHOR == "world"


# --- add_trait effect -------------------------------------------------------------------


def test_add_trait_writes_to_entity_and_ledger() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    foe = engine.spawn_actor("goblin", "g", player.x + 1, player.y, 10, 2, 0, "enemy", None)
    engine._apply_effect(
        {"type": "add_trait", "target": foe.id, "text": "branded a coward", "salience": 4}
    )
    assert "branded a coward" in foe.traits  # rides on the entity (free surfacing)
    notes = engine.state.semantics.for_anchors([entity_anchor(foe.id)], turn=engine.state.turn)
    assert any("coward" in n.text for n in notes)  # also in the shared ledger


def test_add_trait_dedupes_on_entity() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    foe = engine.spawn_actor("goblin", "g", player.x + 1, player.y, 10, 2, 0, "enemy", None)
    for _ in range(3):
        engine._apply_effect({"type": "add_trait", "target": foe.id, "text": "smells of brimstone"})
    assert foe.traits.count("smells of brimstone") == 1


def test_entity_traits_surface_in_public_dict() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    foe = engine.spawn_actor("goblin", "g", player.x + 1, player.y, 10, 2, 0, "enemy", None)
    foe.traits.append("righteously feared by rats")
    assert foe.to_public_dict().get("traits") == ["righteously feared by rats"]


# --- combat write-back ------------------------------------------------------------------


def test_slaying_a_foe_leaves_a_place_note() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    foe = engine.spawn_actor("goblin", "g", player.x + 1, player.y, 3, 2, 0, "enemy", None)
    fx, fy = foe.x, foe.y
    engine.damage_entity(foe, 99, "physical", source=player)
    assert foe.hp <= 0
    notes = engine.state.semantics.for_anchors([place_anchor(fx, fy)], turn=engine.state.turn)
    assert any("slain here" in n.text for n in notes)


# --- the headline: one ledger, surfaced to BOTH consumers -------------------------------


def test_scene_notes_reach_both_resolver_and_dialogue() -> None:
    """A fact minted once must be visible to the resolver AND the dialogue model via the
    single shared ledger -- the cross-subsystem surfacing the whole design hinges on."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    # Mint a world-scoped note (in scope for any scene).
    engine.record_note(WORLD_ANCHOR, "the air tastes of iron today", kind="mood", salience=4)

    # Resolver context sees it.
    ctx = engine.context_for_llm("a small spark")
    resolver_texts = {n["text"] for n in ctx["scene_notes"]}
    assert "the air tastes of iron today" in resolver_texts

    # Dialogue context (for some NPC) sees the SAME note from the SAME ledger.
    npc = engine.spawn_npc(
        "Watchman", "W", player.x + 1, player.y, role="guard",
        backstory="keeps the gate", faction="neutral",
    )
    dctx = engine.dialogue_context_for_llm(npc, "what's the news?")
    dialogue_texts = {n["text"] for n in dctx["scene_notes"]}
    assert "the air tastes of iron today" in dialogue_texts


def test_player_trait_reaches_dialogue_partner() -> None:
    """The animated-hat probe, in miniature: a trait on the player surfaces to an NPC's
    dialogue context, so the NPC could react to it."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.traits.append("wears an obviously goblin-hating hat")
    npc = engine.spawn_npc(
        "Goblin Trader", "t", player.x + 1, player.y, role="merchant",
        backstory="sells trinkets", faction="neutral",
    )
    dctx = engine.dialogue_context_for_llm(npc, "got anything to sell?")
    assert "wears an obviously goblin-hating hat" in dctx["player"].get("traits", [])


def test_preamble_present_and_nonempty() -> None:
    assert "weigh" in SEMANTIC_PREAMBLE.lower()
    assert len(SEMANTIC_PREAMBLE) > 100
