# Architecture

Wild Magic is structured as a Python package (`wildmagic/`) with a clear separation between
data, logic, and presentation layers. The game engine is a single `GameEngine` class assembled
from mixin modules so each concern lives in its own file.

Related structural plan: `docs/WILD_MAGIC_STATE_SURFACE_PLAN.md` describes the staged work
to make game state more legible to wild magic through shared state views, normalized refs,
effect metadata, dynamic context/schema routing, and engine-owned operation primitives.

---

## Entry points

### `main.py`
GUI entry point. Launches `wildmagic.ui.run_game()` and accepts `--autoplay` to start the
graphical UI with AI watch mode already enabled.

### `wildmagic/ui.py`
Pygame front-end. Owns the game loop, renders the tile map and side panels, handles keyboard
input, and routes commands to `GameSession`. Also shows the LLM thinking panel, the model
selector overlay, and a visual AI watch controller that lets the autoplay command chooser
drive the same command path while the renderer stays responsive.

### `wildmagic/cli.py`
Terminal front-end. Parses `--seed`, `--scenario`, `--provider`, `--script`, `--record` flags,
drives `GameSession` in a readline loop, and optionally saves a replay JSON at exit.

### `wildmagic/autoplay.py`
Autonomous headless playtesting harness (`python -m wildmagic.autoplay`). Runs sequential
episodes through `GameSession`, using a stub/random/Ollama command chooser while the
engine remains authoritative. Writes per-step JSONL, command scripts, replay files,
findings, and a Markdown report under `logs/autoplay/<run_id>/`. Its invariant checker
wraps `ActionResult` turn-contract fields, `GameEngine.validate_state()`, and a few
cross-state consistency checks; agent notes are kept as unverified leads.

---

## Session and action layer

### `wildmagic/actions.py`
`GameSession` — the single object callers interact with. Wraps `GameEngine` and the LLM
providers. Exposes `process_command(text)` which routes movement, wait, open/close, stairs,
cast, talk, trade, inventory, and `examine` commands. Returns an `ActionResult`. Also owns the background
lore-extraction executor used after dialogue; completed promises are added to `GameState` and
recorded into replay data. On-demand canon materialization for `examine` also lives here:
valid new room-flavor records cost a turn, technical failures do not, and re-reading existing
canon is free. `close()` cancels pending lore work and shuts down the executor.
Also holds `summarize_state()` (used by the replay system) and `to_replay()` / `from_replay()`
serialization.

### `wildmagic/replay.py`
Save/load/run replay JSON files. `run_replay(path)` re-feeds a recorded session's commands back
through a fresh `GameSession` and optionally checks the final state snapshot against a saved
expectation. Replays carry materialized canon apply points so `examine` and future background
content replay without calling the provider. Used for regression testing.

---

## Game engine

### `wildmagic/engine.py`
`GameState` dataclass and the `GameEngine` class declaration. Gameplay concerns are
split into mixins, leaving engine.py with the infrastructure that everything else depends on:

- `GameState` — the serialisable game world (tiles, entities, inventory, turn counter, flags, tile flows, etc.)
- `GameEngine.__init__` — seeds RNG, builds initial state, dispatches to scenario generators
- Utility queries: `in_bounds`, `tile_at`, `tile_key`, `is_visible`, `can_occupy`, `distance`, `entities_at`, `blocking_entity_at`, `living_enemies`, `is_hostile_to`
- State validation: `validate_state`
- FOV: `update_fov`, `effective_fov_radius`, `has_line_of_sight`, `tile_blocks_sight`.
  The `sight_shrouded` status can temporarily reduce the player's view radius. FOV also
  maintains `GameState.visible_entity_ids` so `on_enters_sight` triggers fire once when a
  living actor/NPC newly enters the player's view.
- Tile mutation: `set_tile`, `tile_tags_at`, `_reacting_tile`
- Spawning: `spawn_actor`, `spawn_npc`, `next_entity_id`
- Player actions: `attempt_player_move`, `wait_turn`, `open_door`, `open_adjacent_door`,
  `descend_stairs`, `ascend_stairs`, `teleport_entity`, `_move_to_nearest_open_tile`. The
  `seal_stairs` world flag blocks stair travel while set.
- Standard spells (deterministic, no LLM): `cast_standard_bolt`, `cast_standard_frost`, `cast_standard_heal`, `cast_standard_ward`, `cast_standard_reveal`
- NPC dialogue/trade/promises: `find_talk_target`, `dialogue_context_for_llm`, `lore_extraction_context`, `promises_for_context`, `promise_hooks_for_zone`, `add_promises`, `apply_dialogue_exchange`, `resolve_pending_trade`, `should_consider_trade`, `trade_context_for_llm`
- LLM context building: `context_for_llm` (delegates to `state_view.spell_context_view`),
  `nearby_spell_anchors`, `nearby_map_strings`.
  Context includes semantic room labels (`current_room`, `nearby_rooms`) and retrieved
  materialized canon (`nearby_canon`) so future richness prompts and wild magic share
  the same world facts.
- Turn bookkeeping: `finish_player_turn`, `_regenerate_player`, `resolve_target`, `resolve_target_group`, `nearest_enemy`, `_verb`.
  `resolve_target`/`resolve_target_group` are thin delegators to the `refs.py` binder, so they
  accept both legacy strings and typed refs (`{kind: entity|tile|room|faction}`, `{selector: …}`).
