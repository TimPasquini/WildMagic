# Agent Playtesting Guide

This guide is for AI agents playtesting Wild Magic through the terminal. The goal is to make the game better by actually playing it, finding rough spots, and reporting or fixing issues with enough evidence that another agent can reproduce them.

Wild Magic is designed to be agent-playable. Prefer the CLI and replay tools over the graphical UI for debugging because they are deterministic, scriptable, and easy to inspect.

## Quick Start

From the project root:

```powershell
python -m wildmagic.smoke_test
```

Run a deterministic mock playtest:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7
```

Run the trim pytest suite:

```powershell
python -m pytest -q
```

The default pytest run is the fast, deterministic trim suite. It skips tests marked
`@pytest.mark.full` and forces background town generation to the mock provider unless
you explicitly request the full suite. Use this for normal development checks.

Run the full pytest suite:

```powershell
python -m pytest -q --full
```

Full mode includes `full`-marked tests and respects provider configuration from the
environment or `.env`, so it may wait on local LLM services.

Run a scripted mock playtest:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --no-render `
  --command "inspect" `
  --command "move east" `
  --command "cast the goblin teeth turn to glass and fall out" `
  --command "cast an army of ants crawls out of the walls" `
  --command "inspect"
```

Run with the local LLM resolver:

```powershell
$env:WILDMAGIC_PROVIDER='ollama'
$env:WILDMAGIC_MODEL='qwen3:8b'
$env:WILDMAGIC_OLLAMA_TIMEOUT='240'
$env:WILDMAGIC_OLLAMA_NUM_PREDICT='512'
$env:WILDMAGIC_OLLAMA_TEMPERATURE='0.25'
python -m wildmagic.cli --provider ollama --scenario test_chamber --seed 7
```

**GPU note for Arc A750:** `qwen3:8b` running mostly on CPU produces garbage output (random tokens,
Chinese characters) for any prompt longer than a few dozen tokens. The game sets `num_gpu=999` in
every Ollama request by default, which forces full GPU offload and fixes this. If you see garbage or
empty spell results, check `ollama ps` — `PROCESSOR` should show `100% GPU`. You can override via
`$env:WILDMAGIC_OLLAMA_NUM_GPU='999'` (already the default).

On the Arc A750 test machine, start Ollama with Intel GPU support:

```powershell
$env:OLLAMA_HOST='127.0.0.1:11435'
$env:OLLAMA_INTEL_GPU='1'
$env:GGML_VK_VISIBLE_DEVICES='0'
ollama serve
```

Then, in another shell:

```powershell
$env:OLLAMA_HOST='http://127.0.0.1:11435'
$env:WILDMAGIC_MODEL='qwen3:8b'
python -m wildmagic.cli --provider ollama --scenario test_chamber --seed 7
```

After an LLM cast, check GPU usage:

```powershell
ollama ps
```

For `qwen3:8b`, `PROCESSOR` should ideally show `100% GPU`.

### Split Ollama Routing

Wild magic, dialogue, trade, and background town generation can use different Ollama endpoints. By default they all fall through to `WILDMAGIC_OLLAMA_HOST`, then `OLLAMA_HOST`, then `http://localhost:11434`.

For a two-server playtest, route urgent calls to the GPU server and background town generation to a CPU server:

```powershell
$env:WILDMAGIC_URGENT_OLLAMA_HOST='http://127.0.0.1:11434'
$env:WILDMAGIC_BACKGROUND_OLLAMA_HOST='http://127.0.0.1:11435'
$env:WILDMAGIC_BACKGROUND_OLLAMA_NUM_GPU='0'
```

Per-purpose overrides are also supported: `WILDMAGIC_WILD_OLLAMA_HOST`, `WILDMAGIC_AGENT_OLLAMA_HOST`, `WILDMAGIC_DIALOGUE_OLLAMA_HOST`, `WILDMAGIC_TRADE_OLLAMA_HOST`, and `WILDMAGIC_TOWN_OLLAMA_HOST`. The same scoped pattern works for `OLLAMA_NUM_CTX`, `OLLAMA_TIMEOUT`, `OLLAMA_NUM_GPU`, `OLLAMA_THINK`, `OLLAMA_FORMAT`, and `OLLAMA_KEEP_ALIVE`.

When you want strict control over manually started servers, set `WILDMAGIC_OLLAMA_AUTOSTART=0`.

## Unattended Playtesting

For long unattended runs, use the autoplay harness instead of driving the CLI by hand
(see `docs/AUTOPLAY_PLAN.md` for design and `python -m wildmagic.autoplay --help` for flags):

