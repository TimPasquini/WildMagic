# Capability Card Plan — Taxonomy, Carve Map, and Backlog

Companion to `CAPABILITY_ROUTING.md` (the strategy) and `wildmagic/capabilities.py` (the
code). This doc is the concrete **card taxonomy**: what is core vs. a card, where each
card's content is carved from, its routing/compose metadata, and the backlog of new
capabilities the `SPELL_COMPENDIUM.md` space implies but the engine doesn't support yet.

Status legend: **live** = card built in `capabilities.py` (`CAPABILITY_CARDS`), engine
handler exists. **planned** = card sketched in `PLANNED_CARDS`, needs new engine work.
**core** = stays in the always-on prompt, never a card.

## 1. The core/card split

The compendium's high-frequency, universal sections map to the **always-on core** — they
appear in a huge fraction of casts and combine with everything, so gating them behind a
trigger would be pure risk for no token saving:

| Compendium section | Disposition | Core effect types |
|---|---|---|
| Combat — Direct Damage | **core** | `damage` |
| Combat — Area & Burst | **core** | `area_damage`, `area_status` |
| Status & Debuffs | **core** | `add_status`, `remove_status` |
| Healing & Support | **core** | `heal`, `restore_mana`, `add_status`, `add_resistance`, `add_weakness` |
| Terrain & Environment (fill/ring) | **core** | `create_tile(s)`, `set_tile` |
| Sacrifice & Risk; Creative Costs & Curses | **core** (cost catalog) | costs: `mana`, `health`, `max_health`, `max_mana`, `item`, `status`, `curse` |
| Environmental Exploitation | **core** | reuses tiles/anchors already in core |
| Standing Auras / Emanations | **core** | `aura` |

**Auras (added 2026-06-14).** `aura` is a core effect: a standing emanation that re-fires
every turn while it lasts, always coupled to a concrete mechanic (`kind: "damage"` or
`kind: "status"`). It can be anchored to any entity (`target: "player"`/actor id), to a
conjured creature (nest an `aura` object inside the `conjure_creature`/`summon` effect), or
to the ground (`target: "tile"` + x/y). Resolved in `GameEngine._tick_auras`; stored on
`Entity.auras` and `GameState.tile_auras`. It is the general superset of the older fixed
`aura_*` behavior tags (`ai.py._process_entity_behaviors`), which remain as a creature-only
shorthand — the two coexist and tick independently.

`CORE_EFFECT_TYPES` in `capabilities.py` is the authoritative list. The core also keeps the
contract shape, `outcome_text` voice, severity→magnitude ladder, the tile catalog, the
mechanical-status catalog, and a handful of representative examples. **Done (2026-06-13):**
`CORE_PROMPT` is carved (`capabilities.py`), `assemble_resolver_system_prompt` is the sole
resolver-prompt path, and the old monolithic `SYSTEM_PROMPT` + its flag/dual-path lane have
been **removed**. A coverage test guards that core + cards still cover every effect.

## 2. Live cards (built, engine-backed)

Seventeen cards in `CAPABILITY_CARDS`, content lifted from the old monolith (plus the three
promoted 2026-06-13, `disfigure` added 2026-06-14, and `sympathetic_link` + `persistent_effect`
promoted 2026-06-16).

