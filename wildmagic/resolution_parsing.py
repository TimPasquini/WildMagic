"""Spell-resolution output parsing: raw LLM string -> validated-shape effect dict.

A pure transform with no I/O and no provider/network dependencies. `resolve_spell`
in wild_magic.py owns orchestration (prompt build, provider call, retry, audit) and
calls `parse_resolution_json` here to turn the model's raw text into a normalized
resolution dict ready for the spell contract validator.

Split out of wild_magic.py; see docs/ARCHITECTURE.md."""

from __future__ import annotations

import json
import re
from typing import Any

from .llm_client import strip_thinking
from .models import MECHANICAL_STATUSES, TILE_ALIASES
from .spell_contract import (
    SUPPORTED_COSTS,
    SUPPORTED_EFFECTS,
    STATUS_FLAVOR_ALIASES as _STATUS_FLAVOR_ALIASES,
)


def parse_resolution_json(raw: str) -> dict[str, Any]:
    cleaned = strip_thinking(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise TypeError("wild magic response was not a JSON object")
    return _normalize_resolution(parsed)


_ELEMENT_DAMAGE_ALIASES: dict[str, str] = {
    "lightning": "lightning",
    "thunder": "lightning",
    "fire": "fire",
    "flame": "fire",
    "inferno": "fire",
    "ice": "frost",
    "frost": "frost",
    "cold": "frost",
    "freeze": "frost",
    "poison": "poison",
    "toxic": "poison",
    "acid": "acid",
    "arcane": "arcane",
    "magic": "arcane",
    "force": "force",
    "radiant": "radiant",
    "holy": "radiant",
    "divine": "radiant",
    "shadow": "shadow",
    "necrotic": "shadow",
    "dark": "shadow",
    "physical": "physical",
    "blunt": "physical",
    "slash": "physical",
    "pierce": "physical",
    "blood": "blood",
    "psychic": "arcane",
    "sonic": "force",
    "wind": "force",
}

# Map LLM-used effect type strings to canonical SUPPORTED_EFFECTS keys.
_EFFECT_TYPE_ALIASES: dict[str, str] = {
    "healing": "heal",
    "restore_health": "heal",
    "restore_hp": "heal",
    "regenerate": "add_status",
    "regeneration": "add_status",
    "regen": "add_status",
    "restore_mana_points": "restore_mana",
    "restore_mp": "restore_mana",
    "replenish_mana": "restore_mana",
    "status": "add_status",
    "apply_status": "add_status",
    "give_status": "add_status",
    "set_status": "add_status",
    "status_effect": "add_status",
    "curse": "add_curse",
    "spawn": "summon",
    "create_creature": "conjure_creature",
    "spawn_creature": "conjure_creature",
    "create_item": "conjure_item",
    "spawn_item": "conjure_item",
    "place_tile": "create_tiles",
    "set_tiles": "create_tiles",
    "tile_effect": "create_tiles",
    "area_effect": "area_damage",
    "explosion": "area_damage",
    "blast": "area_damage",
    "trigger": "create_trigger",
    "ward": "create_trigger",
    "reaction": "create_trigger",
    "contingency": "create_trigger",
    "delayed_reaction": "create_trigger",
    "prophecy": "create_promise",
    "prophesy": "create_promise",
    "foretell": "create_promise",
    "promise": "create_promise",
    "create_prophecy": "create_promise",
}

# Status names that the LLM might use as effect type directly.
_STATUS_AS_TYPE: dict[str, tuple[str, str]] = {
    "regenerating": ("add_status", "regenerating"),
    "regenerate": ("add_status", "regenerating"),
    "burning": ("add_status", "burning"),
    "poisoned": ("add_status", "poisoned"),
    "frozen": ("add_status", "frozen"),
    "stunned": ("add_status", "stunned"),
    "slowed": ("add_status", "slowed"),
    "hasted": ("add_status", "hasted"),
    "invisible": ("add_status", "invisible"),
    "berserk": ("add_status", "berserk"),
    "empowered": ("add_status", "empowered"),
    "warded": ("add_status", "warded"),
    "cursed": ("add_status", "cursed"),
    "bleeding": ("add_status", "bleeding"),
    "rooted": ("add_status", "rooted"),
    "webbed": ("add_status", "webbed"),
    "confused": ("add_status", "confused"),
    "frightened": ("add_status", "frightened"),
    "marked": ("add_status", "marked"),
    "silenced": ("add_status", "silenced"),
}


_KNOWN_TILE_NAMES = frozenset(TILE_ALIASES)


def _normalize_create_tiles_tile(e: dict[str, Any]) -> dict[str, Any]:
    """Infer tile from tags/name when tile field is missing or uses an unrecognized char."""
    tile_val = str(e.get("tile") or "").strip().lower()
    if tile_val in _KNOWN_TILE_NAMES:
        return e
    _raw_tags = e.get("tags") or []
    tags = [
        str(t).lower()
        for t in (_raw_tags if isinstance(_raw_tags, list) else [_raw_tags])
    ]
    name_fields = [
        str(e.get("name") or ""),
        str(e.get("terrain_type") or ""),
        str(e.get("substance") or ""),
        str(e.get("material") or ""),
    ]
    ctx = " ".join(tags + name_fields + [tile_val]).lower()
    if any(
        w in ctx
        for w in (
            "fire",
            "lava",
            "magma",
            "flame",
            "ignite",
            "burn",
            "scorch",
            "incinerate",
        )
    ):
        inferred = "fire"
    elif any(w in ctx for w in ("slick", "ice_floor", "frost_floor")):
        inferred = "slick_ice"
    elif any(
        w in ctx for w in ("poison", "acid", "toxic", "fume", "vapor", "gas", "venom")
    ):
        inferred = "poison_cloud"
    elif any(w in ctx for w in ("smoke", "fog", "mist", "haze", "cloud", "steam")):
        inferred = "mist"
    elif any(
        w in ctx
        for w in (
            "vine",
            "web",
            "thorn",
            "net",
            "caltrop",
            "snare",
            "entangle",
            "trip",
            "hazard",
            "spike",
            "trap",
        )
    ):
        inferred = "vines"
    elif any(
        w in ctx
        for w in ("rubble", "debris", "stone", "rock", "ruin", "bone", "gravel")
    ):
        inferred = "rubble"
    elif any(
        w in ctx for w in ("ice_wall", "wall_ice", "barrier", "block", "iron", "bars")
    ):
        inferred = "ice_wall"
    elif any(
        w in ctx for w in ("water", "flood", "swamp", "mud", "pool", "liquid", "puddle")
    ):
        inferred = "water"
    elif any(w in ctx for w in ("ice", "frost", "frozen", "cold", "chill", "freeze")):
        inferred = "slick_ice"
    else:
        return e
    e = dict(e)
    e["tile"] = inferred
    return e


def _infer_effect_from_fields(e: dict[str, Any]) -> dict[str, Any] | None:
    """When effect type is unparseable natural language, infer the effect type from other keys."""
    result = dict(e)
    if "damage_type" in e or (
        isinstance(e.get("amount"), (int, float))
        and "tile" not in e
        and "status" not in e
    ):
        result["type"] = "damage"
        result.setdefault("target", "nearest_enemy")
        result.setdefault("amount", 5)
        return result
    if "status" in e:
        result["type"] = "add_status"
        result.setdefault("target", "nearest_enemy")
        return result
    if "tile" in e:
        result["type"] = "create_tiles"
        result.setdefault("target", "player")
        return result
    if "name" in e and any(k in e for k in ("hp", "faction", "attack", "template")):
        result["type"] = "conjure_creature"
        result.setdefault("faction", "ally")
        return result
    return None


def _trigger_is_once(obj: dict[str, Any]) -> bool:
    """Models express single-use triggers many ways: "once": true, condition/
    conditions dicts whose type is "once" / "once_per_combat" / etc."""
    if obj.get("once") is True:
        return True
    for key in ("condition", "conditions"):
        cond = obj.get(key)
        if isinstance(cond, dict) and str(cond.get("type") or "").lower().startswith(
            "once"
        ):
            return True
        if isinstance(cond, str) and cond.lower().startswith("once"):
            return True
    return False


def _infer_trigger_action(text: str) -> dict[str, Any] | None:
    """Convert a natural-language trigger action string to a structured effect dict."""
    t = text.lower()
    if any(
        w in t
        for w in ("fire", "flame", "burn", "blaze", "ignite", "scorch", "incinerate")
    ):
        return {
            "type": "damage",
            "target": "trigger_source",
            "amount": 5,
            "damage_type": "fire",
        }
    if any(w in t for w in ("ice", "frost", "freeze", "cold", "chill", "frozen")):
        return {
            "type": "damage",
            "target": "trigger_source",
            "amount": 5,
            "damage_type": "frost",
        }
    if any(
        w in t for w in ("lightning", "thunder", "electric", "shock", "spark", "volt")
    ):
        return {
            "type": "damage",
            "target": "trigger_source",
            "amount": 5,
            "damage_type": "lightning",
        }
    if any(w in t for w in ("poison", "toxic", "venom", "acid")):
        return {
            "type": "damage",
            "target": "trigger_source",
            "amount": 5,
            "damage_type": "poison",
        }
    if any(w in t for w in ("heal", "restore", "mend", "recover", "regenerate")):
        return {"type": "heal", "target": "player", "amount": 5}
    if any(
        w in t
        for w in (
            "retaliate",
            "counter",
            "reflect",
            "strike",
            "attack",
            "damage",
            "hit",
            "hurt",
            "wound",
        )
    ):
        return {
            "type": "damage",
            "target": "trigger_source",
            "amount": 5,
            "damage_type": "physical",
        }
    return None


def _normalize_resolution(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("outcome"), dict):
        outcome = data["outcome"]
        merged = dict(data)
        if not merged.get("effects"):
            if outcome.get("effects") is not None:
                merged["effects"] = outcome["effects"]
            elif outcome.get("effect") is not None:
                merged["effect"] = outcome["effect"]
        if not merged.get("costs"):
            if outcome.get("costs") is not None:
                merged["costs"] = outcome["costs"]
            elif outcome.get("cost") is not None:
                merged["cost"] = outcome["cost"]
        if not merged.get("outcome_text"):
            for key in ("message", "description", "visual", "text"):
                if isinstance(outcome.get(key), str) and outcome[key].strip():
                    merged["outcome_text"] = outcome[key]
                    break
        data = merged

    # If the entire resolution is wrapped inside an "outcome" key, unwrap it.
    if isinstance(data.get("outcome"), dict) and (
        "effects" in data["outcome"] or "costs" in data["outcome"]
    ):
        data = data["outcome"]

    # Coerce explicit null effects/costs to empty lists so the validator doesn't choke.
    if data.get("effects") is None and "effects" in data:
        data = dict(data)
        data["effects"] = []
    if data.get("costs") is None and "costs" in data:
        data = dict(data)
        data["costs"] = []

    # Provide a default rejected_reason when accepted is False but no reason given.
    if (
        data.get("accepted") is False
        and not str(data.get("rejected_reason") or "").strip()
    ):
        data = dict(data)
        data["rejected_reason"] = "The wild magic refuses."

    # Accept common flavor fields. Prefer actual prose over status words like "success".
    if not data.get("outcome_text"):
        for alias in ("message", "response", "text", "description", "outcome"):
            if isinstance(data.get(alias), str) and data[alias].strip():
                data = dict(data)
                data["outcome_text"] = data[alias]
                break
    # Strip non-prose outcome texts (single-word status words from a poorly-behaved LLM).
    _JUNK_OUTCOMES = {
        "success",
        "ok",
        "okay",
        "done",
        "accepted",
        "yes",
        "no",
        "null",
        "none",
        "true",
        "false",
        "error",
        "failed",
        "failure",
        "reject",
        "rejected",
        "completed",
    }
    if isinstance(data.get("outcome_text"), str):
        _ot = data["outcome_text"].strip().rstrip(".!?").lower()
        if _ot in _JUNK_OUTCOMES or len(_ot) < 4:
            data = dict(data)
            data["outcome_text"] = ""

    # Coerce costs dict {"mana": 5} into a list [{type: mana, amount: 5}].
    if isinstance(data.get("costs"), dict):
        raw_dict = data["costs"]
        coerced = _coerce_cost_dict(raw_dict)
        data = dict(data)
        data["costs"] = coerced

    # Normalize "cost" (singular string like "mana 5" or "5 mana") into the costs list.
    if "costs" not in data and isinstance(data.get("cost"), str):
        _cost_str = data["cost"].strip().lower()
        if _cost_str == "item" and data.get("item_used"):
            data = dict(data)
            data["costs"] = [
                {
                    "type": "item",
                    "item": str(data.get("item_used")),
                    "amount": int(data.get("quantity_used") or data.get("amount") or 1),
                }
            ]
        else:
            _cost_match = re.match(r"(\w+)\s+(\d+)|(\d+)\s+(\w+)", _cost_str)
            if _cost_match:
                g = _cost_match.groups()
                _ctype, _camt = (g[0], g[1]) if g[0] else (g[3], g[2])
                if _ctype in {"mana", "health", "hp", "max_health", "max_mana"}:
                    _ctype = "health" if _ctype == "hp" else _ctype
                    try:
                        data = dict(data)
                        data["costs"] = [{"type": _ctype, "amount": int(_camt)}]
                    except (ValueError, TypeError):
                        pass

    # Normalize "cost" (singular dict like {"mana": 3}) into the costs list.
    if "costs" not in data and isinstance(data.get("cost"), dict):
        costs = _coerce_cost_dict(data["cost"])
        if costs:
            data = dict(data)
            data["costs"] = costs

    # Rescue trigger-shaped responses where top-level "effects" is intended as the
    # trigger payload instead of the resolution's effect list.
    top_effect_type = (
        str(data.get("effect") or data.get("effect_type") or data.get("type") or "")
        .lower()
        .strip()
    )
    if top_effect_type in {
        "create_trigger",
        "trigger",
        "ward",
        "reaction",
        "contingency",
        "delayed_reaction",
    } and isinstance(data.get("effects"), list):
        trigger_effect = {
            "type": _EFFECT_TYPE_ALIASES.get(top_effect_type, top_effect_type),
            "effects": data["effects"],
        }
        for key in {
            "target",
            "trigger",
            "on",
            "charges",
            "duration",
            "turns",
            "name",
            "display_name",
            "expiry_text",
        }:
            if key in data:
                trigger_effect[key] = data[key]
        data = dict(data)
        data["effects"] = [trigger_effect]

    # If effects is missing/empty, try to reconstruct from common alternate structures.
    if not data.get("effects"):
        # Case 1: "effect" (singular) at top level — may be a string ("damage") or dict ({"type": "damage", ...}).
        if data.get("effect"):
            effect_raw = data["effect"]
            if isinstance(effect_raw, dict):
                # Already a full effect object — use directly, merge details if present.
                effect_obj: dict[str, Any] = dict(effect_raw)
                outer_details = data.get("details")
                if isinstance(outer_details, dict):
                    for k, v in outer_details.items():
                        if k not in effect_obj:
                            effect_obj[k] = v
            else:
                effect_obj = _effect_from_text(str(effect_raw)) or {
                    "type": str(effect_raw)
                }
                # Merge safe top-level fields into the effect so that patterns like
                # {"effect":"area_status","target":"all enemies","status":"slowed",...}
                # produce a complete effect object.
                _EFFECT_TOP_FIELDS = {
                    "target",
                    "status",
                    "duration",
                    "radius",
                    "tile",
                    "amount",
                    "damage_type",
                    "x",
                    "y",
                    "name",
                    "faction",
                    "template",
                    "hp",
                    "max_hp",
                    "attack",
                    "defense",
                    "char",
                    "count",
                    "tags",
                    "hollow",
                    "ring",
                    "perimeter",
                    "include_player",
                    "affects",
                    "display_name",
                    "expiry_text",
                    "item",
                    "material",
                    "quantity",
                    "dx",
                    "dy",
                    "distance",
                    "positions",
                    "tiles",
                    "creature",
                    "trigger",
                    "on",
                    "effects",
                    "effect",
                    "action",
                    "charges",
                    "shape",
                    "pattern",
                    "width",
                    "length",
                    "from",
                    "to",
                }
                for _k in _EFFECT_TOP_FIELDS:
                    if _k in data and _k not in effect_obj:
                        effect_obj[_k] = data[_k]
            details = data.get("details")
            if isinstance(details, dict):
                for k, v in details.items():
                    if k not in {
                        "costs",
                        "cost",
                        "rules_applied",
                        "supported_effects_used",
                        "supported_costs_used",
                        "description",
                    }:
                        effect_obj[k] = v
                # Rescue costs nested inside details if not already at top level.
                if not data.get("costs") and isinstance(details.get("costs"), dict):
                    raw_costs = details["costs"]
                    rescued: list[dict[str, Any]] = []
                    for key, val in raw_costs.items():
                        if key == "quantity":
                            continue
                        if key in {"mana", "health", "max_health", "max_mana"}:
                            try:
                                rescued.append({"type": key, "amount": int(val)})
                            except (TypeError, ValueError):
                                pass
                        elif key == "item":
                            rescued.append(
                                {
                                    "type": "item",
                                    "item": str(val),
                                    "amount": int(details.get("quantity", 1)),
                                }
                            )
                    if rescued:
                        data = dict(data)
                        data["costs"] = rescued
            data = dict(data)
            data["effects"] = [effect_obj]
        # Case 2: "details" at resolution level describes a single effect.
        elif isinstance(data.get("details"), dict):
            details = data["details"]
            if "effect" in details or "type" in details:
                effect = dict(details)
                if "effect" in effect and "type" not in effect:
                    effect["type"] = effect.pop("effect")
                if effect.get("type"):
                    data = dict(data)
                    data["effects"] = [effect]

    # Normalize element-name effect types and flavor status names.
    effects = data.get("effects")
    if isinstance(effects, list):
        # Flatten: if an effect's "type" field is itself a list of effect dicts, inline them.
        expanded: list[Any] = []
        for e in effects:
            if isinstance(e, list):
                expanded.extend(e)
            elif isinstance(e, dict) and isinstance(e.get("type"), list):
                for nested in e["type"]:
                    if isinstance(nested, dict):
                        expanded.append(nested)
            else:
                expanded.append(e)
        effects = expanded
        normalized_effects = []
        for e in effects:
            if isinstance(e, dict):
                e = _flatten_nested_effect(e)
                et = str(e.get("type") or "").lower().strip()
                # Infer type from fields when absent.
                if not et:
                    if "status" in e:
                        et = "add_status"
                    elif "amount" in e or "damage" in e:
                        et = "damage"
                    elif "hp" in e or "heal" in e:
                        et = "heal"
                    elif "mana" in e:
                        et = "restore_mana"
                    elif "tile" in e:
                        et = "create_tiles"
                    elif "creature" in e or "name" in e:
                        et = "conjure_creature"
                    if et:
                        e = dict(e)
                        e["type"] = et
                # Apply effect type aliases.
                if et and et not in SUPPORTED_EFFECTS:
                    if et in _EFFECT_TYPE_ALIASES:
                        mapped = _EFFECT_TYPE_ALIASES[et]
                        e = dict(e)
                        # "regenerate"/"regen" aliases need status inferred.
                        if mapped == "add_status" and not e.get("status"):
                            if et in {"regenerate", "regeneration", "regen"}:
                                e["status"] = "regenerating"
                        e["type"] = mapped
                        et = mapped
                    elif et in _STATUS_AS_TYPE:
                        mapped_type, mapped_status = _STATUS_AS_TYPE[et]
                        e = dict(e)
                        if not e.get("status"):
                            e["status"] = mapped_status
                        e["type"] = mapped_type
                        et = mapped_type
                    elif et in _ELEMENT_DAMAGE_ALIASES:
                        e = dict(e)
                        e["damage_type"] = _ELEMENT_DAMAGE_ALIASES[et]
                        e["type"] = "damage"
                        et = "damage"
                        if "target" not in e:
                            e["target"] = "nearest_enemy"
                        if "amount" not in e:
                            e["amount"] = 5
                    else:
                        recovered = _effect_from_text(et)
                        if recovered:
                            e = recovered
                            et = str(e.get("type") or "").lower().strip()
                        elif len(et) > 20:
                            # Type field is natural language. Infer effect type from other fields.
                            recovered = _infer_effect_from_fields(e)
                            if recovered:
                                e = recovered
                                et = str(e.get("type") or "").lower().strip()
                # Legacy element-type path (already in ELEMENT_DAMAGE_ALIASES but not in SUPPORTED_EFFECTS).
                elif et in _ELEMENT_DAMAGE_ALIASES and et not in SUPPORTED_EFFECTS:
                    e = dict(e)
                    e["damage_type"] = _ELEMENT_DAMAGE_ALIASES[et]
                    e["type"] = "damage"
                    et = "damage"
                    if "target" not in e:
                        e["target"] = "nearest_enemy"
                    if "amount" not in e:
                        e["amount"] = 5
                # Normalize flavor status names in add_status / area_status effects.
                if et in {"add_status", "area_status"}:
                    raw_status = (
                        str(e.get("status") or "").strip().lower().replace(" ", "_")
                    )
                    if raw_status and raw_status not in MECHANICAL_STATUSES:
                        canonical = _STATUS_FLAVOR_ALIASES.get(raw_status)
                        if canonical:
                            e = dict(e)
                            if not e.get("display_name"):
                                e["display_name"] = raw_status.replace("_", " ")
                            e["status"] = canonical
                # Ensure add_status effects that inferred status from regen/type have a target.
                if et == "add_status" and "target" not in e:
                    e = dict(e)
                    e["target"] = "player"
                if "target" in e:
                    e = dict(e)
                    e["target"] = _normalize_target_text(e["target"])
                if "origin" in e:
                    e = dict(e)
                    e["origin"] = _normalize_target_text(e["origin"])
                # Flatten conjure_creature "creature"/"entity" sub-dict into the effect.
                if et in {"conjure_creature", "summon"}:
                    for _sub_key in ("creature", "entity"):
                        if isinstance(e.get(_sub_key), dict):
                            e = dict(e)
                            sub_data = e.pop(_sub_key)
                            for _ck, _cv in sub_data.items():
                                if _ck not in e:
                                    e[_ck] = _cv
                # Normalize "positions" array → "tiles" array for create_tiles.
                if et in {"conjure_item", "spawn_item"} and isinstance(
                    e.get("item"), dict
                ):
                    e = dict(e)
                    item_data = e.pop("item")
                    for _ik, _iv in item_data.items():
                        if _ik not in e:
                            e[_ik] = _iv
                if et == "schedule_event":
                    e = _normalize_schedule_event(e)
                # Normalize create_trigger: handle many LLM structural variations.
                if et == "create_trigger":
                    e = dict(e)
                    # LLM sometimes nests the whole trigger config under "trigger"
                    # (or "on") as a dict.
                    if isinstance(e.get("on"), dict) and not isinstance(
                        e.get("trigger"), dict
                    ):
                        e["trigger"] = e.pop("on")
                    if isinstance(e.get("trigger"), dict):
                        trigger_obj = e.pop("trigger")
                        trigger_str = str(
                            trigger_obj.get("type")
                            or trigger_obj.get("trigger")
                            or trigger_obj.get("on")
                            or trigger_obj.get("event")
                            or "on_next_spell"
                        )
                        e["trigger"] = trigger_str
                        if not e.get("effects"):
                            nested = trigger_obj.get("effects") or trigger_obj.get(
                                "effect"
                            )
                            action = trigger_obj.get("action")
                            if isinstance(nested, list):
                                e["effects"] = nested
                            elif isinstance(nested, dict):
                                e["effects"] = [nested]
                            elif isinstance(action, dict):
                                e["effects"] = [action]
                            elif isinstance(action, str) and action.strip():
                                inferred = _infer_trigger_action(action)
                                if inferred:
                                    e["effects"] = [inferred]
                        if not e.get("charges") and _trigger_is_once(trigger_obj):
                            e["charges"] = 1
                    if not e.get("charges") and _trigger_is_once(e):
                        e["charges"] = 1
                    e.pop("once", None)
                    # LLM uses "action" (singular dict/string) instead of "effects" (list).
                    if not e.get("effects"):
                        single = e.pop("action", None) or e.pop("effect", None)
                        if isinstance(single, dict):
                            e["effects"] = [single]
                        elif isinstance(single, list):
                            e["effects"] = single
                        elif isinstance(single, str) and single.strip():
                            inferred = _infer_trigger_action(single)
                            if inferred:
                                e["effects"] = [inferred]
                    if isinstance(e.get("effect"), str):
                        e.pop("effect", None)
                    e.pop("conditions", None)
                # For create_tiles: infer tile from tags/name when tile is unrecognized.
                if et in {"create_tiles", "create_tile", "set_tile"}:
                    e = _normalize_create_tiles_tile(e)
                if (
                    et in {"create_tiles", "create_tile", "set_tile"}
                    and "positions" in e
                    and "tiles" not in e
                ):
                    e = dict(e)
                    raw_tile = str(e.get("tile") or ".").lower()
                    e["tiles"] = [
                        {
                            "x": p.get("x", 0),
                            "y": p.get("y", 0),
                            "tile": str(p.get("tile") or raw_tile),
                        }
                        for p in e.pop("positions")
                        if isinstance(p, dict)
                    ]
            normalized_effects.append(e)
        data = dict(data)
        data["effects"] = normalized_effects

    # Flatten "data"/"details"/"params" nesting in both effects and costs.
    effects = data.get("effects")
    if isinstance(effects, list):
        data = dict(data)
        data["effects"] = [
            _flatten_nested_effect(e) if isinstance(e, dict) else e for e in effects
        ]

    costs = data.get("costs")
    if isinstance(costs, list):
        data = dict(data)
        data["costs"] = [
            _flatten_nested_effect(c) if isinstance(c, dict) else c for c in costs
        ]

    # Rescue cost entries whose type is actually a known effect type. The LLM sometimes
    # expresses a spell's mechanical consequence (e.g. "the wraith becomes weak to
    # radiant damage") as a cost entry like {"type": "add_weakness", ...} instead of
    # putting it in the effects list, which would otherwise fail validation outright.
    costs = data.get("costs")
    if isinstance(costs, list):
        rescued_effects: list[dict[str, Any]] = []
        remaining_costs: list[Any] = []
        for c in costs:
            c_type = (
                str(c.get("type") or "").lower().strip() if isinstance(c, dict) else ""
            )
            if c_type in SUPPORTED_EFFECTS and c_type not in SUPPORTED_COSTS:
                rescued = dict(c)
                if "target" in rescued:
                    rescued["target"] = _normalize_target_text(rescued["target"])
                rescued_effects.append(rescued)
            else:
                remaining_costs.append(c)
        if rescued_effects:
            data = dict(data)
            data["costs"] = remaining_costs
            data["effects"] = list(data.get("effects") or []) + rescued_effects

    return data


def _coerce_cost_dict(raw_dict: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(raw_dict.get("type"), str):
        cost = dict(raw_dict)
        if "quantity" in cost and "amount" not in cost:
            cost["amount"] = cost["quantity"]
        return [cost]
    coerced: list[dict[str, Any]] = []
    for key, val in raw_dict.items():
        if key in {"mana", "health", "max_health", "max_mana", "hp"}:
            try:
                amount = int(val)
                if amount > 0:
                    coerced.append(
                        {"type": "health" if key == "hp" else key, "amount": amount}
                    )
            except (TypeError, ValueError):
                pass
        elif key == "item":
            coerced.append(
                {
                    "type": "item",
                    "item": str(val),
                    "amount": int(raw_dict.get("quantity", 1)),
                }
            )
    return coerced


def _effect_from_text(text: str) -> dict[str, Any] | None:
    normalized = text.lower().strip()
    if not normalized:
        return None
    digit_turns = re.search(r"\b(?:in|after)\s+(\d+)\s+turn", normalized)
    if digit_turns:
        turns = int(digit_turns.group(1))
    else:
        word_numbers = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        word_turns = re.search(
            r"\b(?:in|after)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+turn",
            normalized,
        )
        turns = word_numbers.get(word_turns.group(1), 3) if word_turns else 3
    if not any(
        word in normalized for word in ["arrive", "appears", "appear", "summon"]
    ):
        return None

    name = "summoned creature"
    for pattern in [
        r"scheduled\s+(?:a|an|the)?\s*([a-z][a-z _-]+?)\s+to\s+arrive",
        r"(?:a|an|the)\s+([a-z][a-z _-]+?)\s+(?:should|will|shall)?\s*arrive",
        r"summon\s+(?:a|an|the)?\s*([a-z][a-z _-]+)",
    ]:
        match = re.search(pattern, normalized)
        if match:
            candidate = " ".join(match.group(1).split())
            if candidate:
                name = candidate[:40]
                break
    faction = (
        "ally"
        if not any(
            word in normalized
            for word in ["hostile", "enemy", "foe", "threat", "collector"]
        )
        else "enemy"
    )
    return {
        "type": "schedule_event",
        "turns": turns,
        "event_type": "summon",
        "name": name,
        "char": _effect_char(name),
        "hp": 8,
        "attack": 3,
        "faction": faction,
    }


def _effect_char(name: str) -> str:
    for char in name:
        if char.isascii() and char.isalpha():
            return char.lower()
    return "e"


def _normalize_schedule_event(effect: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(effect)
    if "turn" in normalized and "turns" not in normalized:
        normalized["turns"] = normalized["turn"]
    entity = normalized.get("entity")
    if not isinstance(entity, dict) and isinstance(normalized.get("data"), dict):
        nested_data = normalized["data"]
        if isinstance(nested_data.get("entity"), dict):
            entity = nested_data["entity"]
            normalized["entity"] = entity
    if isinstance(entity, dict):
        for key in [
            "name",
            "char",
            "hp",
            "max_hp",
            "attack",
            "defense",
            "faction",
            "tags",
            "resistances",
            "weaknesses",
        ]:
            if key in entity and key not in normalized:
                normalized[key] = entity[key]
    event_text = (
        str(normalized.get("event") or normalized.get("event_type") or "")
        .lower()
        .strip()
        .replace(" ", "_")
        .replace("-", "_")
    )
    if "event_type" not in normalized:
        if (
            isinstance(entity, dict)
            or "arrival" in event_text
            or "summon" in event_text
        ):
            normalized["event_type"] = "summon"
        elif any(key in normalized for key in ["amount", "damage_type"]):
            normalized["event_type"] = "damage"
        else:
            normalized["event_type"] = "message"
    if normalized.get("event_type") == "summon":
        normalized.setdefault("name", "summoned creature")
        normalized.setdefault("char", _effect_char(str(normalized["name"])))
        normalized.setdefault("hp", normalized.get("max_hp", 8))
        normalized.setdefault("attack", 3)
        normalized.setdefault("faction", "ally")
    return normalized


def _normalize_target_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.lower().strip().replace("-", "_").replace(" ", "_")
    if normalized.startswith("the_"):
        normalized = normalized[4:]
    target_aliases = {
        "self": "player",
        "you": "player",
        "me": "player",
        "caster": "player",
        "all_enemies": "all_enemies",
        "all_foes": "all_enemies",
        "all_hostiles": "all_enemies",
        "every_enemy": "all_enemies",
        "enemies": "all_enemies",
        "foes": "all_enemies",
        "all_creatures": "all",
        "everyone": "all",
        "everything": "all",
    }
    if normalized in target_aliases:
        return target_aliases[normalized]
    if normalized.startswith("nearest_") and any(
        word in normalized
        for word in [
            "enemy",
            "foe",
            "hostile",
            "monster",
            "creature",
            "goblin",
            "slime",
            "bat",
        ]
    ):
        return "nearest_enemy"
    if normalized.startswith("closest_") and any(
        word in normalized
        for word in [
            "enemy",
            "foe",
            "hostile",
            "monster",
            "creature",
            "goblin",
            "slime",
            "bat",
        ]
    ):
        return "nearest_enemy"
    return value


def _flatten_nested_effect(effect: dict[str, Any]) -> dict[str, Any]:
    for key in ("data", "details", "params", "parameters"):
        nested = effect.get(key)
        if isinstance(nested, dict):
            merged = dict(nested)
            merged.update({k: v for k, v in effect.items() if k != key})
            return merged
    return effect


def _nearest_enemy_id(context: dict[str, Any]) -> str | None:
    player_position = context["player"]["position"]
    px = player_position["x"]
    py = player_position["y"]
    enemies = [
        entity
        for entity in context.get("nearby_entities", [])
        if entity.get("kind") == "actor"
        and entity.get("faction") == "enemy"
        and entity.get("hp", 0) > 0
    ]
    if not enemies:
        return None
    enemies.sort(
        key=lambda entity: (
            abs(entity["position"]["x"] - px) + abs(entity["position"]["y"] - py)
        )
    )
    return str(enemies[0]["id"])
