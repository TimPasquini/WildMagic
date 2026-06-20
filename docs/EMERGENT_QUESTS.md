# Emergent Quests

Design for turning quests from a fixed fetch-an-item list into **organic objectives that
emerge from what NPCs say (and what the player observes) and resolve from what the player
*does*.** A companion to `EMERGENT_WORLD_STRATEGY.md` (the why) and
`EMERGENT_WORLD_IMPLEMENTATION.md` (the build). This doc is the *quest* slice of that
vision and assumes its layers (L0 ledgers, L1 simulator, L2 interpreter, L3 narrator).

Status: **design only — nothing here is built yet.** It reuses systems that already exist.

Settled design decisions (2026-06-19):

- **Reward flow: hybrid.** A deed that satisfies a quest lands a *small* immediate effect
  (legend/standing already move; a witnessing NPC reacts on the spot); the giver's *full*
  reward is deferred — collected when you next cross paths, or delivered off-screen by the
  simulator when that is plausible. No mandatory backtracking, but returning is still
  rewarded, and many personal rewards should require meeting the giver again.
- **Legibility: always explicit, leads kept quiet.** Every opened quest becomes a tracked
  entry, including overheard/inferred hooks — but unconfirmed `lead`-status hooks live in a
  separate **rumors/leads lane**, not the main active list, so plural concerns + LLM hooks
  don't flood it. (This raises volume, so the log gains a status taxonomy and dedupe; see §7.)
  An entry may gain detail as the world reveals it; the log need not show every hidden fact up
  front.
- **Mutation: full.** Quests can complete, **expire, fail, and transform** on the world
  tick. A missing daughter found dead off-screen becomes a revenge quest; bandits you
  ignore sack the town that asked for help. Mutation should be occasional, causally legible,
  and tied to real world pressure — not a timer that punishes exploration.
- **Identity: soul-first (built up front).** Specific-person objectives bind to stable **soul
  identity**, not transient entity ids. NPCs have no soul id today (only the player does), so a
  real NPC soul-identity layer is built **first** (§10 Q0), as the foundation the matcher and
  deeds depend on: a rescued, disguised, polymorphed, resurrected, possessed, or magically
  regenerated person stays recognizable as the same quest subject.
- **Matching: explicit, not one-size-fits-all.** Objective data uses a match spec with
  subject refs, required tags, any-of tags, and excluded tags. Soul refs are strongest; tags
  provide constraints and fallback matching.
- **Multiplicity: plural concerns.** NPCs may carry more than one active concern. An
  LLM-extracted hook normally opens a second possible quest rather than replacing a
  procedural concern, unless dedupe proves they are the same concern.
- **Repair later, evidence now.** A later appeal/repair layer is on the table: if the game
  missed "I really did free Mara," an LLM may propose a correction from recorded evidence,
  but the deterministic engine still validates and applies it. This is not implemented in
  this plan, but the quest design must preserve the evidence such a system would need.

---

## 1. The one-line reframe

> A quest is a **promise** carrying an **objective**, *opened* by what an NPC says or what
> the player observes, and *closed by a deed* — not by a turn-in.

That single shift — fulfillment by **deed** rather than by walking an item back to a
giver — is what converts a checklist into emergence. The "rescue the daughter" quest
completes the moment the player frees the captive *in play*; the deed ledger reports it,
a matcher sees an open objective whose soul refs / match spec fit, and the quest resolves.

---

## 2. How quests work today

Two creation paths, both producing the same narrow thing — a fetch-this-named-item quest.

- **Procedural roll.** [`generate_npc_quest()`](../wildmagic/npc_quests.py) gives each NPC a
  40% chance to want one of **9 hard-coded items** in `QUEST_ITEMS`, with a gold/item
  reward. Plus dozens of hand-authored `wanted_item=...` story NPCs in `generation.py`.
- **The NPC profile.** A "quest" is four flat fields on the NPC —
  [`wanted_item` / `wanted_qty` / `reward_gold` / `reward_item`](../wildmagic/models.py) —
  surfaced to dialogue as `my_current_need`.
