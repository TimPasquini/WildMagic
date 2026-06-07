from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


FLOOR = "."
WALL = "#"
DOOR = "+"
OPEN_DOOR = "/"
STAIRS_DOWN = ">"
STAIRS_UP = "<"
WATER = "~"
FIRE = "^"
SLICK_ICE = "_"
ICE_WALL = "*"
POISON_CLOUD = "%"
VINES = "&"
RUBBLE = ";"
MIST = ":"


TILE_NAMES = {
    FLOOR: "floor",
    WALL: "wall",
    DOOR: "door",
    OPEN_DOOR: "open door",
    STAIRS_DOWN: "stairs down",
    STAIRS_UP: "stairs up",
    WATER: "water",
    FIRE: "wild fire",
    SLICK_ICE: "slick ice",
    ICE_WALL: "ice wall",
    POISON_CLOUD: "poison cloud",
    VINES: "vines",
    RUBBLE: "rubble",
    MIST: "mist",
}


TILE_TAGS = {
    FLOOR: {"walkable"},
    WALL: {"stone", "opaque", "blocking"},
    DOOR: {"wood", "opaque", "blocking", "door"},
    OPEN_DOOR: {"wood", "door"},
    STAIRS_DOWN: {"stairs"},
    STAIRS_UP: {"stairs"},
    WATER: {"water", "wet", "conductive"},
    FIRE: {"fire", "hot", "hazard", "light"},
    SLICK_ICE: {"ice", "cold", "slippery", "walkable"},
    ICE_WALL: {"ice", "cold", "opaque", "blocking"},
    POISON_CLOUD: {"poison", "gas", "hazard"},
    VINES: {"plant", "flammable", "snaring"},
    RUBBLE: {"stone", "rough"},
    MIST: {"water", "gas"},
}


TILE_ALIASES = {
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
    "wildfire": FIRE,
    "^": FIRE,
    "lava": FIRE,
    "magma": FIRE,
    "ice": SLICK_ICE,
    "slick_ice": SLICK_ICE,
    "ice_floor": SLICK_ICE,
    "_": SLICK_ICE,
    "ice_wall": ICE_WALL,
    "wall_of_ice": ICE_WALL,
    "iron_bars": ICE_WALL,
    "bars": ICE_WALL,
    "barrier": ICE_WALL,
    "*": ICE_WALL,
    "poison": POISON_CLOUD,
    "poison_cloud": POISON_CLOUD,
    "acid": POISON_CLOUD,
    "acid_pool": POISON_CLOUD,
    "%": POISON_CLOUD,
    "vines": VINES,
    "vine": VINES,
    "caltrops": VINES,
    "caltrop": VINES,
    "thorns": VINES,
    "thorn": VINES,
    "netting": VINES,
    "net": VINES,
    "web": VINES,
    "webbing": VINES,
    "&": VINES,
    "rubble": RUBBLE,
    "spikes": RUBBLE,
    "spike": RUBBLE,
    "debris": RUBBLE,
    "wreckage": RUBBLE,
    "bones": RUBBLE,
    ";": RUBBLE,
    "mist": MIST,
    "smoke": MIST,
    "fog": MIST,
    ":": MIST,
    "mud": WATER,
    "swamp": WATER,
}


BLOCKING_TILES = {WALL, DOOR, ICE_WALL}
DAMAGING_TILES = {FIRE, POISON_CLOUD}
DAMAGE_TYPES = {
    "physical",
    "fire",
    "frost",
    "lightning",
    "poison",
    "acid",
    "force",
    "radiant",
    "shadow",
    "psychic",
    "arcane",
    "blood",
    "spark",
}


MECHANICAL_STATUSES = {
    "burning",
    "poisoned",
    "bleeding",
    "frozen",
    "stunned",
    "rooted",
    "webbed",
    "slowed",
    "hasted",
    "invisible",
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
    "regenerating",
    "berserk",
    "empowered",
    "cursed",
}


@dataclass
class Entity:
    id: str
    name: str
    kind: str
    x: int
    y: int
    char: str
    hp: int = 1
    max_hp: int = 1
    mana: int = 0
    max_mana: int = 0
    attack: int = 0
    defense: int = 0
    blocks: bool = False
    faction: str = "neutral"
    ai: str | None = None
    item_type: str | None = None
    material: str | None = None
    quantity: int = 1
    statuses: dict[str, int | str] = field(default_factory=dict)
    status_display: dict[str, str] = field(default_factory=dict)
    status_expiry_text: dict[str, str] = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)
    resistances: dict[str, int] = field(default_factory=dict)
    weaknesses: dict[str, int] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.hp > 0 or self.kind == "item"

    def to_public_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "position": {"x": self.x, "y": self.y},
            "char": self.char,
            "blocks": self.blocks,
            "faction": self.faction,
            "statuses": self.statuses,
            "tags": sorted(self.tags),
            "resistances": self.resistances,
            "weaknesses": self.weaknesses,
        }
        if self.status_display:
            data["status_display"] = self.status_display
        if self.kind != "item":
            data.update(
                {
                    "hp": self.hp,
                    "max_hp": self.max_hp,
                    "mana": self.mana,
                    "max_mana": self.max_mana,
                    "attack": self.attack,
                    "defense": self.defense,
                }
            )
        else:
            data.update({"item_type": self.item_type, "material": self.material, "quantity": self.quantity})
        return data


@dataclass
class Curse:
    id: str
    name: str
    description: str
    stacks: int = 1

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "stacks": self.stacks,
        }


@dataclass
class GameStats:
    enemies_killed: int = 0
    spells_cast: int = 0
    spells_failed: int = 0
    items_used: int = 0
    items_collected: int = 0
    curses_gained: int = 0
    deepest_floor: int = 1
    damage_dealt: int = 0
    damage_taken: int = 0
    hp_healed: int = 0

    def to_dict(self) -> "dict[str, Any]":
        return {
            "enemies_killed": self.enemies_killed,
            "spells_cast": self.spells_cast,
            "spells_failed": self.spells_failed,
            "items_used": self.items_used,
            "items_collected": self.items_collected,
            "curses_gained": self.curses_gained,
            "deepest_floor": self.deepest_floor,
            "damage_dealt": self.damage_dealt,
            "damage_taken": self.damage_taken,
            "hp_healed": self.hp_healed,
        }


@dataclass
class WildMagicOutcome:
    consumed_turn: bool
    technical_failure: bool
    messages: list[str]
