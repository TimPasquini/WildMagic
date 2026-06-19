# Wild Magic Schema

Wild magic is resolved as a structured JSON object. The LLM proposes effects and costs; the engine validates and applies them. Rejected spells consume a turn. Technical failures, such as invalid JSON or unsupported operation types, do not consume a turn.

The resolver and engine share the same structural contract. Accepted resolutions are applied transactionally: if validation or application fails, the engine rolls back the attempted spell and does not advance the turn.

## Context Hints

The LLM receives visible entities, terrain, inventory, and a compact `spell_anchors` list. `spell_anchors` contains nearby visible props such as braziers, mirrors, pools, notices, webs, altars, cages, books, bells, crystals, market ledgers, coin scales, charm-knotted awnings, and spice braziers. Each anchor includes:

- `id`: use this actual prop id as a target, center, origin, or placement anchor.
- `name`, `position`, `distance`, `tags`, and `description`.
- `affordances`: plain-language hints mapping the prop's tags to reusable mechanics.
- `suggested_mechanics`: reminders that props should be expressed through ordinary effects.
- `nearest_visible_enemy` and optional `range_hint`: distance guidance so small area effects centered on a prop do not accidentally miss the intended creature.
- `recommended_effect_patterns`: copyable effect skeletons chosen from the prop's tags and local geometry; the model should fill in balanced amounts, durations, and costs.

Props do not add a separate spell system. They are environmental prompts for normal mechanics: center an `area_damage` on a brazier, create mist from a pool, reveal through a mirror, summon from a ritual circle, web/root from ropes or vines, curse through a notice or tablet, bind through a contract ledger, delay through a water clock, and so on. For attacks, prefer targeting creatures while using a prop as the center/origin; only target a prop directly when the spell explicitly destroys, animates, repairs, or transforms that object. Destroyed props remain as broken scenery with `broken`/`destroyed` tags so the consequence is inspectable.

`active_curses` contains the controlled body's current curses as full cards. Unknown curses
are semantic: the resolver should let their description bite by narrowing the spell's flavor,
costs, compromises, and backfires according to the scene. Known mixed/mechanical curses also
carry engine-enforced `mechanics`, such as `max_distance`, `min_distance`, `max_radius`,
`require_line_of_sight`, or `forbidden_effects`; the resolver should avoid emitting JSON that
violates those limits. If the requested spell cannot fit the curses, reject it or resolve a
smaller curse-shaped version. The engine checks mechanical curses before mutation.

Current recognized mechanical curses:

- `close_curse` / Close Curse: effects must stay within 3 squares.
- `far_curse` / Far Curse: effects must be at least 4 squares away.
- `narrow_curse` / Narrow Curse: area radius cannot exceed 1.
- `straight_path_curse` / Straight Path Curse: effects must stay in line of sight.
- `anchored_curse` / Anchored Curse: forbids teleport and possess.

The resolver may still invent vivid semantic curses as costs, and those should be tailored
to the spell and current context.

`supported_effects` in the context is routed per spell: universal core effects are always
present, and specialist effects appear when the capability router loads their mechanic cards.
`supported_costs` comes from the shared spell contract.

## Top-Level Shape

```json
{
  "accepted": true,
  "severity": "minor",
  "outcome_text": "The spell improvises a mean little miracle.",
  "effects": [],
  "costs": [],
  "rejected_reason": null
}
```

## Effects

Supported effect types:

- `damage`: damage one target.
- `area_damage`: damage entities in a radius.
- `area_status`: apply a status to entities in a radius.
- `heal`: restore HP.
- `restore_mana`: restore mana.
- `teleport`: move an entity to a specific tile.
- `push` / `pull`: move an entity away from or toward an origin.
- `create_tile` / `set_tile`: change one tile.
- `create_tiles`: change an area, a line/path, or an explicit list of tiles.
- `add_status` / `remove_status`: apply or clear statuses.
- `summon`: create an actor.
- `spawn_item`: create an item.
- `conjure_item`: create an item from a safe template while allowing a creative name/material/tags.
- `conjure_creature`: create one or more creatures from a safe template while allowing a creative name/faction/tags.
- `transform_item`: alter an item or prop into a new item form.
- `modify_inventory`: add, remove, or set carried item counts.
- `transform_entity`: alter actor stats, name, glyph, material, or tags. It may set `max_hp`
  as well as current `hp`, so aging/withered-body spells can reduce durability without a
  bespoke effect.
- `edit_memory`: add, alter, or erase a nearby NPC memory.
- `animate_object`: turn a nearby prop into an actor.
- `aura`: attach an ongoing damage or status emanation to an entity or tile.
- `add_trait`: attach a soft narrative trait to an entity.
- `change_faction`: make an entity enemy, ally, neutral, etc.
- `possess`: move player control into another living body.
- `add_tag` / `remove_tag`: alter entity tags.
- `add_resistance` / `add_weakness`: alter damage modifiers.
- `set_flag`: set a persistent world flag. `flag: "seal_stairs"` prevents stair ascent/descent
  while true.
