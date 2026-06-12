"""Promise journal + prophecy spells (create_promise) + debt-as-threat-promise.

The ledger is the single home for everything the world owes: zone generation realizes
spatial promises, event timers settle temporal ones (the debt collector), and the
journal shows the player all of it — with soft spatial hints, never exact zones."""
from __future__ import annotations

import json
from pathlib import Path

from wildmagic.actions import GameSession, describe_journal
from wildmagic.promises import WorldPromise
from wildmagic.replay import run_replay, save_replay


def _walk_north_until_zone(session: GameSession, target_zone_y: int, max_commands: int = 400) -> bool:
    state = session.engine.state
    commands = 0
    while state.zone_y != target_zone_y and commands < max_commands and not state.game_over:
        player = state.player
        for move in ("north", "northeast", "northwest", "east", "west"):
            before = (player.x, player.y, state.zone_y)
            session.execute_command(move)
            commands += 1
            player = state.player
            if (player.x, player.y, state.zone_y) != before:
                break
    return state.zone_y == target_zone_y


def _prophecy_resolution(item: str | None = None) -> dict:
    effect = {
        "type": "create_promise",
        "kind": "prophecy",
        "subject": "a blade that knows my name",
        "text": "Somewhere north of here, a blade waits with my name on it.",
        "what": "cache",
        "where": "north",
        "salience": 4,
    }
    if item:
        effect["item"] = item
    return {
        "accepted": True,
        "severity": "major",
        "outcome_text": "You speak the blade into the world's debt-book.",
        "effects": [effect],
        "costs": [],
        "rejected_reason": None,
    }


def test_journal_lists_heard_rumor_with_hint_and_placeless_threat() -> None:
    session = GameSession(seed=7, scenario="frontier", provider_name="mock")
    session.engine.add_promises([
        WorldPromise(
            id="promise_chapel",
            kind="rumor",
            subject="hill chapel",
            text="There is a chapel north of here.",
            tags=["chapel"],
            source="dialogue:Drover",
            source_turn=1,
            origin_zone=(0, 0),
            salience=4,
            confidence=0.7,
            what="chapel",
        ),
        WorldPromise(
            id="promise_storm",
            kind="threat",
            subject="a gathering storm",
            text="Something is owed and it is coming.",
            tags=["storm"],
            source="dialogue:Old Maren",
            source_turn=2,
            origin_zone=(0, 0),
            salience=3,
            confidence=0.6,
        ),
    ])

    lines = describe_journal(session.engine)
    text = "\n".join(lines)
    assert "[heard] hill chapel" in text
    assert "somewhere north of where you heard it" in text
    assert "[heard] a gathering storm" in text
    # The placeless threat gets no spatial hint line.
    storm_index = next(i for i, line in enumerate(lines) if "gathering storm" in line)
    assert storm_index == len(lines) - 1 or not lines[storm_index + 1].strip().startswith("~")

    result = session.execute_command("journal")
    assert result.success and not result.consumed_turn


def test_journal_empty_message() -> None:
    session = GameSession(seed=7, scenario="frontier", provider_name="mock")
    assert describe_journal(session.engine) == ["Your journal is empty. The world talks - listen to people."]


def test_prophecy_spell_binds_incurs_debt_and_realizes(tmp_path: Path) -> None:
    session = GameSession(seed=101, scenario="frontier", provider_name="mock")
    try:
        mana_before = session.engine.state.player.mana
        session.execute_command("cast somewhere north a blade waits for me", replay_wild_magic={
            "provider": "test",
            "technical_failure": False,
            "error": None,
            "data": _prophecy_resolution(item="named blade"),
        })

        state = session.engine.state
        prophecy = next(p for p in state.promises if p.kind == "prophecy")
        assert prophecy.binding is not None and prophecy.binding.blueprint == "hidden_site"
        assert prophecy.bound_space is not None and prophecy.bound_space.zone == (0, -1)
        # Engine cost floor: 3 + salience 4 + 5 for the item = 12 mana.
        assert state.player.mana == mana_before - 12
        # Prophesied treasure is borrowed: Wild Debt curse, collector timer, ledger entry.
        assert "wild_debt" in state.curses
        assert any(t.get("promise_id") == "promise_wild_debt" for t in state.event_timers)
        assert any(p.id == "promise_wild_debt" for p in state.promises)

        journal = "\n".join(describe_journal(session.engine))
        assert "a blade that knows my name" in journal
        assert "somewhere north of where you heard it" in journal
        assert "wild debt" in journal

        assert _walk_north_until_zone(session, -1)
        assert prophecy.status == "realized"
        blades = [e for e in state.entities.values() if e.kind == "item" and e.name.lower() == "named blade"]
        assert blades, "the prophesied item must exist at the realized site"

        replay_path = tmp_path / "prophecy.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    result = run_replay(replay_path)
    assert result.matched, json.dumps(
        {"expected": result.expected_summary, "actual": result.final_summary}, indent=2, sort_keys=True, default=str
    )


def test_vague_prophecy_stays_flavor_but_is_journaled() -> None:
    session = GameSession(seed=11, scenario="frontier", provider_name="mock")
    session.execute_command("cast I prophesy better days", replay_wild_magic={
        "provider": "test",
        "technical_failure": False,
        "error": None,
        "data": {
            "accepted": True,
            "severity": "minor",
            "outcome_text": "The words leave you.",
            "effects": [{"type": "create_promise", "kind": "prophecy", "subject": "better days", "text": "Better days are coming.", "salience": 2}],
            "costs": [],
            "rejected_reason": None,
        },
    })
    state = session.engine.state
    prophecy = next(p for p in state.promises if p.kind == "prophecy")
    assert prophecy.binding is None
    assert not state.promise_reservations
    assert "better days" in "\n".join(describe_journal(session.engine))
    assert "wild_debt" not in state.curses  # no item, no debt


def test_debt_promise_settles_when_collector_arrives() -> None:
    session = GameSession(seed=13, scenario="frontier", provider_name="mock")
    session.execute_command("cast borrow strength from tomorrow", replay_wild_magic={
        "provider": "test",
        "technical_failure": False,
        "error": None,
        "data": {
            "accepted": True,
            "severity": "moderate",
            "outcome_text": "Strength now. Payment later.",
            "effects": [{"type": "set_flag", "flag": "future_debt"}],
            "costs": [],
            "rejected_reason": None,
        },
    })
    state = session.engine.state
    debt = next(p for p in state.promises if p.id == "promise_wild_debt")
    assert debt.kind == "threat" and debt.status != "fulfilled"
    assert "[heard] wild debt" in "\n".join(describe_journal(session.engine))

    for _ in range(16):
        if not state.event_timers or state.game_over:
            break
        session.execute_command("wait")

    assert not any(t.get("promise_id") == "promise_wild_debt" for t in state.event_timers)
    assert debt.status == "fulfilled"
    assert "[settled] wild debt" in "\n".join(describe_journal(session.engine))
    assert any(e.name == "debt collector" for e in state.entities.values())
