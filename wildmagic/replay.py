from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .actions import GameSession, summarize_state


@dataclass
class ReplayResult:
    path: Path
    action_count: int
    matched: bool
    final_summary: dict[str, Any]
    expected_summary: dict[str, Any] | None


def save_replay(session: GameSession, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.to_replay(), indent=2, sort_keys=True), encoding="utf-8")


def load_replay(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_replay(path: Path) -> ReplayResult:
    data = load_replay(path)
    session = GameSession(
        seed=data.get("seed"),
        scenario=data.get("scenario", "dungeon"),
        provider_name="mock",
    )
    actions = data.get("actions", [])
    for action in actions:
        session.execute_command(
            str(action.get("command") or ""),
            replay_wild_magic=action.get("wild_magic"),
        )
    final_summary = summarize_state(session.engine)
    expected_summary = data.get("final_summary")
    matched = expected_summary is None or final_summary == expected_summary
    return ReplayResult(path, len(actions), matched, final_summary, expected_summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a Wild Magic run.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    result = run_replay(args.path)
    print(f"Replayed {result.action_count} action(s) from {result.path}")
    print(f"Final summary matched: {result.matched}")
    if not result.matched:
        print("Expected:")
        print(json.dumps(result.expected_summary, indent=2, sort_keys=True))
        print("Actual:")
        print(json.dumps(result.final_summary, indent=2, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
