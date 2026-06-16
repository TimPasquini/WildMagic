"""Phase 0 of the emergent world — the micro-loop, end to end.

One deed type (a witnessed imperial kill) flows through the real abstractions:
act (combat) -> record (DeedLedger) -> simulate (the idempotent daily tick) -> show
(standing readout, an NPC's memory, a zone-entry rumor, a wanted poster) -> affect play
(faction standing shifts). Deterministic and replay-safe — no LLM here.

See docs/EMERGENT_WORLD_IMPLEMENTATION.md §3 (Phase 0).
"""

from __future__ import annotations

import json
from pathlib import Path

from wildmagic.actions import GameSession, describe_standing
from wildmagic.deeds import Deed, DeedLedger
from wildmagic.engine import GameEngine
from wildmagic.factions import FactionLedger, seed_phase0_factions
from wildmagic.replay import run_replay, save_replay


def _spawn_imperial(engine: GameEngine, x: int, y: int, hp: int = 1):
    """An Imperial soldier (faction enemy, tagged 'empire') the player can cut down."""
    return engine.spawn_actor(
        "legion spearman", "l", x, y, hp, 1, 0, "enemy", "melee", tags={"empire"}
    )


# --- act -> record ---------------------------------------------------------------


def test_witnessed_imperial_kill_records_a_deed() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99  # guarantee a one-hit kill regardless of derived stats
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    # An NPC a few tiles off witnesses it (within WITNESS_RADIUS) but is far enough from the
    # slain imperial (beyond DEFEND_RADIUS) that this reads as a kill, not a rescue.
    witness = engine.spawn_npc(
        "road drover", "d", player.x, player.y + 5, role="drover", backstory=""
    )

    engine.attack(player, foe)
    assert not foe.alive

    deeds = engine.state.deed_ledger.deeds
    assert len(deeds) == 1
    deed = deeds[0]
    assert deed.type == "killed_imperials"
    assert deed.actor == engine.state.player_soul_id == "player"
    assert deed.source == "combat"
    assert "empire" in deed.target_tags
    assert deed.visibility == "witnessed"
    assert witness.id in deed.witnesses
    # NPC memory line (legibility) lands immediately, before any tick.
    profile = engine.state.npc_profiles[witness.id]
    assert any("cut down" in note for note in profile.memory)


def test_non_imperial_kill_records_no_deed() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    # The pre-placed test goblin carries no 'empire' tag.
    goblin = next(e for e in engine.state.entities.values() if e.name == "test goblin")
    goblin.x, goblin.y = player.x, player.y + 1
    engine.attack(player, goblin)
    assert not goblin.alive
    assert engine.state.deed_ledger.deeds == []


# --- simulate (the daily tick) ---------------------------------------------------


def test_tick_applies_standing_once_and_is_idempotent() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    engine.attack(player, foe)

    empire = engine.state.faction_ledger.get("empire")
    rebellion = engine.state.faction_ledger.get("rebellion")
    assert empire.standing_of("imperial_threat") == 0.0  # not applied until the tick

    assert engine.run_world_tick() is True
    threat_after = empire.standing_of("imperial_threat")
    gratitude_after = rebellion.standing_of("gratitude")
    assert threat_after > 0.0
    assert gratitude_after > 0.0
    assert engine.state.deed_ledger.deeds[0].applied is True
    assert engine.state.simulated_through_turn == engine.state.turn

    # A second tick (a reload, a replay boundary, a repeated day) must not double-apply.
    assert engine.run_world_tick() is False
    assert empire.standing_of("imperial_threat") == threat_after
    assert rebellion.standing_of("gratitude") == gratitude_after


def test_secret_deed_still_shifts_standing() -> None:
    """Even an unwitnessed kill counts — the Empire notices its missing patrols.
    Visibility gates rumor/poster legibility, not the standing math."""
    ledger = seed_phase0_factions()
    state_deeds = DeedLedger()
    state_deeds.record(
        Deed(
            id="d0",
            turn=0,
            zone=(0, 0),
            type="killed_imperials",
            magnitude=0.2,
            actor="player",
            source="combat",
            visibility="secret",
            standing_deltas={"empire": {"imperial_threat": 0.2}},
        )
    )
    # Apply by hand (mirrors run_world_tick's body) to keep this a pure-ledger test.
    for deed in state_deeds.unapplied():
        for fid, axes in deed.standing_deltas.items():
            for axis, delta in axes.items():
                ledger.adjust_standing(fid, axis, delta)
        deed.applied = True
    assert ledger.get("empire").standing_of("imperial_threat") == 0.2


