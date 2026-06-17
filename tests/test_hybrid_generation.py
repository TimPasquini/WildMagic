from __future__ import annotations

from wildmagic.actions import describe_state, summarize_state
from wildmagic.actions import GameSession
from wildmagic.engine import GameEngine
from wildmagic.models import CanonRecord, STAIRS_DOWN
from wildmagic.promises import WorldPromise
from wildmagic.replay import run_replay, save_replay
from wildmagic.texture import grammar_book


def test_room_profiles_feed_context_and_headless_inspect() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    state = engine.state
    current_room = engine.room_profile_at(state.player.x, state.player.y)

    assert current_room is not None
    assert current_room.room_type
    assert current_room.topics
    assert state.tile_rooms[f"{state.player.x},{state.player.y}"] == current_room.id

    context = engine.context_for_llm("make the room remember its oldest book")
    assert context["current_room"]["id"] == current_room.id
    assert any(room["id"] == current_room.id for room in context["nearby_rooms"])

    inspect_lines = describe_state(engine)
    assert any(
        line.startswith("Current room:") and current_room.room_type in line
        for line in inspect_lines
    )
    assert any(
        line.startswith("Visible rooms:") and current_room.room_type in line
        for line in inspect_lines
    )


def test_canon_records_are_retrieved_by_room_threads_and_summarized() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    room = engine.room_profile_at(engine.state.player.x, engine.state.player.y)
    assert room is not None

    record = engine.add_canon_record(
        CanonRecord(
            id="canon_test_book",
            kind="book",
            attachment={"kind": "room", "room_id": room.id},
            title="The Bell That Learned Rain",
            text="A water-stained book insists a chapel north of town still rings when storms come.",
            summary="A chapel is said to ring in storms.",
            tags=[room.topics[0], "chapel", "rain"],
            source="mock",
            seed_packet={"room_id": room.id},
            turn_created=engine.state.turn,
        )
    )

    assert record.status == "canonical"
    context = engine.context_for_llm("ask the room about the chapel")
    assert any(item["id"] == "canon_test_book" for item in context["nearby_canon"])

    summary = summarize_state(engine)
    assert summary["canon_records"][0]["id"] == "canon_test_book"
    assert summary["canon_records"][0]["attachment"]["room_id"] == room.id


def test_room_profiles_survive_dungeon_floor_snapshots() -> None:
    engine = GameEngine(seed=11, scenario="dungeon")
    before = {
        room_id: room.to_public_dict()
        for room_id, room in engine.state.room_profiles.items()
    }
    assert before

    engine._save_dungeon_floor(engine.state.depth)
    engine.state.room_profiles.clear()
    engine.state.tile_rooms.clear()
    engine._load_dungeon_floor(engine.state.depth, STAIRS_DOWN)

    after = {
        room_id: room.to_public_dict()
        for room_id, room in engine.state.room_profiles.items()
    }
    assert after == before
    assert (
        engine.room_profile_at(engine.state.player.x, engine.state.player.y) is not None
    )


def test_realized_promise_flesh_writes_canon_records(monkeypatch) -> None:
    engine = GameEngine(seed=19, scenario="frontier", provider_name="mock")
    monkeypatch.setattr(engine, "_zone_should_be_town", lambda zx, zy: False)
    promise = WorldPromise(
        id="promise_quiet_chapel",
        kind="rumor",
        subject="quiet chapel",
        text="Old Maren says the Chapel of Quiet Hours stands north of town.",
        tags=["chapel", "saint"],
        source="dialogue:Old Maren",
        source_turn=1,
        origin_zone=(0, 0),
        salience=5,
        confidence=0.8,
        what="chapel",
        flesh={
            "site_name": "The Chapel of Quiet Hours",
            "keeper_name": "Warden Bell",
            "keeper_backstory": "Has kept the chapel because Old Maren's story was true before anyone wanted it to be.",
            "keeper_appearance": "A narrow woman with candle soot under her nails and a saint-medal polished bright.",
            "prop_description": "A votive bowl worn smooth by hands that came here believing Old Maren.",
            "arrival_line": "The Chapel of Quiet Hours waits where the rumor said it would.",
        },
    )

    engine.add_promises([promise])
    player = engine.state.player
    engine._load_or_generate_zone(0, -1, player.x, 1)

    canon = engine.state.canon_records
    site = canon["canon_promise_quiet_chapel_site"]
    assert site.title == "The Chapel of Quiet Hours"
    assert site.kind == "room_flavor"
    assert site.attachment["promise_id"] == promise.id
    assert "chapel" in site.tags
    assert site.seed_packet["promise_id"] == promise.id

    assert any(
        record.kind == "npc_appearance" and record.title == "Warden Bell"
        for record in canon.values()
    )
    assert any(
        record.kind == "object_detail" and "votive bowl" in record.text
        for record in canon.values()
    )

    assert any(
        record["id"] == "canon_promise_quiet_chapel_site"
        for record in engine.nearby_canon_records(tags=["chapel"], limit=3)
    )


