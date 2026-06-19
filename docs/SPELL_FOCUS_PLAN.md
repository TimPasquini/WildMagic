# Spell Focus Implementation Plan

> **Status: implemented.** The spell focus is a per-entity mark on an already-equipped item
> (`Entity.focus_slots`), surfaced to the resolver as a heavily-weighted flavor block. Item
> descriptions now survive pickup via `GameState.item_lore` (populated on pickup and through the
> Investigate canon side-effect, so replay reconstructs it). The autoplay run-theme rename and a
> small curated `FOCUS_SPECS` set shipped with it. The text below is the design of record; power
> scaling magnitudes remains the deliberate next step.

The caster marks **one of their already-equipped items** (in any slot) as their **spell
focus**. That item is fed to the wild-magic resolver as heavily-weighted flavor context, so
the implement channeling a spell visibly shapes its imagery, element, and tone. Richer items
steer harder, plain ones barely at all. Mechanical numeric effects (power scaling the severity
bands) are a deliberate Phase 2 — the focus ships as pure semantic context first.

**Design note (the focus is a mark, not a slot).** An earlier draft added a dedicated
`spell_focus` equipment slot. We don't: instead the focus is a per-entity *marker* on an
existing equipped item. This is simpler and dissolves two whole problem classes — a focus is
just a normally-equipped item, so it grants exactly its own legitimate slot stats (no phantom
bonuses) and needs no wearable-check bypass to equip. The marker is a **list** from day one so
the design scales to multiple simultaneous foci later, even though v1 caps it at one.

The resolver mechanism is not new: caster stats already *shift the resolver's anchors* through
[`caster_prompt_block`](../wildmagic/prompts.py) rather than a separate mechanical pass
(Attunement nudges magnitude bands, Composure scales volatility, the `signature` tints prose).
Spell focus rides the same rails with a parallel `focus_prompt_block`.

## Current Baseline

- **Equipment** is `Entity.equipment: dict[str, str | None]` — slot → item *name*, defaulting
  to `weapon/armor/charm/head/chest/legs/feet/hands` ([models.py](../wildmagic/models.py)).
  Read only via `.get(slot)`; saves are replay-based, so adding a key needs no migration.
- **Inventory** is `dict[str, int]` (name → count), a property over the controlled entity
  ([engine.py](../wildmagic/engine.py)). **Items are fungible by name — there is no
  per-instance storage.** This is the central constraint.
- **Resolver context** is assembled in [`state_view.py`](../wildmagic/state_view.py)
  (`caster_profile`, `region_style`, `player.equipment`, etc.), split in
  [`_wild_prompt_messages`](../wildmagic/wild_magic.py) (region/caster stripped from the
  user-message JSON and rendered into the **system** prompt), and appended by
  [`assemble_resolver_system_prompt`](../wildmagic/capabilities.py) as
  `... + region_block + caster_block`.
- **Item descriptions** have two sources, *both lost on pickup*:
  1. `Entity.description`, set at generation for *some* items (books, transformed/conjured
     items, secret/promise rewards); generic gear from `spawn_item` has none.
  2. **Investigate** output — a `CanonRecord` keyed `canon_detail_<entity_id>_close/_far`
     ([actions.py](../wildmagic/actions.py)), surfaced to the resolver via the `nearby_canon`
     channel matched by **live entity id**.
  [`pick_up_items_at_player`](../wildmagic/items.py) does `inventory[type] += qty;
  del entities[id]` — discarding `Entity.description` and orphaning the investigate record
  (its anchor id no longer exists; nothing maps the inventory *name* back to it). Even on the
  floor, `item_card` does not expose `description`, so item text reaches the LLM today only
  through the entity-id-keyed canon channel.

## Goals

- Let the player mark any one already-equipped item as their spell focus (a per-entity marker,
  stored as a list so multiple foci are a later config change, not a rewrite).
- Feed the marked focus to the resolver as heavily-weighted flavor, mirroring
  `caster_prompt_block`.
- **Make item descriptions survive pickup** — including Investigate-authored descriptions — so
  a focus carries the flavor the player discovered.
- Keep power/attribute numeric scaling as a clean Phase 2 hook that the v1 data already carries.
- Rename the unrelated autoplay run-theme `spell_focus` to `autoplay_run_theme`.

## Non-Goals

- No dedicated `spell_focus` equipment slot, and **no new equipment slot at all**.
- No change to the fungible-by-name inventory model (no per-instance item ids).
- No mechanical numeric effect from the focus in v1 (Phase 2).
- No new resolver call — the focus rides the existing single resolution pass.

## Naming Collision: rename first

The existing `spell_focus` in [autoplay.py](../wildmagic/autoplay.py) /
[ui.py](../wildmagic/ui.py) is a per-run **theme** steering which spells the autoplay agent
*invents* ("Focus on terrain-transformation spells this run") — unrelated to equipment. It
lives in the autoplay decision packet, a different dict from the resolver packet, so there is
no runtime conflict, only conceptual confusion.