- Explicit targeting: `set_target`, `clear_target`, `has_target`, `selected_target_entity`,
  `selected_target_tile`, `references_selected_target`, `_aimed_enemy`. A player-marked
  square (clicked in the UI or set via the free `target <x> <y>` command) is stored on
  `GameState` (`target_x/target_y/target_entity_id`). `resolve_target` resolves
  "target"/"there"/"that square" to its occupant; `effect_position`/`resolve_placement`
  (effects.py) and the `teleport` handler honor a bare-tile mark by coordinates; the
  standard spark/frost spells aim at it. `context_for_llm` advertises it as
  `selected_target` so the resolver can reason about range/LOS. Cleared on zone change
  and stair transitions (coordinates become meaningless).
- Environment tick: `_tick_environment`, `_tick_flow_fields`, `_tick_fire_spread`, `_tick_poison_spread`, `_apply_tile_entry`, `_ambient_sounds`, `_tick_simple_statuses`, `_tick_tile_durations`, `_tick_event_timers`
- Trigger system: `_trigger_event`, `_tick_triggers`, `_fire_triggers`, `_fire_damage_triggers`, `_fire_death_triggers`, `_fire_lethal_damage_triggers`, `_trigger_matches`, `_fill_trigger_effect_defaults`. Optional trigger `when` predicates are evaluated through `conditions.py`.
- Curse enforcement: accepted wild-magic resolutions are checked against active mechanical
  curse limits before mutation. Semantic curses stay LLM-facing context; known mechanical
  curses can enforce range, area radius, line of sight, or forbidden effect families.
  Player-attributed enemy kills award placeholder experience used for curse clearing.

`GameEngine` inherits from five mixins (all via `self.*` — no wrapper code needed):
```
GameEngine(_CombatMixin, _ItemsMixin, _AIMixin, _GenerationMixin, _EffectsMixin)
```

### `wildmagic/state_view.py`
The read-only state surface over `GameState`. `GameState` stays the single source of truth;
this module turns it into the compact, stable packets each consumer needs without mutating
anything. It holds one builder per public-dict shape — `entity_card`, `item_card`,
`tile_card`, `room_card`, `selected_target_card`, `scene_notes_card`, `nearby_tile_details` —
and composes them into the two top-level views: `spell_context_view` (the resolver packet
returned by `engine.context_for_llm`) and `state_summary` (exposed as `replay_summary_view`
for `actions.summarize_state`/replay records and `inspection_view` for CLI/GUI inspection).
Everything here is pure reads; `tile_counts` lives here too. See
`docs/WILD_MAGIC_STATE_SURFACE_PLAN.md` (Stage 2). Imports only leaf modules
(`models`, `capabilities`, `spell_contract`, `templates`), never `engine`, so it sits below
the engine in the import order despite reading from it at call time. Active `tile_flows`
surface through tile detail cards and replay/inspection summaries.

### `wildmagic/refs.py`
Normalized references + selectors for resolver JSON (Stage 3). `normalize_ref(value)` turns a
legacy string (`"player"`, `"nearest_enemy"`, `"there"`, an entity id) or a typed ref
(`{kind: entity|tile|room|faction}`, `{selector: …}`) into a single `Ref`. `bind_ref`,
`bind_position`, and `bind_group` are the engine-authoritative resolvers (to an entity, a tile
position, or a group). Legacy strings are bound through the exact pre-refs logic, so existing
JSON is unchanged; typed refs add explicit entity/tile/room/faction targeting. Routed through
by `engine.resolve_target`/`resolve_target_group` and by `effects.effect_position`/
`resolve_placement`. Pure resolution (reads engine, never mutates); imports only `normalize`,
never `engine`.

### `wildmagic/conditions.py`
Pure predicate evaluator for trigger `when` clauses. Supports small deterministic predicates
such as `hp_below`, `hp_above`, `hp_parity`, `inventory_empty`, `on_terrain`,
`step_multiple`, `count_visible`, and `same_spell_streak`. It reads engine/state/event data
only and never mutates or calls providers, so conditional triggers remain replay-safe.

### `wildmagic/behaviors.py`
Normalization and storage helpers for temporary AI behavior modifiers. `set_behavior` writes
modifiers into `Entity.details["behavior_modifiers"]`, and `ai.py` reads them to alter target
selection or action choice (`dance`, `coward`, `duel`, `lowest_hp`, `mimic`,
`freeze_dread`). The helpers also tick modifier durations and keep the behavior vocabulary
separate from ordinary status names.

### `wildmagic/operations.py`
Engine operation primitives + state deltas (Stages 6-7). `StateDelta` is the compact,
observable record of one mutation; the typed primitives (`apply_damage`, `heal`, `apply_status`,
`create_tile`, `move_entity`, `create_actor`/`create_item`) delegate to the engine's shared
mutators, which record a delta while a cast is capturing. Stage 7 adds the durable world-memory
lanes: `write_trait` (entity), `write_semantic_note`/`write_place_note`/`write_faction_note`/
`write_world_note` (place/faction/world), `adjust_faction` (standing), and `emit_deed`. Capture
is owned by the engine (`begin_delta_capture`/`end_delta_capture`/`discard_deltas`/`record_delta`),
scoped to the effects+costs window of `apply_wild_magic_resolution`; the collected deltas ride
out on `WildMagicOutcome.deltas` and are discarded on rollback. Imports only `semantics` +
`models` types, never `engine`.

