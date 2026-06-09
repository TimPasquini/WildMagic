# Architecture

Wild Magic is structured as a Python package (`wildmagic/`) with a clear separation between
data, logic, and presentation layers. The game engine is a single `GameEngine` class assembled
from mixin modules so each concern lives in its own file.

---

## Entry points

### `main.py`
One-liner that calls `wildmagic.ui.run_game()`. The GUI entry point.

### `wildmagic/ui.py`
Pygame front-end. Owns the game loop, renders the tile map and side panels, handles keyboard
input, and routes commands to `GameSession`. Also shows the LLM thinking panel and the model
selector overlay. Imports from `actions`, `models`, `game_data`, and `wild_magic` (for
`fetch_ollama_models` and prompt/thinking helpers).

### `wildmagic/cli.py`
Terminal front-end. Parses `--seed`, `--scenario`, `--provider`, `--script`, `--record` flags,
drives `GameSession` in a readline loop, and optionally saves a replay JSON at exit.

---

## Session and action layer

### `wildmagic/actions.py`
`GameSession` — the single object callers interact with. Wraps `GameEngine` and the LLM
providers. Exposes `process_command(text)` which routes movement, wait, open/close, stairs,
cast, talk, trade, and inventory commands. Returns an `ActionResult`. Also holds `summarize_state()`
(used by the replay system) and `to_replay()` / `from_replay()` serialization.

### `wildmagic/replay.py`
Save/load/run replay JSON files. `run_replay(path)` re-feeds a recorded session's commands back
through a fresh `GameSession` and optionally checks the final state snapshot against a saved
expectation. Used for regression testing.

---

## Game engine

### `wildmagic/engine.py`
`GameState` dataclass and the `GameEngine` class declaration. After the Phase 4–5 refactor,
engine.py contains only the infrastructure that everything else depends on:

- `GameState` — the serialisable game world (tiles, entities, inventory, turn counter, flags, etc.)
- `GameEngine.__init__` — seeds RNG, builds initial state, dispatches to scenario generators
- Utility queries: `in_bounds`, `tile_at`, `tile_key`, `is_visible`, `can_occupy`, `distance`, `entities_at`, `blocking_entity_at`, `living_enemies`, `is_hostile_to`
- FOV: `update_fov`, `has_line_of_sight`, `tile_blocks_sight`
- Tile mutation: `set_tile`, `tile_tags_at`, `_reacting_tile`
- Spawning: `spawn_actor`, `spawn_npc`, `next_entity_id`
- Player actions: `attempt_player_move`, `wait_turn`, `open_door`, `open_adjacent_door`, `descend_stairs`, `ascend_stairs`, `teleport_entity`, `_move_to_nearest_open_tile`
- Standard spells (deterministic, no LLM): `cast_standard_bolt`, `cast_standard_frost`, `cast_standard_heal`, `cast_standard_ward`, `cast_standard_reveal`
- NPC dialogue/trade: `find_talk_target`, `dialogue_context_for_llm`, `apply_dialogue_exchange`, `resolve_pending_trade`, `should_consider_trade`, `trade_context_for_llm`
- LLM context building: `context_for_llm`, `nearby_map_strings`, `nearby_tile_details`
- Turn bookkeeping: `finish_player_turn`, `_regenerate_player`, `resolve_target`, `resolve_target_group`, `nearest_enemy`, `_verb`
- Environment tick: `_tick_environment`, `_tick_fire_spread`, `_tick_poison_spread`, `_apply_tile_entry`, `_ambient_sounds`, `_tick_simple_statuses`, `_tick_tile_durations`, `_tick_event_timers`
- Trigger system: `_trigger_event`, `_tick_triggers`, `_fire_triggers`, `_fire_damage_triggers`, `_fire_death_triggers`, `_trigger_matches_target`, `_fill_trigger_effect_defaults`

`GameEngine` inherits from five mixins (all via `self.*` — no wrapper code needed):
```
GameEngine(_CombatMixin, _ItemsMixin, _AIMixin, _GenerationMixin, _EffectsMixin)
```

### `wildmagic/combat.py` — `_CombatMixin`
Everything that changes HP or resolves physical contact:
`equipment_bonus`, `effective_attack`, `effective_defense`, `attack`, `_is_canonical`,
`damage_entity`, `_conduct_lightning_through_water`, `_modified_damage`, `heal_entity`,
`_split_slime`, `_drop_loot`, `_on_entity_death`.

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
`_behavior_targets`, `enemy_can_sense_player`, `next_path_step`, `_flee_step`, `path_neighbors`.
Also holds the `_AURA_RE` regex used for aura parsing.

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
  `_wall_room_perimeter`, `_populate_zone`, `_spawn_from_template`, `_random_open_ground_tile`,
  `_find_entry_tile`
- **LLM towns** — `_generate_llm_town`, `_draw_road_through_zone`, `_build_town_context`,
  `_get_town_spec`, `_maybe_pregenerate_adjacent_towns`
- **Zone navigation** — `_cross_zone_edge`, `_save_current_zone`, `_load_or_generate_zone`
- **Road network** — `_road_anchor`, `_zone_is_road`, `_road_edges`, `_zone_should_be_town`

### `wildmagic/effects.py` — `_EffectsMixin`
Wild magic resolution and every effect/cost handler:

- `apply_wild_magic_resolution` — top-level entry point; fires `on_next_spell` triggers then
  iterates the resolution's `effects` and `costs` arrays
