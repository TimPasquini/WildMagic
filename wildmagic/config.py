from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv, set_key


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"
DEFAULT_LORE_MODEL = "qwen3:1.7b"
DEFAULT_PROVIDER = "ollama"
# Use the IPv4 literal, not "localhost". On Windows "localhost" resolves to IPv6 ::1
# first; if Ollama is bound to IPv4 the ::1 connect stalls ~2s on a retransmit backoff
# before falling back to 127.0.0.1 — paid on EVERY request (measured 2026-06-13:
# localhost 2.28s vs 127.0.0.1 0.25s wall for the same warm call). See docs/MODEL_CONFIG.md.
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

_FALSE_VALUES = {"0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def load_environment() -> None:
    """Load project configuration without overriding shell-provided values."""
    load_dotenv(dotenv_path=ENV_PATH, override=False)


def get_config_value(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    return value.strip()


def set_config_value(key: str, value: str) -> None:
    """Update the active process and persist the value to the project .env."""
    normalized = str(value).strip()
    ENV_PATH.touch(exist_ok=True)
    set_key(ENV_PATH, key, normalized, quote_mode="auto")
    os.environ[key] = normalized


def set_runtime_config_value(key: str, value: str) -> None:
    """Update this process only, without writing machine-local run state to .env."""
    os.environ[key] = str(value).strip()


def _float_value(key: str, default: float, minimum: float, maximum: float) -> float:
    value = get_config_value(key)
    try:
        parsed = float(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _int_value(key: str, default: int, minimum: int, maximum: int) -> int:
    value = get_config_value(key)
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bool_value(key: str, default: bool) -> bool:
    value = get_config_value(key)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def _normalize_ollama_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def _purpose_key(purpose: str | None) -> str | None:
    if not purpose:
        return None
    normalized = re.sub(r"[^A-Z0-9]+", "_", purpose.upper()).strip("_")
    aliases = {
        "SPELL": "WILD",
        "WILD_MAGIC": "WILD",
        "MAGIC": "WILD",
        "NPC_DIALOGUE": "DIALOGUE",
        "BACKGROUND_TOWN": "TOWN",
        "TOWN_GENERATION": "TOWN",
        "LORE_EXTRACTION": "LORE",
    }
    return aliases.get(normalized, normalized)


def _route_key(purpose: str | None) -> str | None:
    key = _purpose_key(purpose)
    # CANON is on-demand materialization (examine/read): the player is blocked
    # waiting on it, so it routes URGENT. Background prewarming jobs construct
    # their provider with lore-scope overrides instead.
    if key in {"WILD", "DIALOGUE", "TRADE", "CANON", "AGENT"}:
        return "URGENT"
    if key in {"TOWN", "LORE"}:
        return "BACKGROUND"
    return None


def _scoped_keys(purpose: str | None, suffix: str) -> list[str]:
    keys: list[str] = []
    purpose_key = _purpose_key(purpose)
    route_key = _route_key(purpose)
    if purpose_key:
        keys.append(f"WILDMAGIC_{purpose_key}_{suffix}")
    if route_key:
        keys.append(f"WILDMAGIC_{route_key}_{suffix}")
    keys.append(f"WILDMAGIC_{suffix}")
    return keys


def _first_config_value(keys: list[str], default: str) -> str:
    for key in keys:
        value = get_config_value(key)
        if value is not None:
            return value
    return default


def _scoped_int(
    purpose: str | None, suffix: str, default: int, minimum: int, maximum: int
) -> int:
    value = _first_config_value(_scoped_keys(purpose, suffix), str(default))
    try:
        parsed = int(value)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _scoped_float(
    purpose: str | None, suffix: str, default: float, minimum: float, maximum: float
) -> float:
    value = _first_config_value(_scoped_keys(purpose, suffix), str(default))
    try:
        parsed = float(value)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _scoped_bool(purpose: str | None, suffix: str, default: bool) -> bool:
    value = (
        _first_config_value(_scoped_keys(purpose, suffix), "1" if default else "0")
        .lower()
        .strip()
    )
    if value in _TRUE_VALUES or value == "json":
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _shared_model() -> str:
    return get_config_value("WILDMAGIC_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def get_wild_magic_model() -> str:
    return get_config_value("WILDMAGIC_WILD_MODEL") or _shared_model()


def get_dialogue_model() -> str:
    return get_config_value("WILDMAGIC_DIALOGUE_MODEL") or _shared_model()


def get_trade_model() -> str:
    return get_config_value("WILDMAGIC_TRADE_MODEL") or get_dialogue_model()


def get_town_model() -> str:
    return get_config_value("WILDMAGIC_TOWN_MODEL") or _shared_model()


def get_lore_model() -> str:
    return get_config_value("WILDMAGIC_LORE_MODEL") or DEFAULT_LORE_MODEL


def get_props_model() -> str:
    return get_config_value("WILDMAGIC_PROPS_MODEL") or _shared_model()


def get_canon_model() -> str:
    return get_config_value("WILDMAGIC_CANON_MODEL") or _shared_model()


def get_background_canon_model() -> str:
    return get_config_value("WILDMAGIC_BACKGROUND_CANON_MODEL") or get_canon_model()


def get_agent_model() -> str:
    return get_config_value("WILDMAGIC_AGENT_MODEL") or _shared_model()


def get_wild_magic_provider() -> str:
    return (
        get_config_value("WILDMAGIC_PROVIDER", DEFAULT_PROVIDER) or DEFAULT_PROVIDER
    ).lower()


def get_dialogue_provider() -> str:
    return (
        get_config_value("WILDMAGIC_DIALOGUE_PROVIDER") or get_wild_magic_provider()
    ).lower()


def get_trade_provider() -> str:
    return (
        get_config_value("WILDMAGIC_TRADE_PROVIDER") or get_dialogue_provider()
    ).lower()


def get_town_provider() -> str:
    return (
        get_config_value("WILDMAGIC_TOWN_PROVIDER") or get_wild_magic_provider()
    ).lower()


def get_lore_provider() -> str:
    return (
        get_config_value("WILDMAGIC_LORE_PROVIDER") or get_wild_magic_provider()
    ).lower()


def get_props_provider() -> str:
    # Experimental LLM prop set-dressing. On by default wherever the wild-magic
    # provider is (auto -> use Ollama when reachable, else the static prop list).
    return (
        get_config_value("WILDMAGIC_PROPS_PROVIDER") or get_wild_magic_provider()
    ).lower()


def get_canon_provider() -> str:
    return (
        get_config_value("WILDMAGIC_CANON_PROVIDER") or get_wild_magic_provider()
    ).lower()


def get_deeds_model() -> str:
    return get_config_value("WILDMAGIC_DEEDS_MODEL") or get_lore_model()


def get_deeds_provider() -> str:
    # The deed interpreter (Phase A.2) classifies ambiguous spell outcomes into deeds. It
    # follows the wild-magic provider by default; set WILDMAGIC_DEEDS_PROVIDER=off to use
    # only the deterministic fallback (tests/replay always force this off).
    return (
        get_config_value("WILDMAGIC_DEEDS_PROVIDER") or get_wild_magic_provider()
    ).lower()


def ollama_host(purpose: str | None = None) -> str:
    """Return the Ollama endpoint for a provider purpose."""
    keys = _scoped_keys(purpose, "OLLAMA_HOST") + ["OLLAMA_HOST"]
    return _normalize_ollama_url(_first_config_value(keys, DEFAULT_OLLAMA_HOST))


def get_ollama_host() -> str:
    return ollama_host()


def ollama_timeout_seconds(purpose: str | None = None) -> float:
    return _scoped_float(purpose, "OLLAMA_TIMEOUT", 180.0, 5.0, 1800.0)


def ollama_num_predict() -> int:
    return _int_value("WILDMAGIC_OLLAMA_NUM_PREDICT", 1024, 128, 4096)


def ollama_num_ctx(purpose: str | None = None) -> int:
    """Context window size (prompt + response, in tokens)."""
    return _scoped_int(purpose, "OLLAMA_NUM_CTX", 16384, 2048, 32768)


def ollama_temperature() -> float:
    return _float_value("WILDMAGIC_OLLAMA_TEMPERATURE", 0.25, 0.0, 1.5)


def ollama_dialogue_temperature() -> float:
    return _float_value("WILDMAGIC_DIALOGUE_TEMPERATURE", 0.7, 0.0, 1.5)


def ollama_dialogue_num_predict() -> int:
    return _int_value("WILDMAGIC_DIALOGUE_NUM_PREDICT", 320, 32, 1024)


def ollama_trade_temperature() -> float:
    if get_config_value("WILDMAGIC_TRADE_TEMPERATURE") is None:
        return _float_value("WILDMAGIC_DIALOGUE_TEMPERATURE", 0.5, 0.0, 1.5)
    return _float_value("WILDMAGIC_TRADE_TEMPERATURE", 0.5, 0.0, 1.5)


def ollama_trade_num_predict() -> int:
    if get_config_value("WILDMAGIC_TRADE_NUM_PREDICT") is None:
        return _int_value("WILDMAGIC_DIALOGUE_NUM_PREDICT", 320, 32, 1024)
    return _int_value("WILDMAGIC_TRADE_NUM_PREDICT", 320, 32, 1024)


def ollama_thinking_enabled(purpose: str | None = None) -> bool:
    return _scoped_bool(purpose, "OLLAMA_THINK", False)


def ollama_json_format_enabled(purpose: str | None = None) -> bool:
    return _scoped_bool(purpose, "OLLAMA_FORMAT", True)


def ollama_json_schema_enabled(purpose: str | None = None) -> bool:
    """Constrain wild-magic decoding to the *per-cast* response schema (effect enum narrowed
    to the routed core+card effects) instead of the generic JSON mode. Off by default: the
    narrowed schema is computed and audited in shadow mode until this is opted into."""
    return _scoped_bool(purpose, "OLLAMA_SCHEMA", False)


def ollama_town_num_predict() -> int:
    return _int_value("WILDMAGIC_TOWN_NUM_PREDICT", 2000, 256, 8192)


def ollama_lore_num_predict() -> int:
    return _int_value("WILDMAGIC_LORE_NUM_PREDICT", 700, 64, 2048)


def ollama_props_num_predict() -> int:
    """A trim budget: a per-room batch is a handful of one-line props, so keep the
    response small to keep the call fast. Truncation just yields fewer props."""
    return _int_value("WILDMAGIC_PROPS_NUM_PREDICT", 512, 64, 2048)


def ollama_deeds_num_predict() -> int:
    """The deed interpreter returns a tiny JSON classification, so keep the budget
    small and the call fast (it sits adjacent to the wild-magic spell call)."""
    return _int_value("WILDMAGIC_DEEDS_NUM_PREDICT", 256, 64, 1024)


def ollama_props_temperature() -> float:
    """Set-dressing wants surprise and variety; run hot like canon prose (0.85)
    rather than the cautious wild-magic default (0.25)."""
    return _float_value("WILDMAGIC_PROPS_TEMPERATURE", 0.9, 0.0, 1.5)


def ollama_canon_num_predict() -> int:
    """Books are full compressed pages (300-600 words), so the canon budget is
    sized for them; shorter kinds simply stop early. Truncation past this cap is
    recovered by _repair_truncated_json (canon.py). The budget stays bounded because
    this is a blocking, player-facing call and on slow backends a larger cap risks
    blowing the request timeout (empty result). Bumped to 2048 (ceiling 4096) after
    observing NPC-study prose getting cut mid-sentence at the old 1400 cap."""
    return _int_value("WILDMAGIC_CANON_NUM_PREDICT", 2048, 64, 4096)


def ollama_canon_temperature() -> float:
    """Creative prose wants heat; the wild-magic default (0.25) produces
    near-identical titles and passages for similar seed packets."""
    return _float_value("WILDMAGIC_CANON_TEMPERATURE", 0.85, 0.0, 1.5)


def ollama_agent_temperature() -> float:
    return _float_value("WILDMAGIC_AGENT_TEMPERATURE", 0.35, 0.0, 1.5)


def ollama_agent_num_predict() -> int:
    return _int_value("WILDMAGIC_AGENT_NUM_PREDICT", 256, 64, 1024)


def ollama_num_gpu(purpose: str | None = None) -> int:
    default = 0 if _purpose_key(purpose) == "LORE" else 999
    return _scoped_int(purpose, "OLLAMA_NUM_GPU", default, 0, 999)


def ollama_keep_alive(purpose: str | None = None) -> str:
    return _first_config_value(_scoped_keys(purpose, "OLLAMA_KEEP_ALIVE"), "10m")


def ollama_autostart_enabled() -> bool:
    return _bool_value("WILDMAGIC_OLLAMA_AUTOSTART", True)


def ollama_resolution_attempts() -> int:
    return _int_value("WILDMAGIC_OLLAMA_RESOLUTION_ATTEMPTS", 2, 1, 4)


def fallbacks_enabled() -> bool:
    return _bool_value("WILDMAGIC_ENABLE_FALLBACKS", True)


def audit_log_enabled() -> bool:
    return _bool_value("WILDMAGIC_AUDIT_LOG", True)


def lore_enabled() -> bool:
    return _bool_value("WILDMAGIC_LORE_ENABLED", True)


def flesh_enabled() -> bool:
    return _bool_value("WILDMAGIC_FLESH_ENABLED", True)


def lore_cards_enabled() -> bool:
    """Tiered authored world-knowledge (docs/LORE_CARDS.md) injected into dialogue and book
    generation. On by default; the test suite forces it off so engine tests stay model-free."""
    return _bool_value("WILDMAGIC_LORE_CARDS_ENABLED", True)


def canon_prewarm_enabled() -> bool:
    return _bool_value("WILDMAGIC_CANON_PREWARM_ENABLED", False)


def book_titles_enabled() -> bool:
    """Book titles always prewarm in the background (top priority, whole zone) so
    every shelved book is identifiable on sight. Unlike the broader saturation set
    (`canon_prewarm_enabled`), this is on by default; the test suite forces it off."""
    return _bool_value("WILDMAGIC_BOOK_TITLES", True)


def canon_prewarm_limit() -> int:
    """How many background canon jobs may be submitted at once. The default of 2
    keeps one job running and one queued on the single-worker route, so the model
    never idles between jobs; the queued slot is re-chosen by proximity each time a
    slot frees. 0 disables all background canon."""
    return _int_value("WILDMAGIC_CANON_PREWARM_LIMIT", 2, 0, 8)


def audit_dir() -> Path:
    return Path(get_config_value("WILDMAGIC_AUDIT_DIR", "logs") or "logs")


# --- Character portraits (image generation; see docs/MODEL_CONFIG.md) ---------
# A separate subsystem from the LLM stack: SDXL runs in its own venv, driven by a
# worker subprocess, so torch never enters the game process.

_DEFAULT_PORTRAIT_PYTHON = r"C:\Games\wm_image_venv\Scripts\python.exe"


def portrait_python() -> Path:
    """Path to the image venv's Python interpreter."""
    return Path(
        get_config_value("WILDMAGIC_PORTRAIT_PYTHON", _DEFAULT_PORTRAIT_PYTHON)
        or _DEFAULT_PORTRAIT_PYTHON
    )


def portrait_enabled() -> bool:
    """Portraits are on when explicitly enabled, or (auto) when the venv python exists.
    Missing python -> disabled, so the creation screen degrades gracefully."""
    value = get_config_value("WILDMAGIC_PORTRAIT_ENABLED", "auto")
    if value and value.lower() in _TRUE_VALUES:
        return True
    if value and value.lower() in _FALSE_VALUES:
        return False
    return portrait_python().exists()


def portrait_dir() -> Path:
    return Path(
        get_config_value("WILDMAGIC_PORTRAIT_DIR", "tools/portraits/out")
        or "tools/portraits/out"
    )


def portrait_steps() -> int:
    return _int_value("WILDMAGIC_PORTRAIT_STEPS", 28, 1, 80)


def portrait_size() -> int:
    return _int_value("WILDMAGIC_PORTRAIT_SIZE", 768, 256, 1280)


def portrait_quant() -> str:
    """Weight quantization for the portrait model: 'int8' (default), 'fp8', or 'none'.
    int8 halves SDXL's big modules so it fits the Arc's 8GB without spilling to shared
    memory; falls back to bf16 automatically if torchao can't apply it."""
    value = (get_config_value("WILDMAGIC_PORTRAIT_QUANT", "int8") or "int8").lower()
    return value if value in {"int8", "fp8", "none"} else "int8"


def portrait_free_vram() -> bool:
    """Whether the portrait worker should evict resident Ollama models before
    generating. On a small (e.g. 8GB) shared GPU, SDXL and a resident LLM overcommit
    VRAM -> black images or a driver device-loss; freeing the GPU first avoids that.
    The LLM reloads on the next spell. Default on."""
    return _bool_value("WILDMAGIC_PORTRAIT_FREE_VRAM", True)


load_environment()
