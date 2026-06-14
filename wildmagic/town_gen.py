"""Town generation provider — one JSON call produces a full settlement spec.

Split out of wild_magic.py; see docs/ARCHITECTURE.md."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import urllib.error
from typing import Any, Protocol

from .config import audit_dir, get_town_model, get_town_provider
from .fallbacks import fallbacks_enabled
from .llm_client import (
    _post_ollama_chat,
    strip_thinking,
    normalize_ollama_url,
    ollama_host,
    ollama_timeout_seconds,
    ollama_temperature,
    ollama_town_num_predict,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_keep_alive,
    ollama_thinking_enabled,
    ollama_json_format_enabled,
)
from .llm_resolver import _write_jsonl_audit
from .prompts import TOWN_SYSTEM_PROMPT


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
    appearance: str = ""


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
            buildings.append(
                BuildingSpec(
                    type=str(b["type"]).lower().strip(),
                    name=str(b["name"]).strip() if b.get("name") else None,
                )
            )
    npcs = []
    for n in data.get("npcs") or []:
        if not isinstance(n, dict) or not n.get("name"):
            continue
        raw_wares = n.get("wares")
        wares: dict[str, int] | None = None
        if isinstance(raw_wares, dict):
            wares = {
                str(k): int(v)
                for k, v in raw_wares.items()
                if isinstance(v, (int, float)) and int(v) > 0
            }
        npcs.append(
            NpcSpec(
                name=str(n["name"]).strip(),
                role=str(n.get("role") or "resident").strip(),
                backstory=str(n.get("backstory") or "").strip(),
                traits=[str(t) for t in (n.get("traits") or []) if t],
                building=str(n["building"]).lower().strip()
                if n.get("building")
                else None,
                wares=wares or None,
                appearance=str(n.get("appearance") or "").strip(),
            )
        )
    return TownSpec(
        town_name=str(data.get("town_name") or "Unnamed Settlement").strip(),
        description=str(data.get("description") or "").strip(),
        buildings=buildings,
        npcs=npcs,
    )


class TownProvider(Protocol):
    name: str

    def generate(self, zx: int, zy: int, context: dict[str, Any]) -> TownSpec: ...


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
        self.base_url = (
            normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else ollama_timeout_seconds(self.purpose)
        )

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
        raw_response: str | None = None
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
            _write_town_audit(
                self,
                zx,
                zy,
                context,
                raw_response,
                None,
                True,
                "Ollama response did not include message.content",
            )
            raise ValueError("Ollama response did not include message.content")
        raw_response = content
        try:
            spec = _parse_town_spec(content)
        except Exception as exc:
            _write_town_audit(self, zx, zy, context, raw_response, None, True, str(exc))
            raise
        _write_town_audit(self, zx, zy, context, raw_response, spec, False, None)
        return spec


def _write_town_audit(
    provider: TownProvider,
    zx: int,
    zy: int,
    context: dict[str, Any],
    raw_response: str | None,
    spec: TownSpec | None,
    technical_failure: bool,
    error: str | None,
) -> str | None:
    audit_path = audit_dir() / "town_audit.jsonl"
    prompt_messages = [
        {"role": "system", "content": TOWN_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(context, ensure_ascii=True)},
    ]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone": {"x": zx, "y": zy},
        "provider": getattr(provider, "name", "unknown"),
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "prompt": {
            "messages": prompt_messages,
            "context": context,
        },
        "raw_response": raw_response,
        "town": spec.__dict__ if spec else None,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, record)


class MockTownProvider:
    name = "mock"

    def generate(self, zx: int, zy: int, context: dict[str, Any]) -> TownSpec:
        names = [
            "Ashford Crossing",
            "Saltmarket",
            "Cinder Vale",
            "The Waypost",
            "Brackenmere",
        ]
        idx = abs(hash((zx, zy))) % len(names)
        promise_hooks = (
            context.get("promise_hooks") if isinstance(context, dict) else None
        )
        hook = (
            promise_hooks[0]
            if isinstance(promise_hooks, list)
            and promise_hooks
            and isinstance(promise_hooks[0], dict)
            else None
        )
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
                    appearance="A broad-shouldered woman with flour on her forearms and a wedding ring she still wears. Her smile arrives a half-second late, like it had to be fetched.",
                ),
                NpcSpec(
                    name="Oswin Fetch",
                    role="traveling merchant",
                    backstory="Hauls goods between the frontier settlements. Knows every road and most of the people on them.",
                    traits=["chatty", "shrewd"],
                    building="market",
                    wares={"lockpick": 1, "trinket": 2, "gold": 20},
                    appearance="A wiry man whose coat is more pockets than cloth, road dust worked permanently into the seams. His eyes do a quick inventory of you before his mouth says hello.",
                ),
                NpcSpec(
                    name="Old Britta",
                    role="herbalist",
                    backstory=britta_backstory,
                    traits=["quiet", "observant"],
                    building="home",
                    wares={"blood moss": 2, "grave salt": 1, "gold": 10},
                    appearance="A small, weathered woman with green-stained fingertips and a posture like a bent nail. She watches the road the way other people watch the weather.",
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
