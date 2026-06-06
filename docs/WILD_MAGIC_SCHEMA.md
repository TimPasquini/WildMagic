# Wild Magic Schema

Wild magic is resolved as a structured JSON object. The LLM proposes effects and costs; the engine validates and applies them. Rejected spells consume a turn. Technical failures, such as invalid JSON or unsupported operation types, do not consume a turn.

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
- `heal`: restore HP.
- `restore_mana`: restore mana.
- `teleport`: move an entity to a specific tile.
- `push` / `pull`: move an entity away from or toward an origin.
- `create_tile` / `set_tile`: change one tile.
- `create_tiles`: change an area or explicit list of tiles.
- `add_status` / `remove_status`: apply or clear statuses.
- `summon`: create an actor.
- `spawn_item`: create an item.
- `conjure_item`: create an item from a safe template while allowing a creative name/material/tags.
- `conjure_creature`: create one or more creatures from a safe template while allowing a creative name/faction/tags.
- `modify_inventory`: add, remove, or set carried item counts.
- `transform_entity`: alter actor stats, name, glyph, material, or tags.
- `change_faction`: make an entity enemy, ally, neutral, etc.
- `add_tag` / `remove_tag`: alter entity tags.
- `add_resistance` / `add_weakness`: alter damage modifiers.
- `set_flag`: set a persistent world flag.
- `schedule_event`: create a delayed event.
- `add_curse`: add a curse as an effect.
- `message`: add log text.

## Costs

Supported cost types:

- `mana`
- `health`
- `max_health`
- `max_mana`
- `item`
- `status`
- `curse`

Costs are applied after effects, so the player discovers the price after the spell happens.

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
- `ice_wall`
- `poison_cloud`
- `vines`
- `rubble`
- `mist`

Temporary terrain can include a `duration` field. Area effects are currently limited to radius `0-4` and at most 30 changed tiles per effect.

## Statuses

Statuses currently supported by engine rules include:

- `burning`
- `poisoned`
- `frozen`
- `stunned`
- `rooted`
- `slowed`
- `hasted`
- `confused`
- `frightened`
- `marked`
- `warded`

Some statuses already affect behavior. For example, burning and poisoned deal damage over time, stunned and frozen prevent enemy actions, and rooted prevents enemy movement.

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
