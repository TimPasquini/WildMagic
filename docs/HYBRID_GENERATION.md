# Hybrid Generation — the Richness Stack and Materialized Canon

Design drafted June 2026 (Claude draft, revised per Codex feedback and designer Q&A).
Companion to `WORLD_PROMISES.md`: the Promise Ledger governs what the world will make
true *later*; this document governs how the world becomes *lush now* — books with real
pages, NPCs with faces, objects with histories, secrets worth a turn to find.

Decisions locked with the designer:

1. **Authority dial: choose-from-menu.** The engine sets slots and budgets
   (`secret_present`, reward tiers, salience caps, actual reward identity); the LLM
   chooses nonmechanical variants from allowed lists, *what* the clue looks like, *how*
   the truth is worded, and may emit lore claims within the contract. It never conjures
   mechanical facts from nothing, and it never decides whether a secret exists.
   (Spectrum rejected: narrate-only is too inert, propose-and-validate too exploitable
   for ambient content — that latitude stays reserved for spell resolution, where it
   already works under the spell contract.)
2. **Canon is per-run.** Generated content lives and dies with the run. No cross-run
   world memory in v1 (legacy echoes are a possible later experiment, out of scope here).
3. **Generated content may make world promises.** Books are not a special exception:
   any canonical text or description can describe the world and may emit promise
   candidates through the same lore pipeline as dialogue. The Promise Ledger remains the
   yes-and gate: extracted claims can be uncertain, mistaken, contested, relocated, or
   low-salience, but if the engine binds one, the world should honor it.
4. **Background materialization can become canon unseen.** Content may be generated and
   canonical before the player reads it. This lets the world exist ahead of observation
   and lets later interactions retrieve already-painted facts instead of waiting for
   first contact.
5. **On-demand latency: block briefly.** When the player reads/investigates something
   not yet materialized, the urgent (GPU) channel generates it while the UI waits, same
   as spell resolution today. Background prewarming should make this rare.
6. **Technical failures do not consume turns.** Reading, investigating, or examining
   only costs time when the game produces a valid diegetic result. Provider failures,
   invalid JSON, or contract failures surface through grammar fallback/provisional text
   when possible, but they do not advance the turn counter.
7. **Investigation can always cost a turn.** Time passes even in apparently safe places;
   towns, libraries, camps, and dungeons all use the same inspect/search rhythm. The
   cost matters most under danger, but the rule stays uniform.
8. **Engine chooses mechanical rewards.** For secrets and discoveries, the engine chooses
   the actual reward from rich procedural tables and budgets. The LLM may describe,
   contextualize, and name it, but delivery remains deterministic and guaranteed.
9. **Density: saturate.** The background channel works continuously toward full
   materialization of the current map — every named prop's deep description, notable
   books, room flavor — ordered by salience and player proximity. (NPC appearances are
   not queue work: they ride along in the same call that creates the NPC.) Lushness is
   the point; the grammar layer exists so nothing is ever *blank*, not as the
   destination tier.

Implementation note (June 2026): the first foundation slice is live. `RoomProfile`
semantic labels now exist in engine state and are emitted for dungeon rooms, Hollowmere
buildings, generated town buildings, frontier structures, and realized promise sites;
labels feed `context_for_llm`, headless inspect, UI tooltips, and prop theming. The
`CanonRecord` data model and retrieval hooks also exist. Realized promise sites now write
site, keeper, and flesh-prop canon records, so future materialization prompts can
retrieve the story a place was built to honor. The first on-demand materializer is also
live: `examine` creates a canonical `room_flavor` record for the current labeled room,
audits the provider call, records replay apply points, costs a turn only on valid new
materialization, and reuses the same record thereafter. On-demand canon has its own
config purpose (`canon`) routed URGENT — the GPU-resident main model — per decision 5;
the lore/flesh extraction channel stays BACKGROUND/CPU.

## The principle

> **The engine decides what is true, possible, costly, hidden, reachable, and fair.
> The LLM decides how those truths become language, mood, specificity, and surprise.**

The boundary is **not** "procgen makes titles, LLM makes pages." Content type does not
determine the generator; *what's at stake* does. A book title is a superb LLM artifact —
*The Thirty-Seven Lawful Names of Rain* carries more world than a paragraph of template
prose — precisely because a title has no mechanical stakes. What the engine must own is
everything with stakes: whether the book contains a real clue, what the clue unlocks,
what investigating costs, what the reward budget is.

Corollary, and the practical heart of this doc: **richness is a context-assembly
problem.** A 9B model prompted "write a book" produces mush. The same model prompted
with region, room history, shelf topic, faction pressure, two active promises, and an
explicit list of allowed/forbidden outputs produces something that feels authored. Most
of the engineering below is about building and delivering those seed packets.