- `schedule_event`: create a delayed event.
- `delay_incoming`: capture incoming damage on a target and release it after a timer.
- `accelerate_status`: resolve remaining damaging status ticks immediately.
- `set_behavior`: temporarily change creature AI behavior.
- `create_flow`: create a temporary tile current that moves creatures each turn.
- `create_trigger`: create a charged reaction that fires when a later event happens.
- `create_persistent_effect`: create an anchored trigger such as a sympathetic link or ward.
- `create_promise`: speak a prophecy, rumor, threat, or place claim into the promise ledger.
- `add_curse`: add a curse as an effect.
- `message`: add log text. Optional `spoof: true` marks deceptive/fake log text; it writes
  only the message and does not mutate backing state.

## Costs

Supported cost types:

- `mana`
- `health`
- `hp`
- `max_health`
- `max_mana`
- `item`
- `status`
- `curse`

Costs are applied after effects, so the player discovers the price after the spell happens.
Curse costs should include `id`, `name`, and `description` when possible. Unknown curse ids
become semantic curses; recognized ids gain their engine-owned mechanics.

## Terrain

Current engine-native tiles:

- `floor`
- `wall`
- `door`
- `open_door`
- `stairs_down`
- `stairs_up`
- `water`
- `fire`
- `slick_ice`
- `ice_wall`
- `poison_cloud`
- `vines`
- `rubble`
- `mist`
- `dirt_road`

Temporary terrain can include a `duration` field. Area effects can use `radius`, `hollow`, and `target`. Shape effects can use `shape`, `origin`, `target`, and optional `width`.

Useful shapes:

- `line`, `path`, `beam`, `bridge`: draw from `origin` toward `target`.
- `wall`, `barrier`: draw a perpendicular barrier centered on `target`.
- `cone`, `fan`: fill a cone from `origin` toward `target`.
- `scatter`, `spray`: choose scattered tiles around `target`.

## Flow Fields

Use `create_flow` for environmental currents that move creatures once per turn: conveyor
floors, wind, shifting sand, tilted rooms, gravity wells, vortexes, and magnetic pulls.

Useful fields:

- `target`, `center`, `x`/`y`: where the field is centered.
- `radius`, `shape`, or `tiles`: which tiles receive a current.
- `duration` / `turns`: how long the current lasts.
- `dx` / `dy` or `direction`: a fixed vector. Directions include `north`, `south`, `east`,
  and `west`.
- `mode`: `inward` pulls each tile toward the center; `outward` pushes away from it.

Each affected tile stores a current. During the environment tick, a living creature standing
on that tile is pushed one step if the destination is open. Blockers stop the movement.

```json
{
  "accepted": true,
  "severity": "moderate",
  "outcome_text": "The floor starts carrying the room eastward one unwilling footstep at a time.",
  "effects": [
    {"type": "create_flow", "target": "nearest_enemy", "radius": 3, "direction": "east", "duration": 4}
  ],
  "costs": [{"type": "mana", "amount": 4}],
  "rejected_reason": null
}
```

## Statuses

Statuses currently supported by engine rules include:

- `burning`
- `poisoned`
- `bleeding`
- `frozen`
- `stunned`
- `rooted`
- `webbed`
- `slowed`
- `hasted`
- `invisible`
- `confused`
- `frightened`
- `marked`
- `revealed`
- `sight_shrouded`
- `warded`
- `regenerating`
- `berserk`
- `empowered`
- `weakened`
- `silenced`
- `cursed`
- `stasis`
- `delayed_sink`
- `strained`
- `drained`
- `jinxed`
- `crawling_skin`

Some statuses already affect behavior. For example, burning and poisoned deal damage over time,
stunned and frozen prevent enemy actions, rooted prevents enemy movement, and `sight_shrouded`
narrows the player's FOV. Use optional `sight_radius`/`radius` on the `add_status` effect to
choose the temporary view radius.

## Behavior Modifiers

Use `set_behavior` for temporary changes to how creatures choose actions. This is not a
faction change or a memory rewrite; it is a short-lived AI modifier stored on the target.

Supported behaviors:

- `dance`: move if possible, but do not attack.
- `coward`: flee from visible blood, bleeding, or wounded creatures.
- `duel`: lock onto a specific focus target.
- `lowest_hp`: target the weakest visible living creature.
- `mimic`: copy a focus target's last movement vector.
- `freeze_dread`: skip the action while the dread holds.

Useful fields:

- `target`: a creature id, `nearest_enemy`, or a group such as `all_enemies`.
- `behavior`: one of the supported behavior names.
- `duration` / `turns`: how long it lasts.
- `behavior_target`, `focus`, `lock_to`, `duel_target`, or `mimic_target`: the focus for
  `duel` and `mimic`.

