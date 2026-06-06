from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
from typing import Any, Protocol

from .models import MECHANICAL_STATUSES


SUPPORTED_STATUS_TEXT = ", ".join(sorted(MECHANICAL_STATUSES))

SYSTEM_PROMPT = """You are the Wild Magic referee for a turn-based tile roguelike.
Resolve the player's typed spell by returning exactly one JSON object and no prose.
Do not include chain-of-thought, markdown, comments, or <think> text.

Required top-level shape:
{"accepted": true, "severity": "minor|moderate|major|catastrophic", "outcome_text": "short log message", "effects": [], "costs": [], "rejected_reason": null}

Use only the effects and costs needed for this one spell. Do not copy every available option.
Typical minor/moderate spell: 1-3 effects and 1-2 costs.
Typical major spell: 2-5 effects and 2-4 costs.
Catastrophic spell: dangerous effects, severe permanent costs, or rejection.

Effect catalog:
- damage: target, amount, damage_type.
- area_damage: target, radius 0-4, amount, damage_type, include_player boolean, affects "enemies|non_player|allies|all".
- heal or restore_mana: target, amount.
- teleport: target, x, y.
- push or pull: target, origin or dx/dy, distance.
- create_tile or create_tiles: x/y or target, tile, radius, duration.
- add_status or remove_status: target, status, duration.
- summon: name, faction, x, y, hp, attack, defense, char.
- spawn_item: name, item_type, x, y, char, material, quantity, tags.
- conjure_item: template, name, material, tags, target, placement, count.
- conjure_creature: template, name, faction, tags, placement, count.
- modify_inventory, transform_entity, change_faction, add_tag, remove_tag, add_resistance, add_weakness, set_flag, schedule_event, message.

Cost catalog:
- mana, health, max_health, max_mana, item, status, curse.
- Costs are discovered after casting. Effects happen first, then costs.
- If a cost is odd or poetic, use a curse instead of inventing a new status.

Balance rules:
- If the spell is a win button, infinite resource exploit, or outside the genre, reject it or make it catastrophic.
- Damage above 8 needs a meaningful cost. Damage above 16 needs a severe cost or rejection.
- Area effects should usually be weaker than single-target effects.
- Use affects "enemies" for spells that should only harm foes.
- Keep effects local and concrete. Prefer entity ids from context.
- For permanent terrain, omit duration or use "permanent"; otherwise duration must be 1 or more.
- For body-part changes, use damage/status/conjure_item instead of transform_entity unless the whole creature changes.
- For tracking, glowing shadow, locate, or reveal spells, use add_status with status "revealed" on the target.

Useful tiles: floor, wall, door, open_door, stairs_down, stairs_up, water, fire, slick_ice, ice_wall, poison_cloud, vines, rubble, mist.
Supported statuses: {supported_statuses}.
Use status only for supported mechanical statuses.

Conjuration:
- For arbitrary new objects or creatures, prefer template-backed conjuration.
- Item templates: generic_object, body_part, glass_shard, ritual_component, weapon_like, food, key_like, treasure.
- Creature templates: tiny_swarm, small_beast, humanoid, construct, spirit, slime, summoned_servant, hazard_creature.
- Creative names, materials, and tags are allowed, but mechanics come from the chosen template.

Good examples:
{"accepted": true, "severity": "minor", "outcome_text": "A blue shadow pins the target's location in your mind.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "revealed", "duration": 6}], "costs": [{"type": "mana", "amount": 2}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "A tiny sun circles you and lashes out at foes.", "effects": [{"type": "summon", "name": "tiny sun", "faction": "ally", "hp": 4, "attack": 0, "defense": 1, "char": "o"}, {"type": "area_damage", "target": "player", "radius": 3, "amount": 4, "damage_type": "fire", "include_player": false, "affects": "enemies"}], "costs": [{"type": "mana", "amount": 6}, {"type": "status", "status": "burning", "duration": 2}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "The goblin spits out a brittle little treasure.", "effects": [{"type": "damage", "target": "nearest_enemy", "amount": 3, "damage_type": "physical"}, {"type": "add_status", "target": "nearest_enemy", "status": "bleeding", "duration": 3}, {"type": "conjure_item", "template": "body_part", "name": "glass teeth", "material": "glass", "tags": ["fragile", "tooth"], "target": "nearest_enemy", "placement": "target_tile"}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}
{"accepted": true, "severity": "minor", "outcome_text": "Blue webbing pins the target in place.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "webbed", "duration": 3}, {"type": "conjure_item", "template": "generic_object", "name": "sticky blue webbing", "material": "silk", "target": "nearest_enemy", "placement": "target_tile"}], "costs": [{"type": "item", "item": "chalk", "amount": 1}], "rejected_reason": null}
{"accepted": false, "severity": "catastrophic", "outcome_text": "", "effects": [], "costs": [], "rejected_reason": "Reality refuses to become that convenient."}
""".replace("{supported_statuses}", SUPPORTED_STATUS_TEXT)