## The Richness Stack

Five layers. Each layer's output is the next layer's input; only layers 0 and 4 may
write mechanical truth.

**Layer 0 — Deterministic skeleton** (instant, seeded, replay-safe).
Map topology, factions, danger/loot budgets, secret slots, and — the highest-leverage
addition — **semantic labels on rooms and zones**. A room is not a rect; it is
`type=scriptorium, era=pre_charter, condition=ransacked, topics=[forbidden_saints, old_maps],
secret_slots=[hidden_compartment], promise_hooks=[chapel-north]`. Every richer layer
eats these labels. Lives in `generation.py`; extends the existing region-style pattern
down to room scale.

**Layer 1 — Procedural texture** (instant). Grammars and tables for bulk and
distribution control: shelf categories, clutter, material variation, name fragments,
trait tables. This layer guarantees scale (a 40-book library never waits on a model),
guarantees floors (nothing is ever undescribed), and — critically — *manufactures the
seed vocabulary* layer 3 consumes. We decide that 10% of books in imperial rooms are
tax law and 2% are heresy; the LLM decides what the heresy says.

**Layer 2 — Salience assignment** (instant). The skeleton marks which entities are
*notable*: anchors the spell system already sorts, props in labeled rooms, all NPCs,
secret-adjacent objects, promise-linked objects. Saturation order = salience x proximity.

**Layer 3 — LLM materialization** (background CPU channel, continuously; urgent GPU
channel on demand). Seed packet in → prose + nonmechanical choices out → **canon record
stored forever**. Materialization collapses the wavefunction, whether background or
on-demand; the same book never becomes a different book. Same provider/audit/fallback
pattern as `flesh.py`.

**Layer 4 — Mechanical feedback** (validated). Materialized text feeds back into the
game only through existing validated systems: the lore extractor (`lore.py`) harvests
claims from books, inscriptions, object details, investigation results, and other canon
records exactly as it does from dialogue; engine-owned slots resolve discoveries and
rewards; everything else is voice, not law. The LLM suggests; the engine accepts,
clamps, converts, relocates, contests, or drops.

## Materialized Canon — the content layer

One new store in game state, sibling to the Promise Ledger:

```python
@dataclass
class CanonRecord:
    id: str
    kind: str                  # book | npc_appearance | object_detail | inscription |
                               # investigation | room_flavor
    attached_to: str           # entity id | "tile:x,y" | promise id
    title: str | None          # books/inscriptions: the LLM-authored name
    text: str                  # the canonical prose (pages, description, findings)
    summary: str | None        # one line, for tooltips and context retrieval
    tags: list[str]
    # provenance & lifecycle
    source: str                # "background" | "ondemand" | "grammar_fallback"
    seed_packet: dict          # the exact context it was generated from (audit + regen)
    claims_emitted: list[str]  # promise ids harvested from this text by lore.py
    engine_choices: dict       # mechanical slot resolutions chosen before prompting
    llm_choices: dict          # nonmechanical menu choices: tone, clue shape, wording
    turn_created: int
    status: str                # provisional | canonical
```

Rules:

- **Write-once.** A record that reaches `canonical` is never regenerated. `provisional`
  exists only for grammar fallbacks awaiting upgrade after a technical failure or
  background miss. Once the player has fully observed a result, it should be promoted or
  preserved as canonical rather than rewritten later.
- **Retrieval, not dumping.** Every seed packet includes the 3-6 most relevant canon
  records (by attachment, tags, and shared promise hooks) — never the whole store. This
  is what makes the world cross-reference itself without blowing the 16k context.
- **Serialized and replayed.** Canon is part of save state; LLM materializations are
  recorded as replay events like spell resolutions.
- **Relationship to promises:** promises are commitments about the unobserved future;
  canon is the record of the materialized present, whether the player has read it yet or
  not. A promise that realizes gets flesh
  (existing system); flesh becomes canon records attached to the realized entities.
  Canon can also produce new promise candidates through `lore.py`. The Promise Ledger is
  still the only authority for future commitments, so this is a loop through one gate,
  not a second source of truth.

## Seed packets — what the LLM actually sees

The designer's instinct that "we need to think carefully about what information to pass"
is the core discipline. Every materialization prompt is a structured packet with five
blocks, assembled by one shared function (no per-feature ad hoc prompting):

