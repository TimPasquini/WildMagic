from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bonds import Bond


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
    "sight_shrouded",
    "warded",
    "strained",
    "drained",
    "jinxed",
    "crawling_skin",
    "silenced",
    "regenerating",
    "berserk",
    "empowered",
    "weakened",
    "cursed",
    "stasis",
    "delayed_sink",
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
    # Standing emanations this entity radiates each turn -- a hound whose shadow
    # burns nearby foes, a totem that steadies allies' nerve. Every aura is always
    # backed by a concrete mechanical effect (damage, or a status that buffs or
    # debuffs); resolved once per turn in GameEngine._tick_auras.
    auras: list[dict[str, Any]] = field(default_factory=list)
    # Narrative traits: descriptive facts with no fixed mechanical rule that the LLM
    # consumers weigh ("righteously hates goblins", "smells of the deep wild"). Latent
    # mechanics -- see wildmagic/semantics.py and docs/SEMANTIC_EFFECTS.md. Entity-attached
    # so they ride into any prompt this entity appears in for free.
    traits: list[str] = field(default_factory=list)
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
    # Equipment slot keys this creature has marked as their spell focus -- the implement(s)
    # the wild-magic resolver should weigh heavily when flavoring a cast. A spell focus is a
    # *mark on an already-equipped item*, not a separate slot, so a focus grants exactly its
    # own slot's stats and needs no special equip path. A list (not a single value) so the
    # design scales to multiple simultaneous foci; current play marks at most one. Per-entity,
    # so it follows body-swap with the rest of the loadout. See resolve_foci / focus_prompt_block.
    focus_slots: list[str] = field(default_factory=list)
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
        if self.auras:
            data["auras"] = self.auras
        if self.traits:
            data["traits"] = list(self.traits)
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
                    "focus_slots": list(self.focus_slots),
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
    semantic_prompt: str = ""
    mechanics: dict[str, Any] = field(default_factory=dict)
    tags: set[str] = field(default_factory=set)
    xp_to_clear: int = 3
    clear_progress: int = 0
    source_turn: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "stacks": self.stacks,
            "semantic_prompt": self.semantic_prompt,
            "mechanics": dict(self.mechanics),
            "tags": sorted(self.tags),
            "xp_to_clear": self.xp_to_clear,
            "clear_progress": self.clear_progress,
            "source_turn": self.source_turn,
        }


@dataclass
class NPCMemoryRecord:
    """Structured NPC memory.

    `claim` is intentionally neutral. Dialogue rendering adds "I saw..." or
    "someone told me..." framing from provenance instead of storing that framing here.
    """

    id: str
    claim: str
    provenance: str = "firsthand"
    bucket: str = "observation"
    subtype: str = ""
    subject: str = ""
    subject_refs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_npc_id: str | None = None
    source_name: str | None = None
    speaker_names: list[str] = field(default_factory=list)
    place_key: str = ""
    turn: int = 0
    confidence: float = 1.0
    salience: int = 1
    privacy: str = "social"
    shareable: bool = True
    spread_weight: float = 1.0
    hops: int = 0
    source_event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim": self.claim,
            "provenance": self.provenance,
            "bucket": self.bucket,
            "subtype": self.subtype,
            "subject": self.subject,
            "subject_refs": list(self.subject_refs),
            "tags": list(self.tags),
            "source_npc_id": self.source_npc_id,
            "source_name": self.source_name,
            "speaker_names": list(self.speaker_names),
            "place_key": self.place_key,
            "turn": self.turn,
            "confidence": self.confidence,
            "salience": self.salience,
            "privacy": self.privacy,
            "shareable": self.shareable,
            "spread_weight": self.spread_weight,
            "hops": self.hops,
            "source_event_id": self.source_event_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NPCMemoryRecord":
        return cls(
            id=str(data.get("id") or ""),
            claim=str(data.get("claim") or data.get("text") or ""),
            provenance=str(data.get("provenance") or "firsthand"),
            bucket=str(data.get("bucket") or "observation"),
            subtype=str(data.get("subtype") or ""),
            subject=str(data.get("subject") or ""),
            subject_refs=[str(ref) for ref in data.get("subject_refs") or []],
            tags=[str(tag) for tag in data.get("tags") or []],
            source_npc_id=data.get("source_npc_id"),
            source_name=data.get("source_name"),
            speaker_names=[str(name) for name in data.get("speaker_names") or []],
            place_key=str(data.get("place_key") or ""),
            turn=int(data.get("turn") or 0),
            confidence=float(data.get("confidence", 1.0)),
            salience=int(data.get("salience") or 1),
            privacy=str(data.get("privacy") or "social"),
            shareable=bool(data.get("shareable", True)),
            spread_weight=float(data.get("spread_weight", 1.0)),
            hops=int(data.get("hops") or 0),
            source_event_id=str(data.get("source_event_id") or ""),
        )


