"""World generation — the per-run geopolitical roll (Phase C, slice 1).

This module rolls the *political* map for a run: it places the core nations on the overworld
zone grid in relation to one another, picks which old kingdom is the Empire's free **rival**
this run, and seeds the :class:`~wildmagic.factions.FactionLedger` from that placement.

It is the "broad strokes up front" layer of the wavefunction-collapse model: a realm's
**territory** is canon from the start of the run, while each zone's **interior** still
generates lazily on first entry (``generation._generate_open_zone``) and ``WorldPromise``
"yes-and" still drops new sites into unexplored cells. The three layers are orthogonal — the
world map only says *which realm owns a cell*.

Identities and character never roll (the canon roster lives in ``docs/WORLDBUILDING.md``);
only **roles** (which kingdoms are conquered vs. the rival), **rulers**, and **placement** do.
Every roll draws from ``stable_seed(rng_seed, "world_roll")`` so a given run seed always yields
the same world and replays re-derive it with zero model calls.

Two hard geographic rules from the world bible are enforced as invariants (see tests):

* the three conquered old kingdoms are always **adjacent to Vigovia**;
* the one free **rival** is placed **far** — never adjacent to the imperial bloc.

See ``docs/WORLD_GENERATION.md`` and ``EMERGENT_WORLD_IMPLEMENTATION.md`` §0.1 / §0.4.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .determinism import stable_seed
from .factions import (
    EMPIRE_DEFENSE_START,
    EMPIRE_PATROLS_START,
    REBELLION_CELLS_START,
    REBELLION_NAME,
    Faction,
    FactionLedger,
    faction_anchor,
)

Cell = tuple[int, int]


# --- The fixed roster (identities never roll) -------------------------------------------
# Canon names/flavor from docs/WORLDBUILDING.md. `glyph` is the survey-map letter (unique);
# `role_pool` constrains which geopolitical roles a realm can be rolled into.


@dataclass(frozen=True)
class RealmTemplate:
    id: str
    name: str
    glyph: str
    blurb: str
    tradition: (
        str  # the realm's living magical dialect (one wild substrate, cultural accent)
    )
    character_tags: tuple[str, ...]
    role_pool: frozenset[str]
    voice: str  # region-voice handle (WORLDBUILDING "Naming & Voice")


@dataclass(frozen=True)
class Ruler:
    name: str
    disposition: str
    traits: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "disposition": self.disposition,
            "traits": list(self.traits),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Ruler":
        return cls(
            name=str(raw.get("name", "")),
            disposition=str(raw.get("disposition", "")),
            traits=tuple(str(t) for t in raw.get("traits", [])),
        )


_OLD_KINGDOM = frozenset({"conquered", "rival"})

REALM_TEMPLATES: dict[str, RealmTemplate] = {
    t.id: t
    for t in (
        RealmTemplate(
            id="vigovia",
            name="Vigovia",
            glyph="V",
            blurb="Vigo the Lawgiver's heartland — the charter, the Censorate, the emperor's seat.",
            tradition="charter",
            character_tags=("imperial", "charter", "bureaucratic", "heartland"),
            role_pool=frozenset({"founding"}),
            voice="vigovia",
        ),
        RealmTemplate(
            id="stalnaz",
            name="Stalnaz",
            glyph="S",
            blurb="The queendom of crystal and song; the realm others measure their refinement against.",
            tradition="crystal",
            character_tags=("crystal", "music", "queen", "art"),
            role_pool=_OLD_KINGDOM,
            voice="stalnaz",
        ),
        RealmTemplate(
            id="brall",
            name="Brall",
            glyph="B",
            blurb="The holds of bone and ale, ruled by the Bone Jarls' council.",
            tradition="bone",
            character_tags=("bone", "ale", "scrimshaw", "jarls"),
            role_pool=_OLD_KINGDOM,
            voice="brall",
        ),
        RealmTemplate(
            id="ryolan",
            name="Ryolan",
            glyph="R",
            blurb="The kingdom of honor, blood magic, and the duel.",
            tradition="blood",
            character_tags=("blood", "honor", "duel", "chariots"),
            role_pool=_OLD_KINGDOM,
            voice="ryolan",
        ),
        RealmTemplate(
            id="vint",
            name="Vint",
            glyph="W",
            blurb="The woven republic, famed for gossip, intrigue, and tapestry-charms.",
            tradition="woven",
            character_tags=("woven", "gossip", "republic", "tapestry"),
            role_pool=_OLD_KINGDOM,
            voice="vint",
        ),
        RealmTemplate(
            id="threen",
            name="Threen",
            glyph="T",
            blurb="The client kingdom of canals — independent on paper, imperial in fact.",
            tradition="canal",
            character_tags=("canals", "artisans", "literature", "client"),
            role_pool=frozenset({"proxy"}),
            voice="threen",
        ),
    )
}

#: The four old kingdoms, in canonical order (one becomes the rival, three conquered).
OLD_KINGDOM_IDS: tuple[str, ...] = ("stalnaz", "brall", "ryolan", "vint")

#: The emperor never rolls (a fixed entity; the win target, §0.5).
EMPEROR = Ruler(
    name="the Emperor",
    disposition="implacable",
    traits=("lawful", "distant", "sincere-and-self-serving"),
)

#: Per-realm ruler titles (the *who*; disposition is rolled). Vigovia uses the fixed EMPEROR.
RULER_TITLES: dict[str, str] = {
    "stalnaz": "the Queen of Stalnaz",
    "brall": "the Bone Jarls of Brall",
    "ryolan": "the King of Ryolan",
    "vint": "the Vintan Assembly",
    "threen": "the Doge of Threen",
}

_RULER_DISPOSITIONS: tuple[str, ...] = (
    "zealous",
    "pragmatic",
    "weary",
    "ambitious",
    "proud",
    "cautious",
)

#: role → factions.FACTION_KINDS (the existing kingdom kinds, §0.1).
ROLE_TO_KIND: dict[str, str] = {
    "founding": "empire_core",
    "conquered": "conquered",
    "proxy": "proxy",
    "rival": "rival",
}

_ROLE_MOOD: dict[str, str] = {
    "founding": "orderly",
    "conquered": "occupied",
    "proxy": "deferent",
    "rival": "defiant",
}

#: role → how strongly the Grand Empire holds a zone (feeds zone_type + the imperial spawn
#: pool, replacing the old fixed NE gradient). None → unowned wilds.
_ROLE_DENSITY: dict[str, float] = {
    "founding": 0.9,
    "conquered": 0.7,
    "proxy": 0.55,
    "rival": 0.1,
}


def imperial_density_for_role(role: str | None) -> float:
    """How strongly the Empire holds a zone of a realm in ``role`` (0..1). Unowned wilds
    (``role is None``) read low. Consumed by ``generation._imperial_density``."""
    if role is None:
        return 0.2
    return _ROLE_DENSITY.get(role, 0.2)


# --- The macro layout -------------------------------------------------------------------
# The world is a small bounded grid of territory *blocks*; each block expands into a
# REALM_BLOCK_SIZE x REALM_BLOCK_SIZE patch of playable zone cells. The player starts at
# zone (0,0), inside the block anchored at macro (0,0) — always a *conquered* frontier realm
# (the occupied marches the game opens on). Positions are fixed; *which* realm fills each
# slot (and which old kingdom is the rival) is rolled. Rotation preserves adjacency, so the
# two hard invariants hold for any roll.

REALM_BLOCK_SIZE = 2
_WORLD_BOUNDS = (-4, -4, 4, 4)

_CAPITAL_POS: Cell = (1, 0)
#: The three capital-adjacent conquered slots. Index 0 == (0,0) holds the player's start.
_CONQUERED_POS: tuple[Cell, ...] = ((0, 0), (2, 0), (1, 1))
_PROXY_POS: Cell = (0, 1)  # client kingdom, near the bloc but not capital-adjacent
#: Far slots, each isolated from the imperial bloc by wilds — the rival lands in one.
_RIVAL_CANDIDATES: tuple[Cell, ...] = ((3, 1), (2, 2), (3, 2))
_MACRO_BOUNDS = (0, 0, 3, 2)  # min_c, min_r, max_c, max_r → a 4×3 block grid
_RAW_WIDTH = (_MACRO_BOUNDS[2] + 1) * REALM_BLOCK_SIZE
_RAW_HEIGHT = (_MACRO_BOUNDS[3] + 1) * REALM_BLOCK_SIZE
_DIHEDRAL_TRANSFORMS = 8


def _block_cells(macro: Cell) -> list[Cell]:
    """The zone cells covered by a macro block, NW-first (NW cell is the seat/anchor)."""
    c, r = macro
    s = REALM_BLOCK_SIZE
    return [(c * s + dx, r * s + dy) for dy in range(s) for dx in range(s)]


def _transform_cell(cell: Cell, transform: int) -> Cell:
    x, y = cell
    w, h = _RAW_WIDTH, _RAW_HEIGHT
    match transform % _DIHEDRAL_TRANSFORMS:
        case 0:
            return (x, y)
        case 1:
            return (h - 1 - y, x)
        case 2:
            return (w - 1 - x, h - 1 - y)
        case 3:
            return (y, w - 1 - x)
        case 4:
            return (w - 1 - x, y)
        case 5:
            return (y, x)
        case 6:
            return (x, h - 1 - y)
        case _:
            return (h - 1 - y, w - 1 - x)


def _transform_dimensions(transform: int) -> tuple[int, int]:
    if transform % _DIHEDRAL_TRANSFORMS in {1, 3, 5, 7}:
        return (_RAW_HEIGHT, _RAW_WIDTH)
    return (_RAW_WIDTH, _RAW_HEIGHT)


def _world_offset(transform: int) -> Cell:
    min_x, min_y, max_x, max_y = _WORLD_BOUNDS
    world_w = max_x - min_x + 1
    world_h = max_y - min_y + 1
    map_w, map_h = _transform_dimensions(transform)
    return (min_x + (world_w - map_w) // 2, min_y + (world_h - map_h) // 2)


def _offset_cell(cell: Cell, transform: int) -> Cell:
    tx, ty = _transform_cell(cell, transform)
    ox, oy = _world_offset(transform)
    return (tx + ox, ty + oy)


def _offset_cells(cells: list[Cell], transform: int) -> frozenset[Cell]:
    return frozenset(_offset_cell(cell, transform) for cell in cells)


@dataclass
class RealmPlacement:
    realm_id: str
    role: str
    faction_id: str
    cells: frozenset[Cell]
    is_capital_seat: bool
    ruler: Ruler

    def to_dict(self) -> dict[str, Any]:
        return {
            "realm_id": self.realm_id,
            "role": self.role,
            "faction_id": self.faction_id,
            "cells": sorted([list(c) for c in self.cells]),
            "is_capital_seat": self.is_capital_seat,
            "ruler": self.ruler.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RealmPlacement":
        return cls(
            realm_id=str(raw.get("realm_id", "")),
            role=str(raw.get("role", "")),
            faction_id=str(raw.get("faction_id", "")),
            cells=frozenset(
                (int(c[0]), int(c[1]))
                for c in raw.get("cells", [])
                if isinstance(c, (list, tuple)) and len(c) >= 2
            ),
            is_capital_seat=bool(raw.get("is_capital_seat", False)),
            ruler=Ruler.from_dict(raw.get("ruler") or {}),
        )


@dataclass
class WorldMap:
    """The rolled political map for a run. Serialized within a run (never between runs);
    also re-derivable from the seed, so replays reproduce it without recording it."""

    rival_realm_id: str
    capital_zone: Cell
    placements: dict[str, RealmPlacement]
    cell_to_realm: dict[Cell, str]
    bounds: tuple[int, int, int, int]  # min_x, min_y, max_x, max_y of the named world

    def realm_at(self, zx: int, zy: int) -> str | None:
        """The realm id owning a zone, or ``None`` for unowned wilds."""
        return self.cell_to_realm.get((zx, zy))

    def contains(self, zx: int, zy: int) -> bool:
        """Whether a zone lies inside the finite world map."""
        min_x, min_y, max_x, max_y = self.bounds
        return min_x <= zx <= max_x and min_y <= zy <= max_y

    def role_at(self, zx: int, zy: int) -> str | None:
        """The geopolitical role owning a zone (founding/conquered/proxy/rival), or None."""
        realm_id = self.cell_to_realm.get((zx, zy))
        return self.placements[realm_id].role if realm_id else None

    def placement_at(self, zx: int, zy: int) -> RealmPlacement | None:
        realm_id = self.cell_to_realm.get((zx, zy))
        return self.placements.get(realm_id) if realm_id else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rival_realm_id": self.rival_realm_id,
            "capital_zone": list(self.capital_zone),
            "placements": {rid: pl.to_dict() for rid, pl in self.placements.items()},
            "cell_to_realm": {
                f"{x},{y}": rid for (x, y), rid in self.cell_to_realm.items()
            },
            "bounds": list(self.bounds),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorldMap":
        placements = {
            str(rid): RealmPlacement.from_dict(item)
            for rid, item in (raw.get("placements") or {}).items()
            if isinstance(item, dict)
        }
        cell_to_realm: dict[Cell, str] = {}
        for key, rid in (raw.get("cell_to_realm") or {}).items():
            try:
                sx, sy = str(key).split(",")
                cell_to_realm[(int(sx), int(sy))] = str(rid)
            except (ValueError, TypeError):
                continue
        cap = raw.get("capital_zone") or (0, 0)
        bounds = raw.get("bounds") or (0, 0, 0, 0)
        return cls(
            rival_realm_id=str(raw.get("rival_realm_id", "")),
            capital_zone=(int(cap[0]), int(cap[1])),
            placements=placements,
            cell_to_realm=cell_to_realm,
            bounds=tuple(int(b) for b in bounds),  # type: ignore[arg-type]
        )


def realm_card_for_zone(world: WorldMap, zx: int, zy: int) -> dict[str, Any]:
    placement = world.placement_at(zx, zy)
    if placement is None:
        return {
            "zone": [zx, zy],
            "realm_id": None,
            "name": "uncharted wilds",
            "role": None,
        }
    template = REALM_TEMPLATES.get(placement.realm_id)
    return {
        "zone": [zx, zy],
        "realm_id": placement.realm_id,
        "name": template.name if template else placement.realm_id,
        "role": placement.role,
        "faction_id": placement.faction_id,
        "tradition": template.tradition if template else "",
        "voice": template.voice if template else "",
        "tags": list(template.character_tags) if template else [],
        "ruler": placement.ruler.to_dict(),
    }


def _roll_ruler(realm_id: str, rng: random.Random) -> Ruler:
    if realm_id == "vigovia":
        return EMPEROR
    return Ruler(
        name=RULER_TITLES.get(realm_id, f"the ruler of {realm_id}"),
        disposition=rng.choice(_RULER_DISPOSITIONS),
        traits=(),
    )


def _faction_id_for(realm_id: str, role: str) -> str:
    # The founding heartland anchors the empire bloc as id "empire" (back-compat: every
    # literal "empire" reference + the defense/patrol resources keep working).
    return "empire" if role == "founding" else realm_id


def roll_world(seed: int | None) -> WorldMap:
    """Roll the run's political map from ``seed`` (deterministic). Picks the rival, assigns
    roles, rolls rulers, and places every core realm on the bounded zone grid."""
    rng = random.Random(stable_seed(seed, "world_roll"))

    kingdoms = list(OLD_KINGDOM_IDS)
    rng.shuffle(kingdoms)
    rival_id = kingdoms[0]
    conquered_ids = kingdoms[1:]
    rng.shuffle(conquered_ids)
    rival_pos = rng.choice(_RIVAL_CANDIDATES)
    transform = rng.randrange(_DIHEDRAL_TRANSFORMS)

    placements: dict[str, RealmPlacement] = {}
    cell_to_realm: dict[Cell, str] = {}

    def place(realm_id: str, role: str, macro: Cell, capital: bool = False) -> None:
        cells = _offset_cells(_block_cells(macro), transform)
        placements[realm_id] = RealmPlacement(
            realm_id=realm_id,
            role=role,
            faction_id=_faction_id_for(realm_id, role),
            cells=cells,
            is_capital_seat=capital,
            ruler=_roll_ruler(realm_id, rng),
        )
        for cell in cells:
            cell_to_realm[cell] = realm_id

    place("vigovia", "founding", _CAPITAL_POS, capital=True)
    for realm_id, macro in zip(conquered_ids, _CONQUERED_POS):
        place(realm_id, "conquered", macro)
    place("threen", "proxy", _PROXY_POS)
    place(rival_id, "rival", rival_pos)

    capital_zone = _offset_cell(_block_cells(_CAPITAL_POS)[0], transform)
    bounds = _WORLD_BOUNDS

    return WorldMap(
        rival_realm_id=rival_id,
        capital_zone=capital_zone,
        placements=placements,
        cell_to_realm=cell_to_realm,
        bounds=bounds,
    )


WORLD_SCENARIOS: frozenset[str] = frozenset(
    {"frontier", "town", "bazaar", "warren", "archive"}
)

_SCENARIO_REALM_STARTS: dict[str, str] = {
    # Current start hubs are scaffolding for the later four-entry-city design, but they
    # already occupy distinct kingdoms so a run can be read as one connected overworld.
    "town": "ryolan",
    "bazaar": "vint",
    "warren": "brall",
    "archive": "stalnaz",
}


def scenario_uses_world_map(scenario: str) -> bool:
    """Whether a scenario should receive the rolled political world map."""
    return scenario in WORLD_SCENARIOS


def start_zone_for_scenario(world: WorldMap, scenario: str) -> Cell:
    """Deterministic overworld start coordinate for a scenario.

    The four current start hubs are placed in distinct old kingdoms; ``frontier`` starts
    in an unowned central wild zone on the survey board, not at (0,0).
    """
    if scenario == "frontier":
        for candidate in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            if world.contains(*candidate) and world.realm_at(*candidate) is None:
                return candidate
        return (world.bounds[0], world.bounds[1])
    realm_id = _SCENARIO_REALM_STARTS.get(scenario)
    placement = world.placements.get(realm_id or "")
    if placement is None:
        placement = next(
            iter(sorted(world.placements.values(), key=lambda pl: pl.realm_id))
        )
    for cell in sorted(placement.cells):
        if cell != world.capital_zone and cell != (0, 0):
            return cell
    return sorted(placement.cells)[0]


def seed_factions_from_world(world: WorldMap) -> FactionLedger:
    """Seed a :class:`FactionLedger` from a rolled world. Vigovia anchors the empire bloc as
    id ``"empire"``; conquered/proxy/rival realms each get their own faction; the cross-cutting
    ``"rebellion"`` resistance pole (the Unbound — not a placed realm) is kept so existing
    pressure/backlash/gratitude code is untouched. Replaces ``seed_phase0_factions`` for
    world-bearing runs."""
    ledger = FactionLedger()
    for realm_id, pl in sorted(world.placements.items()):
        template = REALM_TEMPLATES[realm_id]
        home = sorted(pl.cells)
        if pl.role == "founding":
            ledger.add(
                Faction(
                    id="empire",
                    name="the Grand Empire of Vigovia",
                    kind="empire_core",
                    standing={"imperial_threat": 0.0},
                    mood="orderly",
                    resources={
                        "defense": EMPIRE_DEFENSE_START,
                        "patrols": EMPIRE_PATROLS_START,
                    },
                    home_zones=home,
                    notes_anchor=faction_anchor("empire"),
                )
            )
        else:
            ledger.add(
                Faction(
                    id=pl.faction_id,
                    name=template.name,
                    kind=ROLE_TO_KIND[pl.role],
                    standing={},
                    mood=_ROLE_MOOD.get(pl.role, "watchful"),
                    resources={},
                    home_zones=home,
                    notes_anchor=faction_anchor(pl.faction_id),
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


# --- Survey map (the coarse political board the player can read from the start) ----------

_ROLE_LABEL: dict[str, str] = {
    "founding": "the Empire's heartland",
    "conquered": "conquered by the Empire",
    "proxy": "client kingdom",
    "rival": "the free RIVAL",
}
_LEGEND_ORDER: dict[str, int] = {"founding": 0, "conquered": 1, "proxy": 2, "rival": 3}


def world_map_strings(
    world: WorldMap,
    current_zone: Cell | None = None,
    visited: set[Cell] | None = None,
) -> list[str]:
    """Render the coarse political survey map: realm territories, the capital, the rival, and
    the player's current zone. Shows the *political* map (known from the start) but never a
    zone's interior (unknown until visited). Uppercase = ground you've walked; lowercase =
    territory known but not yet entered (the wavefunction-collapse boundary)."""
    min_x, min_y, max_x, max_y = world.bounds
    visited = visited or set()
    lines = ["The Known World", ""]
    for y in range(min_y, max_y + 1):
        row: list[str] = []
        for x in range(min_x, max_x + 1):
            cell = (x, y)
            if current_zone is not None and cell == current_zone:
                row.append("@")
                continue
            if cell == world.capital_zone:
                row.append("*")
                continue
            realm_id = world.realm_at(x, y)
            if realm_id is None:
                row.append(".")
                continue
            glyph = REALM_TEMPLATES[realm_id].glyph
            row.append(glyph if cell in visited else glyph.lower())
        lines.append("  " + " ".join(row))
    lines.append("")
    for realm_id, pl in sorted(
        world.placements.items(),
        key=lambda kv: (_LEGEND_ORDER.get(kv[1].role, 9), kv[0]),
    ):
        template = REALM_TEMPLATES[realm_id]
        label = _ROLE_LABEL.get(pl.role, pl.role)
        seat = "  ✦ capital" if pl.is_capital_seat else ""
        lines.append(
            f"  {template.glyph}  {template.name} — {label} (ruled by {pl.ruler.name}){seat}"
        )
    lines.append("")
    lines.append("  *  the imperial capital    @  you    .  uncharted wilds")
    lines.append("  (lowercase = territory known but not yet entered)")
    return lines
