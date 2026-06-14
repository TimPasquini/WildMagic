# Capability Routing — Scaling the Wild-Magic Resolver Beyond One Prompt

Status: **proposal / design doc** (no code written yet). Owner: resolver.
Companion to `EXECUTION_PLAN.md` Phase 8 (Resolver Reliability) and Phase 9 (Spellbook).

## 1. The problem

The wild-magic resolver is one LLM call: typed spell → one JSON object
(`accepted`, `severity`, `effects[]`, `costs[]`, …). Today the entire vocabulary the
model needs lives in a single ~140-line `SYSTEM_PROMPT` (`prompts.py`) — every effect
type, every tile alias, every behavior tag, every balance rule, ~25 few-shot examples.

That worked at the current capability count. It will not keep working. We want to keep
adding *kinds* of wild magic — NPC memory editing (add / remove / alter what an NPC
remembers), weather, time effects, social/reputation effects, building/structure
manipulation, and more. Each new capability wants its own schema fragment, its own
balance rules, and its own one or two examples. If every capability lives in every
prompt:

- **Latency** — prompt length is, per `EXECUTION_PLAN.md` Phase 8 §6, *"the main
  local-latency lever."* On the A750 at ~5.4 tok/s a fat prompt is a real cost. Every
  capability we add taxes every cast, including the 95% of casts that never touch it.