def test_examine_materializes_current_room_once() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        first = session.execute_command("examine")
        assert first.success
        assert first.consumed_turn
        assert first.canon_materialization is not None
        assert first.canon_materialization["technical_failure"] is False
        assert len(session.engine.state.canon_records) == 1
        record = next(iter(session.engine.state.canon_records.values()))
        assert record.kind == "room_flavor"
        assert (
            record.attachment["room_id"]
            == session.engine.room_profile_at(
                session.engine.state.player.x,
                session.engine.state.player.y,
            ).id
        )
        assert session.records[-1]["canon"]["after"][0]["id"] == record.id

        second = session.execute_command("examine")
        assert second.success
        assert not second.consumed_turn
        assert len(session.engine.state.canon_records) == 1
        assert second.canon_materialization is not None
        assert second.canon_materialization["reused"] is True
    finally:
        session.close()


def test_examine_canon_replays_without_provider_call(tmp_path) -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        result = session.execute_command("examine")
        assert result.success
        replay_path = tmp_path / "examine.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched


def test_room_prewarm_materializes_current_room_without_turn_cost(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "1")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        room = session.engine.room_profile_at(
            session.engine.state.player.x, session.engine.state.player.y
        )
        assert room is not None
        result = session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        assert result.success
        assert not result.consumed_turn
        record = session.engine.state.canon_records[f"canon_room_{room.id}"]
        assert record.kind == "room_flavor"
        assert record.source == "background"
        assert record.attachment == {"kind": "room", "room_id": room.id}
        assert record.engine_choices["turn_cost"] == 0
    finally:
        session.close()


def test_examine_after_room_prewarm_reuses_room_flavor(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "1")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        result = session.execute_command("examine")
        assert result.success
        assert not result.consumed_turn
        assert result.canon_materialization["reused"] is True
        assert (
            sum(
                1
                for record in session.engine.state.canon_records.values()
                if record.kind == "room_flavor"
            )
            == 1
        )
    finally:
        session.close()


def test_room_prewarm_replays_without_provider_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "1")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        session.execute_command("inspect")
        replay_path = tmp_path / "room_prewarm.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched
    assert any(
        record["kind"] == "room_flavor"
        for record in replay_result.final_summary["canon_records"]
    )


def test_entity_detail_prewarm_materializes_far_look_without_turn_cost(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "3")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        moss = next(
            entity
            for entity in session.engine.state.entities.values()
            if entity.name == "blood moss"
        )
        result = session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        assert result.success
        assert not result.consumed_turn
        record = session.engine.state.canon_records[f"canon_detail_{moss.id}_far"]
        assert record.kind == "object_detail"
        assert record.source == "background"
        assert record.attachment == {"kind": "entity", "entity_id": moss.id}
        assert record.engine_choices["turn_cost"] == 0
        assert record.engine_choices["distance_band"] == "near"
    finally:
        session.close()


