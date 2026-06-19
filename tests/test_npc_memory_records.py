from wildmagic.actions import describe_state
from wildmagic.engine import GameEngine, TURNS_PER_DAY
from wildmagic.models import NPCMemoryRecord, NPCProfile


def test_dialogue_context_splits_memory_buckets_and_frames_provenance() -> None:
    profile = NPCProfile(
        entity_id="npc1",
        name="Maren",
        role="witness",
        backstory="Keeps careful track of town news.",
    )
    profile.add_memory(
        NPCMemoryRecord(
            id="m1",
            claim="The player cut down an Imperial road-captain.",
            provenance="firsthand",
            bucket="observation",
            subject_refs=["player"],
        ),
        mirror_legacy=False,
    )
    profile.add_memory(
        NPCMemoryRecord(
            id="m2",
            claim="The old oak wakes after midnight.",
            provenance="overheard",
            bucket="overheard",
            source_name="Quill",
            confidence=0.7,
        ),
        mirror_legacy=False,
    )
    profile.add_memory(
        NPCMemoryRecord(
            id="m3",
            claim="The player carries a curse that makes doors whisper.",
            provenance="gossip",
            bucket="gossip",
            source_name="Talla",
            confidence=0.4,
        ),
        mirror_legacy=False,
    )

    context = profile.to_dialogue_context()

    assert context["things_i_personally_witnessed"][0]["claim"].startswith(
        "The player cut down"
    )
    assert "personally" in context["things_i_personally_witnessed"][0]["frame"]
    assert context["things_i_overheard"][0]["confidence"] == "hearsay"
    assert "Quill" in context["things_i_overheard"][0]["frame"]
    assert context["gossip_i_have_heard"][0]["confidence"] == "rumor"
    assert "Talla" in context["gossip_i_have_heard"][0]["frame"]


def test_dialogue_context_uses_structured_memory_without_legacy_duplicates() -> None:
    profile = NPCProfile(
        entity_id="npc1",
        name="Maren",
        role="witness",
        backstory="Keeps careful track of town news.",
    )
    profile.add_memory(
        NPCMemoryRecord(
            id="m1",
            claim="The player cut down an Imperial road-captain.",
            provenance="firsthand",
            bucket="observation",
            subject_refs=["player"],
        )
    )
    profile.record_exchange("player", "Did you hear that?")
    profile.record_exchange("npc", "I heard enough.")

    context = profile.to_dialogue_context()

    assert "things_i_have_noticed" not in context
    assert "recent_conversation" not in context
    assert profile.memory  # legacy mirror remains available outside dialogue context
    assert context["things_i_personally_witnessed"][0]["claim"] == (
        "The player cut down an Imperial road-captain."
    )
    assert context["conversation_memory"]["recent_exchanges"] == profile.conversation


def test_witnessed_deed_writes_neutral_typed_memory_and_legacy_mirror() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    witness = engine.spawn_npc(
        "Witness",
        "w",
        player.x + 1,
        player.y,
        role="witness",
        backstory="Saw what happened.",
    )

    deed = engine.record_deed(
        "killed_imperials",
        magnitude=0.2,
        summary="cut down an Imperial road-captain",
        at=(player.x, player.y),
        target_tags=["empire"],
    )

    assert deed is not None
    profile = engine.state.npc_profiles[witness.id]
    record = profile.memory_records[-1]
    assert record.claim == "The player cut down an Imperial road-captain."
    assert record.provenance == "firsthand"
    assert record.bucket == "observation"
    assert engine.state.player_soul_id in record.subject_refs
    assert any("I saw the player cut down" in note for note in profile.memory)


def test_edit_memory_add_creates_implanted_observation_record() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    npc = engine.spawn_npc(
        "Forgetful Guard",
        "g",
        player.x + 1,
        player.y,
        role="guard",
        backstory="Was certain a moment ago.",
    )
    engine._apply_effect(
        {
            "type": "edit_memory",
            "target": npc.id,
            "op": "add",
            "subject": "the caster",
            "text": "The player is an old friend.",
            "strength": 4,
        }
    )

    profile = engine.state.npc_profiles[npc.id]
    record = profile.memory_records[-1]
    assert record.provenance == "implanted"
    assert record.bucket == "observation"
    assert record.subtype == "false_memory"
    assert not record.shareable
    assert engine.state.player_soul_id in record.subject_refs
    context = profile.to_dialogue_context()
    assert context["things_i_personally_witnessed"][0]["claim"] == (
        "The player is an old friend."
    )
    assert "magic shaped it" in context["things_i_personally_witnessed"][0]["frame"]


