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
    Curse,
    Entity,
    GameStats,
    NPCProfile,
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
from .templates import creature_template, creature_template_ids, item_template, item_template_ids
from .props import get_prop_template, get_all_prop_ids
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
    inventory: dict[str, int] = field(default_factory=lambda: {
        "chalk": 2,
        "grave salt": 2,
        "mana crystal": 2,
        "blood moss": 2,
        "bone shard": 2,
        "viscous residue": 1,
        "metal scrap": 2,
        "arcane residue": 1,
        "gold": 30,
    })
    curses: dict[str, Curse] = field(default_factory=dict)
    npc_profiles: dict[str, NPCProfile] = field(default_factory=dict)
    pending_trade: dict[str, Any] | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    tile_tags: dict[str, list[str]] = field(default_factory=dict)
    tile_durations: dict[str, int] = field(default_factory=dict)
    event_timers: list[dict[str, Any]] = field(default_factory=list)
    triggers: list[dict[str, Any]] = field(default_factory=list)
    game_over: bool = False
    victory: bool = False
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

    @property
    def player(self) -> Entity:
        return self.entities[self.player_id]

    def add_message(self, message: str) -> None:
        self.messages.append(message)
        self.messages = self.messages[-80:]
        # Monotonic; unlike len(messages), it survives the cap above, so callers
        # (e.g. NPC perception) can tell exactly how many messages are new.
        self.message_count += 1