@dataclass
class GossipEdge:
    """Deterministic social connection used for daily memory spread."""

    id: str
    from_id: str
    to_id: str
    zone: tuple[int, int]
    relationship: str = "zone"
    trust: float = 0.65
    contact_chance: float = 0.45
    privacy_bias: float = 0.0
    created_turn: int = 0
    created_day: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "zone": list(self.zone),
            "relationship": self.relationship,
            "trust": self.trust,
            "contact_chance": self.contact_chance,
            "privacy_bias": self.privacy_bias,
            "created_turn": self.created_turn,
            "created_day": self.created_day,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GossipEdge":
        raw_zone = data.get("zone") or (0, 0)
        zone_values = list(raw_zone) if isinstance(raw_zone, (list, tuple)) else [0, 0]
        zone = (
            int(zone_values[0]) if len(zone_values) > 0 else 0,
            int(zone_values[1]) if len(zone_values) > 1 else 0,
        )
        return cls(
            id=str(data.get("id") or ""),
            from_id=str(data.get("from_id") or ""),
            to_id=str(data.get("to_id") or ""),
            zone=zone,
            relationship=str(data.get("relationship") or "zone"),
            trust=float(data.get("trust", 0.65)),
            contact_chance=float(data.get("contact_chance", 0.45)),
            privacy_bias=float(data.get("privacy_bias", 0.0)),
            created_turn=int(data.get("created_turn") or 0),
            created_day=int(data.get("created_day") or 0),
        )


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
    # Tiered world-knowledge per lore tag (region/tradition) — the access gate for lore
    # cards (docs/LORE_CARDS.md). Absent tag => level 0. Seeded deterministically at
    # generation from role+region (replay-safe). NOT surfaced in to_dialogue_context: it
    # gates which card TEXT is injected, it is never itself spoken.
    lore: dict[str, int] = field(default_factory=dict)
    memory: list[str] = field(default_factory=list)
    memory_records: list[NPCMemoryRecord] = field(default_factory=list)
    conversation: list[dict[str, str]] = field(default_factory=list)
    wares: dict[str, int] = field(default_factory=dict)
    wanted_item: str | None = None
    wanted_qty: int = 0
    reward_gold: int = 0
    reward_item: str | None = None
    reward_qty: int = 0
    quest_completed: bool = False
    # This NPC's personal relationship to the player (Phase F). Orthogonal to combat
    # faction and to org membership; evolves from the player's legend, this NPC's traits,
    # and their memories. See wildmagic/bonds.py.
    bond: Bond = field(default_factory=Bond)
    # A secret this NPC knows and may share when they have reason to — e.g. a freed captive,
    # in gratitude, telling you where a cache lies. Shape: {"item", "x", "y"}. None = no
    # secret. Kept off the dialogue context so it surfaces through the act, not idle chatter.
    lead: dict[str, Any] | None = None

    def bond_feeling(self) -> list[str]:
        """The bond rendered as plain words for prompts/readouts — the math stays
        invisible (strategy §5.3: surfaces as character, never approval bars)."""
        bond = self.bond
        words: list[str] = []
        if bond.loyalty >= 50:
            words.append("devoted to you")
        elif bond.loyalty >= 20:
            words.append("loyal to you")
        if bond.admiration >= 40:
            words.append("admires you")
        if bond.ideology >= 40:
            words.append("believes in your cause")
        if bond.fear >= 40:
            words.append("afraid of you")
        if bond.resentment >= 40:
            words.append("resents you")
        return words

    def _next_memory_id(self) -> str:
        return f"{self.entity_id}:memory:{len(self.memory_records) + 1}"

    @staticmethod
    def _confidence_label(confidence: float, provenance: str) -> str:
        if provenance == "overheard":
            return "hearsay"
        if provenance == "gossip":
            return "rumor" if confidence >= 0.35 else "thin rumor"
        if confidence >= 0.85:
            return "certain"
        if confidence >= 0.6:
            return "credible"
        if confidence >= 0.35:
            return "uncertain"
        return "thin rumor"

    @staticmethod
    def _render_memory_frame(record: NPCMemoryRecord) -> str:
        if record.provenance == "implanted":
            if record.bucket == "conversation":
                return "This feels like your own past conversation with the player."
            return "This feels like your own memory, though magic shaped it."
        if record.provenance == "firsthand":
            if record.bucket == "conversation":
                return "This is from your own past conversation with the player."
            return "You personally witnessed or experienced this."
        if record.provenance == "overheard":
            if record.source_name:
                return f"You overheard {record.source_name} say this."
            return "You overheard this nearby."
        if record.provenance == "gossip":
            if record.source_name:
                return f"{record.source_name} told you this."
            return "People are saying this."
        return "This is a memory note."

    @staticmethod
    def _legacy_memory_line(record: NPCMemoryRecord) -> str:
        if record.provenance == "implanted":
            return record.claim
        if record.provenance == "firsthand" and record.bucket == "observation":
            return (
                f"I saw {record.claim[0].lower() + record.claim[1:]}"
                if record.claim
                else ""
            )
        if record.provenance == "overheard":
            source = f" {record.source_name}" if record.source_name else ""
            return f"I overheard{source}: {record.claim}"
        if record.provenance == "gossip":
            source = (
                f"{record.source_name} says" if record.source_name else "People say"
            )
            return f"{source}: {record.claim}"
        return record.claim

    def add_memory(
        self,
        record: NPCMemoryRecord,
        *,
        limit: int = 12,
        mirror_legacy: bool = True,
    ) -> None:
        if not record.id:
            record.id = self._next_memory_id()
        record.claim = " ".join(record.claim.split())
        if not record.claim:
            return
        self.memory_records.append(record)
        self.memory_records = self.memory_records[-limit:]
        if mirror_legacy:
            legacy = self._legacy_memory_line(record)
            if legacy:
                self.memory.append(legacy)
                self.memory = self.memory[-limit:]

    def remember(
        self,
        text: str,
        limit: int = 12,
        *,
        provenance: str = "firsthand",
        bucket: str = "observation",
        subtype: str = "",
        subject: str = "",
        subject_refs: list[str] | None = None,
        tags: list[str] | None = None,
        source_npc_id: str | None = None,
        source_name: str | None = None,
        speaker_names: list[str] | None = None,
        place_key: str = "",
        turn: int = 0,
        confidence: float = 1.0,
        salience: int = 1,
        privacy: str = "social",
        shareable: bool = True,
        spread_weight: float = 1.0,
        hops: int = 0,
        source_event_id: str = "",
    ) -> None:
        text = " ".join(str(text).split())
        if not text:
            return
        record = NPCMemoryRecord(
            id=self._next_memory_id(),
            claim=text,
            provenance=provenance,
            bucket=bucket,
            subtype=subtype,
            subject=subject,
            subject_refs=list(subject_refs or []),
            tags=list(tags or []),
            source_npc_id=source_npc_id,
            source_name=source_name,
            speaker_names=list(speaker_names or []),
            place_key=place_key,
            turn=turn,
            confidence=confidence,
            salience=salience,
            privacy=privacy,
            shareable=shareable,
            spread_weight=spread_weight,
            hops=hops,
            source_event_id=source_event_id,
        )
        self.add_memory(record, limit=limit, mirror_legacy=False)
        self.memory.append(text)
        self.memory = self.memory[-limit:]

    def record_exchange(self, speaker: str, text: str, limit: int = 16) -> None:
        self.conversation.append({"speaker": speaker, "text": text})
        self.conversation = self.conversation[-limit:]

    def to_dialogue_context(self) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        overheard: list[dict[str, Any]] = []
        gossip: list[dict[str, Any]] = []
        conversation_summaries: list[dict[str, Any]] = []
        for record in sorted(
            self.memory_records, key=lambda rec: (rec.salience, rec.turn), reverse=True
        ):
            rendered = {
                "claim": record.claim,
                "frame": self._render_memory_frame(record),
                "confidence": self._confidence_label(
                    record.confidence, record.provenance
                ),
            }
            if record.source_name:
                rendered["source"] = record.source_name
            if record.subject:
                rendered["subject"] = record.subject
            if record.tags:
                rendered["tags"] = list(record.tags[:4])
            if record.bucket == "conversation":
                conversation_summaries.append(rendered)
            elif record.provenance == "overheard" or record.bucket == "overheard":
                overheard.append(rendered)
            elif record.provenance == "gossip" or record.bucket == "gossip":
                gossip.append(rendered)
            elif record.bucket == "observation":
                observations.append(rendered)

        context: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "backstory": self.backstory,
            "appearance": self.appearance,
            "traits": list(self.traits),
            "things_i_personally_witnessed": observations[:4],
            "things_i_overheard": overheard[:3],
            "gossip_i_have_heard": gossip[:3],
            "conversation_memory": {
                "recent_exchanges": list(self.conversation),
                "older_summaries": conversation_summaries[:4],
            },
        }
        # How I feel about you (Phase F) — so dialogue sounds like someone who admires,
        # fears, or resents you, not a blank slate. Surfaced as words, never numbers.
        feeling = self.bond_feeling()
        if feeling:
            context["how_i_feel_about_you"] = feeling
        if self.bond.affiliations:
            context["my_affiliations"] = list(self.bond.affiliations)
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

    def player_memory_multiplier(self, player_soul_id: str) -> float:
        """How strongly reputation should land based on what this NPC knows of the player."""
        best = 1.0
        for record in self.memory_records:
            if player_soul_id not in record.subject_refs:
                continue
            if record.provenance in {"firsthand", "implanted"}:
                best = max(best, 1.5)
            elif record.provenance == "overheard":
                best = max(best, 1.0 + 0.25 * max(0.0, min(record.confidence, 1.0)))
            elif record.provenance == "gossip":
                best = max(best, 1.0 + 0.1 * max(0.0, min(record.confidence, 1.0)))
        # Legacy saves/tests may have only plain strings. Preserve the old behavior until
        # all memory writers use structured records.
        if best == 1.0 and not self.memory_records and self.memory:
            return 1.5
        return best


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
    experience_gained: int = 0

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
            "experience_gained": self.experience_gained,
        }


@dataclass
class WildMagicOutcome:
    consumed_turn: bool
    technical_failure: bool
    messages: list[str]
    # Operation deltas (Stage 6): the mutations this cast applied, in order. Empty unless the
    # cast was accepted and applied. See wildmagic/operations.py.
    deltas: list[dict[str, Any]] = field(default_factory=list)


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
    tile_flows: dict[str, dict[str, Any]]
    entities: dict[str, Entity]
    explored: set[str]
    zone_type: str
    room_profiles: dict[str, RoomProfile] = field(default_factory=dict)
    tile_rooms: dict[str, str] = field(default_factory=dict)
