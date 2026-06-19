from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .models import Curse
from .normalize import clamp_int, coerce_list, normalize_id

if TYPE_CHECKING:
    from .engine import GameEngine


@dataclass(frozen=True)
class CurseTemplate:
    id: str
    name: str
    description: str
    semantic_prompt: str
    mechanics: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    xp_to_clear: int = 3


CURSE_TEMPLATES: dict[str, CurseTemplate] = {
    "close_curse": CurseTemplate(
        id="close_curse",
        name="Close Curse",
        description="Your magic can only affect things within 3 squares.",
        semantic_prompt="Keep the caster's magic cramped, intimate, and near-sighted.",
        mechanics={"max_distance": 3},
        tags=("range", "near", "mechanical"),
        xp_to_clear=4,
    ),
    "far_curse": CurseTemplate(
        id="far_curse",
        name="Far Curse",
        description="Your magic can only affect things more than 3 squares away.",
        semantic_prompt="Keep the caster's magic remote, indirect, and bad at touching what is near.",
        mechanics={"min_distance": 4},
        tags=("range", "distant", "mechanical"),
        xp_to_clear=4,
    ),
    "narrow_curse": CurseTemplate(
        id="narrow_curse",
        name="Narrow Curse",
        description="Your area spells cannot spread beyond a radius of 1.",
        semantic_prompt="Make the caster's magic threadlike, pinched, and unwilling to bloom outward.",
        mechanics={"max_radius": 1},
        tags=("area", "mechanical"),
        xp_to_clear=3,
    ),
    "straight_path_curse": CurseTemplate(
        id="straight_path_curse",
        name="Straight Path Curse",
        description="Your magic can only affect places you can see directly.",
        semantic_prompt="Make the caster's magic honest, line-bound, and unable to turn corners.",
        mechanics={"require_line_of_sight": True},
        tags=("sight", "mechanical"),
        xp_to_clear=3,
    ),
    "anchored_curse": CurseTemplate(
        id="anchored_curse",
        name="Anchored Curse",
        description="You cannot teleport or possess while this curse holds.",
        semantic_prompt="Make the caster's selfhood heavy, hooked, and reluctant to leave its place.",
        mechanics={"forbidden_effects": ["teleport", "possess"]},
        tags=("movement", "mechanical"),
        xp_to_clear=5,
    ),
    "wild_debt": CurseTemplate(
        id="wild_debt",
        name="Wild Debt",
        description="The wild expects repayment. Something is already on its way.",
        semantic_prompt="Let owed prices, collectors, balances, and consequence color the spell.",
        tags=("debt", "semantic"),
        xp_to_clear=5,
    ),
    "borrowed_trust": CurseTemplate(
        id="borrowed_trust",
        name="Borrowed Trust",
        description="Promises made by magic tend to come due.",
        semantic_prompt="Make bargains, loyalty, and trust feel borrowed rather than owned.",
        tags=("oath", "semantic"),
        xp_to_clear=4,
    ),
    "borrowed_body": CurseTemplate(
        id="borrowed_body",
        name="Borrowed Body",
        description="A body you did not grow remembers its old owner.",
        semantic_prompt="Let borrowed flesh, old habits, and bodily memory bend the result.",
        tags=("body", "semantic"),
        xp_to_clear=4,
    ),
}

_ALIASES = {
    "close": "close_curse",
    "near_curse": "close_curse",
    "short_curse": "close_curse",
    "far": "far_curse",
    "distant_curse": "far_curse",
    "narrow": "narrow_curse",
    "thin_curse": "narrow_curse",
    "straight": "straight_path_curse",
    "line_of_sight_curse": "straight_path_curse",
    "honest_path_curse": "straight_path_curse",
    "anchored": "anchored_curse",
    "tethered_curse": "anchored_curse",
}


def normalize_curse_id(value: str) -> str:
    key = normalize_id(value)
    return _ALIASES.get(key, key)


def curse_template(curse_id: str) -> CurseTemplate | None:
    return CURSE_TEMPLATES.get(normalize_curse_id(curse_id))


