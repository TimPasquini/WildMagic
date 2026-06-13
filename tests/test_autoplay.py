from __future__ import annotations

import json

from wildmagic.actions import ActionResult, GameSession
from wildmagic.autoplay import (
    AgentObservation,
    CampaignConfig,
    CampaignRunner,
    Finding,
    InvariantChecker,
    OllamaAgent,
    adjacent_options,
    avoid_commands_from_history,
    cluster_notes,
    compact_messages,
    finding_spell,
    expedition_direction_for_seed,
    local_map_view,
    parse_args,
    parse_agent_response,
    random_seed_base,
    result_summary,
    validate_agent_command,
)
from wildmagic.models import WALL


def test_agent_response_parsing_and_command_validation() -> None:
    decision = parse_agent_response(
        '{"command":"cast bind the goblin in blue webbing","note":"web test","bug_suspected":true}'
    )

    assert decision.command == "cast bind the goblin in blue webbing"
    assert decision.note == "web test"
    assert decision.bug_suspected is True
    assert validate_agent_command("move east") == "move east"


def test_agent_command_validation_rejects_unknown_verbs() -> None:
    try:
        validate_agent_command("dance with the dungeon")
    except ValueError as exc:
        assert "unknown command verb" in str(exc)
    else:
        raise AssertionError("unknown verb should fail validation")


def test_agent_command_validation_rejects_literal_placeholders() -> None:
    for command in ["cast <wild spell idea>", "cast <spell idea>", "talk <message>", "read <target>"]:
        try:
            validate_agent_command(command)
        except ValueError as exc:
            assert "replace placeholder text" in str(exc)
        else:
            raise AssertionError(f"placeholder command should fail validation: {command}")


def test_invariant_checker_contract_violations() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    checker = InvariantChecker()
    try:
        technical = ActionResult(
            command="cast broken json",
            action="cast",
            success=False,
            consumed_turn=True,
            turn_before=0,
            turn_after=1,
            technical_failure=True,
            messages=["technical failure"],
        )
        rejected = ActionResult(
            command="cast become immortal",
            action="cast",
            success=False,
            consumed_turn=False,
            turn_before=1,
            turn_after=1,
            wild_magic={"data": {"accepted": False}},
            messages=["too much"],
        )

        technical_findings = checker.check(session, technical, episode=1)
        rejected_findings = checker.check(session, rejected, episode=1)

        assert any(f.kind == "technical_failure_consumed_turn" for f in technical_findings)
        assert any(f.kind == "rejected_spell_did_not_consume_turn" for f in rejected_findings)
    finally:
        session.close()


def test_invariant_checker_finds_actor_on_blocking_tile() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    checker = InvariantChecker()
    try:
        player = session.engine.state.player
        session.engine.state.tiles[player.y][player.x] = WALL
        result = ActionResult(
            command="wait",
            action="wait",
            success=True,
            consumed_turn=True,
            turn_before=0,
            turn_after=1,
            messages=["waited"],
        )

        findings = checker.check(session, result, episode=1)

        assert any(f.kind == "blocking_actor_on_blocking_tile" for f in findings)
    finally:
        session.close()


def test_action_messages_survive_message_log_cap() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    try:
        state = session.engine.state
        for index in range(200):
            state.add_message(f"filler message {index}")

        result = session.execute_command("cast bind the nearest enemy in sticky blue webbing")

        assert result.messages, "command messages must not vanish once the 80-entry log cap is reached"
        assert all("filler message" not in message for message in result.messages)
    finally:
        session.close()


def test_local_map_view_is_cropped_and_shows_player() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    try:
        view = local_map_view(session, radius_x=10, radius_y=6)

        assert view, "local map view must not be empty"
        assert len(view) <= 13
        assert all(len(row) <= 21 for row in view)
        assert any("@" in row for row in view)
    finally:
        session.close()


def test_agent_observation_compacts_long_messages_and_exposes_decision_hints() -> None:
    long_message = "lore " * 200
    observation = AgentObservation(
        episode=1,
        seed=7,
        scenario="test_chamber",
        persona="cautious",
        theme="Use ordinary roguelike tactics and cast only when pressured.",
        step=3,
        turn=1,
        new_messages=compact_messages([long_message]),
        state_lines=["Turn 1 | HP 24/24 | MP 14/14"],
        adjacent={
            "north": {"status": "blocked", "reason": "wall blocks movement"},
            "east": {"status": "open", "suggested_command": "move east"},
        },
        recent_commands=["move north", "move north"],
        last_result={"command": "move north", "success": False, "messages": ["wall blocks the way."]},
        avoid_commands=["move north"],
        expedition_direction="east",
    )

    payload = observation.to_prompt_dict()

    assert len(payload["new_messages"][0]) < 380
    assert "persona_guidance" in payload
    assert any("Useful directions" in hint and "east" in hint for hint in payload["decision_hints"])
    assert any("Do not choose" in hint and "move north" in hint for hint in payload["decision_hints"])
    assert any("Run heading is east" in hint and "move east" in hint for hint in payload["decision_hints"])


def test_expedition_direction_is_stable_from_seed() -> None:
    assert expedition_direction_for_seed(1, 1) == "east"
    assert expedition_direction_for_seed(1, 99) == "east"
    assert expedition_direction_for_seed(None, 2) == "south"


def test_autoplay_default_seed_base_is_randomized_and_overridable() -> None:
    generated = random_seed_base()
    assert 1 <= generated <= 2_147_483_647

    default_config = parse_args([])
    explicit_config = parse_args(["--seed-base", "7"])

    assert 1 <= default_config.seed_base <= 2_147_483_647
    assert explicit_config.seed_base == 7


