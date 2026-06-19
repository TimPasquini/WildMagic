"""Effect registry: one metadata home per wild-magic effect (Stage 4 of the state-surface plan).

Effect knowledge is spread across the contract (`SUPPORTED_EFFECTS`), the capability cards
(which effects a routed card unlocks), the resolver prompt, normalization aliases, the
handlers in `effects.py`, the schema doc, and tests. This module is the single discoverable
record of *what each effect is*: its canonical name, the alias type-strings that normalize to
it, whether it is a universal core effect or owned by capability cards, the context slices it
needs, and a one-line summary plus the JSON fields it reads.

It does not move handler logic — `effects._apply_effect` still owns application and runs through
the same transactional path. The registry is metadata + a coverage contract: tests assert the
registry, `SUPPORTED_EFFECTS`, the capability cards, and the schema doc cannot drift apart.

`EFFECT_TYPE_ALIASES` (alias type-string -> canonical effect) lives here now and is imported
by `resolution_parsing.py`, so the alias map and the registry share one source.

Imports only `spell_contract` and `capabilities` (both leaf-ish); never `engine`/`effects`/
`resolution_parsing`, so it stays acyclic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import capabilities as cap
from .spell_contract import SUPPORTED_EFFECTS


# Alias type-strings the LLM might emit, mapped to the canonical effect. Moved here from
# resolution_parsing.py so the registry is the single home for effect-name knowledge.
EFFECT_TYPE_ALIASES: dict[str, str] = {
    "healing": "heal",
    "restore_health": "heal",
    "restore_hp": "heal",
    "regenerate": "add_status",
    "regeneration": "add_status",
    "regen": "add_status",
    "restore_mana_points": "restore_mana",
    "restore_mp": "restore_mana",
    "replenish_mana": "restore_mana",
    "status": "add_status",
    "apply_status": "add_status",
    "give_status": "add_status",
    "set_status": "add_status",
    "status_effect": "add_status",
    "curse": "add_curse",
    "spawn": "summon",
    "create_creature": "conjure_creature",
    "spawn_creature": "conjure_creature",
    "create_item": "conjure_item",
    "spawn_item": "conjure_item",
    "place_tile": "create_tiles",
    "set_tiles": "create_tiles",
    "tile_effect": "create_tiles",
    "area_effect": "area_damage",
    "explosion": "area_damage",
    "blast": "area_damage",
    "trigger": "create_trigger",
    "ward": "create_trigger",
    "reaction": "create_trigger",
    "contingency": "create_trigger",
    "delayed_reaction": "create_trigger",
    "delay_damage": "delay_incoming",
    "delay_incoming_damage": "delay_incoming",
    "postpone_damage": "delay_incoming",
    "defer_damage": "delay_incoming",
    "accelerate_poison": "accelerate_status",
    "accelerate_ticks": "accelerate_status",
    "burst_status": "accelerate_status",
    "behavior": "set_behavior",
    "set_ai": "set_behavior",
    "set_ai_behavior": "set_behavior",
    "force_behavior": "set_behavior",
    "compel_behavior": "set_behavior",
    "make_dance": "set_behavior",
    "flow": "create_flow",
    "flow_field": "create_flow",
    "create_current": "create_flow",
    "conveyor": "create_flow",
    "wind_field": "create_flow",
    "gravity_well": "create_flow",
    "prophecy": "create_promise",
    "prophesy": "create_promise",
    "foretell": "create_promise",
    "promise": "create_promise",
    "create_prophecy": "create_promise",
}


@dataclass(frozen=True)
class EffectSpec:
    """Metadata for one effect. `core` and `cards` describe routing (a core effect is
    emittable on every cast; a card-owned effect is unlocked only when its capability card
    is routed). `aliases` are the alternate type-strings that normalize to `name`.
    `schema_fields` is a documentary list of the JSON fields the handler reads."""

    name: str
    summary: str
    core: bool
    cards: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    required_context: tuple[str, ...] = ()
    schema_fields: tuple[str, ...] = field(default_factory=tuple)


# Per-effect summary + the JSON fields each handler reads (documentary; see effects.py for
# the authoritative application logic). Keyed by canonical effect name.
_EFFECT_META: dict[str, tuple[str, tuple[str, ...]]] = {
    "damage": ("Damage one target.", ("target", "amount", "damage_type")),
    "area_damage": (
        "Damage entities in a radius.",
        (
            "target",
            "center",
            "x",
            "y",
            "radius",
            "amount",
            "damage_type",
            "hollow",
            "affects",
            "include_player",
        ),
    ),
    "area_status": (
        "Apply a status to entities in a radius.",
        ("target", "center", "x", "y", "radius", "status", "duration"),
    ),
    "heal": ("Restore HP.", ("target", "amount")),
    "restore_mana": ("Restore mana.", ("target", "amount")),
    "teleport": (
        "Move an entity to a specific tile.",
        ("target", "x", "y", "placement"),
    ),
    "push": ("Shove targets away from a point.", ("target", "distance", "x", "y")),
    "pull": ("Drag targets toward a point.", ("target", "distance", "x", "y")),
    "create_tile": (
        "Write terrain on a tile.",
        ("tile", "target", "x", "y", "duration", "tags"),
    ),
    "set_tile": (
        "Set the terrain of a tile.",
        ("tile", "target", "x", "y", "duration", "tags"),
    ),
    "create_tiles": (
        "Write terrain on one or more tiles, optionally in a shape.",
        (
            "tile",
            "tiles",
            "target",
            "x",
            "y",
            "radius",
            "hollow",
            "shape",
            "duration",
            "tags",
        ),
    ),
    "add_status": (
        "Apply a status to a target.",
        ("target", "status", "duration", "display_name", "expiry_text"),
    ),
    "remove_status": ("Remove a status (or all) from a target.", ("target", "status")),
    "summon": (
        "Summon creatures into the scene.",
        ("name", "creature", "count", "faction", "placement"),
    ),
    "spawn_item": (
        "Create an item on the floor.",
        ("name", "item_type", "x", "y", "quantity"),
    ),
    "conjure_item": (
        "Create an item from a safe template with a creative name/material/tags.",
        ("template", "name", "material", "tags", "count", "placement"),
    ),
    "conjure_creature": (
        "Create creatures from a safe template with creative name/faction/tags.",
        (
            "template",
            "name",
            "faction",
            "count",
            "tags",
            "hp",
            "attack",
            "defense",
            "placement",
            "aura",
        ),
    ),
    "transform_item": (
        "Alter an item or prop into a new item form.",
        (
            "target",
            "item",
            "name",
            "new_name",
            "material",
            "item_type",
            "description",
            "tags",
        ),
    ),
    "modify_inventory": (
        "Add, remove, or set carried item counts.",
        ("item", "count", "op"),
    ),
    "transform_entity": (
        "Alter actor stats, name, glyph, material, or tags.",
        (
            "target",
            "name",
            "char",
            "material",
            "hp",
            "max_hp",
            "attack",
            "defense",
            "tags",
        ),
    ),
    "edit_memory": (
        "Add, alter, or erase a nearby NPC memory.",
        ("target", "op", "text", "subject"),
    ),
    "animate_object": (
        "Turn a nearby prop into an actor.",
        ("target", "name", "hp", "faction", "tags"),
    ),
    "aura": (
        "Attach an ongoing damage or status emanation to an entity or tile.",
        ("target", "x", "y", "kind", "status", "amount", "radius", "duration"),
    ),
    "add_trait": (
        "Attach a soft narrative trait to an entity.",
        ("target", "trait", "text"),
    ),
    "change_faction": ("Change an entity's faction.", ("target", "faction")),
    "possess": ("Move player control into another living body.", ("target",)),
    "add_tag": ("Add tags to an entity.", ("target", "tag", "tags")),
    "remove_tag": ("Remove tags from an entity.", ("target", "tag", "tags")),
    "add_resistance": (
        "Add a damage resistance to an entity.",
        ("target", "damage_type", "amount"),
    ),
    "add_weakness": (
        "Add a damage weakness to an entity.",
        ("target", "damage_type", "amount"),
    ),
    "set_flag": ("Set a persistent world flag.", ("flag", "value", "duration")),
    "schedule_event": (
        "Create a delayed event or payload that fires after N turns.",
        ("turns", "event_type", "effects", "costs", "text", "name"),
    ),
    "delay_incoming": (
        "Capture incoming damage on a target and release it later.",
        ("target", "turns", "duration", "name"),
    ),
    "accelerate_status": (
        "Resolve the remaining damaging ticks of a status immediately.",
        ("target", "status"),
    ),
    "set_behavior": (
        "Apply a temporary AI behavior modifier to one or more creatures.",
        (
            "target",
            "behavior",
            "duration",
            "turns",
            "behavior_target",
            "focus",
            "lock_to",
            "duel_target",
            "mimic_target",
        ),
    ),
    "create_flow": (
        "Create a temporary tile drift field that moves entities each turn.",
        (
            "target",
            "center",
            "x",
            "y",
            "radius",
            "shape",
            "tiles",
            "dx",
            "dy",
            "direction",
            "mode",
            "duration",
            "turns",
        ),
    ),
    "create_trigger": (
        "Create a charged reaction that fires when a later event happens.",
        ("trigger", "on", "target", "when", "charges", "duration", "effects", "name"),
    ),
    "create_persistent_effect": (
        "Create an anchored trigger such as a sympathetic link or ward.",
        ("target", "trigger", "effects", "duration", "name"),
    ),
    "create_promise": (
        "Speak a prophecy, rumor, threat, or place claim into the promise ledger.",
        ("text", "kind", "subject", "location", "direction"),
    ),
    "add_curse": ("Add a curse as an effect.", ("name", "description", "stacks")),
    "message": ("Add log text.", ("text", "spoof")),
}


def _aliases_by_effect() -> dict[str, tuple[str, ...]]:
    reverse: dict[str, list[str]] = {}
    for alias, canonical in EFFECT_TYPE_ALIASES.items():
        reverse.setdefault(canonical, []).append(alias)
    return {canonical: tuple(sorted(names)) for canonical, names in reverse.items()}


def _owners() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """effect -> (owning card names, union of those cards' required_context)."""
    cards: dict[str, list[str]] = {}
    ctx: dict[str, set[str]] = {}
    for card in cap.CAPABILITY_CARDS:
        for effect in card.effect_types:
            cards.setdefault(effect, []).append(card.name)
            ctx.setdefault(effect, set()).update(card.required_context)
    return {
        effect: (tuple(sorted(names)), tuple(sorted(ctx.get(effect, set()))))
        for effect, names in cards.items()
    }


def _build_registry() -> dict[str, EffectSpec]:
    aliases = _aliases_by_effect()
    owners = _owners()
    core = set(cap.CORE_EFFECT_TYPES)
    registry: dict[str, EffectSpec] = {}
    for name in sorted(SUPPORTED_EFFECTS):
        summary, schema_fields = _EFFECT_META.get(name, ("", ()))
        cards, required_context = owners.get(name, ((), ()))
        registry[name] = EffectSpec(
            name=name,
            summary=summary,
            core=name in core,
            cards=cards,
            aliases=aliases.get(name, ()),
            required_context=required_context,
            schema_fields=schema_fields,
        )
    return registry


REGISTRY: dict[str, EffectSpec] = _build_registry()


def effect_spec(name: str) -> EffectSpec | None:
    return REGISTRY.get(name)


def registered_effects() -> set[str]:
    return set(REGISTRY)


def canonical_effect(type_or_alias: str) -> str | None:
    """Resolve an effect type-string or alias to its canonical effect name (or None)."""
    if type_or_alias in REGISTRY:
        return type_or_alias
    return EFFECT_TYPE_ALIASES.get(type_or_alias)
