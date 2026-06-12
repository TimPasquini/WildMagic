from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .normalize import normalize_id


PROMISE_LEDGER_LIMIT = 200
PROMISE_RESERVATION_LIMIT = 30
PROMISE_RESERVATIONS_PER_ZONE = 2

VALID_PROMISE_KINDS = {
    "rumor",
    "background",
    "place",
    "person",
    "threat",
    "quest",
    "prophecy",
    "rendezvous",
    "custom",
}

VALID_PROMISE_STATUSES = {
    "pending",
    "bound",
    "realized",
    "fulfilled",
    "contested",
    "expired",
    "unverified",
    "rumored",
    "verified",
    "false",
    "corroborated",
    "redeemed",
}

DIRECTION_WORDS = {
    "north": (0, -1),
    "south": (0, 1),
    "east": (1, 0),
    "west": (-1, 0),
    "northeast": (1, -1),
    "north east": (1, -1),
    "northwest": (-1, -1),
    "north west": (-1, -1),
    "southeast": (1, 1),
    "south east": (1, 1),
    "southwest": (-1, 1),
    "south west": (-1, 1),
}

TERRAIN_WORDS = {
    "woods": "forest",
    "wood": "forest",
    "forest": "forest",
    "marsh": "marsh",
    "swamp": "marsh",
    "river": "river",
    "road": "road",
    "hills": "hills",
    "hill": "hills",
}

BLUEPRINT_KEYWORDS = {
    "chapel": "sacred_site",
    "shrine": "sacred_site",
    "temple": "sacred_site",
    "altar": "sacred_site",
    "reliquary": "sacred_site",
    "saint": "sacred_site",
    "witch": "inhabited_site",
    "hermit": "inhabited_site",
    "sage": "inhabited_site",
    "healer": "inhabited_site",
    "dwelling": "inhabited_site",
    "hut": "inhabited_site",
    "bandit": "hostile_site",
    "bandits": "hostile_site",
    "camp": "hostile_site",
    "grave": "memorial_site",
    "barrow": "memorial_site",
    "tomb": "memorial_site",
    "cache": "hidden_site",
    "treasure": "hidden_site",
    "stash": "hidden_site",
    "lair": "creature_site",
    "beast": "creature_site",
    "creature": "creature_site",
    "investigator": "authority_site",
    "bounty": "authority_site",
    "warrant": "authority_site",
    "hearing": "authority_site",
    "flier": "authority_site",
    "checkpoint": "authority_site",
}

NPC_BLUEPRINTS = {"inhabited_site", "authority_site", "sacred_site"}

# Flesh is optional narrative decoration drafted by the background model for a bound
# promise. It never determines whether a promise exists, binds, or realizes — the
# deterministic skeleton must stand complete without it. Whitelisted keys -> max length.
FLESH_FIELDS = {
    "site_name": 60,
    "keeper_name": 40,
    "keeper_backstory": 300,
    "prop_description": 200,
    "arrival_line": 160,
}


def normalize_flesh(data: Any) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None
    flesh: dict[str, str] = {}
    for key, limit in FLESH_FIELDS.items():
        text = " ".join(str(data.get(key) or "").split()).strip().strip("\"'")
        if text:
            flesh[key] = text[:limit].strip()
    return flesh or None


@dataclass(frozen=True)
class SpatialHint:
    mode: str
    zone: tuple[int, int] | None = None
    direction: tuple[int, int] | None = None
    anchor_zone: tuple[int, int] | None = None
    terrain_tag: str | None = None
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "zone": list(self.zone) if self.zone is not None else None,
            "direction": list(self.direction) if self.direction is not None else None,
            "anchor_zone": list(self.anchor_zone) if self.anchor_zone is not None else None,
            "terrain_tag": self.terrain_tag,
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SpatialHint | None":
        if not isinstance(data, dict):
            return None
        return cls(
            mode=str(data.get("mode") or "wildcard"),
            zone=_pair(data.get("zone")),
            direction=_pair(data.get("direction")),
            anchor_zone=_pair(data.get("anchor_zone")),
            terrain_tag=str(data.get("terrain_tag") or "") or None,
            raw_text=str(data.get("raw_text") or ""),
        )


@dataclass
class PromiseBinding:
    blueprint: str
    npc_seed: dict[str, Any] | None = None
    capacity_cost: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint": self.blueprint,
            "npc_seed": dict(self.npc_seed or {}) or None,
            "capacity_cost": self.capacity_cost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PromiseBinding | None":
        if not isinstance(data, dict):
            return None
        return cls(
            blueprint=str(data.get("blueprint") or ""),
            npc_seed=dict(data.get("npc_seed") or {}) or None,
            capacity_cost=max(1, int(data.get("capacity_cost") or 1)),
        )