# --- show (legibility: rumor + wanted poster on entry) ---------------------------


def test_public_deed_yields_rumor_and_poster_on_entry() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    foe = _spawn_imperial(engine, player.x, player.y + 1)
    engine.spawn_npc(
        "road drover", "d", player.x, player.y + 2, role="drover", backstory=""
    )
    engine.attack(player, foe)
    engine.run_world_tick()

    messages_before = len(engine.state.messages)
    # Player starts on a down-stair in the test chamber; descending enters a new floor.
    assert engine.descend_stairs() is True

    posters = [e for e in engine.state.entities.values() if "wanted_poster" in e.tags]
    assert len(posters) == 1
    assert "WANTED" in posters[0].description
    new_messages = engine.state.messages[messages_before:]
    assert any("Word on the road" in str(m) for m in new_messages)
    # The deed is only rumored once.
    assert engine.state.deed_ledger.deeds[0].rumored is True


# --- affect play, via commands (the full session path) ---------------------------


def test_command_path_kill_tick_and_standing_readout() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    try:
        engine = session.engine
        player = engine.state.player
        player.attack = 99
        _spawn_imperial(engine, player.x, player.y + 1)  # directly south
        kill = session.execute_command("south")
        assert kill.success
        assert engine.state.deed_ledger.deeds, "the kill should have recorded a deed"

        session.execute_command("tick")
        readout = session.execute_command("standing")
        text = "\n".join(readout.messages)
        assert "the Grand Empire" in text
        assert "imperial_threat" in text
    finally:
        session.close()


# --- soul identity survives a body swap (§1.7) -----------------------------------


def test_soul_id_survives_body_swap() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    player.attack = 99
    # Swap into the test goblin: the controlled body changes, the soul id does not.
    goblin = next(e for e in engine.state.entities.values() if e.name == "test goblin")
    engine.swap_control_to(goblin.id)
    assert engine.state.player_id == goblin.id
    assert engine.state.player_soul_id == "player"

    # A kill by the new body is still attributed to the original soul.
    body = engine.state.player
    foe = _spawn_imperial(engine, body.x, body.y + 1)
    engine.attack(body, foe)
    assert not foe.alive
    deeds = engine.state.deed_ledger.deeds
    assert deeds and deeds[-1].actor == "player"


# --- serialize + replay ----------------------------------------------------------


def test_ledger_serialization_round_trip() -> None:
    deeds = DeedLedger()
    deeds.record(
        Deed(
            id="d0",
            turn=3,
            zone=(1, -2),
            type="killed_imperials",
            magnitude=0.2,
            actor="player",
            source="combat",
            visibility="witnessed",
            witnesses=["npc_1"],
            standing_deltas={"empire": {"imperial_threat": 0.2}},
            applied=True,
        )
    )
    assert DeedLedger.from_dict(deeds.to_dict()).to_dict() == deeds.to_dict()

    factions = seed_phase0_factions()
    factions.adjust_standing("empire", "imperial_threat", 0.6)
    assert FactionLedger.from_dict(factions.to_dict()).to_dict() == factions.to_dict()


def test_replay_round_trip_preserves_ledger_state(tmp_path: Path) -> None:
    """A frontier run that crosses zones, ticks, and reads standing reproduces its
    emergent-world state exactly on replay (the new summarize_state keys are stable)."""
    session = GameSession(
        seed=101,
        scenario="frontier",
        provider_name="mock",
        dialogue_provider_name="mock",
    )
    try:
        for command in ("north", "east", "tick", "standing", "rest", "wait", "south"):
            session.execute_command(command)
        replay_path = tmp_path / "phase0.json"
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


# --- T6: scripted CLI parity -----------------------------------------------------


def test_cli_scripted_run_shows_standing_readout(capsys) -> None:
    from wildmagic import cli

    rc = cli.main(
        [
            "--scenario",
            "test_chamber",
            "--seed",
            "7",
            "--no-render",
            "--command",
            "tick",
            "--command",
            "standing",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Standing - how the powers regard you" in out
    assert "the Grand Empire" in out
