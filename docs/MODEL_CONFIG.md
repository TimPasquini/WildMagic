# Model Configuration

How Wild Magic finds, configures, and talks to its language models. Everything here is
read through `wildmagic/config.py` — if this document and the code disagree, the code
wins and this file should be fixed.

## Where configuration comes from

Precedence, highest first:

1. **Shell environment** — anything exported before launching the game.
2. **Project `.env`** — loaded at import time via `load_dotenv(override=False)`, so it
   never clobbers shell-provided values. This is the right home for per-machine setup.
3. **Code defaults** — every option has one; a fresh checkout with no `.env` plays.

In-game settings changes persist with `set_config_value()`, which writes back to `.env`
and updates the running process.

`.env` matters beyond the game process: when the game **autostarts** `ollama serve`
(see below), the child server inherits the game's full environment — so server-side
Ollama variables also belong in `.env`.

## Purposes and routes

Every LLM call has a **purpose**, and each purpose belongs to a **route**:

| Purpose | What it does | Route | Default model |
|---|---|---|---|
| `wild` | resolve typed wild-magic spells | URGENT | `WILDMAGIC_MODEL` |
| `dialogue` | NPC conversation | URGENT | `WILDMAGIC_MODEL` |
| `trade` | trade-offer extraction from dialogue | URGENT | dialogue's model |
| `canon` | on-demand canon materialization (`examine`, `read`) | URGENT | `WILDMAGIC_MODEL` |
| `agent` | autonomous playtesting command chooser | URGENT | `WILDMAGIC_MODEL` |
| `town` | settlement generation (name, buildings, NPCs) | BACKGROUND | `WILDMAGIC_MODEL` |
| `lore` | rumor/claim extraction, promise flesh | BACKGROUND | `qwen3:1.7b` |

URGENT purposes block the player and want the fastest configuration (GPU). BACKGROUND
purposes run on executor threads behind gameplay and may use a slower, cheaper
configuration (CPU, smaller model) so they never compete with the foreground.

> Note: promise **flesh** rides the `lore` purpose configuration (it is genuinely
> background work). Future background canon prewarming will construct its provider with
> background-channel overrides; the `canon` purpose covers the blocking, player-facing
> path.

### Scoped variables

Options marked **scoped** below resolve through up to three keys, most specific first:

```text
WILDMAGIC_<PURPOSE>_<OPTION>     e.g. WILDMAGIC_LORE_OLLAMA_NUM_GPU
WILDMAGIC_<ROUTE>_<OPTION>       e.g. WILDMAGIC_BACKGROUND_OLLAMA_NUM_GPU
WILDMAGIC_<OPTION>               e.g. WILDMAGIC_OLLAMA_NUM_GPU
```

Purpose names accept aliases (`SPELL`/`WILD_MAGIC`/`MAGIC` → `WILD`, `NPC_DIALOGUE` →
`DIALOGUE`, `TOWN_GENERATION` → `TOWN`, `LORE_EXTRACTION` → `LORE`). Routes are
`URGENT` (wild, dialogue, trade, canon, agent) and `BACKGROUND` (town, lore).

## Providers