```text
WORLD    region style, era strata, current zone, faction pressure
PLACE    room semantic label, condition, neighboring notable props
SUBJECT  the entity: template, tags, material, role, mechanical truths (wounds,
         statuses, inventory tells), grammar-layer fragments already shown to player
THREADS  2-4 retrieved canon records + 1-2 active promises sharing tags/hooks
CONTRACT allowed outputs (fields + nonmechanical menus + claim quota), forbidden outputs
```

The CONTRACT block is per-kind and explicit, e.g. for a readable book:

```text
Allowed: title, author, 2 excerpt pages, 0-2 lore claims with bounded salience
Choose one shelf-topic from: [illegal devotional texts, river-spirit petitions]
Forbidden: treasure locations, guaranteed allies, map exits, named player rewards
```

Claims that exceed quota or salience caps are dropped by the normalizer, not negotiated.
This applies to every materialized content kind, not only books. Prompts request,
parsers enforce, and the Promise Ledger decides whether a claim is bindable, flavor,
contested, or deferred.

## The saturation engine

A background materialization queue, riding the BACKGROUND route (CPU-resident model —
the dual-instance Ollama setup exists for exactly this):

- **Priority = salience x proximity x imminence.** NPCs on screen first; props in the
  player's room next; the rest of the map in descending salience. Entering a labeled
  room bumps its contents; starting dialogue bumps that NPC's threads.
- **One in flight, always.** The queue keeps the CPU instance busy whenever it isn't
  doing lore extraction or flesh; those existing jobs share the queue with higher
  priority (they are load-bearing narration; texture is not).
- **Budgeted per map, not capped.** Saturation is the goal, but the queue never blocks
  gameplay and never preempts the urgent channel. On a slow map the player simply
  outruns the painter — and on-demand blocking (1-5s GPU) covers anything they touch
  before the painter reaches it.
- **Misses are diegetic.** If an on-demand generation fails (provider down), the grammar
  fallback renders *as the fiction*: "the ink has bled too badly to read more" — and the
  record stays `provisional` for later upgrade if it has not been fully observed. Nothing
  surfaces as an error, and no turn is consumed for the technical failure.

## Case studies

Current playable slice: an **always-on book pipeline** (top priority) works strictly
nearest-first — for each book, closest to farthest, it materializes the `book_title`
(cheap, whole zone, ignoring visibility/distance, so the shelf is readable on sight) and
then, for nearby visible books, the full `book` pages under the canonical book id so
`read` reuses them with no wait. This whole pipeline runs independent of the saturation
flag, because legible, readable books are core UX, not opt-in richness. The flag-gated
saturation queue runs *behind* it, painting current-room `room_flavor` and far-look
`object_detail`/`npc_detail`/`creature_detail`. The title call carries a deliberately tiny
seed packet (subjects + the catalog axes that shape a title, no world/place/threads
block); titles have no mechanical stakes, so minimal context keeps the call cheap. The
queue advances both on player turns and on UI idle frames (`pump_canon_prewarm`), and at
the default depth of 2 keeps one job running and one queued — re-picked by proximity each
time a slot frees — so it keeps readying the nearest books while the player stands still.
Background detail records use `claim_quota=0` and `turn_cost=0` and never create
close-study records, so adjacent investigation can still reveal engine-owned secret clues.
(Books are the exception that does carry pages: the prewarmed `book` record is the same
record `read` would have made on demand — claims are still harvested at the player's first
read, not in the background.)

### Books (the model case)

Four tiers, assigned by layer 2 — note the tiers are about *stakes*, not about which
generator writes titles:

| Tier | Decided by | Title | Pages | Mechanical content |
|---|---|---|---|---|
| Ambient shelf | grammar | category only ("damp imperial ledgers") | none | none |
| Notable book | LLM | LLM title (always-on background, title-only, whole zone) | on read or saturation prewarm | 0-1 low-salience claims |
| Readable book | LLM | LLM | 2 excerpt pages from full seed packet, background or on-demand | claims quota |
| Keystone book | engine places it | LLM | LLM | engine-owned clue/promise hook, LLM-worded |

Reading, prewarming, or otherwise canonicalizing pages runs them through `lore.py`
extraction (claim quota enforced), so books are read/write citizens of the Promise
Ledger: the same chapel a rumor promised can surface in a devotional text's marginalia
— written *because* the promise was in THREADS — and a book may itself become the first
source of a future promise. The same rule applies to inscriptions, notices, object
details, and other canon records.

### NPC appearance — *implemented June 2026*

**No separate call.** NPCs are already born in LLM calls — town generation
(`TOWN_SYSTEM_PROMPT`) and promise flesh (`FLESH_SYSTEM_PROMPT` keepers) — so appearance
is simply one more field in those responses (`appearance` on the npc shape,
`keeper_appearance` in flesh). The generating model already holds the role, backstory,
traits, wares, and promise hooks in context at that moment, which is exactly the seed
packet appearance needs; a follow-up call would re-send all of it to learn nothing new.

