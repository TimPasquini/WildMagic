# Wild Magic Execution Plan

This plan grows Wild Magic from the current playable prototype into a richer roguelike sandbox where typed spells can alter many kinds of game state without making the run fragile or impossible to test.

The central design rule is:

> The LLM may propose magical consequences, but the engine remains authoritative.

That means wild magic should feel expansive, strange, and sometimes dangerous, while the game engine validates every change, preserves turn rules, records what happened, and keeps the game playable through both the graphical UI and a headless testing interface.

## Current Baseline

The prototype currently includes:

- A graphical ASCII roguelike window using Pygame.
- A visible dungeon map with rooms, corridors, enemies, items, and walls.
- Turn-based movement, waiting, bump combat, enemy turns, HP, mana, and simple pickup.
- A standard spark bolt spell.
- A wild spell input panel.
- A wild magic resolver that tries Ollama first and falls back to a deterministic mock resolver.
- A JSON response contract with effects for damage, healing, teleporting, tile changes, statuses, summoning, item spawning, messages, and costs.
- Costs in mana, health, inventory items, and curses.
- A smoke test that can exercise the game engine without opening the UI.
- A headless command/session layer shared by tests, CLI, replay, and the graphical UI.
- A terminal CLI for agent-playable runs.
- Replay recording and deterministic replay verification.
- A fixed `test_chamber` scenario for repeatable debugging.
- A richer wild-magic operation surface covering area effects, terrain, movement, statuses, inventory changes, transformations, factions, tags, resistances, world flags, and delayed events.
- Field of view and explored-map tracking in both the graphical UI and CLI.
- Enemy pathfinding through corridors instead of purely greedy movement.
- Closed/open doors, downward/upward stairs, dungeon depth, and floor transitions.
- Template-backed wild-magic conjuration for arbitrary named items and creatures.
- Wild-magic audit logging for every live prompt, raw response, parsed resolution, and technical failure.
- NPC dialogue, trade negotiation, and LLM-generated towns (beyond the original plan scope).

## Phase Status (June 2026)

- **Phase 1 (headless harness, replays): complete.**
- **Phase 2 (state schema, validation): mostly complete.** Transactions, `validate_state`, and replay serialization exist. Versioned save/load snapshots are not built — replays are currently the only persistence.
- **Phase 3 (core roguelike depth): complete.**
- **Phase 4 (elemental/material simulation): complete.**
- **Phase 5 (statuses, curses): partial.** Statuses are done. Curses exist as stored costs with stacks, but the mechanical hooks (modified spell costs, altered enemy behavior, FOV changes, periodic events) are not implemented.
- **Phase 6 (items, crafting, rituals): partial.** Item categories, materials, tags, and transformation operations exist. Crafting and ritual recipes are not implemented.
- **Phase 7 (factions, memory, world consequences): largely complete.** Factions, world flags, event timers, triggers, and ally/summon AI exist. Dialogue, trade, and town generation went beyond the original scope.
- **Phases 8+ : not started.** Rewritten below based on the June 2026 strategic review.
- **Phases 13–16: added mid-June 2026** (regions as first-class data, the Empire as a
  system, origins, within-run rumors). Phase 13 (regions) is scheduled first; two
  cross-cutting decisions from the same review: casting waits are presentation-only (no
  game time passes), and there is no cross-run persistence of any kind.

## Implementation Principles

### Engine First

Core game behavior should live in pure Python engine code, separate from rendering. The UI should call the same action API that tests and playtest scripts use.

### Structured Wild Magic

The LLM should receive a compact game-state summary and return structured JSON. The engine should validate, normalize, and apply the result. Invalid JSON or malformed effects are technical failures and should not consume a turn.

### Rich State, Small Operations

The game state should become broad and expressive, but wild magic should operate through a controlled set of operations: damage, heal, move, teleport, transform tile, transform item, add status, add curse, spawn entity, change faction, set flag, start timer, etc. etc. etc.

### Replayability

Every run should be reproducible from seed plus action log. Every wild magic resolution should be logged after parsing so a run can be replayed without asking the LLM again.

### Agent-Playable Testing

Codex should be able to play the game through a headless command interface. Graphical UI testing is useful, but the project should not depend on manual visual play for debugging.

## Phase 1: Headless Play Harness And Replays

Goal: make the game fully playable and testable without the Pygame UI.

### Features

- Add a command/action API around the engine:
  - `move north`
  - `move south`
  - `move east`
  - `move west`
  - `wait`
  - `standard_spell spark_bolt`
  - `cast "set the goblin on fire"`
  - `inspect`
