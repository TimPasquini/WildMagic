# Autoplay Harness Plan

An overnight autonomous playtesting harness. A local LLM (qwen3:8b via the existing Ollama
infrastructure) plays the game as a QA agent: it chooses commands, takes notes, and flags
suspected bugs. The harness itself does the trustworthy bug detection through programmatic
invariant checks, and produces a morning report combining hard violations, aggregate
statistics, and the agent's (lower-trust) notes.

The agent never edits code. It plays, observes, and reports.

## Design principles

1. **The LLM player is a traffic generator; invariants are the QA.** Every finding is tiered:
   - Tier 1 (trustworthy): exceptions, `validate_state()` errors, turn-contract violations.
   - Tier 2 (trustworthy): aggregate stats from `ActionResult` records and audit logs.
   - Tier 3 (leads only): the agent's freeform notes. Never reported as confirmed bugs.
2. **Every finding must be reproducible or evidenced.** Each episode records its seed,
   scenario, full command list, and a replay file. LLM-resolver findings reference
   `logs/wild_magic_audit.jsonl` records.
3. **The harness never crashes because the model misbehaved.** Malformed agent output,
   timeouts, and hangs are routine events with defined fallbacks.

## Existing seams the harness builds on

- `GameSession.execute_command(command) -> ActionResult` (`wildmagic/actions.py`) — returns
  `success`, `consumed_turn`, `technical_failure`, `turn_before`/`turn_after`, `messages`,
  `wild_magic` record, `should_quit`. This is the entire game-driving API; no subprocess or
  stdout parsing.
- `GameEngine.validate_state() -> list[str]` (`wildmagic/engine.py`) — already checks
  blocking-entity overlap, out-of-bounds entities, HP/MP bounds, inventory sanity, tile
  table integrity. Run after every command.
- `describe_state(engine)` — the `inspect` text, reused as the agent's observation.
- `save_replay(session, path)` (`wildmagic/replay.py`) — per-episode reproducibility.
- `_post_ollama_chat` + purpose-scoped config (`wildmagic/llm_client.py`,
  `wildmagic/config.py`) — the agent becomes a new purpose, `agent`, so
  `WILDMAGIC_AGENT_OLLAMA_HOST`, `WILDMAGIC_AGENT_MODEL`, `WILDMAGIC_AGENT_OLLAMA_TIMEOUT`,
  etc. work like the existing `wild`/`dialogue`/`trade`/`town` scopes.

## Architecture

New module: `wildmagic/autoplay.py`, runnable as `python -m wildmagic.autoplay`.

```
Campaign (overnight driver)
  └── EpisodeRunner (one game, one seed)
        ├── GameSession            (the game; existing code, untouched)
        ├── PlayerAgent            (LLM chooses commands, writes notes)
        ├── InvariantChecker       (tier-1 checks after every command)
        └── EpisodeLog             (JSONL step records + replay + findings)
```

### PlayerAgent

Each turn:

1. Build a compact observation (~1–1.5k tokens):
   - System prompt: persona + episode objective + command surface + note-taking rules.
   - User message: new game messages since the last command, plus a condensed `inspect`
     summary (turn, HP/MP, position, inventory, curses, visible enemies with distances).
2. Call Ollama with `format=json`, requesting:
   ```json
   {"command": "...", "note": "... or null", "bug_suspected": false}
   ```
3. Validate `command` against the known command surface (`cast`/`talk` accept freeform
   tails; everything else must match a known verb). On parse failure or unknown verb:
   retry once with the error appended; on second failure, fall back to `wait` and log a
   `agent_parse_failure` event. Three consecutive parse failures end the episode.

Player model = resolver model (qwen3:8b) by default. The game is turn-based so agent and
resolver calls never overlap — one Ollama server serves both with no contention, and using
one model avoids VRAM swap thrash on the 8 GB Arc A750.

### Personas and objectives

Lifted from `AGENT_PLAYTESTING.md` playtest styles: `cautious`, `wild`, `stress`. Each
episode randomly draws a persona plus an objective/theme to force coverage diversity
instead of letting the model converge on its five favorite spells. Examples:

- "Focus on terrain-transformation spells this run."
- "Get to depth 3 using as few wild casts as possible."
- "Talk to every NPC you meet before fighting anything."
- "Cast only spells involving summoned creatures."

Themes live in a small table in `autoplay.py` so new ones are one-line additions.

### InvariantChecker (tier 1)

After every `execute_command`:

| Check | Source |
|---|---|
| Unhandled exception from `execute_command` | `try/except` around the call |
| `engine.validate_state()` returns errors | existing engine method |
| `technical_failure=True` but turn advanced | `ActionResult` fields (doc: technical failures must not consume the turn) |
| Wild spell rejected as overpowered but turn did **not** advance | `wild_magic` record + `consumed_turn` |
| `consumed_turn=True` but no new messages | `ActionResult.messages` (doc: player must be able to tell what happened) |
| Turn counter decreased or jumped | `turn_before`/`turn_after` |

