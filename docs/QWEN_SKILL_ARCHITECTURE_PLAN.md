# File-Backed Lore Card Plan

Status: implemented. `content/lore/*.md` is the authored source of truth for lore cards.

Companion docs:

- `LORE_CARDS.md` for the current tiered lore-card system.
- `CAPABILITY_ROUTING.md` and `CAPABILITY_CARD_PLAN.md` for the live wild-magic mechanic
  router.
- `ARCHITECTURE.md` for the current implemented modules.

## 1. Decision

Use **file-backed lore cards** for authored world knowledge. The live implementation now
loads `content/lore/*.md` through `wildmagic/file_lore_cards.py` and adapts those sections
into the existing `LoreCard` selection API.

Do not require model tool calls for lore routing. Keep the existing engine-owned
router/card model:

```text
engine reads lore files
-> engine gates sections by lore level
-> router sees only eligible section descriptions
-> router selects relevant eligible sections
-> engine injects selected section bodies
```

The useful architectural idea is progressive disclosure:

```text
topic file -> level sections -> per-level description -> selected body text
```

## 2. Why Change

The current Python `LoreCard(...)` registry works, but it is not the nicest authoring surface
for a large world. As lore deepens into L2-L4, factions, people, traditions, books, and
regional histories, Python constructors will become awkward.

File-backed lore cards give us:

- prose in editable Markdown
- structured per-level metadata for gates and routing
- complete hiding of inaccessible high-level sections
- one file per topic, easier to review than many scattered constructors
- a path to long appendices/deep lore without bloating every prompt
- the same engine authority and deterministic fallback behavior we already have

## 3. Format Choice

Use **Markdown with fenced TOML metadata blocks**.

Why Markdown:

- best authoring experience for lore prose
- easy to read in an editor
- supports headings and long-form text naturally

Why TOML for metadata:

- more structured than ad hoc Markdown headings
- less indentation-sensitive than YAML
- more human-friendly than JSON
- already familiar in this repo via `pyproject.toml`
- Python 3.12 includes `tomllib` for parsing

Avoid putting long lore bodies inside TOML strings. TOML should describe sections; Markdown
should carry prose.

## 4. File Layout

Use one file per lore topic:

```text
content/lore/empire.md
content/lore/vigovia.md
content/lore/charter.md
content/lore/shadow_purge.md
content/lore/stalnaz.md
content/lore/crystal.md
content/lore/brall.md
content/lore/bone.md
content/lore/ryolan.md
content/lore/blood.md
content/lore/vint.md
content/lore/woven.md
content/lore/threen.md
content/lore/monteary.md
content/lore/ontria.md
content/lore/gontark.md
content/lore/parn.md
content/lore/birdfolk.md
content/lore/merfolk.md
content/lore/rentacosta.md
```

The file is the topic card. Each level section is the routed/gated unit. A topic may have
more than one section at the same level when that preserves useful existing card boundaries,
but each section must have a unique `name`.

## 5. File Shape

Example:

````markdown
# Monteary

```toml lore
id = "monteary"
tags = ["monteary"]
```

## Level 0

```toml meta
description = "Common traveler knowledge: Monteary is famous for the world's best horses."
triggers = ["monteary", "horse", "horses", "gelding", "stallion"]
```

Monteary breeds the finest horses in the world. Travelers know its geldings are prized and
its stallions are guarded.

## Level 1

```toml meta
description = "Basic local knowledge about stallion control, gelding exports, and horse politics."
triggers = ["stallion", "gelding", "bloodline", "pasture", "breeding"]
```

Geldings are exported freely, but stallions are watched with political care. Horse talk in
Monteary is never only horse talk.

## Level 2

```toml meta
description = "Experienced knowledge about bloodlines, pasture rights, and breeding disputes."
triggers = ["bloodline", "pasture", "foal", "marriage", "dispute"]
```

...
````

Required file-level metadata:

- `id`: stable topic id
- `tags`: lore tags this file contributes to

Required level metadata:

- `description`: one sentence shown to the router when that level is eligible

Optional level metadata:

- `name`: stable section/card id; defaults to `topic:level` when omitted
- `triggers`: keywords for deterministic routing
- `version`: section-level invalidation if needed later
- `subjects`: extra subject aliases, if a section serves multiple topics
- `draft`: parse this section but exclude it from the live registry until finished

## 6. Access And Routing

Lore access is still engine-owned.

For a speaker with:

```python
npc.lore == {"monteary": 1}
```

the engine may expose:

```json
[
  {
    "id": "monteary:0",
    "topic": "monteary",
    "level": 0,
    "description": "Common traveler knowledge: Monteary is famous for the world's best horses."
  },
  {
    "id": "monteary:1",
    "topic": "monteary",
    "level": 1,
    "description": "Basic local knowledge about stallion control, gelding exports, and horse politics."
  }
]
```

The model/router does not see Level 2-4 descriptions or bodies.

Pipeline:

```text
load files
-> split into level sections
-> gate sections by speaker lore
-> prefilter by query/subjects/triggers
-> deterministic rank or existing JSON router
-> inject selected bodies
```