def test_player_memory_multiplier_uses_structured_subject_refs() -> None:
    profile = NPCProfile(
        entity_id="npc1",
        name="Quill",
        role="gossip",
        backstory="Hears almost everything.",
    )
    profile.add_memory(
        NPCMemoryRecord(
            id="unrelated",
            claim="A brass moth nested in the roof.",
            provenance="firsthand",
            bucket="observation",
            subject_refs=["entity:moth"],
        ),
        mirror_legacy=False,
    )
    assert profile.player_memory_multiplier("player") == 1.0

    profile.add_memory(
        NPCMemoryRecord(
            id="rumor",
            claim="The player saved a road shrine.",
            provenance="gossip",
            bucket="gossip",
            subject_refs=["player"],
            confidence=0.5,
        ),
        mirror_legacy=False,
    )
    assert 1.0 < profile.player_memory_multiplier("player") < 1.5


def test_legacy_memory_multiplier_fallback_only_without_structured_records() -> None:
    legacy = NPCProfile(
        entity_id="legacy",
        name="Legacy",
        role="old save",
        backstory="Loaded from an older run.",
    )
    legacy.memory.append("I will never forget what you did for me.")
    assert legacy.player_memory_multiplier("player") == 1.5

    structured = NPCProfile(
        entity_id="structured",
        name="Structured",
        role="new save",
        backstory="Has structured local memory.",
    )
    structured.add_memory(
        NPCMemoryRecord(
            id="non-player",
            claim="The old oak wakes after midnight.",
            provenance="firsthand",
            bucket="observation",
            subject_refs=["place:old-oak"],
        )
    )
    assert structured.memory
    assert structured.player_memory_multiplier("player") == 1.0


def _spawn_gossip_pair(engine: GameEngine):
    player = engine.state.player
    source = engine.spawn_npc(
        "Maren",
        "m",
        player.x + 1,
        player.y,
        role="witness",
        backstory="Saw the thing happen.",
    )
    receiver = engine.spawn_npc(
        "Quill",
        "q",
        player.x + 2,
        player.y,
        role="listener",
        backstory="Hears the thing later.",
    )
    for edge in engine.state.gossip_edges.values():
        edge.contact_chance = 1.0
    return source, receiver


def test_same_zone_npcs_get_placeholder_directed_gossip_edges() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    player = engine.state.player
    first = engine.spawn_npc(
        "First",
        "f",
        player.x + 1,
        player.y,
        role="townsfolk",
        backstory="First.",
    )
    second = engine.spawn_npc(
        "Second",
        "s",
        player.x + 2,
        player.y,
        role="townsfolk",
        backstory="Second.",
    )
    third = engine.spawn_npc(
        "Third",
        "t",
        player.x + 3,
        player.y,
        role="townsfolk",
        backstory="Third.",
    )

    ids = {first.id, second.id, third.id}
    edges = list(engine.state.gossip_edges.values())
    assert len(edges) == 6
    assert {(edge.from_id, edge.to_id) for edge in edges} == {
        (a, b) for a in ids for b in ids if a != b
    }
    assert all(edge.relationship == "zone" for edge in edges)
    assert all(
        edge.zone == (engine.state.zone_x, engine.state.zone_y) for edge in edges
    )


