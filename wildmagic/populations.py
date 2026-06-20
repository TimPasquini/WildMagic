"""Realm populations — who fills a realm's zones (CONTENT_FLESHING_ROADMAP Tier 1A).

A realm's zones are populated by its own people: a **conquered** realm by imperial occupiers
*and* local folk, the free **rival** by its own martial people, the empire **heartland** by
imperials, the client **proxy** by deferent locals. Each denizen enters as a *politically
situated person* — **neutral by default**, carrying a typed ``identity``/``role`` — so hostility
is **derived** (the exposure model: witnessed wild magic turns the Empire on you; provocation or
reputation turns others), never baked into the spawn. The deterministic roster ships complete
with the model off; the LLM only enriches names/personalities later.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Denizen:
    """A spawn archetype for a realm's person. ``combatant`` routes to ``spawn_actor`` (a body
    with combat AI, talkable lazily) vs ``spawn_npc`` (a persona that flees). All spawn neutral;
    ``identity`` (the realm or ``imperial``) is attached at spawn time, not stored here."""

    role: str
    posture: str  # garrison | official | civilian | trader | partisan
    char: str
    hp: int
    attack: int
    defense: int
    ai: str
    tags: frozenset[str]
    combatant: bool


# Occupiers (carry identity ["imperial"]). Soldiers/officers can fight (when provoked/exposed);
# the clerk is a non-combatant loyalist who will fear and report a sorcerer rather than draw.
_SOLDIER = Denizen(
    "soldier",
    "garrison",
    "i",
    10,
    3,
    1,
    "legion",
    frozenset({"human", "soldier", "disciplined"}),
    True,
)
_OFFICER = Denizen(
    "officer",
    "garrison",
    "O",
    14,
    4,
    2,
    "legion",
    frozenset({"human", "soldier", "officer"}),
    True,
)
_CLERK = Denizen(
    "clerk", "official", "c", 8, 1, 0, "npc", frozenset({"human", "clerk"}), False
)

# Locals (carry identity [realm_id]). All non-combatant in a quiet-embers realm — resistance is
# latent (the partisan carries grievances, doesn't openly skirmish); per-realm texture (Vint
# loud, rival martial) is Tier 3/4A.
_TOWNSFOLK = Denizen(
    "townsfolk",
    "civilian",
    "p",
    10,
    1,
    0,
    "npc",
    frozenset({"human", "townsfolk"}),
    False,
)
_MERCHANT = Denizen(
    "merchant", "trader", "$", 10, 1, 0, "npc", frozenset({"human", "merchant"}), False
)
_PARTISAN = Denizen(
    "partisan",
    "partisan",
    "r",
    12,
    2,
    0,
    "npc",
    frozenset({"human", "partisan"}),
    False,
)
_PRIEST = Denizen(
    "priest", "civilian", "+", 10, 1, 0, "npc", frozenset({"human", "priest"}), False
)

# The free rival fields its own soldiers (identity = the realm, at war with the Empire).
_RIVAL_WARRIOR = Denizen(
    "soldier",
    "partisan",
    "w",
    12,
    3,
    1,
    "legion",
    frozenset({"human", "soldier"}),
    True,
)

#: A small name pool per role — the *identity* (imperial vs realm) carries allegiance, not the
#: name, so a "legionary" reads imperial and a "weaver" reads local. LLM enrich gives real names.
_ROLE_NAMES: dict[str, tuple[str, ...]] = {
    "soldier": ("legionary", "spearman", "drill initiate", "sentry"),
    "officer": ("decanus", "watch-captain", "optio"),
    "clerk": ("tax-clerk", "records-keeper", "notary"),
    "townsfolk": ("weaver", "farmhand", "potter", "cooper", "laborer"),
    "merchant": ("trader", "peddler", "stall-keeper"),
    "partisan": ("malcontent", "quiet partisan", "ember"),
    "priest": ("acolyte", "shrine-keeper"),
}

_IMPERIAL = ["imperial"]

DenizenPlacement = tuple[Denizen, list[str]]  # (archetype, identity tokens)


def denizen_plan(
    role: str, realm_id: str, rng: random.Random
) -> list[DenizenPlacement]:
    """The people to spawn in a zone, by the owning realm's geopolitical role. Conquered land
    mixes a light imperial garrison with locals; the heartland is imperial; the proxy is
    deferent locals with the odd imperial guard; the rival fields its own people. Deterministic
    given ``rng``."""
    plan: list[DenizenPlacement] = []
    if role == "conquered":
        for _ in range(rng.randint(1, 2)):
            plan.append((rng.choice([_SOLDIER, _SOLDIER, _OFFICER]), list(_IMPERIAL)))
        if rng.random() < 0.4:
            plan.append((_CLERK, list(_IMPERIAL)))
        for _ in range(rng.randint(2, 3)):
            plan.append(
                (
                    rng.choice([_TOWNSFOLK, _TOWNSFOLK, _MERCHANT, _PARTISAN, _PRIEST]),
                    [realm_id],
                )
            )
    elif role == "founding":
        for _ in range(rng.randint(1, 2)):
            plan.append((rng.choice([_SOLDIER, _OFFICER, _CLERK]), list(_IMPERIAL)))
        for _ in range(rng.randint(1, 2)):
            plan.append((rng.choice([_TOWNSFOLK, _MERCHANT]), list(_IMPERIAL)))
    elif role == "proxy":
        for _ in range(rng.randint(2, 3)):
            plan.append(
                (rng.choice([_TOWNSFOLK, _MERCHANT, _CLERK, _PRIEST]), [realm_id])
            )
        if rng.random() < 0.3:
            plan.append((_SOLDIER, list(_IMPERIAL)))  # an emissary's guard
    elif role == "rival":
        for _ in range(rng.randint(1, 2)):
            plan.append((_RIVAL_WARRIOR, [realm_id]))
        for _ in range(rng.randint(1, 2)):
            plan.append((rng.choice([_TOWNSFOLK, _PARTISAN]), [realm_id]))
    return plan


def denizen_name(denizen: Denizen, rng: random.Random) -> str:
    return rng.choice(_ROLE_NAMES.get(denizen.role, (denizen.role,)))
