from __future__ import annotations

import re
from typing import Any

from .config import fallbacks_enabled


def fallback_resolution_from_spell(spell: str) -> dict[str, Any] | None:
    force_wave = _force_wave_fallback_from_spell(spell)
    if force_wave is not None:
        return force_wave
    delayed_arrival = _delayed_arrival_fallback_from_spell(spell)
    if delayed_arrival is not None:
        return delayed_arrival
    return None


def bias_resolution_for_profile(
    resolution: dict[str, Any] | None, caster_profile: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Apply the caster's stats to a *non-LLM* resolution so character stats still bite
    when no model shaped the result. The LLM path gets the same intent through
    prompts.caster_prompt_block instead — only deterministic resolutions (local
    fallbacks) are biased here, to avoid double-counting.

    Attunement scales effect magnitudes; low Composure makes the wild bite back with an
    extra strain cost. Deliberately light — see docs/CHARACTER_CREATION.md."""
    if not resolution or not caster_profile:
        return resolution
    attunement = int(caster_profile.get("attunement", 3))
    composure = int(caster_profile.get("composure", 3))

    factor = 1.25 if attunement >= 5 else 0.8 if attunement <= 2 else 1.0
    if factor != 1.0:
        for effect in resolution.get("effects", []):
            amount = effect.get("amount") if isinstance(effect, dict) else None
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                effect["amount"] = max(1, round(amount * factor))

    if composure <= 2:
        costs = resolution.setdefault("costs", [])
        if len(costs) < 8:
            costs.append({"type": "status", "status": "strained", "duration": 3})
    return resolution


def _delayed_arrival_fallback_from_spell(spell: str) -> dict[str, Any] | None:
    effect = _delayed_arrival_effect_from_text(spell)
    if effect is None:
        return None
    name = str(effect.get("name") or "something")
    return {
        "accepted": True,
        "severity": "moderate",
        "outcome_text": f"The spell takes a simpler shape: {name} is due soon.",
        "effects": [effect],
        "costs": [
            {"type": "mana", "amount": 3},
            {"type": "status", "status": "marked", "duration": 4},
        ],
        "rejected_reason": None,
        "fallback": "local_delayed_arrival",
    }


def _delayed_arrival_effect_from_text(text: str) -> dict[str, Any] | None:
    normalized = text.lower().strip()
    if not normalized:
        return None
    digit_turns = re.search(r"\b(?:in|after)\s+(\d+)\s+turn", normalized)
    if digit_turns:
        turns = int(digit_turns.group(1))
    else:
        word_numbers = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        word_turns = re.search(
            r"\b(?:in|after)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+turn",
            normalized,
        )
        turns = word_numbers.get(word_turns.group(1), 3) if word_turns else 3
    if not any(
        word in normalized for word in ["arrive", "appears", "appear", "summon"]
    ):
        return None

    name = "debt collector"
    for pattern in [
        r"scheduled\s+(?:a|an|the)?\s*([a-z][a-z _-]+?)\s+to\s+arrive",
        r"(?:a|an|the)\s+([a-z][a-z _-]+?)\s+(?:should|will|shall)?\s*arrive",
        r"summon\s+(?:a|an|the)?\s*([a-z][a-z _-]+)",
    ]:
        match = re.search(pattern, normalized)
        if match:
            candidate = " ".join(match.group(1).split())
            if candidate:
                name = candidate[:40]
                break
    faction = (
        "ally"
        if any(word in normalized for word in ["friendly", "ally", "helpful"])
        else "enemy"
    )
    return {
        "type": "schedule_event",
        "turns": turns,
        "event_type": "summon",
        "name": name,
        "char": _effect_char(name),
        "hp": 8,
        "attack": 3,
        "faction": faction,
    }


def _force_wave_fallback_from_spell(spell: str) -> dict[str, Any] | None:
    normalized = spell.lower().strip()
    if not normalized:
        return None
    has_impulse = any(
        word in normalized
        for word in [
            "shockwave",
            "shock wave",
            "stomp",
            "quake",
            "tremor",
            "blast",
            "force wave",
            "pressure wave",
            "thunderclap",
            "concussive",
        ]
    )
    has_target = any(
        word in normalized
        for word in [
            "enemy",
            "enemies",
            "foe",
            "foes",
            "target",
            "monster",
            "creature",
            "goblin",
            "slime",
        ]
    )
    if not (has_impulse and has_target):
        return None

    area = any(
        word in normalized
        for word in ["across", "rolling", "all", "room", "floor", "wave"]
    )
    if area:
        effects = [
            {
                "type": "area_damage",
                "target": "player",
                "radius": 4,
                "amount": 3,
                "damage_type": "force",
                "include_player": False,
                "affects": "enemies",
            },
            {
                "type": "push",
                "target": "all_enemies",
                "origin": "player",
                "distance": 2,
            },
        ]
        costs = [
            {"type": "mana", "amount": 5},
            {"type": "status", "status": "slowed", "duration": 1},
        ]
    else:
        effects = [
            {
                "type": "damage",
                "target": "nearest_enemy",
                "amount": 4,
                "damage_type": "force",
            },
            {
                "type": "push",
                "target": "nearest_enemy",
                "origin": "player",
                "distance": 3,
            },
        ]
        costs = [{"type": "mana", "amount": 4}]
    return {
        "accepted": True,
        "severity": "moderate",
        "outcome_text": "The spell takes a simpler shape: a hard wave of force rolls outward.",
        "effects": effects,
        "costs": costs,
        "rejected_reason": None,
        "fallback": "local_force_wave",
    }


def _effect_char(name: str) -> str:
    for char in name:
        if char.isascii() and char.isalpha():
            return char.lower()
    return "e"
