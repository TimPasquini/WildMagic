# Gossip Graph Strategy

Status: strategy proposal. Companion to `EMERGENT_WORLD_STRATEGY.md`,
`SEMANTIC_EFFECTS.md`, and `LORE_CARDS.md`.

The gossip graph is the social transport layer for NPC knowledge. It answers a different
question from the faction ledger or the promise ledger:

> Who has heard what, from whom, and how much do they trust it?

The goal is not to make every NPC omniscient. The goal is a bounded, legible information
ecology where direct witnesses, eavesdroppers, friends, neighbors, coworkers, and faction
contacts carry different versions of the player's story through the world.

Initial scope is player-centered. The first version should track deeds, conversations, and
magic that involve the player or the player's authored consequences. Autonomous NPC-to-NPC
news generation can come later; the graph should not require a full offscreen social sim to
be useful.

This keeps the central Wild Magic rule intact: the LLM may word memories and rumors, but
the engine owns provenance, spread, confidence, caps, replay, and mechanical consequences.

---

## Design Goals

- **Provenance must be structural.** An overheard report is not a personal experience, and
  thirdhand gossip is not known truth. The distinction must live in data fields, not only in
  prose wording.
- **Store neutral claims, frame at render time.** Memory records should not store phrases
  like "I saw..." or "Maren told me...". They should store the claim, then let the prompt
  renderer add the correct firsthand, overheard, gossip, or implanted frame.
- **Consolidation must be bucket-local.** Memories should compact only with the same kind
  of memory. Conversations, witnessed events, overheard dialogue, and gossip should never
  be summarized together.
- **Summaries, not transcripts.** NPC memory should carry short claims and impressions, not
  full dialogue logs. Long transcripts bloat prompts and tempt the model to treat unrelated
  talk as current context.
- **Claims, not canon.** Gossip is what an NPC believes or has heard. It can be wrong,
  stale, exaggerated, or partial. Engine truth remains in deeds, canon records, promises,
  entities, factions, and semantic notes.
- **Local spread before global reputation.** A direct witness reacts more strongly than an
  NPC who heard a tavern story two days later. Gossip can feed reputation, but should not
  replace all faction standing.
- **Replayable by construction.** Any model-generated summary must be recorded at the
  apply point. Replay re-applies the recorded memory record and never asks the model to
  summarize again.
- **Daily spread is deterministic.** Social propagation copies and reframes existing
  records, decays confidence, and bumps hop counts. It should not call the LLM.
- **Wild magic can weaponize memory.** Implanted memories are not just another row in the
  provenance table. A spell that plants a false belief, then lets that belief spread as
  rumor, is one of the strongest reasons to build this system.
- **Clean failure over weak fallback.** Arbitrary conversation is hard to summarize
  deterministically. If the summary provider fails, no overheard memory is created for
  that exchange. Overhearing is social texture, not a required state mutation.

---

## Existing Foundation

Wild Magic already has the pieces this should extend:

- `NPCProfile.memory` stores NPC-facing memory lines, surfaced to dialogue as
  `things_i_have_noticed`.
- `NPCProfile.conversation` stores the NPC's own recent exchange with the player, surfaced
  separately as `recent_conversation`.
- `_update_npc_perceptions` lets visible nearby NPCs remember new non-LLM message-log lines.
- `record_deed` detects witnesses and writes immediate witness memory.
- The daily simulator already uses the presence of personal memory to weight bond drift.
- Background lore, flesh, and canon jobs already demonstrate the replay-safe apply-point
  pattern for model-generated enrichments.

The first implementation should formalize and extend this instead of adding a competing
memory system.

---

## Memory Record Model

The current `memory: list[str]` is too blunt for gossip. Introduce a typed record while
keeping old string memory compatible during migration.

Suggested shape:

