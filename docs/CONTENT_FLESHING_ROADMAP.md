# Content Fleshing — a prioritized roadmap

Drafted 2026-06-20, after the political + quest machinery landed (world roll, the
inter-faction relationship graph, relational kill reactions K1–K5, typed character identity +
derived combat stance, NPC souls + talk-to-anyone, and the deed→objective quest matcher; see
`EMERGENT_WORLD_STRATEGY.md`, `FACTION_KILL_REPUTATION.md`, `EMERGENT_QUESTS.md`,
`WORLD_GENERATION.md`).

**The diagnosis.** The machinery is built and tested, but it is **content-starved**. The
relational kill reaction fires beautifully — but the world only spawns the Empire and wild
beasts, so most of it never runs in play. The quest matcher closes objectives from deeds — but
no NPC carries a concern, so no quests emerge. The relationship graph governs who fights whom —
but the world never spawns two factions in opposition. The leverage now is **feeding the
machinery and connecting the loops**, not building more machinery. This roadmap is how.

This carries forward `EMERGENT_WORLD_CONTENT_PLAN.md` (2026-06-14): its workstream **A** (deed
palette) is mostly done; its **B** (dispositions) is reframed here as faction-aware (Tier 2);
its **C–F** (legibility, milestones, faction texture, LLM) are folded into Tiers 2–4.

---

## Principles (settled)

1. **Authored floor + LLM enrich.** Deterministic tables always produce a complete, working
   game with the model off — every spawn, role, name, concern, and reaction comes from data.
   The LLM only *enriches*: flavor names, personalities, dialogue voice, and the occasional
   novel hook, always **recorded at the apply point** so replays cost zero calls. No content is
   ever load-bearing on the model being up.
2. **Procedural + emergent.** Content is **data tables × small emission hooks** that multiply
   through the systems — no bespoke scripted scenes. A realm roster crossed with a role table
   and a disposition table yields a character who then feeds kills, quests, dialogue, and bonds
   for free. Surprise comes from system interaction, not authored set-pieces.
3. **Feed existing seams; add vocabulary only when it is general.** Every item below plugs into
   a hook that already exists (`spawn_actor`/`spawn_npc` with `identity`/`role`,
   `world_map.placement_at`, `NPCProfile.concern`, promise/journal/quest lanes,
   `run_world_tick`, `bonds.derive_disposition`). New bounded vocab is appropriate only when it
   unlocks a reusable behavior, such as a witnessed-forbidden-magic deed read by many systems.
4. **Political identity by default.** Almost every person belongs somewhere. Tactical
   `Entity.faction` can still be `neutral`, but `identity` should carry the person's realm,
   occupation bloc, guild, or other allegiance unless they are a rare true outsider (hermit,
   exile, uncanny solitary). Kills, dialogue, concerns, and reactions should all read this same
   typed identity.
5. **Hostility is derived, not baked into spawns.** Imperial soldiers, local partisans, clerks,
   and townsfolk enter the world as politically situated people. Whether they attack the player
   follows from existing public deeds, witnessed wild magic, standing, blood feuds, and faction
   relationships, not from a one-off "imperials are always enemies" spawn flag.
6. **Determinism + parity.** Seeded and replay-safe; every player-facing reader has CLI **and**
   GUI parity; faction names stay swappable.

## The opening & the exposure model *(decided 2026-06-20)*

The Tier-1 slice is anchored by a concrete opening and a precise, **asymmetric** model of how
the player draws hostility. This makes the abstract "hostility is derived" principle (above)
playable, and is the tutorial for the whole exposure system.

**Starting posture: suspected, not yet hunted.** A run opens with the player *watched but
unfiled* — no faction attacks on sight, but the file fills fast once you slip. The first beat is
the player's chance to either stay hidden or tip the fuse.

**The opening as a moral fork.** The start zone stages **imperials attacking someone** (a local).
The player's choice *is* the tutorial:
- **Walk away** → you stay unfiled; no one learns what you are. (A real, costless-to-the-system
  option — refusing is a stance, not a failure.)