**Rename `spell_focus` → `autoplay_run_theme` and `spell_focus_for_seed` →
`autoplay_run_theme_for_seed`.** Pure mechanical rename. Touch points:

- `autoplay.py`: the dataclass field, the prompt-dict key, the function definition, the reader,
  the assignment, the pass-through call, and the ~3 prompt-text mentions.
- `ui.py`: the import, the two assignments, the pass-through, and the status-line string.
- `tests/test_autoplay.py`: references to the field/function.

## Part A — Item description retention (`item_lore`)

The enabling change. A lore store keyed the same way the inventory is: two items of the same
inventory key are already identical, so per-key lore matches the model and survives pickup for
free.

1. **New field** `GameState.item_lore: dict[str, dict]` **keyed by the inventory key** (see
   keying note below), value
   `{"display_name": str, "description": str, "themes": list[str], "power": int, "source": str}`
   (all but `description`/`display_name` optional in v1). Additive; deterministic — rebuilt by
   the same commands on replay, so no save migration.

2. **Populate on Investigate, through the canon side-effect path.** Investigate already
   materializes a detail `CanonRecord`, and replay re-injects recorded canon via
   `apply_recorded_canon` → `_apply_canon_record_side_effects`
   ([actions.py](../wildmagic/actions.py)) — the *same* helper live investigate runs. So lore
   materialization must live **inside `_apply_canon_record_side_effects`** (extend it, today
   book-only, to recognize item-detail records), not in `_investigate_entity` directly.
   Otherwise replay would rebuild the canon record but not the lore. The detail record must
   carry the item's **inventory key** (stash `item_type` in the record's `engine_choices`/
   attachment when building the canon context) so the side-effect can key correctly even when
   the source entity is gone.

3. **Populate on pickup** — in `pick_up_items_at_player`, before deleting the entity, if it has
   a `description` (or an attached `canon_detail_<id>` record), write it with
   `source="description"`, keyed by the same `item_type or name` used as the inventory key.
   Deterministic (descriptions are set at generation/replay).

4. **Optional: seed on generation** — items spawned with a `description` can seed `item_lore`
   immediately, so the text is available even before pickup.

5. **Bonus (optional)** — `item_card` can surface `item_lore`/`description` for floor items, so
   the resolver sees loot flavor without an investigate first. Tangential; can defer.

### Keying note: use the inventory key, not the display name

Pickup stores `inventory[entity.item_type or entity.name]` ([items.py](../wildmagic/items.py)),
equipment stores that same inventory key, and conjured/template items deliberately set
`item_type != name` ([effects.py](../wildmagic/effects.py)). So `item_lore` must be keyed by
the **inventory key** (`item_type or name`, normalized) — keying by display name would miss
exactly the conjured/quest/template items most likely to carry interesting lore. Keep the
human-readable `display_name` *inside* the record for prompt rendering.

### Write rule: precedence merge, not blind overwrite-protection

A single writer (`set_item_lore(key, display_name, description, source)`) owns all three paths
above and applies a **source precedence**, *not* a naive "first write wins":

- Precedence: `investigated` > `description` (generation-seeded or pickup-copied).
- Never downgrade tier; within a tier prefer the longer non-empty description; `investigated`
  always wins.

This is deliberately stronger than "skip if an entry already exists" — that naive guard would
block the common, *wanted* upgrade where you pick up an item (thin `description`) and **later
Investigate it** for richer text. The precedence rule is also order-independent (Investigate
wins regardless of pickup order), which keeps replay deterministic.

### Key collisions are inherent to the fungible model

Keying by inventory key means investigating one "rusted locket" describes all of them — but two
differently-described items *cannot coexist in inventory anyway* (they stack into one count
under the same key), so this is a property of the existing model, not a regression.
Implications:

- For intentionally-generic stackable gear (`EQUIPMENT_SPECS` — "iron sword"), shared lore by
  name is correct and desirable.
- For items meant to carry **unique** lore, the generators must spawn them with **distinct
  names** (a material/adjective qualifier: "glowing locket" vs "rusted locket"). This is
  already how the town/prop/canon/flesh generators name things, and it is the *only* way the
  fungible inventory can keep two instances apart — so it is the right place to enforce
  uniqueness, rather than bolting per-instance identity onto inventory.

## Part B — The focus mark (no new slot)

