from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import random
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
    TILE_NAMES,
    TILE_TAGS,
    WildMagicOutcome,
)
from .templates import creature_template, creature_template_ids, item_template, item_template_ids


MAP_WIDTH = 42
MAP_HEIGHT = 28


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
    game_over: bool = False
    victory: bool = False
    rng_seed: int | None = None
    scenario: str = "dungeon"
    fov_radius: int = 9
    depth: int = 1
    max_depth: int = 3

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
            ("goblin cutpurse", "g", 8, 3, 0, "goblin", {"goblin", "flesh"}, {}, {}),
            ("glass bat", "b", 5, 2, 0, "bat", {"beast", "glass"}, {"poison": 25}, {"force": 25}),
            ("ash slime", "s", 10, 2, 1, "slime", {"slime", "ash"}, {"fire": 35, "poison": 50}, {"frost": 25}),
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
            tags=set(tags or ()),
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
        self.pick_up_items_at_player()
        self._apply_tile_entry(player)
        self.update_fov()
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

    def nearest_enemy(self, max_distance: int | None = None) -> Entity | None:
        player = self.state.player
        enemies = self.living_enemies()
        if max_distance is not None:
            enemies = [enemy for enemy in enemies if self.distance(player, enemy) <= max_distance]
        if not enemies:
            return None
        return min(enemies, key=lambda enemy: self.distance(player, enemy))

    def attack(self, attacker: Entity, defender: Entity) -> None:
        amount = max(1, attacker.attack - defender.defense + self.rng.randint(0, 2))
        self.damage_entity(defender, amount, "physical")
        if defender.hp > 0:
            self.state.add_message(f"{attacker.name} hits {defender.name} for {amount}.")
        else:
            self.state.add_message(f"{attacker.name} drops {defender.name}.")

    def damage_entity(self, entity: Entity, amount: int, damage_type: str) -> int:
        if entity.kind == "item" or entity.hp <= 0:
            return 0
        damage_type = normalize_id(damage_type)
        actual = self._modified_damage(entity, amount, damage_type)
        entity.hp -= actual
        if entity.hp <= 0:
            entity.hp = 0
            entity.blocks = False
            entity.char = "%"
            entity.ai = None
            entity.statuses.clear()
            if entity.id == self.state.player_id:
                self.state.game_over = True
                self.state.add_message("You die. The dungeon keeps your echo.")
            elif not self.living_enemies():
                self.state.victory = True
                self.state.add_message("For a breath, the floor is yours.")
        elif damage_type == "fire":
            entity.statuses["burning"] = max(status_duration(entity.statuses.get("burning")), 3)
        elif damage_type == "frost":
            entity.statuses["slowed"] = max(status_duration(entity.statuses.get("slowed")), 2)
        elif damage_type == "lightning" and self.tile_at(entity.x, entity.y) == WATER:
            entity.statuses["stunned"] = max(status_duration(entity.statuses.get("stunned")), 1)
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
        return entity.hp - before

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

    def pick_up_items_at_player(self) -> None:
        player = self.state.player
        for entity in list(self.entities_at(player.x, player.y)):
            if entity.kind != "item":
                continue
            item_type = entity.item_type or entity.name
            self.state.inventory[item_type] = self.state.inventory.get(item_type, 0) + entity.quantity
            self.state.add_message(f"You pick up {entity.name}.")
            del self.state.entities[entity.id]

    def finish_player_turn(self) -> None:
        if self.state.game_over:
            return
        self.state.turn += 1
        self._tick_environment()
        self._tick_tile_durations()
        self._tick_event_timers()
        self.update_fov()
        self._enemy_turns()
        self._regenerate_player()

    def _tick_environment(self) -> None:
        for entity in list(self.state.entities.values()):
            if entity.kind == "item" or entity.hp <= 0:
                continue
            tile = self.tile_at(entity.x, entity.y)
            if tile == FIRE:
                self.damage_entity(entity, 1, "fire")
                if entity.hp > 0:
                    entity.statuses["burning"] = max(status_duration(entity.statuses.get("burning")), 2)
                    self.state.add_message(f"{entity.name} is scorched by wild fire.")
            elif tile == POISON_CLOUD:
                self.damage_entity(entity, 1, "poison")
                if entity.hp > 0:
                    entity.statuses["poisoned"] = max(status_duration(entity.statuses.get("poisoned")), 2)
                    self.state.add_message(f"{entity.name} coughs in poison vapors.")

            if "burning" in entity.statuses:
                turns = status_duration(entity.statuses["burning"])
                self.damage_entity(entity, 1, "fire")
                if entity.hp > 0:
                    self.state.add_message(f"{entity.name} burns.")
                turns -= 1
                if turns <= 0:
                    entity.statuses.pop("burning", None)
                else:
                    entity.statuses["burning"] = turns
            if "poisoned" in entity.statuses:
                turns = status_duration(entity.statuses["poisoned"])
                self.damage_entity(entity, 1, "poison")
                if entity.hp > 0:
                    self.state.add_message(f"{entity.name} weakens from poison.")
                turns -= 1
                if turns <= 0:
                    entity.statuses.pop("poisoned", None)
                else:
                    entity.statuses["poisoned"] = turns
            self._tick_simple_statuses(entity)

    def _apply_tile_entry(self, entity: Entity) -> None:
        tile = self.tile_at(entity.x, entity.y)
        if tile == FIRE:
            self.damage_entity(entity, 1, "fire")
            entity.statuses["burning"] = max(status_duration(entity.statuses.get("burning")), 2)
            self.state.add_message(f"{entity.name} steps into wild fire.")
        elif tile == POISON_CLOUD:
            self.damage_entity(entity, 1, "poison")
            entity.statuses["poisoned"] = max(status_duration(entity.statuses.get("poisoned")), 2)
            self.state.add_message(f"{entity.name} inhales a poison cloud.")
        elif tile == SLICK_ICE:
            entity.statuses["slowed"] = max(status_duration(entity.statuses.get("slowed")), 1)
            self.state.add_message(f"{entity.name} skids on slick ice.")

    def _tick_simple_statuses(self, entity: Entity) -> None:
        if "bleeding" in entity.statuses:
            turns = status_duration(entity.statuses["bleeding"])
            self.damage_entity(entity, 1, "blood")
            if entity.hp > 0:
                self.state.add_message(f"{entity.name} bleeds.")
            turns -= 1
            if turns <= 0:
                entity.statuses.pop("bleeding", None)
            else:
                entity.statuses["bleeding"] = turns

        for status in [
            "frozen",
            "stunned",
            "rooted",
            "webbed",
            "slowed",
            "hasted",
            "confused",
            "frightened",
            "marked",
            "revealed",
            "warded",
            "strained",
            "drained",
            "jinxed",
            "crawling_skin",
            "silenced",
        ]:
            if status not in entity.statuses:
                continue
            value = entity.statuses[status]
            if value == "permanent":
                continue
            turns = status_duration(value) - 1
            if turns <= 0:
                entity.statuses.pop(status, None)
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
            text = str(event.get("text") or "Something promised arrives late.")
            self.state.add_message(text)
        elif event_type == "summon":
            player = self.state.player
            x, y = self.find_open_tile_near(player.x, player.y)
            self.spawn_actor(
                str(event.get("name") or "debt collector"),
                str(event.get("char") or "d")[:1],
                x,
                y,
                clamp_int(event.get("hp"), 1, 30),
                clamp_int(event.get("attack"), 0, 10),
                0,
                str(event.get("faction") or "enemy"),
                "simple",
                tags=set(coerce_list(event.get("tags"))),
            )
            self.state.add_message(f"{event.get('name') or 'Something'} arrives to collect.")

    def _enemy_turns(self) -> None:
        player = self.state.player
        for enemy in list(self.living_enemies()):
            if any(status in enemy.statuses for status in ["stunned", "frozen"]):
                self.state.add_message(f"{enemy.name} cannot act.")
                continue
            if self.distance(enemy, player) <= 1.5:
                self.attack(enemy, player)
                if self.state.game_over:
                    return
                continue
            if any(status in enemy.statuses for status in ["rooted", "webbed"]):
                continue
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

    def enemy_can_sense_player(self, enemy: Entity) -> bool:
        player = self.state.player
        distance = self.distance(enemy, player)
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
            if not self.in_bounds(tx, ty) or self.tile_at(tx, ty) in BLOCKING_TILES:
                continue
            blocker = self.blocking_entity_at(tx, ty)
            if blocker is not None and blocker.id not in {entity.id, self.state.player_id}:
                continue
            if blocker is not None and blocker.id == self.state.player_id and (tx, ty) != goal:
                continue
            valid.append((tx, ty))
        return valid

    def _regenerate_player(self) -> None:
        player = self.state.player
        if self.state.turn % 5 == 0 and player.mana < player.max_mana:
            player.mana += 1

    def resolve_target(self, target_id: str | None) -> Entity | None:
        if not target_id or target_id in {"player", "self", "@", "you"}:
            return self.state.player
        if target_id in {"nearest_enemy", "nearest enemy", "enemy"}:
            return self.nearest_enemy()
        return self.state.entities.get(target_id)

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
            "visible_tile_count": len(self.state.visible),
            "explored_tile_count": len(self.state.explored),
            "nearby_entities": nearby_entities,
            "nearby_map": self.nearby_map_strings(radius=9),
            "nearby_tile_details": self.nearby_tile_details(radius=5),
            "tile_legend": {tile: {"name": name, "tags": sorted(TILE_TAGS.get(tile, set()))} for tile, name in TILE_NAMES.items()},
            "supported_effects": [
                "damage",
                "area_damage",
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
                "area_limits": "radius 0-4, max 30 changed tiles per effect",
                "cost_timing": "effects happen first, then costs are revealed and applied",
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
        outcome_text = str(resolution.get("outcome_text") or "").strip()
        if not accepted:
            reason = str(resolution.get("rejected_reason") or "The spell is too vast to fit through you.")
            self.state.add_message(reason)
            self.finish_player_turn()
            return WildMagicOutcome(True, False, [reason])

        if outcome_text:
            self.state.add_message(outcome_text)
            messages.append(outcome_text)

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

        self.finish_player_turn()
        return WildMagicOutcome(True, False, messages)

    def _apply_cost(self, cost: dict[str, Any]) -> str | None:
        if not isinstance(cost, dict):
            return None
        cost_type = str(cost.get("type", "")).lower()
        player = self.state.player
        if cost_type == "mana":
            amount = clamp_int(cost.get("amount"), 0, 99)
            player.mana = max(0, player.mana - amount)
            return f"Cost: {amount} mana."
        if cost_type in {"health", "hp"}:
            amount = clamp_int(cost.get("amount"), 0, 99)
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
            item = str(cost.get("item") or cost.get("id") or "").strip()
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
            return f"Curse gained: {name}."
        if cost_type == "status":
            status = normalize_id(str(cost.get("status") or cost.get("id") or "strained"))
            duration = cost.get("duration", 5)
            if status not in MECHANICAL_STATUSES:
                name = status.replace("_", " ").title()
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
            player.statuses[status] = "permanent" if duration == "permanent" else clamp_int(duration, 1, 999)
            return f"Cost: you are {status.replace('_', ' ')}."
        return None

    def _apply_effect(self, effect: dict[str, Any]) -> list[str]:
        if not isinstance(effect, dict):
            return []
        effect_type = str(effect.get("type", "")).lower()
        if effect_type == "damage":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return ["The spell claws at empty air."]
            amount = clamp_int(effect.get("amount"), 0, 25)
            damage_type = str(effect.get("damage_type") or "arcane")
            actual = self.damage_entity(target, amount, damage_type)
            return [f"{target.name} takes {actual} {damage_type} damage."]
        if effect_type == "area_damage":
            x, y = self.effect_position(effect)
            radius = clamp_int(effect.get("radius"), 0, 4)
            amount = clamp_int(effect.get("amount"), 0, 20)
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
        if effect_type == "heal":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            amount = clamp_int(effect.get("amount"), 0, 20)
            actual = self.heal_entity(target, amount)
            return [f"{target.name} heals {actual}."]
        if effect_type == "restore_mana":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            amount = clamp_int(effect.get("amount"), 0, 20)
            before = target.mana
            target.mana = min(target.max_mana, target.mana + amount)
            return [f"{target.name} recovers {target.mana - before} mana."]
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
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return []
            distance = clamp_int(effect.get("distance"), 1, 6)
            if "dx" in effect or "dy" in effect:
                dx = sign(clamp_int(effect.get("dx"), -1, 1))
                dy = sign(clamp_int(effect.get("dy"), -1, 1))
            else:
                origin = self.resolve_target(str(effect.get("origin") or "player")) or self.state.player
                dx = sign(target.x - origin.x)
                dy = sign(target.y - origin.y)
                if effect_type == "pull":
                    dx *= -1
                    dy *= -1
            moved = self.push_entity(target, dx, dy, distance)
            return [f"{target.name} is moved {moved} tile(s)."]
        if effect_type in {"create_tile", "set_tile", "create_tiles"}:
            x, y = self.effect_position(effect)
            tile_name = str(effect.get("tile") or FLOOR).lower()
            tile = tile_from_name(tile_name)
            duration = optional_duration(effect.get("duration"))
            tags = set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            changed = 0
            tile_specs = effect.get("tiles")
            if isinstance(tile_specs, list):
                for spec in tile_specs[:30]:
                    if not isinstance(spec, dict):
                        continue
                    tx = clamp_int(spec.get("x"), 0, self.state.width - 1)
                    ty = clamp_int(spec.get("y"), 0, self.state.height - 1)
                    spec_tile = tile_from_name(str(spec.get("tile") or tile_name))
                    spec_duration = optional_duration(spec.get("duration", duration))
                    spec_tags = set(normalize_id(str(tag)) for tag in coerce_list(spec.get("tags", list(tags))) if str(tag).strip())
                    if self.set_tile(tx, ty, spec_tile, spec_duration, spec_tags):
                        changed += 1
            else:
                radius = clamp_int(effect.get("radius"), 0, 4)
                for tx, ty in self.points_in_radius(x, y, radius)[:30]:
                    if self.set_tile(tx, ty, tile, duration, tags):
                        changed += 1
            return [f"Terrain changes to {TILE_NAMES.get(tile, 'strange')} on {changed} tile(s)."]
        if effect_type == "add_status":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return []
            status = normalize_id(str(effect.get("status") or "strange"))
            duration = effect.get("duration", 3)
            target.statuses[status] = "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
            if target.id == self.state.player_id:
                return [f"You are now {status.replace('_', ' ')}."]
            return [f"{target.name} is now {status.replace('_', ' ')}."]
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
            name = str(effect.get("name") or "borrowed thing")
            faction = str(effect.get("faction") or "enemy")
            x, y = self.effect_position(effect)
            if not self.can_occupy(x, y):
                player = self.state.player
                x, y = self.find_open_tile_near(player.x, player.y)
            hp = clamp_int(effect.get("hp"), 1, 20)
            attack = clamp_int(effect.get("attack"), 0, 8)
            char = str(effect.get("char") or ("a" if faction == "ally" else "e"))[:1]
            self.spawn_actor(
                name,
                char,
                x,
                y,
                hp,
                attack,
                clamp_int(effect.get("defense"), 0, 8),
                faction,
                "simple" if faction == "enemy" else None,
                tags=set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip()),
                resistances=normalize_numeric_map(effect.get("resistances"), 0, 95),
                weaknesses=normalize_numeric_map(effect.get("weaknesses"), 0, 200),
            )
            return [f"{name} arrives."]
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
                target.faction = str(effect["faction"])[:24]
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
            if not target:
                return []
            target.faction = str(effect.get("faction") or "neutral")[:24]
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
            damage_type = normalize_id(str(effect.get("damage_type") or "arcane"))
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
        if effect_type == "add_curse":
            message = self._apply_cost({"type": "curse", **effect})
            return [message] if message else []
        if effect_type == "message":
            text = str(effect.get("text") or "").strip()
            return [text] if text else []
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
        template = creature_template(str(effect.get("template") or "small_beast"))
        count = clamp_int(effect.get("count", 1), 1, template.max_count)
        name = sanitize_name(str(effect.get("name") or template.id.replace("_", " ")), template.id.replace("_", " "))
        faction = sanitize_name(str(effect.get("faction") or template.faction), template.faction, 24)
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
    return {
        "floor": FLOOR,
        ".": FLOOR,
        "wall": WALL,
        "#": WALL,
        "door": DOOR,
        "closed_door": DOOR,
        "+": DOOR,
        "open_door": OPEN_DOOR,
        "/": OPEN_DOOR,
        "stairs_down": STAIRS_DOWN,
        "down_stairs": STAIRS_DOWN,
        ">": STAIRS_DOWN,
        "stairs_up": STAIRS_UP,
        "up_stairs": STAIRS_UP,
        "<": STAIRS_UP,
        "water": WATER,
        "~": WATER,
        "fire": FIRE,
        "wild_fire": FIRE,
        "^": FIRE,
        "ice": SLICK_ICE,
        "slick_ice": SLICK_ICE,
        "ice_floor": SLICK_ICE,
        "_": SLICK_ICE,
        "ice_wall": ICE_WALL,
        "wall_of_ice": ICE_WALL,
        "*": ICE_WALL,
        "poison": POISON_CLOUD,
        "poison_cloud": POISON_CLOUD,
        "%": POISON_CLOUD,
        "vines": VINES,
        "vine": VINES,
        "&": VINES,
        "rubble": RUBBLE,
        ";": RUBBLE,
        "mist": MIST,
        ":": MIST,
    }.get(normalized, FLOOR)