def test_investigate_after_entity_detail_prewarm_reuses_far_look(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "3")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        session.execute_command("open")
        session.drain_canon_prewarm(block=True)
        session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        result = session.execute_command("investigate goblin")
        assert result.success
        assert not result.consumed_turn
        assert result.canon_materialization["reused"] is True
        assert result.canon_materialization["record"]["kind"] == "creature_detail"
    finally:
        session.close()


def test_entity_detail_prewarm_replays_without_provider_call(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "3")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        session.execute_command("inspect")
        replay_path = tmp_path / "entity_detail_prewarm.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched
    assert any(
        record["kind"] == "object_detail"
        for record in replay_result.final_summary["canon_records"]
    )


def test_examine_technical_failure_replays_without_materializing(tmp_path) -> None:
    class FailingCanonProvider:
        name = "failing"

        def materialize(self, context):
            raise ValueError("no language today")

    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider=FailingCanonProvider(),
    )
    try:
        result = session.execute_command("examine")
        assert not result.success
        assert result.technical_failure
        assert not result.consumed_turn
        assert not session.engine.state.canon_records
        replay_path = tmp_path / "examine_failure.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched
    assert replay_result.final_summary["turn"] == 0
    assert replay_result.final_summary["canon_records"] == []


def _chamber_book(engine: GameEngine):
    """The deterministic book the test chamber generates beside the player."""
    return next(
        entity
        for entity in engine.state.entities.values()
        if entity.kind == "prop" and "book" in entity.tags
    )


def test_books_place_deterministically_in_labeled_rooms() -> None:
    found_books = False
    for seed in range(1, 21):
        engine_a = GameEngine(seed=seed, scenario="dungeon")
        engine_b = GameEngine(seed=seed, scenario="dungeon")
        books_a = sorted(
            (entity.name, entity.x, entity.y)
            for entity in engine_a.state.entities.values()
            if entity.kind == "prop" and "book" in entity.tags
        )
        books_b = sorted(
            (entity.name, entity.x, entity.y)
            for entity in engine_b.state.entities.values()
            if entity.kind == "prop" and "book" in entity.tags
        )
        assert books_a == books_b
        for name, x, y in books_a:
            found_books = True
            profile = engine_a.room_profile_at(x, y)
            assert profile is not None
            assert set(profile.tags) & {"books", "lore", "paper"}
            assert " of " in name  # grammar name, not the bare template name
    assert found_books


def test_book_texture_carries_rich_seed_axes() -> None:
    import random

    entry = grammar_book(random.Random(3), ["old maps"], "imperial")
    for key in {
        "topic",
        "secondary_topic",
        "genre",
        "discipline",
        "author_role",
        "audience",
        "purpose",
        "stance",
        "institution",
        "title_shape",
        "taboo_level",
    }:
        assert entry[key]
    assert entry["topic"] == "old maps"
    assert entry["genre"] not in {"old maps", "map", "maps"}
    # 1-4 durable subjects seed the title call and (later) the lore-card router.
    assert 1 <= len(entry["subjects"]) <= 4
    assert entry["topic"] in entry["subjects"]


def test_book_title_seed_packet_is_lean(monkeypatch) -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        context = session._canon_context_for_book_title(book, "canon_test_title")
        assert context["kind"] == "book_title"
        assert context["contract"]["allowed_outputs"] == ["title"]
        assert "forbidden" in context["contract"]
        subjects = context["subject"]["book"]["subjects"]
        assert "forbidden saints" in subjects
        # Lean packet: no expensive world/place/threads blocks on the title call.
        assert "threads" not in context
        assert "place" not in context
    finally:
        session.close()


def test_book_seed_packet_includes_catalog_guidance() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        context = session._canon_context_for_book(book, "canon_test_book")
        catalog = context["subject"]["book"]["catalog"]
        assert catalog["genre"] == "saint's life"
        assert catalog["author_role"] == "field nun"
        assert "title_shape" in catalog
        guidance = context["contract"]["book_guidance"]
        assert "genre" in guidance["use_catalog_fields"]
        assert "maps" in guidance["avoid_defaulting_to"]
    finally:
        session.close()


