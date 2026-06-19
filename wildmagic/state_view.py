"""Read-only state views for wild magic's many consumers.

`GameState` (owned by `GameEngine`) is the single source of truth. This module is the
read-only surface that turns that authoritative state into compact, stable packets for the
systems that must *see* the world without mutating it:

- the resolver context the LLM receives (`spell_context_view`)
- the structured run/replay summary (`replay_summary_view`)
- CLI/GUI inspection (`inspection_view`)

It is deliberately *pure reads*: every function here takes a live `GameEngine` (or one of its
records) and returns plain dicts/lists. Nothing in this module writes to state. That invariant
is what lets the resolver, replay, and inspection share one vocabulary for "what the world
contains" while the engine keeps sole authority over mutation. See
`docs/WILD_MAGIC_STATE_SURFACE_PLAN.md` (Stage 2) for the staged design.

The small `*_card` builders are the single home for each public-dict shape. Larger views
compose them. Output shapes are preserved verbatim from the previous inline assembly so the
resolver, replay round-trip, targeting, and semantics tests keep passing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .capabilities import select_cards, selected_effect_types
from .curses import curse_card
from .game_data import FOCUS_SPECS
from .normalize import normalize_id
from .models import (
    CharacterProfile,
    DAMAGE_TYPES,
    FLOOR,
    MECHANICAL_STATUSES,
    TILE_NAMES,
    TILE_TAGS,
)
from .spell_contract import SUPPORTED_COSTS
from .templates import creature_template_ids, item_template_ids

if TYPE_CHECKING:
    from .capabilities import CapabilityCard
    from .engine import GameEngine
    from .models import Entity, RoomProfile


# ----------------------------------------------------------------------------------------
# Card builders: one home per public-dict shape. Each takes the live engine for the lookups
# it needs (visibility, line of sight, tile/room context) and returns a plain dict.
# ----------------------------------------------------------------------------------------
def entity_card(entity: "Entity", engine: "GameEngine") -> dict[str, Any]:
    """Resolver-facing public view of a visible actor."""
    return entity.to_public_dict()


def item_card(entity: "Entity", engine: "GameEngine") -> dict[str, Any]:
    """Resolver-facing public view of an item lying on the floor."""
    return {
        "id": entity.id,
        "name": entity.name,
        "item_type": entity.item_type,
        "material": entity.material,
        "quantity": entity.quantity,
        "x": entity.x,
        "y": entity.y,
        "tags": sorted(entity.tags),
        **({"traits": list(entity.traits)} if entity.traits else {}),
    }


def resolve_foci(engine: "GameEngine") -> list[dict[str, Any]]:
    """The spell foci the controlled entity has marked, resolved to resolver-facing flavor.

    A focus is a mark on an already-equipped item (`Entity.focus_slots`): for each marked,
    occupied slot we read the equipped item, then enrich it with any discovered `item_lore`
    description and curated `FOCUS_SPECS` metadata (themes/power, plus a description fallback).
    Returns a list (empty when nothing is marked) so multiple simultaneous foci need no
    call-site changes. Power is carried but does not yet scale magnitudes."""
    player = engine.state.player
    foci: list[dict[str, Any]] = []
    for slot in player.focus_slots:
        item = player.equipment.get(slot)
        if not item:
            continue
        lore = engine.state.item_lore.get(normalize_id(item)) or {}
        spec = FOCUS_SPECS.get(item.strip().lower()) or {}
        entry: dict[str, Any] = {
            "name": lore.get("display_name") or item,
            "slot": slot,
        }
        description = lore.get("description") or spec.get("description") or ""
        if description:
            entry["description"] = description
        if spec.get("themes"):
            entry["themes"] = list(spec["themes"])
        if spec.get("power") is not None:
            entry["power"] = int(spec["power"])
        foci.append(entry)
    return foci


def room_card(
    room: "RoomProfile", engine: "GameEngine", *, include_secrets: bool = False
) -> dict[str, Any]:
    """Public view of a room. Secret slots stay out by default; only summaries
    (which the model never sees) pass include_secrets=True."""
    return room.to_public_dict(include_secrets=include_secrets)


def tile_card(x: int, y: int, engine: "GameEngine") -> dict[str, Any]:
    """Detail card for a single tile, including any active duration, tags, and the
    room it belongs to. Pure assembly — the caller decides which tiles are worth
    surfacing (see `nearby_tile_details`)."""
    tile = engine.tile_at(x, y)
    key = engine.tile_key(x, y)
    detail: dict[str, Any] = {
        "x": x,
        "y": y,
        "tile": tile,
        "name": TILE_NAMES.get(tile, "strange"),
        "tags": sorted(engine.tile_tags_at(x, y)),
        "duration": engine.state.tile_durations.get(key),
    }
    flow = engine.state.tile_flows.get(key)
    if isinstance(flow, dict):
        detail["flow"] = dict(flow)
    room = engine.room_profile_at(x, y)
    if room is not None:
        detail["room"] = {
            "id": room.id,
            "type": room.room_type,
            "era": room.era,
            "condition": room.condition,
        }
    return detail


def nearby_tile_details(engine: "GameEngine", radius: int = 5) -> list[dict[str, Any]]:
    """Visible, *noteworthy* tiles around the player: non-floor terrain, anything with
    an active duration, or anything carrying tile tags. Capped to keep the packet small."""
    player = engine.state.player
    details: list[dict[str, Any]] = []
    for y in range(player.y - radius, player.y + radius + 1):
        for x in range(player.x - radius, player.x + radius + 1):
            if not engine.in_bounds(x, y):
                continue
            if not engine.is_visible(x, y):
                continue
            tile = engine.tile_at(x, y)
            key = engine.tile_key(x, y)
            duration = engine.state.tile_durations.get(key)
            if (
                tile != FLOOR
                or duration is not None
                or key in engine.state.tile_tags
                or key in engine.state.tile_flows
            ):
                details.append(tile_card(x, y, engine))
    return details[:60]


def selected_target_card(engine: "GameEngine") -> dict[str, Any]:
    """Compact description of the marked square for the resolver. Callers must guard with
    `engine.has_target()` so the coordinates are real."""
    tx, ty = engine.state.target_x, engine.state.target_y
    player = engine.state.player
    occupant = engine.selected_target_entity()
    tile = engine.tile_at(tx, ty)
    return {
        "x": tx,
        "y": ty,
        "tile": TILE_NAMES.get(tile, tile),
        "distance": max(abs(tx - player.x), abs(ty - player.y)),
        "has_line_of_sight": engine.has_line_of_sight(player.x, player.y, tx, ty),
        "entity_id": occupant.id if occupant is not None else None,
        "entity_name": occupant.name if occupant is not None else None,
        "occupied": occupant is not None,
    }


def scene_notes_card(
    engine: "GameEngine", center: "Entity", radius: int
) -> list[dict[str, Any]]:
    """Place/faction/world semantic notes in scope for a cast centered on `center`.
    Entity-attached traits ride along inside entity cards, not here."""
    return engine.collect_scene_notes(
        engine.scene_anchors_around(center.x, center.y, radius, include=[center])
    )


# ----------------------------------------------------------------------------------------
# Card-driven context slices (Stage 5): extra state the routed capability cards declare they
# need (CapabilityCard.required_context). A plain spell routes to no specialist card and gets
# none of these, so its context stays smaller than a memory-edit or prophecy cast.
# ----------------------------------------------------------------------------------------
def _nearby_npc_memories(engine: "GameEngine") -> list[dict[str, Any]]:
    """For memory-edit spells: the memories of visible NPCs, so the resolver can name what to
    add/alter/erase. Only NPCs with profiles (not plain hostile actors) carry memory."""
    player = engine.state.player
    fov = engine.state.fov_radius
    out: list[dict[str, Any]] = []
    for ent in engine.state.entities.values():
        if ent.kind != "npc" or not ent.alive:
            continue
        if not engine.is_visible(ent.x, ent.y):
            continue
        if abs(ent.x - player.x) > fov or abs(ent.y - player.y) > fov:
            continue
        profile = engine.state.npc_profiles.get(ent.id)
        if profile is None:
            continue
        out.append({"id": ent.id, "name": ent.name, "memory": list(profile.memory)})
    return out


def _nearby_structures(engine: "GameEngine") -> list[dict[str, Any]]:
    """For structure-animation spells: visible props/scenery that could be brought to life."""
    player = engine.state.player
    fov = engine.state.fov_radius
    out: list[dict[str, Any]] = []
    for ent in engine.state.entities.values():
        if ent.kind != "prop":
            continue
        if not engine.is_visible(ent.x, ent.y):
            continue
        if abs(ent.x - player.x) > fov or abs(ent.y - player.y) > fov:
            continue
        out.append(
            {
                "id": ent.id,
                "name": ent.name,
                "x": ent.x,
                "y": ent.y,
                "tags": sorted(ent.tags),
            }
        )
    return out


def _conjurable_items(engine: "GameEngine") -> dict[str, Any]:
    """For item-conjuration/transformation spells: the items on the floor (with ids) and in
    inventory that a transmute could target."""
    player = engine.state.player
    fov = engine.state.fov_radius
    floor = [
        item_card(e, engine)
        for e in engine.state.entities.values()
        if e.kind == "item"
        and engine.is_visible(e.x, e.y)
        and abs(e.x - player.x) <= fov
        and abs(e.y - player.y) <= fov
    ]
    return {"floor": floor, "inventory": dict(sorted(engine.state.inventory.items()))}


def card_context_slices(
    engine: "GameEngine", spell: str, selected_cards: "list[CapabilityCard]"
) -> dict[str, Any]:
    """The card-driven slices for this cast, keyed by slice name (see
    capabilities.selected_context_slices for the names)."""
    needed: set[str] = set()
    for card in selected_cards:
        needed.update(card.required_context)
    slices: dict[str, Any] = {}
    if "target_memories" in needed:
        slices["target_memories"] = _nearby_npc_memories(engine)
    if "promise_summaries" in needed:
        slices["promise_summaries"] = engine.promises_for_context(
            subject=spell, limit=6, text_limit=200
        )
    if "nearby_structures" in needed:
        slices["nearby_structures"] = _nearby_structures(engine)
    if "conjurable_items" in needed:
        slices["conjurable_items"] = _conjurable_items(engine)
    return slices


# ----------------------------------------------------------------------------------------
# Composite views.
# ----------------------------------------------------------------------------------------
def spell_context_view(
    engine: "GameEngine",
    spell: str,
    selected_cards: "list[CapabilityCard] | None" = None,
) -> dict[str, Any]:
    """The full resolver packet for one cast. `selected_cards` defaults to the routed
    capability cards for `spell`; pass it explicitly to reuse a single routing pass."""
    if selected_cards is None:
        selected_cards = select_cards(spell)
    player = engine.state.player
    fov = engine.state.fov_radius
    current_room = engine.room_profile_at(player.x, player.y)
    current_room_tags = set(current_room.tags if current_room else [])
    current_room_tags.update(current_room.topics if current_room else [])

    def _in_scope(ent: "Entity") -> bool:
        return (
            engine.is_visible(ent.x, ent.y)
            and abs(ent.x - player.x) <= fov
            and abs(ent.y - player.y) <= fov
        )

    nearby_entities = [
        entity_card(entity, engine)
        for entity in engine.state.entities.values()
        if entity.alive and _in_scope(entity)
    ]
    floor_items = [
        item_card(e, engine)
        for e in engine.state.entities.values()
        if e.kind == "item" and _in_scope(e)
    ]
    context = {
        "spell": spell,
        # Consumed by the prompt builder (spliced into the system prompt),
        # stripped from the user-message JSON. See _wild_prompt_messages.
        "region_style": engine.region.prompt_style(),
        # The profile of whoever is currently controlled (the player, or a body
        # they've swapped into). Carries the Composure volatility band, plus the
        # appearance/backstory/signature flavor lenses, so the resolver styles
        # casts for the soul-in-the-body rather than a hardcoded "player".
        "caster_profile": (player.profile or CharacterProfile()).to_public_dict(),
        # Spell foci the caster has marked (Entity.focus_slots) -- the implement(s) the
        # resolver should weigh heavily when flavoring this cast. Consumed by the prompt
        # builder (spliced into the system prompt as focus_block), stripped from the
        # user-message JSON. See _wild_prompt_messages / focus_prompt_block.
        "spell_foci": resolve_foci(engine),
        "turn": engine.state.turn,
        "depth": engine.state.depth,
        "max_depth": engine.state.max_depth,
        "player": player.to_public_dict(),
        # The square the player explicitly clicked/marked, if any. Present only when
        # set; the resolver is told (in the system prompt) that "target"/"there"/
        # "that square" refer to it. None of this is required — the engine resolves
        # the same keywords deterministically — but it lets the model reason about
        # range, line of sight, and what is actually standing there.
        **(
            {"selected_target": selected_target_card(engine)}
            if engine.has_target()
            else {}
        ),
        "inventory": engine.state.inventory,
        "experience": engine.state.experience,
        "curses": [curse.to_public_dict() for curse in engine.state.curses.values()],
        "active_curses": [curse_card(curse) for curse in engine.state.curses.values()],
        "world_flags": engine.state.flags,
        "event_timers": engine.state.event_timers,
        "triggers": engine.state.triggers,
        "current_room": room_card(current_room, engine) if current_room else None,
        "nearby_rooms": engine.visible_room_profiles(),
        "nearby_canon": engine.nearby_canon_records(tags=current_room_tags),
        "visible_tile_count": len(engine.state.visible),
        "explored_tile_count": len(engine.state.explored),
        "nearby_entities": nearby_entities,
        # Place/faction/world semantic notes in scope for this cast. Entity-attached
        # traits already ride along inside nearby_entities. See wildmagic/semantics.py.
        "scene_notes": scene_notes_card(engine, player, fov),
        "spell_anchors": engine.nearby_spell_anchors(spell),
        "floor_items": floor_items,
        "nearby_map": engine.nearby_map_strings(radius=9),
        "nearby_tile_details": nearby_tile_details(engine, radius=5),
        "tile_legend": {
            tile: {"name": name, "tags": sorted(TILE_TAGS.get(tile, set()))}
            for tile, name in TILE_NAMES.items()
        },
        "supported_effects": sorted(selected_effect_types(selected_cards)),
        "supported_costs": sorted(SUPPORTED_COSTS),
        "supported_statuses": sorted(MECHANICAL_STATUSES),
        "conjuration_templates": {
            "items": item_template_ids(),
            "creatures": creature_template_ids(),
        },
        "damage_types": sorted(DAMAGE_TYPES),
        "rules": {
            "normal_strong_damage": "1-8",
            "major_damage": "9-16 with meaningful cost",
            "outrageous_spell": "reject outright or apply a severe permanent curse",
            "technical_failure": "invalid JSON means the engine will not consume a turn",
            "area_limits": "no hard radius cap — crazy AOE is fine with appropriate costs",
            "cost_timing": "effects happen first, then costs are revealed and applied",
            "environment": "fire+water=mist, water extinguishes burning, vines snare on entry, ice slides movement",
        },
    }
    # Card-driven slices: extra state the routed specialist cards asked for. Plain casts route
    # to no card and add nothing here, keeping their context smaller (see Stage 5).
    context.update(card_context_slices(engine, spell, selected_cards))
    return context


def tile_counts(tiles: list[list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in tiles:
        for tile in row:
            counts[tile] = counts.get(tile, 0) + 1
    return dict(sorted(counts.items()))


def state_summary(engine: "GameEngine") -> dict[str, Any]:
    """Structured snapshot of the whole run: player, world ledgers, enemies, items, and
    emergent-world state. Drives the deterministic replay round-trip and CLI/GUI inspection.
    Unlike the resolver packet this includes room secrets — it is never shown to the model."""
    state = engine.state
    player = state.player
    current_room = engine.room_profile_at(player.x, player.y)
    living_enemies = sorted(engine.living_enemies(), key=lambda entity: entity.id)
    items = sorted(
        [entity for entity in state.entities.values() if entity.kind == "item"],
        key=lambda entity: entity.id,
    )
    return {
        "turn": state.turn,
        "depth": state.depth,
        "max_depth": state.max_depth,
        "game_over": state.game_over,
        "victory": state.victory,
        "player": {
            "x": player.x,
            "y": player.y,
            "hp": player.hp,
            "mana": player.mana,
            "statuses": dict(sorted(player.statuses.items())),
            "equipment": {
                slot: item for slot, item in sorted(player.equipment.items()) if item
            },
            "focus_slots": list(player.focus_slots),
        },
        "visible_count": len(state.visible),
        "explored_count": len(state.explored),
        "inventory": dict(sorted(state.inventory.items())),
        "item_lore": {
            key: dict(value) for key, value in sorted(state.item_lore.items())
        },
        "experience": state.experience,
        "flags": dict(sorted(state.flags.items())),
        "tile_counts": tile_counts(state.tiles),
        "tile_flows": {
            key: dict(value) for key, value in sorted(state.tile_flows.items())
        },
        "current_room": room_card(current_room, engine, include_secrets=True)
        if current_room
        else None,
        "visible_rooms": engine.visible_room_profiles(limit=8),
        "canon_records": [
            record.to_dict()
            for record in sorted(
                state.canon_records.values(), key=lambda record: record.id
            )
        ],
        "event_timers": sorted(
            [
                {
                    "turns": event.get("turns"),
                    "event_type": event.get("event_type") or event.get("type"),
                    "name": event.get("name"),
                    "text": event.get("text"),
                }
                for event in state.event_timers
            ],
            key=lambda event: (
                str(event.get("turns")),
                str(event.get("event_type")),
                str(event.get("name")),
            ),
        ),
        "triggers": sorted(
            [
                {
                    "trigger": trigger.get("trigger") or trigger.get("on"),
                    "target": trigger.get("target"),
                    "charges": trigger.get("charges"),
                    "duration": trigger.get("duration"),
                    "name": trigger.get("name"),
                }
                for trigger in state.triggers
            ],
            key=lambda trigger: (
                str(trigger.get("trigger")),
                str(trigger.get("target")),
                str(trigger.get("name")),
            ),
        ),
        "curses": {
            curse_id: curse_card(curse)
            for curse_id, curse in sorted(state.curses.items())
        },
        "quests": [
            {
                "id": quest.id,
                "name": quest.name,
                "description": quest.description,
                "contact": quest.contact,
                "location": quest.location,
                "status": quest.status,
            }
            for quest in engine.quest_log_entries()
        ],
        "promises": [
            promise.to_dict()
            for promise in sorted(state.promises, key=lambda promise: promise.id)
        ],
        "promise_reservations": [
            reservation.to_dict()
            for zone in sorted(state.promise_reservations)
            for reservation in state.promise_reservations[zone]
        ],
        "living_enemies": [
            {
                "id": enemy.id,
                "name": enemy.name,
                "x": enemy.x,
                "y": enemy.y,
                "hp": enemy.hp,
                "statuses": dict(sorted(enemy.statuses.items())),
                "tags": sorted(enemy.tags),
                "resistances": dict(sorted(enemy.resistances.items())),
                "weaknesses": dict(sorted(enemy.weaknesses.items())),
            }
            for enemy in living_enemies
        ],
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "x": item.x,
                "y": item.y,
                "item_type": item.item_type,
                "material": item.material,
                "quantity": item.quantity,
                "tags": sorted(item.tags),
            }
            for item in items
        ],
        "entity_count": len(state.entities),
        "recent_messages": state.messages[-8:],
        # Emergent-world ledgers (Phase 0): deeds + faction standing + the simulator
        # cursor. Surfaced here so the deterministic replay round-trip verifies them.
        "deeds": [
            deed.to_dict()
            for deed in sorted(state.deed_ledger.deeds, key=lambda deed: deed.id)
        ],
        "story_beats": [
            beat.to_dict()
            for beat in sorted(state.deed_ledger.beats, key=lambda beat: beat.id)
        ],
        "factions": {
            fid: state.faction_ledger.factions[fid].to_dict()
            for fid in sorted(state.faction_ledger.factions)
        },
        "legend": state.legend_ledger.to_dict(),
        "gossip_edges": [
            edge.to_dict()
            for edge in sorted(state.gossip_edges.values(), key=lambda edge: edge.id)
        ],
        "gossip_spread_days": sorted(state.gossip_spread_days),
        "npc_memories": {
            npc_id: {
                "name": profile.name,
                "personally_witnessed": [
                    record.to_dict()
                    for record in profile.memory_records
                    if record.bucket == "observation"
                    and record.provenance in {"firsthand", "implanted"}
                ],
                "overheard": [
                    record.to_dict()
                    for record in profile.memory_records
                    if record.bucket == "overheard" or record.provenance == "overheard"
                ],
                "gossip": [
                    record.to_dict()
                    for record in profile.memory_records
                    if record.bucket == "gossip" or record.provenance == "gossip"
                ],
                "conversation": [
                    record.to_dict()
                    for record in profile.memory_records
                    if record.bucket == "conversation"
                ],
            }
            for npc_id, profile in sorted(state.npc_profiles.items())
            if profile.memory_records
        },
        "pending_backlash": [dict(event) for event in state.pending_backlash],
        "followers": [
            {
                "name": profile.name,
                "loyalty": round(profile.bond.loyalty, 2),
                "affiliations": sorted(profile.bond.affiliations),
            }
            for _npc_id, profile in engine.followers()
        ],
        "simulated_through_turn": state.simulated_through_turn,
        "day": state.day,
        "turn_of_day": state.turn_of_day,
        "day_phase": state.day_phase,
        "ticked_through_day": state.ticked_through_day,
    }


def replay_summary_view(engine: "GameEngine") -> dict[str, Any]:
    """Structured summary embedded in run/replay records for deterministic round-tripping."""
    return state_summary(engine)


def inspection_view(engine: "GameEngine") -> dict[str, Any]:
    """Structured snapshot for CLI/GUI inspection. Currently the same data as the replay
    summary; kept as a distinct view so the two can diverge without churn."""
    return state_summary(engine)
