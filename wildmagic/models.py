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
ROAD = "="


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
    ROAD: "dirt road",
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
    ROAD: {"walkable", "road"},
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
    "road": ROAD,
    "path": ROAD,
    "trail": ROAD,
    "=": ROAD,
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
class CharacterProfile:
    """The universal profile carried by any creature — player, NPC, or enemy alike.
    It is deliberately the *same* type for everyone so the wild-magic resolver,
    character creation, and body-swap all treat every caster identically: when you
    inhabit a body you simply adopt that body's profile.

    Stats are the three wild-magic-flavored axes (see docs/CHARACTER_CREATION.md):
    Vigor (body), Attunement (mana/potency), Composure (how hard wild magic bites
    back). The free-form fields feed the LLM — appearance is what NPCs perceive,
    signature is a persistent per-cast flavor lens."""

    origin_id: str = "wanderer"
    vigor: int = 3
    attunement: int = 3
    composure: int = 3
    appearance: str = ""
    backstory: str = ""
    signature: str = ""
    # The character's proper name, used where *others* refer to them (NPC dialogue,
    # imperial warrants) — never the message log, which stays second-person "You".
    # Empty for most NPCs/enemies (they fall back to the entity name); on body-swap an
    # inhabited body's empty name means NPCs call you by that body's name.
    name: str = ""
    # Self-described gender ("Male"/"Female"/custom), or "" if unspecified. Fed as the
    # first word of the portrait description; otherwise free-form.
    gender: str = ""
    # Filesystem path to a generated character portrait (PNG), if one was made at
    # creation. Empty when none. See wildmagic/portraits.py.
    portrait_path: str = ""

    def composure_band(self) -> str:
        """Coarse label fed to the resolver as a volatility dial."""
        if self.composure <= 2:
            return "low"
        if self.composure >= 5:
            return "high"
        return "steady"

    # Stat → combat derivation. Stats run ~1–6 (origin baselines 2–5, cap 6), so the
    # spread is deliberately noticeable: a high-Vigor body is meaningfully tankier.
    # vigor 3 / attunement 3 reproduce the old fixed 24 HP / 14 MP baseline, so a
    # middling character is unchanged. See docs/CHARACTER_CREATION.md.
    def derive_max_hp(self) -> int:
        return 12 + 4 * self.vigor

    def derive_max_mana(self) -> int:
        return 5 + 3 * self.attunement

    def derive_attack(self) -> int:
        return 3 + self.vigor // 3

    def derive_defense(self) -> int:
        return 1

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin_id,
            "vigor": self.vigor,
            "attunement": self.attunement,
            "composure": self.composure,
            "composure_band": self.composure_band(),
            "appearance": self.appearance,
            "backstory": self.backstory,
            "signature": self.signature,
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
    equipment: dict[str, str | None] = field(
        default_factory=lambda: {
            "weapon": None,
            "armor": None,
            "charm": None,
            "head": None,
            "chest": None,
            "legs": None,
            "feet": None,
            "hands": None,
        }
    )
    description: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    # Per-entity state. Once global on GameState (player-only); now carried by the
    # entity itself so any creature can own items/curses and so body-swap leaves
    # inventory with the body. GameState.inventory/curses are properties that
    # resolve to whichever entity is currently controlled.
    inventory: dict[str, int] = field(default_factory=dict)
    curses: dict[str, "Curse"] = field(default_factory=dict)
    profile: "CharacterProfile | None" = None

    def __post_init__(self) -> None:
        if self.kind == "player" and not any(self.equipment.values()):
            self.equipment["chest"] = "tattered cloak"
            self.equipment["legs"] = "woolen trousers"

    @property
    def alive(self) -> bool:
        return self.hp > 0 or self.kind in {"item", "prop"}

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
        if self.description:
            data["description"] = self.description
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
                    "equipment": self.equipment,
                }
            )
        else:
            data.update(
                {
                    "item_type": self.item_type,
                    "material": self.material,
                    "quantity": self.quantity,
                }
            )
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
class NPCProfile:
    """Persona and perception data for a talkable NPC, kept separate from Entity
    (which only carries physical/combat state) the same way Curse is kept separate."""

    entity_id: str
    name: str
    role: str
    backstory: str
    appearance: str = ""
    traits: list[str] = field(default_factory=list)
    memory: list[str] = field(default_factory=list)
    conversation: list[dict[str, str]] = field(default_factory=list)
    wares: dict[str, int] = field(default_factory=dict)
    wanted_item: str | None = None
    wanted_qty: int = 0
    reward_gold: int = 0
    reward_item: str | None = None
    reward_qty: int = 0
    quest_completed: bool = False

    def remember(self, text: str, limit: int = 12) -> None:
        self.memory.append(text)
        self.memory = self.memory[-limit:]

    def record_exchange(self, speaker: str, text: str, limit: int = 16) -> None:
        self.conversation.append({"speaker": speaker, "text": text})
        self.conversation = self.conversation[-limit:]

    def to_dialogue_context(self) -> dict[str, Any]:
        context: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "backstory": self.backstory,
            "appearance": self.appearance,
            "traits": list(self.traits),
            "things_i_have_noticed": list(self.memory),
            "recent_conversation": list(self.conversation),
        }
        if self.wares:
            context["wares_for_sale"] = dict(sorted(self.wares.items()))
        if self.wanted_item and not self.quest_completed:
            context["my_current_need"] = {
                "wants_item": self.wanted_item,
                "quantity": self.wanted_qty,
                "will_reward_gold": self.reward_gold,
                "will_reward_item": self.reward_item,
                "reward_item_quantity": self.reward_qty,
            }
        elif self.quest_completed:
            context["quest_status"] = (
                "I have already received my requested item and rewarded the player."
            )
        return context


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