- Add a CLI runner:
  - `python -m wildmagic.cli`
  - It should print a compact ASCII map, stats, inventory, curses, and log.
  - It should accept typed commands from stdin.
- Add deterministic scenario setup:
  - fixed map
  - fixed player position
  - fixed enemy positions
  - fixed inventory
  - fixed seed
- Add replay files:
  - seed
  - action list
  - wild magic provider mode
  - parsed wild magic JSON results
- Add replay runner:
  - `python -m wildmagic.replay path/to/replay.json`
- Add mock-provider replay mode so tests do not require Ollama.

### Acceptance Criteria

- A full short run can be played from the terminal.
- A replay can reproduce the same final state from the same seed and actions.
- A technical LLM failure does not consume a turn.
- A rejected overpowered spell does consume a turn.
- Codex can run scripted commands to move, fight, cast, inspect, and verify state.

## Phase 2: Stable State Schema And Validation

Goal: make the game state broad enough for wild magic while remaining safe to mutate.

### Features

- Introduce a versioned save-state schema.
- Add JSON save/load snapshots.
- Separate entity data into clearer component-like sections:
  - identity
  - position
  - combat
  - magic
  - inventory
  - AI
  - statuses
  - tags
- Add map tile metadata:
  - glyph
  - name
  - blocks movement
  - blocks sight
  - tags
  - duration if temporary
- Add a validation pass for the whole game state:
  - player exists
  - entities are in bounds
  - blocking entities do not overlap
  - HP and mana are clamped
  - dead actors do not act
  - inventory quantities are nonnegative
  - curses have valid IDs
- Add wild magic transaction behavior:
  - parse JSON
  - validate all effects and costs
  - normalize targets and values
  - apply effects
  - apply costs
  - advance turn
  - validate final state

### Acceptance Criteria

- The engine can save and load a run.
- Every player action can optionally validate the game state afterward.
- Bad wild magic JSON cannot partially corrupt the state.
- A failed transaction is logged and does not consume a turn unless it was an intentional spell rejection.

## Phase 3: Core Roguelike Depth

Goal: make the game enjoyable even when the player ignores wild magic.

### Features

- Field of view and explored tiles. [done]
- Pathfinding for enemies. [done]
- Doors and stairs. [done]
- Multiple dungeon floors. [done]
- Locked doors and traps. [done]
- More enemy types with different behaviors: [done]
  - melee pursuer
  - ranged caster
  - fleeing scavenger
  - stationary hazard
  - summoner
- Real equipment: [done]
  - weapon slot
  - armor slot
  - charm slot
  - carried inventory
- Consumables: [done]
  - healing potion
  - mana potion
  - smoke vial
  - blink scroll
- Deterministic standard spells: [done]
  - spark bolt
  - ward
  - minor heal
  - frost shard
  - reveal

### Acceptance Criteria

- A player can clear a small dungeon using only normal movement, equipment, items, and standard spells.
- Enemies can navigate around walls.
- Field of view affects what the player can see.
- Standard spells are deterministic and covered by tests.

## Phase 4: Elemental And Material Simulation

Goal: give wild magic many engine-native things to manipulate.

### Features

- Damage types:
  - physical
  - fire
  - frost
  - lightning
  - poison
  - acid
  - force
  - radiant
  - shadow
  - psychic
  - arcane
- Entity resistances and weaknesses.
- Tile tags:
  - flammable
  - wet
  - frozen
  - conductive
  - holy
  - cursed
  - brittle
  - slippery
  - poisonous
- Environmental reactions:
  - fire spreads to flammable tiles [done]
  - water conducts lightning [done]
  - ice melts into water [done]
  - frost freezes water [done]
  - acid weakens walls [done]
  - force can push entities [done]
  - radiant harms undead [done]
  - shadow harms light sources [done]
- Temporary terrain:
  - fire patches
  - poison clouds
  - ice walls
  - fog
  - vines

### Acceptance Criteria

- Wild magic can create and transform terrain in ways that affect later turns.
- Elemental interactions happen through engine rules, not LLM narration alone.
- Tests cover at least five environmental reactions.

## Phase 5: Statuses, Curses, Blessings, And Mutations

Goal: make consequences mechanically strange and memorable.

### Features

- Expand temporary statuses:
  - burning
  - frozen
  - stunned
  - slowed
  - hasted
  - silenced
  - invisible
  - confused
  - frightened
  - poisoned
  - bleeding
  - marked
