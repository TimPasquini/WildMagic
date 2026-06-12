# The Promise Ledger — unifying rumors, quests, and emergent world creation

Design drafted June 2026; revised per `WORLD_PROMISES_NOTES_TO_CLAUDE.md` (Codex/user
feedback). Decisions locked with the designer:

Implementation note: M1-M4 are now partially implemented. The live code has a unified
`WorldPromise` ledger, deterministic binding/reservations, `promise_hooks` for reserved
town context, and non-town open-zone realization through generic site archetypes:
`sacred_site`, `inhabited_site`, `hostile_site`, `memorial_site`, `hidden_site`,
`creature_site`, and `authority_site`. The "chapel north of town" case is now a fixture
over `sacred_site`, not a bespoke chapel handler. Quest logs are also promise-backed:
UI/CLI render `kind=quest` promises, NPC requests create typed fetch-objective promises,
and quest items realize at reserved sites. Full replay event recording remains future work.

1. **Truth economy: always honor (pure yes-and).** Every promise the world makes, it keeps.
   The quality gate therefore moves *upstream*: not everything an NPC says becomes a
   promise — but everything that becomes a promise comes true.
2. **Shape: one `WorldPromise` entity with kinds.** It absorbs `LoreClaim`, `Quest`, and
   town lore-hooks into a single ledger with one lifecycle.
3. **Planner: deterministic skeleton + LLM flesh.** Rules decide what binds where and what
   gets built (seeded, replay-safe); the background CPU model optionally fleshes names and
   backstories via the existing pregeneration pattern.
4. **v1 realization ceiling:** structures in any zone, promise-bound NPCs, quest
   objectives/rewards. (Conditional appearances — "only at midnight" — deferred.)
5. **Migration stance: aggressively delete legacy systems.** The Promise Ledger must not
   become a fourth layer beside lore, quests, town hooks, and quest items — it absorbs
   them, and every milestone below ends with named legacy writers *deleted*. Compatibility
   shims are short-lived parsers with explicit removal gates, never long-term dual
   authority. `state.lore_claims` and `state.promises` must never both be authoritative.

## The concept

A **promise** is content the world has committed to narratively but not yet delivered
mechanically. Rumors, quests, and town generation hooks are all the same thing at
different angles:

| Today | As a promise |
|---|---|
| Lore claim "a chapel stands north of town" | kind=place, bound to a zone, realizes as a structure |
| Quest "bring me grave salt from the ruins" | kind=quest, with objective+reward, whose target realizes through the same pipeline |
| Town lore_hook redeemed at generation | a promise reserved against a town zone |
| NPC says "I'll meet you at the chapel" | kind=rendezvous, binding an NPC commitment to a future place |
| (future) prophecy spell, clerk memo "a second squadron is coming" | promises from wild magic and the heat system |

One ledger, one lifecycle, one spatial-binding model, one realization pipeline.
**The flagship test case:** an NPC says "there is a chapel north of town" → the next
unexplored zone to the north — town or not — generates with a chapel in it, and its keeper
has heard the same story.

## The entity

```python
@dataclass
class WorldPromise:
    id: str
    kind: str                  # rumor | background | place | person | threat | quest | prophecy | rendezvous
    subject: str
    text: str                  # one canonical sentence
    tags: list[str]
    # provenance
    source: str                # "dialogue:Old Maren" | "quest:Quill" | "towngen" | "wild_magic"
    source_turn: int
    origin_zone: tuple[int, int] | None
    salience: int              # 1-5
    confidence: float          # 0-1
    # what was SAID vs what the engine CHOSE — kept separate so relocation
    # ("the chapel is further north than they said") is recorded and narratable,
    # never a silent rewrite of the NPC's claim.
    claimed_space: SpatialHint | None
    bound_space: SpatialHint | None
    # binding — None means pure flavor lore: feeds dialogue, never realizes
    binding: PromiseBinding | None
    # quest block (kind == "quest" only) — typed from day one, not loose dicts
    objective: Objective | None
    reward: Reward | None
    giver_npc: str | None
    # lifecycle
    status: str                # pending | bound | realized | fulfilled | contested | expired
    realized_in: str | None    # "Chapel of Quiet Hours, zone (0,-1)"

@dataclass
class PromiseBinding:
    blueprint: str             # realization template id (see blueprint table)
    npc_seed: dict | None      # name/role/backstory seed for a promise-bound NPC
    capacity_cost: int = 1

# Engine-owned objective/reward schemas (kind == "quest"). Strict from M1 —
# untyped dicts here would calcify into a fifth informal schema.
#   FetchObjective(item_id, quantity, target_zone)
#   KillObjective(entity_template, quantity, target_zone)
#   VisitObjective(site_blueprint, target_zone)
#   TalkObjective(npc_seed, target_zone)
#   Reward(gold, items, reputation, flags)

@dataclass(frozen=True)
class SpatialHint:
    mode: str                          # "zone" | "direction" | "terrain" | "wildcard"
    zone: tuple[int, int] | None       # mode == "zone": an exact zone
    direction: tuple[int, int] | None  # mode == "direction": unit vector...
    anchor_zone: tuple[int, int] | None    # ...measured from this zone
    terrain_tag: str | None            # mode == "terrain": "forest", "river", "hills"
    raw_text: str                      # what was actually said, verbatim
```

