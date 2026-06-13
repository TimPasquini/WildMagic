from __future__ import annotations

from wildmagic.engine import GameEngine


def resolution(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "accepted": True,
        "severity": "minor",
        "outcome_text": "The spell answers.",
        "effects": [{"type": "message", "message": "A harmless light flickers."}],
        "costs": [],
        "rejected_reason": None,
    }
    data.update(overrides)
    return data


def test_contract_failure_does_not_consume_a_turn_or_mutate_spell_stats() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    turn_before = engine.state.turn
    spells_cast_before = engine.state.stats.spells_cast

    outcome = engine.apply_wild_magic_resolution(resolution(effects=[]))

    assert outcome.technical_failure is True
    assert outcome.consumed_turn is False
    assert engine.state.turn == turn_before
    assert engine.state.stats.spells_cast == spells_cast_before


def test_intentional_rejection_consumes_a_turn_and_records_failure() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    turn_before = engine.state.turn
    failures_before = engine.state.stats.spells_failed

    outcome = engine.apply_wild_magic_resolution(
        resolution(
            accepted=False,
            effects=[],
            rejected_reason="The spell is too vast to survive.",
        )
    )

    assert outcome.technical_failure is False
    assert outcome.consumed_turn is True
    assert engine.state.turn == turn_before + 1
    assert engine.state.stats.spells_failed == failures_before + 1


def test_successful_resolution_applies_effects_and_costs_before_advancing_turn() -> (
    None
):
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    turn_before = engine.state.turn
    mana_before = player.mana

    outcome = engine.apply_wild_magic_resolution(
        resolution(
            effects=[
                {
                    "type": "add_status",
                    "target": "player",
                    "status": "warded",
                    "duration": 5,
                }
            ],
            costs=[{"type": "mana", "amount": 3}],
        )
    )

    assert outcome.technical_failure is False
    assert outcome.consumed_turn is True
    assert engine.state.turn == turn_before + 1
    assert engine.state.player.mana == mana_before - 3
    assert "warded" in engine.state.player.statuses
    assert engine.state.stats.spells_cast == 1


def test_waiting_recovers_one_mana() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.mana = max(0, player.max_mana - 3)
    engine.state.turn = 1

    assert engine.wait_turn() is True

    assert player.mana == player.max_mana - 2
    assert "You catch your breath and recover 1 mana." in engine.state.messages


def test_waiting_does_not_exceed_maximum_mana() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.mana = player.max_mana

    assert engine.wait_turn() is True

    assert player.mana == player.max_mana
    assert "You catch your breath and recover 1 mana." not in engine.state.messages


def test_mana_cost_shortfall_becomes_health_cost() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.mana = 1
    hp_before = player.hp

    outcome = engine.apply_wild_magic_resolution(
        resolution(costs=[{"type": "mana", "amount": 4}])
    )

    assert outcome.technical_failure is False
    assert outcome.consumed_turn is True
    assert player.mana == 0
    assert player.hp == hp_before - 3
    assert any("mana shortfall costs 3 health" in message for message in engine.state.messages)


def test_zero_mana_wild_spell_costs_health_instead_of_being_free() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.mana = 0
    hp_before = player.hp

    outcome = engine.apply_wild_magic_resolution(
        resolution(costs=[{"type": "mana", "amount": 3}])
    )

    assert outcome.technical_failure is False
    assert outcome.consumed_turn is True
    assert player.mana == 0
    assert player.hp == hp_before - 3
    assert any("Cost unpaid: no mana" in message for message in engine.state.messages)


def test_application_exception_rolls_back_all_partial_state(
    monkeypatch,
) -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    turn_before = engine.state.turn
    inventory_before = dict(engine.state.inventory)
    messages_before = list(engine.state.messages)

    def fail_after_mutation(effect: dict[str, object]) -> list[str]:
        engine.state.inventory["chalk"] = 0
        engine.state.flags["partial_spell"] = True
        raise RuntimeError("effect handler failed")

    monkeypatch.setattr(
        engine,
        "_apply_effect",
        fail_after_mutation,
    )

    outcome = engine.apply_wild_magic_resolution(resolution())

    assert outcome.technical_failure is True
    assert outcome.consumed_turn is False
    assert engine.state.turn == turn_before
    assert engine.state.inventory == inventory_before
    assert "partial_spell" not in engine.state.flags
    assert engine.state.stats.spells_cast == 0
    assert engine.state.messages[:-1] == messages_before
    assert "effect handler failed" in outcome.messages[0]
