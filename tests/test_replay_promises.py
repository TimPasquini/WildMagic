"""Replay contract for the Promise Ledger: promises are injected at the recorded apply
point (the command boundary where the background lore drain landed), so zones generated
between the dialogue and the drain see the same reservations live and on replay."""

from __future__ import annotations

import concurrent.futures
import json
import threading
from pathlib import Path
from typing import Any

from wildmagic.actions import GameSession
from wildmagic.replay import run_replay, save_replay


class GatedChapelLoreProvider:
    """Holds the background extraction open until the test releases the gate,
    simulating a slow CPU model that drains several commands after the dialogue."""

    name = "gated"

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0

    def extract(self, context: dict[str, Any]) -> str:
        self.gate.wait(timeout=10)
        self.calls += 1
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


def _walk_north_until_zone(
    session: GameSession, target_zone_y: int, max_commands: int = 400
) -> bool:
    state = session.engine.state
    commands = 0
    while (
        state.zone_y != target_zone_y
        and commands < max_commands
        and not state.game_over
    ):
        player = state.player
        for move in ("north", "northeast", "northwest", "east", "west"):
            before = (player.x, player.y, state.zone_y)
            session.execute_command(move)
            commands += 1
            player = state.player
            if (player.x, player.y, state.zone_y) != before:
                break
    return state.zone_y == target_zone_y


def test_replay_reproduces_late_lore_drain_across_zone_generation(
    tmp_path: Path,
) -> None:
    lore_provider = GatedChapelLoreProvider()
    session = GameSession(
        seed=101,
        scenario="frontier",
        provider_name="mock",
        dialogue_provider_name="mock",
        lore_provider=lore_provider,
    )
    try:
        start_zone = (session.engine.state.zone_x, session.engine.state.zone_y)
        first_north = (start_zone[0], start_zone[1] - 1)
        second_north = (start_zone[0], start_zone[1] - 2)
        # A dialogue just happened; its extraction is still running on the CPU model.
        session._enqueue_lore_extraction(
            {
                "npc": "Drover",
                "turn": session.engine.state.turn,
                "location": "frontier",
                "message": "Any rumors?",
                "reply": "There is a chapel north of here.",
                "zone": {"x": start_zone[0], "y": start_zone[1]},
            },
            {},
        )

        # Cross into the first northern zone while extraction is still pending: this zone
        # must generate without the chapel, live and on replay alike.
        assert _walk_north_until_zone(session, first_north[1])
        assert not any(
            promise.kind == "rumor" for promise in session.engine.state.promises
        )

        # Release the extraction; the drain lands at the next command boundary.
        lore_provider.gate.set()
        concurrent.futures.wait(
            [future for future, *_ in session._pending_lore], timeout=10
        )
        session.execute_command("wait")
        assert lore_provider.calls == 1

        chapels = [
            promise
            for promise in session.engine.state.promises
            if "chapel" in promise.tags
        ]
        assert len(chapels) == 1
        chapel = chapels[0]
        # The first northern zone was explored before the drain, so the claim relocates.
        assert (
            chapel.bound_space is not None and chapel.bound_space.zone == second_north
        )
        assert session.records[-1]["promises"]["before"], (
            "drain must be recorded at its apply point"
        )

        # Crossing into the bound zone realizes the promised site.
        assert _walk_north_until_zone(session, second_north[1])
        assert chapel.status == "realized"

        replay_path = tmp_path / "late_drain.json"
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


def test_replay_rejects_pre_apply_point_versions(tmp_path: Path) -> None:
    stale = tmp_path / "old.json"
    stale.write_text(json.dumps({"version": 2, "actions": []}), encoding="utf-8")
    try:
        run_replay(stale)
    except ValueError as exc:
        assert "version 3" in str(exc)
    else:
        raise AssertionError("version 2 replay should be rejected")
