"""Game-side client for the out-of-process portrait worker.

The heavy SDXL/torch stack lives in a separate venv (see tools/portraits/). This module
spawns that worker lazily, ships it portrait requests, and lets the caller poll for
results without ever blocking the game loop. If the venv isn't present, `available()`
is False and the UI simply omits the feature.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import (
    ollama_host,
    portrait_dir,
    portrait_enabled,
    portrait_free_vram,
    portrait_python,
    portrait_quant,
    portrait_size,
    portrait_steps,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKER = _REPO_ROOT / "tools" / "portraits" / "worker.py"


class PortraitClient:
    """Owns the worker subprocess and a background reader thread. Thread-safe poll/request."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._results: dict[str, dict] = {}
        self._ready = False
        self._spawn_failed = False  # Popen itself failed (missing python) -> permanent
        self._next_id = 0

    def available(self) -> bool:
        return portrait_enabled() and _WORKER.exists()

    def warming(self) -> bool:
        """True once a worker is spawned but the model hasn't finished loading."""
        return self._proc is not None and not self._ready and not self._spawn_failed

    def _worker_env(self) -> dict:
        env = os.environ.copy()
        env["WILDMAGIC_PORTRAIT_QUANT"] = portrait_quant()
        if portrait_free_vram():
            # Tell the worker which Ollama to unload before generating, so SDXL doesn't
            # fight a resident LLM for VRAM on a small shared GPU.
            env["WILDMAGIC_FREE_OLLAMA_HOST"] = ollama_host()
        else:
            env.pop("WILDMAGIC_FREE_OLLAMA_HOST", None)
        return env

    def _ensure_worker(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if self._spawn_failed:
            return False
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                [str(portrait_python()), str(_WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
                env=self._worker_env(),
            )
        except Exception:
            self._spawn_failed = True
            return False
        self._ready = False
        self._reader = threading.Thread(
            target=self._read_loop, args=(self._proc,), daemon=True
        )
        self._reader.start()
        return True

    def _read_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("event") == "ready":
                with self._lock:
                    self._ready = True
                continue
            req_id = msg.get("id")
            if req_id is not None:
                with self._lock:
                    self._results[str(req_id)] = msg
        # stdout closed -> this worker exited. A fresh one is spawned on the next
        # request (not a permanent failure, unlike a spawn error).
        with self._lock:
            if self._proc is proc:
                self._ready = False

    def _restart_worker(self) -> None:
        """Tear down the current worker so the next request spawns a clean one. Used
        after a failure: an Arc device-loss kills the XPU context for the whole process,
        so the only recovery is a fresh worker."""
        proc = self._proc
        self._proc = None
        self._ready = False
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.terminate()
            except Exception:
                pass

    def request(self, description: str, seed: int | None = None) -> str | None:
        """Queue a portrait request. Returns a request id to poll, or None if disabled.
        Safe to call before the model finishes loading (the request waits in the pipe)."""
        if not self.available() or not description.strip():
            return None
        if not self._ensure_worker():
            return None
        with self._lock:
            req_id = str(self._next_id)
            self._next_id += 1
        out_dir = portrait_dir()
        if not out_dir.is_absolute():
            out_dir = _REPO_ROOT / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"portrait_{req_id}_{int(time.time())}.png"
        payload = {
            "id": req_id,
            "description": description.strip(),
            "out": str(out_path),
            "seed": seed,
            "size": portrait_size(),
            "steps": portrait_steps(),
        }
        try:
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except Exception:
            self._restart_worker()
            return None
        with self._lock:
            self._results[req_id] = {"pending": True}
        return req_id

    def poll(self, req_id: str) -> tuple[str, str | None]:
        """('pending' | 'done' | 'error' | 'unknown', path_or_error_message)."""
        with self._lock:
            msg = self._results.get(req_id)
        proc_dead = self._proc is None or self._proc.poll() is not None
        if msg is None:
            return (
                ("error", "portrait worker stopped") if proc_dead else ("unknown", None)
            )
        if msg.get("pending"):
            if proc_dead:
                # The worker died (e.g. an Arc device-loss crash) before answering.
                self._restart_worker()
                return ("error", "portrait worker stopped — try again")
            return ("pending", None)
        if msg.get("ok"):
            return ("done", msg.get("out"))
        # A failed request: the worker's GPU context may be poisoned (device-loss), so
        # rebuild it for the next attempt.
        self._restart_worker()
        return ("error", str(msg.get("error") or "unknown error"))

    def close(self) -> None:
        self._restart_worker()
