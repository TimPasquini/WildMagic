# Unsupported Spell Mechanics — Triage & Implementation Plan

This document triages the 200 currently-unsupported spell ideas (see the back half of
`docs/SPELL_COMPENDIUM.md`) by **value** and **difficulty**, grounded in how the engine
actually works today, and lays out concrete implementation plans for the highest-priority
clusters.

The source list was written against an imagined generic roguelike. Several of its
"underlying missing mechanics" already exist here, and several of its categories fight this
game's design contract. This plan re-frames the list against the real architecture
(`docs/ARCHITECTURE.md`, `docs/WILD_MAGIC_STATE_SURFACE_PLAN.md`) before triaging.

---

## 1. What the engine already has (correcting the source doc's assumptions)

The source doc repeatedly lists infrastructure as "missing" that is in fact load-bearing
today. The triage below leans on these:

| Capability | Where | What it already does |
|---|---|---|
| **Armed conditionals** | `effects.py` `create_trigger`; `engine.py` `_tick_triggers`/`_fire_triggers`/`_fire_damage_triggers`/`_fire_death_triggers`; `capabilities.py` `_TRIGGERS_REACTIONS` | "Next time X happens, do Y." Event vocab in `normalize.py::normalize_trigger_name`: `on_next_spell`, `on_damaged`, `on_deal_damage`, `on_player_hit`, `on_player_move`, `on_enemy_death`, etc. |
| **Delayed payoffs** | `schedule_event` effect; `engine._tick_event_timers`; `_DELAYED_EFFECTS` card | "In N turns, do Y." |
| **Standing fields** | `aura` effect (re-fires each turn); `gravity_control` card (region pull/pin) | Ongoing per-turn emanations anchored to an entity or tile. |
| **Bonds between entities** | `create_persistent_effect` / `_SYMPATHETIC_LINK` card | Damage to one echoes onto another (one-way or mutual). |
| **Mind / allegiance** | `edit_memory`, `change_faction`, `possess`, `_FACTION_CHARM`, `_MEMORY_EDIT` cards | Rewrite what an NPC remembers, flip its side, puppet it. |
| **Polymorph / scale / maim** | `transform_entity`, `_SIZE_MODIFICATION`, `_DISFIGURE` cards | Turn a creature into something else; shrink/grow; cripple a limb. |
| **Linked doorways** | `portal_gates` card | Persistent two-tile portal (repeatable, unlike one-shot `teleport`). |
| **Transactional state** | `effects.py::apply_wild_magic_resolution` | Snapshot → validate → apply → **roll back on failure**. A one-cast checkpoint already exists. |
| **Durable world memory** | `operations.py` Stage-7 lanes | traits, place/faction/world notes, faction standing, deeds, promises. |
| **Observable mutations** | `operations.py::StateDelta`, `WildMagicOutcome.deltas` | Every mutation is recorded; replay reproduces it without the LLM. |
| **Curse enforcement** | `curses.py`; engine curse checks | Accepted resolutions are validated against mechanical curse limits before mutation. |

**Two front-ends, not one.** `main.py`/`ui.py` is a Pygame GUI; `cli.py` is a terminal
front-end; `autoplay.py` is headless. The state-surface plan's explicit non-goal — *"Do not
build GUI-only or CLI-only spell behavior"* — means a spell whose entire payload is a render
trick (flip the screen, swap the font, play a sound) is either a no-op or inconsistent
across front-ends. Those are out of scope as written; their *diegetic* cousins (limit sight
radius, write a misleading line into the shared log) are not.

---

## 2. Triage rubric

**Value (V1–V3)** — does it create memorable, systemic, emergent play that fits wild magic's
voice ("joy with teeth"), and does it compose with existing systems rather than sit as a
one-off?
- **V3** — systemic; unlocks many spells at once; emergent; reusable by future content.
- **V2** — strong individual spell, limited reuse.
- **V1** — novelty; narrow; or mostly flavor we can already fake with `message`/`add_trait`.

