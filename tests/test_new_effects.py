"""Engine tests for the newly promoted capability effects: edit_memory (memory_edit card)
and animate_object (structure_animation card). Possession already had coverage via its
long-standing `possess` handler."""

from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.models import Entity


# --- edit_memory ------------------------------------------------------------------------


def _engine_with_hostile_npc(memory: list[str]):
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    npc = engine.spawn_npc(
        "Suspicious Guard",
        "g",
        player.x + 1,
        player.y,
        role="guard",
        backstory="hunts the player",
        hp=18,
        attack=4,
        faction="enemy",
    )
    engine.state.npc_profiles[npc.id].memory = list(memory)
    return engine, npc


def test_edit_memory_remove_forgets_caster_and_calms_hostile_npc() -> None:
    engine, npc = _engine_with_hostile_npc(
        ["You walked in carrying chalk.", "The old well is dry."]
    )
    engine._apply_effect(
        {"type": "edit_memory", "target": npc.id, "op": "remove", "subject": "the caster"}
    )
    memory = engine.state.npc_profiles[npc.id].memory
    # The caster-referencing memory is gone; the unrelated one survives.
    assert not any("walked in" in m for m in memory)
    assert any("well is dry" in m for m in memory)
    # Forgetting the caster ends the hunt.
    assert npc.faction == "neutral"
    assert npc.ai is None


def test_edit_memory_add_plants_a_false_memory() -> None:
    engine, npc = _engine_with_hostile_npc(["The market opens at dawn."])
    engine._apply_effect(
        {
            "type": "edit_memory",
            "target": npc.id,
            "op": "add",
            "text": "You are an old friend I owe a favor.",
        }
    )
    memory = engine.state.npc_profiles[npc.id].memory
    assert any("old friend" in m for m in memory)
    assert any("market opens" in m for m in memory)  # existing memory untouched


def test_edit_memory_on_non_npc_is_harmless() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    # nearest_enemy with no NPCs around resolves to nothing editable -> a graceful message.
    messages = engine._apply_effect(
        {"type": "edit_memory", "target": "nearest_enemy", "op": "remove", "subject": "the caster"}
    )
    assert messages and isinstance(messages[0], str)


# --- animate_object ---------------------------------------------------------------------


def _place_prop(engine: GameEngine) -> Entity:
    player = engine.state.player
    prop = Entity(
        id="prop_test",
        name="brass door",
        kind="prop",
        x=player.x + 2,
        y=player.y,
        char="D",
    )
    engine.state.entities[prop.id] = prop
    return prop


def test_animate_object_consumes_prop_and_spawns_actor() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    _place_prop(engine)
    engine._apply_effect(
        {
            "type": "animate_object",
            "target": "prop_test",
            "name": "angry brass door",
            "faction": "ally",
            "hp": 12,
            "attack": 4,
            "defense": 3,
            "char": "D",
        }
    )
    # The prop is gone, an allied actor stands where it was.
    assert "prop_test" not in engine.state.entities
    actors = [e for e in engine.state.entities.values() if e.kind == "actor"]
    animated = [e for e in actors if e.name == "angry brass door"]
    assert len(animated) == 1
    assert animated[0].faction == "ally"
    assert animated[0].hp == 12


def test_animate_object_without_a_prop_still_spawns_something() -> None:
    """No prop present -> the spell falls back to spawning near the player rather than
    fizzling, so the cast is never wasted."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    # Remove any props the scenario may have placed.
    for eid in [e.id for e in list(engine.state.entities.values()) if e.kind == "prop"]:
        engine.state.entities.pop(eid, None)
    before = sum(1 for e in engine.state.entities.values() if e.kind == "actor")
    engine._apply_effect(
        {"type": "animate_object", "name": "walking stool", "faction": "ally", "hp": 6}
    )
    after = sum(1 for e in engine.state.entities.values() if e.kind == "actor")
    assert after == before + 1


# --- disfigure: the 'weakened' status (a maimed limb deals less damage) -----------------


def _avg_attack_damage(weakened: bool, trials: int = 200) -> float:
    """Average damage a high-attack foe lands on the player, with/without weakened.
    Averaged over seeds because the damage roll has a small random component."""
    total = 0
    for i in range(trials):
        engine = GameEngine(seed=1000 + i, scenario="test_chamber")
        player = engine.state.player
        foe = engine.spawn_actor(
            "brute", "B", player.x + 1, player.y, 30, 8, 0, "enemy", None
        )
        if weakened:
            foe.statuses["weakened"] = 5
        hp_before = player.hp
        engine.attack(foe, player)
        total += hp_before - player.hp
    return total / trials


def test_weakened_attacker_deals_two_less_damage() -> None:
    normal = _avg_attack_damage(weakened=False)
    weak = _avg_attack_damage(weakened=True)
    # Mirror of empowered's +2: weakened is -2 outgoing.
    assert round(normal - weak) == 2


def test_weakened_attacker_still_lands_at_least_one() -> None:
    """A feeble blow is still a blow -- the clamp keeps weakened from zeroing damage."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    # A 1-attack foe that is also weakened would otherwise compute negative damage.
    foe = engine.spawn_actor("frail thing", "f", player.x + 1, player.y, 8, 1, 0, "enemy", None)
    foe.statuses["weakened"] = 5
    hp_before = player.hp
    engine.attack(foe, player)
    assert hp_before - player.hp >= 1


def test_disfigure_applies_weakened_via_flavor_alias() -> None:
    """'withered' (a flavor alias) must resolve to the mechanical 'weakened' status."""
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    foe = engine.spawn_actor("goblin", "g", player.x + 1, player.y, 12, 6, 0, "enemy", None)
    engine._apply_effect(
        {
            "type": "add_status",
            "target": foe.id,
            "status": "withered",
            "display_name": "withered arm",
            "duration": 5,
        }
    )
    assert foe.statuses.get("weakened") == 5
    assert foe.status_display.get("weakened") == "withered arm"