```json
{
  "accepted": true,
  "severity": "moderate",
  "outcome_text": "The brute hears a tune with sharp little teeth and starts stepping instead of swinging.",
  "effects": [
    {"type": "set_behavior", "target": "nearest_enemy", "behavior": "dance", "duration": 3}
  ],
  "costs": [{"type": "mana", "amount": 4}],
  "rejected_reason": null
}
```

## Examples

```json
{
  "accepted": true,
  "severity": "moderate",
  "outcome_text": "Water remembers it used to be everywhere.",
  "effects": [
    {"type": "create_tiles", "target": "player", "radius": 2, "tile": "water", "duration": 8},
    {"type": "push", "target": "nearest_enemy", "origin": "player", "distance": 2}
  ],
  "costs": [{"type": "mana", "amount": 3}],
  "rejected_reason": null
}
```

```json
{
  "accepted": true,
  "severity": "minor",
  "outcome_text": "A slick blue path skids toward the nearest foe.",
  "effects": [
    {"type": "create_tiles", "shape": "line", "origin": "player", "target": "nearest_enemy", "tile": "slick_ice", "duration": 5}
  ],
  "costs": [{"type": "mana", "amount": 2}],
  "rejected_reason": null
}
```

```json
{
  "accepted": true,
  "severity": "moderate",
  "outcome_text": "A brittle clatter answers from inside the mouth.",
  "effects": [
    {"type": "damage", "target": "nearest_enemy", "amount": 3, "damage_type": "physical"},
    {"type": "add_status", "target": "nearest_enemy", "status": "bleeding", "duration": 4},
    {
      "type": "conjure_item",
      "template": "body_part",
      "name": "glass teeth",
      "material": "glass",
      "tags": ["sharp", "fragile", "tooth"],
      "target": "nearest_enemy",
      "placement": "target_tile"
    }
  ],
  "costs": [{"type": "mana", "amount": 3}],
  "rejected_reason": null
}
```

```json
{
  "accepted": true,
  "severity": "major",
  "outcome_text": "The walls darken with thousands of disciplined legs.",
  "effects": [
    {
      "type": "conjure_creature",
      "template": "tiny_swarm",
      "name": "ant swarm",
      "count": 6,
      "faction": "enemy",
      "tags": ["ant", "wall_born"],
      "placement": "near_walls"
    }
  ],
  "costs": [
    {"type": "mana", "amount": 4},
    {"type": "status", "status": "crawling_skin", "duration": 6}
  ],
  "rejected_reason": null
}
```

## Triggers

Use `create_trigger` for "the next time X happens, Y happens" spells. Triggers are stored in the game state with charges and duration, then apply ordinary effects when fired.

Supported trigger names include:

- `on_next_spell`
- `on_player_hit`
- `on_player_damaged`
- `on_player_move`
- `on_enemy_hit`
- `on_enemy_damaged`
- `on_enemy_death`
- `on_lethal_damage`
- `on_curse_gained`
- `on_enters_sight`

Inside trigger effects, `target: "trigger_target"` means the entity that caused the trigger target condition, and `target: "trigger_source"` means the attacker/source when one exists.

Triggers may include an optional `when` predicate. Supported predicate keys include
`hp_below`, `hp_above`, `hp_parity`, `inventory_empty`, `on_terrain`, `step_multiple`,
`count_visible`, and `same_spell_streak`. Examples: `"when": {"hp_below": 0.5}` fires only
when the event target is below half HP; `"when": {"step_multiple": 3}` fires every third
player step.

```json
{
  "accepted": true,
  "severity": "moderate",
  "outcome_text": "Your blood is instructed to answer violence.",
  "effects": [
    {
      "type": "create_trigger",
      "name": "thorn-blood answer",
      "trigger": "on_player_hit",
      "target": "player",
      "charges": 1,
      "duration": 6,
      "effects": [
        {"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "physical"},
        {"type": "add_status", "target": "trigger_source", "status": "bleeding", "duration": 3}
      ]
    }
  ],
  "costs": [{"type": "mana", "amount": 4}],
  "rejected_reason": null
}
```

## Conjuration Templates

Item templates:

- `generic_object`
- `body_part`
- `glass_shard`
- `ritual_component`
- `weapon_like`
- `food`
- `key_like`
- `treasure`

Creature templates:

- `tiny_swarm`
- `small_beast`
- `humanoid`
- `construct`
- `spirit`
- `slime`
- `summoned_servant`
- `hazard_creature`

Supported placements:

- `target_tile`
- `near_target`
- `near_player`
- `visible_floor`
- `near_walls`

```json
{
  "accepted": true,
  "severity": "major",
  "outcome_text": "The spell leaves and promises to come back.",
  "effects": [
    {"type": "set_flag", "flag": "future_debt", "value": true},
    {"type": "schedule_event", "turns": 3, "event_type": "summon", "name": "debt collector", "char": "d", "hp": 6, "attack": 3}
  ],
  "costs": [{"type": "mana", "amount": 2}],
  "rejected_reason": null
}
```
