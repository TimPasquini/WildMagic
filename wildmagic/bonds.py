"""Bonds — every NPC's *personal* relationship to the player.

The strategy is emphatic (§5.3): don't build a "party" system, build a few general
primitives whose interaction produces richness, and don't special-case the player. The
three layers are kept strictly orthogonal:

  1. **Combat allegiance** = `entity.faction` (already exists).
  2. **Organization membership** = `Bond.affiliations` (faction/org ids).
  3. **The personal bond** = the scalars here (loyalty, fear, admiration, resentment,
     ideology) — theirs alone, evolving from the player's legend, the NPC's own
     traits, and accumulated memory.

A reeve can be `faction="neutral"`, affiliated with your guild, and loyalty 90. A love
that "leaves or stays changed", a promoted rival, a double agent (`hidden_pressure`) — all
*emerge* from these primitives plus thresholds; none is a bespoke event.

See `EMERGENT_WORLD_IMPLEMENTATION.md` §1.4 and strategy §5.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


BOND_MIN = -100.0
BOND_MAX = 100.0

#: An NPC is a follower once their loyalty crosses this (a bond state — NOT a combat-faction
#: change; a loyal reeve stays neutral). Below the drift line they fall away again.
FOLLOW_THRESHOLD = 50.0
DRIFT_THRESHOLD = 20.0


# How the player's legend (mechanical tags) moves an ordinary onlooker's bond, per unit of
# legend weight. Trait modifiers (below) bend these — the same deed makes a rebel adore you
# and a loyalist fear you. This is the general seam; richer per-run effects layer on.
_LEGEND_BOND_EFFECTS: dict[str, dict[str, float]] = {
    "defiant": {"admiration": 2.0, "ideology": 2.0},
    "liberator": {"loyalty": 2.0, "admiration": 3.0, "ideology": 3.0},
    "protector": {"loyalty": 2.0, "admiration": 2.0},
    "merciful": {"admiration": 2.0, "resentment": -1.0},
    # Cruelty drives loyalty down as well as fear up, so a patron who turns butcher loses
    # the very followers who once believed in them (the estrangement moment).
    "butcher": {"fear": 3.0, "resentment": 3.0, "admiration": -2.0, "loyalty": -2.0},
    "destroyer": {"fear": 2.0, "resentment": 1.0, "loyalty": -1.0},
    "uncanny": {"fear": 2.0},
}

# Traits that bend how legend lands. Each maps a trait to (multiplier on the pro-rebel /
# admiration axes, multiplier on the fear/resentment axes). A loyalist inverts admiration
# into resentment; the downtrodden amplify a liberator; the pious recoil from the uncanny.
_TRAIT_AFFINITY: dict[str, float] = {
    "downtrodden": 1.6,
    "oppressed": 1.6,
    "rebel": 1.5,
    "poor": 1.3,
    "faithful_friend": 1.4,
}
_TRAIT_AVERSION: dict[str, float] = {
    "loyalist": 1.6,
    "imperial": 1.6,
    "pious": 1.4,
    "devout": 1.4,
    "fearful": 1.5,
}

# Disposition derivation (content workstream B): the affinity/aversion vocab above only bites
# if NPCs actually carry one of those traits — but seeded NPCs carry open-ended *flavor*
# traits ("shrewd", "quietly subversive"). Rather than hand-author a lean onto every NPC, we
# derive one disposition from role/trait/tag keywords, so the SAME legend lands differently on
# a priest, a tax-clerk, and a beggar — and every current and future NPC is covered for free.
# This is a starting distribution; tune the keyword sets freely. First match wins (most
# specific leanings before the broad "downtrodden" common-folk bucket).
_DISPOSITION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "loyalist",
        (
            "official",
            "clerk",
            "magistrate",
            "collector",
            "taxman",
            "tax",
            "reeve",
            "warden",
            "constable",
            "informant",
            "governor",
            "bailiff",
            "loyalist",
            "imperial",
            "quisling",
            "collaborat",
        ),
    ),
    (
        "pious",
        (
            "priest",
            "cleric",
            "monk",
            "nun",
            "acolyte",
            "shrine",
            "temple",
            "saint",
            "devout",
            "pious",
            "votary",
            "friar",
            "abbot",
        ),
    ),
    (
        "rebel",
        (
            "subversive",
            "smuggler",
            "outlaw",
            "dissident",
            "rebel",
            "insurgent",
            "agitator",
            "fence",
            "seditious",
            "partisan",
            "saboteur",
        ),
    ),
    (
        "downtrodden",
        (
            "beggar",
            "peasant",
            "laborer",
            "labourer",
            "farmer",
            "miner",
            "dock",
            "serf",
            "refugee",
            "orphan",
            "destitute",
            "rag",
            "poor",
            "urchin",
            "widow",
            "cripple",
            "drudge",
        ),
    ),
)


def disposition_inclination(traits: list[str]) -> str:
    """Coarse read of how an NPC's disposition inclines toward a sympathetic (rebel-ish)
    player: 'affinity' (the downtrodden/rebel who rally to a liberator), 'aversion'
    (loyalist/pious who recoil), or 'neutral'. Used to seed first-contact bonds — e.g. a
    freed captive whose nature inclines them tips from gratitude into following, while a
    wary one merely thanks you. The *follow* outcome thus emerges from disposition, not a
    hard-coded flag."""
    has_affinity = any(t in _TRAIT_AFFINITY for t in traits)
    has_aversion = any(t in _TRAIT_AVERSION for t in traits)
    if has_affinity and not has_aversion:
        return "affinity"
    if has_aversion and not has_affinity:
        return "aversion"
    return "neutral"


def derive_disposition(
    role: str, traits: list[str], tags: set[str] | None = None
) -> str | None:
    """Return one disposition trait (affinity or aversion vocab) for an NPC from its role,
    flavor traits, and tags — or None when nothing leans (the NPC then drifts at base rate,
    e.g. a mercenary merchant). General by design: one table classifies every NPC."""
    haystack = " ".join([role or "", *(traits or []), *sorted(tags or set())]).lower()
    for disposition, needles in _DISPOSITION_KEYWORDS:
        if any(needle in haystack for needle in needles):
            return disposition
    return None


_POSITIVE_AXES = ("loyalty", "admiration", "ideology")
_NEGATIVE_AXES = ("fear", "resentment")


@dataclass
class Bond:
    loyalty: float = 0.0  # -100..100
    fear: float = 0.0  # 0..100
    admiration: float = 0.0  # 0..100
    resentment: float = 0.0  # 0..100
    ideology: float = 0.0  # -100..100 — alignment with the player's cause
    hidden_pressure: str | None = None  # a secret agenda / prior loyalty (double agent)
    affiliations: list[str] = field(default_factory=list)  # org / faction ids

    def is_follower(self) -> bool:
        return self.loyalty >= FOLLOW_THRESHOLD

    def warmth(self) -> float:
        """A single signed read of how the NPC feels overall (for sorting / display)."""
        return (
            self.loyalty + self.admiration + self.ideology - self.resentment - self.fear
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "loyalty": self.loyalty,
            "fear": self.fear,
            "admiration": self.admiration,
            "resentment": self.resentment,
            "ideology": self.ideology,
            "hidden_pressure": self.hidden_pressure,
            "affiliations": list(self.affiliations),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Bond":
        raw = raw or {}
        return cls(
            loyalty=float(raw.get("loyalty", 0.0)),
            fear=float(raw.get("fear", 0.0)),
            admiration=float(raw.get("admiration", 0.0)),
            resentment=float(raw.get("resentment", 0.0)),
            ideology=float(raw.get("ideology", 0.0)),
            hidden_pressure=(
                str(raw["hidden_pressure"])
                if raw.get("hidden_pressure") is not None
                else None
            ),
            affiliations=[str(a) for a in raw.get("affiliations", [])],
        )


def _clamp(value: float, axis: str) -> float:
    low = BOND_MIN if axis in {"loyalty", "ideology"} else 0.0
    return max(low, min(BOND_MAX, value))


def drift_bond(
    bond: Bond,
    legend: dict[str, float],
    traits: list[str],
    *,
    personal: float = 1.0,
    rate: float = 0.5,
) -> None:
    """Nudge a bond toward what the player's legend means *to this NPC* (traits bend it).

    ``legend`` is the player's weighted legend tags; ``traits`` the NPC's; ``personal`` a
    multiplier for NPCs with first-hand memory of the player (reputation lands harder when
    you've met). Deterministic and idempotent-friendly (small steps, clamped)."""
    if not legend:
        return
    affinity = max((_TRAIT_AFFINITY.get(t, 0.0) for t in traits), default=0.0) or 1.0
    aversion = max((_TRAIT_AVERSION.get(t, 0.0) for t in traits), default=0.0) or 1.0
    loyalist = aversion > 1.0 and affinity <= 1.0
    for tag, weight in legend.items():
        effects = _LEGEND_BOND_EFFECTS.get(tag)
        if not effects or weight <= 0:
            continue
        for axis, base in effects.items():
            delta = base * weight * rate * personal
            if axis in _POSITIVE_AXES:
                delta *= affinity
                # A loyalist reads your virtues as a threat: admiration of a rebel curdles.
                if loyalist and base > 0 and tag in {"defiant", "liberator"}:
                    setattr(
                        bond,
                        "resentment",
                        _clamp(bond.resentment + abs(delta), "resentment"),
                    )
                    continue
            elif axis in _NEGATIVE_AXES:
                delta *= aversion
            setattr(bond, axis, _clamp(getattr(bond, axis) + delta, axis))
