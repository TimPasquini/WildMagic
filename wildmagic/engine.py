from __future__ import annotations

from collections import deque
import concurrent.futures
from dataclasses import dataclass, field
import math
import random
import re
import time
from typing import Any

from .models import (
    BLOCKING_TILES,
    Room,
    ZoneSnapshot,
    DOOR,
    DAMAGE_TYPES,
    FIRE,
    FLOOR,
    ICE_WALL,
    MECHANICAL_STATUSES,
    MIST,
    OPEN_DOOR,
    POISON_CLOUD,
    ROAD,
    RUBBLE,
    SLICK_ICE,
    STAIRS_DOWN,
    STAIRS_UP,
    WALL,
    WATER,
    VINES,
    CharacterProfile,
    Entity,
    GameStats,
    NPCProfile,
    CanonRecord,
    RoomProfile,
    TILE_NAMES,
    TILE_ALIASES,
    TILE_TAGS,
    WildMagicOutcome,
)
from .combat import _CombatMixin
from .ai import _AIMixin
from .generation import _GenerationMixin
from .effects import _EffectsMixin
from .items import _ItemsMixin
from .templates import (
    creature_template,
    creature_template_ids,
    item_template,
    item_template_ids,
)
from .props import get_prop_template, get_all_prop_ids
from .regions import Region, get_region
from .promises import (
    PROMISE_LEDGER_LIMIT,
    PROMISE_RESERVATION_LIMIT,
    PromiseReservation,
    Objective,
    QuestLogEntry,
    Reward,
    WorldPromise,
    bind_promise,
    journal_entry,
    normalize_flesh,
    promise_context_for_prompt,
)
from .game_data import (
    MAP_WIDTH,
    MAP_HEIGHT,
    NPC_PERCEPTION_RADIUS,
    WILD_ENEMY_TEMPLATES,
    LEGION_ENEMY_TEMPLATES,
    FACTION_HOSTILITIES,
    ITEM_USE_SPECS,
    TRAP_SPECS,
    LOCKED_DOOR_KEYS,
    EQUIPMENT_SPECS,
    DEFAULT_ITEM_USE_SPEC,
    TRADE_KEYWORDS,
    scan_for_trade_intent,
    _BUILDING_SIZES,
    _DEFAULT_BUILDING_SIZE,
    _ROLE_STATS,
    _DEFAULT_NPC_STATS,
    _TOWN_LOCATIONS,
    _TOWN_DEFINING_TRAITS,
    _TOWN_SITUATIONS,
    _TOWN_GEN_TIMEOUT,
    _TOWN_SETTLEMENT_TYPES,
)
from .geometry import sign, bresenham_line, unique_points, _on_bresenham
from .normalize import (
    clamp_int,
    optional_duration,
    status_duration,
    parse_tile_key,
    normalize_id,
    normalize_faction,
    normalize_trigger_name,
    infer_behavior_tags,
    singular_target_tag,
    normalize_numeric_map,
    sanitize_name,
    sanitize_char,
    coerce_list,
    _flatten_effect,
    area_damage_affects,
    tile_from_name,
)


class LogMessage(str):
    def __new__(cls, content, is_danger=False):
        obj = str.__new__(cls, content)
        obj.is_danger = is_danger
        return obj


_PROP_TAG_AFFORDANCES: dict[str, list[str]] = {
    "acid": ["corrode armor or stone", "make poison cloud", "burn a path"],
    "alien": ["summon strange life", "confuse or frighten", "mutate nearby matter"],
    "antimagic": ["silence magic", "drain mana", "ward an area"],
    "ash": ["raise smoke or mist", "summon embers", "mark with old fire"],
    "blood": ["curse", "bleed", "call a debt"],
    "bone": ["summon spirits", "root or bind", "make brittle shards"],
    "broken": ["shatter outward", "scatter rubble", "turn failure into force"],
    "cold": ["freeze", "slow", "make slick ice"],
    "crystal": ["refract light", "shatter", "amplify magic"],
    "cursed": ["curse", "frighten", "mark a target"],
    "death": ["summon spirits", "drain life", "delay a consequence"],
    "debris": ["make rubble", "scatter shrapnel", "block movement"],
    "dry": ["ignite", "make ash", "spread dust"],
    "empire": ["bind with regulation", "mark or reveal", "summon hostile attention"],
    "fire": ["ignite", "explode", "make smoke or light"],
    "flammable": ["ignite", "spread fire", "make smoke"],
    "fragile": ["shatter", "make shards", "release stored magic"],
    "fungus": ["poison", "confuse", "spread spores"],
    "glass": ["shatter", "refract light", "make cutting fragments"],
    "heavy": ["drop weight", "anchor a pull", "block movement"],
    "holy": ["ward", "reveal", "burn undead or cursed things"],
    "hot": ["ignite", "burn", "melt ice"],
    "insect": ["summon swarm", "crawl over enemies", "spread panic"],
    "light": ["reveal", "blind or mark", "project a beam"],
    "lightning": ["shock", "stun", "conduct through metal or water"],
    "liquid": ["flood", "splash", "turn to mist or ice"],
    "lore": ["reveal", "set a delayed omen", "name a curse"],
    "magic": ["amplify the spell", "summon", "create a ward"],
    "mechanical": ["trigger a mechanism", "spin time or force", "launch or pull"],
    "metal": ["conduct lightning", "magnetize", "make shrapnel"],
    "music": ["charm or confuse", "push as sound", "summon echoes"],
    "paper": ["burn into sigils", "reveal writing", "scatter pages"],
    "plant": ["grow vines", "snare", "release spores or pollen"],
    "powder": ["make a circle", "blind with dust", "ignite a flash"],
    "prison": ["bind", "root", "lock a target in place"],
    "ritual": ["summon", "curse", "ward"],
    "rope": ["bind", "pull", "snare"],
    "sharp": ["bleed", "make caltrops", "shred armor"],
    "silk": ["web", "snare", "muffle sound"],
    "smelly": ["poison", "frighten", "make a choking cloud"],
    "snaring": ["root", "web", "slow movement"],
    "stone": ["make rubble", "petrify", "raise a barrier"],
    "toxic": ["poison", "make poison cloud", "sicken"],
    "trap": ["trigger", "create ward", "delay an effect"],
    "water": ["flood", "mist", "freeze or conduct lightning"],
    "weapons": ["launch blades", "arm a summon", "make shrapnel"],
    "wet": ["mist", "freeze", "conduct lightning"],
    "wood": ["ignite", "grow thorns", "splinter"],
}


_PROP_GENERIC_AFFORDANCES = [
    "use as a spell center",
    "flavor the outcome",
    "anchor a summon or terrain change",
]


@dataclass
class GameState:
    width: int = MAP_WIDTH
    height: int = MAP_HEIGHT
    tiles: list[list[str]] = field(default_factory=list)
    visible: set[str] = field(default_factory=set)
    explored: set[str] = field(default_factory=set)
    entities: dict[str, Entity] = field(default_factory=dict)
    player_id: str = "player"
    turn: int = 0
    messages: list[str] = field(default_factory=list)
    message_count: int = 0
    npc_profiles: dict[str, NPCProfile] = field(default_factory=dict)
    pending_trade: dict[str, Any] | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    last_talked_npc_name: str | None = None
    tile_tags: dict[str, list[str]] = field(default_factory=dict)
    tile_durations: dict[str, int] = field(default_factory=dict)
    event_timers: list[dict[str, Any]] = field(default_factory=list)
    triggers: list[dict[str, Any]] = field(default_factory=list)
    game_over: bool = False
    victory: bool = False
    death_cause: str | None = (
        None  # "empire" | "wild" | None — set when the player dies
    )
    region_id: str = "frontier"  # which Region (regions.py) the player is currently in
    rng_seed: int | None = None
    scenario: str = "dungeon"
    fov_radius: int = 9
    depth: int = 1
    max_depth: int = 3
    stats: GameStats = field(default_factory=GameStats)
    zone_x: int = 0
    zone_y: int = 0
    zone_type: str = "frontier"
    zones: dict[tuple[int, int], ZoneSnapshot] = field(default_factory=dict)
    dungeon_floors: dict[int, ZoneSnapshot] = field(default_factory=dict)
    room_profiles: dict[str, RoomProfile] = field(default_factory=dict)
    tile_rooms: dict[str, str] = field(default_factory=dict)
    canon_records: dict[str, CanonRecord] = field(default_factory=dict)
    _player_taking_damage: bool = False
    # Stable empty stashes returned by the inventory/curses properties when no body
    # is controlled yet (mid-generation). Never the real store once a body exists.
    _no_body_inventory: dict[str, int] = field(default_factory=dict)
    _no_body_curses: dict[str, Any] = field(default_factory=dict)
    promises: list[WorldPromise] = field(default_factory=list)
    promise_reservations: dict[tuple[int, int], list[PromiseReservation]] = field(
        default_factory=dict
    )
    # Handoff slot for character creation: a profile to stamp onto the starting
    # player entity. None → a default profile is generated. The live profile always
    # lives on the controlled entity (state.player.profile), not here.
    character: CharacterProfile | None = None

    @property
    def player(self) -> Entity:
        # "player" means the currently controlled entity. Body-swap reassigns
        # player_id, so all the player-centric code follows the soul automatically.
        return self.entities[self.player_id]

    @property
    def inventory(self) -> dict[str, int]:
        """The controlled entity's inventory. Inventory is per-entity (it stays with
        the body on a swap); this property keeps the ~200 existing `state.inventory`
        call sites pointing at whoever is currently controlled. During world
        generation the body may not exist yet (entities are cleared then rebuilt),
        in which case there is genuinely no inventory — a stable empty stash is
        returned so reads (and any stray writes) are safe."""
        player = self.entities.get(self.player_id)
        return player.inventory if player is not None else self._no_body_inventory

    @property
    def curses(self) -> dict[str, Any]:
        """The controlled entity's curses (per-entity, like inventory)."""
        player = self.entities.get(self.player_id)
        return player.curses if player is not None else self._no_body_curses

    def add_message(self, message: str, is_danger: bool = False) -> None:
        if self._player_taking_damage:
            msg_lower = message.lower()
            if not any(
                pos in msg_lower
                for pos in {
                    "cauterized",
                    "heal",
                    "recover",
                    "collapses... but begins to stir",
                }
            ):
                is_danger = True
        self.messages.append(LogMessage(message, is_danger))
        self.messages = self.messages[-80:]
        # Monotonic; unlike len(messages), it survives the cap above, so callers
        # (e.g. NPC perception) can tell exactly how many messages are new.
        self.message_count += 1

    def location_label(self) -> str:
        if self.scenario == "frontier":
            return f"Zone ({self.zone_x},{self.zone_y}) - {self.zone_type}"
        if self.scenario == "town":
            return "Hollowmere"
        return f"Depth {self.depth}"

    def room_profile_at(self, x: int, y: int) -> RoomProfile | None:
        room_id = self.tile_rooms.get(f"{x},{y}")
        if not room_id:
            return None
        return self.room_profiles.get(room_id)