- Add permanent or semi-permanent consequences:
  - curse
  - blessing
  - mutation
  - oath
  - debt
  - omen
- Add mechanical hooks for curses:
  - modifies spell costs
  - alters enemy behavior
  - changes FOV
  - periodically triggers events
  - changes inventory behavior
  - attracts a faction
- Add curse stack handling and curse removal.

### Acceptance Criteria

- Wild magic can create a permanent curse with a real mechanical effect.
- Statuses tick down consistently at turn boundaries.
- Curses can be inspected in both UI and CLI.
- At least ten statuses have tests for application and expiration.

## Phase 6: Items, Materials, Crafting, And Rituals

Goal: provide a rich substrate for spells, costs, and transformations.

### Features

- Item categories:
  - weapons
  - armor
  - charms
  - potions
  - scrolls
  - reagents
  - food
  - keys
  - artifacts
- Material tags:
  - iron
  - silver
  - glass
  - bone
  - wood
  - cloth
  - crystal
  - salt
  - blood
  - ash
- Item tags:
  - cursed
  - blessed
  - fragile
  - volatile
  - flammable
  - conductive
  - edible
  - ritual
- Item transformation operations:
  - change material
  - add tag
  - remove tag
  - split stack
  - merge stack
  - enchant
  - curse
  - animate
- Simple crafting and ritual recipes.

### Acceptance Criteria

- Wild magic can consume, transform, spawn, enchant, curse, or animate items.
- Inventory and equipment are represented in save files.
- Item tags affect gameplay.

## Phase 7: Factions, Memory, And World Consequences

Goal: let wild magic permanently change the social and metaphysical state of the run.

### Features

- Factions:
  - player
  - beasts
  - goblins
  - undead
  - cultists
  - spirits
  - constructs
  - dungeon
- Faction relationships:
  - hostile
  - neutral
  - afraid
  - friendly
  - bound
- World flags:
  - moon_noticed_player
  - goblins_fear_fire
  - mirrors_are_hungry
  - doors_whisper_names
- Event timers:
  - delayed curse trigger
  - summoned hunter arrival
  - room transformation
  - faction ambush
- Ally and summon AI.

### Acceptance Criteria

- Wild magic can change faction relationships.
- World flags can affect future generation or encounters.
- Delayed events survive save/load and replay.
- Allies can follow or fight without blocking the player into impossible states.

## Phase 8: Wild Magic Reliability And Economy

Goal: close the balance exploit surface and make every prompt change measurable, without adding LLM latency.

The JSON repair loop, provider diagnostics, and severity classification from the original Phase 8 already exist. What is missing is that **severity is decorative**: the model self-grades and nothing reads it. The fix is not a second judge-LLM pass (which would double per-cast latency on local hardware) — it is a deterministic engine-side economy.

The central principle: **severity must become mechanical.** The engine computes its own power score for every accepted resolution and enforces a cost floor. When the model under-prices a spell, the engine tops up the cost rather than rejecting or weakening the spell. Crazy overpowered spells stay legal; they just always pay.