def curse_card(curse: Curse) -> dict[str, Any]:
    template = curse_template(curse.id)
    mechanics = dict(template.mechanics) if template else {}
    mechanics.update(curse.mechanics)
    tags = sorted({*(template.tags if template else ()), *curse.tags})
    semantic_prompt = (
        curse.semantic_prompt
        or (template.semantic_prompt if template else "")
        or curse.description
    )
    mode = "semantic"
    if mechanics and semantic_prompt:
        mode = "mixed"
    elif mechanics:
        mode = "mechanical"
    return {
        "id": curse.id,
        "name": curse.name,
        "description": curse.description,
        "stacks": curse.stacks,
        "mode": mode,
        "semantic_prompt": semantic_prompt,
        "mechanics": mechanics,
        "mechanical_limits": mechanical_limit_text(mechanics),
        "tags": tags,
        "xp_to_clear": curse.xp_to_clear,
        "clear_progress": curse.clear_progress,
    }


def mechanical_limit_text(mechanics: dict[str, Any]) -> list[str]:
    limits: list[str] = []
    if "max_distance" in mechanics:
        limits.append(f"only affects things within {mechanics['max_distance']} squares")
    if "min_distance" in mechanics:
        limits.append(
            f"only affects things at least {mechanics['min_distance']} squares away"
        )
    if "max_radius" in mechanics:
        limits.append(f"area radius cannot exceed {mechanics['max_radius']}")
    if mechanics.get("require_line_of_sight"):
        limits.append("targets and affected areas must be in line of sight")
    forbidden = [str(effect) for effect in mechanics.get("forbidden_effects", [])]
    if forbidden:
        limits.append("forbidden effects: " + ", ".join(sorted(forbidden)))
    return limits


def build_curse(payload: dict[str, Any], *, turn: int = 0) -> Curse:
    raw_name = str(payload.get("name") or payload.get("id") or "Nameless Curse")
    curse_id = normalize_curse_id(str(payload.get("id") or raw_name))
    template = curse_template(curse_id)
    name = str(payload.get("name") or (template.name if template else raw_name)).strip()
    if not name:
        name = curse_id.replace("_", " ").title()
    description = str(
        payload.get("description")
        or payload.get("text")
        or (
            template.description
            if template
            else "Reality now remembers you incorrectly."
        )
    ).strip()
    semantic_prompt = str(
        payload.get("semantic_prompt")
        or payload.get("flavor")
        or (template.semantic_prompt if template else description)
    ).strip()
    mechanics = dict(template.mechanics) if template else {}
    tags = {
        normalize_id(str(tag))
        for tag in coerce_list(payload.get("tags"))
        if str(tag).strip()
    }
    if template:
        tags.update(template.tags)
    if not mechanics:
        tags.add("semantic")
    xp_to_clear = clamp_int(
        payload.get("xp_to_clear")
        or payload.get("clear_cost")
        or (template.xp_to_clear if template else 3),
        1,
        99,
    )
    return Curse(
        id=curse_id,
        name=name[:60],
        description=description[:240],
        stacks=1,
        semantic_prompt=semantic_prompt[:240],
        mechanics=mechanics,
        tags=tags,
        xp_to_clear=xp_to_clear,
        source_turn=turn,
    )


def merge_curse(existing: Curse, incoming: Curse) -> Curse:
    existing.stacks += incoming.stacks
    if not existing.description and incoming.description:
        existing.description = incoming.description
    if not existing.semantic_prompt and incoming.semantic_prompt:
        existing.semantic_prompt = incoming.semantic_prompt
    existing.mechanics.update(incoming.mechanics)
    existing.tags.update(incoming.tags)
    existing.xp_to_clear = max(existing.xp_to_clear, incoming.xp_to_clear)
    return existing


def find_curse_key(curses: dict[str, Curse], query: str) -> str | None:
    needle = normalize_curse_id(query)
    if needle in curses:
        return needle
    for curse_id, curse in curses.items():
        if normalize_id(curse.name) == needle:
            return curse_id
    for curse_id, curse in curses.items():
        if needle and (needle in curse_id or needle in normalize_id(curse.name)):
            return curse_id
    return None