```powershell
$env:WILDMAGIC_CANON_PREWARM_ENABLED='0'
$env:WILDMAGIC_MODEL='qwen3.5:9b-q4_K_M'
$env:WILDMAGIC_AGENT_MODEL='qwen3.5:9b-q4_K_M'
python -m wildmagic.autoplay --hours 8 --provider ollama --agent ollama --episode-minutes 30
```

Use the same model for `WILDMAGIC_MODEL` and `WILDMAGIC_AGENT_MODEL` so the GPU never swaps
models between turns. Each run writes per-episode JSONL logs, replays, `findings.jsonl`,
`regression_seeds.txt`, and a `report.md` summary under `logs/autoplay/<run_id>/`.

## Visual AI Watch Mode

To watch the autonomous agent play through the same Pygame UI as a human player:

```powershell
python main.py --autoplay
```

You can also launch normally with `python main.py` and press `F8` to start or stop AI watch
mode. `F9` pauses or resumes the agent, and `F10` asks it to take exactly one step while
paused. The visual watcher uses the `agent` Ollama purpose (`WILDMAGIC_AGENT_MODEL`,
`WILDMAGIC_AGENT_OLLAMA_NUM_CTX`, and related scoped settings), builds the same compact
observation as the headless harness, and applies decisions through `GameSession.execute_command`.

## Command Surface

Useful CLI commands:

- `inspect`
- `move north`, `move south`, `move east`, `move west`
- `north`, `south`, `east`, `west`
- `wait` — spend a turn and recover 1 MP
- `open`
- `descend`
- `ascend`
- `spark`
- `cast <wild spell text>`
- `talk <message>` (or `speak`/`say`) — talk to an adjacent NPC; costs a turn
- `examine` (or `study`/`observe`) - materialize or reread room canon
- `quit`

Use `inspect` often. It prints turn, HP, MP, inventory, curses, flags, scheduled events, and enemy summaries.

## Providers

Use the provider that matches the playtest goal:

- `mock`: deterministic, fast, good for engine bugs and replay checks.
- `ollama`: real wild-magic behavior, good for prompt quality, reliability, balance, and weird spell coverage.
- `auto`: tries Ollama, falls back to mock. Avoid this for serious LLM evaluation because fallback can hide resolver failures.

Mock resolver log lines start with `*>`.

LLM resolver log lines start with `>`.

## Audit Logs

Every live LLM resolver call is logged to:

```powershell
logs/wild_magic_audit.jsonl
```

Each record includes:

- spell text
- provider and model
- prompt messages
- compact game-state context
- raw LLM response
- parsed resolution, if any
- technical failure or validation error

When reporting an LLM issue, include the spell text, the visible in-game result, and the relevant audit record or a summary of its `raw_response`, `parsed_resolution`, and `error`.

NPC dialogue (the `talk` command) has its own parallel log at `logs/dialogue_audit.jsonl`, with the NPC name, message, prompt/context, `raw_response`, cleaned `reply`, and any technical error. It's controlled by the same `WILDMAGIC_AUDIT_DIR`/`WILDMAGIC_AUDIT_LOG` settings, and the provider/model can be set independently with `WILDMAGIC_DIALOGUE_PROVIDER`/`WILDMAGIC_DIALOGUE_MODEL` (falling back to `WILDMAGIC_PROVIDER`/`WILDMAGIC_MODEL` if unset).

Materialized canon from `examine` and future richness features is logged to
`logs/canon_audit.jsonl`. Each record includes the seed packet, provider/model,
raw response, normalized `CanonRecord`, and any technical failure.

Background canon saturation is opt-in. To test it, set
`WILDMAGIC_CANON_PREWARM_ENABLED=1`; the current labeled room may receive `room_flavor`
canon, visible non-book entities may receive far-look detail canon, then nearby visible
books may receive title/author/summary preview canon before the player reads them.
Full book pages still come from `read`, and close-study details still come from
player investigation.

## Replays

Record a run:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --record runs/agent_test.json `
  --command "inspect" `
  --command "move east" `
  --command "cast ignite the goblin" `
  --command "inspect"