def test_book_titles_prewarm_independent_of_saturation_flag(monkeypatch) -> None:
    # Saturation off, titles on: titles are not gated by the saturation flag.
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "0")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        original_name = book.name
        result = session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        assert not result.consumed_turn
        titles = [
            record
            for record in session.engine.state.canon_records.values()
            if record.kind == "book_title"
        ]
        assert len(titles) == 1
        assert titles[0].attachment == {"kind": "prop", "entity_id": book.id}
        assert titles[0].title
        assert book.name == titles[0].title
        assert book.name != original_name
        assert book.details.get("title_materialized")
        # Saturation stayed off: no full pages or room flavor materialized.
        assert not any(
            record.kind in {"book", "room_flavor"}
            for record in session.engine.state.canon_records.values()
        )
    finally:
        session.close()


def test_saturation_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("WILDMAGIC_CANON_PREWARM_ENABLED", raising=False)
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "0")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        assert not session.engine.state.canon_records
    finally:
        session.close()


def test_read_after_title_prewarm_reuses_title(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "0")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        title = next(
            record
            for record in session.engine.state.canon_records.values()
            if record.kind == "book_title"
        )
        # Saturation is off, so the full pages materialize on demand at read time
        # and inherit the title already shown on the shelf.
        result = session.execute_command("read")
        assert result.success
        assert result.consumed_turn
        full = next(
            record
            for record in session.engine.state.canon_records.values()
            if record.kind == "book"
        )
        assert full.title == title.title
        assert book.name == full.title
    finally:
        session.close()


def test_book_prewarm_materializes_full_pages_after_title(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "4")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        session.execute_command("inspect")
        # Stage 1: the title lands first and names the book on the shelf.
        session.drain_canon_prewarm(block=True)
        title = next(
            record
            for record in session.engine.state.canon_records.values()
            if record.kind == "book_title"
        )
        full_id = f"canon_book_{book.id}"
        assert full_id not in session.engine.state.canon_records
        # Stage 2: now that the title exists, the full pages prewarm under the
        # canonical book id and inherit that title.
        session._enqueue_canon_prewarm()
        session.drain_canon_prewarm(block=True)
        full = session.engine.state.canon_records[full_id]
        assert full.kind == "book"
        assert full.title == title.title
        assert full.text
        assert book.name == full.title
    finally:
        session.close()


def test_book_pages_prewarm_without_saturation_flag(monkeypatch) -> None:
    # The book pipeline (titles + nearby pages) is always-on; full-page prewarm no
    # longer requires the saturation flag, so a book the player stands beside gets
    # its pages readied for an instant read.
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "0")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        full_id = f"canon_book_{book.id}"
        # Pump the queue: title first, then full pages — saturation stays off.
        for _ in range(4):
            session._enqueue_canon_prewarm()
            session.drain_canon_prewarm(block=True)
        assert full_id in session.engine.state.canon_records
        assert session.engine.state.canon_records[full_id].kind == "book"
        # Saturation off: no room flavor materialized despite the active pipeline.
        assert not any(
            record.kind == "room_flavor"
            for record in session.engine.state.canon_records.values()
        )
    finally:
        session.close()


def test_book_pipeline_readies_closest_book_first(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "0")
    # One at a time, so the first drain materializes only the nearest title.
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "1")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        px, py = session.engine.state.player.x, session.engine.state.player.y
        far = session.engine.spawn_prop("book", px + 4, py)
        assert far is not None
        far.details["book_seed"] = {
            "subjects": ["river custom"],
            "title_shape": "manual",
            "genre": "manual",
        }
        far.tags.add("book")
        near = _chamber_book(session.engine)  # the chamber book, adjacent to player
        near_title = f"canon_book_title_{near.id}"
        near_full = f"canon_book_{near.id}"
        far_title = f"canon_book_title_{far.id}"
        # Phase 1 (no titles yet): both books offer only a title job, nearest first.
        order = [j.record_id for j in session._canon_book_jobs()]
        assert order[0] == near_title
        assert far_title in order
        assert near_full not in order  # pages can't precede the title
        # Materialize the near title, then re-check: the near book's *pages* now
        # outrank the far book's title — the closest book is finished first.
        session._enqueue_canon_prewarm()
        session.drain_canon_prewarm(block=True)
        order = [j.record_id for j in session._canon_book_jobs()]
        assert order[0] == near_full
        assert order.index(near_full) < order.index(far_title)
    finally:
        session.close()