- **Step in** → two consequences fire at once: you reveal yourself as a wild mage (combat magic,
  **witnessed**), which **alerts the Empire** (the garrison turns on you and your file opens), and
  you show your power to the **locals**, whose reactions are **mixed** — some warm to the sorcerer
  who stood up for one of them, some fear what you are.

**Asymmetric, witnessed hostility — the rules:**
- **Witnessed wild magic → the Empire only.** Casting in view of imperials creates a reusable
  *witnessed-forbidden-magic* exposure signal (its own small deed/tag, **not** an Imperial-only
  branch) that turns the witnessing garrison hostile and raises your file
  (`imperial_threat`/notoriety). Most people have a flicker of capacity; *openly working* wild
  magic is the crime, so being *seen* is the trigger — not merely being a sorcerer.
- **Imperial loyalists → wary/afraid, not automatically combatant.** A loyalist civilian who sees
  you cast fears you and may **report** you (feeding the exposure), rather than drawing a blade —
  distinct from the garrison, and disposition-driven.
- **Wild magic never turns a non-imperial hostile by itself.** A Stalnazan or a Brall townsperson
  does not care that you cast. A non-imperial only fights you through **provocation** (you attack
  them or their faction) or **reputation** (a blood feud / infamy with *their* people).
- **Local "mixed reactions" are disposition-driven.** Sympathetic locals warm to a witnessed cast;
  fearful ones recoil — the *same* act, a different person. This is the bonds affinity/aversion
  vocab, so the opening ties directly into Tier 2B (disposition distribution).

**Occupation varies by realm (canon).** Texture is keyed per realm, not uniform: most conquered
realms are **quiet embers** (cold tension, hidden resistance, bureaucratic menace), **Vint** is
**loud** open dissent (a legitimate separatist party), and the free **rival** is **martial**.
The Tier-1 slice should pick a realm whose texture fits the opening (a quiet-embers conquered
realm reads best for "suspected, not yet hunted").

**Two implementation defaults** (flagged; override at will): (1) the witnessed-forbidden-magic
exposure is its **own reusable deed/signal**, consumed by Empire hostility, the file/bounty, and
loyalist fear alike; (2) the opening fork **is** the run start, replacing the current neutral
town drop.

## The test a piece must pass

A content piece earns its place if it makes the systems satisfy one of four properties
(carried from the content plan):

- **Complete** — the full palette of arcs is reachable (you can fight, court, rescue, and
  betray *every* faction, not just the Empire).
- **Differentiated** — the same act lands differently on different people/powers.
- **Legible** — the world visibly, variedly remembers.
- **Compounding** — deed → reaction → quest → consequence is felt as one arc with momentum.

---

## Tier 1 — light up the machinery *(do this first; detailed)*

The two pieces that turn this session's machinery from "tested" into "felt." Do them
**together**, then playtest — they are mutually reinforcing (a faction character to kill *and*
a faction character with a grievance).

### 1A. Faction-aligned zone population — *the keystone*

*Feeds:* relational kill reactions (K3), derived combat stance (warring factions present),
talk-to-anyone (characters worth talking to), faction quests (subjects to act on). **Without
this, Tiers 1B–3 are mostly invisible.**

**The gap.** Zones spawn from region bestiaries (wild creatures) plus an imperial pool gated by
`_imperial_density`. The world map *places* Stalnaz/Brall/Ryolan/Vint/Threen, but their people
never appear, so `resolve_faction` only ever sees `empire` + `civilian`, and the relationship
graph never gets two sides in a room.

**The data shape (authored floor).** A weighted roster per realm, keyed to the realm id the
world roll assigns:

```python
@dataclass(frozen=True)
class RealmDenizen:
    role: str             # soldier | officer | clerk | merchant | townsfolk | priest | partisan
    identity: list[str]   # political tokens; locals carry [realm], occupiers carry ["imperial"]
    archetype: str        # a spawn template (stats/char/tags/ai) — reuse the region template shape
    weight: int
    posture: str          # garrison | civilian | partisan | trader; tactical stance derives later

REALM_POPULATIONS: dict[str, tuple[RealmDenizen, ...]] = { ... }   # in a new content table
```

