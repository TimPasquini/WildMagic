"""Lore cards: tiered authored world-knowledge for dialogue, books, and beyond.

Design + rationale: docs/LORE_CARDS.md. World fact this serves: docs/WORLDBUILDING.md.

This module is pure data + pure functions. Authored lore lives in ``content/lore/*.md``;
the parser in ``file_lore_cards.py`` adapts those files into ``LoreCard`` records here.
Provider calls stay in ``lore_router.py`` and are injected as ``route_call``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .file_lore_cards import load_file_lore_sections


@dataclass(frozen=True)
class LoreCard:
    name: str
    tags: tuple[str, ...]
    threshold: int
    triggers: tuple[str, ...]
    index_line: str
    text: str
    version: int = 1
    topic: str = ""
    level: int | None = None
    source: str = ""


def _load_lore_cards() -> tuple[LoreCard, ...]:
    return tuple(
        LoreCard(
            name=section.name,
            tags=section.tags,
            threshold=section.threshold,
            triggers=section.triggers,
            index_line=section.description,
            text=section.text,
            version=section.version,
            topic=section.topic,
            level=section.level,
            source=section.source_label,
        )
        for section in load_file_lore_sections()
    )


LORE_CARDS: tuple[LoreCard, ...] = _load_lore_cards()
_BY_NAME: dict[str, LoreCard] = {c.name: c for c in LORE_CARDS}
KNOWN_TAGS: frozenset[str] = frozenset(t for c in LORE_CARDS for t in c.tags)

# Book subjects and free-text topics are generated metadata, not guaranteed tags. Normalize
# onto the canonical vocabulary before matching; unknowns are dropped.
TAG_ALIASES: dict[str, str] = {
    "crystals": "crystal",
    "crystal magic": "crystal",
    "charter law": "charter",
    "charter magic": "charter",
    "charters": "charter",
    "the empire": "empire",
    "grand empire": "empire",
    "imperial": "empire",
    "vigovian": "vigovia",
    "stalnazan": "stalnaz",
    "stalnazi": "stalnaz",
    "bralli": "brall",
    "ryolani": "ryolan",
    "vintan": "vint",
    "threenian": "threen",
    "ontrian": "ontria",
    "montearian": "monteary",
    "bone magic": "bone",
    "blood magic": "blood",
    "woven magic": "woven",
    "the parn": "parn",
    "goatfolk": "gontark",
    "the shadow purge": "shadow_purge",
}

_WORD_RE = re.compile(r"[a-z]+")


def normalize_lore_tags(raw: Iterable[str]) -> set[str]:
    """Map free-text subjects onto canonical tags; drop anything unknown."""
    out: set[str] = set()
    for s in raw:
        k = str(s).strip().lower()
        k = TAG_ALIASES.get(k, k)
        if k in KNOWN_TAGS:
            out.add(k)
    return out


_ROLE_LEVELS: tuple[tuple[tuple[str, ...], int], ...] = (
    (
        (
            "scholar",
            "philosoph",
            "sage",
            "priest",
            "scribe",
            "loremaster",
            "historian",
            "chronicler",
            "keeper of",
        ),
        3,
    ),
    (
        (
            "guide",
            "innkeeper",
            "barkeep",
            "bartender",
            "host",
            "elder",
            "steward",
            "archivist",
            "librarian",
        ),
        2,
    ),
    (
        (
            "noble",
            "official",
            "magistrate",
            "captain",
            "courtier",
            "lord",
            "lady",
            "governor",
        ),
        2,
    ),
    (("merchant", "trader", "peddler", "factor"), 1),
)


def _role_level(role: str) -> int:
    r = role.lower()
    for keywords, level in _ROLE_LEVELS:
        if any(k in r for k in keywords):
            return level
    return 1


def seed_npc_lore(
    role: str,
    traits: Iterable[str] = (),
    tags: Iterable[str] = (),
    region: str = "",
) -> dict[str, int]:
    """Assign starting lore from role, entity tags, region, and named traditions."""
    level = _role_level(role)
    sources = (
        list(tags) + _WORD_RE.findall(region.lower()) + _WORD_RE.findall(role.lower())
    )
    home = normalize_lore_tags(sources)
    return {tag: level for tag in home}


def knows(lore: Mapping[str, int], card: LoreCard) -> bool:
    """A knower reaches a card when summed lore across its tags meets its threshold."""
    return sum(lore.get(t, 0) for t in card.tags) >= card.threshold


def eligible_cards(
    lore: Mapping[str, int], registry: Iterable[LoreCard] = LORE_CARDS
) -> list[LoreCard]:
    return [c for c in registry if knows(lore, c)]


LORE_ROUTER_SCHEMA: dict = {
    "type": "object",
    "properties": {"cards": {"type": "array", "items": {"type": "string"}}},
    "required": ["cards"],
}


def _query_words(query: str) -> set[str]:
    return set(_WORD_RE.findall(query.lower()))


def _card_terms(card: LoreCard) -> set[str]:
    return set(card.triggers) | set(card.tags)


def _keyword_hit(card: LoreCard, words: set[str]) -> bool:
    terms = _card_terms(card)
    return any(any(w == t or w.startswith(t) for t in terms) for w in words)


def prefilter(cands: list[LoreCard], query: str, subjects: set[str]) -> list[LoreCard]:
    """Keyword/tag hits over eligible candidates."""
    words = _query_words(query)
    hits: list[LoreCard] = []
    for c in cands:
        if subjects & set(c.tags):
            hits.append(c)
        elif _keyword_hit(c, words):
            hits.append(c)
    return hits


def _score(
    card: LoreCard, words: set[str], subjects: set[str], bias: set[str]
) -> tuple[int, int, int, int]:
    terms = _card_terms(card)
    hits = sum(1 for w in words for t in terms if w == t or w.startswith(t))
    subj_hit = 1 if subjects & set(card.tags) else 0
    bias_hit = 1 if bias & set(card.tags) else 0
    return (subj_hit, hits, bias_hit, -card.threshold)


def _rank(
    cards: list[LoreCard], query: str, subjects: set[str], bias: set[str]
) -> list[LoreCard]:
    words = _query_words(query)
    return sorted(cards, key=lambda c: _score(c, words, subjects, bias), reverse=True)


def _budget(cards: list[LoreCard], max_cards: int, max_chars: int) -> list[LoreCard]:
    out: list[LoreCard] = []
    used = 0
    for c in cards[:max_cards]:
        if out and used + len(c.text) > max_chars:
            break
        out.append(c)
        used += len(c.text)
    return out


def build_router_messages(
    knower_blurb: str, query: str, cands: list[LoreCard]
) -> list[dict]:
    index = "\n".join(f"- {c.name}: {c.index_line}" for c in cands)
    system = (
        "You are a retrieval router for a speaker's world-knowledge. Given who the speaker "
        "is and what is being discussed, choose which LORE CARDS (by id) are relevant to THIS "
        "exchange. Pick only what is on-topic - usually 1 to 5, fewer is better. "
        'Return ONLY {"cards": ["id", ...]}.\n\nAvailable lore cards:\n' + index
    )
    user = json.dumps({"speaker": knower_blurb, "topic": query}, ensure_ascii=True)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def select_lore_cards(
    lore: Mapping[str, int],
    query: str,
    *,
    subjects: Iterable[str] = (),
    bias_tags: Iterable[str] = (),
    knower_blurb: str = "",
    route_call: Callable[[list[dict]], list[str] | None] | None = None,
    registry: Iterable[LoreCard] = LORE_CARDS,
    max_cards: int = 5,
    max_chars: int = 1200,
) -> list[LoreCard]:
    """Gate, prefilter, optionally route, and budget lore cards."""
    elig = eligible_cards(lore, registry)
    if not elig:
        return []
    subj = normalize_lore_tags(subjects)
    bias = normalize_lore_tags(bias_tags)
    hinted = prefilter(elig, query, subj)
    if not hinted:
        return []
    if route_call is None or len(hinted) <= max_cards:
        return _budget(_rank(hinted, query, subj, bias), max_cards, max_chars)
    names = route_call(build_router_messages(knower_blurb, query, hinted))
    if names is None:
        return _budget(_rank(hinted, query, subj, bias), max_cards, max_chars)
    chosen = [c for c in hinted if c.name in set(names)]
    return _budget(chosen, max_cards, max_chars)