def test_read_reuses_prewarmed_pages_without_regenerating(monkeypatch) -> None:
    class CountingMockCanonProvider:
        name = "counting-mock"

        def __init__(self) -> None:
            from wildmagic.canon import MockCanonProvider

            self._mock = MockCanonProvider()
            self.full_book_calls = 0

        def materialize(self, context):
            from wildmagic.normalize import normalize_id

            if normalize_id(str(context.get("kind") or "")) == "book":
                self.full_book_calls += 1
            return self._mock.materialize(context)

    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "4")
    provider = CountingMockCanonProvider()
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        canon_provider=provider,
    )
    # Both the urgent and background canon routes use our counting provider.
    session.background_canon_provider = provider
    try:
        book = _chamber_book(session.engine)
        full_id = f"canon_book_{book.id}"
        session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)  # title
        session._enqueue_canon_prewarm()
        session.drain_canon_prewarm(block=True)  # full pages
        assert full_id in session.engine.state.canon_records
        assert provider.full_book_calls == 1

        result = session.execute_command("read")
        assert result.success
        # First read still costs a turn even though the pages were prewarmed.
        assert result.consumed_turn
        assert result.canon_materialization["reused"] is True
        # No second full-book generation: the prewarmed pages were reused.
        assert provider.full_book_calls == 1

        again = session.execute_command("read")
        assert again.success
        assert not again.consumed_turn
        assert provider.full_book_calls == 1
    finally:
        session.close()


def test_read_waits_for_in_flight_pages_prewarm_no_double_gen(monkeypatch) -> None:
    """Reading a book whose pages are already prewarming reuses that job instead of
    launching a second generation (the cause of slow reads with mismatched text)."""

    class CountingCanonProvider:
        name = "counting"

        def __init__(self) -> None:
            from wildmagic.canon import MockCanonProvider

            self._mock = MockCanonProvider()
            self.page_calls = 0

        def materialize(self, context):
            from wildmagic.normalize import normalize_id

            if normalize_id(str(context.get("kind") or "")) == "book":
                self.page_calls += 1
            return self._mock.materialize(context)

    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "4")
    provider = CountingCanonProvider()
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        canon_provider=provider,
        lore_provider_name="mock",
    )
    session.background_canon_provider = provider
    try:
        book = _chamber_book(session.engine)
        full_id = f"canon_book_{book.id}"
        # Get the title in place, then enqueue the pages prewarm WITHOUT draining it,
        # so it is in flight when we read.
        session._enqueue_canon_prewarm()
        session.drain_canon_prewarm(block=True)  # title
        session._enqueue_canon_prewarm()  # pages job now queued
        assert full_id in session._queued_canon_ids
        # Call _read_book directly so we bypass execute_command's own pre-drain and
        # exercise the in-flight reuse branch.
        success, technical_failure, record, _lines = session._read_book("")
        assert success and not technical_failure
        assert full_id in session.engine.state.canon_records
        assert record.get("reused") is True
        # The page text was generated exactly once (by the prewarm), not again here.
        assert provider.page_calls == 1
    finally:
        session.close()


def test_full_book_prewarm_replays_without_provider_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_LIMIT", "4")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        full_id = f"canon_book_{book.id}"
        session.execute_command("inspect")
        session.drain_canon_prewarm(block=True)
        session._enqueue_canon_prewarm()
        session.drain_canon_prewarm(block=True)
        session.execute_command("read")
        assert full_id in session.engine.state.canon_records
        replay_path = tmp_path / "full_book_prewarm.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched
    assert any(
        record["kind"] == "book"
        for record in replay_result.final_summary["canon_records"]
    )