```python
@dataclass
class NPCMemoryRecord:
    id: str
    claim: str  # neutral claim, without "I saw..." / "Maren told me..." framing
    provenance: str  # firsthand | overheard | gossip | implanted | system
    bucket: str = "observation"  # observation | conversation | overheard | gossip | system
    subtype: str = ""
    subject: str = ""
    subject_refs: list[str] = field(default_factory=list)  # entity/soul/place/faction ids
    tags: list[str] = field(default_factory=list)
    source_npc_id: str | None = None
    source_name: str | None = None
    speaker_names: list[str] = field(default_factory=list)
    place_key: str = ""
    turn: int = 0
    confidence: float = 1.0
    salience: int = 1
    privacy: str = "social"  # public | social | intimate | secret
    shareable: bool = True
    spread_weight: float = 1.0
    hops: int = 0
    source_event_id: str = ""
```

Core provenance meanings:

| Provenance | Meaning | Dialogue stance | Mechanical weight |
|---|---|---|---|
| `firsthand` | The NPC personally saw, heard, suffered, or chose it. | "I saw..." / "I remember..." | Strongest |
| `overheard` | The NPC was nearby when others spoke. | "I overheard..." / "I heard Maren say..." | Medium |
| `gossip` | Another NPC later shared it socially. | "People say..." / "Quill told me..." | Weakest |
| `implanted` | Magic wrote the belief into the NPC. | The NPC treats it as their own memory unless the magic says otherwise. | Strong, but flagged |
| `system` | Engine-authored legibility note, such as bond moments. | Personal if written that way. | Depends on source |

`provenance` answers "how does this NPC know this?" `bucket` answers "which memory lane
does this belong to?" Those are related but not identical. A witnessed spell and a
conversation with the player can both be `firsthand`, but they should render and compact
separately.

`subject_refs` is load-bearing. It should hold durable ids such as `state.player_soul_id`,
entity ids, faction ids, site ids, or semantic subject ids. "Player-related memory" should
mean a structured id match, not substring matching against `claim`. The free-text
`subject` is for prompts and debugging; deterministic scoring, spread relevance, and
contradiction grouping should prefer `subject_refs`.

`claim` should be a neutral, canonical statement. Good claims look like:

```text
The player cut down an Imperial road-captain.
The old oak wakes after midnight.
The player and this NPC argued about whether the Empire keeps roads safe.
```

Avoid storing the provenance frame inside the claim:

```text
I saw the player cut down an Imperial road-captain.
Maren told me the old oak wakes after midnight.
People are saying the player carries a whispering curse.
```

That framing belongs in the dialogue-context renderer. This keeps daily spread cheap:
copying a record changes provenance/source/confidence fields, not prose.

Suggested buckets:

| Bucket | Typical provenance | Prompt surface | Consolidates with |
|---|---|---|---|
| `observation` | `firsthand` | `things_i_personally_witnessed` | Other observations only |
| `conversation` | `firsthand` | `conversation_memory` | Other conversation memories only |
| `overheard` | `overheard` | `things_i_overheard` | Other overheard memories only |
| `gossip` | `gossip` | `gossip_i_have_heard` | Other gossip only |
| `system` | `system` | Explicit special handling | Only when a specific rule allows it |

Implanted memories are not a bucket. They use the bucket that matches what the memory
pretends to be: `bucket="observation"` for "you saw this happen",
`bucket="conversation"` for "we had this conversation", and so on. The
`provenance="implanted"` field remains the engine's truth even when the render frame makes
the NPC treat the claim as personal experience.

`NPCProfile.remember(text)` can remain as a compatibility helper that writes a `firsthand`
observation claim. During migration, old string memories can be treated as best-effort
neutral claims, but new callers should avoid first-person prose and call a richer helper
such as `profile.add_memory(record)` or `engine.record_npc_memory(...)`.

---

## Dialogue Context Rendering

Do not render all memory records into a single `things_i_have_noticed` list forever.
Instead, render by bucket and preserve provenance inside each bucket:

```json
{
  "things_i_personally_witnessed": [
    {
      "claim": "The player cut down an Imperial road-captain.",
      "frame": "You personally witnessed this.",
      "confidence": "certain"
    }
  ],
  "things_i_overheard": [
    {
      "claim": "The old oak wakes after midnight.",
      "frame": "You overheard Maren tell the player this.",
      "heard_from": "Maren",
      "confidence": "hearsay"
    }
  ],
  "gossip_i_have_heard": [
    {
      "claim": "The player carries a curse that makes doors whisper.",
      "frame": "Quill told you this.",
      "source": "Quill",
      "confidence": "rumor"
    }
  ],
  "conversation_memory": {
    "recent_exchanges": [...],
    "older_summaries": [
      {
        "claim": "The player and this NPC previously argued about whether the Empire keeps roads safe.",
        "frame": "This is from your own past conversation with the player."
      }
    ]
  }
}
```

