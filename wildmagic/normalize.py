from __future__ import annotations

from typing import Any

from .models import FLOOR, TILE_ALIASES, Entity


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def optional_duration(value: Any) -> int | None:
    if value is None or value == "permanent":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return clamp_int(parsed, 1, 999)


def status_duration(value: Any) -> int:
    if value == "permanent":
        return 999
    return clamp_int(value, 0, 999)


def parse_tile_key(key: str) -> tuple[int, int]:
    x_text, y_text = key.split(",", 1)
    return int(x_text), int(y_text)


def normalize_id(value: str) -> str:
    return value.lower().strip().replace(" ", "_").replace("-", "_")


def normalize_faction(
    value: Any, default: str = "ally", neutral_is_ally: bool = False
) -> str:
    normalized = normalize_id(str(value or default))
    aliases = {
        "player": "ally",
        "self": "ally",
        "you": "ally",
        "friendly": "ally",
        "friend": "ally",
        "friends": "ally",
        "companion": "ally",
        "summoned": "ally",
        "hostile": "enemy",
        "hostiles": "enemy",
        "foe": "enemy",
        "foes": "enemy",
        "monster": "enemy",
        "monsters": "enemy",
    }
    if normalized == "neutral" and neutral_is_ally:
        return "ally"
    if normalized in aliases:
        return aliases[normalized]
    if normalized in {"ally", "enemy", "neutral"}:
        return normalized
    return normalize_faction(default, default="ally")


def normalize_trigger_name(value: str) -> str:
    normalized = normalize_id(value)
    aliases = {
        "next_spell": "on_next_spell",
        "on_spell": "on_next_spell",
        "when_i_cast": "on_next_spell",
        # Explicitly-player phrasings stay scoped to the player.
        "player_hit": "on_player_hit",
        "on_player_hit": "on_player_hit",
        "on_player_takes_damage": "on_player_hit",
        "when_i_am_hit": "on_player_hit",
        "player_damaged": "on_player_damaged",
        # Generic "was struck / took damage" with NO subject is universal: it fires for any
        # entity (the engine always fires `on_damaged`, even for allies and NPCs, which have
        # no faction-specific hook) and is scoped by the trigger's `target` -- the player, an
        # ally/enemy id, a tag, or "any". This is what lets an on-hit ward watch an ally, a
        # summon, or one enemy fighting another, not only the player. NB: this is the
        # DEFENDER side ("when X is struck"); an attacker-side "when X strikes" hook does not
        # exist yet (it needs source-matching; tracked with the deferred item work).
        "on_hit": "on_damaged",
        "when_hit": "on_damaged",
        "on_struck": "on_damaged",
        "when_struck": "on_damaged",
        "on_take_damage": "on_damaged",
        "on_takes_damage": "on_damaged",
        "on_receive_damage": "on_damaged",
        "on_receives_damage": "on_damaged",
        "on_hurt": "on_damaged",
        "on_wounded": "on_damaged",
        "on_damage": "on_damaged",
        "on_damaged": "on_damaged",
        # Attacker side ("when X deals a hit / strikes / lands a blow") -> on_deal_damage,
        # matched against the event's SOURCE (the attacker), so an effect can ride the blow an
        # entity lands: a blade that bleeds whatever its wielder strikes. The mirror of the
        # defender-side "on_hit" above.
        "on_deal_damage": "on_deal_damage",
        "on_deals_damage": "on_deal_damage",
        "on_dealing_damage": "on_deal_damage",
        "on_dealt_damage": "on_deal_damage",
        "on_strike": "on_deal_damage",
        "on_strikes": "on_deal_damage",
        "when_strike": "on_deal_damage",
        "when_i_strike": "on_deal_damage",
        "when_it_strikes": "on_deal_damage",
        "on_attack": "on_deal_damage",
        "on_attacks": "on_deal_damage",
        "on_attacking": "on_deal_damage",
        "on_hit_target": "on_deal_damage",
        "on_lands_a_hit": "on_deal_damage",
        "on_landing_a_hit": "on_deal_damage",
        "on_melee": "on_deal_damage",
        "enemy_hit": "on_enemy_hit",
        "enemy_damaged": "on_enemy_damaged",
        "enemy_death": "on_enemy_death",
        "on_kill": "on_enemy_death",
        "on_enemy_killed": "on_enemy_death",
        "on_enemy_dies": "on_enemy_death",
        "on_target_death": "on_enemy_death",
        "on_target_dies": "on_enemy_death",
        "on_lethal": "on_lethal_damage",
        "on_lethal_damage": "on_lethal_damage",
        "on_would_die": "on_lethal_damage",
        "on_deathblow": "on_lethal_damage",
        "before_death": "on_lethal_damage",
        "on_curse": "on_curse_gained",
        "on_curse_gained": "on_curse_gained",
        "when_cursed": "on_curse_gained",
        "curse_gained": "on_curse_gained",
        "enters_sight": "on_enters_sight",
        "enter_sight": "on_enters_sight",
        "on_enters_sight": "on_enters_sight",
        "on_enter_sight": "on_enters_sight",
        "when_seen": "on_enters_sight",
        "when_spotted": "on_enters_sight",
        "on_spotted": "on_enters_sight",
        "player_move": "on_player_move",
        "on_move": "on_player_move",
        "on_player_moves": "on_player_move",
    }
    if normalized in aliases:
        return aliases[normalized]
    if not normalized.startswith("on_"):
        normalized = f"on_{normalized}"
    return normalized


