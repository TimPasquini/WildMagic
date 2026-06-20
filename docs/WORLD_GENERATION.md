# World Generation

Wild Magic rolls a political overworld at run start for world-bearing scenarios. The roll
is deterministic from the run seed and fixes only the coarse geopolitical board: which realm
owns each overworld zone, which old kingdom is the free rival, where the imperial capital is,
and where the current start scenarios sit. Zone interiors still generate lazily on first
entry.

The political layout uses a fixed relationship graph, then applies a seeded rotation or
reflection before placing it on the finite zone grid. That keeps the canon constraints stable
while preventing every run from putting the imperial bloc and rival in the same compass
directions.

The implementation lives in `wildmagic/worldgen.py`.

## Current Scope

- Fixed roster: Vigovia, Stalnaz, Brall, Ryolan, Vint, and Threen.
- Rolled role assignment: one old kingdom is the rival; the other three are conquered.
- Threen is always the proxy client; Vigovia is always the imperial heartland.
- The faction ledger is seeded from the roll for world-bearing scenarios.
- The map is finite. The only hard overworld edges are the world-map bounds.
- `(0,0)` is the survey-map center, not a guaranteed start location.
- Current start scenarios are placed at real overworld coordinates:
  - `town` in Ryolan
  - `bazaar` in Vint
  - `warren` in Brall
  - `archive` in Stalnaz
  - `frontier` in a central unowned wild zone

The four authored starts are still local handcrafted interiors. Each surface start must have
walkable local exits to all four zone edges so the player can leave on foot and cross the
overworld toward another start. Pre-generating every start interior in every run is deferred;
if you choose one start, that location becomes a real visited zone and ordinary foot travel
can continue from there.

## Boundaries

The world map owns political placement only. It does not pre-generate towns, buildings,
actors, quest subjects, or promise sites. `WorldPromise` reservations still bind and realize
inside unexplored cells, now with a known owning realm when the cell belongs to one.

Faction identity on spawned locals is deferred to the faction/quest identity work. For now,
the map seeds faction-ledger entries and exposes current-realm context; later slices should
tag generated characters and sites with their owning realm so kills, quests, and dialogue
can react relationally.

## Player-Facing Surface

`world` / `atlas` is a free command through the shared action layer. It shows the political
survey map, the imperial capital, the rival, the player's current zone, visited territory,
and unowned wilds. The GUI binds `m` to a modal atlas view backed by the same shared
description function.

The shared state view includes:

- `world_map`: serialized political map
- `current_realm`: the realm, role, tradition, ruler, and faction for the current zone

These are replay-safe and are also available to wild-magic context.

The same `current_realm` card is also threaded into dialogue, trade, lore extraction, and
background town-generation contexts so generated social content can react to the owning
realm without duplicating map logic.
