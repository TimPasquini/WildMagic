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
   is slow. Any design that adds a *second LLM generation* on the critical path (model
   picks tools → model resolves) roughly doubles cast latency. **Disqualifying.** The
   selector must be non-generative (keyword match, embedding lookup) — cheap relative to
   a 9B decode.
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
    prompt_block: str              # schema fragment + balance rules, injected when selected
    examples: tuple[str, ...]      # 1-2 few-shot JSON examples, injected when selected
    cost_hint: str = ""            # optional: how this capability should be priced
```

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
- **Always:** union with core; **cap the selected set** (e.g. ≤ 3 cards) to bound prompt
  growth even if many triggers fire.

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
  the hot path.

## 9. Risks and mitigations

- **Routing miss (a needed card isn't loaded) → silent capability loss.** The model can't
  emit an effect whose schema it never saw (and, with dynamic enums, literally can't).
  Mitigations: (a) keep the **always-on core broad** so a miss degrades to a plausible
  generic resolution, not a failure; (b) **log selected-cards** into
  `wild_magic_audit.jsonl` next to the prompt we already store; (c) the **escape hatch**
  below.
- **Over-selection (too many triggers fire) → prompt bloat returns.** Cap the selected set
  and rank (keyword-hit count, then embedding score).
- **Trigger drift / maintenance.** Triggers are data on the card and unit-tested; adding a
  synonym is a one-line change with a test, not prompt surgery.
- **Embedding model adds a moving part.** It's optional (tier 2) and CPU-pinned; if it's
  down, tier 1 + core still resolve every spell.

## 10. The escape hatch (cheap, self-reporting)

Add an optional `needs_capability` string field to the resolution schema. The always-on
index tells the model: *if the right tool for this spell isn't in your loaded set, name
what you needed here.* On a miss the model still returns a best-effort resolution **and**
flags the gap. The engine logs it; a recurring `needs_capability: "memory"` is a precise,
zero-guesswork signal to widen `memory_edit`'s triggers or add a card. This converts
routing misses from silent failures into a tuning feed.

## 11. Migration plan (each step independently shippable)

1. **Pure refactor — no behavior change.** Carve `SYSTEM_PROMPT` into `CORE_PROMPT` + a
   set of `CapabilityCard`s, with **all cards always loaded**. Assert the assembled prompt
   is byte-identical to today's (or audit output is unchanged). This is the risky-looking
   step made safe: it's just reorganizing the existing string.
2. **Keyword routing (tier 1).** Add `select_cards` (keyword only) + the always-on index;
   gate cards by trigger. Re-run the playtest scripts and the offline audit re-parse
   (`speleval --from-audit`, EXECUTION_PLAN Phase 8) and confirm fidelity holds. Add the
   `needs_capability` field and start logging selected-cards.
3. **Dynamic schema enums.** Feed the selected set into `SPELL_RESPONSE_JSON_SCHEMA`'s
   effect enum per cast (Phase 8 §4 lands here, scoped to effect types first).
4. **First *new* capability via the system:** ship `memory_edit` as a card + handler —
   proof that adding a capability touches only the registry and the engine, not the core
   prompt.
5. **Embedding routing (tier 2) — only if measured necessary.** If selected-cards logs
   show keyword misses on real paraphrases, add the `nomic-embed-text` CPU route and the
   cosine fallback.
6. **Live state provider — only for capabilities that need it.** Plain in-process function
   feeding current NPC memory (or similar) into `context`. MCP stays deferred.

## 12. Testing

- **Router unit tests** (`tests/test_capability_routing.py`): table of `spell → expected
  card set`, including the negatives (a fireball must *not* load `memory_edit`) and the
  paraphrase cases once tier 2 lands.
- **Assembly test:** selected cards → the effect enum in the per-cast schema contains
  exactly core + their effect types, and nothing else.
- **Refactor guard:** step 1's "all cards loaded" assembly equals the legacy prompt.
- **Offline regression:** `speleval --from-audit` over historical casts under the new
  assembler — selection must not regress resolution/rejection rates on the existing corpus.

## 13. Open decisions (for review, not blocking)

- **Core/card boundary:** which effects are "common enough" to live in the always-on core
  vs. become cards. Starting proposal in §5.2; tune from the audit's effect-type
  histogram.
- **Selected-set cap:** 3 is a guess; calibrate against real multi-intent spells ("a wall
  of fire *and* make them forget I was here").
- **Tier-2 trigger threshold:** when does "keyword routing was thin" fire the embedding
  pass? Start: only when tier 1 adds zero cards.
- **Whether to gate at all initially:** we could ship steps 1 + 3 (refactor + dynamic
  enums, all cards loaded) and measure the latency/fidelity baseline *before* turning on
  gating, to quantify the win.

---

### One-line summary

Split the monolith prompt into **capability cards**, show the resolver only the cards a
cast's text selects (cheap keyword scan now, optional local embeddings later), and feed
the same selection into a **per-cast JSON-schema enum** so unselected capabilities are
un-emittable — reusing the `scan_for_trade_intent` gating pattern and unifying
`EXECUTION_PLAN.md` Phase 8 §4/§6. Defer MCP until something needs live external
tool-calls. Routing is a discovery-time cost only (Phase 9), so capabilities can grow
without ever taxing the hot recast path.
