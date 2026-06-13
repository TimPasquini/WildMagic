# Entity Unification — Plan

Status: **implemented (core)** as of 2026-06-13. Captures decisions and the shipped
refactor. Goal: PC, NPCs, and enemies are all the *same kind of thing* running the *same
systems*, so capabilities available to the player are available to any entity — and you
can body-swap into any of them and "everything just works." Done deliberately, now, to
avoid the late massive refactor we hit in a previous project.

## What shipped

- **Universal profile** — `CharacterProfile` (models.py) lives on `Entity.profile`;
  every player/NPC/enemy carries one (`character.py` holds origins + `default_profile`).
- **Per-entity state** — `inventory` and `curses` moved onto `Entity`. `GameState`
  `.inventory`/`.curses` are now **properties** that resolve to the controlled entity, so
  the ~200 existing call sites follow whoever is controlled with no edits.
- **Control indirection** — `state.player` / `player_id` already meant "the avatar," so
  body-swap is just reassigning `player_id`; everything player-centric follows.
- **Centralized creation** — all five scenario player-spawn sites now call one
  `_make_player(x, y)` that stamps the profile and seeds per-entity inventory.
- **Body-swap** — `GameEngine.swap_control_to(target_id)`, the `possess` wild-magic
  effect (in `SUPPORTED_EFFECTS`), and a player-facing `possess [name]` command. Inherits
  the body's stats/profile, leaves inventory with the body, identity follows the body,
  vacated body becomes an inert `husk`.
- **LLM context** — `context_for_llm` exposes `caster_profile` (the controlled entity's
  profile + composure band), so the resolver styles casts for the soul-in-the-body.
- **Tests** — `tests/test_entity_unification.py` (8 tests); full suite 158 green.

Deferred (documented, not built): deriving combat stats from the profile during
character creation; rich/LLM-tiered NPC autonomy (capability is universal, but routine
NPC turns remain cheap rule-based by latency choice); converging `NPCProfile` persona
fields fully into the universal profile (persona/wares still live in `NPCProfile`).

## North-star scenario
Body-swap into an arbitrary NPC/enemy and seamlessly start controlling it. No special
cases: the same action layer, stats, casting, inventory, and LLM context all operate on
"the controlled entity," whoever that currently is.

## Decisions locked

- **One entity model for PC, NPC, and enemy.** `kind`/`faction`/`ai` stay as *data on*
  the entity, not separate code paths. **Full refactor now**, not incremental.
- **All per-entity state moves onto the Entity:** inventory, curses, mana, and the new
  stats/profile. (Today `inventory` and `curses` are global on `GameState` — player-only.
  They migrate onto the entity.)
- **One universal profile type for all entities.** The character profile
  (Vigor/Attunement/Composure + appearance/backstory/signature) and the existing
  `NPCProfile` (persona/memory/wares) converge into a single profile that any entity may
  carry; most mooks just use defaults/empties. The wild-magic resolver then works
  identically for any caster.
- **Control is a reassignable locus, not a fixed identity.** "Who is the player" becomes
  a variable (a controlled-entity id + an agency flag: input-driven vs AI-driven), instead
  of the hardcoded `player_id` threaded through ~210 call sites.
- **NPC/enemy agency: capability is universal, decision policy stays cheap.** Any entity
  *can* do everything the PC can, but non-controlled entities decide via **rule-based**
  logic by default and only **rarely** make LLM calls. The constraint is **latency**, not
  a hard ban: an occasional LLM-driven NPC action (improvised wild magic, richer dialogue)
  is fine *as long as it doesn't noticeably slow the turn loop* — e.g. infrequent, or
  resolved off the critical path. Routine per-turn NPC/enemy behavior must stay
  LLM-free. Capability ≠ habitual behavior.

### Body-swap rules (locked)
- **Inherit the body's stats/abilities.** You get the target's Vigor/Attunement/Composure,
  HP, and innate abilities. The body defines capability; you bring the steering.
- **Inventory stays with the body.** Items belong to whatever body holds them; swapping
  leaves your old pack behind unless explicitly carried/transferred.
- **Identity/profile (name, appearance) follows the body.** NPCs perceive the body's
  appearance, not your original. The free-form "physical description" is a property of the
  body, not a permanent you. LLM/NPC perception keys off the inhabited body.