Do not also emit the legacy `recent_conversation` key once `conversation_memory` exists.
The prompt shape should have one authoritative conversation-memory block.

The dialogue prompt should be explicit:

```text
Memory provenance matters. Treat things_i_personally_witnessed and conversation_memory as
your own experience. Treat things_i_overheard and gossip_i_have_heard as hearsay. Do not
speak as if you personally experienced overheard or gossiped events. Attribute them
naturally when relevant: "I overheard...", "Maren said...", "people are saying...".
Ignore irrelevant memory instead of reciting it.
```

Budgeting should happen before rendering:

- Pick recent and high-salience records first.
- Prefer records whose subject/tags overlap the player's message, current location, visible
  entities, or player legend.
- Cap per bucket, for example 4 observations, 4 conversation memories, 3 overheard
  memories, and 3 gossip memories.
- Truncate rendered summaries aggressively, around one or two sentences.
- Keep a small window of the NPC's own player conversation verbatim, then render older
  exchanges as summaries rather than letting them roll off forever.
- Render confidence as words, not raw floats. For example, `0.85+` can become `certain`,
  `0.6+` can become `credible`, `0.35+` can become `uncertain`, and lower values can
  become `thin rumor`. Provenance-specific labels such as `hearsay` and `rumor` are fine
  when they are clearer than numbers.

This prevents a social NPC from becoming a transcript dump.

---

## Conversation Memory Compression

The same summary utility should compress an NPC's own long-running conversation with the
player. This is separate from overheard gossip: an NPC's own conversation summaries are
firsthand memory, not hearsay.

Suggested policy:

- Keep the last 5-10 complete player/NPC exchanges word-for-word.
- When older complete exchanges fall outside that window, queue a background summary job.
- Store successful rollups as `firsthand` memory records with `bucket="conversation"` and
  a subtype such as `conversation_summary`.
- Render those records under `conversation_memory.older_summaries`, not under gossip.
- If the summary provider fails, keep the old verbatim exchanges around until another
  attempt or until a generous storage cap forces normal eviction. Do not replace them with
  an ad hoc local substitute.

When conversation summaries themselves become numerous, they should enter the bucket-local
consolidation system described below:

- Keep perhaps 10-20 conversation summaries per NPC before compaction.
- Compact them only with other `conversation` records.
- Preserve source ids inside the compacted record so replay/dedupe/audit can still explain
  what was absorbed.
- If compaction fails, keep the existing summaries unchanged.

This creates a three-tier memory shape:

```text
recent conversation: exact, small window
older conversation: short summaries
very old conversation: consolidated summaries
```

The point is continuity without context bloat. A shopkeeper can remember the shape of a
relationship across a long run without dragging every sentence into every dialogue prompt.

---

## Conversation-To-Gossip Gate

NPCs may share conversations, but conversation should not automatically become gossip.
Treat it as eligible material that must pass a "worth repeating" gate.

Rules:

- Never spread raw recent exchanges. Only summarized `bucket="conversation"` records can
  enter social spread.
- Default routine small talk to `shareable=False` and low `salience`.
- Mark a conversation summary shareable when it contains something socially meaningful:
  threats, bargains, confessions, warnings, faction information, magic, scandal, public
  plans, favors, betrayals, or emotionally salient relationship moments.
- Use `privacy` to distinguish ordinary social talk from secrets. `public` and `social`
  records can spread normally. `intimate` records require a close/trusted edge. `secret`
  records do not spread unless a trait, spell, or explicit betrayal mechanic overrides it.
- Lower the spread priority for conversation-derived records compared with witnessed deeds
  of the same salience, so gossip about action usually beats gossip about chatter.
- Cap conversation-derived spread per NPC per day, for example at one selected record.
- Dedupe by `source_event_id` and apply cooldowns so the same conversation does not get
  retold every day.

