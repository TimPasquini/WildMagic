from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "wildmagic"
CONFIG_MODULE = PACKAGE_ROOT / "config.py"


def _python_modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.glob("*.py"))


def _string_argument(call: ast.Call) -> str | None:
    if not call.args:
        return None
    value = call.args[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _direct_wildmagic_environment_reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        key: str | None = None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            owner = node.func.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "os"
                and owner.attr == "environ"
            ):
                key = _string_argument(node)
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "getenv"
        ):
            key = _string_argument(node)

        if key and key.startswith("WILDMAGIC_"):
            findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} reads {key}")

    return findings


def test_shared_configuration_module_exists() -> None:
    assert importlib.util.find_spec("wildmagic.config") is not None, (
        "wildmagic.config must own .env loading, defaults, typed parsing, "
        "fallback chains, and persisted updates."
    )


def test_wildmagic_environment_reads_are_owned_by_config_module() -> None:
    findings = [
        finding
        for path in _python_modules()
        if path != CONFIG_MODULE
        for finding in _direct_wildmagic_environment_reads(path)
    ]

    assert findings == [], (
        "WILDMAGIC_* settings must be resolved through wildmagic.config.\n"
        + "\n".join(findings)
    )


def test_legacy_json_configuration_is_removed() -> None:
    legacy_path = REPO_ROOT / "wildmagic_config.json"
    references = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_modules()
        if "wildmagic_config.json" in path.read_text(encoding="utf-8")
    ]

    assert not legacy_path.exists(), (
        "wildmagic_config.json is a competing persisted source of truth"
    )
    assert references == [], (
        f"production modules still reference wildmagic_config.json: {references}"
    )


def test_model_default_is_not_owned_by_provider_or_ui_modules() -> None:
    findings: list[str] = []
    for path in _python_modules():
        if path == CONFIG_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(target, ast.Name) and target.id == "DEFAULT_MODEL"
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            ):
                findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert findings == [], (
        "The model fallback/default must be owned by wildmagic.config, not provider or UI modules: "
        + ", ".join(findings)
    )


def test_mock_session_uses_mock_town_provider(monkeypatch) -> None:
    from wildmagic.actions import GameSession

    monkeypatch.setenv("WILDMAGIC_TOWN_PROVIDER", "ollama")

    session = GameSession(seed=7, scenario="frontier", provider_name="mock")

    assert session.engine.town_provider.name == "mock"
