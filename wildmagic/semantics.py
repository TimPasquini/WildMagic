"""The semantic-effects substrate: one shared world-knowledge layer that every LLM
touchpoint (resolver, AI, dialogue, trade, lore) reads from and writes to.

The design rationale lives in docs/SEMANTIC_EFFECTS.md. In short: in an LLM-resolved game,
a description like "a righteous, goblin-hating hat" is not flavor and not mechanics -- it is
*latent* mechanics, waiting for a context where it becomes relevant (the hat is animated and
picks a goblin; a goblin merchant refuses to trade). For that latent payoff to fire, the
fact has to be in the prompt at the moment it matters. That is a retrieval problem, and this
module is its answer.

A semantic fact (a `WorldNote`) attaches to an **anchor** -- the thing it is about. The
anchor is the retrieval index: gather the notes anchored to the entities, place, and factions
in the current scene, rank them, and splice a budgeted block into the prompt. Entity-attached
facts ride into prompts for free (they are already in the scene); place/faction/world facts
are gathered by the scene assembler here.

Principles enforced structurally (see the doc):
  * Semantic by default, mechanical on demand -- notes carry no rules; the model *weighs*
    them. The only sanctioned bridge to mechanics is explicit crystallization (emit an aura
    or tag), which lives in the effect layer, not here.
  * Never on the critical path -- the hard engine never reads notes to decide outcomes; only
    the LLM consumers read them, and only to color judgment.
  * Bounded -- notes have salience and a finite life, are capped per anchor, and decay, so the
    context never silts up into contradiction soup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ----------------------------------------------------------------------------------------
# Anchors. An anchor is a short string key naming what a note is about. Keeping it a plain
# string (rather than a typed object) means it slots straight into a dict and into logs.
# ----------------------------------------------------------------------------------------

WORLD_ANCHOR = "world"


def entity_anchor(entity_id: str) -> str:
    return f"entity:{entity_id}"


def place_anchor(x: int, y: int) -> str:
    return f"place:{int(x)},{int(y)}"


def faction_anchor(faction: str) -> str:
    return f"faction:{str(faction).strip().lower()}"


# The shared interpretation convention. Included by EVERY LLM consumer so a trait means the
# same thing to the resolver, the AI, and the dialogue model -- which is what lets richness
# compound across subsystems instead of fragmenting.
SEMANTIC_PREAMBLE = (
    "NARRATIVE TRAITS & WORLD NOTES. Entities, places, and the world may carry traits and "
    "notes -- descriptive facts with no fixed mechanical rule (e.g. \"a righteous, "
    "goblin-hating hat\", \"this floor remembers a murder\", \"the captain is secretly "
    "afraid of crows\"). Let them color tone, targeting, plausibility, and reactions. You "
    "MAY turn one into a concrete effect when the situation squarely calls for it (and then "
    "price that effect normally), but do not manufacture power from flavor on every action. "
    "Weigh them; do not mechanically apply them."
)


@dataclass
class WorldNote:
    """One atomic semantic fact about an anchor. Short on purpose: a note is a sentence the
    models can weigh, not a paragraph. `salience` (1-5) and `expires_turn` let the scene
    assembler rank and prune, so the context stays sharp."""

    text: str
    kind: str = "trait"  # trait | event | rumor | mood | secret ...
    source: str = "unknown"  # what minted it: a spell id, "combat", "dialogue", ...
    turn_created: int = 0
    salience: int = 3  # 1 (trivia) .. 5 (defining)
    expires_turn: int | None = None  # None = lasts until evicted by the per-anchor cap

    def is_live(self, turn: int) -> bool:
        return self.expires_turn is None or turn < self.expires_turn

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"text": self.text, "kind": self.kind}
        # Keep the surfaced form compact -- the model needs the fact and its weight, not the
        # bookkeeping. Provenance stays available in logs via the full record.
        if self.salience != 3:
            data["salience"] = self.salience
        return data

    def to_record(self, anchor: str, turn: int) -> dict[str, Any]:
        return {
            "anchor": anchor,
            "text": self.text,
            "kind": self.kind,
            "source": self.source,
            "salience": self.salience,
            "turn_created": self.turn_created,
            "age": max(0, turn - self.turn_created),
            "expires_turn": self.expires_turn,
        }


@dataclass
class SemanticLedger:
    """The world's system of record for semantic facts, keyed by anchor. One ledger, shared
    by every consumer -- this single-substrate decision is the whole point: a fact minted by
    a spell is visible to dialogue, trade, and the AI without any per-subsystem wiring."""

    notes: dict[str, list[WorldNote]] = field(default_factory=dict)
    per_anchor_cap: int = 6

    def record(
        self,
        anchor: str,
        text: str,
        *,
        turn: int,
        kind: str = "trait",
        source: str = "unknown",
        salience: int = 3,
        ttl: int | None = None,
    ) -> WorldNote | None:
        """Deposit a fact. De-duplicates against the same anchor (a repeated observation
        refreshes/raises salience rather than piling up) and enforces the per-anchor cap,
        evicting the lowest-salience, oldest note first."""
        text = " ".join(str(text).split())[:200]
        if not text:
            return None
        salience = max(1, min(5, int(salience)))
        expires_turn = None if ttl is None else int(turn) + max(1, int(ttl))
        bucket = self.notes.setdefault(anchor, [])
        lowered = text.lower()
        for existing in bucket:
            if existing.text.lower() == lowered:
                existing.salience = max(existing.salience, salience)
                existing.turn_created = turn
                existing.expires_turn = expires_turn
                return existing
        note = WorldNote(
            text=text,
            kind=str(kind),
            source=str(source),
            turn_created=int(turn),
            salience=salience,
            expires_turn=expires_turn,
        )
        bucket.append(note)
        if len(bucket) > self.per_anchor_cap:
            # Evict the least worth keeping: lowest salience, then oldest.
            bucket.sort(key=lambda n: (n.salience, n.turn_created))
            del bucket[0]
        return note

    def for_anchors(
        self, anchors: Iterable[str], *, turn: int, limit: int = 8
    ) -> list[WorldNote]:
        """Gather the live notes for a set of anchors, ranked by salience then recency, and
        budgeted to `limit`. This is the retrieval half of the scene assembler."""
        gathered: list[WorldNote] = []
        seen: set[int] = set()
        for anchor in anchors:
            for note in self.notes.get(anchor, ()):  # type: ignore[arg-type]
                if note.is_live(turn) and id(note) not in seen:
                    gathered.append(note)
                    seen.add(id(note))
        gathered.sort(key=lambda n: (n.salience, n.turn_created), reverse=True)
        return gathered[:limit]

    def decay(self, turn: int) -> int:
        """Drop expired notes. Called once per turn alongside the other tick passes."""
        removed = 0
        for anchor in list(self.notes.keys()):
            kept = [n for n in self.notes[anchor] if n.is_live(turn)]
            removed += len(self.notes[anchor]) - len(kept)
            if kept:
                self.notes[anchor] = kept
            else:
                del self.notes[anchor]
        return removed