First slices landed June 2026, driven by audit-log mining (1,989 casts): debt-flavored
`set_flag` (148× "future_debt" — the model's favorite inert escape valve) now creates a
stacking **Wild Debt** curse plus a scheduled collector instead of doing nothing, and
`create_trigger` normalization handles the malformed nested-dict/once-condition shapes
observed in the logs. The same mining fixed the priority order below: `add_status` is the
de-facto universal adapter (923 uses, 4 statuses = 85% of them), the top 10 ops cover ~97%
of usage, and the missing general primitives are `modify_stat`, `transfer`, and `dispel`.

### Features

In recommended build order:

1. **Engine power score.**
   - A pure function over the normalized effects list: total damage × targets affected, healing, summon stats × count, status disable-turns, terrain area, trigger potency.
   - Power bands map to severity: harmless, minor, moderate, major, catastrophic.
   - Reconcile model-claimed severity against the computed band and log the calibration delta to the audit log.
2. **Cost-floor economy ("the wild takes what it is owed").**
   - A matching cost-value function over the costs list (mana, health, max stats, items, statuses, curses).
   - Per-band cost floors. If a spell is under-paid, the engine appends top-up costs in escalating order: extra mana, then health, then a curse drawn from a curse table.
   - Outcome text gets a short annotation when the wild tops up the price.
   - Pre-cast warning hook for the catastrophic band ("This will have a terrible cost. Cast anyway?") without revealing the exact cost.
3. **Spell eval harness (`python -m wildmagic.speleval`).** *[built v1, June 2026]*
   - Corpus of 107 intent-tagged spells in `speleval_corpus.py`: 47 common, 45 creative
     (drawn from the compendium), 15 exploit ("deal 999999 damage", prompt injection,
     "I instantly win", literal-number requests).
   - Live mode runs each spell through the FULL pipeline (fresh deterministic
     test-chamber session per cast: resolve → validate → normalize → apply) and scores
     resolution/rejection/technical rates, hallucinated targets, exploit-leak heuristics,
     latency, and effect-type mix, broken down by kind and intent. `--json` writes the
     report; eval traffic goes to `logs/speleval/`, not the main audit log.
   - Offline mode (`--from-audit logs/wild_magic_audit.jsonl`) re-parses recorded raw
     responses under the *current* contract code and reports regressions/improvements
     against ~2k historical casts. (Caveat: a handful of historical records were rescued
     by fallbacks at the time, so the regression count slightly overcounts.)
   - Still missing: severity-vs-power-score calibration — needs item 1 (the power score).
4. **Dynamic schema tightening.**
   - Per-cast enum injection into `SPELL_RESPONSE_JSON_SCHEMA` before it is passed as the Ollama `format`: visible entity ids plus symbolic targets for `target`, actual inventory keys for item costs, and the real tile/status/template catalogs.
   - With grammar enforcement the model becomes structurally unable to hallucinate a target. This is the cheapest reliability win available for 8B-class models.
5. **Async LLM calls in the UI.**
   - Move provider calls to a worker thread; poll for the result each frame.
   - Decided (June 2026): **no game time passes while a cast resolves** — enemies do not act
     and there is no channeling mechanic. The wait is presentation only: an atmospheric
     casting overlay (wild-magic visuals, streamed thinking text) instead of a frozen frame.
   - Stop flushing accumulated input events after the call returns.
   - The engine/UI separation already exists, so this is contained to `ui.py`.
6. **Provider layer cleanup.**
   - Consolidate the triplicated Ollama `format`-fallback/retry logic into one `ollama_chat_json(payload, schema)` in `llm_client.py`.
   - Trim duplicated context fields sent per cast (`supported_effects` repeats the system prompt; `tile_legend` is static) — context length is the main local-latency lever.

### Acceptance Criteria

- The exploit corpus passes 100%: every exploit spell is either rejected or cost-topped into the correct band.
- Severity calibration (model-claimed vs engine-computed) is logged and reportable per model.
- The UI never freezes during a wild magic, dialogue, trade, or town generation call.
- The eval harness produces comparable scores for at least two models or prompt variants.
- Malformed model output still does not consume a turn.

## Phase 9: Spellbook And Wild Surges

Goal: turn one-shot LLM resolutions into a progression system, and add real stakes to casting.

This is the strategic bet. The LLM becomes a **discovery engine** rather than a per-cast oracle: a good resolution is paid for once, then becomes a learned, deterministic, instant-recast spell. This simultaneously solves latency (LLM cost per discovery, not per cast), consistency (a learned spell behaves the same on turn 80 as turn 8), mastery (experimentation becomes investment; a run's spellbook is a build), and curation (the best LLM outputs get reused instead of regenerated).

### Features

**Spellbook:**

- When a wild spell resolves and applies successfully, the player may learn it: store the normalized resolution, the player's original phrasing, a name, and a fixed (slightly discounted) cost.
- Recasting a learned spell is deterministic and makes no LLM call. Symbolic targets (`nearest_enemy`, placements) re-resolve against the current context — this already works because effects use symbolic target strings.
- Spellbook screen in UI and a `spells` command in the CLI.
- Permadeath loses the spellbook — losing a good book should hurt.
- Optional: slight drift or decay on learned spells so discovery stays alive across a long run.
- Decided (June 2026): **no cross-run meta-progression** — discoveries die with the run.

**Casting check and wild surges:**

- Add a player attunement/arcana stat. Casting rolls the stat against the engine-computed power band (not the model's claimed severity).
- On a failed roll the spell does not fizzle — it **surges**: the engine applies a deterministic mutation to the already-validated resolution. Surge table examples: retarget to a random visible entity, double one effect and its cost, swap the damage type, include the caster in an area effect, convert a duration to permanent.
- Learned spells surge less; novel casts surge more. Higher attunement shrinks surge chance. This makes spamming brand-new wild magic risky rather than dominant, and gives standard spells and learned spells a clear role.
- Surges are pure engine code over validated effects: zero added latency, fully replayable.

### Acceptance Criteria

- A learned spell recasts with no LLM call and identical mechanics in a new context.
- Spellbooks serialize into replays and survive deterministic replay verification.
- Surge mutations are seed-deterministic and covered by tests.
- A playtest run demonstrates the intended loop: discover, learn, rely on, lose.

## Phase 10: Procedural Content And Tone

Goal: support the eclectic fantasy tone with varied places, enemies, and magical objects.

### Features

- Room themes:
  - flooded shrine
  - fungal vault
  - mirror hall
  - burnt library
  - salt crypt
  - observatory
  - bone market
  - abandoned kitchen
- Theme-specific enemies, items, and terrain.
- More item names and curse names.
- More death messages and spell outcome text.
- Optional LLM-assisted flavor generation that maps back onto validated mechanics.

### Acceptance Criteria

- Dungeon floors have recognizable themes.
- Content variety appears in both graphical and CLI play.
- Generated flavor never bypasses mechanical validation.

## Phase 11: Balancing, UX, And Release Readiness

Goal: make runs readable, fair, and satisfying.

### Features

- Better message log filtering and color — including register coloring: imperial events in
  cold grey-blue, wild magic in saturated jewel tones, ambience in its own hue. The
  color-vs-marble polarity on screen every single turn.
- Spell spectacle split: particle bursts and afterglow for wild casts vs. clean geometric
  flashes for charter casts — the two magics tellable apart with the sound off.
- Inspect/look mode.
- Target highlighting.
- Spell history.
- Help screen.
- Game-over summary:
  - turns survived
  - enemies defeated
  - curses gained
  - wild spells cast
  - cause of death
- Config file for:
  - model
  - provider
  - window size
  - debug logging
  - mock mode
- Basic packaging instructions.

### Acceptance Criteria

- A new player can understand the controls in-game.
- A run produces a useful death or victory summary.
- The game can be launched, tested, and configured without editing source code.

## Phase 12: Run Structure, Progression, And Lore (Ideation)

Goal: decide what a run *is*. This phase is deliberately unscheduled — it is a design question, not an engineering one, and the systems above (spellbook, curses, factions, towns) all gain meaning once it is answered.

The reception data from comparable games is clear: the difference between "novel toy" and "game" is whether freeform magic is in service of something the player can lose. The world already has the substrate — towns, an empire faction, a frontier, dungeons — but no macro loop.

### Decided (June 2026)

- **Nothing persists between runs.** Each run starts from zero — no meta-progression of any
  kind. The clerk's escalating notices, reputation, rumors, and all world consequences live
  and die within a single run.
- **The macro win condition is ending the prohibition on wild magic by bringing down the
  Grand Empire** — achievable within a single (long) run, either solo (e.g. kill the emperor)
  or geopolitically (aid other nations, win them to your cause). See `AESTHETICS_AND_TONE.md`.

### Open questions

- What is the typical run length, and what intermediate win states exist short of toppling
  the Empire?
- What stats does the player have, and what raises them? Where does the attunement stat from Phase 9 come from?
- Is wild magic itself the source of the world's problem, the tool against it, or both?

### Candidate directions (not decisions)

- **The Westward Burn.** The Legion advances across the frontier; zones behind the player are consumed. Constant forward pressure replaces a timer — you outrun the front or turn and stop it. Towns are temporary shelters whose NPCs and stock matter more because they will be gone.
- **The Debt.** Wild magic always balances its books. Every cast quietly accrues debt; collectors arrive mid-run in escalating forms; the run's climax is settling or defying the debt. This unifies the Phase 8 cost-top-up economy, curses, and `schedule_event` into one fiction.
- **The Leaking God.** Something sealed at depth N is the source of wild magic. Descend-and-confront classic structure: magic gets stronger and stranger with depth, surge rates climb near the source, and the player chooses to seal, free, or drink it.
- **Frontier Reputation.** Towns and factions as reputation hubs. Your magical record follows you — towns hear what you did at the last one. Factions court or hunt spellcasters. Lighter on plot, heavier on systemic consequence. *(Adopted in within-run form as Phase 16; the cross-run variant is rejected.)*

These compose: The Debt works as the moment-to-moment economy inside any of the other three frames.

### Exit criteria for the ideation phase

- A one-paragraph run fantasy statement ("a run is ...").
- A decided win/loss condition and target run length.
- A decided between-run persistence list.
- A player stat block sketch that Phase 9's casting check can build on.

## Phase 13: Regions As First-Class Data

Goal: make "where you are" a single data bundle instead of facts smeared across five files,
so a new region is a content file rather than five-file surgery. **Scheduled first** among
the June 2026 additions; the exact shape of a region is being designed before building.

Today the facts of a place are scattered: enemy pools in `game_data.py`, floor themes and
prop scenes in `generation.py`, ambient tables hardcoded in `engine.py`, the palette in
`ui.py`, and the narrative voice nowhere at all. Nearly everything in
`AESTHETICS_AND_TONE.md` — varied-by-run dungeons, eclectic-by-region voice, region-skinned
UI, the weirdness gradient — wants one structure.

### Features

- A `Region` definition bundling, at minimum:
  - identity: id, name, naming flavor for generated places and NPCs
  - voice: a short prose spec injected into the wild-magic and dialogue system prompts
  - look: UI palette and tile/glyph skin
  - population: enemy template pool, prop category weights, prop scene list
  - sound of the place: ambient message tables
  - town generation seeds (locations, traits, situations)
  - a wildness score driving the strangeness gradient (ambience, generation quirks, surge flavor)
- `state.region` as the single source of truth; generation, ambience, prompt building, and
  the UI all read from it.
- Port the current hardcoded content into region #1 with zero behavior change, then build
  region #2 to prove the seam.
- Phase 10's "room themes" fold into this structure as region content rather than a
  separate system.

### Decided (June 2026)

- **Granularity: geography plus a wildness axis.** Regions are overworld geography — the
  zone grid maps onto them, and towns/dungeons inherit their zone's region. Wildness is an
  orthogonal scalar (effective wildness = region.wildness_base + dungeon depth), so any
  region gets stranger with depth, and some regions start strange.
- **Format: Python data modules.** A frozen `Region` dataclass plus per-region definitions
  in `wildmagic/regions.py` (the props.py pattern). Migrating to external files later is
  mechanical if ever wanted.
- **Voice → LLM: style line + example swap.** A 1–2 sentence voice spec plus ~3
  region-voiced outcome_text samples, appended to the system prompt at cast time.
- **First regions:** the Hollowmere frontier (port of existing content, wildness_base 0)
  and the Glasswild (dreamlike deep-wild interior, wildness_base 6).

### Built (first pass, June 2026)

- `wildmagic/regions.py`: `Region` dataclass (voice, example outcomes, bestiary,
  imperial_presence, floor themes, ambient tables, wildness-banded wonder lines),
  `REGIONS` registry, `region_for_zone(zx, zy)`.
- Wired: `state.region_id` + `engine.region`; region-driven floor themes, prop scenes,
  enemy spawns, and Censorate-notice frequency in generation; region ambience with
  wildness-scaled wonder lines; region voice spliced into the wild-magic system prompt
  (`region_style` rides the LLM context and is stripped from the user JSON); region name
  in dialogue scene context; region switch and announcement on zone crossing.
- Still open: region-skinned UI palette (deliberately not added to the dataclass yet —
  lands with the Phase 11 UI work), region-flavored town seeds, and a real region map to
  replace the crude distance ring in `region_for_zone`.

### Acceptance Criteria

- Two visibly, audibly, and mechanically distinct regions exist.
- Adding a third region touches no engine code.
- Region voice measurably changes wild-magic outcome text (eval-harness spot check).

## Phase 14: The Empire As A System

Goal: implement the game's thesis — readable imperial doctrine that is frustrating to die
to and invigorating to outwit (see `AESTHETICS_AND_TONE.md`: deaths to the Empire are
paperwork; victories over it are jazz).

### Features

- **Thaumic heat.** Wild casts raise a per-run heat value scaled by the Phase 8 power
  score. Heat drives procedural, visible responses: notices posted, patrols rerouted,
  cordons, escalating squad tiers. The clerk's found-document memos escalate with
  heat/incidents within the run, not merely with depth.
- **Doctrine AI.** Legion squads fight by the book: formations, advance-by-rank,
  flank-by-procedure, retreat thresholds, reinforcement calls. Telegraphed and predictable
  on purpose — readable doctrine is what makes improvised counters feel earned.
- **Visible charter magic.** Imperial casters use cold, geometric, deterministic spells
  with clear wind-ups; suppression wards create zones where wild casting costs more or
  surges harder.
- **Standard spells become looted charter magic.** Reframe the existing deterministic
  spells (bolt, frost, heal, ward, reveal) as spells learned from confiscated charter
  spellbooks. Charter spellbooks become loot and heist objectives, giving the deterministic
  spell list a diegetic reason to be fixed and reliable — and a clean acquisition path.

### Acceptance Criteria

- A squad encounter plays out observably differently from a beast encounter, repeatably.
- Heat visibly changes the world (notices, patrol density, squad tier) within a single run.
- A player can learn and exploit at least three doctrine behaviors.
- Charter and wild casting are visually and mechanically distinguishable at a glance.

## Phase 15: Origins

Goal: implement the player-defined origin decision from the tone bible.

### Features

- Pick or roll an origin at run start (e.g. bone-singer's apprentice, deserter charter
  mage, merfolk exile, desert nomad).
- An origin seeds: starting inventory, one starting standard spell, faction reaction
  modifiers, and a tradition tag injected into the wild-magic prompt as idiom and mild
  mechanical bias.
- Origins are data (a small table), not code.

### Acceptance Criteria

- At least four origins, each producing a visibly different first ten minutes.
- The tradition idiom audibly shows up in spell outcome text (eval-harness spot check).

## Phase 16: Rumors And Reputation (Within-Run)

Goal: the world reacts to your legend — within a single run. Word of your exploits travels;
when you return to a town where you did something notable, the townsfolk have heard about it.

### Features

- **Unified event stream first.** Triggers, NPC perception, stats, and death effects are
  already four separate observation systems bolted onto the turn loop; route them through
  one `emit(event)` bus and make the rumor system its fifth subscriber rather than a sixth
  bolt-on.
- **Rumor ledger.** Notable events (big casts, squad wipes, NPC rescues or deaths, town
  incidents) become short rumor records with location, turn, and a salience score.
- **Propagation.** Rumors travel outward over game time along the road graph, mutate
  slightly with distance, and decay.
- **Consumption.** NPC dialogue context and town-generation situations receive
  locally-known rumors; faction reputation derives from rumor history and gates reactions
  (shelter, prices, bounties — and, later, coalition willingness for the macro arc).
- All of it is per-run state: serialized with the run, dead with the run.

### Acceptance Criteria

- Do something dramatic in town A, travel two zones to town B, and an NPC mentions a
  distorted version of it.
- Return to town A and the dialogue references the event directly.
- Reputation observably changes at least three NPC behaviors (prices, hostility, sheltering).
- Rumor state is covered by replay tests.

*(June 2026 note: the dialogue-lore extraction system has landed — `wildmagic/lore.py`,
LoreClaim ledger, town lore_hooks. Phase 17 below absorbs that system and this phase's
rumor-ledger half into a unified Promise Ledger; the reputation/propagation half of this
phase remains as scoped, consuming the same ledger.)*

## Phase 17: The Promise Ledger (Unify Rumors, Quests, And Emergent World Creation)

Goal: one system for everything the world has committed to narratively but not yet
delivered mechanically. Full design in `docs/WORLD_PROMISES.md` — decisions locked
June 2026:

- **Always honor (pure yes-and):** every bound promise comes true; the quality gate is
  binding, not realization. What the world can't build stays flavor lore.
- **One `WorldPromise` entity** absorbs `LoreClaim`, `Quest`, and town lore_hooks — one
  lifecycle (pending → bound → realized/fulfilled/contested), one spatial-binding model,
  one realization pipeline.
- **Deterministic skeleton + LLM flesh:** seeded rules decide what binds where and what
  gets built (replay-safe); the background CPU model optionally fleshes names and
  backstories via the existing pregen pattern, never load-bearing.
- **v1 ceiling:** structures in any zone (the "chapel north of town" case), promise-bound
  NPCs aware of the rumor that created them, and quest objectives/rewards realized
  cross-zone. Conditional appearances ("only at midnight") deferred.

Flagship acceptance test: an NPC says "there is a chapel north of town" → the next
unexplored zone north — town or not — contains a chapel whose keeper has heard the story.

Build order (M1–M6 detailed in the design doc): ledger + migration → binding (spatial
resolver, blueprint table, reservations) → open-zone realization → quest unification →
LLM flesh → `promise_eval` harness.

**Status (late June 2026): M1–M4 implemented** — unified ledger, deterministic
binding/reservations, archetype-site realization in any zone, promise-backed quest log;
all deletion gates verified held. The replay contract (the open M2 debt) is now closed:
replay format v3 records promise apply points per action (`promises.before/after` +
`final_promises`), replays inject them with zero model calls, and a late-drain
integration test proves a zone generated between dialogue and drain replays identically.
M5 flesh is also in: background decoration drafts (keeper, arrival line, prop flavor)
enqueued when a promise binds, never load-bearing, recorded at apply points and
replay-injected with zero model calls. Remaining, in order (detailed in the design
doc's "Next pieces"): live-model capture shakedown (funnel-scoring `lore_eval` built;
live run pending review), M6 eval graduation + agent playtest, a player-facing promise
journal, then prophecy spells as the first new producer bridging back to Phase 8.

Revised after Codex review (`WORLD_PROMISES_NOTES_TO_CLAUDE.md`): **every milestone now
ends with named legacy writers deleted** (M1 kills `LoreClaim`/`lore_claims`, M2 kills
`lore_hooks`/`mark_lore_redeemed`, M4 kills persistent `Quest`/`maybe_spawn_quest_item`);
the schema separates `claimed_space` from `bound_space` so always-honor relocations are
recorded, not silent; objective/reward schemas are typed from M1; the replay contract
(record creation/binding/reservation/realization/flesh; zero model calls on replay) and
golden binding tests start in M2, not M6. Old replay files are deleted outright — no
migration parser; the format version bumps so stale files fail fast, and new goldens are
recorded fresh.

Future producers already designed for: prophecy spells (wild magic that mints promises),
NPC rendezvous commitments ("I'll meet you at the chapel"), clerk/flier threat-promises
from the Phase 14 heat system (including named investigators like Kipler), and
player-spread false rumors for the coalition arc.

## Continuous Testing Plan

Testing should grow alongside features rather than wait until the end.

### Unit Tests

- State validation.
- Movement and collision.
- Combat and death.
- Inventory changes.
- Status application and expiration.
- Curse mechanics.
- Terrain transformation.
- Wild magic JSON parsing and validation.

### Scenario Tests

- Small fixed maps for specific spells:
  - fire spell near flammable terrain
  - frost spell near water
  - teleport into blocked tile
  - summon ally near player
  - rejected infinite-resource spell
  - malformed JSON technical failure

### Replay Tests

- Record a few short successful runs.
- Replay them in CI or local smoke tests.
- Ensure final state matches expected summaries.

### Prompt Regression (audit-log evals)

- `logs/wild_magic_audit.jsonl` is a growing corpus of real spells with real model
  responses. Re-run recorded prompts against the *current* system prompt and score
  parse/validation/acceptance rates before and after any prompt change.
- Complements the Phase 8 `speleval` corpus: speleval is curated and adversarial; the
  audit log is organic and ever-growing. Prompt edits (voice guides, region specs, origin
  idioms) get a number instead of a vibe.

### Formalization

- Consolidate the above into a real pytest suite (unit + scenario + replay), starting with
  the highest-density pure-function targets: `normalize.py` and
  `spell_contract.validate_resolution`.
- Run the suite plus `python -m wildmagic.smoke_test` as a pre-commit hook or CI step.

### Agent Playtests

Add command-line playtest policies:

- `cautious`: avoid low HP, use safe spells, pick up items.
- `wild`: cast wild magic often.
- `melee`: avoid magic when possible.
- `stress`: intentionally casts weird spells to test validation.

Example command:

```powershell
python -m wildmagic.playtest --seed 123 --policy wild --turns 200
```

The playtest should report:

- turns survived
- final HP and mana
- enemies defeated
- wild spells cast
- rejected spells
- technical failures
- curses gained
- validation failures
- crash status

## Recommended Next Step

Phases 1–7 are done or close to it. June 2026 decision: **Phase 13 (regions) goes first** —
every line of content written before regions exist is a line migrated later. Its open
design questions are being settled now; building follows immediately after.

Then Phase 8 in its listed order (power score → cost-floor economy → eval harness → dynamic
schema enums → async UI → provider cleanup) — the power score also feeds Phase 14's heat
system, and the eval harness makes every prompt change (region voices, origin idioms)
measurable instead of vibes-based. Then Phase 14 (the Empire as a system), with Phase 15
(origins) and Phase 16 (rumors) slotting in behind — both are mostly data and prompt
context once regions exist.

Phase 12's remaining ideation (run length, player stats, intermediate win states) can
proceed in parallel with any of this.

Deferred deliberately:

- **A second judge-LLM plausibility pass.** The deterministic power-score economy does the
  same job with zero latency cost, and latency is the scarcest resource on local hardware.
  Revisit only if the eval harness shows exploits leaking through the deterministic layer.
- **Components-as-ingredients** (inventory materials shaping spell power and flavor beyond
  flat item costs). Promising — it gives the economy, looting, and trading a reason to loop
  back into casting — but deferred until the Phase 8 economy lands and proves out.
- **Any cross-run persistence or meta-progression: rejected outright** (June 2026). Each
  run is its own complete story, starting from zero.
