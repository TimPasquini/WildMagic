"""Phase F — bonds, organizations & followers (strategy §5.3).

The richness comes from a few general primitives, not bespoke events. The three layers are
orthogonal (combat faction / org membership / personal bond); bonds drift from the player's
legend bent by each NPC's traits; crossing the follow line is a moment; turning butcher
loses the believers; a parted follower's memory persists and colours later bonds; founding
an organization draws true believers to it.

See docs/EMERGENT_WORLD_IMPLEMENTATION.md §3 (Phase F).
"""

from __future__ import annotations

from wildmagic.bonds import Bond, derive_disposition
from wildmagic.engine import GameEngine


def _npc(engine: GameEngine, name: str, x: int, y: int, traits: list[str]):
    entity = engine.spawn_npc(
        name, "n", x, y, role="stranger", backstory="", traits=traits
    )
    return entity, engine.state.npc_profiles[entity.id]


def _legend(engine: GameEngine, tag: str, weight: float) -> None:
    engine.state.legend_ledger.add_tag(engine.state.player_soul_id, tag, weight)


# --- the three orthogonal layers -------------------------------------------------


def test_bond_is_orthogonal_to_combat_faction_and_affiliation() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    entity, profile = _npc(engine, "reeve", 3, 3, traits=[])
    profile.bond = Bond(loyalty=90.0, affiliations=["player_org_guild"])
    # Combat-neutral, org-affiliated, personally devoted — all at once.
    assert entity.faction == "neutral"
    assert "player_org_guild" in profile.bond.affiliations
    assert profile.bond.is_follower()


# --- legend x traits drives bonds ------------------------------------------------


def test_same_legend_lands_opposite_on_rebel_and_loyalist() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    _legend(engine, "liberator", 5.0)
    _legend(engine, "defiant", 5.0)
    _, rebel = _npc(engine, "freedman", 3, 3, traits=["downtrodden"])
    _, loyalist = _npc(engine, "clerk", 3, 5, traits=["loyalist"])
    engine._simulate_bonds()
    assert rebel.bond.admiration > 0 and rebel.bond.loyalty > 0
    assert loyalist.bond.resentment > 0
    assert loyalist.bond.loyalty <= 0


# --- disposition is derived for every NPC (content workstream B) ------------------


def test_derive_disposition_classifies_by_role_trait_and_tag() -> None:
    assert derive_disposition("temple acolyte", []) == "pious"
    assert derive_disposition("tax collector", []) == "loyalist"
    assert derive_disposition("charm-seller", ["quietly subversive"]) == "rebel"
    assert derive_disposition("rag-and-bone dealer", []) == "downtrodden"
    # The truly uncommitted lean nowhere (they drift at base rate).
    assert derive_disposition("spice merchant", ["shrewd", "warm"]) is None
    # Regression: "innkeeper" must not match the pious keyword set ("keeper").
    assert derive_disposition("innkeeper", []) is None


def test_spawn_npc_auto_derives_disposition_so_strangers_differentiate() -> None:
    """An NPC spawned with no hand-authored lean still reacts in character: a priest fears an
    uncanny reputation more than a beggar does, with zero per-NPC authoring."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    _legend(engine, "uncanny", 5.0)
    priest = engine.spawn_npc("village priest", "n", 3, 3, role="priest", backstory="")
    beggar = engine.spawn_npc("street beggar", "n", 3, 5, role="beggar", backstory="")
    priest_bond = engine.state.npc_profiles[priest.id]
    beggar_bond = engine.state.npc_profiles[beggar.id]
    assert "pious" in priest_bond.traits
    assert "downtrodden" in beggar_bond.traits
    engine._simulate_bonds()
    assert priest_bond.bond.fear > beggar_bond.bond.fear


# --- freeing captives (content workstream A) --------------------------------------


def _bound_captive(engine: GameEngine, role: str, traits: list[str], dx: int = 1):
    player = engine.state.player
    entity = engine.spawn_npc(
        "a captive",
        "p",
        player.x + dx,
        player.y,
        role=role,
        backstory="",
        traits=traits,
        tags={"captive", "bound", "human"},
    )
    return entity, engine.state.npc_profiles[entity.id]


def test_freeing_a_captive_records_a_deed_and_turns_them_to_your_side() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    captive, profile = _bound_captive(engine, "captured poacher", ["downtrodden"])
    assert engine.free_captive()
    assert "bound" not in captive.tags
    assert captive.faction == "ally"
    assert any(d.type == "freed_captive" for d in engine.state.deed_ledger.deeds)


def test_sympathetic_captive_follows_but_a_wary_one_only_thanks_you() -> None:
    """Who joins emerges from disposition, not a flag: a downtrodden captive's gratitude
    tips into following; a wary one becomes a grateful ally but not a sworn follower."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    _, sympathetic = _bound_captive(engine, "captured farmhand", ["downtrodden"], dx=1)
    engine.free_captive()
    engine2 = GameEngine(seed=7, scenario="test_chamber")
    _, wary = _bound_captive(engine2, "captured deserter", ["wary"], dx=1)
    engine2.free_captive()
    assert sympathetic.bond.is_follower()
    assert not wary.bond.is_follower()