def test_ollama_agent_prompt_contains_loop_recovery_instructions() -> None:
    observation = AgentObservation(
        episode=1,
        seed=7,
        scenario="test_chamber",
        persona="wild",
        theme="Focus on terrain-transformation spells this run.",
        step=1,
        turn=0,
        new_messages=["Episode started."],
        state_lines=["Turn 0 | HP 24/24 | MP 14/14"],
        avoid_commands=["cast earth wall"],
    )

    system = OllamaAgent()._messages(observation, None)[0]["content"]

    assert "Do not repeat a command that just failed" in system
    assert "Do not read the same book/title repeatedly" in system
    assert "Do not cast the same spell phrase more than twice" in system


def test_ollama_agent_prompt_explains_gameplay_systems_to_cover() -> None:
    observation = AgentObservation(
        episode=1,
        seed=7,
        scenario="town",
        persona="cautious",
        theme="Talk to every NPC you meet before fighting anything.",
        step=1,
        turn=0,
        new_messages=["Episode started."],
        state_lines=["Turn 0 | HP 24/24 | MP 14/14"],
    )

    system = OllamaAgent()._messages(observation, None)[0]["content"]

    assert "Exercise wild magic regularly" in system
    assert "Engage enemies instead of wandering" in system
    assert "investigate/search [target]" in system
    assert "talk/speak/say your own message" in system
    assert "Do not treat movement as the default answer" in system


def test_ollama_agent_prompt_forbids_literal_placeholders() -> None:
    observation = AgentObservation(
        episode=1,
        seed=7,
        scenario="test_chamber",
        persona="wild",
        theme="Use wild magic.",
        step=1,
        turn=0,
        new_messages=["A goblin is nearby."],
        state_lines=["Turn 0 | HP 24/24 | MP 14/14"],
    )

    messages = OllamaAgent()._messages(observation, None)
    system = messages[0]["content"]
    payload = json.loads(messages[1]["content"])

    assert "Never include angle brackets" in system
    assert "Do not copy placeholder text" in system
    assert "cast your own concrete spell idea" in payload["command_surface"]
    assert "cast <spell idea>" not in payload["command_surface"]


def test_adjacent_options_and_recent_failure_avoidance() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    try:
        options = adjacent_options(session)
        blocked_result = ActionResult(
            command="move north",
            action="move",
            success=False,
            consumed_turn=False,
            turn_before=0,
            turn_after=0,
            messages=["wall blocks the way."],
        )
        recent = [result_summary(blocked_result)]

        assert set(options) == {"north", "south", "east", "west"}
        assert options["east"]["status"] == "door"
        assert options["east"]["suggested_command"] == "open"
        assert "move north" in avoid_commands_from_history(["move north"], recent, "move north", 1)
    finally:
        session.close()


def test_cluster_notes_groups_similar_notes() -> None:
    notes = [
        "turn 5: the ice wall blocked my own escape route",
        "turn 9: ice wall blocked the escape route again",
        "turn 12: the goblin dialogue repeated the same greeting",
    ]

    clusters = cluster_notes(notes)

    assert clusters[0] == ("turn 5: the ice wall blocked my own escape route", 2)
    assert clusters[1] == ("turn 12: the goblin dialogue repeated the same greeting", 1)


def test_finding_spell_reads_wild_magic_evidence() -> None:
    finding = Finding(
        tier=2,
        kind="turn_consumed_without_messages",
        episode=1,
        seed=7,
        scenario="dungeon",
        turn=3,
        evidence={"wild_magic": {"spell": "summon a brass moth"}},
    )
    nested = Finding(
        tier=1,
        kind="state_validation_error",
        episode=1,
        seed=7,
        scenario="dungeon",
        turn=3,
        evidence={"result": {"wild_magic": {"spell": "turn the floor to ice"}}},
    )

    assert finding_spell(finding) == "summon a brass moth"
    assert finding_spell(nested) == "turn the floor to ice"


def test_campaign_writes_regression_seeds_for_serious_findings(tmp_path) -> None:
    config = CampaignConfig(episodes=1, out=tmp_path, run_id="regression_test")
    runner = CampaignRunner(config)
    runner.regression_entries[(7, "dungeon")] = {"unhandled_exception", "possible_softlock"}

    runner.write_regression_seeds()
    content = runner.regression_path.read_text(encoding="utf-8")

    assert "7\tdungeon\tpossible_softlock,unhandled_exception" in content


def test_campaign_runner_with_stub_agent_writes_artifacts(tmp_path) -> None:
    config = CampaignConfig(
        episodes=1,
        max_turns=2,
        max_steps=4,
        scenarios=["test_chamber"],
        personas=["cautious"],
        seed_base=7,
        provider="mock",
        agent="stub",
        out=tmp_path,
        run_id="test_run",
        stub_commands=["inspect", "wait", "quit"],
    )

    report_path = CampaignRunner(config).run()
    run_dir = tmp_path / "test_run"

    assert report_path == run_dir / "report.md"
    assert report_path.exists()
    assert (run_dir / "episode_001.jsonl").exists()
    assert (run_dir / "episode_001.replay.json").exists()
    assert (run_dir / "episode_001.commands.txt").read_text(encoding="utf-8").splitlines() == [
        "inspect",
        "wait",
        "quit",
    ]
    replay = json.loads((run_dir / "episode_001.replay.json").read_text(encoding="utf-8"))
    assert replay["seed"] == 7
    assert replay["scenario"] == "test_chamber"