class GameEngine(_CombatMixin, _ItemsMixin, _AIMixin, _GenerationMixin, _EffectsMixin):
    def __init__(
        self,
        seed: int | None = None,
        scenario: str = "dungeon",
        provider_name: str | None = None,
        character: CharacterProfile | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.state = GameState(rng_seed=seed, scenario=scenario)
        # A profile from character creation, stamped onto the starting player by
        # _make_player. Set before generation runs. None → a random default profile.
        self.state.character = character
        self._next_entity_number = 1
        self._conducting_lightning = False
        self._npc_perception_message_count = 0
        # Town generation: background executor + pending futures (not in GameState — not serializable)
        self._pending_towns: dict[tuple[int, int], concurrent.futures.Future[Any]] = {}
        self._pending_town_contexts: dict[tuple[int, int], dict] = {}
        self._pending_town_start_times: dict[tuple[int, int], float] = {}
        self._town_executor: concurrent.futures.ThreadPoolExecutor | None = None
        from .wild_magic import make_town_provider

        self.town_provider = make_town_provider(provider_name)
        if scenario == "test_chamber":
            self._generate_test_chamber()
        elif scenario == "empire_compound":
            self._generate_empire_compound()
        elif scenario == "frontier":
            self._generate_frontier_start()
            self._maybe_pregenerate_adjacent_towns()
        elif scenario == "town":
            self._generate_town_start()
            self._maybe_pregenerate_adjacent_towns()
        else:
            self._generate_new_run()

    def next_entity_id(self, prefix: str) -> str:
        value = f"{prefix}_{self._next_entity_number}"
        self._next_entity_number += 1
        return value

    def spawn_actor(
        self,
        name: str,
        char: str,
        x: int,
        y: int,
        hp: int,
        attack: int,
        defense: int,
        faction: str,
        ai: str | None,
        tags: set[str] | None = None,
        resistances: dict[str, int] | None = None,
        weaknesses: dict[str, int] | None = None,
    ) -> Entity:
        faction = normalize_faction(faction, default="ally")
        actor_tags = {
            normalize_id(str(tag)) for tag in (tags or set()) if str(tag).strip()
        }
        actor_tags = infer_behavior_tags(name, actor_tags)
        entity = Entity(
            id=self.next_entity_id("actor"),
            name=name,
            kind="actor",
            x=x,
            y=y,
            char=char,
            hp=hp,
            max_hp=hp,
            attack=attack,
            defense=defense,
            blocks=True,
            faction=faction,
            ai=ai,
            tags=actor_tags,
            resistances=dict(resistances or {}),
            weaknesses=dict(weaknesses or {}),
            # Every creature carries the same universal profile, so a body you swap
            # into already has stats to inherit. Vigor scales with the body's bulk.
            profile=CharacterProfile(
                origin_id="creature",
                vigor=max(1, min(6, hp // 8)),
                attunement=2,
                composure=3,
            ),
        )
        self.state.entities[entity.id] = entity
        return entity

    def spawn_npc(
        self,
        name: str,
        char: str,
        x: int,
        y: int,
        role: str,
        backstory: str,
        appearance: str = "",
        traits: list[str] | None = None,
        tags: set[str] | None = None,
        wares: dict[str, int] | None = None,
        hp: int = 14,
        attack: int = 2,
        defense: int = 0,
        faction: str = "neutral",
        wanted_item: str | None = None,
        wanted_qty: int = 0,
        reward_gold: int = 0,
        reward_item: str | None = None,
        reward_qty: int = 0,
    ) -> Entity:
        """Spawn a talkable NPC: a physical Entity plus a parallel NPCProfile carrying
        persona/memory data (kept separate the same way Curse data lives off-Entity).

        `hp`/`attack`/`defense`/`faction` default to ordinary-townsfolk values, but can
        be overridden for NPCs who are meant to actually fight -- a guard captain who
        holds her ground rather than a peddler who'd rather not be there at all."""
        npc_tags = {
            normalize_id(str(tag)) for tag in (tags or set()) if str(tag).strip()
        }
        npc_tags.add("npc")
        entity = Entity(
            id=self.next_entity_id("npc"),
            name=name,
            kind="npc",
            x=x,
            y=y,
            char=char,
            hp=hp,
            max_hp=hp,
            attack=attack,
            defense=defense,
            blocks=True,
            faction=faction,
            ai="npc",
            tags=npc_tags,
            description=appearance or None,
            # The universal profile mirrors the NPC's persona appearance/backstory so
            # that inhabiting this body presents its identity (name/appearance follow
            # the body), and the wild-magic resolver can read its stats like anyone's.
            profile=CharacterProfile(
                origin_id="folk",
                vigor=max(1, min(6, hp // 8)),
                attunement=2,
                composure=3,
                appearance=appearance,
                backstory=backstory,
            ),
        )
        self.state.entities[entity.id] = entity
        npc_wares = dict(wares or {})
        if reward_gold > 0:
            npc_wares["gold"] = npc_wares.get("gold", 0) + reward_gold
        if reward_item and reward_qty > 0:
            reward_item_key = reward_item.strip().lower()
            npc_wares[reward_item_key] = npc_wares.get(reward_item_key, 0) + reward_qty

        self.state.npc_profiles[entity.id] = NPCProfile(
            entity_id=entity.id,
            name=name,
            role=role,
            backstory=backstory,
            appearance=appearance,
            traits=list(traits or []),
            wares=npc_wares,
            wanted_item=wanted_item,
            wanted_qty=wanted_qty,
            reward_gold=reward_gold,
            reward_item=reward_item,
            reward_qty=reward_qty,
        )
        return entity

    def spawn_prop(
        self,
        template_id: str,
        x: int,
        y: int,
    ) -> Entity | None:
        template = get_prop_template(template_id)
        if not template:
            return None
        entity = Entity(
            id=self.next_entity_id("prop"),
            name=template.name,
            kind="prop",
            x=x,
            y=y,
            char=template.char,
            blocks=template.blocks,
            tags=set(template.tags),
            description=template.description,
            hp=10,
            max_hp=10,
            faction="neutral",
        )
        self.state.entities[entity.id] = entity
        return entity

    def room_profile_at(self, x: int, y: int) -> RoomProfile | None:
        return self.state.room_profile_at(x, y)

    def visible_room_profiles(self, limit: int = 6) -> list[dict[str, Any]]:
        player = self.state.player
        rooms: list[tuple[int, dict[str, Any]]] = []
        for profile in self.state.room_profiles.values():
            if not any(
                self.is_visible(x, y)
                for y in range(profile.y, profile.y + profile.h)
                for x in range(profile.x, profile.x + profile.w)
            ):
                continue
            cx, cy = profile.center
            distance = abs(cx - player.x) + abs(cy - player.y)
            data = profile.to_public_dict()
            data["distance"] = distance
            rooms.append((distance, data))
        rooms.sort(key=lambda item: (item[0], item[1]["id"]))
        return [room for _distance, room in rooms[:limit]]

    def nearby_canon_records(
        self,
        tags: list[str] | set[str] | tuple[str, ...] = (),
        attachment: dict[str, Any] | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        wanted_tags = {normalize_id(str(tag)) for tag in tags if str(tag).strip()}
        scored: list[tuple[int, str, CanonRecord]] = []
        for record in self.state.canon_records.values():
            score = 0
            record_tags = {
                normalize_id(str(tag)) for tag in record.tags if str(tag).strip()
            }
            overlap = wanted_tags & record_tags
            if overlap:
                score += 10 + len(overlap)
            if attachment:
                for key, value in attachment.items():
                    if record.attachment.get(key) == value:
                        score += 8
            if record.status == "canonical":
                score += 2
            if score <= 0 and (wanted_tags or attachment):
                continue
            scored.append((-score, record.id, record))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [record.to_context_dict() for _score, _id, record in scored[:limit]]

    def add_canon_record(self, record: CanonRecord) -> CanonRecord:
        existing = self.state.canon_records.get(record.id)
        if existing is not None and existing.status == "canonical":
            return existing
        record.tags = sorted(
            {normalize_id(str(tag)) for tag in record.tags if str(tag).strip()}
        )
        record.kind = normalize_id(record.kind or "object_detail")
        record.status = (
            "canonical"
            if record.status not in {"provisional", "canonical"}
            else record.status
        )
        self.state.canon_records[record.id] = record
        return record

    @property
    def region(self) -> Region:
        return get_region(self.state.region_id)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.state.width and 0 <= y < self.state.height

    def tile_at(self, x: int, y: int) -> str:
        if not self.in_bounds(x, y):
            return WALL
        return self.state.tiles[y][x]

    def tile_key(self, x: int, y: int) -> str:
        return f"{x},{y}"

    def is_visible(self, x: int, y: int) -> bool:
        return self.tile_key(x, y) in self.state.visible

    def is_explored(self, x: int, y: int) -> bool:
        return self.tile_key(x, y) in self.state.explored

    def tile_blocks_sight(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return True
        return "opaque" in self.tile_tags_at(x, y)

    def update_fov(self) -> None:
        player = self.state.player
        visible: set[str] = set()
        radius = self.state.fov_radius
        for y in range(player.y - radius, player.y + radius + 1):
            for x in range(player.x - radius, player.x + radius + 1):
                if not self.in_bounds(x, y):
                    continue
                if math.hypot(x - player.x, y - player.y) > radius:
                    continue
                if self.has_line_of_sight(player.x, player.y, x, y):
                    visible.add(self.tile_key(x, y))
        self.state.visible = visible
        self.state.explored.update(visible)

    def has_line_of_sight(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        for x, y in bresenham_line(x1, y1, x2, y2)[1:-1]:
            if self.tile_blocks_sight(x, y):
                return False
        return True

    def set_tile(
        self,
        x: int,
        y: int,
        tile: str,
        duration: int | None = None,
        tags: set[str] | None = None,
    ) -> bool:
        if not self.in_bounds(x, y):
            return False
        if tile not in TILE_NAMES:
            tile = FLOOR
        tile = self._reacting_tile(self.tile_at(x, y), tile)
        self.state.tiles[y][x] = tile
        key = self.tile_key(x, y)
        if duration is not None and duration > 0:
            self.state.tile_durations[key] = duration
        else:
            self.state.tile_durations.pop(key, None)
        if tags:
            self.state.tile_tags[key] = sorted(set(tags))
        elif tile == FLOOR:
            self.state.tile_tags.pop(key, None)
        if tile in BLOCKING_TILES:
            for entity in self.entities_at(x, y):
                if entity.blocks:
                    self._move_to_nearest_open_tile(entity)
        return True

    def tile_tags_at(self, x: int, y: int) -> set[str]:
        tile = self.tile_at(x, y)
        tags = set(TILE_TAGS.get(tile, set()))
        tags.update(self.state.tile_tags.get(self.tile_key(x, y), []))
        return tags

    def _reacting_tile(self, old_tile: str, new_tile: str) -> str:
        if old_tile == WATER and new_tile == FIRE:
            return MIST
        if old_tile == FIRE and new_tile == WATER:
            return MIST
        if old_tile == WATER and new_tile == ICE_WALL:
            return ICE_WALL
        if old_tile == VINES and new_tile == FIRE:
            return FIRE
        return new_tile

    def can_occupy(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y) or self.tile_at(x, y) in BLOCKING_TILES:
            return False
        return self.blocking_entity_at(x, y) is None

    def validate_state(self) -> list[str]:
        errors: list[str] = []
        state = self.state
        if state.player_id not in state.entities:
            errors.append("player entity is missing")
            return errors
        if len(state.tiles) != state.height:
            errors.append("tile row count does not match map height")
        for y, row in enumerate(state.tiles):
            if len(row) != state.width:
                errors.append(f"tile row {y} does not match map width")
                break
            for x, tile in enumerate(row):
                if tile not in TILE_NAMES:
                    errors.append(f"unknown tile at {x},{y}: {tile!r}")
                    break
        blocking_positions: dict[tuple[int, int], str] = {}
        for entity_id, entity in state.entities.items():
            if entity.id != entity_id:
                errors.append(f"entity key/id mismatch for {entity_id}")
            if not self.in_bounds(entity.x, entity.y):
                errors.append(f"{entity.id} is out of bounds at {entity.x},{entity.y}")
            if entity.max_hp < 1 and entity.kind not in {"item", "prop"}:
                errors.append(f"{entity.id} has invalid max_hp {entity.max_hp}")
            if entity.hp < 0 or entity.hp > max(entity.max_hp, 0):
                errors.append(f"{entity.id} has invalid hp {entity.hp}/{entity.max_hp}")
            if entity.max_mana < 0 or entity.mana < 0 or entity.mana > entity.max_mana:
                errors.append(
                    f"{entity.id} has invalid mana {entity.mana}/{entity.max_mana}"
                )
            if entity.quantity < 0:
                errors.append(f"{entity.id} has negative quantity")
            if entity.blocks and entity.alive:
                position = (entity.x, entity.y)
                other = blocking_positions.get(position)
                if other is not None:
                    errors.append(
                        f"blocking entities overlap at {entity.x},{entity.y}: {other}, {entity.id}"
                    )
                else:
                    blocking_positions[position] = entity.id
        for item, amount in state.inventory.items():
            if not isinstance(amount, int) or amount < 0:
                errors.append(f"inventory item {item!r} has invalid amount {amount!r}")
        for curse_id, curse in state.curses.items():
            if curse.id != curse_id or curse.stacks < 1:
                errors.append(f"curse {curse_id!r} is invalid")
        for table_name, table in (
            ("tile_tags", state.tile_tags),
            ("tile_durations", state.tile_durations),
        ):
            for key in table:
                try:
                    x, y = parse_tile_key(key)
                except (ValueError, TypeError):
                    errors.append(f"{table_name} has invalid key {key!r}")
                    continue
                if not self.in_bounds(x, y):
                    errors.append(f"{table_name} key {key!r} is out of bounds")
        for key, duration in state.tile_durations.items():
            if not isinstance(duration, int) or duration < 1:
                errors.append(f"tile duration {key!r} is invalid: {duration!r}")
        for index, event in enumerate(state.event_timers):
            if not isinstance(event, dict):
                errors.append(f"event timer {index} is not an object")
        for index, trigger in enumerate(state.triggers):
            if not isinstance(trigger, dict):
                errors.append(f"trigger {index} is not an object")
        return errors

    def entities_at(self, x: int, y: int) -> list[Entity]:
        return [
            entity
            for entity in self.state.entities.values()
            if entity.x == x and entity.y == y and entity.alive
        ]

    def blocking_entity_at(self, x: int, y: int) -> Entity | None:
        for entity in self.entities_at(x, y):
            if entity.blocks:
                return entity
        return None

    def living_enemies(self) -> list[Entity]:
        return [
            entity
            for entity in self.state.entities.values()
            if entity.kind in {"actor", "npc"}
            and entity.faction == "enemy"
            and entity.hp > 0
        ]

    def is_hostile_to(self, actor: Entity, other: Entity) -> bool:
        """Whether `actor` is willing to fight `other` -- the general notion of
        "who's at war with whom," not just "everything hates the player."

        Baseline: enemies oppose the player and its allies, and vice versa --
        exactly what every fight in the game already assumed. On top of that,
        FACTION_HOSTILITIES declares standing conflicts between tagged groups
        (e.g. {"empire"} vs {"hollowmere_townsfolk"}), so e.g. an Imperial
        soldier and a Hollowmere local are hostile to each other without either
        of them needing to be the player or an ally.
        """
        if actor.id == other.id or other.hp <= 0:
            return False
        if actor.faction == "enemy" and other.faction in {"player", "ally"}:
            return True
        if actor.faction in {"player", "ally"} and other.faction == "enemy":
            return True
        return self._declared_conflict(actor, other)

    def _declared_conflict(self, actor: Entity, other: Entity) -> bool:
        """Whether `actor` and `other` are bound by a standing FACTION_HOSTILITIES
        conflict -- a known, ongoing war between tagged groups, not a perception-
        based grudge. Used both to widen `is_hostile_to` beyond the player/ally
        baseline and (in `_select_target`) to let such forces march on each
        other with intent rather than waiting to physically spot one another."""
        for side_a, side_b in FACTION_HOSTILITIES:
            if (side_a & actor.tags and side_b & other.tags) or (
                side_b & actor.tags and side_a & other.tags
            ):
                return True
        return False

    def distance(self, a: Entity, b: Entity) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def attempt_player_move(self, dx: int, dy: int) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if any(s in player.statuses for s in ["rooted", "webbed", "frozen", "stunned"]):
            self.state.add_message(
                "You strain against it, but you cannot move - you are held in place."
            )
            self.finish_player_turn()
            return True
        target_x = player.x + dx
        target_y = player.y + dy
        if not self.in_bounds(target_x, target_y):
            if self.state.scenario in {"frontier", "town"} and self._cross_zone_edge(
                target_x, target_y
            ):
                self.finish_player_turn()
                return True
            self.state.add_message("The dungeon refuses that edge.")
            return False
        target = self.blocking_entity_at(target_x, target_y)
        if target and target.faction in {"ally", "neutral"}:
            self.state.add_message(f"{target.name} is in your way.")
            return False
        if target and target.faction != "player":
            self.attack(player, target)
            self.finish_player_turn()
            return True
        if self.tile_at(target_x, target_y) == DOOR:
            tags = self.tile_tags_at(target_x, target_y)
            if "locked" in tags:
                key_tag = next((tag for tag in tags if tag.startswith("key_")), None)
                owned = self.find_inventory_item(key_tag[4:]) if key_tag else None
                if not owned:
                    self.state.add_message(
                        "The door is locked tight. You'll need the right key."
                    )
                    return False
                self.consume_inventory_item(owned, 1)
                self.state.tile_tags.pop(self.tile_key(target_x, target_y), None)
                self.state.add_message(
                    f"You turn the {owned} in the lock and the door swings open."
                )
            self.open_door(target_x, target_y)
            self.finish_player_turn()
            return True
        if self.tile_at(target_x, target_y) in BLOCKING_TILES:
            self.state.add_message(
                f"{TILE_NAMES.get(self.tile_at(target_x, target_y), 'stone')} blocks the way."
            )
            return False
        player.x = target_x
        player.y = target_y
        moved = True
        self.pick_up_items_at_player()
        self._apply_tile_entry(player)
        self.update_fov()
        # Slick ice keeps you sliding one extra tile in the same direction.
        if self.tile_at(player.x, player.y) == SLICK_ICE:
            slide_x, slide_y = player.x + dx, player.y + dy
            if (
                self.in_bounds(slide_x, slide_y)
                and self.tile_at(slide_x, slide_y) not in BLOCKING_TILES
                and not self.blocking_entity_at(slide_x, slide_y)
            ):
                player.x = slide_x
                player.y = slide_y
                self.state.add_message("You slide on the ice!")
                self._apply_tile_entry(player)
        if moved:
            self._fire_triggers("on_player_move", {"target": player, "source": player})
        self.finish_player_turn()
        return True

    def wait_turn(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        mana_before = player.mana
        if player.mana < player.max_mana:
            player.mana += 1
        self.state.add_message("You hold still and listen.")
        if player.mana > mana_before:
            self.state.add_message("You catch your breath and recover 1 mana.")
        self.finish_player_turn()
        return True

    def find_talk_target(self) -> Entity | None:
        """The NPC the player is positioned to talk to: any talkable NPC in an
        adjacent (8-directional) tile, picked deterministically if more than one."""
        player = self.state.player
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                entity = self.blocking_entity_at(player.x + dx, player.y + dy)
                if entity is not None and entity.kind == "npc" and entity.alive:
                    candidates.append(entity)
        if not candidates:
            return None
        return min(candidates, key=lambda entity: entity.id)

    def add_promises(self, promises: list[WorldPromise]) -> list[WorldPromise]:
        added: list[WorldPromise] = []
        reserved_zones: set[tuple[int, int]] = set()
        for promise in promises:
            if not promise.text.strip():
                continue
            duplicate = self._matching_promise(promise)
            if duplicate is not None:
                self._merge_promise(duplicate, promise)
                reservation = self._bind_and_reserve_promise(duplicate)
                if reservation is not None:
                    reserved_zones.add(reservation.zone)
                continue
            self.state.promises.append(promise)
            added.append(promise)
            reservation = self._bind_and_reserve_promise(promise)
            if reservation is not None:
                reserved_zones.add(reservation.zone)
        self.state.promises.sort(key=lambda promise: (promise.source_turn, promise.id))
        self._trim_promises()
        for zone in reserved_zones:
            self._invalidate_pending_town_generation(zone)
        return added

    def journal_entries(self) -> list[dict[str, Any]]:
        """Everything the world has told the player, except quests (those have the
        quest log). Open entries newest-first; settled history sinks to the bottom."""
        open_entries: list[dict[str, Any]] = []
        closed_entries: list[dict[str, Any]] = []
        for promise in self.state.promises:
            if promise.kind == "quest":
                continue
            entry = journal_entry(promise)
            if entry["status"] in {"settled", "proved false"}:
                closed_entries.append(entry)
            else:
                open_entries.append(entry)
        open_entries.sort(key=lambda entry: (-entry["turn"], entry["id"]))
        closed_entries.sort(key=lambda entry: (-entry["turn"], entry["id"]))
        return open_entries + closed_entries

    def fulfill_promise(
        self, promise_id: str, realized_in: str | None = None
    ) -> WorldPromise | None:
        for promise in self.state.promises:
            if promise.id == promise_id:
                promise.status = "fulfilled"
                if realized_in:
                    promise.realized_in = realized_in
                return promise
        return None

    def apply_promise_flesh(
        self, promise_id: str, flesh: dict[str, Any] | None
    ) -> WorldPromise | None:
        """Attach background-model decoration to a promise. Decoration only: this never
        creates, moves, unbinds, or realizes anything."""
        normalized = normalize_flesh(flesh)
        if normalized is None:
            return None
        for promise in self.state.promises:
            if promise.id == promise_id:
                promise.flesh = normalized
                return promise
        return None

    def add_quest_promise(
        self,
        *,
        name: str,
        description: str,
        contact: str,
        location: str,
        objective: Objective | None = None,
        reward: Reward | None = None,
        source: str | None = None,
        tags: list[str] | None = None,
    ) -> WorldPromise:
        promise_id = f"quest_{normalize_id(contact)}_{normalize_id(name)}"
        existing = next(
            (promise for promise in self.state.promises if promise.id == promise_id),
            None,
        )
        if existing is not None:
            return existing
        promise = WorldPromise(
            id=promise_id,
            kind="quest",
            subject=name,
            text=description,
            tags=list(tags or ["quest"]),
            source=source or f"quest:{contact}",
            source_turn=self.state.turn,
            origin_zone=(self.state.zone_x, self.state.zone_y),
            salience=5,
            confidence=1.0,
            objective=objective,
            reward=reward,
            giver_npc=contact,
            status="unverified",
            location=location,
        )
        self.add_promises([promise])
        return promise

    def quest_log_entries(self) -> list[QuestLogEntry]:
        entries: list[QuestLogEntry] = []
        for promise in sorted(
            self.state.promises,
            key=lambda item: (
                item.status == "fulfilled",
                item.source_turn,
                item.subject.lower(),
            ),
        ):
            if promise.kind != "quest":
                continue
            entries.append(
                QuestLogEntry(
                    id=promise.id,
                    name=promise.subject,
                    description=promise.text,
                    contact=promise.giver_npc or promise.source,
                    location=promise.realized_in or promise.location or "unknown",
                    status="completed" if promise.status == "fulfilled" else "active",
                )
            )
        return entries

    def complete_quest_by_index(self, index: int) -> QuestLogEntry | None:
        entries = self.quest_log_entries()
        if index < 0 or index >= len(entries):
            return None
        entry = entries[index]
        promise = next(
            (item for item in self.state.promises if item.id == entry.id), None
        )
        if promise is None:
            return None
        promise.status = "fulfilled"
        return QuestLogEntry(
            entry.id,
            entry.name,
            entry.description,
            entry.contact,
            entry.location,
            "completed",
        )

    def remove_quest_by_index(self, index: int) -> QuestLogEntry | None:
        entries = self.quest_log_entries()
        if index < 0 or index >= len(entries):
            return None
        entry = entries[index]
        self.state.promises = [
            promise for promise in self.state.promises if promise.id != entry.id
        ]
        self.state.promise_reservations = {
            zone: [
                reservation
                for reservation in reservations
                if reservation.promise_id != entry.id
            ]
            for zone, reservations in self.state.promise_reservations.items()
        }
        self.state.promise_reservations = {
            zone: reservations
            for zone, reservations in self.state.promise_reservations.items()
            if reservations
        }
        return entry

    def _matching_promise(self, promise: WorldPromise) -> WorldPromise | None:
        promise_subject = normalize_id(promise.subject)
        promise_tags = {normalize_id(tag) for tag in promise.tags if tag}
        promise_text = promise.text.strip().lower()
        for existing in self.state.promises:
            if existing.id == promise.id:
                return existing
            if existing.text.strip().lower() == promise_text:
                return existing
            existing_subject = normalize_id(existing.subject)
            if not promise_subject or promise_subject != existing_subject:
                continue
            existing_tags = {normalize_id(tag) for tag in existing.tags if tag}
            if not promise_tags or not existing_tags or promise_tags & existing_tags:
                return existing
        return None

    def _merge_promise(self, existing: WorldPromise, incoming: WorldPromise) -> None:
        existing.salience = min(5, max(existing.salience, incoming.salience) + 1)
        existing.confidence = max(existing.confidence, incoming.confidence)
        if not existing.what and incoming.what:
            existing.what = incoming.what
        for tag in incoming.tags:
            tag_id = normalize_id(tag)
            if tag_id and tag_id not in existing.tags:
                existing.tags.append(tag_id)
        if existing.status not in {
            "verified",
            "false",
            "redeemed",
        } and incoming.status not in {"false"}:
            existing.status = "corroborated"

    def _bind_and_reserve_promise(
        self, promise: WorldPromise
    ) -> PromiseReservation | None:
        reservation = bind_promise(
            promise,
            explored_zones=set(self.state.zones)
            | {(self.state.zone_x, self.state.zone_y)},
            reserved_counts=self._promise_reservation_counts(),
        )
        if reservation is None:
            return None
        return self._reserve_promise(reservation)

    def _promise_reservation_counts(self) -> dict[tuple[int, int], int]:
        return {
            zone: sum(reservation.capacity_cost for reservation in reservations)
            for zone, reservations in self.state.promise_reservations.items()
        }

    def _reserve_promise(
        self, reservation: PromiseReservation
    ) -> PromiseReservation | None:
        existing = self.state.promise_reservations.setdefault(reservation.zone, [])
        if any(item.promise_id == reservation.promise_id for item in existing):
            return None
        existing.append(reservation)
        self._trim_promise_reservations()
        return reservation

    def _trim_promise_reservations(self) -> None:
        all_reservations = [
            reservation
            for reservations in self.state.promise_reservations.values()
            for reservation in reservations
        ]
        if len(all_reservations) <= PROMISE_RESERVATION_LIMIT:
            return
        keep_ids = {
            reservation.promise_id
            for reservation in all_reservations[-PROMISE_RESERVATION_LIMIT:]
        }
        self.state.promise_reservations = {
            zone: [
                reservation
                for reservation in reservations
                if reservation.promise_id in keep_ids
            ]
            for zone, reservations in self.state.promise_reservations.items()
        }
        self.state.promise_reservations = {
            zone: reservations
            for zone, reservations in self.state.promise_reservations.items()
            if reservations
        }

    def _trim_promises(self) -> None:
        if len(self.state.promises) <= PROMISE_LEDGER_LIMIT:
            return
        ranked = sorted(
            self.state.promises,
            key=lambda promise: (
                promise.status in {"realized", "fulfilled", "redeemed"},
                promise.salience,
                promise.confidence,
                promise.source_turn,
                promise.id,
            ),
        )
        keep = set(promise.id for promise in ranked[-PROMISE_LEDGER_LIMIT:])
        self.state.promises = [
            promise for promise in self.state.promises if promise.id in keep
        ]

    def _invalidate_pending_town_generation(
        self, zone: tuple[int, int] | None = None
    ) -> None:
        if not self._pending_towns:
            return
        if zone is not None:
            future = self._pending_towns.pop(zone, None)
            self._pending_town_contexts.pop(zone, None)
            self._pending_town_start_times.pop(zone, None)
            if future is not None:
                future.cancel()
            return
        for future in self._pending_towns.values():
            future.cancel()
        self._pending_towns.clear()
        self._pending_town_contexts.clear()
        self._pending_town_start_times.clear()

    def promises_for_context(
        self,
        *,
        subject: str | None = None,
        tags: set[str] | None = None,
        include_realized: bool = False,
        limit: int = 8,
        text_limit: int = 240,
    ) -> list[dict[str, Any]]:
        wanted_terms = {
            normalize_id(part)
            for part in re.findall(r"[A-Za-z0-9_'-]+", subject or "")
            if len(part) >= 3
        }
        wanted_tags = {normalize_id(tag) for tag in (tags or set()) if tag}
        ranked: list[tuple[int, WorldPromise]] = []
        for promise in self.state.promises:
            if (
                promise.status in {"realized", "fulfilled", "redeemed"}
                and not include_realized
            ):
                continue
            promise_terms = set(normalize_id(promise.subject).split("_"))
            promise_terms.update(normalize_id(tag) for tag in promise.tags)
            score = promise.salience * 10 + int(promise.confidence * 5)
            if promise.origin_zone == (self.state.zone_x, self.state.zone_y):
                score += 12
            if wanted_terms & promise_terms:
                score += 18
            if wanted_tags & promise_terms:
                score += 10
            if promise.location == self.state.location_label():
                score += 6
            ranked.append((score, promise))
        ranked.sort(
            key=lambda item: (-item[0], -item[1].source_turn, item[1].subject.lower())
        )
        return promise_context_for_prompt(
            [promise for _, promise in ranked[:limit]],
            limit=limit,
            text_limit=text_limit,
        )

    def promise_hooks_for_zone(
        self, zone: tuple[int, int], *, limit: int = 3, text_limit: int = 240
    ) -> list[dict[str, Any]]:
        reservations = self.state.promise_reservations.get(zone, [])[:limit]
        by_id = {promise.id: promise for promise in self.state.promises}
        hooks: list[dict[str, Any]] = []
        for reservation in reservations:
            promise = by_id.get(reservation.promise_id)
            if promise is None or promise.status in {
                "realized",
                "fulfilled",
                "redeemed",
            }:
                continue
            hook = promise_context_for_prompt(
                [promise], limit=1, text_limit=text_limit
            )[0]
            hook["blueprint"] = reservation.blueprint
            hook["bound_zone"] = list(reservation.zone)
            hooks.append(hook)
        return hooks

    def lore_extraction_context(
        self, npc: Entity, message: str, reply: str
    ) -> dict[str, Any]:
        return {
            "npc": npc.name,
            "turn": self.state.turn,
            "location": self.state.location_label(),
            "zone": {
                "x": self.state.zone_x,
                "y": self.state.zone_y,
                "type": self.state.zone_type,
            },
            "message": message,
            "reply": reply,
            "npc_profile": self.state.npc_profiles[npc.id].to_dialogue_context(),
            "existing_lore": self.promises_for_context(
                subject=npc.name, tags=npc.tags, limit=5, text_limit=160
            ),
        }

    def dialogue_context_for_llm(self, npc: Entity, message: str) -> dict[str, Any]:
        profile = self.state.npc_profiles[npc.id]
        player = self.state.player
        # How the NPC refers to the player: their chosen name if they gave one,
        # otherwise the body they're in (its name, or "a wandering stranger" for the
        # nameless default player whose entity name is the second-person "You").
        player_profile = player.profile
        player_name = (player_profile.name if player_profile else "") or player.name
        if player_name == "You":
            player_name = "a wandering stranger"
        # What the NPC sees of the player — physical description, not magical signature.
        player_appearance = player.description or (
            player_profile.appearance if player_profile else ""
        )
        player_block: dict[str, Any] = {
            "name": player_name,
            "hp": player.hp,
            "max_hp": player.max_hp,
            "statuses": sorted(player.statuses),
            "equipment": {
                slot: item for slot, item in player.equipment.items() if item
            },
        }
        if player_appearance:
            player_block["appearance"] = player_appearance
        return {
            "npc": profile.to_dialogue_context(),
            "player": player_block,
            "scene": {
                "turn": self.state.turn,
                "depth": self.state.depth,
                "scenario": self.state.scenario,
                "region": self.region.name,
            },
            "nearby_objects": self._npc_nearby_objects(npc),
            "relevant_lore": self.promises_for_context(
                subject=npc.name, tags=npc.tags, limit=5, text_limit=160
            ),
            "message": message,
        }

    def _npc_nearby_objects(
        self, npc: Entity, radius: int = NPC_PERCEPTION_RADIUS, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Props and loose items within the NPC's perception, nearest first, so the NPC
        can talk about the objects in the room around them."""
        nearby: list[tuple[int, str, Entity]] = []
        for entity in self.state.entities.values():
            if entity.kind not in {"prop", "item"} or entity.id == npc.id:
                continue
            distance = max(abs(entity.x - npc.x), abs(entity.y - npc.y))
            if distance <= radius:
                nearby.append((distance, entity.id, entity))
        nearby.sort()
        return [
            {
                "name": entity.name,
                "what": "object" if entity.kind == "prop" else "loose item",
                "description": entity.description,
                "tags": sorted(entity.tags),
            }
            for _, _, entity in nearby[:limit]
        ]

    def should_consider_trade(self, npc: Entity, message: str, reply: str) -> bool:
        """Stages 1+2 of the trigger funnel that gates the expensive structuring
        call (resolve_trade_proposal): an NPC with nothing to trade can never
        produce a real proposal (free check, eliminates almost everyone), and an
        exchange that doesn't even brush against trade-ish language essentially
        never turns into one either (cheap in-process keyword scan). Only when
        both hold is it worth spending a ~6-12s LLM round trip to find out for sure."""
        profile = self.state.npc_profiles.get(npc.id)
        if profile is None or not profile.wares:
            return False
        return scan_for_trade_intent(message, reply)

    def trade_context_for_llm(
        self, npc: Entity, message: str, reply: str
    ) -> dict[str, Any]:
        profile = self.state.npc_profiles[npc.id]
        player = self.state.player
        return {
            "npc": profile.to_dialogue_context(),
            "player": {
                "name": player.name,
                "inventory": dict(sorted(self.state.inventory.items())),
                "equipment": {
                    slot: item for slot, item in player.equipment.items() if item
                },
            },
            "scene": {
                "turn": self.state.turn,
                "depth": self.state.depth,
                "scenario": self.state.scenario,
            },
            "exchange": {"player_said": message, "npc_replied": reply},
        }

    def _validate_trade_payload(
        self, npc: Entity, trade_data: dict[str, Any]
    ) -> str | None:
        profile = self.state.npc_profiles.get(npc.id)
        if profile is None:
            return "trader no longer exists"
        for label, entries, source in (
            ("npc_gives", coerce_list(trade_data.get("npc_gives")), profile.wares),
            (
                "npc_wants",
                coerce_list(trade_data.get("npc_wants")),
                self.state.inventory,
            ),
        ):
            for entry in entries:
                if not isinstance(entry, dict):
                    return f"{label} contains a malformed entry"
                item = str(entry.get("item") or "").strip()
                try:
                    quantity = int(entry.get("quantity") or 0)
                except (TypeError, ValueError):
                    return f"{label} has an invalid quantity for {item or '(missing item)'}"
                key = self.find_item_in(source, item) if item else None
                if key is None:
                    owner = (
                        "the trader's wares"
                        if label == "npc_gives"
                        else "your inventory"
                    )
                    return f"{item or '(missing item)'} is not in {owner}"
                available = source.get(key, 0)
                if quantity < 1 or quantity > available:
                    return f"{key} quantity is unavailable ({quantity} requested, {available} available)"
        return None

    def apply_dialogue_exchange(
        self,
        npc: Entity,
        message: str,
        reply: str,
        trade_data: dict[str, Any] | None = None,
    ) -> None:
        """Record + display the exchange, then either settle the turn immediately
        (the normal case) or -- when the structuring call came back with a real
        proposal -- stash it as `pending_trade` and stop short of finishing the
        turn. The confirmation modal takes over from there; accepting or rejecting
        (resolve_pending_trade) is what reaches finish_player_turn for this beat,
        exactly as the two branches of apply_wild_magic_resolution each
        independently reach it exactly once."""
        profile = self.state.npc_profiles[npc.id]
        profile.record_exchange("player", message)
        profile.record_exchange("npc", reply)
        self.state.add_message(f'You say to {npc.name}: "{message}"')
        self.state.add_message(f'{npc.name} says: "{reply}"')

        # Track last talked NPC and auto-register active quest item
        self.state.last_talked_npc_name = npc.name
        if profile.wanted_item and not profile.quest_completed:
            from .npc_quests import register_heard_quest_item

            register_heard_quest_item(self, npc.id)

        if trade_data is not None and trade_data.get("trade_proposed"):
            trade_error = self._validate_trade_payload(npc, trade_data)
            if trade_error:
                self.state.add_message(
                    f"The proposed trade cannot be settled: {trade_error}."
                )
                self.finish_player_turn()
                return
            proposal_text = str(trade_data.get("proposal_text") or "").strip()
            self.state.pending_trade = {
                "npc_id": npc.id,
                "npc_name": npc.name,
                "initiator": trade_data.get("initiator"),
                "npc_gives": [
                    dict(entry) for entry in coerce_list(trade_data.get("npc_gives"))
                ],
                "npc_wants": [
                    dict(entry) for entry in coerce_list(trade_data.get("npc_wants"))
                ],
                "proposal_text": proposal_text,
            }
            if proposal_text:
                self.state.add_message(
                    f'{npc.name} proposes a trade: "{proposal_text}"'
                )
            return

        self.finish_player_turn()

    def resolve_pending_trade(self, accept: bool) -> None:
        """Settle the trade apply_dialogue_exchange paused on -- the second half
        of an interrupted player-turn beat. Both branches independently reach
        finish_player_turn exactly once, mirroring how apply_wild_magic_resolution's
        accept/reject branches each do. Two transfers plus two messages is the
        whole operation; deliberately not wrapped in a transaction abstraction."""
        trade = self.state.pending_trade
        if trade is None:
            return
        self.state.pending_trade = None
        npc = self.state.entities.get(trade["npc_id"])
        profile = self.state.npc_profiles.get(trade["npc_id"])
        npc_name = trade.get("npc_name", "the trader")

        if not accept or npc is None or profile is None:
            self.state.add_message(f"You step back from the deal with {npc_name}.")
            self.finish_player_turn()
            return

        trade_error = self._validate_trade_payload(npc, trade)
        if trade_error:
            self.state.add_message(
                f"The deal with {npc_name} falls apart: {trade_error}."
            )
            self.finish_player_turn()
            return

        received: list[str] = []
        for entry in trade.get("npc_gives", []):
            name = str(entry.get("item", "")).strip()
            quantity = max(0, int(entry.get("quantity", 0) or 0))
            if not name or quantity <= 0:
                continue
            key = self.find_item_in(profile.wares, name) or name
            taken = self.consume_inventory_item(key, quantity, container=profile.wares)
            if taken:
                self.add_inventory_item(self.state.inventory, key, taken)
                received.append(f"{taken} {key}")

        given: list[str] = []
        for entry in trade.get("npc_wants", []):
            name = str(entry.get("item", "")).strip()
            quantity = max(0, int(entry.get("quantity", 0) or 0))
            if not name or quantity <= 0:
                continue
            key = self.find_inventory_item(name) or name
            taken = self.consume_inventory_item(key, quantity)
            if taken:
                self.add_inventory_item(profile.wares, key, taken)
                given.append(f"{taken} {key}")

        receive_text = ", ".join(received) or "nothing"
        give_text = ", ".join(given) or "nothing"
        self.state.add_message(
            f"Deal struck with {npc_name} -- you receive {receive_text}, and hand over {give_text}."
        )

        # Check if this trade fulfills the NPC's quest/need
        if profile.wanted_item and not profile.quest_completed:
            for entry in trade.get("npc_wants", []):
                item_name = str(entry.get("item") or "").strip().lower()
                qty = int(entry.get("quantity") or 0)
                if (
                    item_name == profile.wanted_item.lower()
                    and qty >= profile.wanted_qty
                ):
                    profile.quest_completed = True
                    self.state.add_message(
                        f"Quest completed: You delivered {profile.wanted_item} to {profile.name}!"
                    )
                    for promise in self.state.promises:
                        if (
                            promise.kind == "quest"
                            and promise.giver_npc == profile.name
                            and promise.status != "fulfilled"
                        ):
                            promise.status = "fulfilled"
                    break

        self.finish_player_turn()

    def open_door(self, x: int, y: int) -> bool:
        if self.tile_at(x, y) != DOOR:
            return False
        self.state.tiles[y][x] = OPEN_DOOR
        self.state.tile_tags.pop(self.tile_key(x, y), None)
        self.state.add_message("The door opens.")
        self.update_fov()
        return True

    def open_adjacent_door(self) -> bool:
        player = self.state.player
        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            if self.open_door(player.x + dx, player.y + dy):
                self.finish_player_turn()
                return True
        self.state.add_message("There is no closed door nearby.")
        return False

    def descend_stairs(self) -> bool:
        player = self.state.player
        if self.tile_at(player.x, player.y) != STAIRS_DOWN:
            self.state.add_message("There are no downward stairs here.")
            return False
        if self.state.depth >= self.state.max_depth:
            self.state.victory = True
            self.state.game_over = True
            self.state.turn += 1
            self.state.add_message(
                "You descend past the last stair and escape with your impossible magic intact."
            )
            return True

        # Save current floor before transitioning
        is_surface = self.state.depth == 1 and self.state.scenario != "dungeon"
        if is_surface:
            self._save_current_zone()
        else:
            self._save_dungeon_floor(self.state.depth)

        self.state.depth += 1
        self.state.stats.deepest_floor = max(
            self.state.stats.deepest_floor, self.state.depth
        )

        # Load or generate the next dungeon floor
        if self.state.depth in self.state.dungeon_floors:
            self._load_dungeon_floor(self.state.depth, STAIRS_UP)
        else:
            self._generate_dungeon_floor(preserve_player=True)

        self.state.turn += 1
        self.update_fov()
        self.state.add_message(f"You descend to dungeon floor {self.state.depth}.")
        return True

    def ascend_stairs(self) -> bool:
        player = self.state.player
        if self.tile_at(player.x, player.y) != STAIRS_UP:
            self.state.add_message("There are no upward stairs here.")
            return False
        if self.state.depth <= 1:
            self.state.add_message("The dungeon mouth is not that easy to find again.")
            return False

        # Save current dungeon floor
        self._save_dungeon_floor(self.state.depth)

        self.state.depth -= 1

        # Are we returning to the surface?
        is_surface = self.state.depth == 1 and self.state.scenario != "dungeon"
        if is_surface:
            key = (self.state.zone_x, self.state.zone_y)
            if key in self.state.zones:
                snapshot = self.state.zones[key]
                stairs_x, stairs_y = player.x, player.y
                found = False
                for y, row in enumerate(snapshot.tiles):
                    for x, tile in enumerate(row):
                        if tile == STAIRS_DOWN:
                            stairs_x, stairs_y = x, y
                            found = True
                            break
                    if found:
                        break
                self._load_or_generate_zone(
                    self.state.zone_x, self.state.zone_y, stairs_x, stairs_y
                )
            else:
                self._load_or_generate_zone(
                    self.state.zone_x, self.state.zone_y, player.x, player.y
                )
            self.state.turn += 1
            self.update_fov()
            self.state.add_message("You climb back to the surface.")
            return True
        else:
            if self.state.depth in self.state.dungeon_floors:
                self._load_dungeon_floor(self.state.depth, STAIRS_DOWN)
            else:
                self._generate_dungeon_floor(preserve_player=True)
            self.state.turn += 1
            self.update_fov()
            self.state.add_message(
                f"You climb back to dungeon floor {self.state.depth}."
            )
            return True

    def swap_control_to(self, target_id: str) -> list[str]:
        """Move the locus of control into another entity — body-swap / possession.

        Per docs/ENTITY_UNIFICATION.md the whole point of unifying PC/NPC/enemy onto
        one entity model is that this needs no special cases:

        - You inherit the body's stats/abilities, HP, mana, and profile, because you
          literally *become* that entity — `player_id` now points at it, and every
          player-centric system (casting, inventory, FOV, combat ownership, the LLM
          caster profile) follows that one pointer.
        - Inventory stays with the body: it is per-entity, so nothing is carried over.
        - Identity follows the body: the inhabited entity keeps its own name and
          appearance; only the avatar glyph and the controlling faction change.
        - The vacated body becomes an inert husk: no AI, neutral faction, tagged
          ``husk`` and left unconscious until something re-inhabits it.
        """
        state = self.state
        target = state.entities.get(target_id)
        old_id = state.player_id
        if target is None or not target.alive or target.kind in {"item", "prop"}:
            return ["There is no body there to inhabit."]
        if target_id == old_id:
            return ["You already wear this body."]

        old = state.entities.get(old_id)
        if old is not None:
            # The body keeps its own glyph and appearance — it's still that body, just
            # emptied — but loses all agency until something re-inhabits it.
            old.kind = "actor"
            old.faction = "neutral"
            old.ai = None
            old.blocks = True
            old.tags.discard("npc")
            old.tags.add("husk")
            old.statuses["unconscious"] = "permanent"
            if old.name == "You":
                old.name = "your emptied body"

        state.player_id = target_id
        target.kind = "player"
        target.faction = "player"
        target.ai = None
        target.char = "@"
        target.blocks = True
        target.tags.discard("husk")
        target.statuses.pop("unconscious", None)
        if target.profile is None:
            target.profile = CharacterProfile()
        self.update_fov()
        return [f"Your soul tears free and pours into {target.name}."]

    def cast_standard_bolt(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if player.mana < 2:
            self.state.add_message("The safe spell fizzles. You need 2 mana.")
            return False
        target = self.nearest_enemy(max_distance=8)
        if target is None:
            self.state.add_message("No enemy is close enough for a spark bolt.")
            return False
        player.mana -= 2
        self.damage_entity(target, 5, "spark")
        self.state.add_message(f"A tidy spark bolt hits {target.name}.")
        self.finish_player_turn()
        return True

    def cast_standard_frost(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if player.mana < 2:
            self.state.add_message("The safe spell fizzles. You need 2 mana.")
            return False
        target = self.nearest_enemy(max_distance=6)
        if target is None:
            self.state.add_message("No enemy is close enough for a frost shard.")
            return False
        player.mana -= 2
        self.damage_entity(target, 4, "frost")
        if target.hp > 0:
            target.statuses["slowed"] = max(
                status_duration(target.statuses.get("slowed")), 2
            )
            self.state.add_message(
                f"A frost shard bites into {target.name}, slowing it."
            )
        else:
            self.state.add_message(f"A frost shard bites into {target.name}.")
        self.finish_player_turn()
        return True

    def cast_standard_heal(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if player.mana < 3:
            self.state.add_message("The safe spell fizzles. You need 3 mana.")
            return False
        player.mana -= 3
        actual = self.heal_entity(player, 4)
        if actual == 0:
            self.state.add_message("Your wounds are already mended.")
        else:
            self.state.add_message(f"A minor heal mends {actual} HP.")
        self.finish_player_turn()
        return True

    def cast_standard_ward(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if player.mana < 3:
            self.state.add_message("The safe spell fizzles. You need 3 mana.")
            return False
        player.mana -= 3
        player.statuses["warded"] = max(
            status_duration(player.statuses.get("warded")), 6
        )
        self.state.add_message(
            "A steady ward settles over you, dulling the next blows."
        )
        self.finish_player_turn()
        return True

    def cast_standard_reveal(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if player.mana < 2:
            self.state.add_message("The safe spell fizzles. You need 2 mana.")
            return False
        player.mana -= 2
        found = 0
        for entity in self.entities_in_radius(player.x, player.y, 8):
            if (
                entity.id == self.state.player_id
                or entity.kind != "actor"
                or entity.hp <= 0
            ):
                continue
            entity.statuses["revealed"] = max(
                status_duration(entity.statuses.get("revealed")), 6
            )
            found += 1
        if found:
            self.state.add_message(
                f"Your senses sharpen. {found} hidden presence(s) stand revealed nearby."
            )
        else:
            self.state.add_message(
                "Your senses sharpen, but nothing nearby is hiding from you."
            )
        self.finish_player_turn()
        return True

    def nearest_enemy(self, max_distance: int | None = None) -> Entity | None:
        player = self.state.player
        enemies = self.living_enemies()
        if max_distance is not None:
            enemies = [
                enemy
                for enemy in enemies
                if self.distance(player, enemy) <= max_distance
            ]
        if not enemies:
            return None
        return min(enemies, key=lambda enemy: self.distance(player, enemy))

    def teleport_entity(self, entity: Entity, x: int, y: int) -> bool:
        if self.can_occupy(x, y):
            entity.x = x
            entity.y = y
            if entity.id == self.state.player_id:
                self.pick_up_items_at_player()
                self.update_fov()
            self._apply_tile_entry(entity)
            return True
        return False

    def _move_to_nearest_open_tile(self, entity: Entity) -> bool:
        for radius in range(1, 8):
            for y in range(entity.y - radius, entity.y + radius + 1):
                for x in range(entity.x - radius, entity.x + radius + 1):
                    if self.can_occupy(x, y):
                        entity.x = x
                        entity.y = y
                        return True
        return False

    def finish_player_turn(self) -> None:
        if self.state.game_over:
            return
        self.state.turn += 1
        self._tick_environment()
        self._tick_tile_durations()
        self._tick_event_timers()
        self._tick_triggers()
        self.update_fov()
        self._enemy_turns()
        self._ally_turns()
        self._npc_turns()
        self._process_entity_behaviors()
        self._regenerate_player()
        self._ambient_sounds()
        self._update_npc_perceptions()

    def _ambient_sounds(self) -> None:
        if self.rng.random() > 0.12:
            return
        region = self.region
        unseen_enemies = [
            e for e in self.living_enemies() if not self.is_visible(e.x, e.y)
        ]
        if not unseen_enemies:
            # No threat nearby: the place itself speaks. Strangeness scales
            # with effective wildness (region base + depth) — surveyed and
            # sensible near imperial reach, dreamlike in the deep wild.
            self.state.add_message(
                self.rng.choice(list(region.wonder_lines(self.state.depth)))
            )
            return
        enemy = self.rng.choice(unseen_enemies)
        messages = list(region.ambient_default)
        for tag, tag_messages in region.ambient_by_tag.items():
            if tag in enemy.tags:
                messages = list(tag_messages)
                break
        self.state.add_message(self.rng.choice(messages))

    def _tick_environment(self) -> None:
        for entity in list(self.state.entities.values()):
            if entity.kind == "item" or entity.hp <= 0:
                continue
            tile = self.tile_at(entity.x, entity.y)
            is_player = entity.id == self.state.player_id
            if tile == FIRE:
                self.damage_entity(entity, 1, "fire")
                if entity.hp > 0:
                    entity.statuses["burning"] = max(
                        status_duration(entity.statuses.get("burning")), 2
                    )
                    self.state.add_message(
                        "You are scorched by wild fire."
                        if is_player
                        else f"{entity.name} is scorched by wild fire.",
                        is_danger=is_player,
                    )
            elif tile == POISON_CLOUD:
                self.damage_entity(entity, 1, "poison")
                if entity.hp > 0:
                    entity.statuses["poisoned"] = max(
                        status_duration(entity.statuses.get("poisoned")), 2
                    )
                    self.state.add_message(
                        "You cough in poison vapors."
                        if is_player
                        else f"{entity.name} coughs in poison vapors.",
                        is_danger=is_player,
                    )
            elif tile == WATER and "burning" in entity.statuses:
                entity.statuses.pop("burning")
                if is_player:
                    self.state.add_message("The water extinguishes your flames.")
                else:
                    self.state.add_message(f"{entity.name} is doused by the water.")
            elif (
                tile == VINES
                and "rooted" not in entity.statuses
                and "webbed" not in entity.statuses
            ):
                entity.statuses["rooted"] = 2
                if is_player:
                    self.state.add_message("Vines coil around your feet!")
                else:
                    self.state.add_message(f"{entity.name} is snared by vines.")

            _is_player = entity.id == self.state.player_id
            if "burning" in entity.statuses:
                turns = status_duration(entity.statuses["burning"])
                self.damage_entity(entity, 1, "fire")
                if entity.hp > 0:
                    burn_name = entity.status_display.get("burning", "burning")
                    self.state.add_message(
                        "You burn."
                        if _is_player
                        else f"{entity.name} burns ({burn_name}).",
                        is_danger=_is_player,
                    )
                turns -= 1
                if turns <= 0:
                    entity.statuses.pop("burning", None)
                    entity.status_display.pop("burning", None)
                    entity.status_expiry_text.pop("burning", None)
                else:
                    entity.statuses["burning"] = turns
            if "poisoned" in entity.statuses:
                turns = status_duration(entity.statuses["poisoned"])
                self.damage_entity(entity, 1, "poison")
                if entity.hp > 0:
                    poison_name = entity.status_display.get("poisoned", "poison")
                    self.state.add_message(
                        "You weaken from poison."
                        if _is_player
                        else f"{entity.name} weakens ({poison_name}).",
                        is_danger=_is_player,
                    )
                turns -= 1
                if turns <= 0:
                    entity.statuses.pop("poisoned", None)
                    entity.status_display.pop("poisoned", None)
                    entity.status_expiry_text.pop("poisoned", None)
                else:
                    entity.statuses["poisoned"] = turns
            self._tick_simple_statuses(entity)
        self._tick_fire_spread()
        self._tick_poison_spread()

    def _tick_fire_spread(self) -> None:
        fire_tiles = [
            (x, y)
            for y, row in enumerate(self.state.tiles)
            for x, tile in enumerate(row)
            if tile == FIRE
        ]
        for fx, fy in fire_tiles:
            if self.rng.random() > 0.25:
                continue
            dx, dy = self.rng.choice([(0, -1), (0, 1), (-1, 0), (1, 0)])
            nx, ny = fx + dx, fy + dy
            if not self.in_bounds(nx, ny):
                continue
            neighbor = self.tile_at(nx, ny)
            if neighbor == WATER:
                self.set_tile(nx, ny, MIST, duration=3)
                self.set_tile(fx, fy, MIST, duration=2)
            elif "flammable" in TILE_TAGS.get(neighbor, set()):
                self.set_tile(nx, ny, FIRE, duration=4)

    def _tick_poison_spread(self) -> None:
        poison_tiles = [
            (x, y)
            for y, row in enumerate(self.state.tiles)
            for x, tile in enumerate(row)
            if tile == POISON_CLOUD
        ]
        for px, py in poison_tiles:
            if self.rng.random() > 0.15:
                continue
            dx, dy = self.rng.choice([(0, -1), (0, 1), (-1, 0), (1, 0)])
            nx, ny = px + dx, py + dy
            if not self.in_bounds(nx, ny) or self.tile_at(nx, ny) not in {FLOOR, MIST}:
                continue
            self.set_tile(nx, ny, POISON_CLOUD, duration=3)

    def _apply_tile_entry(self, entity: Entity) -> None:
        tile = self.tile_at(entity.x, entity.y)
        is_player = entity.id == self.state.player_id
        trap_tag = next(
            (tag for tag in self.tile_tags_at(entity.x, entity.y) if tag in TRAP_SPECS),
            None,
        )
        if trap_tag is not None:
            spec = TRAP_SPECS[trap_tag]
            self.damage_entity(entity, spec["damage"], spec["damage_type"])
            if entity.alive:
                entity.statuses[spec["status"]] = max(
                    status_duration(entity.statuses.get(spec["status"])),
                    spec["duration"],
                )
            self.state.add_message(
                spec["message"]
                if is_player
                else spec["message_other"].format(name=entity.name),
                is_danger=is_player,
            )
            self.set_tile(entity.x, entity.y, RUBBLE)
            self.state.tile_tags.pop(self.tile_key(entity.x, entity.y), None)
            tile = RUBBLE
        # Non-player entities auto-open closed doors they walk into.
        if tile == DOOR and not is_player:
            self.state.tiles[entity.y][entity.x] = OPEN_DOOR
            self.update_fov()
            tile = OPEN_DOOR
        if tile == FIRE:
            self.damage_entity(entity, 1, "fire")
            entity.statuses["burning"] = max(
                status_duration(entity.statuses.get("burning")), 2
            )
            self.state.add_message(
                "You step into wild fire."
                if is_player
                else f"{entity.name} steps into wild fire.",
                is_danger=is_player,
            )
        elif tile == POISON_CLOUD:
            self.damage_entity(entity, 1, "poison")
            entity.statuses["poisoned"] = max(
                status_duration(entity.statuses.get("poisoned")), 2
            )
            self.state.add_message(
                "You inhale a poison cloud."
                if is_player
                else f"{entity.name} inhales a poison cloud.",
                is_danger=is_player,
            )
        elif tile == SLICK_ICE:
            entity.statuses["slowed"] = max(
                status_duration(entity.statuses.get("slowed")), 1
            )
            self.state.add_message(
                "You skid on slick ice."
                if is_player
                else f"{entity.name} skids on slick ice."
            )
        elif tile == WATER and "burning" in entity.statuses:
            entity.statuses.pop("burning")
            if entity.id == self.state.player_id:
                self.state.add_message("The water extinguishes your flames.")
            else:
                self.state.add_message(f"{entity.name} is doused.")
        if (
            tile == VINES
            and "rooted" not in entity.statuses
            and "webbed" not in entity.statuses
        ):
            entity.statuses["rooted"] = 2
            if entity.id == self.state.player_id:
                self.state.add_message("Vines coil around your feet!")
            else:
                self.state.add_message(f"{entity.name} is snared by vines.")

    def _tick_simple_statuses(self, entity: Entity) -> None:
        _sp = entity.id == self.state.player_id
        if "bleeding" in entity.statuses:
            turns = status_duration(entity.statuses["bleeding"])
            self.damage_entity(entity, 1, "blood")
            if entity.hp > 0:
                bleed_name = entity.status_display.get("bleeding", "bleeding")
                self.state.add_message(
                    "You bleed." if _sp else f"{entity.name} bleeds ({bleed_name}).",
                    is_danger=_sp,
                )
            turns -= 1
            if turns <= 0:
                entity.statuses.pop("bleeding", None)
                entity.status_display.pop("bleeding", None)
                entity.status_expiry_text.pop("bleeding", None)
            else:
                entity.statuses["bleeding"] = turns

        if "regenerating" in entity.statuses:
            turns = status_duration(entity.statuses["regenerating"])
            if entity.hp < entity.max_hp:
                entity.hp += 1
                if _sp:
                    regen_name = entity.status_display.get(
                        "regenerating", "regenerating"
                    )
                    self.state.add_message(
                        f"You regenerate ({regen_name})."
                        if regen_name != "regenerating"
                        else "You regenerate."
                    )
            turns -= 1
            if turns <= 0:
                entity.statuses.pop("regenerating", None)
                entity.status_display.pop("regenerating", None)
                entity.status_expiry_text.pop("regenerating", None)
            else:
                entity.statuses["regenerating"] = turns

        _DEFAULT_EXPIRY = {
            "frozen": "You thaw.",
            "stunned": "Your head clears.",
            "rooted": "The grip releases.",
            "webbed": "The webbing falls away.",
            "silenced": "Your voice returns.",
            "invisible": "You become visible again.",
            "berserk": "The rage subsides.",
            "burning": "The flames die out.",
        }
        for status in [
            "frozen",
            "stunned",
            "rooted",
            "webbed",
            "slowed",
            "hasted",
            "confused",
            "frightened",
            "invisible",
            "marked",
            "revealed",
            "warded",
            "strained",
            "drained",
            "jinxed",
            "crawling_skin",
            "silenced",
            "berserk",
            "empowered",
            "cursed",
        ]:
            if status not in entity.statuses:
                continue
            value = entity.statuses[status]
            if value == "permanent":
                continue
            turns = status_duration(value) - 1
            if turns <= 0:
                entity.statuses.pop(status, None)
                custom_expiry = entity.status_expiry_text.pop(status, None)
                entity.status_display.pop(status, None)
                if entity.id == self.state.player_id:
                    msg = custom_expiry or _DEFAULT_EXPIRY.get(status)
                    if msg:
                        self.state.add_message(msg)
            else:
                entity.statuses[status] = turns

    def _tick_tile_durations(self) -> None:
        expired: list[str] = []
        for key, duration in list(self.state.tile_durations.items()):
            next_duration = duration - 1
            if next_duration <= 0:
                expired.append(key)
            else:
                self.state.tile_durations[key] = next_duration
        for key in expired:
            x, y = parse_tile_key(key)
            if self.in_bounds(x, y):
                self.state.tiles[y][x] = FLOOR
            self.state.tile_durations.pop(key, None)
            self.state.tile_tags.pop(key, None)

    def _tick_event_timers(self) -> None:
        remaining: list[dict[str, Any]] = []
        for event in self.state.event_timers:
            turns = clamp_int(event.get("turns"), 0, 999) - 1
            if turns <= 0:
                self._trigger_event(event)
            else:
                event["turns"] = turns
                remaining.append(event)
        self.state.event_timers = remaining

    def _trigger_event(self, event: dict[str, Any]) -> None:
        event_type = str(
            event.get("event_type") or event.get("type") or "message"
        ).lower()
        # Timers are the temporal executor of the Promise Ledger, the way zone
        # generation is its spatial one: a timer carrying a promise_id settles
        # that promise when it fires (e.g. the debt collector arriving).
        promise_id = str(event.get("promise_id") or "")
        if promise_id:
            self.fulfill_promise(promise_id, realized_in=f"turn {self.state.turn}")
        if event_type == "message":
            text = str(
                event.get("text")
                or event.get("message")
                or "Something promised arrives late."
            )
            self.state.add_message(text)
        elif event_type in {"summon", "spawn"}:
            player = self.state.player
            x, y = self.find_open_tile_near(player.x, player.y)
            faction = normalize_faction(
                event.get("faction"), default="ally", neutral_is_ally=True
            )
            name = str(event.get("name") or "summoned creature")
            count = clamp_int(event.get("count") or event.get("quantity") or 1, 1, 6)
            for _ in range(count):
                if not self.can_occupy(x, y):
                    x, y = self.find_open_tile_near(player.x, player.y)
                if not self.can_occupy(x, y):
                    break
                self.spawn_actor(
                    name,
                    str(event.get("char") or ("a" if faction == "ally" else "d"))[:1],
                    x,
                    y,
                    clamp_int(event.get("hp") or 6, 1, 30),
                    clamp_int(event.get("attack") or 2, 0, 10),
                    clamp_int(event.get("defense") or 0, 0, 8),
                    faction,
                    None if faction in {"ally", "player"} else "simple",
                    tags=set(coerce_list(event.get("tags"))),
                )
            self.state.add_message(f"{name} arrives.")
        elif event_type == "conjure":
            self._apply_effect(
                {"type": "conjure_creature", **event, "event_type": None}
            )
        elif event_type in {"damage", "area_damage"}:
            player = self.state.player
            self._apply_effect(
                {"type": event_type, "target": "player", **event, "event_type": None}
            )
        elif event_type in {"heal", "restore_mana"}:
            self._apply_effect(
                {"type": event_type, "target": "player", **event, "event_type": None}
            )
        elif event_type in {"status", "add_status"}:
            self._apply_effect(
                {"type": "add_status", "target": "player", **event, "event_type": None}
            )
        elif event_type == "flood":
            tile = str(event.get("tile") or "water")
            radius = clamp_int(event.get("radius") or 3, 0, 99)
            player = self.state.player
            self._apply_effect(
                {
                    "type": "create_tiles",
                    "target": "player",
                    "tile": tile,
                    "radius": radius,
                    "event_type": None,
                }
            )
            self.state.add_message(f"{TILE_NAMES.get(tile, tile)} floods the area.")
        elif event_type == "curse":
            self._apply_cost({"type": "curse", **event})

    def _tick_triggers(self) -> None:
        remaining: list[dict[str, Any]] = []
        for trigger in self.state.triggers:
            expires_turn = trigger.get("expires_turn")
            if expires_turn is not None:
                if self.state.turn <= clamp_int(expires_turn, 0, 999999):
                    remaining.append(trigger)
                else:
                    name = str(trigger.get("name") or "A waiting spell").strip()
                    self.state.add_message(f"{name} fades.")
                continue
            duration = trigger.get("duration", trigger.get("turns"))
            if duration in {None, "permanent"}:
                remaining.append(trigger)
                continue
            turns = clamp_int(duration, 0, 999) - 1
            if turns > 0:
                trigger = dict(trigger)
                trigger["duration"] = turns
                remaining.append(trigger)
            else:
                name = str(trigger.get("name") or "A waiting spell").strip()
                self.state.add_message(f"{name} fades.")
        self.state.triggers = remaining

    def _fire_damage_triggers(
        self,
        target: Entity,
        source: Entity | None,
        amount: int,
        damage_type: str,
    ) -> None:
        event = {
            "target": target,
            "source": source,
            "amount": amount,
            "damage_type": damage_type,
        }
        names = ["on_damaged", "on_actor_damaged"]
        if target.id == self.state.player_id:
            names.extend(["on_player_damaged", "on_player_hit"])
        elif target.faction == "enemy":
            names.extend(["on_enemy_damaged", "on_enemy_hit"])
        self._fire_triggers(names, event)

    def _fire_death_triggers(
        self,
        target: Entity,
        source: Entity | None,
        previous_hp: int,
        damage_type: str,
    ) -> None:
        event = {
            "target": target,
            "source": source,
            "amount": previous_hp,
            "damage_type": damage_type,
        }
        names = ["on_death", "on_actor_death"]
        if target.id == self.state.player_id:
            names.append("on_player_death")
        elif target.faction == "enemy":
            names.append("on_enemy_death")
        self._fire_triggers(names, event)

    def _fire_triggers(
        self, names: str | list[str], event: dict[str, Any] | None = None
    ) -> list[str]:
        if isinstance(names, str):
            wanted = {normalize_trigger_name(names)}
        else:
            wanted = {normalize_trigger_name(name) for name in names}
        event = event or {}
        messages: list[str] = []
        remaining: list[dict[str, Any]] = []
        original_triggers = list(self.state.triggers)
        self.state.triggers = []
        for trigger in original_triggers:
            trigger_name = normalize_trigger_name(
                str(trigger.get("trigger") or trigger.get("on") or "")
            )
            if trigger_name not in wanted or not self._trigger_matches_target(
                trigger, event
            ):
                remaining.append(trigger)
                continue
            name = str(trigger.get("name") or "A waiting spell").strip()
            self.state.add_message(f"{name} triggers.")
            messages.append(f"{name} triggers.")
            effects = coerce_list(trigger.get("effects") or trigger.get("effect"))
            for raw_effect in effects[:8]:
                if not isinstance(raw_effect, dict):
                    continue
                effect = dict(raw_effect)
                self._fill_trigger_effect_defaults(effect, event)
                for message in self._apply_effect(effect):
                    self.state.add_message(message)
                    messages.append(message)
            charges = clamp_int(trigger.get("charges"), 1, 99) - 1
            if charges > 0:
                trigger = dict(trigger)
                trigger["charges"] = charges
                remaining.append(trigger)
        self.state.triggers = remaining + self.state.triggers
        return messages

    def _trigger_matches_target(
        self, trigger: dict[str, Any], event: dict[str, Any]
    ) -> bool:
        raw_target = trigger.get("target")
        if raw_target in {None, "", "any"}:
            return True
        target = event.get("target")
        source = event.get("source")
        if not isinstance(target, Entity):
            return True
        trigger_target = normalize_id(str(raw_target))
        if trigger_target in {"player", "self", "you"}:
            return target.id == self.state.player_id
        if trigger_target in {"enemy", "nearest_enemy", "all_enemies", "enemies"}:
            return target.faction == "enemy"
        if trigger_target in {"source", "attacker", "caster"}:
            return isinstance(source, Entity)
        return (
            target.id == trigger_target
            or trigger_target in target.tags
            or trigger_target in normalize_id(target.name).split("_")
        )

    def _fill_trigger_effect_defaults(
        self, effect: dict[str, Any], event: dict[str, Any]
    ) -> None:
        target = event.get("target")
        source = event.get("source")
        if not isinstance(target, Entity):
            return
        if effect.get("target") == "trigger_target":
            effect["target"] = target.id
        elif effect.get("target") == "trigger_source":
            effect["target"] = source.id if isinstance(source, Entity) else target.id
        elif "target" not in effect:
            effect["target"] = target.id
        if effect.get("origin") == "trigger_target":
            effect["origin"] = target.id
        elif effect.get("origin") == "trigger_source" and isinstance(source, Entity):
            effect["origin"] = source.id

        # Disciplined troops hold their post rather than break formation to wander.

    _SUMMONER_MINIONS = ["bog whelp", "carrion sprite", "husk crawler"]

    def _regenerate_player(self) -> None:
        player = self.state.player
        if self.state.turn % 5 == 0 and player.mana < player.max_mana:
            player.mana += 1

    def resolve_target(self, target_id: str | None) -> Entity | None:
        if not target_id or target_id in {"player", "self", "@", "you", "me"}:
            return self.state.player
        if target_id in {
            "nearest_enemy",
            "nearest enemy",
            "enemy",
            "nearest_foe",
            "nearest_entity",
            "nearest_target",
            "closest_enemy",
            "target",
            "foe",
            "nearest_actor",
        }:
            return self.nearest_enemy()
        return self.state.entities.get(target_id)

    def resolve_target_group(self, target_id: str | None) -> list[Entity]:
        target = normalize_id(str(target_id or ""))
        if target in {"all", "everyone", "all_entities", "all_nearby", "everything"}:
            return [
                entity
                for entity in self.state.entities.values()
                if entity.kind in {"actor", "npc"} and entity.hp > 0
            ]
        if target in {
            "all_enemies",
            "enemies",
            "all_foes",
            "all_hostiles",
            "nearby_enemies",
            "every_enemy",
        }:
            return self.living_enemies()
        if target in {"allies", "all_allies", "friends", "friendlies"}:
            return [
                entity
                for entity in self.state.entities.values()
                if entity.kind in {"actor", "npc"}
                and entity.hp > 0
                and entity.faction in {"ally", "player"}
            ]
        singular = singular_target_tag(target)
        if not singular:
            return []
        return [
            entity
            for entity in self.state.entities.values()
            if entity.kind in {"actor", "npc"}
            and entity.hp > 0
            and entity.id != self.state.player_id
            and (
                singular in entity.tags
                or singular in normalize_id(entity.name).split("_")
            )
        ]

    def _verb(self, entity: Entity, second_person: str, third_person: str) -> str:
        """Pick the grammatically correct verb for f"{entity.name} {verb} ...".

        The player's display name is the second-person pronoun "You", so a
        message built that way needs "take"/"are" for the player but
        "takes"/"is" for anyone else (e.g. "You take 3 damage." vs.
        "cave spider takes 3 damage.").
        """
        return second_person if entity.id == self.state.player_id else third_person

    def nearby_spell_anchors(self, spell: str, limit: int = 8) -> list[dict[str, Any]]:
        """Visible props distilled for the spell resolver.

        Props already appear in nearby_entities, but that list has many entity kinds.
        This compact view tells the model which environmental objects are good spell
        anchors and what normal engine mechanics they suggest.
        """
        player = self.state.player
        spell_terms = {
            normalize_id(part)
            for part in re.findall(r"[A-Za-z0-9_'-]+", spell.lower())
            if len(part) >= 3
        }
        visible_enemies = [
            enemy
            for enemy in self.living_enemies()
            if self.is_visible(enemy.x, enemy.y)
            and abs(enemy.x - player.x) <= self.state.fov_radius
            and abs(enemy.y - player.y) <= self.state.fov_radius
        ]
        anchors: list[tuple[int, dict[str, Any]]] = []
        for entity in self.state.entities.values():
            if (
                entity.kind != "prop"
                or not entity.alive
                or not self.is_visible(entity.x, entity.y)
            ):
                continue
            if (
                abs(entity.x - player.x) > self.state.fov_radius
                or abs(entity.y - player.y) > self.state.fov_radius
            ):
                continue
            tags = sorted(entity.tags)
            name_terms = set(normalize_id(entity.name).split("_"))
            tag_terms = {normalize_id(tag) for tag in tags}
            desc_terms = {
                normalize_id(part)
                for part in re.findall(r"[A-Za-z0-9_'-]+", entity.description or "")
                if len(part) >= 4
            }
            matched_terms = sorted(spell_terms & (name_terms | tag_terms | desc_terms))
            affordances: list[str] = []
            for tag in tags:
                for affordance in _PROP_TAG_AFFORDANCES.get(tag, []):
                    if affordance not in affordances:
                        affordances.append(affordance)
            if not affordances:
                affordances = list(_PROP_GENERIC_AFFORDANCES)
            distance = abs(entity.x - player.x) + abs(entity.y - player.y)
            reactive_tags = {
                "magic",
                "ritual",
                "fire",
                "hot",
                "water",
                "liquid",
                "lightning",
                "toxic",
                "acid",
                "cursed",
                "death",
                "blood",
                "bone",
                "crystal",
                "glass",
                "fragile",
                "mechanical",
                "snaring",
                "trap",
                "empire",
                "holy",
                "music",
            }
            priority = distance
            if matched_terms:
                priority -= 20
            if reactive_tags & entity.tags:
                priority -= 6
            anchor = {
                "id": entity.id,
                "name": entity.name,
                "position": {"x": entity.x, "y": entity.y},
                "distance": distance,
                "tags": tags,
                "description": entity.description,
                "affordances": affordances[:5],
                "suggested_mechanics": [
                    f'use "{entity.id}" as target/center/origin for local effects',
                    'damage/status should usually target creatures; use affects:"enemies" for blasts',
                    "use create_tiles, area_damage, area_status, summon, conjure_item, or create_trigger to express the prop",
                ],
            }
            room = self.room_profile_at(entity.x, entity.y)
            if room is not None:
                anchor["room"] = {
                    "id": room.id,
                    "type": room.room_type,
                    "era": room.era,
                    "condition": room.condition,
                    "topics": list(room.topics),
                    "tags": list(room.tags),
                }
            if matched_terms:
                anchor["matches_spell_terms"] = matched_terms[:6]
            damage_type = "arcane"
            if {"fire", "hot", "flammable"} & entity.tags:
                damage_type = "fire"
            elif {"toxic", "acid", "fungus"} & entity.tags:
                damage_type = "poison"
            elif "lightning" in entity.tags:
                damage_type = "lightning"
            elif "cold" in entity.tags:
                damage_type = "frost"
            elif {"sharp", "heavy", "metal", "stone", "glass", "broken"} & entity.tags:
                damage_type = "physical"
            elif {"holy", "light"} & entity.tags:
                damage_type = "radiant"
            elif {"cursed", "death"} & entity.tags:
                damage_type = "shadow"

            terrain_tile = "mist"
            if {"fire", "hot", "flammable"} & entity.tags:
                terrain_tile = "fire"
            elif {"toxic", "acid", "fungus"} & entity.tags:
                terrain_tile = "poison_cloud"
            elif {"water", "wet", "liquid"} & entity.tags:
                terrain_tile = "water"
            elif "cold" in entity.tags:
                terrain_tile = "slick_ice"
            elif {"plant", "snaring", "rope", "silk"} & entity.tags:
                terrain_tile = "vines"
            elif {"stone", "debris", "broken", "heavy"} & entity.tags:
                terrain_tile = "rubble"

            if visible_enemies:
                nearest_enemy = min(
                    visible_enemies,
                    key=lambda enemy: abs(enemy.x - entity.x) + abs(enemy.y - entity.y),
                )
                enemy_distance = abs(nearest_enemy.x - entity.x) + abs(
                    nearest_enemy.y - entity.y
                )
                anchor["nearest_visible_enemy"] = {
                    "id": nearest_enemy.id,
                    "name": nearest_enemy.name,
                    "distance": enemy_distance,
                }
                if enemy_distance > 4:
                    anchor["range_hint"] = (
                        "small area_damage centered here may miss that enemy; consider direct damage "
                        "on the enemy, or a create_tiles line/beam from this prop toward nearest_enemy"
                    )
                    anchor["recommended_effect_patterns"] = [
                        {
                            "type": "damage",
                            "target": nearest_enemy.id,
                            "damage_type": damage_type,
                        },
                        {
                            "type": "create_tiles",
                            "shape": "line",
                            "origin": entity.id,
                            "target": nearest_enemy.id,
                            "tile": terrain_tile,
                            "duration": 3,
                        },
                    ]
                else:
                    anchor["recommended_effect_patterns"] = [
                        {
                            "type": "area_damage",
                            "target": entity.id,
                            "radius": max(1, enemy_distance),
                            "damage_type": damage_type,
                            "include_player": False,
                            "affects": "enemies",
                        }
                    ]
            else:
                anchor["recommended_effect_patterns"] = [
                    {
                        "type": "create_tiles",
                        "target": entity.id,
                        "radius": 1,
                        "tile": terrain_tile,
                        "duration": 4,
                    }
                ]
            anchors.append((priority, anchor))
        anchors.sort(key=lambda item: (item[0], item[1]["distance"], item[1]["name"]))
        return [anchor for _, anchor in anchors[:limit]]

    def context_for_llm(self, spell: str) -> dict[str, Any]:
        player = self.state.player
        current_room = self.room_profile_at(player.x, player.y)
        current_room_tags = set(current_room.tags if current_room else [])
        current_room_tags.update(current_room.topics if current_room else [])
        nearby_entities = [
            entity.to_public_dict()
            for entity in self.state.entities.values()
            if entity.alive
            and self.is_visible(entity.x, entity.y)
            and abs(entity.x - player.x) <= self.state.fov_radius
            and abs(entity.y - player.y) <= self.state.fov_radius
        ]
        floor_items = [
            {
                "id": e.id,
                "name": e.name,
                "item_type": e.item_type,
                "material": e.material,
                "quantity": e.quantity,
                "x": e.x,
                "y": e.y,
                "tags": sorted(e.tags),
            }
            for e in self.state.entities.values()
            if e.kind == "item"
            and self.is_visible(e.x, e.y)
            and abs(e.x - player.x) <= self.state.fov_radius
            and abs(e.y - player.y) <= self.state.fov_radius
        ]
        return {
            "spell": spell,
            # Consumed by the prompt builder (spliced into the system prompt),
            # stripped from the user-message JSON. See _wild_prompt_messages.
            "region_style": self.region.prompt_style(),
            # The profile of whoever is currently controlled (the player, or a body
            # they've swapped into). Carries the Composure volatility band, plus the
            # appearance/backstory/signature flavor lenses, so the resolver styles
            # casts for the soul-in-the-body rather than a hardcoded "player".
            "caster_profile": (player.profile or CharacterProfile()).to_public_dict(),
            "turn": self.state.turn,
            "depth": self.state.depth,
            "max_depth": self.state.max_depth,
            "player": player.to_public_dict(),
            "inventory": self.state.inventory,
            "curses": [curse.to_public_dict() for curse in self.state.curses.values()],
            "world_flags": self.state.flags,
            "event_timers": self.state.event_timers,
            "triggers": self.state.triggers,
            "current_room": current_room.to_public_dict() if current_room else None,
            "nearby_rooms": self.visible_room_profiles(),
            "nearby_canon": self.nearby_canon_records(tags=current_room_tags),
            "visible_tile_count": len(self.state.visible),
            "explored_tile_count": len(self.state.explored),
            "nearby_entities": nearby_entities,
            "spell_anchors": self.nearby_spell_anchors(spell),
            "floor_items": floor_items,
            "nearby_map": self.nearby_map_strings(radius=9),
            "nearby_tile_details": self.nearby_tile_details(radius=5),
            "tile_legend": {
                tile: {"name": name, "tags": sorted(TILE_TAGS.get(tile, set()))}
                for tile, name in TILE_NAMES.items()
            },
            "supported_effects": [
                "damage",
                "area_damage",
                "area_status",
                "heal",
                "restore_mana",
                "teleport",
                "push",
                "pull",
                "create_tile",
                "create_tiles",
                "add_status",
                "remove_status",
                "summon",
                "spawn_item",
                "conjure_item",
                "conjure_creature",
                "modify_inventory",
                "transform_entity",
                "change_faction",
                "possess",
                "add_tag",
                "remove_tag",
                "add_resistance",
                "add_weakness",
                "set_flag",
                "schedule_event",
                "create_trigger",
                "message",
            ],
            "supported_costs": [
                "mana",
                "health",
                "max_health",
                "max_mana",
                "item",
                "status",
                "curse",
            ],
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

    def nearby_map_strings(self, radius: int = 9) -> list[str]:
        player = self.state.player
        rows: list[str] = []
        for y in range(player.y - radius, player.y + radius + 1):
            chars: list[str] = []
            for x in range(player.x - radius, player.x + radius + 1):
                if not self.in_bounds(x, y):
                    chars.append(" ")
                    continue
                if not self.is_explored(x, y):
                    chars.append(" ")
                    continue
                entity = self.blocking_entity_at(x, y)
                if entity and self.is_visible(x, y):
                    chars.append(entity.char)
                else:
                    item = next(
                        (
                            candidate
                            for candidate in self.entities_at(x, y)
                            if candidate.kind == "item" and self.is_visible(x, y)
                        ),
                        None,
                    )
                    tile = self.tile_at(x, y)
                    chars.append(
                        item.char
                        if item
                        else (tile if self.is_visible(x, y) else tile.lower())
                    )
            rows.append("".join(chars))
        return rows

    def nearby_tile_details(self, radius: int = 5) -> list[dict[str, Any]]:
        player = self.state.player
        details: list[dict[str, Any]] = []
        for y in range(player.y - radius, player.y + radius + 1):
            for x in range(player.x - radius, player.x + radius + 1):
                if not self.in_bounds(x, y):
                    continue
                if not self.is_visible(x, y):
                    continue
                tile = self.tile_at(x, y)
                key = self.tile_key(x, y)
                duration = self.state.tile_durations.get(key)
                if tile != FLOOR or duration is not None or key in self.state.tile_tags:
                    detail = {
                        "x": x,
                        "y": y,
                        "tile": tile,
                        "name": TILE_NAMES.get(tile, "strange"),
                        "tags": sorted(self.tile_tags_at(x, y)),
                        "duration": duration,
                    }
                    room = self.room_profile_at(x, y)
                    if room is not None:
                        detail["room"] = {
                            "id": room.id,
                            "type": room.room_type,
                            "era": room.era,
                            "condition": room.condition,
                        }
                    details.append(detail)
        return details[:60]
