# AGENTS.md

This project is a graphical ASCII roguelike about wild magic. The player can type almost any spell idea, and a local LLM resolves it into structured JSON. The engine validates that JSON and applies it to the dungeon.

The core design rule:

> The LLM may propose magical consequences, but the engine remains authoritative.

Your job as an agent is to help the game become a broader, more reliable simulation where strange prompts can be expressed through reusable mechanics.

## Project Strategy

Wild Magic should handle arbitrary inputs gracefully by growing general systems:

- Prefer reusable operations over prompt-specific behavior.
- Prefer normalization and schema repair over hard-coded fallbacks.
- Prefer re-skinning existing mechanics with flavor names over inventing new mechanics for every spell.
- Prefer rich state plus small composable effects: damage, status, terrain, movement, summoning, faction changes, tags, inventory, curses, flags, delayed events.
- Keep the deterministic engine in charge of legality, bounds, turn cost, state validation, and logging.
- Make every feature playable through the headless CLI so agents can test it without manual UI work.

## Design Principles

- Stable world, unstable magic: persist consequences, relationships, and world facts without making future spell outcomes deterministic.
- The engine owns truth. LLM output is an untrusted proposal until it is normalized, validated, and applied transactionally.
- Prefer one authoritative representation for each concern. Configuration, schemas, mechanics, and persistent state should not have competing sources of truth.
- Keep configuration user-controlled. Load local defaults from `.env`, preserve explicit shell-environment overrides, and route all consumers through one shared configuration API.
- Separate durable world memory from magical generation. Retrieval may supply lore, history, reputation, and NPC knowledge, but must not recycle previous spell resolutions as templates.
- Favor explicit data flow and observable boundaries over hidden global state, duplicated defaults, dynamic dispatch, or silent fallback behavior.
- Add tests at architectural boundaries. Tests should enforce contracts such as turn consumption, transaction rollback, configuration precedence, replayability, and provider consistency.
- Use `pyproject.toml` as the authoritative Python project and dependency metadata file, following the applicable Python packaging standards. Keep it current when dependencies, supported Python versions, build configuration, or development tooling change.
- `uv` is a supported dependency-management and execution workflow for this repository. Keep `uv.lock` current when using it, while avoiding parallel dependency manifests or undocumented installation paths.
- Inspect impact, coupling, affected execution paths, and test coverage before and after architectural changes. Use available structural-analysis tools as evidence to verify against source, not as infallible truth.

Examples of good general work:

- Add a `display_name` to statuses so "petrified", "crystallized", and "time-locked" can all use `frozen` mechanics.
- Add group targeting to an existing effect, such as `push` affecting `all_enemies`.
- Add a template-backed creature/item path that lets the LLM create "glass teeth", "brass moth", or "ant army" without bespoke classes.
- Improve JSON normalization for common LLM shape mistakes, such as `effect` vs `effects`, nested `details`, or target aliases.

Examples to avoid:

- A one-off handler for exactly "turn the goblin's teeth to glass".
- A hidden fallback spell engine inside the main resolver.
- UI-only behavior that cannot be tested through `wildmagic.cli`.
- Letting malformed LLM output partially mutate game state.

## Important Files

- `main.py`: graphical entry point.
- `wildmagic/engine.py`: authoritative game rules and state mutation.
- `wildmagic/models.py`: shared data classes (`Entity`, `NPCProfile`, etc.) used by engine and state.
- `wildmagic/actions.py`: shared action/session layer used by UI, CLI, tests, and replays.
- `wildmagic/config.py`: `.env` loading, defaults, typed settings, provider/model fallback chains, and persisted configuration updates.
- `wildmagic/wild_magic.py`: LLM prompt, provider calls, JSON parsing, normalization, validation, audit logging.
- `wildmagic/fallbacks.py`: quarantined replacement-resolution fallbacks. Keep this isolated and optional.
- `wildmagic/templates.py`: template-backed arbitrary item and creature creation.
- `wildmagic/ui.py`: Pygame renderer and input handling.
- `wildmagic/cli.py`: headless agent-playable interface.
- `wildmagic/replay.py`: deterministic replay runner.
- `docs/ARCHITECTURE.md`: full map of every module — what it contains, what it imports, and how the layers fit together. Read this first when orienting to the codebase.
- `docs/WILD_MAGIC_SCHEMA.md`: current wild-magic operation surface.
- `docs/AGENT_PLAYTESTING.md`: practical playtesting guide.
- `docs/EXECUTION_PLAN.md`: staged project plan.
- `docs/AESTHETICS_AND_TONE.md`: North star document for content creation aesthetics.

## How To Run

Graphical game:

```powershell
python main.py
```

Smoke test:

```powershell
python -m wildmagic.smoke_test
```

