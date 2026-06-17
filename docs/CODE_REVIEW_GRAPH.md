# Code knowledge graph (code-review-graph)

This repo has a structural **knowledge graph** of its own source, built by
[`tirth8205/code-review-graph`](https://github.com/tirth8205/code-review-graph)
(third-party, MIT, **local-first** — the graph never leaves your machine). It parses the
tracked Python with Tree-sitter into nodes (files, classes, functions, tests) and edges
(calls, imports, inheritance, test coverage), stores them in a local SQLite db, and exposes
them to Claude Code over MCP so an agent can ask "who calls this?", "what's the blast radius
of changing X?", "what tests cover Y?" and get a small structural answer instead of reading
whole files.

It is **installed and live** on this machine. This document is the operational reference:
what's wired up, how to use it, and how to keep it fresh.

## Current state

- **Graph:** ~98 files → ~1,663 nodes / ~16,083 edges (1,108 functions, 337 tests, 120
  classes, 98 files). Stored at `.code-review-graph\graph.db` (gitignored).
- **Embeddings:** 1,565 nodes embedded with the local `all-MiniLM-L6-v2` model, so
  `semantic_search_nodes` runs **hybrid** search (BM25 + vectors). The 98 `File` nodes are
  not embedded; functions/classes/tests are.
- **MCP server:** registered in `.mcp.json`, launches on Claude Code start, verified
  responding (stats + hybrid semantic queries).
- **Auto-update:** a Claude Code hook refreshes the graph after edits (see *Keeping it
  fresh*), so it tracks the working tree without manual rebuilds.

## Using it from Claude Code (the point of all this)

When the MCP server is loaded, **prefer the graph tools for structural questions** — they're
cheaper (fewer tokens) and more accurate than scanning files for these jobs:

| Need | Tool |
|---|---|
| Blast radius of changing a symbol | `get_impact_radius`, `get_affected_flows` |
| Who calls / is called by / imports / tests a symbol | `query_graph` (`callers_of` / `callees_of` / `imports_of` / `tests_for`) |
| Review the working-tree diff | `detect_changes` + `get_review_context` |
| High-level structure / subsystems | `get_architecture_overview`, `list_communities`, `get_hub_nodes` |
| Find a function/class by name or concept | `semantic_search_nodes` |
| Plan a rename / find dead code | `refactor_tool` |

Use **Grep/Glob/Read** for content search, exact-string matches, and reading specific files
— they remain the right tool there. Treat `semantic_search_nodes` as a ranked hint, not
ground truth (local semantic recall is modest). **If the graph and the source disagree,
trust the source** — this matches the project rule of using structural analysis as evidence
to verify against source, not as infallible truth.

There are also four Claude Code **skills** installed under `.claude\skills\`:
`explore-codebase`, `debug-issue`, `refactor-safely`, `review-changes`.

## How it's wired (files this added to the repo)

| File | Purpose |
|---|---|
| `.mcp.json` | Registers the MCP server. **Points at the system install**, not `uvx` (see gotcha below), with `PYTHONUTF8=1` for Windows. |
| `.claude/settings.json` | `PostToolUse` hook runs `code-review-graph update --skip-flows` after each Edit/Write/Bash; `SessionStart` prints `status`. |
| `.claude/skills/` | The four skills listed above. |
| `.gitignore` | Ignores `.code-review-graph/` and exported `*.crg.*` artifacts. |
| `.git/hooks/pre-commit` | Has an appended CRG block that is **dead code** — the existing pre-commit-framework hook `exec`s first, so the append never runs. Harmless; ignore or strip it. |

`.mcp.json` deliberately runs the on-PATH executable
(`...\Python312\Scripts\code-review-graph.exe`) rather than `uvx code-review-graph` so the
server shares the Python environment that has `sentence-transformers` installed.

## Keeping it fresh

The `PostToolUse` hook in `.claude/settings.json` runs an incremental `update` after every
file-mutating tool call, so during a Claude Code session the graph stays current
automatically. The trade-off is a small (~1-2s, non-blocking) latency added to each
Edit/Write/Bash. To disable it, delete the `PostToolUse` block from `.claude/settings.json`.

Manual commands (run from the repo root):

```powershell
code-review-graph status      # node/edge counts, languages, last update, branch/commit
code-review-graph update      # incremental refresh after edits
code-review-graph build       # full rebuild from scratch
code-review-graph embed       # recompute vector embeddings (needs the embeddings extra)
```

### Semantic search setup (already done, for reference)

Embeddings need the extra and a one-time embed pass:

```powershell
pip install "code-review-graph[embeddings]"   # pulls sentence-transformers + torch
$env:PYTHONUTF8='1'; code-review-graph embed   # first run downloads all-MiniLM-L6-v2 (~90MB)
```

Re-run `embed` after a large `build`/`update` if you want new nodes covered. The embedding
model is `all-MiniLM-L6-v2` (local, CPU); override with `CRG_EMBEDDING_MODEL`.

## Visualizations

```powershell
code-review-graph visualize                  # interactive D3 HTML (search, community toggles)
code-review-graph visualize --format svg     # static image for a doc
code-review-graph visualize --format graphml # open in Gephi / yEd
```

Outputs land under `.code-review-graph\` (e.g. `graph.html`, `graph.svg`) and are gitignored.
Open `.code-review-graph\graph.html` in a browser to eyeball the module structure (e.g.
`ui -> actions -> canon/engine` and where the hubs are).

## Limitations to keep in mind

- **Verify against source.** Call/impact edges are only as good as the parser. The
  `[enrichment]` (Jedi) extra improves Python call resolution and is installed, but treat
  impact analysis as indicative.
- **Semantic search is modest** (local MiniLM; right answer usually in the top few, not
  always #1). Lean on the deterministic `query_graph` relationship queries over fuzzy search.
- **Small single-file changes can cost *more* tokens** than just reading the file — the win
  is on larger reviews and tracing relationships across modules.
- **Impact "recall" is partly circular** (ground truth comes from the same graph edges), so
  don't read the tool's own accuracy numbers as proof.

## Gotcha we hit (so it isn't rediscovered)

The installer's default `.mcp.json` ran the server via `uvx code-review-graph serve`. `uvx`
uses an **isolated environment without `sentence-transformers`**, so the server couldn't
embed query text and `semantic_search_nodes` silently fell back to keyword-only
(`search_mode: keyword`, often 0 results for natural-language queries). Fix: point `.mcp.json`
at the system install (which has the embeddings extra) and restart Claude Code. After that,
semantic queries return `search_mode: hybrid`. If semantic search ever regresses to keyword
mode, check that the server's Python environment can `import sentence_transformers`.

On Windows the MCP server also wants a UTF-8 environment (`PYTHONUTF8=1`, set in `.mcp.json`)
or non-ASCII output can break the connection — the same mojibake class of issue this repo
hits elsewhere.

---

### Sources
- [tirth8205/code-review-graph (GitHub)](https://github.com/tirth8205/code-review-graph)
- [README.md](https://github.com/tirth8205/code-review-graph/blob/main/README.md)
- [Quick Start Guide (DeepWiki)](https://deepwiki.com/tirth8205/code-review-graph/2.2-quick-start-guide)