class GameEngine(_CombatMixin, _ItemsMixin, _AIMixin, _GenerationMixin, _EffectsMixin):
    def __init__(self, seed: int | None = None, scenario: str = "dungeon", provider_name: str | None = None) -> None:
        self.rng = random.Random(seed)
        self.state = GameState(rng_seed=seed, scenario=scenario)
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
            normalize_id(str(tag))
            for tag in (tags or set())
            if str(tag).strip()
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
        traits: list[str] | None = None,
        tags: set[str] | None = None,
        wares: dict[str, int] | None = None,
        hp: int = 14,
        attack: int = 2,
        defense: int = 0,
        faction: str = "neutral",
    ) -> Entity:
        """Spawn a talkable NPC: a physical Entity plus a parallel NPCProfile carrying
        persona/memory data (kept separate the same way Curse data lives off-Entity).

        `hp`/`attack`/`defense`/`faction` default to ordinary-townsfolk values, but can
        be overridden for NPCs who are meant to actually fight -- a guard captain who
        holds her ground rather than a peddler who'd rather not be there at all."""
        npc_tags = {normalize_id(str(tag)) for tag in (tags or set()) if str(tag).strip()}
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
        )
        self.state.entities[entity.id] = entity
        self.state.npc_profiles[entity.id] = NPCProfile(
            entity_id=entity.id,
            name=name,
            role=role,
            backstory=backstory,
            traits=list(traits or []),
            wares=dict(wares or {}),
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

    def set_tile(self, x: int, y: int, tile: str, duration: int | None = None, tags: set[str] | None = None) -> bool:
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

    def entities_at(self, x: int, y: int) -> list[Entity]:
        return [entity for entity in self.state.entities.values() if entity.x == x and entity.y == y and entity.alive]

    def blocking_entity_at(self, x: int, y: int) -> Entity | None:
        for entity in self.entities_at(x, y):
            if entity.blocks:
                return entity
        return None

    def living_enemies(self) -> list[Entity]:
        return [
            entity
            for entity in self.state.entities.values()
            if entity.kind == "actor" and entity.faction == "enemy" and entity.hp > 0
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
            if (side_a & actor.tags and side_b & other.tags) or (side_b & actor.tags and side_a & other.tags):
                return True
        return False


    def distance(self, a: Entity, b: Entity) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def attempt_player_move(self, dx: int, dy: int) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if any(s in player.statuses for s in ["rooted", "webbed", "frozen", "stunned"]):
            self.state.add_message("You strain against it, but you cannot move - you are held in place.")
            self.finish_player_turn()
            return True
        target_x = player.x + dx
        target_y = player.y + dy
        if not self.in_bounds(target_x, target_y):
            if self.state.scenario in {"frontier", "town"} and self._cross_zone_edge(target_x, target_y):
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
                    self.state.add_message("The door is locked tight. You'll need the right key.")
                    return False
                self.consume_inventory_item(owned, 1)
                self.state.tile_tags.pop(self.tile_key(target_x, target_y), None)
                self.state.add_message(f"You turn the {owned} in the lock and the door swings open.")
            self.open_door(target_x, target_y)
            self.finish_player_turn()
            return True
        if self.tile_at(target_x, target_y) in BLOCKING_TILES:
            self.state.add_message(f"{TILE_NAMES.get(self.tile_at(target_x, target_y), 'stone')} blocks the way.")
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
            if (self.in_bounds(slide_x, slide_y)
                    and self.tile_at(slide_x, slide_y) not in BLOCKING_TILES
                    and not self.blocking_entity_at(slide_x, slide_y)):
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
        self.state.add_message("You hold still and listen.")
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

    def dialogue_context_for_llm(self, npc: Entity, message: str) -> dict[str, Any]:
        profile = self.state.npc_profiles[npc.id]
        player = self.state.player
        return {
            "npc": profile.to_dialogue_context(),
            "player": {
                "name": player.name,
                "hp": player.hp,
                "max_hp": player.max_hp,
                "statuses": sorted(player.statuses),
                "equipment": {slot: item for slot, item in player.equipment.items() if item},
            },
            "scene": {"turn": self.state.turn, "depth": self.state.depth, "scenario": self.state.scenario},
            "message": message,
        }

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

    def trade_context_for_llm(self, npc: Entity, message: str, reply: str) -> dict[str, Any]:
        profile = self.state.npc_profiles[npc.id]
        player = self.state.player
        return {
            "npc": profile.to_dialogue_context(),
            "player": {
                "name": player.name,
                "inventory": dict(sorted(self.state.inventory.items())),
            },
            "scene": {"turn": self.state.turn, "depth": self.state.depth, "scenario": self.state.scenario},
            "exchange": {"player_said": message, "npc_replied": reply},
        }

    def apply_dialogue_exchange(
        self, npc: Entity, message: str, reply: str, trade_data: dict[str, Any] | None = None
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

        if trade_data is not None and trade_data.get("trade_proposed"):
            proposal_text = str(trade_data.get("proposal_text") or "").strip()
            self.state.pending_trade = {
                "npc_id": npc.id,
                "npc_name": npc.name,
                "initiator": trade_data.get("initiator"),
                "npc_gives": [dict(entry) for entry in coerce_list(trade_data.get("npc_gives"))],
                "npc_wants": [dict(entry) for entry in coerce_list(trade_data.get("npc_wants"))],
                "proposal_text": proposal_text,
            }
            if proposal_text:
                self.state.add_message(f'{npc.name} proposes a trade: "{proposal_text}"')
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
        self.state.add_message(f"Deal struck with {npc_name} -- you receive {receive_text}, and hand over {give_text}.")
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
            self.state.add_message("You descend past the last stair and escape with your impossible magic intact.")
            return True
        self.state.depth += 1
        self.state.stats.deepest_floor = max(self.state.stats.deepest_floor, self.state.depth)
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
        self.state.depth -= 1
        self._generate_dungeon_floor(preserve_player=True)
        self.state.turn += 1
        self.update_fov()
        self.state.add_message(f"You climb back to dungeon floor {self.state.depth}.")
        return True

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
            target.statuses["slowed"] = max(status_duration(target.statuses.get("slowed")), 2)
            self.state.add_message(f"A frost shard bites into {target.name}, slowing it.")
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
        player.statuses["warded"] = max(status_duration(player.statuses.get("warded")), 6)
        self.state.add_message("A steady ward settles over you, dulling the next blows.")
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
            if entity.id == self.state.player_id or entity.kind != "actor" or entity.hp <= 0:
                continue
            entity.statuses["revealed"] = max(status_duration(entity.statuses.get("revealed")), 6)
            found += 1
        if found:
            self.state.add_message(f"Your senses sharpen. {found} hidden presence(s) stand revealed nearby.")
        else:
            self.state.add_message("Your senses sharpen, but nothing nearby is hiding from you.")
        self.finish_player_turn()
        return True


    def nearest_enemy(self, max_distance: int | None = None) -> Entity | None:
        player = self.state.player
        enemies = self.living_enemies()
        if max_distance is not None:
            enemies = [enemy for enemy in enemies if self.distance(player, enemy) <= max_distance]
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
        player = self.state.player
        unseen_enemies = [
            e for e in self.living_enemies()
            if not self.is_visible(e.x, e.y)
        ]
        if not unseen_enemies:
            return
        enemy = self.rng.choice(unseen_enemies)
        sounds_by_tag = {
            "undead": ["Something rattles in the dark.", "You hear hollow footsteps.", "A cold draft passes through the wall."],
            "beast": ["Claws scrape stone somewhere nearby.", "You hear labored breathing.", "Something heavy shifts in the shadows."],
            "slime": ["A wet sound gurgles in the distance.", "Something drips that isn't water.", "You hear a slow, wet pulse."],
            "spider": ["Silk scrapes against stone.", "You hear many legs on the ceiling.", "A faint clicking echoes past."],
            "construct": ["Metal grinds against stone.", "A low hum resonates from the walls.", "Gears turn somewhere unseen."],
            "shadow": ["The shadows pool and shift.", "Something cold watches from the dark.", "Your torch dims for a moment."],
            "empire": ["You hear the rhythmic stamp of boots in unison.", "A horn sounds three precise notes, then falls silent.", "Iron scrapes against iron in perfect time."],
        }
        messages = ["Something moves in the dark.", "You sense you are not alone.", "The dungeon breathes."]
        for tag, tag_messages in sounds_by_tag.items():
            if tag in enemy.tags:
                messages = tag_messages
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
                    entity.statuses["burning"] = max(status_duration(entity.statuses.get("burning")), 2)
                    self.state.add_message("You are scorched by wild fire." if is_player else f"{entity.name} is scorched by wild fire.")
            elif tile == POISON_CLOUD:
                self.damage_entity(entity, 1, "poison")
                if entity.hp > 0:
                    entity.statuses["poisoned"] = max(status_duration(entity.statuses.get("poisoned")), 2)
                    self.state.add_message("You cough in poison vapors." if is_player else f"{entity.name} coughs in poison vapors.")
            elif tile == WATER and "burning" in entity.statuses:
                entity.statuses.pop("burning")
                if is_player:
                    self.state.add_message("The water extinguishes your flames.")
                else:
                    self.state.add_message(f"{entity.name} is doused by the water.")
            elif tile == VINES and "rooted" not in entity.statuses and "webbed" not in entity.statuses:
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
                    self.state.add_message("You burn." if _is_player else f"{entity.name} burns ({burn_name}).")
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
                    self.state.add_message("You weaken from poison." if _is_player else f"{entity.name} weakens ({poison_name}).")
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
        trap_tag = next((tag for tag in self.tile_tags_at(entity.x, entity.y) if tag in TRAP_SPECS), None)
        if trap_tag is not None:
            spec = TRAP_SPECS[trap_tag]
            self.damage_entity(entity, spec["damage"], spec["damage_type"])
            if entity.alive:
                entity.statuses[spec["status"]] = max(status_duration(entity.statuses.get(spec["status"])), spec["duration"])
            self.state.add_message(spec["message"] if is_player else spec["message_other"].format(name=entity.name))
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
            entity.statuses["burning"] = max(status_duration(entity.statuses.get("burning")), 2)
            self.state.add_message("You step into wild fire." if is_player else f"{entity.name} steps into wild fire.")
        elif tile == POISON_CLOUD:
            self.damage_entity(entity, 1, "poison")
            entity.statuses["poisoned"] = max(status_duration(entity.statuses.get("poisoned")), 2)
            self.state.add_message("You inhale a poison cloud." if is_player else f"{entity.name} inhales a poison cloud.")
        elif tile == SLICK_ICE:
            entity.statuses["slowed"] = max(status_duration(entity.statuses.get("slowed")), 1)
            self.state.add_message("You skid on slick ice." if is_player else f"{entity.name} skids on slick ice.")
        elif tile == WATER and "burning" in entity.statuses:
            entity.statuses.pop("burning")
            if entity.id == self.state.player_id:
                self.state.add_message("The water extinguishes your flames.")
            else:
                self.state.add_message(f"{entity.name} is doused.")
        if tile == VINES and "rooted" not in entity.statuses and "webbed" not in entity.statuses:
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
                self.state.add_message("You bleed." if _sp else f"{entity.name} bleeds ({bleed_name}).")
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
                    regen_name = entity.status_display.get("regenerating", "regenerating")
                    self.state.add_message(f"You regenerate ({regen_name})." if regen_name != "regenerating" else "You regenerate.")
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
        event_type = str(event.get("event_type") or event.get("type") or "message").lower()
        if event_type == "message":
            text = str(event.get("text") or event.get("message") or "Something promised arrives late.")
            self.state.add_message(text)
        elif event_type in {"summon", "spawn"}:
            player = self.state.player
            x, y = self.find_open_tile_near(player.x, player.y)
            faction = normalize_faction(event.get("faction"), default="ally", neutral_is_ally=True)
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
                    x, y,
                    clamp_int(event.get("hp") or 6, 1, 30),
                    clamp_int(event.get("attack") or 2, 0, 10),
                    clamp_int(event.get("defense") or 0, 0, 8),
                    faction,
                    None if faction in {"ally", "player"} else "simple",
                    tags=set(coerce_list(event.get("tags"))),
                )
            self.state.add_message(f"{name} arrives.")
        elif event_type == "conjure":
            template_id = str(event.get("template") or "small_beast")
            self._apply_effect({"type": "conjure_creature", **event, "event_type": None})
        elif event_type in {"damage", "area_damage"}:
            player = self.state.player
            self._apply_effect({"type": event_type, "target": "player", **event, "event_type": None})
        elif event_type in {"heal", "restore_mana"}:
            self._apply_effect({"type": event_type, "target": "player", **event, "event_type": None})
        elif event_type in {"status", "add_status"}:
            self._apply_effect({"type": "add_status", "target": "player", **event, "event_type": None})
        elif event_type == "flood":
            tile = str(event.get("tile") or "water")
            radius = clamp_int(event.get("radius") or 3, 0, 99)
            player = self.state.player
            self._apply_effect({"type": "create_tiles", "target": "player", "tile": tile, "radius": radius, "event_type": None})
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
        event = {"target": target, "source": source, "amount": amount, "damage_type": damage_type}
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
        event = {"target": target, "source": source, "amount": previous_hp, "damage_type": damage_type}
        names = ["on_death", "on_actor_death"]
        if target.id == self.state.player_id:
            names.append("on_player_death")
        elif target.faction == "enemy":
            names.append("on_enemy_death")
        self._fire_triggers(names, event)

    def _fire_triggers(self, names: str | list[str], event: dict[str, Any] | None = None) -> list[str]:
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
            trigger_name = normalize_trigger_name(str(trigger.get("trigger") or trigger.get("on") or ""))
            if trigger_name not in wanted or not self._trigger_matches_target(trigger, event):
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

    def _trigger_matches_target(self, trigger: dict[str, Any], event: dict[str, Any]) -> bool:
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
        return target.id == trigger_target or trigger_target in target.tags or trigger_target in normalize_id(target.name).split("_")

    def _fill_trigger_effect_defaults(self, effect: dict[str, Any], event: dict[str, Any]) -> None:
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
            "nearest_enemy", "nearest enemy", "enemy", "nearest_foe", "nearest_entity",
            "nearest_target", "closest_enemy", "target", "foe", "nearest_actor",
        }:
            return self.nearest_enemy()
        return self.state.entities.get(target_id)

    def resolve_target_group(self, target_id: str | None) -> list[Entity]:
        target = normalize_id(str(target_id or ""))
        if target in {"all", "everyone", "all_entities", "all_nearby", "everything"}:
            return [entity for entity in self.state.entities.values() if entity.kind == "actor" and entity.hp > 0]
        if target in {"all_enemies", "enemies", "all_foes", "all_hostiles", "nearby_enemies", "every_enemy"}:
            return self.living_enemies()
        if target in {"allies", "all_allies", "friends", "friendlies"}:
            return [
                entity
                for entity in self.state.entities.values()
                if entity.kind == "actor" and entity.hp > 0 and entity.faction in {"ally", "player"}
            ]
        singular = singular_target_tag(target)
        if not singular:
            return []
        return [
            entity
            for entity in self.state.entities.values()
            if entity.kind == "actor"
            and entity.hp > 0
            and entity.id != self.state.player_id
            and (singular in entity.tags or singular in normalize_id(entity.name).split("_"))
        ]

    def _verb(self, entity: Entity, second_person: str, third_person: str) -> str:
        """Pick the grammatically correct verb for f"{entity.name} {verb} ...".

        The player's display name is the second-person pronoun "You", so a
        message built that way needs "take"/"are" for the player but
        "takes"/"is" for anyone else (e.g. "You take 3 damage." vs.
        "cave spider takes 3 damage.").
        """
        return second_person if entity.id == self.state.player_id else third_person

    def context_for_llm(self, spell: str) -> dict[str, Any]:
        player = self.state.player
        nearby_entities = [
            entity.to_public_dict()
            for entity in self.state.entities.values()
            if entity.alive
            and self.is_visible(entity.x, entity.y)
            and abs(entity.x - player.x) <= self.state.fov_radius
            and abs(entity.y - player.y) <= self.state.fov_radius
        ]
        floor_items = [
            {"id": e.id, "name": e.name, "item_type": e.item_type, "material": e.material,
             "quantity": e.quantity, "x": e.x, "y": e.y, "tags": sorted(e.tags)}
            for e in self.state.entities.values()
            if e.kind == "item"
            and self.is_visible(e.x, e.y)
            and abs(e.x - player.x) <= self.state.fov_radius
            and abs(e.y - player.y) <= self.state.fov_radius
        ]
        return {
            "spell": spell,
            "turn": self.state.turn,
            "depth": self.state.depth,
            "max_depth": self.state.max_depth,
            "player": player.to_public_dict(),
            "inventory": self.state.inventory,
            "curses": [curse.to_public_dict() for curse in self.state.curses.values()],
            "world_flags": self.state.flags,
            "event_timers": self.state.event_timers,
            "triggers": self.state.triggers,
            "visible_tile_count": len(self.state.visible),
            "explored_tile_count": len(self.state.explored),
            "nearby_entities": nearby_entities,
            "floor_items": floor_items,
            "nearby_map": self.nearby_map_strings(radius=9),
            "nearby_tile_details": self.nearby_tile_details(radius=5),
            "tile_legend": {tile: {"name": name, "tags": sorted(TILE_TAGS.get(tile, set()))} for tile, name in TILE_NAMES.items()},
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
                "add_tag",
                "remove_tag",
                "add_resistance",
                "add_weakness",
                "set_flag",
                "schedule_event",
                "create_trigger",
                "message",
            ],
            "supported_costs": ["mana", "health", "max_health", "max_mana", "item", "status", "curse"],
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
                    chars.append(item.char if item else (tile if self.is_visible(x, y) else tile.lower()))
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
                    details.append(
                        {
                            "x": x,
                            "y": y,
                            "tile": tile,
                            "name": TILE_NAMES.get(tile, "strange"),
                            "tags": sorted(self.tile_tags_at(x, y)),
                            "duration": duration,
                        }
                    )
        return details[:60]