def test_daily_gossip_spreads_shareable_memory_as_gossip() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, receiver = _spawn_gossip_pair(engine)
    source_profile = engine.state.npc_profiles[source.id]
    source_profile.add_memory(
        NPCMemoryRecord(
            id="source-memory",
            claim="The player rescued a road shrine.",
            provenance="firsthand",
            bucket="observation",
            subtype="witnessed_deed",
            subject="the player",
            subject_refs=[engine.state.player_soul_id],
            tags=["deed"],
            turn=engine.state.turn,
            confidence=1.0,
            salience=4,
            shareable=True,
            source_event_id="deed:road-shrine",
        )
    )

    assert engine.spread_daily_gossip(day=2) == 1

    receiver_profile = engine.state.npc_profiles[receiver.id]
    gossip = [
        record
        for record in receiver_profile.memory_records
        if record.provenance == "gossip"
    ]
    assert len(gossip) == 1
    assert gossip[0].claim == "The player rescued a road shrine."
    assert gossip[0].source_name == "Maren"
    assert gossip[0].source_event_id == "deed:road-shrine"
    assert engine.state.player_soul_id in gossip[0].subject_refs
    context = receiver_profile.to_dialogue_context()
    assert context["gossip_i_have_heard"][0]["claim"] == (
        "The player rescued a road shrine."
    )
    assert "Maren" in context["gossip_i_have_heard"][0]["frame"]


def test_daily_gossip_spread_is_idempotent_per_day() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, receiver = _spawn_gossip_pair(engine)
    engine.state.npc_profiles[source.id].add_memory(
        NPCMemoryRecord(
            id="once-per-day",
            claim="The player frightened an Imperial tax clerk.",
            provenance="firsthand",
            bucket="observation",
            subject_refs=[engine.state.player_soul_id],
            salience=4,
            shareable=True,
            source_event_id="deed:tax-clerk",
        )
    )

    assert engine.spread_daily_gossip(day=2) == 1
    assert engine.spread_daily_gossip(day=2) == 0
    assert len(engine.state.npc_profiles[receiver.id].memory_records) == 1
    assert engine.state.gossip_spread_days == {2}


def test_conversation_summary_spreads_only_after_conversation_gate() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, receiver = _spawn_gossip_pair(engine)
    source_profile = engine.state.npc_profiles[source.id]
    source_profile.add_memory(
        NPCMemoryRecord(
            id="small-talk",
            claim="The player and Maren discussed the weather.",
            provenance="firsthand",
            bucket="conversation",
            subtype="conversation_summary",
            subject_refs=[engine.state.player_soul_id],
            salience=1,
            shareable=True,
            source_event_id="conversation:small-talk",
        )
    )
    source_profile.add_memory(
        NPCMemoryRecord(
            id="threat",
            claim="The player warned Maren that the Empire would raid at dusk.",
            provenance="firsthand",
            bucket="conversation",
            subtype="conversation_summary",
            subject_refs=[engine.state.player_soul_id],
            tags=["warning", "empire"],
            salience=3,
            shareable=True,
            source_event_id="conversation:warning",
        )
    )

    assert engine.spread_daily_gossip(day=2) == 1

    receiver_records = engine.state.npc_profiles[receiver.id].memory_records
    assert len(receiver_records) == 1
    assert receiver_records[0].claim == (
        "The player warned Maren that the Empire would raid at dusk."
    )


def test_intimate_memory_requires_private_or_trusted_edge_to_spread() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, receiver = _spawn_gossip_pair(engine)
    source_profile = engine.state.npc_profiles[source.id]
    source_profile.add_memory(
        NPCMemoryRecord(
            id="intimate",
            claim="The player confessed fear of the river saints.",
            provenance="firsthand",
            bucket="conversation",
            subtype="conversation_summary",
            subject_refs=[engine.state.player_soul_id],
            salience=4,
            privacy="intimate",
            shareable=True,
            source_event_id="conversation:intimate",
        )
    )

    assert engine.spread_daily_gossip(day=2) == 0
    assert not engine.state.npc_profiles[receiver.id].memory_records

    for edge in engine.state.gossip_edges.values():
        edge.privacy_bias = 0.75
    assert engine.spread_daily_gossip(day=3) == 1
    assert (
        engine.state.npc_profiles[receiver.id].memory_records[-1].privacy == "intimate"
    )


