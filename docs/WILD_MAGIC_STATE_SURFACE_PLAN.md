# Wild Magic State Surface Implementation Plan

> **Status (implemented):** All seven stages below are in the codebase.
> - Stage 1 — contract alignment (resolver context derives supported effects/costs from data).
> - Stage 2 — `state_view.py` (read-only resolver/replay/inspection views).
> - Stage 3 — `refs.py` (normalized refs/selectors; `resolve_target*`/placement routed through it).
> - Stage 4 — `effect_registry.py` (`EffectSpec` metadata; owns `EFFECT_TYPE_ALIASES`).
> - Stage 5 — per-cast schema (`per_cast_response_schema`, opt-in via `OLLAMA_SCHEMA`) +
>   card-driven context slices + audit `routing` block.
> - Stage 6 — `operations.py` (`StateDelta` capture in the shared mutators; `WildMagicOutcome.deltas`).
> - Stage 7 — durable world-memory lanes in `operations.py` (traits / place·faction·world notes /
>   faction standing / deeds) + resolver prompt guidance on soft vs mechanical writes.
>
> The text below is the original design; it is kept as the rationale of record.

Wild Magic's core promise depends on a legible world: the player can type strange spell
ideas, the resolver can see enough concrete state to propose useful JSON, and the engine can
bind, validate, apply, audit, and replay the result without surrendering authority.

This plan turns the current architecture into a clearer state surface for wild magic. It is
not a rewrite of `GameState`; it is a staged path toward one shared vocabulary for what the
world contains, how JSON refers to it, and which engine-owned operations may mutate it.

## Current Baseline

The project already has the right backbone:

- `GameState` is the authoritative run state, owned by `GameEngine`.
- `actions.py` is the shared command/session layer used by CLI, GUI, tests, and replay.
- `apply_wild_magic_resolution()` is transactional: validation happens before effects, and
  application rolls back on failure.
- `context_for_llm()` builds a rich resolver packet with visible entities, tiles, room
  context, spell anchors, semantic notes, canon, inventory, triggers, timers, and catalogs.
- `capabilities.py` routes each spell to a small set of relevant mechanic cards.
- `semantics.py`, promises, canon records, deeds, factions, and legend already provide
  several durable memory lanes.

The risk is drift. The resolver context, schema, effect prompt text, validation, effect
handlers, replay summaries, CLI inspection, and docs all describe overlapping pieces of the
same world. As the operation surface grows, that overlap will become the main source of
bugs and hesitation.

## Goals

- Give every LLM-facing system a compact, stable, typed view of the relevant game state.
- Let JSON refer to entities, tiles, rooms, props, items, factions, promises, memories, and
  places through one normalized reference language.
- Keep the engine authoritative by binding refs, validating effects, and applying mutations
  through shared operation primitives.
- Derive prompts, schema enums, validation rules, and documentation from the same effect
  metadata wherever practical.
- Preserve the shared action path so GUI, CLI, replay, smoke tests, and scripted playtests
  remain in sync.
- Improve auditability: a cast should record what the resolver saw, what refs were bound,
  which operations were applied, and why anything was rejected.

## Non-Goals

- Do not replace `GameState` with a second state model.
- Do not perform a large entity-component-system rewrite before there is pressure for it.
- Do not let the LLM mutate raw state directly.
- Do not build GUI-only or CLI-only spell behavior.
- Do not make semantic notes mechanically load-bearing without explicit crystallization into
  engine-visible operations such as statuses, tags, auras, promises, triggers, or deeds.

## Design Decisions

### One Authoritative State, Many Typed Views

`GameState` remains the source of truth. New view builders read from it and produce stable
packets for specific consumers:

- resolver context
- dialogue context
- trade context
- CLI/GUI inspection
- replay summaries
- audit records
- test assertions

The first extraction should be read-only. Do not move mutation code while introducing the
view layer.

### Refs Before Mutation

Resolver JSON should eventually support typed references:

```json
{"kind": "entity", "id": "actor_3"}
{"kind": "tile", "x": 12, "y": 8}
{"kind": "room", "id": "room_2"}
{"kind": "faction", "id": "empire"}
{"selector": "nearest_enemy"}
{"selector": "selected_target"}
```

Existing string targets such as `"player"`, `"nearest_enemy"`, `"target"`, and entity ids
must continue to work. A normalization layer should translate old strings into the new ref
language before validation and application.

### Effects Are Specs, Not Just Branches

Effect support currently spans the contract, prompt cards, normalization, validation,
handlers, docs, and tests. The long-term shape should be an effect registry where each
effect has a single metadata record:

- canonical name
- aliases
- schema fields
- validator
- applier
- prompt/card text or reference
- required context slices
- examples
- audit labels

The registry can start as metadata around existing handlers. It does not need to move every
handler at once.

### Capability Routing Drives Context And Schema

Capability cards already decide which specialist mechanics are relevant to a cast. The same
selection should also decide:

- which effect types are legal in the per-cast schema
- which specialized context slices are included
- which target/item/template/status/tile enums are narrow enough to provide

A fireball should not receive NPC memory-edit context. A memory spell should.

### Mutation Goes Through Engine Operations

Effect handlers should gradually delegate to shared operation primitives:

- damage or heal an entity
- move or teleport an entity
- create, transform, or remove an entity
- mutate a tile or tile aura
- add or remove status, tag, resistance, weakness, curse, trait, or inventory
- schedule an event or create a trigger
- write a semantic note, promise, canon record, deed, or faction change

These operations should be the place that clamps values, checks bounds, records messages,
updates stats, and produces audit deltas.

### Durable Memory Lanes Stay Distinct

Wild magic can change more than HP and tiles, but each kind of durable world change needs a
clear lane:

- **Entity trait:** soft descriptive fact attached to an entity or item; surfaced whenever
  that thing is in context.
- **Semantic note:** soft fact about a place, faction, or the world; retrieved by anchors.
- **Promise:** future or rumored world commitment that may bind/reserve content.
- **Canon record:** materialized description of an existing thing.
- **Deed:** consequential action judged by bounded rules.
- **Faction/legend change:** mechanical standing or reputation consequence.
- **Flag:** simple mechanical switch, used sparingly.

This prevents `set_flag` and free-text traits from becoming catch-all substitutes for the
world simulation.

## Implementation Stages

### Stage 1: Align The Existing Contract

Goal: remove avoidable drift before adding new abstractions.

Work:

- Replace hard-coded `supported_effects` and `supported_costs` in resolver context with data
  derived from `spell_contract.py` and capability routing.
- Add a regression test that `context_for_llm()["supported_effects"]` contains every
  effect type the currently selected prompt/schema allows.
- Add a regression test that every `SUPPORTED_EFFECTS` key has a core or capability-card
  home.
- Review `docs/WILD_MAGIC_SCHEMA.md` against the live contract and update mismatches.

Acceptance criteria:

- A new effect cannot be registered without surfacing in the appropriate prompt/schema/test
  path.
- The resolver context no longer has a stale hand-maintained supported-effect list.
- Existing mock-provider spell tests still pass.

### Stage 2: Introduce `state_view.py`

Goal: build one read-only state surface without changing behavior.

Work:

- Add `wildmagic/state_view.py`.
- Define compact typed builders for:
  - `entity_card(entity, engine)`
  - `item_card(entity, engine)`
  - `tile_card(x, y, engine)`
  - `room_card(room, engine)`
  - `selected_target_card(engine)`
  - `scene_notes_card(engine, center, radius)`
  - `spell_context_view(engine, spell, selected_cards)`
  - `inspection_view(engine)`
  - `replay_summary_view(engine)`
- Move the duplicated public-dict assembly from `context_for_llm()` and
  `summarize_state()` into these builders incrementally.
- Preserve existing output shape at first, even if the internal implementation changes.
- Keep `GameEngine.context_for_llm()` as the public method, delegating into the new module.

Acceptance criteria:

- Existing tests that assert resolver context, replay summary, targeting, semantics, and
  hybrid generation continue to pass.
- CLI `inspect`, replay final summaries, and resolver audit context still include the same
  core information.
- The new module has no mutation paths.

### Stage 3: Add Normalized Refs And Selectors

Goal: make JSON references explicit while preserving the old target strings.

Work:

- Add `wildmagic/refs.py`.
- Define normalized ref dictionaries or dataclasses for entities, tiles, rooms, factions,
  promises, canon records, and selectors.
- Implement:
  - `normalize_ref(value)`
  - `bind_ref(engine, ref)`
  - `bind_position(engine, ref)`
  - `bind_group(engine, ref)`
- Route existing `resolve_target()`, `resolve_target_group()`, `effect_position()`, and
  `resolve_placement()` through the ref layer where possible.
- Continue accepting legacy strings in all existing effects.
- Add tests for selected-target tile refs, moving selected-target entity refs, nearest enemy,
  all enemies, explicit entity ids, explicit tile coords, and invalid refs.

Acceptance criteria:

- Existing wild-magic JSON continues to work unchanged.
- New typed refs can target at least entities and tiles.
- Invalid refs fail as technical validation/application failures without partial mutation.

### Stage 4: Build The Effect Registry Shell

Goal: make effect knowledge discoverable without immediately moving all handler logic.

Work:

- Add `wildmagic/effect_registry.py`.
- Define an `EffectSpec` structure with name, aliases, schema fields, required context,
  capability card ownership, validator, and applier reference.
- Register the current live effects.
- Derive `SUPPORTED_EFFECTS` from the registry, or add a test that the registry and
  `SUPPORTED_EFFECTS` are identical during the transition.