**The hook.** In the zone-populate step (`generation._populate_zone`), read
`state.world_map.placement_at(zx, zy)` and spend a **mixed encounter budget**: preserve some
region bestiary pressure, but replace part of the creature budget with N denizens from the
owning realm's roster (N scaled by realm "grip"/`_imperial_density` + region archetype).
Spawn through `spawn_actor`/`spawn_npc` with `identity`/`role` set. Unowned wilds keep the
current creature-first pattern. The realm's `character_tags`/`tradition` (already on
`RealmTemplate`) flavor the draw.

**Hostility model.** Imperial garrisons are not hostile just because they spawned. They become
hostile through general systems: public or witnessed imperial killings, blood feud, standing
axes such as `imperial_threat`/`uncanniness`, faction relationships, and a general witnessed
forbidden-magic signal. If the current deed vocabulary cannot express "the player was publicly
seen casting ordinary wild magic," add a small, reusable deed or tag for witnessed forbidden
magic and let the existing deed/standing/combat-stance machinery consume it. Do not add an
Imperial-only hostility branch.

**The emergent payoff to design for:** a **conquered** realm's zone should spawn *both* imperial
occupiers (`identity:["imperial"]`, garrison) **and** local folk (`identity:[realm]`, civilian
or `partisan`). Then, with no extra code: killing occupiers pleases their enemies and angers
their allies (relational K3), an imperial garrison and a local partisan may fight each other
while both initially leave an under-the-radar player alone, and the locals carry the grievances
Tier 1B turns into quests. The rival realm spawns proud free-magic partisans; Threen spawns
deferent clerks who won't fight.

**First slice.** Prove the system in one conquered realm before filling every realm. The slice is
"occupied local + imperial garrison + local concerns + one deed-closable quest loop," with the
same roster format ready to generalize once tuning feels right.

**LLM enrich (optional, recorded).** Names, faces, and one-line personalities for the spawned
characters via the existing background lore channel; the deterministic roster ships complete
without them.

**Tuning to watch:** density per realm-role (occupied = tense, rival = martial, Threen = sparse
garrison); the garrison/civilian/creature ratio; how many faction characters per zone before
combat soup; whether "flying under the radar" is actually possible in occupied territory.

### 1B. Concerns → emergent quests — *give the matcher fuel*

*Feeds:* the entire Step-6 quest pipeline (matcher, deferred rewards, rescue→avenge mutation),
which is dark today because **no NPC carries a `concern`**. The target end-state is that most
meaningful NPCs have a concern, but only some concerns become active commitments.

**The data shape (authored floor).** A procedural concern table, keyed by `role` × `region` ×
realm-role, that stamps an `NPCProfile.concern` at spawn — mirroring how `derive_disposition`
already stamps a disposition:

```python
@dataclass(frozen=True)
class ConcernTemplate:
    applies: Callable      # role/region/realm predicate
    kind: str              # rescue | slay | defend | clear
    subject_kind: str      # "missing_kin" | "tormentor" | "raiders" | "home"
    binds_to: str          # "captive" | "faction_combatant" | "zone"
    reward: Reward
    salience: int
    ask_style: str         # lead | request; controls whether talk quietly reveals or asks

CONCERN_TEMPLATES: tuple[ConcernTemplate, ...] = ( ... )
```

Examples that fall straight out of Tier 1A's population:
- an **occupied-realm townsperson** → `slay` an imperial tormentor (`victim_faction:"empire"`),
  or `rescue` a kin taken by the garrison
- a **merchant** → `clear` the raiders troubling the road
- a **rival partisan** → `defend` the free-magic shrine