### `wildmagic/effect_registry.py`
The single metadata home for effects (Stage 4). `EffectSpec` records each effect's canonical
name, one-line summary, whether it is a universal core effect or owned by capability cards,
the context slices it needs, the alias type-strings that normalize to it, and the JSON fields
its handler reads. `REGISTRY` covers every `SUPPORTED_EFFECTS` entry; tests assert it cannot
drift from the contract, the capability cards, the alias map, or the schema doc. The alias map
`EFFECT_TYPE_ALIASES` lives here and is imported by `resolution_parsing.py`. Metadata only — it
does not move handler logic; `effects._apply_effect` still owns application. Imports only
`spell_contract` and `capabilities`.

### `wildmagic/combat.py` — `_CombatMixin`
Everything that changes HP or resolves physical contact:
`equipment_bonus`, `effective_attack`, `effective_defense`, `attack`, `_is_canonical`,
`damage_entity`, `_conduct_lightning_through_water`, `_modified_damage`, `heal_entity`,
`_split_slime`, `_drop_loot`, `_on_entity_death`.

### `wildmagic/curses.py`
Curse catalogue and curse-facing helpers. Defines known mixed/mechanical curse templates
(`Close Curse`, `Far Curse`, `Narrow Curse`, `Straight Path Curse`, `Anchored Curse`) plus
semantic templates for recurring costs such as `Wild Debt`; normalizes new curse payloads
from the resolver, builds public curse cards for context/UI/CLI, resolves curse names for
clearing commands, and validates accepted wild-magic resolutions against engine-owned
mechanical curse limits.

### `wildmagic/items.py` — `_ItemsMixin`
Inventory management and item use:
`spawn_item`, `use_item`, `drop_item`, `find_inventory_item`, `find_item_in`,
`consume_inventory_item`, `add_inventory_item`, `_apply_item_use_spec`, `_apply_item_effect`,
`_roll_item_amount`, `equip_item`, `unequip_item`, `pick_up_items_at_player`.
Also holds `_EQUIPMENT_SLOT_ALIASES`.

### `wildmagic/ai.py` — `_AIMixin`
NPC perception and turn execution:
`can_sense`, `_select_target`, `_update_npc_perceptions`, `_enemy_turns`, `_enemy_single_action`,
`_try_enemy_summon`, `_ally_turns`, `_npc_turns`, `_process_entity_behaviors`,
`_behavior_targets`, `next_path_step`, `_flee_step`, `path_neighbors`.
Reads temporary behavior modifiers from `behaviors.py`: duel/lowest-HP target selection,
dance/no-attack movement, cowardly flight from visible blood, mimic movement, and
freeze-dread no-ops. Also holds the `_AURA_RE` regex used for aura parsing.

### `wildmagic/generation.py` — `_GenerationMixin`
All map and world generation (41 methods):

- **Dungeon** — `_generate_dungeon_floor`, `_carve_room`, `_carve_corridor`, `_carve_h_tunnel`,
  `_carve_v_tunnel`, `_mirror_room`, `_carve_room_mirrored`, `_carve_corridor_straight`,
  `_carve_corridor_mirrored`, `_place_doors`, `_place_doors_mirrored`, `_floor_reachable`,
  `_place_locked_door`, `_random_open_tile_in_room`
- **Scenarios** — `_generate_new_run`, `_generate_test_chamber`, `_generate_empire_compound`,
  `_generate_town_start`, `_generate_frontier_start`
- **Frontier/open zones** — `_generate_open_zone`, `_scatter_terrain_features`,
  `_place_zone_buildings`, `_build_common_structure`, `_build_imperial_structure`,
  `_wall_room_perimeter`, `_realize_zone_promises`, `_build_promise_structure`,
  `_populate_promise_structure`, `_populate_zone`, `_spawn_from_template`,
  `_random_open_ground_tile`, `_find_entry_tile`. Open-zone promise realization uses
  data-driven site archetypes: `sacred_site`, `inhabited_site`, `hostile_site`,
  `memorial_site`, `hidden_site`, `creature_site`, and `authority_site`. Each archetype
  defines structure style, footprint, props, optional NPC role, and optional hostile count.
- **LLM towns** — `_generate_llm_town`, `_draw_road_through_zone`, `_build_town_context`,
  `_get_town_spec`, `_maybe_pregenerate_adjacent_towns`. Town context includes
  `promise_hooks` reserved for that specific zone; generated towns can fold one into
  description/NPC/building content and mark it `realized`.
- **Zone navigation** — `_cross_zone_edge`, `_save_current_zone`, `_load_or_generate_zone`
- **Road network** — `_road_anchor`, `_zone_is_road`, `_road_edges`, `_zone_should_be_town`

Generation emits `RoomProfile` semantic labels (type, era, condition, topics, tags,
promise hooks, and future secret slots) for dungeon rooms, Hollowmere buildings,
generated town buildings, frontier structures, and realized promise sites. Labels are
saved in `ZoneSnapshot` / dungeon-floor snapshots and bias prop selection without
consuming gameplay RNG. Realized promise sites also write `CanonRecord` entries for the
site, keeper, and flesh-described prop details, making the fulfilled story retrievable
by later prompts.

### `wildmagic/effects.py` — `_EffectsMixin`
Wild magic resolution and every effect/cost handler:

- `apply_wild_magic_resolution` — top-level entry point; validates the spell contract,
  snapshots state transactionally, fires `on_next_spell` triggers, iterates the resolution's
  `effects` and `costs` arrays, and rolls back on validation/application failure
