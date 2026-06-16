# Emergent World — content plan ("make the systems shine")

The Phase 0–F systems are built and verified (deeds → legend → standing → daily Simulator →
backlash + consequence props + bonds/followers). They are **general primitives**. They will
*shine* not by adding more machinery but by filling the content the machinery consumes — and
doing it the way the strategy asks: **data tables + small emission hooks that multiply through
the systems**, not bespoke scripted scenes.

> **Status (2026-06-14):** Workstreams **A and B are largely implemented** (see
> `implementation_session_log.md` → "Content workstreams A+B"). B: NPC disposition derivation
> closes the bonds-differentiation gap. A: `defended_townsfolk` now fires, and the Hollowmere
> dungeon is a literal prison — free captives → a `freed_captive` deed, some follow by
> disposition, a knower reveals a real item's location. **Remaining in A:** `spared_enemy`
> emission. Then C (legibility variety), D (milestones/epithets/nemeses), E/F.

## The guiding test

A system shines when content makes it satisfy four properties. Every item below is justified
by which one it serves:

1. **Complete** — the *full* palette of arcs is reachable, light and dark. (Right now only the
   dark half is.)
2. **Differentiated** — the same input lands differently on different people/powers. (Right now
   everyone reacts identically.)
3. **Legible** — the world visibly, *variedly* remembers; you can read the story it's telling.
4. **Compounding** — deed → legend → standing → reaction chains are felt as one arc with
   momentum, not isolated blips.

## Current content inventory (grounded in the code, 2026-06-14)

| Surface | State |
|---|---|
| Deed **emission sites** | Only `killed_imperials`/`killed_civilians` (combat) and `raised_dead`/`razed_building`/`desecration`/`cast_atrocity` (LLM spell interpreter). |
| **Dormant** deeds (rules + props + legend + compression authored, but *can never fire*) | `spared_enemy`, `freed_captive`, `defended_townsfolk`. |
| Reachable legends | defiant, butcher, uncanny, destroyer. **Unreachable:** merciful, liberator, protector. |
| Consequence props | One fixed prop per deed type (`_DEED_CONSEQUENCE_PROPS`). |
| Rumors | One template line for everything ("Word on the road: they say you …"). |
| Wanted poster | One template, bounty scales; no escalation tiers or legend flavor. |
| Backlash spawns | One generic `Imperial enforcer`; one generic `sworn sympathizer`. No scaling/naming. |
| NPC dispositions (bond affinity/aversion) | **Unwired** — no seeded NPC carries an affinity/aversion trait, so all bonds drift identically. |
| Dialogue legend-awareness | NPCs get bond *feeling* + memory lines, but not the player's current legend tags. |
| Faction flavor | Two placeholder poles, names swappable; no per-faction voice/register. |

## Workstreams (ordered by leverage)

### A. Complete the deed palette — emission sites + world objects *(highest leverage)*
*Serves: Complete.* Without this, half the game's morality is impossible and the most
resonant arcs (the rebel hero, the merciful conqueror, the moral fork) cannot happen.

- **Wire the three dormant emission sites:**
  - `freed_captive` — add **captive entities** and a `free`/`release` interaction. Natural homes:
    the empire_compound **cells** (currently spawn only guards), prisoner-escort patrols on the
    frontier, cages in the bazaar. Freeing → deed + (often) a grateful follower/ally on the spot.
  - `defended_townsfolk` — in `_record_kill_deed`, when the imperial you cut down was adjacent
    to / threatening a civilian NPC, *also* emit `defended_townsfolk` (one act, two deeds — the
    rules table already handles multi-axis). Needs civilians present in combat zones (see below).
  - `spared_enemy` — emit when the player disengages from a foe reduced to near-death, or via an
    explicit `spare` action on a downed-but-alive enemy. (Trigger choice = a design decision.)
- **Populate the world objects these act on:** captives in cells/escorts; civilians present in
  town/frontier/empire raids (some already exist as townsfolk — make sure they appear *in
  danger*, so defending them is possible); shrines/altars (already exist) flagged as
  desecration/raze targets so the LLM-interpreted deeds have real referents.
- **Unlocks:** liberator/protector/merciful legends → the whole "good" half of bonds, the
  butcher-vs-liberator fork, rescue → loyal follower pipelines, "the people's champion" arc.

### B. Differentiate the reactors — NPC dispositions + legend-aware dialogue *(highest leverage)*
*Serves: Differentiated.* This is what turns one legend into many different felt relationships.

- **Seed NPC disposition traits** from the affinity/aversion vocab (`downtrodden`/`oppressed`/
  `rebel`/`poor`/`faithful_friend` vs `loyalist`/`imperial`/`pious`/`devout`/`fearful`),
  distributed by role + region — occupied-frontier folk lean oppressed; garrison/clergy lean
  loyalist/pious; merchants mixed. **Decision needed:** the distribution (who leans which way)
  is a world-design call and ties into Phase C's faction roster.
