# Emergent World Strategy

Decisions and direction set 2026-06-14. This document is the north star for how Wild
Magic becomes an **emergent, player-driven world** — one where each run drops the player
into a fresh, surprising world that they and their deeds author together as it responds to
them. It also fixes the division of labor between deterministic procedural systems and the
LLM.

It is a companion to `AESTHETICS_AND_TONE.md` (what the world feels like) and
`ARCHITECTURE.md` (how the code is shaped). This is the *why* and the *plan*.

> **Build status — updated 2026-06-19.** Much of this plan is now implemented. Phases
> **0, A, B, D, E, and F are built** — the deterministic spine (deeds, factions, legend) and
> most of its consumers (bonds/orgs/followers, backlash, the consequence renderer, the daily
> 05:00 tick, the standing/legend readout) are live. The major remaining phase is **C — the
> per-run geopolitical world roll.** §3 marks which original gaps are now closed and §8
> carries per-phase status markers. This document stays the *why*; for live build state and
> per-phase detail see `EMERGENT_WORLD_IMPLEMENTATION.md` and the session log.

---

## 1. The Vision

> The player is the **director**. The procedural rules engine is the **stage crew** that
> moves the scenery. The LLM is the **playwright and the press** — it reads what the
> player meant and writes the world's lush reaction. The player should be able to *try
> anything*, and the world should answer naturally: rumors of their deeds spread,
> resistance movements rise, backlash sparks riots and wars, NPCs defect to or flee from
> them. And every run is a **new world** — a fresh geopolitical situation to read,
> exploit, and bend — so the story flows both ways: the player reacts to the world as much
> as it reacts to them.

Four felt promises:

1. **Consequence (within the run).** Nothing the player does of significance vanishes
   *while the run lasts*. Deeds ripple outward in space — distant zones hear, factions
   respond, the temperature changes.
2. **Agency.** The player can found organizations (more than one, with distinct
   identities) or climb existing ones, win individuals to a personal cause, turn a town,
   topple a province — by *acting*, not by picking dialogue options.
3. **Freshness.** No meta-progression. Every run rolls a different world — which kingdoms
   dominate, who rules them, how the map lies, where the old traditions survive — so each
   run begins as a genuine surprise the player must read and master. The broad strokes are
   constant (a cold Empire vs. wild magic; you are the pest); the specifics are never the
   same twice.
4. **Organic lushness.** The world is shockingly rich and specific, and that richness
   increasingly *responds to the player* rather than generic flavor.

**Explicitly not a goal: meta-continuity.** Runs do not carry state forward. Death is a
real ending, not a chapter break — and the reward for it is a wholly new world, not a
slightly-advanced old one. Emergence lives *inside* a run; variety lives *between* them.

---

## 2. The Core Principle: who places which lego block

The single most important design decision is **what the LLM is for**. Getting this wrong
makes the game slow, incoherent, and non-deterministic. Getting it right makes it feel
alive on modest hardware.

**The LLM is an interpreter and a narrator. It is not the simulator.**

| Job | Owner | Why |
|---|---|---|
| Hold world state (who, where, standing, goals) | **Procedural ledgers** | Must be exact, fast, serializable, replayable |
| Decide *mechanical* consequences (reputation deltas, who spawns, who flees) | **Procedural rules** | Determinism, balance, no latency on the critical path |
| Read the *meaning* of an ambiguous player action | **LLM (semantic)** | "What did that weird spell *do*, socially?" is exactly what LLMs are great at |
| Decide an NPC's heart on the cusp (join? betray? flee?) | **LLM (semantic), procedural-first** | Score it procedurally; ask the LLM only when it's genuinely a character judgment |
| Generate long-form lush text (rumors, memos, manifestos, dialogue, chronicles) | **LLM (generative)** | Long-form natural language is the LLM's home turf |
| Place ordinary set-dressing | **Procedural** | Cheap, deterministic, already rich; not emergent |

Reframed as the user's metaphor: **the rules engine snaps most of the lego together by
itself.** The LLM does two things only it can do well — (a) *read the player's intent and
the situation* to decide which few blocks matter and why, and (b) *paint* the assembled
structure in specific, lush prose. The ledgers are the studs both systems snap onto.

