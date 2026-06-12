"""Promise Ledger M5: optional background flesh for bound promises.

Flesh decorates realization (keeper, arrival line, prop flavor) but is never
load-bearing — the deterministic skeleton stands complete without it — and it is
recorded at its apply point so replays reproduce it with zero model calls."""
from __future__ import annotations

import concurrent.futures
import json
import threading
from pathlib import Path
from typing import Any

from wildmagic.actions import GameSession
from wildmagic.promises import WorldPromise, normalize_flesh
from wildmagic.replay import run_replay, save_replay


class CountingFleshProvider:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def draft(self, context: dict[str, Any]) -> str:
        self.calls += 1
        return json.dumps({"site_name": "The Counted Place"})


class GatedChapelLoreProvider:
    name = "gated"

    def __init__(self) -> None:
        self.gate = threading.Event()

    def extract(self, context: dict[str, Any]) -> str:
        self.gate.wait(timeout=10)
        return json.dumps(
            {
                "claims": [
                    {
                        "kind": "rumor",
                        "subject": "hill chapel",
                        "text": "A drover swears there is a chapel north of here.",
                        "status": "rumored",
                        "confidence": 0.7,
                        "salience": 4,
                        "tags": ["chapel"],
                        "where": "north",
                        "what": "chapel",
                    }
                ]
            }
        )


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


def test_normalize_flesh_whitelists_and_clamps() -> None:
    assert normalize_flesh(None) is None
    assert normalize_flesh({"unknown_key": "x", "site_name": "  "}) is None
    flesh = normalize_flesh({"site_name": "The Long " + "Name " * 40, "keeper_name": "Warden Ash", "effects": [{"type": "damage"}]})
    assert flesh is not None
    assert set(flesh) == {"site_name", "keeper_name"}
    assert len(flesh["site_name"]) <= 60


def test_replay_flesh_injects_without_provider_call() -> None:
    flesh_provider = CountingFleshProvider()
    session = GameSession(
        seed=7,
        scenario="town",
        provider_name="mock",
        flesh_provider=flesh_provider,
        replay_mode=True,
    )
    promise = WorldPromise(
        id="promise_chapel",
        kind="rumor",
        subject="hill chapel",
        text="There is a chapel north of town.",
        tags=["chapel"],
        source="dialogue:Drover",
        source_turn=0,
        origin_zone=(0, 0),
        location="Hollowmere",
        salience=4,
        confidence=0.7,
    )
    flesh_event = {"promise_id": "promise_chapel", "flesh": {"keeper_name": "Warden Ash", "arrival_line": "The bell still rings."}}

    session.execute_command(
        "wait",
        replay_promises={"before": [promise.to_dict()], "after": []},
        replay_flesh={"before": [], "after": [flesh_event]},
    )

    assert flesh_provider.calls == 0
    stored = next(p for p in session.engine.state.promises if p.id == "promise_chapel")
    assert stored.flesh == flesh_event["flesh"]
    assert session.records[-1]["flesh"] == {"before": [], "after": [flesh_event]}


def test_flesh_decorates_realization_and_replays_identically(tmp_path: Path) -> None:
    lore_provider = GatedChapelLoreProvider()
    session = GameSession(
        seed=101,
        scenario="frontier",
        provider_name="mock",
        dialogue_provider_name="mock",
        lore_provider=lore_provider,
    )
    try:
        session._enqueue_lore_extraction(
            {
                "npc": "Drover",
                "turn": session.engine.state.turn,
                "location": "frontier",
                "message": "Any rumors?",
                "reply": "There is a chapel north of here.",
                "zone": {"x": 0, "y": 0},
            },
            {},
        )
        lore_provider.gate.set()
        concurrent.futures.wait([future for future, _ in session._pending_lore], timeout=10)
        session.execute_command("wait")  # drains lore, binds the chapel, enqueues flesh
        concurrent.futures.wait([future for future, _ in session._pending_flesh], timeout=10)
        session.execute_command("wait")  # drains flesh at a recorded apply point

        chapel = next(p for p in session.engine.state.promises if "chapel" in p.tags)
        assert chapel.flesh is not None
        assert chapel.flesh["keeper_name"] == "Warden Hill"
        assert session.records[-1]["flesh"]["before"] or session.records[-1]["flesh"]["after"]

        assert _walk_north_until_zone(session, -1)
        # The chapel bound to the first unexplored northern zone: (0, -1).
        assert chapel.status == "realized"
        messages = session.engine.state.messages
        assert any("The story was true after all" in message for message in messages)
        keepers = [e for e in session.engine.state.entities.values() if e.name == "Warden Hill"]
        assert keepers, "flesh keeper_name should name the realized site's keeper"

        replay_path = tmp_path / "flesh.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    result = run_replay(replay_path)
    assert result.matched, json.dumps(
        {"expected": result.expected_summary, "actual": result.final_summary}, indent=2, sort_keys=True, default=str
    )