- `_apply_effect` — dispatches on `effect["type"]` for 25+ effect types: `damage`,
  `area_damage`, `area_status`, `heal`, `restore_mana`, `teleport`, `push/pull`,
  `create_tile/set_tile`, `add_status`, `remove_status`, `summon`, `spawn_item`,
  `conjure_item`, `conjure_creature`, `transform_item`, `modify_inventory`,
  `transform_entity`, `change_faction`, `add_tag/remove_tag`, `add_resistance/add_weakness`,
  `set_flag`, `schedule_event`, `create_trigger/ward`, `add_curse`, `message`
- `_apply_cost` — dispatches on `cost["type"]`: `mana`, `health/hp`, `max_health`,
  `max_mana`, `item`, `curse`, `status`
- Placement helpers: `effect_position`, `resolve_placement`, `random_visible_floor`,
  `find_open_tile_near`, `find_open_tile_near_wall`
- Geometry helpers: `shape_points`, `points_in_radius`, `entities_in_radius`, `push_entity`
- Template conjuring: `_conjure_item`, `_conjure_creature`

---

## LLM layer

### `wildmagic/wild_magic.py`
The LLM provider layer. Defines Protocol classes and concrete implementations for three
provider kinds, each returning a typed resolution object:

- `WildMagicProvider` / `OllamaWildMagicProvider` / `MockWildMagicProvider` → `MagicResolution`
- `DialogueProvider` / `OllamaDialogueProvider` / `MockDialogueProvider` → `DialogueResolution`
- `TradeProvider` / `OllamaTradeProvider` / `MockTradeProvider` → `TradeResolution`
- `TownProvider` / `OllamaTownProvider` / `MockTownProvider` → `TownSpec`

Factory functions `make_provider`, `make_dialogue_provider`, `make_trade_provider`,
`make_town_provider` read environment variables (`WILDMAGIC_PROVIDER`,
`WILDMAGIC_DIALOGUE_PROVIDER`, etc.) to select the active backend.

Also contains `resolve_spell`, `resolve_dialogue`, `resolve_trade_proposal`,
`_effect_from_text` (regex-based fallback parser), the audit log writers, and the
`_STATUS_FLAVOR_ALIASES` map used by effect/cost handlers.

### `wildmagic/llm_client.py`
Raw Ollama HTTP transport, completely decoupled from game logic:
`_post_ollama_chat`, `parse_ollama_error_body`, `strip_thinking`, `extract_thinking`,
`normalize_ollama_url`, `fetch_ollama_models`, and all 13 `ollama_*()` config readers
that pull values from environment variables.

### `wildmagic/llm_resolver.py`
Shared retry and audit utilities:
`_write_jsonl_audit` (JSONL append helper shared by all three audit log writers),
`should_retry_resolution`, `retry_context`.

### `wildmagic/prompts.py`
System prompt strings only — `SYSTEM_PROMPT`, `DIALOGUE_SYSTEM_PROMPT`,
`TRADE_SYSTEM_PROMPT`, `TOWN_SYSTEM_PROMPT`. No logic; imported by `wild_magic.py`
and re-exported for display in `ui.py`.

### `wildmagic/fallbacks.py`
Pure-Python regex spell parser used when the LLM is unavailable or returns garbage.
Recognises common spell patterns (force wave, delayed arrival, etc.) and produces a
minimal `MagicResolution`-shaped dict. Controlled by the `WILDMAGIC_ENABLE_FALLBACKS`
env var.

---

## Data layer

### `wildmagic/models.py`
All shared data types and tile constants. No game logic.

- Tile string constants: `FLOOR`, `WALL`, `DOOR`, `OPEN_DOOR`, `STAIRS_DOWN`, `STAIRS_UP`,
  `WATER`, `FIRE`, `SLICK_ICE`, `ICE_WALL`, `POISON_CLOUD`, `VINES`, `RUBBLE`, `MIST`, `ROAD`
- Derived tile sets: `BLOCKING_TILES`, `DAMAGING_TILES`, `TILE_NAMES`, `TILE_TAGS`, `TILE_ALIASES`
- Status/damage type catalogues: `MECHANICAL_STATUSES`, `DAMAGE_TYPES`
- Dataclasses: `Entity`, `Curse`, `NPCProfile`, `GameStats`, `WildMagicOutcome`, `Room`, `ZoneSnapshot`

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
dict of 30 props across five thematic categories: Arcane & Ritual, Ruined & Abandoned,
Macabre & Somber, Natural & Overgrown, Dungeon Infrastructure. Look-up functions
`get_prop_template` and `get_all_prop_ids`. Props are spawned via `engine.spawn_prop()`,
stored as `Entity(kind="prop")`, and appear in the LLM context's `nearby_entities` list
with their description and tags once visible.

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

---

## Dev / test

### `wildmagic/smoke_test.py`
Headless integration test. Creates a `test_chamber` session with `MockWildMagicProvider`,
fires a movement, a well-formed spell, a malformed response, and a rejection scenario,
then asserts the turn counter, HP, mana, and log contents. Run with
`python -m wildmagic.smoke_test`.

### `wildmagic/__init__.py`
Empty; makes `wildmagic` a package.

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
            │       └── effects.py  (_EffectsMixin)
            └── wild_magic.py (providers + resolution)
                    ├── llm_client.py   (Ollama HTTP)
                    ├── llm_resolver.py (audit + retry)
                    ├── prompts.py      (system prompts)
                    └── fallbacks.py    (regex fallback)

Shared leaves (imported by many, import nothing above them):
    models.py  ←  game_data.py  ←  templates.py
                                ←  props.py
    geometry.py
    normalize.py
```
