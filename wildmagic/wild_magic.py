from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import urllib.error
from typing import Any, Protocol

from .config import (
    audit_dir,
    fallbacks_enabled,
    get_wild_magic_model,
    get_wild_magic_provider,
    ollama_host,
    ollama_json_format_enabled,
    ollama_json_schema_enabled,
    ollama_keep_alive,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_num_predict,
    ollama_resolution_attempts,
    ollama_temperature,
    ollama_thinking_enabled,
    ollama_timeout_seconds,
)
from .fallbacks import (
    bias_resolution_for_profile,
    fallback_resolution_from_spell,
)
from .llm_client import (
    _post_ollama_chat_with_json_retry,
    normalize_ollama_url,
)
from .capabilities import (
    assemble_resolver_system_prompt,
    select_cards,
    selected_context_slices,
    selected_effect_types,
)
from .spell_contract import per_cast_response_schema
from .llm_resolver import _write_jsonl_audit, should_retry_resolution, retry_context
from .resolution_parsing import parse_resolution_json, _nearest_enemy_id
from .prompts import (
    caster_prompt_block,
    focus_prompt_block,
    region_prompt_block,
)


def _wild_prompt_messages(spell: str, context: dict[str, Any]) -> list[dict[str, str]]:
    """Assemble the wild-magic chat messages. The engine rides the region's voice and
    the caster's stat-derived anchors along in context; both belong in the system
    prompt, not the user-message JSON, so they are split out here.

    The system prompt is assembled from the always-on core plus only the capability cards
    this spell routes to (wildmagic/capabilities.py) — hence the explicit spell argument."""
    region_style = context.get("region_style")
    caster_profile = context.get("caster_profile")
    spell_foci = context.get("spell_foci")
    payload_context = {
        k: v
        for k, v in context.items()
        if k not in {"region_style", "caster_profile", "spell_foci"}
    }
    system_content = assemble_resolver_system_prompt(
        spell,
        region_block=region_prompt_block(region_style),
        caster_block=caster_prompt_block(caster_profile),
        focus_block=focus_prompt_block(spell_foci),
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(payload_context, ensure_ascii=True)},
    ]


from .spell_contract import validate_resolution


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

    def resolve(self, spell: str, context: dict[str, Any]) -> str: ...


class OllamaWildMagicProvider:
    name = "ollama"
    purpose = "wild"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model_override = model
        self.model = model or get_wild_magic_model()
        self.base_url = (
            normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else ollama_timeout_seconds(self.purpose)
        )

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        payload = {
            "model": self._model_override or get_wild_magic_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": _wild_prompt_messages(spell, context),
            "options": {
                "temperature": ollama_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_num_predict(),
                "num_ctx": ollama_num_ctx(self.purpose),
                "num_gpu": ollama_num_gpu(self.purpose),
            },
            "keep_alive": ollama_keep_alive(self.purpose),
        }
        if ollama_json_schema_enabled(self.purpose):
            # Constrained decoding: the per-cast schema narrows the effect enum to the routed
            # core+card effects advertised in context["supported_effects"].
            payload["format"] = per_cast_response_schema(
                context.get("supported_effects")
            )
        elif ollama_json_format_enabled(self.purpose):
            payload["format"] = "json"
        data = _post_ollama_chat_with_json_retry(
            self.base_url, payload, self.timeout_seconds
        )
        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama response did not include message.content")
        return content