On an exception: capture the traceback, seed, persona, and full command history; save the
replay; end the episode; continue the campaign with the next episode. A crash is a finding,
not a harness failure.

Implementation note: the exact shape of the `wild_magic` record for "rejected as too
powerful" vs "technical failure" needs to be confirmed against `wild_magic.py` during
implementation, and a game-over/death detection check is needed to end episodes cleanly
(verify how the engine signals player death).

### Episode and campaign structure

- Episode: one `GameSession` with a fresh seed. Ends on death, `should_quit`, max turns
  (default 120), wall-clock budget (default 15 min), or 3 consecutive agent parse failures.
- Loop detection: the same command 4 times in a row injects a one-time nudge into the next
  observation ("you have repeated this command; it is not changing anything"); 6 times ends
  the episode with a `possible_softlock` finding — this class of bug (free no-op moves) has
  occurred before and is exactly what an overnight run should catch.
- Campaign: `--episodes N` or `--hours H`, cycling scenarios (`dungeon` weighted heaviest,
  plus `test_chamber`, `town`) and personas. Per-call Ollama timeouts and the episode
  budget bound worst-case stall time. Ctrl+C finishes the current episode's logs and writes
  the report for whatever completed.
- End of episode: one extra LLM call — "you played N turns; here are your notes; summarize
  the problems you saw and what felt unsatisfying" — stored as the episode summary (tier 3).

### CLI

```powershell
python -m wildmagic.autoplay --hours 8 --provider ollama --out logs/autoplay
python -m wildmagic.autoplay --episodes 3 --max-turns 50 --provider mock   # harness shakedown
```

Flags: `--episodes`, `--hours`, `--max-turns`, `--scenario` (repeatable; default rotation),
`--persona` (repeatable; default rotation), `--seed-base`, `--provider` (game resolver:
mock/ollama), `--out`.

### Output layout

```
logs/autoplay/<run_id>/
  episode_001.jsonl      # one record per step: command, ActionResult.to_record(),
                         #   violations, agent note, timing
  episode_001.replay.json
  findings.jsonl         # tier-1 violations + tier-3 flagged notes, each with
                         #   {tier, episode, seed, turn, evidence, replay_path}
  report.md              # the morning read
```

### Morning report (`report.md`)

Generated at campaign end (and incrementally after each episode, so a killed run still has
a report):

1. **Run summary** — episodes, total turns, total casts, deaths, completion reasons.
2. **Tier 1 findings** — crash signatures deduped by traceback tail, each with a
   reproduction line (`python -m wildmagic.cli --provider mock --seed S --script ...`);
   contract violations grouped by type.
3. **Stats** — wild-cast technical-failure rate, OP-rejection rate, parse-failure rate of
   the agent itself, deaths per episode, command distribution, per-persona differences.
4. **Agent notes** — grouped by keyword overlap, each linked to episode/turn/audit record,
   clearly labeled as unverified leads.

## Phases

**Phase 1 — harness against mock resolver.** Build `autoplay.py` end to end: PlayerAgent
(real LLM), EpisodeRunner, InvariantChecker, logs, report. Run with `--provider mock` so
the game side is fast and deterministic while harness bugs get shaken out.
*Milestone: 3 episodes × 50 turns complete unattended; report.md is readable; a manually
injected invariant violation shows up correctly in findings.*

**Phase 2 — live resolver, first overnight run.** Switch to `--provider ollama`, run 1–2
hours supervised, tune observation size, loop detection thresholds, and per-call timeouts;
then a full overnight run.
*Milestone: 8-hour run completes without harness intervention; morning report contains
tier-1 findings with working reproduction commands.*

**Phase 3 — triage quality.** Cross-reference findings with `wild_magic_audit.jsonl`
records automatically; promote recurring agent notes into the regression list in
`AGENT_PLAYTESTING.md`; maintain a `regression_seeds.txt` of seeds that produced findings
for re-runs after fixes.
*Milestone: a finding from a nightly run gets fixed and verified by replaying its seed.*

## Testing

- Unit tests (`tests/test_autoplay.py`): command validation, agent-output parsing and
  fallback chain, loop detector, each invariant check fed synthetic `ActionResult`s.
- Integration smoke: one short episode with `--provider mock` and a **stub agent** (a fake
  LLM returning a fixed command script) so CI exercises the full episode loop with no
  Ollama dependency.
- `python -m wildmagic.smoke_test` must stay green; the harness adds no imports to game
  modules (autoplay imports the game, never the reverse).

## Non-goals

- The agent does not modify code, delete logs, or write outside `logs/autoplay/`.
- No fun/balance verdicts from the agent are reported as facts — tier 3 is always labeled.
- No graphical UI testing; CLI surface only.
- No multi-process parallelism in v1 (one GPU, sequential turns; parallel episodes would
  thrash the model cache for little gain).
