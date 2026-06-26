from __future__ import annotations

from wildmagic.models import Entity
from wildmagic.rendering.map_view import ENTITY_COLORS, entity_color
from wildmagic.ui_theme import BACKGROUND, blend_color


def _entity(
    *,
    kind: str = "creature",
    faction: str = "enemy",
    alive: bool = True,
    statuses: set[str] | None = None,
) -> Entity:
    return Entity(
        id="test",
        name="test",
        char="t",
        x=0,
        y=0,
        hp=1 if alive else 0,
        max_hp=1,
        attack=1,
        defense=0,
        faction=faction,
        kind=kind,
        statuses=statuses or set(),
    )


def test_entity_color_uses_item_color_regardless_of_faction() -> None:
    assert entity_color(_entity(kind="item", faction="enemy")) == ENTITY_COLORS["item"]


def test_entity_color_uses_neutral_for_unknown_faction() -> None:
    assert entity_color(_entity(faction="unknown")) == ENTITY_COLORS["neutral"]


def test_entity_color_keeps_dead_entity_base_color() -> None:
    assert (
        entity_color(_entity(alive=False, statuses={"burning"}))
        == ENTITY_COLORS["enemy"]
    )


def test_entity_color_tints_active_statuses() -> None:
    base = ENTITY_COLORS["enemy"]

    assert entity_color(_entity(statuses={"burning"})) == blend_color(
        base, (232, 96, 70), 0.55
    )
    assert entity_color(_entity(statuses={"frozen"})) == blend_color(
        base, (156, 210, 224), 0.55
    )
    assert entity_color(_entity(statuses={"poisoned"})) == blend_color(
        base, (130, 200, 80), 0.55
    )
    assert entity_color(_entity(statuses={"bleeding"})) == blend_color(
        base, (200, 60, 60), 0.4
    )
    assert entity_color(_entity(statuses={"invisible"})) == blend_color(
        base, BACKGROUND, 0.65
    )
