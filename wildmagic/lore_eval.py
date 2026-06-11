from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .lore import MockLoreProvider, OllamaLoreProvider, resolve_lore_extraction


def run_lore_eval(input_path: Path, output_path: Path, provider_name: str, model: str | None) -> dict[str, Any]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    if provider_name == "mock":
        provider = MockLoreProvider()
    else:
        provider = OllamaLoreProvider(model=model)

    eval_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        context = {
            "npc": row.get("npc") or "unknown",
            "turn": index,
            "location": "dialogue_eval",
            "zone": {"x": 0, "y": 0, "type": "eval"},
            "message": row.get("message") or "",
            "reply": row.get("reply") or "",
            "existing_lore": [],
        }
        resolution = resolve_lore_extraction(provider, context)
        eval_rows.append(
            {
                "source_model": row.get("model"),
                "npc": row.get("npc"),
                "message": row.get("message"),
                "reply": row.get("reply"),
                "provider": resolution.provider_name,
                "technical_failure": resolution.technical_failure,
                "error": resolution.error,
                "claims": [claim.to_dict() for claim in resolution.claims],
            }
        )

    claim_count = sum(len(row["claims"]) for row in eval_rows)
    failures = sum(1 for row in eval_rows if row["technical_failure"])
    report = {
        "input": str(input_path),
        "provider": provider_name,
        "model": model,
        "dialogue_rows": len(eval_rows),
        "claim_count": claim_count,
        "technical_failures": failures,
        "rows": eval_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract lore claims from saved dialogue eval logs.")
    parser.add_argument("--input", default="logs/dialogue_eval/requested_models.json", type=Path)
    parser.add_argument("--output", default="logs/lore_eval/report.json", type=Path)
    parser.add_argument("--provider", choices=["mock", "ollama"], default="ollama")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    report = run_lore_eval(args.input, args.output, args.provider, args.model)
    print(f"Read {report['dialogue_rows']} dialogue rows")
    print(f"Extracted {report['claim_count']} claim(s); technical failures: {report['technical_failures']}")
    print(f"Wrote {args.output}")
    return 0 if report["technical_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
