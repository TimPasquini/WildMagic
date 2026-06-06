# Wild Magic

A tiny graphical ASCII roguelike prototype where normal actions are deterministic and wild spells are resolved through a structured local-LLM JSON contract.

## Run

```powershell
python main.py
```

The default resolver is `ollama`, so the game uses the local LLM path unless you explicitly choose another provider.

```powershell
$env:WILDMAGIC_PROVIDER='ollama'
$env:WILDMAGIC_MODEL='qwen3:8b'
python main.py
```

Use `WILDMAGIC_PROVIDER=mock` for repeatable debugging without an LLM.

Use `WILDMAGIC_PROVIDER=auto` if you want the old behavior: try Ollama first, then fall back to the deterministic mock resolver.

Wild spell log lines from the mock resolver are marked with `*>` instead of `>` so they are easy to distinguish from LLM-resolved spells.

## Wild Magic Audit Logs

Every live wild-magic resolver call writes a JSONL audit record to:

```powershell
logs/wild_magic_audit.jsonl
```

Each record includes the spell text, provider/model, full prompt messages, game-state context, raw response, parsed resolution, and any validation or technical error.

You can change or disable this with:

```powershell
$env:WILDMAGIC_AUDIT_DIR='logs'
$env:WILDMAGIC_AUDIT_LOG='0'
```

If local model responses time out, increase the Ollama request timeout:

```powershell
$env:WILDMAGIC_OLLAMA_TIMEOUT='300'
```

If Ollama returns a 404, check the installed model name:

```powershell
ollama list
$env:WILDMAGIC_MODEL='qwen3:8b'
```

The model name must match an installed Ollama tag, such as `qwen3:8b`, `qwen3.6`, or another local model from your list.

## Intel Arc GPU Notes

On the tested Arc A750 setup, `qwen3.6` loaded as mixed CPU/GPU because it is too large for the available VRAM. `qwen3:8b` fit fully on the GPU and is the recommended default.

To run Ollama on the Arc A750 through Vulkan, start the Ollama server with:

```powershell
$env:OLLAMA_VULKAN='1'
$env:GGML_VK_VISIBLE_DEVICES='0'
$env:WILDMAGIC_MODEL='qwen3:8b'
ollama serve
```

If the desktop Ollama app is already running on port `11434`, quit it before starting this server, or run the Vulkan server on another port and point the game at it:

```powershell
$env:OLLAMA_HOST='http://127.0.0.1:11435'
```

Use `ollama ps` after a spell cast. The `PROCESSOR` column should show `100% GPU` for `qwen3:8b`.

## Controls

- Type a wild spell in the right panel, then press `Enter`.
- Press `Tab` to leave the spell input and move around.
- Move with arrow keys, WASD, or vi keys.
- Press `f` for a safe spark bolt.
- Press `o` to open an adjacent closed door.
- Press `>` to descend stairs and `<` to ascend stairs.
- Press `.` to wait.
- Press `Esc` to clear the spell input or quit.
- The map uses field of view: unseen tiles are hidden, explored tiles outside sight are dimmed.

## Smoke Test

```powershell
python -m wildmagic.smoke_test
```

## Headless Play And Replays

Play from the terminal:

```powershell
$env:WILDMAGIC_PROVIDER='mock'
python -m wildmagic.cli --scenario test_chamber --seed 7
```

Run scripted commands and save a replay:

```powershell
python -m wildmagic.cli --provider mock --scenario test_chamber --seed 7 --record runs/test.json --command "move east" --command "cast ignite the goblin"
python -m wildmagic.replay runs/test.json
```

## Project Plan

See [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) for the staged feature plan, including the headless play harness needed for agent-driven testing.

See [docs/WILD_MAGIC_SCHEMA.md](docs/WILD_MAGIC_SCHEMA.md) for the current wild-magic JSON operation surface.

## Wild Magic Contract

The LLM receives a compact game-state summary and must return one JSON object. Valid spell failures caused by overpowered requests consume a turn. Technical failures, such as invalid JSON, do not.

The engine currently supports direct and area effects for damage, healing, mana restoration, forced movement, terrain changes, statuses, summoning, template-backed item/creature conjuration, item spawning, inventory changes, transformations, factions, tags, resistances, world flags, delayed events, and messages. It supports costs in mana, health, maximum stats, inventory items, statuses, and permanent curses.
