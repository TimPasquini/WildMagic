from __future__ import annotations

import pytest

from wildmagic.actions import GameSession
from wildmagic.generation import SITE_BLUEPRINTS
from promise_golden_cases import GOLDEN_BINDING_CASES
from wildmagic.promises import WorldPromise, bind_promise


def _promise_from_case(case: dict) -> WorldPromise:
    claim = case["claim"]
    return WorldPromise(
        id=f"promise_{case['name']}",
        kind="rumor",
        subject=str(claim.get("what") or claim.get("text") or "unknown"),
        text=str(claim.get("text") or ""),
        tags=[str(tag) for tag in claim.get("tags") or []],
        source="dialogue:test",
        source_turn=1,
        origin_zone=(0, 0),
        salience=int(claim.get("salience") or 2),
        confidence=float(claim.get("confidence") or 0.5),
        what=str(claim.get("what") or ""),
    )


@pytest.mark.parametrize(
    "case", GOLDEN_BINDING_CASES, ids=[case["name"] for case in GOLDEN_BINDING_CASES]
)
def test_golden_promise_binding_cases(case: dict) -> None:
    promise = _promise_from_case(case)
    expected = case["expected"]

    reservation = bind_promise(
        promise,
        explored_zones={tuple(zone) for zone in case.get("explored", [])},
        reserved_counts={},
    )

    if expected is None:
        assert promise.binding is None
        assert reservation is None
        return

    assert promise.binding is not None
    assert promise.binding.blueprint == expected["blueprint"]
    assert promise.status == "bound"
    assert promise.claimed_space is not None
    assert promise.claimed_space.mode == expected["space"]["mode"]
    if "direction" in expected["space"]:
        assert promise.claimed_space.direction == expected["space"]["direction"]
    if "terrain_tag" in expected["space"]:
        assert promise.claimed_space.terrain_tag == expected["space"]["terrain_tag"]
    if expected.get("npc_bound"):
        assert promise.binding.npc_seed is not None
    if "bound_zone" in expected:
        assert reservation is not None
        assert reservation.zone == expected["bound_zone"]
        assert promise.bound_space is not None
        assert promise.bound_space.zone == expected["bound_zone"]
    if expected.get("relocated"):
        assert promise.claimed_space.mode == "direction"
        assert promise.bound_space is not None
        assert promise.bound_space.zone != (
            promise.claimed_space.anchor_zone[0] + promise.claimed_space.direction[0],
            promise.claimed_space.anchor_zone[1] + promise.claimed_space.direction[1],
        )


@pytest.mark.parametrize(
    ("name", "subject", "text", "tags", "what"),
    [
        # All taken from real false bindings in the 2026-06 live shakedown: chatter that
        # the old substring/full-text matcher turned into committed world sites.
        (
            "campaign_map_is_not_a_camp",
            "Imperial Campaign Map",
            "I've got my eye on an Imperial Campaign Map.",
            ["quest_item", "navigation", "map"],
            "map",
        ),
        (
            "passage_is_not_a_sage",
            "ship passage",
            "might be able to get you passage on a ship for a price",
            ["quest_hook", "ship"],
            "ship",
        ),
        (
            "saints_philosophy_is_not_a_chapel",
            "old saints",
            "The old saints care not for our petty squabbles over magic's form.",
            ["magic", "religion"],
            "",
        ),
        (
            "trade_chatter_is_not_a_creature_site",
            "strange brass moth",
            "That little fellow is quite rare and would fetch a tidy sum.",
            ["creature", "item"],
            "",
        ),
        # kind="quest" from the extractor does NOT skip the gate: only engine-authored
        # quests (with a typed objective) are structurally trusted.
        (
            "extractor_quest_label_is_not_trusted",
            "dangerous bounty out for a mage",
            "Those scouts mentioned a dangerous bounty out for a mage.",
            ["quest"],
            "",
        ),
        # Residual risk: blueprint keywords the model puts into TAGS still bind
        # (e.g. a fetch request tagged "grave" or "cache"). That is extraction-prompt
        # territory — tags must describe the claim's referent, not items mentioned.
    ],
)
def test_live_chatter_stays_flavor(
    name: str, subject: str, text: str, tags: list[str], what: str
) -> None:
    promise = WorldPromise(
        id=f"promise_{name}",
        # The extractor labeled the bounty case "quest"; the gate must not trust that.
        kind="quest" if "quest" in tags else "rumor",
        subject=subject,
        text=text,
        tags=tags,
        source="dialogue:test",
        source_turn=1,
        origin_zone=(0, 0),
        salience=4,
        confidence=0.95,
        what=what,
    )

    reservation = bind_promise(promise, explored_zones={(0, 0)}, reserved_counts={})

    assert promise.binding is None
    assert reservation is None


