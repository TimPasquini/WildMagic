"""Long-lived portrait worker. Runs in the image venv; the game spawns one and talks to
it over stdin/stdout so the SDXL model loads exactly once.

Protocol (one JSON object per line):
  stdin  <- {"id": "abc", "description": "...", "out": "C:/.../p.png", "seed": 7,
             "size": 768, "steps": 28}
  stdout -> {"event": "ready"}                          once, after the model loads
  stdout -> {"id": "abc", "ok": true, "out": "..."}     per completed request
  stdout -> {"id": "abc", "ok": false, "error": "..."}  per failed request

Diagnostic [portrait] lines go to stderr so they never corrupt the stdout protocol.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# Diagnostics from generate_portrait print to stdout; redirect them to stderr so the
# stdout channel carries only protocol JSON.
sys.stdout, _real_stdout = sys.stderr, sys.stdout

from generate_portrait import generate_portrait, load_pipeline


def _emit(obj: dict) -> None:
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


def _free_gpu_vram() -> None:
    """Evict any resident Ollama models so SDXL gets the GPU to itself. The game passes
    the Ollama host via env; best-effort, silent on any failure (Ollama may be off)."""
    host = os.environ.get("WILDMAGIC_FREE_OLLAMA_HOST")
    if not host:
        return
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=2) as resp:
            models = json.loads(resp.read()).get("models", [])
    except Exception:
        return
    for model in models:
        name = model.get("name") or model.get("model")
        if not name:
            continue
        try:
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            req = urllib.request.Request(
                f"{host}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()
            print(f"[portrait] freed GPU: unloaded {name}", flush=True)
        except Exception:
            pass


def main() -> None:
    _free_gpu_vram()  # before loading SDXL, so it isn't fighting a resident LLM
    load_pipeline()  # preload so the first request isn't a cold start
    _emit({"event": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        req_id = req.get("id")
        try:
            _free_gpu_vram()  # in case a spell reloaded the LLM since last time
            out = generate_portrait(
                req["description"],
                req["out"],
                seed=req.get("seed"),
                size=int(req.get("size", 768)),
                steps=int(req.get("steps", 28)),
            )
            _emit({"id": req_id, "ok": True, "out": str(out)})
        except Exception as exc:  # keep the worker alive across a bad request
            _emit({"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
