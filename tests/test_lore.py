from __future__ import annotations

from typing import Any

from wildmagic.actions import GameSession
from wildmagic.lore import MockLoreProvider, normalize_lore_claims, parse_lore_json
from wildmagic.models import LoreClaim
from wildmagic.wild_magic import MockTownProvider


class FixedDialogueProvider:
    name = "fixed"

    def reply(self, message: str, context: dict[str, Any]) -> str:
        return "I heard a rumor that the Emberwood witch keeps an old oak awake at midnight."


def _stand_next_to_first_npc(session: GameSession) -> None:
    npc = min(
        (entity for entity in session.engine.state.entities.values() if entity.kind == "npc"),
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

    claims = normalize_lore_claims(
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

    assert len(claims) == 1
    assert claims[0].status == "unverified"
    assert claims[0].confidence == 0.5
    assert claims[0].salience == 5
    assert claims[0].tags == ["oak", "midnight"]


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
    assert len(session.engine.state.lore_claims) == 1
    claim = session.engine.state.lore_claims[0]
    assert claim.kind == "rumor"
    assert claim.status == "rumored"
    assert "Emberwood witch" in claim.text
    assert result.dialogue is not None
    assert result.dialogue["lore"]["claims"][0]["id"] == claim.id
    assert session.records[-1]["dialogue"]["lore"]["claims"][0]["id"] == claim.id


def test_replay_dialogue_applies_recorded_lore_without_provider_call() -> None:
    session = GameSession(seed=7, scenario="town", provider_name="mock")
    _stand_next_to_first_npc(session)
    claim = LoreClaim(
        id="lore_replay",
        kind="rumor",
        subject="midnight oak",
        text="Old Maren says an old oak wakes at midnight.",
        source_npc="Old Maren",
        source_turn=0,
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
            "lore": {"enabled": True, "claims": [claim.to_dict()]},
        },
    )

    assert result.success is True
    assert [stored.id for stored in session.engine.state.lore_claims] == ["lore_replay"]


def test_matching_lore_claims_merge_and_corroborate() -> None:
    session = GameSession(seed=7, scenario="town", provider_name="mock")
    first = LoreClaim(
        id="lore_first",
        kind="rumor",
        subject="old oak",
        text="Old Maren says an old oak wakes at midnight.",
        source_npc="Old Maren",
        source_turn=0,
        location="Hollowmere",
        salience=2,
        tags=["oak", "midnight"],
    )
    second = LoreClaim(
        id="lore_second",
        kind="rumor",
        subject="old oak",
        text="Quill says the old oak opens one eye after midnight.",
        source_npc="Quill Hatchet",
        source_turn=1,
        location="Hollowmere",
        salience=3,
        tags=["oak", "witch"],
    )

    assert session.engine.add_lore_claims([first]) == [first]
    assert session.engine.add_lore_claims([second]) == []

    assert len(session.engine.state.lore_claims) == 1
    stored = session.engine.state.lore_claims[0]
    assert stored.status == "corroborated"
    assert stored.salience == 4
    assert "witch" in stored.tags


def test_town_generation_context_redeems_lore_hook() -> None:
    session = GameSession(seed=11, scenario="frontier", provider_name="mock")
    claim = LoreClaim(
        id="lore_oak",
        kind="rumor",
        subject="old oak",
        text="Quill says a witch keeps an old oak awake at midnight.",
        source_npc="Quill Hatchet",
        source_turn=1,
        location="Hollowmere",
        salience=5,
        tags=["witch", "oak", "midnight"],
    )
    session.engine.add_lore_claims([claim])

    context = session.engine._build_town_context(1, 1)
    spec = MockTownProvider().generate(1, 1, context)
    session.engine._generate_llm_town(1, 1, spec, context)

    assert context["lore_hooks"][0]["id"] == "lore_oak"
    assert claim.status == "redeemed"
    assert claim.redeemed_in is not None
    assert "old oak" in spec.description