SUPPORTED_EFFECTS = {
    "damage",
    "area_damage",
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
    "modify_inventory",
    "transform_entity",
    "change_faction",
    "add_tag",
    "remove_tag",
    "add_resistance",
    "add_weakness",
    "set_flag",
    "schedule_event",
    "add_curse",
    "message",
}


SUPPORTED_COSTS = {"mana", "health", "hp", "max_health", "max_mana", "item", "status", "curse"}


@dataclass
class MagicResolution:
    data: dict[str, Any] | None
    technical_failure: bool
    error: str | None = None
    provider_name: str = "unknown"
    raw_response: str | None = None
    audit_path: str | None = None


class WildMagicProvider(Protocol):
    name: str

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        ...


class OllamaWildMagicProvider:
    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.model = model or os.environ.get("WILDMAGIC_MODEL", "qwen3:8b")
        self.base_url = normalize_ollama_url(base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else ollama_timeout_seconds()

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "think": ollama_thinking_enabled(),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
            ],
            "options": {
                "temperature": ollama_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_num_predict(),
            },
        }
        if ollama_json_format_enabled():
            payload["format"] = "json"
        try:
            data = self._post_chat(payload)
        except ValueError as exc:
            if "Unexpected empty grammar stack" not in str(exc) or "format" not in payload:
                raise
            retry_payload = dict(payload)
            retry_payload.pop("format", None)
            data = self._post_chat(retry_payload)
        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama response did not include message.content")
        return content

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = parse_ollama_error_body(body)
            raise ValueError(f"Ollama HTTP {exc.code}: {detail or exc.reason}") from exc
        return data


