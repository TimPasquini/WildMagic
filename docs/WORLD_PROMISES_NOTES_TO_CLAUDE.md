# Notes Back To Claude On The Promise Ledger Plan

These are Codex/user-side notes on `docs/WORLD_PROMISES.md`. The core direction is strong:
`WorldPromise` should become the unified abstraction for rumors, quests, town hooks, prophecy,
and other narratively committed future content.

The main adjustment from the user is philosophical and architectural:

> Err toward aggressively deleting legacy baggage and duplicate systems.

Compatibility shims are acceptable as short migration scaffolding, but they should have explicit
removal milestones. We should avoid leaving `LoreClaim`, `Quest`, `lore_hooks`, and
`maybe_spawn_quest_item` as parallel systems that future agents have to mentally reconcile.

## Migration Stance

Prefer **early replacement with clear deletion gates** over long-term dual systems.

The Promise Ledger should not become a fourth layer sitting beside lore, quests, town hooks, and
quest items. It should absorb them, then remove the old storage and control flow.

Good temporary shape:

- Add `WorldPromise`.
- Migrate existing saved/runtime lore into `WorldPromise`.
- Provide small compatibility readers only where needed for UI/replay.
- Delete the old writers immediately after the promise writer is tested.
- Delete compatibility readers after the UI/CLI/replay paths read promises directly.

Bad long-term shape:

- `state.lore_claims` and `state.promises` both authoritative.
- `state.quests` and quest-promises both authoritative.
- Towns still consuming `lore_hooks` while open zones consume promise reservations.
- `maybe_spawn_quest_item` still independently spawning objectives outside the promise pipeline.

## Deletion Map

### Lore

Target deletion:

- `LoreClaim`
- `GameState.lore_claims`
- `GameEngine.add_lore_claims`
- `GameEngine.lore_claims_for_context`
- `GameEngine.mark_lore_redeemed`
- town-generation `lore_hooks`

Replacement:

- `WorldPromise(kind="rumor" | "background" | "place" | "person" | "threat")`
- `GameState.promises`
- `add_world_promises`
- `promises_for_context`
- reservation + realization status

`wildmagic/lore.py` should survive as the capture/extraction provider, but its output should be
`WorldPromise` or a small intermediate extraction DTO that immediately binds into promises. It
should not continue producing a separate persistent `LoreClaim` type.

### Quests

Target deletion:

- `Quest` as a persistent data model
- `GameState.quests`
- quest objective spawning that bypasses reservations
- `maybe_spawn_quest_item` as an independent zone-entry side effect

Replacement:

- `WorldPromise(kind="quest")`
- strict `objective` and `reward` schemas
- quest log as a view over promise entries
- quest objective realization through the reservation/blueprint system

It is fine for the UI/CLI to keep saying "Quest Log", but it should be rendering promises, not a
separate quest list.

### Town Hooks

Target deletion:

- ad-hoc `lore_hooks`
- `mark_lore_redeemed`
- town-only rumor redemption

Replacement:

- promise reservations read by all zone generation, town or not
- `status="realized"` / `realized_in=...`
- optional LLM flesh that decorates a deterministic skeleton

Towns become just one kind of zone that can realize promises.

## Proposed Build Order With Deletion Gates

### M1: Promise Ledger Replaces Lore Storage

Add:

- `wildmagic/promises.py`
- `WorldPromise`, `PromiseBinding`, strict status/kind constants
- `GameState.promises`
- migration helper from old `LoreClaim` dicts for replay/backcompat

Change:

- lore extraction writes promises, not lore claims
- dialogue context reads promises
- town context reads promises

Delete by end of M1 if tests pass:

- `GameState.lore_claims`
- `LoreClaim`
- lore-specific add/context/redeem engine methods

Temporary compatibility allowed:

- `LoreClaim.from_dict`-style replay migration helper, but only as a parser inside
  `promises.py`, not as an active model.

### M2: Binding And Reservations Replace Town Hooks

Add:

- spatial resolver
- blueprint matcher
- reservation store
- golden binding tests

Change:

- bind eligible promises at capture time or at a deterministic turn boundary
- town generation reads reservations instead of `lore_hooks`
- open-zone generation reads the same reservations

Delete by end of M2:

- `lore_hooks`
- `mark_lore_redeemed`
- pregen invalidation based on raw lore/promise additions

### M3: First Realization Blueprint

Implement the flagship chapel case:

- "chapel north of town" binds north
- next unexplored northern zone realizes a chapel structure
- chapel keeper NPC knows the originating promise
- promise becomes `realized`

This should work in a non-town open zone first. Town support follows naturally once reservations
are shared.

### M4: Quest Migration

Add:

- strict quest objective schemas: `fetch`, `kill`, `visit`, `talk`
- strict reward schemas
- quest log view over promises

Change:

- NPC quest generation appends `WorldPromise(kind="quest")`
- quest objectives reserve and realize like any other promise

Delete by end of M4:

- persistent `Quest`
- `GameState.quests`
- independent quest-item spawning
- `maybe_spawn_quest_item` as a separate world-generation path

### M5: Optional Flesh

Add:

- background model flesh for realized/reserved promises
- replay recording of flesh outputs

Constraint:

- flesh never determines whether the promise exists or where it binds
- deterministic skeleton remains complete without model output

### M6: Promise Eval

This should probably start earlier as a small harness in M2, then graduate here.

Required cases:

- chapel north of town
- bandit camp east
- witch in the woods
- grave/barrow/tomb
- cache/stash
- unbuildable poetic claim stays flavor
- already-explored target relocates with explicit `claimed_space` vs `bound_space`
- replay uses recorded promises and bindings with zero model calls

## Schema Notes

The `WorldPromise` shape should distinguish what was said from what the engine chose:

```python
claimed_space: SpatialHint | None
bound_space: SpatialHint | None
binding: PromiseBinding | None
```

This makes "the chapel is farther north than they said" debuggable and narratable. It also keeps
"always honor" honest: the world can relocate a promise, but the relocation is recorded instead
of silently rewriting what the NPC claimed.

Quest fields should not stay untyped `dict` forever. Define engine-owned schemas early, even if
they are simple dataclasses or typed dictionaries:

- `FetchObjective(item_id, quantity, target_zone)`
- `KillObjective(entity_template, quantity, target_zone)`
- `VisitObjective(site_blueprint, target_zone)`
- `TalkObjective(npc_seed, target_zone)`
- `Reward(gold, items, reputation, flags)`

## Replay Contract

Replay determinism should be part of the promise system from the first binding milestone.

Record:

- promise creation
- binding result
- reservation target
- realization seed
- realized blueprint id
- optional LLM flesh output

If a replay has those records, it should not call extraction, binding LLMs, or flesh models. The
engine should inject the recorded promise/binding/realization data.

## Main Ask Back To Claude

Please revise the plan so deletion is explicit:

- every compatibility layer gets a removal milestone
- `LoreClaim` and persistent `Quest` are not treated as long-term neighbors of `WorldPromise`
- the first implementation slice ends with at least one legacy writer removed
- the docs say which old modules/functions become capture helpers versus which are deleted

The Promise Ledger is the right destination. The implementation plan should make it hard for the
repo to drift into "promise ledger plus old lore plus old quests plus old town hooks" by accident.
