# Emergent World — playtest plan

How to exercise the Phase 0–F systems (deeds → legend → standing → daily Simulator →
backlash + consequence props + bonds/followers). Two tracks: hands-on CLI, then an
overnight Qwen3.5 auto-playtest. See `EMERGENT_WORLD_PRIMER.md` for what the systems are.

## Key facts that shape testing
- **The Simulator runs once per in-game day at 05:00.** A day is `TURNS_PER_DAY = 1440`
  turns, so in normal play the daily tick (pressure depletion, backlash, bond drift)
  **almost never fires unless you `rest`/`camp`** (which jumps ~8h, crossing 05:00) or use
  the debug `tick`. *This is the single most important thing to make the autoplayer do.*
- **Best scenarios:** `empire_compound` (imperial enemies → kill deeds → standing/legend/
  backlash), `frontier` (zone crossings → consequence renderer + backlash arrival),
  `bazaar`/`archive` (NPCs present → bonds/followers).
- **Deterministic vs LLM:** the spine is deterministic (`--provider mock` exercises it
  fully). The **deed interpreter** (ambiguous spell outcomes) only runs under
  `--provider auto/ollama` with `WILDMAGIC_DEEDS_PROVIDER` not `off`.

## Track A — hands-on CLI (deterministic, fast, run first)

Use `python -m wildmagic.cli --scenario <s> --provider mock --no-render --command "..."`
(repeat `--command`), or pipe a script. Self-check signals via the `standing` / `followers`
readouts and the log lines.

1. **Deeds → standing → legend → kill-emperor gate** (`empire_compound`): move into imperial
   enemies to kill them; `standing` (expect `imperial_threat`/`fear` up on the Empire,
   `gratitude`/`legitimacy` on the resistance, a `defiant` legend); `rest until dawn` a few
   times; `standing` again (Empire **defenses** should tick down toward "within reach").
2. **Backlash** (`empire_compound` → then move to a fresh room/zone): after threat ≥ ~1.0,
   `rest until dawn` to let the Empire spend a patrol; on entering a zone expect "An
   Imperial patrol has tracked you here" + an `Imperial enforcer`. High resistance gratitude
   → a "sworn sympathizer" ally instead.
3. **Consequence renderer** (`frontier`): kill an imperial in a zone, cross a map edge and
   come back → expect a `bloodstained ground` prop + an `Imperial wanted poster` (and a
   "Word on the road" rumor). Confirm they are **not** regenerated/overwritten each move
   (the bug we just fixed).
4. **Deed interpreter** (`--provider auto`, Ollama up): `cast raise the dead to walk`, then
   `tick`, then `standing` → expect an `uncanny` legend + a `raised_dead` deed. Try
   `cast bring the tower down in rubble` (razed_building). Ordinary spells must record no
   deed.
5. **Bonds / followers / orgs** (`bazaar`/`archive`, has NPCs): `found the Ashen Hand`;
   build a pro-people legend; `rest until dawn` repeatedly; `followers` → expect an NPC to
   come to follow you and (if a believer) pledge to the org. (Natural legend-building is
   slow; this one is most convincing via a longer session.)
6. **Replay safety:** add `--record logs/pt.json` to any of the above, then
   `python -m wildmagic.replay logs/pt.json` → "Final summary matched: True".

Acceptance: each system shows its signal; no tracebacks; replay matches.

## Track B — overnight Qwen3.5 auto-playtest

**Prep — DONE 2026-06-14 (`wildmagic/autoplay.py`).** The autoplayer now knows the emergent
verbs and can reach the systems:
- `standing`/`followers`/`tick` (+ aliases) are in `EXACT_VERBS`; `rest`/`camp`/`sleep` and
  `found`/`establish` are in `TAIL_VERBS` (optional tail). `COMMAND_SURFACE` documents them and
  a coverage-goal paragraph tells the agent to fight imperials, periodically `rest until dawn`
  so the world reacts, read `standing`/`followers`, and `found` an org once notable.
- **Episode budget is now agent *steps* + wall-clock, not the in-game turn counter.** A
  `rest until dawn` advances the turn counter by a full day (`TURNS_PER_DAY`), which would
  have ended an episode on the first rest under the old turn-based cap. `--max-turns` is now
  the per-episode *step* budget (1 step ≈ 1 turn for ordinary play); `--max-steps` overrides.
- **Invariant fix:** `rest`/`investigate` are exempt from the Tier-1 `turn_counter_jump`
  finding (a big legitimate jump is not a bug) — without this, every rest would log a false
  confirmed bug. Backward counters and other jumps are still caught. Regression test added.

**Invocation (auto, tuned for throughput — tune hours/scenarios to the box):**
```
python -m wildmagic.autoplay --agent ollama --provider auto --hours 8 \
  --scenario empire_compound --scenario frontier --scenario bazaar --scenario archive \
  --max-turns 100 --episode-minutes 30 \
  --run-id emergent_overnight
```
- `--agent ollama` = Qwen3.5 chooses commands; `--provider auto` = real wild-magic + deed
  interpreter + canon (tests the LLM paths). Use `--provider mock` for a fast deterministic
  spine-only soak at much higher volume.
- `--max-turns 100` = ~100 agent decisions/episode. With `rest`s interspersed, that crosses
  several in-game days so the daily Simulator (pressure/backlash/bonds) actually fires.
- `--episode-minutes 30` is a wall-clock safety cap (the agent call has a 300s×2 timeout, so a
  single stuck step can be slow; this bounds the damage).
- **Omit `--drain-background` under `auto`** — it makes canon/prop background work synchronous
  (the ~15s+/cast hitch). Wild-magic + deed-interpreter are synchronous to the cast regardless,
  so the LLM paths you care about are still exercised; background canon just runs async.
- Ollama hosts already resolve to `127.0.0.1` (no localhost/IPv6 stall); num_gpu 999 (A750).

**What to review** (under `logs/autoplay/emergent_overnight/`): the Markdown report +
per-step JSONL. Grep the logs for evidence each system fired and stayed sane:
- deeds/standing: `imperial_threat`, `defiant`, `Word on the road`, `defenses`
- backlash: `Imperial patrol has tracked`, `sworn sympathizer`, faction mood (`alarmed`,
  `rising`)
- consequence renderer: `bloodstained`, `wanted poster`
- bonds/orgs: `has come to follow you`, `pledges to`, `can no longer walk`
- the invariant checker's findings (confirmed bugs vs. agent leads), any tracebacks,
  and per-move latency (watch for the prop-gen/deed-interpreter Ollama hitch).

Acceptance: systems show up in the logs across episodes, the invariant checker reports no
new confirmed bugs, and nothing crashes or runs away (e.g., unbounded backlash, followers
flapping, standing exploding).
