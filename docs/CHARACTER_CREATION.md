# Character Creation — Plan

Status: **planning** (not yet implemented). This captures the design agreed in the
2026-06-13 session so it isn't lost. Update once it's built.

## Decisions locked

- **Stat block: three wild-magic-flavored stats** — Vigor / Attunement / Composure.
  No classic D&D six; no Cunning (perception/social handled by origin + items for now).
  | Stat | Drives |
  |---|---|
  | **Vigor** | `max_hp`, melee `attack`/`defense`, carry/encumbrance |
  | **Attunement** | `max_mana`, spell potency (thumb on severity / effect magnitude) |
  | **Composure** | how hard wild magic bites back — surge/backfire volatility |
- **Assignment: origin baseline + small point pool.** Each origin sets a themed
  baseline; the player then spends a small pool (~3 points, soft per-stat cap).
- **Composure is central to the wild-magic loop** (not just a stat that sits there).
- **Free-form fields (all four):** Name, Physical description (LLM-fed), Backstory
  blurb, Magical signature / casting style.
- **Creation UX: quick-start + optional customize.** Enter = sensible random
  character (death-heavy game, many runs); opt into the full creation screen.

## Origins (v1)

Each ties to a magical tradition and seeds a stat baseline + starting items.

- **Bone-singer's apprentice** — bone/sound tradition; Attunement-lean; bone implement.
- **Deserter charter mage** — ex-imperial; Composure-lean (trained discipline) but the
  Empire knows your face; charter gear. (Recognition/bounty behavior is a *later*
  feature; creation just stores the flag.)
- **Desert nomad** — sound-magic folk; Vigor-lean; mobile, trade-savvy; nomad goods.
- **Merfolk exile** — water/trench tradition; balanced; trench relic; outsider faction
  reactions.

## How Composure hooks the wild-magic loop

Reality check from the code: there is **no numeric backfire roll** today. The LLM
resolver picks a `severity` (minor→catastrophic) and attaches `costs` (mana,
self-`burning`, curses, scheduled wrath). "Wildness" exists but is a *region/depth*
axis, not per-cast. So Composure hooks in two places:

1. **Resolver prompt (primary lever).** Inject Composure as an explicit volatility
   dial: low → backfires more frequent and *gorgeous-but-costly*; high → the wild
   answers more cleanly. Matches the aesthetics bible ("wild magic doesn't entirely
   love you back").
2. **Light mechanical post-pass (stub).** `adjust_for_composure(resolution, composure)`
   clamps self-directed cost durations a notch up/down, so the stat still bites on the
   offline/fallback resolver. Deliberately light; balance-tuned later.

## Free-form fields → systems

- **Name** → logs, NPC dialogue, the clerk's warrants.
- **Physical description** → stored on the player + fed to the LLM (NPC perception +
  cast/dialogue flavor). `NPCProfile` already has `appearance`; player needs the
  equivalent.
- **Backstory blurb** → resolver idiom + NPC reactions, layered on the mechanical origin.
- **Magical signature** → persistent flavor lens injected into *every* spell resolution.

## Implementation outline (file-by-file)

Grounding facts:
- Player `Entity` is created in **three** places in `generation.py` (~L326, L446, L591),
  one per scenario — needs a single source of truth.
- `GameState` (`engine.py:171`) holds `inventory`, `curses`, `scenario`. An existing
  `GameStats` is gameplay counters — do **not** reuse that name; character stats go in a
  new `CharacterProfile`.
- LLM spell context is assembled in `context_for_llm` (`engine.py:2163`) and spliced
  into the system prompt by the builder in `prompts.py` — `region_style` is the existing
  pattern to mirror for the player profile + Composure dial.
- No JSON/pickle save-load path exists today → no serialization migration needed.

1. **New module `wildmagic/character.py`**
   - `STATS = ("vigor", "attunement", "composure")`; `CREATION_POINTS = 3`; per-stat cap.
   - `@dataclass Origin`: `id, name, tradition, blurb, stat_baseline, starting_items,
     default_appearance, default_backstory, default_signature, faction_notes`.
   - `ORIGINS` — the four above.
   - `@dataclass CharacterProfile`: `origin_id, name, vigor, attunement, composure,
     appearance, backstory, signature`; helpers `derive_hp/derive_mana/derive_attack`,
     `composure_band()` ("low/steady/high"), `to_prompt_dict()`, `to_public_dict()`.
   - `default_profile(rng)` (random, used by autoplay/tests/quick-start so nothing blocks
     on input); `build_profile(...)` with cap validation.

2. **`engine.py`**
   - Add `character: CharacterProfile` to `GameState` (default = `default_profile`).
   - `Engine.__init__(..., character=None)` stores it on `state.character` **before**
     generation.
   - New `_make_player(x, y)` helper builds the player `Entity` from `state.character`
     (derived hp/mana/attack/defense; `description` = appearance). Replace the 3 inline
     `Entity(id="player", …)` sites with it.
   - Merge `origin.starting_items` into starting `inventory`.
   - `context_for_llm`: add `"player_profile"` (appearance, backstory, signature,
     origin/tradition, composure band), marked like `region_style`.
   - `dialogue_context_for_llm`: include player appearance so NPCs perceive it.

3. **`prompts.py`**
   - Splice player appearance/backstory/signature as flavor; add the Composure volatility
     instruction; inject the magical signature as a persistent per-cast flavor lens.

4. **Composure mechanical stub** (`wild_magic.py` / `effects.py`)
   - `adjust_for_composure(resolution, composure)` clamping self-directed cost durations.

5. **`cli.py` — quick-start + optional customize**
   - Enter = quick-start (random `default_profile`); `customize`/`c` → guided flow
     (origin by number → spend points → free-form fields, blank accepts defaults).
   - `--quickstart` flag for non-interactive starts; **autoplay always uses
     `default_profile`** so the harness never blocks on input.

6. **Tests — `tests/test_character.py`** (no LLM, deterministic)
   - Origin baseline + spend → expected derived stats; cap enforcement; default validity;
     player `Entity` reflects profile across scenarios; `context_for_llm` includes
     `player_profile`; composure band correct at low/mid/high.

7. **Docs** — flesh out this file once built; tick the relevant Open Question in
   `AESTHETICS_AND_TONE.md`.

### Order of work
data model → engine player creation + context → prompts/Composure → CLI flow → tests →
docs. Each step keeps the existing suite green.

## Deferred (not in v1)
- Exact derivation constants (pick sane starting values, tune later).
- Deserter's "Empire knows your face" recognition/bounty behavior (store the flag now;
  behavior is a separate feature).

## Dependency: entity unification (decided)
We've since decided to **unify PC / NPC / enemy onto one entity system** (see
`ENTITY_UNIFICATION.md`). Consequently `CharacterProfile` is **not** player-only — it
**becomes the universal entity profile** that any entity carries (the existing
`NPCProfile` persona fields converge into it). Build character creation on top of the
unified profile so the two efforts don't fork. Likely sequencing: land the universal
profile + control indirection first, then character creation, then the body-swap effect.