def infer_behavior_tags(name: str, tags: set[str]) -> set[str]:
    tag_set = {normalize_id(str(tag)) for tag in tags if str(tag).strip()}
    name_text = normalize_id(name).replace("_", " ")

    def has_name_word(*words: str) -> bool:
        return any(word in name_text for word in words)

    def has_tag(*words: str) -> bool:
        return any(word in tag_set for word in words)

    def missing(prefix: str) -> bool:
        return not any(tag.startswith(prefix) for tag in tag_set)

    if has_name_word(
        "archer", "ranger", "shooter", "bowman", "sniper", "gunner", "crossbow"
    ) or has_tag("archer", "shooter", "bowman"):
        tag_set.add("ranged")
    if has_name_word(
        "ward",
        "totem",
        "beacon",
        "font",
        "pillar",
        "obelisk",
        "turret",
        "emanation",
        "radiator",
        "anchor",
    ) or has_tag("immobile", "passive", "ward", "totem"):
        tag_set.add("stationary")
    if (
        has_name_word("guardian", "sentinel", "warden", "protector")
        and "stationary" not in tag_set
    ):
        tag_set.add("guardian")
    if has_name_word(
        "legion",
        "legionary",
        "centurion",
        "marshal",
        "exemplar",
        "spearman",
        "sergeant",
        "chaplain",
        "drill",
        "imperial",
        "praetorian",
    ) or has_tag("empire", "legion", "disciplined", "imperial"):
        tag_set.add("disciplined")
    if has_name_word("bomb", "explosive", "volatile", "detonator") or has_tag(
        "bomb", "explosive", "volatile"
    ):
        tag_set.add("explode_on_death")

    aura_rules = [
        (
            "aura_burn_2",
            "aura_burn",
            ("fire", "burning", "flaming", "flame", "hot", "infernal", "scorching"),
            ("burn", "fire", "flame", "scorch", "ember", "inferno", "blaze"),
        ),
        (
            "aura_heal_2",
            "aura_heal",
            ("heal", "healing", "restorative", "regenerative", "life", "mending"),
            ("heal", "healing", "medic", "cleric", "life", "restore", "mend"),
        ),
        (
            "aura_poison_2",
            "aura_poison",
            ("poison", "toxic", "plague", "venomous", "venom", "miasma"),
            ("poison", "toxic", "plague", "miasma", "venom", "pestilence"),
        ),
        (
            "aura_fear_2",
            "aura_fear",
            ("fear", "terror", "dread", "horror", "frightening", "terrifying"),
            ("fear", "dread", "terror", "horror", "despair"),
        ),
        (
            "aura_slow_2",
            "aura_slow",
            ("slow", "sluggish", "leaden", "weight", "heavy", "torpor"),
            ("slow", "sluggish", "leaden", "weight", "torpor"),
        ),
        (
            "aura_bleed_2",
            "aura_bleed",
            ("bleed", "bleeding", "hemorrhage", "thorn", "barbed"),
            ("bleed", "thorn", "shard", "barb", "needle"),
        ),
    ]
    for aura_tag, prefix, tag_words, name_words in aura_rules:
        if missing(prefix) and (has_tag(*tag_words) or has_name_word(*name_words)):
            tag_set.add(aura_tag)
    if "stationary" in tag_set and any(tag.startswith("aura_") for tag in tag_set):
        tag_set.add("pacifist")
    return tag_set


def singular_target_tag(value: str) -> str:
    normalized = normalize_id(value)
    if normalized.startswith("all_"):
        normalized = normalized[4:]
    if normalized.startswith("nearby_"):
        normalized = normalized[7:]
    if normalized.endswith("ies") and len(normalized) > 3:
        return f"{normalized[:-3]}y"
    if normalized.endswith(("ses", "xes", "ches", "shes")) and len(normalized) > 2:
        return normalized[:-2]
    if normalized.endswith("s") and len(normalized) > 1:
        return normalized[:-1]
    return normalized


def normalize_numeric_map(value: Any, minimum: int, maximum: int) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        normalize_id(str(key)): clamp_int(raw, minimum, maximum)
        for key, raw in value.items()
    }


def sanitize_name(value: str, fallback: str, max_length: int = 40) -> str:
    cleaned = "".join(char for char in value.strip() if 32 <= ord(char) < 127)
    cleaned = " ".join(cleaned.split())
    return (cleaned or fallback)[:max_length]


def sanitize_char(value: str, fallback: str) -> str:
    for char in value:
        if 32 <= ord(char) < 127 and char != " ":
            return char
    return fallback[:1] or "?"


def coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _flatten_effect(effect: dict[str, Any]) -> dict[str, Any]:
    """Hoist fields from a nested 'details' sub-object so the engine finds them at top level."""
    details = effect.get("details")
    if not isinstance(details, dict):
        return effect
    merged = dict(details)
    merged.update({k: v for k, v in effect.items() if k != "details"})
    return merged


def area_damage_affects(entity: Entity, affects: str, player_id: str) -> bool:
    if affects in {"all", "everyone", "any"}:
        return True
    if affects in {"enemies", "enemy", "hostile", "hostiles", "foes"}:
        return entity.faction == "enemy"
    if affects in {"allies", "ally", "friendlies", "friendly"}:
        return entity.faction in {"ally", "player"} or entity.id == player_id
    if affects in {"non_player", "nonplayer", "others"}:
        return entity.id != player_id
    if affects in {"player", "self"}:
        return entity.id == player_id
    return entity.id != player_id


def tile_from_name(name: str) -> str:
    normalized = name.lower().strip().replace(" ", "_")
    return TILE_ALIASES.get(normalized, FLOOR)