def test_freed_captive_with_a_lead_points_to_a_real_item_location() -> None:
    """A grateful captive who knows a cache reveals it as a journal lead with a rough
    direction — organic, optional (only captives seeded with a lead do this)."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    captive, profile = _bound_captive(engine, "imprisoned scribe", ["bookish"])
    profile.lead = {"item": "iron sword", "x": player.x - 3, "y": player.y - 3}
    engine.free_captive()
    leads = [p for p in engine.state.promises if "lead" in p.tags]
    assert leads and leads[0].subject == "iron sword"
    assert leads[0].claimed_space is not None
    assert leads[0].claimed_space.direction == (-1, -1)  # to the northwest
    assert profile.lead is None  # told once


def test_free_with_no_adjacent_captive_does_nothing() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    assert not engine.free_captive()


# --- threshold moments -----------------------------------------------------------


def test_crossing_the_follow_line_is_a_moment() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    _legend(engine, "liberator", 8.0)
    _, profile = _npc(engine, "freed prisoner", 3, 3, traits=["downtrodden"])
    for _ in range(6):
        engine._simulate_bonds()
        if profile.bond.is_follower():
            break
    assert profile.bond.is_follower()
    assert "follower" in profile.traits
    assert any("follow you" in note for note in profile.memory)


def test_turning_butcher_loses_a_believer() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    _, profile = _npc(engine, "old comrade", 3, 3, traits=["downtrodden", "follower"])
    profile.bond.loyalty = 60.0  # already pledged
    _legend(engine, "butcher", 10.0)
    for _ in range(6):
        engine._simulate_bonds()
        if "follower" not in profile.traits:
            break
    assert "follower" not in profile.traits
    assert any("left your side" in note for note in profile.memory)


# --- the durable consequence (a memory colours later bonds) -----------------------


def test_memory_makes_reputation_land_harder() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    _legend(engine, "liberator", 2.0)
    _, with_memory = _npc(engine, "one who knows you", 3, 3, traits=["downtrodden"])
    with_memory.memory.append("I will never forget what you did for me.")
    _, a_stranger = _npc(engine, "a stranger", 3, 5, traits=["downtrodden"])
    engine._simulate_bonds()
    # First-hand memory (personal x1.5) means the same legend moves them further.
    assert with_memory.bond.loyalty > a_stranger.bond.loyalty


# --- organizations draw believers ------------------------------------------------


def test_founding_an_org_draws_a_true_believer() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    org = engine.found_organization("the Ashen Hand")
    assert org.kind == "player_org"
    assert org.player_rank == "founder"
    _legend(engine, "liberator", 10.0)
    _, profile = _npc(engine, "zealot", 3, 3, traits=["downtrodden"])
    for _ in range(6):
        engine._simulate_bonds()
    assert profile.bond.is_follower()
    assert org.id in profile.bond.affiliations


def test_followers_readout_lists_followers_and_orgs() -> None:
    from wildmagic.actions import describe_followers

    engine = GameEngine(seed=7, scenario="test_chamber")
    engine.found_organization("the Ashen Hand")
    _, profile = _npc(engine, "lieutenant", 3, 3, traits=[])
    profile.bond = Bond(loyalty=80.0)
    lines = "\n".join(describe_followers(engine))
    assert "the Ashen Hand" in lines
    assert "lieutenant" in lines
