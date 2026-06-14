from __future__ import annotations

import pytest

from wildmagic.character import (
    CREATION_POINTS,
    ORIGINS,
    build_profile,
    default_profile,
)
from wildmagic.engine import GameEngine
from wildmagic.fallbacks import bias_resolution_for_profile
from wildmagic.models import CharacterProfile
from wildmagic.prompts import caster_prompt_block
from wildmagic.wild_magic import _wild_prompt_messages


def test_stat_derivation_spreads_hp_and_mana_with_a_neutral_baseline() -> None:
    # A 3/3/3 character reproduces the old fixed 24 HP / 14 MP baseline.
    mid = CharacterProfile(vigor=3, attunement=3)
    assert mid.derive_max_hp() == 24
    assert mid.derive_max_mana() == 14
    # Vigor and Attunement produce a noticeable spread (HP ~16-32, MP ~8-20).
    assert CharacterProfile(vigor=1).derive_max_hp() == 16
    assert CharacterProfile(vigor=5).derive_max_hp() == 32
    assert CharacterProfile(attunement=1).derive_max_mana() == 8
    assert CharacterProfile(attunement=5).derive_max_mana() == 20


def test_build_profile_enforces_point_pool_and_cap() -> None:
    profile = build_profile("desert_nomad", {"composure": 2, "attunement": 1})
    assert profile.composure == ORIGINS["desert_nomad"].to_profile().composure + 2
    with pytest.raises(ValueError):
        build_profile("desert_nomad", {"vigor": CREATION_POINTS + 1})
    with pytest.raises(ValueError):
        # desert_nomad starts vigor 5; +2 would exceed the cap of 6.
        build_profile("desert_nomad", {"vigor": 2})
    with pytest.raises(ValueError):
        build_profile("nope_not_an_origin", {})


def test_created_profile_drives_the_player_entity() -> None:
    profile = build_profile(
        "bone_singer_apprentice",
        {"vigor": 1},
        name="Vashti",
        appearance="ash-grey and humming",
    )
    engine = GameEngine(seed=5, scenario="test_chamber", character=profile)
    player = engine.state.player
    assert player.max_hp == profile.derive_max_hp()
    assert player.max_mana == profile.derive_max_mana()
    assert player.attack == profile.derive_attack()
    assert player.description == "ash-grey and humming"
    # The handoff clones, so the engine's profile is equal but not the same object.
    assert player.profile.origin_id == "bone_singer_apprentice"
    assert player.profile is not profile


def test_caster_prompt_block_varies_with_stats() -> None:
    # A perfectly middling, flavorless caster adds nothing to the prompt.
    assert caster_prompt_block(CharacterProfile().to_public_dict()) == ""
    low_composure = caster_prompt_block(CharacterProfile(composure=1).to_public_dict())
    assert "chaotically" in low_composure.lower()
    high_attunement = caster_prompt_block(
        CharacterProfile(attunement=6).to_public_dict()
    )
    assert "high" in high_attunement.lower()
    signed = caster_prompt_block(
        CharacterProfile(signature="smells of brine").to_public_dict()
    )
    assert "brine" in signed


def test_wild_prompt_strips_caster_profile_into_the_system_prompt() -> None:
    context = {
        "spell": "a bright unraveling",
        "caster_profile": CharacterProfile(composure=1).to_public_dict(),
    }
    messages = _wild_prompt_messages(context)
    system, user = messages[0]["content"], messages[1]["content"]
    assert "chaotically" in system.lower()  # spliced into the system prompt
    assert "caster_profile" not in user  # stripped from the user JSON payload


def test_fallback_bias_scales_amounts_and_adds_low_composure_cost() -> None:
    resolution = {
        "accepted": True,
        "severity": "moderate",
        "outcome_text": "x",
        "effects": [{"type": "damage", "target": "nearest_enemy", "amount": 8}],
        "costs": [],
        "rejected_reason": None,
    }
    biased = bias_resolution_for_profile(
        resolution, CharacterProfile(attunement=6, composure=1).to_public_dict()
    )
    assert biased["effects"][0]["amount"] == 10  # 8 * 1.25
    assert any(cost.get("status") == "strained" for cost in biased["costs"])


def test_npc_sees_player_appearance_and_external_name() -> None:
    profile = build_profile(
        "merfolk_exile", {}, name="Vashti", appearance="iridescent and gilled"
    )
    engine = GameEngine(seed=5, scenario="town", character=profile)
    npc = next(e for e in engine.state.entities.values() if e.kind == "npc")
    context = engine.dialogue_context_for_llm(npc, "hello")
    assert context["player"]["name"] == "Vashti"
    assert context["player"]["appearance"] == "iridescent and gilled"


def test_nameless_default_player_reads_as_a_stranger_to_npcs() -> None:
    engine = GameEngine(seed=5, scenario="town")  # no character -> default profile
    npc = next(e for e in engine.state.entities.values() if e.kind == "npc")
    context = engine.dialogue_context_for_llm(npc, "hello")
    assert context["player"]["name"] == "a wandering stranger"


def test_default_profile_is_a_valid_ready_to_play_character() -> None:
    profile = default_profile()
    assert profile.origin_id in ORIGINS
    assert profile.derive_max_hp() >= 16