class MockWildMagicProvider:
    name = "mock"

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        text = spell.lower().strip()
        player = context["player"]
        px = player["position"]["x"]
        py = player["position"]["y"]
        nearest_enemy = _nearest_enemy_id(context)

        if any(word in text for word in ["win game", "infinite", "immortal", "kill everything", "kill all"]):
            return json.dumps(
                {
                    "accepted": False,
                    "severity": "catastrophic",
                    "outcome_text": "",
                    "effects": [],
                    "costs": [],
                    "rejected_reason": "Reality clenches shut around that spell. Your turn is lost.",
                }
            )

        if "teeth" in text and "glass" in text:
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "moderate",
                    "outcome_text": "A brittle clatter answers from inside the mouth.",
                    "effects": [
                        {"type": "damage", "target": nearest_enemy or "nearest_enemy", "amount": 3, "damage_type": "physical"},
                        {"type": "add_status", "target": nearest_enemy or "nearest_enemy", "status": "bleeding", "duration": 4},
                        {
                            "type": "conjure_item",
                            "template": "body_part",
                            "name": "glass teeth",
                            "material": "glass",
                            "tags": ["sharp", "fragile", "tooth"],
                            "target": nearest_enemy or "nearest_enemy",
                            "placement": "target_tile",
                            "count": 1,
                        },
                    ],
                    "costs": [{"type": "mana", "amount": 3}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["ant", "ants"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "major",
                    "outcome_text": "The walls darken with thousands of disciplined legs.",
                    "effects": [
                        {
                            "type": "conjure_creature",
                            "template": "tiny_swarm",
                            "name": "ant swarm",
                            "count": 6,
                            "faction": "enemy",
                            "tags": ["ant", "wall_born"],
                            "placement": "near_walls",
                            "target": "player",
                        }
                    ],
                    "costs": [
                        {"type": "mana", "amount": 4},
                        {"type": "status", "status": "crawling_skin", "duration": 6},
                    ],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["heal", "mend", "restore"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "moderate",
                    "outcome_text": "Green light stitches through your ribs.",
                    "effects": [{"type": "heal", "target": "player", "amount": 6}],
                    "costs": [{"type": "mana", "amount": 4}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["teleport", "blink", "swap"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "moderate",
                    "outcome_text": "Space turns sideways.",
                    "effects": [{"type": "teleport", "target": "player", "x": px + 3, "y": py}],
                    "costs": [{"type": "mana", "amount": 3}, {"type": "health", "amount": 1}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["wall", "ice", "block"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "Frost writes a hard line on the floor.",
                    "effects": [{"type": "create_tile", "x": px + 1, "y": py, "tile": "ice_wall"}],
                    "costs": [{"type": "mana", "amount": 2}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["summon", "call", "create friend"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "major",
                    "outcome_text": "A helpful shape steps out of a bad idea.",
                    "effects": [
                        {
                            "type": "summon",
                            "name": "chalk homunculus",
                            "faction": "ally",
                            "x": px + 1,
                            "y": py + 1,
                            "hp": 5,
                            "attack": 1,
                            "char": "h",
                        }
                    ],
                    "costs": [
                        {"type": "item", "item": "chalk", "amount": 1},
                        {
                            "type": "curse",
                            "id": "borrowed_hands",
                            "name": "Borrowed Hands",
                            "description": "Sometimes your fingers remember someone else's work.",
                        },
                    ],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["flood", "water", "wave"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "moderate",
                    "outcome_text": "Water remembers it used to be everywhere.",
                    "effects": [
                        {"type": "create_tiles", "target": "player", "radius": 2, "tile": "water", "duration": 8},
                        {"type": "push", "target": nearest_enemy or "nearest_enemy", "origin": "player", "distance": 2},
                    ],
                    "costs": [{"type": "mana", "amount": 3}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["storm", "lightning", "thunder"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "major",
                    "outcome_text": "Lightning takes the long way through every wet thing.",
                    "effects": [
                        {
                            "type": "area_damage",
                            "target": nearest_enemy or "nearest_enemy",
                            "radius": 2,
                            "amount": 5,
                            "damage_type": "lightning",
                            "include_player": False,
                        },
                        {"type": "add_status", "target": nearest_enemy or "nearest_enemy", "status": "stunned", "duration": 1},
                    ],
                    "costs": [
                        {"type": "mana", "amount": 6},
                        {"type": "status", "status": "marked", "duration": 6},
                    ],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["poison", "miasma", "toxic"]) and not any(
            word in text for word in ["ward", "shield", "protect"]
        ):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "moderate",
                    "outcome_text": "A green weather curls low across the stones.",
                    "effects": [
                        {"type": "create_tiles", "target": nearest_enemy or "nearest_enemy", "radius": 1, "tile": "poison_cloud", "duration": 4},
                        {"type": "add_status", "target": nearest_enemy or "nearest_enemy", "status": "poisoned", "duration": 3},
                    ],
                    "costs": [{"type": "health", "amount": 2}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["root", "vine", "snare", "entangle"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "The floor grows hands.",
                    "effects": [
                        {"type": "create_tiles", "target": nearest_enemy or "nearest_enemy", "radius": 1, "tile": "vines", "duration": 5},
                        {"type": "add_status", "target": nearest_enemy or "nearest_enemy", "status": "rooted", "duration": 3},
                    ],
                    "costs": [{"type": "mana", "amount": 3}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["charm", "befriend", "ally", "friend"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "major",
                    "outcome_text": "A hostile thought changes its coat.",
                    "effects": [
                        {"type": "change_faction", "target": nearest_enemy or "nearest_enemy", "faction": "ally"},
                        {"type": "add_tag", "target": nearest_enemy or "nearest_enemy", "tag": "oath_bound"},
                    ],
                    "costs": [
                        {
                            "type": "curse",
                            "id": "borrowed_trust",
                            "name": "Borrowed Trust",
                            "description": "Promises made by magic tend to come due.",
                        }
                    ],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["ward", "shield", "protect"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "moderate",
                    "outcome_text": "A thin law wraps itself around your skin.",
                    "effects": [
                        {"type": "add_status", "target": "player", "status": "warded", "duration": 6},
                        {"type": "add_resistance", "target": "player", "damage_type": "fire", "amount": 25},
                        {"type": "add_resistance", "target": "player", "damage_type": "poison", "amount": 25},
                    ],
                    "costs": [{"type": "item", "item": "grave salt", "amount": 1}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["transmute", "glass", "gold", "change my body"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "major",
                    "outcome_text": "Alchemy forgets which side of your skin is outside.",
                    "effects": [
                        {
                            "type": "transform_entity",
                            "target": "player",
                            "name": "You",
                            "char": "@",
                            "hp": player.get("hp", 1),
                            "max_hp": player.get("max_hp", 1),
                            "attack": player.get("attack", 0),
                            "defense": player.get("defense", 0) + 1,
                            "material": "glass",
                            "tags": ["glass", "brittle"],
                        },
                        {"type": "modify_inventory", "item": "glass shard", "mode": "add", "amount": 1},
                    ],
                    "costs": [{"type": "max_health", "amount": 1}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["omen", "later", "debt", "curse the future"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "major",
                    "outcome_text": "The spell leaves and promises to come back.",
                    "effects": [
                        {"type": "set_flag", "flag": "future_debt", "value": True},
                        {
                            "type": "schedule_event",
                            "turns": 3,
                            "event_type": "summon",
                            "name": "debt collector",
                            "char": "d",
                            "hp": 6,
                            "attack": 3,
                        },
                    ],
                    "costs": [{"type": "mana", "amount": 2}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["fire", "burn", "flame", "ignite"]):
            damage_type = "fire"
            outcome_text = "A red syllable lands with teeth."
        else:
            damage_type = "arcane"
            outcome_text = "The spell improvises a mean little miracle."

        return json.dumps(
            {
                "accepted": True,
                "severity": "minor",
                "outcome_text": outcome_text,
                "effects": [
                    {"type": "damage", "target": nearest_enemy or "nearest_enemy", "amount": 6, "damage_type": damage_type},
                    {"type": "add_status", "target": nearest_enemy or "nearest_enemy", "status": "burning", "duration": 2}
                    if damage_type == "fire"
                    else {"type": "message", "text": "A coin somewhere lands on its edge."},
                ],
                "costs": [{"type": "mana", "amount": 3}],
                "rejected_reason": None,
            }
        )


class AutoWildMagicProvider:
    name = "auto"

    def __init__(self) -> None:
        self.ollama = OllamaWildMagicProvider()
        self.mock = MockWildMagicProvider()
        self.last_provider_name = "ollama"

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.resolve(spell, context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            self.last_provider_name = self.mock.name
            return self.mock.resolve(spell, context)


def make_provider(provider_name: str | None = None) -> WildMagicProvider:
    provider = (provider_name or os.environ.get("WILDMAGIC_PROVIDER", "ollama")).lower().strip()
    if provider == "mock":
        return MockWildMagicProvider()
    if provider == "ollama":
        return OllamaWildMagicProvider()
    return AutoWildMagicProvider()


def resolve_spell(provider: WildMagicProvider, spell: str, context: dict[str, Any]) -> MagicResolution:
    provider_name = getattr(provider, "name", "unknown")
    raw: str | None = None
    parsed_data: dict[str, Any] | None = None
    error: str | None = None
    technical_failure = False
    resolved_provider_name = provider_name
    active_context = context
    max_attempts = ollama_resolution_attempts()
    for attempt in range(max_attempts):
        try:
            raw = provider.resolve(spell, active_context)
        except Exception as exc:
            error = str(exc)
            technical_failure = True
            resolved_provider_name = _provider_name(provider)
            audit_path = write_audit_log(provider, spell, active_context, raw, None, technical_failure, error, resolved_provider_name)
            return MagicResolution(None, True, error, resolved_provider_name, raw, audit_path)

        try:
            parsed_data = parse_resolution_json(raw)
            error = validate_resolution(parsed_data)
            resolved_provider_name = _provider_name(provider)
            if error:
                technical_failure = True
                if should_retry_resolution(resolved_provider_name, attempt, max_attempts):
                    write_audit_log(
                        provider,
                        spell,
                        active_context,
                        raw,
                        parsed_data,
                        True,
                        f"{error}; retrying once",
                        resolved_provider_name,
                    )
                    active_context = retry_context(context, raw, error)
                    continue
                audit_path = write_audit_log(provider, spell, active_context, raw, parsed_data, technical_failure, error, resolved_provider_name)
                return MagicResolution(None, True, error, resolved_provider_name, raw, audit_path)
            audit_path = write_audit_log(provider, spell, active_context, raw, parsed_data, False, None, resolved_provider_name)
            return MagicResolution(parsed_data, False, None, resolved_provider_name, raw, audit_path)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            error = str(exc)
            technical_failure = True
            resolved_provider_name = _provider_name(provider)
            if should_retry_resolution(resolved_provider_name, attempt, max_attempts):
                write_audit_log(
                    provider,
                    spell,
                    active_context,
                    raw,
                    parsed_data,
                    True,
                    f"{error}; retrying once",
                    resolved_provider_name,
                )
                active_context = retry_context(context, raw, error)
                continue
            audit_path = write_audit_log(provider, spell, active_context, raw, parsed_data, technical_failure, error, resolved_provider_name)
            return MagicResolution(None, True, error, resolved_provider_name, raw, audit_path)

    audit_path = write_audit_log(provider, spell, active_context, raw, parsed_data, True, error, resolved_provider_name)
    return MagicResolution(None, True, error, resolved_provider_name, raw, audit_path)


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
    return parsed


def strip_thinking(raw: str) -> str:
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()


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
        if effect_type in {"create_tiles", "area_damage"} and "radius" in effect:
            try:
                radius = int(effect["radius"])
            except (TypeError, ValueError):
                return f"{effect_type} radius must be an integer"
            if radius < 0 or radius > 4:
                return f"{effect_type} radius must be between 0 and 4"
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
    for index, cost in enumerate(costs):
        if not isinstance(cost, dict):
            return f"cost {index} must be an object"
        cost_type = str(cost.get("type") or "").lower()
        if cost_type not in SUPPORTED_COSTS:
            return f"unsupported cost type: {cost_type or '(missing)'}"
    return None


def _nearest_enemy_id(context: dict[str, Any]) -> str | None:
    player_position = context["player"]["position"]
    px = player_position["x"]
    py = player_position["y"]
    enemies = [
        entity
        for entity in context.get("nearby_entities", [])
        if entity.get("kind") == "actor" and entity.get("faction") == "enemy" and entity.get("hp", 0) > 0
    ]
    if not enemies:
        return None
    enemies.sort(key=lambda entity: abs(entity["position"]["x"] - px) + abs(entity["position"]["y"] - py))
    return str(enemies[0]["id"])


def _provider_name(provider: WildMagicProvider) -> str:
    if isinstance(provider, AutoWildMagicProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")


def should_retry_resolution(resolved_provider_name: str, attempt: int, max_attempts: int) -> bool:
    return resolved_provider_name == "ollama" and attempt + 1 < max_attempts


def retry_context(context: dict[str, Any], raw_response: str | None, error: str) -> dict[str, Any]:
    updated = dict(context)
    updated["retry_after_invalid_resolution"] = {
        "error": error,
        "instruction": "The previous response could not be parsed or validated. Return only one complete JSON object.",
        "previous_response_prefix": (raw_response or "")[:600],
    }
    return updated


def write_audit_log(
    provider: WildMagicProvider,
    spell: str,
    context: dict[str, Any],
    raw_response: str | None,
    parsed_resolution: dict[str, Any] | None,
    technical_failure: bool,
    error: str | None,
    resolved_provider_name: str,
) -> str | None:
    if os.environ.get("WILDMAGIC_AUDIT_LOG", "1").lower().strip() in {"0", "false", "no", "off"}:
        return None
    audit_dir = Path(os.environ.get("WILDMAGIC_AUDIT_DIR", "logs"))
    audit_path = audit_dir / "wild_magic_audit.jsonl"
    prompt_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
    ]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spell": spell,
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "prompt": {
            "messages": prompt_messages,
            "context": context,
        },
        "raw_response": raw_response,
        "parsed_resolution": parsed_resolution,
        "technical_failure": technical_failure,
        "error": error,
    }
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError:
        return None
    return str(audit_path)


def normalize_ollama_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def ollama_timeout_seconds() -> float:
    value = os.environ.get("WILDMAGIC_OLLAMA_TIMEOUT", "180")
    try:
        timeout = float(value)
    except ValueError:
        return 180.0
    return max(5.0, timeout)


def ollama_num_predict() -> int:
    value = os.environ.get("WILDMAGIC_OLLAMA_NUM_PREDICT", "512")
    try:
        parsed = int(value)
    except ValueError:
        return 512
    return max(128, min(2048, parsed))


def ollama_temperature() -> float:
    value = os.environ.get("WILDMAGIC_OLLAMA_TEMPERATURE", "0.25")
    try:
        parsed = float(value)
    except ValueError:
        return 0.25
    return max(0.0, min(1.5, parsed))


def ollama_thinking_enabled() -> bool:
    value = os.environ.get("WILDMAGIC_OLLAMA_THINK", "0").lower().strip()
    return value in {"1", "true", "yes", "on"}


def ollama_json_format_enabled() -> bool:
    value = os.environ.get("WILDMAGIC_OLLAMA_FORMAT", "json").lower().strip()
    return value in {"1", "true", "yes", "on", "json"}


def ollama_resolution_attempts() -> int:
    value = os.environ.get("WILDMAGIC_OLLAMA_RESOLUTION_ATTEMPTS", "2")
    try:
        parsed = int(value)
    except ValueError:
        return 2
    return max(1, min(4, parsed))


def parse_ollama_error_body(body: str) -> str:
    stripped = body.strip()
    if not stripped:
        return ""
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped[:500]
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, str):
            return error
    return stripped[:500]