Per purpose: `WILDMAGIC_PROVIDER` (the master switch, default `ollama`), plus
`WILDMAGIC_DIALOGUE_PROVIDER`, `WILDMAGIC_TRADE_PROVIDER` (defaults to dialogue's),
`WILDMAGIC_TOWN_PROVIDER`, `WILDMAGIC_LORE_PROVIDER`, `WILDMAGIC_CANON_PROVIDER` (each
defaults to the master).

- `ollama` — real local LLM. The mode the game is designed around.
- `mock` — deterministic fake. For tests, replays, and engine work; never needs a server.
- `auto` — tries Ollama, falls back to mock when `WILDMAGIC_ENABLE_FALLBACKS=1`.
  Avoid for LLM evaluation; it hides provider failures.

Autonomous playtesting has a separate `--agent` flag. `--provider` still selects the game
resolver, while `--agent ollama` uses the `agent` purpose only to choose CLI commands.

## Models

| Variable | Default | Notes |
|---|---|---|
| `WILDMAGIC_MODEL` | `qwen3.5:9b-q4_K_M` | shared default for wild/dialogue/town/agent |
| `WILDMAGIC_WILD_MODEL` | shared | spell resolution |
| `WILDMAGIC_DIALOGUE_MODEL` | shared | NPC conversation (a chattier finetune works well) |
| `WILDMAGIC_TRADE_MODEL` | dialogue's | trade extraction |
| `WILDMAGIC_CANON_MODEL` | shared | examine/read materialization |
| `WILDMAGIC_BACKGROUND_CANON_MODEL` | lore model | background book previews |
| `WILDMAGIC_AGENT_MODEL` | shared | autonomous playtesting command chooser |
| `WILDMAGIC_TOWN_MODEL` | shared | settlement generation |
| `WILDMAGIC_LORE_MODEL` | `qwen3:1.7b` | extraction/flesh; small is fine for extraction |

Any Ollama model tag works. Sizing guidance: spell resolution and town generation are
the most demanding (structured JSON against a long system prompt — 7B+ instruct models
recommended); lore extraction is the least (1.5–4B is usable). Models below ~7B start
ignoring the spell contract in creative ways.

## Ollama connection and request options

| Variable | Scoped | Default (clamp) | Meaning |
|---|---|---|---|
| `WILDMAGIC_*_OLLAMA_HOST` / `OLLAMA_HOST` | yes | `http://127.0.0.1:11434` | endpoint per purpose; bare `OLLAMA_HOST` is the final fallback. **Use the IPv4 literal, not `localhost`** — see latency note below |
| `WILDMAGIC_*_OLLAMA_TIMEOUT` | yes | 180s (5–1800) | HTTP timeout |
| `WILDMAGIC_*_OLLAMA_NUM_CTX` | yes | 16384 (2048–32768) | context window. **Load-time option** — see thrash warning below |
| `WILDMAGIC_*_OLLAMA_NUM_GPU` | yes | 999; **0 for `LORE`** | GPU layer offload. 999 = everything on GPU, 0 = pure CPU. **Load-time option** |
| `WILDMAGIC_*_OLLAMA_KEEP_ALIVE` | yes | `10m` | how long the server keeps the model resident after a request |
| `WILDMAGIC_*_OLLAMA_THINK` | yes | off | request model thinking (slower; usually unnecessary) |
| `WILDMAGIC_*_OLLAMA_FORMAT` | yes | on | ask Ollama for strict JSON output (auto-retries without it on grammar errors) |
| `WILDMAGIC_OLLAMA_TEMPERATURE` | no | 0.25 | wild/town/lore/canon temperature |
| `WILDMAGIC_DIALOGUE_TEMPERATURE` | no | 0.7 | dialogue temperature |
| `WILDMAGIC_TRADE_TEMPERATURE` | no | 0.5 | falls back to the dialogue variable if unset |
| `WILDMAGIC_CANON_TEMPERATURE` | no | 0.85 | examine/read prose; hot by design so similar seed packets don't yield identical titles |
| `WILDMAGIC_AGENT_TEMPERATURE` | no | 0.35 | autonomous playtesting command variety |
| `WILDMAGIC_OLLAMA_NUM_PREDICT` | no | 1024 (128–4096) | wild-magic response budget |
| `WILDMAGIC_DIALOGUE_NUM_PREDICT` | no | 320 (32–1024) | dialogue budget |
| `WILDMAGIC_TRADE_NUM_PREDICT` | no | dialogue's | trade budget |
| `WILDMAGIC_AGENT_NUM_PREDICT` | no | 256 (64-1024) | command-chooser response budget |
| `WILDMAGIC_TOWN_NUM_PREDICT` | no | 2000 (256–8192) | town generation budget |
| `WILDMAGIC_LORE_NUM_PREDICT` | no | 700 (64–2048) | lore/flesh budget |
| `WILDMAGIC_CANON_NUM_PREDICT` | no | 2048 (64–4096) | examine/study/investigate/read budget; sized for compressed book pages and close-study prose. Truncation past the cap is recovered by the canon JSON salvage. This is a blocking call, and on slow backends a bigger cap risks blowing the timeout (empty result) instead of just truncating — raise with care |
| `WILDMAGIC_OLLAMA_RESOLUTION_ATTEMPTS` | no | 2 (1–4) | wild-magic retries on malformed JSON |

**Use `127.0.0.1`, not `localhost`, for the host (especially on Windows).** `localhost`
resolves to IPv6 `::1` first; if the Ollama server is bound to IPv4 (the common case), the
`::1` connection stalls on a retransmit backoff — measured at **~2.0 s of dead time per
request** — before falling back to `127.0.0.1`. That cost is paid on *every* call
(resolve, dialogue, trade, canon), so it is the dominant latency on the short calls.
Measured 2026-06-13 (warm `qwen3.5:9b`, identical payload, Ollama's own `total_duration`
~0.22 s both ways): `localhost` → **2.28 s** wall vs `127.0.0.1` → **0.25 s** wall. The
default is now `http://127.0.0.1:11434`; only set `WILDMAGIC_OLLAMA_HOST` if you point at a
different host/port, and prefer an IP literal there too.

For `--agent ollama`, keep `WILDMAGIC_AGENT_OLLAMA_NUM_CTX` aligned with the foreground
resolver's `num_ctx` when both use the same model tag. The command prompt is compact, but
matching load-time options prevents Ollama from reloading the model between agent and
resolver calls.

## Canon prewarming

Two background tiers share one canon worker. **The book pipeline is always-on** (top
priority) and works strictly nearest-first: for each book, closest to farthest, it
materializes the title (cheap, whole zone, so every shelf is readable by name) and then —
for nearby visible books — the full pages (under the canonical book id, so `read` reuses
them with no wait). It runs independent of the saturation flag. The broader **saturation**
tier is off by default so the game never starts extra LLM work without an explicit opt-in;
when on it adds the current labeled room's flavor and far-look entity details *behind* the
book pipeline.

The queue advances both on player turns and, in the pygame UI, on idle frames
(`pump_canon_prewarm`), so titles and pages keep materializing while the player stands
still and the queued job is re-chosen by proximity each time a slot frees.

| Variable | Default | Notes |
|---|---|---|
| `WILDMAGIC_BOOK_TITLES` | on | the always-on book pipeline: titles for every book in the zone (ignores visibility/distance) + full pages for nearby visible books, nearest-first; the test suite forces it off |
| `WILDMAGIC_CANON_PREWARM_ENABLED` | off | when on, adds room flavor and far-look entity details behind the book pipeline |
| `WILDMAGIC_CANON_PREWARM_LIMIT` | 2 | max queued/in-flight background canon jobs. 2 keeps one running and one queued on the single worker so it never idles; 0 disables all background canon (the book pipeline included) |

Prewarming uses the background route (`LORE`/`BACKGROUND` Ollama options) and the
`WILDMAGIC_BACKGROUND_CANON_MODEL` model, falling back to `WILDMAGIC_LORE_MODEL`.
It can materialize `book_title`, full `book`, `room_flavor`, and far-look
`object_detail`/`npc_detail`/`creature_detail` records. A book's full pages are prewarmed
only after its title exists (so they inherit the shelf name); a book read before the
painter reaches it materializes on the urgent channel on demand. Close-study details
still come from player investigation.

### The model-reload (thrash) warning

Ollama reloads a model from scratch whenever a request arrives for an already-loaded
model tag with **different load-time options** (`num_gpu`, `num_ctx`). If two purposes
share one model tag but differ in those options, every alternation between them evicts
and reloads the model — visible as VRAM emptying and refilling, and felt as long stalls.

Rules of thumb:
- Purposes that share a model tag should share `num_gpu` and `num_ctx`.
- If you want the same weights with two different splits (GPU for play, CPU for
  background), make a free alias: `ollama cp qwen3.5:9b-q4_K_M qwen3.5:9b-cpu` —
  copies share blobs on disk, but distinct tags get distinct runners that can stay
  resident simultaneously.
- Raise the server's `OLLAMA_MAX_LOADED_MODELS` (see below) so resident models don't
  evict each other.

## Server lifecycle and server-side variables

`WILDMAGIC_OLLAMA_AUTOSTART` (default on): if no server answers at the configured host,
the game launches `ollama serve` in the background and waits up to 12s. The child
inherits the game's environment — including everything from `.env` — so **server-side
Ollama variables work from `.env` only when the game starts the server**. If Ollama is
already running (tray app, your own terminal), those values come from wherever *that*
process got its environment, and `.env` is irrelevant to it.

Server-side variables worth setting (these are Ollama's, not ours — see Ollama's docs):

- `OLLAMA_MAX_LOADED_MODELS` — how many models stay resident at once. With a main GPU
  model, a CPU lore model, and a separate dialogue model, you want `3`. Old/forked
  builds may ignore this.
- `OLLAMA_NUM_PARALLEL` — parallel requests per loaded model. `1` is fine; separate
  models already serve concurrently on separate runners.
- GPU backend selection — vendor-specific:
  - **NVIDIA**: works out of the box (CUDA).
  - **AMD**: ROCm builds; `HIP_VISIBLE_DEVICES` selects the card.
  - **Intel Arc** (current stock Ollama): `OLLAMA_VULKAN=1` enables the Vulkan backend,
    and `GGML_VK_VISIBLE_DEVICES=<n>` selects the card. Discrete card and iGPU each get
    an index; if you select the iGPU by mistake Ollama drops it ("dropping integrated
    GPU") and serves **CPU-only without erroring**. `OLLAMA_IGPU_ENABLE=1` un-drops
    integrated GPUs if you really want one.

### Two-server setups

Per-purpose `OLLAMA_HOST` means purposes can use entirely separate servers, e.g. a GPU
server for play and a CPU-only server for background work. Point the whole background
route at a second port:

```dotenv
WILDMAGIC_BACKGROUND_OLLAMA_HOST=http://127.0.0.1:11435
```

This routes `lore`, `town`, and background-canon prewarm to `:11435` while the urgent
purposes (`wild`, `dialogue`, `trade`, `canon`) stay on the default `:11434`. You don't
have to launch the second server yourself: the game's autostart (`ensure_ollama_running`)
spins it up on the first background call, inheriting `.env` — so it costs only a one-time
startup wait on that first call. Because background requests force `num_gpu=0`, that
server only ever runs models on the CPU even though it inherits the GPU/Vulkan env. If
you'd rather start it ahead of time, run `OLLAMA_HOST=127.0.0.1:11435 ollama serve` (or
`$env:OLLAMA_HOST="127.0.0.1:11435"; ollama serve` in PowerShell).

**Why reach for this:** two servers never queue behind or evict each other, so a slow
background CPU job (a book, a town) can never block a foreground GPU spell — concurrency
is *guaranteed*, independent of `OLLAMA_MAX_LOADED_MODELS`/`OLLAMA_NUM_PARALLEL` and of
any scheduler quirks in forked builds. On a single shared server those settings *should*
let a CPU model and a GPU model run at once, but some builds (notably the Intel Arc
Vulkan fork) serialize anyway — the GPU spell stalls until the CPU book finishes. The
two-server split is the robust fix.

> Verify the split actually took with `ollama ps` against each port: the GPU model at
> `100% GPU` on `:11434` and the CPU model at `100% CPU` on `:11435`, generating
> simultaneously. Only an **autostarted** server gets `.env` — if a tray/desktop Ollama
> is already holding `:11434`, the game attaches to it and your GPU/Vulkan env never
> applies there, so quit any externally-started Ollama first.

## Feature toggles and logging

| Variable | Default | Meaning |
|---|---|---|
| `WILDMAGIC_ENABLE_FALLBACKS` | on | allow mock fallback when a provider fails. Set `0` for strict LLM-contract testing |
| `WILDMAGIC_LORE_ENABLED` | on | background rumor/claim extraction from dialogue and books |
| `WILDMAGIC_FLESH_ENABLED` | on | background narrative decoration of bound promises |
| `WILDMAGIC_AUDIT_LOG` | on | JSONL audit of every LLM call |
| `WILDMAGIC_AUDIT_DIR` | `logs` | audit destination: `wild_magic_audit.jsonl`, `dialogue_audit.jsonl`, `lore_audit.jsonl`, `flesh_audit.jsonl`, `canon_audit.jsonl`, … |

## Example setups

**Single decent GPU (12GB+):** nothing to set. Optionally one model for everything:

```dotenv
WILDMAGIC_MODEL=qwen3.5:9b-q4_K_M
OLLAMA_MAX_LOADED_MODELS=2
```

**8GB GPU + strong CPU (split foreground/background):** keep play on the GPU, push
background work to a CPU-resident alias of the same weights:

```dotenv
WILDMAGIC_MODEL=qwen3.5:9b-q4_K_M
WILDMAGIC_LORE_MODEL=qwen3.5:9b-cpu      # created with: ollama cp qwen3.5:9b-q4_K_M qwen3.5:9b-cpu
WILDMAGIC_TOWN_MODEL=qwen3.5:9b-cpu
WILDMAGIC_BACKGROUND_OLLAMA_NUM_GPU=0
OLLAMA_MAX_LOADED_MODELS=3
```

(Or use a genuinely small lore model — `qwen3:1.7b`, the default — instead of the alias.)

**CPU only:** expect slow spell resolution; shrink budgets and lengthen timeouts:

```dotenv
WILDMAGIC_OLLAMA_NUM_GPU=0
WILDMAGIC_OLLAMA_TIMEOUT=600
WILDMAGIC_MODEL=qwen3:4b        # or another small instruct model
```

**No LLM at all:** `python -m wildmagic.cli --provider mock` — deterministic, playable,
and what the test suite uses.

## Troubleshooting

**First, look at the audit logs** (`logs/*.jsonl`): every call records the prompt,
context, raw response, parse result, and error. Most "the model is broken" reports are
visible there in one minute.

- **Everything is slow / GPU sits idle.** Run `ollama ps` after casting a spell. The
  PROCESSOR column must say `100% GPU` for your main model. If it says CPU: the *server*
  has no working GPU backend — request options can't fix that. Check the server's env
  (backend variables above), and remember an autostarted server gets `.env` while a
  tray-started one does not. Server logs (`%LOCALAPPDATA%\Ollama\server.log` on Windows)
  show GPU discovery at startup; look for your card's name, `no compatible GPUs`, or
  `dropping integrated GPU`. Three things that trip people up here:
  - **A running server's device is fixed at startup.** Editing backend env (or `.env`)
    does nothing to a server that's already up — `ollama ps` will keep reporting the old
    device. Fully stop Ollama and let it restart so it re-runs GPU discovery.
  - **`ollama ps` says GPU, not *which* GPU.** On a machine with more than one adapter
    (e.g. a discrete card plus an integrated one), `100% GPU` only confirms offload
    happened, not that it's the fast card. The startup discovery line in `server.log` is
    the authoritative source for the selected device name.
  - **An autostarted server has no readable log.** When the *game* starts `ollama serve`,
    it discards the server's stdout/stderr, so `server.log` is **not** refreshed and may
    sit stale from an earlier manual/tray launch. To actually read GPU discovery, start
    `ollama serve` yourself in a terminal (it logs to the console), or just trust
    `ollama ps` plus cast latency.
- **Every call is ~2 s slower than `ollama ps`/the audit timings say it should be
  (Windows).** The `localhost`→IPv6 `::1` connection stall described above. Compare the
  request's wall-clock to Ollama's reported `total_duration`; a steady ~2 s gap that does
  not depend on prompt or generation length is the signature. Fix: use `127.0.0.1` (now
  the default) rather than `localhost` for `WILDMAGIC_OLLAMA_HOST` / `OLLAMA_HOST`.
- **VRAM fills and empties repeatedly; periodic long stalls.** Model thrash — same model
  tag requested with different `num_gpu`/`num_ctx`, or more models than
  `OLLAMA_MAX_LOADED_MODELS` allows. See the thrash warning above.
- **Garbage output (random tokens, wrong language) on longer prompts.** Seen with
  partial CPU offload on some hardware. Force a clean split: `num_gpu=999` (fully GPU)
  or `0` (fully CPU) for that purpose, and verify with `ollama ps`.
- **Spells fail with timeouts.** Raise `WILDMAGIC_OLLAMA_TIMEOUT` (or the purpose-scoped
  variant). First call after idle includes model load time; `OLLAMA_KEEP_ALIVE=10m`
  (our default) keeps warm models warm.
- **`examine`/`read` or town generation return nothing (empty canon/settlement).** A
  long-form generation hit the timeout and produced an empty result rather than
  truncating. The risk scales with response budget ÷ throughput: at low tokens/sec a
  full book (`WILDMAGIC_CANON_NUM_PREDICT`) or town (`WILDMAGIC_TOWN_NUM_PREDICT` 2000)
  can exceed the default timeout. Check `ollama ps`/audit for throughput, then either
  raise the scoped timeout (`WILDMAGIC_CANON_OLLAMA_TIMEOUT`, `WILDMAGIC_TOWN_OLLAMA_TIMEOUT`
  — town is background, so a multi-minute budget is fine) or lower that purpose's
  `NUM_PREDICT`. Canon truncation is salvaged automatically, but a timeout is not.
- **Spells return prose or broken JSON.** Confirm `WILDMAGIC_OLLAMA_FORMAT` is on, try a
  larger/instruct-tuned model, and check the audit log for what the model actually said.
  The resolver already retries (`WILDMAGIC_OLLAMA_RESOLUTION_ATTEMPTS`).
- **Mock responses during an Ollama run** (log lines marked `*>` instead of `>`): a
  provider failure triggered the fallback. Set `WILDMAGIC_ENABLE_FALLBACKS=0` to surface
  the real error instead.
- **`.env` changes seem ignored.** A shell-exported variable overrides `.env` (by
  design). For server-side variables, the running Ollama predates your edit — quit it
  and let the game restart it.

## Character portraits (image generation)

Portraits are a **separate subsystem from the LLM stack** — not Ollama. The character
creation screen can generate a portrait from the typed physical description using
**SDXL** (Stable Diffusion XL) via PyTorch-XPU on the Intel Arc. The heavy
`torch`/`diffusers` stack lives in its **own venv**, and the game talks to a long-lived
**worker subprocess** over stdin/stdout, so torch never enters the game process and the
model loads once (not per portrait). See `tools/portraits/README.md` for the venv setup
and `tools/portraits/generate_portrait.py` / `worker.py` for the code.

Key implementation facts:
- **bfloat16, not fp16.** On Arc XPU the SDXL UNet overflows fp16 to NaN → solid black
  images. bf16 has fp32's range at fp16's memory, so it's the default on XPU. (CUDA uses
  fp16 + the `madebyollin/sdxl-vae-fp16-fix` VAE; CPU uses fp32.)
