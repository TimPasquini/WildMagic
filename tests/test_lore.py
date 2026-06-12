from __future__ import annotations

from typing import Any

from wildmagic.actions import GameSession
from wildmagic.lore import MockLoreProvider, normalize_lore_promises, parse_lore_json
from wildmagic.promises import WorldPromise
from wildmagic.wild_magic import MockTownProvider


class FixedDialogueProvider:
    name = "fixed"

    def reply(self, message: str, context: dict[str, Any]) -> str:
        return "I heard a rumor that the Emberwood witch keeps an old oak awake at midnight."


def _stand_next_to_first_npc(session: GameSession) -> None:
    npc = min(
        (
            entity
            for entity in session.engine.state.entities.values()
            if entity.kind == "npc"
        ),
        key=lambda entity: entity.id,
    )
    player = session.engine.state.player
    player.x = npc.x + 1
    player.y = npc.y


def test_lore_claim_normalization_defaults_and_bounds() -> None:
    parsed = parse_lore_json(
        """
        {"claims":[
          {"kind":"rumor","subject":"Old Oak","text":"Maren says an old oak wakes at midnight.","status":"maybe","confidence":9,"salience":99,"tags":["Oak"," midnight "]}
        ]}
        """
    )

    promises = normalize_lore_promises(
        parsed,
        {
            "npc": "Old Maren",
            "turn": 4,
            "location": "Hollowmere",
            "message": "Any rumors?",
            "reply": "Maren says an old oak wakes at midnight.",
            "zone": {"x": 0, "y": 0},
        },
    )

    assert len(promises) == 1
    assert promises[0].status == "unverified"
    assert promises[0].confidence == 0.5
    assert promises[0].salience == 5
    assert promises[0].tags == ["oak", "midnight"]


def test_dialogue_lore_extraction_adds_claim_to_ledger() -> None:
    session = GameSession(
        seed=7,
        scenario="town",
        provider_name="mock",
        dialogue_provider=FixedDialogueProvider(),
        lore_provider=MockLoreProvider(),
    )
    _stand_next_to_first_npc(session)

    result = session.execute_command("talk Any rumors?")
    session.drain_lore(block=True)

    assert result.success is True
    lore_promises = [promise for promise in session.engine.state.promises if promise.kind != "quest"]
    assert len(lore_promises) == 1
    promise = lore_promises[0]
    assert promise.kind == "rumor"
    assert promise.status == "bound"
    assert promise.binding is not None
    assert "Emberwood witch" in promise.text
    assert result.dialogue is not None
    assert result.dialogue["lore"]["promises"][0]["id"] == promise.id
    assert session.records[-1]["dialogue"]["lore"]["promises"][0]["id"] == promise.id


class CountingLoreProvider:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, context: dict[str, Any]) -> str:
        self.calls += 1
        return '{"claims": []}'


def test_replay_promises_inject_at_apply_point_without_provider_call() -> None:
    lore_provider = CountingLoreProvider()
    session = GameSession(seed=7, scenario="town", provider_name="mock", lore_provider=lore_provider)
    _stand_next_to_first_npc(session)
    promise = WorldPromise(
        id="promise_replay",
        kind="rumor",
        subject="midnight oak",
        text="Old Maren says an old oak wakes at midnight.",
        tags=[],
        source="dialogue:Old Maren",
        source_turn=0,
        origin_zone=(0, 0),
        location="Hollowmere",
    )

    result = session.execute_command(
        "talk Any rumors?",
        replay_dialogue={
            "npc": "Old Maren",
            "message": "Any rumors?",
            "provider": "replay",
            "technical_failure": False,
            "reply": "Mind the old oak after midnight.",
        },
        replay_promises={"before": [], "after": [promise.to_dict()]},
    )

    assert result.success is True
    assert lore_provider.calls == 0
    assert [stored.id for stored in session.engine.state.promises if stored.kind != "quest"] == ["promise_replay"]
    # Replaying re-records the same apply point, so a replay round-trips byte-identically.
    assert session.records[-1]["promises"] == {"before": [], "after": [promise.to_dict()]}


def test_dialogue_context_includes_nearby_objects() -> None:
    session = GameSession(seed=7, scenario="town", provider_name="mock")
    engine = session.engine
    npc = min(
        (entity for entity in engine.state.entities.values() if entity.kind == "npc"),
        key=lambda entity: entity.id,
    )
    near = engine.spawn_prop("votive_candles", npc.x + 1, npc.y)
    assert near is not None

    context = engine.dialogue_context_for_llm(npc, "what's that on the table?")
    names = [obj["name"] for obj in context["nearby_objects"]]
    assert near.name in names
    nearest = context["nearby_objects"][0]
    assert nearest["description"]
    assert nearest["what"] in {"object", "loose item"}
    # Objects across the map are not in the NPC's perception.
    for obj in context["nearby_objects"]:
        assert obj["name"] != "nonexistent"
    far_names = [
        entity.name
        for entity in engine.state.entities.values()
        if entity.kind in {"prop", "item"} and max(abs(entity.x - npc.x), abs(entity.y - npc.y)) > 6
    ]
    for name in far_names:
        if name not in [e.name for e in engine.state.entities.values() if max(abs(e.x - npc.x), abs(e.y - npc.y)) <= 6]:
            assert name not in names


def test_matching_promises_merge_and_corroborate() -> None:
    session = GameSession(seed=7, scenario="town", provider_name="mock")
    first = WorldPromise(
        id="promise_first",
        kind="rumor",
        subject="old oak",
        text="Old Maren says an old oak wakes at midnight.",
        tags=["oak", "midnight"],
        source="dialogue:Old Maren",
        source_turn=0,
        origin_zone=(0, 0),
        location="Hollowmere",
        salience=2,
    )
    second = WorldPromise(
        id="promise_second",
        kind="rumor",
        subject="old oak",
        text="Quill says the old oak opens one eye after midnight.",
        tags=["oak", "witch"],
        source="dialogue:Quill Hatchet",
        source_turn=1,
        origin_zone=(0, 0),
        location="Hollowmere",
        salience=3,
        what="witch",
    )

    assert session.engine.add_promises([first]) == [first]
    assert session.engine.add_promises([second]) == []

    assert len(session.engine.state.promises) == 1
    stored = session.engine.state.promises[0]
    assert stored.status == "bound"
    assert stored.binding is not None
    assert stored.salience == 4
    assert "witch" in stored.tags


def test_town_generation_context_redeems_lore_hook() -> None:
    session = GameSession(seed=11, scenario="frontier", provider_name="mock")
    promise = WorldPromise(
        id="promise_oak",
        kind="rumor",
        subject="old oak",
        text="Quill says a witch keeps an old oak awake southeast of here at midnight.",
        tags=["witch", "oak", "midnight"],
        source="dialogue:Quill Hatchet",
        source_turn=1,
        origin_zone=(0, 0),
        location="Hollowmere",
        salience=5,
        what="witch",
    )
    session.engine.add_promises([promise])

    context = session.engine._build_town_context(1, 1)
    spec = MockTownProvider().generate(1, 1, context)
    session.engine._generate_llm_town(1, 1, spec, context)

    assert context["promise_hooks"][0]["id"] == "promise_oak"
    assert promise.status == "realized"
    assert promise.realized_in is not None
    assert "old oak" in spec.description
