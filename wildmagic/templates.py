from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ItemTemplate:
    id: str
    char: str
    item_type: str
    material: str
    tags: set[str] = field(default_factory=set)
    max_quantity: int = 12


@dataclass(frozen=True)
class CreatureTemplate:
    id: str
    char: str
    hp: int
    attack: int
    defense: int
    ai: str | None
    faction: str
    tags: set[str] = field(default_factory=set)
    resistances: dict[str, int] = field(default_factory=dict)
    weaknesses: dict[str, int] = field(default_factory=dict)
    max_count: int = 1


ITEM_TEMPLATES = {
    "generic_object": ItemTemplate("generic_object", "?", "object", "unknown", {"conjured"}),
    "body_part": ItemTemplate("body_part", ",", "body part", "flesh", {"organic", "ritual", "conjured"}, 8),
    "glass_shard": ItemTemplate("glass_shard", "*", "shard", "glass", {"sharp", "fragile", "conjured"}, 10),
    "ritual_component": ItemTemplate("ritual_component", "?", "ritual component", "unknown", {"ritual", "conjured"}, 8),
    "weapon_like": ItemTemplate("weapon_like", "/", "improvised weapon", "iron", {"weapon", "conjured"}, 1),
    "food": ItemTemplate("food", "%", "food", "organic", {"edible", "conjured"}, 6),
    "key_like": ItemTemplate("key_like", ";", "key-like object", "brass", {"key", "conjured"}, 3),
    "treasure": ItemTemplate("treasure", "$", "treasure", "gold", {"valuable", "conjured"}, 5),
}


CREATURE_TEMPLATES = {
    "tiny_swarm": CreatureTemplate(
        "tiny_swarm",
        "a",
        2,
        1,
        0,
        "swarm",
        "enemy",
        {"tiny", "swarm", "beast", "conjured"},
        max_count=8,
    ),
    "small_beast": CreatureTemplate("small_beast", "r", 5, 2, 0, "simple", "enemy", {"beast", "conjured"}, max_count=4),
    "humanoid": CreatureTemplate("humanoid", "h", 8, 3, 1, "simple", "enemy", {"humanoid", "conjured"}, max_count=3),
    "construct": CreatureTemplate(
        "construct",
        "c",
        9,
        2,
        2,
        "simple",
        "enemy",
        {"construct", "conjured"},
        {"poison": 95, "psychic": 50},
        {"lightning": 25},
        2,
    ),
    "spirit": CreatureTemplate(
        "spirit",
        "w",
        6,
        3,
        0,
        "simple",
        "enemy",
        {"spirit", "conjured"},
        {"physical": 35, "poison": 95},
        {"radiant": 40},
        3,
    ),
    "slime": CreatureTemplate(
        "slime",
        "s",
        8,
        2,
        1,
        "simple",
        "enemy",
        {"slime", "conjured"},
        {"poison": 50},
        {"frost": 25},
        3,
    ),
    "summoned_servant": CreatureTemplate(
        "summoned_servant",
        "h",
        5,
        1,
        0,
        None,
        "ally",
        {"servant", "conjured"},
        max_count=2,
    ),
    "hazard_creature": CreatureTemplate(
        "hazard_creature",
        "x",
        4,
        3,
        0,
        "simple",
        "enemy",
        {"hazard", "conjured"},
        max_count=4,
    ),
}


def item_template(template_id: str | None) -> ItemTemplate:
    return ITEM_TEMPLATES.get(normalize_template_id(template_id), ITEM_TEMPLATES["generic_object"])


def creature_template(template_id: str | None) -> CreatureTemplate:
    return CREATURE_TEMPLATES.get(normalize_template_id(template_id), CREATURE_TEMPLATES["small_beast"])


def normalize_template_id(template_id: str | None) -> str:
    if not template_id:
        return ""
    return template_id.lower().strip().replace(" ", "_").replace("-", "_")


def item_template_ids() -> list[str]:
    return sorted(ITEM_TEMPLATES)


def creature_template_ids() -> list[str]:
    return sorted(CREATURE_TEMPLATES)
