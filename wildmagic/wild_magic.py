from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import urllib.error
from typing import Any, Protocol

from .config import (
    audit_dir,
    get_dialogue_model,
    get_dialogue_provider,
    get_town_model,
    get_town_provider,
    get_trade_model,
    get_trade_provider,
    get_wild_magic_model,
    get_wild_magic_provider,
)
from .fallbacks import fallback_resolution_from_spell, fallbacks_enabled
from .llm_client import (
    _post_ollama_chat,
    strip_thinking,
    extract_thinking,
    parse_ollama_error_body,
    normalize_ollama_url,
    ollama_host,
    fetch_ollama_models,
    ollama_timeout_seconds,
    ollama_num_predict,
    ollama_num_ctx,
    ollama_temperature,
    ollama_dialogue_temperature,
    ollama_dialogue_num_predict,
    ollama_trade_temperature,
    ollama_trade_num_predict,
    ollama_thinking_enabled,
    ollama_json_format_enabled,
    ollama_town_num_predict,
    ollama_num_gpu,
    ollama_keep_alive,
    ollama_resolution_attempts,
)
from .llm_resolver import _write_jsonl_audit, should_retry_resolution, retry_context
from .models import MECHANICAL_STATUSES, TILE_ALIASES
from .prompts import SYSTEM_PROMPT, DIALOGUE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT, TOWN_SYSTEM_PROMPT, region_prompt_block


def _wild_prompt_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    """Assemble the wild-magic chat messages. The engine rides the region's
    voice along in context["region_style"]; it belongs in the system prompt,
    not the user-message JSON, so it is split out here."""
    region_style = context.get("region_style")
    payload_context = {k: v for k, v in context.items() if k != "region_style"}
    return [
        {"role": "system", "content": SYSTEM_PROMPT + region_prompt_block(region_style)},
        {"role": "user", "content": json.dumps(payload_context, ensure_ascii=True)},
    ]
