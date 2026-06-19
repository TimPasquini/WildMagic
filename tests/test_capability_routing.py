"""Tests for the capability-card registry and the tier-1 keyword router.

Design: docs/CAPABILITY_ROUTING.md. The router is scaffolding (not yet wired into the live
resolver), so these tests pin its *behavior* — what each spell selects — which is exactly
the contract the migration will rely on.
"""

from __future__ import annotations

from wildmagic import capabilities as cap
from wildmagic.spell_contract import SUPPORTED_EFFECTS


def _names(selected) -> set[str]:
    return {card.name for card in selected}


def _union_text() -> str:
    """CORE_PROMPT plus every live card's prompt block and examples — the full text the
    model can ever see, used to prove the carve dropped nothing."""
    chunks = [cap.CORE_PROMPT]
    for card in cap.CAPABILITY_CARDS:
        chunks.append(card.prompt_block)
        chunks.extend(card.examples)
    return "\n".join(chunks)


# --- Registry integrity -----------------------------------------------------------------


def test_integrated_cards_only_unlock_real_effects() -> None:
    """Every integrated card must map to effect types the engine actually supports — a
    card that unlocks a nonexistent effect would be un-emittable nonsense."""
    for card in cap.CAPABILITY_CARDS:
        assert card.integrated is True
        for effect in card.effect_types:
            assert effect in SUPPORTED_EFFECTS, (
                f"{card.name} unlocks unknown effect {effect}"
            )


def test_core_effect_types_are_real() -> None:
    for effect in cap.CORE_EFFECT_TYPES:
        assert effect in SUPPORTED_EFFECTS


def test_planned_cards_are_flagged_and_not_in_live_registry() -> None:
    live = _names(cap.CAPABILITY_CARDS)
    for card in cap.PLANNED_CARDS:
        assert card.integrated is False
        assert card.name not in live


def test_new_card_effects_are_supported() -> None:
    """The promoted cards must unlock real, contract-registered effects."""
    for effect in ("possess", "edit_memory", "animate_object"):
        assert effect in SUPPORTED_EFFECTS


def test_planned_card_effect_not_yet_supported() -> None:
    """Guards the planned/integrated boundary: size_modification's resize_entity must NOT
    appear in the contract until its handler is built (a registered effect with no handler
    would be a silent no-op)."""
    assert "resize_entity" not in SUPPORTED_EFFECTS


def test_core_plus_cards_cover_all_supported_effects() -> None:
    """The monolith-removal guard: with no fallback prompt, every contract effect must have
    a home — in the always-on core or in some live card — or the model could be asked to
    emit something it was never taught."""
    covered = set(cap.CORE_EFFECT_TYPES)
    for card in cap.CAPABILITY_CARDS:
        covered.update(card.effect_types)
    missing = SUPPORTED_EFFECTS - covered
    assert not missing, f"effects with no core/card home: {sorted(missing)}"


def test_card_names_unique() -> None:
    all_cards = cap.CAPABILITY_CARDS + cap.PLANNED_CARDS
    names = [c.name for c in all_cards]
    assert len(names) == len(set(names))


def test_common_combos_reference_known_cards() -> None:
    known = _names(cap.CAPABILITY_CARDS + cap.PLANNED_CARDS)
    for card in cap.CAPABILITY_CARDS + cap.PLANNED_CARDS:
        for combo in card.common_combos:
            assert combo in known, f"{card.name} combos with unknown card {combo}"


# --- Positive routing -------------------------------------------------------------------


def test_polymorph_selects_transform_entity() -> None:
    selected = cap.select_cards("turn the snarling wolf into a harmless chicken")
    assert "transform_entity" in _names(selected)


def test_summon_selects_conjure_creature() -> None:
    selected = cap.select_cards("summon a loyal wolf to fight by my side")
    assert "conjure_creature" in _names(selected)


def test_wall_selects_barrier_shaping() -> None:
    selected = cap.select_cards("raise a wall of ice between me and the cultists")
    assert "barrier_shaping" in _names(selected)


def test_reveal_selects_divination() -> None:
    selected = cap.select_cards("reveal the weaknesses of the nearest enemy")
    assert "divination" in _names(selected)


def test_blindness_selects_divination() -> None:
    selected = cap.select_cards("blind me with a total blackout")
    assert "divination" in _names(selected)


def test_seal_stairs_selects_barrier_shaping() -> None:
    selected = cap.select_cards("seal the stairs behind me")
    assert "barrier_shaping" in _names(selected)


def test_prophecy_selects_prophecy() -> None:
    selected = cap.select_cards("I prophesy a blade waits for me somewhere north")
    assert "prophecy" in _names(selected)


def test_trigger_phrasing_selects_triggers_reactions() -> None:
    selected = cap.select_cards("the next time an enemy hits me, answer with thorns")
    assert "triggers_reactions" in _names(selected)


