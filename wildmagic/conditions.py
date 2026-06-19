from __future__ import annotations

from typing import Any

from .models import Entity
from .normalize import clamp_int, normalize_id


def evaluate_condition(engine: Any, when: Any, event: dict[str, Any]) -> bool:
    """Evaluate an optional trigger predicate.

    Conditions are pure reads over current engine state plus the firing event. They never call
    the provider and never mutate, so replay only needs the trigger data and game state.
    """
    if when is None or when == "" or when is True:
        return True
    if when is False or not isinstance(when, dict):
        return False
    op = normalize_id(str(when.get("op") or when.get("type") or ""))
    if op:
        return _evaluate_single(engine, op, when, event)
    for key, value in when.items():
        op_name = normalize_id(str(key))
        spec = value if isinstance(value, dict) else {"value": value}
        if not _evaluate_single(engine, op_name, spec, event):
            return False
    return True


def _evaluate_single(
    engine: Any, op: str, spec: dict[str, Any], event: dict[str, Any]
) -> bool:
    if op in {"hp_below", "hp_under"}:
        entity = _condition_entity(engine, spec, event)
        threshold = _hp_threshold(entity, spec)
        return entity is not None and entity.hp < threshold
    if op in {"hp_at_or_below", "hp_lte"}:
        entity = _condition_entity(engine, spec, event)
        threshold = _hp_threshold(entity, spec)
        return entity is not None and entity.hp <= threshold
    if op in {"hp_above", "hp_over"}:
        entity = _condition_entity(engine, spec, event)
        threshold = _hp_threshold(entity, spec)
        return entity is not None and entity.hp > threshold
    if op == "hp_parity":
        entity = _condition_entity(engine, spec, event)
        want = normalize_id(str(spec.get("value") or spec.get("parity") or "even"))
        return entity is not None and (
            (want == "even" and entity.hp % 2 == 0)
            or (want == "odd" and entity.hp % 2 == 1)
        )
    if op == "inventory_empty":
        return not bool(engine.state.inventory)
    if op == "on_terrain":
        entity = _condition_entity(engine, spec, event) or engine.state.player
        wanted = {
            normalize_id(str(tag))
            for tag in _as_list(
                spec.get("value") or spec.get("tag") or spec.get("tags")
            )
            if str(tag).strip()
        }
        if not wanted:
            return False
        tile = normalize_id(str(engine.tile_at(entity.x, entity.y)))
        tags = {
            normalize_id(str(tag)) for tag in engine.tile_tags_at(entity.x, entity.y)
        }
        return bool(wanted & ({tile} | tags))
    if op == "step_multiple":
        multiple = clamp_int(spec.get("value") or spec.get("multiple"), 1, 999)
        steps = int(getattr(engine.state, "player_steps", 0))
        return steps > 0 and steps % multiple == 0
    if op == "count_visible":
        faction = normalize_id(
            str(spec.get("faction") or spec.get("group") or "enemies")
        )
        minimum = clamp_int(spec.get("min") or spec.get("at_least"), 0, 999)
        maximum = spec.get("max") or spec.get("at_most")
        count = _visible_count(engine, faction)
        if maximum is not None and count > clamp_int(maximum, 0, 999):
            return False
        return count >= minimum
    if op == "same_spell_streak":
        needed = clamp_int(spec.get("value") or spec.get("count"), 1, 999)
        return int(getattr(engine.state, "same_spell_streak", 0)) >= needed
    return False


def _condition_entity(
    engine: Any, spec: dict[str, Any], event: dict[str, Any]
) -> Entity | None:
    role = normalize_id(
        str(spec.get("role") or spec.get("target") or spec.get("entity") or "target")
    )
    if role in {"target", "trigger_target"} and isinstance(event.get("target"), Entity):
        return event["target"]
    if role in {"source", "attacker", "caster", "trigger_source"} and isinstance(
        event.get("source"), Entity
    ):
        return event["source"]
    if role in {"player", "self", "you"}:
        return engine.state.player
    return engine.resolve_target(role)


def _hp_threshold(entity: Entity | None, spec: dict[str, Any]) -> float:
    if entity is None:
        return 0
    raw = spec.get("value", spec.get("threshold", spec.get("hp")))
    ratio = spec.get("ratio")
    if ratio is None and isinstance(raw, float):
        ratio = raw
    if ratio is None and isinstance(raw, str):
        text = raw.strip()
        if text.endswith("%"):
            try:
                ratio = float(text[:-1]) / 100
            except ValueError:
                ratio = None
    if ratio is not None:
        try:
            return entity.max_hp * float(ratio)
        except (TypeError, ValueError):
            return 0
    return clamp_int(raw, 0, max(999, entity.max_hp))


def _visible_count(engine: Any, faction: str) -> int:
    count = 0
    for entity in engine.state.entities.values():
        if entity.kind not in {"player", "actor", "npc"} or entity.hp <= 0:
            continue
        if not engine.is_visible(entity.x, entity.y):
            continue
        if faction in {"enemy", "enemies"} and entity.faction != "enemy":
            continue
        if faction in {"ally", "allies"} and entity.faction not in {"ally", "player"}:
            continue
        if faction in {"npc", "npcs"} and entity.kind != "npc":
            continue
        count += 1
    return count


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]
