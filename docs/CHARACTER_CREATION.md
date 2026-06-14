# Character Creation — Plan

Status: **shipped (v1), 2026-06-13.** Stats, origins, the quick-start + customize CLI
flow, stat→combat derivation, the stat-scaled resolver anchors, and NPC perception of the
player are all in. This doc records the decisions and what landed; tuning and the deferred
items below remain. See also `ENTITY_UNIFICATION.md`.

## Decisions locked

- **Stat block: three wild-magic-flavored stats** — Vigor / Attunement / Composure.
  No classic D&D six; no Cunning (perception/social handled by origin + items for now).
  | Stat | Drives |
  |---|---|
  | **Vigor** | `max_hp`, melee `attack`/`defense`, carry/encumbrance |
  | **Attunement** | `max_mana`, spell potency (scales the magnitude anchors sent to the resolver) |
  | **Composure** | how hard wild magic bites back — surge/backfire volatility |
- **Assignment: origin baseline + small point pool.** Each origin sets a themed
  baseline; the player spends a small pool (`CREATION_POINTS = 3`, soft cap `STAT_CAP = 6`).
- **Stats meaningfully change the start (noticeable spread).** Creation is not just
  flavor: Vigor maps starting HP across roughly **16–32** and Attunement maps starting MP
  across roughly **8–20**, so a Desert Nomad is visibly tankier than a Bone-singer.
- **Stats modulate the LLM resolver's anchors — numbers *and* tone.** This is the core
  mechanism (see "Stats → resolver anchors" below). Composure is the headline case, but
  the principle is general: every stat shifts the anchors we send the model.
- **Free-form fields (all four):** Name, Physical description (LLM-fed), Backstory blurb,
  Magical signature / casting style.
- **Name stays external; the log stays second-person.** The message log keeps saying
  "You". The entered name surfaces only where *others* refer to you — NPC dialogue,
  imperial warrants, the clerk's memos.
- **Creation UX: quick-start + optional customize.** Enter = sensible random character
  (death-heavy game, many runs); opt into the full creation screen.

## Origins (v1) — BUILT

All four exist in `character.py` (`ORIGINS`), each with a stat baseline, starting items,
and default appearance/backstory/signature:
- **Bone-singer's apprentice** — bone/sound; Attunement-lean.
- **Deserter charter mage** — ex-imperial; Composure-lean; `faction_notes="empire_recognizes"`
  (recognition/bounty behavior is a *later* feature; the flag is stored now).
- **Desert nomad** — sound-magic folk; Vigor-lean.
- **Merfolk exile** — water/trench; balanced.

## Stats → resolver anchors (the core of "Composure is central")

The wild-magic resolver is prompt-driven: `prompts.py:SYSTEM_PROMPT` gives the model
severity→number bands (e.g. "major: damage ~8–15, radius 3–5") and volatility/cost
framing. Today `caster_profile` is only dumped into the JSON payload — the model *sees*
the stats but gets no instruction to act on them. The plan: a **stat-scaled anchor
block** spliced into the system prompt the same way `region_prompt_block` already is.

- **Attunement → magnitude.** High Attunement pushes the suggested effect magnitudes
  toward (and slightly past) the top of each severity band; low Attunement keeps them at
  the floor. ("This caster is strongly attuned — lean damage/heal/radius high.")
- **Composure → volatility.** Low Composure tells the model wild magic answers more
  chaotically: backfires/costs more frequent and *gorgeous-but-costly*, more surprising
  side-effects. High Composure: the wild answers cleanly, backfires rarer and gentler.
- **Vigor → physical framing.** High Vigor lets the model lean on health/physical costs
  the caster can shoulder; low Vigor steers costs away from raw HP.
- **Signature → flavor lens.** The magical signature is injected as a persistent per-cast
  idiom ("everything you cast smells of brine and copper"), used sparingly so it tints
  rather than dominates.

Mechanism, not magic numbers: a helper (e.g. `prompts.caster_prompt_block(profile)`)
emits the stat-conditioned guidance; constants are tunable. Because it shapes the prompt,
it works through the normal resolver. The deterministic/offline fallback can read the
same profile to bias its canned anchors so the stats still bite without an LLM.

## Free-form fields → systems

- **Name** → NPC dialogue, imperial warrants, the clerk's memos (NOT the log).
- **Gender** → Male / Female / Other (custom text). Stored on the profile; when set it is
  prepended as the **first word of the physical description sent to the portrait LLM**.
- **Physical description** → stored on the entity (`description`) and fed to the LLM:
  the spell resolver's `caster_profile` (done) AND NPC dialogue context (TODO — NPCs
  don't yet perceive the player's appearance).
- **Backstory blurb** → resolver idiom + NPC reactions, layered on the mechanical origin.
- **Magical signature** → persistent flavor lens injected into every spell resolution.

## What's already BUILT (entity-unification refactor)

