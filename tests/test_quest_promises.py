from __future__ import annotations

from wildmagic.actions import GameSession


def test_manual_quest_commands_use_promise_log() -> None:
    session = GameSession(seed=5, scenario="test_chamber", provider_name="mock")

    added = session.execute_command("quest add Errand Find-the-thing Maren Hollowmere")
    listed = session.execute_command("quest list")
    completed = session.execute_command("quest complete 1")

    assert added.success is True
    assert listed.success is True
    assert any("Errand" in line for line in listed.messages)
    assert completed.success is True
    entries = session.engine.quest_log_entries()
    assert len(entries) == 1
    assert entries[0].status == "completed"
    assert session.engine.state.promises[0].kind == "quest"


def test_npc_request_registers_quest_promise_and_realizes_fetch_item(monkeypatch) -> None:
    session = GameSession(seed=23, scenario="frontier", provider_name="mock")
    engine = session.engine
    monkeypatch.setattr(engine, "_zone_should_be_town", lambda zx, zy: False)
    player = engine.state.player
    npc = engine.spawn_npc(
        "Mara Flint",
        "m",
        player.x + 1,
        player.y,
        role="collector",
        backstory="Collects things that should have stayed buried.",
        wanted_item="Glass Eye of Hollowmere",
        wanted_qty=1,
        reward_gold=25,
    )

    engine.apply_dialogue_exchange(npc, "Any work?", "Bring me the Glass Eye of Hollowmere.", None)

    entries = engine.quest_log_entries()
    assert len(entries) == 1
    promise = next(promise for promise in engine.state.promises if promise.kind == "quest")
    assert promise.binding is not None
    assert promise.binding.blueprint == "hidden_site"
    assert promise.bound_space is not None
    target_zone = promise.bound_space.zone
    assert target_zone is not None

    engine._load_or_generate_zone(target_zone[0], target_zone[1], player.x, player.y)

    assert promise.status == "realized"
    assert any(
        entity.kind == "item" and entity.item_type == "glass eye of hollowmere"
        for entity in engine.state.entities.values()
    )


def test_quest_turn_in_fulfills_matching_promise() -> None:
    session = GameSession(seed=29, scenario="frontier", provider_name="mock")
    engine = session.engine
    player = engine.state.player
    npc = engine.spawn_npc(
        "Mara Flint",
        "m",
        player.x + 1,
        player.y,
        role="collector",
        backstory="Collects things that should have stayed buried.",
        wanted_item="Glass Eye of Hollowmere",
        wanted_qty=1,
        reward_gold=25,
    )
    engine.apply_dialogue_exchange(npc, "Any work?", "Bring me the Glass Eye of Hollowmere.", None)
    engine.state.inventory["glass eye of hollowmere"] = 1
    engine.state.pending_trade = {
        "npc_id": npc.id,
        "npc_name": npc.name,
        "initiator": "player",
        "npc_gives": [{"item": "gold", "quantity": 25}],
        "npc_wants": [{"item": "glass eye of hollowmere", "quantity": 1}],
        "proposal_text": "Glass Eye for gold.",
    }

    engine.resolve_pending_trade(True)

    promise = next(promise for promise in engine.state.promises if promise.kind == "quest")
    assert promise.status == "fulfilled"
    assert engine.state.npc_profiles[npc.id].quest_completed is True
    assert engine.quest_log_entries()[0].status == "completed"
