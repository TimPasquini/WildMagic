"""NPC dialogue. A deliberately separate, much simpler provider stack from
wild magic resolution: replies are plain spoken text with no JSON schema
to validate/normalize/retry, and the model can be swapped independently
(WILDMAGIC_DIALOGUE_MODEL / WILDMAGIC_DIALOGUE_PROVIDER) so spell
resolution and dialogue never have to share one model.

Split out of wild_magic.py (which had grown to four unrelated LLM
subsystems in one file); see docs/ARCHITECTURE.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import urllib.error
from typing import Any, Protocol

from .config import audit_dir, get_dialogue_model, get_dialogue_provider
from .fallbacks import fallbacks_enabled
from .llm_client import (
    _post_ollama_chat,
    strip_thinking,
    normalize_ollama_url,
    ollama_host,
    ollama_timeout_seconds,
    ollama_dialogue_temperature,
    ollama_dialogue_num_predict,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_keep_alive,
    ollama_thinking_enabled,
)
from .llm_resolver import _write_jsonl_audit
from .prompts import DIALOGUE_SYSTEM_PROMPT


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

    def reply(self, message: str, context: dict[str, Any]) -> str: ...


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
        self.base_url = (
            normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else ollama_timeout_seconds(self.purpose)
        )

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
        if any(
            word in text
            for word in ("hello", "hi", "greetings", "hail", "morning", "evening")
        ):
            return f"Well met, traveler. Not many stop to talk to a {role} like me."
        if "?" in text:
            return (
                "Hard to say, honestly. I keep my head down and mind my own business."
            )
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
            audit_path = write_dialogue_audit_log(
                provider,
                npc_name,
                message,
                active_context,
                raw,
                None,
                True,
                error,
                resolved_provider_name,
            )
            return DialogueResolution(
                None, True, error, resolved_provider_name, raw, audit_path
            )

        reply = strip_thinking(raw).strip().strip('"').strip()
        if not reply:
            problem = "empty reply"
        elif _is_degenerate_echo(message, reply):
            problem = "echoed the player's message"
        elif _is_self_repetition(reply, active_context):
            problem = "repeated its own last line verbatim"
        else:
            audit_path = write_dialogue_audit_log(
                provider,
                npc_name,
                message,
                active_context,
                raw,
                reply,
                False,
                None,
                resolved_provider_name,
            )
            return DialogueResolution(
                reply, False, None, resolved_provider_name, raw, audit_path
            )

        can_retry = attempt == 0 and resolved_provider_name == "ollama"
        if can_retry:
            write_dialogue_audit_log(
                provider,
                npc_name,
                message,
                active_context,
                raw,
                reply or None,
                True,
                f"{problem}; retrying once",
                resolved_provider_name,
            )
            active_context = _dialogue_retry_context(
                context,
                "Your last reply was unusable - it was empty, just repeated the player's words "
                "back instead of answering, or repeated something you yourself already said "
                "regardless of what the player just said. Speak again in your own voice, fully "
                "in character, and react freshly to what the player just said this time.",
            )
            continue

        audit_path = write_dialogue_audit_log(
            provider,
            npc_name,
            message,
            active_context,
            raw,
            reply or None,
            True,
            problem,
            resolved_provider_name,
        )
        return DialogueResolution(
            None, True, problem, resolved_provider_name, raw, audit_path
        )

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