def test_plural_keyword_in_what_still_binds() -> None:
    promise = WorldPromise(
        id="promise_saints_tombs",
        kind="place",
        subject="old bones of our saints' tombs",
        text="here in the old bones of our saints' tombs",
        tags=["sacred"],
        source="dialogue:test",
        source_turn=1,
        origin_zone=(0, 0),
        salience=3,
        confidence=0.8,
        what="tombs",
    )

    reservation = bind_promise(promise, explored_zones={(0, 0)}, reserved_counts={})

    assert promise.binding is not None
    assert promise.binding.blueprint == "memorial_site"
    assert reservation is not None


def test_reservation_capacity_spills_directional_promise_outward() -> None:
    promise = WorldPromise(
        id="promise_overflow",
        kind="rumor",
        subject="north chapel",
        text="There is a chapel north of town.",
        tags=["chapel"],
        source="dialogue:test",
        source_turn=1,
        origin_zone=(0, 0),
        salience=4,
        confidence=0.7,
        what="chapel",
    )

    reservation = bind_promise(
        promise,
        explored_zones={(0, 0)},
        reserved_counts={(0, -1): 2},
    )

    assert reservation is not None
    assert reservation.zone == (0, -2)
    assert promise.bound_space is not None
    assert promise.bound_space.zone == (0, -2)


def test_sacred_site_promise_realizes_in_open_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = GameSession(seed=19, scenario="frontier", provider_name="mock")
    engine = session.engine
    monkeypatch.setattr(engine, "_zone_should_be_town", lambda zx, zy: False)
    promise = WorldPromise(
        id="promise_chapel_north",
        kind="rumor",
        subject="chapel north of town",
        text="Old Maren says there is a chapel north of town.",
        tags=["chapel"],
        source="dialogue:Old Maren",
        source_turn=1,
        origin_zone=(0, 0),
        salience=5,
        confidence=0.8,
        what="chapel",
    )

    engine.add_promises([promise])
    assert engine.state.promise_reservations[(0, -1)][0].promise_id == promise.id

    player = engine.state.player
    engine._load_or_generate_zone(0, -1, player.x, 1)

    assert promise.status == "realized"
    assert promise.realized_in == "sacred_site at zone (0,-1)"
    assert (0, -1) not in engine.state.promise_reservations
    site_props = {
        entity.name
        for entity in engine.state.entities.values()
        if entity.kind == "prop"
    }
    assert {"saint statue", "votive candles"} & site_props
    keepers = [
        profile
        for profile in engine.state.npc_profiles.values()
        if profile.role == "site keeper"
    ]
    assert keepers
    assert "Old Maren says" in keepers[0].backstory


@pytest.mark.parametrize(
    ("blueprint", "text", "tags", "what", "expected_prop", "expected_role"),
    [
        (
            "inhabited_site",
            "A witch keeps a hut east of here.",
            ["witch", "hut"],
            "witch",
            "writing desk",
            "local keeper",
        ),
        (
            "hostile_site",
            "Bandits have a camp east of here.",
            ["bandits", "camp"],
            "bandit camp",
            "old campfire ash",
            None,
        ),
        (
            "memorial_site",
            "They buried the old king in a barrow east of here.",
            ["barrow", "tomb"],
            "barrow",
            "inscribed gravestone",
            None,
        ),
        (
            "hidden_site",
            "Smugglers keep a cache east of here.",
            ["cache", "smugglers"],
            "cache",
            "locked chest",
            None,
        ),
        (
            "creature_site",
            "A beast has a lair east of here.",
            ["beast", "lair"],
            "lair",
            "moss-covered bones",
            None,
        ),
        (
            "authority_site",
            "A warrant checkpoint waits east of here.",
            ["warrant", "checkpoint"],
            "checkpoint",
            "posted notice",
            "field official",
        ),
    ],
)
def test_site_archetypes_realize_without_bespoke_handlers(
    monkeypatch: pytest.MonkeyPatch,
    blueprint: str,
    text: str,
    tags: list[str],
    what: str,
    expected_prop: str,
    expected_role: str | None,
) -> None:
    session = GameSession(seed=31, scenario="frontier", provider_name="mock")
    engine = session.engine
    monkeypatch.setattr(engine, "_zone_should_be_town", lambda zx, zy: False)
    promise = WorldPromise(
        id=f"promise_{blueprint}",
        kind="rumor",
        subject=blueprint,
        text=text,
        tags=tags,
        source="dialogue:test",
        source_turn=1,
        origin_zone=(0, 0),
        salience=5,
        confidence=0.8,
        what=what,
    )

    engine.add_promises([promise])
    assert promise.binding is not None
    assert promise.binding.blueprint == blueprint
    assert blueprint in SITE_BLUEPRINTS

    player = engine.state.player
    engine._load_or_generate_zone(1, 0, 1, player.y)

    assert promise.status == "realized"
    assert promise.realized_in == f"{blueprint} at zone (1,0)"
    prop_names = {
        entity.name
        for entity in engine.state.entities.values()
        if entity.kind == "prop"
    }
    assert expected_prop in prop_names
    if expected_role is not None:
        matching_profiles = [
            profile
            for profile in engine.state.npc_profiles.values()
            if profile.role == expected_role
        ]
        assert matching_profiles
        assert text in matching_profiles[0].backstory