| Card | Unlocks (effect types) | Carved from (compendium / monolith) | Common combos |
|---|---|---|---|
| `conjure_creature` | `summon`, `conjure_creature` | Summoning — Allies / Aura Bearers & Wards / Hazardous Creatures; monolith summon line + the whole behavior-tags block | `conjure_item` |
| `conjure_item` | `conjure_item`, `spawn_item`, `transform_item`, `modify_inventory` | "glass teeth", "webbing", "transmute the chalk"; monolith conjure_item/spawn_item lines + item templates | — |
| `transform_entity` | `transform_entity` | "turn the wolf into a chicken", "petrify", aging/withered-body stat changes, Strange & Esoteric polymorphs | `disfigure` |
| `disfigure` | *(none — uses core `add_status`/`damage`/`add_weakness`; introduces the `weakened` status)* | "turn his legs to iron", "boil his brain", "wither his sword-arm", "rot his flesh" — targeted body-part maiming | — |
| `faction_charm` | `change_faction`, `add_tag`, `remove_tag` | "charm/befriend", "make the weapon defect", "ally for one turn" | `transform_entity` |
| `barrier_shaping` | *(none — refines core `create_tiles`/`set_flag`)* | Terrain "wall of ice across the corridor", "line of ice east", "between me and them"; cave-ins; sealed stairs; the directional-shape balance rules | — |
| `divination` | *(none — refines core `add_status` 'revealed'/'sight_shrouded')* | Information & Divination section; reveal/track/sight-shroud balance rules | — |
| `triggers_reactions` | `create_trigger` | Combo & Conditional "next time X…"; predicate-gated triggers; lethal-damage and curse-gained hooks | `delayed_effects` |
| `delayed_effects` | `schedule_event`, `delay_incoming`, `accelerate_status` | "in five turns…", "comes back to collect", debt-later spells, delayed wounds, accelerated poison/fire/bleeding ticks | `triggers_reactions` |
| `prophecy` | `create_promise` | "I prophesy…", "somewhere north a chapel waits"; the create_promise block | — |
| `possession` | `possess` | "take over the guard", "see through the eyes of", "ride the beast" | — |
| `memory_edit` | `edit_memory` (NEW handler) | "make the nearest enemy forget me", "plant a false memory", "convince the guard…" | `faction_charm` |
| `structure_animation` | `animate_object` (NEW handler) | "make the brass door angry", "the statue steps down", "persuade the door to bite" | `conjure_creature` |
| `sympathetic_link` | `create_persistent_effect` (`kind: "sympathetic_link"`) | "whatever wounds me wounds him", "bind the goblin's pain to the ogre", "tie their heartbeats together" | `persistent_effect` |
| `persistent_effect` | `create_persistent_effect` | "hex the ogre so anyone who strikes it rots", "ward my ally so attackers are burned", "make my blade bleed whatever I strike" | `sympathetic_link` |
| `behavior_control` | `set_behavior` | "make them dance", "duel the brute", "flee from blood", "target the weakest", "mimic my movement" | `faction_charm`, `memory_edit` |
| `environment_flow` | `create_flow` | "conveyor floor", "shifting sand", "tilt the room", "wind pushes them", "gravity well", "black hole" | `barrier_shaping` |

The last three were promoted from planned (2026-06-13): `possess` already had an engine
handler; `edit_memory` (edits the NPC's `NPCProfile.memory`; forgetting the caster also
calms a hostile NPC) and `animate_object` (consumes a prop, spawns an actor in its place)
got new handlers in `effects.py`.

**`sympathetic_link` + `persistent_effect` (added 2026-06-16)** share one new effect,
`create_persistent_effect` — an *anchored trigger*. Rather than build a third persistence
system, it reuses the existing trigger store (`GameState.triggers`, `_fire_triggers`,
`_tick_triggers`, save/context): a persistent effect is an ordinary trigger dict plus
`kind`, `anchor` (the entity it is bound to), and — for links — `link_partner`. Two small
engine additions make it work: `_fill_trigger_effect_defaults` now echoes the firing event's
magnitude (`amount: "trigger_amount"` × optional `amount_ratio`, `damage_type:
"trigger_damage_type"`), which is what lets a link land the *same-sized* wound; and
`_tick_triggers` prunes an anchored effect when its anchor (or either end of a link) dies.
`sympathetic_link` echoes a creature's actual damage onto a linked one (one-way or
`mutual`, scaled by `ratio`); `persistent_effect` binds a ward/rider to a creature with two
sides — **defender** (`on_hit`: when the anchor is struck, afflict `trigger_source`, the
attacker) and **attacker** (`on_strike`: when the anchor lands a blow, afflict
`trigger_target`, the victim).