- **Fidelity** — the ["Less is More"](https://arxiv.org/pdf/2411.15399) finding and the
  RAG-MCP results below both show that *fewer, relevant* options improve a small model's
  decisions. A memory-editing block sitting in a fireball's prompt is pure distraction.
- **Maintainability** — one monolith prompt that every capability must coexist in is
  already near the edge of what's reviewable.

The goal: **let the resolver address an open-ended and growing set of capabilities while
each individual cast only ever sees the handful relevant to what the player typed.**

## 2. Constraints that pick the design

These are specific to this project and they rule several popular answers in or out.

1. **Single-pass, latency-bound backend.** The resolver is one shot, and the local GPU
   is slow. A *full second resolution-length generation* on the critical path would roughly
   double cast latency, so the selector must stay cheap. Prefer non-generative routing
   (keyword match, embedding lookup). **Measured caveat (2026-06-13):** a *short*
   generative routing pass — name the spell + a flat list of card names, `think:false`,
   return a JSON array — costs only **~0.25s warm** on `qwen3.5:9b` (≈8 tokens out, the
   card-list prefix prompt-cached to ~37ms), reusing the same resident model with no
   reload. So a generative router *tier* is affordable as a fallback for ambiguous spells;
   it is just never the *default* when a free keyword scan suffices. (This also corrected an
   earlier ~2s-per-call figure that turned out to be a Windows `localhost`→IPv6 stall, now
   fixed — see [[localhost-ipv6-stall]] / MODEL_CONFIG.md.)
2. **The "capabilities" are output schemas, not live services.** Memory-edit, terrain,
   polymorph etc. are *effect types the engine already executes* from returned JSON
   (`SUPPORTED_EFFECTS` in `spell_contract.py`). The model doesn't need to *call* a tool
   and await a result; it needs to know the right schema to *emit*. This is a
   context-assembly problem, not an agentic tool-calling problem.
3. **We own both sides.** No third-party tools, no cross-vendor interop requirement.
4. **There is already precedent in the codebase** for cheap in-process gating of
   expensive LLM calls: `scan_for_trade_intent` (`game_data.py:451`) is a deliberately
   crude, false-positive-biased keyword scan that decides *whether* to spend the trade
   structuring call. Capability routing is the same instinct, one level in: decide
   *which context* the resolver call gets.
5. **Discovery, not per-cast, is the steady state (Phase 9).** The spellbook plan turns
   a good resolution into a learned, deterministic, instant recast — the LLM becomes a
   *discovery engine*, paid once per spell, not once per cast. So routing cost (already
   tiny) is only paid on **first discovery** of a spell, never on the hot recast path.

## 3. What the literature says (and what we take from it)

The "too many tools" problem is well-studied; the consensus answer is **retrieval, not
stuffing**:

- [RAG-MCP](https://medium.com/towards-explainable-ai/llms-drowning-in-tools-rag-mcp-is-the-smart-lifeline-you-need-55781c7d440f):
  retrieve only relevant tools per query → **>3× tool-selection accuracy, >50% fewer
  prompt tokens.**
- [ToolRAG](https://github.com/antl3x/ToolRAG) / [MCP-Zero](https://arxiv.org/pdf/2506.01056):
  vector-retrieve the relevant tool subset from a large registry instead of presenting
  all of them.
- [Less is More](https://arxiv.org/pdf/2411.15399) (edge devices): selectively reducing
  the tool set *improves* decisions, fine-tuning-free.

What we take: the **retrieval-gated subset** idea. What we leave: the heavy agentic /
two-pass framing. Our retrieval is an embedding lookup or keyword scan, not a generation,
so we get the token/accuracy win without the latency hit.

## 4. Decision

Adopt a **capability-card** architecture: Skills-style progressive disclosure (a lean
base + a one-line index, full detail loaded only when relevant), gated by a **cheap,
non-generative, tiered selector**, and reinforced by **dynamic schema tightening** so an
un-loaded capability is *structurally* un-emittable.

Explicitly:

- **Adopt:** capability cards + keyword routing (tier 1) + optional embedding routing
  (tier 2) + dynamic JSON-schema enums per cast.
- **Defer:** MCP. It solves interop and live tool-calling we don't have. Revisit only if
  a capability needs to *read live game state mid-resolution* (see §9) — and even then a
  plain in-process function beats a protocol until we want external tools.
- **Reuse:** the `scan_for_trade_intent` gating pattern, the purpose-scoped Ollama config
  (a CPU embedding route slots in exactly like the existing BACKGROUND/lore routes), and
  the existing `spell_contract.py` contract layer.

## 5. Architecture

### 5.0 Framing: the LLM proposes a composition; the engine is the source of truth

Two principles shape everything below:

- **Spells are compositions of engine primitives, not single effects.** "Turn the rain
  into knives" is weather + area damage + hazardous tiles; "make the guard forget me and
  see my enemy's face" is memory-edit + illusion + faction hostility. The resolver's real
  job is to *map poetic intent onto a small set of safe engine primitives* — so routing
  must be able to pull in **several** co-occurring cards: the engine auto-loads each
  selected card's frequent specialist partners as bonus cards, while universal primitives
  (AoE, damage, status, tiles) live in the always-on core so they can never be missed
  (§5.1 `common_combos`, §5.2 core, §5.3 recall bias).
- **The engine validates, clamps, and prices; the model only proposes.** Routing and
  dynamic schemas keep the model *on rails*, but they never replace the existing
  post-generation pipeline. This is already how the resolver works today — `parse_resolution_json`
  → `_normalize_resolution` (effect/cost alias + tile inference) → `validate_resolution`
  (`spell_contract.py`) → engine apply/clamp. Capability routing changes *what context the
  model sees*, not the rule that **every** field is re-validated and balance-clamped after
  generation. A narrowed schema that still admits `{"area_damage", "radius": 999}` is fine;
  the engine clamps it by severity/mana/range as it does now.

### 5.1 The capability card

Each capability is a small, self-contained, versioned record. New module
`wildmagic/capabilities.py` (data + registry; no provider logic, mirroring how
`spell_contract.py` holds contract data):

```python
@dataclass(frozen=True)
class CapabilityCard:
    name: str                      # "memory_edit"
    triggers: tuple[str, ...]      # keyword/stem list for tier-1 routing
    embed_description: str         # natural-language gloss for tier-2 routing
    index_line: str                # the ONE line shown in the always-on index
    effect_types: tuple[str, ...]  # SUPPORTED_EFFECTS keys this card unlocks
    prompt_block: str              # schema fragment + balance rules + limits, injected when selected
    examples: tuple[str, ...]      # 1-2 few-shot JSON examples, injected when selected
    cost_hint: str = ""            # optional: how this capability should be priced
    # Composition + scoping (see §5.0, §5.3, §5.5):
    common_combos: tuple[str, ...] = ()   # specialist partners the ENGINE auto-loads as bonus cards (one hop)
    required_context: tuple[str, ...] = () # game-state keys to inject when this card is selected
    version: int = 1               # bumped on any schema/balance change; spellbook cache keys on it
```

Deliberately *not* a field each: `default_limits`, `balance_rules`, and `failure_modes`
live **inside** `prompt_block` (one prose block the model reads), not as separate
structured fields — keeping cards lean and the model-facing text in one place is more
valuable here than machine-readable balance metadata we wouldn't consume programmatically.
The three fields we *did* add earn their place because code uses them: `common_combos`
drives routing expansion, `required_context` drives state injection (§5.5), and `version`
drives cache invalidation (§8).

A card bundles everything a capability needs to be *understood and priced and
exemplified* — and nothing it doesn't.

### 5.2 The always-on core

`SYSTEM_PROMPT` splits into:

- **Core (always sent):** the contract shape, `outcome_text` voice, the
  severity→magnitude ladder, costs catalog, and the *common* effects that appear in most
  casts (`damage`, `area_damage`, `add_status`, `create_tiles`, `heal`, `summon`). This
  is the graceful-degradation floor: even a total routing miss still resolves a
  recognizable spell.
- **Capability index (always sent):** one line per card —
  `memory_edit — alter, plant, or erase what an NPC remembers`. A dozen capabilities cost
  ~a dozen lines, and the index never grows the *body*. This is the "menu"; the model
  reads it for free and, if a card was wrongly not loaded, can signal via the escape
  hatch (§10).

The base stops growing as capabilities are added. Only the index grows, one line at a time.

### 5.3 The selector (tiered, non-generative)

In `wildmagic/capabilities.py`, `select_cards(spell_text, context) -> list[CapabilityCard]`:

- **Tier 1 — keyword/trigger match (free, deterministic, first).** Scan the lowercased
  spell text against each card's `triggers`. This is `scan_for_trade_intent`'s pattern.
  For a roguelike where players type intent-laden words ("make him *forget* he saw me",
  "*wall* of fire"), this alone covers most casts. It is trivially **unit-testable**
  ("spell X selects cards {A, B}"), which suits the project's test culture.
- **Tier 2 — embedding fallback (~tens of ms, only when tier 1 is thin).** When keyword
  matching selects nothing beyond core (or below a confidence bar), embed the spell text
  with a small local model and cosine-match against the pre-embedded `embed_description`s;
  take top-k. Catches paraphrase ("convince the guard he never had a brother" → memory
  card with no literal trigger word). Model: **`nomic-embed-text`** (768-dim, ~274 MB,
  Matryoshka-truncatable) — the
  [recommended default for local semantic routing](https://www.morphllm.com/ollama-embedding-models):
  small, fast, low-latency. Run it on a **CPU embedding route** (`num_gpu=0`, like the
  existing BACKGROUND/lore models) so it never evicts the 9B from the A750's 8 GB.
  Card `embed_description`s are embedded once at startup and cached.
- **Combo expansion.** After tiers 1–2, the **engine** unions in the `common_combos` of
  every selected card as fully-loaded bonus cards — **one hop, never transitive (no
  chaining)** — so memory-edit drags in social/reputation, weather drags in terrain + area
  hazards. The bonus cards are *loaded*, not merely mentioned: under the dynamic schema
  (§5.4) an unloaded card's effects are un-emittable, so telling the model about a card it
  doesn't have is pointless. This catches frequent pairings the spell *text* never names
  ("make him forget me" has social fallout it doesn't mention).

  Two design choices settle the obvious failure modes, leaving one knob empirical:
  - **Universal primitives go in CORE, not in `common_combos`.** Anything that combines
    with *everything* (AoE templates, plain damage, status, tiles) is already always-on
    (§5.2), so it can never be "missed by routing." `common_combos` is only for
    *specialist→specialist* pairings, which keeps expansion from degenerating into "send
    every card."
  - **No chaining.** One hop is bounded and explainable; transitive expansion balloons
    unpredictably. A bonus card does not pull in *its* combos.
  - **Empirical knob:** whether one-hop expansion earns its keep at all (vs. recall-biased
    routing + core already pulling everything) is data-decidable, not guessable. Gate it
    behind a flag, default **on but conservative** (well-established pairings only), and let
    the shadow-mode logs decide per card: log whether each combo-added card was actually
    *used* in the emitted effects. Rarely used → drop that pairing; frequent
    routed-miss-then-needed → keep or widen it (§10 mismatch detection is the same instrument).
- **Always:** union with core, then apply a **dynamic, recall-biased cap** (below).

**Routing is recall-biased on purpose.** In a game, *under*-selection (missing the one
card that makes a spell work) is far worse than *over*-selection (a few extra schema lines
the model ignores). A dropped `memory_edit` turns "make the baron remember being raised by
wolves" into a generic buff; a surplus card just costs tokens. So, mirroring
`scan_for_trade_intent`'s deliberate false-positive bias, the selector errs toward
**including** cards, and the cap is dynamic rather than a hard 3:

```
base cap = 5 on strong keyword hits, 3 on embedding-only matches
+1–2 if the spell text contains compositional connectives ("and", "while",
       "then", "but also", "except", "into")  → these signal multi-mechanic intent
hard ceiling ~7 to bound worst-case prompt growth
```

The numbers are starting points to calibrate from the audit (§13). The point is the
*direction*: when in doubt, load it.

Tier 2 is optional and *measured into existence*: ship tier 1 first, add embeddings only
if the audit log shows keyword routing missing real paraphrases (§11).

### 5.4 Assembly — prompt and schema together

`_wild_prompt_messages` (`wild_magic.py:61`) already composes the system prompt from
`SYSTEM_PROMPT + region_prompt_block + caster_prompt_block`. Add one more term:

```
system = CORE_PROMPT + capability_index + region_block + caster_block
       + "".join(card.prompt_block + card.examples for card in selected)
```

The **key synergy with `EXECUTION_PLAN.md` Phase 8 §4** ("Dynamic schema tightening"):
the same `selected` set drives a per-cast `SPELL_RESPONSE_JSON_SCHEMA`. The effect-type
enum is no longer the full static `sorted(SUPPORTED_EFFECTS)` — it is
`core_effects + selected cards' effect_types`. Passed as Ollama's `format`, grammar
enforcement then makes a capability the model wasn't given **structurally impossible to
emit**. Routing decides what the model *sees*; the dynamic grammar decides what it can
*say*. Together they make "the fireball prompt accidentally triggers a half-remembered
memory-edit effect" not a probability to tune but a state the decoder cannot enter.

This is strictly cheaper than today on the latency lever Phase 8 §6 named: most casts
send a *shorter* prompt than the current monolith, not a longer one.

Two guardrails so the narrowed schema doesn't become a trap:

- **`needs_capability` enumerates ALL card names, not just the selected ones.** It is the
  one schema field deliberately *not* narrowed — the model must be able to name a
  capability it can see in the index but wasn't handed (§10). Narrowing it to the selected
  set would defeat its purpose.
- **The narrowed schema does not replace `validate_resolution`.** Grammar enforcement
  guarantees *shape*, never *sense* — a schema-valid `{"area_damage","radius":999}` or a
  hallucinated target id still flows through the existing
  `_normalize_resolution → validate_resolution → engine clamp` pipeline unchanged (§5.0).
  Schema tightening is a reliability *assist*, not the validator.

### 5.5 Card-driven game-state retrieval (the other half of "retrieval")

Routing retrieves the right *mechanics*. The symmetric, equally important move is
retrieving the right *game state*. Today the resolver context is assembled the same way
for every spell. But a memory-edit spell needs the target NPC's remembered-facts summary;
a weather spell needs region/weather tags; a structure spell needs nearby structures — and
a fireball needs none of those. Sending all of it every time is exactly the per-cast
context bloat `EXECUTION_PLAN.md` Phase 8 §6 flags as the main latency lever.

So make state injection **card-driven**, using each card's `required_context`:

```python
selected = select_cards(spell_text, context)
packet   = build_context_packet(selected, live_game_state)  # union of cards' required_context
messages = assemble(core, capability_index, selected, packet)
```

`required_context` keys map to small extractors over live state — e.g.
`memory_edit → {"target_memories": npc_memory_summary(nearest_npc)}`,
`weather → {"region": region_tags}`, `structures → {"nearby_structures": ...}`. The base
packet (caster stats, visible targets with ids/tags, nearby tiles, `spell_anchors`) stays
as it is; cards only *add* their specialized slices when selected.

This also subsumes the §9 "live state provider" idea generally: most state is read once
*before* the call and injected (no round-trip), which is why MCP stays unnecessary. A
capability that truly needs a mid-resolution lookup is the rare exception, not the model.
It dovetails with Phase 8 §4's other half too — the dynamic schema can pull its `target`
enum from the same packet's real entity ids, so the model can't aim at a nonexistent foe.

## 6. Worked example — the user's case: NPC memory editing

```python
MEMORY_EDIT = CapabilityCard(
    name="memory_edit",
    triggers=("remember", "forget", "memory", "memories", "recall",
              "mind", "convince", "erase", "implant", "amnesia", "recollect"),
    embed_description=(
        "Spells that change what a person knows or remembers: planting a false "
        "memory, erasing an event, making an NPC forget the caster, rewriting a grudge."
    ),
    index_line="memory_edit — alter, plant, or erase what an NPC remembers or knows",
    effect_types=("edit_memory",),  # new SUPPORTED_EFFECTS key + engine handler
    prompt_block=(
        "edit_memory: target (npc id), op ('add'|'remove'|'alter'), subject, "
        "text (the new/removed memory in the NPC's frame), strength 1-5. Use to "
        "make an NPC forget, misremember, or newly believe something. Memory edits "
        "are major: they bend a mind. Pair with a real cost (a curse, max-resource "
        "loss) and never let one be a free win-button against a quest gate."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "The guard\'s eyes '
        'go soft; the face he was hunting slides out of his memory like a name off wet '
        'ink.", "effects": [{"type": "edit_memory", "target": "guard_7", "op": "remove", '
        '"subject": "the caster", "text": "He never saw you here.", "strength": 4}], '
        '"costs": [{"type": "curse", "id": "borrowed_forgetting", "name": "Borrowed '
        'Forgetting", "description": "What you took from him, you owe."}], '
        '"rejected_reason": null}',
    ),
    cost_hint="major+; always a curse or max-resource cost",
)
```

This is the whole footprint of a new capability: one card, plus the `edit_memory` engine
handler and its `SUPPORTED_EFFECTS`/validator entry. A fireball cast never sees any of it;
"make the captain forget my face" loads it via the `forget`/`memory` triggers, the schema
enum gains `edit_memory` for that one call, and the model can express it.

The same shape covers every future capability (weather, time, reputation, structures):
write a card, write the handler, register both. The prompt and schema stay lean by
construction.

## 7. Why not MCP (for now)

MCP standardizes an agent *calling out* to external/third-party tool servers in a
multi-turn loop. Three reasons it is the wrong tool for the resolver path:

- **No round-trip needed.** Our capabilities are emitted-JSON effects the engine already
  executes, not services to call and await. MCP's request/response loop buys nothing here.
- **Latency.** Native tool-calling (which Qwen3.5 does support well — see
  [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent), Hermes-style templates) is
  multi-turn by nature. On this backend that is the doubled-latency trap from §2.1. Our
  one-shot "emit all effects at once" is already the right collapse of tool-calling for a
  slow local model.
- **No interop requirement.** MCP's real payoff is third-party/cross-vendor tools. We own
  every effect handler.

**When MCP (or just a plain function) would earn its place:** a capability that must
*read live game state during resolution* — e.g. memory-edit that needs the NPC's *current*
remembered facts to choose which to alter by content. That is a genuine request→response.
But we control both ends, so a plain in-process **state-provider function** (inject the
NPC's memory list into `context` before the call, or expose a typed lookup) is simpler,
faster, and testable. Reach for MCP only if/when we want to expose these to *external*
agents — not for internal resolution.

## 8. How this composes with the roadmap

- **Phase 8 §4 (dynamic schema tightening):** capability cards *are* the natural source of
  the per-cast effect-type enum. Build them together; the card registry feeds both the
  prompt assembler and the schema builder.
- **Phase 8 §6 (trim per-cast context):** cards directly attack the named waste — the
  static `supported_effects` dump and tile legend stop being sent wholesale; each cast
  carries only its selected cards' fragments.
- **Phase 9 (spellbook / discovery engine):** routing is a *discovery-time* cost only. A
  learned spell records which cards/effects it resolved to; recasts are deterministic and
  skip the resolver (and the router) entirely. Capability growth therefore never touches
  the hot path. **Two things the cache entry must store to stay correct as cards evolve:**
  - **Versions, for invalidation.** Record `card_versions` (the `version` of each card used)
    and the `schema_version`. When a card's balance/schema changes (its `version` bumps), a
    stale cached resolution is re-derived instead of silently replaying old mechanics.
  - **Recipe, not a frozen object, for context-sensitive spells.** "Lesser Firebolt" can
    cache a fixed effect. But "make the nearest enemy forget why they came" must cache a
    *recipe* — cards + an effect template with `requires_retargeting` / `requires_repricing`
    flags — so a recast re-resolves the target id and re-applies costs against current
    state, rather than editing the wrong NPC with a stale id. (Detailed design belongs in
    the Phase 9 doc; flagged here so the cache schema is built for it from the start.)

## 9. Risks and mitigations

- **Routing miss (a needed card isn't loaded) → silent capability loss.** The model can't
  emit an effect whose schema it never saw (and, with dynamic enums, literally can't).
  Mitigations: (a) keep the **always-on core broad** so a miss degrades to a plausible
  generic resolution, not a failure; (b) **log selected-cards** into
  `wild_magic_audit.jsonl` next to the prompt we already store; (c) the **escape hatch**
  below.
- **Over-selection (too many triggers fire) → mild prompt bloat.** Deliberately the
  *lesser* evil (§5.3): a surplus card costs a few tokens; a missing one breaks the spell.
  Bound it with the dynamic ceiling (~7) and ranking (keyword-hit count, then embedding
  score), but bias toward inclusion, not exclusion.
- **Trigger drift / maintenance.** Triggers are data on the card and unit-tested; adding a
  synonym is a one-line change with a test, not prompt surgery.
- **Embedding model adds a moving part.** It's optional (tier 2) and CPU-pinned; if it's
  down, tier 1 + core still resolve every spell.

## 10. The escape hatch (cheap, self-reporting) — but don't lean on it alone

Add an optional `needs_capability` field to the resolution schema, **enumerated over ALL
known card names** (not just the selected set — see §5.4). The always-on index tells the
model: *if the right tool for this spell isn't in your loaded set, name what you needed
here.* On a miss the model still returns a best-effort resolution **and** flags the gap;
a recurring `needs_capability: "memory_edit"` is a precise, zero-guesswork signal to widen
triggers or add a card.

**Caveat (don't over-trust a 9B's self-report).** A small local model will not reliably
notice that a capability is missing once you've hidden its schema — so `needs_capability`
is a *bonus* signal, not the primary safety net. The primary net is recall-biased routing
(§5.3) plus **offline mismatch detection**: log selected-cards alongside the actual emitted
effects, and flag casts where the resolution leaned on the always-on core in a way that
suggests a specialist card *should* have fired (e.g. the spell text hit a card's
`embed_description` strongly but that card wasn't selected). That catches misses the model
never self-reports.

## 11. Migration plan (each step independently shippable)

Ordered so the **observability and safety nets exist before gating turns on** — otherwise
a routing miss is hard to tell from a model error.

**Done (2026-06-13).** The carve shipped faster than the cautious order below — the
post-generation safety net (`_normalize_resolution → validate_resolution → engine clamp`)
already existed and is strong, so routing went straight to being the **sole path** rather
than a shadowed flag:

1. ✅ Carved `CORE_PROMPT` + the `CapabilityCard` registry + `select_cards` router.
2. ✅ Built `assemble_resolver_system_prompt` and wired it into `_wild_prompt_messages`.
3. ✅ **Removed** the monolithic `SYSTEM_PROMPT` and the flag/dual-path lane — routing is
   now the only resolver-prompt path. A coverage test guards that core + cards cover every
   `SUPPORTED_EFFECTS`.
4. ✅ Promoted three capabilities end to end (`possession`, `memory_edit`,
   `structure_animation`) — proof that adding a capability touches only the registry +
   engine, not a core prompt. Validated by live CLI playtests + the overnight harness.

**Remaining:**

5. **Dynamic schema enums + `needs_capability` (global enum).** Feed `selected_effect_types`
   into a per-cast `SPELL_RESPONSE_JSON_SCHEMA`; build it in shadow mode against the offline
   audit (`speleval --from-audit`) before enforcing.
6. **Embedding routing (tier 2) — only if measured necessary.** If the selected-cards logs
   (§10) show keyword misses on real paraphrases, add the `nomic-embed-text` CPU route.
7. **Card-driven state packets (§5.5) and any live state provider** for capabilities that
   need current NPC memory etc. MCP stays deferred throughout.

## 12. Testing

- **Router unit tests** (`tests/test_capability_routing.py`): table of `spell → expected
  card set`, including the **negatives** (a plain fireball must *not* load `memory_edit`)
  and the paraphrase cases once tier 2 lands.
- **Multi-card composition tests:** compositional spells select the full set — "a wall of
  fire that makes them forget I was here" → `{terrain_shape, area_damage(core), memory_edit}`.
  These guard the recall bias and combo expansion (§5.3).
- **Assembly test:** selected cards → the effect enum in the per-cast schema contains
  exactly core + their effect types, and nothing else; `needs_capability` enum stays global.
- **Refactor guard:** step 1's "all cards loaded" assembly equals the legacy prompt.
- **Shadow all-cards comparison:** routed/narrowed output vs full-context output on the
  golden corpus — quantifies what gating changes before it's enforced (migration step 4).
- **Validation fuzzing:** weird model outputs (illegal magnitudes, missing/hallucinated
  targets, schema-valid nonsense) still get clamped/rejected, never crash or consume a turn.
- **Balance-histogram drift:** severity/cost-type distribution over the corpus must not
  drift when cards or routing change — the spell economy stays where the design wants it.
- **Cache invalidation:** a card `version` bump (or `schema_version` change) invalidates the
  matching spellbook entries and forces re-derivation; context-sensitive recipes retarget.

**Metrics to log per cast** (so we measure *fun and reliability*, not just latency):
`tokens_in`, `decode_time`, `selected_cards`, `needs_capability`, `schema_validation_pass`,
`semantic_validation_pass`, `engine_clamps_applied`, `accepted`/`rejected`, `severity`,
`cost_type`, and — once the spellbook lands — the **player recast/use rate** per discovered
spell (the strongest signal a resolution was actually good).

## 13. Open decisions (for review, not blocking)

- **Core/card boundary:** which effects are "common enough" to live in the always-on core
  vs. become cards. Starting proposal in §5.2; tune from the audit's effect-type
  histogram.
- **Recall-biased cap tuning:** the dynamic cap (§5.3, base 5/3, +1–2 on connectives,
  ceiling ~7) is a starting point — calibrate the numbers against real multi-intent spells
  and the over-vs-under-selection rates in the logs. The *direction* (bias to include) is
  settled; the constants are not.
- **Tier-2 trigger threshold:** when does "keyword routing was thin" fire the embedding
  pass? Start: only when tier 1 adds zero cards.
- **Whether to gate at all initially:** we could ship steps 1 + 3 (refactor + dynamic
  enums, all cards loaded) and measure the latency/fidelity baseline *before* turning on
  gating, to quantify the win.

---

### One-line summary

Build a **retrieval-assisted semantic adapter in front of a deterministic engine
validator**: split the monolith prompt into **capability cards**, **recall-biased**-select
only the cards (and the card-driven game state) a cast needs — cheap keyword scan now,
optional local embeddings later — and feed the same selection into a **per-cast
JSON-schema enum** so unselected capabilities are un-emittable, while every output still
flows through the existing normalize→validate→clamp pipeline (the engine, not the schema,
is the source of truth). Treat spells as *compositions* of engine primitives, keep
`needs_capability` global, version the spellbook cache, and stand up validation + logging
+ shadow-mode *before* enabling gating. Reuses the `scan_for_trade_intent` pattern, unifies
`EXECUTION_PLAN.md` Phase 8 §4/§6, defers MCP, and — since routing is a discovery-time cost
only (Phase 9) — lets capabilities grow without taxing the hot recast path.
