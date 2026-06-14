# Semantic Effects — Latent Mechanics in an LLM-Resolved Roguelike

Status: **substrate implemented (2026-06-14)**; consumers wired for resolver + dialogue.
Owner: resolver / world-state.
Companion to `CAPABILITY_ROUTING.md`, `CAPABILITY_CARD_PLAN.md`, and `WILD_MAGIC_SCHEMA.md`.

## 0. Implementation status (2026-06-14)

The substrate from §6–§9 is built and tested (`wildmagic/semantics.py`, `tests/test_semantics.py`,
234 tests green):

- **Note/anchor model + ledger** — `WorldNote` and `SemanticLedger` in `semantics.py`, anchored
  over entity / item / place / faction / world. De-dupes, caps per anchor (evicting
  lowest-salience), ranks by salience×recency, and decays by TTL. Lives on `GameState.semantics`;
  `GameEngine._tick_auras`/turn loop calls `semantics.decay()` each turn.
- **Entity traits channel** — `Entity.traits` (narrative facts), surfaced in `to_public_dict`, so
  they ride into any prompt the entity appears in for free.
- **Scene assembler** — `GameEngine.scene_anchors_around()` + `collect_scene_notes()` gather the
  place/faction/world notes in scope, budgeted. Injected as `scene_notes` into **both**
  `context_for_llm` (resolver) and `dialogue_context_for_llm` (dialogue); item traits flow into
  `floor_items`/`nearby_objects`, the player's traits into the dialogue `player` block.
- **Shared interpretation preamble** — `SEMANTIC_PREAMBLE` is spliced into the resolver
  `CORE_PROMPT` *and* the `DIALOGUE_SYSTEM_PROMPT` from one source, so a trait means the same
  thing to both models.
- **Write paths** — the `add_trait` effect mints a trait onto an entity *and* records a ledger
  note (the spell-facing mint); `GameEngine.record_note()` is the single deposit API; combat
  writes back a place note when a non-player entity is slain ("X was slain here").
- **Observability** — `scene_notes` rides inside the logged resolver/dialogue context, so audits
  capture which facts surfaced on each call.

**Not yet built (deliberate, see §4/§7):** automatic crystallization of a trait into an `aura`/tag
(the model can already do it by emitting an `aura` effect; no dedicated promotion verb yet); a
periodic LLM consolidation pass for contradiction-resolution (only the cap + TTL bound notes
today); faction/world note *minting* from trade/lore consumers (only combat + `add_trait` write
back so far); and surfacing into the trade/lore prompts (resolver + dialogue are wired; the rest
reuse the same `collect_scene_notes` when wanted). The cheapest next step remains the live
animated-hat probe in §8.

## 1. The idea

A normal roguelike has a hard wall between **flavor text** and **mechanics**. A "righteous,
goblin-hating hat" is a string; it does nothing until a designer writes a rule for it.

This game does not have that wall, because the thing that resolves spells, drives creature
behavior, and voices NPCs is a language model that *re-reads descriptions at decision time*.
So a descriptor like "righteous, goblin-hating hat" is not flavor and not mechanics — it is
**mechanics in a dormant state**, waiting for a context where it becomes relevant:

- The hat is animated (`animate_object`) → the resolver, seeing "goblin-hating," has the hat
  pick a goblin to attack.
- An NPC sees the player wearing it → the dialogue model colors their reaction.
- A goblin merchant is asked to trade → the trade model factors in the insult.

None of those payoffs were authored as rules. They emerge because the trait was *present in
the prompt* at a moment the model was making a judgment. This is a genuine structural
advantage of an LLM-driven game, and it is cheap to produce: the player (or the resolver)
can mint evocative, specific content and let the mechanical payoff arrive *later*, through
the model, in contexts nobody enumerated in advance.

The question this document answers: **how far should we lean into semantic-only content, and
how do we make it pay off reliably without wrecking the things roguelikes depend on?**

## 2. The one fact that drives every decision

A semantic effect only exists **if it is in the prompt at the moment it becomes relevant.**

The hat attacks the goblin only if, when the hat acts, *both* "goblin-hating" *and* "there is
a goblin at (x,y)" are in the same context window. This is not a storage problem — strings are
free to store. It is a **retrieval / context-assembly problem**, the same one
`CAPABILITY_ROUTING.md` wrestles with for capability cards.

