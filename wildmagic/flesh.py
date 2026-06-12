"""Optional narrative flesh for bound promises (Promise Ledger M5).

When a promise binds and reserves a future site, the background CPU model drafts small
decorations for it — a site name, a keeper, an arrival line. Flesh is never load-bearing:
it cannot create, move, or unbind a promise, and realization stands complete without it.
Flesh rides the same background channel as lore extraction (purpose "lore" config), so
no extra environment setup is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import urllib.error
from typing import Any, Protocol

from .config import audit_dir, get_lore_model, get_lore_provider, ollama_lore_num_predict
from .fallbacks import fallbacks_enabled
from .llm_client import (
    _post_ollama_chat,
    normalize_ollama_url,
    ollama_host,
    ollama_json_format_enabled,
    ollama_keep_alive,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_temperature,
    ollama_thinking_enabled,
    ollama_timeout_seconds,
    strip_thinking,
)
from .llm_resolver import _write_jsonl_audit
from .prompts import FLESH_SYSTEM_PROMPT
from .promises import normalize_flesh


@dataclass
class FleshResolution:
    flesh: dict[str, str] | None
    technical_failure: bool
    error: str | None = None
    provider_name: str = "unknown"
    raw_response: str | None = None
    audit_path: str | None = None


class FleshProvider(Protocol):
    name: str

    def draft(self, context: dict[str, Any]) -> str:
        ...


class OllamaFleshProvider:
    name = "ollama"
    purpose = "lore"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model_override = model
        self.model = model or get_lore_model()
        self.base_url = normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else ollama_timeout_seconds(self.purpose)

    def draft(self, context: dict[str, Any]) -> str:
        payload = {
            "model": self._model_override or get_lore_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": [
                {"role": "system", "content": FLESH_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
            ],
            "options": {
                "temperature": ollama_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_lore_num_predict(),
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


class MockFleshProvider:
    name = "mock"

    def draft(self, context: dict[str, Any]) -> str:
        subject = " ".join(str(context.get("subject") or "promised place").split()).strip() or "promised place"
        first_word = subject.split()[0].capitalize()
        return json.dumps(
            {
                "site_name": f"The {subject.title()}",
                "keeper_name": f"Warden {first_word}",
                "keeper_backstory": f"Has tended this place since long before anyone in town believed the stories about {subject}.",
                "prop_description": f"It is worn smooth by hands that came here believing in {subject}.",
                "arrival_line": f"The story was true after all - {subject} stands here, just as they said.",
            }
        )


class AutoFleshProvider:
    name = "auto"

    def __init__(self) -> None:
        self.ollama = OllamaFleshProvider()
        self.mock = MockFleshProvider()
        self.last_provider_name = "ollama"

    def draft(self, context: dict[str, Any]) -> str:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.draft(context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            if not fallbacks_enabled():
                raise
            self.last_provider_name = self.mock.name
            return self.mock.draft(context)


def make_flesh_provider(provider_name: str | None = None) -> FleshProvider:
    provider = (provider_name or get_lore_provider()).lower().strip()
    if provider == "mock":
        return MockFleshProvider()
    if provider == "ollama":
        return OllamaFleshProvider()
    return AutoFleshProvider()


def resolve_flesh(provider: FleshProvider, context: dict[str, Any]) -> FleshResolution:
    resolved_provider_name = _flesh_provider_name(provider)
    raw: str | None = None
    try:
        raw = provider.draft(context)
        flesh = normalize_flesh(parse_flesh_json(raw))
        audit_path = _write_flesh_audit(provider, context, raw, flesh, False, None, resolved_provider_name)
        return FleshResolution(flesh, False, None, resolved_provider_name, raw, audit_path)
    except (OSError, TimeoutError, urllib.error.URLError, TypeError, ValueError, json.JSONDecodeError) as exc:
        resolved_provider_name = _flesh_provider_name(provider)
        error = str(exc)
        audit_path = _write_flesh_audit(provider, context, raw, None, True, error, resolved_provider_name)
        return FleshResolution(None, True, error, resolved_provider_name, raw, audit_path)


def parse_flesh_json(raw: str) -> dict[str, Any]:
    cleaned = strip_thinking(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise TypeError("flesh response was not a JSON object")
    return parsed


def flesh_context_for_promise(promise: Any) -> dict[str, Any]:
    binding = getattr(promise, "binding", None)
    return {
        "promise_id": getattr(promise, "id", ""),
        "kind": getattr(promise, "kind", "rumor"),
        "subject": getattr(promise, "subject", ""),
        "text": getattr(promise, "text", ""),
        "tags": list(getattr(promise, "tags", []) or []),
        "blueprint": getattr(binding, "blueprint", None),
        "location_heard": getattr(promise, "location", ""),
    }


def _write_flesh_audit(
    provider: FleshProvider,
    context: dict[str, Any],
    raw_response: str | None,
    flesh: dict[str, str] | None,
    technical_failure: bool,
    error: str | None,
    resolved_provider_name: str,
) -> str | None:
    audit_path = audit_dir() / "flesh_audit.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "promise_id": context.get("promise_id"),
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "context": context,
        "raw_response": raw_response,
        "flesh": flesh,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, record)


def _flesh_provider_name(provider: FleshProvider) -> str:
    if isinstance(provider, AutoFleshProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")