This lets NPCs share interesting things the player said without turning every dialogue
exchange into a rumor. A confession to a friend, a public threat, or a promise made in a
tavern can travel; "hello" and shop chatter should stay local texture.

---

## Bucket-Local Memory Consolidation

Memory consolidation should not be one global compactor. Each prompt bucket should have its
own queue, threshold, and provider call. This is the main protection against the summary
model accidentally cross-mixing provenance.

Rules:

- Partition candidate records by `bucket`, then by `provenance`.
- Never send `conversation`, `observation`, `overheard`, and `gossip` records in the same
  provider request.
- Prefer grouping inside a bucket by subject/tag, then by age when no better grouping
  exists.
- The compacted record inherits the same `bucket` and `provenance` as the input batch.
- Reject provider output that changes bucket/provenance, removes required hearsay
  attribution, or upgrades hearsay into direct experience.
- Preserve absorbed source ids inside the compacted record.
- If compaction fails, keep the original records unchanged.

Suggested threshold style:

| Bucket | Example trigger | Compaction stance |
|---|---|---|
| `conversation` | More than 10-20 older summaries | Summarize relationship/history with the player |
| `observation` | Many old low-salience firsthand notes | Be conservative; direct experiences are valuable |
| `overheard` | More than 8-12 overheard summaries | Keep "I overheard..." framing |
| `gossip` | More than 10-20 rumors | Keep source/confidence/hop language |
| `system` | No automatic trigger | Compact only with an explicit subsystem rule |

The output should stay in the same lane it came from. For example, ten overheard snippets
about the old oak can become one compacted overheard memory, but they should not merge with
the NPC's own conversations about the old oak or with a direct sighting of the tree waking.

---

## Overheard Conversation Capture

The near-term feature is overheard dialogue. The best insertion point is after a dialogue
exchange is accepted and recorded, in `apply_dialogue_exchange`.

Process:

- Record the speaking NPC's own exchange in `conversation_memory.recent_exchanges`
  (`recent_conversation` during migration).
- Build an overheard event from the player message, NPC reply, speaker ids, turn, place,
  and location.
- Find living NPCs close enough to plausibly hear it.
- Exclude the speaking NPC.
- Exclude NPCs blocked by obvious perception constraints where available.
- Queue one background summary request for the overheard event if at least one listener
  qualifies.
- If the summary succeeds, write one short `overheard` memory record per listener.
- If the summary fails technically, write nothing. The NPC simply did not retain a useful
  overheard memory from that exchange.
- Dedupe by `source_event_id` so repeated drain/replay paths cannot double-add it.

Do not store raw transcripts as fallback memories. Full dialogue text is too bulky for
context and too easy for the dialogue model to mistake for direct conversation history.

---

## Memory Summary Provider

Overheard dialogue, conversation compression, and bucket-local consolidation should use
the same cheap background LLM route. The provider is best-effort and mode-aware: overheard
summary failure drops the overheard memory; compression/consolidation failure leaves
existing records unchanged.

Suggested provider contract:

```python
@dataclass
class MemorySummaryResolution:
    claim: str
    bucket: str
    provenance: str
    subject: str
    tags: list[str]
    salience: int
    privacy: str
    shareable: bool
    technical_failure: bool
    provider_name: str
    absorbed_record_ids: list[str] = field(default_factory=list)
    raw_response: str | None = None
```

Prompt rules:

- Summarize only what was present in the supplied exchange or memory records.
- Add no new facts.
- Write one or two short neutral claim sentences.
- Do not include provenance framing in the claim. Avoid phrases such as "I saw",
  "I overheard", "Maren told me", or "people are saying".
- Accept exactly one memory bucket/provenance lane per request.
- Return the same bucket and provenance the engine requested.
- Preserve the requested provenance lane. The render layer will make overheard summaries
  read as overheard and conversation summaries read as the NPC's own prior conversation
  with the player.
- Never combine firsthand observation, direct conversation, overheard dialogue, and gossip
  into one summary.
- Prefer concrete subjects and tags.
- Mark privacy as `public`, `social`, `intimate`, or `secret`.
- Mark private, intimate, or purely mechanical chatter as less shareable.
- Return an empty or non-shareable result when the exchange is too trivial, too ambiguous,
  or too private to become useful gossip.

