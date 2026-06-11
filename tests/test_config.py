from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from wildmagic import config


MODEL_KEYS = (
    "WILDMAGIC_MODEL",
    "WILDMAGIC_WILD_MODEL",
    "WILDMAGIC_DIALOGUE_MODEL",
    "WILDMAGIC_TRADE_MODEL",
    "WILDMAGIC_TOWN_MODEL",
)
PROVIDER_KEYS = (
    "WILDMAGIC_PROVIDER",
    "WILDMAGIC_DIALOGUE_PROVIDER",
    "WILDMAGIC_TRADE_PROVIDER",
    "WILDMAGIC_TOWN_PROVIDER",
)
OLLAMA_ROUTE_KEYS = (
    "OLLAMA_HOST",
    "WILDMAGIC_OLLAMA_HOST",
    "WILDMAGIC_URGENT_OLLAMA_HOST",
    "WILDMAGIC_BACKGROUND_OLLAMA_HOST",
    "WILDMAGIC_WILD_OLLAMA_HOST",
    "WILDMAGIC_DIALOGUE_OLLAMA_HOST",
    "WILDMAGIC_TRADE_OLLAMA_HOST",
    "WILDMAGIC_TOWN_OLLAMA_HOST",
    "WILDMAGIC_OLLAMA_NUM_GPU",
    "WILDMAGIC_BACKGROUND_OLLAMA_NUM_GPU",
    "WILDMAGIC_TOWN_OLLAMA_NUM_GPU",
)


def test_shell_environment_takes_precedence_over_dotenv(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("WILDMAGIC_MODEL=dotenv-model\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setenv("WILDMAGIC_MODEL", "shell-model")

    config.load_environment()

    assert config.get_wild_magic_model() == "shell-model"


def test_model_fallback_chains(monkeypatch) -> None:
    for key in MODEL_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert config.get_wild_magic_model() == config.DEFAULT_MODEL
    assert config.get_dialogue_model() == config.DEFAULT_MODEL
    assert config.get_trade_model() == config.DEFAULT_MODEL
    assert config.get_town_model() == config.DEFAULT_MODEL

    monkeypatch.setenv("WILDMAGIC_MODEL", "shared-model")
    assert config.get_wild_magic_model() == "shared-model"
    assert config.get_dialogue_model() == "shared-model"
    assert config.get_trade_model() == "shared-model"
    assert config.get_town_model() == "shared-model"

    monkeypatch.setenv("WILDMAGIC_WILD_MODEL", "wild-model")
    assert config.get_wild_magic_model() == "wild-model"
    assert config.get_dialogue_model() == "shared-model"
    assert config.get_town_model() == "shared-model"

    monkeypatch.setenv("WILDMAGIC_DIALOGUE_MODEL", "dialogue-model")
    assert config.get_dialogue_model() == "dialogue-model"
    assert config.get_trade_model() == "dialogue-model"
    assert config.get_town_model() == "shared-model"

    monkeypatch.setenv("WILDMAGIC_TRADE_MODEL", "trade-model")
    monkeypatch.setenv("WILDMAGIC_TOWN_MODEL", "town-model")
    assert config.get_trade_model() == "trade-model"
    assert config.get_town_model() == "town-model"


def test_provider_fallback_chains(monkeypatch) -> None:
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert config.get_wild_magic_provider() == config.DEFAULT_PROVIDER
    assert config.get_dialogue_provider() == config.DEFAULT_PROVIDER
    assert config.get_trade_provider() == config.DEFAULT_PROVIDER
    assert config.get_town_provider() == config.DEFAULT_PROVIDER

    monkeypatch.setenv("WILDMAGIC_PROVIDER", "mock")
    assert config.get_dialogue_provider() == "mock"
    assert config.get_trade_provider() == "mock"
    assert config.get_town_provider() == "mock"

    monkeypatch.setenv("WILDMAGIC_DIALOGUE_PROVIDER", "auto")
    assert config.get_dialogue_provider() == "auto"
    assert config.get_trade_provider() == "auto"
    assert config.get_town_provider() == "mock"


def test_typed_values_use_defaults_and_bounds(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_OLLAMA_TIMEOUT", "not-a-number")
    monkeypatch.setenv("WILDMAGIC_OLLAMA_NUM_CTX", "100")
    monkeypatch.setenv("WILDMAGIC_OLLAMA_TEMPERATURE", "9")
    monkeypatch.setenv("WILDMAGIC_OLLAMA_THINK", "yes")
    monkeypatch.setenv("WILDMAGIC_AUDIT_LOG", "off")

    assert config.ollama_timeout_seconds() == 180.0
    assert config.ollama_num_ctx() == 2048
    assert config.ollama_temperature() == 1.5
    assert config.ollama_thinking_enabled() is True
    assert config.audit_log_enabled() is False


def test_scoped_ollama_routing_precedence(monkeypatch) -> None:
    for key in OLLAMA_ROUTE_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert config.ollama_host("wild") == "http://localhost:11434"

    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11430")
    monkeypatch.setenv("WILDMAGIC_OLLAMA_HOST", "127.0.0.1:11431")
    monkeypatch.setenv("WILDMAGIC_URGENT_OLLAMA_HOST", "127.0.0.1:11432")
    monkeypatch.setenv("WILDMAGIC_BACKGROUND_OLLAMA_HOST", "127.0.0.1:11433")

    assert config.ollama_host("wild") == "http://127.0.0.1:11432"
    assert config.ollama_host("dialogue") == "http://127.0.0.1:11432"
    assert config.ollama_host("trade") == "http://127.0.0.1:11432"
    assert config.ollama_host("town") == "http://127.0.0.1:11433"
    assert config.ollama_host(None) == "http://127.0.0.1:11431"

    monkeypatch.setenv("WILDMAGIC_WILD_OLLAMA_HOST", "127.0.0.1:11434")
    monkeypatch.setenv("WILDMAGIC_TOWN_OLLAMA_HOST", "127.0.0.1:11435")
    assert config.ollama_host("wild") == "http://127.0.0.1:11434"
    assert config.ollama_host("town") == "http://127.0.0.1:11435"

    monkeypatch.setenv("WILDMAGIC_OLLAMA_NUM_GPU", "999")
    monkeypatch.setenv("WILDMAGIC_BACKGROUND_OLLAMA_NUM_GPU", "0")
    monkeypatch.setenv("WILDMAGIC_TOWN_OLLAMA_NUM_GPU", "2")
    assert config.ollama_num_gpu("wild") == 999
    assert config.ollama_num_gpu("town") == 2


def test_scoped_ollama_settings_follow_purpose_and_route_precedence(monkeypatch) -> None:
    monkeypatch.setenv("WILDMAGIC_OLLAMA_TIMEOUT", "100")
    monkeypatch.setenv("WILDMAGIC_URGENT_OLLAMA_TIMEOUT", "200")
    monkeypatch.setenv("WILDMAGIC_WILD_OLLAMA_TIMEOUT", "300")
    monkeypatch.setenv("WILDMAGIC_BACKGROUND_OLLAMA_NUM_GPU", "0")

    assert config.ollama_timeout_seconds("dialogue") == 200.0
    assert config.ollama_timeout_seconds("wild") == 300.0
    assert config.ollama_num_gpu("town") == 0


def test_set_config_value_updates_process_and_dotenv(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.delenv("WILDMAGIC_MODEL", raising=False)

    config.set_config_value("WILDMAGIC_MODEL", "local-model")

    assert os.environ["WILDMAGIC_MODEL"] == "local-model"
    assert dotenv_values(env_path)["WILDMAGIC_MODEL"] == "local-model"