The description should still be evidence, not wallpaper: marks of trade and history that
quietly agree with role, backstory, and wares (the prompt says so explicitly). It is
fixed at creation — not updated for wounds or statuses; those already surface
mechanically in the tooltip. Stored on `NPCProfile.appearance`, shown in the inspect
tooltip, and included in dialogue context so NPCs know what the player sees.
Hand-authored NPCs (the Hollowmere four, mock providers) carry hand-written appearances.

### Investigate

The dangerous one — an unguarded version is an infinite loot button. Contract:

- Layer 0 places **secret slots** at generation:
  `secret_present, secret_kind, reveal_difficulty, possible_rewards=[...], clue_style`.
- Investigate costs **turns** (1-3, danger clock running): richness becomes a tactical
  decision — study the altar, or move before something finds you. The rule is uniform:
  towns and apparently safe rooms still advance time.
- The LLM receives the slot truth (usually `secret_present=false`) and writes the
  deepened description; when a secret is near it words the **clue** and describes the
  engine-chosen reward. It never invents presence, kind, tier, or reward identity.
- Technical generation failures do not consume the turn. A valid fallback finding may
  consume the turn; an invalid or unavailable model response does not.
- Discoveries are **knowledge-gated, not RNG-gated** where possible: investigation
  yields a clue ("drag-scuffs arc away from the bookshelf"); the clue names an *action*
  (push the bookshelf); the action opens the passage. Clue-chains make discovery feel
  earned, and clues can leak through other channels (a book mentions the false
  hearthstone; the keeper's knees are dusty) because they are canon records with tags.
- Investigation findings are canon: investigating the same altar twice retells, never
  rerolls.

### Inscriptions, props, aftermath

Everything else is the same machine with different CONTRACT blocks: gravestones get
1-line epitaphs + optional claim; the spell system's conjured objects get object_detail
records on first inspect (the glass teeth the goblin spat out deserve a history);
`room_flavor` records give labeled rooms one sensory paragraph for first entry.

## Convergence — why this is the richest version

Richness is not volume of prose; it is the world **agreeing with itself**. A book
mentions the Chapel of Quiet Hours; an NPC wears its medal; investigating a shrine finds
candle wax from the same order; the north road on a notice is scratched out; and when
you walk north, the chapel — promise-bound long ago — is *there*. Five windows onto one
underlying truth. The THREADS block is the entire mechanism: every materialization reads
a few shared facts, so cross-reference is the default, not a scripted event. Books,
NPCs, props, rumors, inscriptions, dreams, and spell aftermath are different renderers
pointed at the same scene graph.

Make richness playable, always: every lush description should have a *chance* to matter
through tags, clues, claims, promises, or memory. The goal is a world that remembers
what it said.

## Determinism, audits, fallbacks

- Seed packets are deterministic given (run seed, entity, turn); raw responses are
  audited to `canon_audit.jsonl` like `flesh_audit.jsonl`.
- Replay records every materialization apply point; replays render identical canon even
  when background jobs complete at different wall-clock times.
- Mock providers for every kind (test suite never needs Ollama); grammar layer is the
  universal runtime fallback.
- Eval harness per kind, following `lore_eval.py`/`dialogue_eval.py`: golden seed
  packets, scored for contract compliance (no forbidden outputs, claim quotas,
  engine-choice adherence) before scoring for style.

## Milestones

Each milestone is playable and ends with its exit gate; per `WORLD_PROMISES.md` stance,
no compatibility shims with dual authority.

- **R1 — Room semantic labels.** *Initial implementation live:* `generation.py` emits typed room labels with topics,
  era, condition, secret_slots. Exit: labels visible in debug inspect; spell_anchors and
  region prompts consume them.
- **R2 — Canon store.** *First writers live:* `CanonRecord`, retrieval-by-tags, replay-summary
  visibility, realized-promise canon writing, on-demand room-flavor materialization,
  materialization apply-point replay, and `canon_audit.jsonl` exist. Remaining exit
  check: flesh and `examine` canon both survive save/replay in longer mixed-provider
  playtests.
- **R3 — Books end-to-end.** *First playable slice live (June 2026):* grammar-tier book
  props (`texture.py`) place deterministically in rooms labeled with books/lore/paper
  across dungeon, open-zone, LLM-town, and Hollowmere generation. The grammar tier now
  keeps a hidden shelf card with genre, discipline, author role, audience, purpose,
  stance, institution, title shape, taboo level, and secondary topic, so the LLM sees
  more than a narrow room topic like "old maps"; `read` materializes
  title, author, and excerpt pages on the urgent channel through the shared canon layer
  (turn cost on first valid read, free reread, failure costs nothing, replay-safe); the
  materialized title becomes the book's in-world name; pages run through `lore.py`
  extraction with the CONTRACT claim quota (2) clamped at the drain, and claims carry
  `source="book:<title>"`. Live-verified on GPU (June 2026): a fresh-run book wove an
  active chapel promise from THREADS into its excerpt as the author's hearsay; reads
  run ~10s on the urgent channel at canon temperature 0.85 (titles/authors vary across
  packets). Background canon now runs in two tiers. **The book pipeline is always-on**
  (`WILDMAGIC_BOOK_TITLES=1`, default; top priority) and strictly nearest-first: for each
  book, closest to farthest, a cheap title-only `book_title` call (tiny subjects+axes
  packet) names it — whole zone, so every shelf is readable and the disliked grammar
  placeholder description is gone — and then, for nearby visible books, the full `book`
  pages prewarm under the canonical book id, so `read` opens instantly. The broader
  **saturation** tier stays opt-in (`WILDMAGIC_CANON_PREWARM_ENABLED=1`) and runs *behind*
  the book pipeline: current-room `room_flavor` and far-look entity detail. `read` reuses
  prewarmed pages instantly when present (the first read still costs a turn and harvests
  its lore claims, tracked per-book via `details["read"]` so rereads stay free) and falls
  back to urgent-channel materialization when the painter hasn't reached the book yet. The
  queue advances on player turns and on UI idle frames (`pump_canon_prewarm`) so it keeps
  working while the player stands still; the default depth of 2 keeps one job running and
  one queued, re-picked by proximity as a slot frees. Subjects (1-4 per book) are durable
  metadata seeding the title call and the planned lore-card router. Remaining for R3/R6:
  the lore-card content router, plus inscription/notices, richer NPC-detail
  prioritization, and clue-leak jobs.
- **R4 — NPC appearance.** *Done (pulled forward — it needed no canon store):*
  appearance fields ride the existing towngen and flesh calls; tooltip + dialogue
  integration. Remaining exit check: a promise-bound keeper's generated appearance
  references their promise in live play.
- **R5 — Investigate.** *Live (June 2026):* `investigate`/`search` runs the
  knowledge-gated loop — a sweep costs 1–3 turns (by `reveal_difficulty`, danger clock
  running) and materializes either an honest no-secret deepening
  (`secret_present=false`) or a clue worded by the LLM in the slot's `clue_style`
  pointing at an engine-chosen anchor prop; `investigate <anchor>` then opens the
  secret deterministically (no provider call) and delivers the engine-chosen reward
  from tag-keyed tables (`secrets.py`). Clue and finding are canon (retell, never
  reroll); failures cost no turn; full sequences replay. Secret slots no longer leak
  into any LLM context (`to_public_dict` excludes them by default). Exit gates pass:
  hidden compartment found via clue→anchor in live play, and the no-secret test
  spam-investigates every prop without yielding loot. Remaining polish: richer reward
  tables, multi-slot rooms, and clue leakage through books/NPC descriptions (THREADS
  already supports it once clue canon carries shared tags).

  *Extended (June 2026) — targeted investigation:* `investigate <entity>` (or the
  inspect-tooltip's Investigate button, which sends the entity id) materializes
  `object_detail`/`npc_detail`/`creature_detail` canon in two distance tiers —
  far (silhouette and bearing) and close (texture and fine marks), close superseding
  far. Creatures yield one engine-chosen weakness per study (`secrets.py`
  `choose_weakness_hint`): a real mechanical weakness when one exists (the prose IS
  the stat) or a tag-derived flavor weakness a wild spell can exploit. Adjacent study
  of the prop anchoring a hidden slot surfaces the clue without a sweep. NPC details
  may emit one lore claim (`source="observation:<name>"`). Secretless sweeps can
  develop the room: the engine offers a menu of fitting non-blocking props plus a
  validated tile (passed via `engine_private`, stripped from the prompt so
  coordinates never leak into prose), and the LLM may surface one as a discovered
  decoration — which then exists on the map as a spell anchor. Canon prose now reaches
  the message log, so all of this is visible in the pygame UI, and the tooltip shows
  learned detail summaries with Read/Investigate buttons per entity.
- **R6 — Saturation queue.** Priority queue on the background channel unifying flesh,
  lore, and texture jobs. Exit: on a fresh map, all NPCs and top-salience props
  materialize within N turns of play without urgent-channel contention.
