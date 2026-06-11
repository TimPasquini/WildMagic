from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import urllib.error
from typing import Any, Protocol

from .config import (
    audit_dir,
    get_lore_model,
    get_lore_provider,
    ollama_lore_num_predict,
)
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
from .models import LoreClaim
from .normalize import normalize_id
from .prompts import LORE_EXTRACTION_SYSTEM_PROMPT


VALID_STATUSES = {"unverified", "rumored", "verified", "contested", "false", "corroborated", "redeemed"}
VALID_KINDS = {"rumor", "background", "quest_hook", "place", "person", "threat", "custom"}


@dataclass
class LoreExtractionResolution:
    claims: list[LoreClaim]
    technical_failure: bool
    error: str | None = None
    provider_name: str = "unknown"
    raw_response: str | None = None
    audit_path: str | None = None


class LoreExtractionProvider(Protocol):
    name: str

    def extract(self, context: dict[str, Any]) -> str:
        ...


class OllamaLoreProvider:
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

    def extract(self, context: dict[str, Any]) -> str:
        payload = {
            "model": self._model_override or get_lore_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": [
                {"role": "system", "content": LORE_EXTRACTION_SYSTEM_PROMPT},
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


class MockLoreProvider:
    name = "mock"

    def extract(self, context: dict[str, Any]) -> str:
        reply = str(context.get("reply") or "")
        npc = str(context.get("npc") or "Someone")
        lowered = reply.lower()
        if not reply.strip() or len(reply.split()) < 8:
            return json.dumps({"claims": []})

        if any(word in lowered for word in ("rumor", "heard", "seen", "midnight", "witch", "oak")):
            subject = _subject_from_text(reply) or "local rumor"
            return json.dumps(
                {
                    "claims": [
                        {
                            "kind": "rumor",
                            "subject": subject,
                            "text": _attributed_claim_text(npc, reply),
                            "status": "rumored",
                            "confidence": 0.55,
                            "salience": 4,
                            "tags": _tags_from_text(reply),
                        }
                    ]
                }
            )
        if any(word in lowered for word in ("used to", "once", "before", "old", "ancient")):
            subject = _subject_from_text(reply) or npc
            return json.dumps(
                {
                    "claims": [
                        {
                            "kind": "background",
                            "subject": subject,
                            "text": _attributed_claim_text(npc, reply),
                            "status": "unverified",
                            "confidence": 0.6,
                            "salience": 3,
                            "tags": _tags_from_text(reply),
                        }
                    ]
                }
            )
        return json.dumps({"claims": []})


class AutoLoreProvider:
    name = "auto"

    def __init__(self) -> None:
        self.ollama = OllamaLoreProvider()
        self.mock = MockLoreProvider()
        self.last_provider_name = "ollama"

    def extract(self, context: dict[str, Any]) -> str:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.extract(context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            if not fallbacks_enabled():
                raise
            self.last_provider_name = self.mock.name
            return self.mock.extract(context)


def make_lore_provider(provider_name: str | None = None) -> LoreExtractionProvider:
    provider = (provider_name or get_lore_provider()).lower().strip()
    if provider == "mock":
        return MockLoreProvider()
    if provider == "ollama":
        return OllamaLoreProvider()
    return AutoLoreProvider()


def resolve_lore_extraction(provider: LoreExtractionProvider, context: dict[str, Any]) -> LoreExtractionResolution:
    resolved_provider_name = _lore_provider_name(provider)
    raw: str | None = None
    try:
        raw = provider.extract(context)
        parsed = parse_lore_json(raw)
        claims = normalize_lore_claims(parsed, context)
        audit_path = write_lore_audit_log(provider, context, raw, [claim.to_dict() for claim in claims], False, None, resolved_provider_name)
        return LoreExtractionResolution(claims, False, None, resolved_provider_name, raw, audit_path)
    except (OSError, TimeoutError, urllib.error.URLError, TypeError, ValueError, json.JSONDecodeError) as exc:
        resolved_provider_name = _lore_provider_name(provider)
        error = str(exc)
        audit_path = write_lore_audit_log(provider, context, raw, None, True, error, resolved_provider_name)
        return LoreExtractionResolution([], True, error, resolved_provider_name, raw, audit_path)


def parse_lore_json(raw: str) -> dict[str, Any]:
    cleaned = strip_thinking(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise TypeError("lore response was not a JSON object")
    return parsed


def normalize_lore_claims(data: dict[str, Any], context: dict[str, Any]) -> list[LoreClaim]:
    raw_claims = data.get("claims") or []
    if isinstance(raw_claims, dict):
        raw_claims = [raw_claims]
    if not isinstance(raw_claims, list):
        raise TypeError("claims must be a list")

    claims: list[LoreClaim] = []
    seen_texts: set[str] = set()
    for index, raw in enumerate(raw_claims[:3]):
        if not isinstance(raw, dict):
            continue
        text = _clean_text(raw.get("text"), 360)
        if not text:
            continue
        subject = _clean_text(raw.get("subject"), 80) or "unknown"
        kind = normalize_id(str(raw.get("kind") or "rumor")) or "rumor"
        if kind not in VALID_KINDS:
            kind = "custom"
        status = normalize_id(str(raw.get("status") or "unverified")) or "unverified"
        if status not in VALID_STATUSES:
            status = "unverified"
        lowered = text.lower()
        if lowered in seen_texts:
            continue
        seen_texts.add(lowered)
        confidence = _bounded_float(raw.get("confidence"), 0.0, 1.0, 0.5)
        salience = _clamp_int(raw.get("salience"), 1, 5, 2)
        tags = _normalize_tags(raw.get("tags"))
        if not tags:
            tags = _tags_from_text(f"{subject} {text}")[:6]
        claims.append(
            LoreClaim(
                id=_claim_id(context, index, subject, text),
                kind=kind,
                subject=subject,
                text=text,
                source_npc=str(context.get("npc") or "unknown"),
                source_turn=int(context.get("turn") or 0),
                location=str(context.get("location") or "unknown"),
                status=status,
                confidence=confidence,
                salience=salience,
                tags=tags[:8],
                source_message=str(context.get("message") or ""),
                source_reply=str(context.get("reply") or ""),
                zone_x=int(context["zone"]["x"]) if isinstance(context.get("zone"), dict) and context["zone"].get("x") is not None else None,
                zone_y=int(context["zone"]["y"]) if isinstance(context.get("zone"), dict) and context["zone"].get("y") is not None else None,
            )
        )
    return claims


def lore_context_for_prompt(claims: list[LoreClaim], limit: int = 8, text_limit: int = 240) -> list[dict[str, Any]]:
    ranked = sorted(
        claims,
        key=lambda claim: (
            claim.status == "redeemed",
            -claim.salience,
            -claim.confidence,
            claim.source_turn,
            claim.subject.lower(),
        ),
    )
    return [
        {
            "id": claim.id,
            "kind": claim.kind,
            "subject": claim.subject,
            "text": _clean_text(claim.text, text_limit),
            "source_npc": claim.source_npc,
            "status": claim.status,
            "salience": claim.salience,
            "tags": list(claim.tags),
        }
        for claim in ranked[:limit]
    ]


def write_lore_audit_log(
    provider: LoreExtractionProvider,
    context: dict[str, Any],
    raw_response: str | None,
    claims: list[dict[str, Any]] | None,
    technical_failure: bool,
    error: str | None,
    resolved_provider_name: str,
) -> str | None:
    audit_path = audit_dir() / "lore_audit.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "npc": context.get("npc"),
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "prompt": {
            "messages": [
                {"role": "system", "content": LORE_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
            ],
            "context": context,
        },
        "raw_response": raw_response,
        "claims": claims,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, record)


def _lore_provider_name(provider: LoreExtractionProvider) -> str:
    if isinstance(provider, AutoLoreProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")


def _claim_id(context: dict[str, Any], index: int, subject: str, text: str) -> str:
    seed = "|".join(
        [
            str(context.get("turn") or 0),
            str(context.get("npc") or ""),
            str(index),
            subject,
            text,
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"lore_{digest}"


def _clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip("\"'")
    return text[:limit].strip()


def _bounded_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_tags = re.split(r"[,;/]", value)
    elif isinstance(value, list):
        raw_tags = value
    else:
        raw_tags = []
    tags: list[str] = []
    for raw in raw_tags:
        tag = normalize_id(str(raw))
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _attributed_claim_text(npc: str, reply: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", reply.strip())[0]
    return _clean_text(f"{npc} says {sentence}", 180)


def _tags_from_text(text: str) -> list[str]:
    tags: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.lower()):
        tag = normalize_id(word)
        if tag in {"the", "and", "that", "this", "with", "from", "says", "said", "you", "your", "there"}:
            continue
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= 8:
            break
    return tags


def _subject_from_text(text: str) -> str | None:
    match = re.search(r"\b([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,2})\b", text)
    if match:
        return match.group(1)
    for keyword in ("witch", "oak", "tree", "spellbook", "saint", "empire", "road", "ruin"):
        if keyword in text.lower():
            return keyword
    return None
