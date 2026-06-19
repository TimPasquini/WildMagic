# Lore Cards — Tiered World-Knowledge for Dialogue, Books, and Beyond

Status: **M1–M3 implemented; file-backed registry live (2026-06-18)** —
`content/lore/*.md` is the authored source of truth, `wildmagic/file_lore_cards.py` parses
it, `wildmagic/lore_cards.py` adapts those sections into the gate + selection API, and
`wildmagic/lore_router.py` provides optional provider wiring. `NPCProfile.lore`, generation
seeding, dialogue `world_knowledge` injection, and book THREADS injection remain on the
same public path. Deeper L2–L4 tiers + audit-log tuning are still deferred. See §13 for what
shipped vs. what's deferred. Companion to
`WORLDBUILDING.md` (the
world fact this system serves), `CAPABILITY_ROUTING.md` + `CAPABILITY_CARD_PLAN.md` (the
routing pattern this mirrors), and `WORLD_PROMISES.md` (the *dynamic* lore system this sits
beside — see §4). Latency basis recorded in the `lore_router_latency` memory.

---

## 1. Goals

Make the authored world canon in `WORLDBUILDING.md` **accessible to the game at the right
depth, to the right speaker, on the right topic** — without stuffing the whole world into
every prompt.

- **Right depth.** A Monteary stablehand can tell you horses are bred here (everyone knows
  that); only a Conservatory philosopher can explain Stalnazan Investiture politics. Knowledge
  is *tiered*, and a speaker only reaches as deep as they actually know.
- **Right topic.** A Stalnazan conversation should never be carrying merfolk trivia. Only the
  cards relevant to *this* exchange get injected.
- **Right cost.** The mechanism must be cheap enough to run inline (dialogue is on the
  player's critical path). The latency probe (§5) shows a schema-constrained generative router
  on the **already-resident** model is ~1s warm — affordable.
- **One growing registry, many consumers.** Dialogue and book generation are the v1
  consumers; the same registry should later serve examine/read flavor, rumor seeding, quest
  text, the (deferred) player codex, and anything else that wants grounded world fact.
- **Authored now, extensible later.** Levels 0–1 are carved by hand into `content/lore/*.md`;
  levels 2–4 should be added only when there is real canon to freeze.

## 2. The model (tiers, tags, access)

A **lore tag** is a subject the world tracks knowledge about. v1 tags are **regions and
traditions**:

- *Regions/peoples:* `vigovia` (the imperial **heartland** specifically, *not* the whole
  Empire — the distinction `WORLDBUILDING.md` draws), `stalnaz`, `brall`, `ryolan`, `vint`,
  `threen`, `monteary`, `ontria`, `gontark`, `parn`, `birdfolk`, `merfolk`, `rentacosta`.
- *Cross-cutting topics:* `crystal`, `bone`, `blood`, `woven`, `sound`, `curse`, plus the
  imperial topics `empire` (the whole construction, vs `vigovia` the heartland), `charter`, and
  `shadow_purge`, and a `magic` topic for the metaphysics of how magic works. These cut across
  regions.

(The system is tag-agnostic; "eventually many more things than regions" — factions, persons,
items — become tags by adding cards, no schema change.)

A **knower** (an NPC, a book) has a sparse `lore: dict[tag -> level]`. Anything absent is **0**.
Levels follow the 0–4 scale:

| Level | Meaning |
|---|---|
| 0 | common knowledge — *everyone* has it on *every* tag. **Keep L0 lightweight and folksy:** the one-line reputation a tavern traveler would repeat ("Monteary breeds the best horses"), never anything scholarly, technical, or metaphysical (those start at L1+). |
| 1 | basic familiarity |
| 2 | educated or experienced |
| 3 | detailed study / very familiar (a local guide, a courtier) |
| 4 | expert scholar |

A **lore card** is one fact block, gated by a **threshold** over one or more tags. **Access =
sum of the knower's levels across the card's tags ≥ the card's threshold** (pure sum, any
distribution). For single-tag cards the threshold *is* the 0–4 tier. Multi-tag cards can carry
higher thresholds (`(threen, crystal) 6`).

Pure sum is the v1 rule by design decision. Its known weakness: a `stalnaz 4 / charter 0`
speaker clears a `(stalnaz, charter) 4` card and may then talk confidently about something they
only half-know. The mitigation — optional per-tag minimums (`required={"stalnaz":2,"charter":1}`)
— is **deferred** (§15). Until then, **author multi-tag cards conservatively:** don't gate a claim
on a tag a likely reader could be ignorant of, and let the dominant tag carry most of the threshold.

