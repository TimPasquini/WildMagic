from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import random
import re
from typing import Any

from .models import (
    BLOCKING_TILES,
    DOOR,
    DAMAGE_TYPES,
    FIRE,
    FLOOR,
    ICE_WALL,
    MECHANICAL_STATUSES,
    MIST,
    OPEN_DOOR,
    POISON_CLOUD,
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
    TILE_NAMES,
    TILE_ALIASES,
    TILE_TAGS,
    WildMagicOutcome,
)
from .templates import creature_template, creature_template_ids, item_template, item_template_ids


MAP_WIDTH = 42
MAP_HEIGHT = 28


ITEM_USE_SPECS: dict[str, dict[str, Any]] = {
    "mana_crystal": {
        "effects": [{"kind": "restore_mana", "amount": 6}],
        "message": "The {item} dissolves. You recover {amount} mana.",
    },
    "blood_moss": {
        "effects": [{"kind": "heal", "amount": 5}],
        "message": "You chew the {item}. You heal {amount} HP.",
    },
    "bone_charm": {
        "effects": [
            {"kind": "status", "status": "warded", "duration": 8},
            {"kind": "resistance", "damage_type": "physical", "amount": 20},
        ],
        "message": "The {item} crumbles. You feel warded and resistant.",
    },
    "healing_potion": {
        "effects": [{"kind": "heal", "amount": 10}],
        "message": "The {item} works. You heal {amount} HP.",
    },
    "mana_potion": {
        "effects": [{"kind": "restore_mana", "amount": 10}],
        "message": "The {item} restores {amount} mana.",
    },
    "smoke_vial": {
        "effects": [{"kind": "create_tiles", "tile": MIST, "radius": 2, "duration": 5}],
        "message": "A cloud of mist erupts around you.",
    },
    "blink_scroll": {
        "effects": [{"kind": "teleport_explored"}],
        "message": "You blink to an explored tile.",
        "failure": "The scroll finds nowhere to send you.",
    },
    "beast_claw": {
        "effects": [{"kind": "status", "status": "empowered", "duration": 4}],
        "message": "You drag the {item} across your palm. Your strikes feel sharper.",
    },
    "bone_shard": {
        "effects": [{"kind": "damage_nearest", "range": 12, "amount": 4, "damage_type": "physical", "required": True}],
        "message": "You hurl the {item}. {target} takes {amount} damage.",
        "failure": "No enemy is close enough to throw at.",
    },
    "viscous_residue": {
        "effects": [{"kind": "status_nearest", "range": 8, "status": "poisoned", "duration": 4, "required": True}],
        "message": "You fling the {item}. {target} is poisoned.",
        "failure": "No enemy to throw this at.",
    },
    "metal_scrap": {
        "effects": [{"kind": "damage_nearest", "range": 6, "amount_min": 3, "amount_max": 6, "damage_type": "physical", "required": True}],
        "message": "You bash with the {item}. {target} takes {amount} damage.",
        "failure": "No enemy nearby.",
    },
    "arcane_residue": {
        "effects": [
            {"kind": "restore_mana", "amount": 3},
            {"kind": "damage_nearest", "range": 8, "amount": 3, "damage_type": "arcane"},
        ],
        "message": "The {item} sparks. You gain {mana} mana. {target_clause}",
    },
    "stolen_coin": {
        "choices": [
            {
                "effects": [{"kind": "restore_mana", "amount_min": 4, "amount_max": 8}],
                "message": "The {item} lands lucky side up. You gain {amount} mana.",
            },
            {
                "effects": [{"kind": "heal", "amount_min": 2, "amount_max": 5}],
                "message": "The {item} lands fair. You heal {amount} HP.",
            },
            {
                "effects": [{"kind": "status", "status": "marked", "duration": 4}],
                "message": "The {item} lands cursed side up. You are marked.",
            },
        ],
    },
}

DEFAULT_ITEM_USE_SPEC: dict[str, Any] = {
    "effects": [{"kind": "restore_mana", "amount": 2}],
    "message": "You consume the {item}. It restores {amount} mana.",
}


