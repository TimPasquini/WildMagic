from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .config import (
    ollama_autostart_enabled,
    ollama_dialogue_num_predict,
    ollama_dialogue_temperature,
    ollama_host,
    ollama_json_format_enabled,
    ollama_keep_alive,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_num_predict,
    ollama_resolution_attempts,
    ollama_temperature,
    ollama_thinking_enabled,
    ollama_timeout_seconds,
    ollama_town_num_predict,
    ollama_trade_num_predict,
    ollama_trade_temperature,
)


def parse_ollama_error_body(body: str) -> str:
    stripped = body.strip()
    if not stripped:
        return ""
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped[:500]
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, str):
            return error
    return stripped[:500]


def ensure_ollama_running(base_url: str) -> bool:
    """Check if Ollama is running at base_url. If not, try to start it in the background
    and wait up to 12 seconds for it to become responsive."""
    import socket
    import subprocess
    import time
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434

    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except (OSError, ConnectionRefusedError):
        pass

    if not ollama_autostart_enabled():
        print(f"Ollama server not detected at {base_url}. Autostart is disabled.")
        return False

    print(
        f"Ollama server not detected at {base_url}. Attempting to start 'ollama serve' in the background..."
    )
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        child_env = os.environ.copy()
        child_env["OLLAMA_HOST"] = base_url

        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
            env=child_env,
        )
    except FileNotFoundError:
        print("Ollama command-line tool not found on PATH. Cannot auto-start server.")
        return False

    for attempt in range(1, 13):
        time.sleep(1.0)
        try:
            with socket.create_connection((host, port), timeout=1):
                # Verify HTTP response
                req = urllib.request.Request(f"{base_url}/api/tags")
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        print(
                            f"Ollama server successfully started and responsive after {attempt}s."
                        )
                        return True
        except Exception:
            pass

    print("Ollama server failed to start or respond within 12 seconds.")
    return False


def _post_ollama_chat(
    base_url: str, payload: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    """Shared low-level Ollama /api/chat POST, used by every Ollama-backed provider
    (wild magic resolution, NPC dialogue, and any future LLM-driven subsystem) so
    that swapping models per-purpose never requires duplicating HTTP plumbing."""
    ensure_ollama_running(base_url)
    request = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = parse_ollama_error_body(body)
        raise ValueError(f"Ollama HTTP {exc.code}: {detail or exc.reason}") from exc


def strip_thinking(raw: str) -> str:
    return re.sub(
        r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE
    ).strip()


def extract_thinking(raw: str) -> str | None:
    if not raw:
        return None
    match = re.search(r"<think>(.*?)</think>", raw, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    thought = match.group(1).strip()
    return thought or None


def normalize_ollama_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def fetch_ollama_models(
    base_url: str | None = None, purpose: str | None = "wild"
) -> list[str]:
    """Return sorted list of model names available from Ollama. Empty list on failure."""
    url = normalize_ollama_url(base_url) if base_url else ollama_host(purpose)
    ensure_ollama_running(url)
    try:
        req = urllib.request.Request(f"{url}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return sorted(m["name"] for m in data.get("models", []))
    except Exception:
        return []
