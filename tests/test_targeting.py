"""Explicit spell targeting: the player marks a square (click / 'target <x> <y>') and
the engine resolves "target"/"there"/"that square" to it deterministically, while the
resolver context advertises the mark. Covers the engine plumbing, the bare-tile path,
the standard-spell aim override, context exposure, and the free-action command path."""

from __future__ import annotations

from wildmagic.actions import GameSession
from wildmagic.engine import GameEngine


def _engine_with_enemy(dx: int = 2, dy: int = 0):
    engine = GameEngine(seed=11, scenario="test_chamber")
    player = engine.state.player
    enemy = engine.spawn_actor(
        "cave spider",
        "s",
        player.x + dx,
        player.y + dy,
        hp=6,
        attack=2,
        defense=0,
        faction="enemy",
        ai="hunt",
    )
    return engine, player, enemy


# --- engine resolution -------------------------------------------------------------------


def test_set_target_binds_occupant_and_resolve_target_returns_it() -> None:
    engine, _player, enemy = _engine_with_enemy()
    assert engine.set_target(enemy.x, enemy.y) is True
    assert engine.has_target()
    assert engine.state.target_entity_id == enemy.id
    # The "target" keyword now resolves to the marked creature, not just nearest_enemy.
    assert engine.resolve_target("target") is enemy
    assert engine.selected_target_entity() is enemy


def test_no_target_keeps_legacy_nearest_enemy_behavior() -> None:
    engine, _player, enemy = _engine_with_enemy()
    # Without a mark, "target" falls through to the nearest-enemy auto-aim.
    assert not engine.has_target()
    assert engine.resolve_target("target") is enemy


def test_bare_tile_target_has_no_entity_but_resolves_position() -> None:
    engine, player, _enemy = _engine_with_enemy()
    tx, ty = player.x, player.y + 3
    assert engine.set_target(tx, ty) is True
    assert engine.state.target_entity_id is None
    assert engine.selected_target_entity() is None
    # No occupant, so resolve_target yields None...
    assert engine.resolve_target("there") is None
    # ...but the position resolvers honor the clicked square.
    assert engine.effect_position({"type": "area_damage", "target": "there"}) == (tx, ty)
    assert engine.resolve_placement(
        {"placement": "selected_target"}, prefer_unblocked=False
    ) == (tx, ty)


def test_dead_occupant_falls_back_to_bare_tile() -> None:
    engine, _player, enemy = _engine_with_enemy()
    engine.set_target(enemy.x, enemy.y)
    enemy.hp = 0  # `alive` is a derived property (hp > 0)
    assert not enemy.alive
    assert engine.selected_target_entity() is None
    # The mark survives as a tile even though its creature died.
    assert engine.has_target()


def test_teleport_to_target_lands_on_marked_tile_without_coords() -> None:
    engine, player, _enemy = _engine_with_enemy()
    tx, ty = player.x + 1, player.y + 1
    engine.set_target(tx, ty)
    engine._apply_effect(
        {"type": "teleport", "target": "player", "destination": "target"}
    )
    assert (player.x, player.y) == (tx, ty)


def test_teleport_with_no_coordinate_at_all_uses_mark_not_origin() -> None:
    # The reported bug: model returns {"type":"teleport","target":"player"} with no
    # destination key whatsoever. It must land on the mark, never at (0,0).
    engine, player, _enemy = _engine_with_enemy()
    tx, ty = player.x + 2, player.y + 1
    engine.set_target(tx, ty)
    engine._apply_effect({"type": "teleport", "target": "player"})
    assert (player.x, player.y) == (tx, ty)


def test_teleport_with_no_coord_and_no_mark_never_lands_at_origin() -> None:
    engine, player, _enemy = _engine_with_enemy()
    assert not engine.has_target()
    engine._apply_effect({"type": "teleport", "target": "player"})
    # Falls back to a visible floor tile, not the (0,0) corner.
    assert (player.x, player.y) != (0, 0)
    assert engine.is_visible(player.x, player.y)