**v1 spatial vocabulary (a lookup table, not an NLP project):** the eight compass
directions ("north of", "east of", ...), "near/at <known place name>" for zones the
ledger has already seen named, and nothing-said → wildcard. Anything fancier ("beyond
the second river", "three days' ride") parses to wildcard in v1 — the claim still binds
and realizes *somewhere* plausible, which always-honor permits. Depth-binding ("below",
"deeper") is **deferred**: v1 binds overworld zones only; the dungeon variant is cheap
and symmetric (reserve against the next ungenerated depth) when wanted.

## The pipeline (six stages)

### 1. Capture — many producers, one ledger
- **Dialogue extraction** (exists — `lore.py`). The extraction prompt gains two optional
  fields: `where` (direction words, place names, distances as said) and `what` (the
  concrete thing claimed to exist). Extraction quality is the yes-and front door.
- **Quest assignment** (`npc_quests.py` migrates): generating a quest *is* appending a
  kind=quest promise.
- **Town generation**: hooks become reservations (below) instead of a parallel mechanism.
- **NPC commitments:** dialogue/trade can create rendezvous promises ("I'll meet you at
  the chapel", "come find me at the north shrine", "I'll wait by the old oak"). The
  promise system owns the future appointment; companion/follower behavior itself remains
  an adjacent engine system (see below).
- Future producers: wild-magic prophecy effects ("somewhere, a sword is waiting" — a spell
  that creates a promise is pure wild magic), clerk notices and posted fliers (the
  Censorate promising Investigator Kipler, a hearing date, or a second squadron is a
  threat-promise the heat system keeps), region events.

### 2. Binding — the deterministic quality gate
A rules pass (no LLM) converts claim text/tags into a `PromiseBinding`, or declines.
Binding runs at the drain turn boundary (main thread, seeded) so it is deterministic
given the ledger and replay-reproducible:

- **Spatial resolver:** "north of town" + utterance zone → `(zx, zy-1)` if unexplored;
  known place names match existing zones; bare claims get a region/terrain wildcard
  ("somewhere in the frontier"). The resolver writes `claimed_space` (what was said)
  and `bound_space` (what the engine chose) separately. If the named zone is already
  explored, bind to the nearest unexplored zone in that direction — *the chapel is
  further north than they said* — with the relocation visible in the record, not a
  silent rewrite. Always-honor never breaks; it relocates.
- **Blueprint matcher:** tags → generic realization archetype, not a bespoke site class.
  chapel/shrine/temple/altar/reliquary → `sacred_site`; witch/hermit/sage/healer →
  `inhabited_site`; bandits/camp → `hostile_site`; grave/barrow/tomb →
  `memorial_site`; cache/treasure/stash → `hidden_site`; beast/creature →
  `creature_site`; investigator/bounty/warrant/hearing/flier/checkpoint →
  `authority_site`.
  **No blueprint match → no binding** — the claim stays flavor lore (still feeds
  dialogue, never realizes). This is how always-honor stays sane: we always honor what we
  can build; what we can't build remains talk.
- **Floors:** confidence < ~0.4 or salience 1 stays flavor. Player-asserted claims are
  never captured (existing guard) — until the deliberate "spread false rumors" mechanic
  arrives, at which point player assertions become a *tool*, not a leak.
- **Corroboration, not duplication:** before binding, merge by subject+tag overlap and bump
  salience (this also fixes the current lore ledger's duplicate problem).
- **Contradiction rule:** if the subject already realized differently, mark `contested`
  instead of binding.

### 3. Reservation
Bound promises reserve their target zone in a `zone → promise queue` map. Capacity: **max
2 realizations per zone**; overflow spills to the next candidate zone in the same
direction (again: "further than they said"). Global cap on bound-pending promises (~30),
oldest spill first. **Exception: kind=quest reservations are never the ones spilled** —
they count against capacity but always win the squeeze, because a quest objective that
spills forever is an impossible quest, and always-honor binds the world hardest to the
promises it made the player a personal party to. Town pregeneration contexts read from reservations — replacing the
current ad-hoc `lore_hooks` injection and the too-aggressive pregen invalidation (only a
reservation against a *pending* town invalidates that town's pregen, nothing else).

### 4. Realization — deterministic skeleton
**The blueprint contract** — a blueprint template defines, and only defines: footprint
(w×h), structure style (which `_build_*_structure` shell), a prop scene list, an optional
NPC slot (role + tag seeds for the `NPCProfile`), an optional loot/objective slot, and an
arrival-line template. Realization is filling those slots from the promise; anything a
blueprint doesn't declare, realization doesn't improvise.

When a reserved zone generates (any zone type, not just towns):
- The blueprint instantiates from templates: structure shell (reusing
  `_build_common_structure` machinery), themed props (prop scenes selected by promise
  tags), a **promise-bound NPC** whose `NPCProfile` is seeded from the promise (their
  backstory references the rumor; `relevant_lore` makes them aware of it — so the witch
  you heard about can confirm, deny, or complicate the story that created her).
- Quest objectives realize here too: the fetchable item, the killable threat — meaning
  quests can finally point at *other zones* with the world guaranteed to deliver.
- Promise marked `realized`, with an arrival beat in the log ("The story was true: ...").

### 5. Flesh — optional LLM pass, never load-bearing
For reserved zones, the background CPU model pre-drafts flavor via the existing pregen
executor: a name ("the Chapel of Quiet Hours"), the keeper's backstory, two prop
descriptions. If the flesh isn't ready when the zone generates, the skeleton ships with
template flavor — correctness never waits on the model. Flesh results are recorded into
action records for replay (same aliasing pattern as recorded lore).

### 6. Settlement — the loop closes
Realization writes back to the ledger: realized promises become canon (`verified` lore fed
to dialogue everywhere), quest completion marks `fulfilled`, visiting a realized site can
bump the source NPC's credibility. Every stage publishes events — when the unified event
bus (Phase 16) lands, reputation and folklore subscribe here.

## High-value promise producers

These are not all v1 requirements, but they are high-payoff uses of the same ledger.

### Rendezvous and NPC commitments

The promise system should support NPCs committing to future meetings:

- "I'll meet you at the chapel."
- "Find me at the old oak after you have seen the shrine."
- "If things turn ugly, I'll wait by the north road."

This is a natural `kind=rendezvous` promise. It binds an NPC commitment to a future place,
usually one already created by another promise or a known zone. If the destination has not
generated yet, the rendezvous reserves enough content to make the meeting possible.

Important boundary: the Promise Ledger should not own all companion behavior. If an NPC agrees
to accompany the player right now, that is a companion/follower engine system: faction, AI,
pathing, wages, morale, dismissal, death, inventory/trade rules. A promise can create or schedule
that state ("meet me there", "I'll join you once we reach the chapel"), but the moment-to-moment
companion behavior should live in a dedicated companion layer, not inside promise binding.

No duplicates: if the rendezvous involves an existing NPC, realization must move or reference
that NPC rather than creating a second copy. If the origin zone is saved, the NPC can be marked
`departed_for=<promise_id>` there and restored at the destination.

### Imperial paperwork as threat-promises

Imperial documents should often be promises, not mere flavor. A posted flier that says:

> "Information regarding the unlicensed sorcerer should be directed to Investigator Kipler."

means the world has introduced Investigator Kipler as a likely future actor. The binding might
reserve:

- a named investigator NPC
- a checkpoint
- a patrol asking about the player
- a hearing notice or warrant office
- an informant who has already sent word ahead

This fits the Grand Empire's tone: bureaucracy does not simply threaten; it schedules future
events. Fliers, clerk notices, warrants, and formal letters become `kind=threat` promises that
the heat system can prioritize and escalate.

### Prophecy magic

Wild magic can mint promises directly once M2 exists. Examples:

- "Somewhere north, a sword is waiting for me."
- "The next chapel I find will know my name."
- "A door I have not seen yet opens for blood."

This should eventually be a normal spell effect:

```json
{"type": "create_promise", "kind": "prophecy", "subject": "waiting sword", "where": "north", "tags": ["weapon", "fated"]}
```

The effect is powerful because it writes obligations into the world. Costs should scale with
binding strength: vague color promises are cheap; guaranteed items, allies, or threats are major
magic.

## Deletion map (what gets absorbed, then removed)

Survivors change roles; nothing keeps parallel authority:

- `wildmagic/lore.py` **survives as the capture provider** — extraction, audit, eval —
  but emits `WorldPromise` (or a thin extraction DTO that immediately binds), never a
  persistent `LoreClaim`.
- `npc_quests.py` **survives as a quest-promise producer**; its independent spawning path
  does not.

Deleted, by milestone (gates below): `LoreClaim`, `GameState.lore_claims`,
`add_lore_claims` / `lore_claims_for_context` / `mark_lore_redeemed`, town `lore_hooks`,
raw-claim pregen invalidation, persistent `Quest` + `GameState.quests`, and
`maybe_spawn_quest_item` as an independent zone-entry side effect.

New module: `wildmagic/promises.py` (entity, typed objective/reward schemas, spatial
resolver, blueprint table, reservation store). Realization lives in `generation.py`.

## Build order — every milestone ends with legacy writers deleted

- **M1 — Ledger replaces lore storage.**
  *Add:* `promises.py` (`WorldPromise`, `PromiseBinding`, strict kind/status constants,
  typed objective/reward schemas), `GameState.promises`, corroboration dedupe, caps.
  Old replay files are **deleted, not migrated** — no compatibility parser; bump the
  replay format version so any stale file fails fast with a clear message, and record
  fresh goldens.
  *Change:* lore extraction emits promises; dialogue and town contexts read promises.
  *Delete by end of M1:* `LoreClaim`, `GameState.lore_claims`, the lore-specific
  add/context/redeem engine methods.

- **M2 — Binding + reservations replace town hooks.**
  *Add:* spatial resolver, blueprint matcher, reservation store, the replay contract
  (below), and **golden binding tests from day one** (the eval starts here, graduates in
  M6). Extraction prompt gains `where`/`what` fields.
  *Change:* binding runs at the drain turn boundary; town generation and open-zone
  generation read the same reservations; pregen invalidation becomes
  reservation-targeted (only a reservation against a pending town invalidates it).
  *Delete by end of M2:* `lore_hooks`, `mark_lore_redeemed`, raw-addition pregen
  invalidation.

- **M3 — First realization archetype (`sacred_site`).**
  Flagship case end-to-end, **in a non-town open zone first**: "chapel north of town"
  binds north, the next unexplored northern zone realizes a sacred-site structure flavored
  by the promise text/tags, the keeper NPC knows the originating promise, status becomes
  `realized`. Town realization follows naturally since reservations are already shared.
  This is also the first foundation for rendezvous promises, because "I'll meet you at
  the chapel" can bind to the same realized site once existing-NPC movement/proxy rules
  are in place.

- **M4 — Quest migration.**
  *Add:* quest log as a view over kind=quest promises (UI/CLI may keep saying
  "Quest Log" — it renders promises).
  *Change:* NPC quest generation appends promises; objectives reserve and realize
  through the same pipeline (cross-zone objectives now guaranteed).
  *Delete by end of M4:* persistent `Quest`, `GameState.quests`, independent quest-item
  spawning, `maybe_spawn_quest_item` as a separate generation path.
  *Implemented note:* the persistent `Quest` dataclass and `GameState.quests` are gone;
  `QuestLogEntry` is a view over promises, and fetch quest items spawn when the reserved
  quest site realizes.

- **M5 — Optional flesh.**
  Background-model flavor for reserved/realized promises + replay recording.
  *Constraint:* flesh never determines whether a promise exists or where it binds; the
  deterministic skeleton is complete without model output.

- **M6 — `promise_eval` graduates.**
  Required cases: chapel north of town; bandit camp east; witch in the woods;
  grave/barrow/tomb; cache/stash; an unbuildable poetic claim staying flavor; an
  already-explored target relocating with distinct `claimed_space` vs `bound_space`;
  a full replay using recorded promises/bindings/realizations with zero model calls.

## Next pieces (post-M4 plan, June 2026)

M1–M4 are live (unified ledger, deterministic binding/reservations, archetype-site
realization in any zone, promise-backed quests) and the deletion gates held — a grep for
every legacy name comes back empty. Remaining work, in order:

1. **Finish the replay contract — before any new producers.** ✅ Done (June 2026):
   replay format v3 records promise *apply points* per action; replays inject them with
   zero model calls; binding/reservation/realization re-derive deterministically. See
   the Replay contract section below and `tests/test_replay_promises.py`.
2. **Live-model shakedown of capture → binding.** ✅ First round done (June 2026), and it
   paid for itself. 32 live dialogue rows through qwen3.5:9b: 82 promises, 0 technical
   failures, 0 compass-direction `where`s (8 good place-flavored ones fell to wildcard,
   as designed) — but 17 bindings of which most were FALSE: substring keyword matching
   ("camp" ⊂ "Campaign Map", "sage" ⊂ "passage") and full-text scanning (a temple
   keeper's saints *philosophy* produced six sacred-site bindings; "bring me grave salt"
   bound a memorial). Fixes shipped: `match_blueprint` now matches whole words (plus
   plural) against `what` + subject + tags only — never free text; `what` is a first-class
   `WorldPromise` field; and `bind_promise` gained the always-honor eligibility gate —
   a non-quest promise binds only if it names a buildable `what` or carries a real
   spatial hint. Offline re-score of the same 82 recorded outputs: bound 17 → 6, with
   the survivors defensible (Emberwood witch, bandit camp, bounty, saints' tombs) plus
   two residual `what`-misuses now forbidden by the tuned extraction prompt (requests
   and held items are not claims; confidence calibration guidance). The dialogue prompt
   also now nudges NPCs to anchor rumors in space ("north of town", "at the old
   windmill") — without that, nothing directional ever reaches the binder. Regression
   tests: `test_live_chatter_stays_flavor` in `tests/test_promises.py`. Remaining: a
   second live run with the tuned prompt to confirm the extraction-side improvements.
3. **M5 — flesh.** ✅ Done (June 2026): `wildmagic/flesh.py` drafts decorations on the
   background lore channel when a promise binds (whitelisted fields: `site_name`,
   `keeper_name`, `keeper_backstory`, `prop_description`, `arrival_line`, clamped by
   `normalize_flesh`); realization consumes them as decoration only (keeper name and
   backstory, first prop's description, arrival message); apply points are recorded as
   `flesh: {before, after}` per action + top-level `final_flesh`, and replays inject
   them with zero model calls (`tests/test_flesh.py`). A promise that never receives
   flesh realizes from the deterministic skeleton unchanged.
4. **M6 — eval graduation + agent playtest.** Confirm the golden cases are consumed by a
   test, add the end-to-end determinism case, and script an agent playtest:
   talk → walk → verify realization → quest turn-in.
5. **A promise journal (player legibility).** ✅ Done (June 2026): `journal` CLI command
   (aliases `rumors`/`promises`, free action) and a `J`-key UI page mirroring the quest
   log. Player-facing statuses (`journal_status`): heard / corroborated / found true /
   settled / proved false — binding is engine-internal and never shown. Bound entries
   get a soft hint ("somewhere north of where you heard it", terrain variant); promises
   without a spatial component simply have no hint; realized entries show the flesh
   `site_name` when one exists. Quests stay in the quest log.
6. **First new producer: prophecy spells.** ✅ Done (June 2026): `create_promise` effect
   (aliases prophecy/prophesy/foretell/promise). The spoken claim goes through the same
   binding gate as any rumor — concrete `what`/`where` binds, loose words stay flavor
   ("Your words drift into the world, too loose yet to bind it"). Item prophecies are
   allowed ("somewhere north, a blade waits with my name on it"): they default to a
   `hidden_site` cache and the item spawns there via the generalized fetch-objective
   path. Engine-authoritative cost floor (3 + salience + 5 for items) on top of the
   resolution's costs, and prophesied treasure always incurs Wild Debt.

**Debt/prophecy consolidation (June 2026, user decision):** debt and prophecy are the
same idea — the world committing to a future event — so the Promise Ledger is the single
home for both. Zone generation is the *spatial* executor; event timers are the *temporal*
executor (a timer carrying `promise_id` settles that promise when it fires). Wild Debt
(`_incur_wild_debt`, shared by debt-flavored `set_flag` and item prophecies) keeps one
rolling `promise_wild_debt` threat-promise: journal-visible while owed, settled when the
collector arrives, reopened if you borrow again. Eligibility-gate note: `kind="quest"`
exempts the gate only when structurally trusted (engine-authored with a typed objective);
an extractor merely labeling chatter "quest" does not skip it. Tests:
`tests/test_journal.py`.

**Shakedown round 2 (tuned prompt, same 32 saved dialogues):** 66 promises (was 82 —
less chatter extracted), 0 technical failures, confidence now centered on 0.6 instead of
0.95-everywhere, bound 2/66 — both via the quest-label loophole, closed above. Caveat:
usable-`where` stayed 0 because this corpus re-extracts *saved* replies; the dialogue
geography nudge only affects fresh conversations, so the next live playtest (M6) is
where binding recall should reappear.

## Replay contract (implemented June 2026 — replay format version 3)

**What's recorded:** the promise **apply point**, not just the payloads. Each action
record may carry a `promises: {before: [...], after: [...]}` field holding the
extraction-output promise dicts applied to the engine at that command boundary
(snapshotted *pre-merge*, so duplicate-merge corroboration re-runs); promises drained
after the last recorded action land in a top-level `final_promises` list. Replay injects
these via `GameSession.apply_recorded_promises` at the same boundaries and calls no
extraction, binding, or flesh models.

**What's deliberately not recorded:** binding, reservation, and realization. Binding and
merging are pure deterministic functions of the promise plus engine state, and once the
apply point is timing-faithful the engine state matches — so replay re-derives identical
bindings (verified by `tests/test_replay_promises.py`, which generates a zone in the gap
between dialogue and drain and confirms the live relocation to the next unexplored zone
replays exactly). Realization RNG is `stable_seed(rng_seed, "frontier_zone", zx, zy)`,
fully derivable from the run seed. Recording derived results would create a second
authority that could drift from the code. LLM flesh (M5, now implemented) is the one
promise-pipeline model output and is recorded the same way: `flesh: {before, after}`
apply-point events per action plus a top-level `final_flesh`, injected on replay via
`GameSession.apply_recorded_flesh`. Replay sessions run with `replay_mode=True`, which
silences both background producers.

Replay format version is 3; older files fail fast and are simply deleted (no migration
parser, no dual support). The old scheme of re-injecting promises at the *dialogue*
action is gone — it generated zones differently whenever the background drain landed
late, which was exactly the M2 debt.

## Acceptance criteria (tagged by milestone)

- **[M3]** Tell-and-walk: a fixed dialogue line about a chapel north of town produces a
  `sacred_site` flavored as that chapel, with a keeper who knows the story, in the next
  unexplored northern zone — town or not.
- **[M4]** A quest whose target binds eastward realizes its objective in the first
  eastern unexplored zone; completing it marks the promise fulfilled and the giver
  acknowledges it.
- **[M2+]** Replays reproduce identical bindings and realizations with zero model calls.
- **[M2+]** Unbindable claims still color dialogue but never realize; the ledger respects
  its caps; quest reservations are never spilled.
- **[every milestone]** **No dual authority:** after M1 there is no `lore_claims` state;
  after M2 no `lore_hooks`; after M4 no persistent `Quest`. A grep for the deleted names
  is part of each milestone's done-check.

### Post-M6 stretch (designed for, not in the M1–M6 contract)

- Meet-and-walk: "I'll meet you at the chapel" binds an existing NPC to a future site
  without duplicating them. **This needs a new engine capability — moving a live NPC
  between saved zone snapshots (`departed_for=<promise_id>` + restore at destination) —
  which belongs to the companion/follower layer, not promise binding.** The kind and
  schema land in M1; the behavior waits for that layer.
- Imperial paperwork: a flier naming Investigator Kipler binds a future named
  investigator, checkpoint, or patrol threat-promise. Wants Phase 14's heat system as the
  producer-of-record so escalation is paced, not ad hoc.

## Deliberately deferred

- Conditional appearances ("only at midnight") — needs a time/condition vocabulary on
  bindings; the event-timer machinery is ready for it when wanted.
- Promise *expiry and betrayal* as content (an NPC who knowingly lied) — wants the
  reputation system first.
- Player-authored promises (spreading false rumors as a geopolitical weapon) — wants the
  coalition arc.
- Prophecy spells: wild magic that mints promises. The plumbing will exist after M2 — one
  new effect type away whenever we want it.
- Promise pressure (unresolved promises creating narrative weather, decay, or demands) —
  interesting long-term texture, but too much for the current implementation target.