No model tool call is required. If a model router is used, it should be the existing
schema-constrained card-id router style.

## 7. Router Context

The router should see only eligible section summaries:

```json
{
  "speaker": "Monteary stablehand",
  "topic": "Why are stallions guarded?",
  "eligible_lore_sections": [
    {
      "id": "monteary:0",
      "description": "Common traveler knowledge: Monteary is famous for the world's best horses."
    },
    {
      "id": "monteary:1",
      "description": "Basic local knowledge about stallion control, gelding exports, and horse politics."
    }
  ]
}
```

The selected section bodies are then injected into dialogue/book/canon context as ordinary
world knowledge.

Consumer prompt guidance:

```text
world_knowledge contains engine-approved canon. Draw on it in character. Do not recite it
verbatim. Do not treat it as a new rumor, promise, quest, or mechanical fact unless the
engine separately records one.
```

## 8. Relationship To LoreCard

The implementation preserves the current `LoreCard` behavior while changing the source of
truth.

Current path:

1. `wildmagic/file_lore_cards.py` parses `content/lore/*.md`.
2. `wildmagic/lore_cards.py` converts file sections into live `LoreCard` records.
3. `select_lore_cards(...)` remains the public selection API.

Possible in-memory shape:

```python
@dataclass(frozen=True)
class FileBackedLoreSection:
    name: str              # "monteary:1"
    topic: str             # "monteary"
    level: int             # 1
    tags: tuple[str, ...]  # ("monteary",)
    threshold: int         # 1
    triggers: tuple[str, ...]
    description: str       # router-facing summary
    text: str              # injected body
    source: Path
```

This is now adapted into `LoreCard` records at import time. The live registry records file
source, topic, and level metadata for audit/debug use.

## 9. Deep Sections And Appendices

Level sections should remain the main access unit. Do not hide L2/L3/L4 in appendices; those
are normal gated sections.

Appendices are for expensive, rarely needed depth inside an already eligible section:

```text
content/lore/stalnaz.md
content/lore/stalnaz/succession_appendix.md
content/lore/stalnaz/crystal_court_appendix.md
```

Use appendices later for:

- long regional histories
- scholarly book threads
- faction genealogies
- named-person dossiers
- deep conflict timelines

Appendices should have their own descriptions and triggers, and should only load when the
main section is eligible and tightly on-topic.

## 10. Tool Calls

Model tool calls are out of scope for the default lore plan.

Keep the normal router+cards model:

```text
eligible sections -> router sees descriptions -> router returns section ids -> engine injects bodies
```

Tool calls may be reconsidered later only if:

- there are many eligible sections,
- descriptions are too numerous for the router prompt,
- appendices become large enough that a second retrieval step is worthwhile,
- and audit data shows deterministic routing is insufficient.

Even then, tool calls would only request bodies from an engine-approved eligible set. They
would not control access.

## 12. Testing

Implemented parser tests:

- parses file-level `toml lore` block
- parses each `## Level N` section
- requires a per-level `description`
- rejects duplicate section names
- rejects malformed TOML
- parses draft sections while excluding them from the live registry by default

Implemented gate tests:

- lore 0 sees only Level 0
- lore 1 sees Level 0-1
- unrelated lore sees no nonzero sections for that topic
- inaccessible level descriptions are absent from router menus

Implemented/existing selection tests:

- deterministic prefilter uses per-section triggers
- selected sections flow through the existing `LoreCard` API
- small talk injects nothing
- book subjects select the matching topic sections

Integration tests:

- dialogue context receives selected file-backed lore
- book/canon thread receives selected file-backed lore
- promise extraction does not ingest world-knowledge text as new claims
- replay does not call a model to recover selected lore

## 13. Migration Plan

### M1: Parser And Data Model

Implemented as `wildmagic/file_lore_cards.py`.

Acceptance:

- `content/lore/monteary.md` parses into section records
- tests cover malformed files and hidden descriptions
- live behavior remains compatible with the existing selection API

### M2: Topic Files

Implemented for the current lore-card surface under `content/lore/*.md`.

Acceptance:

- current lore-card tests still pass
- new tests prove file-backed Monteary sections gate correctly
- dialogue/book behavior remains on the same public API

### M3: Live Read Path For Lore Files

Implemented. `LORE_CARDS` is file-backed from `content/lore/*.md`.

Acceptance:

- Monteary dialogue can use file-backed sections
- file source/topic/level metadata is attached to live `LoreCard` records

### M4: Complete Transfer

Implemented. Authored lore now has one source of truth: `content/lore/*.md`.

Acceptance:

- no duplicate ids
- all current lore tests pass
- live dialogue smoke test still works

### M5: Add L2-L4

Fill deeper sections in files.

Acceptance:

- high-lore NPCs can reach deeper sections
- low-lore NPCs cannot see descriptions or bodies
- router remains precise enough with larger eligible menus

## 14. Recommended Next Step

Fill real L2-L4 sections in `content/lore/*.md` as authored canon becomes available.