- `_apply_effect` — dispatches on `effect["type"]` for 35+ effect types: `damage`,
  `area_damage`, `area_status`, `heal`, `restore_mana`, `teleport`, `push/pull`,
  `create_tile/set_tile`, `add_status`, `remove_status`, `summon`, `spawn_item`,
  `conjure_item`, `conjure_creature`, `transform_item`, `modify_inventory`,
  `transform_entity`, `change_faction`, `add_tag/remove_tag`, `add_resistance/add_weakness`,
  `set_flag`, `schedule_event`, `delay_incoming`, `accelerate_status`, `set_behavior`,
  `create_flow`, `create_trigger/ward`, `create_persistent_effect`, `create_promise`, `possess`,
  `edit_memory`, `animate_object`, `aura`, `add_trait`, `add_curse`, `message`. The
  authoritative operation catalogue and generated documentation metadata live in
  `spell_contract.py`.
- `_apply_cost` — dispatches on `cost["type"]`: `mana`, `health/hp`, `max_health`,
  `max_mana`, `item`, `curse`, `status`
- Placement helpers: `effect_position`, `resolve_placement`, `random_visible_floor`,
  `find_open_tile_near`, `find_open_tile_near_wall`
- Geometry helpers: `shape_points`, `points_in_radius`, `entities_in_radius`, `push_entity`
- Template conjuring: `_conjure_item`, `_conjure_creature`

---

## LLM layer

### `wildmagic/wild_magic.py`
The LLM provider layer. This file once held all four provider subsystems; dialogue,
trade, and town generation have since been split into their own modules (below), so
`wild_magic.py` now owns the **wild-magic spell** subsystem proper:

- `WildMagicProvider` / `OllamaWildMagicProvider` / `MockWildMagicProvider` → `MagicResolution`

`make_provider` uses `wildmagic.config` to select the active backend. Ollama-backed
providers carry a purpose label (here, `wild`) so config can route urgent and background
requests to different Ollama hosts.

Owns **orchestration**: `resolve_spell` (prompt build → provider call → retry → audit),
`_wild_prompt_messages` (input/prompt assembly), and the wild-magic audit log writer. The
**output parsing** that turns the model's raw text into a normalized resolution dict was
split into `resolution_parsing.py` (below); `resolve_spell` calls `parse_resolution_json`
from there.

Spell operation constants, status flavor aliases, structural validation, and the
JSON Schema used for constrained spell decoding live in `spell_contract.py`. Dialogue,
trade, town generation, resolution parsing, and Ollama model-list helpers are imported
from their own modules rather than through this spell module.

### `wildmagic/resolution_parsing.py`
The spell-resolution **output parser**: a pure `str → dict` transform with no I/O or
network dependency. `parse_resolution_json` strips `<think>` text, extracts the JSON, and
runs `_normalize_resolution` — a large defensive normalizer that reconciles the many
shapes a local model emits (singular vs. plural effects/costs, nested `outcome`/`details`,
element-name and flavor-status aliases, trigger/schedule restructuring, natural-language
effect inference via `_effect_from_text` / `_infer_effect_from_fields`) into the exact
shape `spell_contract.validate_resolution` expects. Also home to `_nearest_enemy_id`
(target resolution helper used by the Ollama provider).

### `wildmagic/dialogue.py`
NPC dialogue provider stack (`DialogueProvider` / `Ollama` / `Mock` / `Auto` →
`DialogueResolution`), `make_dialogue_provider`, `resolve_dialogue`, and the degenerate-reply
guards (`_is_degenerate_echo`, `_is_self_repetition`). Replies are plain prose — no JSON
schema — and the model is swappable independently of spell resolution.

### `wildmagic/trade.py`
Trade provider stack (`TradeProvider` / `Ollama` / `Mock` / `Auto` → `TradeResolution`),
`make_trade_provider`, `parse_trade_json`, `validate_trade_resolution`, and
`resolve_trade_proposal`. A small structured-JSON surface decoupled from dialogue prose.

### `wildmagic/town_gen.py`
Town generation provider (`TownProvider` / `Ollama` / `Mock` / `Auto` → `TownSpec`),
`make_town_provider`, the `BuildingSpec` / `NpcSpec` / `TownSpec` dataclasses, and
`_parse_town_spec`. One JSON call produces a full settlement spec.

### `wildmagic/lore.py`
Dialogue-derived lore extraction. Defines `LoreExtractionProvider` plus Ollama/mock/auto
providers, `LoreExtractionResolution`, JSON parsing/normalization into `WorldPromise`, and
`write_lore_audit_log`. The Ollama provider uses purpose `lore`, which routes through the
background Ollama settings by default; lore `num_gpu` defaults to `0` unless overridden.
Extracted promises are stored in the unified Promise Ledger rather than a separate lore
claim list.

### `wildmagic/file_lore_cards.py`
Parser for file-backed authored lore cards under `content/lore/`. Each Markdown file has a
`toml lore` block for topic metadata and one or more `## Level N` sections with `toml meta`
router descriptions, triggers, optional subjects, version, and draft flags. The parser
returns neutral `FileLoreSection` records, rejects malformed metadata and duplicate section
names, and excludes draft sections from the live load path unless explicitly requested.

