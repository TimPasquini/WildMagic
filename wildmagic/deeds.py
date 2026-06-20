"""Deeds — the append-only record of what a soul has *done*.

A Deed is the atom of the emergent world: the moment the player (their soul, never the
body they happen to wear — §1.7) does something the world might react to. A deed is
*recorded* the instant it happens, carrying its **proposed** consequences (standing
shifts, legend tags). The world Simulator then **applies** those consequences exactly
once, on its daily tick (idempotency, §1.8) — the engine never double-counts a deed
across reloads, replays, or repeated ticks.

This is the Phase-0 slice: one deed type (`killed_imperials`) flowing end to end through
the real abstraction. Breadth (more deed types, witness-via-FOV, LLM interpretation of
ambiguous deeds) lands in Phase A; causal compression into `StoryBeat`s lands there too.

See `EMERGENT_WORLD_IMPLEMENTATION.md` §1.1 / §1.8.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- Bounded vocabularies (§1.8) ---------------------------------------------------
# Curated and small, like the prop mechanical-tag list. Emergent state must not sprawl,
# so additions to these tuples are deliberate, not incidental.

#: The kinds of deed the world recognizes (§1.8). Each maps to a consequence rule in
#: DEED_RULES below; the LLM interpreter (A.2) handles deeds it can't classify by rule.
DEED_TYPES: tuple[str, ...] = (
    "killed_imperials",
    "killed_combatant",
    "killed_civilians",
    "spared_enemy",
    "freed_captive",
    "defended_townsfolk",
    "witnessed_forbidden_magic",
    "razed_building",
    "cast_atrocity",
    "raised_dead",
    "desecration",
)

#: What a deed acted *upon* (used to route consequences in later phases).
TARGET_TAGS: tuple[str, ...] = (
    "empire",
    "civilian",
    "shrine",
    "rebel",
    "creature",
)

#: How widely a deed is known (strategy §5.1). Drives rumor/poster legibility; the order
#: is the escalation ladder secret → witnessed → public → mythic.
VISIBILITY: tuple[str, ...] = ("secret", "witnessed", "public", "mythic")

#: Visibility levels at which a deed is "out in the world" (can spawn rumors/posters).
PUBLIC_VISIBILITY: frozenset[str] = frozenset({"witnessed", "public", "mythic"})

#: Deed types that record killing a faction's member — the basis of per-faction kill
#: accounting (`FACTION_KILL_REPUTATION.md` K2). Each carries a ``victim_faction``. New
#: kill types (e.g. a generic combatant kill, rolled-faction kills) are added here.
KILL_DEEDS: frozenset[str] = frozenset(
    {"killed_imperials", "killed_combatant", "killed_civilians"}
)

#: Kill deeds whose standing consequences are **relational** (K3): computed at record time
#: from each faction's stance toward the victim's faction, *overriding* any role-based rule.
#: Killing a faction's combatant pleases that faction's enemies and angers its friends —
#: generalizing the hardcoded empire↔resistance reaction to the whole rolled roster. Civilian
#: killings stay rule-based (the qualitatively different butchery reaction), so they are out.
RELATIONAL_KILL_DEEDS: frozenset[str] = frozenset(
    {"killed_imperials", "killed_combatant"}
)


@dataclass
class Deed:
    """One recorded action by a soul, plus the consequences it *proposes*.

    Intentionally **not** ``frozen``: the daily tick flips ``applied`` when it consumes
    the deed (idempotency, §1.8) and the legibility layer flips ``rumored`` once the deed
    has surfaced as a rumor. The strategy doc sketched ``frozen=True`` but also asked for
    a mutable ``applied`` flag — the flag wins; see the session log.
    """

    id: str
    turn: int
    zone: tuple[int, int]
    type: str  # one of DEED_TYPES
    magnitude: float  # normalized 0..1 (count killed, structure size, severity)
    actor: str  # the SOUL id (state.player_soul_id), never the body (§1.7)
    source: str  # action source: combat | spell | interaction
    interpretation_source: str = "rules"  # rules | llm | fallback (D5)
    # A finer location than the overworld zone alone: "<zx>,<zy>@<depth>" — so a deed on a
    # dungeon level doesn't blur with one on the surface above it when consequences render.
    place_key: str = ""
    target_tags: list[str] = field(default_factory=list)  # TARGET_TAGS
    #: For kill deeds (KILL_DEEDS): the faction-ledger id (or the ``civilian`` bucket) whose
    #: member died, for per-faction kill accounting (`FACTION_KILL_REPUTATION.md` K1). ``""``
    #: means not a kill, or an unaligned creature (beasts are tally-exempt).
    victim_faction: str = ""
    #: The **souls** this deed touched (EMERGENT_QUESTS Q0/§5): the freed captive, the slain
    #: target, the defended townsperson — by stable soul ref, so a specific-person quest
    #: objective matches the right person across disguise/resurrection, not merely "a civilian".
    subject_refs: list[str] = field(default_factory=list)
    # Knowledge model (strategy §5.1):
    visibility: str = "secret"
    witnesses: list[str] = field(default_factory=list)  # entity ids that perceived it
    evidence_tags: list[str] = field(
        default_factory=list
    )  # bloodstain, burned_market...
    # Proposed consequences — recorded here, applied once by the tick:
    standing_deltas: dict[str, dict[str, float]] = field(default_factory=dict)
    legend_tags: dict[str, float] = field(default_factory=dict)
    applied: bool = False  # set True when the simulator has consumed it
    rumored: bool = False  # set True when surfaced as a rumor (legibility de-dupe)
    summary: str = ""  # one line for chronicle / named voices

    @property
    def is_public(self) -> bool:
        """True once the deed is known beyond the doer — eligible for rumors/posters."""
        return self.visibility in PUBLIC_VISIBILITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "turn": self.turn,
            "zone": list(self.zone),
            "type": self.type,
            "magnitude": self.magnitude,
            "actor": self.actor,
            "source": self.source,
            "interpretation_source": self.interpretation_source,
            "place_key": self.place_key,
            "target_tags": list(self.target_tags),
            "victim_faction": self.victim_faction,
            "subject_refs": list(self.subject_refs),
            "visibility": self.visibility,
            "witnesses": list(self.witnesses),
            "evidence_tags": list(self.evidence_tags),
            "standing_deltas": {
                fid: dict(axes) for fid, axes in self.standing_deltas.items()
            },
            "legend_tags": dict(self.legend_tags),
            "applied": self.applied,
            "rumored": self.rumored,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Deed":
        zone = raw.get("zone") or [0, 0]
        return cls(
            id=str(raw.get("id", "")),
            turn=int(raw.get("turn", 0)),
            zone=(int(zone[0]), int(zone[1])),
            type=str(raw.get("type", "")),
            magnitude=float(raw.get("magnitude", 0.0)),
            actor=str(raw.get("actor", "")),
            source=str(raw.get("source", "")),
            interpretation_source=str(raw.get("interpretation_source", "rules")),
            place_key=str(raw.get("place_key", "")),
            target_tags=[str(t) for t in raw.get("target_tags", [])],
            victim_faction=str(raw.get("victim_faction", "")),
            subject_refs=[str(s) for s in raw.get("subject_refs", [])],
            visibility=str(raw.get("visibility", "secret")),
            witnesses=[str(w) for w in raw.get("witnesses", [])],
            evidence_tags=[str(t) for t in raw.get("evidence_tags", [])],
            standing_deltas={
                str(fid): {str(axis): float(v) for axis, v in (axes or {}).items()}
                for fid, axes in (raw.get("standing_deltas") or {}).items()
            },
            legend_tags={
                str(tag): float(v) for tag, v in (raw.get("legend_tags") or {}).items()
            },
            applied=bool(raw.get("applied", False)),
            rumored=bool(raw.get("rumored", False)),
            summary=str(raw.get("summary", "")),
        )


# --- The deterministic deed -> consequence rules (Phase A.1) ----------------------
# A declarative table is the general system the strategy asks for: emission sites only
# describe *what happened* (type + magnitude + target tags + visibility); this table
# decides *what it means* — and crucially, "one deed produces different consequences along
# different axes" (strategy §5.1). Coefficients are per unit of deed magnitude; the rules
# interpreter scales them. The LLM interpreter (A.2) only handles deeds with no rule.
#
# Standing keys are stable **roles** (factions.ROLE_TO_KINDS), not literal faction ids:
# "empire" = the imperial bloc, "resistance" = those who oppose it. The engine resolves a
# role to the concrete faction(s) filling it (record_deed), so the same rule works for the
# Phase-0 two-pole scaffold and Phase C's full rolled roster.


@dataclass(frozen=True)
class DeedRule:
    #: role (factions.ROLE_TO_KINDS) -> standing axis -> coefficient (per unit magnitude)
    standing: dict[str, dict[str, float]] = field(default_factory=dict)
    #: legend tag (LEGEND_VOCAB) -> coefficient (per unit magnitude)
    legend: dict[str, float] = field(default_factory=dict)


DEED_RULES: dict[str, DeedRule] = {
    # Striking the Empire: rebels are grateful and emboldened; the Empire marks you a
    # threat and fears you; you become known as defiant.
    "killed_imperials": DeedRule(
        standing={
            "empire": {"imperial_threat": 1.0, "fear": 0.5},
            "resistance": {"gratitude": 0.8, "notoriety": 0.4, "legitimacy": 0.3},
        },
        legend={"defiant": 1.0},
    ),
    # Cutting down the helpless: the people fear and disown you, your cause loses
    # legitimacy, and you earn the name butcher; the Empire notes a dangerous element.
    "killed_civilians": DeedRule(
        standing={
            "empire": {"imperial_threat": 0.3, "fear": 0.3},
            "resistance": {
                "gratitude": -0.8,
                "fear": 0.7,
                "legitimacy": -0.6,
                "notoriety": 0.4,
            },
        },
        legend={"butcher": 1.0},
    ),
    # Mercy to a beaten foe: rebels see a rightful cause; you are known as merciful.
    "spared_enemy": DeedRule(
        standing={
            "resistance": {"gratitude": 0.3, "legitimacy": 0.4},
            "empire": {"fear": -0.2},
        },
        legend={"merciful": 1.0},
    ),
    # Freeing captives: deep gratitude and legitimacy; the Empire prioritizes you.
    "freed_captive": DeedRule(
        standing={
            "resistance": {"gratitude": 1.0, "legitimacy": 0.6, "notoriety": 0.4},
            "empire": {"imperial_threat": 0.6},
        },
        legend={"liberator": 1.0},
    ),
    # Seen working forbidden wild magic: the Empire opens your file and marks you a threat —
    # the exposure model (CONTENT_FLESHING_ROADMAP). Being *seen* is the crime, not the result,
    # so this carries no legend of its own (what you DO with the magic earns the legend).
    "witnessed_forbidden_magic": DeedRule(
        standing={"empire": {"imperial_threat": 0.4, "fear": 0.2}},
        legend={},
    ),
    # Defending townsfolk: gratitude and a protector's name.
    "defended_townsfolk": DeedRule(
        standing={
            "resistance": {"gratitude": 0.8, "legitimacy": 0.5},
            "empire": {"imperial_threat": 0.2},
        },
        legend={"protector": 1.0},
    ),
    # Razing a structure: the Empire is threatened and afraid; the people are awed and
    # wary; you are a destroyer.
    "razed_building": DeedRule(
        standing={
            "empire": {"imperial_threat": 0.7, "fear": 0.4},
            "resistance": {"fear": 0.5, "notoriety": 0.5},
        },
        legend={"destroyer": 1.0},
    ),
    # Catastrophic destructive magic: alarming on every side; you are uncanny and a
    # destroyer both.
    "cast_atrocity": DeedRule(
        standing={
            "empire": {"imperial_threat": 0.6, "fear": 0.6, "uncanniness": 0.5},
            "resistance": {"fear": 0.7, "uncanniness": 0.8},
        },
        legend={"uncanny": 1.0, "destroyer": 0.5},
    ),
    # Raising the dead: spiritually alarming; gratitude only when it shields the living.
    "raised_dead": DeedRule(
        standing={
            "empire": {"imperial_threat": 0.4, "uncanniness": 0.6},
            "resistance": {"uncanniness": 1.0, "fear": 0.3},
        },
        legend={"uncanny": 1.0},
    ),
    # Desecration: a wound to legitimacy and a spiritual horror.
    "desecration": DeedRule(
        standing={
            "empire": {"imperial_threat": 0.3, "uncanniness": 0.5},
            "resistance": {"legitimacy": -0.5, "uncanniness": 1.0, "fear": 0.4},
        },
        legend={"uncanny": 1.0},
    ),
}


def interpret_deed_rules(deed: Deed) -> None:
    """Fill a deed's *proposed* consequences from the deterministic rule table, scaled by
    its magnitude (Phase A.1). Sets ``interpretation_source="rules"``. A deed whose type
    has no rule is left for the LLM interpreter (A.2); it stays consequence-free here."""
    deed.interpretation_source = "rules"
    rule = DEED_RULES.get(deed.type)
    if rule is None:
        return
    deed.standing_deltas = {
        faction: {
            axis: round(coeff * deed.magnitude, 4) for axis, coeff in axes.items()
        }
        for faction, axes in rule.standing.items()
    }
    deed.legend_tags = {
        tag: round(coeff * deed.magnitude, 4) for tag, coeff in rule.legend.items()
    }


@dataclass(frozen=True)
class StoryBeat:
    """A causal compression of several deeds into one narratable arc (§1.5). Beats let the
    chronicle and named voices summarize from a handful of arcs instead of raw deeds,
    keeping prompts small on the A750. Compression is **additive** — it only references
    deed ids, never deletes or rewrites deeds."""

    id: str
    summary: str
    source_deeds: list[str]
    salience: float
    factions_affected: list[str]
    tags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "source_deeds": list(self.source_deeds),
            "salience": self.salience,
            "factions_affected": list(self.factions_affected),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StoryBeat":
        return cls(
            id=str(raw.get("id", "")),
            summary=str(raw.get("summary", "")),
            source_deeds=[str(d) for d in raw.get("source_deeds", [])],
            salience=float(raw.get("salience", 0.0)),
            factions_affected=[str(f) for f in raw.get("factions_affected", [])],
            tags=[str(t) for t in raw.get("tags", [])],
        )


#: How a cluster of same-type deeds reads once compressed into a beat.
_BEAT_SUMMARIES: dict[str, str] = {
    "killed_imperials": "a campaign of strikes against the Empire",
    "killed_combatant": "a tally of fighters cut down",
    "killed_civilians": "a trail of slaughter among the helpless",
    "spared_enemy": "a pattern of mercy to the beaten",
    "freed_captive": "a run of jailbreaks and freed captives",
    "defended_townsfolk": "a record of standing for the common folk",
    "witnessed_forbidden_magic": "a habit of working magic in plain sight",
    "razed_building": "a swath of razed and ruined places",
    "cast_atrocity": "a series of catastrophic conjurings",
    "raised_dead": "repeated raisings of the dead",
    "desecration": "a string of desecrations",
}


@dataclass
class DeedLedger:
    """Append-only list of deeds on the GameState. The world tick reads ``unapplied()``;
    the legibility layer reads ``public()``; ``compress()`` mints story beats."""

    deeds: list[Deed] = field(default_factory=list)
    beats: list["StoryBeat"] = field(default_factory=list)

    def record(self, deed: Deed) -> Deed:
        self.deeds.append(deed)
        return deed

    def recent(self, since_turn: int) -> list[Deed]:
        return [deed for deed in self.deeds if deed.turn >= since_turn]

    def by_visibility(self, *levels: str) -> list[Deed]:
        wanted = set(levels)
        return [deed for deed in self.deeds if deed.visibility in wanted]

    def kills_by_faction(self) -> dict[str, int]:
        """How many of each faction the player has killed — a pure projection over recorded
        kill deeds (`FACTION_KILL_REPUTATION.md` K2). Derived, not stored: it can't desync
        from the deeds, replays for free, and never decays (it is the raw fact; *feelings*
        about it live in faction standing). Keyed by ``victim_faction`` (a faction id or the
        ``civilian`` bucket); unaligned creatures carry no faction and are excluded."""
        counts: dict[str, int] = {}
        for deed in self.deeds:
            if deed.type in KILL_DEEDS and deed.victim_faction:
                counts[deed.victim_faction] = counts.get(deed.victim_faction, 0) + 1
        return counts

    def unapplied(self) -> list[Deed]:
        return [deed for deed in self.deeds if not deed.applied]

    def public(self) -> list[Deed]:
        return [deed for deed in self.deeds if deed.is_public]

    def next_id(self, turn: int) -> str:
        """A deterministic, ledger-local id (does not perturb the entity counter, so
        deed creation can't shift entity ids and is replay-stable)."""
        return f"deed_{turn}_{len(self.deeds)}"

    def compress(self, min_cluster: int = 3) -> list[StoryBeat]:
        """Additively mint story beats from clusters of same-type deeds not yet beaten
        (§1.5). Deeds are never modified or removed — a beat only *references* deed ids.
        Returns the beats created this call. Deterministic (sorted by deed type)."""
        already: set[str] = {
            deed_id for beat in self.beats for deed_id in beat.source_deeds
        }
        clusters: dict[str, list[Deed]] = {}
        for deed in self.deeds:
            if deed.id in already:
                continue
            clusters.setdefault(deed.type, []).append(deed)
        created: list[StoryBeat] = []
        for deed_type in sorted(clusters):
            group = clusters[deed_type]
            if len(group) < min_cluster:
                continue
            factions = sorted({fid for deed in group for fid in deed.standing_deltas})
            beat = StoryBeat(
                id=f"beat_{len(self.beats) + len(created)}",
                summary=_BEAT_SUMMARIES.get(deed_type, f"a pattern of {deed_type}"),
                source_deeds=[deed.id for deed in group],
                salience=round(sum(deed.magnitude for deed in group), 4),
                factions_affected=factions,
                tags=[deed_type],
            )
            created.append(beat)
        self.beats.extend(created)
        return created

    def to_dict(self) -> dict[str, Any]:
        return {
            "deeds": [deed.to_dict() for deed in self.deeds],
            "beats": [beat.to_dict() for beat in self.beats],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeedLedger":
        return cls(
            deeds=[
                Deed.from_dict(item)
                for item in (raw or {}).get("deeds", [])
                if isinstance(item, dict)
            ],
            beats=[
                StoryBeat.from_dict(item)
                for item in (raw or {}).get("beats", [])
                if isinstance(item, dict)
            ],
        )
