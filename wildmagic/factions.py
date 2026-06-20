"""Factions — the powers the world's standing/resources are tracked against.

A Faction is a power the player can affect: a kingdom, the Empire bloc, a resistance, a
guild, or (Phase F) a player-founded organization. Each carries **multidimensional
standing** toward the player (an open set of axes — notoriety, fear, gratitude, …) and a
pool of spendable **resources** it acts through (Phase B+).

Phase 0 seeds just two poles — the Empire and one rebel faction — with a 2-axis standing
(``imperial_threat`` on the Empire, ``gratitude`` on the rebels). The full rolled roster
(fixed kingdoms in rolled roles, §0.1) replaces this scaffold in Phase C.

The ``FactionLedger`` is serialized inside a run but **never carried between runs** (a new
run rolls fresh factions — no meta-progression).

See `EMERGENT_WORLD_IMPLEMENTATION.md` §1.2 / §0.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


#: The standing axes a faction can hold toward the player (strategy §5.1). Open-ended —
#: the world roll may add run-specific axes — but this is the curated core vocabulary.
STANDING_AXES: tuple[str, ...] = (
    "notoriety",
    "fear",
    "gratitude",
    "legitimacy",
    "uncanniness",
    "imperial_threat",
)

#: The kinds a faction can be (§0.1). ``empire_core``/``conquered``/``proxy`` make up the
#: Empire bloc; ``player_org`` is a Phase-F player-founded organization.
FACTION_KINDS: tuple[str, ...] = (
    "empire_core",
    "conquered",
    "proxy",
    "rival",
    "independent",
    "resistance",
    "guild",
    "cult",
    "player_org",
)


# --- Placeholder names (swap-point for worldbuilding, D1) ---------------------------
# These are deliberately the *only* place the Phase-0 faction names live, so they can be
# renamed in one edit when lore lands (and Phase C's world roll supplies real names per
# the fixed kingdom roster). Keep new placeholders here too.
EMPIRE_NAME = "the Grand Empire"
REBELLION_NAME = "the Unbound"

#: The Empire's defensive pool — the legions, patrols, sealed capital, and guard that keep
#: the emperor unreachable (D9, §0.5). Pressure (the player's imperial_threat) spends it
#: down each day; when it hits zero the path to the emperor opens. Small for the
#: fast-escalation start; Phase C/D calibrate it.
EMPIRE_DEFENSE_START = 20

#: Action resources factions *spend to act* (Phase D backlash, strategy §5.2): a crackdown
#: is not "fear > 70", it is "the Empire spends a patrol". Finite, with slow daily regen,
#: so reactions ebb and flow — an overspent faction goes quiet for a while.
EMPIRE_PATROLS_START = 3
REBELLION_CELLS_START = 3


#: Stable *roles* the emergent systems target, mapped to the faction kinds that fill them.
#: Downstream code (deed consequences, backlash, bonds, Phase F orgs) references roles, not
#: literal faction ids, so it generalizes the moment Phase C's world roll seeds a full
#: roster (multiple empire-bloc kingdoms, several resistances). "the Empire" is the bloc.
ROLE_TO_KINDS: dict[str, tuple[str, ...]] = {
    "empire": ("empire_core", "conquered", "proxy"),
    "resistance": ("resistance",),
    "rival": ("rival",),
    "independent": ("independent",),
    "player_org": ("player_org",),
}


def faction_anchor(faction_id: str) -> str:
    """Anchor key for this faction's notes in the semantic ledger (prose mirror)."""
    return f"faction:{faction_id}"


def resolve_faction(tags: set[str], kind: str, ledger: "FactionLedger") -> str:
    """Resolve a victim (by its combat tags and entity kind) to the faction whose member it
    was, for per-faction kill accounting (`FACTION_KILL_REPUTATION.md` K1). Returns, in order
    of specificity: the **faction-ledger id** when one is tagged directly (e.g. a Phase-C
    conquered kingdom's own id); the **primary faction of a tagged role** otherwise (so an
    ``empire``-tagged soldier resolves to the empire bloc's lead faction); the ``civilian``
    bucket for an unaligned person; and ``""`` for an unaligned creature — beasts are not
    politics and stay tally-exempt. Pure: depends only on the tags, kind, and current roster,
    so it generalizes automatically as the world roll seeds more factions."""
    for faction_id in ledger.factions:
        if faction_id in tags:
            return faction_id
    for role in ROLE_TO_KINDS:
        if role in tags:
            primary = ledger.primary(role)
            if primary is not None:
                return primary.id
    if kind == "npc" or "civilian" in tags:
        return "civilian"
    return ""


