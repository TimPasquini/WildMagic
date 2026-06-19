"""Spell focus + item-lore retention.

Covers the three behaviors that make a marked focus meaningful: item descriptions must
survive pickup (so a found/investigated item keeps its flavor), the focus is a mark on an
already-equipped item (no new slot, legitimate stats), and the marked focus must reach the
wild-magic resolver's system prompt while staying out of the user-message payload.
"""

from __future__ import annotations

import json
from pathlib import Path

from wildmagic.actions import GameSession
from wildmagic.engine import GameEngine
from wildmagic.normalize import normalize_id
from wildmagic.prompts import focus_prompt_block
from wildmagic.replay import run_replay, save_replay
from wildmagic.state_view import resolve_foci, spell_context_view
from wildmagic.wild_magic import _wild_prompt_messages


def _engine() -> GameEngine:
    return GameEngine(seed=7, scenario="test_chamber")


# --- item_lore retention -------------------------------------------------------------------


def test_pickup_retains_item_description_as_item_lore() -> None:
    engine = _engine()
    player = engine.state.player
    item = engine.spawn_item("cracked geode", "o", player.x, player.y, "cracked geode")
    item.description = "A split stone whose hollow heart glitters with violet crystal."
    engine.pick_up_items_at_player()
    lore = engine.state.item_lore.get(normalize_id("cracked geode"))
    assert lore is not None
    assert "violet crystal" in lore["description"]
    assert lore["source"] == "description"


def test_item_lore_keyed_by_inventory_key_not_display_name() -> None:
    # Conjured/template items deliberately set item_type != name; lore must key by the
    # inventory key so an equipped/marked focus can find it.
    engine = _engine()
    player = engine.state.player
    item = engine.spawn_item("brass moth", "m", player.x, player.y, "conjured_moth")
    item.description = "Wings of beaten brass tick faintly as it rests."
    engine.pick_up_items_at_player()
    assert normalize_id("conjured_moth") in engine.state.item_lore
    assert normalize_id("brass moth") not in engine.state.item_lore
    assert engine.state.item_lore[normalize_id("conjured_moth")]["display_name"] == (
        "brass moth"
    )


def test_item_lore_precedence_investigated_outranks_pickup_description() -> None:
    engine = _engine()
    key = normalize_id("whispering orb")
    engine.set_item_lore(
        "whispering orb", "whispering orb", "a plain orb", source="description"
    )
    engine.set_item_lore(
        "whispering orb",
        "whispering orb",
        "a far richer investigated account of the orb that runs on at length",
        source="investigated",
    )
    # A later, weaker pickup description must not clobber the investigated one.
    engine.set_item_lore(
        "whispering orb", "whispering orb", "short", source="description"
    )
    assert engine.state.item_lore[key]["source"] == "investigated"
    assert "richer investigated" in engine.state.item_lore[key]["description"]


def test_investigate_item_writes_item_lore_through_canon_side_effect() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        player = engine.state.player
        engine.spawn_item("singing kettle", "k", player.x, player.y, "singing_kettle")
        result = session.execute_command("investigate singing kettle")
        assert result.success
        lore = engine.state.item_lore.get(normalize_id("singing_kettle"))
        assert lore is not None
        assert lore["source"] == "investigated"
        assert lore["description"]
        # And it survives pickup without being downgraded by the plainer Entity copy.
        session.execute_command("pickup")
        kept = engine.state.item_lore.get(normalize_id("singing_kettle"))
        assert kept["source"] == "investigated"
    finally:
        session.close()


# --- focus mark ----------------------------------------------------------------------------


def test_focus_marks_equipped_slot_and_is_single() -> None:
    engine = _engine()
    player = engine.state.player
    # Starting loadout equips chest + legs.
    assert engine.set_focus("chest") is True
    assert player.focus_slots == ["chest"]
    # A second focus replaces the first (v1 single-focus cap).
    assert engine.set_focus("woolen trousers") is True
    assert player.focus_slots == ["legs"]
    assert engine.clear_focus() is True
    assert player.focus_slots == []


def test_focus_rejects_empty_slot() -> None:
    engine = _engine()
    assert engine.set_focus("head") is False
    assert engine.state.player.focus_slots == []


def test_focus_item_keeps_its_legitimate_slot_stats() -> None:
    # A focus is a normally-equipped item, so it grants exactly its own slot bonus -- no
    # phantom stats and no slot-aware skipping.
    engine = _engine()
    player = engine.state.player
    engine.state.inventory["emberglass wand"] = 1
    engine.equip_item("emberglass wand")
    base = player.attack
    engine.set_focus("emberglass wand")
    assert engine.effective_attack(player) == base + 2


def test_focus_follows_body_on_focus_slots_field() -> None:
    # The mark lives on the entity, so it travels with the body (like equipment/inventory).
    engine = _engine()
    engine.set_focus("chest")
    assert "chest" in engine.state.player.focus_slots


# --- resolver wiring -----------------------------------------------------------------------


def test_curated_focus_resolves_with_description_themes_and_power() -> None:
    engine = _engine()
    engine.state.inventory["emberglass wand"] = 1
    engine.equip_item("emberglass wand")
    engine.set_focus("emberglass wand")
    foci = resolve_foci(engine)
    assert len(foci) == 1
    focus = foci[0]
    assert focus["name"] == "emberglass wand"
    assert focus["description"]
    assert focus["themes"]
    assert focus["power"] == 4


def test_marked_focus_reaches_system_prompt_and_is_stripped_from_payload() -> None:
    engine = _engine()
    engine.set_focus("chest")
    spell = "a gout of flame erupts from my hand"
    context = spell_context_view(engine, spell)
    assert context["spell_foci"], "resolver context should carry the marked focus"

    messages = _wild_prompt_messages(spell, context)
    system_prompt, user_payload = messages[0]["content"], messages[1]["content"]
    assert "Spell focus (the implement" in system_prompt
    assert "tattered cloak" in system_prompt
    assert "spell_foci" not in user_payload


def test_focus_block_absent_when_nothing_marked() -> None:
    assert focus_prompt_block([]) == ""
    assert focus_prompt_block(None) == ""
    engine = _engine()
    context = spell_context_view(engine, "a quiet light")
    assert context["spell_foci"] == []
    messages = _wild_prompt_messages("a quiet light", context)
    assert "Spell focus (the implement" not in messages[0]["content"]


# --- replay determinism --------------------------------------------------------------------


def test_replay_reproduces_item_lore_and_focus_mark(tmp_path: Path) -> None:
    # Investigate writes item_lore through the canon side-effect path, which replay re-runs via
    # apply_recorded_canon. With item_lore/equipment/focus_slots in state_summary, a matching
    # round-trip proves the focus state reconstructs deterministically.
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        # Drive only through recorded commands so replay can reproduce every mutation. The
        # mana crystal is a deterministic floor item in this seed.
        session.execute_command("move east")
        session.execute_command("investigate mana crystal")
        session.execute_command("focus chest")
        assert engine.state.item_lore.get(normalize_id("mana crystal"))
        assert engine.state.player.focus_slots == ["chest"]
        replay_path = tmp_path / "focus_replay.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    result = run_replay(replay_path)
    assert result.matched, json.dumps(
        {"expected": result.expected_summary, "actual": result.final_summary},
        indent=2,
        sort_keys=True,
        default=str,
    )
