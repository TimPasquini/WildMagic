from __future__ import annotations

from wildmagic.actions import GameSession
from wildmagic.curses import build_curse, curse_card
from wildmagic.engine import GameEngine


def resolution(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "accepted": True,
        "severity": "minor",
        "outcome_text": "The spell answers.",
        "effects": [{"type": "message", "text": "A harmless light flickers."}],
        "costs": [],
        "rejected_reason": None,
    }
    data.update(overrides)
    return data


def add_curse(engine: GameEngine, curse_id: str, name: str | None = None) -> None:
    payload = {"id": curse_id}
    if name is not None:
        payload["name"] = name
    curse = build_curse(payload, turn=engine.state.turn)
    engine.state.curses[curse.id] = curse


def test_semantic_curse_reaches_resolver_context_without_mechanics() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    curse = build_curse(
        {
            "id": "moth_lantern_curse",
            "name": "Moth-Lantern Curse",
            "description": "Every spell wants to circle the nearest light.",
        },
        turn=engine.state.turn,
    )
    engine.state.curses[curse.id] = curse

    context = engine.context_for_llm("summon a brass moth")
    active = {card["id"]: card for card in context["active_curses"]}

    assert active["moth_lantern_curse"]["mode"] == "semantic"
    assert active["moth_lantern_curse"]["mechanics"] == {}
    assert "nearest light" in active["moth_lantern_curse"]["semantic_prompt"]


def test_close_curse_blocks_far_spell_and_consumes_turn_without_mutation() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "close_curse")
    enemy = engine.nearest_enemy()
    assert (
        enemy is not None
        and max(
            abs(enemy.x - engine.state.player.x), abs(enemy.y - engine.state.player.y)
        )
        > 3
    )
    hp_before = enemy.hp
    turn_before = engine.state.turn

    outcome = engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "damage",
                    "target": "nearest_enemy",
                    "amount": 5,
                    "damage_type": "arcane",
                }
            ]
        )
    )

    assert outcome.consumed_turn is True
    assert outcome.technical_failure is False
    assert engine.state.turn == turn_before + 1
    assert enemy.hp == hp_before
    assert engine.state.stats.spells_failed == 1
    assert "Close Curse" in outcome.messages[0]


def test_far_curse_blocks_self_spell_but_allows_distant_target() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "far_curse")

    blocked = engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "add_status",
                    "target": "player",
                    "status": "warded",
                    "duration": 3,
                }
            ]
        )
    )
    assert blocked.consumed_turn is True
    assert "warded" not in engine.state.player.statuses
    assert "Far Curse" in blocked.messages[0]

    enemy = engine.nearest_enemy()
    assert enemy is not None
    hp_before = enemy.hp
    allowed = engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "damage",
                    "target": enemy.id,
                    "amount": 2,
                    "damage_type": "arcane",
                }
            ]
        )
    )
    assert allowed.technical_failure is False
    assert allowed.consumed_turn is True
    assert enemy.hp < hp_before


def test_narrow_curse_blocks_large_area_radius() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "narrow_curse")

    outcome = engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "area_status",
                    "target": "player",
                    "radius": 2,
                    "status": "slowed",
                    "duration": 2,
                    "affects": "enemies",
                }
            ]
        )
    )

    assert outcome.consumed_turn is True
    assert "Narrow Curse" in outcome.messages[0]
    assert not any("slowed" in enemy.statuses for enemy in engine.living_enemies())


def test_player_attributed_enemy_kill_grants_placeholder_experience() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    enemy = engine.nearest_enemy()
    assert enemy is not None

    outcome = engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "damage",
                    "target": enemy.id,
                    "amount": 99,
                    "damage_type": "arcane",
                }
            ]
        )
    )

    assert outcome.consumed_turn is True
    assert engine.state.experience == 1
    assert engine.state.stats.experience_gained == 1


def test_curse_clears_automatically_after_earning_required_xp() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "close_curse")
    threshold = engine.state.curses["close_curse"].xp_to_clear
    assert threshold > 1

    for _ in range(threshold - 1):
        engine.award_experience(1)
    # Not yet: the meter has filled but not reached the threshold.
    assert "close_curse" in engine.state.curses
    assert engine.state.curses["close_curse"].clear_progress == threshold - 1

    engine.award_experience(1)
    assert "close_curse" not in engine.state.curses
    # XP is a lifetime tally; clearing a curse never spends it.
    assert engine.state.experience == threshold
    assert engine.state.stats.experience_gained == threshold


def test_curse_clearing_does_not_spend_experience_on_bulk_award() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "narrow_curse")

    engine.award_experience(10)

    assert "narrow_curse" not in engine.state.curses
    assert engine.state.experience == 10


def test_multi_stack_curse_lifts_one_stack_per_threshold() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "narrow_curse")
    engine.state.curses["narrow_curse"].stacks = 2
    threshold = engine.state.curses["narrow_curse"].xp_to_clear

    engine.award_experience(threshold)
    curse = engine.state.curses.get("narrow_curse")
    assert curse is not None and curse.stacks == 1
    assert curse.clear_progress == 0

    engine.award_experience(threshold)
    assert "narrow_curse" not in engine.state.curses
    assert engine.state.experience == threshold * 2


def test_kill_xp_advances_curse_clearing() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    add_curse(engine, "narrow_curse")  # xp_to_clear == 3
    enemy = engine.nearest_enemy()
    assert enemy is not None

    # One player-attributed kill grants 1 XP, which weathers the curse by one tick.
    engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "damage",
                    "target": enemy.id,
                    "amount": 99,
                    "damage_type": "arcane",
                }
            ]
        )
    )
    assert engine.state.experience == 1
    assert engine.state.curses["narrow_curse"].clear_progress == 1


def test_clear_curse_command_is_informational_only() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    curse = build_curse({"id": "close_curse"}, turn=session.engine.state.turn)
    session.engine.state.curses[curse.id] = curse
    session.engine.state.experience = 10

    result = session.execute_command("clear curse close", record=False)

    assert result.success is True
    assert result.consumed_turn is False
    # The command no longer spends XP or removes the curse; it only explains.
    assert session.engine.state.experience == 10
    assert "close_curse" in session.engine.state.curses
    assert any("lift on their own" in message for message in result.messages)


def test_curse_card_labels_known_mechanical_curses() -> None:
    curse = build_curse({"id": "anchored_curse"}, turn=0)
    card = curse_card(curse)

    assert card["mode"] == "mixed"
    assert "teleport" in "; ".join(card["mechanical_limits"])