@dataclass
class Faction:
    id: str
    name: str
    kind: str  # one of FACTION_KINDS
    standing: dict[str, float] = field(default_factory=dict)  # axis -> value (open set)
    mood: str = "watchful"
    resources: dict[str, int] = field(
        default_factory=dict
    )  # spendable pools (Phase B+)
    goals: list[str] = field(default_factory=list)
    home_zones: list[tuple[int, int]] = field(default_factory=list)
    player_rank: str | None = None  # set if the player leads/has climbed this org
    notes_anchor: str = ""

    def standing_of(self, axis: str) -> float:
        return self.standing.get(axis, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "standing": dict(self.standing),
            "mood": self.mood,
            "resources": dict(self.resources),
            "goals": list(self.goals),
            "home_zones": [list(z) for z in self.home_zones],
            "player_rank": self.player_rank,
            "notes_anchor": self.notes_anchor,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Faction":
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            kind=str(raw.get("kind", "independent")),
            standing={
                str(axis): float(v) for axis, v in (raw.get("standing") or {}).items()
            },
            mood=str(raw.get("mood", "watchful")),
            resources={str(k): int(v) for k, v in (raw.get("resources") or {}).items()},
            goals=[str(g) for g in raw.get("goals", [])],
            home_zones=[
                (int(z[0]), int(z[1]))
                for z in raw.get("home_zones", [])
                if isinstance(z, (list, tuple)) and len(z) >= 2
            ],
            player_rank=(
                str(raw["player_rank"]) if raw.get("player_rank") is not None else None
            ),
            notes_anchor=str(raw.get("notes_anchor", "")),
        )


@dataclass
class FactionLedger:
    factions: dict[str, Faction] = field(default_factory=dict)

    def get(self, faction_id: str) -> Faction | None:
        return self.factions.get(faction_id)

    def add(self, faction: Faction) -> Faction:
        self.factions[faction.id] = faction
        return faction

    def by_kind(self, *kinds: str) -> list[Faction]:
        wanted = set(kinds)
        return sorted(
            (f for f in self.factions.values() if f.kind in wanted),
            key=lambda f: f.id,
        )

    def ids_by_role(self, role: str) -> list[str]:
        """All faction ids filling a stable role (§0.1). Deterministic order. Empty if no
        faction fills the role yet."""
        return [f.id for f in self.by_kind(*ROLE_TO_KINDS.get(role, ()))]

    def primary(self, role: str) -> Faction | None:
        """The lead faction for a role — e.g. the empire core, or the main resistance. The
        first by id, deterministically; None if the role is unfilled."""
        members = self.by_kind(*ROLE_TO_KINDS.get(role, ()))
        return members[0] if members else None

    def adjust_standing(self, faction_id: str, axis: str, delta: float) -> float:
        """Accumulate ``delta`` on a faction's standing axis. Axes are open scales in
        Phase 0 (clamping/normalization is Phase B). Returns the new value; a no-op for
        unknown factions."""
        faction = self.factions.get(faction_id)
        if faction is None:
            return 0.0
        faction.standing[axis] = faction.standing.get(axis, 0.0) + delta
        return faction.standing[axis]

    def spend(self, faction_id: str, resource: str, n: int) -> bool:
        """Spend ``n`` of a resource if available (Phase B+ uses this to gate events).
        Returns True if the spend succeeded."""
        faction = self.factions.get(faction_id)
        if faction is None or faction.resources.get(resource, 0) < n:
            return False
        faction.resources[resource] -= n
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "factions": {
                fid: faction.to_dict() for fid, faction in self.factions.items()
            }
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FactionLedger":
        return cls(
            factions={
                str(fid): Faction.from_dict(item)
                for fid, item in (raw or {}).get("factions", {}).items()
                if isinstance(item, dict)
            }
        )


def seed_phase0_factions() -> FactionLedger:
    """The Phase-0 two-pole scaffold: the Empire bloc and one rebel pole, each with the
    single standing axis the micro-loop moves. Placeholder names (D1). Phase C's world
    roll replaces this with the full rolled roster."""
    ledger = FactionLedger()
    ledger.add(
        Faction(
            id="empire",
            name=EMPIRE_NAME,
            kind="empire_core",
            standing={"imperial_threat": 0.0},
            mood="orderly",
            resources={
                "defense": EMPIRE_DEFENSE_START,
                "patrols": EMPIRE_PATROLS_START,
            },
            notes_anchor=faction_anchor("empire"),
        )
    )
    ledger.add(
        Faction(
            id="rebellion",
            name=REBELLION_NAME,
            kind="resistance",
            standing={"gratitude": 0.0},
            mood="hopeful",
            resources={"cells": REBELLION_CELLS_START},
            notes_anchor=faction_anchor("rebellion"),
        )
    )
    return ledger