The engine assigns `bucket` and `provenance`. The provider may echo them so the engine can
detect a confused response, but the echoed fields are never authoritative. If they do not
match the request, reject the result and leave existing memory unchanged.

Replay rule:

- The live run records applied memory records and successful compaction transformations in
  the action replay data.
- Replay injects the exact records and transformations.
- If the provider fails live, no new summary or compaction is stored, and replay has
  nothing to inject.

Queue rule:

- Memory summary jobs should drain under a strict per-tick and per-real-time budget.
- Cap concurrent provider calls.
- Prefer player-near, high-salience, and soon-to-render memories when the queue is backed
  up.
- Let low-salience overheard and consolidation jobs expire rather than stalling a day tick.
- Daily spread never waits on this queue.

This mirrors the existing promise, flesh, and canon apply-point model.

---

## Gossip Graph

The gossip graph is a deterministic directed graph over NPC profiles.

Suggested edge shape:

```python
@dataclass
class GossipEdge:
    from_id: str
    to_id: str
    relationship: str  # zone | neighbor | friend | coworker | faction | family | patron
    trust: float
    contact_chance: float
    privacy_bias: float = 0.0
    created_turn: int = 0
    created_day: int = 0
```

Use a deliberately simple placeholder first:

- When NPCs are realized in the same zone, create fully connected directed `zone` edges
  between them.
- Use conservative defaults for `trust`, `contact_chance`, and `privacy_bias`.
- Keep these placeholder edges visibly tagged as `relationship="zone"` so later
  worldbuilding rules can replace or supplement them.

The graph should live in `GameState`. Edges may be derived from durable NPC/site metadata,
but only as a creation step. Once both endpoints are realized and an edge is created, it
should be durable and monotonic: do not recompute past edges from a later realized entity
set. Daily spread should traverse only edges that existed at that tick. This keeps replay
safe when the world realizes new NPCs over time.

This is intentionally underdesigned. Once worldbuilding is more concrete, edge creation can
be culture- and place-aware:

- Vint might create unusually dense social edges and higher contact chances because gossip
  is central to local culture.
- Brall might preserve ordinary edges but give dialogue prompts a cultural instruction to
  embellish witnessed and heard events into tall tales.
- Factions, families, guilds, workplaces, patronage, rivalries, and authored NPC ties can
  add stronger or more private edges later.

Those richer rules should be added when the relevant towns and factions have enough shape
to justify them. Until then, same-zone complete edges are a testable skeleton, not the final
social model.

---

## Daily Gossip Spread

Run social propagation during the daily world tick, after deed consequences and bond drift
have had a chance to update memory and standing.

Daily spread must be LLM-free. It copies neutral claims, changes structured fields, decays
confidence/salience, and records deterministic transformations for replay. It does not
rewrite prose, because provenance framing happens later in dialogue-context rendering.

For each eligible edge:

- Roll deterministic contact using day, zone/place, and edge id.
- Pick at most a tiny number of shareable source memories.
- Prefer high salience, recent events, and subject overlap with the relationship.
- Prefer structured `subject_refs` matches over text similarity when checking subject
  overlap.
- Skip records the receiver already knows by source event id.
- Reduce confidence and salience when copying. Trusted edges preserve more confidence than
  casual edges: `received_confidence = source_confidence * edge.trust * hop_decay` is a
  reasonable starting point.
- Use `privacy_bias` to decide whether sensitive records can traverse an edge. Family,
  lovers, and close friends can carry `intimate` records; coworkers and neighbors usually
  should not. `secret` records require an explicit override.
- Increment `hops`.
- Stop spreading once `hops` exceeds a small cap, such as 2.
- Preserve the neutral `claim`.

Copying rule:

```text
firsthand -> gossip when shared socially
overheard -> gossip when shared socially
gossip -> gossip with lower confidence and higher hops
implanted -> usually not shareable unless the magic made it socially visible
```

This makes gossip degrade naturally. The same stored claim can render as a direct witness
memory, a named-source rumor, or a vague public rumor depending on provenance, source, and
confidence.

---

## Relationship To Reputation

The gossip graph should not delete the faction ledger. It should make reputation more local
and legible.

Keep three layers:

- **Deeds and legend:** mechanical truth about what the player's soul has done.
- **Faction standing:** political aggregate, used for large-scale simulation and backlash.
- **NPC memory and gossip:** local knowledge, used for dialogue, bonds, social trust, and
  small-scale reactions.

Bond drift should eventually use provenance-weighted memory, not simply whether the NPC has
any memory:

```text
firsthand player-related memory: strongest personal multiplier
overheard player-related memory: smaller multiplier
gossip player-related memory: smallest multiplier, scaled by confidence
```

This lets the world feel fair. People who saw the player defend them can love them before
the whole town does. People who only heard a rumor react with uncertainty.

---

## Player-Facing Loops

The system should be built toward visible gameplay, not only better simulation internals.
Two loops are near-term goals:

- **Your reputation precedes you locally.** An NPC the player has never met reacts because
  a witnessed deed reached them through a plausible social path. This is the clearest
  one-beat demonstration that the graph exists.
- **False beliefs can spread.** The player can plant, distort, or magically implant a
  claim, then watch it travel with decaying confidence and changing attribution.

The early implementation should prove both loops in CLI and GUI play, even if the first
version of each is narrow. An inspect panel or rumors command is useful, but the strongest
payoff is an NPC changing dialogue, trust, fear, price, or willingness to help because of
what they have heard or falsely believe.

---

## Implanted Memory As Wild Magic

`implanted` deserves special treatment because it is a Wild Magic-native use case. A spell
could make an NPC believe they personally saw the player rescue them, steal from them, or
swear an oath beside them. That belief is not engine canon, but it can be socially real.

Implementation stance:

- Store implanted memories with `provenance="implanted"` and an explicit subtype such as
  `false_memory`, `edited_memory`, or `magical_compulsion`.
- The NPC may treat the claim as firsthand in dialogue if the spell says the memory feels
  real, but the engine still knows it was implanted.
- Implanted claims should not become deed truth, promise truth, or faction truth unless a
  separate explicit mechanic crystallizes them.
- Shareability should be controlled by the spell result. A private implanted memory may
  affect only one NPC; a socially contagious delusion can enter the gossip graph.
- Spread copies should usually become `gossip` for receivers, while preserving metadata
  that the origin was magical if the spell left detectable traces.

This gives the graph a distinct fantasy payoff: the player can change what people think
happened without changing what actually happened.

---

## Privacy, Lies, And Contradiction

Gossip is more interesting if it can be selective and imperfect, but the first version
should keep imperfection bounded.

Recommended fields:

- `confidence`: how much the receiving NPC trusts this memory.
- `shareable`: whether ordinary social spread can pass it onward.
- `privacy`: whether the record is public, social, intimate, or secret.
- `spread_weight`: how likely this record is to be selected when several shareable memories
  compete for a tiny spread budget.
- `source_name`: who the receiver believes supplied it.
- `source_event_id`: the underlying event, for dedupe and contradiction handling.
- `subject_refs`: structured subjects used for player-related checks, spread relevance, and
  grouping.
- `tags`: subjects for retrieval and spread.

Tie `shareable` to existing deed visibility wherever possible. Deterministic deed memories
do not pass through the summary provider, so they need deterministic defaults: secret deeds
should create `shareable=False` memories, witnessed deeds can be shared locally, and public
deeds can enter broader rumor surfaces.

Deterministic origins also need deterministic salience. Do not rely on the summary provider
to be the only source of meaningful `salience`. Deeds can derive salience from magnitude,
danger, faction impact, witnesses, and whether the player was the subject. Perception notes
can stay low salience unless they involve danger, magic, named NPCs, or explicit social
stakes.

Later mechanics can use this without changing the base model:

- Liar NPCs can lower confidence or alter summaries through a controlled provider.
- Fearful NPCs may refuse to share anti-Empire memories.
- Faction agents can spread high-salience propaganda.
- Memory-edit spells can alter, remove, or mark records as implanted.
- Multiple conflicting memories about the same subject can surface as uncertainty in
  dialogue instead of silently merging.