- `character.py`: `STATS`, `CREATION_POINTS=3`, `STAT_CAP=6`, `Origin` + the four
  `ORIGINS`, `default_profile()`, `build_profile()` (cap-validated), `clone_profile()`,
  `starting_inventory_for()`.
- `CharacterProfile` (models.py): vigor/attunement/composure + appearance/backstory/
  signature, `composure_band()`, `to_public_dict()`.
- `_make_player()` (generation.py): single source of truth for the player entity; stamps
  the profile, sets `description` from appearance, seeds origin starting inventory.
- `GameState.character` handoff slot; `context_for_llm` exposes `caster_profile`.

## Shipped (v1)

1. **Stat → combat derivation** — `CharacterProfile.derive_max_hp/derive_max_mana/
   derive_attack/derive_defense`; `_make_player` uses them. HP `12 + 4·vigor` (16–32 over
   the normal range, up to 36 at the vigor cap), MP `5 + 3·attunement` (8–23), attack
   `3 + vigor//3`. A 3/3/3 character reproduces the old 24 HP / 14 MP baseline.
2. **Handoff** — `GameEngine.__init__(..., character=None)` sets `state.character` before
   generation; `GameSession.__init__(..., character=None)` passes it through. Autoplay/
   tests pass `None` → random `default_profile`, so nothing blocks on input.
3. **`prompts.caster_prompt_block`** — spliced into the resolver system prompt by
   `_wild_prompt_messages` (which now strips `caster_profile` from the user JSON, like
   `region_style`). Attunement shifts magnitude, Composure shifts volatility, Vigor shifts
   cost framing, signature/appearance tint the prose. Middling 3/3/3 caster adds nothing.
4. **Fallback bias** — `fallbacks.bias_resolution_for_profile` scales amounts by Attunement
   and adds a strain cost at low Composure, applied to local (non-LLM) fallbacks so stats
   still bite offline.
5. **NPC perception** — `dialogue_context_for_llm` sends the player's appearance and the
   external name (or "a wandering stranger" for the nameless default; the body's own name
   after a body-swap).
6. **CLI** — `cli.prompt_character_creation`: a menu of the four ready-made characters
   (pick a number to play as-is), `customize` → origin pick → point spend → free-form
   fields (incl. gender), or Enter for a random wild mage. `--quickstart` skips it.
6b. **Pygame UI** — a **single-screen scene** in its own module,
   `wildmagic/scenes/character_creation_scene.py` (`CharacterCreationScene`), shown at
   startup. Three columns: ready-made characters + **Random** on the left; the editable
   build in the middle (origin blurb, stat point-spend, and the free-form fields — name,
   **gender** (Male/Female/Other), description, backstory, signature); the **portrait +
   Begin** on the right. Fully mouse-clickable (every control has a hitbox) with keyboard
   Tab/arrows. Picking an origin seeds the editable build; Begin applies it. The host
   `GameUI` provides the surface/fonts/`draw_text`/portrait client and a
   `finish_creation(profile)` callback that calls `GameEngine.restamp_player` (the world
   was already built with a random default player, so it's restamped in place at turn 0,
   not regenerated). Autoplay skips the scene.
   - Shared palette/helpers live in `wildmagic/ui_theme.py` so scene modules don't import
     `ui.py` (which imports them) — avoiding a cycle. This is the first extracted scene;
     others can follow the same pattern.
7. **Tests** — `tests/test_character.py` (9 tests, no LLM). Full suite green (167).

### Name handling
The chosen name lives on `CharacterProfile.name`, surfaced only externally (NPC dialogue,
later warrants). The log stays second-person "You" (entity name unchanged). On body-swap,
an inhabited body's empty profile name means NPCs call you by that body's name.

### In-game character view
`wildmagic/scenes/character_view_scene.py` (`CharacterViewScene`), opened with **c**
(or **Ctrl+c**) during play. Mirrors creation but edits the *live* player: update name,
gender, description, backstory, signature, and regenerate the portrait; stats are shown
read-only (no mid-run respec). Edits commit to the player profile on close (Esc/Done).
Both scenes share `scenes/_widgets.py` (text field, gender selector, portrait panel).

### Input modes (in-game)
**Tab** cycles Wild Spell → Controls → Talk (Talk only when an NPC is in range). In
Controls mode the letter keys are hotkeys (`c` opens the sheet, `j` journal, etc.).
Holding **Ctrl** is a momentary Controls modifier, so **Ctrl+c** opens the sheet without
leaving Wild Spell mode. Ctrl+C still copies when log/LLM text is selected.

## Deferred (not in v1)
- Tuning the exact derivation/anchor constants (sane values now, balance later).
- Deserter's "Empire knows your face" recognition/bounty behavior (flag stored; behavior
  is a separate feature).
- Folding `NPCProfile` persona/wares fully into the universal profile (still separate).