**Corollary — the LLM is always optional decoration.** The deterministic skeleton must
stand complete without a single model call (this is already the project's discipline for
flesh/lore/canon). The world simulates fine offline; the LLM makes it *beautiful and
legible*, never *functional*. This guarantees replay, testability, and graceful
degradation on a cold or absent backend.

---

## 3. What already exists (the foundation is real)

We are not starting from zero. The bones of an emergent world are already in the repo:

- **Promise ledger** (`promises.py`) — `WorldPromise` with kind/subject/tags/salience/
  confidence, a status lifecycle, spatial hints, binding to `SITE_BLUEPRINTS`,
  `Objective`, and a `Reward` that *already has a `reputation` field*. Claims become
  reservations become realized sites. **This is the spine for turning ideas into world.**
- **Lore extraction** (`lore.py`) — reads NPC dialogue and read-matter into structured
  claims. This is *already* "LLM reads meaning → structured world state." We generalize
  it from words to deeds.
- **Semantic ledger** (`semantics.py`) — a shared blackboard of `WorldNote`s anchored to
  entities, **places, and factions** (`faction_anchor` exists today), with salience and
  decay, read by every LLM consumer, never on the critical path. **This is the substrate
  for accreting "what is true about X now."**
- **Canon records** (`canon.py`) — materialized, observed world canon as guardrails.
- **NPC memory & perception** (`models.py` `NPCProfile.remember`, perception radius) —
  NPCs already witness and remember nearby events.
- **Effect verbs** — `change_faction`, `add_trait`, `animate_object`, `schedule_event`
  already let a spell flip allegiance, brand a reputation, and arm a future consequence.
- **Capability routing** (`capabilities.py`) — the pattern for "only invoke the LLM work
  this situation actually needs." We reuse it to gate emergent calls.
- **The clerk** — `CLERK_NOTICES`, a recurring imperial voice (today a static table; to
  become a dynamic named voice that escalates with the imperial-threat axis).

**The gaps this plan set out to fill — and where they stand now** (updated 2026-06-19):

- `Reward.reputation` was **defined but never consumed**. ✅ **Closed** — consumed via
  `engine._grant_reward_reputation`, wiring quest/promise rewards into faction standing
  (which is multidimensional, per §5.1).
- Factions were a **per-entity string** (`player`/`ally`/`enemy`/`neutral`) with no standing
  powers. ✅ **Closed** — `factions.py`/`FactionLedger` makes the Empire, resistance, and
  player orgs first-class objects with multidimensional standing, keyed by **role** so one
  rule fits both the current two-pole scaffold and Phase C's rolled roster.
- There was **no deed ledger**. ✅ **Closed** — `deeds.py`/`DeedLedger` records the player's
  actions with visibility/witnesses/target tags; `DEED_RULES` interprets them; the daily
  tick applies them once.
- There was **no off-screen world simulation**. ◐ **Partly** — the daily 05:00 tick runs
  backlash, Empire pressure, and bond drift; still thin are zones evolving in your absence
  and **follower off-screen assignments** (the reeve who collects taxes — a tracked
  follow-up).
- There was **no per-run geopolitical roll** — kingdoms, rulers, region map, and surviving
  traditions are still effectively fixed rather than rolled fresh each run. ◯ **Still open —
  this is Phase C, the major remaining phase.** (Cross-run *persistence* remains deliberately
  **not** a goal — see §5.4; the gap is per-run *variety*, not continuity.)
- There were **no legibility surfaces**. ◐ **Partly** — the consequence renderer (deed
  props, a wanted poster bearing your legend), a standing/legend readout (`describe_standing`),
  and daily rumor spread exist; the fuller **named-voices chorus** and zone-entry situation
  reports are still thin.

The plan below describes the full arc; most of the deterministic spine and its consumers are
now built, reusing the foundation rather than duplicating it.

---

## 4. The Architecture: four layers