def test_implanted_memory_spreads_only_when_shareable() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, receiver = _spawn_gossip_pair(engine)
    source_profile = engine.state.npc_profiles[source.id]
    source_profile.add_memory(
        NPCMemoryRecord(
            id="private-false-memory",
            claim="The player saved Maren from a glass wolf.",
            provenance="implanted",
            bucket="observation",
            subtype="false_memory",
            subject_refs=[engine.state.player_soul_id],
            tags=["implanted"],
            salience=4,
            shareable=False,
            source_event_id="spell:false-memory",
        )
    )
    assert engine.spread_daily_gossip(day=2) == 0
    assert not engine.state.npc_profiles[receiver.id].memory_records

    source_profile.memory_records[0].shareable = True
    assert engine.spread_daily_gossip(day=3) == 1
    receiver_record = engine.state.npc_profiles[receiver.id].memory_records[-1]
    assert receiver_record.provenance == "gossip"
    assert "implanted_origin" in receiver_record.tags


def test_shareable_edit_memory_effect_can_enter_gossip_spread() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, receiver = _spawn_gossip_pair(engine)

    engine._apply_effect(
        {
            "type": "edit_memory",
            "target": source.id,
            "op": "add",
            "subject": "the caster",
            "text": "The player saved Maren from a glass wolf.",
            "strength": 4,
            "shareable": True,
            "privacy": "public",
        }
    )

    source_record = engine.state.npc_profiles[source.id].memory_records[-1]
    assert source_record.provenance == "implanted"
    assert source_record.shareable is True
    assert source_record.privacy == "public"
    assert engine.spread_daily_gossip(day=2) == 1
    receiver_record = engine.state.npc_profiles[receiver.id].memory_records[-1]
    assert receiver_record.provenance == "gossip"
    assert receiver_record.claim == "The player saved Maren from a glass wolf."


def test_daily_gossip_spread_is_deterministic() -> None:
    def run() -> list[dict[str, object]]:
        engine = GameEngine(seed=9, scenario="test_chamber")
        source, receiver = _spawn_gossip_pair(engine)
        for edge in engine.state.gossip_edges.values():
            edge.contact_chance = 1.0
        engine.state.npc_profiles[source.id].add_memory(
            NPCMemoryRecord(
                id="deterministic-memory",
                claim="The player broke an Imperial seal.",
                provenance="firsthand",
                bucket="observation",
                subject_refs=[engine.state.player_soul_id],
                salience=3,
                shareable=True,
                source_event_id="deed:seal",
            )
        )
        engine.spread_daily_gossip(day=2)
        return [
            record.to_dict()
            for record in engine.state.npc_profiles[receiver.id].memory_records
        ]

    assert run() == run()


def test_daily_tick_spreads_gossip_before_bond_drift() -> None:
    def run(spread: bool) -> float:
        engine = GameEngine(seed=7, scenario="test_chamber")
        engine.state.legend_ledger.add_tag(
            engine.state.player_soul_id, "liberator", 2.0
        )
        source, receiver = _spawn_gossip_pair(engine)
        engine.state.npc_profiles[receiver.id].traits.append("downtrodden")
        engine.state.npc_profiles[source.id].add_memory(
            NPCMemoryRecord(
                id="liberation",
                claim="The player freed a prisoner from an Imperial chain.",
                provenance="firsthand",
                bucket="observation",
                subject_refs=[engine.state.player_soul_id],
                tags=["deed"],
                salience=4,
                shareable=spread,
                source_event_id="deed:liberation",
            )
        )
        engine.state.turn += TURNS_PER_DAY
        engine._maybe_run_daily_tick()
        return engine.state.npc_profiles[receiver.id].bond.loyalty

    assert run(spread=True) > run(spread=False)


def test_inspect_exposes_memory_categories_and_gossip_edges() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    source, _receiver = _spawn_gossip_pair(engine)
    engine.state.npc_profiles[source.id].add_memory(
        NPCMemoryRecord(
            id="visible-memory",
            claim="The player warned Maren about the river.",
            provenance="firsthand",
            bucket="observation",
            subject_refs=[engine.state.player_soul_id],
        )
    )

    lines = describe_state(engine)

    assert any(line.startswith("NPC memory:") for line in lines)
    assert any(line.startswith("Gossip edges:") for line in lines)
