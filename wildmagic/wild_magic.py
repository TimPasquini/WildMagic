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

from .fallbacks import fallback_resolution_from_spell, fallbacks_enabled
from .models import MECHANICAL_STATUSES, TILE_ALIASES


SUPPORTED_STATUS_TEXT = ", ".join(sorted(MECHANICAL_STATUSES))

SYSTEM_PROMPT = """You are the Wild Magic referee for a turn-based tile roguelike.
Resolve the player's typed spell by returning exactly one JSON object and no prose.
Do not include chain-of-thought, markdown, comments, or <think> text.
IMPORTANT: All fields inside each effect or cost must be at the top level of that object.
Never use sub-keys like "data", "details", or "params" inside an effect or cost.
Never wrap the result in an "outcome" or "result" key — the JSON object IS the result.
Use "effects" (array) and "costs" (array) — never "effect" (singular) or "cost" (singular dict).

Required top-level shape:
{"accepted": true, "severity": "minor|moderate|major|catastrophic", "outcome_text": "short log message", "effects": [], "costs": [], "rejected_reason": null}

Use only the effects and costs needed for this one spell. Do not copy every available option.
Typical minor/moderate spell: 1-3 effects and 1-2 costs.
Typical major spell: 2-5 effects and 2-4 costs.
Catastrophic spell: dangerous effects, severe permanent costs, or rejection.

Effect catalog:
- damage: target, amount, damage_type.
- area_damage: target (center entity or "player"), radius 0-4, amount, damage_type, include_player boolean, affects "enemies|non_player|allies|all".
- area_status: target (center), radius 0-4, status, duration, affects "enemies|non_player|allies|all". Use for "slow all enemies in sight", "confuse everything nearby", etc.
- heal or restore_mana: target, amount.
- teleport: target, x, y.
- push or pull: target, origin or dx/dy, distance.
- create_tile or create_tiles: x/y or target, tile, radius, duration. Add hollow:true for a ring/perimeter pattern, or shape:"line|wall|cone|scatter" with origin:"player" and target:"nearest_enemy" for paths, barriers, cones, and bursts. Use ONE create_tiles effect for shapes — never list individual coordinates.
- add_status or remove_status: target, status, duration. Optional display_name (shown to player instead of the status key, e.g. "petrified" for frozen) and expiry_text (message when it wears off). For single target: an actor id, "player", or "nearest_enemy". For all enemies: "all_enemies". For everyone: "all".
- summon: name, faction ("ally" or "enemy"), hp, attack, defense, char, x, y. All at top level.
- spawn_item: name, item_type, x, y, char, material, quantity, tags.
- conjure_item: template, name, material, tags, target, placement, count.
- conjure_creature: template, name, faction ("ally" or "enemy"), tags, placement, count. Always include faction.
- modify_inventory, transform_entity, change_faction, add_tag, remove_tag, add_resistance (fields: target, damage_type, amount), add_weakness (fields: target, damage_type, amount), set_flag, schedule_event, create_trigger, message.

Valid target strings: "player", "nearest_enemy", or a specific entity id from context. For add_status, you may also use "all_enemies" or "enemies" to affect all enemies, or "all" for everyone.

Cost catalog:
- mana, health, max_health, max_mana, item (fields: item name, amount), status, curse.
- Costs are discovered after casting. Effects happen first, then costs.
- If a cost is odd or poetic, use a curse instead of inventing a new status.
- Item costs should match items visible in the player's inventory. Use the exact inventory key name.

Balance rules:
- Allow crazy, powerful, and dramatic spells — they should just have appropriate costs.
- If the spell is a literal win button or infinite resource exploit with no cost, reject or make it catastrophic.
- Big damage, big area, big effects are fine — they need commensurate costs (mana, health, curses, items).
- Use affects "enemies" for spells that should only harm foes.
- Keep effects local and concrete. Prefer entity ids from context.
- For permanent terrain, omit duration or use "permanent"; otherwise duration must be 1 or more.
- For body-part changes, use damage/status/conjure_item instead of transform_entity unless the whole creature changes.
- For tracking, glowing shadow, locate, or reveal spells, use add_status with status "revealed" on the target.
- For spells promising a delayed payoff or future consequence, use schedule_event to create the payoff. schedule_event fields: turns (number), event_type (summon|message|damage|heal|status|flood|curse|conjure), plus event-specific fields (name, hp, attack, faction, amount, tile, status, etc.).
- For "next time X happens, Y happens" spells, use create_trigger. Fields: trigger ("on_next_spell|on_player_hit|on_player_damaged|on_player_move|on_enemy_hit|on_enemy_damaged|on_enemy_death"), target ("player|nearest_enemy|all_enemies|any"), charges, duration, name, effects. Trigger effects may use target:"trigger_target" or target:"trigger_source".
- For physically impossible global requests (reverse gravity for everything, turn all walls into X), reject with a creative reason or give a local creative interpretation using available effects.

Useful tiles: floor, wall, door, open_door, stairs_down, stairs_up, water, fire, slick_ice, ice_wall, poison_cloud, vines, rubble, mist. Also accepted: lava/magma→fire, caltrops/thorns/web/net→vines, spikes/debris/bones→rubble, smoke/fog→mist, acid→poison_cloud, iron_bars/barrier→ice_wall.
Tile usage: use vines for tangling hazards (webs, thorns, nets, caltrops), rubble for destructive debris, mist for obscuring clouds, slick_ice for sliding hazards. Always use radius for room/area coverage — e.g. {"type":"create_tiles","tile":"mist","target":"player","radius":5} for filling a room with smoke.
Supported statuses: {supported_statuses}.
Use status only for supported mechanical statuses.
Key behaviors: burning/bleeding/poisoned deal 1 damage/turn; regenerating heals 1 HP/turn; slowed skips every other turn; berserk deals +2 damage but self-damages; empowered deals +2 damage; marked/cursed take extra damage; invisible reduces enemy sensing; confused moves randomly; frightened flees; frozen/stunned/rooted/silenced/webbed are disabling.

Conjuration:
- For arbitrary new objects or creatures, prefer template-backed conjuration.
- Item templates: generic_object, body_part, glass_shard, ritual_component, weapon_like, food, key_like, treasure.
- Creature templates: tiny_swarm, small_beast, humanoid, construct, spirit, slime, summoned_servant, hazard_creature.
- Creative names, materials, and tags are allowed, but mechanics come from the chosen template.

Behavior tags (add to any summoned/conjured creature's tags array for special per-turn behaviors):
- "pacifist" means the creature never attacks; useful for healing fonts, wards, shrines, and aura-only objects.
- "aura_burn_N" — sets nearby enemies on fire each turn (radius N, default 2)
- "aura_heal_N" — heals nearby allies 1 HP/turn
- "aura_fear_N" — frightens nearby enemies each turn
- "aura_slow_N" — slows nearby enemies each turn
- "aura_poison_N" — poisons nearby enemies each turn
- "aura_bleed_N" — causes bleeding in nearby enemies each turn
- "aura_reveal_N" — applies revealed status to all nearby entities
- "aura_mana_N" — restores 1 mana/turn to player when within radius N
- "aura_damage_N" — deals 1 arcane damage to nearby enemies each turn
- "aura_confuse_N" — confuses nearby enemies each turn
- "ranged" — attacks from up to 7 tiles away (line of sight required) instead of melee
- "guardian" — stays in place, only acts against enemies within 3 tiles; never chases
- "stationary" — never moves at all; only attacks adjacent enemies
- "explode_on_death" — explodes for fire damage in radius 3 when killed
- "shatter_on_death" — deals physical damage in radius 2 when killed
- "poison_cloud_on_death" — fills radius 3 with poison cloud when killed
- "freeze_on_death" — freezes and ices the area around itself when killed
- "spawn_on_death" — spawns two smaller creatures when killed

Good examples:
{"accepted": true, "severity": "minor", "outcome_text": "A blue shadow pins the target's location in your mind.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "revealed", "duration": 6}], "costs": [{"type": "mana", "amount": 2}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "A tiny sun circles you and lashes out at foes.", "effects": [{"type": "summon", "name": "tiny sun", "faction": "ally", "hp": 4, "attack": 0, "defense": 1, "char": "o"}, {"type": "area_damage", "target": "player", "radius": 3, "amount": 4, "damage_type": "fire", "include_player": false, "affects": "enemies"}], "costs": [{"type": "mana", "amount": 6}, {"type": "status", "status": "burning", "duration": 2}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "The goblin spits out a brittle little treasure.", "effects": [{"type": "damage", "target": "nearest_enemy", "amount": 3, "damage_type": "physical"}, {"type": "add_status", "target": "nearest_enemy", "status": "bleeding", "duration": 3}, {"type": "conjure_item", "template": "body_part", "name": "glass teeth", "material": "glass", "tags": ["fragile", "tooth"], "target": "nearest_enemy", "placement": "target_tile"}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}
{"accepted": true, "severity": "minor", "outcome_text": "Blue webbing pins the target in place.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "webbed", "duration": 3}, {"type": "conjure_item", "template": "generic_object", "name": "sticky blue webbing", "material": "silk", "target": "nearest_enemy", "placement": "target_tile"}], "costs": [{"type": "item", "item": "chalk", "amount": 1}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "Time thickens around your enemies.", "effects": [{"type": "area_status", "target": "player", "radius": 4, "status": "slowed", "duration": 4, "affects": "enemies"}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "Two wolves lope out of a dark corner.", "effects": [{"type": "conjure_creature", "template": "small_beast", "name": "shadow wolf", "count": 2, "faction": "ally", "tags": ["wolf", "predator"], "placement": "near_player"}], "costs": [{"type": "mana", "amount": 5}, {"type": "curse", "id": "wild_debt", "name": "Wild Debt", "description": "The wild expects repayment."}], "rejected_reason": null}
{"accepted": true, "severity": "major", "outcome_text": "Wounds close. In five turns, something hostile will arrive to collect.", "effects": [{"type": "heal", "target": "player", "amount": 8}, {"type": "schedule_event", "turns": 5, "event_type": "summon", "name": "wrath echo", "char": "W", "hp": 10, "attack": 4, "faction": "enemy"}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "Your bones remember fire.", "effects": [{"type": "add_resistance", "target": "player", "damage_type": "fire", "amount": 50}], "costs": [{"type": "mana", "amount": 6}, {"type": "curse", "id": "fire_debt", "name": "Fire Debt", "description": "Something hot is owed."}], "rejected_reason": null}
{"accepted": true, "severity": "minor", "outcome_text": "Your bones lock like limestone.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "frozen", "display_name": "petrified", "expiry_text": "The stone cracks. You can move.", "duration": 3}], "costs": [{"type": "mana", "amount": 2}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "A smouldering ward takes shape. Enemies who approach will burn.", "effects": [{"type": "conjure_creature", "template": "hazard_creature", "name": "burning ward", "faction": "ally", "tags": ["aura_burn_3", "stationary", "ward"], "placement": "near_player", "count": 1}], "costs": [{"type": "mana", "amount": 5}, {"type": "item", "item": "chalk", "amount": 1}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "A spectral archer materialises, nocking an arrow of shadow.", "effects": [{"type": "conjure_creature", "template": "spirit", "name": "shadow archer", "faction": "ally", "tags": ["ranged", "undead"], "placement": "near_player", "count": 1}], "costs": [{"type": "mana", "amount": 6}], "rejected_reason": null}
{"accepted": true, "severity": "major", "outcome_text": "Something volatile and eager answers the call. It will not last long.", "effects": [{"type": "conjure_creature", "template": "construct", "name": "bomb golem", "faction": "ally", "hp": 4, "tags": ["explode_on_death", "bomb"], "placement": "near_player", "count": 1}], "costs": [{"type": "mana", "amount": 8}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "A healing font pulses softly. Stand near it to recover.", "effects": [{"type": "summon", "name": "healing font", "faction": "ally", "hp": 6, "attack": 0, "defense": 2, "char": "+", "tags": ["aura_heal_3", "stationary"]}], "costs": [{"type": "mana", "amount": 7}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "A ring of fire erupts around you.", "effects": [{"type": "create_tiles", "tile": "fire", "target": "player", "radius": 3, "hollow": true, "duration": 5}], "costs": [{"type": "mana", "amount": 5}], "rejected_reason": null}
{"accepted": true, "severity": "minor", "outcome_text": "Ice draws a straight path to your enemy.", "effects": [{"type": "create_tiles", "shape": "line", "origin": "player", "target": "nearest_enemy", "tile": "slick_ice", "duration": 4}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "Your wound learns to answer.", "effects": [{"type": "create_trigger", "name": "thorn-blood answer", "trigger": "on_player_hit", "target": "player", "charges": 1, "duration": 6, "effects": [{"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "physical"}, {"type": "add_status", "target": "trigger_source", "status": "bleeding", "duration": 3}]}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}
{"accepted": false, "severity": "catastrophic", "outcome_text": "", "effects": [], "costs": [], "rejected_reason": "Reality refuses to become that convenient."}
""".replace("{supported_statuses}", SUPPORTED_STATUS_TEXT)


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
                "num_gpu": ollama_num_gpu(),
            },
            "keep_alive": "10m",
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
                            "name": "wild echo",
                            "char": "w",
                            "hp": 6,
                            "attack": 3,
                            "faction": "ally",
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
            if not fallbacks_enabled():
                raise
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
            fallback = fallback_resolution_from_spell(spell) if fallbacks_enabled() else None
            if fallback is not None and resolved_provider_name == "ollama":
                audit_path = write_audit_log(
                    provider,
                    spell,
                    active_context,
                    raw,
                    fallback,
                    False,
                    f"{error}; used local fallback",
                    resolved_provider_name,
                )
                return MagicResolution(fallback, False, None, resolved_provider_name, raw, audit_path)
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
    return _normalize_resolution(parsed)