```
   PLAYER ACTS  ───────────────────────────────────────────────┐
        │                                                       │
        ▼                                                       │
 ┌─────────────────┐   most deeds detected by rules;            │
 │ L2 INTERPRETER  │   LLM only classifies the ambiguous/novel  │
 │ (LLM, semantic) │   ── "what did this MEAN?" → tags, deltas  │
 └─────────────────┘                                            │
        │ structured deed                                       │
        ▼                                                       │
 ┌─────────────────────────────────────────────────────────┐   │
 │ L0  THE LEDGERS (deterministic, serializable studs)      │◄──┘
 │  • Deed ledger      (NEW)   what the player has done      │
 │  • Faction ledger   (NEW)   standing powers, reputation   │
 │  • Promise ledger   (have)  pending truths → sites/events │
 │  • Semantic ledger  (have)  accreted notes per anchor     │
 │  • Canon            (have)  committed facts (guardrails)  │
 └─────────────────────────────────────────────────────────┘
        │ consumed by the daily 05:00 world tick (and at run end)
        ▼
 ┌─────────────────┐   reputation math, faction goals, spawns, │
 │ L1 SIMULATOR    │   new promises (a resistance cell, a       │
 │ (deterministic) │   crackdown, a riot) — all rules, no LLM   │
 └─────────────────┘
        │ new world state + "things worth narrating"
        ▼
 ┌─────────────────┐   rumors, memos, manifestos, news, the    │
 │ L3 NARRATOR     │   run chronicle, consequence-bearing       │
 │ (LLM, long-form)│   props/notices — the lush, legible voice  │
 └─────────────────┘
```

### L0 — The Ledgers (deterministic state)

The lego studs. Everything else reads and writes here. All serializable, all replay-safe.