class MockWildMagicProvider:
    name = "mock"

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        text = spell.lower().strip()
        player = context["player"]
        px = player["position"]["x"]
        py = player["position"]["y"]
        nearest_enemy = _nearest_enemy_id(context)

        if any(
            word in text
            for word in [
                "win game",
                "infinite",
                "immortal",
                "kill everything",
                "kill all",
            ]
        ):
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
                        {
                            "type": "damage",
                            "target": nearest_enemy or "nearest_enemy",
                            "amount": 3,
                            "damage_type": "physical",
                        },
                        {
                            "type": "add_status",
                            "target": nearest_enemy or "nearest_enemy",
                            "status": "bleeding",
                            "duration": 4,
                        },
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
                    "effects": [
                        {"type": "teleport", "target": "player", "x": px + 3, "y": py}
                    ],
                    "costs": [
                        {"type": "mana", "amount": 3},
                        {"type": "health", "amount": 1},
                    ],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["wall", "ice", "block"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "Frost writes a hard line on the floor.",
                    "effects": [
                        {
                            "type": "create_tile",
                            "x": px + 1,
                            "y": py,
                            "tile": "ice_wall",
                        }
                    ],
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
                        {
                            "type": "create_tiles",
                            "target": "player",
                            "radius": 2,
                            "tile": "water",
                            "duration": 8,
                        },
                        {
                            "type": "push",
                            "target": nearest_enemy or "nearest_enemy",
                            "origin": "player",
                            "distance": 2,
                        },
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
                        {
                            "type": "add_status",
                            "target": nearest_enemy or "nearest_enemy",
                            "status": "stunned",
                            "duration": 1,
                        },
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
                        {
                            "type": "create_tiles",
                            "target": nearest_enemy or "nearest_enemy",
                            "radius": 1,
                            "tile": "poison_cloud",
                            "duration": 4,
                        },
                        {
                            "type": "add_status",
                            "target": nearest_enemy or "nearest_enemy",
                            "status": "poisoned",
                            "duration": 3,
                        },
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
                        {
                            "type": "create_tiles",
                            "target": nearest_enemy or "nearest_enemy",
                            "radius": 1,
                            "tile": "vines",
                            "duration": 5,
                        },
                        {
                            "type": "add_status",
                            "target": nearest_enemy or "nearest_enemy",
                            "status": "rooted",
                            "duration": 3,
                        },
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
                        {
                            "type": "change_faction",
                            "target": nearest_enemy or "nearest_enemy",
                            "faction": "ally",
                        },
                        {
                            "type": "add_tag",
                            "target": nearest_enemy or "nearest_enemy",
                            "tag": "oath_bound",
                        },
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
                        {
                            "type": "add_status",
                            "target": "player",
                            "status": "warded",
                            "duration": 6,
                        },
                        {
                            "type": "add_resistance",
                            "target": "player",
                            "damage_type": "fire",
                            "amount": 25,
                        },
                        {
                            "type": "add_resistance",
                            "target": "player",
                            "damage_type": "poison",
                            "amount": 25,
                        },
                    ],
                    "costs": [{"type": "item", "item": "grave salt", "amount": 1}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["transmute", "glass", "change chalk"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "The chalk turns to glass.",
                    "effects": [
                        {
                            "type": "transform_item",
                            "target": "inventory",
                            "item": "chalk",
                            "new_item_type": "glass chalk",
                            "material": "glass",
                            "tags": ["fragile"],
                        }
                    ],
                    "costs": [{"type": "mana", "amount": 2}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["transmute", "gold", "change my body"]):
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
                        {
                            "type": "modify_inventory",
                            "item": "glass shard",
                            "mode": "add",
                            "amount": 1,
                        },
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

        if any(word in text for word in ["force", "push", "shove"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "An invisible hand shoves the target.",
                    "effects": [
                        {
                            "type": "damage",
                            "target": nearest_enemy or "nearest_enemy",
                            "amount": 2,
                            "damage_type": "force",
                        }
                    ],
                    "costs": [{"type": "mana", "amount": 2}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["transform item on ground"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "The ground shimmers.",
                    "effects": [
                        {
                            "type": "transform_item",
                            "target": "nearest_item",
                            "item": "potion",
                            "new_item_type": "poison flask",
                            "material": "glass",
                            "tags": ["toxic"],
                        }
                    ],
                    "costs": [{"type": "mana", "amount": 2}],
                    "rejected_reason": None,
                }
            )

        if any(word in text for word in ["acid", "melt", "dissolve"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "Acid splashes the target.",
                    "effects": [
                        {
                            "type": "damage",
                            "target": nearest_enemy or "nearest_enemy",
                            "amount": 6,
                            "damage_type": "acid",
                        }
                    ],
                    "costs": [{"type": "mana", "amount": 3}],
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
                    {
                        "type": "damage",
                        "target": nearest_enemy or "nearest_enemy",
                        "amount": 6,
                        "damage_type": damage_type,
                    },
                    {
                        "type": "add_status",
                        "target": nearest_enemy or "nearest_enemy",
                        "status": "burning",
                        "duration": 2,
                    }
                    if damage_type == "fire"
                    else {
                        "type": "message",
                        "text": "A coin somewhere lands on its edge.",
                    },
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
    provider = (provider_name or get_wild_magic_provider()).lower().strip()
    if provider == "mock":
        return MockWildMagicProvider()
    if provider == "ollama":
        return OllamaWildMagicProvider()
    return AutoWildMagicProvider()


def resolve_spell(
    provider: WildMagicProvider, spell: str, context: dict[str, Any]
) -> MagicResolution:
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
            audit_path = write_audit_log(
                provider,
                spell,
                active_context,
                raw,
                None,
                technical_failure,
                error,
                resolved_provider_name,
            )
            return MagicResolution(
                None, True, error, resolved_provider_name, raw, audit_path
            )

        try:
            parsed_data = parse_resolution_json(raw)
            error = validate_resolution(parsed_data)
            resolved_provider_name = _provider_name(provider)
            if error:
                technical_failure = True
                if should_retry_resolution(
                    resolved_provider_name, attempt, max_attempts
                ):
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
                audit_path = write_audit_log(
                    provider,
                    spell,
                    active_context,
                    raw,
                    parsed_data,
                    technical_failure,
                    error,
                    resolved_provider_name,
                )
                return MagicResolution(
                    None, True, error, resolved_provider_name, raw, audit_path
                )
            audit_path = write_audit_log(
                provider,
                spell,
                active_context,
                raw,
                parsed_data,
                False,
                None,
                resolved_provider_name,
            )
            return MagicResolution(
                parsed_data, False, None, resolved_provider_name, raw, audit_path
            )
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
            fallback = (
                fallback_resolution_from_spell(spell) if fallbacks_enabled() else None
            )
            if fallback is not None:
                fallback = bias_resolution_for_profile(
                    fallback, context.get("caster_profile")
                )
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
                return MagicResolution(
                    fallback, False, None, resolved_provider_name, raw, audit_path
                )
            audit_path = write_audit_log(
                provider,
                spell,
                active_context,
                raw,
                parsed_data,
                technical_failure,
                error,
                resolved_provider_name,
            )
            return MagicResolution(
                None, True, error, resolved_provider_name, raw, audit_path
            )

    audit_path = write_audit_log(
        provider,
        spell,
        active_context,
        raw,
        parsed_data,
        True,
        error,
        resolved_provider_name,
    )
    return MagicResolution(None, True, error, resolved_provider_name, raw, audit_path)


def _provider_name(provider: WildMagicProvider) -> str:
    if isinstance(provider, AutoWildMagicProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")


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
    audit_path = audit_dir() / "wild_magic_audit.jsonl"
    prompt_messages = _wild_prompt_messages(spell, context)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spell": spell,
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        # What capability routing decided for this cast: which specialist cards loaded, which
        # effect types the per-cast schema allows, and which context slices were injected.
        "routing": _resolver_routing(spell),
        "prompt": {
            "messages": prompt_messages,
            "context": context,
        },
        "raw_response": raw_response,
        "parsed_resolution": parsed_resolution,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, record)


def _resolver_routing(spell: str) -> dict[str, Any]:
    """Routing metadata for the audit: the specialist cards a spell loads, the effect types
    its per-cast schema allows, and the card-driven context slices injected for it."""
    selected = select_cards(spell)
    return {
        "selected_cards": [card.name for card in selected],
        "selected_effect_types": sorted(selected_effect_types(selected)),
        "context_slices": list(selected_context_slices(selected)),
    }
