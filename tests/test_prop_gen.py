"""Tests for experimental LLM prop set-dressing (prop_gen.py + engine wiring).

Covers the parser/sanitation, provider routing, and the engine's background swap:
hybrid scope (only ambient set_dressing props, never books/notices/scenes), and the
freeze-once-seen rule (props the player has already laid eyes on are never rewritten).
All tests use the deterministic MockPropProvider — no network, no model calls.
"""

from __future__ import annotations

import json
import time

from wildmagic.engine import GameEngine
from wildmagic.prop_gen import (
    MockPropProvider,
    OllamaPropProvider,
    make_prop_provider,
    parse_prop_batch,
)


# ----------------------------------------------------------------------------
# Parser / sanitation
# ----------------------------------------------------------------------------


def test_parse_prop_batch_sanitizes_and_drops_invalid() -> None:
    raw = json.dumps(
        {
            "props": [
                {
                    "name": "Brass Lamp",
                    "description": "A dented brass lamp, cold to the touch.",
                    "char": "!",
                    "blocks": False,
                    "tags": ["Metal", "LIGHT", "light"],  # mixed case + dup
                },
                {"name": "", "description": "no name -> dropped"},
                {"name": "No Description"},  # dropped
                {
                    "name": "Great Vat",
                    "description": "A vat.",
                    "char": "VV",  # too long -> first glyph
                    "blocks": "yes",  # truthy -> True
                    "tags": "not-a-list",  # -> []
                },
            ]
        }
    )
    specs = parse_prop_batch(raw)
    assert [s.name for s in specs] == ["Brass Lamp", "Great Vat"]
    assert specs[0].tags == ["metal", "light"]  # lowercased + deduped
    assert specs[1].char == "V"  # clamped to one glyph
    assert specs[1].blocks is True
    assert specs[1].tags == []


def test_parse_prop_batch_accepts_bare_list_and_caps() -> None:
    items = [
        {"name": f"Thing {i}", "description": f"Item number {i}."} for i in range(20)
    ]
    specs = parse_prop_batch(json.dumps(items))
    assert 0 < len(specs) <= 6  # _MAX_PROPS_PER_BATCH


def test_mock_provider_is_deterministic_and_valid() -> None:
    provider = MockPropProvider()
    ctx = {
        "region": "the Warren",
        "room": {
            "room_type": "ossuary",
            "era": "pre_charter",
            "condition": "ransacked",
        },
        "count": 3,
    }
    first = [s.__dict__ for s in provider.generate(ctx)]
    second = [s.__dict__ for s in provider.generate(ctx)]
    assert first == second
    assert len(first) == 3
    for spec in first:
        assert spec["name"] and spec["description"] and spec["char"]


def test_make_prop_provider_routing() -> None:
    assert make_prop_provider("off") is None
    assert make_prop_provider("none") is None
    assert isinstance(make_prop_provider("mock"), MockPropProvider)
    assert isinstance(make_prop_provider("ollama"), OllamaPropProvider)
    assert isinstance(make_prop_provider("auto"), OllamaPropProvider)


# ----------------------------------------------------------------------------
# Engine integration
# ----------------------------------------------------------------------------


def _drive_prop_gen(engine: GameEngine, timeout: float = 5.0) -> None:
    """Run background prop generation to completion (mock provider is fast)."""
    engine._PROP_MAX_PENDING = 999
    deadline = time.time() + timeout
    while time.time() < deadline:
        engine._poll_prop_generation()
        engine._launch_prop_generation()
        if not engine._pending_prop_rooms:
            break
        time.sleep(0.01)
    while engine._pending_prop_rooms and time.time() < deadline:
        engine._poll_prop_generation()
        time.sleep(0.01)


def _props(engine: GameEngine):
    return [e for e in engine.state.entities.values() if e.kind == "prop"]


def test_disabled_by_default_offline() -> None:
    # No provider injected and (in CI/offline) no Ollama reachable -> generation off,
    # props stay exactly as the static generator placed them.
    engine = GameEngine(seed=7, scenario="warren")
    if engine._prop_provider is not None:  # a dev box with Ollama up; not under test
        return
    _drive_prop_gen(engine)
    assert all("llm_generated" not in p.tags for p in _props(engine))
    engine.close()


def test_swap_replaces_unseen_set_dressing_in_place() -> None:
    engine = GameEngine(seed=7, scenario="warren", prop_provider=MockPropProvider())
    engine.state.explored.clear()  # nothing seen -> everything eligible
    before = {e.id: (e.x, e.y) for e in _props(engine) if "set_dressing" in e.tags}
    assert before, "warren should place ambient set-dressing props"
    _drive_prop_gen(engine)
    generated = [p for p in _props(engine) if "llm_generated" in p.tags]
    assert generated, "expected some props to be swapped"
    for prop in generated:
        # Swap is in place: same entity id and position, just re-skinned.
        assert prop.id in before
        assert (prop.x, prop.y) == before[prop.id]
        assert prop.details.get("prop_spec")
    engine.close()


def test_freeze_once_seen_blocks_swaps() -> None:
    engine = GameEngine(seed=7, scenario="warren", prop_provider=MockPropProvider())
    # Mark the whole map as already seen -> every prop is frozen.
    engine.state.explored = {
        engine.tile_key(x, y)
        for y in range(engine.state.height)
        for x in range(engine.state.width)
    }
    _drive_prop_gen(engine)
    assert all("llm_generated" not in p.tags for p in _props(engine))
    engine.close()


def test_hybrid_leaves_books_and_notices_static() -> None:
    # The archive hub has books + a posted notice; none are set_dressing, so the
    # generator must never rewrite them even when it runs over the whole floor.
    engine = GameEngine(seed=7, scenario="archive", prop_provider=MockPropProvider())
    engine.state.explored.clear()
    protected = [
        p
        for p in _props(engine)
        if p.details.get("book_seed") or "empire" in p.tags or "readable" in p.tags
    ]
    assert protected, "archive should have books/notices to protect"
    protected_ids = {p.id for p in protected}
    _drive_prop_gen(engine)
    for prop in _props(engine):
        if prop.id in protected_ids:
            assert "llm_generated" not in prop.tags
    engine.close()