Contradiction should be surfaced, not solved, in the first version. The engine should not
try to semantically prove that two arbitrary claims conflict. Instead, keep bucket-local
consolidation from merging separate claims about the same `subject_refs`; render both when
they are relevant and let the dialogue model voice uncertainty. A memory-edit spell that
knowingly plants a contrary belief may explicitly mark a record as conflicting with another
record id.

The engine should not read gossip text to decide hard outcomes. If gossip needs mechanical
force, first crystallize it into a deed, faction delta, semantic note, promise, or explicit
memory operation.

---

## UI And CLI Surfaces

This system needs player-legible surfaces, or it will feel like invisible math.

Useful surfaces:

- `inspect <npc>` shows a compact split between witnessed memories and heard rumors.
- Dialogue can naturally reveal provenance through wording.
- A journal or rumors command can show public rumors, not private NPC memories.
- Debug overlays can show selected gossip edges and pending background summary jobs.
- Replay records should include applied gossip/memory records when they were model-shaped.

Both GUI and CLI must expose the same player-facing abilities. Debug presentation can differ,
but the underlying information should be reachable in both.

---

## Implementation Slices

Recommended order:

**No-new-LLM baseline**

- Add `NPCMemoryRecord`, serialization helpers, save back-compat for old string memories,
  neutral `claim` storage, and structured `subject_refs`.
- Add render-time provenance framing for dialogue context, without leaking raw confidence
  floats.
- Update the dialogue prompt and tests so overheard and gossip memory are treated as
  hearsay.
- Convert `_update_npc_perceptions`, deed witness notes, and `record_deed` memory writes to
  the typed memory API.
- Tie deterministic deed memories to deed visibility so secret deeds default to
  `shareable=False`.
- Make bond drift and local reactions consider provenance-weighted player-related memories
  instead of a binary "has memory" check.
- Use `subject_refs`, especially `state.player_soul_id`, to identify player-related
  memories.
- Expose the split memory categories through matching CLI and GUI inspection surfaces.

This is the first shippable slice. It adds no background LLM calls and should already make
direct witnesses, overhearers, and rumor-holders feel different.

**Deterministic social graph**

- Seed stable placeholder gossip edges by fully connecting realized NPCs within the same
  zone.
- Persist graph edges monotonically as NPCs are realized.
- Copy shareable neutral claims across edges during the daily tick, with confidence decay,
  hop limits, trust/privacy handling, source attribution, and dedupe.
- Add the conversation-to-gossip gate so only socially meaningful conversation summaries
  can spread.
- Record deterministic spread transformations for replay/debugging.
- Add player-facing loops where an unfamiliar NPC reacts to locally spread information.
- Add support for implanted or false memories entering the graph when a spell explicitly
  makes them shareable.

This layer should remain provider-free. It is simulation and replay work, not summarization
work.

**LLM-shaped memory utilities**

- Add the cheap background memory summary provider with mode-specific failure behavior.
- Add strict summary-queue drain limits and concurrent-call caps.
- Add LLM-shaped overhearing after dialogue exchanges; provider failure creates no
  overheard record.
- Keep a verbatim recent-exchange window and summarize older complete exchanges into
  firsthand conversation memory records.
- Compact older summary batches only within one bucket/provenance lane; failed compactions
  leave existing summaries unchanged.
- Add a memory apply buffer similar to promises/canon for model-shaped summaries.

Each slice should be testable through `wildmagic.cli`.

---

## Tests

Important tests:

- Old saves with `memory: list[str]` load, migrate, and render through the new context
  shape.
- Dialogue context separates firsthand, overheard, and gossip memory.
- The prompt-facing context never puts overheard records into the firsthand bucket.
- The prompt-facing context frames neutral claims at render time and does not expose raw
  confidence floats.
- New engine-authored memory records store neutral claims, not first-person framed prose.
- Player-related memory checks use `subject_refs`, not claim substring matching.
- Implanted memories can use `bucket="observation"` with `provenance="implanted"` and
  render as personally believed without becoming system records.
- A nearby NPC overhears a dialogue exchange; the speaker does not overhear their own
  exchange.
- Distant or blocked NPCs do not receive the overheard record.
- Provider failure creates no overheard memory and does not affect turn settlement.
- Trivial or non-shareable summaries create no overheard memory.
- An NPC keeps the last complete exchanges verbatim and receives older conversation
  summaries without losing provenance.