def test_teleport_nested_destination_object_is_honored() -> None:
    engine, player, _enemy = _engine_with_enemy()
    tx, ty = player.x + 1, player.y + 1
    engine._apply_effect(
        {"type": "teleport", "target": "player", "destination": {"x": tx, "y": ty}}
    )
    assert (player.x, player.y) == (tx, ty)


def test_explicit_coords_still_win_over_mark() -> None:
    engine, player, _enemy = _engine_with_enemy()
    engine.set_target(player.x + 4, player.y)
    # An effect carrying its own x/y is unaffected by the mark.
    pos = engine.effect_position(
        {"type": "area_damage", "target": "there", "x": player.x + 2, "y": player.y}
    )
    assert pos == (player.x + 2, player.y)


def test_set_target_out_of_bounds_rejected() -> None:
    engine, _player, _enemy = _engine_with_enemy()
    assert engine.set_target(-1, 0) is False
    assert engine.set_target(engine.state.width, 0) is False
    assert not engine.has_target()


def test_clear_target_resets_all_fields() -> None:
    engine, _player, enemy = _engine_with_enemy()
    engine.set_target(enemy.x, enemy.y)
    engine.clear_target()
    assert engine.state.target_x is None
    assert engine.state.target_y is None
    assert engine.state.target_entity_id is None
    assert not engine.has_target()


# --- standard spells -----------------------------------------------------------------------


def test_standard_bolt_prefers_marked_enemy_over_nearest() -> None:
    engine = GameEngine(seed=12, scenario="test_chamber")
    player = engine.state.player
    near = engine.spawn_actor(
        "near rat", "r", player.x + 1, player.y, hp=6, attack=1, defense=0,
        faction="enemy", ai="hunt",
    )
    far = engine.spawn_actor(
        "far rat", "R", player.x + 3, player.y, hp=6, attack=1, defense=0,
        faction="enemy", ai="hunt",
    )
    engine.set_target(far.x, far.y)
    engine.cast_standard_bolt()
    # The marked-but-farther rat took the hit; the nearer one is untouched.
    assert far.hp < 6
    assert near.hp == 6


# --- resolver context ----------------------------------------------------------------------


def test_context_exposes_selected_target_only_when_set() -> None:
    engine, _player, enemy = _engine_with_enemy()
    assert "selected_target" not in engine.context_for_llm("zap")
    engine.set_target(enemy.x, enemy.y)
    ctx = engine.context_for_llm("zap at target")
    st = ctx["selected_target"]
    assert (st["x"], st["y"]) == (enemy.x, enemy.y)
    assert st["entity_id"] == enemy.id
    assert st["occupied"] is True
    assert "has_line_of_sight" in st


# --- command path (free action + replay) ---------------------------------------------------


def test_target_command_is_a_free_action() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    player = session.engine.state.player
    result = session.execute_command(f"target {player.x + 1} {player.y}")
    assert result.success
    assert result.consumed_turn is False
    assert session.engine.has_target()
    cleared = session.execute_command("untarget")
    assert cleared.consumed_turn is False
    assert not session.engine.has_target()


def test_target_command_replays_round_trip() -> None:
    session = GameSession(seed=7, scenario="test_chamber", provider_name="mock")
    player = session.engine.state.player
    tx, ty = player.x + 1, player.y
    session.execute_command(f"target {tx} {ty}")
    data = session.to_replay()
    session.close()

    fresh = GameSession(
        seed=data.get("seed"),
        scenario=data.get("scenario", "dungeon"),
        provider_name="mock",
        dialogue_provider_name="mock",
        replay_mode=True,
    )
    try:
        for action in data.get("actions", []):
            fresh.execute_command(str(action.get("command") or ""))
        assert (fresh.engine.state.target_x, fresh.engine.state.target_y) == (tx, ty)
    finally:
        fresh.close()