This single fact has a hard consequence:

> **Attach traits to entities and items, never to "ambient space around creatures."**

An entity's description already rides along in its context block everywhere that entity
appears — resolver, AI, dialogue, trade. So an entity-attached trait gets surfaced *for free
and reliably* whenever the entity is in play. A free-floating "semantic field on nearby
creatures" is the expensive, leaky version: we would have to actively gather and inject it,
and it would usually be *absent* exactly when it mattered. Entity-attached traits are
Chekhov's guns that stay on the mantel; ambient traits are guns that wander off before Act 3.

We already have the right substrate — see §6.

## 3. Design principles (the load-bearing rules)

1. **Semantic by default, mechanical on demand.** A trait is, until proven otherwise, pure
   description with no fixed rule. It is cheap to mint precisely because it promises nothing.
   It becomes mechanical only when the model decides a situation squarely calls for it.

2. **Cost at mechanization, not at description.** Sprinkling a trait is free. When a trait is
   *cashed out* into a real effect — the hat actually swings at the goblin — that **action**
   passes through normal resolution: severity, costs, clamps. This is self-balancing: an
   unredeemed trait that never becomes relevant costs nothing and quietly fades, so there is
   no broken promise, because nothing was promised. It also dodges an otherwise nasty
   question ("how do you price a power whose payoff is unknown?") by never pricing the
   description at all.

3. **Never on the critical path.** A player must **never need** a semantic effect to fire in
   order to make progress. Semantic effects are *upside and delight*, never a gate. They must
   not silently lock a quest, create an unwinnable state, or be the only way past an
   obstacle. This is the rule that protects everything in §5.

4. **Weigh, don't apply.** When we tell the model about traits, the instruction biases
   *judgment*, not *mechanization*. "Let them color tone, targeting, and plausibility; you
   *may* turn one into a concrete effect when the situation squarely calls for it" — not "use
   these traits to resolve the spell." The verbs are the guardrail against over-firing.

## 4. The lifecycle: semantic → (judgment) → mechanical

The centerpiece. Purely-semantic-and-load-bearing is a trap (see §5 on legibility); the way
out is a **two-stage life cycle** that gives both emergence *and* eventual reliability.

```
   mint a trait            model judges it          crystallize
   (free, no rule)   -->   relevant, this cast  --> into a standing
   "goblin-hating"         (weighed, may fire)      mechanic (aura/tag)
        |                        |                        |
   stage 1: SEMANTIC        one-off payoff           stage 2: MECHANICAL
   color only               (a single resolution)    reliable + legible
```

- **Stage 1 — semantic.** The trait sits on the entity as narrative weight. It colors tone,
  targeting, and plausibility whenever the entity is in a prompt. No rule, no guarantee.
- **One-off payoff.** On a given cast/turn the model may *weigh* the trait and let it shape a
  single resolution (the animated hat targets the goblin this turn). Still no standing rule.
- **Stage 2 — mechanical crystallization.** When a trait genuinely matters, the resolver can
  **promote** it into a real standing modifier using machinery we already built: an `aura`
  (e.g. +damage vs goblins) or a deterministic tag the AI reads every turn
  (`hates:goblin` → `_select_target` prefers goblins). After promotion it is reliable and
  legible — the player can see "Righteous (hates goblins)" and reason about it.

This is where the recent `aura` / `weakened` / behavior-tag work pays a **second dividend**:
crystallization has somewhere to land. Promotion is just emitting an `aura` effect or a
behavior tag whose *content* came from a trait the model read.

## 5. The risks, and how each is mitigated

| Risk | Why it bites | Mitigation |
|---|---|---|
| **Illegibility / unfairness** | A trait that fires *sometimes* (LLM nondeterminism) breaks the player's mental model; unreliable-but-powerful corrodes strategy. | Principle §3.3 (never on critical path) + the lifecycle §4 (promotion makes load-bearing traits reliable). Semantic stays delight; mechanical stays predictable. |
| **Trait soup / context bloat** | Over a long run, entities accumulate contradictory descriptors ("cowardly" + "fearless"), bloating prompts and confusing the model. | Per-entity **cap** with newest-wins; optional periodic LLM **consolidation** pass. Same bounding problem `NPCProfile.memory` already has — reuse the approach. |
| **Chekhov's whiff** | A trait surfaced once and never again is wasted tokens and a quiet broken promise. | Entity-attached storage (§2) means traits ride with the thing and *do* get re-surfaced. Resist the ambient-field version. |
| **Over-firing / power creep** | "Use traits to resolve" makes every descriptor a power. | "Weigh, don't apply" framing (§3.4); cost-at-mechanization (§3.2) means firing isn't free. |
| **Contradiction with existing balance** | Powerful spells are fine *with costs* (see `feedback`/balance notes), but semantic payoff is deferred and unpriced. | Cost-at-mechanization resolves this directly: the description is free, the cashed-out action is priced normally. |

