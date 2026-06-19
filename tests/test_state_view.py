"""state_view is the read-only state surface (Stage 2 of the state-surface plan).

These tests pin two things: (1) the engine/actions public methods still delegate to the
view builders with identical output shape, and (2) building any view never mutates state.
"""

from __future__ import annotations

from wildmagic import state_view
from wildmagic.actions import summarize_state
from wildmagic.engine import GameEngine


def _engine() -> tuple[GameEngine, object, object]:
    engine = GameEngine(seed=11, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "cave spider",
        "s",
        player.x + 2,
        player.y,
        hp=6,
        attack=2,
        defense=0,
        faction="enemy",
        ai="hunt",
    )
    engine.spawn_item(
        "iron key",
        "k",
        player.x + 1,
        player.y,
        item_type="key",
        material="iron",
        tags={"metal"},
    )
    return engine, player, enemy


# --- delegation parity -------------------------------------------------------------------


def test_context_for_llm_delegates_to_spell_context_view() -> None:
    engine, _player, _enemy = _engine()
    spell = "hurl a roaring fireball at the spider"

    assert engine.context_for_llm(spell) == state_view.spell_context_view(engine, spell)


def test_summarize_state_delegates_to_replay_summary_view() -> None:
    engine, _player, _enemy = _engine()

    summary = summarize_state(engine)
    assert summary == state_view.replay_summary_view(engine)
    # inspection_view currently shares the replay shape.
    assert summary == state_view.inspection_view(engine)


# --- card builders -----------------------------------------------------------------------


def test_entity_card_is_the_public_dict() -> None:
    engine, _player, enemy = _engine()

    assert state_view.entity_card(enemy, engine) == enemy.to_public_dict()


def test_item_card_shape() -> None:
    engine, player, _enemy = _engine()
    item = next(
        e
        for e in engine.state.entities.values()
        if e.kind == "item" and e.name == "iron key"
    )

    card = state_view.item_card(item, engine)

    assert card["id"] == item.id
    assert card["name"] == "iron key"
    assert card["item_type"] == "key"
    assert card["material"] == "iron"
    assert card["x"] == player.x + 1
    assert card["tags"] == ["metal"]
    # No traits on this item, so the optional key is omitted entirely.
    assert "traits" not in card


def test_tile_card_shape() -> None:
    engine, player, _enemy = _engine()

    card = state_view.tile_card(player.x, player.y, engine)

    assert card["x"] == player.x
    assert card["y"] == player.y
    assert set(card) >= {"x", "y", "tile", "name", "tags", "duration"}


def test_selected_target_card_reports_marked_occupant() -> None:
    engine, _player, enemy = _engine()
    assert engine.set_target(enemy.x, enemy.y) is True

    card = state_view.selected_target_card(engine)

    assert card["x"] == enemy.x and card["y"] == enemy.y
    assert card["occupied"] is True
    assert card["entity_id"] == enemy.id
    assert card["entity_name"] == enemy.name


def test_equipment_inventory_view_owns_presentation_rules() -> None:
    engine, _player, _enemy = _engine()
    engine.state.inventory["emberglass wand"] = 1
    engine.state.inventory["plain stone"] = 2
    assert engine.equip_item("emberglass wand")
    assert engine.set_focus("weapon")

    view = state_view.equipment_inventory_view(engine)

    assert [slot["slot"] for slot in view["slots"]] == [
        "weapon",
        "armor",
        "charm",
        "head",
        "chest",
        "legs",
        "feet",
        "hands",
    ]
    weapon = next(slot for slot in view["slots"] if slot["slot"] == "weapon")
    assert weapon == {
        "slot": "weapon",
        "item": "emberglass wand",
        "occupied": True,
        "focused": True,
    }
    stone = next(item for item in view["items"] if item["name"] == "plain stone")
    assert stone["quantity"] == 2
    assert stone["equippable"] is False
    assert stone["equipment_slot"] is None


def test_room_card_secrets_flag_passes_through() -> None:
    engine, player, _enemy = _engine()
    room = engine.room_profile_at(player.x, player.y)
    assert room is not None

    assert state_view.room_card(room, engine) == room.to_public_dict()
    assert state_view.room_card(
        room, engine, include_secrets=True
    ) == room.to_public_dict(include_secrets=True)


# --- read-only invariant -----------------------------------------------------------------


def test_building_views_does_not_mutate_state() -> None:
    engine, player, enemy = _engine()
    before = summarize_state(engine)
    fov = engine.state.fov_radius

    state_view.spell_context_view(engine, "make the spider forget my face")
    state_view.replay_summary_view(engine)
    state_view.inspection_view(engine)
    state_view.entity_card(enemy, engine)
    state_view.tile_card(player.x, player.y, engine)
    state_view.scene_notes_card(engine, player, fov)
    state_view.nearby_tile_details(engine, radius=5)
    state_view.equipment_inventory_view(engine)

    assert summarize_state(engine) == before
