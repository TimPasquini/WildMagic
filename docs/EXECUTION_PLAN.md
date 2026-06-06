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
- Locked doors and traps.
- More enemy types with different behaviors:
  - melee pursuer
  - ranged caster
  - fleeing scavenger
  - stationary hazard
  - summoner
- Real equipment:
  - weapon slot
  - armor slot
  - charm slot
  - carried inventory
- Consumables:
  - healing potion
  - mana potion
  - smoke vial
  - blink scroll
- Deterministic standard spells:
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
  - fire spreads to flammable tiles
  - water conducts lightning
  - ice melts into water
  - frost freezes water
  - acid weakens walls
  - force can push entities
  - radiant harms undead
  - shadow harms light sources
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

## Phase 8: Wild Magic Intelligence Layer

Goal: make typed magic more reliable, expressive, and debuggable.

### Features

- Improve the LLM prompt with examples for:
  - ordinary attack spells
  - terrain manipulation
  - item transformation
  - summoning
  - healing
  - overpowered requests
  - catastrophic costs
- Add spell severity classification:
  - harmless
  - minor
  - moderate
  - major
  - catastrophic
  - reject
- Add optional pre-cast warning for catastrophic spells:
  - “This will have a terrible cost. Cast anyway?”
  - The warning should not reveal the exact cost.
- Add JSON repair attempt:
  - if the first response is malformed, ask the model to repair it
  - if repair fails, treat as technical failure
- Add provider diagnostics:
  - provider name
  - model name
  - latency
  - raw response saved to debug log
  - parsed response
  - validation errors
- Add operation budget rules:
  - limits by severity
  - maximum spawned entities
  - maximum terrain changes
  - maximum healing/damage
  - required cost thresholds

### Acceptance Criteria

- Common spells resolve correctly most of the time with Ollama.
- Malformed model output does not consume a turn.
- Catastrophic spells can warn the player before casting.
- Debug logs make it clear why a spell was accepted, rejected, repaired, or failed.

## Phase 9: Procedural Content And Tone

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

## Phase 10: Balancing, UX, And Release Readiness

Goal: make runs readable, fair, and satisfying.

### Features

- Better message log filtering and color.
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

Start with Phase 1.

The headless play harness, command API, and replay logs are the most important foundation because they let future work move quickly without relying on manual UI testing. Once Codex can play repeatable runs from the terminal, the game can safely expand into richer terrain, items, curses, factions, and world-state mutations.
