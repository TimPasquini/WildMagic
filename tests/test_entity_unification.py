from __future__ import annotations

from wildmagic.engine import GameEngine
from wildmagic.actions import GameSession
from wildmagic.models import CharacterProfile


def _enemy(engine: GameEngine):
    return next(e for e in engine.state.entities.values() if e.faction == "enemy" and e.alive)


def test_inventory_is_per_entity_and_aliases_the_controlled_body() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    player = engine.state.player
    # The global-looking state.inventory is really the controlled entity's inventory.
    assert engine.state.inventory is player.inventory
    engine.state.inventory["chalk"] = engine.state.inventory.get("chalk", 0) + 5
    assert player.inventory["chalk"] >= 5


def test_every_creature_carries_the_universal_profile() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    player = engine.state.player
    assert isinstance(player.profile, CharacterProfile)
    for entity in engine.state.entities.values():
        if entity.kind in {"player", "actor", "npc"}:
            assert isinstance(entity.profile, CharacterProfile), entity.name


def test_caster_profile_in_llm_context_carries_composure_band() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    context = engine.context_for_llm("a curious little spell")
    profile = context["caster_profile"]
    assert profile["composure_band"] in {"low", "steady", "high"}
    assert set(profile) >= {"vigor", "attunement", "composure", "appearance", "signature"}


def test_body_swap_moves_control_and_inherits_the_body() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    old_player = engine.state.player
    old_id = old_player.id
    old_player.inventory["secret token"] = 1  # belongs to the body we're leaving
    enemy = _enemy(engine)
    enemy_id, enemy_name = enemy.id, enemy.name
    enemy_profile = enemy.profile

    messages = engine.swap_control_to(enemy_id)

    assert messages and "soul" in messages[0].lower()
    # Control moved: the player is now the body we inhabited.
    assert engine.state.player_id == enemy_id
    assert engine.state.player is enemy
    assert enemy.kind == "player" and enemy.faction == "player" and enemy.char == "@"
    # Identity follows the body.
    assert engine.state.player.name == enemy_name
    # Stats/abilities inherited (you ARE the body, profile and all).
    assert engine.state.player.profile is enemy_profile
    # Inventory stays with the body: the token left behind is not in the new body.
    assert "secret token" not in engine.state.inventory
    assert engine.state.inventory is enemy.inventory


def test_vacated_body_becomes_an_inert_husk() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    old_id = engine.state.player_id
    enemy = _enemy(engine)

    engine.swap_control_to(enemy.id)

    husk = engine.state.entities[old_id]
    assert husk.kind == "actor"
    assert husk.faction == "neutral"
    assert husk.ai is None
    assert "husk" in husk.tags
    assert husk.statuses.get("unconscious") == "permanent"


def test_swap_into_self_or_nothing_is_rejected() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    assert "already" in engine.swap_control_to(engine.state.player_id)[0].lower()
    assert "no body" in engine.swap_control_to("does_not_exist")[0].lower()


def test_possess_command_drives_the_unified_action_layer() -> None:
    session = GameSession(seed=3, scenario="test_chamber")
    engine = session.engine
    enemy = _enemy(engine)
    # Stand on the enemy's doorstep so it's the nearest body.
    engine.state.player.x, engine.state.player.y = enemy.x + 1, enemy.y
    turn_before = engine.state.turn

    result = session.execute_command("possess")

    assert engine.state.player_id == enemy.id
    assert engine.state.turn == turn_before + 1  # possession consumed a turn
    # The newly controlled body can now use the very same player action layer.
    assert result.action == "possess"


def test_possess_effect_routes_through_the_resolver() -> None:
    engine = GameEngine(seed=3, scenario="test_chamber")
    enemy = _enemy(engine)
    outcome = engine.apply_wild_magic_resolution(
        {
            "accepted": True,
            "severity": "major",
            "outcome_text": "You pour yourself into the nearest shape.",
            "effects": [{"type": "possess", "target": enemy.id}],
            "costs": [],
            "rejected_reason": None,
        }
    )
    assert outcome.consumed_turn is True
    assert engine.state.player_id == enemy.id
