# Wild Magic

Wild Magic is a graphical ASCII roguelike about unstable, player-authored magic. You
can cast any spell; wild spells are typed in plain English and resolved through a
LLM that interprets the spell and returns a structured JSON contract.

The design rule is simple:

> The LLM may propose magical consequences, but the engine remains authoritative.

The engine validates every resolution, applies costs transactionally, owns world state,
and keeps replays deterministic. The model supplies interpretation, prose, and weirdness;
it does not get to silently mutate the game.

## What You Can Do

- Type almost any spell idea, from "bind the nearest enemy in blue webbing" to "somewhere
  north, a blade waits with my name on it."
- Talk to NPCs whose memories, traits, and local knowledge shape their replies.
- Let rumors and promises become real places, quests, threats, or discoveries in later
  zones.
- Build a character from origin, stats, appearance, backstory, and magical signature.
- Equip gear, mark one equipped item as a spell focus, and let it color future wild magic.
- Read books, investigate rooms, find secrets, browse wares, possess bodies, found
  organizations, gain followers, and watch factions react to your deeds.
- Play through the Pygame UI or the fully scriptable CLI. Both use the same action layer.

## Quick Start

Wild Magic uses Python 3.12+ and [uv](https://github.com/astral-sh/uv). For real
wild-magic resolution, install [Ollama](https://ollama.com/) and pull the default models:

```powershell
ollama pull qwen3.5:9b-q4_K_M
ollama pull qwen3:1.7b
```

Create local configuration:

```powershell
cp .env.example .env
```

The project `.env` is the persisted local config file. Shell environment variables still
win when the process starts, and in-game settings changes write back to `.env`.

Install dependencies and launch the graphical game:

```powershell
uv sync
uv run python main.py
```

Start with AI watch mode already enabled:

```powershell
uv run python main.py --autoplay
```

Run without a local LLM by using the deterministic mock provider:

```powershell
$env:WILDMAGIC_PROVIDER='mock'
uv run python main.py
```

## Headless Play

The CLI is the same game through a scriptable interface. It is the best way to reproduce
bugs, run smoke tests, and save replays.

```powershell
uv run python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --quickstart
```

Script a short run:

```powershell
uv run python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --quickstart --no-render `
  --command "inspect" `
  --command "move east" `
  --command "cast turn the goblin teeth to glass and make them fall out" `
  --command "cast summon a friendly brass moth that bites enemies" `
  --command "inspect"
```

Record and replay:

```powershell
uv run python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --record runs/test.json --command "move east" --command "cast ignite the goblin"
uv run python -m wildmagic.replay runs/test.json
```

## Character Creation

New interactive games start with character creation. Pick a ready-made origin, customize
stats and free-form details, or press Enter for a random wild mage. The current stats are:

- `Vigor`: HP and physical staying power.
- `Attunement`: mana and the magnitude anchors sent to the resolver.
- `Composure`: how cleanly or chaotically wild magic tends to answer.

Name, gender, appearance, backstory, and magical signature are stored on the character.
The message log stays second-person, while NPCs and future world systems can refer to the
external identity you chose.

## Controls

- Type a wild spell in the right panel and press `Enter`.
- Press `Tab` to cycle input modes. Talk mode appears when an NPC is nearby.
- Move with arrow keys, WASD, or the keypad. Keypad corners move diagonally.
- Click a map square, or use `target <x> <y>`, to mark an explicit spell target.
- Press `f` for a safe spark bolt. In the CLI, `spark`, `frost`, `heal`, `ward`, and
  `reveal` are deterministic standard spells; `cast frost` asks wild magic to improvise.
- Press `j` for the journal, `q` for quests, `i` for inventory, and `c` for the character
  sheet.
- Press `x` to investigate, `e` to examine, `r` to read, `u` to free a captive, `g` to
  pick up items, and `o` to open a nearby door.
- Press `z` to rest, `b` to browse nearby wares, `p` to possess a nearby body, `l` to
  inspect, `t` for standing, `n` for followers, and `h` for command help.
- Press `>` to descend stairs, `<` to ascend stairs, and `.` to wait.
- Press `F8` to start or stop AI watch mode, `F9` to pause it, and `F10` to step it once.
- Press `Esc` to clear input, close screens, clear a target, or quit depending on context.

Inventory supports equipping, unequipping, using, dropping, and marking a focus. In the UI,
select equipped gear and press `F` to toggle it as your spell focus. In the CLI, use
`focus <item-or-slot>` and `unfocus`.

## Useful CLI Commands

- `inspect`, `look`, or `status`: show the current state.
- `cast <spell>`: send a wild spell through the resolver.
- `target <x> <y>` / `untarget`: mark or clear an explicit spell target.
- `talk <message>`, `say <message>`, or `speak <message>`: talk to an adjacent NPC.
- `journal`, `rumors`, or `promises`: review persistent world memory.
- `standing`, `reputation`, or `factions`: show how powers regard you.
- `followers`, `retinue`, or `bonds`: show followers and founded organizations.
- `found <name>`: raise a new organization.
- `rest`, `rest 4`, or `rest until dawn`: pass time and let daily world simulation run.
- `examine`, `investigate [target]`, and `read [book]`: materialize room, secret, and book
  details.
- `wares`, `browse`, `accept`, and `reject`: trade with nearby merchants.
- `pickup`, `drop <item>`, `use <item>`, `equip <item>`, `unequip <slot-or-item>`.
- `focus <item-or-slot>` / `unfocus`: mark or clear an equipped spell focus.

## Wild Magic Contract

The LLM receives compact game context and must return one JSON object. The engine then
normalizes, validates, and applies it. Technical failures such as invalid JSON do not
consume a turn. Intentional rejections for invalid or overpowered spells do consume a turn.

The current operation surface includes direct and area damage, healing, mana restoration,
forced movement, terrain changes, statuses, summoning, template-backed item and creature
creation, inventory changes, transformations, faction changes, tags, resistances, world
flags, delayed events, flow fields, triggers, persistent effects, promises, possession,
memory edits, traits, curses, and messages.

Status effects support flavor names through `display_name` and `expiry_text`, so
"petrified", "crystallized", and "time-locked" can all ride the same engine-owned frozen
mechanic. Environmental interactions include fire and water making mist, water
extinguishing burning entities, vines snaring entrants, slick ice sliding movement, frost
freezing water-soaked entities, and fire cauterizing bleeding wounds.

See [docs/WILD_MAGIC_SCHEMA.md](docs/WILD_MAGIC_SCHEMA.md) for the generated operation
reference.

## NPCs, Promises, And The World

NPC dialogue uses the same discipline as wild magic: the engine controls turn cost,
visibility, memory, and formatting; the LLM only supplies speech. Dialogue can also leave
behind `WorldPromise` entries. A promise may remain rumor, bind to a future zone, realize
as a site or quest objective, or become part of later dialogue context.

The promise ledger is also used by prophecy-style wild magic. A spell can create an
engine-owned obligation such as "a blade waits north of here"; the engine decides whether
that is concrete enough to bind, where it can safely appear, what it costs, and how it is
shown in the journal.

Deeds feed the emergent-world layer: factions track multidimensional standing, NPC bonds
can drift, daily rest can advance off-screen consequences, and the UI/CLI expose the
results through standing and followers views.

## Providers And Configuration

Provider choices:

- `ollama`: real local LLM resolution. This is the default.
- `mock`: deterministic fake provider for tests, replays, and engine work.
- `auto`: try Ollama first, then use mock fallback when fallbacks are enabled.

Common variables:

```powershell
$env:WILDMAGIC_PROVIDER='ollama'
$env:WILDMAGIC_MODEL='qwen3.5:9b-q4_K_M'
$env:WILDMAGIC_LORE_MODEL='qwen3:1.7b'
$env:WILDMAGIC_OLLAMA_TIMEOUT='300'
```

Use `127.0.0.1` rather than `localhost` for Ollama hosts, especially on Windows. The
default is `http://127.0.0.1:11434`.

Strict LLM-contract testing disables local fallback paths:

```powershell
$env:WILDMAGIC_ENABLE_FALLBACKS='0'
```

For multi-model routing, background lore/town/canon work, Intel Arc setup, schema decoding,
timeouts, and troubleshooting, see [docs/MODEL_CONFIG.md](docs/MODEL_CONFIG.md).

## Audit Logs

Live model calls write JSONL audit records under `logs/`:

- `logs/wild_magic_audit.jsonl`: spell resolution.
- `logs/dialogue_audit.jsonl`: NPC dialogue.
- `logs/lore_audit.jsonl`: promise extraction and lore work.
- `logs/flesh_audit.jsonl`: promise decoration.
- `logs/canon_audit.jsonl`: examine/read materialization.

Each record includes the prompt/context, provider/model, raw response, normalized result,
and any error. Control logging with:

```powershell
$env:WILDMAGIC_AUDIT_DIR='logs'
$env:WILDMAGIC_AUDIT_LOG='0'
```

## Optional Character Portraits

Portrait generation is experimental and intentionally isolated from the main game process.
It uses a separate SDXL environment and worker process so the core game never imports
`torch`.

To enable it, create the portrait environment, set `WILDMAGIC_PORTRAIT_PYTHON` to that
environment's Python executable, and set:

```powershell
$env:WILDMAGIC_PORTRAIT_ENABLED='1'
```

See [tools/portraits/README.md](tools/portraits/README.md) and the portrait section of
[docs/MODEL_CONFIG.md](docs/MODEL_CONFIG.md).

## Development Checks

Run the smoke test:

```powershell
uv run python -m wildmagic.smoke_test
```

Run the deterministic test suite:

```powershell
uv run python -m pytest -q
```

Compile check:

```powershell
Get-ChildItem wildmagic -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }; python -m py_compile main.py
```

## Further Reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): module map and system boundaries.
- [docs/AGENT_PLAYTESTING.md](docs/AGENT_PLAYTESTING.md): CLI playtesting, audit logs,
  replays, and reporting.
- [docs/MODEL_CONFIG.md](docs/MODEL_CONFIG.md): all provider, model, routing, and hardware
  configuration.
- [docs/WILD_MAGIC_SCHEMA.md](docs/WILD_MAGIC_SCHEMA.md): wild-magic operation surface.
- [docs/EMERGENT_WORLD_STRATEGY.md](docs/EMERGENT_WORLD_STRATEGY.md): player-driven world
  simulation direction.
- [docs/AESTHETICS_AND_TONE.md](docs/AESTHETICS_AND_TONE.md): tone and content north star.