- **int8 weight quantization (default).** Unquantized bf16 SDXL pins all 8GB of dedicated
  VRAM and spills ~25GB into shared system memory. int8 weight-only quantization (torchao)
  on the UNet + second text encoder drops peak VRAM to **~5.3GB** at the **same speed and
  ~same quality** — so it fits dedicated VRAM with no spill. Falls back to bf16 if torchao
  can't apply it. See `WILDMAGIC_PORTRAIT_QUANT`.
- **Shared-GPU contention.** SDXL and a resident Ollama LLM cannot both fit the 8GB Arc:
  overcommit causes silent black images and then a driver device-loss (`DEVICE_LOST`). The
  worker therefore **unloads resident Ollama models before generating** (it reloads on the
  next spell). See `WILDMAGIC_PORTRAIT_FREE_VRAM`. A degenerate (black) result is detected
  and surfaced as an error rather than saved.
- **~16–18s per 768² portrait** on the A750; the worker preloads the model (~a few seconds
  warm, much longer the very first time as files copy into the HF cache).
- **Graceful when absent.** If the portrait venv/python isn't found, portraits are simply
  disabled — the creation screen still works, just without the button.
- **Not Qwen-Image.** Qwen-Image (~20B + a 7B text encoder) overflows 8GB even quantized
  and needs CPU offload (minutes/image) on the A750; SDXL fits in VRAM and stays fast.