- **Pass the player's top legend tags into `to_dialogue_context`** so an NPC speaks *to your
  reputation* ("they say you raised the dead — I'll keep my distance"), not just their feeling.
- **Add reputation-keyed greeting/refusal/awe registers** (templated by top legend + disposition;
  LLM-narrated when available, table fallback) — a rebel hails a liberator, a loyalist spits,
  the pious recoil from the uncanny, a merchant prices by fear.
- **Unlocks:** "the same legend makes a rebel adore you and a loyalist fear you" actually
  happening; double agents (`hidden_pressure`), estrangement when you turn butcher.

### C. Make consequences legible & varied — prose/props/rumors/escalation
*Serves: Legible.* The systems already remember; this makes remembering *feel* rich.

- **Consequence-prop variety:** 2–4 variants per deed type, chosen by magnitude/condition
  (a lone bloodstain vs a field of cairns; a wilted bouquet vs a growing shrine). Scale text by
  cluster count (already half-done: "It happened more than once here.").
- **Rumor variety:** a templated rumor table keyed by (legend tag × magnitude band × faction
  mood) with several variants each; LLM narrator generates when available, recorded at apply
  point, table fallback. Retire the single hardcoded line.
- **Wanted-poster escalation:** bounty *tiers* with distinct copy ("PERSON OF INTEREST" →
  "WANTED" → "WANTED DEAD OR ALIVE — by order of the Emperor"), legend-flavored ("for
  necromancy", "for sedition"), poster degrades/multiplies as threat climbs.
- **Faction-mood ambient prose:** surface `empire.mood`/`rebellion.mood` in ambient lines and
  NPC chatter ("the garrison's been jumpy since…", "people are starting to whisper your name").

### D. Escalation & milestones — give the arc momentum
*Serves: Compounding.* Make the player feel the world tightening (or rallying) around them.

- **Backlash scaling & naming:** at higher threat, a *squad* not a lone enforcer; eventually a
  **named hunter** (a recurring nemesis who remembers you). On the resistance side, named
  sympathizers / a lieutenant who can become a lasting follower.
- **Legend milestones:** crossing a tag-weight threshold mints a **titled epithet** ("the
  Unbound", "the Butcher of …", "the Pale Hand") and a one-time world beat — recorded, read by
  rumors/dialogue/posters. The epithet is the player-facing payoff of the legend system.
- **Empire-pressure stage prose:** distinct beats as defenses fall (holding → "legions thin" →
  "the road to the emperor opens"), so the win condition is felt, not just a number.
- **Org growth:** a founded org gains mood/strength, named members, a claimed base/safehouse,
  and eventually mounts its *own* actions (resistance-side backlash sourced from your org).

### E. Faction texture (placeholders → evocative; Phase-C-adjacent)
*Serves: Legible + Differentiated.* Keep all names swappable per `EMPIRE_NAME`/`REBELLION_NAME`.

- A short voice/register per pole (the Empire: bureaucratic, euphemistic, "files closed";
  the resistance: hungry, hopeful, folk-song). Drives rumor/greeting/poster tone.
- The **emperor as a distant presence**: edicts, heralds, the wanted poster as his reach — a
  felt antagonist long before he's reachable.

### F. Lean on the LLM where it adds the most variety (with fallbacks)
*Serves: all four — generative variety on the legible surfaces.* Follows the repo rule:
deterministic skeleton always works; LLM enriches; output recorded at apply point (replay-free).

- Narrator role for rumors / greetings / epithets / poster copy from legend + context.
- Broaden the deed interpreter's interpretable types as the world gains objects (e.g.
  `freed_captive`/`defended_townsfolk` via ambiguous spell outcomes, not just combat).

## Recommended sequencing

1. **A + B together** — they unlock the most story and are mutually reinforcing (freeing a
   captive *and* an NPC whose disposition makes the rescue land). A has a clear deterministic
   path; B needs the distribution decision.
2. **C** — once more deeds fire and more people react, make it all legible and varied.
3. **D** — add momentum/payoff (epithets, named nemeses, org growth).
4. **E/F** — texture and generative variety; naturally merges into Phase C.

## Decisions needed from Mark
- **B:** the NPC disposition distribution (which roles/regions lean loyalist vs rebel vs pious).
- **A:** the `spared_enemy` trigger (explicit `spare` action vs. auto-detected disengage).
- **Balance:** how much of C/E prose should be authored tables vs. LLM-narrated (default per
  repo philosophy: authored fallback always present, LLM enriches when up).

## Guardrails carried from the repo
Deterministic skeleton runs with zero LLM calls; LLM output recorded at apply point (replay
stays free); tests force providers mock/off; **GUI and CLI parity** for every new reader;
faction names stay swappable; victory/empire-depletion stays deliberately light (Mark's steer).
