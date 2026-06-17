"""Materialized canon generation for room, object, and text details.

Canon records are per-run generated descriptions that have become true in the
current world. The engine supplies the facts; the provider supplies wording only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import urllib.error
from typing import Any, Protocol

from .config import (
    audit_dir,
    get_background_canon_model,
    get_canon_model,
    get_canon_provider,
    ollama_canon_num_predict,
    ollama_canon_temperature,
    ollama_resolution_attempts,
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
    ollama_thinking_enabled,
    ollama_timeout_seconds,
    strip_thinking,
)
from .llm_resolver import _write_jsonl_audit
from .models import CanonRecord
from .normalize import normalize_id
from .prompts import CANON_SYSTEM_PROMPT


_TEXT_LIMITS = {
    "title": 80,
    "text": 800,
    "summary": 180,
}

# Books are real reading matter — compressed pages, not a vignette.
_BOOK_TEXT_LIMIT = 4200


@dataclass
class CanonResolution:
    record: CanonRecord | None
    technical_failure: bool
    error: str | None = None
    provider_name: str = "unknown"
    raw_response: str | None = None
    audit_path: str | None = None


class CanonProvider(Protocol):
    name: str

    def materialize(self, context: dict[str, Any]) -> str: ...


class OllamaCanonProvider:
    """On-demand canon materialization (examine/read): the player is blocked
    waiting, so this rides the URGENT route — GPU-resident main model by
    default. Background prewarming should construct this with explicit
    model/base_url overrides pointing at the background channel instead."""

    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        purpose: str = "canon",
    ) -> None:
        self.purpose = purpose
        self._model_override = model
        self.model = model or (
            get_background_canon_model() if purpose == "lore" else get_canon_model()
        )
        self.base_url = (
            normalize_ollama_url(base_url) if base_url else ollama_host(self.purpose)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else ollama_timeout_seconds(self.purpose)
        )

    def materialize(self, context: dict[str, Any]) -> str:
        # engine_private carries bookkeeping (tile coordinates, ids) the model
        # must never see — it leaks into prose as "at position nine" otherwise.
        prompt_context = {
            key: value for key, value in context.items() if key != "engine_private"
        }
        payload = {
            "model": self._model_override or get_canon_model(),
            "stream": False,
            "think": ollama_thinking_enabled(self.purpose),
            "messages": [
                {"role": "system", "content": CANON_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(prompt_context, ensure_ascii=True),
                },
            ],
            "options": {
                "temperature": ollama_canon_temperature(),
                "top_p": 0.9,
                "num_predict": ollama_canon_num_predict(),
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


class MockCanonProvider:
    name = "mock"

    def materialize(self, context: dict[str, Any]) -> str:
        kind = normalize_id(str(context.get("kind") or ""))
        if kind == "book_title":
            return self._materialize_book_title(context)
        if kind == "book":
            return self._materialize_book(context)
        if kind == "investigation":
            return self._materialize_investigation(context)
        if kind in {"object_detail", "npc_detail", "creature_detail"}:
            return self._materialize_detail(context)
        room = (
            context.get("subject", {}).get("room", {})
            if isinstance(context.get("subject"), dict)
            else {}
        )
        room_type = str(room.get("type") or "room")
        era = str(room.get("era") or "old")
        condition = str(room.get("condition") or "strange")
        topics = [str(topic) for topic in room.get("topics", []) if str(topic).strip()]
        topic_text = topics[0] if topics else "forgotten work"
        return json.dumps(
            {
                "title": f"{room_type.title()}",
                "summary": f"A {condition} {room_type} carrying traces of {topic_text}.",
                "text": (
                    f"This {room_type} is {condition}, its {era} bones showing through the dust. "
                    f"Everything here seems arranged around {topic_text}, as if the room has been "
                    "waiting for someone to notice what all its small objects already know."
                ),
                "tags": [room_type, era, condition, *topics[:2]],
                "llm_choices": {"voice": "quietly observant"},
            }
        )

    def _materialize_detail(self, context: dict[str, Any]) -> str:
        subject = (
            context.get("subject") if isinstance(context.get("subject"), dict) else {}
        )
        engine_choices = (
            context.get("engine_choices")
            if isinstance(context.get("engine_choices"), dict)
            else {}
        )
        name = str(subject.get("name") or "the thing")
        band = str(subject.get("distance_band") or "adjacent")
        sentences = []
        if band == "adjacent":
            sentences.append(
                f"Up close, {name} shows its grain: wear in the places hands go, age in the places they don't."
            )
        else:
            sentences.append(
                f"From {band.replace('_', ' ')}, {name} gives away only outline and bearing."
            )
        hint = (
            engine_choices.get("weakness_hint")
            if isinstance(engine_choices.get("weakness_hint"), dict)
            else None
        )
        if hint:
            if hint.get("kind") == "mechanical":
                sentences.append(
                    f"It carries itself carefully around any suggestion of {hint.get('damage_type')}, the way wounded things avoid a remembered hurt."
                )
            else:
                sentences.append(
                    f"Something in its posture flinches at {hint.get('hint')}."
                )
        if engine_choices.get("secret_present"):
            anchor = str(engine_choices.get("anchor_name") or name)
            style = str(engine_choices.get("clue_style") or "scratches")
            sentences.append(
                f"{style.capitalize()} ring {anchor}, too deliberate to be accident."
            )
        person = (
            subject.get("person") if isinstance(subject.get("person"), dict) else None
        )
        if person and person.get("appearance"):
            sentences.append(str(person["appearance"]))
        return json.dumps(
            {
                "title": f"A Study of {name.title()}",
                "summary": f"What patient observation makes of {name} from {band}.",
                "text": " ".join(sentences),
                "tags": ["detail"],
                "llm_choices": {},
            }
        )

    def _materialize_investigation(self, context: dict[str, Any]) -> str:
        engine_choices = (
            context.get("engine_choices")
            if isinstance(context.get("engine_choices"), dict)
            else {}
        )
        subject = (
            context.get("subject") if isinstance(context.get("subject"), dict) else {}
        )
        room = subject.get("room") if isinstance(subject.get("room"), dict) else {}
        room_type = str(room.get("type") or "room")
        if engine_choices.get("secret_present"):
            anchor = str(engine_choices.get("anchor_name") or "the floor")
            style = str(engine_choices.get("clue_style") or "scratches")
            return json.dumps(
                {
                    "title": "Something Disturbed",
                    "summary": f"{style.capitalize()} point toward {anchor}.",
                    "text": (
                        f"Patience pays. {style.capitalize()} arc away from {anchor}, too regular to be "
                        f"accident; whatever stands there has been moved, and moved again, by someone "
                        "careful to put it back."
                    ),
                    "tags": ["investigation", room_type],
                    "llm_choices": {},
                }
            )
        llm_choices: dict[str, str] = {}
        text = (
            f"You go over the {room_type} a hand-span at a time. The craft is plain and the "
            "age is honest; everything here is exactly what it appears to be, worn by use "
            "rather than secrecy."
        )
        options = engine_choices.get("decoration_options")
        if isinstance(options, list) and options and isinstance(options[0], dict):
            chosen = options[0]
            llm_choices = {
                "decoration_template": str(chosen.get("template") or ""),
                "decoration_name": f"overlooked {chosen.get('name')}",
                "decoration_description": f"A {chosen.get('name')} the dust had nearly finished swallowing.",
            }
            text += f" Under a drift of dust, though, your hands find an overlooked {chosen.get('name')}."
        return json.dumps(
            {
                "title": "An Honest Accounting",
                "summary": f"Close study of the {room_type} finds craft and age, nothing concealed.",
                "text": text,
                "tags": ["investigation", room_type],
                "llm_choices": llm_choices,
            }
        )

    def _materialize_book(self, context: dict[str, Any]) -> str:
        info = self._book_seed_info(context)
        # A title prewarmed on the shelf is reused verbatim; the author is always
        # the book's own (title-only previews carry no author).
        title = info["materialized_title"] or self._mock_book_title(
            info["title_shape"],
            info["topic"],
            info["secondary"],
            info["genre"],
        )
        author = self._mock_author(info["author_role"])
        threads = (
            context.get("threads") if isinstance(context.get("threads"), dict) else {}
        )
        promises = (
            threads.get("promises") if isinstance(threads.get("promises"), list) else []
        )
        thread_line = ""
        if promises and isinstance(promises[0], dict) and promises[0].get("text"):
            thread_line = f" In the margin, another hand: '{promises[0]['text']}'"
        return json.dumps(
            {
                "title": title,
                "summary": (
                    f"A {info['stance']} {info['genre']} by a {info['author_role']}, "
                    f"written for {info['audience']} and circling {info['topic']}."
                ),
                "text": (
                    f"For {info['audience']}, I set down this {info['genre']} from the benches of the "
                    f"{info['institution']}. My office in these pages is plain: {info['purpose']}, "
                    f"though the matter has already earned the label {info['taboo']} from people who "
                    "prefer tidy shelves to true accounts.\n\n"
                    f"Do not approach {info['topic']} as a picture to admire. Approach it as work. "
                    f"The first lesson is that {info['secondary']} always stands nearby, asking to be "
                    "paid in attention before it will explain the smaller facts.\n\n"
                    f"I write as a {info['author_role']}, which means I have been wrong in public and "
                    f"corrected in private. That is why the tone here is {info['stance']}; any softer voice would make "
                    "the dangerous parts sound optional.\n\n"
                    f"Let the impatient reader take only this: the matter of {info['topic']} changes when "
                    "handled by the wrong institution, and it changes again when named for the "
                    "right audience."
                    f"{thread_line}"
                ),
                "tags": ["book", "lore"],
                "llm_choices": {
                    "author": author,
                    "voice": info["stance"],
                    "genre": info["genre"],
                },
            }
        )

    def _materialize_book_title(self, context: dict[str, Any]) -> str:
        info = self._book_seed_info(context)
        title = self._mock_book_title(
            info["title_shape"], info["topic"], info["secondary"], info["genre"]
        )
        return json.dumps(
            {
                "title": title,
                "text": title,
                "tags": ["book", "lore", "book_title"],
                "llm_choices": {},
            }
        )

    def _book_seed_info(self, context: dict[str, Any]) -> dict[str, str]:
        subject = (
            context.get("subject") if isinstance(context.get("subject"), dict) else {}
        )
        book = subject.get("book") if isinstance(subject.get("book"), dict) else {}
        catalog = book.get("catalog") if isinstance(book.get("catalog"), dict) else {}
        book_name = str(book.get("name") or "untitled volume")
        subjects = [str(s).strip() for s in book.get("subjects", []) if str(s).strip()]
        # The lean title packet omits the topic axes but carries subjects; fall back
        # to them so the mock title reads sensibly, mirroring what the LLM is told.
        topic = str(catalog.get("topic") or "").strip()
        if not topic:
            topic = subjects[0] if subjects else ""
        if not topic:
            topic_words = [w for w in book_name.split() if len(w) > 3][-2:]
            topic = " ".join(topic_words) or "small weather"
        secondary = str(catalog.get("secondary_topic") or "").strip()
        if not secondary:
            secondary = subjects[1] if len(subjects) > 1 else "ordinary grief"
        return {
            "topic": topic,
            "secondary": secondary,
            "genre": str(catalog.get("genre") or "treatise"),
            "author_role": str(catalog.get("author_role") or "minor clerk"),
            "audience": str(catalog.get("audience") or "patient readers"),
            "purpose": str(catalog.get("purpose") or "to correct a famous mistake"),
            "stance": str(catalog.get("stance") or "patient and defensive"),
            "institution": str(catalog.get("institution") or "provincial office"),
            "title_shape": str(catalog.get("title_shape") or "manual"),
            "taboo": str(catalog.get("taboo_level") or "ordinary"),
            "materialized_title": str(book.get("title") or ""),
        }

    def _mock_book_title(
        self, shape: str, topic: str, secondary: str, genre: str
    ) -> str:
        if "complaint" in shape:
            return f"Complaint Against the Keepers of {topic.title()}"
        if "confession" in shape:
            return f"Confession Concerning {topic.title()} and {secondary.title()}"
        if "registry" in shape or "record" in shape:
            return f"Registry of {topic.title()}, With Doubts"
        if "sermon" in shape:
            return f"Sermon for Those Who Touch {topic.title()}"
        if "number" in shape or "calendar" in shape:
            return f"Seventeen Appointed Days of {topic.title()}"
        if "letter" in shape:
            return f"Letter to a Student of {topic.title()}"
        if "songbook" in shape:
            return f"Songs for {topic.title()} and Bad Weather"
        if "insult" in shape:
            return f"Answer to the Fool Who Mocked {topic.title()}"
        return f"A {genre.title()} of {topic.title()} and {secondary.title()}"

    def _mock_author(self, role: str) -> str:
        words = [word for word in re.split(r"[^A-Za-z]+", role.title()) if word]
        if not words:
            return "Ellow Venn"
        return f"{words[0]} Venn"


class AutoCanonProvider:
    name = "auto"

    def __init__(self, background: bool = False) -> None:
        self.ollama = (
            OllamaCanonProvider(model=get_background_canon_model(), purpose="lore")
            if background
            else OllamaCanonProvider()
        )
        self.mock = MockCanonProvider()
        self.last_provider_name = "ollama"

    def materialize(self, context: dict[str, Any]) -> str:
        try:
            self.last_provider_name = self.ollama.name
            return self.ollama.materialize(context)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            if not fallbacks_enabled():
                raise
            self.last_provider_name = self.mock.name
            return self.mock.materialize(context)


def make_canon_provider(provider_name: str | None = None) -> CanonProvider:
    provider = (provider_name or get_canon_provider()).lower().strip()
    if provider == "mock":
        return MockCanonProvider()
    if provider == "ollama":
        return OllamaCanonProvider()
    return AutoCanonProvider()


def make_background_canon_provider(provider_name: str | None = None) -> CanonProvider:
    provider = (provider_name or get_canon_provider()).lower().strip()
    if provider == "mock":
        return MockCanonProvider()
    if provider == "ollama":
        return OllamaCanonProvider(model=get_background_canon_model(), purpose="lore")
    return AutoCanonProvider(background=True)


def resolve_canon(provider: CanonProvider, context: dict[str, Any]) -> CanonResolution:
    """Materialize one canon record, retrying malformed responses like the wild
    resolver does — a truncated or invalid JSON reply should cost a second
    attempt, not the whole interaction."""
    attempts = max(1, ollama_resolution_attempts())
    resolution = CanonResolution(None, True, "no attempts made")
    for _ in range(attempts):
        resolution = _resolve_canon_once(provider, context)
        if not resolution.technical_failure:
            return resolution
    return resolution


def _resolve_canon_once(
    provider: CanonProvider, context: dict[str, Any]
) -> CanonResolution:
    resolved_provider_name = _canon_provider_name(provider)
    raw: str | None = None
    try:
        raw = provider.materialize(context)
        data = parse_canon_json(raw)
        record = normalize_canon_record(data, context)
        audit_path = _write_canon_audit(
            provider, context, raw, record, False, None, resolved_provider_name
        )
        return CanonResolution(
            record, False, None, resolved_provider_name, raw, audit_path
        )
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        resolved_provider_name = _canon_provider_name(provider)
        error = str(exc)
        audit_path = _write_canon_audit(
            provider, context, raw, None, True, error, resolved_provider_name
        )
        return CanonResolution(
            None, True, error, resolved_provider_name, raw, audit_path
        )


def _repair_truncated_json(raw: str) -> str | None:
    """Best-effort recovery of a JSON object truncated mid-output, which happens
    when the model hits its token budget partway through a long book. Closes an
    unterminated string and any still-open brackets, trimming a dangling trailing
    key/colon/comma if needed, and only returns a result that actually parses.
    Returns None when the text can't be salvaged so callers can raise as before."""
    start = raw.find("{")
    if start == -1:
        return None
    body = raw[start:]
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in body:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()
    if escape:  # ended on a dangling backslash that would escape our closing quote
        body = body[:-1]
    if in_string:
        body += '"'
    closers = "".join(reversed(stack))
    # Closing the open brackets is enough when truncation landed after a complete
    # value (the common case). When it landed on a dangling key/colon/comma, peel
    # that fragment off and retry; trimming keys never changes bracket depth, so
    # the precomputed closers stay valid.
    candidate = body
    for _ in range(6):
        candidate = candidate.rstrip()
        try:
            # strict=False so a body that is both truncated and contains literal
            # newlines/tabs inside a string value still validates.
            json.loads(candidate + closers, strict=False)
            return candidate + closers
        except json.JSONDecodeError:
            trimmed = re.sub(r'(?:,\s*)?"[^"]*"\s*:?\s*$', "", candidate)
            trimmed = re.sub(r"[:,]\s*$", "", trimmed)
            if trimmed == candidate:
                return None
            candidate = trimmed
    return None