Configuration (read through `config.py`):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `WILDMAGIC_PORTRAIT_PYTHON` | no | `C:\Games\wm_image_venv\Scripts\python.exe` | Python interpreter of the image venv. If it doesn't exist, portraits are disabled. |
| `WILDMAGIC_PORTRAIT_ENABLED` | no | auto | `1`/`0` to force; `auto` enables it iff the python above exists. |
| `WILDMAGIC_PORTRAIT_DIR` | no | `tools/portraits/out` | Where generated PNGs are written (gitignored). |
| `WILDMAGIC_PORTRAIT_STEPS` | no | `28` | Diffusion steps. Lower = faster, rougher. |
| `WILDMAGIC_PORTRAIT_SIZE` | no | `768` | Square portrait edge in px. 1024 is sharper but tighter on 8GB. |
| `WILDMAGIC_PORTRAIT_QUANT` | no | `int8` | Weight quantization: `int8` (fits 8GB, recommended), `fp8` (needs hw support; experimental on Arc), or `none` (bf16, overflows 8GB). Auto-falls back to bf16 if torchao can't apply it. |
| `WILDMAGIC_PORTRAIT_FREE_VRAM` | no | `1` | Unload resident Ollama models before generating so SDXL gets the GPU. The LLM reloads on the next spell. |

Speed note: an SDXL-Lightning/Turbo LoRA can drop generation to ~2–4 steps (near-instant)
if the default ~18s feels slow; not wired in yet.