```

Verify the replay:

```powershell
python -m wildmagic.replay runs/agent_test.json
```

Use replays for deterministic mock-provider issues. Live LLM runs are logged through audit records instead, because the same prompt can produce different responses.

## Recommended Playtest Loop

1. Run `python -m wildmagic.smoke_test`.
2. Play 20-50 turns in `test_chamber` with `--provider mock`.
3. Play 20-50 turns in `test_chamber` with `--provider ollama`.
4. Play 50-150 turns in `dungeon` with `--provider ollama`.
5. Look for crashes, technical failures, confusing logs, bad targeting, impossible costs, and boring or unfair outcomes.
6. Fix focused issues immediately when the cause is clear.
7. Re-run smoke tests and a short scripted playtest.
8. Summarize what was tested, what changed, and what risk remains.

## Playtest Styles

### Cautious

Use normal roguelike tactics. Move carefully, use `spark`, pick defensible positions, avoid casting when low on HP.

Good for checking whether the base game is playable without abusing wild magic.

### Wild

Cast wild magic frequently, including spells that transform terrain, summon allies, conjure objects, and impose strange costs.

Good for checking the core premise.

Example spells:

- `cast bind the goblin in sticky blue webbing`
- `cast turn the goblin teeth to glass and make them fall out`
- `cast summon a friendly brass moth that bites enemies`
- `cast turn the floor between me and the nearest enemy into slick ice`
- `cast reveal the nearest creature by making its shadow glow blue`
- `cast make a tiny sun orbit me and burn nearby foes`
- `cast an army of ants crawls out of the walls`

### Stress

Try ambiguous, large, or dangerous spells. The goal is not to win; it is to test validation and failure behavior.

Example spells:

- `cast kill every enemy on this floor instantly`
- `cast make me immortal and give me infinite mana`
- `cast turn every wall in the dungeon into bees`
- `cast reverse gravity for all creatures except me`
- `cast replace the nearest enemy with a locked treasure chest full of teeth`

Outright overpowered spells may be rejected and should consume a turn. Technical failures, such as invalid JSON, should not consume a turn.

### Regression

Replay spells that previously failed or produced bad results.

Current useful regressions:

- Reveal spells should apply `revealed`, not randomly buff enemies.
- Slick ice should create `slick ice`, not blocking `ice wall`.
- Foe-only area spells should use `affects: "enemies"` and avoid allies.
- Body-part transformation spells should usually use `damage`, `add_status`, and `conjure_item`, not transform the whole creature unless requested.
- Unknown poetic status costs should become curses, not inert mechanical statuses.

## What Counts As A Bug

Treat these as bugs:

- Crash, traceback, frozen process, or unhandled exception.
- Technical LLM failure consumes a turn.
- Rejected overpowered spell does not consume a turn.
- Invalid JSON partially changes game state.
- Player or monsters move through blocking terrain without a clear effect.
- Blocking entities overlap after a spell.
- Summoned creatures appear inside walls and stay trapped.
- UI log or input overlaps.
- CLI and graphical UI disagree about visible state.
- Audit log omits a live LLM prompt, raw response, or error.

Treat these as likely design/balance issues:

- LLM chooses a cost that is legal but feels wildly disproportionate.
- A common spell produces boring narration or no useful effect.
- A spell targets an ally when the wording clearly said foe.
- The player cannot understand what happened from the message log.
- Wild magic repeatedly produces valid JSON but poor gameplay.

Treat these as acceptable wild-magic chaos:

- A spell has a dangerous side effect.
- A powerful spell gains a curse, max-stat loss, or hostile summon.
- An ambiguous spell does something surprising but mechanically coherent.
- A rejected spell consumes the turn because it was too powerful.

## Fixing Guidelines

When you find an issue:

1. Reproduce it with the shortest command sequence possible.
2. Decide whether the fix belongs in the engine, prompt, validation, UI, or docs.
3. Prefer engine rules over prompt-only fixes when the issue can corrupt or confuse state.
4. Prefer prompt examples when the engine already supports the desired behavior but the model chooses poorly.
5. Keep changes scoped.
6. Run `python -m wildmagic.smoke_test`.
7. Run `py_compile` on `main.py` and `wildmagic/*.py`.
8. Run one short CLI playtest that exercises the fix.

Useful compile check:

```powershell
Get-ChildItem wildmagic -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }; python -m py_compile main.py
```

## Reporting Format

Use this structure when handing results back to a human or another agent:

```text
Playtest setup:
- provider:
- model:
- scenario:
- seed:
- commands or policy:

Findings:
- issue:
- evidence:
- expected:
- actual:

Changes made:
- file:
- behavior:

Verification:
- smoke test:
- compile:
- live/mock playtest:

Remaining risk:
- ...
```

## Notes For Claude

You can play the game entirely through `python -m wildmagic.cli`. You do not need to open the Pygame window.

When using the LLM resolver, do not assume the resolver is deterministic. Use audit records for postmortems.

If you cannot run Ollama, use `--provider mock` and focus on engine, UI-independent mechanics, replay, and documentation. Clearly say that live LLM behavior was not tested.

If a spell fails technically, check whether the turn changed. Technical failures should preserve the turn. If the spell was intentionally rejected as too powerful, the turn should advance.

When making code changes, protect existing user work. Do not delete logs or replay files unless explicitly asked.
