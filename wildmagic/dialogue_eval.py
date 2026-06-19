"""Dialogue model comparison harness.

Runs fixed Hollowmere NPC prompts through the real dialogue provider path and
emits a compact JSON report plus a readable table.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .actions import GameSession
from .dialogue import OllamaDialogueProvider, resolve_dialogue


DEFAULT_MODELS = [
    "qwen3.5:9b-q4_K_M",
    "mannix/hermes-3-llama-3.1-8b:q4_k_m",
    "gurubot/llama3-lunaris:latest",
    "fluffy/llama-3.1-8b-stheno-v3.4:q4_K_M",
]

PROMPTS = [
    ("Old Maren", "Evening. Is it safe to stay here tonight?"),
    ("Old Maren", "What do you really think of the Empire?"),
    (
        "Quill Hatchet",
        "I found a strange brass moth in the dungeon. Is it worth anything?",
    ),
    ("Quill Hatchet", "Tell me a rumor that would get someone in trouble."),
    ("Sister Wren", "The dead below are restless. What should I listen for?"),
    ("Sister Wren", "Do the old saints hate wild magic?"),
    (
        "Captain Ressa Vane",
        "I can help defend Hollowmere, but my magic is not chartered.",
    ),
    ("Captain Ressa Vane", "If Imperial soldiers come for me, will you turn me over?"),
]

_GENERIC_PHRASES = {
    "hard to say",
    "i keep my head down",
    "mind my own business",
    "well met",
    "traveler",
    "if you say so",
}


def _npc_by_name(session: GameSession, name: str):
    for entity in session.engine.state.entities.values():
        if entity.kind == "npc" and entity.name == name:
            return entity
    raise KeyError(name)


def _score_reply(message: str, reply: str, context: dict[str, Any]) -> dict[str, Any]:
    npc = context.get("npc") or {}
    lower_reply = reply.lower()
    lower_message = message.lower().strip()
    words = re.findall(r"[A-Za-z']+", reply)
    flags: list[str] = []
    if not reply.strip():
        flags.append("empty")
    if lower_reply.strip().strip('"') == lower_message.strip('"'):
        flags.append("echo")
    if "*" in reply or reply.lstrip().startswith(("(", "[")):
        flags.append("stage_direction")
    if any(phrase in lower_reply for phrase in _GENERIC_PHRASES):
        flags.append("generic_phrase")
    if len(words) < 6:
        flags.append("too_short")
    if len(words) > 90:
        flags.append("too_long")
    if "?" in reply and len(words) < 18:
        flags.append("deflecting_question")

    grounding_terms = set()
    for key in ("name", "role", "backstory"):
        grounding_terms.update(re.findall(r"[a-z]{4,}", str(npc.get(key, "")).lower()))
    for trait in npc.get("traits") or []:
        grounding_terms.update(re.findall(r"[a-z]{4,}", str(trait).lower()))
    for bucket in (
        "things_i_personally_witnessed",
        "things_i_overheard",
        "gossip_i_have_heard",
    ):
        for memory in npc.get(bucket) or []:
            grounding_terms.update(re.findall(r"[a-z]{4,}", str(memory).lower()))
    conversation_memory = npc.get("conversation_memory")
    if isinstance(conversation_memory, dict):
        for memory in conversation_memory.get("older_summaries") or []:
            grounding_terms.update(re.findall(r"[a-z]{4,}", str(memory).lower()))
    grounding_hits = sorted(term for term in grounding_terms if term in lower_reply)

    score = 10
    score -= 2 * len(flags)
    score += min(3, len(grounding_hits))
    if 18 <= len(words) <= 65:
        score += 1
    return {
        "score": max(0, min(12, score)),
        "flags": flags,
        "word_count": len(words),
        "grounding_hits": grounding_hits[:8],
    }


def run_dialogue_eval(
    models: list[str], output_path: Path, seed: int
) -> dict[str, Any]:
    os.environ.setdefault("WILDMAGIC_DIALOGUE_PROVIDER", "ollama")
    rows: list[dict[str, Any]] = []
    for model in models:
        provider = OllamaDialogueProvider(model=model)
        for npc_name, message in PROMPTS:
            session = GameSession(
                seed=seed, scenario="town", dialogue_provider=provider
            )
            npc = _npc_by_name(session, npc_name)
            context = session.engine.dialogue_context_for_llm(npc, message)
            started = time.perf_counter()
            resolution = resolve_dialogue(provider, npc.name, message, context)
            latency = time.perf_counter() - started
            reply = resolution.reply or ""
            scored = _score_reply(message, reply, context)
            rows.append(
                {
                    "model": model,
                    "npc": npc_name,
                    "message": message,
                    "reply": reply,
                    "technical_failure": resolution.technical_failure,
                    "error": resolution.error,
                    "latency_s": round(latency, 3),
                    **scored,
                }
            )

    report = {"seed": seed, "models": models, "rows": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def print_summary(report: dict[str, Any]) -> None:
    rows = report["rows"]
    print("DIALOGUE EVAL")
    print(f"rows={len(rows)} seed={report['seed']}")
    print()
    for model in report["models"]:
        model_rows = [row for row in rows if row["model"] == model]
        if not model_rows:
            continue
        failures = sum(1 for row in model_rows if row["technical_failure"])
        avg_score = sum(row["score"] for row in model_rows) / len(model_rows)
        avg_latency = sum(row["latency_s"] for row in model_rows) / len(model_rows)
        flags = Counter(flag for row in model_rows for flag in row["flags"])
        flag_text = (
            ", ".join(f"{flag} x{count}" for flag, count in flags.most_common())
            or "none"
        )
        print(f"{model}")
        print(
            f"  avg_score={avg_score:.2f}/12  avg_latency={avg_latency:.2f}s  failures={failures}"
        )
        print(f"  flags: {flag_text}")
        for row in model_rows:
            preview = row["reply"].replace("\n", " ")[:150]
            print(f"  {row['score']:2d}/12 {row['npc']}: {preview}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", action="append", dest="models", help="Ollama model tag; repeatable"
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", default="logs/dialogue_eval/report.json")
    args = parser.parse_args(argv)

    models = args.models or list(DEFAULT_MODELS)
    report = run_dialogue_eval(models, Path(args.output), args.seed)
    print_summary(report)
    print(f"report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