- Conversation-summary provider failure leaves existing exchanges/summaries unchanged.
- Consolidation queues never batch records from different buckets or provenances.
- A provider compaction result that changes bucket/provenance is rejected and leaves
  originals unchanged.
- Conversation summaries are not compacted with overheard, gossip, or observation records.
- Summary compaction preserves absorbed record ids and replays without another provider
  call.
- Model-generated summaries are recorded once and replay without another provider call.
- Secret deed memories default to `shareable=False`; witnessed/public deeds use the
  appropriate deterministic sharing default.
- Deterministic deed and perception memories receive non-flat salience based on their
  origin.
- Graph seeding creates deterministic same-zone directed edges for realized NPCs.
- Graph edges are monotonic; realizing a new NPC later does not rewrite edges used by
  earlier daily ticks.
- The same day, seed, and world state produce byte-identical daily spread results.
- Daily spread copies shareable memories across graph edges, lowers confidence, increments
  hops, and dedupes by source event.
- Daily spread uses edge `trust` to compute received confidence and `privacy_bias` to gate
  intimate records.
- Daily spread reframes provenance through structured fields without calling the provider.
- Conversation-derived gossip spreads only when the conversation summary is shareable,
  salient enough, and within the per-day conversation spread cap.
- Gossip does not spread past the hop cap.
- Bond drift weights firsthand memory more than overheard memory, and overheard memory more
  than thirdhand gossip.
- Implanted memories affect NPC belief without becoming engine canon.
- Provider queue draining respects per-tick and concurrent-call caps; daily spread does not
  wait on summary jobs.
- CLI inspection exposes the split memory categories.

---

## Main Risks

| Risk | Mitigation |
|---|---|
| The LLM treats hearsay as personal experience. | Structural provenance buckets plus explicit prompt rules. |
| Stored prose bakes in the wrong provenance frame. | Store neutral claims and apply "I saw" / "Maren told me" / "people say" wording only at render time. |
| Player-related scoring falls back to prose matching. | Store structured `subject_refs` and key player-related checks off `state.player_soul_id`. |
| Consolidation cross-mixes memory types. | Bucket-local queues, one provenance lane per provider request, and engine-side rejection of mismatched outputs. |
| Context grows without bound. | Store summaries, cap per bucket, rank by salience/recency/relevance. |
| Replay calls the summarizer again. | Record applied memory records at the same boundary that mutates state. |
| Replay changes when new NPCs are realized. | Persist monotonic graph edges and traverse only edges that existed at the tick being replayed. |
| Summary jobs stall a busy day. | Use per-tick drain budgets, concurrent-call caps, priority ordering, and expiry for low-salience jobs. |
| Summary provider failures erase possible overhearing. | Accept this as the clean failure mode; overhearing is optional texture and can be audited. |
| Conversation compression loses useful details. | Keep recent exchanges verbatim, compact only older material, and preserve absorbed source ids for audit. |
| Every conversation becomes gossip. | Spread only summarized, shareable, socially meaningful conversation memories, with per-day caps and cooldowns. |
| The graph is correct but not fun. | Build toward visible loops: unfamiliar NPCs reacting to rumors, and planted or implanted beliefs spreading. |
| Gossip becomes hidden reputation math. | Keep faction standing separate; use gossip for local knowledge and provenance-weighted bonds. |
| Rumors become engine truth. | The engine never reads gossip prose for hard outcomes. Crystallize explicit mechanics when needed. |
| Implanted memories accidentally become canon. | Keep implanted provenance explicit; treat belief effects separately from deeds, promises, and faction truth. |
| Everyone knows everything. | Zone-local placeholder edges, contact chances, shareable flags, confidence decay, hop caps, and later culture-specific graph rules. |

---

## Summary

The gossip graph should be a provenance-first memory transport system:

```text
event or dialogue
-> neutral typed memory claim
-> optional bucket-local summary provider
-> deterministic daily graph spread
-> render-time provenance frame
-> local dialogue and bond effects
```

It should make the world feel socially alive without turning the LLM into the simulator.
NPCs hear, mishear, repeat, doubt, and remember, but the engine decides what was heard, who
heard it, how far it travels, and how strongly it can matter.