## 6. What already exists (we are formalizing, not inventing)

This is mostly a generalization of patterns the codebase already has:

- **`entity.description`** — free-text already surfaced in context blocks.
- **`entity.tags`** — discrete flags; the AI already reads behavior tags (`aura_*`,
  `guardian`, `pacifist`, death-triggers) deterministically every turn. This is the natural
  home for *crystallized* traits.
- **`entity.details`** — a per-entity dict for structured extras.
- **`NPCProfile.memory`** — **this is already semantic effects for NPCs.** It is a list of
  narrative notes the dialogue model weighs with no fixed rule, surfaced as
  "things_i_have_noticed." What this document proposes is essentially *generalizing NPC
  memory from NPCs to all entities and items*, plus adding the crystallization path.
- **`aura` effect + behavior tags** (just built) — the landing zone for Stage 2.

So the new surface area is small: a labeled **`traits`** channel on entities/items, the
prompt framing that tells the model how to weigh them, and an optional promotion affordance.

## 7. Proposed shape (minimal)

1. **Storage.** A `traits: list[str]` channel on `Entity` (or a reserved key in `details`),
   capped (e.g. ≤ 6, newest-wins). Items carry theirs through pickup/equip/animate.
2. **Surfacing.** Include `traits` in the entity's `to_public_dict()` so it rides into the
   resolver, AI, dialogue, and trade prompts wherever the entity already appears. No new
   retrieval machinery — that is the whole point of entity-attachment.
3. **Framing.** One short block in the shared prompt preamble:
   > "Entities may carry narrative **traits** — descriptive properties with no fixed
   > mechanical rule. Let them color tone, targeting, and plausibility. You *may* translate a
   > trait into a concrete effect when the situation squarely calls for it (and price that
   > action normally), but do not manufacture power from flavor on every cast."
4. **Minting.** A spell resolution may attach traits to a target via an effect (e.g. an
   `add_trait` effect, or reuse `details`/`description` writes). Free; no severity.
5. **Crystallization (optional, later).** Let the resolver promote a trait into an `aura` or
   a behavior tag when it decides the trait should become standing and reliable. Reuses §4
   machinery; priced as a normal effect.

Stages 1–3 are the high-value, low-risk core. Stages 4–5 are incremental and can wait for the
validation result in §8.

## 8. Cheap validation before building (the animated-hat probe)

Do **not** build the whole thing first. One experiment tells us whether the surfacing and the
"weigh, may-promote" framing actually land:

1. Give an item `traits: ["righteously hates goblins"]`.
2. Animate it (`animate_object`) **next to a goblin** and, separately, **next to a
   non-goblin**.
3. Observe whether the resolver (a) targets the goblin and spares the non-goblin, and (b)
   ever chooses to crystallize the trait into a `hates:goblin` aura/tag.

If (a) works, entity-attached surfacing + "weigh" framing is enough to ship Stages 1–3. If
(b) ever happens, the crystallization path is worth building out. If neither lands, the
problem is surfacing or framing, and we learned it for the price of one scripted scenario
instead of a subsystem.

## 9. Recommendation

**Lean in hard on representation and surfacing; lean in cautiously on load-bearing
reliability.** Concretely:

- **Yes**, make `traits` a first-class, entity-attached, always-surfaced channel — it is
  cheap, high-upside, and we already have every piece.
- **Yes**, tell the model about traits — but with "weigh, don't apply" framing.
- **Yes**, build toward the promotion path so load-bearing traits can become reliable; it is
  where the aura/tag work pays its second dividend.
- **No**, never put semantic effects on the critical path, and never price the description
  (only the cashed-out action).

One line to remember: **semantic by default, mechanical on demand, never on the critical
path.** That lets us be lavish with weird, specific, evocative content without making the
game's reliability hostage to it.