@dataclass
class PromiseReservation:
    promise_id: str
    zone: tuple[int, int]
    blueprint: str
    capacity_cost: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "promise_id": self.promise_id,
            "zone": list(self.zone),
            "blueprint": self.blueprint,
            "capacity_cost": self.capacity_cost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromiseReservation":
        zone = _pair(data.get("zone")) or (0, 0)
        return cls(
            promise_id=str(data.get("promise_id") or ""),
            zone=zone,
            blueprint=str(data.get("blueprint") or ""),
            capacity_cost=max(1, int(data.get("capacity_cost") or 1)),
        )


@dataclass(frozen=True)
class Objective:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": dict(self.data)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Objective | None":
        if not isinstance(data, dict):
            return None
        objective_type = normalize_id(str(data.get("type") or ""))
        if objective_type not in {"fetch", "kill", "visit", "talk"}:
            return None
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        return cls(type=objective_type, data=dict(payload))


@dataclass(frozen=True)
class Reward:
    gold: int = 0
    items: dict[str, int] = field(default_factory=dict)
    reputation: dict[str, int] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gold": self.gold,
            "items": dict(self.items),
            "reputation": dict(self.reputation),
            "flags": dict(self.flags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Reward | None":
        if not isinstance(data, dict):
            return None
        return cls(
            gold=max(0, int(data.get("gold") or 0)),
            items={normalize_id(k): max(0, int(v)) for k, v in dict(data.get("items") or {}).items()},
            reputation={normalize_id(k): int(v) for k, v in dict(data.get("reputation") or {}).items()},
            flags=dict(data.get("flags") or {}),
        )


@dataclass(frozen=True)
class QuestLogEntry:
    id: str
    name: str
    description: str
    contact: str
    location: str
    status: str


@dataclass
class WorldPromise:
    id: str
    kind: str
    subject: str
    text: str
    tags: list[str]
    source: str
    source_turn: int
    origin_zone: tuple[int, int] | None
    salience: int = 2
    confidence: float = 0.5
    # The concrete buildable thing the claim asserts (extractor's `what`); binding
    # nominates blueprints from this, the subject, and tags — never from free text.
    what: str = ""
    claimed_space: SpatialHint | None = None
    bound_space: SpatialHint | None = None
    binding: PromiseBinding | None = None
    objective: Objective | None = None
    reward: Reward | None = None
    giver_npc: str | None = None
    status: str = "unverified"
    realized_in: str | None = None
    source_message: str = ""
    source_reply: str = ""
    location: str = ""
    flesh: dict[str, str] | None = None

    @property
    def source_npc(self) -> str:
        if self.source.startswith("dialogue:"):
            return self.source.split(":", 1)[1]
        return self.source

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "text": self.text,
            "tags": list(self.tags),
            "source": self.source,
            "source_turn": self.source_turn,
            "origin_zone": list(self.origin_zone) if self.origin_zone is not None else None,
            "salience": self.salience,
            "confidence": self.confidence,
            "what": self.what,
            "claimed_space": self.claimed_space.to_dict() if self.claimed_space else None,
            "bound_space": self.bound_space.to_dict() if self.bound_space else None,
            "binding": self.binding.to_dict() if self.binding else None,
            "objective": self.objective.to_dict() if self.objective else None,
            "reward": self.reward.to_dict() if self.reward else None,
            "giver_npc": self.giver_npc,
            "status": self.status,
            "realized_in": self.realized_in,
            "source_message": self.source_message,
            "source_reply": self.source_reply,
            "location": self.location,
            "flesh": dict(self.flesh) if self.flesh else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldPromise":
        return cls(
            id=str(data.get("id") or ""),
            kind=_valid_kind(data.get("kind")),
            subject=str(data.get("subject") or "unknown"),
            text=str(data.get("text") or ""),
            tags=[normalize_id(str(tag)) for tag in data.get("tags") or [] if str(tag).strip()],
            source=str(data.get("source") or data.get("source_npc") or "unknown"),
            source_turn=int(data.get("source_turn") or 0),
            origin_zone=_pair(data.get("origin_zone")),
            salience=_clamp_int(data.get("salience"), 1, 5, 2),
            confidence=_bounded_float(data.get("confidence"), 0.0, 1.0, 0.5),
            what=str(data.get("what") or ""),
            claimed_space=SpatialHint.from_dict(data.get("claimed_space")),
            bound_space=SpatialHint.from_dict(data.get("bound_space")),
            binding=PromiseBinding.from_dict(data.get("binding")),
            objective=Objective.from_dict(data.get("objective")),
            reward=Reward.from_dict(data.get("reward")),
            giver_npc=str(data.get("giver_npc") or "") or None,
            status=_valid_status(data.get("status")),
            realized_in=str(data.get("realized_in") or data.get("redeemed_in") or "") or None,
            source_message=str(data.get("source_message") or ""),
            source_reply=str(data.get("source_reply") or ""),
            location=str(data.get("location") or ""),
            flesh=normalize_flesh(data.get("flesh")),
        )


DIRECTION_NAMES = {
    (0, -1): "north",
    (0, 1): "south",
    (1, 0): "east",
    (-1, 0): "west",
    (1, -1): "northeast",
    (-1, -1): "northwest",
    (1, 1): "southeast",
    (-1, 1): "southwest",
}


def journal_status(promise: WorldPromise) -> str:
    """Player-facing status. Binding is engine-internal and never shown."""
    if promise.status in {"fulfilled", "redeemed"}:
        return "settled"
    if promise.status in {"realized", "verified"}:
        return "found true"
    if promise.status == "corroborated":
        return "corroborated"
    if promise.status == "false":
        return "proved false"
    return "heard"


def journal_hint(promise: WorldPromise) -> str | None:
    """A soft spatial hint for the journal — never the exact zone. Promises without a
    spatial component (threats, debts, placeless rumors) simply have no hint."""
    if promise.status in {"realized", "fulfilled", "redeemed"}:
        flesh = promise.flesh or {}
        site_name = flesh.get("site_name")
        return f"found: {site_name}" if site_name else None
    space = promise.bound_space or promise.claimed_space
    if space is None:
        return None
    if space.direction in DIRECTION_NAMES:
        return f"somewhere {DIRECTION_NAMES[space.direction]} of where you heard it"
    if space.terrain_tag:
        return f"somewhere in the {space.terrain_tag}"
    return None


def journal_entry(promise: WorldPromise) -> dict[str, Any]:
    return {
        "id": promise.id,
        "kind": promise.kind,
        "subject": promise.subject,
        "text": promise.text,
        "source": promise.source_npc,
        "status": journal_status(promise),
        "hint": journal_hint(promise),
        "turn": promise.source_turn,
    }


def promise_context_for_prompt(promises: list[WorldPromise], limit: int = 8, text_limit: int = 240) -> list[dict[str, Any]]:
    ranked = sorted(
        promises,
        key=lambda promise: (
            promise.status in {"realized", "fulfilled", "redeemed"},
            -promise.salience,
            -promise.confidence,
            promise.source_turn,
            promise.subject.lower(),
        ),
    )
    return [
        {
            "id": promise.id,
            "kind": promise.kind,
            "subject": promise.subject,
            "text": _clean_text(promise.text, text_limit),
            "source": promise.source,
            "source_npc": promise.source_npc,
            "status": promise.status,
            "salience": promise.salience,
            "tags": list(promise.tags),
            "realized_in": promise.realized_in,
        }
        for promise in ranked[:limit]
    ]


def bind_promise(
    promise: WorldPromise,
    *,
    explored_zones: set[tuple[int, int]],
    reserved_counts: dict[tuple[int, int], int] | None = None,
) -> PromiseReservation | None:
    if promise.binding is not None:
        if promise.bound_space and promise.bound_space.zone:
            return PromiseReservation(promise.id, promise.bound_space.zone, promise.binding.blueprint, promise.binding.capacity_cost)
        return None
    if promise.confidence < 0.4 or promise.salience <= 1:
        return None
    # Always-honor eligibility gate: the world only commits to build what was concretely
    # claimed to exist — a named buildable thing (`what`) or a real spatial hint. Talk
    # that names neither stays flavor lore. Quests are exempt only when structurally
    # trusted (engine-authored, carrying a typed objective) — an extractor merely
    # *labeling* chatter "quest" does not skip the gate.
    trusted_quest = promise.kind == "quest" and promise.objective is not None
    if not trusted_quest and not promise.what.strip():
        spatial = promise.claimed_space
        if spatial is None or spatial.mode not in {"direction", "terrain", "zone"}:
            return None
    blueprint = match_blueprint(promise)
    if blueprint is None:
        return None
    anchor_zone = promise.origin_zone or (0, 0)
    claimed = parse_spatial_hint(
        promise.claimed_space.raw_text if promise.claimed_space else "",
        fallback_text=f"{promise.location} {promise.subject} {promise.text} {' '.join(promise.tags)}",
        anchor_zone=anchor_zone,
    )
    bound_zone = choose_bound_zone(
        claimed,
        explored_zones=explored_zones,
        reserved_counts=reserved_counts or {},
        quest=promise.kind == "quest",
    )
    bound_space = claimed
    if bound_zone is not None:
        bound_space = SpatialHint(
            mode="zone",
            zone=bound_zone,
            direction=claimed.direction,
            anchor_zone=claimed.anchor_zone,
            terrain_tag=claimed.terrain_tag,
            raw_text=claimed.raw_text,
        )
    npc_seed = None
    if blueprint in NPC_BLUEPRINTS:
        npc_seed = {"subject": promise.subject, "tags": list(promise.tags), "source": promise.text}
    promise.claimed_space = promise.claimed_space or claimed
    promise.bound_space = bound_space
    promise.binding = PromiseBinding(blueprint=blueprint, npc_seed=npc_seed)
    promise.status = "bound"
    if bound_zone is None:
        return None
    return PromiseReservation(promise.id, bound_zone, blueprint)


def match_blueprint(promise: WorldPromise) -> str | None:
    # Only the parts of the claim that name a thing — the extractor's `what`, the
    # subject, and tags — can nominate a blueprint, and only on whole words. Free text
    # and substrings are excluded so philosophy, fetch requests, and trade chatter stay
    # flavor: talk of "saints" is not a chapel, an "Imperial Campaign Map" is not a
    # camp, and "passage" is not a sage.
    haystack = " ".join([promise.what, promise.subject, *promise.tags]).lower()
    words = set(re.findall(r"[a-z][a-z_'-]*", haystack))
    words |= {word[:-1] for word in words if word.endswith("s") and len(word) > 3}
    for keyword, blueprint in BLUEPRINT_KEYWORDS.items():
        if keyword in words:
            return blueprint
    return None


def parse_spatial_hint(raw_text: str | None, *, fallback_text: str, anchor_zone: tuple[int, int]) -> SpatialHint:
    text = " ".join([str(raw_text or ""), fallback_text]).lower()
    for phrase, direction in sorted(DIRECTION_WORDS.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return SpatialHint(mode="direction", direction=direction, anchor_zone=anchor_zone, raw_text=str(raw_text or phrase))
    for phrase, terrain in TERRAIN_WORDS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return SpatialHint(mode="terrain", terrain_tag=terrain, anchor_zone=anchor_zone, raw_text=str(raw_text or phrase))
    return SpatialHint(mode="wildcard", anchor_zone=anchor_zone, raw_text=str(raw_text or ""))


def choose_bound_zone(
    claimed: SpatialHint,
    *,
    explored_zones: set[tuple[int, int]],
    reserved_counts: dict[tuple[int, int], int],
    quest: bool = False,
) -> tuple[int, int] | None:
    anchor = claimed.anchor_zone or (0, 0)
    if claimed.mode == "zone" and claimed.zone is not None:
        candidates = [claimed.zone]
    elif claimed.mode == "direction" and claimed.direction is not None:
        dx, dy = claimed.direction
        candidates = [(anchor[0] + dx * distance, anchor[1] + dy * distance) for distance in range(1, 8)]
    elif claimed.mode == "terrain":
        candidates = _ring_candidates(anchor)
    elif claimed.mode == "wildcard":
        candidates = _ring_candidates(anchor)
    else:
        return None
    for zone in candidates:
        if zone in explored_zones:
            continue
        if quest or reserved_counts.get(zone, 0) < PROMISE_RESERVATIONS_PER_ZONE:
            return zone
    return None


def _ring_candidates(anchor: tuple[int, int]) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []
    for radius in range(1, 5):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                candidates.append((anchor[0] + dx, anchor[1] + dy))
    return candidates


def _valid_kind(value: Any) -> str:
    kind = normalize_id(str(value or "rumor")) or "rumor"
    return kind if kind in VALID_PROMISE_KINDS else "custom"


def _valid_status(value: Any) -> str:
    status = normalize_id(str(value or "unverified")) or "unverified"
    return status if status in VALID_PROMISE_STATUSES else "unverified"


def _pair(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return (int(value[0]), int(value[1]))
    except (TypeError, ValueError):
        return None


def _bounded_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit].strip()