**Difficulty (D0–D4)** — grounded in this architecture, not a generic one.
- **D0** — *already expressible.* Needs at most a prompt example or a `normalize.py` alias. No engine code.
- **D1** — *small.* One new effect type or extend one handler/card; fits the transactional model; local blast radius.
- **D2** — *medium.* New subsystem or durable lane; touches the turn loop, AI, or environment tick, but respects GameState authority and replay.
- **D3** — *large.* Fights a core invariant (strict player-then-enemy rounds, single-room grid, front-end neutrality) and needs an architectural change (initiative queue, rolling history, Z-axis, multi-map).
- **D4** — *out of scope / anti-pattern.* Breaks replay determinism, front-end neutrality, or the fiction; or is anti-fun.

**Priority** = high V, low D. The shortlist in §4 is the V3/V2 × D0–D2 quadrant.

---

## 3. Category-by-category triage

### Cat 1 — Time & Turn Manipulation
*The richest "looks impossible, mostly isn't" category, because `schedule_event` + triggers + the per-cast snapshot already exist.*

- **Already expressible (D0–D1):** delay damage then apply (#4 → `schedule_event`), accelerate poison ticks (#16 → status-tick burst), borrow future mana (#11 → `schedule_event` mana cost + the existing **Wild Debt** curse), freeze all status durations (#14 → "stasis" flag in the tick), age an enemy (#15 → max-HP/speed debuff), anchor-and-revert-on-death (#17 → reuse the snapshot machinery + an `on_death` trigger). **V2–V3, D1.**
- **Tractable but real work (D2):** take an extra turn (#6), steal/transfer a turn (#18), 2:1 time-slow (#9) — all are action-economy and touch the turn loop but don't require a full initiative rewrite.
- **Architectural (D3):** rewind the room N turns (#1, #13), fast-forward the sim (#5), split-timeline pick-best (#8), initiative swap (#10) — need a rolling state-history ring buffer or a real initiative queue. #1 is the flagship "big swing" (sketch in §5.F).
- **Verdict:** **highest-yield category.** Do the D1 cluster first (§5.A).

### Cat 2 — Space & Map Geometry
- **Already expressible (D0–D1):** wormhole/edge portals (#27, #30 → `portal_gates`), compress distance / fold corridor (#24 → `teleport` or a portal), void tile (#35 → `set_tile` to a blocking tile), grow perimeter floor (#39 → `create_tiles`), conveyor/shifting-sand/tilt (#26, #34, #40 → flow-field tiles, §5.D).
- **Architectural (D3):** rotate/fold/mirror the whole room (#21, #22, #153), toroidal wrap (#23), dynamic grid resize/insertion (#25, #32), spherical/non-Euclidean grids (#33), global distance-metric swap (#36) — these touch the grid model itself and every consumer of coordinates (FOV, pathfinding, rendering on both front-ends). Low ROI for the blast radius.
- **Verdict:** cherry-pick the portal/flow-field spells (V2, D1–D2); leave whole-room geometry alone.

### Cat 3 — Faction Dynamics & Social Manipulation
*Second-richest, because `change_faction`, `edit_memory`, `possess`, `faction_charm`, and `sympathetic_link` already exist.*

- **Already expressible (D0–D1):** soul-bind damage split (#41 → `sympathetic_link`), charm/defend (#42 → `faction_charm`), faction inversion (#47 → `change_faction` over a group), puppet-mimic (#46 → `possess`), wipe ally memory (#54 → `edit_memory`), believe-it's-the-shopkeeper / long-lost-sibling (#49, #60 → `edit_memory` + dialogue).
- **New behavior traits (D2, §5.C):** coward-flees-from-blood (#48), duel-lock two enemies (#58), target-lowest-HP (#51), forced dance — move-but-not-attack (#55), mimic-my-movement (#46 alt). These need an AI-readable **behavior-modifier status family**, one new system that unlocks ~8 spells.
- **Group/event coordination (D2–D3):** chain-of-inheritance on death (#50), collective-guilt reaction (#59), class-consciousness minion revolt (#57), delayed conspiracy (#52) — mostly `on_enemy_death`/`on_damaged` triggers scoped to groups plus stat-inheritance handlers.
- **Verdict:** the behavior-trait system (§5.C) is the unlock; the rest is prompt examples on existing cards.

### Cat 4 — Interface & Player Perception
- **D4 (out of scope as written):** flip screen (#62), grayscale/blur/shaders (#64, #70), comic sans (#75), four mirrored viewports (#73), magnifying glass (#80), mouse cobwebs (#67) — Pygame-only render tricks that are no-ops in CLI/headless and violate front-end neutrality.
- **D1 (diegetic versions worth keeping):** hide the map / sight radius → 0 (#72 → temporary FOV-radius status, front-end-agnostic), fake log messages (#66 → push spoofed lines into the shared message log), scramble HUD numbers as *state* not render (#61, partial), show enemy planned paths (#78 → expose existing pathfinding to context/log).
- **Verdict:** implement the **sight-radius status** and the **deceptive-log** primitive (both V2, D1); reject the render-only ones. Note the rejection explicitly so the resolver can down-convert ("flip the screen" → a brief confusion/disorient status).

### Cat 5 — Meta-Magic & LLM Interface Control
- **D4 (reject):** disable fallbacks / crash on parse fail (#86 — deliberately breaks the safety net), multimodal screenshot prompt (#96), parse the audit log at runtime (#93), dual-LLM averaging (#99), runtime provider/model swap (#90, #92) — these make spell *outcomes* depend on LLM internals, which breaks replay determinism (the whole point of recording deltas at the apply point).
- **D1 (cheap, harmless, fun):** pirate-voice / "only words starting with S" (#84, #95 → transient style string spliced into `_wild_prompt_messages`), lock to a category (#100 → constrain `select_cards`), force-curse-outcome (#98 → bias the schema/prompt), temperature override for the next cast (#88 → per-cast option, already plumbed via `config`).
- **Verdict:** a small **"meta-modifier" status** that decorates the *next* cast's prompt (voice, constraint, card lock) is V1–V2, D1 and on-theme. Anything that touches determinism or the fallback safety net is D4.

### Cat 6 — Dynamic Item & Economy
- **Already expressible (D0–D1):** transform/merge items (#101 → `transform_item`), disenchant prop → mana (#105 → `transform_item` + `restore_mana`), transient duplicate (#106 → `conjure_item` + `schedule_event` removal), reflect-spell shield (#118 → `create_persistent_effect` ward), trail of coins on move (#111 → `on_player_move` trigger + `spawn_item`), swap inventories (#112 → `modify_inventory` both ways), food→potions (#114 → `transform_item` filter), kill-memory weapon buff (#120 → `add_trait` + small mechanical rider).
- **D2:** sentient commenting weapon (#102 → item-owned `on_deal_damage` trigger that emits `message`), bank/debt ledger (#103 → reuse Wild Debt + a scheduled drain), weightless/levitating items (#110, #119 → only if a weight system exists; it currently doesn't, so this is "invent a system" — defer).
- **Verdict:** most of this is prompt examples on `transform_item`/`modify_inventory`/triggers. The sentient-item-trigger pattern (V2, D2) is a nice reusable unlock that overlaps §5.B.

### Cat 7 — Esoteric Physics & Fluid Dynamics
- **Already expressible (D1–D2):** gravity vector / black hole / magnetize (#121, #128, #134 → `gravity_control` region field + flow-field §5.D), billiard/frictionless momentum (#129, #131 → push with travel-until-wall), geyser toss (#140 → push + brief stun, no real Z-axis).
- **D2 (cellular automata):** thermal conduction (#126), vacuum/oxygen (#127), combustible air chain reaction (#132) — a per-turn neighbor-propagation pass over tiles. This is a real but self-contained subsystem; the existing `_tick_fire_spread`/`_tick_poison_spread` are a working template to generalize.
- **D3:** raycasting mirror beam (#125), light refraction decoupling visual/physics position (#137), true Z-axis (#140 done properly) — defer.
- **Verdict:** flow fields (§5.D) cover the gravity/wind/conveyor spells; a generalized **tile-reaction automaton** (extend the fire/poison spread we already have) is the medium-term V2 prize.

### Cat 8 — Dungeon Architecture & World-State Rules
- **Already expressible (D1):** seal stairs (#142 → flag on descend/ascend), cave-in random walls (#145 → `create_tiles` wall with reachability guard — reuse `_floor_reachable`), shrinking lava ring (#155 → scheduled `create_tiles` expansion), spawn a shopkeeper room (#148 → reuse `_build_*_structure` + spawn NPC), labyrinth of temporary walls (#159 → `create_tiles` + duration).
- **D2:** retheme the level (#141 → `RoomProfile` retheme + prop regen), spawn-table override (#149 → mutate the floor's spawn weights), day/night vision swing (#146 → the day/night clock already exists in `engine.py`; hook a global vision modifier), ghost-on-death (#152 → `on_enemy_death` global trigger).
- **D3:** carve a new corridor between dead ends at runtime (#143, #150), merge two levels via floor holes (#160) — runtime maze-solving + multi-level coordinates; defer.
- **Verdict:** the "seal/collapse/shrinking-hazard/spawn-a-room" set is a tidy V2, D1–D2 batch that reuses generation helpers.

### Cat 9 — Complex Conditionals & Logic Gates
*Effectively "trigger system v2." The hooks exist; this is mostly new **conditions** and a few new **event types**.*

- **Already expressible (D0–D1):** lethal-damage swap (#161 → `on_player_hit` + threshold), every-third-step explosion (#169 → `on_player_move` + a step counter on the player), vision-entry freeze (#170 → new `on_enters_sight` event), chain-reaction on death (#166 → `on_enemy_death` + adjacency scan), redirect next curse (#178 → `on_curse_gained` event), empty-inventory damage buff (#171 → condition predicate), free third identical cast (#179 → spell-history on `on_next_spell`).
- **D1–D2:** mana-shield redirection (#167 → damage-application hook), HP-parity math (#162 → arithmetic condition predicate), XOR floor trap (#163 → two-entity tile predicate), terrain-conditional spell modifiers (#168), enemy-mirrors-my-spell (#164 → needs enemy spellcasting, D3).
- **Verdict:** **co-flagship with Cat 1.** A modest **condition-predicate language + 3–4 new event hooks** (§5.B) unlocks a dozen of the most "designed-feeling" spells.

### Cat 10 — Sensory, Audio & Visual
- **D4 (reject):** audio triggers (#182, #192), window pulse/zoom (#188), particles/shaders/silhouettes (#187, #191, #197), font/glyph dance (#193) — render/audio-only, front-end-specific.
- **D1–D2 (diegetic, worth keeping):** scent trail that draws enemies (#181 → `on_player_move` writes a decaying lure field the AI reads — overlaps the flow-field work), footstep noise wakes sleepers (#195 → noise + sleep state, real mechanic), paint/reveal tiles on walk (#196 → persistent "revealed" flag on tiles), blackout = sight radius down (#185, #198 → the §5.D / Cat-4 sight-radius status).
- **Verdict:** everything *mechanical* here collapses into two systems already on the shortlist (sight-radius status, lure/flow field). The rest is D4.

---

## 4. Priority shortlist (build order)

Ranked by value ÷ difficulty, biased toward systems that unlock many spells at once:

| # | Cluster | Unlocks (examples) | V | D | Plan |
|---|---|---|---|---|---|
| 1 | **Delayed & accelerated effects** | #4, #11, #16, future-debt, scheduled hazards | V3 | D1 | §5.A |
| 2 | **Conditionals v2** (predicates + new events) | #161, #166, #169, #170, #178, #179, XOR/parity | V3 | D1–D2 | §5.B |
| 3 | **AI behavior-modifier statuses** | #48, #51, #55, #58, dance/duel/coward/lowest-HP | V3 | D2 | §5.C |
| 4 | **Environmental flow fields** | #26, #34, #40, #121, #134, scent #181 | V2 | D2 | §5.D |
| 5 | **Quick D1 wins bundle** | age (#15), sight-shroud (#72/#185), extra-turn (#6), seal/collapse (#142/#145) | V2 | D1 | §5.E |
| ★ | **Room rewind** (big swing, optional) | #1, #13, #17 | V3 | D3 | §5.F |

Do **1 → 2** first: they share the trigger/schedule plumbing and together cover the
single largest count of listed spells with the least new surface area. **3** and **4** are
the next tier (each is one new subsystem). **5** is opportunistic polish that can land
alongside any of the above. **★** is a standalone research spike — high value, but it earns
its own milestone because it touches replay determinism.

---

## 5. Implementation plans for the highest-priority clusters

Every plan below must honor the same invariants (they are not repeated per plan):

- **Authority:** the LLM emits JSON; the engine binds refs (`refs.py`), validates
  (`spell_contract.validate_resolution`), applies through `_apply_effect`/`operations.py`,
  and records `StateDelta`s. The model never mutates state.
- **Transactional:** new effects participate in the existing snapshot/rollback in
  `apply_wild_magic_resolution`. A scheduled/triggered payload that fails later must fail
  cleanly, not half-apply.
- **Replay:** anything stored on `GameState` must serialize (it already round-trips via
  `to_replay`/`from_replay`) and reproduce without provider calls. No new mechanic may read
  the LLM at fire time unless the verdict is recorded at the apply point (the deed pattern).
- **Curses:** new effect families should be nameable in the curse "forbidden families" check
  in `curses.py` so curses can still constrain them.
- **Per change, run:** `ruff check`, `py_compile` sweep, `python -m wildmagic.smoke_test`,
  `pytest -q`, and a `--provider mock` CLI playtest (pattern in the state-surface plan).

---

### 5.A — Delayed & accelerated effects  *(V3, D1)*

**Goal:** spells that pay off later, accelerate existing timers, or borrow from the future.
Unlocks #4 (delay damage), #11 (future mana), #16 (accelerate poison), scheduled hazards
(#155 lava ring), and is the substrate for #2's timed payoffs.

**What exists:** `schedule_event` + `engine._tick_event_timers` + the `_DELAYED_EFFECTS`
card already run an effects array "in N turns." The **Wild Debt** semantic curse template in
`curses.py` already models "borrow now, pay later."

**Changes:**
1. **`delay_incoming` effect (new) — the delayed-damage buffer (#4).** A status-like marker
   on an entity: damage routed to it is captured into a buffer instead of applied, and a
   `schedule_event` releases the sum after N turns. Implement as: `add_status delayed_sink`
   read in `combat.damage_entity` (divert to `entity.details["delayed_damage"]`), plus a
   scheduled event that applies the accumulated total and clears the marker. Small hook in
   the one chokepoint (`damage_entity`) keeps it transactional and replay-safe.
2. **`accelerate_status` effect (new) — burst remaining ticks (#16).** Reads a target's
   status (e.g. `poisoned`), computes remaining ticks × per-tick damage from the existing
   poison logic, applies it at once via `operations.apply_damage`, and clears the status.
   Pure reuse of existing tick math; no new tick loop.
3. **Future-mana debt (#11) — no new effect.** Resolve as a `schedule_event` whose payload
   is a `mana` cost, plus an `add_curse` of the existing **Wild Debt** template for flavor and
   so it shows in the curse UI. This is a **prompt example + normalizer alias**, not code,
   once #1 lands the "scheduled cost" path (schedule_event today carries effects; allow it to
   carry a `costs` array too — a one-line extension to the handler).
4. **Freeze all durations (#14)** — a `stasis` flag checked at the top of
   `_tick_simple_statuses`/`_tick_tile_durations`/`_tick_event_timers` to skip decrements for
   N turns. One guard in three tick functions.

**Touchpoints:** `spell_contract.SUPPORTED_EFFECTS` (+`delay_incoming`, `accelerate_status`),
`effect_registry.py` (two `EffectSpec`s, owned by `_DELAYED_EFFECTS`), `effects._apply_effect`
(two handlers; extend the `schedule_event` handler for `costs`), `combat.damage_entity` (sink
divert), `engine` tick functions (stasis guard), `resolution_parsing.py` (aliases:
"delay/postpone damage", "speed up the poison"), `capabilities._DELAYED_EFFECTS` (mechanics
text + examples).

**Tests:** scheduled release applies the summed damage on the right turn and rolls back if
the cast fails; `accelerate_status` on a 3-tick poison deals exactly 3× per-tick and removes
the status; stasis skips exactly N decrements; replay reproduces a delayed payoff with the
mock provider.

---

### 5.B — Conditionals v2: predicate language + new event hooks  *(V3, D1–D2)*

**Goal:** turn the existing single-shot trigger system into a small but expressive
"if-this-then-that" language. Unlocks #161 (lethal swap), #166 (death chain), #169 (every-Nth
step), #170 (on-sight freeze), #178 (curse redirect), #179 (free repeat cast), #162/#171
(state predicates).

**What exists:** `create_trigger` stores a condition name + an effects array; the engine
fires by event name (`_fire_triggers`, `_fire_damage_triggers`, `_fire_death_triggers`) and
already matches by `target`/tag (`_trigger_matches`). Events: `on_next_spell`, `on_damaged`,
`on_deal_damage`, `on_player_hit`, `on_player_move`, `on_enemy_death`.

**Changes:**
1. **A tiny condition predicate, optional on any trigger.** Add a `when` object to the
   trigger schema, evaluated by a new pure function `evaluate_condition(engine, when, event)`
   in a new `conditions.py` leaf (imports only `state_view`/`models`, like `refs.py`).
   Start with a closed vocab: `hp_below`/`hp_above` (ratio or absolute), `hp_parity`
   (odd/even, #162), `count_visible` (enemies/allies > N, #165), `inventory_empty` (#171),
   `on_terrain` (caster standing on tile tag, #168), `step_multiple` (#169), `same_spell_streak`
   (#179). Predicates read only the state surface — **never** the LLM — so they replay.
2. **New event hooks (engine fire points):**
   - `on_enters_sight` — fired from `_update_npc_perceptions`/`update_fov` when an entity
     newly enters the player's FOV (#170, #182-diegetic).
   - `on_curse_gained` — fired in `_apply_cost`'s curse branch (#178: redirect the curse to
     the nearest enemy instead).
   - `on_lethal_damage` — fired in `combat.damage_entity` *before* death is committed when a
     hit would reduce HP ≤ 0, with a chance for a trigger to intercede (#161 swap-and-give,
     #17 anchor-revert). This is the highest-value hook and the only D2 one (it must be
     careful about ordering vs. `_on_entity_death`).
3. **Step counter** on the player (`GameState`) incremented in `attempt_player_move`, read by
   `step_multiple`. Serializes for free.
4. **Death-chain / adjacency** (#166): a trigger whose `on_enemy_death` payload re-scans
   adjacency and applies `area_damage` to same-type neighbors. The recursion guard is a
   visited-set in the fire pass so a chain terminates.

**Touchpoints:** new `wildmagic/conditions.py`; `effects.py` `create_trigger` handler (accept
`when`); `engine.py` new fire points + `_trigger_matches` calling `evaluate_condition`;
`combat.damage_entity` (`on_lethal_damage`); `normalize.py` (new trigger-name aliases);
`capabilities._TRIGGERS_REACTIONS` (document conditions + examples); `effect_registry`.

**Tests:** each predicate true/false path; `on_lethal_damage` can both intercept (swap,
prevent death) and pass through; death-chain terminates and respects the visited-set;
`on_enters_sight` fires once per entry not per turn; everything round-trips under replay.

**Risk:** `on_lethal_damage` ordering. Keep death committal in one place
(`_on_entity_death`) and let the hook return a "handled/prevented" verdict before it runs.

---

### 5.C — AI behavior-modifier statuses  *(V3, D2)*

**Goal:** statuses that change *how* an NPC decides, not just whether it can act. Unlocks #48
(coward flees from blood), #51 (target lowest HP), #55 (forced dance: move, no attack), #58
(duel-lock), #46 (mimic the caster), #53 (existential freeze).

**What exists:** `_AIMixin` (`ai.py`) computes targets and moves: `_select_target`,
`_behavior_targets`, `_enemy_single_action`, `next_path_step`, `_flee_step`. Statuses already
gate action (`frozen`, `webbed`, `confused`). `change_faction`/`possess` already exist for the
allegiance-level cases.

**Changes:**
1. **A `behavior` status family** — values carried in `entity.statuses` (e.g.
   `behavior:coward`, `behavior:duel:<id>`, `behavior:dance`, `behavior:lowest_hp`). Reuses
   the existing status duration/serialization; no new storage.
2. **AI read points** (the whole change is localized to `ai.py`):
   - `_select_target`: if `behavior:lowest_hp`, pick the lowest-HP visible entity regardless
     of faction (#51); if `behavior:duel:<id>`, lock to that id and ignore the player (#58).
   - `_enemy_single_action`: if `behavior:dance`, allow `next_path_step` movement but suppress
     the attack branch (#55); if `behavior:coward` and blood/`bleeding` is visible, force
     `_flee_step` (#48); if `behavior:freeze_dread`, no-op the turn (#53).
   - `behavior:mimic:<id>`: mirror the referenced entity's last move vector (#46) — store the
     last move delta on the entity (cheap) and apply its inverse/copy.
3. **Resolver surface:** these are emitted as `add_status` with a `behavior` key, or via the
   `_FACTION_CHARM`/`_MEMORY_EDIT` cards which already own social mechanics. Add the behavior
   vocab to those cards' mechanics text.

**Touchpoints:** `ai.py` (read points); `models.MECHANICAL_STATUSES` (register the family so
ticking/validation accepts it); `spell_contract`/`normalize` (accept `behavior:*` status
keys); `capabilities` (card text); `effect_registry` if a dedicated `set_behavior` effect is
preferred over overloading `add_status` (recommended for clarity).

**Tests:** a `behavior:dance` enemy moves but never attacks across N turns; `lowest_hp`
retargets when HP order changes; `duel` ignores an in-range player; coward flees only while
blood is visible; all expire correctly and serialize.

**Design note:** keep these as *statuses with durations*, not permanent rewrites, so they fit
the wild-magic "strange, temporary, costed" frame and clear naturally.

---

### 5.D — Environmental flow fields  *(V2, D2)*

**Goal:** terrain that imparts motion each turn — conveyors, tilt, shifting sand, wind,
gravity wells, scent lures. Unlocks #26, #34, #40 (slide/tilt/sand), #121/#122 (gravity/wind
vector), #134 (black hole), #181 (scent trail the AI follows).

**What exists:** the `aura` effect is already a per-turn standing emanation; `gravity_control`
card already promises a region pull/pin field; `_tick_environment` already iterates tiles each
turn; `push_entity` already moves entities along a vector with wall stops.

**Changes:**
1. **A `flow` tile property** — a per-tile (dx, dy) drift vector + optional duration, stored
   in the tile-aura/duration structures `set_tile` already manages. A `create_flow` effect (or
   an `aura kind:"flow"`) writes a region of drift vectors.
2. **A drift pass in `_tick_environment`** — for each entity standing on a flow tile, attempt
   a `push_entity` of one step along the vector (respecting blockers, the same way fire/poison
   spread already respects the grid). Order entities deterministically (by id) so replay is
   stable.
3. **Gravity well (#134) as a radial flow** — a standing field anchored at a tile whose drift
   vector at each point points inward; reuse the `aura` radius machinery to stamp the field.
4. **Scent/lure field (#181)** — the *same* structure, but read by `ai.py` pathing instead of
   moving the entity: a decaying scalar the enemy pathing is biased toward. This is why #181,
   #195 (noise wakes sleepers), and #134 collapse into one subsystem.

**Touchpoints:** `models.py` (tile flow field on the tile-duration/aura store), `effects.py`
(`create_flow` handler or `aura kind:"flow"`), `engine._tick_environment` (drift pass),
`ai.py` (optional lure bias), `capabilities._GRAVITY_CONTROL` (extend its mechanics to name
flow), `effect_registry`.

**Tests:** an entity on a north-conveyor moves one tile north per turn until it hits a wall;
a gravity well pulls entities one ring inward per turn; drift order is deterministic under a
fixed seed; flow expires; replay reproduces positions exactly.

**Risk:** interaction with player agency — a conveyor that moves the player must not eat the
player's turn silently. Surface it in the log and let it resolve on `finish_player_turn`.

---

### 5.E — Quick D1 wins bundle  *(V2, D1)*

Independent small spells that each reuse one existing system; land opportunistically.

- **Age an enemy (#15):** `transform_entity`/`_DISFIGURE`-adjacent — reduce `max_hp` (clamp
  current HP) and apply `slowed`. No new system; possibly just a prompt example + a
  `max_health`-debuff path mirroring the existing `max_health` *cost*.
- **Sight-shroud (#72, #185, #198):** an `add_status sight_radius:N` read by `update_fov`
  when computing the player's view radius. Front-end-agnostic (changes FOV, not rendering),
  so it works identically in Pygame, CLI, and headless. Also covers the "blackout/blindness"
  diegetic versions from Cat 4/10.
- **Deceptive log line (#66):** a `message` variant flagged `spoof:true` that the engine
  writes verbatim into the shared log without a backing mutation — clearly bounded, and the
  resolver already controls `message`.
- **Seal stairs (#142) / cave-in (#145):** a `seal_stairs` flag checked in
  `descend_stairs`/`ascend_stairs`, and a `create_tiles wall` cave-in that runs the existing
  `_floor_reachable` guard so it can never trap the player.

These are mostly prompt/normalizer work plus a few-line handler each.

---

### 5.F — Room rewind  *(V3, D3 — optional flagship)*

**Goal:** #1 "rewind the room N turns," #13 "clocks tick backward," and the durable form of
#17 "anchor and revert on death."

**Why it's D3:** there is a *per-cast* snapshot in `apply_wild_magic_resolution`, but no
*rolling* history of prior turns, and naive full-state snapshots every turn are expensive and
risk replay drift.

**Feasible approach (spike, don't commit blind):**
1. **Bounded snapshot ring.** `GameState` already serializes cleanly (`to_replay`). Keep a
   ring buffer of the last K (e.g. 3–5) end-of-turn serialized snapshots, zone-scoped, cleared
   on zone/stair transitions (positions become meaningless across zones — the engine already
   clears the target mark on transition for the same reason).
2. **Rewind = restore snapshot N back**, then re-emit a `StateDelta` summary so the UI/log and
   replay see one coherent "rewound" event rather than a desync. Player inventory and the
   deed/legend ledgers need an explicit policy (probably: rewind tiles/positions/HP, but
   **keep** deeds/legend so the world's memory of what you did is not also erased — otherwise
   rewind becomes a consequence-eraser, which fights the emergent-world design).
3. **Replay:** record the rewind as a single applied event with the restored snapshot id, so
   replay re-applies the restore deterministically without recomputing history.

**Cost/benefit:** memory and serialization cost per turn, plus a subtle determinism surface.
Worth a milestone of its own *if* time-magic becomes a signature pillar; otherwise the D1
anchor-revert (#17, via §5.B `on_lethal_damage` + the existing snapshot) delivers ~70% of the
fantasy for ~10% of the cost. **Recommendation: ship #17 in §5.B; defer full rewind.**

---

## 6. Explicitly out of scope (and why)

Surface these to the resolver as **down-convert** guidance, not hard rejections — when a
player asks for one of these, the resolver should resolve the nearest in-engine analogue
rather than refuse.

| Spells | Why out | Down-convert to |
|---|---|---|
| Flip screen, grayscale/blur, comic sans, viewports, magnifier, mouse cobwebs (#62,#64,#70,#73,#75,#80,#67) | Pygame-only render tricks; no-ops in CLI/headless; violate front-end neutrality | a brief `confused`/disorient status; sight-shroud |
| Audio/jump-scare/window-pulse/particles/shaders (#69,#182,#187,#188,#192,#197) | Render/audio-only; front-end-specific | `message` flavor + a status |
| Disable fallbacks, multimodal screenshot, runtime provider/model swap, dual-LLM average, parse audit log live (#86,#90,#92,#96,#99,#93) | Make outcomes depend on LLM internals → break replay determinism and the safety net | reject, or resolve as ordinary high-variance wild magic |
| Whole-room rotate/fold/mirror, toroidal/spherical grids, dynamic resize, global distance-metric swap (#21,#22,#23,#25,#32,#33,#36,#153) | Touch the grid model + every coordinate consumer (FOV, pathing, two renderers); low ROI | localized `teleport`/`portal_gates`/flow fields |
| True Z-axis, raycast mirror beam, refraction position-decoupling, weightless-item economy (#125,#137,#110,#119) | Require systems the engine doesn't have (vertical space, weight) | push/stun analogues; defer |
| Morse-code input, type-to-continue gates, "press Y" chest jokes (#79,#113,#154) | Input-layer reskins; break headless/autoplay and the command grammar | flavor `message` only |

---

## 7. Recommended sequencing

1. **Milestone A — Time & Conditionals (§5.A + §5.B).** Shared schedule/trigger plumbing;
   biggest spell-count payoff per unit work; includes the D1 anchor-revert (#17).
2. **Milestone B — Social & Behavior (§5.C).** One subsystem in `ai.py`; unlocks the faction
   category's "designed" spells.
3. **Milestone C — Environment (§5.D).** Flow fields; folds in the diegetic scent/noise spells
   from Cats 7/10.
4. **Continuous — §5.E** wins land alongside A–C as small PRs.
5. **Optional spike — §5.F** room rewind, only if time-magic becomes a pillar.

Each milestone follows the state-surface plan's discipline: new effects get an `EffectSpec`,
a capability-card home, a schema enum entry, normalizer aliases, drift tests, and a mock-CLI
playtest, with the audit log inspected after at least one live-provider run.