**Binding to real entities (the important part).** A `rescue` concern must point at a captive
who actually exists somewhere reachable. Reuse the **promise/realization** system: the concern
opens (or reserves) a `captive`-archetype site in a nearby unexplored zone whose freed-soul ref
*is* the concern's `subject_soul`. The reservation may happen when the concern is stamped, even
before the player accepts anything. So the loop is whole: NPC voices the plight → the player
learns a lead or receives a request → the captive realizes in a zone → freeing them fires
`freed_captive` with the matching soul → the matcher closes it. `slay`/`clear` concerns bind by
`victim_faction`/tags and need no placement (Tier 1A's garrison already satisfies them). If a
`slay` concern does not specify combatant-only targeting, a noncombatant can satisfy it for now;
the reputation and kill-reaction machinery already makes that choice costly.

**Lead → active quest flow.** The current implementation promotes a concern into a quest as soon
as the player talks to the NPC. That is too noisy once most NPCs carry concerns. The final flow
should reuse the existing promise/journal/quest machinery:

- Hearing or inferring a concern creates a quiet `lead`, not an active quest-log entry.
- If the NPC formally asks for help, the player can `accept` or `decline`.
- `accept` promotes the lead to an `active` quest; `decline` marks it `declined`/`cold` without
  deleting the underlying world fact.
- If the player solves the issue without accepting, the deed matcher may still notice it and let
  the giver react later with gratitude, surprise, reward, or resentment.
- CLI and GUI show the same split: active commitments in the quest log, quieter leads in a
  rumors/leads lane.

**LLM enrich (optional).** The concern is *voiced* through dialogue (`my_concern` is already in
the dialogue context); the LLM phrases "my daughter was taken to the keep" naturally. The
**LLM hook-extraction** (free dialogue → a *new* concern) is the later Q3 generative layer — the
deterministic concern floor stands without it.

**Tuning to watch:** concern frequency (most NPCs, with rare true outsiders carrying none), the
quiet-leads vs active-quest split (`EMERGENT_QUESTS.md` §7), reward sizes, mutation pacing, and
whether decline feels like a real stance rather than a delete button.

---

## Tier 2 — make the graph live *(sketched)*

Tier 1 makes the world *reactive*; Tier 2 makes it *move on its own* between visits, using the
daily 05:00 tick that already exists.

- **2A. The simulator reads relationships.** `run_world_tick` / `_simulate_backlash` currently
  ignore the new graph. Let factions act on *each other*: a realm at war with the Empire raids
  it; a **blood feud** (K5) makes that faction spend resources hunting you (route the existing
  backlash spawns through `feuding_factions()` so the *right* faction sends the hunters); the
  Unbound exploit a feud you started. Off-screen outcomes surface as rumors/journal lines. This
  is where "who you kill reshapes the board" becomes felt, not just a standing number.
- **2B. Disposition distribution by faction (the bonds gap, now roster-aware).** `bonds.py`'s
  affinity/aversion vocab still lands ~uniformly because dispositions aren't keyed to the real
  roster. Distribute via Tier 1A's roster: occupied-realm folk lean oppressed/rebel, imperial
  clerks loyalist, rival partisans defiant, Threenians deferent. Then "the same legend makes a
  rebel adore you and a loyalist fear you" finally happens. (Extend `derive_disposition` to read
  `identity` + realm role.)
- **2C. Relationship-aware dialogue.** An NPC's opinion of you is already colored by your legend
  and `_kill_standing_note`; add the *graph*: a Stalnazan's warmth shifts with what you've done
  to factions Stalnaz cares about. Reads `ledger.regard` + the kill tally; pure context, no new
  model dependency.

---

## Tier 3 — texture & legibility *(sketched)*

- **3A. Per-realm voice & set-dressing.** `WORLDBUILDING.md` already gives each realm a voice
  handle and a tradition (crystal/bone/blood/woven/charter/canal). Turn `RealmTemplate.voice`
  into real region voice/ambient/prop themes so crossing into Brall *reads* as bone-and-ale and
  Stalnaz as crystal-and-song. Occupation texture should vary by realm: some conquered places
  feel openly warlike, some quietly oppressed, some bureaucratically managed, some socially
  compromised. Today realms map onto the existing five region archetypes; this gives them their
  own skins.