def validate_resolution_against_curses(
    engine: "GameEngine", resolution: dict[str, Any]
) -> str | None:
    active = [curse_card(curse) for curse in engine.state.curses.values()]
    mechanical = [card for card in active if card["mechanics"]]
    if not mechanical:
        return None
    for effect in coerce_list(resolution.get("effects")):
        if not isinstance(effect, dict):
            continue
        error = _validate_effect_against_curses(engine, effect, mechanical)
        if error:
            return error
    return None


def _validate_effect_against_curses(
    engine: "GameEngine", effect: dict[str, Any], curses: list[dict[str, Any]]
) -> str | None:
    effect_type = normalize_id(str(effect.get("type") or ""))
    for card in curses:
        mechanics = card["mechanics"]
        forbidden = {
            normalize_id(str(t)) for t in mechanics.get("forbidden_effects", [])
        }
        if effect_type in forbidden:
            return f"{card['name']} forbids {effect_type.replace('_', ' ')}."
    for nested in coerce_list(effect.get("effects") or effect.get("effect")):
        if isinstance(nested, dict):
            error = _validate_effect_against_curses(engine, nested, curses)
            if error:
                return error

    radius = _effect_radius(effect)
    positions = _effect_positions(engine, effect)
    player = engine.state.player
    for card in curses:
        mechanics = card["mechanics"]
        max_radius = mechanics.get("max_radius")
        if max_radius is not None and radius > clamp_int(max_radius, 0, 99):
            return f"{card['name']} pinches area magic down to radius {max_radius}."
        for x, y in positions:
            distance = max(abs(x - player.x), abs(y - player.y))
            if "max_distance" in mechanics:
                max_distance = clamp_int(mechanics["max_distance"], 0, 99)
                if distance + radius > max_distance:
                    return f"{card['name']} yanks the spell back before it reaches that far."
            if "min_distance" in mechanics:
                min_distance = clamp_int(mechanics["min_distance"], 0, 99)
                if max(0, distance - radius) < min_distance:
                    return f"{card['name']} refuses magic that close."
            if (
                mechanics.get("require_line_of_sight")
                and distance > 0
                and not engine.has_line_of_sight(player.x, player.y, x, y)
            ):
                return f"{card['name']} cannot bend around what you cannot see."
    return None


def _effect_radius(effect: dict[str, Any]) -> int:
    effect_type = normalize_id(str(effect.get("type") or ""))
    if effect_type in {
        "area_damage",
        "area_status",
        "create_tiles",
        "aura",
        "create_flow",
    }:
        return clamp_int(effect.get("radius"), 0, 99)
    return 0


def _effect_positions(
    engine: "GameEngine", effect: dict[str, Any]
) -> list[tuple[int, int]]:
    positions: list[tuple[int, int]] = []
    effect_type = normalize_id(str(effect.get("type") or ""))
    if "x" in effect and "y" in effect:
        positions.append(
            (
                clamp_int(effect.get("x"), 0, engine.state.width - 1),
                clamp_int(effect.get("y"), 0, engine.state.height - 1),
            )
        )
    for key in ("target", "center", "origin", "anchor", "source", "sink"):
        if key not in effect:
            continue
        group = engine.resolve_target_group(effect.get(key))
        if group:
            positions.extend((entity.x, entity.y) for entity in group)
            continue
        bound = engine.resolve_target(effect.get(key))
        if bound is not None:
            positions.append((bound.x, bound.y))
            continue
        bound_pos = engine.effect_position({"target": effect.get(key)})
        if bound_pos != (engine.state.player.x, engine.state.player.y) or key in {
            "target",
            "center",
        }:
            positions.append(bound_pos)
    if effect_type in {
        "area_damage",
        "area_status",
        "create_tile",
        "set_tile",
        "create_tiles",
        "create_flow",
        "aura",
    }:
        positions.append(engine.effect_position(effect))
    if effect_type == "teleport":
        positions.append(engine._teleport_destination(effect))
    if not positions and effect_type not in {"message", "add_curse", "set_flag"}:
        positions.append((engine.state.player.x, engine.state.player.y))
    return sorted(set(positions))