Access is **cumulative**: a `stalnaz 3` knower clears the `stalnaz` cards at thresholds 0/1/2/3.
By authoring convention L0 cards are one sentence, L1 short, deeper tiers longer — so sending a
knower's whole eligible-and-relevant stack stays cheap.

## 3. Strategy — two filters, then inject

Every consumer runs the same pipeline. **Both filters must pass.**

```
            knower.lore                     the exchange
                │                                │
         ┌──────▼───────┐                 ┌──────▼────────┐
         │ ACCESS GATE  │                 │  RELEVANCE    │
         │ sum(tags) ≥  │                 │  routing      │
         │ threshold    │                 │ (topic/region)│
         └──────┬───────┘                 └──────┬────────┘
                └───────────────┬────────────────┘
                          eligible ∩ relevant
                                │ (cap ~5, recall-biased)
                          inject card.text
                                │
                   dialogue context / book THREADS slot
```

1. **Access gate** (pure function, no LLM): keep cards the knower can reach.
2. **Relevance routing**: pick the on-topic few. *This is the only place an LLM is involved,
   and it is optional.*
   - **Cheap keyword prefilter first** (the `scan_for_trade_intent` pattern): match the query
     (player's message; or a book's `subjects`) against eligible cards' triggers/tags. This
     both narrows the candidate set and **decides whether to spend a router call at all** — a
     "hello"/"goodbye" line that hits nothing skips the LLM entirely.
   - **If the surviving candidate set is small, just send it** — eligible L0/L1 cards are short
     and the gate already pruned hard. The router only earns its keep when many tags are in
     play at once.
   - **Otherwise, the generative router**: a schema-constrained call (returns `{"cards":[...]}`)
     on the consumer's own resident model picks the relevant names. Recall-biased; capped ~5.
   - **Deterministic fallback** (mock provider / offline / router error): keyword-rank the
     eligible∩hinted set and take top-k. The system never *needs* a backend to produce a
     sensible result.
3. **Inject** the selected cards' `text` into the consuming prompt's world-knowledge slot.

Why a gate *and* a router: L0 cards are eligible for *every* tag (everyone has 0 ≥ 0), so the
eligible set always spans the whole world. Relevance routing is what keeps a Stalnaz chat from
hauling in merfolk and Brall trivia. The gate controls *depth per subject*; routing controls
*which subjects*.

### 3.1 Reuse the consumer's model (no second runner, no thrash)

**The router runs on the same model as the call it feeds, resolved through the same config.**
Concretely it inherits the consumer purpose's **load-time** options so it shares the
already-resident Ollama runner with **zero reload**, and overrides only **request-time**
options:

| Option | Source | Why |
|---|---|---|
| model, host, `num_ctx`, `num_gpu`, `keep_alive` | **inherited** from the consumer purpose (`dialogue`, `canon`, background) | load-time — changing any of these evicts/reloads the model (the 8GB A750 can't co-resident stheno+qwen; a mismatched router would thrash ~10s) |
| `num_predict` | **override** → small (~96) | router output is a tiny array |
| `temperature` | **override** → low (~0.1) | routing wants determinism, not the 0.7 dialogue heat |
| `format` | **override** → the cards JSON-schema | forces a stop after the array (the §5 lever) |
| `think` | **override** → false | no chain-of-thought |

So the dialogue router is `get_dialogue_model()` on `ollama_host("dialogue")` with the
dialogue route's ctx/gpu; the book router rides whichever purpose generates that book
(`canon` on-demand, or the background canon model for prewarm). No new model variables.

## 4. Relationship to the other "lore" systems (don't conflate)

- **Lore cards = static authored canon.** What Stalnaz *is*. Hand-written, run-invariant,
  tiered by who-knows-it. Lives in a registry like `capabilities.py`.
- **The Promise Ledger / `lore.py` = dynamic run truth.** What happened *this run* — rumors,
  "a chapel north of town", quests. Extracted, bound, realized; obeys always-honor. See
  `WORLD_PROMISES.md`.

They are complementary and both feed dialogue: a Stalnazan NPC can cite **lore-card** canon
about the Queendom *and* a **promise-derived** rumor about the chapel two zones north. They
occupy **separate context slots** (existing `relevant_lore` for promise/claim material; a new
`world_knowledge` slot for lore cards) and must not be merged.

### 4.1 Don't let static canon leak into the dynamic ledger

Injected card text is canon, not a fresh claim. The post-dialogue/post-read extraction in
`lore.py` (which mints `WorldPromise`s from what was said) must **not** re-ingest it — otherwise
reading a book would spawn "rumors" that are merely restated background canon, polluting the
ledger and the journal. Two guards: (1) keep card text in its own clearly-labeled
`world_knowledge` slot, separate from generated prose, so extraction can be pointed away from it;
(2) when extraction runs over text that drew on cards, drop/deprioritize claims that merely
restate a card used this turn (a cheap overlap check against the turn's selected cards). Static
canon shapes the dynamic ledger's *voice*; it never becomes a new entry in it.

- **Capability cards = the resolver's mechanics.** Same *routing pattern* (registry + index +
  recall-biased select), different axis: capability cards have no access gate (any spell may
  use any mechanic); lore cards add the gate (not every speaker knows everything). Lore-card
  routing reuses the capability-card lessons (keyword tier first, schema output, recall bias,
  unit-testable `query → expected set`).

## 5. Latency basis (measured 2026-06-17, A750, warm)

A faithful probe (long ~1,220-token NPC packet + 22-card index + player question → `{"cards":[]}`):

- **Prompt length is free.** 1220 tokens prefilled in **0.03s** (stable card-index prefix is
  prompt-cached; the GPU processes prompt ~100× faster than it generates). The candidate index
  can grow to hundreds of cards and stay <0.1s. The whole cost is the few emitted card-ids.
- **A strict JSON *schema* (not `format:"json"`) is the lever.** Free-json let the chatty
  dialogue finetune print the array then keep narrating to the token cap (96 tok / 2.8s). The
  schema makes it stop after the array.
- **Warm wall times, with schema:** qwen3.5:9b → ~0.85s (precise, 2 cards); stheno dialogue
  finetune → ~0.6–1.5s (recall-happy, ~7 cards). Either is fine inline; the dialogue finetune
  over-selects, which the ~5 cap bounds.

Conclusion: generative routing on the resident model is affordable; keyword/embedding routing
is **not needed for latency**, only as the cheap prefilter/gate and the offline fallback.

---

# Detailed implementation

## 6. Data model

`wildmagic/lore_cards.py` remains **pure data + pure functions**, no HTTP/provider logic
(mirrors `capabilities.py` / `spell_contract.py`). The live registry is loaded from
`content/lore/*.md` through `wildmagic/file_lore_cards.py`.

```python
@dataclass(frozen=True)
class LoreCard:
    name: str                  # unique id, e.g. "stalnaz_succession"
    tags: tuple[str, ...]      # access keys (regions/traditions): ("stalnaz",) or ("threen","crystal")
    threshold: int             # combined lore across tags to ACCESS (single-tag: the 0-4 tier)
    triggers: tuple[str, ...]  # keywords for the cheap prefilter + deterministic fallback ranking
    index_line: str            # one-line gloss the router sees in the candidate menu
    text: str                  # the fact block injected into the consuming prompt
    version: int = 1           # bump on content change (future: cache invalidation for frozen book canon)
    topic: str = ""            # file-backed topic id, when loaded from content/lore
    level: int | None = None   # file-backed lore level, when loaded from content/lore
    source: str = ""           # source file path for file-backed cards

LORE_CARDS: tuple[LoreCard, ...] = (...)   # file-backed live registry
_BY_NAME: dict[str, LoreCard] = {c.name: c for c in LORE_CARDS}
```

A **knower** carries `lore: dict[str, int]`:

- **NPCs:** add `lore: dict[str, int] = field(default_factory=dict)` to `NPCProfile`
  (`models.py`). Absent ⇒ 0 everywhere. Saved/loaded like the rest of the profile.
- **Books:** a book has `subjects` metadata already (the book pipeline's router key). Give it a
  flat **author level** `N` (3–4 typical) and synthesize `lore = {s: N for s in subjects}`. A
  book is just another knower; no special path. (Authorship mechanics stay deferred — for now
  `N` is a generation parameter, default ~3.)
- **Player:** *no lore stats in v1* (deferred — see §13). The player receives whatever NPCs and
  books produce.

## 7. The access gate (pure)

```python
def knows(lore: Mapping[str, int], card: LoreCard) -> bool:
    return sum(lore.get(t, 0) for t in card.tags) >= card.threshold

def eligible_cards(lore: Mapping[str, int], registry=LORE_CARDS) -> list[LoreCard]:
    return [c for c in registry if knows(lore, c)]
```

Trivially unit-testable: default-0, pure-sum multi-tag, cumulative tiers.

## 8. Relevance routing

```python
LORE_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"cards": {"type": "array", "items": {"type": "string"}}},
    "required": ["cards"],
}

# Book subjects (and any free-text topic) are generated metadata, NOT guaranteed tags.
# Normalize onto the canonical vocabulary before matching; drop unknowns.
TAG_ALIASES: dict[str, str] = {
    "crystals": "crystal", "crystal magic": "crystal",
    "charter law": "charter", "charter magic": "charter",
    "the empire": "empire", "grand empire": "empire", "vigovian": "vigovia",
    "stalnazan": "stalnaz", "bralli": "brall", "ryolani": "ryolan", "vintan": "vint",
    # ... one entry per known demonym / plural / common phrasing
}
KNOWN_TAGS: frozenset[str] = frozenset(t for c in LORE_CARDS for t in c.tags)

def normalize_lore_tags(raw: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for s in raw:
        k = s.strip().lower()
        k = TAG_ALIASES.get(k, k)
        if k in KNOWN_TAGS:
            out.add(k)
    return out

def prefilter(cands: list[LoreCard], query: str, subjects: set[str]) -> list[LoreCard]:
    """Keyword/tag hits over eligible candidates. `subjects` are HARD, already-normalized
    topic tags (e.g. a book's subjects). Recall-biased — matches tags AND triggers."""
    q = f" {query.lower()} "
    hits = []
    for c in cands:
        if subjects & set(c.tags):                              # hard topic match (books)
            hits.append(c); continue
        if any(f" {t} " in q for t in c.triggers + c.tags):     # query keyword match
            hits.append(c)
    return hits

def build_router_messages(knower_blurb: str, query: str, cands: list[LoreCard]) -> list[dict]:
    index = "\n".join(f"- {c.name}: {c.index_line}" for c in cands)
    system = (
        "You are a retrieval router for a speaker's world-knowledge. Given who the speaker is "
        "and what is being discussed, choose which LORE CARDS (by id) are relevant to THIS "
        "exchange. Pick only what is on-topic — usually 1 to 5, fewer is better. "
        'Return ONLY {"cards": ["id", ...]}.\n\nAvailable lore cards:\n' + index
    )
    user = json.dumps({"speaker": knower_blurb, "topic": query}, ensure_ascii=True)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def select_lore_cards(
    lore: Mapping[str, int], query: str, *,
    subjects: Iterable[str] = (),      # HARD topic (book subjects): authoritative, drives selection
    bias_tags: Iterable[str] = (),     # SOFT speaker bias (NPC home region): ranking tiebreak only
    knower_blurb: str = "",
    route_call: Callable[[list[dict]], list[str] | None] | None = None,
    registry=LORE_CARDS, max_cards: int = 5, max_chars: int = 1200,
) -> list[LoreCard]:
    elig = eligible_cards(lore, registry)
    if not elig:
        return []
    subj = normalize_lore_tags(subjects)
    bias = normalize_lore_tags(bias_tags)
    hinted = prefilter(elig, query, subj)
    if not hinted:
        return []                       # no topical engagement → inject nothing (a bare "hello"),
                                        # whether or not a router is available. A soft bias_tag
                                        # ALONE never selects: local lore waits to be asked about.
    if route_call is None or len(hinted) <= max_cards:
        return _budget(_rank(hinted, query, subj, bias), max_cards, max_chars)
    names = route_call(build_router_messages(knower_blurb, query, hinted))
    if names is None:                   # router FAILED → deterministic fallback
        return _budget(_rank(hinted, query, subj, bias), max_cards, max_chars)
    chosen = [c for c in hinted if c.name in set(names)]   # ⊆ eligible∩hinted; unknown ids ignored
    return _budget(chosen, max_cards, max_chars)           # a successful empty [] is respected

def _budget(cards, max_cards, max_chars):
    out, used = [], 0
    for c in cards[:max_cards]:
        if out and used + len(c.text) > max_chars:
            break
        out.append(c); used += len(c.text)
    return out
```

`route_call` is **injected** — `lore_cards.py` never imports the provider — and returns the
selected ids **or `None` on failure**, kept distinct from a deliberate empty `[]`: a broken
router falls back to `_rank`, while a router that genuinely found nothing relevant injects
nothing. `_rank` orders by hit count, then `bias` overlap, then lower threshold (foundational
cards first, deeper detail fills the remaining room). `_budget` bounds the injection by **both** a
card count and a character budget — five L4 cards weigh far more than five L0 lines.

## 9. The router call wiring (config reuse)

A thin helper near the provider code (e.g. `llm_client.py` or a small `lore_router.py`) turns a
**purpose** into a `route_call`, honoring §3.1 — inherit load-time options, override
request-time:

```python
def make_lore_route_call(purpose: str) -> Callable[[list[dict]], list[str] | None]:
    # The CONSUMER passes the purpose it is itself about to generate under — never inferred from
    # the kind of work. Dialogue passes "dialogue"; the book pipeline passes "canon" for an
    # on-demand read or its background route for prewarm. The router thus always lands on the
    # model that is (or is about to be) resident for that work — §3.1.
    model = resolve_model(purpose)          # get_dialogue_model() / get_canon_model() / bg canon
    host  = ollama_host(purpose)
    ctx, gpu, keep = num_ctx(purpose), num_gpu(purpose), keep_alive(purpose)  # inherited (load-time)
    def call(messages: list[dict]) -> list[str] | None:
        payload = {
            "model": model, "messages": messages, "stream": False,
            "format": LORE_ROUTER_SCHEMA, "think": False, "keep_alive": keep,
            "options": {"temperature": 0.1, "num_predict": 96, "num_ctx": ctx, "num_gpu": gpu},
        }
        try:
            resp = _post_ollama_chat(host, payload, ollama_timeout_seconds(purpose))
            return list(json.loads(strip_thinking(resp["message"]["content"])).get("cards", []))
        except (OSError, KeyError, ValueError, TypeError):
            return None   # FAILURE — distinct from a successful empty []; caller's _rank fallback runs
    return call
```

Under the **mock provider / offline**, the consumer simply passes `route_call=None`, so selection
takes the deterministic `_rank` path with no server — matching the codebase's mock discipline and
keeping engine tests model-free.

## 10. Consumers

### 10.1 Dialogue (`dialogue.py`)
- The knower is the NPC: `select_lore_cards(npc.lore, query=player_message,
  bias_tags=(npc_home_region,), knower_blurb=f"{npc.role} of {region}",
  route_call=make_lore_route_call("dialogue"))`.
  The home region is a **soft bias** (a ranking tiebreak among already-relevant cards), **not** a
  hard subject — so a bare "hello" with no topical hit injects nothing, and the NPC's own
  backstory still carries the flavor. Local knowledge surfaces once the player actually asks.
- Inject the selected `card.text` into a **new** context field `world_knowledge` (distinct from
  the promise-fed `relevant_lore`, §4). The `DIALOGUE_SYSTEM_PROMPT` gains one line: *"world_knowledge
  holds canon you may draw on; speak it in character, never recite it verbatim."*
- The router runs **before** the reply call, on the same resident dialogue model — one extra
  ~1s step, gated by the prefilter so trivial lines skip it.

### 10.2 Books (the canon/book pipeline)
- Per the book pipeline (`book_content_pipeline` memory, "request C"): the router is keyed on
  the book's **title + subjects**, selecting cards fed into the book-writer prompt's **THREADS**
  slot. Knower = the book's synthesized author-lore (§6); query = title;
  `subjects = normalize_lore_tags(book.subjects)` — a **hard** topic, and since book subjects are
  generated metadata they *must* pass through the alias map (§8) to land on real tags.
- The consumer passes **its own** generation purpose to `make_lore_route_call`: `"canon"` for an
  on-demand `read`, the background route for prewarm — never a hardcoded "book". The router then
  shares whichever model that path uses.
- Books are background/non-blocking, so even CPU decode of the tiny router output is fine.

## 11. Seeding NPC lore at generation

Deterministic, seeded, rules-first (town/NPC generation). A small role×region table; no LLM
needed in v1:

| Role archetype | Lore seed |
|---|---|
| innkeeper / barkeep / local guide | home region 2–3 |
| scholar / philosopher / sage / priest | home region 3 + a relevant tradition 2–3 |
| noble / official | home region 2 (+ `charter`/politics 1–2) |
| merchant / traveller | 1 across a couple of visited regions |
| commoner / laborer / guard | home region 1 |
| foreigner | their origin region at the above, home region 0–1 |

Everyone keeps the implicit 0 everywhere. LLM-assigned nuance is a later option; the table is
the floor and is enough to make locals feel knowledgeable and outsiders shallow.

## 12. Feature flag, audit, testing

- **Flag:** `WILDMAGIC_LORE_CARDS_ENABLED` (default on; the test suite forces it off, like
  `WILDMAGIC_BOOK_TITLES`, so engine tests stay model-free).
- **Audit (planned, not yet built):** `logs/lore_router_audit.jsonl` — knower lore, query,
  eligible count, prefilter hits, router output, final selection. The instrument for tuning
  thresholds and triggers; lands with M4. (Dialogue records do carry the chosen card names in
  `dialogue_record["lore_cards"]` for now.)
- **Tests** (`tests/test_lore_cards.py`):
  - *Registry validation:* unique names; every tag ∈ `KNOWN_TAGS`; thresholds ≥ 0; non-empty
    `triggers` and `text`; `index_line` present. (Guards the carve as cards accumulate.)
  - *Gate:* default-0; pure-sum multi-tag (`threen 6,crystal 0` clears `(threen,crystal) 6`);
    cumulative tiers; a 0-lore knower gets exactly the L0 cards.
  - *Relevance (`route_call=None`):* table `knower+query → expected set`, **including negatives**
    (a Stalnaz chat must not load `merfolk`/`brall`); off-topic small talk → `[]` and **does not
    route**; a soft `bias_tag` alone never selects on a bare greeting.
  - *Router contract:* unknown ids in the reply are ignored; selection ⊆ eligible∩hinted; `None`
    (failure) → deterministic `_rank` fallback, but a successful `[]` → inject nothing; **both**
    `max_cards` and `max_chars` budgets respected.
  - *Tag normalization:* `normalize_lore_tags` maps `crystals`/`crystal magic`/`Stalnazan`/… onto
    canonical tags and drops unknowns.
  - *Separation:* injected `world_knowledge` is never fed to the promise/claim extractor (§4.1).

## 13. Build order

- ✅ **M1 — Registry + gate + L0/L1 carve.** `lore_cards.py` (`LoreCard`, ~24-card registry,
  `knows`, `eligible_cards`, `prefilter`, `_rank`, `_budget`, schema, message builder,
  `seed_npc_lore`, `select_lore_cards`). L0 (folksy) + L1 carved from `WORLDBUILDING.md`. Gate +
  deterministic selection fully tested, **no LLM**. Flag `WILDMAGIC_LORE_CARDS_ENABLED` (default
  on; trim suite forces off).
- ✅ **M2 — NPC knower + dialogue injection.** `NPCProfile.lore` (seeded in `engine.spawn_npc`
  via `seed_npc_lore`, replay-safe), the `world_knowledge` context slot + prompt bullet,
  `make_lore_route_call("dialogue")` + `dialogue_lore_cards` wired into `actions._talk` before
  the reply (mock provider ⇒ deterministic, no server). Live smoke confirmed: a Stalnazan
  philosopher gets the succession/crystal cards; an outsider only reaches L0.
- ✅ **M3 — Book consumer.** `book_lore_cards` injects selected card text into the canon
  book context's `threads["lore"]` (`actions._canon_context_for_book`), gated by the book's
  `subjects`. **Deviation from the original sketch:** books use the *deterministic* path
  (`route_call=None`), not a generative router — book subjects are authoritative HARD tags, so
  subject-matching is both better and keeps the canon pipeline replay-safe (no extra recorded
  LLM call). Upgrading books to the generative router (with explicit foreground/background
  purpose plumbing) remains available if wanted.
- ✅ **File-backed authoring.** `content/lore/*.md` now contains the live authored card
  source. `wildmagic/file_lore_cards.py` parses Markdown plus fenced TOML metadata, and
  `lore_cards.py` adapts sections into the existing `LoreCard` API. Existing dialogue/book
  consumers keep the same routing path.
- **M4 — L2–L4 + tuning (deferred per designer).** Fill deeper tiers (hand or
  LLM-drafted-then-frozen); add the `lore_router_audit.jsonl` log (§12 — **not yet built**) and
  tune thresholds/triggers/caps from it.

## 14. Initial carve map (L0/L1 from `WORLDBUILDING.md`)

One-line, **folksy** L0 (the reputation a traveler would repeat) + short L1 per realm/tradition.
Names illustrative. Note `empire` (the whole) vs `vigovia` (the heartland).

| Card | tags | thr | gist (from the doc) |
|---|---|---|---|
| `empire_basics` | empire | 0 | The Grand Empire of Vigovia, founded by Vigo the Lawgiver; licenses magic via the charter. |
| `empire_heartland` | vigovia | 1 | Stodgy, bureaucratic, beards-and-permits; charter-mage is a prestige profession; the emperor is of Vigo's line. |
| `charter_basics` | charter | 0 | Charter magic = the Empire's licensed, repeatable magic. |
| `charter_truth` | charter | 1 | It subsumed the old traditions — licensed some spells, outlawed the dangerous. |
| `shadow_purge` | shadow_purge | 1 | The body-jumping shadow-spirit catastrophe that justified the charter. |
| `stalnaz_basics` | stalnaz | 0 | A queendom famed for music, art, and philosophy. |
| `stalnaz_rule` | stalnaz | 1 | Ruled by a queen who names her heiress (often an unrelated worthy woman); founding regicide is a celebrated holiday. |
| `crystal_basics` | crystal | 0 | Stalnaz's signature tradition; stones that hold light. |
| `crystal_truth` | crystal | 1 | Crystals genuinely *store* magic — one hand charges, another channels. |
| `brall_basics` | brall | 0 | Holds of ale, tall tales, scrimshaw, bone magic; ruled by Bone Jarls. |
| `bone_basics` | bone | 0 | Brall's tradition; magic worked through bone (whale/beast — human bone is charter-taboo). |
| `ryolan_basics` | ryolan | 0 | A kingdom of honor, duels, chariot races, blood magic. |
| `blood_basics` | blood | 0 | Ryolan's tradition; the duel's palm-cut descends from it. |
| `vint_basics` | vint | 0 | A gossipy republic famed for woven charms; ever-shifting politics. |
| `woven_basics` | woven | 0 | Vint's tradition; charms embroidered into cloth (now bought from charter mages). |
| `threen_basics` | threen | 0 | A wealthy independent canal-kingdom that everyone knows answers to the emperor. |
| `monteary_basics` | monteary | 0 | The horse-realm; world's best geldings, stallions guarded. |
| `ontria_basics` | ontria | 0 | Yoghurt tribes whose ritual cultures grant clan-specific powers. |
| `gontark_basics` | gontark | 0 | The caprine goatfolk, feared for vicious curses. |
| `parn_basics` | parn | 0 | Tattooed nomadic desert caravans devoted to music. |
| `birdfolk_basics` | birdfolk | 0 | Sociable avian people who collect and carry true stories. |
| `merfolk_basics` | merfolk | 0 | Xenophobic ocean people who hold themselves superior to land-folk. |
| `rentacosta_basics` | rentacosta | 0 | A relaxed, multilingual free city of sailors that trades with the merfolk. |

**World-frame cards.** The **metaphysical** truth that "all traditions are dialects of one wild
substrate" is **not** common knowledge — it is `magic`-tag scholarship at **L2–L3**, what a mage
or philosopher knows, while ordinary folk simply believe the traditions are different magics. The
*folksy* L0 world-frame is the lived texture instead: the common coin and common tongue, the safe
imperial roads, and the peace people grudgingly enjoy (all `empire` 0).

## 15. Open / deferred

- **Per-tag access minimums** (`required={tag: min}` alongside the summed threshold) — deferred
  per designer; v1 is pure sum (§2). Revisit if the audit shows confident-but-wrong multi-tag
  speakers; until then, author multi-tag cards conservatively.
- **Player codex lore** (player accrues lore from reading/talking; gates a journal). Deferred
  per designer; the knower abstraction already fits the player when wanted.
- **Contradictory tiers** (L0 misconception corrected at L3). Not in v1; cards may *frame* a
  fact as "a common belief" but must not assert falsehoods. The two-filter design supports
  adding correcting tiers later without schema change.
- **LLM-authored L2–L4**, frozen as canon (reuse the book/flesh freeze pattern + `version`).
- **Book authorship mechanics** (who wrote it, bias, reliability) — for now a flat author level.
- **Embedding tier** for paraphrase, only if the audit shows the keyword prefilter missing real
  topic matches (the candidate set the router sees is already eligibility-pruned, so misses are
  unlikely to matter).