1. **Model** — add `focus_slots: list[str] = field(default_factory=list)` to `Entity`
   ([models.py](../wildmagic/models.py)): the equipment slot keys currently marked as foci.
   Empty = no focus. **Per-entity, so it follows body-swap for free** — exactly like
   `equipment`/`inventory`, which are already per-entity. v1 enforces at most one entry
   (marking a new one replaces the old); the list type is what makes multi-focus a later policy
   change rather than a schema change. Surface it in `Entity.to_public_dict()` alongside
   `equipment`.

   *Why mark by slot, not by item key:* one item occupies a slot, so the slot key is an
   unambiguous handle, it sidesteps inventory fungibility (no "which instance?"), and it reads
   naturally against the existing per-slot `equipment` dict. Consequence to document: the mark
   tracks the **slot** — if you swap out the item in a focused slot, the new item becomes the
   focus, and unequipping that slot leaves the mark dormant (no item → not surfaced). (If we
   later prefer "the mark dies when that specific item leaves," switch the store to inventory
   keys; called out in Open Questions.)

2. **GUI inventory panel — the primary interaction** ([ui.py](../wildmagic/ui.py)). The marking
   happens on the **left (Equipped Gear) pane**, not the carried-items pane. Add a key (e.g. `F`)
   that toggles the focus mark on the selected *occupied* equipped slot, and render a marker
   (e.g. a `★` or `[focus]` tag) next to focused slots. Update the hint line. No change to the
   carried-items equip flow, the `is_wearable` coloring, or the `slots` lists (no new slot
   exists). This is how players will normally set their focus.

3. **`focus` / `unfocus` commands** ([actions.py](../wildmagic/actions.py)) — a secondary
   convenience verb that marks an **already-equipped** item. `focus <slot-or-item>` resolves the
   argument to an occupied equipment slot (reuse `_EQUIPMENT_SLOT_ALIASES` and the equipped-item
   name matching `unequip_item` already does), and adds that slot to `focus_slots` (replacing any
   existing entry under the v1 single-focus cap). `unfocus [slot-or-item]` clears it; bare
   `focus` with nothing equipped or an empty arg explains how to use it. Reject marking an empty
   slot ("you have nothing equipped there to channel through"). No wearable bypass, no new equip
   path — the item is already equipped by the normal rules. Add the verb to the help text. The
   GUI toggle (point 2) routes through the same engine method this command calls.

4. **Stats are automatically correct** — because a focus is just a normally-equipped item, its
   contribution to `equipment_bonus` ([combat.py](../wildmagic/combat.py)) is exactly its own
   slot's legitimate `EQUIPMENT_SPECS` bonus. No phantom stats, no slot-aware skipping needed.
   (This is the problem the dedicated-slot draft would have created and this design avoids.)

## Part C — Resolver wiring (mirrors caster_profile)

1. **`state_view.py`** — add `"spell_foci": resolve_foci(engine)` to the resolver context (a
   **list**, for forward-compat with multiple foci; v1 length 0 or 1). For each slot in the
   controlled entity's `focus_slots` that holds an item, `resolve_foci` reads the equipment key,
   then assembles `{"name", "description", "power", "themes"}` from `item_lore`
   (description/themes/display_name), `FOCUS_SPECS`/`EQUIPMENT_SPECS` (power when known), with
   sensible fallbacks. Empty list when nothing is marked.

2. **`prompts.py`** — new `focus_prompt_block(foci: list[dict]) -> str`, parallel to
   `caster_prompt_block`. Empty list → empty string (no bare header). Renders the system-prompt
   addendum (one bullet group per focus; v1 has one):

   ```
   Spell focus (the implement the caster channels through — weigh it HEAVILY in the
   spell's flavor; let it shape imagery, element, and tone. Shape the flavor when
   compatible; do NOT override the spell's stated intent):
   - The caster channels through: {name}.
   - What it is: {description}              # omitted when no lore/description
   # Phase 2 hook (commented until power scales numbers):
   # - A potent focus: lean magnitudes to the HIGH end of each band ...
   ```

   A plain item with no lore emits just the name line (or a brief "nothing remarkable" note),
   so weak focuses barely steer — the gradient the design wants. The "do NOT override intent"
   clause keeps a thematic focus from hijacking an explicit spell (a fire-orb focus shouldn't
   turn "heal me" into a burn).

3. **`wild_magic.py`** — add `spell_foci` to the stripped-keys set in `_wild_prompt_messages`
   and pass `focus_block=focus_prompt_block(spell_foci)` into
   `assemble_resolver_system_prompt`.

4. **`capabilities.py`** — `assemble_resolver_system_prompt` gains a `focus_block: str = ""`
   param appended as `... + region_block + caster_block + focus_block`.

## Part D — Power & attributes (Phase 2, not now)