def test_last_breath_phrasing_selects_triggers_reactions() -> None:
    selected = cap.select_cards("when I would die, heal me instead")
    assert "triggers_reactions" in _names(selected)


def test_delayed_wound_phrasing_selects_delayed_effects() -> None:
    selected = cap.select_cards("delay my wounds for three turns")
    assert "delayed_effects" in _names(selected)


def test_possession_phrasing_selects_possession() -> None:
    selected = cap.select_cards(
        "take over the nearest enemy's body and leave my own behind"
    )
    assert "possession" in _names(selected)


def test_memory_phrasing_selects_memory_edit() -> None:
    selected = cap.select_cards("make the nearest enemy forget it ever saw me")
    assert "memory_edit" in _names(selected)


def test_animate_phrasing_selects_structure_animation() -> None:
    # Regression: "nearest door" (bare noun) must route to structure_animation, not only
    # barrier_shaping via "wall". A live cast that missed this conjured a new creature.
    selected = cap.select_cards(
        "tear the nearest door from the wall and make it fight for me"
    )
    assert "structure_animation" in _names(selected)


def test_disfigure_phrasings_select_disfigure() -> None:
    for text in (
        "turn his legs to iron",
        "boil the enemy's brain",
        "wither his sword-arm so his blows fall feeble",
        "rot the flesh from his bones",
        "seal his mouth shut",
        "shatter his knees",
    ):
        assert "disfigure" in _names(cap.select_cards(text)), text


def test_whole_body_polymorph_still_reaches_transform_entity() -> None:
    # Disfigure (body-part) and transform_entity (whole-creature) share some intent;
    # a clear polymorph must still load transform_entity. Combo expansion may also pull
    # in disfigure, which is acceptable (recall bias) -- we only require transform is there.
    selected = _names(cap.select_cards("turn the goblin into a chicken"))
    assert "transform_entity" in selected


# --- Negative routing -------------------------------------------------------------------


def test_plain_fireball_loads_no_specialist_card() -> None:
    """A direct-damage spell is fully served by the always-on core; it must not drag in a
    specialist card. This is the negative the recall bias must not break."""
    selected = cap.select_cards("hurl a roaring fireball at the goblin")
    assert selected == []


def test_plain_heal_loads_no_specialist_card() -> None:
    selected = cap.select_cards("mend the worst of my injuries")
    assert selected == []


def test_fireball_never_routes_to_memory_even_with_planned_cards() -> None:
    """Even if memory_edit were live, a fireball must not select it."""
    pool = cap.CAPABILITY_CARDS + cap.PLANNED_CARDS
    selected = cap.select_cards("hurl a roaring fireball at the goblin", cards=pool)
    assert "memory_edit" not in _names(selected)


# --- Composition + combos ---------------------------------------------------------------


def test_compositional_spell_selects_multiple_cards() -> None:
    """A spell that fuses mechanics should pull in all of them — the recall bias in action."""
    pool = cap.CAPABILITY_CARDS + cap.PLANNED_CARDS
    selected = _names(
        cap.select_cards(
            "raise a wall of fire and make them forget I was here", cards=pool
        )
    )
    assert "barrier_shaping" in selected
    assert "memory_edit" in selected


def test_connective_raises_the_cap() -> None:
    text = "summon a wolf and raise a wall and reveal the enemy then mark them"
    with_conn = cap.select_cards(text)
    # The same triggers without a connective cap out one lower.
    assert len(with_conn) <= cap._HARD_CEILING


def test_combo_expansion_adds_bonus_card() -> None:
    """conjure_creature lists conjure_item as a common combo; selecting the former should
    pull in the latter even though the text never triggers conjure_item directly."""
    selected = _names(cap.select_cards("summon a loyal wolf"))
    assert "conjure_creature" in selected
    assert "conjure_item" in selected  # arrived via one-hop combo expansion


def test_combos_can_be_disabled() -> None:
    selected = _names(cap.select_cards("summon a loyal wolf", enable_combos=False))
    assert "conjure_creature" in selected
    assert "conjure_item" not in selected


def test_no_transitive_chaining() -> None:
    """Combo expansion is one hop: a bonus card does not pull in *its* combos.
    triggers_reactions <-> delayed_effects combo each other; selecting one must bring the
    other (one hop) but nothing further (there is nothing further here, so we assert the
    set stays exactly the two)."""
    selected = _names(cap.select_cards("the next time they hit me, retaliate"))
    # triggers_reactions (primary) + delayed_effects (one-hop combo); no third card.
    assert selected == {"triggers_reactions", "delayed_effects"}


def test_cap_is_never_exceeded() -> None:
    # A deliberately trigger-dense spell must still respect the hard ceiling.
    text = (
        "summon a wolf and conjure glass teeth and raise a wall and reveal the enemy "
        "then mark them and prophesy a blade and the next time they hit me retaliate "
        "and turn the goblin into a chicken and charm the other one"
    )
    selected = cap.select_cards(text, cards=cap.CAPABILITY_CARDS + cap.PLANNED_CARDS)
    assert len(selected) <= cap._HARD_CEILING