- **Deed ledger (built — `deeds.py`).** Append-only, structured log of consequential player actions:
  `{turn, zone, type, magnitude, target_tags, source, visibility, witnessed_by,
  evidence_tags, faction_implications}`.
  Examples: `killed_imperials(n=3, witnessed)`, `freed_captive`, `razed_building`,
  `cast_atrocity_in_market`, `spared_enemy`, `desecrated_shrine`, `defended_townsfolk`.
  Most deeds are emitted by the **rules engine** at the moment they happen (combat deaths
  by faction, structures destroyed, NPCs saved/killed, catastrophic spell severities).
  **Visibility gates everything downstream:** a deed is `secret | witnessed | public |
  mythic`, carries `witnesses` (entity ids) and `evidence_tags` (`bloodstain`,
  `burned_market`, `survivor_testimony`), and only enters the legend through one of those
  channels. The propagation half mostly **exists already** — NPC perception/memory *is*
  the witness list, the promise ledger already carries claims between zones, and lore
  claims already track `status` (unverified/rumored/contested/**false**) and `confidence`,
  which is your built-in rumor distortion ("blamed for a thing you didn't do"; "worshipped
  for a deed they misunderstood"). This is the input to all emergence.
- **Faction ledger (built — `factions.py`).** Named standing powers as first-class objects:
  `{id, name, kind (empire|nation|resistance|player|cult|guild), standing, mood,
  resources, goals, home_zones, notes_anchor}`. **Seeded fresh at the start of every
  run** from the run seed (§5.4) — the Empire is a constant, but which rival nation still
  defies it, which kingdoms are conquered or client, who rules them, and where they sit on
  the map all vary run-to-run. Holds **any number of player-founded organizations** (a
  guild, a warband, a cult — distinct identities, each its own entry) that come into being
  as the player creates them, and tracks the player's **rank** in orgs they climb rather
  than found. `standing` is **multidimensional** (§5.1), not one number, and is finally
  **consumed** (wiring up `Reward.reputation` and deed-driven deltas). `resources` are
  spendable (§5.2) — patrols, informants, recruits, relics — so a faction's reactions are
  *expenditures*, not just threshold trips, which also means a faction can run *out* and
  escalation self-limits. The ledger is **never carried between runs.** (Per-NPC *personal
  bonds* to the player — orthogonal to org membership — live on the NPC, not here; see
  §5.3.)
- **Promise ledger (have).** Stays the mechanism for "an idea that will become real."
  Emergent events mint promises: a resistance cell = a promise that realizes as an
  `inhabited_site` with allied NPCs; a crackdown = imperial reservations; a riot/siege =
  a hostile event reserved in a zone.
- **Semantic ledger (have) + Legend ledger (built — `legend.py`).** The player's **legend** has two forms
  that must not be conflated — and, because the semantic ledger's contract is *"the hard
  engine never reads notes to decide outcomes,"* they live in **two places**:
  (a) **bounded-vocabulary weighted tags** the *simulator and scores* read
  (`{"marketburner": 0.8, "imperial_killer": 0.9}`) — these are mechanical state and live in
  a dedicated **`LegendLedger`** (engine-truth), not the semantic ledger; and
  (b) **prose notes** the *prompts* read ("the one who freed Hollowmere") — mirrored into the
  existing `SemanticLedger` (which has salience/decay/caps), pure flavor, never consumed for
  outcomes. A legend change writes the tag (mechanical) and optionally the prose (flavor).
  See `EMERGENT_WORLD_IMPLEMENTATION.md` §1.3.
- **Canon (have).** Committed facts as contradiction guardrails for the Narrator.

### L1 — The Simulator (deterministic, off-screen)

A **world tick** that runs **once per in-game day at 05:00** (and at run end) — *not* per
game-turn, and deliberately *not* on zone crossing. Pure rules, no LLM, fully deterministic
(though may use seeded random rolls). It consumes the deed ledger and advances the world:

- Apply standing deltas (across the dimensions of §5.1) to factions from recent deeds.
- Let each faction **spend resources** to pursue goals: the Empire spends patrol capacity
  and an informant network to seed a raid promise; a battered resistance spends recruits
  on a cell or goes quiet; a furious populace riots. Reactions are budgeted expenditures,
  not free threshold trips — which maps directly onto the promise system's existing
  `PromiseReservation.capacity_cost` and per-zone reservation caps.
- Translate those into concrete world changes via the **promise ledger** (new sites,
  reservations, scheduled events) and **semantic notes** (mood shifts).
- Resolve bonded followers' **off-screen assignments** (§5.3): the reeve's tax take, the
  librarian's discovery, the spy's intel — applied here, between beats.
- Decide off-screen outcomes for places the player changed (a freed town prospers or is
  razed; a province tips toward revolt).
- **Causal compression.** A long run can emit hundreds of low-level deeds; the tick
  periodically rolls many into one higher-level **story beat** (`"a three-zone guerrilla
  campaign against the Censorate"`, with `source_deeds`, `salience`, `factions_affected`,
  `tags`). This keeps prompts small (critical on the A750) and the legend coherent, and it
  is what the run chronicle and named voices summarize from.

The Simulator owns **balance and pacing**. Escalation is bounded two ways — rules-driven
caps *and* finite faction resources — so the world can't run away or become unwinnable.
The LLM never decides magnitudes.

### L2 — The Interpreter (LLM, semantic, sparing)

The LLM reads *meaning* where rules can't. Reuse and generalize `lore.py`'s
extraction-to-structured-JSON pattern. Two jobs:

- **Deed interpretation.** For *ambiguous or novel* deeds only (a strange wild-magic
  outcome, an unprecedented social act), classify the social meaning → `{tags, faction
  deltas (bounded), salience, legend_note}`. Routed like capabilities: clear-cut deeds
  (a plain kill) skip the LLM entirely; only the weird ones get a look.
- **Disposition on the cusp.** When an NPC must decide to join/flee/betray and the
  procedural score is *near a threshold*, ask the LLM to weigh persona × legend ×
  personal history into a verdict + one line of reasoning. Far-from-threshold cases are
  decided by arithmetic.

These are short, structured, background calls batched at pause points.

### L3 — The Narrator (LLM, long-form, the lush layer)

Where the model earns its keep. All background, all batched at pauses, all recorded at
apply point so replays cost zero calls.

- **Rumors & news.** Turn high-salience deeds into rumors that propagate (seeded into NPC
  memory and the promise ledger so distant towns greet you by reputation).
- **Named voices.** Generalize the clerk into a chorus that comments on the player's arc:
  the weary Censorate clerk, a resistance pamphleteer, town criers, a rival nation's
  envoy. Their text escalates with the deed/faction ledgers.
- **Faction situation reports** shown on zone entry ("the borderlands are in open
  revolt; imperial patrols have doubled").
- **Manifestos & propaganda** when an organization the player founds or leads forms or acts
  (each with its own voice and identity).
- **The world roll.** At run *start*, name and flavor the freshly-rolled geopolitics —
  the rival nation and its ruler, the conquered kingdoms and their grievances, each
  province's mood (§5.4). The structure is procedural; the LLM gives it specific, lush
  identity the player reads on arrival.
- **The run chronicle.** At run *end*, a generated saga of the player's deeds that gives
  the run narrative closure. It is a capstone the player reads, **not** a seed for the
  next run — the next run is a wholly new world.
- **Consequence-bearing world detail** (see §6 on props).

---

## 5. The headline emergent systems

These are the systems that deliver the vision, built on the four layers.

### 5.1 Deeds → Legend → Reputation
Every consequential act lands in the deed ledger, gated by **visibility** (a secret deed
shapes only those who know it). The Simulator converts known deeds into faction standing
shifts and the Interpreter distills a **legend** (mechanical tags in the `LegendLedger` +
a prose mirror for prompts). The legend is the connective tissue: dialogue, rumors, faction
reactions, and follower decisions all read it. *This is the first thing to build —
everything else consumes it.*

**Reputation is multidimensional, not a single score** — a scalar would flatten exactly
the cases this game is about. Track a small, **open** set of named axes, each of which
must drive a *distinct* consequence or it doesn't earn a slot. A useful starting set:

- **notoriety** — how widely you are known;
- **fear** — how dangerous you are believed to be;
- **gratitude** — who thinks you helped them;
- **legitimacy** — whether your cause is seen as rightful;
- **uncanniness** — how spiritually/magically alarming you are;
- **imperial threat** — how hard the Empire prioritizes suppressing you.

The point is that **one deed produces different consequences along different axes**:
burning an imperial barracks raises rebel gratitude, townsfolk fear, notoriety
everywhere, and imperial threat sharply; raising the dead to defend a village earns
gratitude *and* uncanniness at once. The axis set is open so a per-run world can introduce
one it needs (a death-cult run might add `sanctity`).

### 5.2 Backlash → Resistance, Riots, Wars
The Simulator watches standing and mood, and factions **spend resources** to act on what
they see — this is what makes reactions feel intentional rather than arbitrary. A
crackdown is not "Empire fear > 70"; it is "the Empire spends 2 patrol capacity and 1
informant network to seed a raid promise in this province." Expenditures mint world events
via the promise ledger: a **resistance cell** (allied `inhabited_site` + recruitable NPCs)
in a zone that loves you; a **riot** or **crackdown** where the Empire is enraged and has
the means; at the extreme, a **province tipping to revolt** — a standing change in the
faction ledger that re-skins whole regions. Because resources are finite, a faction that
has overspent goes quiet, and pressure ebbs and flows instead of spiking forever. The
Narrator announces these; the player feels the temperature change.

### 5.3 Bonds, Organizations & Followers
This is where the world should feel **richer and more organic than other games**. The way
to get there is to refuse to build a single "party" system and instead build a few general
primitives whose interaction produces the richness. The player is not special-cased; they
are one agent in the same social model as everyone else.

**The primitives (build these, not the examples):**

- **Individual bonds, not a party.** Every NPC carries a *personal* relationship to the
  player — loyalty, trust, fear, admiration, resentment — that is theirs alone, evolving
  from accumulated memory, the player's legend, and their own life. A bond exists
  independent of any organization: a single companion can be fiercely loyal to *you* while
  belonging to nothing.
- **Organizations are first-class and plural — and not player-exclusive.** Guilds,
  warbands, cults, courts, the Empire itself are all entries in the faction ledger with an
  identity, goals, resources, and internal ranks. The player can **found several with
  distinct identities** (a trade guild *and* a paramilitary, each its own org), **rise
  through the ranks of an existing one** and draw followers from inside it, hold rank in
  several at once, or belong to none. "The player's faction" is therefore not one thing —
  it is whatever organizations the player has founded or climbed.
- **Three orthogonal layers, never conflated.** (1) combat allegiance (the existing
  `faction` string — who swings at whom), (2) organizational membership + rank (the
  affiliation graph), (3) the personal bond to the player. A reeve can be loyal to you,
  belong to your guild, and never lift a weapon.
- **Followers are agents with their own posture and assignment, not an entourage.** A
  bonded NPC may travel with you, *or* hold a post and act **off-screen** through the
  Simulator (L1): a reeve who stays at your manor and collects taxes, a librarian who turns
  up useful knowledge while you are away, a fence who moves your loot, a spy in a rival
  court. "Follower" means *bonded and tasked*, not *standing next to you*.
- **NPCs have inner lives and hidden agendas.** Their memory accretes not only what they
  saw *you* do but their *own* developments — they fall in love, grow rich, grow afraid,
  nurse a secret prior loyalty. These private goals and the bond model are what make
  devotion, drift, departure, and betrayal *emerge* rather than being scripted.

**Emergence is the deliverable; the events below are samples, not features to code.**
Given those primitives, all of the following should fall out without a bespoke system for
any of them:

- An ally **betrays** you — because a despised deed crossed their loyalty threshold, or
  because they were a **double agent** whose hidden allegiance outweighed the bond all
  along.
- A follower **falls in love** with someone in a town you pass through and leaves to settle
  down — *unless* you persuade them; and even if you do, that **love-memory persists** and
  reshapes them: less willing to risk their life, more interested in money, a softness an
  enemy can exploit.
- A guildmate you promoted becomes a rival when your paramilitary's reputation starts
  costing the guild trade.
- A frightened follower quietly starts skimming; a devoted one takes a blow meant for you.

**How the primitives produce that:** the bond/disposition model — a few per-NPC scalars
(loyalty, fear, admiration, resentment, ideological alignment, optional hidden pressure)
scored deterministically from traits × legend × personal memory — decides the *direction*;
thresholds trigger *moments* (join, drift, defect, betray, depart); the **Interpreter**
(LLM) is consulted only when a heart is genuinely on the cusp, and the **Narrator** voices
the moment; the *consequences* are written back as durable traits/notes on that NPC (via
`add_trait` + the semantic ledger) so they **persist and color every future decision** —
the love-memory literally becomes a note that future bond checks and dialogue read. We are
building the engine that makes these stories possible, not the stories.

**Keep the math invisible.** The scalars are internal plumbing, not a system the player
min-maxes. This must read as *relationships*, not a social-stat battle — the Narrator
surfaces it as character (a hesitation, a confession, a cooling), never as numbers. If it
ever feels like grinding approval bars, the design has failed, however correct the math.

### 5.4 A Fresh, Varied World Each Run (procedural geopolitics)
**No meta-progression.** Every run rolls a new world from the run seed. Constant: a
handsome, cold Empire that outlaws wild magic, and you as the pest. Varied: which
conquered kingdoms exist and which still openly defy the Empire; who rules each and what
they want; how the region map is laid out; where each old tradition (blood, bone, crystal,
song) survives; how hard the Empire grips each province. **Procedural rolls the structure**
— deterministically from the seed, so a given seed always yields the same world and replays
stay free. **The Narrator names and flavors it** — the rival nation and its ruler, the
client kingdom's grievance, the borderland's mood — a high-value LLM use (long-form,
specific, lush) that the player *reads and adapts to* at the start of every run.

This is the other half of the loop the user wants: the player must **react to the world**
as much as the world reacts to them. A run where the southern kingdom is in open revolt and
the bone-singers hold the marsh plays nothing like one where the Empire grips everything
and the only allies are smugglers. Death ends *this* world's story; the next run is a
genuinely new board to read and master. The faction ledger (§4) is seeded here and never
carried forward. The win condition (`AESTHETICS_AND_TONE.md` #14) is concrete and resolved
*within a single run*: **kill the emperor** — who is reachable only once geopolitical
pressure has spent down the Empire's defenses, so the emergent loop *is* the path to
victory (see `EMERGENT_WORLD_IMPLEMENTATION.md` §0.5).

**The roll must be gameplay-legible, not just lore.** Every rolled world-feature has to
imply at least one *tactical affordance* the player can act on — a safer or hostile
region, a recruitable tradition, a faction conflict to exploit, a trade opportunity, a
usable spell school, a rumor source, a danger. The **procedural** layer emits those
affordances (they are mechanical and the player can reason over them); the **LLM** only
names and flavors them — it cannot invent an affordance, only dress one. The player should
be able to glance at the opening situation ("the Marsh Kingdom is in open revolt; bone
magic is tolerated there; patrols are thin but informants are everywhere") and make
strategic choices — a paragraph of beautiful lore with no implied moves is a failure of
this section.

### 5.5 Legibility (do not skip this)
Emergence the player can't perceive is indistinguishable from randomness. The world must
*narrate its responses*: rumor lines on zone entry, NPCs greeting you by legend, a faction
ledger/standing screen, the named voices, the run chronicle. **Budget as much design
effort on showing the reaction as on computing it.** This is a first-class system, not
polish.

---

## 6. Reconsidering prop generation (the prompt that started this)

The user's instinct is correct: **LLM-generating ordinary props is the lowest-value use
of the LLM** because props are not emergent — they don't respond to the player. The call
cost (~6–9s/batch on the A750) buys flavor that good procedural generation already
provides (180 templates + scenes + region themes).

**Decision:**

- **Keep procedural props as the lush default.** They are cheap, deterministic, replay-
  safe, and already rich. Procedural generation, well-tuned, can make the world
  "shockingly lush" on its own.
- **Demote pure-flavor LLM prop generation** from on-by-default to opt-in/occasional. It
  stays in the codebase (`prop_gen.py`) as a toggle and a proof-of-pattern, but it is not
  where LLM bandwidth should go.
- **Redirect that bandwidth into consequence-bearing world detail.** Re-aim the same
  machinery so the LLM dresses things that *reflect the deed ledger*: a memorial or a
  bloodstain where you slaughtered a squad; a smashed table in the tavern you brawled in;
  a wanted poster bearing *your* legend; graffiti for or against you; a shrine the
  townsfolk raised to "the Marketburner." These **are** emergent and use the LLM's
  strengths (read the deed → write specific lush prose). The prop generator becomes a
  **consequence renderer**, not a random dresser.

Net: the world gets *more* lush and *also* more emergent, with the LLM spent where it's
irreplaceable.

---

## 7. Risks & guardrails

- **Coherence drift** (LLM contradicting established facts). *Guard:* the Narrator always
  receives relevant canon + semantic notes and is instructed to *decorate, never
  contradict* (existing flesh discipline). Canon is the source of truth.
- **Determinism / replay.** *Guard:* the deterministic skeleton (L0/L1) never needs a
  model call; all LLM output is recorded at its apply point and replays consume the
  recording. Tests force providers to mock/off.
- **Runaway escalation / unwinnability.** *Guard:* the Simulator owns all magnitudes and
  pacing with hard caps; the LLM proposes meaning, never numbers. Faction reactions are
  paid for from **finite resources** (§5.2), so escalation self-limits — an overspent
  faction goes quiet — rather than ratcheting forever.
- **Semantic ledger as junk drawer.** *Guard:* keep the legend in two forms (§4) —
  bounded-vocabulary weighted **tags** that systems can reason over, separate from **prose**
  only prompts read — atop the ledger's existing salience/decay/caps. Prose that no system
  consumes is flavor, not state, and is treated as disposable.
- **Latency** (A750 ~6–9s/call). *Guard:* all emergent LLM work is background and
  **batched at the daily 05:00 tick and other natural pauses**, never per-turn; foreground
  LLM stays reserved for spells and dialogue. **Causal compression** (§4/L1) keeps prompts
  small. Route aggressively so most ticks make zero calls.
- **Legibility debt.** *Guard:* §5.5 is a tracked deliverable, not an afterthought.
- **Scope.** *Guard:* the phased roadmap below ships value at every step; each phase is
  playable and useful even if the next never lands.

---

## 8. Phased roadmap

Each phase is independently shippable and leaves the game better. The order is chosen so
that the **deed → legend → reputation → faction** spine is stable *before* the deep systems
that consume it are built — which is precisely how we avoid building anything throwaway.

**Phase 0 — The micro-loop (proof of aliveness).** ✅ **Built.** Before any breadth, run *one* deed type
end-to-end through the **real abstractions** (not mocks): the player kills imperial
soldiers in a witnessed fight → the deed ledger records it → the world tick shifts one
standing axis up and one down → the next zone entry shows one rumor line → one NPC
references it → one wanted poster appears → the standing screen reflects it. This is the
smallest complete loop — **act → record → simulate → narrate → show → affect play** — and
it validates the spine's *shape* before anything is built on top of it. (Doubles as the
anti-throwaway insurance: the abstractions are exercised for real on day one.)

**Phase A — Deeds & Legend.** ✅ **Built.** Generalize the micro-loop's deed ledger to the obvious acts
(kills by faction, structures destroyed, NPCs saved/killed, catastrophic spells), with
**visibility/witnesses/evidence** on every deed. Distill the legend in both forms —
**bounded weighted tags** (for scores) and prose (for prompts) — procedural-first, LLM
classification only for ambiguous deeds. Add **causal compression** once deed volume
grows. *Payoff:* dialogue and rumors already read the semantic ledger, so the world starts
referencing your deeds with little new Narrator work.

**Phase B — Multidimensional factions & reputation.** ✅ **Built.** Promote factions to first-class
ledger objects (seeded fresh per run, never persisted); make `standing` the **open
multidimensional set** of §5.1 (wiring up `Reward.reputation`); give factions **spendable
resources** (mapping onto `PromiseReservation.capacity_cost`); add a faction/standing
screen (legibility). Empire pressure scales with the *imperial-threat* axis specifically,
not a blob.

**Phase C — Fresh geopolitics at run start.** ◯ **Next — not yet built (the major remaining phase).** Roll the per-run world from the seed — which
kingdoms dominate/defy, who rules them, the region map, where traditions survive — each
rolled feature carrying a **tactical affordance** (§5.4); the Narrator names and flavors
it. Seeds Phase B's faction ledger and delivers the "every run is a new world" promise.
(Deterministic from seed; replay-safe.)

**Phase D — Backlash events.** ✅ **Built.** The Simulator **spends faction resources** into promise-
ledger events (resistance cells, riots, crackdowns) from standing/mood. Narrator announces
them on zone entry.

**Phase E — Consequence renderer.** ✅ **Built.** Re-aim prop/detail generation at the deed ledger
(memorials, wanted posters bearing your legend, graffiti, damage); demote pure-flavor prop
gen. Deliberately **ahead of Phase F**: it is cheap and produces a large *perceived* jump
in "the world remembers me" while the spine the deep social system needs finishes settling.

**Phase F — Bonds, organizations & followers (full ambition, built last on purpose).**
✅ **Built** (core; per-NPC bond drift, player orgs, follower postures). The deep social
system of §5.3, kept at full scope — *not* de-scoped. It is sequenced last
because it *consumes* the legend, multidimensional standing, and factions; building it on a
still-shifting spine is exactly what would force a later rewrite. Build the general
primitives — per-NPC bond model (a few scalars), the affiliation graph (found multiple
player orgs, or climb existing ones with ranks) kept distinct from combat allegiance,
follower postures and **off-screen assignments** resolved in the Simulator (a reeve
collects taxes, a librarian surfaces knowledge). Devotion, drift, departure, and betrayal
*emerge* from thresholds — voiced by the LLM, durable trait/note consequences written back
to color all future behavior, the math kept invisible (§5.3). Build the primitives that
generate the stories, never bespoke event systems.

The **run-end chronicle** (a Narrator capstone, no carry-forward) ◯ **not yet built** — can
ship alongside any phase now that deeds exist.

Phase 0 then A/B are the highest leverage: once deeds and multidimensional reputation exist
and are legible, the world is already visibly responsive. Phase C is the highest-*delight*
early add — it makes every run feel new. The ordering protects Phase F's ambition by
ensuring it is built once, on a stable spine, rather than rebuilt.

---

## 9. One-paragraph summary

Roll a **fresh, varied world each run** (procedural structure with tactical affordances,
LLM-flavored geopolitics) — no meta-progression, just a new board to read every time. Build
deterministic **ledgers** (deeds with visibility; factions with **multidimensional**
standing and spendable resources) on top of the ones we already have (promises, semantics,
canon); let a deterministic **Simulator** turn the player's known deeds into faction
standing and concrete world events at natural pause points; use the LLM *only* as an
**Interpreter** (reading the meaning of ambiguous deeds and NPC hearts) and a **Narrator**
(the world roll, rumors, memos, manifestos, the run chronicle, and consequence-bearing
detail). Make every reaction **legible**. Prove it with a one-deed **micro-loop** first,
then build outward — sequencing the deep social system (bonds, organizations, followers)
*last and at full ambition*, on a spine that has already stopped moving. The result: a
world that is procedurally exact, LLM-lush where it counts, different every run, and —
within each run — genuinely authored by the player one deed at a time.