### `wildmagic/lore_cards.py`
Pure access-gate and relevance-selection functions for authored world knowledge. The live
`LORE_CARDS` registry is now adapted from `content/lore/*.md` via
`wildmagic/file_lore_cards.py`. `LoreCard` records carry stable ids, access tags/thresholds,
router triggers/descriptions, injected text, and file-source metadata. This module still has
no provider calls; `lore_router.py` injects optional model routing.

### `wildmagic/canon.py`
Materialized canon generation for room, object, and text details (`examine`, `read`,
`investigate`). Defines `CanonProvider` plus Ollama/mock/auto providers,
`CanonResolution`, JSON parsing/normalization into `CanonRecord`,
`make_canon_provider()`, `make_background_canon_provider()`, `resolve_canon()` (with
malformed-response retries), and `logs/canon_audit.jsonl` writing. The Ollama provider
uses the `canon` purpose for blocking calls, which routes URGENT (GPU-resident main
model); background canon saturation uses the `lore`/BACKGROUND route and can override
the canon model via `WILDMAGIC_BACKGROUND_CANON_MODEL`. The always-on book pipeline runs
first, nearest-first: `book_title` for every book in the zone, then full `book` pages for
nearby visible books (so `read` is instant). The flag-gated saturation set
(`room_flavor`, far-look entity detail) runs behind it. The queue advances on player
turns and on UI idle frames (`GameSession.pump_canon_prewarm`); the default depth of 2
keeps one job running and one queued on the single-worker route, re-picked by proximity
as a slot frees. `GameSession.canon_queue_snapshot()` exposes a read-only view of the
queue (in-flight jobs + every zone book's title/pages state) that the pygame UI renders
as a scrollable **F7** debug overlay.
The engine supplies attachments, tags, allowed outputs, and mechanical choices; the
provider supplies wording and nonmechanical choices only.

### `wildmagic/texture.py`
Layer-1 procedural texture grammars: instant, model-free naming for bulk content.
Currently `grammar_book()`, which gives placed books a concrete catalog-style name and
description plus a richer hidden shelf card (`topic`, `secondary_topic`, `genre`,
`discipline`, `author_role`, `audience`, `purpose`, `stance`, `institution`,
`title_shape`, `taboo_level`) plus 1-4 durable `subjects` (the title-call seed and
lore-router key). The subject picker rotates between room-colored general subjects and
known lore-card subjects, so about half of procedural books can pull authored world canon
while the rest stay local, practical, or odd. Printed titles materialize through the
always-on background `book_title` pass, and nearby visible books prewarm their full pages
after the title exists; unreadied books materialize on first `read`.

### `wildmagic/secrets.py`
Engine-owned secret resolution for the `investigate` verb: difficulty→turn costs,
deterministic anchor selection among room props, and tag-keyed reward tables. The engine
fixes whether a secret exists (RoomProfile secret slots placed at generation), what
anchors it, and what the reward is before any model is prompted; the LLM only words the
clue. No provider calls live here.

### `wildmagic/promises.py`
Promise Ledger data types and prompt-shaping helpers. Defines `WorldPromise`,
`SpatialHint`, `PromiseBinding`, typed `Objective`/`Reward` stubs, lifecycle/kind constants,
`PromiseReservation`, deterministic binding helpers, serialization helpers, and
`promise_context_for_prompt()`. `GameState.promises` is capped to 200 entries. Repeated
subject/tag-overlap promises merge into the existing entry, bump salience/confidence, and
can mark it `corroborated`; old low-salience entries are evicted first.
Buildable promises bind immediately at the lore-drain/action boundary. Directional,
terrain, and wildcard hints can reserve future zones, with a default capacity of two
promise realizations per zone and directional overflow spilling outward.

### `wildmagic/llm_client.py`
Raw Ollama HTTP transport, completely decoupled from game logic:
`_post_ollama_chat`, `_post_ollama_chat_with_json_retry`, `parse_ollama_error_body`,
`strip_thinking`, `extract_thinking`, `normalize_ollama_url`, and `fetch_ollama_models`.
Provider modules import request configuration directly from `wildmagic.config`; this
module owns transport behavior only.

### `wildmagic/config.py`
The single configuration boundary. Loads the project `.env` without overriding shell
values, owns defaults and typed parsing, resolves provider/model fallback chains,
persists in-game configuration changes back to `.env`, and owns purpose-scoped Ollama
routing precedence for `WILDMAGIC_WILD_OLLAMA_HOST`, `WILDMAGIC_URGENT_OLLAMA_HOST`,
`WILDMAGIC_BACKGROUND_OLLAMA_HOST`, and matching scoped request options such as
`OLLAMA_NUM_CTX`, `OLLAMA_TIMEOUT`, `OLLAMA_NUM_GPU`, `OLLAMA_THINK`,
`OLLAMA_FORMAT`, and `OLLAMA_KEEP_ALIVE`. Lore has first-class config via
`WILDMAGIC_LORE_ENABLED`, `WILDMAGIC_LORE_PROVIDER`, `WILDMAGIC_LORE_MODEL`,
`WILDMAGIC_LORE_NUM_PREDICT`, and purpose-scoped Ollama overrides.

### `wildmagic/llm_resolver.py`
Shared retry and audit utilities:
`_write_jsonl_audit` (JSONL append helper shared by all three audit log writers),
`should_retry_resolution`, `retry_context`.

### `wildmagic/spell_contract.py`
Wild-magic contract data that is shared by resolver and engine code:
`SUPPORTED_EFFECTS`, `SUPPORTED_COSTS`, `STATUS_FLAVOR_ALIASES`,
`EFFECT_DOCUMENTATION`, `COST_DOCUMENTATION`, `SPELL_RESPONSE_JSON_SCHEMA`,
`render_operation_reference`, `update_operation_reference`, and `validate_resolution`.
Run `python -m wildmagic.spell_contract --write-docs` after changing the operation
catalogue to refresh the generated block in `docs/WILD_MAGIC_SCHEMA.md`.

### `wildmagic/prompts.py`
System prompt strings only — `SYSTEM_PROMPT`, `DIALOGUE_SYSTEM_PROMPT`,
`TRADE_SYSTEM_PROMPT`, `TOWN_SYSTEM_PROMPT`, `LORE_EXTRACTION_SYSTEM_PROMPT`,
`PROPS_SYSTEM_PROMPT`, `DEED_INTERPRETER_SYSTEM_PROMPT`, `FLESH_SYSTEM_PROMPT`, and
`CANON_SYSTEM_PROMPT`. No logic; imported by the provider modules.

### `wildmagic/fallbacks.py`
Pure-Python regex spell parser used when the LLM is unavailable or returns garbage.
Recognises common spell patterns (force wave, delayed arrival, etc.) and produces a
minimal `MagicResolution`-shaped dict. Controlled by the `WILDMAGIC_ENABLE_FALLBACKS`
env var.

---

## Emergent world (deeds → legend → standing → simulation)

The emergent-world systems (`docs/EMERGENT_WORLD_STRATEGY.md` / `EMERGENT_WORLD_IMPLEMENTATION.md`,
Phases 0–F) turn what the player *does* into a world that reacts. The spine is deterministic;
the LLM is used only where meaning is genuinely ambiguous, always with a deterministic
fallback, and recorded at its apply point so replays are free.

**The loop:** an action becomes a **Deed** → a declarative rules table (or, for ambiguous
spell outcomes, the LLM interpreter) reads it into multi-axis **standing** shifts + **legend**
tags → the daily **Simulator** tick applies them, spends Empire defenses toward the
kill-emperor gate, mints **backlash** (crackdowns/resistance), and drifts every NPC's
**bond** → the world *shows* it (rumors, wanted posters, consequence props, situation
reports, followers). All new state is in `GameState`, surfaces in `summarize_state`, and
reproduces under replay.

### `wildmagic/deeds.py`
`Deed` (the atom: type, magnitude, actor = player **soul** id, `place_key` = zone+depth,
visibility/witnesses, proposed `standing_deltas`/`legend_tags`, `applied` flag for
idempotency), `DeedLedger` (append-only + `compress()` into `StoryBeat`s), the bounded
vocab (`DEED_TYPES`/`TARGET_TAGS`/`VISIBILITY`), and the **declarative `DEED_RULES`** table
+ `interpret_deed_rules` (one deed → different consequences on different axes; keyed by
faction **role**, not literal id).

### `wildmagic/factions.py`
`Faction` (multidimensional `standing`, spendable `resources`, `mood`, `player_rank`,
affiliations) and `FactionLedger` with **stable role queries** (`by_kind`/`ids_by_role`/
`primary`) over `ROLE_TO_KINDS`, so the emergent systems target roles (empire bloc /
resistance / player_org) and generalize to Phase C's full roster. `seed_phase0_factions`
is the two-pole scaffold (kept minimal); never carried between runs.

### `wildmagic/legend.py`
`LegendLedger` — bounded-vocab (`LEGEND_VOCAB`) weighted legend tags per actor soul, the
**mechanical** truth the simulator/dialogue/scores read; a prose mirror is written to the
semantic ledger for prompts (the two-form split keeps the ledger's "engine never reads
notes for outcomes" contract).

### `wildmagic/bonds.py`
`Bond` — every NPC's *personal* relationship to the player (loyalty/fear/admiration/
resentment/ideology + `hidden_pressure` + `affiliations`), one of three orthogonal layers
(combat faction / org membership / bond). `drift_bond` scores legend × traits × memory.

Structured NPC memories supply a provenance-weighted personal multiplier for reputation
drift; legacy string memories preserve the old firsthand-memory behavior.

### `wildmagic/deed_interpreter.py`
The Phase-A.2 LLM provider that classifies *ambiguous* spell outcomes (raise-dead, raze,
desecrate, atrocity) into the bounded deed vocabulary — consequences still come from the
rules table. Provider stack (Ollama/Mock/Auto, `off`→None), a cheap candidate **gate** so
ordinary spells make zero calls, a conservative deterministic **fallback**, and a verdict
recorded on the wild-magic action record so replays reproduce the deed with no model call.

### Engine integration (`engine.py`)
`GameState` gains `deed_ledger`/`faction_ledger`/`legend_ledger`, `player_soul_id`, the
day/night clock (derived from `turn`), placeholder `experience` for curse clearing,
`ticked_through_day`, `pending_backlash`, `gossip_edges` for deterministic NPC memory
spread, and `gossip_spread_days` so repeated daily ticks cannot push rumors another hop in
the same in-world day. The daily
**Simulator** is `_maybe_run_daily_tick` (fires once per in-game day at 05:00):
`run_world_tick` (apply deeds → standing + legend + compress) · `_simulate_empire_pressure`
(D9 kill-emperor gate) · `_simulate_backlash` (factions spend to act) · `_simulate_bonds`
(bond drift + follower moments). `_on_enter_location` narrates on arrival (rumors,
`_render_deed_consequences`, `_realize_backlash`). `camp_rest` and `found_organization` are
the player levers.

---

## Data layer

### `wildmagic/models.py`
All shared data types and tile constants. No game logic.

- Tile string constants: `FLOOR`, `WALL`, `DOOR`, `OPEN_DOOR`, `STAIRS_DOWN`, `STAIRS_UP`,
  `WATER`, `FIRE`, `SLICK_ICE`, `ICE_WALL`, `POISON_CLOUD`, `VINES`, `RUBBLE`, `MIST`, `ROAD`
- Derived tile sets: `BLOCKING_TILES`, `DAMAGING_TILES`, `TILE_NAMES`, `TILE_TAGS`, `TILE_ALIASES`
- Status/damage type catalogues: `MECHANICAL_STATUSES`, `DAMAGE_TYPES`
- Dataclasses: `Entity`, `Curse`, `NPCMemoryRecord`, `GossipEdge`, `NPCProfile`,
  `GameStats`, `WildMagicOutcome`, `Room`, `RoomProfile`, `CanonRecord`, `ZoneSnapshot`.
  `NPCMemoryRecord` stores neutral memory claims with provenance, bucket, subject refs,
  salience, privacy, and shareability; `NPCProfile` renders those records into dialogue
  buckets while keeping the legacy `memory: list[str]` mirror compatible. `GossipEdge`
  stores deterministic NPC-to-NPC social spread links. `RoomProfile` is the deterministic
  semantic seed layer for richer content; `CanonRecord` stores per-run materialized text
  or descriptions that have become game canon. `Entity.details` stores engine-side
  nonmechanical metadata such as a book's procedural shelf card; feature-specific
  context builders decide what, if anything, becomes visible to an LLM.
  `Curse` stores both semantic prompt text and optional engine-owned mechanics; unknown
  curses are semantic by default.

### `wildmagic/game_data.py`
All hand-authored game content and tunable constants:

- Map dimensions (`MAP_WIDTH`, `MAP_HEIGHT`), perception radius
- Enemy template lists: `WILD_ENEMY_TEMPLATES`, `LEGION_ENEMY_TEMPLATES`
- Faction hostility table: `FACTION_HOSTILITIES`
- Item use specs: `ITEM_USE_SPECS`, `DEFAULT_ITEM_USE_SPEC`
- Equipment specs: `EQUIPMENT_SPECS`
- Trap specs: `TRAP_SPECS`, `LOCKED_DOOR_KEYS`
- Town generation data: `_TOWN_LOCATIONS`, `_TOWN_DEFINING_TRAITS`, `_TOWN_SITUATIONS`,
  `_TOWN_SETTLEMENT_TYPES`, `_TOWN_GEN_TIMEOUT`, `_BUILDING_SIZES`, `_DEFAULT_BUILDING_SIZE`,
  `_ROLE_STATS`, `_DEFAULT_NPC_STATS`
- Trade keyword detection: `TRADE_KEYWORDS`, `scan_for_trade_intent`

### `wildmagic/templates.py`
Frozen dataclasses `ItemTemplate` and `CreatureTemplate` plus their catalogues
(`ITEM_TEMPLATES`, `CREATURE_TEMPLATES`). Look-up functions `item_template`,
`creature_template`, `item_template_ids`, `creature_template_ids`. Used by `_conjure_item`
and `_conjure_creature` in `effects.py` and by `_spawn_from_template` in `generation.py`.

### `wildmagic/props.py`
Static environmental scenery that the LLM can target as spell anchors. Frozen dataclass
`PropTemplate` (`id`, `char`, `name`, `description`, `blocks`, `tags`). `PROP_TEMPLATES`
dict of ~130 props across ten thematic categories: Arcane & Ritual, Ruined & Abandoned,
Old Traditions (buried strata of pre-charter magic), Imperial, Saltmarket/Vint,
Natural & Overgrown, Dungeon Infrastructure, Alchemical, Religious, Furniture. Look-up functions
`get_prop_template` and `get_all_prop_ids`. Props are spawned via `engine.spawn_prop()`,
stored as `Entity(kind="prop")`, and appear in the LLM context's `nearby_entities` list
with their description and tags once visible. `GameEngine.nearby_spell_anchors()` also
distills visible props into a compact `spell_anchors` context list with tag-derived
affordances so the resolver is more likely to use scenery as a center, origin, or
thematic anchor for normal effects.

### `wildmagic/regions.py`
Regions as first-class data (see `EXECUTION_PLAN.md` Phase 13). Frozen dataclass `Region`
bundling everything about "where you are": LLM voice spec + example outcome lines,
enemy template pool, `imperial_presence`, floor theme weights, ambient message tables,
wildness-banded wonder lines, and `wildness_base` (effective wildness = base + depth).
`REGIONS` registry, `get_region`, and `region_for_zone(zx, zy)` mapping overworld zones to
regions. Consumed by `engine.region` (a property over `state.region_id`), generation,
ambience, and the wild-magic prompt builder (`region_style` in the LLM context, spliced
into the system prompt by `_wild_prompt_messages` in `wild_magic.py`).

### `content/lore/`
File-backed authored lore-card source of truth. Each topic is a Markdown file with fenced
TOML metadata and gated `## Level N` sections. Level 0 sections are public/common
knowledge; higher sections are exposed only when the knower's lore stat reaches that level.
Router-facing descriptions and injected bodies both come from these files, but inaccessible
sections are omitted from model-facing prompts.

### `wildmagic/npc_quests.py`
Quest-promise producer logic and data. Defines the `QUEST_ITEMS` dictionary mapping special unique quest items to their visual/materials/tags specs. When an NPC request is heard, `register_heard_quest_item()` appends a `WorldPromise(kind="quest")` with typed fetch objective/reward data. Quest item placement happens when that promise's reserved site realizes; there is no independent random zone-entry quest-item spawner. `generate_npc_quest()` still supplies request fields for procedural NPC profiles.

### `wildmagic/normalize.py`
Pure functions for sanitising and coercing LLM output. No side effects, no imports outside
`models.py`. Key exports: `clamp_int`, `normalize_id`, `normalize_faction`,
`normalize_trigger_name`, `status_duration`, `optional_duration`, `parse_tile_key`,
`sanitize_name`, `sanitize_char`, `coerce_list`, `tile_from_name`, `_flatten_effect`,
`area_damage_affects`, `infer_behavior_tags`, `normalize_numeric_map`, `singular_target_tag`.

### `wildmagic/geometry.py`
Pure coordinate math with no imports outside stdlib:
`sign`, `bresenham_line`, `unique_points`, `_on_bresenham` (point-on-line test used by the
road network in `generation.py`).

### `wildmagic/determinism.py`
Stable deterministic helpers. `stable_seed()` derives process-stable integer seeds from
arbitrary seed parts so procedural zones/towns do not depend on Python's randomized
process-local `hash()` implementation.

---

## Dev / test

### `wildmagic/speleval.py` + `wildmagic/speleval_corpus.py`
Spell-resolution eval harness (`python -m wildmagic.speleval`): runs a 107-spell
intent-tagged corpus (common / creative / exploit) through the full cast pipeline on a
fresh deterministic session per spell and scores resolution/rejection/technical rates,
hallucinated targets, exploit leakage, and latency. `--from-audit` re-validates recorded
raw responses from an audit JSONL under the current contract code (offline regression
check). Eval traffic is audited to `logs/speleval/` instead of the main audit log.

### `wildmagic/dialogue_eval.py`
Dialogue-model comparison harness (`python -m wildmagic.dialogue_eval`): runs fixed
Hollowmere NPC prompts through the real Ollama dialogue provider path, scores simple
failure/genericness/grounding heuristics, prints a comparison table, and writes a JSON
report under `logs/dialogue_eval/`.

### `wildmagic/lore_eval.py`
Lore-extraction eval harness (`python -m wildmagic.lore_eval`): reads saved dialogue eval
reports, runs each NPC reply through the lore extractor provider, and writes the extracted
promises plus technical failures under `logs/lore_eval/`.

### `wildmagic/smoke_test.py`
Headless integration test. Creates a `test_chamber` session with `MockWildMagicProvider`,
fires a movement, a well-formed spell, a malformed response, and a rejection scenario,
then asserts the turn counter, HP, mana, and log contents. Run with
`python -m wildmagic.smoke_test`.

### `wildmagic/__init__.py`
Imports the configuration boundary so `.env` is loaded at package import time;
makes `wildmagic` a package.

---

## Dependency graph (simplified)

```
main.py / cli.py
    └── actions.py (GameSession)
            ├── engine.py (GameEngine + GameState)
            │       ├── combat.py   (_CombatMixin)
            │       ├── items.py    (_ItemsMixin)
            │       ├── ai.py       (_AIMixin)
            │       ├── generation.py (_GenerationMixin)
            │       ├── effects.py  (_EffectsMixin)
            │       ├── state_view.py (read-only views: resolver context, replay, inspection)
            │       ├── refs.py       (normalized refs/selectors: bind_ref/position/group)
            │       ├── operations.py (operation primitives + StateDelta capture)
            │       ├── effect_registry.py (effect metadata + EFFECT_TYPE_ALIASES)
            │       └── curses.py (curse templates + mechanical validation)
            ├── wild_magic.py (spell provider + resolve_spell orchestration)
            │       ├── resolution_parsing.py (raw LLM text -> normalized effect dict)
            │       ├── llm_client.py   (Ollama HTTP)
            │       ├── llm_resolver.py (audit + retry)
            │       ├── prompts.py      (system prompts)
            │       ├── spell_contract.py (spell schema + validation)
            │       └── fallbacks.py    (regex fallback)
            ├── dialogue.py (NPC dialogue provider + resolution)
            ├── trade.py    (trade provider + resolution)
            ├── town_gen.py (town generation provider)
            ├── lore.py (dialogue promise extraction)
            │       ├── llm_client.py
            │       ├── llm_resolver.py
            │       └── prompts.py
            └── deed_interpreter.py (ambiguous-spell-outcome → deed; LLM + fallback)
                    ├── deeds.py (DEED_RULES, vocab)
                    ├── llm_client.py / llm_resolver.py / prompts.py

Emergent-world leaves (imported by engine; import only stdlib + each other):
    bonds.py
    deeds.py    ←  deed_interpreter.py
    factions.py
    legend.py

Shared leaves (imported by many, import nothing above them):
    models.py  ←  game_data.py  ←  templates.py
       ↑ (bond)                 ←  props.py
    bonds.py                    ←  npc_quests.py
    geometry.py
    determinism.py
    normalize.py
    conditions.py
    promises.py
```
