from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import math
import random
import re
from typing import Any
from collections.abc import Iterable

from .models import (
    BLOCKING_TILES,
    ZoneSnapshot,
    DOOR,
    FIRE,
    FLOOR,
    ICE_WALL,
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
    CharacterProfile,
    Entity,
    GameStats,
    GossipEdge,
    NPCMemoryRecord,
    NPCProfile,
    CanonRecord,
    RoomProfile,
    TILE_NAMES,
    TILE_TAGS,
)
from .semantics import (
    SemanticLedger,
    entity_anchor,
    faction_anchor,
    place_anchor,
    WORLD_ANCHOR,
)
from .determinism import stable_seed
from .bonds import (
    DRIFT_THRESHOLD,
    derive_disposition,
    disposition_inclination,
    drift_bond,
)
from .deeds import Deed, DeedLedger, interpret_deed_rules
from .factions import (
    EMPIRE_PATROLS_START,
    REBELLION_CELLS_START,
    Faction,
    FactionLedger,
    resolve_faction,
    seed_phase0_factions,
)
from .legend import LegendLedger
from .combat import _CombatMixin
from .ai import _AIMixin
from .generation import _GenerationMixin
from .effects import _EffectsMixin
from .items import _ItemsMixin
from . import state_view
from . import refs
from .behaviors import tick_behavior_modifiers
from .conditions import evaluate_condition
from .operations import StateDelta
from .props import get_prop_template
from .prop_gen import make_prop_provider, PropProvider, PropSpec, MECHANICAL_TAGS
from .regions import Region, get_region
from .lore_cards import seed_npc_lore
from .config import get_props_provider, ollama_host
from .llm_client import ollama_reachable
from .promises import (
    DIRECTION_NAMES,
    PROMISE_LEDGER_LIMIT,
    PROMISE_RESERVATION_LIMIT,
    PromiseReservation,
    Objective,
    QuestLogEntry,
    Reward,
    SpatialHint,
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
    FACTION_HOSTILITIES,
    TRAP_SPECS,
    scan_for_trade_intent,
)
from .geometry import bresenham_line
from .normalize import (
    clamp_int,
    status_duration,
    parse_tile_key,
    normalize_id,
    normalize_faction,
    normalize_trigger_name,
    infer_behavior_tags,
    coerce_list,
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
    "balance": ["weigh a debt", "tip a bargain", "measure a curse"],
    "broken": ["shatter outward", "scatter rubble", "turn failure into force"],
    "cold": ["freeze", "slow", "make slick ice"],
    "cloth": ["snare or muffle", "carry a charm", "catch fire"],
    "contract": ["bind by oath", "call in a debt", "mark a bargain"],
    "crystal": ["refract light", "shatter", "amplify magic"],
    "cursed": ["curse", "frighten", "mark a target"],
    "debt": ["call in a debt", "summon collection", "curse unpaid promises"],
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
    "ink": ["stain or reveal", "write a mark", "spread a slick"],
    "law": ["bind with statute", "summon hostile attention", "seal a path"],
    "light": ["reveal", "blind or mark", "project a beam"],
    "lightning": ["shock", "stun", "conduct through metal or water"],
    "liquid": ["flood", "splash", "turn to mist or ice"],
    "lore": ["reveal", "set a delayed omen", "name a curse"],
    "magic": ["amplify the spell", "summon", "create a ward"],
    "mechanical": ["trigger a mechanism", "spin time or force", "launch or pull"],
    "metal": ["conduct lightning", "magnetize", "make shrapnel"],
    "mirror": ["reveal", "reflect a spell", "confuse with doubles"],
    "music": ["charm or confuse", "push as sound", "summon echoes"],
    "paper": ["burn into sigils", "reveal writing", "scatter pages"],
    "plant": ["grow vines", "snare", "release spores or pollen"],
    "powder": ["make a circle", "blind with dust", "ignite a flash"],
    "prison": ["bind", "root", "lock a target in place"],
    "readable": ["reveal writing", "name a curse", "seed a rumor"],
    "ritual": ["summon", "curse", "ward"],
    "rope": ["bind", "pull", "snare"],
    "rumor": ["reveal secrets", "confuse or frighten", "carry a message"],
    "sharp": ["bleed", "make caltrops", "shred armor"],
    "silk": ["web", "snare", "muffle sound"],
    "smelly": ["poison", "frighten", "make a choking cloud"],
    "smoke": ["make mist", "blind or choke", "hide movement"],
    "snaring": ["root", "web", "slow movement"],
    "spice": ["make choking smoke", "enrage or charm", "mark by scent"],
    "stone": ["make rubble", "petrify", "raise a barrier"],
    "thread": ["bind", "stitch a ward", "pull fate taut"],
    "time": ["delay a consequence", "slow or hasten", "schedule an omen"],
    "toxic": ["poison", "make poison cloud", "sicken"],
    "trade": ["swap or summon goods", "bind a bargain", "price a cost"],
    "trap": ["trigger", "create ward", "delay an effect"],
    "vint": ["twist gossip", "weave a charm", "bind with civic rumor"],
    "volatile": ["backfire", "explode", "spill wild consequences"],
    "ward": ["protect", "repel", "store a trigger"],
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


# Phase E — the consequence renderer. When you return to a place where you did something
# the world remembers, it *shows*. One evocative mark per kind of deed per zone (not a pile
# of stains), placed deterministically. (name, glyph, tags, base description.)
_DEED_CONSEQUENCE_PROPS: dict[str, tuple[str, str, set[str], str]] = {
    "killed_imperials": (
        "bloodstained ground",
        ",",
        {"consequence", "blood"},
        "Dark stains and a hasty cairn mark where the Empire's dead were left.",
    ),
    "killed_civilians": (
        "makeshift memorial",
        ",",
        {"consequence"},
        "Wilted flowers and a scrap of cloth mark where the unarmed fell.",
    ),
    "razed_building": (
        "rubble and scorch",
        "%",
        {"consequence", "stone"},
        "Charred timbers and broken stone - something stood here before it came down.",
    ),
    "raised_dead": (
        "disturbed graves",
        ",",
        {"consequence"},
        "The earth is turned and broken, the way ground looks after the dead have walked.",
    ),
    "desecration": (
        "defiled ground",
        ",",
        {"consequence"},
        "Salt, ash, and broken icons - this place has been profaned, and the air flinches.",
    ),
    "cast_atrocity": (
        "blasted ground",
        "%",
        {"consequence"},
        "The ground is fused glassy and black where catastrophe was called down.",
    ),
    "freed_captive": (
        "broken shackles",
        ",",
        {"consequence"},
        "Snapped chains and an open cell - someone was freed here, and folk remember who.",
    ),
    "defended_townsfolk": (
        "chalked thanks",
        ",",
        {"consequence"},
        "A symbol chalked low on the wall - a quiet thanks from folk you stood for.",
    ),
}


# Time (EMERGENT_WORLD_IMPLEMENTATION.md §0.3 / §1.6).
#
# The canonical unit is the **tick**: 10 ticks make one **round** (a standard action,
# read as ~one minute), and a day is 1440 rounds = 14400 ticks. Modelling the tick as the
# floor leaves headroom for actions up to 10x faster than a standard move (1-tick actions).
#
# Until sub-round-cost actions exist, every action is exactly one round, so the wall clock
# is derived from `state.turn` (the action/round counter) — `TURNS_PER_DAY` rounds per day
# — which keeps every turn-advancing path in sync for free. When faster actions land, the
# clock moves onto a real tick accumulator advanced by each action's tick cost.
TICKS_PER_ROUND = 10
TURNS_PER_DAY = (
    1440  # rounds per day (a round ~= 1 minute); the clock counts rounds today
)
TICKS_PER_DAY = TICKS_PER_ROUND * TURNS_PER_DAY  # 14400 — the tick-floor framing
DAWN_HOUR = 5  # turn_of_day 0 == 05:00; the world Simulator runs its daily tick at dawn

# How fast the Empire's defenses bleed under pressure (D9, §0.5): each daily tick the
# Empire loses this many points of `defense` per point of the player's imperial_threat
# standing. The higher your threat, the faster the road to the emperor opens.
EMPIRE_PRESSURE_RATE = 1.0

# Backlash thresholds (Phase D, strategy §5.2): standing at which a faction will spend an
# action resource to act. The Empire cracks down on a threat; the people rise for an ally.
CRACKDOWN_THRESHOLD = 1.0  # empire imperial_threat
UPRISING_THRESHOLD = 1.0  # rebellion gratitude
MAX_PENDING_BACKLASH = 4  # the world can only have so much in motion at once


@dataclass
class GameState:
    width: int = MAP_WIDTH
    height: int = MAP_HEIGHT
    tiles: list[list[str]] = field(default_factory=list)
    visible: set[str] = field(default_factory=set)
    visible_entity_ids: set[str] = field(default_factory=set)
    explored: set[str] = field(default_factory=set)
    entities: dict[str, Entity] = field(default_factory=dict)
    player_id: str = "player"
    turn: int = 0
    messages: list[str] = field(default_factory=list)
    message_count: int = 0
    npc_profiles: dict[str, NPCProfile] = field(default_factory=dict)
    gossip_edges: dict[str, GossipEdge] = field(default_factory=dict)
    pending_trade: dict[str, Any] | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    last_talked_npc_name: str | None = None
    # Explicit player-chosen spell target (a clicked square or its occupant). The
    # word "target"/"there"/"that square" in a wild spell resolves here; the resolver
    # context advertises it. tile coords persist (so "teleport to target" works);
    # target_entity_id snapshots whoever stood there so a moving creature stays "the
    # target". Cleared on zone change (coords become meaningless). See set_target.
    target_x: int | None = None
    target_y: int | None = None
    target_entity_id: str | None = None
    tile_tags: dict[str, list[str]] = field(default_factory=dict)
    tile_durations: dict[str, int] = field(default_factory=dict)
    tile_flows: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Standing auras anchored to ground rather than to a creature, keyed by "x,y"
    # -- a hexed circle that bleeds anyone standing on it, a warded floor that
    # steadies allies. Resolved alongside entity-borne auras in _tick_auras.
    tile_auras: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # The shared semantic substrate: world notes/traits keyed by anchor, read and
    # written by every LLM consumer. See wildmagic/semantics.py.
    semantics: SemanticLedger = field(default_factory=SemanticLedger)
    # The emergent-world ledgers (EMERGENT_WORLD_IMPLEMENTATION.md §1). The DeedLedger
    # is the append-only record of what the player's soul has done; the FactionLedger
    # holds standing/resources for the world's powers. Both are serialized inside a run
    # (summarize_state + deterministic replay) but never carried between runs.
    deed_ledger: DeedLedger = field(default_factory=DeedLedger)
    faction_ledger: FactionLedger = field(default_factory=seed_phase0_factions)
    # The mechanical legend: bounded-vocab weighted tags per actor soul, distilled from
    # deeds and read by the simulator/dialogue/scores (legend.py). The prose mirror lives
    # in the semantic ledger (§1.3).
    legend_ledger: LegendLedger = field(default_factory=LegendLedger)
    # The world Simulator's idempotency cursor (§1.8): the last turn whose deeds it has
    # consumed. The daily tick applies each deed exactly once; reloads/replays/repeat
    # ticks never double-apply.
    simulated_through_turn: int = 0
    # The daily-cadence cursor: the last day number whose 05:00 tick has fired. The run
    # opens on day 1 at dawn (nothing to simulate yet), so this starts at 1; the first
    # automatic tick is the start of day 2.
    ticked_through_day: int = 1
    # Backlash events the factions have decided on but not yet realized in the world
    # (Phase D). Minted by the daily tick when standing crosses a threshold and the faction
    # can spend; realized (spawned) when the player next enters a zone.
    pending_backlash: list[dict[str, Any]] = field(default_factory=list)
    # Days whose social graph spread has already run. Keeps repeated manual/day replay ticks
    # from pushing gossip another hop in the same in-world day.
    gossip_spread_days: set[int] = field(default_factory=set)
    # A stable handle for the player's *soul*, independent of the body being worn.
    # Body-swap reassigns player_id (the controlled body) but leaves this untouched, so
    # deeds and legend bind to the actor across possessions (§1.7).
    player_soul_id: str = "player"
    event_timers: list[dict[str, Any]] = field(default_factory=list)
    triggers: list[dict[str, Any]] = field(default_factory=list)
    player_steps: int = 0
    last_spell_text: str = ""
    same_spell_streak: int = 0
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
    experience: int = 0
    zone_x: int = 0
    zone_y: int = 0
    zone_type: str = "frontier"
    zones: dict[tuple[int, int], ZoneSnapshot] = field(default_factory=dict)
    dungeon_floors: dict[int, ZoneSnapshot] = field(default_factory=dict)
    room_profiles: dict[str, RoomProfile] = field(default_factory=dict)
    tile_rooms: dict[str, str] = field(default_factory=dict)
    canon_records: dict[str, CanonRecord] = field(default_factory=dict)
    # Per-item-type flavor that must outlive the Entity it was discovered on. The
    # inventory is fungible-by-name (item key -> count, no per-instance storage), so an
    # item's Entity.description and any Investigate-authored description are lost the moment
    # it is picked up. This store keeps that flavor keyed by the normalized inventory key
    # (item_type or name), so it survives pickup and rides into the resolver when the item is
    # marked as a spell focus. Deterministic: rebuilt by the same pickup/investigate commands
    # on replay (investigate writes flow through canon side effects). See set_item_lore.
    item_lore: dict[str, dict[str, Any]] = field(default_factory=dict)
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
        if self.depth == 1:
            hub_labels = {
                "bazaar": "the Saltmarket",
                "warren": "the Warren",
                "archive": "the Foxed Stacks",
            }
            if self.scenario in hub_labels:
                return hub_labels[self.scenario]
        return f"Depth {self.depth}"

    # --- The day/night clock (§0.3). Derived from `turn` so it never desyncs. --------
    @property
    def day(self) -> int:
        return self.turn // TURNS_PER_DAY + 1

    @property
    def turn_of_day(self) -> int:
        return self.turn % TURNS_PER_DAY

    @property
    def hour_of_day(self) -> float:
        """The wall-clock hour (0..24), with turn_of_day 0 mapped to 05:00 (dawn)."""
        return (self.turn_of_day / TURNS_PER_DAY * 24 + DAWN_HOUR) % 24

    @property
    def day_phase(self) -> str:
        hour = self.hour_of_day
        if 5 <= hour < 8:
            return "dawn"
        if 8 <= hour < 18:
            return "day"
        if 18 <= hour < 21:
            return "dusk"
        return "night"

    def clock_label(self) -> str:
        hour = int(self.hour_of_day)
        minute = int((self.hour_of_day - hour) * 60)
        return f"Day {self.day} {hour:02d}:{minute:02d} ({self.day_phase})"

    def current_place_key(self) -> str:
        """A location key finer than the overworld zone: distinguishes dungeon levels /
        the surface above them so deed consequences don't blur across depth."""
        return f"{self.zone_x},{self.zone_y}@{self.depth}"

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
        prop_provider: PropProvider | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.state = GameState(rng_seed=seed, scenario=scenario)
        # A profile from character creation, stamped onto the starting player by
        # _make_player. Set before generation runs. None → a random default profile.
        self.state.character = character
        self._next_entity_number = 1
        self._conducting_lightning = False
        self._npc_perception_message_count = 0
        # Operation deltas (Stage 6): a transient per-cast log of the mutations a wild-magic
        # resolution applied. Lives on the engine (not GameState), so it is untouched by the
        # snapshot/rollback and is simply cleared when a cast rolls back. See operations.py.
        self._delta_capture = False
        self._delta_log: list[StateDelta] = []
        # Per-cast sticky bindings for volatile single-target selectors. If one effect
        # changes the nearest enemy's faction, later effects in the same resolution should
        # still refer to the creature the resolver meant.
        self._cast_ref_cache: dict[str, str] = {}
        # Town generation: background executor + pending futures (not in GameState — not serializable)
        self._pending_towns: dict[tuple[int, int], concurrent.futures.Future[Any]] = {}
        self._pending_town_contexts: dict[tuple[int, int], dict] = {}
        self._pending_town_start_times: dict[tuple[int, int], float] = {}
        self._town_executor: concurrent.futures.ThreadPoolExecutor | None = None
        from .town_gen import make_town_provider

        self.town_provider = make_town_provider(provider_name)
        self._setup_prop_generation(provider_name, prop_provider)
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
        elif scenario == "bazaar":
            self._generate_bazaar_start()
        elif scenario == "warren":
            self._generate_warren_start()
        elif scenario == "archive":
            self._generate_archive_start()
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
        auras: list[dict[str, Any]] | None = None,
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
        if auras:
            entity.auras = [dict(aura) for aura in auras]
        self.state.entities[entity.id] = entity
        if self._delta_capture:
            self.record_delta(
                StateDelta(
                    op="create_entity",
                    target=entity.id,
                    summary=f"{entity.name} ({faction}) appeared at {x},{y}",
                    details={
                        "kind": "actor",
                        "name": name,
                        "faction": faction,
                        "x": x,
                        "y": y,
                    },
                )
            )
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

        # Derive a disposition (rebel/loyalist/pious/downtrodden) from role/traits/tags so the
        # bond system differentiates: the same legend makes a rebel adore you and a loyalist
        # fear you (content workstream B). Appended to the flavor traits; None for the truly
        # uncommitted (they drift at base rate).
        profile_traits = list(traits or [])
        disposition = derive_disposition(role, profile_traits, npc_tags)
        if disposition is not None and disposition not in profile_traits:
            profile_traits.append(disposition)
        self.state.npc_profiles[entity.id] = NPCProfile(
            entity_id=entity.id,
            name=name,
            role=role,
            backstory=backstory,
            appearance=appearance,
            traits=profile_traits,
            lore=seed_npc_lore(
                role,
                profile_traits,
                npc_tags,
                getattr(getattr(self, "region", None), "name", "") or "",
            ),
            wares=npc_wares,
            wanted_item=wanted_item,
            wanted_qty=wanted_qty,
            reward_gold=reward_gold,
            reward_item=reward_item,
            reward_qty=reward_qty,
        )
        self.seed_gossip_edges_for_current_zone()
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

    # --- Emergent world: deeds, the daily tick, and legibility --------------------
    # The Phase-0 micro-loop (EMERGENT_WORLD_IMPLEMENTATION.md §3): a witnessed kill is
    # recorded as a Deed; the daily tick applies its proposed standing shifts exactly
    # once; the world then *shows* it (a rumor on entry, an NPC's memory, a wanted
    # poster, the standing readout). Deterministic and replay-safe — no LLM here.

    #: How close a creature must be (Chebyshev tiles) to witness a deed. A Phase-0
    #: stand-in for FOV + NPC perception, which Phase A.1 substitutes.
    WITNESS_RADIUS = 8
    #: How close a civilian must be to a slain imperial to read the kill as *defending*
    #: them (the imperial was looming over them, not a distant onlooker). Deliberately tight.
    DEFEND_RADIUS = 2

    def _endangered_civilian_near(self, x: int, y: int) -> Entity | None:
        """The nearest living non-combatant townsperson close to (x, y) — used to read a kill
        of an imperial standing over the common folk as *defending* them. A civilian is a
        talkable NPC who isn't part of the Empire and isn't already your sworn follower
        (rescuing your own retinue isn't a fresh act of protection). Tight radius: the
        imperial had to be right on top of them, not merely somewhere in sight."""
        best: Entity | None = None
        best_dist = self.DEFEND_RADIUS + 1
        for entity in self.state.entities.values():
            if entity.kind != "npc" or not entity.alive:
                continue
            if "empire" in entity.tags:
                continue
            profile = self.state.npc_profiles.get(entity.id)
            if profile is not None and "follower" in profile.traits:
                continue
            dist = max(abs(entity.x - x), abs(entity.y - y))
            if dist <= self.DEFEND_RADIUS and dist < best_dist:
                best, best_dist = entity, dist
        return best

    def _deed_witnesses(self, x: int, y: int, exclude: set[str]) -> list[Entity]:
        """Living NPCs/actors near enough to have seen something happen at (x, y)."""
        witnesses: list[Entity] = []
        for entity in self.state.entities.values():
            if entity.id in exclude or entity.kind not in {"npc", "actor"}:
                continue
            if not entity.alive:
                continue
            if max(abs(entity.x - x), abs(entity.y - y)) <= self.WITNESS_RADIUS:
                witnesses.append(entity)
        return sorted(witnesses, key=lambda e: e.id)

    def legend_words(self, actor: str, n: int = 3) -> list[str]:
        """A soul's strongest legend tags as plain words — the shorthand dialogue,
        rumors, and readouts use (e.g. ['defiant', 'uncanny'])."""
        return [tag for tag, _weight in self.state.legend_ledger.top_tags(actor, n)]

    def _gossip_zone(self) -> tuple[int, int]:
        return (self.state.zone_x, self.state.zone_y)

    def _gossip_edge_id(self, from_id: str, to_id: str) -> str:
        zx, zy = self._gossip_zone()
        return f"zone:{zx},{zy}:{from_id}->{to_id}"

    def seed_gossip_edges_for_current_zone(self) -> int:
        """Create placeholder same-zone directed gossip edges for realized NPCs."""
        state = self.state
        zone = self._gossip_zone()
        npc_ids = sorted(
            entity.id
            for entity in state.entities.values()
            if entity.kind == "npc" and entity.alive and entity.id in state.npc_profiles
        )
        created = 0
        for from_id in npc_ids:
            for to_id in npc_ids:
                if from_id == to_id:
                    continue
                edge_id = self._gossip_edge_id(from_id, to_id)
                if edge_id in state.gossip_edges:
                    continue
                state.gossip_edges[edge_id] = GossipEdge(
                    id=edge_id,
                    from_id=from_id,
                    to_id=to_id,
                    zone=zone,
                    relationship="zone",
                    trust=0.65,
                    contact_chance=0.45,
                    privacy_bias=0.0,
                    created_turn=state.turn,
                    created_day=state.day,
                )
                created += 1
        return created

    def _gossip_contact_occurs(self, edge: GossipEdge, day: int) -> bool:
        roll = stable_seed(self.state.rng_seed, "gossip_contact", day, edge.id) % 10000
        return roll < int(max(0.0, min(edge.contact_chance, 1.0)) * 10000)

    def _gossip_memory_key(self, record: NPCMemoryRecord) -> str:
        return record.source_event_id or record.id or record.claim

    def _memory_can_spread(self, record: NPCMemoryRecord, edge: GossipEdge) -> bool:
        if not record.shareable:
            return False
        if record.hops >= 2:
            return False
        if record.privacy == "secret":
            return False
        if (
            record.privacy == "intimate"
            and edge.privacy_bias < 0.5
            and edge.trust < 0.8
        ):
            return False
        if record.bucket == "conversation":
            return (
                record.subtype in {"conversation_summary", "conversation_gossip"}
                and record.salience >= 2
            )
        return record.bucket in {"observation", "overheard", "gossip"}

    def _spreadable_memories(
        self, source_profile: NPCProfile, edge: GossipEdge
    ) -> list[NPCMemoryRecord]:
        candidates = [
            record
            for record in source_profile.memory_records
            if self._memory_can_spread(record, edge)
        ]
        return sorted(
            candidates,
            key=lambda record: (
                record.salience,
                record.spread_weight,
                record.confidence,
                record.turn,
                record.id,
            ),
            reverse=True,
        )

    def _receiver_knows_memory(
        self, receiver_profile: NPCProfile, record: NPCMemoryRecord
    ) -> bool:
        source_key = self._gossip_memory_key(record)
        return any(
            self._gossip_memory_key(known) == source_key
            for known in receiver_profile.memory_records
        )

    def spread_daily_gossip(self, day: int | None = None) -> int:
        """Deterministically copy shareable memories across same-zone gossip edges."""
        state = self.state
        day = state.day if day is None else day
        if day in state.gossip_spread_days:
            return 0
        state.gossip_spread_days.add(day)
        self.seed_gossip_edges_for_current_zone()
        zone = self._gossip_zone()
        spread_count = 0
        for edge in sorted(state.gossip_edges.values(), key=lambda edge: edge.id):
            if edge.zone != zone or edge.created_day > day:
                continue
            if not self._gossip_contact_occurs(edge, day):
                continue
            source_entity = state.entities.get(edge.from_id)
            receiver_entity = state.entities.get(edge.to_id)
            if (
                source_entity is None
                or receiver_entity is None
                or source_entity.kind != "npc"
                or receiver_entity.kind != "npc"
                or not source_entity.alive
                or not receiver_entity.alive
            ):
                continue
            source_profile = state.npc_profiles.get(edge.from_id)
            receiver_profile = state.npc_profiles.get(edge.to_id)
            if source_profile is None or receiver_profile is None:
                continue
            for record in self._spreadable_memories(source_profile, edge):
                if self._receiver_knows_memory(receiver_profile, record):
                    continue
                confidence = round(
                    max(0.05, min(record.confidence * edge.trust * 0.75, 1.0)),
                    3,
                )
                tags = list(dict.fromkeys([*record.tags, "gossip"]))
                if record.provenance == "implanted":
                    tags = list(dict.fromkeys([*tags, "implanted_origin"]))
                receiver_profile.add_memory(
                    NPCMemoryRecord(
                        id="",
                        claim=record.claim,
                        provenance="gossip",
                        bucket="gossip",
                        subtype="shared_memory",
                        subject=record.subject,
                        subject_refs=list(record.subject_refs),
                        tags=tags,
                        source_npc_id=edge.from_id,
                        source_name=source_profile.name,
                        speaker_names=list(record.speaker_names),
                        place_key=record.place_key,
                        turn=state.turn,
                        confidence=confidence,
                        salience=max(1, record.salience - 1),
                        privacy="social"
                        if record.privacy in {"public", "social"}
                        else record.privacy,
                        shareable=record.hops + 1 < 2,
                        spread_weight=max(0.1, record.spread_weight * 0.5),
                        hops=record.hops + 1,
                        source_event_id=self._gossip_memory_key(record),
                    )
                )
                spread_count += 1
                break
        return spread_count

    def record_deed(
        self,
        deed_type: str,
        *,
        magnitude: float,
        summary: str,
        at: tuple[int, int] | None = None,
        source: str = "combat",
        target_tags: list[str] | None = None,
        victim_faction: str = "",
        evidence_tags: list[str] | None = None,
        interpretation_source: str = "rules",
    ) -> Deed | None:
        """The general deed-emission path (Phase A): an emission site describes *what
        happened* (type, magnitude, where, what it touched); the rules interpreter
        (`interpret_deed_rules`) decides *what it means* (multi-axis standing + legend),
        and the tick applies it once. Witnesses are detected at the deed moment and
        remember it immediately (legibility). Returns the recorded deed.

        ``interpretation_source`` records *who judged* the deed: "rules" for the
        deterministic table (combat), or "llm"/"fallback" when the A.2 interpreter
        classified an ambiguous spell outcome — the consequences still come from the
        bounded rules table either way (keeps the world coherent and replay-safe).

        Deeds are always attributed to the player's **soul** (§1.7). ``at`` defaults to
        the controlled body's tile (where most deeds happen)."""
        state = self.state
        x, y = at if at is not None else (state.player.x, state.player.y)
        witnesses = self._deed_witnesses(x, y, exclude={state.player_id})
        deed = Deed(
            id=state.deed_ledger.next_id(state.turn),
            turn=state.turn,
            zone=(state.zone_x, state.zone_y),
            type=deed_type,
            magnitude=magnitude,
            actor=state.player_soul_id,
            source=source,
            place_key=state.current_place_key(),
            target_tags=list(target_tags or []),
            victim_faction=victim_faction,
            visibility="witnessed" if witnesses else "secret",
            witnesses=[w.id for w in witnesses],
            evidence_tags=list(evidence_tags or []),
            summary=summary,
        )
        interpret_deed_rules(
            deed
        )  # consequences from the bounded rules table (by role)
        deed.standing_deltas = self._resolve_role_deltas(deed.standing_deltas)
        deed.interpretation_source = interpretation_source  # who *judged* it (D5)
        state.deed_ledger.record(deed)
        # NPC memory line (legibility): witnesses carry it even before the tick, so it
        # surfaces naturally in their dialogue context.
        for witness in witnesses:
            profile = state.npc_profiles.get(witness.id)
            if profile is not None:
                salience = max(1, min(5, int(round(deed.magnitude * 10))))
                profile.add_memory(
                    NPCMemoryRecord(
                        id=f"{witness.id}:deed:{deed.id}",
                        claim=f"The player {deed.summary}.",
                        provenance="firsthand",
                        bucket="observation",
                        subtype="witnessed_deed",
                        subject="the player",
                        subject_refs=[state.player_soul_id],
                        tags=["deed", deed.type, *deed.target_tags],
                        place_key=deed.place_key,
                        turn=state.turn,
                        confidence=1.0,
                        salience=salience,
                        privacy="social",
                        shareable=deed.visibility != "secret",
                        spread_weight=1.0 + deed.magnitude,
                        source_event_id=deed.id,
                    )
                )
        return deed

    def _resolve_role_deltas(
        self, role_deltas: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        """Turn role-keyed consequence deltas (from DEED_RULES) into concrete per-faction
        deltas, applying each role's shift to every faction that fills it. On the two-pole
        scaffold this is 1:1; once Phase C seeds the full roster, the whole imperial bloc
        feels a strike at the same deed with no rule changes. Unknown roles fall back to a
        literal id (keeps ad-hoc/test deltas working)."""
        resolved: dict[str, dict[str, float]] = {}
        ledger = self.state.faction_ledger
        for role, axes in role_deltas.items():
            targets = ledger.ids_by_role(role) or [role]
            for faction_id in targets:
                dest = resolved.setdefault(faction_id, {})
                for axis, delta in axes.items():
                    dest[axis] = round(dest.get(axis, 0.0) + delta, 4)
        return resolved

    def _deed_attributed_to_player(self, source: Entity | None) -> bool:
        """Whether a kill counts as the player soul's deed. Direct kills always do; the
        ``owner_soul_id`` seam lets *indirect* kills (a summon, a triggered ward, a charmed
        agent) count too once those carriers stamp the player's soul as owner. NOTE: that
        owner-stamping isn't threaded through summons/triggers/timers yet — tracked as a
        follow-up; today this captures direct kills and any future owned-carrier."""
        if source is None:
            return False
        if source.id == self.state.player_id:
            return True
        return getattr(source, "owner_soul_id", None) == self.state.player_soul_id

    def _record_kill_deed(self, victim: Entity, source: Entity | None) -> None:
        """Translate a combat kill the player's soul is responsible for into a deed.
        Imperial dead and slain civilians read very differently (the rules table sorts
        out the consequences); other creatures aren't (yet) deed-worthy."""
        if not self._deed_attributed_to_player(source):
            return
        # Whose member died — stamped on the kill deed so it feeds the per-faction kill
        # tally (FACTION_KILL_REPUTATION.md K1/K2). Reactions stay role-based for now; the
        # tally is pure data capture. "defended_townsfolk" is not a kill, so it carries none.
        victim_faction = resolve_faction(
            victim.tags, victim.kind, self.state.faction_ledger
        )
        if "empire" in victim.tags:
            self.record_deed(
                "killed_imperials",
                magnitude=0.2,  # one imperial; Phase A may scale by count/severity
                summary=f"cut down {victim.name}, one of the Empire's own",
                at=(victim.x, victim.y),
                target_tags=["empire"],
                victim_faction=victim_faction,
                evidence_tags=["bloodstain"],
            )
            # Cutting down an imperial who stood over the common folk *also* reads as
            # defending them (one act, two deeds — the rules table sorts the consequences).
            # This is how the "people's champion" / protector legend arises in play.
            bystander = self._endangered_civilian_near(victim.x, victim.y)
            if bystander is not None:
                self.record_deed(
                    "defended_townsfolk",
                    magnitude=0.2,
                    summary=f"stood between the Empire and {bystander.name}",
                    at=(victim.x, victim.y),
                    target_tags=["civilian"],
                    evidence_tags=["survivor_testimony"],
                )
        elif (
            victim.kind == "npc" or "civilian" in victim.tags
        ) and not self._was_hostile_to_player(victim):
            self.record_deed(
                "killed_civilians",
                magnitude=0.2,
                summary=f"struck down {victim.name}, who bore no arms",
                at=(victim.x, victim.y),
                target_tags=["civilian"],
                victim_faction=victim_faction,
                evidence_tags=["bloodstain", "survivor_testimony"],
            )

    def _was_hostile_to_player(self, victim: Entity) -> bool:
        """Whether `victim` stood against the player by its own disposition — it was already
        an enemy (or in a declared faction war the player belongs to) *before* the player
        struck it. Striking a neutral never flips its faction (neutrals flee; they do not
        turn ``enemy`` — see ``ai._npc_turns``), so the victim's faction at death still
        reflects its stance before the player's aggression: a townsperson cut down unprovoked
        is still ``neutral`` and reads as a civilian, while a bandit who came at the player
        does not. Keeps ``killed_civilians`` from branding the player a butcher for killing
        someone who attacked them first."""
        player = self.state.player
        return player is not None and self.is_hostile_to(victim, player)

    def kills_by_faction(self) -> dict[str, int]:
        """Per-faction kill tally (`FACTION_KILL_REPUTATION.md` K2) — how many of each faction
        the player has killed. A pure projection over the deed ledger (never decays; the
        `civilian` bucket included, unaligned creatures excluded)."""
        return self.state.deed_ledger.kills_by_faction()

    def run_world_tick(self, day: int | None = None) -> bool:
        """The world Simulator's daily beat. Applies every unapplied deed's proposed
        consequences **exactly once** (idempotency, §1.8): repeated ticks, reloads, or a
        replay boundary never double-count. Phase 0 only moves standing; Phase D fills
        this with backlash, off-screen assignments, and region re-skins (seeded by
        ``day`` via ``stable_seed`` — the param is threaded now for that determinism).

        Returns True if any deed was applied this tick."""
        state = self.state
        applied_any = False
        for deed in state.deed_ledger.unapplied():
            for faction_id, axes in deed.standing_deltas.items():
                for axis, delta in axes.items():
                    state.faction_ledger.adjust_standing(faction_id, axis, delta)
            # The legend (mechanical tags) grows from the same deeds; a prose mirror is
            # written for the prompts to read (§1.3).
            for tag, weight in deed.legend_tags.items():
                state.legend_ledger.add_tag(deed.actor, tag, weight)
            if deed.legend_tags and deed.summary:
                self.record_note(
                    WORLD_ANCHOR,
                    f"It is said of you that you {deed.summary}.",
                    kind="legend",
                    source="legend",
                    salience=2,
                    ttl=600,
                )
            deed.applied = True
            applied_any = True
        # Causal compression keeps the chronicle/voices reading a few arcs, not raw deeds.
        state.deed_ledger.compress()
        if self.spread_daily_gossip(day=day):
            applied_any = True
        state.simulated_through_turn = max(state.simulated_through_turn, state.turn)
        return applied_any

    def _maybe_run_daily_tick(self) -> bool:
        """Fire the 05:00 daily tick for each day boundary crossed since it last ran
        (D4: at 05:00, not on zone-cross). Stateful via ``ticked_through_day``, so it
        catches up even if a turn advance bypassed this hook, and never fires twice for
        the same day."""
        fired = False
        while self.state.day > self.state.ticked_through_day:
            self.state.ticked_through_day += 1
            self.run_world_tick(day=self.state.ticked_through_day)
            # Pressure on the Empire is a once-per-day event (cursor-guarded here, not in
            # run_world_tick, so an ad-hoc reckoning can't double-spend its defenses).
            self._simulate_empire_pressure()
            self._simulate_backlash()
            self._simulate_bonds()
            fired = True
        return fired

    def _simulate_bonds(self) -> None:
        """Phase F — every NPC's personal bond to the player drifts with the player's
        legend, bent by the NPC's own traits (a rebel comes to adore a liberator; a
        loyalist a defiant soul to fear). Crossing the follow line is a *moment*; turning
        butcher loses the very people who believed in you. First-hand memory makes
        reputation land harder. Followers who believe also rally to the player's org."""
        state = self.state
        legend = state.legend_ledger.tags_for(state.player_soul_id)
        if not legend:
            return
        player_orgs = state.faction_ledger.by_kind("player_org")
        primary_org = player_orgs[0] if player_orgs else None
        for npc_id, profile in state.npc_profiles.items():
            # The "follower" trait is the persistent marker (set on join, cleared on
            # depart), so a gradual drift down through the band still fires exactly one
            # estrangement moment when loyalty finally falls past the drift line.
            pledged = "follower" in profile.traits
            personal = profile.player_memory_multiplier(state.player_soul_id)
            drift_bond(profile.bond, legend, profile.traits, personal=personal)
            if profile.bond.is_follower() and not pledged:
                self._fire_bond_moment(profile, "join")
            elif pledged and profile.bond.loyalty < DRIFT_THRESHOLD:
                self._fire_bond_moment(profile, "depart")
            # True believers rally to your cause's banner if you've raised one.
            if (
                profile.bond.is_follower()
                and primary_org is not None
                and profile.bond.ideology >= 50
                and primary_org.id not in profile.bond.affiliations
            ):
                profile.bond.affiliations.append(primary_org.id)
                profile.remember(f"I joined {primary_org.name}, your cause made real.")
                self.state.add_message(f"{profile.name} pledges to {primary_org.name}.")

    def _fire_bond_moment(self, profile: Any, kind: str) -> None:
        """A bond crossing a threshold becomes a felt moment, written back to memory so it
        colours all future behaviour (a parted follower remembers leaving — and why)."""
        if kind == "join":
            self.state.add_message(
                f"{profile.name} has come to follow you, won by what you've done."
            )
            profile.remember("I chose to follow you.")
            if "follower" not in profile.traits:
                profile.traits.append("follower")
        elif kind == "depart":
            self.state.add_message(
                f"{profile.name} can no longer walk the road you walk, and falls away."
            )
            profile.remember("I left your side; I could not follow what you became.")
            if "follower" in profile.traits:
                profile.traits.remove("follower")

    def _adjacent_bound_captive(self) -> Entity | None:
        """A bound captive on a tile adjacent (8-dir) to the player — someone to free.
        Deterministic pick (lowest id) when more than one is in reach."""
        player = self.state.player
        best: Entity | None = None
        for entity in self.state.entities.values():
            if entity.kind != "npc" or not entity.alive or "bound" not in entity.tags:
                continue
            if max(abs(entity.x - player.x), abs(entity.y - player.y)) <= 1:
                if best is None or entity.id < best.id:
                    best = entity
        return best

    def free_captive(self) -> bool:
        """Free a bound captive on an adjacent tile (content workstream A). A *general* act,
        not a scripted event: it records a `freed_captive` deed (→ liberator legend +
        resistance gratitude), turns the freed prisoner to your side, and seeds a grateful
        bond. Whether that gratitude deepens into *following* you is left to the captive's
        nature (disposition) and the legend you go on to earn — the daily bond tick decides,
        so some join and some simply thank you and go. A captive who happens to know where
        something lies may, in thanks, tell you (a real cache, seeded sparingly)."""
        if self.state.game_over:
            return False
        captive = self._adjacent_bound_captive()
        if captive is None:
            self.state.add_message("There is no one bound here to free.")
            return False
        captive.tags.discard("bound")
        captive.tags.add("freed")
        captive.faction = "ally"  # freed, and grateful enough to take your side
        self.record_deed(
            "freed_captive",
            magnitude=0.4,
            summary=f"struck the chains from {captive.name}",
            at=(captive.x, captive.y),
            target_tags=["civilian", "captive"],
            evidence_tags=["survivor_testimony"],
        )
        self.state.add_message(
            f"You break {captive.name} free. They stagger up, blinking at the light."
        )
        profile = self.state.npc_profiles.get(captive.id)
        if profile is not None:
            # Gratitude seeds the bond, scaled by the captive's *nature*: one whose
            # disposition inclines to your cause tips from thanks into following; a wary or
            # loyal-hearted one merely thanks you and keeps their distance. So *who* joins
            # emerges from disposition, not a hard-coded flag (§5.3).
            bond = profile.bond
            lean = disposition_inclination(profile.traits)
            loyalty_seed = {"affinity": 55.0, "neutral": 28.0, "aversion": 6.0}[lean]
            ideology_seed = {"affinity": 50.0, "neutral": 12.0, "aversion": 4.0}[lean]
            bond.loyalty = min(100.0, bond.loyalty + loyalty_seed)
            bond.admiration = min(100.0, bond.admiration + loyalty_seed * 0.6)
            bond.ideology = min(100.0, bond.ideology + ideology_seed)
            profile.remember("You freed me from the Empire's cage. I won't forget it.")
            self._reveal_captive_lead(captive, profile)
        self.finish_player_turn()
        return True

    def _reveal_captive_lead(self, captive: Entity, profile: Any) -> None:
        """A freed captive who knows where a cache lies shares it in gratitude — a real item
        already placed in the world, pointed to by a rough direction in the journal. Organic
        and optional: most captives carry no such secret (leads are seeded sparingly at
        generation, so it lands sometimes and whiffs others)."""
        lead = profile.lead
        if not lead:
            return
        item = str(lead.get("item") or "something of worth")
        x, y = int(lead.get("x", captive.x)), int(lead.get("y", captive.y))
        player = self.state.player
        direction = ((x > player.x) - (x < player.x), (y > player.y) - (y < player.y))
        compass = DIRECTION_NAMES.get(direction, "near")
        where = "close by" if direction == (0, 0) else f"to the {compass}"
        self.state.add_message(
            f'{captive.name} leans close: "I owe you my life. Hear this - there is '
            f'{item} hidden {where}. Go - it is yours."'
        )
        self.state.promises.append(
            WorldPromise(
                id=f"lead_{captive.id}_{normalize_id(item)}",
                kind="rumor",
                subject=item,
                text=(
                    f"{captive.name}, freed from the cells, swears {item} lies hidden "
                    f"{where} of where they were held."
                ),
                tags=["lead", "cache", "item"],
                source=f"dialogue:{captive.name}",
                source_turn=self.state.turn,
                origin_zone=(self.state.zone_x, self.state.zone_y),
                salience=4,
                confidence=0.8,
                claimed_space=SpatialHint(mode="direction", direction=direction),
                status="unverified",
            )
        )
        profile.lead = None  # told once

    def found_organization(self, name: str) -> Faction:
        """Raise a player-founded organization — a guild, warband, cult, court (Phase F).
        Plural and distinct: you may found several. It is a first-class faction
        (kind ``player_org``) the social systems treat like any other; you start as its
        founder."""
        org_id = f"player_org_{normalize_id(name) or len(self.state.faction_ledger.factions)}"
        existing = self.state.faction_ledger.get(org_id)
        if existing is not None:
            return existing
        org = Faction(
            id=org_id,
            name=name.strip() or "your banner",
            kind="player_org",
            mood="fledgling",
            player_rank="founder",
            notes_anchor=f"faction:{org_id}",
        )
        self.state.faction_ledger.add(org)
        self.state.add_message(f"You raise a banner: {org.name} is founded.")
        return org

    def followers(self) -> list[tuple[str, Any]]:
        """(entity_id, NPCProfile) for every NPC whose bond has crossed into following —
        a *bond* state, not a combat-faction change (a loyal reeve stays neutral)."""
        return sorted(
            (
                (npc_id, profile)
                for npc_id, profile in self.state.npc_profiles.items()
                if profile.bond.is_follower()
            ),
            key=lambda item: item[0],
        )

    def _simulate_backlash(self) -> None:
        """The factions read the world and *spend to act* (Phase D, strategy §5.2). A
        crackdown is not "fear high", it is "the Empire spends a patrol"; resources are
        finite with slow regen, so reactions ebb and flow and an overspent faction goes
        quiet. Intents are queued and realized when the player next enters a zone."""
        state = self.state
        ledger = state.faction_ledger
        empire = ledger.primary("empire")
        rebellion = ledger.primary("resistance")
        # Slow daily regen + mood drift (legibility), before anyone spends.
        if empire is not None:
            empire.resources["patrols"] = min(
                EMPIRE_PATROLS_START, empire.resources.get("patrols", 0) + 1
            )
            threat = empire.standing_of("imperial_threat")
            empire.mood = (
                "furious" if threat >= 3 else "alarmed" if threat >= 1 else "orderly"
            )
        if rebellion is not None:
            rebellion.resources["cells"] = min(
                REBELLION_CELLS_START, rebellion.resources.get("cells", 0) + 1
            )
            gratitude = rebellion.standing_of("gratitude")
            rebellion.mood = (
                "rising"
                if gratitude >= 3
                else "stirring"
                if gratitude >= 1
                else "hopeful"
            )
        if (
            len(state.pending_backlash) < MAX_PENDING_BACKLASH
            and empire is not None
            and empire.standing_of("imperial_threat") >= CRACKDOWN_THRESHOLD
            and ledger.spend(empire.id, "patrols", 1)
        ):
            state.pending_backlash.append({"kind": "crackdown"})
        if (
            len(state.pending_backlash) < MAX_PENDING_BACKLASH
            and rebellion is not None
            and rebellion.standing_of("gratitude") >= UPRISING_THRESHOLD
            and ledger.spend(rebellion.id, "cells", 1)
        ):
            state.pending_backlash.append({"kind": "resistance"})

    def _simulate_empire_pressure(self) -> None:
        """The Empire spends down its finite defenses fighting the threat you represent
        (D9, §0.5). Depletion scales with your imperial_threat standing; when the pool hits
        zero, the path to the emperor opens. Phase D will route this through richer faction
        spending; this is the v1 single-pool gate."""
        empire = self.state.faction_ledger.primary("empire")
        if empire is None:
            return
        threat = empire.standing_of("imperial_threat")
        current = int(empire.resources.get("defense", 0))
        if threat <= 0 or current <= 0:
            return
        loss = max(1, round(threat * EMPIRE_PRESSURE_RATE))
        empire.resources["defense"] = max(0, current - loss)
        if empire.resources["defense"] == 0:
            self.state.add_message(
                "Word spreads that the Empire's defenses are breaking - the road to the "
                "emperor lies open."
            )

    def emperor_reachable(self) -> bool:
        """True once the Empire's defenses are spent — the emperor can be reached and
        killed (D9). Until then he is the best-guarded target alive."""
        empire = self.state.faction_ledger.primary("empire")
        return empire is not None and int(empire.resources.get("defense", 0)) <= 0

    def _on_enter_location(self) -> None:
        """Called when the player arrives in a (new) zone or floor. Catches up the daily
        tick (a turn advance during the transition may have crossed 05:00), then shows
        what the world has registered: a rumor of a recent public deed, and the marks the
        place itself bears from what you did here (Phase E)."""
        self._maybe_run_daily_tick()
        self._announce_deed_rumors()
        self._render_deed_consequences()
        self._realize_backlash()

    def _realize_backlash(self) -> None:
        """Spawn the events the factions set in motion (Phase D): an Imperial patrol sent
        to hunt you down, sympathizers taking up arms for you. The threat (and the help)
        are real bodies in the world, so pressure has teeth and a price."""
        if self.state.game_over or not self.state.pending_backlash:
            return
        events, self.state.pending_backlash = self.state.pending_backlash, []
        for event in events:
            kind = event.get("kind")
            if kind == "crackdown":
                self._spawn_backlash_crackdown()
            elif kind == "resistance":
                self._spawn_backlash_resistance()

    def _spawn_backlash_crackdown(self) -> None:
        tile = self._find_open_prop_tile(min_radius=3, max_radius=8)
        if tile is None:
            return
        x, y = tile
        self.spawn_actor(
            "Imperial enforcer",
            "e",
            x,
            y,
            12,
            4,
            1,
            "enemy",
            "melee",
            tags={"empire", "human", "soldier", "backlash"},
        )
        self.state.add_message(
            "An Imperial patrol has tracked you here - they mean to make an example of you."
        )

    def _spawn_backlash_resistance(self) -> None:
        tile = self._find_open_prop_tile(min_radius=2, max_radius=6)
        if tile is None:
            return
        x, y = tile
        ally = self.spawn_actor(
            "sworn sympathizer",
            "s",
            x,
            y,
            12,
            3,
            0,
            "ally",
            "melee",
            tags={"human", "rebel", "backlash"},
        )
        ally.description = (
            "A stranger who heard what you did and came to stand with you."
        )
        self.state.add_message(
            "A sympathizer, hearing of your deeds, takes up arms at your side."
        )

    def _render_deed_consequences(self) -> None:
        """Phase E — the world shows it remembers. For each kind of public deed you did in
        *this* zone, leave one evocative, deterministic mark (a bloodstain, ruin, defiled
        ground, a memorial), placed once. Plus the Empire's wanted poster, which follows
        your legend everywhere. Returning to a place you changed looks changed."""
        state = self.state
        here = state.current_place_key()
        by_type: dict[str, list[Deed]] = {}
        for deed in state.deed_ledger.deeds:
            # Match on place_key (zone + depth) so a dungeon-level deed doesn't mark the
            # surface; fall back to zone for deeds recorded before place_key existed.
            deed_place = deed.place_key or f"{deed.zone[0]},{deed.zone[1]}@1"
            if deed.is_public and deed.applied and deed_place == here:
                by_type.setdefault(deed.type, []).append(deed)
        for deed_type in sorted(by_type):
            spec = _DEED_CONSEQUENCE_PROPS.get(deed_type)
            if spec is None:
                continue
            prop_id = f"consequence_{here}_{deed_type}"
            if prop_id in state.entities:
                continue  # placed once; the zone keeps it
            tile = self._find_open_prop_tile()
            if tile is None:
                break
            name, char, tags, description = spec
            count = len(by_type[deed_type])
            if count >= 3:
                description += " It happened more than once here."
            x, y = tile
            state.entities[prop_id] = Entity(
                id=prop_id,
                name=name,
                kind="prop",
                x=x,
                y=y,
                char=char,
                blocks=False,
                tags=set(tags) | {deed_type},
                description=description,
                hp=4,
                max_hp=4,
                faction="neutral",
            )
        self._maybe_place_wanted_poster()

    def camp_rest(self, hours: float = 8.0, until_hour: float | None = None) -> bool:
        """Make camp and rest. By default a full night's 8 hours; ``until_hour`` rests
        until the next occurrence of that wall-clock hour instead. Advances the clock,
        restores mana fully and health in proportion to the time rested, and lets any
        daily tick that the rest crosses (05:00) fire. A deliberate skip of time —
        resting in the open is, in the fiction, a vulnerability, though
        encounters-during-rest aren't simulated yet."""
        state = self.state
        if state.game_over:
            return False
        if until_hour is not None:
            delta = (until_hour - state.hour_of_day) % 24
            hours = delta if delta > 1e-9 else 24.0  # already there → rest a full day
        hours = max(0.0, hours)
        rounds = max(1, round(hours / 24 * TURNS_PER_DAY))
        state.turn += rounds
        player = state.player
        player.mana = player.max_mana
        # Health recovers with rest, saturating around a half-day's sleep.
        player.hp = min(
            player.max_hp,
            player.hp + round(player.max_hp * min(hours, 12.0) / 24.0),
        )
        hour = int(state.hour_of_day)
        minute = int((state.hour_of_day - hour) * 60)
        state.add_message(
            f"You make camp and rest. You wake at {hour:02d}:{minute:02d}."
        )
        self._maybe_run_daily_tick()
        self.update_fov()
        return True

    def _announce_deed_rumors(self) -> None:
        """Surface one rumor for the most notable public, already-simulated deed that
        hasn't been rumored yet (so a deed becomes 'talk of the road' once, on arrival)."""
        state = self.state
        candidates = [
            deed
            for deed in state.deed_ledger.deeds
            if deed.is_public and deed.applied and not deed.rumored
        ]
        if not candidates:
            return
        deed = max(candidates, key=lambda d: d.magnitude)
        deed.rumored = True
        state.add_message(
            f"Word on the road: they say you {deed.summary}. The Empire has taken note."
        )

    def _maybe_place_wanted_poster(self) -> None:
        """Hang an Imperial wanted poster in this location if the player has a public,
        simulated deed and there isn't already one here — a prop bearing their legend."""
        state = self.state
        if not any(deed.is_public and deed.applied for deed in state.deed_ledger.deeds):
            return
        if any("wanted_poster" in entity.tags for entity in state.entities.values()):
            return
        tile = self._find_open_prop_tile()
        if tile is None:
            return
        x, y = tile
        threat = state.faction_ledger.get("empire")
        threat_level = threat.standing_of("imperial_threat") if threat else 0.0
        bounty = max(1, int(round(threat_level * 500)))
        entity = Entity(
            id=self.next_entity_id("prop"),
            name="Imperial wanted poster",
            kind="prop",
            x=x,
            y=y,
            char="!",
            blocks=False,
            tags={"wanted_poster", "empire", "paper", "flammable"},
            description=(
                "A hastily nailed Imperial notice. A crude likeness glares above the "
                f"word WANTED, and a bounty of {bounty} crowns that climbs with every "
                "patrol you cut down."
            ),
            hp=4,
            max_hp=4,
            faction="neutral",
        )
        state.entities[entity.id] = entity

    def _find_open_prop_tile(
        self, min_radius: int = 1, max_radius: int = 4
    ) -> tuple[int, int] | None:
        """A free, walkable tile near the player (nearest first, from ``min_radius`` out).
        Used to place consequence props and to spawn backlash combatants a little away."""
        player = self.state.player
        occupied = {
            (entity.x, entity.y)
            for entity in self.state.entities.values()
            if entity.kind != "item"
        }
        for radius in range(min_radius, max_radius + 1):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    x, y = player.x + dx, player.y + dy
                    if not self.in_bounds(x, y) or (x, y) in occupied:
                        continue
                    tile = self.tile_at(x, y)
                    if tile in BLOCKING_TILES or tile == DOOR:
                        continue
                    return (x, y)
        return None

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

    # Source precedence for item flavor: a discovered Investigate description is richer and
    # more authoritative than the plain Entity.description copied at pickup, so it must never
    # be downgraded. Equal-tier writes keep the longer text. This keeps the merge
    # order-independent (Investigate always wins regardless of pickup order), so replay is
    # deterministic. See GameState.item_lore.
    _ITEM_LORE_SOURCE_RANK = {"description": 1, "generated": 1, "investigated": 2}

    def set_item_lore(
        self,
        item_key: str,
        display_name: str,
        description: str,
        *,
        source: str = "description",
    ) -> None:
        key = normalize_id(str(item_key or ""))
        text = " ".join(str(description or "").split())
        if not key or not text:
            return
        rank = self._ITEM_LORE_SOURCE_RANK.get(source, 1)
        existing = self.state.item_lore.get(key)
        if existing is not None:
            existing_rank = self._ITEM_LORE_SOURCE_RANK.get(existing.get("source"), 1)
            if rank < existing_rank:
                return
            if rank == existing_rank and len(text) <= len(
                str(existing.get("description") or "")
            ):
                return
        self.state.item_lore[key] = {
            "display_name": str(display_name or item_key),
            "description": text,
            "source": source,
        }

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

    # ------------------------------------------------------------------
    # Experimental LLM prop set-dressing. A background per-room batch call
    # (prop_gen.py) generates ambient props for the current floor; results are
    # swapped in only for props the player has NOT seen yet (freeze-once-seen),
    # so the world never rewrites itself under the player's eye. On by default
    # when an Ollama backend is reachable; static props otherwise. See the plan
    # in docs and prop_gen.py.
    # ------------------------------------------------------------------

    _PROP_MAX_PENDING = 3  # cap concurrent room calls; nearest rooms go first
    _PROP_GLYPH_BY_TAG = {
        "water": "~",
        "liquid": "~",
        "acid": "~",
        "oil": "~",
        "plant": "p",
        "fungus": "p",
        "web": "w",
        "silk": "w",
        "bone": ";",
        "ash": ".",
        "paper": "~",
        "cloth": "|",
        "glass": "o",
        "crystal": "*",
        "light": "*",
        "magic": "*",
        "metal": "=",
        "stone": "n",
        "wood": "n",
        "sharp": "/",
    }

    def _setup_prop_generation(
        self, provider_name: str | None, prop_provider: PropProvider | None
    ) -> None:
        self._prop_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._pending_prop_rooms: dict[
            str, concurrent.futures.Future[list[PropSpec]]
        ] = {}
        self._prop_rooms_done: set[str] = set()
        if prop_provider is not None:
            # Explicit injection (tests, embedders) always wins and is enabled.
            self._prop_provider: PropProvider | None = prop_provider
            return
        self._prop_provider = None
        resolved = (provider_name or get_props_provider() or "").lower().strip()
        if resolved in {"ollama", "auto"} and ollama_reachable(ollama_host("props")):
            # Build lazily here so the unreachable/offline path costs only one probe.
            self._prop_provider = make_prop_provider("ollama")

    def _glyph_for_tags(self, tags: list[str]) -> str:
        for tag in tags:
            if tag in self._PROP_GLYPH_BY_TAG:
                return self._PROP_GLYPH_BY_TAG[tag]
        return "*"

    def _prop_seen(self, entity: Entity) -> bool:
        """A prop is frozen once the player has laid eyes on its tile."""
        return self.tile_key(entity.x, entity.y) in self.state.explored

    def _replaceable_props_by_room(self) -> dict[str, list[Entity]]:
        by_room: dict[str, list[Entity]] = {}
        for entity in self.state.entities.values():
            if entity.kind != "prop" or "set_dressing" not in entity.tags:
                continue
            if "llm_generated" in entity.tags:
                continue
            # Deed-driven marks (consequence props, wanted posters) are meaningful and
            # fixed — never let the flavor generator replace them (that would also re-fire
            # background LLM work every move).
            if "consequence" in entity.tags or "wanted_poster" in entity.tags:
                continue
            room_id = self.state.tile_rooms.get(self.tile_key(entity.x, entity.y))
            if room_id:
                by_room.setdefault(room_id, []).append(entity)
        return by_room

    def _launch_prop_generation(self) -> None:
        if self._prop_provider is None:
            return
        if len(self._pending_prop_rooms) >= self._PROP_MAX_PENDING:
            return
        by_room = self._replaceable_props_by_room()
        player = self.state.player
        pending = [
            (room_id, props)
            for room_id, props in by_room.items()
            if room_id not in self._prop_rooms_done
            and room_id not in self._pending_prop_rooms
        ]
        if not pending:
            return

        def room_distance(item: tuple[str, list[Entity]]) -> int:
            profile = self.state.room_profiles.get(item[0])
            if profile is None:
                return 1_000_000
            cx, cy = profile.center
            return abs(cx - player.x) + abs(cy - player.y)

        pending.sort(key=room_distance)
        slots = self._PROP_MAX_PENDING - len(self._pending_prop_rooms)
        for room_id, props in pending[:slots]:
            profile = self.state.room_profiles.get(room_id)
            if profile is None:
                self._prop_rooms_done.add(room_id)
                continue
            if self._prop_executor is None:
                self._prop_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=2
                )
            context = self._prop_context(profile, props)
            self._pending_prop_rooms[room_id] = self._prop_executor.submit(
                self._prop_provider.generate, context
            )

    def _prop_context(
        self, profile: RoomProfile, props: list[Entity]
    ) -> dict[str, Any]:
        region = self.region
        # Names already in the room (the ones we'll replace + any kept static props)
        # become the anti-repetition signal.
        room_tiles = {
            self.tile_key(x, y)
            for y in range(profile.y, profile.y + profile.h)
            for x in range(profile.x, profile.x + profile.w)
        }
        avoid = sorted(
            {
                entity.name
                for entity in self.state.entities.values()
                if entity.kind == "prop"
                and self.tile_key(entity.x, entity.y) in room_tiles
            }
        )
        return {
            "region": region.name,
            "voice": region.voice,
            "room": {
                "room_type": profile.room_type,
                "era": profile.era,
                "condition": profile.condition,
                "topics": list(profile.topics),
                "tags": list(profile.tags),
            },
            "wildness": region.effective_wildness(self.state.depth),
            "depth": self.state.depth,
            "count": min(len(props), 4),
            "avoid": avoid,
            "mechanical_tags": list(MECHANICAL_TAGS),
        }

    def _poll_prop_generation(self) -> None:
        if not self._pending_prop_rooms:
            return
        for room_id, future in list(self._pending_prop_rooms.items()):
            if not future.done():
                continue
            self._pending_prop_rooms.pop(room_id, None)
            self._prop_rooms_done.add(room_id)
            try:
                specs = future.result()
            except Exception:
                continue  # any failure: keep the static props already in place
            self._apply_prop_specs(room_id, specs)

    def _apply_prop_specs(self, room_id: str, specs: list[PropSpec]) -> None:
        if not specs:
            return
        by_room = self._replaceable_props_by_room()
        # Freeze-once-seen: only swap props the player has not yet laid eyes on.
        eligible = [
            entity for entity in by_room.get(room_id, []) if not self._prop_seen(entity)
        ]
        for entity, spec in zip(eligible, specs):
            self._replace_prop_with_spec(entity, spec)

    def _replace_prop_with_spec(self, entity: Entity, spec: PropSpec) -> None:
        entity.name = spec.name
        entity.description = spec.description
        entity.char = spec.char or self._glyph_for_tags(spec.tags)
        entity.blocks = spec.blocks
        # Keep set_dressing for consistency; llm_generated marks it frozen against
        # re-generation and records the spec for saves/audit.
        entity.tags = set(spec.tags) | {"set_dressing", "llm_generated"}
        entity.details["prop_spec"] = {
            "name": spec.name,
            "description": spec.description,
            "char": entity.char,
            "blocks": spec.blocks,
            "tags": list(spec.tags),
        }

    def close(self) -> None:
        """Release background executors. Idempotent; safe to call more than once."""
        for future in self._pending_prop_rooms.values():
            future.cancel()
        self._pending_prop_rooms.clear()
        if self._prop_executor is not None:
            self._prop_executor.shutdown(wait=False, cancel_futures=True)
            self._prop_executor = None
        if self._town_executor is not None:
            self._town_executor.shutdown(wait=False, cancel_futures=True)
            self._town_executor = None

    def update_fov(self) -> None:
        player = self.state.player
        previous_entity_ids = set(self.state.visible_entity_ids)
        visible: set[str] = set()
        radius = self.effective_fov_radius()
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
        current_entity_ids = {
            entity.id
            for entity in self.state.entities.values()
            if entity.id != self.state.player_id
            and entity.kind in {"actor", "npc"}
            and entity.hp > 0
            and self.tile_key(entity.x, entity.y) in visible
        }
        self.state.visible_entity_ids = current_entity_ids
        for entity_id in sorted(current_entity_ids - previous_entity_ids):
            entity = self.state.entities.get(entity_id)
            if entity is None or entity.hp <= 0:
                continue
            self._fire_triggers(
                ["on_enters_sight", "on_entity_enters_sight"],
                {"target": entity, "source": player},
            )
        if self._prop_provider is not None:
            self._poll_prop_generation()
            self._launch_prop_generation()

    def effective_fov_radius(self) -> int:
        player = self.state.player
        radius = clamp_int(self.state.fov_radius, 0, 99)
        if "sight_shrouded" not in player.statuses:
            return radius
        override = player.details.get("sight_radius")
        if override is None:
            return min(radius, 2)
        return min(radius, clamp_int(override, 0, 99))

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
        if self._delta_capture:
            self.record_delta(
                StateDelta(
                    op="create_tile",
                    target=key,
                    summary=f"tile at {x},{y} became {TILE_NAMES.get(tile, tile)}",
                    details={"x": x, "y": y, "tile": tile, "duration": duration},
                )
            )
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
            if curse.xp_to_clear < 1 or curse.clear_progress < 0:
                errors.append(f"curse {curse_id!r} has invalid clearing progress")
        for table_name, table in (
            ("tile_tags", state.tile_tags),
            ("tile_durations", state.tile_durations),
            ("tile_flows", state.tile_flows),
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
        for key, flow in state.tile_flows.items():
            if not isinstance(flow, dict):
                errors.append(f"tile flow {key!r} is not an object")
                continue
            if flow.get("duration") != "permanent":
                duration = flow.get("duration")
                if not isinstance(duration, int) or duration < 1:
                    errors.append(
                        f"tile flow {key!r} has invalid duration: {duration!r}"
                    )
            for axis in ("dx", "dy"):
                value = flow.get(axis)
                if not isinstance(value, int) or value < -1 or value > 1:
                    errors.append(f"tile flow {key!r} has invalid {axis}: {value!r}")
            if flow.get("dx") == 0 and flow.get("dy") == 0:
                errors.append(f"tile flow {key!r} has no direction")
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
        from_x, from_y = player.x, player.y
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
            player.details["last_move_delta"] = [player.x - from_x, player.y - from_y]
            self.state.player_steps += 1
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
        self._grant_reward_reputation(promise.reward)
        return QuestLogEntry(
            entry.id,
            entry.name,
            entry.description,
            entry.contact,
            entry.location,
            "completed",
        )

    def _grant_reward_reputation(self, reward: Any) -> None:
        """Apply a promise/quest reward's standing deltas to the faction ledger — the
        previously-defined-but-unconsumed `Reward.reputation` (strategy §3), now wired.
        Keys are ``faction`` (→ gratitude) or ``faction.axis`` for a specific axis."""
        if reward is None:
            return
        for key, amount in getattr(reward, "reputation", {}).items():
            faction_id, _, axis = str(key).partition(".")
            self.state.faction_ledger.adjust_standing(
                faction_id, axis or "gratitude", float(amount)
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
        if player.traits:
            player_block["traits"] = list(player.traits)
        # The player's legend reaches dialogue so NPCs can greet (or fear) them by
        # reputation — the connective tissue of §5.1. Mechanical tags become plain words;
        # the prose mirror in scene_notes carries the colour.
        legend = self.legend_words(self.state.player_soul_id)
        if legend:
            player_block["legend"] = legend
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
            # The same semantic substrate the resolver reads, focused on this NPC's scene:
            # the player's traits, the room's notes, the NPC's faction standing. A fact
            # minted by a spell ("wears a goblin-hating hat") reaches dialogue with no extra
            # wiring -- the whole point of one shared ledger.
            "scene_notes": self.collect_scene_notes(
                self.scene_anchors_around(
                    npc.x, npc.y, NPC_PERCEPTION_RADIUS, include=[npc, player]
                )
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
                **({"traits": list(entity.traits)} if entity.traits else {}),
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

    def announce_dialogue_reply(self, npc: Entity, message: str, reply: str) -> None:
        """Put the spoken exchange on the message log immediately, so the player can read
        it before any follow-up work (the trade-structuring call, lore extraction) resolves.
        Talk runs on a worker thread while the UI keeps redrawing, so this paints right away;
        the durable record and the turn settlement happen in apply_dialogue_exchange."""
        self.state.add_message(f'You say to {npc.name}: "{message}"')
        self.state.add_message(f'{npc.name} says: "{reply}"')

    def apply_dialogue_exchange(
        self,
        npc: Entity,
        message: str,
        reply: str,
        trade_data: dict[str, Any] | None = None,
        announced: bool = False,
    ) -> None:
        """Record + display the exchange, then either settle the turn immediately
        (the normal case) or -- when the structuring call came back with a real
        proposal -- stash it as `pending_trade` and stop short of finishing the
        turn. The confirmation modal takes over from there; accepting or rejecting
        (resolve_pending_trade) is what reaches finish_player_turn for this beat,
        exactly as the two branches of apply_wild_magic_resolution each
        independently reach it exactly once.

        `announced=True` means the spoken lines were already shown via
        announce_dialogue_reply (the live talk path, so the reply paints before the
        trade-judge wait); we then skip re-adding them and just record + settle."""
        profile = self.state.npc_profiles[npc.id]
        profile.record_exchange("player", message)
        profile.record_exchange("npc", reply)
        if not announced:
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
        if self.state.flags.get("seal_stairs") or self.state.flags.get("stairs_sealed"):
            self.state.add_message("The stairs are sealed by magic.")
            return False
        if self.state.depth >= self.state.max_depth:
            # Verticality is bounded and local (D2/§0.2): a site has a few levels, like
            # the real world — reaching the bottom is never a win or progression. Victory
            # is killing the emperor (Phase B), unlocked by pressure, not by descending.
            self.state.add_message(
                "The passage bottoms out in solid rock - there is no way further down."
            )
            return False

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

        self.clear_target()
        self.state.turn += 1
        self.update_fov()
        self.state.add_message(f"You descend to dungeon floor {self.state.depth}.")
        self._on_enter_location()
        return True

    def ascend_stairs(self) -> bool:
        player = self.state.player
        if self.tile_at(player.x, player.y) != STAIRS_UP:
            self.state.add_message("There are no upward stairs here.")
            return False
        if self.state.flags.get("seal_stairs") or self.state.flags.get("stairs_sealed"):
            self.state.add_message("The stairs are sealed by magic.")
            return False
        if self.state.depth <= 1:
            self.state.add_message("The dungeon mouth is not that easy to find again.")
            return False

        # Save current dungeon floor
        self._save_dungeon_floor(self.state.depth)
        self.clear_target()

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
            self._on_enter_location()
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
            self._on_enter_location()
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

    def _aimed_enemy(self, max_distance: int) -> Entity | None:
        """Target for a standard offensive spell: the player's explicitly marked
        creature when one is set and in range, otherwise the nearest enemy. Lets a
        player focus-fire a specific foe instead of always hitting the closest."""
        marked = self.selected_target_entity()
        if (
            marked is not None
            and marked.id != self.state.player_id
            and self.distance(self.state.player, marked) <= max_distance
        ):
            return marked
        return self.nearest_enemy(max_distance=max_distance)

    def cast_standard_bolt(self) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        if player.mana < 2:
            self.state.add_message("The safe spell fizzles. You need 2 mana.")
            return False
        target = self._aimed_enemy(max_distance=8)
        if target is None:
            self.state.add_message("No enemy is close enough for a spark bolt.")
            return False
        player.mana -= 2
        self.damage_entity(target, 5, "spark", source=player)
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
        target = self._aimed_enemy(max_distance=6)
        if target is None:
            self.state.add_message("No enemy is close enough for a frost shard.")
            return False
        player.mana -= 2
        self.damage_entity(target, 4, "frost", source=player)
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
            from_x, from_y = entity.x, entity.y
            entity.x = x
            entity.y = y
            if entity.id == self.state.player_id:
                self.pick_up_items_at_player()
                self.update_fov()
            self._apply_tile_entry(entity)
            if self._delta_capture:
                self.record_delta(
                    StateDelta(
                        op="move",
                        target=entity.id,
                        summary=f"{entity.name} moved to {x},{y}",
                        details={"from": [from_x, from_y], "to": [entity.x, entity.y]},
                    )
                )
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
        self._maybe_run_daily_tick()
        self._tick_environment()
        self._tick_tile_durations()
        self._tick_auras()
        self.state.semantics.decay(self.state.turn)
        self._tick_event_timers()
        self._tick_triggers()
        self.update_fov()
        self._enemy_turns()
        self._ally_turns()
        self._npc_turns()
        self._tick_behavior_modifiers()
        self._process_entity_behaviors()
        self._regenerate_player()
        self._ambient_sounds()
        self._update_npc_perceptions()

    def _stasis_active(self) -> bool:
        if getattr(self, "_stasis_pause_turn", None) == self.state.turn:
            return True
        player = self.state.player
        return status_duration(player.statuses.get("stasis")) > 0

    def _tick_stasis_status(self) -> None:
        player = self.state.player
        if "stasis" not in player.statuses:
            return
        self._stasis_pause_turn = self.state.turn
        value = player.statuses.get("stasis")
        if value == "permanent":
            return
        turns = status_duration(value) - 1
        if turns <= 0:
            player.statuses.pop("stasis", None)
            player.status_display.pop("stasis", None)
            player.status_expiry_text.pop("stasis", None)
            self.state.add_message("Time resumes its grip.")
        else:
            player.statuses["stasis"] = turns

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

    def _tick_behavior_modifiers(self) -> None:
        for entity in self.state.entities.values():
            if entity.kind not in {"player", "actor", "npc"} or entity.hp <= 0:
                continue
            tick_behavior_modifiers(entity)

    def _tick_environment(self) -> None:
        if self._stasis_active():
            self._tick_stasis_status()
            return
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
        self._tick_flow_fields()
        self._tick_fire_spread()
        self._tick_poison_spread()

    def _tick_flow_fields(self) -> None:
        if not self.state.tile_flows:
            return
        for entity in sorted(self.state.entities.values(), key=lambda e: e.id):
            if entity.kind == "item" or entity.hp <= 0:
                continue
            flow = self.state.tile_flows.get(self.tile_key(entity.x, entity.y))
            if not isinstance(flow, dict):
                continue
            raw_dx = flow.get("dx", 0)
            raw_dy = flow.get("dy", 0)
            dx = clamp_int(raw_dx, -1, 1) if raw_dx is not None else 0
            dy = clamp_int(raw_dy, -1, 1) if raw_dy is not None else 0
            if dx == 0 and dy == 0:
                continue
            before = (entity.x, entity.y)
            moved = self.push_entity(entity, dx, dy, 1)
            if not moved:
                continue
            if entity.id == self.state.player_id:
                self.state.add_message("The current carries you.")
            else:
                self.state.add_message(f"The current carries {entity.name}.")
            if (entity.x, entity.y) == before:
                continue

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
            "sight_shrouded": "Your sight clears.",
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
            "sight_shrouded",
            "warded",
            "strained",
            "drained",
            "jinxed",
            "crawling_skin",
            "silenced",
            "berserk",
            "empowered",
            "weakened",
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
                if status == "sight_shrouded":
                    entity.details.pop("sight_radius", None)
                if entity.id == self.state.player_id:
                    msg = custom_expiry or _DEFAULT_EXPIRY.get(status)
                    if msg:
                        self.state.add_message(msg)
                    if status == "sight_shrouded":
                        self.update_fov()
            else:
                entity.statuses[status] = turns

    def _tick_tile_durations(self) -> None:
        if self._stasis_active():
            return
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
        expired_flows: list[str] = []
        for key, flow in list(self.state.tile_flows.items()):
            if not isinstance(flow, dict) or flow.get("duration") == "permanent":
                continue
            duration = clamp_int(flow.get("duration"), 0, 999) - 1
            if duration <= 0:
                expired_flows.append(key)
            else:
                flow["duration"] = duration
        for key in expired_flows:
            self.state.tile_flows.pop(key, None)

    def _tick_event_timers(self) -> None:
        if self._stasis_active():
            return
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
        if self._run_scheduled_payload(event):
            return
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
        elif event_type == "release_delayed_damage":
            self._release_delayed_damage(event)

    def _persistent_anchor_alive(self, trigger: dict[str, Any]) -> bool:
        """An anchored persistent effect (or sympathetic link) ends when the thing it is
        bound to -- or the other end of a link -- is dead or gone. Free-floating triggers
        (no anchor) are unaffected, so ordinary create_trigger wards are untouched."""
        for key in ("anchor", "link_partner"):
            ref = trigger.get(key)
            if not ref:
                continue
            entity = self.state.entities.get(str(ref))
            if entity is None or not entity.alive:
                return False
            if entity.kind == "prop" and entity.hp <= 0:
                return False
        return True

    def _tick_triggers(self) -> None:
        remaining: list[dict[str, Any]] = []
        for trigger in self.state.triggers:
            if not self._persistent_anchor_alive(trigger):
                name = str(trigger.get("name") or "A waiting spell").strip()
                self.state.add_message(f"{name} unravels, its anchor gone.")
                continue
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
        # Defender side: hooks keyed on who WAS damaged (matched against event["target"]).
        names = ["on_damaged", "on_actor_damaged"]
        if target.id == self.state.player_id:
            names.extend(["on_player_damaged", "on_player_hit"])
        elif target.faction == "enemy":
            names.extend(["on_enemy_damaged", "on_enemy_hit"])
        # Attacker side: hooks keyed on who DEALT the damage (matched against event["source"]
        # by a trigger whose `match` is "source"). Only fire when there is an attacker, so a
        # trap or hazard tile never trips a "when I strike" effect. This is the substrate a
        # later item enchantment reuses: when melee carries the weapon used, add an item-keyed
        # name here and let the trigger match:"weapon".
        if isinstance(source, Entity):
            names.append("on_deal_damage")
            if source.id == self.state.player_id:
                names.append("on_player_deal_damage")
            elif source.faction == "enemy":
                names.append("on_enemy_deal_damage")
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

    def _fire_lethal_damage_triggers(
        self,
        target: Entity,
        source: Entity | None,
        amount: int,
        damage_type: str,
    ) -> list[str]:
        event = {
            "target": target,
            "source": source,
            "amount": amount,
            "damage_type": damage_type,
        }
        names = ["on_lethal_damage"]
        if target.id == self.state.player_id:
            names.append("on_player_lethal_damage")
        elif target.faction == "enemy":
            names.append("on_enemy_lethal_damage")
        return self._fire_triggers(names, event)

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
            if trigger_name not in wanted or not self._trigger_matches(trigger, event):
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

    def _trigger_matches(self, trigger: dict[str, Any], event: dict[str, Any]) -> bool:
        """Does this trigger's criterion match the firing event?

        A trigger's `target` is the criterion (player / an id / a faction / a tag / "any").
        Its `match` field names WHICH role of the event the criterion is compared against:
        "target" (the entity that was damaged -- the default, defender side) or "source" (the
        entity that dealt the damage -- attacker side, e.g. "a blade that bleeds whatever it
        strikes"). The role is a plain `event.get(role)` lookup, so a future damage event that
        also carries a "weapon"/"item" role (once items have instances) can be matched the same
        way -- match:"weapon" with no other change here. See _fire_damage_triggers."""
        role = normalize_id(str(trigger.get("match") or "target"))
        if role not in {"target", "source"}:
            role = "target"
        subject = event.get(role)
        raw_target = trigger.get("target")
        if raw_target in {None, "", "any"}:
            # A source-side ward needs an attacker to ride; environmental damage (a trap, a
            # poison tile) has source=None and must not fire it. Target-side keeps the old
            # permissive default so non-damage events still fire by name alone.
            if role == "source":
                target_matches = isinstance(subject, Entity)
            else:
                target_matches = True
        elif not isinstance(subject, Entity):
            # Target role keeps the old permissive default (a non-damage event with no real
            # target still fires by name alone); a source-side criterion cannot match without
            # an attacker in the event.
            target_matches = role == "target"
        else:
            trigger_target = normalize_id(str(raw_target))
            if trigger_target in {"player", "self", "you"}:
                target_matches = subject.id == self.state.player_id
            elif trigger_target in {"enemy", "nearest_enemy", "all_enemies", "enemies"}:
                target_matches = subject.faction == "enemy"
            elif trigger_target in {"source", "attacker", "caster"}:
                target_matches = isinstance(event.get("source"), Entity)
            else:
                target_matches = (
                    subject.id == trigger_target
                    or trigger_target in subject.tags
                    or trigger_target in normalize_id(subject.name).split("_")
                )
        return target_matches and evaluate_condition(self, trigger.get("when"), event)

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

        # Sympathetic/persistent effects echo the firing event's magnitude: amount of
        # "trigger_amount" copies the damage just dealt (scaled by an optional amount_ratio),
        # and "trigger_damage_type" copies its damage type. This is what makes "whatever
        # wounds me wounds him" land the same-sized wound. amount_ratio never reaches a real
        # handler -- it is consumed here.
        if effect.get("amount") == "trigger_amount":
            base = event.get("amount")
            ratio = effect.pop("amount_ratio", 1)
            if isinstance(base, (int, float)):
                try:
                    scaled = int(round(float(base) * float(ratio)))
                except (TypeError, ValueError):
                    scaled = int(base)
                effect["amount"] = max(1, scaled)
            else:
                effect.pop("amount", None)
        else:
            effect.pop("amount_ratio", None)
        if effect.get("damage_type") == "trigger_damage_type":
            effect["damage_type"] = str(event.get("damage_type") or "arcane")

        # Disciplined troops hold their post rather than break formation to wander.

    _SUMMONER_MINIONS = ["bog whelp", "carrion sprite", "husk crawler"]

    def _regenerate_player(self) -> None:
        player = self.state.player
        if self.state.turn % 5 == 0 and player.mana < player.max_mana:
            player.mana += 1

    # Strings the resolver (or the engine internals) may use to mean "the square the
    # player explicitly marked". When a target is set, these resolve to its occupant;
    # when none is set they fall through to the nearest-enemy aliases below, preserving
    # the old auto-aim behavior.
    _SELECTED_TARGET_KEYWORDS = frozenset(
        {
            "target",
            "selected",
            "selected_target",
            "selected_tile",
            "there",
            "that_tile",
            "that_square",
            "marked",
            "marked_tile",
            "reticle",
            "cursor",
        }
    )

    def has_target(self) -> bool:
        return self.state.target_x is not None and self.state.target_y is not None

    # --- operation deltas (Stage 6) ------------------------------------------------------
    def begin_delta_capture(self) -> None:
        """Start recording operation deltas for a wild-magic cast."""
        self._delta_capture = True
        self._delta_log = []

    def end_delta_capture(self) -> None:
        """Stop recording (e.g. before the turn's environment/AI ticks)."""
        self._delta_capture = False

    def discard_deltas(self) -> None:
        """Drop the recorded deltas (used when a cast rolls back)."""
        self._delta_capture = False
        self._delta_log = []

    def record_delta(self, delta: StateDelta) -> None:
        """Append a delta when capture is active. Shared mutators call this; outside a
        wild-magic cast capture is off, so this is a no-op and core combat is unaffected."""
        if getattr(self, "_delta_capture", False):
            self._delta_log.append(delta)

    def collected_deltas(self) -> list[dict[str, Any]]:
        return [delta.to_dict() for delta in self._delta_log]

    def references_selected_target(self, value: Any) -> bool:
        """True when an effect's target/center/placement string names the marked square."""
        return normalize_id(str(value or "")) in self._SELECTED_TARGET_KEYWORDS

    def selected_target_tile(self) -> tuple[int, int] | None:
        if self.state.target_x is None or self.state.target_y is None:
            return None
        return self.state.target_x, self.state.target_y

    def selected_target_entity(self) -> Entity | None:
        """The live creature standing on the marked square, if it is still alive.
        Returns None for a bare-tile target (or once the snapshotted occupant dies)."""
        target_id = self.state.target_entity_id
        if not target_id:
            return None
        entity = self.state.entities.get(target_id)
        if entity is not None and entity.alive:
            return entity
        return None

    def _target_entity_at(self, x: int, y: int) -> Entity | None:
        """Pick the creature to bind a target to: a living actor/npc on the tile,
        preferring a non-player so clicking your own square still lets you self-cast
        only when nothing else is there."""
        occupants = [
            e
            for e in self.state.entities.values()
            if e.x == x and e.y == y and e.alive and e.kind in {"actor", "npc"}
        ]
        if not occupants:
            return None
        for entity in occupants:
            if entity.id != self.state.player_id:
                return entity
        return occupants[0]

    def set_target(self, x: int, y: int) -> bool:
        """Mark a square as the explicit spell target. Snapshots its current occupant
        (if any) so a moving creature stays bound. No turn is consumed by the caller."""
        if not self.in_bounds(x, y):
            return False
        self.state.target_x = x
        self.state.target_y = y
        occupant = self._target_entity_at(x, y)
        self.state.target_entity_id = occupant.id if occupant is not None else None
        return True

    def clear_target(self) -> None:
        self.state.target_x = None
        self.state.target_y = None
        self.state.target_entity_id = None

    def resolve_target(self, target_id: Any) -> Entity | None:
        """Resolve a single-entity reference. Accepts legacy strings (`"player"`,
        `"nearest_enemy"`, an entity id, the selected-target keywords) and the typed refs
        in refs.py, all through the shared binder."""
        return refs.bind_ref(self, refs.normalize_ref(target_id))

    def resolve_target_group(self, target_id: Any) -> list[Entity]:
        """Resolve a group reference (selectors like `all_enemies`/`allies`, a faction ref,
        or a singular tag) through the shared binder."""
        return refs.bind_group(self, refs.normalize_ref(target_id))

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
            ordered_tags = sorted(
                tags, key=lambda tag: (normalize_id(tag) not in spell_terms, tag)
            )
            affordances: list[str] = []
            for tag in ordered_tags:
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
                "smoke",
                "spice",
                "cursed",
                "death",
                "debt",
                "contract",
                "law",
                "rumor",
                "blood",
                "bone",
                "crystal",
                "glass",
                "fragile",
                "mechanical",
                "time",
                "thread",
                "snaring",
                "trap",
                "trade",
                "volatile",
                "vint",
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
            elif {"debt", "contract", "law", "rumor"} & entity.tags:
                damage_type = "force"

            terrain_tile = "mist"
            if {"fire", "hot", "flammable"} & entity.tags:
                terrain_tile = "fire"
            elif {"toxic", "acid", "fungus"} & entity.tags:
                terrain_tile = "poison_cloud"
            elif {"smoke", "spice", "ink", "rumor"} & entity.tags:
                terrain_tile = "mist"
            elif {"water", "wet", "liquid"} & entity.tags:
                terrain_tile = "water"
            elif "cold" in entity.tags:
                terrain_tile = "slick_ice"
            elif {"plant", "snaring", "rope", "silk", "thread", "cloth"} & entity.tags:
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

    def record_note(
        self,
        anchor: str,
        text: str,
        *,
        kind: str = "trait",
        source: str = "engine",
        salience: int = 3,
        ttl: int | None = None,
    ) -> None:
        """Single write-path into the semantic ledger. Combat, spells, dialogue, and trade
        all deposit facts through here so a fact minted anywhere is visible everywhere."""
        self.state.semantics.record(
            anchor,
            text,
            turn=self.state.turn,
            kind=kind,
            source=source,
            salience=salience,
            ttl=ttl,
        )

    def scene_anchors_around(
        self, cx: int, cy: int, radius: int, *, include: Iterable[Entity] = ()
    ) -> list[str]:
        """The retrieval index for a scene: the anchors whose notes should be in scope --
        every nearby living entity, the factions present, the ground underfoot, and the
        world. Entity-attached facts are already in the prompt via to_public_dict; this is
        how place/faction/world facts get gathered."""
        anchors: list[str] = []
        seen: set[str] = set()

        def add(anchor: str) -> None:
            if anchor not in seen:
                seen.add(anchor)
                anchors.append(anchor)

        for entity in include:
            add(entity_anchor(entity.id))
            if entity.faction and entity.faction not in {"neutral", ""}:
                add(faction_anchor(entity.faction))
        for entity in self.state.entities.values():
            if not entity.alive or entity.kind == "item":
                continue
            if max(abs(entity.x - cx), abs(entity.y - cy)) <= radius:
                add(entity_anchor(entity.id))
                if entity.faction and entity.faction not in {"neutral", ""}:
                    add(faction_anchor(entity.faction))
        add(place_anchor(cx, cy))
        add(WORLD_ANCHOR)
        return anchors

    def collect_scene_notes(
        self, anchors: list[str], limit: int = 8
    ) -> list[dict[str, Any]]:
        """Gather, rank, and budget the notes for a scene's anchors, ready to splice into a
        prompt. Logged as part of the call's context, so we can audit which facts surfaced."""
        notes = self.state.semantics.for_anchors(
            anchors, turn=self.state.turn, limit=limit
        )
        return [note.to_dict() for note in notes]

    def context_for_llm(self, spell: str) -> dict[str, Any]:
        """The resolver packet for one cast. Assembly lives in `state_view` so the
        resolver, replay summary, and inspection share one read-only state surface."""
        return state_view.spell_context_view(self, spell)

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
