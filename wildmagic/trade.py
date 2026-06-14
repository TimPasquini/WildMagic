"""Trade resolution: a small structured-JSON surface, deliberately separate
from resolve_dialogue. Dialogue stays plain prose with no schema to
contaminate; a cheap in-process keyword scan (see
GameEngine.scan_for_trade_intent) decides WHEN to even ask, and this
surface - mirroring resolve_spell's parse/validate/retry apparatus -
decides WHETHER the exchange amounts to a real trade and exactly what
it looks like, so the conversational voice and the schema never have to
share one model call.

Split out of wild_magic.py; see docs/ARCHITECTURE.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import urllib.error
from typing import Any, Protocol

from .config import audit_dir, get_trade_model, get_trade_provider
from .fallbacks import fallbacks_enabled
from .llm_client import (
    _post_ollama_chat,
    strip_thinking,
    normalize_ollama_url,
    ollama_host,
    ollama_timeout_seconds,
    ollama_trade_temperature,
    ollama_trade_num_predict,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_keep_alive,
    ollama_thinking_enabled,
    ollama_json_format_enabled,
)
from .llm_resolver import _write_jsonl_audit
from .prompts import TRADE_SYSTEM_PROMPT


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

    def propose(self, context: dict[str, Any]) -> str: ...


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
        self.base_url = (
            normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else ollama_timeout_seconds(self.purpose)
        )

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
            if (
                "Unexpected empty grammar stack" not in str(exc)
                or "format" not in payload
            ):
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


def _trade_retry_context(
    context: dict[str, Any], raw_response: str | None, error: str
) -> dict[str, Any]:
    updated = dict(context)
    updated["retry_after_invalid_resolution"] = {
        "error": error,
        "instruction": "The previous response could not be parsed or validated. Reply again with "
        "only one complete, valid JSON object in the exact shape described - no markdown fences, "
        "no commentary, no <think> text.",
        "previous_response_prefix": (raw_response or "")[:600],
    }
    return updated


def resolve_trade_proposal(
    provider: TradeProvider, npc_name: str, context: dict[str, Any]
) -> TradeResolution:
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
            audit_path = write_trade_audit_log(
                provider,
                npc_name,
                active_context,
                raw,
                None,
                True,
                error,
                resolved_provider_name,
            )
            return TradeResolution(
                None, True, error, resolved_provider_name, raw, audit_path
            )

        resolved_provider_name = _trade_provider_name(provider)
        try:
            parsed = parse_trade_json(raw)
            error = validate_trade_resolution(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parsed = None
            error = str(exc)

        if error is None:
            audit_path = write_trade_audit_log(
                provider,
                npc_name,
                active_context,
                raw,
                parsed,
                False,
                None,
                resolved_provider_name,
            )
            return TradeResolution(
                parsed, False, None, resolved_provider_name, raw, audit_path
            )

        can_retry = attempt == 0 and resolved_provider_name == "ollama"
        if can_retry:
            write_trade_audit_log(
                provider,
                npc_name,
                active_context,
                raw,
                parsed,
                True,
                f"{error}; retrying once",
                resolved_provider_name,
            )
            active_context = _trade_retry_context(context, raw, error)
            continue

        audit_path = write_trade_audit_log(
            provider,
            npc_name,
            active_context,
            raw,
            parsed,
            True,
            error,
            resolved_provider_name,
        )
        return TradeResolution(
            None, True, error, resolved_provider_name, raw, audit_path
        )

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