def test_book_title_prewarm_replays_without_provider_call(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("WILDMAGIC_BOOK_TITLES", "1")
    monkeypatch.setenv("WILDMAGIC_CANON_PREWARM_ENABLED", "0")
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        session.execute_command("inspect")
        replay_path = tmp_path / "book_title_prewarm.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched
    assert any(
        record["kind"] == "book_title"
        for record in replay_result.final_summary["canon_records"]
    )


def test_read_book_materializes_title_pages_and_costs_turn() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        book = _chamber_book(session.engine)
        first = session.execute_command("read")
        assert first.success
        assert first.consumed_turn
        assert first.canon_materialization is not None
        assert first.canon_materialization["technical_failure"] is False
        record = next(
            r for r in session.engine.state.canon_records.values() if r.kind == "book"
        )
        assert record.attachment == {"kind": "prop", "entity_id": book.id}
        assert record.title
        # Materialized title becomes the book's in-world identity.
        assert book.name == record.title
        assert record.llm_choices.get("author")

        second = session.execute_command("read")
        assert second.success
        assert not second.consumed_turn
        assert second.canon_materialization["reused"] is True
        assert (
            sum(
                1
                for r in session.engine.state.canon_records.values()
                if r.kind == "book"
            )
            == 1
        )
    finally:
        session.close()


def test_read_book_failure_costs_no_turn_and_writes_no_canon() -> None:
    class FailingCanonProvider:
        name = "failing"

        def materialize(self, context):
            raise ValueError("the ink refuses")

    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider=FailingCanonProvider(),
    )
    try:
        _chamber_book(session.engine)
        result = session.execute_command("read")
        assert not result.success
        assert result.technical_failure
        assert not result.consumed_turn
        assert not session.engine.state.canon_records
    finally:
        session.close()


def test_book_pages_feed_lore_extraction_with_book_source() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
        lore_provider_name="mock",
    )
    try:
        _chamber_book(session.engine)
        result = session.execute_command("read")
        assert result.success
        session.drain_lore(block=True)
        book_promises = [
            p for p in session.engine.state.promises if p.source.startswith("book:")
        ]
        assert book_promises
        assert all(p.source_reply for p in book_promises)
    finally:
        session.close()


def test_book_claim_quota_clamps_extra_claims() -> None:
    import json as _json

    class ChattyLoreProvider:
        name = "chatty"

        def extract(self, context):
            claims = [
                {
                    "kind": "rumor",
                    "subject": f"written thing {index}",
                    "text": f"The author insists that written thing {index} waits east of the river.",
                    "status": "rumored",
                    "confidence": 0.6,
                    "salience": 2,
                    "tags": ["book_quota_test"],
                }
                for index in range(3)
            ]
            return _json.dumps({"claims": claims})

    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
        lore_provider=ChattyLoreProvider(),
    )
    try:
        _chamber_book(session.engine)
        result = session.execute_command("read")
        assert result.success
        session.drain_lore(block=True)
        book_promises = [
            p for p in session.engine.state.promises if p.source.startswith("book:")
        ]
        # CONTRACT claim_quota for books is 2; the third claim is dropped by the clamp.
        assert len(book_promises) == 2
    finally:
        session.close()


def _chamber_secret_slot(engine: GameEngine) -> dict:
    room = engine.room_profile_at(engine.state.player.x, engine.state.player.y)
    assert room is not None
    return room.secret_slots[0]


def test_room_context_never_exposes_secret_slots() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    room = engine.room_profile_at(engine.state.player.x, engine.state.player.y)
    assert room is not None
    assert room.secret_slots  # the chamber has a deterministic secret
    assert "secret_slots" not in room.to_public_dict()
    assert "secret_slots" in room.to_public_dict(include_secrets=True)
    context = engine.context_for_llm("look around")
    assert "secret_slots" not in (context.get("current_room") or {})


