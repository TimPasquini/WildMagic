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

Thirteen cards in `CAPABILITY_CARDS`, content lifted from the old monolith (plus the three
promoted 2026-06-13 and `disfigure` added 2026-06-14).

| Card | Unlocks (effect types) | Carved from (compendium / monolith) | Common combos |
|---|---|---|---|
| `conjure_creature` | `summon`, `conjure_creature` | Summoning — Allies / Aura Bearers & Wards / Hazardous Creatures; monolith summon line + the whole behavior-tags block | `conjure_item` |
| `conjure_item` | `conjure_item`, `spawn_item`, `transform_item`, `modify_inventory` | "glass teeth", "webbing", "transmute the chalk"; monolith conjure_item/spawn_item lines + item templates | — |
| `transform_entity` | `transform_entity` | "turn the wolf into a chicken", "petrify", Strange & Esoteric polymorphs | `disfigure` |
| `disfigure` | *(none — uses core `add_status`/`damage`/`add_weakness`; introduces the `weakened` status)* | "turn his legs to iron", "boil his brain", "wither his sword-arm", "rot his flesh" — targeted body-part maiming | — |
| `faction_charm` | `change_faction`, `add_tag`, `remove_tag` | "charm/befriend", "make the weapon defect", "ally for one turn" | `transform_entity` |
| `barrier_shaping` | *(none — refines core `create_tiles`)* | Terrain "wall of ice across the corridor", "line of ice east", "between me and them"; the directional-shape balance rules | — |
| `divination` | *(none — refines core `add_status` 'revealed')* | Information & Divination section; the reveal/track balance rule | — |
| `triggers_reactions` | `create_trigger` | Combo & Conditional "next time X…"; the create_trigger rule | `delayed_effects` |
| `delayed_effects` | `schedule_event` | "in five turns…", "comes back to collect", debt-later spells | `triggers_reactions` |
| `prophecy` | `create_promise` | "I prophesy…", "somewhere north a chapel waits"; the create_promise block | — |
| `possession` | `possess` | "take over the guard", "see through the eyes of", "ride the beast" | — |
| `memory_edit` | `edit_memory` (NEW handler) | "make the nearest enemy forget me", "plant a false memory", "convince the guard…" | `faction_charm` |
| `structure_animation` | `animate_object` (NEW handler) | "make the brass door angry", "the statue steps down", "persuade the door to bite" | `conjure_creature` |

The last three were promoted from planned (2026-06-13): `possess` already had an engine
handler; `edit_memory` (edits the NPC's `NPCProfile.memory`; forgetting the caster also
calms a hostile NPC) and `animate_object` (consumes a prop, spawns an actor in its place)
got new handlers in `effects.py`.

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

| Card | New effect(s) | Compendium evidence | `required_context` | Notes / open questions |
|---|---|---|---|---|
| `size_modification` | `resize_entity` | "summon a chalk giant no taller than my knee", "shrink it to a mote", "make the goblin huge and lumbering" | — | Change SCALE while staying the same thing (hp/attack/reach scale with size). Distinct from `transform_entity` (which changes *what* it is) — `shrink to a mouse` overlaps both, so they're `common_combos`. Sketched in `PLANNED_CARDS`. |
| `perception_illusion` | `add_illusion` / reuse `summon` pacifist + `add_status` | "make the goblin hallucinate allies", "make every hostile eye see a different me", "believe it has already won" | `visible_targets` | Some of this is expressible today (phantom decoys via summon + faction; `confused`/`frightened`). Card may start as a *guidance-only* refinement, like `divination`, then grow a real `add_illusion` effect. |
| `time_warp` | `extra_turn`, `time_stall` | "make my next step happen twice", "freeze time for everyone except me", "borrow tomorrow's gravity" | — | Action-economy is delicate; engine decision (June 2026) is no time passes during a cast, so "act twice" needs a real turn-grant primitive. High design risk; price heavily. |
| `weather` | `set_weather` (+ ambient tile/aura) | "call down a thunderstorm", "a rain of warm nails", "curtain of black rain that only wets enemies" | `region` | Often decomposes into existing primitives (area_damage + create_tiles); a card may be mostly *composition guidance* + a light ambient effect rather than a brand-new system. |
| `reputation_social` | `adjust_standing` | "make the enemy's anger too loud to aim", "make its fear contagious", social fallout of `memory_edit` | `nearby_npcs` | Couples to dialogue/lore standing. Most valuable as `memory_edit`'s `common_combo`. |
| `gravity_control` | `set_gravity` | "levitate the brute off the floor", "pin him under his own weight", "reverse gravity in the hall", "make it feather-light" | — | **Sketched in `PLANNED_CARDS` (2026-06-14).** Genuinely new: a *standing* field, unlike one-shot `push`/`pull`. Reuses the aura tick. Modes: levitate / pin-crush / lighten / reverse. Strong control — price sustained and region fields heavily. |
| `portal_gates` | `create_portal` | "rip open a portal back to the entrance", "an escape hole through the wall", "a linked doorway to the room I saw" | `known_locations` | **Sketched in `PLANNED_CARDS` (2026-06-14).** A *persistent, repeatable* linked doorway, unlike one-shot `teleport`. Implementation: a tile-pair that teleports on entry. Stronger than teleport — never open onto an unreached quest gate; cost long/far gates. |
| `plant_growth` | *(none — composes core `create_tiles` + `area_status` + `damage`/`conjure_item`)* | "ensnaring vines erupt around the goblin", "a wall of thorns", "roots crack the floor", "sprout healing fruit" | — | **Sketched in `PLANNED_CARDS` (2026-06-14).** A `barrier_shaping`-style *composition-guidance* card needing no new effect — entangle via `rooted`/`webbed`, block via thicket tiles, cut via `bleeding`. Cheapest of the three; could promote to live as soon as a thicket tile and the prompt block are settled. |

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

**Remaining, in order** (see `CAPABILITY_ROUTING.md` §11):

1. **Add dynamic schema enums + `needs_capability`** (Phase 8 §4): feed `selected_effect_types`
   into a per-cast `SPELL_RESPONSE_JSON_SCHEMA` so an unrouted effect is un-emittable;
   shadow-compare on the offline audit first.
2. **Build the next planned cards** as their handlers land: `perception_illusion`,
   `time_warp`, `weather`, `reputation_social`, `size_modification`.
3. **Tier-2 embedding routing** only if the selected-cards logs show keyword misses on real
   paraphrases.