- **Creation → tracking.** Talking to the NPC calls
  [`register_heard_quest_item()`](../wildmagic/npc_quests.py), which mints a `quest`
  [`WorldPromise`](../wildmagic/promises.py) with a `fetch` `Objective` and a `Reward`
  (salience 5, confidence 1.0). So a quest is *already* "a promise with an objective +
  reward + giver." **The spine is correct.**
- **Fulfillment.** Only one path is wired: **trade the item back** →
  [`quest_completed = True`](../wildmagic/engine.py) (engine ~L3003). Otherwise a manual CLI
  `quest complete <idx>`.

### The two reasons it feels hard-coded

1. **The objective vocabulary is a dead enum.** `Objective.from_dict` accepts
   `fetch | kill | visit | talk`, but **only `fetch` has any completion detection** (via
   trade). `kill`/`visit`/`talk` are accepted and then never satisfied by anything.
2. **The one organic path is forbidden from making quests.** The lore interpreter is the
   only system that reads free dialogue, but
   [`LORE_EXTRACTION_SYSTEM_PROMPT`](../wildmagic/prompts.py) is told to *discard* exactly
   the hooks we want: *"requests for items are NOT claims … 'bring me grave salt' is a
   request."* "My daughter is missing" is a plea — filtered out. And even if the model
   emitted `kind:"quest"`, the lore schema carries no objective, so
   [`bind_promise`'s `trusted_quest` gate](../wildmagic/promises.py)
   (`objective is not None`) treats it as plain rumor flavor.

### What already exists that we build on (the lucky part)

The emergent-world spine is **already in the repo and wired**, so quests are a connective
layer, not a new engine:

- [`deeds.py`](../wildmagic/deeds.py) — `Deed` (with `type`, `magnitude`, **`target_tags`**,
  `visibility`, `witnesses`, `applied`), the `DeedLedger`, and a declarative
  `DEED_RULES` table. `DEED_TYPES` already includes `freed_captive`, `defended_townsfolk`,
  `killed_imperials`, `razed_building`, etc.
- [`record_deed()`](../wildmagic/engine.py) — the general emission path; witnesses are
  detected and remember the deed immediately (legibility for free).
- [`run_world_tick()`](../wildmagic/engine.py) / `_maybe_run_daily_tick()` — the daily
  05:00 simulator beat, with **idempotent** deed application (`applied` flag) and seams for
  `_simulate_empire_pressure` / `_simulate_backlash` / `_simulate_bonds`.
- [`faction_ledger`](../wildmagic/factions.py) with multidimensional standing, and
  [`_grant_reward_reputation()`](../wildmagic/engine.py) already consuming
  `Reward.reputation`.
- `NPCProfile.bond` and `NPCProfile.lead` (a secret an NPC may share — essentially an
  emergent micro-quest already) in [`models.py`](../wildmagic/models.py).

---

## 3. The model: quest = promise + deed-satisfied objective + reward

Nothing here replaces the promise ledger; it generalizes the **objective** and adds a
**matcher** that ties the existing deed ledger to it.

### 3.1 Generalize `Objective` (L0)

Keep `Objective(type, data)`; widen the vocabulary and make `data` carry a match spec the
deed matcher can read. Proposed bounded vocabulary (curated like `DEED_TYPES`):

| objective | satisfied by deed(s) | example |
|---|---|---|
| `fetch` | item in inventory / trade (existing) | bring the glass eye |
| `rescue` | `freed_captive` matching target tags | free the missing daughter |
| `slay` | `killed_*` matching target tags / a named entity | kill the bandit chief |
| `clear` | N `killed_*` of a tag in a zone | drive the raiders from the mill |
| `defend` | `defended_townsfolk` for a subject/zone | protect the market |
| `deliver` | `talk`/interaction with item held at target | take this to the witch |
| `visit` | enters a zone / realized site | see the shrine |
| `investigate` | `visit` + observe an evidence tag | find what razed the chapel |
| `avenge` | `killed_*` of the entity that wronged the subject | revenge transform target |

**Q1 ships only the objective types whose deeds already exist — `rescue` (`freed_captive`)
and `defend` (`defended_townsfolk`).** The rest (`slay`, `clear`, `visit`, `investigate`,
`deliver`, `avenge`) wait on new deed / synthetic-deed emission (§5, §10 Q1b); they are listed
here so the vocabulary is designed once, not so they all land at once.

`Objective.data` gains an explicit, bounded **match spec**. There is no single global
"tags intersect" rule; each objective states how strict it needs to be:

```
{ "deed_types": ["freed_captive"],       # which deeds can satisfy
  "subject_refs": ["soul:mara"],         # specific subject identity, when known
  "required_tags": ["civilian"],         # all must be present
  "any_tags": ["kin:innkeeper_07"],      # at least one, when provided
  "excluded_tags": ["illusion"],         # none may be present
  "zone": [x,y] | null,                  # scope, when placebound
  "count": 1, "progress": 0 }            # for clear/collect style
```

Soul identity is the strongest signal. If an objective names `soul:mara`, freeing Mara can
complete it even if a non-essential kin tag was missing, provided required/excluded
constraints still pass. If no `subject_refs` are known, tags become stricter because they
are the only identity signal. The matcher (§5) is the *only* new code that reads this.
`fetch` keeps its existing trade-based path so nothing regresses.

### 3.2 Generalize the NPC's needs: `Concern` (L0)

Replace the four flat `wanted_*` fields with a list of structured **concerns** the NPC
carries (fetch becomes a special case, migrated, not deleted):

```
Concern = { concern_id: str,             # stable local seed id
            objective: Objective,        # any type, not just fetch
            subject: str,                 # "my daughter Mara"
            reward_hint: Reward,          # gold/items/reputation the giver offers
            reward_source: str,           # giver | rescued_subject | faction | site | unknown
            delivery: str,                # in_person | immediate | offscreen_possible
            visibility: str,              # secret | spoken | public  (gates who can pick it up)
            status: str }                 # see §7 taxonomy
```

`to_dialogue_context` surfaces the relevant subset as `my_concerns` (generalizing
`my_current_need`) so the dialogue model can voice them naturally instead of only quoting an
item name. Once a concern is opened into a quest promise, the promise becomes the source of
truth; the concern remains the NPC-side seed and dialogue context, not a competing quest
record.

---

## 4. Opening a quest (generation) — deterministic floor, LLM enrichment

Two producers, in priority order. **The procedural floor stands with the LLM off** (the
project's core discipline): every NPC can carry a typed, varied concern with zero model
calls; the LLM only enriches surface text and mints genuinely novel hooks.

### 4.1 Procedural concern vocabulary (rules, always on)

Replace the 9-item fetch roll with a small **concern template table** keyed by NPC
role/region/traits — e.g. *missing kin*, *a monster troubling them*, *a theft*, *someone
they want dead*, *a place they fear*, *a debt*. Each template names an objective type,
seeds a `subject` (often a real or promise-bound entity), and a `reward_hint`. This is the
deterministic, replay-safe generator that gives the world quests on a cold backend.

### 4.2 LLM hook extraction (interpreter, sparing) — the "missing daughter" path

This is the L2 Interpreter job: *read meaning → structured objective.* Two changes:

- **Stop suppressing pleas.** Add a hook-extraction mode (a second claim shape, or a new
  pass) so a *need voiced in dialogue* becomes a candidate objective with a match spec:
  `{ objective_type, subject, match_spec, reward_hint, salience }`. The existing
  blanket "requests are not claims" rule stays for *trade chatter* but no longer eats
  *plights*.
- **Route it like capabilities.** Only fire when an NPC actually voices a concern (the
  router already distinguishes this kind of turn), and batch at pause points — never
  per-turn (A750 latency, `EMERGENT_WORLD_STRATEGY.md` §7).

The procedural concern is the floor; an LLM hook normally opens an additional possible
quest specific to what was actually said. If the hook clearly describes the same giver,
subject, and objective as an existing procedural concern, dedupe may enrich that concern's
flavor or evidence instead of creating a duplicate. With the model off, you still get a
quest — just a templated one.

> Generation guardrail (mirrors `EMERGENT_WORLD_STRATEGY.md` §5.4): the LLM may only
> *name and flavor* an objective the rules vocabulary already supports — it cannot invent
> an objective type the matcher can't satisfy. No un-closable quests.

---

## 5. Closing a quest (the heart): the deed → objective matcher

One general function, not per-quest scripting. After a deed is recorded, scan open quest
promises and test each objective's match spec against the deed:

```
on record_deed(deed):
    for promise in open quest promises:
        obj = promise.objective
        if deed.type in obj.deed_types
           and all(obj.required_tags in deed.target_tags)
           and (obj.any_tags intersects deed.target_tags or unset)
           and not (obj.excluded_tags intersects deed.target_tags)
           and (obj.subject_refs intersects deed.subject_refs or unset)
           and (obj.zone == deed.zone or unset):
                advance obj.progress; if complete -> mark promise objective satisfied
```

For specific-person quests, `subject_refs` should be stable soul refs (`soul:mara`) rather
than only current entity ids. Entity ids can be carried too, but soul identity is what lets
the same objective survive disguise, transformation, resurrection, possession, or a body
being regenerated by magic. For broad objectives like `clear`, the subject may be unset and
the matcher relies on deed type, zone, required/any tags, and count.

**Every trigger is a deed.** Non-combat completions — picking up or delivering an item
(`fetch`/`deliver`), entering a zone or realized site (`visit`), observing evidence
(`investigate`) — mint **synthetic deeds** (`acquired_item`, `delivered`, `visited`,
`observed_evidence`) so the matcher has a *single* input and every match traces to one
`deed_id`. That uniform evidence trail is exactly what the appeal layer (§9.1) re-runs over.
`fetch` keeps its existing trade path during the Q1 transition, then migrates onto a synthetic
deed in Q1b — so there is one fulfillment mechanism, not two, once breadth lands.

Two-stage timing, which is what delivers the **hybrid** reward and keeps the world
idempotent:

1. **Immediate (at `record_deed`):** mark objective progress / satisfaction and push the
   quest-log update — this is the legibility beat ("you realize this was Mara"). The small
   immediate reward is already happening for free: witnesses to the deed react and
   remember it via the existing `record_deed` witness loop, and standing/legend move
   through the deed's own `standing_deltas`.
2. **Deferred (on `run_world_tick`):** deliver the giver's **full** `Reward` exactly once
    (idempotency via a `granted` flag on the quest, matching the deed `applied` discipline).
    Route the reward's reputation through the existing
    [`_grant_reward_reputation()`](../wildmagic/engine.py). The giver thanks/pays the player
    on next meeting, or the simulator delivers off-screen when the reward source and world
    state make that plausible (a noble's courier, a faction favor, a rumor, a `lead`).
    Many rewards should *not* teleport to the player: a personal heirloom, rank promotion,
    apology, oath, or fragile relationship beat usually waits until the player meets the
    giver again. Conversely, a rescued subject carrying the reward can grant it
    immediately at the rescue scene.

Generalize the `lead` field (a freed captive telling you where a cache lies) into this same
pipeline — a `lead` is just a quest opened by a deed instead of by dialogue.

> Note: the matcher needs identity rich enough to bind a deed to a *specific* subject (this
> captive **is** Mara). That rides on the new NPC soul-identity layer (§10 Q0): every NPC
> gets a stable soul ref, and `Deed` gains a **`subject_refs`** carry holding the souls (and,
> optionally, current entity ids) it touched — so a `rescue` matches the right person across
> disguise/resurrection, not just "a civilian." Additive and replay-safe.

---

## 6. Mutation, failure & expiry (full) — the world tick rewrites quests

This is what stops the explicit quest log from being a static checklist. On the daily tick,
a bounded **mutation rule table** (curated like `DEED_RULES`) scans open quests and may:

- **Expire / go cold.** Untouched for too many days → status `cold` (still visible, lower
  salience). Some concerns harden into resentment on the giver's `bond`.
- **Fail.** The world resolved it without the player (another faction freed/killed the
  target; the town fell). Status `failed`, with a narrated reason.
- **Transform.** The subject's fate changes the objective: *rescue* → *avenge* when the
  captive is killed off-screen; *defend* may become an existing actionable objective such as
  `avenge`, `recover`, `visit`, `clear`, or `slay` when the town is sacked. "Mourning" and
  "retaliation" are narrative frames or quest-log reasons unless they earn general objective
  mechanics of their own. A transform mints a *new* objective referencing the old (never
  silently rewrites history — same additive discipline as `StoryBeat`).

Triggers come from the simulator the tick already runs (`_simulate_backlash`,
empire pressure, off-screen site outcomes) — quests subscribe to those outcomes rather
than inventing their own world events.

Mutation is meant to create the occasional living-world surprise, not constant punishment.
The best failures are concrete consequences of visible pressure: bandits ignored for days
attack the town and steal the merchant's stock; a crackdown reaches the prisoner first; a
rival faction claims the rescue. Rates, windows, and severity live in the bounded mutation
rule table, never in LLM prose.

---

## 7. Legibility (always explicit)

Every opened quest is a tracked [`quest_log_entries()`](../wildmagic/engine.py) entry,
including overheard/inferred hooks. To keep that from becoming noise:

- **Status taxonomy** (extend the current `active|completed`):
  `lead` (inferred/overheard, unconfirmed) · `active` (a giver asked) · `completed` ·
  `failed` · `changed` (transformed; links to its successor) · `cold` (expired).
- **Quiet leads lane.** Only giver-asked quests (`active` and beyond) sit in the main quest
  list; unconfirmed `lead` entries live in a separate rumors/leads view the player can browse
  but isn't nagged by. A lead is promoted to `active` when a giver formally asks or evidence
  confirms it — so "always explicit" never means "always in your face."
- **Dedupe** by `(giver, objective.subject_refs, objective.type)` so the same concern heard
  twice is one entry.
- **Surface mutation:** when the tick changes a quest, drop a journal line and (when the
  deed/standing warrants) let the consequence renderer / named voices reference it
  (`EMERGENT_WORLD_STRATEGY.md` §5.5).
- **Reveal progressively:** log entries may gain additional data once the player learns it
  or the promise system has legitimate spatial evidence. A first entry might say "Find
  Mara"; later evidence can add a soft hint like "captors were seen near the old mill."
  This supports explicit tracking without giving omniscient spoilers.

The promise journal hint machinery (`journal_status`, `journal_hint`) already exists; quests
reuse it for soft spatial hints rather than exact markers.

Both interfaces must stay in sync as statuses become player-facing: the GUI quest view and
the CLI quest output can begin with a minimal shared taxonomy, then grow into richer
presentation together.

---

## 8. Worked example — "the missing daughter"

1. **Open.** In dialogue an NPC mentions their daughter Mara is missing. Router flags a
    concern → L2 hook extraction emits `Objective(rescue, {deed_types:[freed_captive],
   subject_refs:[soul:mara], required_tags:[civilian], any_tags:[kin:innkeeper_07]})`,
   `reward_hint` = small gold + gratitude. A quest promise opens; quest log shows
   **active: "Find Mara."**
   *(LLM off? The innkeeper's procedural concern is already "missing kin" — same objective,
   templated subject.)*
2. **Close by deed.** Three zones away the player frees a captive whose soul ref is Mara.
    `record_deed("freed_captive", target_tags=[civilian, kin:innkeeper_07],
    subject_refs=[soul:mara])`. The matcher fires immediately: quest → **completed**; the
    freed captive reacts on the spot (existing witness loop); `liberator` legend + rebel
    gratitude move via the deed's own rules. No backtracking required.
3. **Hybrid reward.** On the next tick the quest becomes reward-ready once; if the player
   returns, the innkeeper thanks them in dialogue (their `bond` warms) and pays the personal
   reward. If the giver has a plausible off-screen channel — a noble's courier, faction
   patronage, a rumor network — the simulator may deliver it without backtracking.
4. **Mutation branch.** Had the player ignored it, a later tick might roll Mara killed by
   her captors → quest **transforms** to `avenge` (slay the captor), and the innkeeper's
   bond curdles toward grief/resentment.

All of it falls out of: one generalized objective, one deed matcher, one mutation table —
**no quest-specific code.**

---

## 9. Determinism & the LLM-optional discipline

- The deterministic skeleton (concern templates + deed matcher + mutation table) produces,
  satisfies, and mutates quests with **zero model calls**. The LLM only (a) reads a plea
  into a structured objective and (b) voices givers, rewards, and mutations.
- All LLM quest work is **background, batched at pauses**, recorded at apply point so
  replays cost zero calls (`EMERGENT_WORLD_STRATEGY.md` §7 guards).
- The LLM never picks magnitudes, rewards, or whether a quest can close — only the rules
  vocabulary does, so no un-satisfiable or runaway quests.

### 9.1 Future appeal / repair layer (not built now)

Design decisions should assume a later **appeal** path can exist. This is a humane escape
hatch for cases where the world clearly recorded enough evidence but the first matcher pass
missed it: the player might say, "I really did free Mara," and an LLM can inspect the quest,
recent deeds, soul refs, witnesses, inventory, zone history, and audit logs to propose a
small repair.

The LLM never mutates state directly. It returns a structured repair proposal such as "link
this `freed_captive` deed to this quest" or "add missing `soul:mara` to this deed's
subject refs"; the deterministic engine validates the operation against the same matcher and
idempotency rules before applying it. A more permissive "wish" override may be allowed for
bug recovery, but it should be explicitly logged as an intervention.

To keep this possible without implementing it now, preserve evidence:

- stable `quest_id`, `concern_id`, `deed_id`, `soul_ref`, `giver_ref`, source zone, and turn;
- append-only quest status history instead of silent overwrites;
- pure objective matcher that can be re-run during review;
- completion/failure/transform records that name the deed or world event responsible;
- reward grants with idempotent ids and traceable sources;
- recorded LLM hook outputs as proposals, with engine validation deciding what applied.

---

## 10. Sequencing (slots into the existing roadmap)

Because the deed ledger already exists, this can land incrementally without waiting on
Phase D:

- **Q0 — soul identity foundation (built first).** Give NPCs a stable **soul ref** that
  survives respawn, zone reload, disguise, polymorph, possession, resurrection, and magical
  body regeneration; add the soul-based **`subject_refs`** carry to `Deed`. Nothing
  player-facing changes, but every later step depends on it. *Payoff: specific-person quests
  can exist at all.*
- **Q1 — objectives become live (vertical slice).** Generalize `Objective` + explicit match
  spec; build the deed → objective matcher as a pure function. Wire only the two objective
  types whose deeds already exist — **`rescue`** (`freed_captive`) and **`defend`**
  (`defended_townsfolk`) — and prove the whole loop end-to-end on the real abstractions (the
  Hollowmere prison / `free` machinery already emits `freed_captive`). `fetch` keeps its trade
  path; the manual CLI `quest complete <idx>` retires for a type once deed matching covers it.
  *Payoff: the first quests that close from play, on a matcher validated before breadth.*
- **Q1b — objective breadth + synthetic deeds.** Add the missing emission so the rest of the
  vocabulary becomes closable: a **hostile-kill deed** for `slay`/`clear` (which must first fix
  the `killed_civilians` mis-tag — see §11), and **synthetic deeds**
  (`acquired_item`/`delivered`/`visited`/`observed_evidence`) so `fetch`/`deliver`/`visit`/
  `investigate` flow through the same matcher (everything-is-a-deed). *Payoff: the full
  objective table is live and uniformly evidenced.*
- **Q2 — generalized concerns.** `wanted_*` → plural `Concern` records; procedural concern
  vocabulary; `my_concerns` in dialogue context. Migrate the 9-item table + story NPCs.
  *Payoff: quests vary by NPC situation, still LLM-free.*
- **Q3 — hook extraction.** Loosen the lore prompt; add the sparing L2 hook pass. *Payoff:
  the organic "daughter is missing" path.* LLM hooks create additional possible quests by
  default, with dedupe/enrichment only when they clearly duplicate an existing concern.
- **Q4 — mutation & hybrid rewards.** Mutation rule table on the tick; deferred reward
  grant with `granted` idempotency and context-aware delivery; always-explicit log taxonomy
  + dedupe. *Payoff: the world rewrites quests; the explicit log stops being static.*
- **Q5 — appeal-ready evidence.** Not a player-facing repair system yet: add append-only
  status history and durable source links where Q1-Q4 did not already require them. *Payoff:
  future repair/appeal can link existing evidence instead of inventing state.*

Q0–Q2 make quests emerge from any NPC's situation and close from play; Q3–Q4 make them feel
authored by the world.

---

## 11. Risks & open questions

- **Subject identity.** Specific-person objectives need stable soul identity, which NPCs lack
  today — so the soul layer is **Q0**, built before the matcher. Confirm it survives
  transform/resurrection and that summon/trigger-owned kills stamp `subject_refs` (the
  `owner_soul_id` follow-up noted in `record_deed`).
- **Hostile-kill deed semantics.** Today `_record_kill_deed` emits `killed_civilians` for any
  `kind=="npc"` victim, so a bandit kill could both mis-score as butchery *and* satisfy a
  `slay`. Q1b's hostile-kill deed must disambiguate threat from civilian before `slay`/`clear`
  go live.
- **Explicit-log volume.** "Always explicit" + plural concerns + LLM hooks can flood the log;
  the **quiet leads lane** + status taxonomy + dedupe (§7) are load-bearing, not polish. Watch
  this in playtest.
- **Concern ↔ promise duplication.** An NPC can have multiple concerns, and LLM hooks may
  add more; once a concern opens a quest promise, the promise is the source of truth and the
  concern is the seed/dialogue context.
- **Mutation pacing.** Transform/fail rates need tuning so the world feels alive, not
  capricious; keep magnitudes in the rules table, never the LLM.
- **Reward delivery.** Organic reward flow means no universal "grant everything at tick"
  rule. Some rewards are immediate, some wait for an in-person meeting, and some can arrive
  off-screen when a faction/giver has the means. Keep this data-driven by reward source and
  delivery mode rather than by bespoke quest cases.
- **Appeal surface.** Future repairs must be evidence-based by default and logged when they
  intervene. Preserve enough ids and histories now so that repair can be added later without
  giving the LLM arbitrary state-edit power.
- **Backward compat.** `wanted_item` migration must keep existing saves and the trade-based
  `fetch` turn-in working through the transition.

---

## 12. File-by-file change index (proposed)

| File | Change |
|---|---|
| `wildmagic/promises.py` | Widen `Objective` vocab + explicit `data` match spec; keep `fetch`. |
| `wildmagic/deeds.py` | Add soul-based `subject_refs` to `Deed`; new deed types — a hostile-kill type for `slay`/`clear` and synthetic `acquired_item`/`delivered`/`visited`/`observed_evidence` (Q1b); (optional) widen `TARGET_TAGS`. |
| `wildmagic/quests.py` *(NEW)* | The pure deed → objective matcher; mutation rule table; context-aware reward idempotency; status history helpers. |
| `wildmagic/npc_quests.py` | `generate_npc_quest` → concern vocabulary; `register_heard_quest_item` generalized to any objective. |
| `wildmagic/models.py` | NPC **soul ref** stable across transform/resurrection (Q0); `NPCProfile`: `wanted_*` → plural `Concern`; `to_dialogue_context` → `my_concerns`. |
| `wildmagic/engine.py` | Soul-stamp `subject_refs` on deeds (Q0); fix `_record_kill_deed` civilian mis-tag + emit hostile-kill / synthetic deeds (Q1b); call matcher from `record_deed`; deferred reward grant + mutation in `run_world_tick`; extend `quest_log_entries` with status taxonomy, quiet leads lane + dedupe. |
| `wildmagic/prompts.py` | Loosen lore prompt to extract plights; add hook-extraction shape. |
| `wildmagic/lore.py` | Hook-extraction pass / claim shape carrying an objective. |
| `tests/` | `test_quest_promises.py` extended; new matcher + mutation tests. |
