from __future__ import annotations

from typing import Any

from .models import Entity
from .normalize import clamp_int, normalize_id, status_duration


BEHAVIOR_ALIASES: dict[str, str] = {
    "coward": "coward",
    "cowardly": "coward",
    "flee": "coward",
    "fleeing": "coward",
    "fear_blood": "coward",
    "blood_coward": "coward",
    "dance": "dance",
    "dancing": "dance",
    "forced_dance": "dance",
    "move_only": "dance",
    "duel": "duel",
    "duelist": "duel",
    "duel_lock": "duel",
    "single_combat": "duel",
    "lowest_hp": "lowest_hp",
    "weakest": "lowest_hp",
    "target_lowest_hp": "lowest_hp",
    "hunt_weakest": "lowest_hp",
    "freeze_dread": "freeze_dread",
    "dread": "freeze_dread",
    "existential_dread": "freeze_dread",
    "freeze": "freeze_dread",
    "mimic": "mimic",
    "mirror": "mimic",
    "copy_movement": "mimic",
    "mirror_movement": "mimic",
}

SUPPORTED_BEHAVIORS = frozenset(BEHAVIOR_ALIASES.values())


def normalize_behavior(value: Any) -> str:
    key = normalize_id(str(value or ""))
    return BEHAVIOR_ALIASES.get(key, key)


def behavior_modifiers(entity: Entity) -> list[dict[str, Any]]:
    raw = entity.details.get("behavior_modifiers")
    if isinstance(raw, list):
        return [mod for mod in raw if isinstance(mod, dict)]
    if isinstance(raw, dict):
        mods = []
        for behavior, payload in raw.items():
            mod = dict(payload) if isinstance(payload, dict) else {}
            mod.setdefault("behavior", behavior)
            mods.append(mod)
        entity.details["behavior_modifiers"] = mods
        return mods
    entity.details["behavior_modifiers"] = []
    return entity.details["behavior_modifiers"]


def active_behavior(entity: Entity, behavior: str) -> dict[str, Any] | None:
    wanted = normalize_behavior(behavior)
    for mod in behavior_modifiers(entity):
        if normalize_behavior(mod.get("behavior")) != wanted:
            continue
        if (
            mod.get("duration") == "permanent"
            or status_duration(mod.get("duration")) > 0
        ):
            return mod
    return None


def upsert_behavior_modifier(
    entity: Entity,
    behavior: str,
    *,
    duration: Any = 3,
    target_id: str | None = None,
    label: str = "",
) -> dict[str, Any]:
    canonical = normalize_behavior(behavior)
    duration_value: int | str = (
        "permanent" if duration == "permanent" else clamp_int(duration, 1, 999)
    )
    mods = behavior_modifiers(entity)
    for mod in mods:
        if normalize_behavior(mod.get("behavior")) == canonical:
            mod["duration"] = duration_value
            if target_id:
                mod["target_id"] = target_id
            if label:
                mod["label"] = label
            return mod
    mod = {"behavior": canonical, "duration": duration_value}
    if target_id:
        mod["target_id"] = target_id
    if label:
        mod["label"] = label
    mods.append(mod)
    return mod


def tick_behavior_modifiers(entity: Entity) -> list[dict[str, Any]]:
    expired: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for mod in behavior_modifiers(entity):
        if mod.get("duration") == "permanent":
            kept.append(mod)
            continue
        turns = status_duration(mod.get("duration")) - 1
        if turns <= 0:
            expired.append(mod)
        else:
            mod["duration"] = turns
            kept.append(mod)
    if kept:
        entity.details["behavior_modifiers"] = kept
    else:
        entity.details.pop("behavior_modifiers", None)
    return expired
