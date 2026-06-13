"""Character profiles, origins, and the starting kit.

The profile type itself (`CharacterProfile`) lives in `models.py` alongside `Entity`
because it is universal — every creature carries one. This module holds the *data*:
the origin roster, the default/random profile factory, and the default starting
inventory. Character creation (docs/CHARACTER_CREATION.md) builds on top of this; the
entity-unification refactor only needs the foundation so any entity can have a profile.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from .models import CharacterProfile

STATS = ("vigor", "attunement", "composure")
CREATION_POINTS = 3
STAT_CAP = 6


# The kit that used to be hard-coded as GameState.inventory's default. It now seeds
# the controlled entity's per-entity inventory at creation.
DEFAULT_STARTING_INVENTORY: dict[str, int] = {
    "chalk": 2,
    "grave salt": 2,
    "mana crystal": 2,
    "blood moss": 2,
    "bone shard": 2,
    "viscous residue": 1,
    "metal scrap": 2,
    "arcane residue": 1,
    "gold": 30,
}


@dataclass(frozen=True)
class Origin:
    """A starting background tied to a magical tradition. Sets a stat baseline and a
    starting kit; see docs/CHARACTER_CREATION.md for the design rationale."""

    id: str
    name: str
    tradition: str
    blurb: str
    stat_baseline: dict[str, int] = field(default_factory=dict)
    starting_items: dict[str, int] = field(default_factory=dict)
    default_appearance: str = ""
    default_backstory: str = ""
    default_signature: str = ""
    faction_notes: str = ""

    def to_profile(self) -> CharacterProfile:
        base = {"vigor": 3, "attunement": 3, "composure": 3}
        base.update(self.stat_baseline)
        return CharacterProfile(
            origin_id=self.id,
            vigor=base["vigor"],
            attunement=base["attunement"],
            composure=base["composure"],
            appearance=self.default_appearance,
            backstory=self.default_backstory,
            signature=self.default_signature,
        )


ORIGINS: dict[str, Origin] = {
    "bone_singer_apprentice": Origin(
        id="bone_singer_apprentice",
        name="Bone-singer's apprentice",
        tradition="bone",
        blurb="Raised among the Bone-Singers, you learned to coax magic from marrow and song.",
        stat_baseline={"attunement": 5, "vigor": 2},
        starting_items={"bone shard": 3, "chalk": 1},
        default_appearance="A wiry youth hung with carved bone charms that click when you move.",
        default_backstory="Apprenticed to the Bone-Singers until the Empire came counting.",
        default_signature="Everything you cast hums faintly, like a struck bone flute.",
    ),
    "deserter_charter_mage": Origin(
        id="deserter_charter_mage",
        name="Deserter charter mage",
        tradition="charter",
        blurb="You trained in the Censorate's precise, cold magic — then walked away. They know your face.",
        stat_baseline={"composure": 5, "attunement": 3, "vigor": 2},
        starting_items={"mana crystal": 3, "metal scrap": 2},
        default_appearance="Imperial bearing gone to seed: a once-crisp charter coat, insignia torn away.",
        default_backstory="A Censorate calligrapher who broke their oath and fled into the wild.",
        default_signature="Your wild magic keeps snapping toward clean geometry before it frays.",
        faction_notes="empire_recognizes",
    ),
    "desert_nomad": Origin(
        id="desert_nomad",
        name="Desert nomad",
        tradition="sound",
        blurb="A child of the singing dunes — sound-magic folk, mobile and trade-savvy.",
        stat_baseline={"vigor": 5, "composure": 3, "attunement": 2},
        starting_items={"grave salt": 3, "gold": 20},
        default_appearance="Sun-dark and lean, wrapped in layered indigo against a sun that isn't here.",
        default_backstory="Crossed three deserts with a caravan that the Empire 'regularized' out of existence.",
        default_signature="Your spells arrive on a sympathetic drone, like wind over open dunes.",
    ),
    "merfolk_exile": Origin(
        id="merfolk_exile",
        name="Merfolk exile",
        tradition="water",
        blurb="Cast out of the trenches, you carry deep-water rites into dry, hostile air.",
        stat_baseline={"attunement": 4, "vigor": 3, "composure": 3},
        starting_items={"viscous residue": 2, "blood moss": 2},
        default_appearance="Faintly iridescent skin, gills sealed to thin scars, eyes too large for the light.",
        default_backstory="Exiled from a trench-hold for a rite that went wrong, or too right.",
        default_signature="A smell of brine and cold pressure clings to whatever you conjure.",
    ),
}


def default_profile(rng: random.Random | None = None) -> CharacterProfile:
    """A ready-to-play random profile (random origin baseline). Used by quick-start,
    autoplay, and tests so nothing ever blocks on character-creation input."""
    chooser = rng or random
    origin = chooser.choice(list(ORIGINS.values()))
    return origin.to_profile()


def starting_inventory_for(profile: CharacterProfile | None) -> dict[str, int]:
    """The default kit merged with the profile's origin starting items."""
    items = dict(DEFAULT_STARTING_INVENTORY)
    origin = ORIGINS.get(profile.origin_id) if profile else None
    if origin:
        for name, qty in origin.starting_items.items():
            items[name] = items.get(name, 0) + qty
    return items


def build_profile(
    origin_id: str,
    point_spend: dict[str, int] | None = None,
    name: str | None = None,
    appearance: str | None = None,
    backstory: str | None = None,
    signature: str | None = None,
) -> CharacterProfile:
    """Construct a profile from an origin baseline plus a small point spend, validating
    the per-stat cap and total pool. `name` is accepted for the creation flow's
    convenience but is stored on the entity, not the profile."""
    origin = ORIGINS.get(origin_id)
    if origin is None:
        raise ValueError(f"unknown origin: {origin_id}")
    profile = origin.to_profile()
    spend = point_spend or {}
    total = sum(max(0, int(v)) for v in spend.values())
    if total > CREATION_POINTS:
        raise ValueError(f"spent {total} points; only {CREATION_POINTS} allowed")
    for stat, amount in spend.items():
        if stat not in STATS:
            raise ValueError(f"unknown stat: {stat}")
        current = getattr(profile, stat)
        new_value = current + max(0, int(amount))
        if new_value > STAT_CAP:
            raise ValueError(f"{stat} {new_value} exceeds cap {STAT_CAP}")
        setattr(profile, stat, new_value)
    if appearance is not None:
        profile.appearance = appearance
    if backstory is not None:
        profile.backstory = backstory
    if signature is not None:
        profile.signature = signature
    return profile


def clone_profile(profile: CharacterProfile | None) -> CharacterProfile:
    """A fresh copy so two entities never share a mutable profile object."""
    return copy.deepcopy(profile) if profile is not None else CharacterProfile()