def parse_canon_json(raw: str) -> dict[str, Any]:
    cleaned = strip_thinking(raw)
    parsed: Any
    # strict=False tolerates literal newlines/tabs inside string values, which the
    # model routinely emits in long book prose instead of escaping them as \n.
    try:
        parsed = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        parsed = None
        start = cleaned.find("{")
        if start != -1:
            # raw_decode reads the first complete object and ignores any prose or
            # second object the model appended after it ("Extra data" errors).
            try:
                parsed, _ = json.JSONDecoder(strict=False).raw_decode(cleaned[start:])
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0), strict=False)
                except json.JSONDecodeError:
                    parsed = None
        if parsed is None:
            repaired = _repair_truncated_json(cleaned)
            if repaired is None:
                raise
            parsed = json.loads(repaired, strict=False)
    if not isinstance(parsed, dict):
        raise TypeError("canon response was not a JSON object")
    return parsed


def normalize_canon_record(
    data: dict[str, Any], context: dict[str, Any]
) -> CanonRecord:
    subject = context.get("subject") if isinstance(context.get("subject"), dict) else {}
    attachment = (
        subject.get("attachment") if isinstance(subject.get("attachment"), dict) else {}
    )
    kind = normalize_id(str(context.get("kind") or data.get("kind") or "room_flavor"))
    record_id = normalize_id(
        str(context.get("record_id") or data.get("id") or "canon_record")
    )
    if kind == "book":
        text = _clean_body(data.get("text"), _BOOK_TEXT_LIMIT)
    else:
        text = _clean_text(data.get("text"), _TEXT_LIMITS["text"])
    if not text:
        raise ValueError("canon response did not include text")
    title = _clean_text(data.get("title"), _TEXT_LIMITS["title"]) or None
    summary = (
        _clean_text(data.get("summary"), _TEXT_LIMITS["summary"])
        or text[: _TEXT_LIMITS["summary"]]
    )
    allowed_tags = {
        normalize_id(str(tag))
        for tag in context.get("allowed_tags", [])
        if str(tag).strip()
    }
    tags = [normalize_id(str(tag)) for tag in data.get("tags", []) if str(tag).strip()]
    tags.extend(str(tag) for tag in context.get("base_tags", []) if str(tag).strip())
    normalized_tags = sorted(
        {tag for tag in tags if tag and (not allowed_tags or tag in allowed_tags)}
    )
    llm_choices = {
        str(key): _clean_text(value, 60)
        for key, value in (data.get("llm_choices") or {}).items()
        if isinstance(value, (str, int, float)) and str(value).strip()
    }
    engine_choices = dict(context.get("engine_choices") or {})
    engine_choices.update(context.get("engine_private") or {})
    return CanonRecord(
        id=record_id,
        kind=kind,
        attachment=dict(attachment),
        title=title,
        text=text,
        summary=summary,
        tags=normalized_tags,
        source=str(context.get("source") or "ondemand"),
        seed_packet=dict(context),
        engine_choices=engine_choices,
        llm_choices=llm_choices,
        turn_created=int(context.get("turn") or 0),
        status="canonical",
    )


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text.strip()
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip(" ,;:-")