def test_investigate_clue_then_anchor_opens_secret() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        slot = _chamber_secret_slot(session.engine)
        turn_before = session.engine.state.turn

        sweep = session.execute_command("investigate")
        assert sweep.success
        assert session.engine.state.turn == turn_before + 1  # plain difficulty = 1 turn
        assert slot["status"] == "clued"
        assert slot["anchor"]
        assert slot["reward"]["name"]
        clue = session.engine.state.canon_records[slot["clue_record"]]
        assert clue.kind == "investigation"
        assert slot["anchor"] in clue.text  # the clue points at the anchor

        # Sweeping again retells the clue for free.
        again = session.execute_command("investigate")
        assert again.success
        assert not again.consumed_turn
        assert again.canon_materialization["reused"] is True

        # Investigating the clued anchor opens the compartment deterministically.
        reward_name = slot["reward"]["name"]
        reveal = session.execute_command(f"investigate {slot['anchor']}")
        assert reveal.success
        assert reveal.consumed_turn
        assert slot["status"] == "opened"
        assert session.engine.state.inventory.get(reward_name, 0) >= 1
        open_record = session.engine.state.canon_records[
            f"canon_secret_open_{slot['id']}"
        ]
        assert open_record.source == "engine"
        assert reward_name in open_record.text
    finally:
        session.close()


def test_investigate_without_secret_never_yields_rewards() -> None:
    """Exit gate: where secret_present=false, no investigation can produce loot."""
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        room = session.engine.room_profile_at(
            session.engine.state.player.x, session.engine.state.player.y
        )
        room.secret_slots[:] = []  # no secret placed by the engine
        inventory_before = dict(session.engine.state.inventory)

        sweep = session.execute_command("investigate")
        assert sweep.success
        assert sweep.consumed_turn
        record = next(
            r
            for r in session.engine.state.canon_records.values()
            if r.kind == "investigation"
        )
        assert record.engine_choices["secret_present"] is False

        # Spam-focusing every prop in the room yields details at most — never loot.
        for entity in list(session.engine.state.entities.values()):
            if entity.kind == "prop":
                session.execute_command(f"investigate {entity.name}")
        assert session.engine.state.inventory == inventory_before
    finally:
        session.close()


def test_investigate_failure_costs_no_turn_and_leaves_secret_hidden() -> None:
    class FailingCanonProvider:
        name = "failing"

        def materialize(self, context):
            raise ValueError("the dust refuses")

    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider=FailingCanonProvider(),
    )
    try:
        slot = _chamber_secret_slot(session.engine)
        result = session.execute_command("investigate")
        assert not result.success
        assert result.technical_failure
        assert not result.consumed_turn
        assert slot.get("status") is None or slot.get("status") == ""
    finally:
        session.close()


def test_investigate_full_sequence_replays(tmp_path) -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        slot = _chamber_secret_slot(session.engine)
        assert session.execute_command("investigate").success
        assert session.execute_command(f"investigate {slot['anchor']}").success
        reward_name = slot["reward"]["name"]
        assert session.engine.state.inventory.get(reward_name, 0) >= 1
        replay_path = tmp_path / "investigate.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched


def test_targeted_investigate_materializes_detail_tiers() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        room = engine.room_profile_at(engine.state.player.x, engine.state.player.y)
        room.secret_slots[:] = []  # keep this test about details, not secrets
        goblin = next(
            e for e in engine.state.entities.values() if e.name == "test goblin"
        )
        session.execute_command(
            "open"
        )  # the chamber door hides the goblin until opened

        far = session.execute_command(f"investigate {goblin.id}")
        assert far.success and far.consumed_turn
        far_record = engine.state.canon_records[f"canon_detail_{goblin.id}_far"]
        assert far_record.kind == "creature_detail"
        # One weakness per study, engine-chosen, woven into the prose.
        hint = far_record.engine_choices["weakness_hint"]
        assert hint["kind"] in {"mechanical", "flavor"}
        # Far look reused for free.
        again = session.execute_command(f"investigate {goblin.id}")
        assert again.success and not again.consumed_turn

        # Walking up earns the close tier; it supersedes the far record.
        player = engine.state.player
        player.x, player.y = goblin.x - 1, goblin.y
        close = session.execute_command(f"investigate {goblin.id}")
        assert close.success and close.consumed_turn
        assert f"canon_detail_{goblin.id}_close" in engine.state.canon_records
        # Prose reached the message log (display fix).
        assert any(
            far_record.text in m for m in [str(m) for m in engine.state.messages]
        )
    finally:
        session.close()