- Move effect-type aliases from `resolution_parsing.py` into registry metadata over time.
- Add tests that docs/prompt/schema-visible effects cannot drift from the registry.

Acceptance criteria:

- There is one obvious place to inspect what an effect means.
- Adding a new effect requires registering metadata before prompt/schema/tests pass.
- Existing effect handlers still run through the same transactional path.

### Stage 5: Dynamic Per-Cast Schema And Context

Goal: give each cast the operation surface and state slices it actually needs.

Work:

- Use `selected_effect_types(selected_cards)` to build a per-cast response schema.
- Keep `needs_capability` global if added later, so the model can name a missing card.
- Pass the narrowed schema to the Ollama JSON `format` path when supported.
- Add card-driven context slices based on `CapabilityCard.required_context`.
- Start with low-risk slices:
  - memory-edit spells receive nearby NPC memories.
  - prophecy spells receive promise summaries.
  - structure-animation spells receive nearby prop/structure cards.
  - item-conjuration/transformation spells receive floor and inventory item cards.
- Log selected cards, selected effect types, and included context slices in the audit record.

Acceptance criteria:

- A plain direct-damage spell has a smaller schema/context than a memory-edit or prophecy
  spell.
- Routed specialist spells still receive all information needed to produce valid JSON.
- Mock and replay flows do not require live provider calls.

### Stage 6: Operation Primitives And State Deltas

Goal: make mutation more observable and reusable.

Work:

- Add shared operation helpers under the engine or a new `wildmagic/operations.py`.
- Start with operations already used by many effects:
  - `apply_damage`
  - `apply_status`
  - `create_tiles`
  - `create_entity`
  - `move_entity`
  - `write_trait`
  - `write_semantic_note`
  - `schedule_event`
- Have each operation return a compact `StateDelta` or append one to a transaction log.
- Record deltas in wild-magic audit records and replay records when useful.
- Keep user-facing messages stable unless the operation extraction requires a wording fix.

Acceptance criteria:

- A multi-effect spell can be explained as a sequence of bound operations.
- Transaction rollback still restores the previous state on failure.
- Tests can assert applied deltas for important behavior without relying only on message
  text.

### Stage 7: Durable World-Memory Write API

Goal: make world-level spell consequences expressive without abusing flags or traits.

Work:

- Add explicit operation helpers for:
  - entity trait writes
  - place/faction/world semantic notes
  - promises and prophecies
  - canon records attached to known objects or rooms
  - deed emission for consequential outcomes
  - faction/legend changes
- Document when each lane should be used.
- Add resolver prompt guidance that distinguishes soft semantic writes from mechanical
  crystallization.
- Consider a future `record_note` or `record_world_fact` effect only if it can be validated
  and routed to the correct lane safely.

Acceptance criteria:

- Spells like "make this room remember my name", "the captain owes me a favor", and "north
  of here a glass chapel waits" land in distinct durable systems.
- The engine still decides whether any such write creates mechanical obligations.
- Semantic-only changes are never required for critical path progression.

## Testing And Playtesting

Each stage should run:

```powershell
python -m ruff check wildmagic tests
Get-ChildItem wildmagic -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }; python -m py_compile main.py
python -m wildmagic.smoke_test
python -m pytest -q
```

For stages touching wild magic behavior, also run a short CLI playtest:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --no-render `
  --command "inspect" `
  --command "target 12 8" `
  --command "cast turn the target square into slick ice" `
  --command "cast make the nearest enemy forget why it came here" `
  --command "cast somewhere north of here a glass chapel waits for me" `
  --command "inspect"
```

If a stage changes resolver prompts, schema, normalization, or effect application, inspect
`logs/wild_magic_audit.jsonl` after at least one live-provider run.

## Migration Strategy

- Keep public methods such as `GameEngine.context_for_llm()` and
  `GameEngine.apply_wild_magic_resolution()` in place while internals move.
- Preserve existing JSON spell shapes and normalize them into new refs/effect specs.
- Move one effect family at a time onto registry metadata and operation primitives.
- Prefer shadow-mode tests before enforcing narrowed schemas.
- Update `docs/ARCHITECTURE.md` whenever a new module is added or responsibilities move.
- Update `docs/WILD_MAGIC_SCHEMA.md` whenever the emitted JSON contract changes.

## First Concrete Pull Requests

1. **Contract alignment.**
   Remove stale hard-coded supported-effect lists from resolver context and add drift tests.

2. **Read-only state view.**
   Add `state_view.py`, delegate `context_for_llm()` and `summarize_state()` to it without
   changing output shape.

3. **Refs foundation.**
   Add `refs.py`, normalize existing target strings, and route entity/tile targeting
   through the binder.

4. **Effect registry shell.**
   Add `effect_registry.py`, register live effects, and assert registry/contract/card
   coverage.

5. **Dynamic schema shadow mode.**
   Build per-cast schemas from selected cards, log the narrowed effect set, and compare
   live outputs before enforcing it.

D