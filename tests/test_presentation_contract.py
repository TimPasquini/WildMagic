from __future__ import annotations

import ast
from pathlib import Path

from wildmagic.actions import GameSession
from wildmagic.cli import render_screen


ROOT = Path(__file__).resolve().parents[1]


def test_frontends_do_not_import_equipment_mechanics() -> None:
    forbidden: list[str] = []
    for relative in ("wildmagic/ui.py", "wildmagic/cli.py"):
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            names = {alias.name for alias in node.names}
            if node.module in {"equipment", "items"}:
                forbidden.append(
                    f"{relative}:{node.lineno} imports wildmagic.{node.module}"
                )
            if node.module == "game_data" and "EQUIPMENT_SPECS" in names:
                forbidden.append(f"{relative}:{node.lineno} imports EQUIPMENT_SPECS")

    assert forbidden == [], (
        "Frontends must consume GameSession.equipment_inventory_view(), not equipment "
        "rules:\n" + "\n".join(forbidden)
    )


def test_cli_renders_shared_equipment_projection() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    try:
        session.engine.state.inventory["emberglass wand"] = 1
        session.execute_command("equip emberglass wand")
        session.execute_command("focus weapon")

        output = render_screen(session)

        assert "Equipment: weapon: emberglass wand [focus]" in output
        assert "Gold: " in output
        inventory_line = output.split("Inventory: ", 1)[1].splitlines()[0]
        assert "emberglass wand" not in inventory_line
    finally:
        session.close()
