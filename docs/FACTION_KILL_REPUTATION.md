# Faction Kill Tracking & Differentiated Reactions

Design for tracking, as first-class state, **how many of each faction the player has
killed**, and making factions react by *who* the player has been killing — not one flat
"kills" number. A companion to `EMERGENT_WORLD_STRATEGY.md` (§5.1 multidimensional
standing) and `EMERGENT_WORLD_IMPLEMENTATION.md` (Phase B factions); it generalizes the
deed→standing path already in the repo. It also carries the **character-unification
philosophy** (§0) — NPCs and enemies are one kind of thing — because robust faction
attribution depends on it.

Status: **K1–K2 implemented** (2026-06-19). The relational layers (**K3–K5**) are
**deferred** until the nations worldbuilding lands — the inter-faction relationship system
arrives with it — so the data is captured now and the differentiated *reactions* are built on
top later. The civilian/hostile prerequisite also shipped: `killed_civilians` no longer fires
when the victim was hostile to the player before being struck
(`engine._was_hostile_to_player`; see §2).

---

## 0. Unification philosophy: one kind of character

*(Settled 2026-06-19. The north star the actor model builds toward — staged, not all at
once. Robust faction attribution depends on it, which is why it leads this doc.)*

**NPCs and enemies are not different kinds of thing.** They are all *characters*:
soul-bearing actors with an identity, a role, and a place in the world. "Enemy," "ally," and
"townsperson" are not *types* — they are situational, mostly **derived** stances. The habit
of using `kind == "npc"` to mean "neutral non-combatant townsperson" is the conflation we are
unwinding: it bundles three independent facts — *has a persona* (talkable), *won't fight*
(flees), *is innocent* (a non-combatant) — into one flag. (Same unification as
`EMERGENT_QUESTS.md` Q0, seen from the faction side.)

**A character stores three identity dimensions — hybrid: typed where it's load-bearing, flat
tags for the rest.**

- **`identity`** — faction allegiance, a typed list: `["hollowmere"]`, `["imperial"]`, or
  `["hollowmere", "merchant_guild"]` when one belongs to several. The **source of truth** for
  `resolve_faction` and all reputation; maps to faction-ledger factions.