from .spell_contract import (
    SPELL_RESPONSE_JSON_SCHEMA,
    STATUS_FLAVOR_ALIASES as _STATUS_FLAVOR_ALIASES,
    SUPPORTED_COSTS,
    SUPPORTED_EFFECTS,
    validate_resolution,
)


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
    purpose = "wild"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model_override = model
        self.model = model or get_wild_magic_model()
        self.base_url = normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else ollama_timeout_seconds(self.purpose)

    def resolve(self, spell: str, context: dict[str, Any]) -> str:
        payload = {
            "model": self._model_override or get_wild_magic_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": _wild_prompt_messages(context),
            "options": {
                "temperature": ollama_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_num_predict(),
                "num_ctx": ollama_num_ctx(self.purpose),
                "num_gpu": ollama_num_gpu(self.purpose),
            },
            "keep_alive": ollama_keep_alive(self.purpose),
        }
        if ollama_json_format_enabled(self.purpose):
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
        return _post_ollama_chat(self.base_url, payload, self.timeout_seconds)


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

        if any(word in text for word in ["transmute", "glass", "change chalk"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "The chalk turns to glass.",
                    "effects": [{"type": "transform_item", "target": "inventory", "item": "chalk", "new_item_type": "glass chalk", "material": "glass", "tags": ["fragile"]}],
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

        if any(word in text for word in ["force", "push", "shove"]):
            return json.dumps(
                {
                    "accepted": True,
                    "severity": "minor",
                    "outcome_text": "An invisible hand shoves the target.",
                    "effects": [
                        {"type": "damage", "target": nearest_enemy or "nearest_enemy", "amount": 2, "damage_type": "force"}
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
                    "effects": [{"type": "transform_item", "target": "nearest_item", "item": "potion", "new_item_type": "poison flask", "material": "glass", "tags": ["toxic"]}],
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
                        {"type": "damage", "target": nearest_enemy or "nearest_enemy", "amount": 6, "damage_type": "acid"}
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
    provider = (provider_name or get_wild_magic_provider()).lower().strip()
    if provider == "mock":
        return MockWildMagicProvider()
    if provider == "ollama":
        return OllamaWildMagicProvider()
    return AutoWildMagicProvider()


# ----------------------------------------------------------------------
# NPC dialogue. A deliberately separate, much simpler provider stack from
# wild magic resolution: replies are plain spoken text with no JSON schema
# to validate/normalize/retry, and the model can be swapped independently
# (WILDMAGIC_DIALOGUE_MODEL / WILDMAGIC_DIALOGUE_PROVIDER) so spell
# resolution and dialogue never have to share one model.
# ----------------------------------------------------------------------



@dataclass
class DialogueResolution:
    reply: str | None
    technical_failure: bool
    error: str | None = None
    provider_name: str = "unknown"
    raw_response: str | None = None
    audit_path: str | None = None


class DialogueProvider(Protocol):
    name: str

    def reply(self, message: str, context: dict[str, Any]) -> str:
        ...


class OllamaDialogueProvider:
    name = "ollama"
    purpose = "dialogue"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model_override = model
        self.model = model or get_dialogue_model()
        self.base_url = normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else ollama_timeout_seconds(self.purpose)

    def reply(self, message: str, context: dict[str, Any]) -> str:
        payload = {
            "model": self._model_override or get_dialogue_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": [
                {"role": "system", "content": DIALOGUE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
            ],
            "options": {
                "temperature": ollama_dialogue_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_dialogue_num_predict(),
                "num_ctx": ollama_num_ctx(self.purpose),
                "num_gpu": ollama_num_gpu(self.purpose),
            },
            "keep_alive": ollama_keep_alive(self.purpose),
        }
        data = _post_ollama_chat(self.base_url, payload, self.timeout_seconds)
        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama response did not include message.content")
        return strip_thinking(content).strip()


class MockDialogueProvider:
    name = "mock"

    def reply(self, message: str, context: dict[str, Any]) -> str:
        npc = context.get("npc") or {}
        role = str(npc.get("role") or "stranger").strip().lower()
        text = message.lower().strip()
        if not text:
            return "Lost for words, are you?"
        if any(word in text for word in ("hello", "hi", "greetings", "hail", "morning", "evening")):
            return f"Well met, traveler. Not many stop to talk to a {role} like me."
        if "?" in text:
            return "Hard to say, honestly. I keep my head down and mind my own business."
        if any(word in text for word in ("thank", "thanks")):
            return "No need for that. Just doing what I do."
        return "Mm. If you say so."


class AutoDialogueProvider:
    name = "auto"

    def __init__(self) -> None:
        self.ollama = OllamaDialogueProvider()
        self.mock = MockDialogueProvider()
        self.last_provider_name = "ollama"

    def reply(self, message: str, context: dict[str, Any]) -> str:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.reply(message, context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            if not fallbacks_enabled():
                raise
            self.last_provider_name = self.mock.name
            return self.mock.reply(message, context)


def make_dialogue_provider(provider_name: str | None = None) -> DialogueProvider:
    provider = (provider_name or get_dialogue_provider()).lower().strip()
    if provider == "mock":
        return MockDialogueProvider()
    if provider == "ollama":
        return OllamaDialogueProvider()
    return AutoDialogueProvider()


def _dialogue_provider_name(provider: DialogueProvider) -> str:
    if isinstance(provider, AutoDialogueProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")


def _is_degenerate_echo(message: str, reply: str) -> bool:
    """True when the model just parroted the player's words back as its own reply.
    Observed live with qwen3:8b during playtesting (e.g. asked Quill the peddler
    about a cutpurse, got "I heard there's a cutpurse... seen them yourself?" right
    back) - a broken non-answer, not a creative one, so it's worth catching and
    retrying rather than displaying as the NPC's voice."""
    normalized_message = message.strip().strip('"').strip().lower()
    normalized_reply = reply.strip().strip('"').strip().lower()
    return bool(normalized_message) and normalized_message == normalized_reply


def _is_self_repetition(reply: str, context: dict[str, Any]) -> bool:
    """True when the model repeated its own most recent line back verbatim, no
    matter what the player just said. Observed live with qwen3:8b: Captain Ressa
    Vane gave the exact same "...but I've seen worse. What's your take?" deflection
    both to an open question about the Empire AND to "I will burn the Empire to the
    ground" - two wildly different prompts (confirmed via the audit log's stored
    `prompt`, which genuinely differed turn to turn). The model anchored on its own
    prior line in recent_conversation instead of reacting to the new message - a
    stuck-in-a-loop failure, distinct from _is_degenerate_echo (which catches
    parroting the *player's* words back, not the NPC's own)."""
    npc = context.get("npc")
    conversation = npc.get("recent_conversation") if isinstance(npc, dict) else None
    if not isinstance(conversation, list):
        return False
    last_npc_line: str | None = None
    for entry in reversed(conversation):
        if isinstance(entry, dict) and entry.get("speaker") == "npc":
            text = entry.get("text")
            if isinstance(text, str):
                last_npc_line = text
            break
    if not last_npc_line:
        return False
    normalized_last = last_npc_line.strip().strip('"').strip().lower()
    normalized_reply = reply.strip().strip('"').strip().lower()
    return bool(normalized_last) and normalized_last == normalized_reply


def _dialogue_retry_context(context: dict[str, Any], note: str) -> dict[str, Any]:
    updated = dict(context)
    updated["retry_note"] = note
    return updated


def resolve_dialogue(
    provider: DialogueProvider, npc_name: str, message: str, context: dict[str, Any]
) -> DialogueResolution:
    """Ask the dialogue provider what an NPC says back. Deliberately simpler than
    resolve_spell: replies are plain flavor text with no schema to validate or
    normalize - the engine just displays whatever the NPC says. The one thing worth
    catching is a degenerate non-reply (empty, an echo of the player's own words back
    at them, or a verbatim repeat of the NPC's own last line regardless of what the
    player just said) - those break the conversational illusion outright, so we give
    the model one retry with a nudge before giving up, mirroring resolve_spell's
    single-retry-on-ollama convention."""
    resolved_provider_name = _dialogue_provider_name(provider)
    active_context = context
    raw: str | None = None
    for attempt in range(2):
        try:
            raw = provider.reply(message, active_context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            error = str(exc)
            audit_path = write_dialogue_audit_log(provider, npc_name, message, active_context, raw, None, True, error, resolved_provider_name)
            return DialogueResolution(None, True, error, resolved_provider_name, raw, audit_path)

        reply = strip_thinking(raw).strip().strip('"').strip()
        if not reply:
            problem = "empty reply"
        elif _is_degenerate_echo(message, reply):
            problem = "echoed the player's message"
        elif _is_self_repetition(reply, active_context):
            problem = "repeated its own last line verbatim"
        else:
            audit_path = write_dialogue_audit_log(provider, npc_name, message, active_context, raw, reply, False, None, resolved_provider_name)
            return DialogueResolution(reply, False, None, resolved_provider_name, raw, audit_path)

        can_retry = attempt == 0 and resolved_provider_name == "ollama"
        if can_retry:
            write_dialogue_audit_log(provider, npc_name, message, active_context, raw, reply or None, True, f"{problem}; retrying once", resolved_provider_name)
            active_context = _dialogue_retry_context(
                context,
                "Your last reply was unusable - it was empty, just repeated the player's words "
                "back instead of answering, or repeated something you yourself already said "
                "regardless of what the player just said. Speak again in your own voice, fully "
                "in character, and react freshly to what the player just said this time.",
            )
            continue

        audit_path = write_dialogue_audit_log(provider, npc_name, message, active_context, raw, reply or None, True, problem, resolved_provider_name)
        return DialogueResolution(None, True, problem, resolved_provider_name, raw, audit_path)

    raise AssertionError("unreachable")


def write_dialogue_audit_log(
    provider: DialogueProvider,
    npc_name: str,
    message: str,
    context: dict[str, Any],
    raw_response: str | None,
    reply: str | None,
    technical_failure: bool,
    error: str | None,
    resolved_provider_name: str,
) -> str | None:
    audit_path = audit_dir() / "dialogue_audit.jsonl"
    prompt_messages = [
        {"role": "system", "content": DIALOGUE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
    ]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "npc": npc_name,
        "message": message,
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "prompt": {
            "messages": prompt_messages,
            "context": context,
        },
        "raw_response": raw_response,
        "reply": reply,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, record)


# ----------------------------------------------------------------------
# Trade resolution: a small structured-JSON surface, deliberately separate
# from resolve_dialogue. Dialogue stays plain prose with no schema to
# contaminate; a cheap in-process keyword scan (see
# GameEngine.scan_for_trade_intent) decides WHEN to even ask, and this
# surface - mirroring resolve_spell's parse/validate/retry apparatus -
# decides WHETHER the exchange amounts to a real trade and exactly what
# it looks like, so the conversational voice and the schema never have to
# share one model call.
# ----------------------------------------------------------------------



@dataclass
class TradeResolution:
    data: dict[str, Any] | None
    technical_failure: bool
    error: str | None = None
    provider_name: str = "unknown"
    raw_response: str | None = None
    audit_path: str | None = None


class TradeProvider(Protocol):
    name: str

    def propose(self, context: dict[str, Any]) -> str:
        ...


class OllamaTradeProvider:
    name = "ollama"
    purpose = "trade"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model_override = model
        self.model = model or get_trade_model()
        self.base_url = normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else ollama_timeout_seconds(self.purpose)

    def propose(self, context: dict[str, Any]) -> str:
        payload = {
            "model": self._model_override or get_trade_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": [
                {"role": "system", "content": TRADE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
            ],
            "options": {
                "temperature": ollama_trade_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_trade_num_predict(),
                "num_ctx": ollama_num_ctx(self.purpose),
                "num_gpu": ollama_num_gpu(self.purpose),
            },
            "keep_alive": ollama_keep_alive(self.purpose),
        }
        if ollama_json_format_enabled(self.purpose):
            payload["format"] = "json"
        try:
            data = _post_ollama_chat(self.base_url, payload, self.timeout_seconds)
        except ValueError as exc:
            if "Unexpected empty grammar stack" not in str(exc) or "format" not in payload:
                raise
            retry_payload = dict(payload)
            retry_payload.pop("format", None)
            data = _post_ollama_chat(self.base_url, retry_payload, self.timeout_seconds)
        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama response did not include message.content")
        return content


class MockTradeProvider:
    name = "mock"

    def propose(self, context: dict[str, Any]) -> str:
        return json.dumps(
            {
                "trade_proposed": False,
                "initiator": "player",
                "npc_gives": [],
                "npc_wants": [],
                "proposal_text": "",
                "rejected_reason": "mock provider never proposes trades",
            }
        )


class AutoTradeProvider:
    name = "auto"

    def __init__(self) -> None:
        self.ollama = OllamaTradeProvider()
        self.mock = MockTradeProvider()
        self.last_provider_name = "ollama"

    def propose(self, context: dict[str, Any]) -> str:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.propose(context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            if not fallbacks_enabled():
                raise
            self.last_provider_name = self.mock.name
            return self.mock.propose(context)


def make_trade_provider(provider_name: str | None = None) -> TradeProvider:
    provider = (provider_name or get_trade_provider()).lower().strip()
    if provider == "mock":
        return MockTradeProvider()
    if provider == "ollama":
        return OllamaTradeProvider()
    return AutoTradeProvider()


def _trade_provider_name(provider: TradeProvider) -> str:
    if isinstance(provider, AutoTradeProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")


def parse_trade_json(raw: str) -> dict[str, Any]:
    """Defensive JSON parsing mirroring parse_resolution_json's strip-thinking +
    json.loads + regex-extraction fallback - minus _normalize_resolution, whose
    effect/cost normalization is wild-magic-specific and doesn't apply to trades."""
    cleaned = strip_thinking(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise TypeError("trade response was not a JSON object")
    return parsed


def _validate_trade_item_list(value: Any, label: str) -> str | None:
    if not isinstance(value, list):
        return f"{label} must be a list"
    if len(value) > 8:
        return f"{label} must contain at most 8 entries"
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            return f"{label}[{index}] must be an object"
        name = entry.get("item")
        if not isinstance(name, str) or not name.strip():
            return f"{label}[{index}] needs a non-empty item name"
        try:
            quantity = int(entry.get("quantity"))
        except (TypeError, ValueError):
            return f"{label}[{index}] quantity must be an integer"
        if quantity < 1 or quantity > 99:
            return f"{label}[{index}] quantity must be between 1 and 99"
    return None


def validate_trade_resolution(data: dict[str, Any]) -> str | None:
    """Mirrors validate_resolution's accepted-branch asymmetry: a non-trade only
    needs a reason, a real proposal needs the full structured payload."""
    if "trade_proposed" not in data or not isinstance(data["trade_proposed"], bool):
        return "trade_proposed must be a boolean"
    if data["trade_proposed"] is False:
        if not str(data.get("rejected_reason") or "").strip():
            return "a non-trade needs a rejected_reason"
        return None
    if str(data.get("initiator") or "").strip().lower() not in {"player", "npc"}:
        return "initiator must be 'player' or 'npc'"
    error = _validate_trade_item_list(data.get("npc_gives", []), "npc_gives")
    if error:
        return error
    error = _validate_trade_item_list(data.get("npc_wants", []), "npc_wants")
    if error:
        return error
    if not data.get("npc_gives") and not data.get("npc_wants"):
        return "a proposed trade needs at least one item or gold amount on one side"
    if not str(data.get("proposal_text") or "").strip():
        return "a proposed trade needs proposal_text to show the player"
    return None


def _trade_retry_context(context: dict[str, Any], raw_response: str | None, error: str) -> dict[str, Any]:
    updated = dict(context)
    updated["retry_after_invalid_resolution"] = {
        "error": error,
        "instruction": "The previous response could not be parsed or validated. Reply again with "
        "only one complete, valid JSON object in the exact shape described - no markdown fences, "
        "no commentary, no <think> text.",
        "previous_response_prefix": (raw_response or "")[:600],
    }
    return updated


def resolve_trade_proposal(provider: TradeProvider, npc_name: str, context: dict[str, Any]) -> TradeResolution:
    """Ask the trade provider whether the exchange just displayed amounts to a real
    trade and, if so, structure exactly what's proposed. Mirrors resolve_spell's
    parse -> validate -> retry-once-on-ollama -> clean technical_failure shape, minus
    its local-fallback machinery: "no trade" is always a safe, meaningful outcome on
    its own, so there is no equivalent of a fallback resolution to fall back to."""
    resolved_provider_name = _trade_provider_name(provider)
    active_context = context
    raw: str | None = None
    for attempt in range(2):
        try:
            raw = provider.propose(active_context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            error = str(exc)
            resolved_provider_name = _trade_provider_name(provider)
            audit_path = write_trade_audit_log(provider, npc_name, active_context, raw, None, True, error, resolved_provider_name)
            return TradeResolution(None, True, error, resolved_provider_name, raw, audit_path)

        resolved_provider_name = _trade_provider_name(provider)
        try:
            parsed = parse_trade_json(raw)
            error = validate_trade_resolution(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parsed = None
            error = str(exc)

        if error is None:
            audit_path = write_trade_audit_log(provider, npc_name, active_context, raw, parsed, False, None, resolved_provider_name)
            return TradeResolution(parsed, False, None, resolved_provider_name, raw, audit_path)

        can_retry = attempt == 0 and resolved_provider_name == "ollama"
        if can_retry:
            write_trade_audit_log(provider, npc_name, active_context, raw, parsed, True, f"{error}; retrying once", resolved_provider_name)
            active_context = _trade_retry_context(context, raw, error)
            continue

        audit_path = write_trade_audit_log(provider, npc_name, active_context, raw, parsed, True, error, resolved_provider_name)
        return TradeResolution(None, True, error, resolved_provider_name, raw, audit_path)

    raise AssertionError("unreachable")


def write_trade_audit_log(
    provider: TradeProvider,
    npc_name: str,
    context: dict[str, Any],
    raw_response: str | None,
    parsed: dict[str, Any] | None,
    technical_failure: bool,
    error: str | None,
    resolved_provider_name: str,
) -> str | None:
    audit_path = audit_dir() / "trade_audit.jsonl"
    prompt_messages = [
        {"role": "system", "content": TRADE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
    ]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "npc": npc_name,
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "prompt": {
            "messages": prompt_messages,
            "context": context,
        },
        "raw_response": raw_response,
        "parsed_resolution": parsed,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, record)


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


def _trigger_is_once(obj: dict[str, Any]) -> bool:
    """Models express single-use triggers many ways: "once": true, condition/
    conditions dicts whose type is "once" / "once_per_combat" / etc."""
    if obj.get("once") is True:
        return True
    for key in ("condition", "conditions"):
        cond = obj.get(key)
        if isinstance(cond, dict) and str(cond.get("type") or "").lower().startswith("once"):
            return True
        if isinstance(cond, str) and cond.lower().startswith("once"):
            return True
    return False


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
                    # LLM sometimes nests the whole trigger config under "trigger"
                    # (or "on") as a dict.
                    if isinstance(e.get("on"), dict) and not isinstance(e.get("trigger"), dict):
                        e["trigger"] = e.pop("on")
                    if isinstance(e.get("trigger"), dict):
                        trigger_obj = e.pop("trigger")
                        trigger_str = str(
                            trigger_obj.get("type") or trigger_obj.get("trigger")
                            or trigger_obj.get("on") or trigger_obj.get("event") or "on_next_spell"
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

    # Rescue cost entries whose type is actually a known effect type. The LLM sometimes
    # expresses a spell's mechanical consequence (e.g. "the wraith becomes weak to
    # radiant damage") as a cost entry like {"type": "add_weakness", ...} instead of
    # putting it in the effects list, which would otherwise fail validation outright.
    costs = data.get("costs")
    if isinstance(costs, list):
        rescued_effects: list[dict[str, Any]] = []
        remaining_costs: list[Any] = []
        for c in costs:
            c_type = str(c.get("type") or "").lower().strip() if isinstance(c, dict) else ""
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
    prompt_messages = _wild_prompt_messages(context)
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
    return _write_jsonl_audit(audit_path, record)


# ---------------------------------------------------------------------------
# Town generation provider — one JSON call produces a full settlement spec
# ---------------------------------------------------------------------------

@dataclass
class BuildingSpec:
    type: str
    name: str | None


@dataclass
class NpcSpec:
    name: str
    role: str
    backstory: str
    traits: list[str]
    building: str | None
    wares: dict[str, int] | None


@dataclass
class TownSpec:
    town_name: str
    description: str
    buildings: list[BuildingSpec]
    npcs: list[NpcSpec]




def _parse_town_spec(raw: str) -> TownSpec:
    """Parse a JSON string from the LLM into a TownSpec, tolerating missing fields."""
    data = json.loads(strip_thinking(raw).strip())
    buildings = []
    for b in data.get("buildings") or []:
        if isinstance(b, dict) and b.get("type"):
            buildings.append(BuildingSpec(
                type=str(b["type"]).lower().strip(),
                name=str(b["name"]).strip() if b.get("name") else None,
            ))
    npcs = []
    for n in data.get("npcs") or []:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        raw_wares = n.get("wares")
        wares: dict[str, int] | None = None
        if isinstance(raw_wares, dict):
            wares = {str(k): int(v) for k, v in raw_wares.items() if isinstance(v, (int, float)) and int(v) > 0}
        npcs.append(NpcSpec(
            name=str(n["name"]).strip(),
            role=str(n.get("role") or "resident").strip(),
            backstory=str(n.get("backstory") or "").strip(),
            traits=[str(t) for t in (n.get("traits") or []) if t],
            building=str(n["building"]).lower().strip() if n.get("building") else None,
            wares=wares or None,
        ))
    return TownSpec(
        town_name=str(data.get("town_name") or "Unnamed Settlement").strip(),
        description=str(data.get("description") or "").strip(),
        buildings=buildings,
        npcs=npcs,
    )


class TownProvider(Protocol):
    name: str

    def generate(self, zx: int, zy: int, context: dict[str, Any]) -> TownSpec:
        ...


class OllamaTownProvider:
    name = "ollama"
    purpose = "town"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model_override = model
        self.model = model or get_town_model()
        self.base_url = normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else ollama_timeout_seconds(self.purpose)

    def generate(self, zx: int, zy: int, context: dict[str, Any]) -> TownSpec:
        payload = {
            "model": self._model_override or get_town_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": [
                {"role": "system", "content": TOWN_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
            ],
            "options": {
                "temperature": ollama_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_town_num_predict(),
                "num_ctx": ollama_num_ctx(self.purpose),
                "num_gpu": ollama_num_gpu(self.purpose),
            },
            "keep_alive": ollama_keep_alive(self.purpose),
        }
        if ollama_json_format_enabled(self.purpose):
            payload["format"] = "json"
        try:
            data = _post_ollama_chat(self.base_url, payload, self.timeout_seconds)
        except ValueError as exc:
            if "Unexpected empty grammar stack" not in str(exc) or "format" not in payload:
                raise
            retry_payload = dict(payload)
            retry_payload.pop("format", None)
            data = _post_ollama_chat(self.base_url, retry_payload, self.timeout_seconds)
        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama response did not include message.content")
        return _parse_town_spec(content)


class MockTownProvider:
    name = "mock"

    def generate(self, zx: int, zy: int, context: dict[str, Any]) -> TownSpec:
        names = ["Ashford Crossing", "Saltmarket", "Cinder Vale", "The Waypost", "Brackenmere"]
        idx = abs(hash((zx, zy))) % len(names)
        promise_hooks = context.get("promise_hooks") if isinstance(context, dict) else None
        hook = promise_hooks[0] if isinstance(promise_hooks, list) and promise_hooks and isinstance(promise_hooks[0], dict) else None
        description = "A rough cluster of buildings where the road bends. Travelers stop here to rest; most keep moving."
        britta_backstory = "Has lived here longer than the buildings. Knows every plant in a day's walk."
        if hook:
            hook_text = str(hook.get("text") or "").strip()
            hook_subject = str(hook.get("subject") or "an old rumor").strip()
            if hook_text:
                description = f"A rough cluster of buildings where the road bends. Locals keep repeating this: {hook_text}"
            britta_backstory = f"Has lived here longer than the buildings. Keeps watch over local talk about {hook_subject}."
        return TownSpec(
            town_name=names[idx],
            description=description,
            buildings=[
                BuildingSpec(type="tavern", name="The Hollow Cup"),
                BuildingSpec(type="market", name=None),
                BuildingSpec(type="home", name=None),
            ],
            npcs=[
                NpcSpec(
                    name="Dara Mull",
                    role="innkeeper",
                    backstory="Runs the tavern alone since her husband left for the capital. Claims he'll be back any day.",
                    traits=["tired", "hospitable"],
                    building="tavern",
                    wares={"smoke vial": 2, "gold": 15},
                ),
                NpcSpec(
                    name="Oswin Fetch",
                    role="traveling merchant",
                    backstory="Hauls goods between the frontier settlements. Knows every road and most of the people on them.",
                    traits=["chatty", "shrewd"],
                    building="market",
                    wares={"lockpick": 1, "trinket": 2, "gold": 20},
                ),
                NpcSpec(
                    name="Old Britta",
                    role="herbalist",
                    backstory=britta_backstory,
                    traits=["quiet", "observant"],
                    building="home",
                    wares={"blood moss": 2, "grave salt": 1, "gold": 10},
                ),
            ],
        )


class AutoTownProvider:
    name = "auto"

    def __init__(self) -> None:
        self.ollama = OllamaTownProvider()
        self.mock = MockTownProvider()
        self.last_provider_name = "ollama"

    def generate(self, zx: int, zy: int, context: dict[str, Any]) -> TownSpec:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.generate(zx, zy, context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            if not fallbacks_enabled():
                raise
            self.last_provider_name = self.mock.name
            return self.mock.generate(zx, zy, context)


def make_town_provider(provider_name: str | None = None) -> TownProvider:
    provider = (provider_name or get_town_provider()).lower().strip()
    if provider == "mock":
        return MockTownProvider()
    if provider == "ollama":
        return OllamaTownProvider()
    return AutoTownProvider()