The focus ships semantic-only. The v1 data (`FOCUS_SPECS.power`, `item_lore.power`) is already
present so Phase 2 is purely additive — uncomment the power branch in `focus_prompt_block` to
make a potent focus push the severity bands up, exactly as Attunement ≥5 does in
`caster_prompt_block` today. Power derivation, in order: explicit `FOCUS_SPECS`/`EQUIPMENT_SPECS`
value → modest default → eventually an LLM-assigned value stored in `item_lore` at investigate
time (rarity/theme-driven). "Less powerful" focuses = items with sparse/no lore and baseline
power. Because `resolve_foci` already returns a list, multiple foci can later aggregate (sum or
cap their power, concatenate their themes) without touching the call sites built now.

## Files Touched (summary)

| File | Change |
| --- | --- |
| `wildmagic/autoplay.py`, `wildmagic/ui.py`, `tests/test_autoplay.py` | rename `spell_focus` → `autoplay_run_theme` |
| `wildmagic/engine.py` (`GameState`) | add `item_lore` field + single `set_item_lore(key, display_name, description, source)` writer (precedence merge) |
| `wildmagic/actions.py` | item-lore materialization inside `_apply_canon_record_side_effects` (live + replay); `focus`/`unfocus` commands; help text |
| `wildmagic/items.py` | write `item_lore` on pickup, keyed by inventory key (`item_type or name`) |
| `wildmagic/models.py` | add `Entity.focus_slots: list[str]`; surface in `to_public_dict` |
| `wildmagic/game_data.py` | optional `FOCUS_SPECS` (power/themes/description for curated foci) |
| `wildmagic/state_view.py` | `resolve_foci` + `spell_foci` context key; add `equipment`+`focus_slots`+`item_lore` to `state_summary`; optional `item_card` description |
| `wildmagic/prompts.py` | `focus_prompt_block` (list input) |
| `wildmagic/wild_magic.py` | strip `spell_foci`; render focus block |
| `wildmagic/capabilities.py` | `focus_block` param on `assemble_resolver_system_prompt` |

> **No change needed in `combat.py`** — a focus is a normally-equipped item, so its stat bonus
> is already correct (Codex #1 dissolved by the mark-not-slot design).

## Shipping order

Three reviewable changes, smallest blast radius first (matches Codex's suggestion):

1. **Autoplay rename** — `spell_focus` → `autoplay_run_theme`. Pure rename, no behavior change.
2. **Item-lore retention + replay** — `item_lore` field, `set_item_lore` writer, pickup +
   canon-side-effect population, and the `state_summary` additions so replay comparison covers
   it. Independently useful (loot keeps its flavor) even before foci exist.
3. **Focus mark + resolver wiring** — `focus_slots`, `focus`/`unfocus` command + GUI toggle,
   `resolve_foci`, `focus_prompt_block`, and the `wild_magic`/`capabilities` plumbing.

## Testing

- Rename: existing `tests/test_autoplay.py` green after the symbol rename.
- `item_lore`: investigate an item → pick it up → assert lore persists, keyed by the inventory
  key; replay round-trip reproduces `item_lore` deterministically (via the canon side-effect
  path, not a live-only write).
- Keying: a conjured/template item whose `item_type != name` resolves its lore by the inventory
  key, not the display name.
- Precedence: a thin pickup `description` does **not** clobber an existing `investigated`
  entry; a later Investigate **upgrades** a thin pickup entry; result is independent of write
  order.
- Focus mark: `focus <equipped item>` marks it; marking an empty slot is rejected; marking a
  second replaces the first under the v1 cap; `unfocus` clears it; the mark **survives
  body-swap** (it lives on the entity).
- Stats: an item marked as focus contributes only its own slot's `EQUIPMENT_SPECS` bonus (no
  phantom stats).
- Replay inspection: `state_summary` includes `equipment`, `focus_slots`, and `item_lore` so a
  replay comparison actually covers focus state.
- Resolver: `focus_prompt_block` appears in the assembled system prompt when a focus is marked
  and is absent (no bare header) when `focus_slots` is empty — mirror the `caster_prompt_block`
  coverage style in `tests/test_capability_routing.py`.

## Resolved Decisions (confirmed 2026-06-19)

- **Mark by slot**, not item key — the focus follows whatever occupies the marked slot.
- **GUI toggle is the primary interaction**; `focus`/`unfocus` verbs ship as a secondary
  convenience.
- **Curated `FOCUS_SPECS`: yes, a small starter set** (~3 hand-authored foci — e.g. orb, sigil,
  reliquary — with strong descriptions + power values) so there's evocative texture to test with
  immediately, alongside the any-found-item mechanism.
- **Commits:** none during execution — changes are left in the working tree for review.

## Open Questions (deferred, non-blocking)

- **Multi-focus cap.** v1 caps `focus_slots` at one entry. When we lift it: a hard cap (e.g. 3),
  or gated by a stat/feat? And does power aggregate (sum, or strongest-wins)?
- **Floor-item descriptions in `item_card`** — surface now (Part A bonus) or defer? (Leaning
  defer; tangential to the focus feature.)