def _clean_body(value: Any, limit: int) -> str:
    """Multi-paragraph body text: collapse whitespace within paragraphs but
    keep paragraph breaks — book pages need them."""
    raw = str(value or "").replace("\r", "")
    paragraphs = [" ".join(part.split()) for part in raw.split("\n\n")]
    paragraphs = [part for part in paragraphs if part]
    text = "\n\n".join(paragraphs)
    if len(text) <= limit:
        return text.strip()
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip(" ,;:-")


def _write_canon_audit(
    provider: CanonProvider,
    context: dict[str, Any],
    raw_response: str | None,
    record: CanonRecord | None,
    technical_failure: bool,
    error: str | None,
    resolved_provider_name: str,
) -> str | None:
    audit_path = audit_dir() / "canon_audit.jsonl"
    prompt_context = {
        key: value for key, value in context.items() if key != "engine_private"
    }
    prompt_messages = [
        {"role": "system", "content": CANON_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(prompt_context, ensure_ascii=True)},
    ]
    audit_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "record_id": context.get("record_id"),
        "kind": context.get("kind"),
        "provider": resolved_provider_name,
        "provider_requested": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", None),
        "ollama_base_url": getattr(provider, "base_url", None),
        "prompt": {
            "messages": prompt_messages,
            "context": prompt_context,
        },
        "context": context,
        "raw_response": raw_response,
        "record": record.to_dict() if record else None,
        "technical_failure": technical_failure,
        "error": error,
    }
    return _write_jsonl_audit(audit_path, audit_record)


def _canon_provider_name(provider: CanonProvider) -> str:
    if isinstance(provider, AutoCanonProvider):
        return provider.last_provider_name
    return getattr(provider, "name", "unknown")