Headless deterministic play:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7
```

Scripted headless play:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --no-render `
  --command "inspect" `
  --command "move east" `
  --command "cast turn the goblin teeth to glass and make them fall out" `
  --command "cast an army of ants crawls out of the walls" `
  --command "inspect"
```

Local LLM play:

```powershell
$env:WILDMAGIC_PROVIDER='ollama'
$env:WILDMAGIC_MODEL='qwen3:8b'
$env:WILDMAGIC_OLLAMA_TIMEOUT='240'
python -m wildmagic.cli --provider ollama --scenario test_chamber --seed 7
```

Compile check:

```powershell
Get-ChildItem wildmagic -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }; python -m py_compile main.py
```

## Wild Magic Contract

The LLM should return exactly one JSON object. Technical failures, such as invalid JSON, should not consume a turn. Intentional rejections for overpowered or invalid spells should consume a turn.

When improving the LLM path:

- First improve the prompt/schema if the model misunderstands available operations.
- Then improve normalization if the response is semantically usable but shaped wrong.
- Only add a fallback when playtesting absolutely needs a local replacement resolution.
- Put all replacement-resolution fallbacks in `wildmagic/fallbacks.py`.
- Respect `WILDMAGIC_ENABLE_FALLBACKS=0`, which disables fallback paths for strict LLM-contract testing.

Audit logs live at:

```powershell
logs/wild_magic_audit.jsonl
```

Use audit logs for postmortems. They include prompt messages, compact game context, raw LLM response, parsed resolution, and errors.

## Provider Modes

- `ollama`: real local LLM resolver. Best for evaluating core wild-magic behavior.
- `mock`: deterministic fake resolver. Best for engine, UI, replay, and regression tests.
- `auto`: tries Ollama and may fall back to mock when fallbacks are enabled. Avoid for strict LLM evaluation because it can hide provider failures.

Mock resolver log lines are marked with `*>`. LLM resolver log lines are marked with `>`.

For strict LLM-contract testing:

```powershell
$env:WILDMAGIC_ENABLE_FALLBACKS='0'
```

## Development Guidelines

Read before editing. This repo may have uncommitted work from another agent or the user. Do not revert changes you did not make.

Keep changes scoped and testable:

- Put core behavior in the engine or action layer, not only in the UI.
- Add or adjust CLI/scripted coverage when changing gameplay.
- Preserve replayability where possible.
- Keep wild-magic effects transactional: validate, normalize, apply effects, apply costs, advance turn, log.
- Make costs visible after casting, not before, except for severe warning behavior explicitly supported by the design.
- Do not let technical LLM failures consume a turn.
- Do not let rejected overpowered spells avoid turn cost.

Keep durable repository language independent of temporary planning context:

- Do not mention local working notes, temporary planning documents, deleted roadmaps, or private agent context in code comments, tests, commit messages, pull-request titles/descriptions, changelogs, or durable documentation.
- Do not identify work as "Phase N", "Step N", "PR N", or similar unless that identifier belongs to a permanent, published project structure that will remain available to future contributors.
- Name branches, commits, tests, modules, and documentation after the technical problem or behavior they address.
- Explain decisions using durable architectural reasons, constraints, and behavior. Future readers must be able to understand the text using only the repository and its retained history.
- Before committing or publishing, search changed durable files for temporary planning terminology and rewrite it in problem-domain language.

Keep `docs/ARCHITECTURE.md` current. Update it whenever you add a new module, move code between files, rename a significant class, or introduce a new subsystem. The blurb for each file should reflect what actually lives there after your change, not what used to live there.

When adding mechanics, ask: "Can this support ten weird spell prompts, not just one?"

Good broad additions include:

- New status mechanics with flavor aliases.
- New target selectors or group targeting.
- New terrain interactions.
- New template fields for generated items/creatures.
- New reusable effect types with strict validation.
- Better save/replay/audit information.
- Better CLI inspection output.

## Playtesting Expectations

Actually play the game after changing it. Use both ordinary roguelike actions and wild spells.

Recommended loop:

1. Run `python -m wildmagic.smoke_test`.
2. Play or script a short mock-provider run.
3. Play or script a short Ollama run if the change touches wild magic.
4. Inspect `logs/wild_magic_audit.jsonl` for LLM failures.
5. Fix general causes rather than adding narrow cases.
6. Re-run smoke and compile checks.

Try creative prompts:

- `cast bind the nearest enemy in sticky blue webbing`
- `cast the goblin's teeth turn to glass and fall out`
- `cast summon a friendly brass moth that bites enemies`
- `cast turn the floor between me and the enemy into slick ice`
- `cast reveal the nearest creature by making its shadow glow blue`
- `cast an army of ants crawls out of the walls`
- `cast in three turns a debt collector arrives because I stole tomorrow`

When reporting results, include what you ran, what changed, tests performed, and any remaining risk.
