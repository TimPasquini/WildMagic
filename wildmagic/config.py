from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv, set_key


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_MODEL = "qwen3.5:9b-q4_K_M"
DEFAULT_PROVIDER = "ollama"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

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
    }
    return aliases.get(normalized, normalized)


def _route_key(purpose: str | None) -> str | None:
    key = _purpose_key(purpose)
    if key in {"WILD", "DIALOGUE", "TRADE"}:
        return "URGENT"
    if key == "TOWN":
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


def _scoped_int(purpose: str | None, suffix: str, default: int, minimum: int, maximum: int) -> int:
    value = _first_config_value(_scoped_keys(purpose, suffix), str(default))
    try:
        parsed = int(value)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _scoped_float(purpose: str | None, suffix: str, default: float, minimum: float, maximum: float) -> float:
    value = _first_config_value(_scoped_keys(purpose, suffix), str(default))
    try:
        parsed = float(value)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _scoped_bool(purpose: str | None, suffix: str, default: bool) -> bool:
    value = _first_config_value(_scoped_keys(purpose, suffix), "1" if default else "0").lower().strip()
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


def get_wild_magic_provider() -> str:
    return (get_config_value("WILDMAGIC_PROVIDER", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).lower()


def get_dialogue_provider() -> str:
    return (get_config_value("WILDMAGIC_DIALOGUE_PROVIDER") or get_wild_magic_provider()).lower()


def get_trade_provider() -> str:
    return (get_config_value("WILDMAGIC_TRADE_PROVIDER") or get_dialogue_provider()).lower()


def get_town_provider() -> str:
    return (get_config_value("WILDMAGIC_TOWN_PROVIDER") or get_wild_magic_provider()).lower()


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


def ollama_town_num_predict() -> int:
    return _int_value("WILDMAGIC_TOWN_NUM_PREDICT", 2000, 256, 8192)


def ollama_num_gpu(purpose: str | None = None) -> int:
    return _scoped_int(purpose, "OLLAMA_NUM_GPU", 999, 0, 999)


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


def audit_dir() -> Path:
    return Path(get_config_value("WILDMAGIC_AUDIT_DIR", "logs") or "logs")


load_environment()