@dataclass(frozen=True)
class Room:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def intersects(self, other: "Room") -> bool:
        return not (
            self.x + self.w + 1 < other.x
            or other.x + other.w + 1 < self.x
            or self.y + self.h + 1 < other.y
            or other.y + other.h + 1 < self.y
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
    inventory: dict[str, int] = field(default_factory=lambda: {"chalk": 2, "grave salt": 1})
    curses: dict[str, Curse] = field(default_factory=dict)
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

    @property
    def player(self) -> Entity:
        return self.entities[self.player_id]

    def add_message(self, message: str) -> None:
        self.messages.append(message)
        self.messages = self.messages[-80:]


class GameEngine:
    def __init__(self, seed: int | None = None, scenario: str = "dungeon") -> None:
        self.rng = random.Random(seed)
        self.state = GameState(rng_seed=seed, scenario=scenario)
        self._next_entity_number = 1
        if scenario == "test_chamber":
            self._generate_test_chamber()
        else:
            self._generate_new_run()

    def _generate_new_run(self) -> None:
        state = self.state
        state.depth = 1
        self._generate_dungeon_floor(preserve_player=False)
        state.add_message("The dungeon exhales. Wild magic listens.")
        state.add_message("Type a spell in the right panel and press Enter.")
        self.update_fov()

    def _generate_dungeon_floor(self, preserve_player: bool) -> None:
        state = self.state
        existing_player = state.entities.get(state.player_id) if preserve_player else None
        state.tiles = [[WALL for _ in range(state.width)] for _ in range(state.height)]
        state.visible.clear()
        state.explored.clear()
        state.tile_tags.clear()
        state.tile_durations.clear()
        state.entities = {}
        rooms: list[Room] = []
        for _ in range(80):
            w = self.rng.randint(5, 10)
            h = self.rng.randint(4, 8)
            x = self.rng.randint(1, state.width - w - 2)
            y = self.rng.randint(1, state.height - h - 2)
            room = Room(x, y, w, h)
            if any(room.intersects(existing) for existing in rooms):
                continue
            self._carve_room(room)
            if rooms:
                self._carve_corridor(rooms[-1].center, room.center)
            rooms.append(room)
            if len(rooms) >= 8:
                break

        if not rooms:
            fallback = Room(4, 4, 12, 8)
            self._carve_room(fallback)
            rooms.append(fallback)

        px, py = rooms[0].center
        if existing_player is None:
            player = Entity(
                id="player",
                name="You",
                kind="player",
                x=px,
                y=py,
                char="@",
                hp=24,
                max_hp=24,
                mana=14,
                max_mana=14,
                attack=4,
                defense=1,
                blocks=True,
                faction="player",
            )
        else:
            player = existing_player
            player.x = px
            player.y = py
            player.blocks = True
        state.entities[player.id] = player
        if state.depth > 1:
            state.tiles[py][px] = STAIRS_UP

        enemy_templates = [
            ("goblin cutpurse", "g", 8, 3, 0, "goblin", {"goblin", "humanoid", "flesh"}, {}, {}),
            ("glass bat", "b", 5, 2, 0, "bat", {"beast", "glass"}, {"poison": 25}, {"force": 25}),
            ("ash slime", "s", 10, 2, 1, "slime", {"slime", "ash"}, {"fire": 35, "poison": 50}, {"frost": 25}),
            ("bone skeleton", "k", 7, 3, 1, "simple", {"undead", "bone"}, {"poison": 100, "frost": 50}, {"force": 50, "radiant": 50}),
            ("cave spider", "x", 6, 2, 0, "simple", {"beast", "spider"}, {}, {"fire": 25}),
            ("shadow wraith", "W", 4, 4, 0, "simple", {"undead", "shadow"}, {"physical": 25, "poison": 100}, {"radiant": 75, "fire": 25}),
            ("fungal crawler", "c", 9, 2, 0, "simple", {"beast", "fungus"}, {"acid": 50}, {"fire": 50}),
        ]
        for room in rooms[1:]:
            if self.rng.random() < 0.85:
                name, char, hp, attack, defense, ai, tags, resistances, weaknesses = self.rng.choice(enemy_templates)
                x, y = self._random_open_tile_in_room(room)
                self.spawn_actor(
                    name,
                    char,
                    x,
                    y,
                    hp,
                    attack,
                    defense,
                    "enemy",
                    ai,
                    tags=tags,
                    resistances=resistances,
                    weaknesses=weaknesses,
                )
            if self.rng.random() < 0.55:
                item = self.rng.choice(
                    [
                        ("mana crystal", "!", "mana crystal"),
                        ("blood moss", ",", "blood moss"),
                        ("bone charm", "?", "bone charm"),
                    ]
                )
                x, y = self._random_open_tile_in_room(room)
                self.spawn_item(item[0], item[1], x, y, item[2])

        down_x, down_y = rooms[-1].center
        state.tiles[down_y][down_x] = STAIRS_DOWN
        self._place_doors()

    def _generate_test_chamber(self) -> None:
        state = self.state
        state.tiles = [[WALL for _ in range(state.width)] for _ in range(state.height)]
        chamber = Room(2, 2, 18, 12)
        self._carve_room(chamber)
        for x in range(20, 30):
            state.tiles[7][x] = FLOOR
        self._carve_room(Room(30, 4, 8, 7))
        state.tiles[7][6] = DOOR
        state.tiles[7][20] = DOOR

        player = Entity(
            id="player",
            name="You",
            kind="player",
            x=5,
            y=7,
            char="@",
            hp=24,
            max_hp=24,
            mana=14,
            max_mana=14,
            attack=4,
            defense=1,
            blocks=True,
            faction="player",
        )
        state.entities[player.id] = player
        state.tiles[8][10] = WATER
        state.tiles[6][11] = VINES
        state.tiles[7][13] = RUBBLE
        state.tiles[7][5] = STAIRS_DOWN
        state.tiles[7][18] = STAIRS_DOWN
        self.spawn_actor("test goblin", "g", 10, 7, 8, 3, 0, "enemy", "goblin", tags={"goblin", "flesh"})
        self.spawn_actor(
            "patient slime",
            "s",
            34,
            7,
            10,
            2,
            1,
            "enemy",
            "slime",
            tags={"slime", "ash"},
            resistances={"poison": 50},
            weaknesses={"frost": 25},
        )
        self.spawn_item("mana crystal", "!", 7, 7, "mana crystal")
        self.spawn_item("blood moss", ",", 6, 8, "blood moss")
        state.add_message("The test chamber waits without pretending to be fair.")
        state.add_message("Use CLI commands or type wild spells in the panel.")
        self.update_fov()

    def _carve_room(self, room: Room) -> None:
        for y in range(room.y, room.y + room.h):
            for x in range(room.x, room.x + room.w):
                self.state.tiles[y][x] = FLOOR

    def _carve_corridor(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        x1, y1 = start
        x2, y2 = end
        if self.rng.random() < 0.5:
            self._carve_h_tunnel(x1, x2, y1)
            self._carve_v_tunnel(y1, y2, x2)
        else:
            self._carve_v_tunnel(y1, y2, x1)
            self._carve_h_tunnel(x1, x2, y2)

    def _carve_h_tunnel(self, x1: int, x2: int, y: int) -> None:
        for x in range(min(x1, x2), max(x1, x2) + 1):
            self.state.tiles[y][x] = FLOOR

    def _carve_v_tunnel(self, y1: int, y2: int, x: int) -> None:
        for y in range(min(y1, y2), max(y1, y2) + 1):
            self.state.tiles[y][x] = FLOOR

    def _place_doors(self) -> None:
        candidates: list[tuple[int, int]] = []
        for y in range(1, self.state.height - 1):
            for x in range(1, self.state.width - 1):
                if self.state.tiles[y][x] != FLOOR:
                    continue
                if any(entity.x == x and entity.y == y for entity in self.state.entities.values()):
                    continue
                horizontal_floor = self.state.tiles[y][x - 1] != WALL and self.state.tiles[y][x + 1] != WALL
                vertical_walls = self.state.tiles[y - 1][x] == WALL and self.state.tiles[y + 1][x] == WALL
                vertical_floor = self.state.tiles[y - 1][x] != WALL and self.state.tiles[y + 1][x] != WALL
                horizontal_walls = self.state.tiles[y][x - 1] == WALL and self.state.tiles[y][x + 1] == WALL
                if (horizontal_floor and vertical_walls) or (vertical_floor and horizontal_walls):
                    candidates.append((x, y))
        self.rng.shuffle(candidates)
        for x, y in candidates[: max(2, min(8, len(candidates) // 5))]:
            self.state.tiles[y][x] = DOOR

    def _random_open_tile_in_room(self, room: Room) -> tuple[int, int]:
        for _ in range(50):
            x = self.rng.randint(room.x, room.x + room.w - 1)
            y = self.rng.randint(room.y, room.y + room.h - 1)
            if self.can_occupy(x, y):
                return x, y
        return room.center

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

    def spawn_item(
        self,
        name: str,
        char: str,
        x: int,
        y: int,
        item_type: str,
        quantity: int = 1,
        material: str | None = None,
        tags: set[str] | None = None,
    ) -> Entity:
        entity = Entity(
            id=self.next_entity_id("item"),
            name=name,
            kind="item",
            x=x,
            y=y,
            char=char,
            item_type=item_type,
            material=material,
            quantity=quantity,
            blocks=False,
            tags=set(tags or ()),
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

    def distance(self, a: Entity, b: Entity) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def attempt_player_move(self, dx: int, dy: int) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if any(s in player.statuses for s in ["rooted", "webbed", "frozen", "stunned"]):
            self.state.add_message("You cannot move — you are held in place.")
            return False
        target_x = player.x + dx
        target_y = player.y + dy
        if not self.in_bounds(target_x, target_y):
            self.state.add_message("The dungeon refuses that edge.")
            return False
        target = self.blocking_entity_at(target_x, target_y)
        if target and target.faction != "player":
            self.attack(player, target)
            self.finish_player_turn()
            return True
        if self.tile_at(target_x, target_y) == DOOR:
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

    def use_item(self, item_name: str) -> bool:
        if self.state.game_over:
            return False
        matched = self.find_inventory_item(item_name)
        if matched is None or self.state.inventory.get(matched, 0) < 1:
            self.state.add_message(f"You don't have any {item_name.strip().lower()}.")
            return False
        spec = ITEM_USE_SPECS.get(normalize_id(matched), DEFAULT_ITEM_USE_SPEC)
        consumed = self._apply_item_use_spec(matched, spec)
        if consumed:
            self.consume_inventory_item(matched, 1)
            self.state.stats.items_used += 1
            self.finish_player_turn()
        return consumed

    def drop_item(self, item_name: str) -> bool:
        if self.state.game_over:
            return False
        matched = self.find_inventory_item(item_name)
        if matched is None or self.state.inventory.get(matched, 0) < 1:
            self.state.add_message(f"You don't have any {item_name.strip().lower()}.")
            return False
        self.consume_inventory_item(matched, 1)
        player = self.state.player
        self.spawn_item(matched, "?", player.x, player.y, item_type=normalize_id(matched))
        self.state.add_message(f"You drop {matched}.")
        self.finish_player_turn()
        return True

    def find_inventory_item(self, item_name: str) -> str | None:
        wanted = normalize_id(item_name)
        for key in self.state.inventory:
            if key.lower() == item_name.strip().lower() or normalize_id(key) == wanted:
                return key
        return None

    def consume_inventory_item(self, item_name: str, amount: int) -> int:
        current = self.state.inventory.get(item_name, 0)
        spent = min(current, max(0, amount))
        remaining = current - spent
        if remaining:
            self.state.inventory[item_name] = remaining
        else:
            self.state.inventory.pop(item_name, None)
        return spent

    def _apply_item_use_spec(self, item_name: str, spec: dict[str, Any]) -> bool:
        if "choices" in spec:
            choices = [choice for choice in coerce_list(spec.get("choices")) if isinstance(choice, dict)]
            if choices:
                spec = self.rng.choice(choices)
        context: dict[str, Any] = {"item": item_name.replace("_", " ")}
        target_clause = ""
        for effect in coerce_list(spec.get("effects")):
            if not isinstance(effect, dict):
                continue
            success, updates = self._apply_item_effect(effect)
            context.update(updates)
            if "target" in updates and "amount" in updates and "damage_type" in updates:
                target_clause = f"{updates['target']} takes {updates['amount']} {updates['damage_type']}."
            if not success and effect.get("required"):
                self.state.add_message(str(spec.get("failure") or "Nothing happens."))
                return False
        context["target_clause"] = target_clause or "No enemy is close enough to be caught in it."
        self.state.add_message(str(spec.get("message") or "You use the {item}.").format(**context))
        return True

    def _apply_item_effect(self, effect: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        player = self.state.player
        kind = normalize_id(str(effect.get("kind") or ""))
        amount = self._roll_item_amount(effect)
        if kind == "restore_mana":
            gained = min(amount, player.max_mana - player.mana)
            player.mana += gained
            return True, {"amount": gained, "mana": gained}
        if kind == "heal":
            healed = self.heal_entity(player, amount)
            return True, {"amount": healed}
        if kind == "status":
            status = normalize_id(str(effect.get("status") or "marked"))
            player.statuses[status] = max(status_duration(player.statuses.get(status)), clamp_int(effect.get("duration"), 1, 999))
            return True, {"status": status, "duration": player.statuses[status]}
        if kind == "resistance":
            damage_type = normalize_id(str(effect.get("damage_type") or "physical"))
            player.resistances[damage_type] = clamp_int(player.resistances.get(damage_type, 0) + amount, 0, 95)
            return True, {"damage_type": damage_type, "amount": amount}
        if kind == "create_tiles":
            tile = str(effect.get("tile") or MIST)
            for tx, ty in self.points_in_radius(player.x, player.y, clamp_int(effect.get("radius"), 0, 6)):
                self.set_tile(tx, ty, tile, optional_duration(effect.get("duration")))
            return True, {"tile": tile}
        if kind == "teleport_explored":
            candidates = [
                (x, y)
                for x, y in (
                    (self.rng.randint(0, self.state.width - 1), self.rng.randint(0, self.state.height - 1))
                    for _ in range(40)
                )
                if self.can_occupy(x, y) and self.is_explored(x, y)
            ]
            if not candidates:
                return False, {}
            x, y = self.rng.choice(candidates)
            self.teleport_entity(player, x, y)
            return True, {"x": x, "y": y}
        if kind in {"damage_nearest", "status_nearest"}:
            target = self.nearest_enemy(max_distance=clamp_int(effect.get("range"), 1, 99))
            if not target:
                return False, {}
            if kind == "damage_nearest":
                damage_type = normalize_id(str(effect.get("damage_type") or "physical"))
                actual = self.damage_entity(target, amount, damage_type)
                return True, {"target": target.name, "amount": actual, "damage_type": damage_type}
            status = normalize_id(str(effect.get("status") or "poisoned"))
            target.statuses[status] = max(status_duration(target.statuses.get(status)), clamp_int(effect.get("duration"), 1, 999))
            return True, {"target": target.name, "status": status}
        return True, {}

    def _roll_item_amount(self, effect: dict[str, Any]) -> int:
        if "amount_min" in effect or "amount_max" in effect:
            return self.rng.randint(clamp_int(effect.get("amount_min"), 0, 99), clamp_int(effect.get("amount_max"), 0, 99))
        return clamp_int(effect.get("amount"), 0, 99)

    def nearest_enemy(self, max_distance: int | None = None) -> Entity | None:
        player = self.state.player
        enemies = self.living_enemies()
        if max_distance is not None:
            enemies = [enemy for enemy in enemies if self.distance(player, enemy) <= max_distance]
        if not enemies:
            return None
        return min(enemies, key=lambda enemy: self.distance(player, enemy))

    def attack(self, attacker: Entity, defender: Entity) -> None:
        base = max(1, attacker.attack - defender.defense + self.rng.randint(0, 2))
        bonus = 2 if ("berserk" in attacker.statuses or "empowered" in attacker.statuses) else 0
        amount = base + bonus
        actual = self.damage_entity(defender, amount, "physical", source=attacker)
        if "berserk" in attacker.statuses:
            self.damage_entity(attacker, 1, "blood", source=attacker)
        if defender.hp > 0:
            self.state.add_message(f"{attacker.name} hits {defender.name} for {actual}.")
            # Spider webs on hit
            if "spider" in attacker.tags and "webbed" not in defender.statuses and self.rng.random() < 0.5:
                defender.statuses["webbed"] = 2
                self.state.add_message(f"{defender.name} is webbed!")
            # Fungus spreads spores on hit (poisoned)
            if "fungus" in attacker.tags and "poisoned" not in defender.statuses and self.rng.random() < 0.4:
                defender.statuses["poisoned"] = 3
                self.state.add_message(f"Fungal spores infect {defender.name}!")
        else:
            self.state.add_message(f"{attacker.name} drops {defender.name}.")

    def damage_entity(self, entity: Entity, amount: int, damage_type: str, source: Entity | None = None) -> int:
        if entity.kind == "item" or entity.hp <= 0:
            return 0
        damage_type = normalize_id(damage_type)
        if "marked" in entity.statuses and damage_type not in {"blood"}:
            amount = amount + 2
        if "cursed" in entity.statuses and damage_type not in {"blood"}:
            amount = amount + 1
        if "warded" in entity.statuses and damage_type not in {"blood"}:
            amount = max(0, amount - 2)
        actual = self._modified_damage(entity, amount, damage_type)
        hp_before = entity.hp
        entity.hp -= actual
        if entity.id == self.state.player_id:
            self.state.stats.damage_taken += actual
        elif entity.kind == "actor":
            self.state.stats.damage_dealt += actual
        if actual > 0:
            self._fire_damage_triggers(entity, source, actual, damage_type)
        if entity.hp <= 0:
            # Undead entities have a 30% chance to reform at 1 HP rather than dying.
            if ("undead" in entity.tags and entity.kind == "actor" and entity.id != self.state.player_id
                    and "slain" not in entity.tags and self.rng.random() < 0.3):
                entity.hp = 1
                entity.tags.add("slain")
                self.state.add_message(f"{entity.name} collapses… but begins to stir again!")
                return 0
            entity.hp = 0
            entity.blocks = False
            entity.char = "%"
            entity.ai = None
            entity.statuses.clear()
            if entity.id == self.state.player_id:
                self.state.game_over = True
                self.state.add_message("You die. The dungeon keeps your echo.")
            else:
                self.state.stats.enemies_killed += 1
                self._drop_loot(entity)
                # Slime splits into two smaller ones.
                if "slime" in entity.tags and "split" not in entity.tags and entity.max_hp > 2:
                    self._split_slime(entity)
                if not self.living_enemies():
                    self.state.victory = True
                    self.state.add_message("For a breath, the floor is yours.")
                # Death-effect tags.
                self._on_entity_death(entity)
                self._fire_death_triggers(entity, source, hp_before, damage_type)
        elif damage_type == "fire":
            if "bleeding" in entity.statuses:
                entity.statuses.pop("bleeding")
                entity.hp -= 1
                wound_subj = "Your wound is" if entity.id == self.state.player_id else f"{entity.name}'s wound is"
                self.state.add_message(f"{wound_subj} cauterized — brutal but effective.")
            else:
                entity.statuses["burning"] = max(status_duration(entity.statuses.get("burning")), 3)
        elif damage_type == "frost":
            if self.tile_at(entity.x, entity.y) == WATER:
                entity.statuses["frozen"] = max(status_duration(entity.statuses.get("frozen")), 2)
                self.state.add_message(f"{'You are' if entity.id == self.state.player_id else entity.name + ' is'} frozen solid in the water!")
            else:
                entity.statuses["slowed"] = max(status_duration(entity.statuses.get("slowed")), 2)
        elif damage_type == "lightning":
            if self.tile_at(entity.x, entity.y) == WATER:
                entity.statuses["stunned"] = max(status_duration(entity.statuses.get("stunned")), 2)
                self.state.add_message(f"Lightning courses through the water!")
        elif damage_type == "poison" and "poisoned" in entity.statuses:
            entity.statuses["poisoned"] = min(99, status_duration(entity.statuses.get("poisoned", 0)) + 2)
        return actual

    def _modified_damage(self, entity: Entity, amount: int, damage_type: str) -> int:
        base = max(0, int(amount))
        if base == 0:
            return 0
        resistance = clamp_int(entity.resistances.get(damage_type), 0, 95)
        weakness = clamp_int(entity.weaknesses.get(damage_type), 0, 200)
        multiplier = max(0.05, (100 - resistance + weakness) / 100)
        actual = int(round(base * multiplier))
        return max(1, actual)

    def heal_entity(self, entity: Entity, amount: int) -> int:
        if entity.kind == "item" or entity.hp <= 0:
            return 0
        before = entity.hp
        entity.hp = min(entity.max_hp, entity.hp + max(0, int(amount)))
        actual = entity.hp - before
        if entity.id == self.state.player_id:
            self.state.stats.hp_healed += actual
        return actual

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

    def _split_slime(self, parent: Entity) -> None:
        split_hp = max(1, parent.max_hp // 2)
        spawned = 0
        for _ in range(2):
            sx, sy = self.find_open_tile_near(parent.x, parent.y)
            if not self.can_occupy(sx, sy):
                continue
            self.spawn_actor(
                f"small {parent.name}", parent.char, sx, sy,
                hp=split_hp, attack=max(1, parent.attack - 1), defense=0,
                faction=parent.faction,
                tags=parent.tags | {"split"},
                ai=parent.ai or "simple",
            )
            spawned += 1
        if spawned:
            self.state.add_message(f"{parent.name} splits into {spawned} smaller slimes!")

    def _drop_loot(self, entity: Entity) -> None:
        tags = entity.tags
        # 40% drop chance; conjured creatures and constructs don't drop loot
        if "conjured" in tags or self.rng.random() > 0.4:
            return
        loot_by_tag = {
            "undead": ("bone shard", "bone_shard", "?", "bone"),
            "beast": ("beast claw", "claw", "?", "bone"),
            "humanoid": ("stolen coin", "coin", "$", "metal"),
            "slime": ("viscous residue", "residue", "~", "slime"),
            "construct": ("metal scrap", "scrap", "/", "metal"),
        }
        drop_name, drop_type, drop_char, drop_mat = ("arcane residue", "arcane_residue", "*", "essence")
        for tag, drop_data in loot_by_tag.items():
            if tag in tags:
                drop_name, drop_type, drop_char, drop_mat = drop_data
                break
        self.spawn_item(drop_name, drop_char, entity.x, entity.y, item_type=drop_type, material=drop_mat)
        self.state.add_message(f"{entity.name} drops {drop_name}.")

    def pick_up_items_at_player(self) -> None:
        player = self.state.player
        for entity in list(self.entities_at(player.x, player.y)):
            if entity.kind != "item":
                continue
            item_type = entity.item_type or entity.name
            self.state.inventory[item_type] = self.state.inventory.get(item_type, 0) + entity.quantity
            self.state.add_message(f"You pick up {entity.name}.")
            self.state.stats.items_collected += 1
            del self.state.entities[entity.id]

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
        self._process_entity_behaviors()
        self._regenerate_player()
        self._ambient_sounds()

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

    def _enemy_turns(self) -> None:
        player = self.state.player
        for enemy in list(self.living_enemies()):
            if any(status in enemy.statuses for status in ["stunned", "frozen"]):
                self.state.add_message(f"{enemy.name} cannot act.")
                continue
            if "slowed" in enemy.statuses and self.state.turn % 2 == 1:
                continue
            hasted = "hasted" in enemy.statuses
            action_count = 2 if hasted else 1
            for _ in range(action_count):
                if enemy.hp <= 0 or self.state.game_over:
                    break
                self._enemy_single_action(enemy, player)
        return

    def _enemy_single_action(self, enemy: Entity, player: Entity) -> None:
        if "pacifist" in enemy.tags or "noncombatant" in enemy.tags:
            return
        if "frightened" in enemy.statuses and self.distance(enemy, player) <= 8:
            step = self._flee_step(enemy, player.x, player.y)
            if step is not None:
                enemy.x, enemy.y = step
                self._apply_tile_entry(enemy)
            return
        if self.distance(enemy, player) <= 1.5:
            self.attack(enemy, player)
            return
        if any(status in enemy.statuses for status in ["rooted", "webbed"]):
            return
        if "confused" in enemy.statuses:
            dx, dy = self.rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            if self.can_occupy(enemy.x + dx, enemy.y + dy):
                enemy.x += dx
                enemy.y += dy
                self._apply_tile_entry(enemy)
            return
        if self.enemy_can_sense_player(enemy):
            step = self.next_path_step(enemy, player.x, player.y)
            if step is not None:
                enemy.x, enemy.y = step
                self._apply_tile_entry(enemy)
        else:
            dx, dy = self.rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)])
            if (dx or dy) and self.can_occupy(enemy.x + dx, enemy.y + dy):
                enemy.x += dx
                enemy.y += dy
                self._apply_tile_entry(enemy)

    def _ally_turns(self) -> None:
        allies = [
            e for e in self.state.entities.values()
            if e.kind == "actor" and e.faction == "ally" and e.hp > 0
        ]
        for ally in allies:
            if any(s in ally.statuses for s in ["stunned", "frozen"]):
                continue
            if "slowed" in ally.statuses and self.state.turn % 2 == 1:
                continue
            if "pacifist" in ally.tags or "noncombatant" in ally.tags:
                continue
            enemies = self.living_enemies()
            # Stationary entities never move; guardian entities only act within their territory.
            if "stationary" in ally.tags:
                nearby = [e for e in enemies if self.distance(ally, e) <= 1.5]
                if nearby:
                    self.attack(ally, min(nearby, key=lambda e: self.distance(ally, e)))
                continue
            if "guardian" in ally.tags:
                guard_range = 3.0
                nearby = [e for e in enemies if self.distance(ally, e) <= guard_range]
                if nearby:
                    target = min(nearby, key=lambda e: self.distance(ally, e))
                    if self.distance(ally, target) <= 1.5:
                        self.attack(ally, target)
                    elif not any(s in ally.statuses for s in ["rooted", "webbed"]):
                        step = self.next_path_step(ally, target.x, target.y)
                        if step is not None:
                            ally.x, ally.y = step
                            self._apply_tile_entry(ally)
                continue
            if not enemies:
                continue
            # Ranged allies attack from distance without closing in.
            if "ranged" in ally.tags:
                ranged_range = 7
                los_enemies = [
                    e for e in enemies
                    if self.distance(ally, e) <= ranged_range
                    and self.has_line_of_sight(ally.x, ally.y, e.x, e.y)
                ]
                if los_enemies:
                    target = min(los_enemies, key=lambda e: self.distance(ally, e))
                    self.attack(ally, target)
                    continue
                # No target in range — advance toward nearest enemy.
                if not any(s in ally.statuses for s in ["rooted", "webbed"]):
                    closest = min(enemies, key=lambda e: self.distance(ally, e))
                    step = self.next_path_step(ally, closest.x, closest.y)
                    if step is not None:
                        ally.x, ally.y = step
                        self._apply_tile_entry(ally)
                continue
            # Default: chase and melee.
            target = min(enemies, key=lambda e: self.distance(ally, e))
            if self.distance(ally, target) <= 1.5:
                self.attack(ally, target)
            elif not any(s in ally.statuses for s in ["rooted", "webbed"]):
                step = self.next_path_step(ally, target.x, target.y)
                if step is not None:
                    ally.x, ally.y = step
                    self._apply_tile_entry(ally)

    _AURA_RE = re.compile(r"^aura_([a-z]+)(?:_(\d+))?$")

    def _process_entity_behaviors(self) -> None:
        """Process per-turn behavior tags on all living actors."""
        player = self.state.player
        for entity in list(self.state.entities.values()):
            if entity.kind not in {"actor", "player"} or entity.hp <= 0:
                continue
            for tag in list(entity.tags):
                m = self._AURA_RE.match(tag)
                if not m:
                    continue
                aura_type = m.group(1)
                radius = int(m.group(2)) if m.group(2) else 2
                nearby = [
                    e for e in self.entities_in_radius(entity.x, entity.y, radius)
                    if e.kind in {"actor", "player"} and e.hp > 0 and e.id != entity.id
                ]
                offensive_targets, beneficial_targets = self._behavior_targets(entity, nearby)
                if aura_type in {"burn", "fire"}:
                    for t in offensive_targets:
                        t.statuses["burning"] = max(status_duration(t.statuses.get("burning")), 2)
                elif aura_type in {"heal", "healing"}:
                    for t in beneficial_targets:
                        self.heal_entity(t, 1)
                elif aura_type in {"fear", "dread"}:
                    for t in offensive_targets:
                        t.statuses["frightened"] = max(status_duration(t.statuses.get("frightened")), 2)
                elif aura_type in {"slow", "sluggish", "weight"}:
                    for t in offensive_targets:
                        t.statuses["slowed"] = max(status_duration(t.statuses.get("slowed")), 2)
                elif aura_type in {"poison", "toxic", "plague"}:
                    for t in offensive_targets:
                        t.statuses["poisoned"] = max(status_duration(t.statuses.get("poisoned")), 3)
                elif aura_type in {"bleed", "bleeding", "wound"}:
                    for t in offensive_targets:
                        t.statuses["bleeding"] = max(status_duration(t.statuses.get("bleeding")), 2)
                elif aura_type in {"reveal", "sight", "detect"}:
                    for t in nearby:
                        t.statuses["revealed"] = max(status_duration(t.statuses.get("revealed")), 2)
                elif aura_type in {"mana", "arcane", "font"}:
                    dist = math.hypot(entity.x - player.x, entity.y - player.y)
                    if dist <= radius and player.mana < player.max_mana:
                        player.mana = min(player.max_mana, player.mana + 1)
                elif aura_type in {"damage", "harm", "pain"}:
                    for t in offensive_targets:
                        self.damage_entity(t, 1, "arcane")
                elif aura_type in {"confuse", "confusion"}:
                    for t in offensive_targets:
                        t.statuses["confused"] = max(status_duration(t.statuses.get("confused")), 2)
                elif aura_type in {"berserk", "rage"}:
                    for t in beneficial_targets:
                        t.statuses["berserk"] = max(status_duration(t.statuses.get("berserk")), 2)
                elif aura_type in {"regen", "regenerate"}:
                    for t in beneficial_targets:
                        self.heal_entity(t, 1)

    def _behavior_targets(self, source: Entity, nearby: list[Entity]) -> tuple[list[Entity], list[Entity]]:
        player_side = {"ally", "player"}
        if source.faction == "enemy":
            offensive = [e for e in nearby if e.faction in player_side or e.id == self.state.player_id]
            beneficial = [e for e in nearby if e.faction == "enemy"]
        elif source.faction in player_side or source.id == self.state.player_id:
            offensive = [e for e in nearby if e.faction == "enemy"]
            beneficial = [e for e in nearby if e.faction in player_side or e.id == self.state.player_id]
        else:
            offensive = nearby
            beneficial = [e for e in nearby if e.faction == source.faction]
        return offensive, beneficial

    def _on_entity_death(self, entity: Entity) -> None:
        """Fire death-effect tags when an entity dies."""
        if "explode_on_death" in entity.tags or "bomb" in entity.tags:
            radius = 3
            for t in self.entities_in_radius(entity.x, entity.y, radius):
                if t.hp > 0 and t.id != entity.id:
                    self.damage_entity(t, 5, "fire")
            for tx, ty in self.points_in_radius(entity.x, entity.y, radius):
                self.set_tile(tx, ty, FIRE, duration=3)
            self.state.add_message(f"{entity.name} explodes in a gout of flame!")
        if "shatter_on_death" in entity.tags or "glass" in entity.tags and "fragile" in entity.tags:
            for t in self.entities_in_radius(entity.x, entity.y, 2):
                if t.hp > 0 and t.id != entity.id:
                    self.damage_entity(t, 3, "physical")
            self.state.add_message(f"{entity.name} shatters in a shower of shards!")
        if "poison_cloud_on_death" in entity.tags or "plague_on_death" in entity.tags:
            for tx, ty in self.points_in_radius(entity.x, entity.y, 3):
                self.set_tile(tx, ty, POISON_CLOUD, duration=6)
            self.state.add_message(f"{entity.name} dissolves into toxic vapor!")
        if "freeze_on_death" in entity.tags or "ice_burst_on_death" in entity.tags:
            for t in self.entities_in_radius(entity.x, entity.y, 2):
                if t.hp > 0 and t.id != entity.id:
                    t.statuses["frozen"] = max(status_duration(t.statuses.get("frozen")), 3)
            for tx, ty in self.points_in_radius(entity.x, entity.y, 2):
                self.set_tile(tx, ty, SLICK_ICE, duration=5)
            self.state.add_message(f"{entity.name} bursts in a spray of ice!")
        if "spawn_on_death" in entity.tags:
            for _ in range(2):
                sx, sy = self.find_open_tile_near(entity.x, entity.y)
                if self.can_occupy(sx, sy):
                    self.spawn_actor(
                        f"spawn of {entity.name}", "s", sx, sy,
                        hp=max(1, entity.max_hp // 3), attack=max(1, entity.attack - 1),
                        defense=0, faction=entity.faction, ai=entity.ai or "simple",
                        tags={"summoned"},
                    )
            self.state.add_message(f"{entity.name} bursts open — something crawls out!")

    def enemy_can_sense_player(self, enemy: Entity) -> bool:
        player = self.state.player
        distance = self.distance(enemy, player)
        if "invisible" in player.statuses:
            return distance <= 1.5
        if distance <= 5:
            return True
        if distance <= 11 and self.has_line_of_sight(enemy.x, enemy.y, player.x, player.y):
            return True
        return "marked" in player.statuses and distance <= 14

    def next_path_step(self, entity: Entity, goal_x: int, goal_y: int) -> tuple[int, int] | None:
        start = (entity.x, entity.y)
        goal = (goal_x, goal_y)
        queue: deque[tuple[int, int]] = deque([start])
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        while queue:
            current = queue.popleft()
            if current == goal:
                break
            for neighbor in self.path_neighbors(entity, current[0], current[1], goal):
                if neighbor in came_from:
                    continue
                came_from[neighbor] = current
                queue.append(neighbor)
        if goal not in came_from:
            return None
        current = goal
        while came_from[current] is not None and came_from[current] != start:
            current = came_from[current]  # type: ignore[index]
        if current == goal and self.blocking_entity_at(goal_x, goal_y) is self.state.player:
            return None
        if current == start:
            return None
        return current

    def _flee_step(self, entity: Entity, from_x: int, from_y: int) -> tuple[int, int] | None:
        neighbors = [(entity.x + 1, entity.y), (entity.x - 1, entity.y), (entity.x, entity.y + 1), (entity.x, entity.y - 1)]
        self.rng.shuffle(neighbors)
        best: tuple[int, int] | None = None
        best_dist = self.distance(entity, self.state.player)
        for tx, ty in neighbors:
            if not self.can_occupy(tx, ty):
                continue
            d = math.hypot(tx - from_x, ty - from_y)
            if d > best_dist:
                best_dist = d
                best = (tx, ty)
        return best

    def path_neighbors(
        self,
        entity: Entity,
        x: int,
        y: int,
        goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        neighbors = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        self.rng.shuffle(neighbors)
        valid: list[tuple[int, int]] = []
        for tx, ty in neighbors:
            if not self.in_bounds(tx, ty):
                continue
            tile = self.tile_at(tx, ty)
            # Doors are openable — treat as passable for pathfinding.
            if tile in BLOCKING_TILES and tile != DOOR:
                continue
            # Always allow the goal tile so entities can reach their target.
            if (tx, ty) == goal:
                valid.append((tx, ty))
                continue
            blocker = self.blocking_entity_at(tx, ty)
            if blocker is not None and blocker.id != entity.id:
                continue
            valid.append((tx, ty))
        return valid

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

    def apply_wild_magic_resolution(self, resolution: dict[str, Any]) -> WildMagicOutcome:
        messages: list[str] = []
        if self.state.game_over:
            return WildMagicOutcome(False, False, ["The dead do not cast."])

        accepted = bool(resolution.get("accepted", True))
        outcome_text = str(resolution.get("outcome_text") or resolution.get("outcome") or resolution.get("message") or "").strip()
        if not accepted:
            reason = str(resolution.get("rejected_reason") or "The spell is too vast to fit through you.")
            self.state.add_message(reason)
            self.state.stats.spells_failed += 1
            self.finish_player_turn()
            return WildMagicOutcome(True, False, [reason])

        if outcome_text:
            self.state.add_message(outcome_text)
            messages.append(outcome_text)

        for message in self._fire_triggers("on_next_spell", {"target": self.state.player, "source": self.state.player}):
            messages.append(message)

        for effect in coerce_list(resolution.get("effects")):
            for message in self._apply_effect(effect):
                self.state.add_message(message)
                messages.append(message)

        for cost in coerce_list(resolution.get("costs")):
            message = self._apply_cost(cost)
            if message:
                self.state.add_message(message)
                messages.append(message)

        if not messages:
            message = "The spell answers with a small, embarrassed pop."
            self.state.add_message(message)
            messages.append(message)

        self.state.stats.spells_cast += 1
        self.finish_player_turn()
        return WildMagicOutcome(True, False, messages)

    def _apply_cost(self, cost: dict[str, Any]) -> str | None:
        if not isinstance(cost, dict):
            return None
        cost_type = str(cost.get("type", "")).lower()
        player = self.state.player
        if cost_type == "mana":
            amount = clamp_int(cost.get("amount"), 1, 99)
            player.mana = max(0, player.mana - amount)
            return f"Cost: {amount} mana."
        if cost_type in {"health", "hp"}:
            amount = clamp_int(cost.get("amount"), 1, 99)
            self.damage_entity(player, amount, "blood")
            return f"Cost: {amount} health."
        if cost_type == "max_health":
            amount = clamp_int(cost.get("amount"), 0, 10)
            player.max_hp = max(1, player.max_hp - amount)
            player.hp = min(player.hp, player.max_hp)
            return f"Cost: {amount} maximum health."
        if cost_type == "max_mana":
            amount = clamp_int(cost.get("amount"), 0, 10)
            player.max_mana = max(0, player.max_mana - amount)
            player.mana = min(player.mana, player.max_mana)
            return f"Cost: {amount} maximum mana."
        if cost_type == "item":
            item = str(cost.get("item") or cost.get("item_name") or cost.get("id") or "").strip()
            amount = clamp_int(cost.get("amount"), 1, 99)
            if not item:
                return None
            current = self.state.inventory.get(item, 0)
            spent = min(current, amount)
            if spent:
                remaining = current - spent
                if remaining:
                    self.state.inventory[item] = remaining
                else:
                    self.state.inventory.pop(item, None)
            return f"Cost: {spent} {item}." if spent else f"Cost unpaid: no {item}."
        if cost_type == "curse":
            curse_id = str(cost.get("id") or cost.get("name") or "nameless_curse").lower().replace(" ", "_")
            name = str(cost.get("name") or curse_id.replace("_", " ").title())
            description = str(cost.get("description") or "Reality now remembers you incorrectly.")
            if curse_id in self.state.curses:
                self.state.curses[curse_id].stacks += 1
            else:
                self.state.curses[curse_id] = Curse(curse_id, name, description)
            self.state.stats.curses_gained += 1
            return f"Curse gained: {name}."
        if cost_type == "status":
            raw_status = str(cost.get("status") or cost.get("id") or "strained")
            status = normalize_id(raw_status)
            # Alias flavor names to canonical
            from .wild_magic import _STATUS_FLAVOR_ALIASES
            display_name = str(cost.get("display_name") or "").strip()
            if status not in MECHANICAL_STATUSES:
                canonical = _STATUS_FLAVOR_ALIASES.get(status)
                if canonical:
                    if not display_name:
                        display_name = status.replace("_", " ")
                    status = canonical
            duration = cost.get("duration", 5)
            expiry_text = str(cost.get("expiry_text") or "").strip()
            if status not in MECHANICAL_STATUSES:
                name = display_name or status.replace("_", " ").title()
                curse_id = f"wild_condition_{status}"
                if curse_id in self.state.curses:
                    self.state.curses[curse_id].stacks += 1
                else:
                    self.state.curses[curse_id] = Curse(
                        curse_id,
                        name,
                        f"Wild magic leaves you with an uncanny condition: {name}.",
                    )
                return f"Cost became a curse: {name}."
            dur_val3: int | str = "permanent" if duration == "permanent" else clamp_int(duration, 1, 999)
            player.statuses[status] = dur_val3
            shown = display_name or status.replace("_", " ")
            if display_name:
                player.status_display[status] = display_name
            if expiry_text:
                player.status_expiry_text[status] = expiry_text
            return f"Cost: you are {shown}."
        return None

    def _apply_effect(self, effect: dict[str, Any]) -> list[str]:
        if not isinstance(effect, dict):
            return []
        effect = _flatten_effect(effect)
        effect_type = str(effect.get("type", "")).lower()
        if effect_type == "damage":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return ["The spell claws at empty air."]
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            damage_type = str(effect.get("damage_type") or "arcane")
            actual = self.damage_entity(target, amount, damage_type)
            return [f"{target.name} takes {actual} {damage_type} damage."]
        if effect_type == "area_damage":
            x, y = self.effect_position(effect)
            radius = clamp_int(effect.get("radius"), 0, 99) if effect.get("radius") is not None else 3
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            damage_type = str(effect.get("damage_type") or "arcane")
            include_player = bool(effect.get("include_player", False))
            affects = normalize_id(str(effect.get("affects") or "non_player"))
            hit: list[str] = []
            for entity in self.entities_in_radius(x, y, radius):
                if entity.kind == "item" or entity.hp <= 0:
                    continue
                if entity.id == self.state.player_id and not include_player:
                    continue
                if not area_damage_affects(entity, affects, self.state.player_id):
                    continue
                actual = self.damage_entity(entity, amount, damage_type)
                hit.append(f"{entity.name} takes {actual} {damage_type}")
            if not hit:
                return ["The blast spends itself on empty stone."]
            return [f"Area spell hits {len(hit)} target(s): {', '.join(hit)}."]
        if effect_type == "area_status":
            x, y = self.effect_position(effect)
            radius = clamp_int(effect.get("radius"), 0, 99) if effect.get("radius") is not None else 15
            status = normalize_id(str(effect.get("status") or "strange"))
            display_name = str(effect.get("display_name") or effect.get("name") or "").strip() or status.replace("_", " ")
            expiry_text = str(effect.get("expiry_text") or effect.get("wears_off") or "").strip()
            duration = effect.get("duration", 3)
            affects = normalize_id(str(effect.get("affects") or "enemies"))
            include_player = bool(effect.get("include_player", False))
            if status not in MECHANICAL_STATUSES:
                return [f"Unknown status: {status}."]
            affected: list[str] = []
            dur_val2: int | str = "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
            for entity in self.entities_in_radius(x, y, radius):
                if entity.kind == "item" or entity.hp <= 0:
                    continue
                if entity.id == self.state.player_id and not include_player:
                    continue
                if not area_damage_affects(entity, affects, self.state.player_id):
                    continue
                entity.statuses[status] = dur_val2
                if display_name != status.replace("_", " "):
                    entity.status_display[status] = display_name
                if expiry_text:
                    entity.status_expiry_text[status] = expiry_text
                affected.append(entity.name)
            if not affected:
                return ["The status finds no one to cling to."]
            return [f"{display_name.title()} spreads to: {', '.join(affected)}."]
        if effect_type == "heal":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            actual = self.heal_entity(target, amount)
            if target.id == self.state.player_id:
                if actual == 0:
                    return ["Your wounds are already mended."]
                return [f"You heal {actual} HP."]
            if actual == 0:
                return [f"{target.name} is already whole."]
            return [f"{target.name} heals {actual} HP."]
        if effect_type == "restore_mana":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            before = target.mana
            target.mana = min(target.max_mana, target.mana + amount)
            gained = target.mana - before
            if target.id == self.state.player_id:
                return [f"You recover {gained} mana."]
            return [f"{target.name} recovers {gained} mana."]
        if effect_type == "teleport":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            x = clamp_int(effect.get("x"), 0, self.state.width - 1)
            y = clamp_int(effect.get("y"), 0, self.state.height - 1)
            if self.teleport_entity(target, x, y):
                return [f"{target.name} snaps to another tile."]
            return ["The teleport folds into a wall and fails."]
        if effect_type in {"push", "pull"}:
            target_str = str(effect.get("target") or "nearest_enemy")
            distance = clamp_int(effect.get("distance"), 1, 20)
            targets = self.resolve_target_group(target_str)
            if not targets:
                target = self.resolve_target(target_str)
                targets = [target] if target else []
            if not targets:
                return []
            origin = self.resolve_target(str(effect.get("origin") or "player")) or self.state.player
            moved_total = 0
            moved_names: list[str] = []
            for target in targets[:12]:
                if "dx" in effect or "dy" in effect:
                    dx = sign(clamp_int(effect.get("dx"), -1, 1))
                    dy = sign(clamp_int(effect.get("dy"), -1, 1))
                else:
                    dx = sign(target.x - origin.x)
                    dy = sign(target.y - origin.y)
                    if effect_type == "pull":
                        dx *= -1
                        dy *= -1
                moved = self.push_entity(target, dx, dy, distance)
                if moved:
                    moved_total += moved
                    moved_names.append(target.name)
            if len(targets) == 1:
                return [f"{targets[0].name} is moved {moved_total} tile(s)."]
            if moved_names:
                return [f"{len(moved_names)} target(s) are moved {moved_total} tile(s) total."]
            return ["The force finds no room to move anyone."]
        if effect_type in {"create_tile", "set_tile", "create_tiles"}:
            x, y = self.effect_position(effect)
            tile_name = str(effect.get("tile") or FLOOR).lower()
            tile = tile_from_name(tile_name)
            duration = optional_duration(effect.get("duration"))
            tags = set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            changed = 0
            tile_specs = effect.get("tiles")
            if isinstance(tile_specs, list):
                first_spec_tile: str | None = None
                for spec in tile_specs[:30]:
                    if not isinstance(spec, dict):
                        continue
                    tx = clamp_int(spec.get("x"), 0, self.state.width - 1)
                    ty = clamp_int(spec.get("y"), 0, self.state.height - 1)
                    spec_tile = tile_from_name(str(spec.get("tile") or tile_name))
                    if first_spec_tile is None:
                        first_spec_tile = spec_tile
                    spec_duration = optional_duration(spec.get("duration", duration))
                    spec_tags = set(normalize_id(str(tag)) for tag in coerce_list(spec.get("tags", list(tags))) if str(tag).strip())
                    if self.set_tile(tx, ty, spec_tile, spec_duration, spec_tags):
                        changed += 1
                if first_spec_tile is not None:
                    tile = first_spec_tile
            else:
                radius = clamp_int(effect.get("radius"), 0, 99)
                hollow = bool(effect.get("hollow") or effect.get("ring") or effect.get("perimeter"))
                inner_radius = max(0, radius - 1) if hollow else -1
                shape = normalize_id(str(effect.get("shape") or effect.get("pattern") or ""))
                if shape in {"line", "beam", "path", "corridor", "ray", "bridge", "wall", "barrier", "cone", "fan", "scatter", "spray"}:
                    for tx, ty in self.shape_points(effect, x, y)[:200]:
                        if self.set_tile(tx, ty, tile, duration, tags):
                            changed += 1
                else:
                    for tx, ty in self.points_in_radius(x, y, radius)[:200]:
                        if hollow and math.hypot(tx - x, ty - y) <= inner_radius:
                            continue
                        if self.set_tile(tx, ty, tile, duration, tags):
                            changed += 1
            return [f"Terrain changes to {TILE_NAMES.get(tile, 'strange')} on {changed} tile(s)."]
        if effect_type == "add_status":
            target_str = normalize_id(str(effect.get("target") or "nearest_enemy"))
            status = normalize_id(str(effect.get("status") or "strange"))
            display_name = str(effect.get("display_name") or effect.get("name") or "").strip() or status.replace("_", " ")
            expiry_text = str(effect.get("expiry_text") or effect.get("wears_off") or "").strip()
            duration = effect.get("duration", 3)
            dur_val: int | str = "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
            group_targets = self.resolve_target_group(target_str)
            if group_targets:
                if status not in MECHANICAL_STATUSES:
                    return [f"Unknown status: {status}."]
                for ent in group_targets:
                    ent.statuses[status] = dur_val
                    if display_name != status.replace("_", " "):
                        ent.status_display[status] = display_name
                    if expiry_text:
                        ent.status_expiry_text[status] = expiry_text
                return [f"{display_name.title()} spreads to {len(group_targets)} target(s)."]
            target = self.resolve_target(target_str)
            if not target or target.kind == "item":
                return []
            if status not in MECHANICAL_STATUSES:
                return [f"Unknown status: {status}."]
            target.statuses[status] = dur_val
            if display_name != status.replace("_", " "):
                target.status_display[status] = display_name
            if expiry_text:
                target.status_expiry_text[status] = expiry_text
            if target.id == self.state.player_id:
                return [f"You are now {display_name}."]
            return [f"{target.name} is now {display_name}."]
        if effect_type == "remove_status":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            status = normalize_id(str(effect.get("status") or ""))
            if status:
                target.statuses.pop(status, None)
                if target.id == self.state.player_id:
                    return [f"You are no longer {status.replace('_', ' ')}."]
                return [f"{target.name} is no longer {status.replace('_', ' ')}."]
            target.statuses.clear()
            if target.id == self.state.player_id:
                return ["All statuses leave you."]
            return [f"All statuses leave {target.name}."]
        if effect_type == "summon":
            name = str(effect.get("name") or effect.get("creature") or effect.get("creature_type") or "borrowed thing")
            faction = normalize_faction(effect.get("faction"), default="ally", neutral_is_ally=True)
            count = clamp_int(effect.get("count") or effect.get("quantity") or 1, 1, 6)
            char = str(effect.get("char") or ("a" if faction == "ally" else "e"))[:1]
            hp = clamp_int(effect.get("hp") or 5, 1, 20)
            attack = clamp_int(effect.get("attack") or 2, 0, 8)
            defense = clamp_int(effect.get("defense") or 0, 0, 8)
            tags = set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            spawned = 0
            for attempt in range(count):
                x, y = self.effect_position(effect) if attempt == 0 else (self.state.player.x, self.state.player.y)
                if not self.can_occupy(x, y):
                    x, y = self.find_open_tile_near(self.state.player.x, self.state.player.y)
                if not self.can_occupy(x, y):
                    continue
                self.spawn_actor(
                    name, char, x, y, hp, attack, defense, faction,
                    "simple" if faction == "enemy" else None,
                    tags=tags,
                    resistances=normalize_numeric_map(effect.get("resistances"), 0, 95),
                    weaknesses=normalize_numeric_map(effect.get("weaknesses"), 0, 200),
                )
                spawned += 1
            if spawned == 0:
                return [f"{name} tries to arrive, but finds no room."]
            return [f"{spawned} {name}{'' if spawned == 1 else 's'} arrive."]
        if effect_type == "spawn_item":
            name = str(effect.get("name") or effect.get("item") or "oddment")
            item_type = str(effect.get("item_type") or effect.get("item") or name)
            x, y = self.effect_position(effect)
            if self.tile_at(x, y) in BLOCKING_TILES:
                player = self.state.player
                x, y = self.find_open_tile_near(player.x, player.y)
            self.spawn_item(
                name,
                str(effect.get("char") or "?")[:1],
                x,
                y,
                item_type,
                clamp_int(effect.get("quantity"), 1, 99),
                material=str(effect.get("material") or "") or None,
                tags=set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip()),
            )
            return [f"{name} appears."]
        if effect_type == "conjure_item":
            return self._conjure_item(effect)
        if effect_type == "conjure_creature":
            return self._conjure_creature(effect)
        if effect_type == "modify_inventory":
            item = str(effect.get("item") or effect.get("item_type") or "").strip()
            if not item:
                return []
            amount = clamp_int(effect.get("amount"), -99, 99)
            mode = str(effect.get("mode") or "add").lower()
            current = self.state.inventory.get(item, 0)
            if mode == "set":
                new_amount = max(0, amount)
            elif mode == "remove":
                new_amount = max(0, current - abs(amount))
            else:
                new_amount = max(0, current + amount)
            if new_amount:
                self.state.inventory[item] = new_amount
            else:
                self.state.inventory.pop(item, None)
            return [f"Inventory shifts: {item} x{new_amount}."]
        if effect_type == "transform_entity":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return []
            if "name" in effect:
                target.name = str(effect["name"])[:40]
            if "char" in effect:
                target.char = str(effect["char"])[:1] or target.char
            if "faction" in effect:
                target.faction = normalize_faction(effect["faction"], default=target.faction)
            if "material" in effect:
                target.material = str(effect["material"])[:32]
            target.max_hp = clamp_int(effect.get("max_hp", target.max_hp), 1, 99)
            target.hp = clamp_int(effect.get("hp", target.hp), 0, target.max_hp)
            target.attack = clamp_int(effect.get("attack", target.attack), 0, 20)
            target.defense = clamp_int(effect.get("defense", target.defense), 0, 20)
            target.tags.update(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            if target.id == self.state.player_id:
                return ["You are transformed."]
            return [f"{target.name} is transformed."]
        if effect_type == "change_faction":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target or target.kind == "item":
                return []
            new_faction = normalize_faction(effect.get("faction"), default="neutral")
            target.faction = new_faction
            target.ai = None if target.faction in {"ally", "player"} else target.ai
            return [f"{target.name} now belongs to {target.faction}."]
        if effect_type in {"add_tag", "remove_tag"}:
            target = self.resolve_target(str(effect.get("target") or "player"))
            tag = normalize_id(str(effect.get("tag") or "strange"))
            if not target:
                return []
            if effect_type == "add_tag":
                target.tags.add(tag)
                if target.id == self.state.player_id:
                    return [f"You gain the {tag} tag."]
                return [f"{target.name} gains the {tag} tag."]
            target.tags.discard(tag)
            if target.id == self.state.player_id:
                return [f"You lose the {tag} tag."]
            return [f"{target.name} loses the {tag} tag."]
        if effect_type in {"add_resistance", "add_weakness"}:
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            damage_type = normalize_id(str(effect.get("damage_type") or effect.get("resistance") or "arcane"))
            amount = clamp_int(effect.get("amount"), 1, 95 if effect_type == "add_resistance" else 200)
            table = target.resistances if effect_type == "add_resistance" else target.weaknesses
            table[damage_type] = clamp_int(table.get(damage_type, 0) + amount, 0, 95 if effect_type == "add_resistance" else 200)
            word = "resists" if effect_type == "add_resistance" else "is vulnerable to"
            if target.id == self.state.player_id:
                player_word = "resist" if effect_type == "add_resistance" else "are vulnerable to"
                return [f"You {player_word} {damage_type}."]
            return [f"{target.name} {word} {damage_type}."]
        if effect_type == "set_flag":
            flag = normalize_id(str(effect.get("flag") or effect.get("id") or "unnamed_flag"))
            self.state.flags[flag] = effect.get("value", True)
            return [f"World flag set: {flag}."]
        if effect_type == "schedule_event":
            event = dict(effect.get("event") if isinstance(effect.get("event"), dict) else effect)
            event.pop("type", None)
            event["turns"] = clamp_int(effect.get("turns", event.get("turns")), 1, 999)
            event["event_type"] = str(effect.get("event_type") or event.get("event_type") or "message")
            self.state.event_timers.append(event)
            return [f"Something has been scheduled in {event['turns']} turn(s)."]
        if effect_type in {"create_trigger", "trigger", "ward"}:
            trigger_name = normalize_trigger_name(str(effect.get("trigger") or effect.get("on") or "on_next_spell"))
            effects = coerce_list(effect.get("effects") or effect.get("effect"))
            if not effects:
                return ["The trigger has nothing to do and collapses."]
            _TRIGGER_DEFAULT_NAMES = {
                "on_player_hit": "Retaliatory echo",
                "on_player_damaged": "Wound pact",
                "on_damaged": "Wound pact",
                "on_enemy_hit": "Predator's mark",
                "on_enemy_damaged": "Predator's mark",
                "on_enemy_death": "Death-pact",
                "on_next_spell": "Spell chain",
                "on_player_move": "Footstep echo",
            }
            raw_name = str(effect.get("name") or "").strip()
            default_name = _TRIGGER_DEFAULT_NAMES.get(trigger_name, "A waiting spell")
            trigger = {
                "id": self.next_entity_id("trigger"),
                "name": sanitize_name(raw_name or default_name, default_name),
                "trigger": trigger_name,
                "target": effect.get("target", "any"),
                "charges": clamp_int(effect.get("charges"), 1, 9),
                "duration": effect.get("duration", effect.get("turns", 6)),
                "effects": [dict(raw) for raw in effects[:8] if isinstance(raw, dict)],
            }
            if trigger["duration"] != "permanent":
                trigger["expires_turn"] = self.state.turn + clamp_int(trigger["duration"], 1, 999)
            self.state.triggers.append(trigger)
            return [f"{trigger['name']} waits for {trigger_name.replace('_', ' ')}."]
        if effect_type == "add_curse":
            message = self._apply_cost({"type": "curse", **effect})
            return [message] if message else []
        if effect_type == "message":
            text = str(effect.get("text") or "").strip()
            return [text] if text else []
        return []

    def shape_points(self, effect: dict[str, Any], fallback_x: int, fallback_y: int) -> list[tuple[int, int]]:
        shape = normalize_id(str(effect.get("shape") or effect.get("pattern") or ""))
        origin = self.resolve_target(str(effect.get("origin") or effect.get("from") or "player")) or self.state.player
        target = self.resolve_target(str(effect.get("target") or effect.get("to") or "nearest_enemy"))
        end_x, end_y = (target.x, target.y) if target else (fallback_x, fallback_y)
        width = clamp_int(effect.get("width"), 0, 3)
        radius = clamp_int(effect.get("radius"), 1, 12)
        points: list[tuple[int, int]] = []

        if shape in {"line", "beam", "path", "corridor", "ray", "bridge"}:
            for lx, ly in bresenham_line(origin.x, origin.y, end_x, end_y)[1:31]:
                points.extend(self.points_in_radius(lx, ly, width))
            return unique_points(points)

        if shape in {"wall", "barrier"}:
            dx = sign(end_x - origin.x)
            dy = sign(end_y - origin.y)
            if dx == 0 and dy == 0:
                dx = 1
            px, py = -dy, dx
            half = clamp_int(effect.get("length"), 1, 12) if "length" in effect else radius
            for step in range(-half, half + 1):
                wx = end_x + px * step
                wy = end_y + py * step
                points.extend(self.points_in_radius(wx, wy, width))
            return unique_points([(px, py) for px, py in points if self.in_bounds(px, py)])

        if shape in {"cone", "fan"}:
            vx = end_x - origin.x
            vy = end_y - origin.y
            if vx == 0 and vy == 0:
                vx, vy = 1, 0
            mag = max(0.001, math.hypot(vx, vy))
            ux, uy = vx / mag, vy / mag
            min_dot = 0.35
            for tx, ty in self.points_in_radius(origin.x, origin.y, radius):
                if tx == origin.x and ty == origin.y:
                    continue
                qx = tx - origin.x
                qy = ty - origin.y
                qmag = max(0.001, math.hypot(qx, qy))
                if (qx / qmag) * ux + (qy / qmag) * uy >= min_dot:
                    points.append((tx, ty))
            return unique_points(points)

        if shape in {"scatter", "spray"}:
            count = clamp_int(effect.get("count") or effect.get("quantity") or 8, 1, 40)
            candidates = self.points_in_radius(fallback_x, fallback_y, radius)
            self.rng.shuffle(candidates)
            return candidates[:count]

        return []

    def effect_position(self, effect: dict[str, Any]) -> tuple[int, int]:
        if "x" in effect and "y" in effect:
            return (
                clamp_int(effect.get("x"), 0, self.state.width - 1),
                clamp_int(effect.get("y"), 0, self.state.height - 1),
            )
        target = self.resolve_target(str(effect.get("target") or effect.get("center") or ""))
        if target:
            return target.x, target.y
        player = self.state.player
        return player.x, player.y

    def entities_in_radius(self, x: int, y: int, radius: int) -> list[Entity]:
        return [
            entity
            for entity in self.state.entities.values()
            if entity.alive and math.hypot(entity.x - x, entity.y - y) <= radius
        ]

    def points_in_radius(self, x: int, y: int, radius: int) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        for ty in range(y - radius, y + radius + 1):
            for tx in range(x - radius, x + radius + 1):
                if self.in_bounds(tx, ty) and math.hypot(tx - x, ty - y) <= radius:
                    points.append((tx, ty))
        return points

    def push_entity(self, entity: Entity, dx: int, dy: int, distance: int) -> int:
        if dx == 0 and dy == 0:
            return 0
        moved = 0
        for _ in range(distance):
            tx = entity.x + dx
            ty = entity.y + dy
            if not self.can_occupy(tx, ty):
                break
            entity.x = tx
            entity.y = ty
            moved += 1
            self._apply_tile_entry(entity)
            if entity.hp <= 0:
                break
        return moved

    def _conjure_item(self, effect: dict[str, Any]) -> list[str]:
        template = item_template(str(effect.get("template") or "generic_object"))
        count = clamp_int(effect.get("count", effect.get("quantity", 1)), 1, template.max_quantity)
        name = sanitize_name(str(effect.get("name") or template.item_type), template.item_type)
        material = sanitize_name(str(effect.get("material") or template.material), template.material, 24)
        tags = set(template.tags)
        tags.update(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
        x, y = self.resolve_placement(effect, prefer_unblocked=False)
        self.spawn_item(
            name,
            sanitize_char(str(effect.get("char") or template.char), template.char),
            x,
            y,
            str(effect.get("item_type") or template.item_type),
            count,
            material=material,
            tags=tags,
        )
        return [f"{name} appears."]

    def _conjure_creature(self, effect: dict[str, Any]) -> list[str]:
        template = creature_template(str(effect.get("template") or effect.get("creature_type") or "small_beast"))
        count = clamp_int(effect.get("count") or effect.get("quantity") or 1, 1, template.max_count)
        name = sanitize_name(str(effect.get("name") or template.id.replace("_", " ")), template.id.replace("_", " "))
        faction = normalize_faction(effect.get("faction"), default="ally", neutral_is_ally=True)
        char = sanitize_char(str(effect.get("char") or template.char), template.char)
        tags = set(template.tags)
        tags.update(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
        resistances = dict(template.resistances)
        resistances.update(normalize_numeric_map(effect.get("resistances"), 0, 95))
        weaknesses = dict(template.weaknesses)
        weaknesses.update(normalize_numeric_map(effect.get("weaknesses"), 0, 200))
        spawned = 0
        for index in range(count):
            x, y = self.resolve_placement(effect, prefer_unblocked=True, attempt=index)
            if not self.can_occupy(x, y):
                continue
            self.spawn_actor(
                name,
                char,
                x,
                y,
                clamp_int(effect.get("hp", template.hp), 1, 30),
                clamp_int(effect.get("attack", template.attack), 0, 12),
                clamp_int(effect.get("defense", template.defense), 0, 12),
                faction,
                None if faction in {"ally", "player"} else template.ai,
                tags=tags,
                resistances=resistances,
                weaknesses=weaknesses,
            )
            spawned += 1
        if spawned == 0:
            return [f"{name} tries to arrive, but finds no room."]
        return [f"{spawned} {name}{'' if spawned == 1 else 's'} arrive."]

    def resolve_placement(self, effect: dict[str, Any], prefer_unblocked: bool, attempt: int = 0) -> tuple[int, int]:
        placement = normalize_id(str(effect.get("placement") or "near_target"))
        if "x" in effect and "y" in effect:
            x = clamp_int(effect.get("x"), 0, self.state.width - 1)
            y = clamp_int(effect.get("y"), 0, self.state.height - 1)
            if not prefer_unblocked or self.can_occupy(x, y):
                return x, y
            return self.find_open_tile_near(x, y)

        target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
        player = self.state.player
        anchor = target if target is not None else player
        if placement == "target_tile":
            return (anchor.x, anchor.y) if not prefer_unblocked else self.find_open_tile_near(anchor.x, anchor.y)
        if placement == "near_player":
            return self.find_open_tile_near(player.x, player.y)
        if placement == "visible_floor":
            return self.random_visible_floor()
        if placement == "near_walls":
            near_wall = self.find_open_tile_near_wall(anchor.x, anchor.y, attempt)
            return near_wall if near_wall is not None else self.find_open_tile_near(anchor.x, anchor.y)
        return self.find_open_tile_near(anchor.x, anchor.y)

    def random_visible_floor(self) -> tuple[int, int]:
        candidates: list[tuple[int, int]] = []
        for key in self.state.visible:
            x, y = parse_tile_key(key)
            if self.can_occupy(x, y):
                candidates.append((x, y))
        if candidates:
            return self.rng.choice(candidates)
        player = self.state.player
        return self.find_open_tile_near(player.x, player.y)

    def find_open_tile_near_wall(self, x: int, y: int, attempt: int = 0) -> tuple[int, int] | None:
        candidates: list[tuple[int, int]] = []
        for radius in range(1, 10):
            for ty in range(y - radius, y + radius + 1):
                for tx in range(x - radius, x + radius + 1):
                    if not self.can_occupy(tx, ty):
                        continue
                    if any(self.tile_at(tx + dx, ty + dy) == WALL for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]):
                        candidates.append((tx, ty))
            if candidates:
                self.rng.shuffle(candidates)
                return candidates[attempt % len(candidates)]
        return None

    def find_open_tile_near(self, x: int, y: int) -> tuple[int, int]:
        for radius in range(0, 10):
            points: list[tuple[int, int]] = []
            for ty in range(y - radius, y + radius + 1):
                for tx in range(x - radius, x + radius + 1):
                    points.append((tx, ty))
            self.rng.shuffle(points)
            for tx, ty in points:
                if self.can_occupy(tx, ty):
                    return tx, ty
        return self.state.player.x, self.state.player.y


def sign(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def bresenham_line(x1: int, y1: int, x2: int, y2: int) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    error = dx + dy
    x = x1
    y = y1
    while True:
        points.append((x, y))
        if x == x2 and y == y2:
            return points
        twice_error = 2 * error
        if twice_error >= dy:
            error += dy
            x += sx
        if twice_error <= dx:
            error += dx
            y += sy


def unique_points(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []
    for point in points:
        if point in seen:
            continue
        seen.add(point)
        result.append(point)
    return result


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def optional_duration(value: Any) -> int | None:
    if value is None or value == "permanent":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return clamp_int(parsed, 1, 999)


def status_duration(value: Any) -> int:
    if value == "permanent":
        return 999
    return clamp_int(value, 0, 999)


def parse_tile_key(key: str) -> tuple[int, int]:
    x_text, y_text = key.split(",", 1)
    return int(x_text), int(y_text)


def normalize_id(value: str) -> str:
    return value.lower().strip().replace(" ", "_").replace("-", "_")


def normalize_faction(value: Any, default: str = "ally", neutral_is_ally: bool = False) -> str:
    normalized = normalize_id(str(value or default))
    aliases = {
        "player": "ally",
        "self": "ally",
        "you": "ally",
        "friendly": "ally",
        "friend": "ally",
        "friends": "ally",
        "companion": "ally",
        "summoned": "ally",
        "hostile": "enemy",
        "hostiles": "enemy",
        "foe": "enemy",
        "foes": "enemy",
        "monster": "enemy",
        "monsters": "enemy",
    }
    if normalized == "neutral" and neutral_is_ally:
        return "ally"
    if normalized in aliases:
        return aliases[normalized]
    if normalized in {"ally", "enemy", "neutral"}:
        return normalized
    return normalize_faction(default, default="ally")


def normalize_trigger_name(value: str) -> str:
    normalized = normalize_id(value)
    aliases = {
        "next_spell": "on_next_spell",
        "on_spell": "on_next_spell",
        "when_i_cast": "on_next_spell",
        "player_hit": "on_player_hit",
        "when_hit": "on_player_hit",
        "on_hit": "on_player_hit",
        "on_take_damage": "on_player_hit",
        "on_takes_damage": "on_player_hit",
        "on_receive_damage": "on_player_hit",
        "on_receives_damage": "on_player_hit",
        "on_player_takes_damage": "on_player_hit",
        "player_damaged": "on_player_damaged",
        "on_damage": "on_damaged",
        "on_damaged": "on_damaged",
        "enemy_hit": "on_enemy_hit",
        "enemy_damaged": "on_enemy_damaged",
        "enemy_death": "on_enemy_death",
        "on_kill": "on_enemy_death",
        "on_enemy_killed": "on_enemy_death",
        "on_enemy_dies": "on_enemy_death",
        "on_target_death": "on_enemy_death",
        "on_target_dies": "on_enemy_death",
        "player_move": "on_player_move",
        "on_move": "on_player_move",
        "on_player_moves": "on_player_move",
    }
    if normalized in aliases:
        return aliases[normalized]
    if not normalized.startswith("on_"):
        normalized = f"on_{normalized}"
    return normalized


def infer_behavior_tags(name: str, tags: set[str]) -> set[str]:
    tag_set = {normalize_id(str(tag)) for tag in tags if str(tag).strip()}
    name_text = normalize_id(name).replace("_", " ")

    def has_name_word(*words: str) -> bool:
        return any(word in name_text for word in words)

    def has_tag(*words: str) -> bool:
        return any(word in tag_set for word in words)

    def missing(prefix: str) -> bool:
        return not any(tag.startswith(prefix) for tag in tag_set)

    if has_name_word("archer", "ranger", "shooter", "bowman", "sniper", "gunner", "crossbow") or has_tag("archer", "shooter", "bowman"):
        tag_set.add("ranged")
    if has_name_word("ward", "totem", "beacon", "font", "pillar", "obelisk", "turret", "emanation", "radiator", "anchor") or has_tag("immobile", "passive", "ward", "totem"):
        tag_set.add("stationary")
    if has_name_word("guardian", "sentinel", "warden", "protector") and "stationary" not in tag_set:
        tag_set.add("guardian")
    if has_name_word("bomb", "explosive", "volatile", "detonator") or has_tag("bomb", "explosive", "volatile"):
        tag_set.add("explode_on_death")

    aura_rules = [
        ("aura_burn_2", "aura_burn", ("fire", "burning", "flaming", "flame", "hot", "infernal", "scorching"), ("burn", "fire", "flame", "scorch", "ember", "inferno", "blaze")),
        ("aura_heal_2", "aura_heal", ("heal", "healing", "restorative", "regenerative", "life", "mending"), ("heal", "healing", "medic", "cleric", "life", "restore", "mend")),
        ("aura_poison_2", "aura_poison", ("poison", "toxic", "plague", "venomous", "venom", "miasma"), ("poison", "toxic", "plague", "miasma", "venom", "pestilence")),
        ("aura_fear_2", "aura_fear", ("fear", "terror", "dread", "horror", "frightening", "terrifying"), ("fear", "dread", "terror", "horror", "despair")),
        ("aura_slow_2", "aura_slow", ("slow", "sluggish", "leaden", "weight", "heavy", "torpor"), ("slow", "sluggish", "leaden", "weight", "torpor")),
        ("aura_bleed_2", "aura_bleed", ("bleed", "bleeding", "hemorrhage", "thorn", "barbed"), ("bleed", "thorn", "shard", "barb", "needle")),
    ]
    for aura_tag, prefix, tag_words, name_words in aura_rules:
        if missing(prefix) and (has_tag(*tag_words) or has_name_word(*name_words)):
            tag_set.add(aura_tag)
    if "stationary" in tag_set and any(tag.startswith("aura_") for tag in tag_set):
        tag_set.add("pacifist")
    return tag_set


def singular_target_tag(value: str) -> str:
    normalized = normalize_id(value)
    if normalized.startswith("all_"):
        normalized = normalized[4:]
    if normalized.startswith("nearby_"):
        normalized = normalized[7:]
    if normalized.endswith("ies") and len(normalized) > 3:
        return f"{normalized[:-3]}y"
    if normalized.endswith(("ses", "xes", "ches", "shes")) and len(normalized) > 2:
        return normalized[:-2]
    if normalized.endswith("s") and len(normalized) > 1:
        return normalized[:-1]
    return normalized


def normalize_numeric_map(value: Any, minimum: int, maximum: int) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {normalize_id(str(key)): clamp_int(raw, minimum, maximum) for key, raw in value.items()}


def sanitize_name(value: str, fallback: str, max_length: int = 40) -> str:
    cleaned = "".join(char for char in value.strip() if 32 <= ord(char) < 127)
    cleaned = " ".join(cleaned.split())
    return (cleaned or fallback)[:max_length]


def sanitize_char(value: str, fallback: str) -> str:
    for char in value:
        if 32 <= ord(char) < 127 and char != " ":
            return char
    return fallback[:1] or "?"


def coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _flatten_effect(effect: dict[str, Any]) -> dict[str, Any]:
    """Hoist fields from a nested 'details' sub-object so the engine finds them at top level."""
    details = effect.get("details")
    if not isinstance(details, dict):
        return effect
    merged = dict(details)
    merged.update({k: v for k, v in effect.items() if k != "details"})
    return merged


def area_damage_affects(entity: Entity, affects: str, player_id: str) -> bool:
    if affects in {"all", "everyone", "any"}:
        return True
    if affects in {"enemies", "enemy", "hostile", "hostiles", "foes"}:
        return entity.faction == "enemy"
    if affects in {"allies", "ally", "friendlies", "friendly"}:
        return entity.faction in {"ally", "player"} or entity.id == player_id
    if affects in {"non_player", "nonplayer", "others"}:
        return entity.id != player_id
    if affects in {"player", "self"}:
        return entity.id == player_id
    return entity.id != player_id


def tile_from_name(name: str) -> str:
    normalized = name.lower().strip().replace(" ", "_")
    return TILE_ALIASES.get(normalized, FLOOR)