def test_adjacent_prop_study_surfaces_hidden_clue() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        slot = _chamber_secret_slot(engine)
        book = _chamber_book(engine)  # the chamber's only prop => the anchor
        result = session.execute_command(f"investigate {book.id}")
        assert result.success
        assert slot["status"] == "clued"
        # And the clued anchor then opens via the normal reveal.
        reveal = session.execute_command(f"investigate {slot['anchor']}")
        assert reveal.success
        assert slot["status"] == "opened"
        assert engine.state.inventory.get(slot["reward"]["name"], 0) >= 1
    finally:
        session.close()


def test_canon_retells_do_not_flood_the_log() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        room = engine.room_profile_at(engine.state.player.x, engine.state.player.y)
        room.secret_slots[:] = []
        first = session.execute_command("investigate")
        assert first.success
        record = next(
            r for r in engine.state.canon_records.values() if r.kind == "investigation"
        )
        count_after_first = sum(
            1 for m in engine.state.messages if str(m) == record.text
        )
        assert count_after_first == 1
        # Spamming the key retells for free without re-logging the prose.
        for _ in range(5):
            again = session.execute_command("investigate")
            assert again.success and not again.consumed_turn
        assert sum(1 for m in engine.state.messages if str(m) == record.text) == 1
    finally:
        session.close()


def test_unmatched_target_costs_nothing() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        result = session.execute_command("investigate the moon")
        assert not result.success
        assert not result.consumed_turn
    finally:
        session.close()


def test_sweep_decoration_spawns_from_engine_menu() -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        room = engine.room_profile_at(engine.state.player.x, engine.state.player.y)
        room.secret_slots[:] = []
        props_before = {
            e.id for e in engine.state.entities.values() if e.kind == "prop"
        }
        result = session.execute_command("investigate")
        assert result.success
        record = next(
            r for r in engine.state.canon_records.values() if r.kind == "investigation"
        )
        new_props = [
            e
            for e in engine.state.entities.values()
            if e.kind == "prop" and e.id not in props_before
        ]
        if record.llm_choices.get("decoration_template"):
            assert len(new_props) == 1
            allowed = {
                o["template"] for o in record.engine_choices["decoration_options"]
            }
            # The spawned prop came from the engine's menu, at the engine's spot.
            assert new_props[0].x == record.engine_choices["decoration_spot"][0]
            assert new_props[0].y == record.engine_choices["decoration_spot"][1]
            assert allowed
            # Sweeping again never duplicates it.
            session.execute_command("investigate")
            assert (
                sum(1 for e in engine.state.entities.values() if e.kind == "prop")
                == len(props_before) + 1
            )
        else:
            assert not new_props
    finally:
        session.close()


def test_detail_and_decoration_replays(tmp_path) -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        engine = session.engine
        goblin = next(
            e for e in engine.state.entities.values() if e.name == "test goblin"
        )
        session.execute_command("open")
        assert session.execute_command(f"investigate {goblin.id}").success
        assert session.execute_command(
            "investigate"
        ).success  # clue stage (chamber secret)
        replay_path = tmp_path / "detail.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched


def test_read_book_replays_without_provider_call(tmp_path) -> None:
    session = GameSession(
        seed=7,
        scenario="test_chamber",
        provider_name="mock",
        canon_provider_name="mock",
    )
    try:
        _chamber_book(session.engine)
        result = session.execute_command("read")
        assert result.success
        replay_path = tmp_path / "read.json"
        save_replay(session, replay_path)
    finally:
        session.close()

    replay_result = run_replay(replay_path)
    assert replay_result.matched
