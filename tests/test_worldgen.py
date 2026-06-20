from __future__ import annotations

from collections import deque

from wildmagic.engine import GameEngine
from wildmagic.factions import Faction, FactionLedger
from wildmagic.models import BLOCKING_TILES
from wildmagic.worldgen import (
    OLD_KINGDOM_IDS,
    REALM_TEMPLATES,
    roll_world,
    scenario_uses_world_map,
    seed_factions_from_world,
    start_zone_for_scenario,
    world_map_strings,
)


WORLD_SCENARIOS = ("frontier", "town", "bazaar", "warren", "archive")


def _adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def _reachable_edges(engine: GameEngine) -> set[str]:
    state = engine.state
    player = state.player
    queue = deque([(player.x, player.y)])
    seen = {(player.x, player.y)}
    edges: set[str] = set()
    while queue:
        x, y = queue.popleft()
        if x == 0:
            edges.add("west")
        if x == state.width - 1:
            edges.add("east")
        if y == 0:
            edges.add("north")
        if y == state.height - 1:
            edges.add("south")
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not (
                0 <= nx < state.width and 0 <= ny < state.height
            ):
                continue
            if state.tiles[ny][nx] in BLOCKING_TILES:
                continue
            seen.add((nx, ny))
            queue.append((nx, ny))
    return edges


def test_roll_world_is_deterministic_and_serializable() -> None:
    world = roll_world(7)
    assert roll_world(7).to_dict() == world.to_dict()
    assert type(world).from_dict(world.to_dict()).to_dict() == world.to_dict()


def test_world_roll_places_core_realms_with_hard_invariants() -> None:
    world = roll_world(11)
    assert set(world.placements) == {"vigovia", "threen", *OLD_KINGDOM_IDS}
    assert world.rival_realm_id in OLD_KINGDOM_IDS
    assert world.placements["vigovia"].role == "founding"
    assert world.placements["threen"].role == "proxy"
    assert world.placements[world.rival_realm_id].role == "rival"

    min_x, min_y, max_x, max_y = world.bounds
    assert min_x <= 0 <= max_x
    assert min_y <= 0 <= max_y
    assert world.contains(0, 0)

    seen: set[tuple[int, int]] = set()
    for placement in world.placements.values():
        assert placement.cells
        for cell in placement.cells:
            assert world.contains(*cell)
            assert cell not in seen
            seen.add(cell)

    vigovia = world.placements["vigovia"]
    for placement in world.placements.values():
        if placement.role == "conquered":
            assert any(_adjacent(a, b) for a in placement.cells for b in vigovia.cells)
    rival = world.placements[world.rival_realm_id]
    assert not any(_adjacent(a, b) for a in rival.cells for b in vigovia.cells)


def test_start_zones_are_distinct_and_not_origin() -> None:
    world = roll_world(7)
    scenarios = ("town", "bazaar", "warren", "archive")
    starts = {
        scenario: start_zone_for_scenario(world, scenario) for scenario in scenarios
    }
    assert len(set(starts.values())) == len(starts)
    assert all(zone != (0, 0) for zone in starts.values())
    assert all(world.contains(*zone) for zone in starts.values())
    assert start_zone_for_scenario(world, "frontier") != (0, 0)


def test_seed_factions_from_world_preserves_empire_primary() -> None:
    world = roll_world(7)
    ledger = seed_factions_from_world(world)
    assert ledger.get("empire") is not None
    assert ledger.primary("empire").id == "empire"
    assert ledger.get("rebellion") is not None
    assert {f.kind for f in ledger.factions.values()} >= {
        "empire_core",
        "conquered",
        "proxy",
        "rival",
        "resistance",
    }


def test_primary_prefers_canonical_kind_before_id() -> None:
    ledger = FactionLedger(
        {
            "brall": Faction(id="brall", name="Brall", kind="conquered"),
            "empire": Faction(id="empire", name="Empire", kind="empire_core"),
        }
    )
    assert ledger.primary("empire").id == "empire"


def test_world_map_strings_marks_current_zone_and_legend() -> None:
    world = roll_world(7)
    current = start_zone_for_scenario(world, "town")
    lines = world_map_strings(world, current, {current})
    text = "\n".join(lines)
    assert "@" in text
    assert "*" in text
    assert REALM_TEMPLATES[world.rival_realm_id].name in text


def test_world_bearing_engine_starts_have_world_maps() -> None:
    for scenario in WORLD_SCENARIOS:
        engine = GameEngine(seed=7, scenario=scenario)
        assert scenario_uses_world_map(scenario)
        assert engine.state.world_map is not None
        assert (engine.state.zone_x, engine.state.zone_y) == start_zone_for_scenario(
            engine.state.world_map, scenario
        )
        assert engine.state.faction_ledger.primary("empire").id == "empire"


def test_world_start_scenarios_have_reachable_zone_edges() -> None:
    for scenario in WORLD_SCENARIOS:
        engine = GameEngine(seed=7, scenario=scenario)
        assert _reachable_edges(engine) == {"west", "east", "north", "south"}


def test_model_contexts_include_current_realm() -> None:
    engine = GameEngine(seed=7, scenario="bazaar")
    npc = next(
        entity
        for entity in engine.state.entities.values()
        if entity.id in engine.state.npc_profiles
    )
    dialogue = engine.dialogue_context_for_llm(npc, "hello")
    trade = engine.trade_context_for_llm(npc, "want to trade?", "Maybe.")
    lore = engine.lore_extraction_context(npc, "hello", "Welcome.")
    town = engine._build_town_context(engine.state.zone_x, engine.state.zone_y)

    assert dialogue["scene"]["current_realm"]["realm_id"] is not None
    assert trade["scene"]["current_realm"]["realm_id"] is not None
    assert lore["current_realm"]["realm_id"] is not None
    assert town["current_realm"]["realm_id"] is not None


def test_synthetic_scenarios_keep_phase0_factions() -> None:
    engine = GameEngine(seed=7, scenario="test_chamber")
    assert engine.state.world_map is None
    assert sorted(engine.state.faction_ledger.factions) == ["empire", "rebellion"]


def test_world_map_bounds_are_hard_traversal_edges() -> None:
    engine = GameEngine(seed=7, scenario="frontier")
    world = engine.state.world_map
    assert world is not None
    min_x, _min_y, _max_x, _max_y = world.bounds
    player = engine.state.player
    engine.state.zone_x = min_x
    player.x = 0
    player.y = engine.state.height // 2

    assert engine.attempt_player_move(-1, 0) is False
    assert engine.state.zone_x == min_x
    assert any("known world" in message for message in engine.state.messages)