_STATUS_FLAVOR_ALIASES: dict[str, str] = {
    # frozen synonyms
    "petrified": "frozen", "stone": "frozen", "crystallized": "frozen",
    "paralyzed": "frozen", "paralysed": "frozen", "iced": "frozen",
    "glaciated": "frozen", "encased": "frozen",
    # stunned synonyms
    "dazed": "stunned", "staggered": "stunned", "concussed": "stunned",
    "knocked_out": "stunned", "knocked_back": "stunned", "disoriented": "stunned",
    "dazzled": "stunned",
    # rooted synonyms
    "immobilized": "rooted", "pinned": "rooted", "anchored": "rooted",
    "grounded": "rooted", "earthbound": "rooted", "trapped": "rooted",
    # webbed synonyms
    "entangled": "webbed", "snared": "webbed", "ensnared": "webbed",
    "bound": "webbed", "cocooned": "webbed", "wrapped": "webbed",
    "tangled": "webbed",
    # burning synonyms
    "aflame": "burning", "alight": "burning", "on_fire": "burning",
    "ignited": "burning", "flaming": "burning", "ablaze": "burning",
    "smoldering": "burning",
    # poisoned synonyms
    "diseased": "poisoned", "infected": "poisoned", "plagued": "poisoned",
    "venomous": "poisoned", "toxic": "poisoned", "envenomed": "poisoned",
    "tainted": "poisoned", "corrupted": "poisoned",
    # bleeding synonyms
    "lacerated": "bleeding", "wounded": "bleeding", "cut": "bleeding",
    "hemorrhaging": "bleeding", "bloodied": "bleeding",
    # slowed synonyms
    "sluggish": "slowed", "lethargic": "slowed", "lagging": "slowed",
    "encumbered": "slowed", "weighed_down": "slowed", "dragging": "slowed",
    # hasted synonyms
    "hastened": "hasted", "swift": "hasted", "quickened": "hasted",
    "accelerated": "hasted", "blurred": "hasted",
    # invisible synonyms
    "cloaked": "invisible", "hidden": "invisible", "shrouded": "invisible",
    "shadowed": "invisible", "veiled": "invisible", "ethereal": "invisible",
    "ghostly": "invisible", "transparent": "invisible",
    # confused synonyms
    "deluded": "confused", "disoriented": "confused", "maddened": "confused",
    "crazed": "confused", "muddled": "confused", "lost": "confused",
    "bewildered": "confused", "blind": "confused", "blinded": "confused",
    "sightless": "confused", "unseeing": "confused",
    # frightened synonyms
    "panicked": "frightened", "terrified": "frightened", "afraid": "frightened",
    "scared": "frightened", "fleeing": "frightened", "cowering": "frightened",
    "horrified": "frightened",
    # marked synonyms
    "doomed": "marked", "condemned": "marked", "targeted": "marked",
    "branded": "marked", "cursed_mark": "marked", "hexed": "marked",
    # cursed synonyms
    "hexed_deep": "cursed", "afflicted": "cursed", "jinxed_deep": "cursed",
    "damned": "cursed",
    # berserk synonyms
    "enraged": "berserk", "frenzied": "berserk", "frantic": "berserk",
    "wrathful": "berserk", "bloodlusted": "berserk", "feral": "berserk",
    # empowered synonyms
    "strengthened": "empowered", "supercharged": "empowered", "buffed": "empowered",
    "fortified": "empowered", "charged": "empowered", "bolstered": "empowered",
    # warded synonyms
    "protected": "warded", "shielded": "warded", "guarded": "warded",
    "defended": "warded",
    # regenerating synonyms
    "healing": "regenerating", "mending": "regenerating", "recovering": "regenerating",
    "recuperating": "regenerating", "restored": "regenerating",
    # silenced synonyms
    "muted": "silenced", "gagged": "silenced", "voiceless": "silenced",
    # revealed synonyms
    "exposed": "revealed", "uncloaked": "revealed", "illuminated": "revealed",
    "highlighted": "revealed",
}


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
    tags = [str(t).lower() for t in (_raw_tags if isinstance(_raw_tags, list) else [_raw_tags])]
    name_fields = [
        str(e.get("name") or ""),
        str(e.get("terrain_type") or ""),
        str(e.get("substance") or ""),
        str(e.get("material") or ""),
    ]
    ctx = " ".join(tags + name_fields + [tile_val]).lower()
    if any(w in ctx for w in ("fire", "lava", "magma", "flame", "ignite", "burn", "scorch", "incinerate")):
        inferred = "fire"
    elif any(w in ctx for w in ("slick", "ice_floor", "frost_floor")):
        inferred = "slick_ice"
    elif any(w in ctx for w in ("poison", "acid", "toxic", "fume", "vapor", "gas", "venom")):
        inferred = "poison_cloud"
    elif any(w in ctx for w in ("smoke", "fog", "mist", "haze", "cloud", "steam")):
        inferred = "mist"
    elif any(w in ctx for w in ("vine", "web", "thorn", "net", "caltrop", "snare", "entangle", "trip", "hazard", "spike", "trap")):
        inferred = "vines"
    elif any(w in ctx for w in ("rubble", "debris", "stone", "rock", "ruin", "bone", "gravel")):
        inferred = "rubble"
    elif any(w in ctx for w in ("ice_wall", "wall_ice", "barrier", "block", "iron", "bars")):
        inferred = "ice_wall"
    elif any(w in ctx for w in ("water", "flood", "swamp", "mud", "pool", "liquid", "puddle")):
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
    if "damage_type" in e or (isinstance(e.get("amount"), (int, float)) and "tile" not in e and "status" not in e):
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


