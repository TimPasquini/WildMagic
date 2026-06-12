from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .lore import MockLoreProvider, OllamaLoreProvider, resolve_lore_extraction
from .promises import WorldPromise, bind_promise


def score_binding_funnel(promise: WorldPromise) -> dict[str, Any]:
    """Push one extracted promise through the deterministic binding funnel in isolation
    (fresh world: only the origin zone explored, no reservations) and report each stage.

    Under always-honor the binder is the safety gate, so the eval surfaces exactly what
    it would commit the world to: a usable spatial hint, a matched blueprint, a bound
    zone — or flavor."""
    # Read the extractor's spatial output before bind_promise back-fills claimed_space
    # from fallback text — usable_where measures extraction quality, not binder rescue.
    claimed_mode = promise.claimed_space.mode if promise.claimed_space else None
    reservation = bind_promise(promise, explored_zones={(0, 0)}, reserved_counts={})
    return {
        "usable_where": claimed_mode in {"direction", "terrain", "zone"},
        "claimed_mode": claimed_mode,
        "blueprint": promise.binding.blueprint if promise.binding else None,
        "bound": reservation is not None,
        "bound_zone": list(reservation.zone) if reservation else None,
        "status": promise.status,
    }


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
        promise_rows = []
        for promise in resolution.promises:
            promise_dict = promise.to_dict()
            promise_rows.append({"promise": promise_dict, "binding": score_binding_funnel(promise)})
        eval_rows.append(
            {
                "source_model": row.get("model"),
                "npc": row.get("npc"),
                "message": row.get("message"),
                "reply": row.get("reply"),
                "provider": resolution.provider_name,
                "technical_failure": resolution.technical_failure,
                "error": resolution.error,
                "promises": promise_rows,
            }
        )

    promise_count = sum(len(row["promises"]) for row in eval_rows)
    failures = sum(1 for row in eval_rows if row["technical_failure"])
    bindings = [entry["binding"] for row in eval_rows for entry in row["promises"]]
    usable_where = sum(1 for binding in bindings if binding["usable_where"])
    blueprint_matched = sum(1 for binding in bindings if binding["blueprint"])
    bound = sum(1 for binding in bindings if binding["bound"])
    # Bound promises commit the world to build something; review these rows by hand for
    # false bindings (poetic or hedged claims that should have stayed flavor).
    bound_rows = [
        {
            "npc": row["npc"],
            "reply": row["reply"],
            "subject": entry["promise"]["subject"],
            "text": entry["promise"]["text"],
            "confidence": entry["promise"]["confidence"],
            "blueprint": entry["binding"]["blueprint"],
            "bound_zone": entry["binding"]["bound_zone"],
        }
        for row in eval_rows
        for entry in row["promises"]
        if entry["binding"]["bound"]
    ]
    report = {
        "input": str(input_path),
        "provider": provider_name,
        "model": model,
        "dialogue_rows": len(eval_rows),
        "promise_count": promise_count,
        "technical_failures": failures,
        "funnel": {
            "promises": len(bindings),
            "usable_where": usable_where,
            "usable_where_rate": round(usable_where / len(bindings), 3) if bindings else None,
            "blueprint_matched": blueprint_matched,
            "blueprint_match_rate": round(blueprint_matched / len(bindings), 3) if bindings else None,
            "bound": bound,
            "binding_rate": round(bound / len(bindings), 3) if bindings else None,
        },
        "bound_for_review": bound_rows,
        "rows": eval_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract lore promises from saved dialogue eval logs.")
    parser.add_argument("--input", default="logs/dialogue_eval/requested_models.json", type=Path)
    parser.add_argument("--output", default="logs/lore_eval/report.json", type=Path)
    parser.add_argument("--provider", choices=["mock", "ollama"], default="ollama")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    report = run_lore_eval(args.input, args.output, args.provider, args.model)
    print(f"Read {report['dialogue_rows']} dialogue rows")
    print(f"Extracted {report['promise_count']} promise(s); technical failures: {report['technical_failures']}")
    funnel = report["funnel"]
    print(
        f"Funnel: usable-where {funnel['usable_where']}/{funnel['promises']}"
        f" | blueprint {funnel['blueprint_matched']}/{funnel['promises']}"
        f" | bound {funnel['bound']}/{funnel['promises']}"
    )
    print(f"Bound promises to review for false bindings: {len(report['bound_for_review'])}")
    print(f"Wrote {args.output}")
    return 0 if report["technical_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