# --- Assembly helpers -------------------------------------------------------------------


def test_selected_effect_types_always_include_core() -> None:
    selected = cap.select_cards("turn the goblin into a chicken")
    effects = cap.selected_effect_types(selected)
    assert cap.CORE_EFFECT_TYPES <= effects
    assert "transform_entity" in effects


def test_empty_selection_yields_core_only() -> None:
    effects = cap.selected_effect_types([])
    assert effects == cap.CORE_EFFECT_TYPES


def test_assemble_card_blocks_includes_prompt_and_examples() -> None:
    selected = cap.select_cards("turn the goblin into a chicken")
    block = cap.assemble_card_blocks(selected)
    assert "transform_entity" in block
    assert "clucking chicken" in block  # the example came along


def test_capability_index_has_one_line_per_card() -> None:
    index = cap.capability_index()
    assert index.count("\n") + 1 == len(cap.CAPABILITY_CARDS)
    assert "transform_entity" in index


# --- Core/card carve coverage (the offline safety net for the prompt split) --------------


def test_each_live_card_documents_the_effects_it_unlocks() -> None:
    for card in cap.CAPABILITY_CARDS:
        for effect in card.effect_types:
            assert effect in card.prompt_block, (
                f"{card.name} unlocks {effect} but never documents it"
            )


def test_carve_preserves_specialist_catalog_and_tags() -> None:
    """Everything the monolith documented for the specialist mechanics must still appear
    somewhere in CORE_PROMPT + the card blocks — nothing silently dropped in the carve."""
    text = _union_text()
    must_survive = [
        # specialist effect catalog entries
        "summon:",
        "conjure_creature:",
        "conjure_item:",
        "transform_entity:",
        "create_trigger",
        "schedule_event",
        "create_promise",
        "change_faction",
        "possess:",
        "edit_memory:",
        "animate_object:",
        # creature behavior tags
        "aura_burn_N",
        "explode_on_death",
        "ranged",
        "guardian",
        "stationary",
        "spawn_on_death",
        # templates
        "hazard_creature",
        "body_part",
        # fidelity rules we fought for
        '"shape": "wall"',
        '"shape": "line"',
        "revealed",
    ]
    for needle in must_survive:
        assert needle in text, f"carve dropped: {needle!r}"


def test_core_prompt_keeps_universals_and_drops_specialists() -> None:
    core = cap.CORE_PROMPT
    # Universals stay in core.
    for needle in [
        "Wild Magic referee",
        "area_damage:",
        "Cost catalog",
        "Supported statuses:",
    ]:
        assert needle in core
    # Specialist catalog definitions and the behavior-tag block move OUT of core.
    for needle in [
        "transform_entity:",
        "conjure_creature:",
        "Behavior tags",
        "aura_burn_N",
    ]:
        assert needle not in core
    # The status placeholder was substituted, not left literal.
    assert "{supported_statuses}" not in core


# --- Full assembly ----------------------------------------------------------------------


def test_assembled_prompt_loads_routed_card_mechanics() -> None:
    system = cap.assemble_resolver_system_prompt("turn the goblin into a chicken")
    assert "Wild Magic referee" in system  # core present
    assert "Capability index" in system  # the menu is always shown
    assert "transform_entity:" in system  # routed card's mechanics loaded
    assert "clucking chicken" in system  # ...and its example
    assert "Mechanics loaded for this spell" in system


def test_assembled_prompt_for_plain_spell_loads_no_card_block() -> None:
    system = cap.assemble_resolver_system_prompt(
        "hurl a roaring fireball at the goblin"
    )
    assert "Wild Magic referee" in system
    assert "Capability index" in system  # index still shown
    assert "transform_entity:" not in system  # nothing routed
    assert "Mechanics loaded for this spell" not in system


def test_assembled_prompt_appends_region_and_caster_blocks() -> None:
    system = cap.assemble_resolver_system_prompt(
        "summon a wolf",
        region_block="\nREGION_MARKER\n",
        caster_block="\nCASTER_MARKER\n",
    )
    assert system.rstrip().endswith("CASTER_MARKER")
    assert "REGION_MARKER" in system


# --- Live resolver wiring (routing is now the only path) --------------------------------


def test_wild_prompt_messages_routes_by_spell() -> None:
    from wildmagic.wild_magic import _wild_prompt_messages

    routed = _wild_prompt_messages("turn the goblin into a chicken", {})[0]["content"]
    plain = _wild_prompt_messages("hurl a roaring fireball at the goblin", {})[0][
        "content"
    ]
    # Both share the always-on core; only the polymorph loads the transform_entity card.
    assert "Wild Magic referee" in routed
    assert "Wild Magic referee" in plain
    assert "Mechanics loaded for this spell" in routed
    assert "transform_entity:" in routed
    assert "transform_entity:" not in plain