- **`role`** — function, a single value: `townsfolk`, `soldier`, `clerk`, `merchant`,
  `priest`. Decides combat *capability/willingness* (a clerk won't fight) and the
  *non-combatant* read for kills. (`NPCProfile.role` already exists — this promotes it to
  load-bearing.)
- **`affiliations`** — organizations, a typed list: `["merchant_guild"]`. Org membership,
  distinct from allegiance. (`Bond.affiliations` already exists.)
- plus the existing flat **`tags`** (`set[str]`) for everything loose — `flammable`,
  `undead`, lore hooks, prop matching, `FACTION_HOSTILITIES`. Identity no longer *lives* in
  `tags`; we stop guessing allegiance from them.

Worked examples: a Hollowmere weaver `identity:["hollowmere"], role:"townsfolk"`; a garrison
soldier `identity:["imperial"], role:"soldier"`; a records-keeper `identity:["imperial"],
role:"clerk"`; a guild trader `identity:["hollowmere","merchant_guild"], role:"merchant"`.

**Combat stance is derived and relational, not stored.** We stop tagging people
`ally/enemy/neutral` as a fixed property. Who fights whom is **computed** from (a) the two
characters' identities and how their factions regard each other, (b) role — a clerk or a
child won't draw a blade even for a faction at war, (c) situation/provocation. This unlocks
the cases a flat field can't express: **two characters both neutral to the player fighting
each other** because their factions are at war; **a member of a hostile faction who won't
fight because they're just a clerk**; an ambusher peaceable until provoked. *Staging: the
stored `Entity.faction` stance string stays as an interim crutch until the inter-faction
relationship model lands with the nations worldbuilding (§7 K3–K5). The philosophy is the
target; today's string is scaffolding to be replaced by a derivation.*

**Kill semantics fall out of identity × role.** A kill records two orthogonal facts: *whose
member died* (**identity** → the faction's grudge, always) and *were they a non-combatant*
(**role** → the butchery/legitimacy hit). Killing an unarmed imperial clerk is therefore
**both** "you struck the Empire" *and* "you butchered a non-combatant" — not one or the
other. This supersedes today's `killed_imperials`-vs-`killed_civilians` split, which wrongly
treats "imperial" and "non-combatant" as mutually exclusive. Target shape: one kill deed
carrying `(victim_faction, non_combatant, was_hostile)`, consequences derived from the three.
(The shipped `_was_hostile_to_player` guard is an interim proxy — "hostile ⇒ combatant"; the
real axis is the victim's combat role.)

**What this means for the build.** `victim_faction` (K1, built) is the *identity* half and
already correct. The *non-combatant* axis and *derived hostility* ride with the broader actor
unification (Q0) and the inter-faction relationship system (nations worldbuilding) — north
star, not this sprint. The near-term, relationship-free structural step is **typing the
`identity` / `role` / `affiliation` dimensions** and migrating spawners/generation to set
them, so `resolve_faction` reads `identity` instead of inferring from `kind`/`tags`.

---

## 1. The reframe

> Killing is **relational**. The reaction to a death is not "+reputation" or "−reputation";
> it depends on **whose** member died and on **each onlooker's stance toward that faction.**
> The same blow earns a rebel's gratitude, the Empire's grudge, and a bystander's fear.

So two things are missing and worth building: (a) a **per-faction kill tally** (the raw,
never-decaying fact the player asked to track), and (b) **differentiated reactions** keyed
to the victim's faction and the onlooker's relationship to it.

---

## 2. What exists today

- **Kills become deeds.** [`_record_kill_deed`](../wildmagic/engine.py) emits
  `killed_imperials` for empire-tagged victims (and `defended_townsfolk` when one stood over
  a civilian), and `killed_civilians` for innocents. [`DEED_RULES`](../wildmagic/deeds.py)
  turns `killed_imperials` into `empire +imperial_threat/+fear` and
  `resistance +gratitude/+legitimacy/+notoriety` — already a *differentiated*, multi-axis
  reaction, but **hardcoded to the empire↔resistance pair.**
- **The civilian/hostile distinction is now correct** (shipped): `killed_civilians` is
  guarded by `_was_hostile_to_player`, so killing a foe who came at you is no longer logged
  as butchery. This is the precondition for any kill-faction accounting being trustworthy.
- **Standing is multidimensional and lives per faction** ([`FactionLedger`](../wildmagic/factions.py)),
  with `adjust_standing`, role resolution (`ids_by_role`, `primary`), and `ROLE_TO_KINDS`.
- **A flat kill counter exists** — `state.stats.enemies_killed` — but it is one integer with
  no faction breakdown.

**The gaps:**

1. **Only empire kills are faction-attributed.** Killing a rival nation's soldier, a cult,
   bandits, or a beast records no faction-aware deed and no tally — so most of the world
   can't react to who you've been killing.
2. **No per-faction kill count.** Nothing answers "how many of the Marsh Kingdom have you
   killed?" — the fact the player explicitly wants tracked.
3. **No inter-faction stance model.** Factions have no "X is hostile/allied to Y." Reactions
   can't generalize past the hardcoded empire↔resistance bipole — a problem the moment Phase
   C rolls a full roster (rival nations, conquered kingdoms, cults).
4. **Reactions don't scale with volume.** One imperial kill and fifty read identically per
   deed; nothing turns sustained slaughter of one faction into a blood feud.

---

## 3. The model

### 3.1 Victim → faction resolver (one shared helper)

A single function maps a victim entity to the faction-ledger faction(s) it belonged to,
from its `tags`/`faction` (e.g. `{"empire"}` → the empire bloc; a Phase-C entity tagged with
a rolled faction id → that faction). Civilians with no faction tag map to a **`civilian`
bucket**; unaligned beasts/monsters map to **none** (not deed-worthy — consistent with
today). Used by both deed emission and the tally so they can never disagree.

*Unified-model target (§0):* this resolves against the typed **`identity`** axis, and
"non-combatant" becomes a **`role`**-derived status — not the current `kind == "npc"`
heuristic. The shipped helper reads `tags` + `kind` as the interim source until
`identity`/`role` are typed; migrating it is the first relationship-free step of §0.

### 3.2 Faction-aware kill deed

Stamp the victim's faction on the kill deed. Add a thin **`victim_faction: str`** field to
`Deed` (faction id or role; mirrors the `subject_refs` carry the quest plan adds — small,
additive, replay-safe). `Deed.target_tags`' bounded vocab (`empire/civilian/…`) can't hold
Phase-C rolled faction ids, so the typed field is the clean home.

Keep deed *types* only where the reaction is **qualitatively** different — `killed_civilians`
(innocents) vs. a general combatant kill — because those drive different legend tags
(`butcher` vs none) and feelings. Within "killed a combatant," the **faction** rides on
`victim_faction`, not on a proliferation of per-faction deed types (which wouldn't survive
the per-run roll anyway). So: a small fixed set of kill deed types × an open `victim_faction`.

### 3.3 The kill tally (a projection, not a parallel counter)

Expose `kills_by_faction() -> dict[faction_id, int]` (plus `civilian_kills`, and a
`kills_by_role` rollup) **derived from the deed ledger** — `Counter(d.victim_faction for d in
deeds if d.type in KILL_DEEDS)`. Deriving it (optionally cached) rather than maintaining a
separate mutable counter means it **can't desync, replays for free, and rebuilds during an
appeal** (the evidence discipline from `EMERGENT_QUESTS.md` §9.1). This is engine-truth and
**never decays** — it is the raw fact; *feelings* about it live in standing and do decay.

### 3.4 Differentiated, relational reactions

A kill of faction **X** adjusts standing for *every* faction by its stance toward X:

| onlooker's stance toward X | reaction (sample axes) |
|---|---|
| **is X** | fear ↑, grudge/`imperial_threat`-equivalent ↑ — they want you dead |
| **enemy of X** | gratitude ↑, legitimacy ↑ — my enemy's killer is my friend |
| **ally of X** | anger/fear ↑, gratitude ↓ |
| **neutral / civilian** | fear ↑; legitimacy ± depending on whether X was seen as oppressor or innocent |

**Near-term (no Phase C needed): role-relational.** A small role-stance matrix
(empire / resistance / rival / independent / civilian) yields the reaction from the victim's
role and the onlooker's role. This *generalizes* the current bipolar `killed_imperials` rule
— that rule becomes one cell of the matrix (empire victim → empire grudge, resistance
gratitude) — and immediately covers rival nations and cults as they appear.

**Full (Phase C): an inter-faction stance model.** When the world roll seeds real
relationships (who is allied/at war with whom), reactions read those directly instead of
role defaults. The matrix is the deterministic fallback; rolled stances are the override.

**Volume scaling.** Magnitude grows with the tally but with **diminishing returns and caps**
— the 1st imperial kill shifts standing more than the 50th; a blood feud saturates rather
than ratcheting to infinity. The simulator owns all magnitudes (strategy §7); the LLM never
sets them.

---

## 4. Legibility (the reaction must be visible)

- **Standing screen.** [`describe_standing`](../wildmagic/actions.py) gains a tally line per
  faction: "Imperial dead by your hand: 23 · Marsh Kingdom: 4." Raw counts beside felt
  standing.
- **Dialogue / rumors / named voices** key off *which* faction and *how many*: a rebel greets
  the "scourge of the Censor's men"; a rival envoy notes "you have made yourself our enemy";
  a town fears the one who has killed indiscriminately. (Reads the tally + `victim_faction`.)
- **Thresholds → events.** Sustained killing of one faction mints a **blood feud / vendetta**
  via the promise + backlash systems (that faction spends resources hunting you), and the
  opposite pole may court you. This is the "people react differently" payoff in play.

---

## 5. Guardrails

- **Tally is fact, standing is feeling.** Counts never decay and are derived from deeds;
  standing decays and is what drives behavior. Don't conflate them.
- **Bounded influence.** Diminishing returns + caps on how far kills move standing; relational
  ripples reach only factions *related* to the victim, never the whole roster arbitrarily.
- **Determinism / replay.** `victim_faction` is recorded at the deed; the tally and all
  reactions are pure functions of the deed ledger; no LLM in the loop. Tests force providers
  off.
- **Beasts aren't politics.** Unaligned creatures resolve to no faction and stay
  tally-exempt, so clearing a monster lair doesn't read as a massacre.

---

## 6. Relationship to the quest plan

This **is** the proper form of `EMERGENT_QUESTS.md` Q1b's "hostile-kill deed." A
faction-stamped kill deed simultaneously (a) feeds this tally and (b) satisfies a `slay`
objective against faction X or a `clear` of N members of X — with `subject_refs` riding along
for specific-target slays. Building the faction-aware kill deed once serves both systems;
sequence it with quest Q1b.

---

## 7. Sequencing (K1–K5, each shippable)

- **K1 — faction-attributed kills.** ✅ **Built** (2026-06-19). `factions.resolve_faction`
  (tags/kind → faction id · `civilian` bucket · `""` for unaligned creatures); a
  `victim_faction` field on `Deed` (`deeds.py`, serialized); stamped on the empire/civilian
  kill deeds in `engine._record_kill_deed`. Behavior otherwise unchanged. *Payoff: every kill
  now knows whose member it was.*
- **K2 — the tally.** ✅ **Built** (2026-06-19). `DeedLedger.kills_by_faction()` — a pure
  projection over recorded kill deeds (`deeds.KILL_DEEDS`), exposed via
  `GameEngine.kills_by_faction()`. Derived, not stored (can't desync, replays for free, never
  decays); the flat `stats.enemies_killed` is left untouched. *Payoff: the fact the player
  asked to track exists and is replay-safe.*

  *Current content note:* the only factions whose members the player kills today are `empire`
  and the `civilian` bucket, so the tally reads e.g. `{"empire": 23, "civilian": 2}`. It
  extends automatically as worldbuilding tags more entities with their nation and adds kill
  deeds for them — no change to K1/K2 needed.
- **K3 — relational reactions** *(deferred — needs the nations + inter-faction relationship
  system).* Replace the hardcoded bipolar `killed_*` entries in
  `DEED_RULES` with the role-stance matrix driven by `victim_faction`; add volume scaling.
  *Payoff: rival nations, cults, and rolled factions all react correctly, not just the
  Empire.*
- **K4 — legibility.** Standing-screen tally lines; dialogue/rumor/named-voice hooks on
  faction + count. *Payoff: the player perceives the differentiated reaction.*
- **K5 — feuds & Phase-C stances.** Threshold-driven blood-feud/vendetta events via
  promise/backlash; and, with Phase C, the full inter-faction stance model overriding the
  role matrix. *Payoff: who you kill reshapes the geopolitical board.*

K1–K2 deliver the tracking the player asked for; K3–K4 make the world visibly respond to it;
K5 turns it into consequences.

---

## 8. Open decisions

- **Identity / role / affiliation structure** — *settled (§0):* **hybrid** — typed
  `identity`/`role`/`affiliations` as the source of truth, flat `tags` retained for loose
  matching.
- **Combat stance** — *settled (§0):* **derived and relational**, not a stored
  `ally/enemy/neutral` field (lands with the relationship model; interim crutch until then).
- **Tally granularity.** Per-faction id (recommended, with a per-role rollup) vs. per-role
  only vs. per-tag. Per-id is most precise and rolls up cleanly.
- **Relational model timing.** Role-stance matrix now, full inter-faction stances at Phase C
  (recommended) — vs. waiting for Phase C to do relational reactions at all (leaves rivals/
  cults flat until then).
- **Civilian sub-buckets.** With "non-combatant" now a **`role`** axis (§0), the `civilian`
  *tally* bucket is still keyed by `identity`; the question is only display granularity — one
  bucket vs. splitting by town/region (so "you butchered *our* village" is local). Start
  single, add region tags if dialogue needs it.
- **Influence curve.** Diminishing-returns (log-ish) vs. flat-with-hard-cap for volume
  scaling — a tuning call for the simulator.

---

## 9. File-by-file change index (proposed)

| File | Change |
|---|---|
| `wildmagic/deeds.py` | Add `victim_faction` to `Deed`; define `KILL_DEEDS`; role-stance matrix + volume scaling feeding/extending `DEED_RULES` (K1/K3). |
| `wildmagic/factions.py` | Victim→faction resolver helper; (Phase C) inter-faction stance fields on `Faction`. |
| `wildmagic/engine.py` | Stamp `victim_faction` in `_record_kill_deed`; `kills_by_faction()` projection over the deed ledger; relational standing application in `record_deed`/the tick (K1–K3). |
| `wildmagic/actions.py` | `describe_standing` tally lines (K4). |
| `wildmagic/dialogue.py` / prompts | Surface kill tally + victim factions to dialogue/rumor/named-voice context (K4). |
| `tests/` | Resolver, tally-from-deeds, relational-reaction, and civilian/beast-exemption tests. |

---

## 10. Contract — what the nations worldbuilding must provide

*(The bridge to the next task. When you build world generation, these are the hooks the
already-built and planned systems expect. Satisfy them and quests, faction-kill reputation,
derived combat stance, and Phase C all light up; skip one and that system stays dark. The
roll itself is specced in `EMERGENT_WORLD_STRATEGY.md` §5.4 and `EMERGENT_WORLD_IMPLEMENTATION.md`
Phase C — this is the downstream-requirements view.)*

**For faction attribution & reputation (this doc):**

- **A faction-ledger entry per nation/power** — a `Faction(id, name, kind, …)` for each rolled
  kingdom/bloc/cult/guild, replacing the Phase-0 two-pole scaffold (`seed_phase0_factions`).
  `kind` from `FACTION_KINDS`; roles via `ROLE_TO_KINDS`.
- **An `identity` tag on every character that maps to a faction id** — so `resolve_faction`
  attributes kills. A Marsh Kingdom soldier carries `identity:["marsh_kingdom"]` *and* the
  ledger has a `marsh_kingdom` faction.
- **Inter-faction relationships** — who is allied / at war / wary with whom. This single thing
  unblocks both **K3–K5** (relational reactions) *and* **derived combat stance** (§0). Shape
  TBD, but minimally a per-pair stance the simulator and AI can read.

**For the character model (§0 / EMERGENT_QUESTS Q0):**

- **Typed `role` on generated characters** — `townsfolk`/`soldier`/`clerk`/`merchant`/… —
  driving non-combatant kill semantics, fight-or-flee, and dialogue tone.
- **Stable soul ids on NPCs** (EMERGENT_QUESTS Q0) so specific-person quests/relationships
  survive transformation.
- **`affiliations`** where relevant (guild/cult membership, distinct from nation).

**For quests (EMERGENT_QUESTS):**

- **Promise-bound, faction-aware sites & people** — a rolled nation's grievance is a natural
  concern source; its captives/oppressors are `rescue`/`slay` subjects.
- **A tactical affordance per rolled feature** (strategy §5.4): every world-feature implies a
  move the player can act on — a recruitable tradition, a faction conflict to exploit, a thin
  patrol to slip past.

**Determinism:** the roll is seeded and replay-safe; the LLM only *names and flavors* what the
procedural roll has already decided (strategy §5.4). Nothing here carries between runs.