- **3B. Consequence legibility variety (carries content-plan C).** Rumors/posters/consequence
  props keyed now to **faction + magnitude + feud**: a wanted poster "by order of the Queen of
  Stalnaz" reads differently from the Censor's; a blood feud grows its own ambient dread. Retire
  the single hardcoded rumor line.
- **3C. The played overworld.** The world map is a survey; make *traversing* it matter — crossing
  a realm border announces the political shift, the rival feels martial and free, the capital
  feels guarded. Eventually D8 (four entry cities, start = strategic posture).

---

## Tier 4 — breadth *(listed; later passes)*

- **4A. The seven smaller realms/peoples** — Monteary, Ontria, Gontark, the Parn, birdfolk,
  merfolk, Rentacosta (the deferred periphery pass from `WORLD_GENERATION.md`), each as a
  faction with a population roster + a tradition. Keep the Tier 1 slice mostly human/social;
  stranger peoples belong here unless a later implementation needs one as a general test case.
- **4B. Realm-flavored items/equipment/economy** — Stalnaz crystals (the one tradition with a
  real material edge), Brall scrimshaw/bone, Ryolan blood-duel gear, Vint woven charms — as
  loot, foci, and trade goods, so the geography is felt in the inventory.
- **4C. Naming conventions** — the bible's split (folk = earthy English compounds; Empire = cold
  Latinate officialese) as name tables per realm, feeding spawns and places.
- **4D. `spared_enemy` emission + the moral palette** — the last dormant deed; completes
  merciful/liberator/protector so the whole "good half" of the game is reachable.

---

## Recommended sequencing

1. **One conquered-realm vertical slice of Tier 1A + 1B → then *play*.** This is the smallest
   change that turns the whole session's machinery into a felt, content-rich run: locals,
   occupiers, mixed creatures, concerns, one lead/accept quest loop, and deed closure. Playtest
   via CLI immediately — it will surface tuning (kill-reaction magnitudes, feud thresholds,
   spawn density, concern rates) that unit tests can't.
2. **Tier 2** — once factions populate and react, make them *act* (simulator reads the graph,
   dispositions distribute, dialogue reads the graph).
3. **Tier 3** — texture and legibility, so the living world is also *vivid* and *readable*.
4. **Tier 4** — breadth (more peoples, items, names) and the moral-palette completion.

## Guardrails (carried from the repo)

Deterministic skeleton runs with **zero LLM calls**; LLM output recorded at the apply point so
replays stay free; tests force providers mock/off; **CLI + GUI parity** for every new reader;
faction names stay swappable; victory/empire-depletion stays deliberately light (designer
steer). New content tables follow the bounded-vocabulary discipline (curated, deliberate
additions), like `DEED_TYPES` and the prop tag list. Active quests and quiet leads must be
distinct in both interfaces. Hostility toward the player must be explainable from faction
relationships, public deeds, witnessed magic, standing, or feud state rather than spawn-time
special cases.

## Open tuning decisions (for playtest, not up front)

- Spawn density per realm-role and the garrison/civilian ratio in occupied zones.
- Creature-vs-person encounter budget: richly mixed zones without combat soup.
- How much public evidence makes Imperial soldiers hostile: imperial kill reputation, witnessed
  wild magic, blood feud, or accumulated `imperial_threat`/`uncanniness`.
- Concern frequency and the leads-vs-active-quest balance when most NPCs carry some worry.
- Accept/decline consequences: when declining stays quiet, offends the giver, or leaves the lead
  available for later.
- Kill-reaction magnitudes + the diminishing-returns curve (`0.05`) and `BLOOD_FEUD_KILLS` (5)
  against real play.
- How aggressively the simulator should let factions act on each other (living vs. capricious).
- How each realm's occupation feels: warlike, oppressed, bureaucratic, compromised, deferent,
  martial, etc.
