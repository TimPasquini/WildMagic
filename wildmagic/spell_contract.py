from __future__ import annotations

from typing import Any


SUPPORTED_EFFECTS = {
    "damage",
    "area_damage",
    "area_status",
    "heal",
    "restore_mana",
    "teleport",
    "push",
    "pull",
    "create_tile",
    "set_tile",
    "create_tiles",
    "add_status",
    "remove_status",
    "summon",
    "spawn_item",
    "conjure_item",
    "conjure_creature",
    "transform_item",
    "modify_inventory",
    "transform_entity",
    "change_faction",
    "add_tag",
    "remove_tag",
    "add_resistance",
    "add_weakness",
    "set_flag",
    "schedule_event",
    "create_trigger",
    "create_promise",
    "add_curse",
    "message",
}


SUPPORTED_COSTS = {"mana", "health", "hp", "max_health", "max_mana", "item", "status", "curse"}


STATUS_FLAVOR_ALIASES: dict[str, str] = {
    "petrified": "frozen", "stone": "frozen", "crystallized": "frozen",
    "paralyzed": "frozen", "paralysed": "frozen", "iced": "frozen",
    "glaciated": "frozen", "encased": "frozen",
    "dazed": "stunned", "staggered": "stunned", "concussed": "stunned",
    "knocked_out": "stunned", "knocked_back": "stunned", "disoriented": "stunned",
    "dazzled": "stunned",
    "immobilized": "rooted", "pinned": "rooted", "anchored": "rooted",
    "grounded": "rooted", "earthbound": "rooted", "trapped": "rooted",
    "entangled": "webbed", "snared": "webbed", "ensnared": "webbed",
    "bound": "webbed", "cocooned": "webbed", "wrapped": "webbed",
    "tangled": "webbed",
    "aflame": "burning", "alight": "burning", "on_fire": "burning",
    "ignited": "burning", "flaming": "burning", "ablaze": "burning",
    "smoldering": "burning",
    "diseased": "poisoned", "infected": "poisoned", "plagued": "poisoned",
    "venomous": "poisoned", "toxic": "poisoned", "envenomed": "poisoned",
    "tainted": "poisoned", "corrupted": "poisoned", "corroded": "poisoned",
    "rusted": "poisoned", "rusting": "poisoned", "decaying": "poisoned",
    "rotting": "poisoned", "withering": "poisoned",
    "lacerated": "bleeding", "wounded": "bleeding", "cut": "bleeding",
    "hemorrhaging": "bleeding", "bloodied": "bleeding",
    "sluggish": "slowed", "lethargic": "slowed", "lagging": "slowed",
    "encumbered": "slowed", "weighed_down": "slowed", "dragging": "slowed",
    "hastened": "hasted", "swift": "hasted", "quickened": "hasted",
    "accelerated": "hasted", "blurred": "hasted",
    "cloaked": "invisible", "hidden": "invisible", "shrouded": "invisible",
    "shadowed": "invisible", "veiled": "invisible", "ethereal": "invisible",
    "ghostly": "invisible", "transparent": "invisible",
    "deluded": "confused", "maddened": "confused", "crazed": "confused",
    "muddled": "confused", "lost": "confused", "bewildered": "confused",
    "blind": "confused", "blinded": "confused", "sightless": "confused",
    "unseeing": "confused",
    "panicked": "frightened", "terrified": "frightened", "afraid": "frightened",
    "scared": "frightened", "fleeing": "frightened", "cowering": "frightened",
    "horrified": "frightened",
    "doomed": "marked", "condemned": "marked", "targeted": "marked",
    "branded": "marked", "cursed_mark": "marked", "hexed": "marked",
    "hexed_deep": "cursed", "afflicted": "cursed", "jinxed_deep": "cursed",
    "damned": "cursed",
    "enraged": "berserk", "frenzied": "berserk", "frantic": "berserk",
    "wrathful": "berserk", "bloodlusted": "berserk", "feral": "berserk",
    "strengthened": "empowered", "supercharged": "empowered", "buffed": "empowered",
    "fortified": "empowered", "charged": "empowered", "bolstered": "empowered",
    "protected": "warded", "shielded": "warded", "guarded": "warded",
    "defended": "warded",
    "healing": "regenerating", "mending": "regenerating", "recovering": "regenerating",
    "recuperating": "regenerating", "restored": "regenerating",
    "muted": "silenced", "gagged": "silenced", "voiceless": "silenced",
    "exposed": "revealed", "uncloaked": "revealed", "illuminated": "revealed",
    "highlighted": "revealed",
}


SPELL_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["minor", "moderate", "major", "catastrophic"]},
        "outcome_text": {"type": "string"},
        "effects": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {"type": {"type": "string", "enum": sorted(SUPPORTED_EFFECTS)}},
                "required": ["type"],
                "additionalProperties": True,
            },
        },
        "costs": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {"type": {"type": "string", "enum": sorted(SUPPORTED_COSTS)}},
                "required": ["type"],
                "additionalProperties": True,
            },
        },
        "rejected_reason": {"type": ["string", "null"]},
    },
    "required": ["accepted", "severity", "outcome_text", "effects", "costs", "rejected_reason"],
    "additionalProperties": True,
}


def validate_resolution(data: dict[str, Any]) -> str | None:
    if "accepted" in data and not isinstance(data["accepted"], bool):
        return "accepted must be a boolean"
    if data.get("accepted", True) is False:
        if not str(data.get("rejected_reason") or "").strip():
            return "rejected spells need a rejected_reason"
        return None
    effects = data.get("effects", [])
    costs = data.get("costs", [])
    if not isinstance(effects, list):
        return "effects must be a list"
    if not isinstance(costs, list):
        return "costs must be a list"
    if not effects:
        return "accepted spells must have at least one effect"
    if len(effects) > 12:
        return "effects must contain at most 12 entries"
    if len(costs) > 8:
        return "costs must contain at most 8 entries"
    for index, effect in enumerate(effects):
        if not isinstance(effect, dict):
            return f"effect {index} must be an object"
        effect_type = str(effect.get("type") or "").lower()
        if effect_type not in SUPPORTED_EFFECTS:
            return f"unsupported effect type: {effect_type or '(missing)'}"
        if effect_type in {"create_tiles", "area_damage", "area_status"} and "radius" in effect:
            try:
                int(effect["radius"])
            except (TypeError, ValueError):
                return f"{effect_type} radius must be an integer"
        if effect_type == "conjure_creature" and "count" in effect:
            try:
                count = int(effect["count"])
            except (TypeError, ValueError):
                return "conjure_creature count must be an integer"
            if count < 1 or count > 12:
                return "conjure_creature count must be between 1 and 12"
        if effect_type == "conjure_item" and "count" in effect:
            try:
                count = int(effect["count"])
            except (TypeError, ValueError):
                return "conjure_item count must be an integer"
            if count < 1 or count > 20:
                return "conjure_item count must be between 1 and 20"
        if effect_type == "create_trigger":
            trigger_effects = effect.get("effects")
            if not isinstance(trigger_effects, list) or not trigger_effects:
                return "create_trigger effects must be a non-empty list"
    for index, cost in enumerate(costs):
        if not isinstance(cost, dict):
            return f"cost {index} must be an object"
        cost_type = str(cost.get("type") or "").lower()
        if cost_type not in SUPPORTED_COSTS:
            return f"unsupported cost type: {cost_type or '(missing)'}"
    return None