**Hook taxonomy: who vs. what (2026-06-16).** Damage hooks fire by the side they watch, and
"who" is a `target`/`match` filter, never baked into the hook name:
- *Defender side.* `normalize_trigger_name` had mapped `on_hit`/`when_hit`/`on_take_damage`
  to the player-only `on_player_hit`, so a ward on an ally — or one enemy striking another —
  silently never fired (only player and enemy factions get a faction-specific damage hook;
  allies/NPCs get only `on_damaged`). Those generic phrasings now map to the *universal*
  `on_damaged`, scoped by the trigger's `target`, so an on-hit ward works on any anchor. Only
  *explicitly-player* phrasings (`on_player_hit`, `when_i_am_hit`) stay player-scoped, and a
  free-floating reaction ward with no named subject defaults its target to the caster (so
  "lash back when I'm hit" never fires for an ally's blow).
- *Attacker side (source-matching).* `_fire_damage_triggers` now also fires `on_deal_damage`
  (+ faction variants) keyed on the **source** of the blow, but only when there is an
  attacker (a trap or hazard tile never trips a "when I strike" effect). A trigger's new
  `match` field selects which event role its criterion is checked against — `"target"`
  (defender, default) or `"source"` (attacker) — resolved generically as `event.get(role)`
  in `_trigger_matches` (the renamed `_trigger_matches_target`). `on_strike`/`on_attack`/… →
  `on_deal_damage`; the handler infers `match: "source"` from the hook. This is the
  **substrate the deferred item enchantment reuses**: it is anchored on the wielding
  *character* for now, but when melee carries the weapon used, `_fire_damage_triggers` adds
  an item-keyed name and a trigger matches `match: "weapon"` against `event["weapon"]` — no
  change to the matcher.

Re-entrancy is already safe:
`_fire_triggers` empties the store while firing, so an echo's own damage cannot re-fire the
link — mutual links resolve in a single hop with no double-count. **Item-bound enchantment
is deliberately NOT built** (see §3): inventory is `dict[name→count]` and equipment slots
hold name strings, so there is no item instance to anchor to.

**`disfigure` (added 2026-06-14)** is a targeted-maiming card: it leaves a creature alive
but broken in a specific way, mapping body-part flavor onto the matching status (legs→`rooted`,
arm→`weakened`, brain→`damage`+`stunned`, flesh→`poisoned`, mouth→`silenced`, eyes→`confused`,
skin→`add_weakness`). It introduces **one** new mechanic: the `weakened` status — the mirror of
`empowered`, subtracting 2 from the afflicted's *outgoing* attack damage (clamped to ≥1) in
`combat.py:attack()`. `weakened` is a normal `MECHANICAL_STATUS` with flavor aliases
(`feeble`/`palsied`/`withered`/`crippled`/`sapped`/…) and flows through the core `add_status`
path, so the card unlocks **no new effect type**. It shares trigger overlap with
`transform_entity` (both fire on "turn his X"); the prompt block disambiguates — disfigure is
*partial* (a status on a living creature), transform is *whole-body polymorph*.

**`delayed_effects` expanded** to own two timer-adjacent effects in addition to
`schedule_event`: `delay_incoming` captures raw incoming damage packets on a target and
releases them later through the normal damage path, and `accelerate_status` immediately
resolves the remaining damaging ticks of `poisoned`, `burning`, or `bleeding`. `schedule_event`
now also accepts generalized delayed payloads: `effects[]`, `costs[]`, and/or a message, while
preserving the older `event_type` shorthands.

**`triggers_reactions` expanded** with optional `when` predicates on `create_trigger`:
`hp_below`, `hp_above`, `hp_parity`, `inventory_empty`, `on_terrain`, `step_multiple`,
`count_visible`, and `same_spell_streak`. It also owns the new event hooks
`on_lethal_damage` (a pre-death intercession; if the target can survive the same blow after
trigger effects, death is prevented), `on_curse_gained`, and `on_enters_sight`.

**`behavior_control` added** as a typed, temporary AI-modifier card. It owns
`set_behavior`, which stores short-lived behavior modifiers in an entity's details and is
read by `ai.py`: `dance` moves without attacking, `coward` flees visible blood or bleeding,
`duel` locks onto a focus target, `lowest_hp` hunts the weakest visible creature, `mimic`
copies a focus target's last movement vector, and `freeze_dread` skips the action. This is
distinct from `faction_charm` and `memory_edit`: it changes tactical choice for a duration,
not allegiance or remembered facts.

**`environment_flow` added** as the standing-field movement card. It owns `create_flow`,
which stores per-tile drift vectors in `GameState.tile_flows`. `_tick_environment` moves each
living entity on a flow tile one step per turn in deterministic entity-id order, then
`_tick_tile_durations` expires the flow durations. Fixed vectors cover conveyors, wind,
tilted floors, and shifting sand; radial `inward`/`outward` modes cover gravity wells,
black holes, magnets, whirlpools, and repulsion fields.

Two cards (`barrier_shaping`, `divination`) unlock **no new effect type** — they're
*prompt-only refinements* of a core effect. They still earn cardhood: each carries the
fidelity rules and examples that the monolith spent many lines on (the wall/line direction
fix; the "use `revealed` for reveal/locate/track" mapping), and pulling them out of every
cast is most of the token win. They show that a card is a unit of *guidance*, not only of
schema.

## 3. Planned cards (need new engine work)

Drawn from the compendium's creative reach — mostly the **Wild Stream** and **Strange &
Esoteric** sections, which is where the current resolver is weakest because there's no
mechanic to land the intent on. Each needs a new effect handler (and usually a new
`SUPPORTED_EFFECTS` key) before it leaves `PLANNED_CARDS`.

| Card                  | New effect(s) | Compendium evidence | `required_context` | Notes / open questions |
|-----------------------|---|---|---|---|
| `size_modification`   | `resize_entity` | "summon a chalk giant no taller than my knee", "shrink it to a mote", "make the goblin huge and lumbering" | — | Change SCALE while staying the same thing (hp/attack/reach scale with size). Distinct from `transform_entity` (which changes *what* it is) — `shrink to a mouse` overlaps both, so they're `common_combos`. Sketched in `PLANNED_CARDS`. |
| `perception_illusion` | `add_illusion` / reuse `summon` pacifist + `add_status` | "make the goblin hallucinate allies", "make every hostile eye see a different me", "believe it has already won" | `visible_targets` | Some of this is expressible today (phantom decoys via summon + faction; `confused`/`frightened`). Card may start as a *guidance-only* refinement, like `divination`, then grow a real `add_illusion` effect. |
| `time_warp`           | `extra_turn`, `time_stall` | "make my next step happen twice", "freeze time for everyone except me", "borrow tomorrow's gravity" | — | Action-economy is delicate; engine decision (June 2026) is no time passes during a cast, so "act twice" needs a real turn-grant primitive. High design risk; price heavily. |
| `weather`             | `set_weather` (+ ambient tile/aura) | "call down a thunderstorm", "a rain of warm nails", "curtain of black rain that only wets enemies" | `region` | Often decomposes into existing primitives (area_damage + create_tiles); a card may be mostly *composition guidance* + a light ambient effect rather than a brand-new system. |
| `reputation_social`   | `adjust_standing` | "make the enemy's anger too loud to aim", "make its fear contagious", social fallout of `memory_edit` | `nearby_npcs` | Couples to dialogue/lore standing. Most valuable as `memory_edit`'s `common_combo`. |
| `gravity_control`     | `set_gravity` | "levitate the brute off the floor", "pin him under his own weight", "reverse gravity in the hall", "make it feather-light" | — | **Sketched in `PLANNED_CARDS` (2026-06-14).** Genuinely new: a *standing* field, unlike one-shot `push`/`pull`. Reuses the aura tick. Modes: levitate / pin-crush / lighten / reverse. Strong control — price sustained and region fields heavily. |
| `portal_gates`        | `create_portal` | "rip open a portal back to the entrance", "an escape hole through the wall", "a linked doorway to the room I saw" | `known_locations` | **Sketched in `PLANNED_CARDS` (2026-06-14).** A *persistent, repeatable* linked doorway, unlike one-shot `teleport`. Implementation: a tile-pair that teleports on entry. Stronger than teleport — never open onto an unreached quest gate; cost long/far gates. |
| `plant_growth`        | *(none — composes core `create_tiles` + `area_status` + `damage`/`conjure_item`)* | "ensnaring vines erupt around the goblin", "a wall of thorns", "roots crack the floor", "sprout healing fruit" | — | **Sketched in `PLANNED_CARDS` (2026-06-14).** A `barrier_shaping`-style *composition-guidance* card needing no new effect — entangle via `rooted`/`webbed`, block via thicket tiles, cut via `bleeding`. Cheapest of the three; could promote to live as soon as a thicket tile and the prompt block are settled. |
| `item_enchantment`    | `create_persistent_effect` (`kind: "item_enchantment"`) | "make my dagger bleed enemies it strikes", "curse the coin so it burns liars", "let this key remember every lock", "bless this shield to flash when I am hit" | `inventory`, held/equipped item state if available, nearby props/items | **Deferred (2026-06-16) pending an item-instance model.** Inventory is `dict[name→count]` and equipment slots hold name strings, so there is no per-item instance to anchor `on_hit`/`on_use`/`on_pickup`/`on_wear` behavior to, and melee does not route through a weapon object. Most of the *intent* ("dagger bleeds on strike", "shield flashes when hit") is really a caster-anchored on-hit/on-defend rider expressible via `persistent_effect`/`create_trigger` today; true item-bound persistence (survives drop/trade; "burns any dishonest *holder*") waits on a real item-instance refactor (inventory, equipment, ground items, trade, save, UI). See §3 note. |

(Promoted to live 2026-06-13 and removed from this table: `possession`, `memory_edit`,
`structure_animation` — see §2. Disfigure added live 2026-06-14 — see §2.)

**On the 50-card brainstorm (2026-06-14).** A large external list of candidate cards was
triaged against what we already have. Most collapsed into one of three buckets and were
*not* added: (a) **already expressible** via tiles + statuses + triggers — temperature,
fire/smoke, water, earth, disease, sleep, light/shadow, sound/silence, rune-inscription
(= `create_trigger` on tiles); (b) **semantic effects** that belong in the traits channel,
not a bespoke handler — emotion, body-mutation (also overlaps `disfigure`), material
transmutation (overlaps `transform_entity`), identity-masking, language, reputation — see
`SEMANTIC_EFFECTS.md`; (c) **already planned/live** — illusion (`perception_illusion`),
time (`time_warp`), weather, size, reputation. The only genuinely new, legible,
broad-language mechanics worth queuing were `gravity_control` and `portal_gates`, plus
`plant_growth` as a cheap composition card. Deferred as low-ROI or anti-legibility:
space-folding, causality-swap, rewind, parallel-echoes, probability/luck.

The compendium's sheer creative breadth is the argument for the whole system: a 9B can't
hold guidance for memory + illusion + time + weather + social + animation + possession in
one prompt, but it can resolve any one of them well when shown only that one.

## 4. Routing notes specific to this taxonomy

- **Trigger collisions are expected and fine** (recall bias). "raise a wall" hits both
  `conjure_creature` ("raise") and `barrier_shaping` ("wall"); loading both is correct —
  the model picks. We prune a trigger only when it causes a *wrong* selection often enough
  to matter (we already dropped over-generic `into a`/`out of` from `conjure_item`).
- **Substring matching is the v1.** Triggers match as lowercased substrings with space
  padding. If false fires accumulate (e.g. a trigger inside a longer word), switch to
  token/stem matching — a localized change in `_keyword_hits`, covered by the negative
  routing tests.
- **`common_combos` is one-hop, engine-loaded, and flagged empirical** (`enable_combos`).
  The current links: `conjure_creature→conjure_item`, `faction_charm→transform_entity`,
  `triggers_reactions↔delayed_effects`, `structure_animation→conjure_creature`, and
  `memory_edit→faction_charm`. Whether each pays off is decided from the usage logs, per
  `CAPABILITY_ROUTING.md` §5.3.

## 5. Status and next steps

**Done (2026-06-13):**
- Card scaffolding — `CapabilityCard`, the registry, `select_cards` router, tests.
- `CORE_PROMPT` carved + `assemble_resolver_system_prompt`; **routing is the sole resolver
  path** — the monolith and its flag/dual-path lane are removed.
- Promoted three cards end to end: `possession` (`possess`), `memory_edit` (new
  `edit_memory` handler), `structure_animation` (new `animate_object` handler). Added
  `size_modification` to the planned backlog.
- Tests: `tests/test_capability_routing.py` + `tests/test_new_effects.py`; full suite green.

**Done (2026-06-16):**
- Promoted `sympathetic_link` + `persistent_effect` end to end on the new
  `create_persistent_effect` effect (an anchored trigger; see §2). Engine: magnitude echo in
  `_fill_trigger_effect_defaults`, anchor-death pruning in `_tick_triggers`. Tests in
  `tests/test_persistent_effects.py`; full suite green.
- `item_enchantment` consciously deferred pending an item-instance model (see §2/§3).
- Expanded `delayed_effects` end to end: generalized scheduled `effects[]`/`costs[]`
  payloads, `delay_incoming`, `accelerate_status`, and broad `stasis` timer pausing. Tests in
  `tests/test_delayed_effects.py`; full suite green.
- Expanded `triggers_reactions` with trigger `when` predicates, `on_lethal_damage`,
  `on_curse_gained`, `on_enters_sight`, player step counting, and spell-streak state. Tests in
  `tests/test_condition_triggers.py`; full suite green.

**Remaining, in order** (see `CAPABILITY_ROUTING.md` §11):

1. **Add dynamic schema enums + `needs_capability`** (Phase 8 §4): feed `selected_effect_types`
   into a per-cast `SPELL_RESPONSE_JSON_SCHEMA` so an unrouted effect is un-emittable;
   shadow-compare on the offline audit first.
2. **Build the next planned cards** as their handlers land: `perception_illusion`,
   `time_warp`, `weather`, `reputation_social`, `size_modification`.
3. **Tier-2 embedding routing** only if the selected-cards logs show keyword misses on real
   paraphrases.