- **The vacated body becomes an inert husk.** When the soul leaves, the old body drops
  unconscious/empty with no agency until something re-inhabits it. Swapping is risky:
  you leave a body undefended. (This is a global rule for now; could later become a
  per-effect parameter.)

## Current divergence (what the refactor must collapse)

Already shared: `Entity` holds physical/combat state (hp, mana, attack, defense, statuses,
equipment, resistances) for all kinds.

Player-only coupling (the work):
1. **Control** hardcoded to `player_id` — ~210 references across 12 files (49 in
   `engine.py`).
2. **Inventory & curses** live on `GameState` globally, not on the entity.
3. **Rich action layer** (`cast_wild`, item use, talk, examine) in `actions.py`/`engine.py`
   assumes the actor *is* the player.
4. Planned **CharacterProfile** drafted as player-only.

NPC-only: a *separate, thin* action vocabulary in `ai.py` (move-toward-target, melee,
tag-based summon). NPCs currently **cannot** cast wild magic, use items, or talk — a
different code path entirely.

## Target architecture

- **Universal `Entity` + profile.** Move inventory/curses/stats/persona onto the entity
  (likely grouped in one `profile`/components object hung off `Entity` to keep it lean and
  serializable — decision deferred to implementation: bare fields vs a bundle).
- **Subject-agnostic action API.** Actions take an `actor: Entity` argument instead of
  implicitly meaning the player. `cast_wild`, item use, talk, move, examine all become
  "entity X does this." `cast_standard_*` and mana spend read from the actor entity.
- **Control/agency layer.** A `controlled_entity_id` (default the starting PC) plus an
  agency flag per entity (`input` vs `ai`). The turn loop dispatches: controlled entity
  reads player input; everyone else runs their AI policy. Camera/FOV/UI follow the
  controlled entity. Body-swap = reassign `controlled_entity_id`, set the old body's agency
  to husk/none, set the new body to `input`.
- **LLM context keyed off the subject.** `context_for_llm` and `dialogue_context_for_llm`
  build around "the acting/controlled entity," not `state.player`. The player's profile
  becomes the controlled entity's profile.
- **AI decision policy unchanged in spirit.** `ai.py` keeps choosing actions with cheap
  rules, but now expresses choices through the shared action API. No LLM calls on routine
  NPC/enemy turns; rare/off-critical-path LLM actions are allowed when latency permits.

## Implementation outline (high level — detailed plan TBD)

1. **Profile/state migration.** Define the universal profile; move `inventory` and
   `curses` off `GameState` onto entities; provide accessors so existing call sites keep
   working during migration. Reconcile with `NPCProfile`.
2. **Control indirection.** Replace direct `state.player` / `player_id` assumptions with a
   `controlled_entity` concept. Mechanical, wide, low-risk if done with a property shim
   first (`state.player` can alias the controlled entity during transition).
3. **Subject-agnostic actions.** Thread `actor` through the action layer and effects;
   re-point mana/inventory/casting to the actor entity.
4. **Turn loop & agency.** Unify the per-turn dispatch over all entities by agency flag.
5. **Body-swap effect.** Implement as a wild-magic effect type using the above: reassign
   control, husk the old body, inherit the new body's stats, leave inventory in place,
   re-key identity/appearance to the new body.
6. **NPC capability wiring.** Let `ai.py` invoke shared actions (e.g. innate abilities)
   without LLM calls; retire the duplicated thin vocabulary where the shared API covers it.
7. **Tests.** Body-swap round-trip (control moves, husk left behind, stats inherited,
   inventory stays, appearance re-keyed); NPC uses a shared action; no LLM call on routine
   NPC turns; existing suite stays green.

## Risks / watch-items
- **Breadth of churn** (~210 coupling sites). Mitigate with a transitional `state.player`
  alias that points at the controlled entity, so call sites migrate gradually.
- **Autoplay & tests** assume a single player; ensure `default_profile` + control default
  keep them working.
- **UI/camera/FOV** must follow the controlled entity, not a fixed id.
- **Serialization** — no save system exists today, so no migration burden yet, but design
  the profile to be serialization-friendly for when one lands.

## Relationship to character creation
The planned `CharacterProfile` (see `CHARACTER_CREATION.md`) **becomes** the universal
entity profile rather than a player-only type. Build character creation on top of the
unified profile so the two efforts don't fork. Sequencing TBD: likely land the profile +
control indirection first, then character creation, then the body-swap effect.