def _infer_trigger_action(text: str) -> dict[str, Any] | None:
    """Convert a natural-language trigger action string to a structured effect dict."""
    t = text.lower()
    if any(w in t for w in ("fire", "flame", "burn", "blaze", "ignite", "scorch", "incinerate")):
        return {"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "fire"}
    if any(w in t for w in ("ice", "frost", "freeze", "cold", "chill", "frozen")):
        return {"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "frost"}
    if any(w in t for w in ("lightning", "thunder", "electric", "shock", "spark", "volt")):
        return {"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "lightning"}
    if any(w in t for w in ("poison", "toxic", "venom", "acid")):
        return {"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "poison"}
    if any(w in t for w in ("heal", "restore", "mend", "recover", "regenerate")):
        return {"type": "heal", "target": "player", "amount": 5}
    if any(w in t for w in ("retaliate", "counter", "reflect", "strike", "attack", "damage", "hit", "hurt", "wound")):
        return {"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "physical"}
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
    if data.get("accepted") is False and not str(data.get("rejected_reason") or "").strip():
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
        "success", "ok", "okay", "done", "accepted", "yes", "no", "null", "none",
        "true", "false", "error", "failed", "failure", "reject", "rejected", "completed",
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
    top_effect_type = str(data.get("effect") or data.get("effect_type") or data.get("type") or "").lower().strip()
    if top_effect_type in {"create_trigger", "trigger", "ward", "reaction", "contingency", "delayed_reaction"} and isinstance(data.get("effects"), list):
        trigger_effect = {
            "type": _EFFECT_TYPE_ALIASES.get(top_effect_type, top_effect_type),
            "effects": data["effects"],
        }
        for key in {
            "target", "trigger", "on", "charges", "duration", "turns", "name",
            "display_name", "expiry_text",
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
                effect_obj = _effect_from_text(str(effect_raw)) or {"type": str(effect_raw)}
                # Merge safe top-level fields into the effect so that patterns like
                # {"effect":"area_status","target":"all enemies","status":"slowed",...}
                # produce a complete effect object.
                _EFFECT_TOP_FIELDS = {
                    "target", "status", "duration", "radius", "tile", "amount",
                    "damage_type", "x", "y", "name", "faction", "template", "hp",
                    "max_hp", "attack", "defense", "char", "count", "tags",
                    "hollow", "ring", "perimeter", "include_player", "affects",
                    "display_name", "expiry_text", "item", "material", "quantity",
                    "dx", "dy", "distance", "positions", "tiles", "creature",
                    "trigger", "on", "effects", "effect", "action", "charges", "shape",
                    "pattern", "width", "length", "from", "to",
                }
                for _k in _EFFECT_TOP_FIELDS:
                    if _k in data and _k not in effect_obj:
                        effect_obj[_k] = data[_k]
            details = data.get("details")
            if isinstance(details, dict):
                for k, v in details.items():
                    if k not in {"costs", "cost", "rules_applied", "supported_effects_used",
                                 "supported_costs_used", "description"}:
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
                            rescued.append({
                                "type": "item",
                                "item": str(val),
                                "amount": int(details.get("quantity", 1)),
                            })
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
                    raw_status = str(e.get("status") or "").strip().lower().replace(" ", "_")
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
                if et in {"conjure_item", "spawn_item"} and isinstance(e.get("item"), dict):
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
                    # LLM sometimes nests the whole trigger config under "trigger" as a dict.
                    if isinstance(e.get("trigger"), dict):
                        trigger_obj = e.pop("trigger")
                        trigger_str = str(
                            trigger_obj.get("type") or trigger_obj.get("trigger")
                            or trigger_obj.get("on") or "on_next_spell"
                        )
                        e["trigger"] = trigger_str
                        if not e.get("effects"):
                            nested = trigger_obj.get("effects") or trigger_obj.get("effect")
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
                        if not e.get("charges") and isinstance(trigger_obj.get("condition"), dict):
                            if trigger_obj["condition"].get("type") == "once":
                                e["charges"] = 1
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
                if et in {"create_tiles", "create_tile", "set_tile"} and "positions" in e and "tiles" not in e:
                    e = dict(e)
                    raw_tile = str(e.get("tile") or ".").lower()
                    e["tiles"] = [
                        {"x": p.get("x", 0), "y": p.get("y", 0), "tile": str(p.get("tile") or raw_tile)}
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
        data["effects"] = [_flatten_nested_effect(e) if isinstance(e, dict) else e for e in effects]

    costs = data.get("costs")
    if isinstance(costs, list):
        data = dict(data)
        data["costs"] = [_flatten_nested_effect(c) if isinstance(c, dict) else c for c in costs]

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
                    coerced.append({"type": "health" if key == "hp" else key, "amount": amount})
            except (TypeError, ValueError):
                pass
        elif key == "item":
                coerced.append({"type": "item", "item": str(val), "amount": int(raw_dict.get("quantity", 1))})
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
        word_turns = re.search(r"\b(?:in|after)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+turn", normalized)
        turns = word_numbers.get(word_turns.group(1), 3) if word_turns else 3
    if not any(word in normalized for word in ["arrive", "appears", "appear", "summon"]):
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
    faction = "ally" if not any(word in normalized for word in ["hostile", "enemy", "foe", "threat", "collector"]) else "enemy"
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
        for key in ["name", "char", "hp", "max_hp", "attack", "defense", "faction", "tags", "resistances", "weaknesses"]:
            if key in entity and key not in normalized:
                normalized[key] = entity[key]
    event_text = str(normalized.get("event") or normalized.get("event_type") or "").lower().strip().replace(" ", "_").replace("-", "_")
    if "event_type" not in normalized:
        if isinstance(entity, dict) or "arrival" in event_text or "summon" in event_text:
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
        word in normalized for word in ["enemy", "foe", "hostile", "monster", "creature", "goblin", "slime", "bat"]
    ):
        return "nearest_enemy"
    if normalized.startswith("closest_") and any(
        word in normalized for word in ["enemy", "foe", "hostile", "monster", "creature", "goblin", "slime", "bat"]
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
    value = os.environ.get("WILDMAGIC_OLLAMA_NUM_PREDICT", "800")
    try:
        parsed = int(value)
    except ValueError:
        return 800
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


def ollama_num_gpu() -> int:
    value = os.environ.get("WILDMAGIC_OLLAMA_NUM_GPU", "999")
    try:
        parsed = int(value)
    except ValueError:
        return 999
    return max(0, min(999, parsed))


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
