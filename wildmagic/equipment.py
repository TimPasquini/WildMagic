"""Pure equipment policy shared by mutation and presentation layers."""

from __future__ import annotations

from .game_data import EQUIPMENT_SPECS


EQUIPMENT_SLOTS: tuple[str, ...] = (
    "weapon",
    "armor",
    "charm",
    "head",
    "chest",
    "legs",
    "feet",
    "hands",
)


def infer_equipment_slot(item_name: str) -> str | None:
    """Infer a slot for generated gear that has no authored equipment specification."""

    name_lower = item_name.strip().lower()
    keyword_slots = {
        "weapon": (
            "sword",
            "blade",
            "dagger",
            "axe",
            "pick",
            "bow",
            "staff",
            "mace",
            "hammer",
            "whip",
            "spear",
            "rapier",
            "scythe",
            "club",
            "wand",
        ),
        "head": (
            "hat",
            "helmet",
            "cowl",
            "crown",
            "hood",
            "circlet",
            "mask",
            "visor",
            "helm",
            "coif",
        ),
        "feet": ("boot", "shoe", "slipper", "sabaton", "footwear"),
        "legs": (
            "trouser",
            "pant",
            "legging",
            "breeches",
            "greaves",
            "cuisses",
            "skirt",
            "kilt",
            "hosen",
        ),
        "hands": ("glove", "gauntlet", "mitt", "bracer"),
        "chest": (
            "cloak",
            "robe",
            "tunic",
            "shirt",
            "coat",
            "jacket",
            "cape",
            "shroud",
            "doublet",
            "jerkin",
        ),
        "charm": (
            "charm",
            "trinket",
            "amulet",
            "ring",
            "talisman",
            "necklace",
            "locket",
            "pendant",
        ),
        "armor": (
            "shield",
            "buckler",
            "armor",
            "armour",
            "cuirass",
            "breastplate",
            "vest",
            "mail",
        ),
    }
    for slot, keywords in keyword_slots.items():
        if any(keyword in name_lower for keyword in keywords):
            return slot
        if slot == "head" and "cap" in name_lower.split():
            return "head"
    return None


def equipment_slot_for_item(item_name: str) -> str | None:
    """Return the authoritative slot for authored or generated equipment."""

    spec = EQUIPMENT_SPECS.get(item_name.strip().lower())
    if spec:
        return str(spec["slot"])
    return infer_equipment_slot(item_name)