@dataclass(frozen=True)
class RoomProfile:
    """Semantic room data that richer generation can use as seed context."""

    id: str
    x: int
    y: int
    w: int
    h: int
    room_type: str
    era: str
    condition: str
    topics: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    secret_slots: list[dict[str, Any]] = field(default_factory=list)
    promise_hooks: list[str] = field(default_factory=list)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def to_public_dict(self, include_secrets: bool = False) -> dict[str, Any]:
        """Room data for LLM context and summaries. Secret slots stay out of
        LLM-facing packets by default — the model must never learn whether a
        secret exists except through the explicit investigate contract."""
        data = {
            "id": self.id,
            "bounds": {"x": self.x, "y": self.y, "w": self.w, "h": self.h},
            "center": {"x": self.center[0], "y": self.center[1]},
            "type": self.room_type,
            "era": self.era,
            "condition": self.condition,
            "topics": list(self.topics),
            "tags": list(self.tags),
            "promise_hooks": list(self.promise_hooks),
        }
        if include_secrets:
            data["secret_slots"] = [dict(slot) for slot in self.secret_slots]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoomProfile":
        bounds = data.get("bounds") if isinstance(data.get("bounds"), dict) else {}
        return cls(
            id=str(data.get("id") or ""),
            x=int(bounds.get("x", data.get("x", 0))),
            y=int(bounds.get("y", data.get("y", 0))),
            w=int(bounds.get("w", data.get("w", 1))),
            h=int(bounds.get("h", data.get("h", 1))),
            room_type=str(data.get("type") or data.get("room_type") or "room"),
            era=str(data.get("era") or "unknown"),
            condition=str(data.get("condition") or "undisturbed"),
            topics=[
                str(topic) for topic in data.get("topics", []) if str(topic).strip()
            ],
            tags=[str(tag) for tag in data.get("tags", []) if str(tag).strip()],
            secret_slots=[
                dict(slot)
                for slot in data.get("secret_slots", [])
                if isinstance(slot, dict)
            ],
            promise_hooks=[
                str(hook) for hook in data.get("promise_hooks", []) if str(hook).strip()
            ],
        )


@dataclass
class CanonRecord:
    """Per-run materialized text or description that has become game canon."""

    id: str
    kind: str
    attachment: dict[str, Any]
    text: str
    title: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str = "grammar_fallback"
    seed_packet: dict[str, Any] = field(default_factory=dict)
    claims_emitted: list[str] = field(default_factory=list)
    engine_choices: dict[str, Any] = field(default_factory=dict)
    llm_choices: dict[str, Any] = field(default_factory=dict)
    turn_created: int = 0
    status: str = "canonical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "attachment": dict(self.attachment),
            "title": self.title,
            "text": self.text,
            "summary": self.summary,
            "tags": list(self.tags),
            "source": self.source,
            "seed_packet": dict(self.seed_packet),
            "claims_emitted": list(self.claims_emitted),
            "engine_choices": dict(self.engine_choices),
            "llm_choices": dict(self.llm_choices),
            "turn_created": self.turn_created,
            "status": self.status,
        }

    def to_context_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "attachment": dict(self.attachment),
            "title": self.title,
            "summary": self.summary or self.text[:160],
            "tags": list(self.tags),
            "source": self.source,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonRecord":
        attachment = data.get("attachment")
        if not isinstance(attachment, dict):
            attachment = {"kind": "unknown", "id": str(data.get("attached_to") or "")}
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "object_detail"),
            attachment=dict(attachment),
            title=str(data["title"]) if data.get("title") is not None else None,
            text=str(data.get("text") or ""),
            summary=str(data["summary"]) if data.get("summary") is not None else None,
            tags=[str(tag) for tag in data.get("tags", []) if str(tag).strip()],
            source=str(data.get("source") or "grammar_fallback"),
            seed_packet=dict(data.get("seed_packet") or {}),
            claims_emitted=[
                str(claim)
                for claim in data.get("claims_emitted", [])
                if str(claim).strip()
            ],
            engine_choices=dict(data.get("engine_choices") or {}),
            llm_choices=dict(data.get("llm_choices") or data.get("menu_choices") or {}),
            turn_created=int(data.get("turn_created") or 0),
            status=str(data.get("status") or "canonical"),
        )


@dataclass
class ZoneSnapshot:
    """A cached, persisted record of a previously-visited frontier zone (sans player)."""

    tiles: list[list[str]]
    tile_tags: dict[str, list[str]]
    tile_durations: dict[str, int]
    entities: dict[str, Entity]
    explored: set[str]
    zone_type: str
    room_profiles: dict[str, RoomProfile] = field(default_factory=dict)
    tile_rooms: dict[str, str] = field(default_factory=dict)
