from __future__ import annotations

import copy
import hashlib
import math
from typing import Any

from . import refs
from . import operations
from .behaviors import SUPPORTED_BEHAVIORS, normalize_behavior, upsert_behavior_modifier
from .curses import build_curse, merge_curse, validate_resolution_against_curses
from .geometry import bresenham_line, sign, unique_points
from .models import (
    BLOCKING_TILES,
    DOOR,
    FLOOR,
    ICE_WALL,
    MECHANICAL_STATUSES,
    STAIRS_DOWN,
    STAIRS_UP,
    TILE_NAMES,
    WALL,
    Entity,
    NPCMemoryRecord,
    WildMagicOutcome,
)
from .normalize import (
    _flatten_effect,
    area_damage_affects,
    clamp_int,
    coerce_list,
    normalize_faction,
    normalize_id,
    normalize_numeric_map,
    normalize_trigger_name,
    optional_duration,
    parse_tile_key,
    sanitize_char,
    sanitize_name,
    status_duration,
    tile_from_name,
)
from .promises import Objective, WorldPromise, parse_spatial_hint
from .spell_contract import STATUS_FLAVOR_ALIASES, validate_resolution
from .templates import creature_template, item_template


def _positive_cost_amount(value: Any, maximum: int, default: int = 1) -> int:
    """A cost magnitude. The model occasionally emits a negative number when it
    means a cost of that size (e.g. -5 for "lose 5"), so take the absolute value,
    and clamp to [1, maximum] so any emitted cost actually bites at least 1 — a
    0/missing/negative amount must never resolve to a free cost."""
    try:
        amount = abs(int(value))
    except (TypeError, ValueError):
        amount = default
    return max(1, min(maximum, amount))


_REF_FIELDS = {
    "target",
    "center",
    "origin",
    "source",
    "sink",
    "anchor",
    "attached_to",
    "object",
    "prop",
    "destination",
    "to",
    "from",
    "position",
    "dest",
}


class _EffectsMixin:
    """Effect/cost application and placement helpers extracted from GameEngine."""

    def _spell_tile_preserves_player_escape(
        self,
        x: int,
        y: int,
        tile: str,
        pending_blocked: set[tuple[int, int]] | None = None,
    ) -> bool:
        if tile not in BLOCKING_TILES or tile == DOOR:
            return True
        player = self.state.player
        blocked = set(pending_blocked or set())
        blocked.add((x, y))
        player_xy = (player.x, player.y)
        if player_xy in blocked:
            return False

        has_exit = False
        for nx, ny in (
            (player.x + 1, player.y),
            (player.x - 1, player.y),
            (player.x, player.y + 1),
            (player.x, player.y - 1),
        ):
            if not self.in_bounds(nx, ny) or (nx, ny) in blocked:
                continue
            if self.state.tiles[ny][nx] in BLOCKING_TILES:
                continue
            has_exit = True
            break
        if not has_exit:
            return False

        stairs = [
            (sx, sy)
            for sy, row in enumerate(self.state.tiles)
            for sx, existing in enumerate(row)
            if existing in {STAIRS_DOWN, STAIRS_UP} and (sx, sy) != player_xy
        ]
        if not stairs:
            return True
        return any(self._floor_reachable(player_xy, goal, blocked) for goal in stairs)

    def apply_wild_magic_resolution(
        self, resolution: dict[str, Any]
    ) -> WildMagicOutcome:
        messages: list[str] = []
        if self.state.game_over:
            return WildMagicOutcome(False, False, ["The dead do not cast."])

        validation_error = validate_resolution(resolution)
        if validation_error:
            message = f"Wild magic failed validation: {validation_error}"
            self.state.add_message(message)
            return WildMagicOutcome(False, True, [message])

        ref_error = self._resolution_ref_error(resolution)
        if ref_error:
            message = f"Wild magic failed validation: {ref_error}"
            self.state.add_message(message)
            return WildMagicOutcome(False, True, [message])

        accepted = bool(resolution.get("accepted", True))
        outcome_text = str(
            resolution.get("outcome_text")
            or resolution.get("outcome")
            or resolution.get("message")
            or ""
        ).strip()
        if not accepted:
            reason = str(
                resolution.get("rejected_reason")
                or "The spell is too vast to fit through you."
            )
            self.state.add_message(reason)
            self.state.stats.spells_failed += 1
            self.finish_player_turn()
            return WildMagicOutcome(True, False, [reason])

        curse_error = validate_resolution_against_curses(self, resolution)
        if curse_error:
            self.state.add_message(curse_error)
            self.state.stats.spells_failed += 1
            self.finish_player_turn()
            return WildMagicOutcome(True, False, [curse_error])

        snapshot = copy.deepcopy(self.state)
        # Capture operation deltas while the spell's effects + costs apply (Stage 6). Turned
        # off before finish_player_turn so the turn's environment/AI ticks aren't counted.
        self.begin_delta_capture()
        nearest = self.nearest_enemy()
        self._cast_ref_cache = {"nearest_enemy": nearest.id} if nearest else {}
        try:
            if outcome_text:
                self.state.add_message(outcome_text)
                messages.append(outcome_text)

            spell_text = normalize_id(str(resolution.get("spell") or "")).strip("_")
            if spell_text:
                if spell_text == self.state.last_spell_text:
                    self.state.same_spell_streak += 1
                else:
                    self.state.last_spell_text = spell_text
                    self.state.same_spell_streak = 1

            for message in self._fire_triggers(
                "on_next_spell",
                {"target": self.state.player, "source": self.state.player},
            ):
                messages.append(message)

            for effect in coerce_list(resolution.get("effects")):
                for message in self._apply_effect(effect):
                    self.state.add_message(message)
                    messages.append(message)

            for cost in coerce_list(resolution.get("costs")):
                message = self._apply_cost(cost)
                if message:
                    self.state.add_message(message)
                    messages.append(message)

            if not messages:
                message = "The spell answers with a small, embarrassed pop."
                self.state.add_message(message)
                messages.append(message)

            deltas = self.collected_deltas()
            self.end_delta_capture()
            self._cast_ref_cache = {}
            self.state.stats.spells_cast += 1
            self.finish_player_turn()
            state_errors = self.validate_state()
            if state_errors:
                self.state = snapshot
                self.discard_deltas()
                self._cast_ref_cache = {}
                message = f"Wild magic failed state validation: {state_errors[0]}"
                self.state.add_message(message)
                return WildMagicOutcome(False, True, [message])
            return WildMagicOutcome(True, False, messages, deltas=deltas)
        except Exception as exc:
            self.state = snapshot
            self.discard_deltas()
            self._cast_ref_cache = {}
            message = f"Wild magic failed during application: {exc}"
            self.state.add_message(message)
            return WildMagicOutcome(False, True, [message])

    def _apply_cost(self, cost: dict[str, Any]) -> str | None:
        if not isinstance(cost, dict):
            return None
        cost_type = str(cost.get("type", "")).lower()
        player = self.state.player
        if cost_type == "mana":
            amount = clamp_int(cost.get("amount"), 1, 99)
            paid = min(player.mana, amount)
            shortfall = amount - paid
            player.mana -= paid
            if shortfall:
                if paid:
                    self.state.add_message(
                        f"Cost: {paid} mana; mana shortfall costs {shortfall} health.",
                        is_danger=True,
                    )
                else:
                    self.state.add_message(
                        f"Cost unpaid: no mana; wild magic takes {shortfall} health.",
                        is_danger=True,
                    )
                self.damage_entity(player, shortfall, "blood")
                return None
            return f"Cost: {amount} mana."
        if cost_type in {"health", "hp"}:
            amount = clamp_int(cost.get("amount"), 1, 99)
            self.state.add_message(f"Cost: {amount} health.", is_danger=True)
            self.damage_entity(player, amount, "blood")
            return None
        if cost_type == "max_health":
            # abs()+floor-of-1: a max-stat cost the model bothered to emit must bite.
            # The old 0 floor let a missing/negative amount clamp to 0 and silently do
            # nothing while still printing a cost line.
            amount = _positive_cost_amount(cost.get("amount"), 10)
            player.max_hp = max(1, player.max_hp - amount)
            player.hp = min(player.hp, player.max_hp)
            return f"Cost: {amount} maximum health."
        if cost_type == "max_mana":
            amount = _positive_cost_amount(cost.get("amount"), 10)
            player.max_mana = max(0, player.max_mana - amount)
            player.mana = min(player.mana, player.max_mana)
            return f"Cost: {amount} maximum mana."
        if cost_type == "item":
            item = str(
                cost.get("item") or cost.get("item_name") or cost.get("id") or ""
            ).strip()
            amount = clamp_int(cost.get("amount"), 1, 99)
            if not item:
                return None
            current = self.state.inventory.get(item, 0)
            spent = min(current, amount)
            if spent:
                remaining = current - spent
                if remaining:
                    self.state.inventory[item] = remaining
                else:
                    self.state.inventory.pop(item, None)
            return f"Cost: {spent} {item}." if spent else f"Cost unpaid: no {item}."
        if cost_type == "curse":
            curse = build_curse(cost, turn=self.state.turn)
            if curse.id in self.state.curses:
                merge_curse(self.state.curses[curse.id], curse)
            else:
                self.state.curses[curse.id] = curse
            self.state.stats.curses_gained += 1
            self._fire_triggers(
                "on_curse_gained",
                {"target": player, "source": player, "curse": curse},
            )
            return f"Curse gained: {curse.name}."
        if cost_type == "status":
            raw_status = str(cost.get("status") or cost.get("id") or "strained")
            status = normalize_id(raw_status)
            display_name = str(cost.get("display_name") or "").strip()
            if status not in MECHANICAL_STATUSES:
                canonical = STATUS_FLAVOR_ALIASES.get(status)
                if canonical:
                    if not display_name:
                        display_name = status.replace("_", " ")
                    status = canonical
            duration = cost.get("duration", 5)
            expiry_text = str(cost.get("expiry_text") or "").strip()
            if status not in MECHANICAL_STATUSES:
                name = display_name or status.replace("_", " ").title()
                curse_id = f"wild_condition_{status}"
                curse = build_curse(
                    {
                        "id": curse_id,
                        "name": name,
                        "description": f"Wild magic leaves you with an uncanny condition: {name}.",
                        "semantic_prompt": f"Let this uncanny condition shape future spells: {name}.",
                    },
                    turn=self.state.turn,
                )
                if curse.id in self.state.curses:
                    merge_curse(self.state.curses[curse.id], curse)
                else:
                    self.state.curses[curse.id] = curse
                return f"Cost became a curse: {name}."
            dur_val3: int | str = (
                "permanent" if duration == "permanent" else clamp_int(duration, 1, 999)
            )
            player.statuses[status] = dur_val3
            shown = display_name or status.replace("_", " ")
            if display_name:
                player.status_display[status] = display_name
            if expiry_text:
                player.status_expiry_text[status] = expiry_text
            return f"Cost: you are {shown}."
        return None

    def _apply_effect(self, effect: dict[str, Any]) -> list[str]:
        if not isinstance(effect, dict):
            return []
        effect = _flatten_effect(effect)
        ref_error = self._effect_ref_error(effect)
        if ref_error:
            raise ValueError(ref_error)
        effect_type = str(effect.get("type", "")).lower()
        if effect_type == "damage":
            target = self.resolve_target(effect.get("target") or "nearest_enemy")
            if not target:
                return ["The spell claws at empty air."]
            amount = (
                clamp_int(effect.get("amount"), 1, 999)
                if effect.get("amount") is not None
                else 5
            )
            damage_type = str(effect.get("damage_type") or "arcane")
            if not self._damage_would_be_delayed(target):
                actual = self.calculate_actual_damage(target, amount, damage_type)
                is_player_dmg = target.id == self.state.player_id and actual > 0
                self.state.add_message(
                    f"{target.name} {self._verb(target, 'take', 'takes')} {actual} {damage_type} damage.",
                    is_danger=is_player_dmg,
                )
            self.damage_entity(target, amount, damage_type, source=self.state.player)
            return []
        if effect_type == "area_damage":
            x, y = self.effect_position(effect)
            radius = (
                clamp_int(effect.get("radius"), 0, 99)
                if effect.get("radius") is not None
                else 3
            )
            amount = (
                clamp_int(effect.get("amount"), 1, 999)
                if effect.get("amount") is not None
                else 5
            )
            damage_type = str(effect.get("damage_type") or "arcane")
            include_player = bool(effect.get("include_player", False))
            affects = normalize_id(str(effect.get("affects") or "non_player"))
            hit: list[str] = []
            actuals = []
            is_player_dmg = False
            for entity in self.entities_in_radius(x, y, radius):
                if entity.kind == "item" or entity.hp <= 0:
                    continue
                if entity.id == self.state.player_id and not include_player:
                    continue
                if not area_damage_affects(entity, affects, self.state.player_id):
                    continue
                if self._damage_would_be_delayed(entity):
                    hit.append(f"{entity.name}'s {damage_type} damage is delayed")
                else:
                    actual = self.calculate_actual_damage(entity, amount, damage_type)
                    hit.append(
                        f"{entity.name} {self._verb(entity, 'take', 'takes')} {actual} {damage_type}"
                    )
                actuals.append(entity)
                if (
                    entity.id == self.state.player_id
                    and not self._damage_would_be_delayed(entity)
                    and self.calculate_actual_damage(entity, amount, damage_type) > 0
                ):
                    is_player_dmg = True
            if not hit:
                return ["The blast spends itself on empty stone."]
            self.state.add_message(
                f"Area spell hits {len(hit)} target(s): {', '.join(hit)}.",
                is_danger=is_player_dmg,
            )
            for entity in actuals:
                self.damage_entity(
                    entity, amount, damage_type, source=self.state.player
                )
            return []
        if effect_type == "area_status":
            x, y = self.effect_position(effect)
            radius = (
                clamp_int(effect.get("radius"), 0, 99)
                if effect.get("radius") is not None
                else 15
            )
            status = normalize_id(str(effect.get("status") or "strange"))
            display_name = str(
                effect.get("display_name") or effect.get("name") or ""
            ).strip() or status.replace("_", " ")
            expiry_text = str(
                effect.get("expiry_text") or effect.get("wears_off") or ""
            ).strip()
            duration = effect.get("duration", 3)
            affects = normalize_id(str(effect.get("affects") or "enemies"))
            include_player = bool(effect.get("include_player", False))
            if status not in MECHANICAL_STATUSES:
                canonical = STATUS_FLAVOR_ALIASES.get(status)
                if not canonical:
                    return []
                status = canonical
            affected: list[str] = []
            dur_val2: int | str = (
                "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
            )
            for entity in self.entities_in_radius(x, y, radius):
                if entity.kind == "item" or entity.hp <= 0:
                    continue
                if entity.id == self.state.player_id and not include_player:
                    continue
                if not area_damage_affects(entity, affects, self.state.player_id):
                    continue
                entity.statuses[status] = dur_val2
                if display_name != status.replace("_", " "):
                    entity.status_display[status] = display_name
                if expiry_text:
                    entity.status_expiry_text[status] = expiry_text
                affected.append(entity.name)
            if not affected:
                return ["The status finds no one to cling to."]
            return [f"{display_name.title()} spreads to: {', '.join(affected)}."]
        if effect_type == "heal":
            target = self.resolve_target(effect.get("target") or "player")
            if not target:
                return []
            amount = (
                clamp_int(effect.get("amount"), 1, 999)
                if effect.get("amount") is not None
                else 5
            )
            actual = self.heal_entity(target, amount)
            if actual == 0:
                if target.id == self.state.player_id:
                    return ["Your wounds are already mended."]
                return [
                    f"{target.name} {self._verb(target, 'are', 'is')} already whole."
                ]
            return [f"{target.name} {self._verb(target, 'heal', 'heals')} {actual} HP."]
        if effect_type == "restore_mana":
            target = self.resolve_target(effect.get("target") or "player")
            if not target:
                return []
            amount = (
                clamp_int(effect.get("amount"), 1, 999)
                if effect.get("amount") is not None
                else 5
            )
            before = target.mana
            target.mana = min(target.max_mana, target.mana + amount)
            gained = target.mana - before
            return [
                f"{target.name} {self._verb(target, 'recover', 'recovers')} {gained} mana."
            ]
        if effect_type == "teleport":
            target = self.resolve_target(effect.get("target") or "player")
            if not target:
                return []
            x, y = self._teleport_destination(effect)
            if self.teleport_entity(target, x, y):
                return [
                    f"{target.name} {self._verb(target, 'snap', 'snaps')} to another tile."
                ]
            return ["The teleport folds into a wall and fails."]
        if effect_type in {"push", "pull"}:
            target_str = str(effect.get("target") or "nearest_enemy")
            distance = clamp_int(effect.get("distance"), 1, 20)
            targets = self.resolve_target_group(target_str)
            if not targets:
                target = self.resolve_target(target_str)
                targets = [target] if target else []
            if not targets:
                return []
            origin = (
                self.resolve_target(effect.get("origin") or "player")
                or self.state.player
            )
            moved_total = 0
            moved_names: list[str] = []
            for target in targets[:12]:
                if "dx" in effect or "dy" in effect:
                    dx = sign(clamp_int(effect.get("dx"), -1, 1))
                    dy = sign(clamp_int(effect.get("dy"), -1, 1))
                else:
                    dx = sign(target.x - origin.x)
                    dy = sign(target.y - origin.y)
                    if effect_type == "pull":
                        dx *= -1
                        dy *= -1
                moved = self.push_entity(target, dx, dy, distance)
                if moved:
                    moved_total += moved
                    moved_names.append(target.name)
            if len(targets) == 1:
                return [f"{targets[0].name} is moved {moved_total} tile(s)."]
            if moved_names:
                return [
                    f"{len(moved_names)} target(s) are moved {moved_total} tile(s) total."
                ]
            return ["The force finds no room to move anyone."]
        if effect_type in {"create_tile", "set_tile", "create_tiles"}:
            x, y = self.effect_position(effect)
            tile_name = str(effect.get("tile") or FLOOR).lower()
            tile = tile_from_name(tile_name)
            duration = optional_duration(effect.get("duration"))
            tags = set(
                normalize_id(str(tag))
                for tag in coerce_list(effect.get("tags"))
                if str(tag).strip()
            )
            changed = 0
            pending_blocked: set[tuple[int, int]] = set()
            tile_specs = effect.get("tiles")
            if isinstance(tile_specs, list):
                first_spec_tile: str | None = None
                for spec in tile_specs[:30]:
                    if not isinstance(spec, dict):
                        continue
                    tx = clamp_int(spec.get("x"), 0, self.state.width - 1)
                    ty = clamp_int(spec.get("y"), 0, self.state.height - 1)
                    spec_tile = tile_from_name(str(spec.get("tile") or tile_name))
                    if first_spec_tile is None:
                        first_spec_tile = spec_tile
                    spec_duration = optional_duration(spec.get("duration", duration))
                    spec_tags = set(
                        normalize_id(str(tag))
                        for tag in coerce_list(spec.get("tags", list(tags)))
                        if str(tag).strip()
                    )
                    if not self._spell_tile_preserves_player_escape(
                        tx, ty, spec_tile, pending_blocked
                    ):
                        continue
                    if self.set_tile(tx, ty, spec_tile, spec_duration, spec_tags):
                        changed += 1
                        if spec_tile in {WALL, ICE_WALL}:
                            pending_blocked.add((tx, ty))
                if first_spec_tile is not None:
                    tile = first_spec_tile
            else:
                radius = clamp_int(effect.get("radius"), 0, 99)
                hollow = bool(
                    effect.get("hollow")
                    or effect.get("ring")
                    or effect.get("perimeter")
                )
                inner_radius = max(0, radius - 1) if hollow else -1
                shape = normalize_id(
                    str(effect.get("shape") or effect.get("pattern") or "")
                )
                if shape in {
                    "line",
                    "beam",
                    "path",
                    "corridor",
                    "ray",
                    "bridge",
                    "wall",
                    "barrier",
                    "cone",
                    "fan",
                    "scatter",
                    "spray",
                }:
                    for tx, ty in self.shape_points(effect, x, y)[:200]:
                        if not self._spell_tile_preserves_player_escape(
                            tx, ty, tile, pending_blocked
                        ):
                            continue
                        if self.set_tile(tx, ty, tile, duration, tags):
                            changed += 1
                            if tile in {WALL, ICE_WALL}:
                                pending_blocked.add((tx, ty))
                else:
                    for tx, ty in self.points_in_radius(x, y, radius)[:200]:
                        if hollow and math.hypot(tx - x, ty - y) <= inner_radius:
                            continue
                        if not self._spell_tile_preserves_player_escape(
                            tx, ty, tile, pending_blocked
                        ):
                            continue
                        if self.set_tile(tx, ty, tile, duration, tags):
                            changed += 1
                            if tile in {WALL, ICE_WALL}:
                                pending_blocked.add((tx, ty))
            return [
                f"Terrain changes to {TILE_NAMES.get(tile, 'strange')} on {changed} tile(s)."
            ]
        if effect_type == "create_flow":
            return self._apply_create_flow(effect)
        if effect_type == "add_status":
            target_str = normalize_id(str(effect.get("target") or "nearest_enemy"))
            status = normalize_id(str(effect.get("status") or "strange"))
            display_name = str(
                effect.get("display_name") or effect.get("name") or ""
            ).strip() or status.replace("_", " ")
            expiry_text = str(
                effect.get("expiry_text") or effect.get("wears_off") or ""
            ).strip()
            duration = effect.get("duration", 3)
            dur_val: int | str = (
                "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
            )
            if status not in MECHANICAL_STATUSES:
                canonical = STATUS_FLAVOR_ALIASES.get(status)
                if not canonical:
                    return []
                status = canonical
            group_targets = self.resolve_target_group(target_str)
            if group_targets:
                for ent in group_targets:
                    operations.apply_status(self, ent, status, dur_val)
                    if status == "sight_shrouded":
                        ent.details["sight_radius"] = clamp_int(
                            effect.get(
                                "sight_radius",
                                effect.get("radius", effect.get("fov_radius", 2)),
                            ),
                            0,
                            99,
                        )
                    if display_name != status.replace("_", " "):
                        ent.status_display[status] = display_name
                    if expiry_text:
                        ent.status_expiry_text[status] = expiry_text
                if any(ent.id == self.state.player_id for ent in group_targets):
                    self.update_fov()
                return [
                    f"{display_name.title()} spreads to {len(group_targets)} target(s)."
                ]
            target = self.resolve_target(target_str)
            if not target or target.kind == "item":
                return []
            operations.apply_status(self, target, status, dur_val)
            if status == "sight_shrouded":
                target.details["sight_radius"] = clamp_int(
                    effect.get(
                        "sight_radius",
                        effect.get("radius", effect.get("fov_radius", 2)),
                    ),
                    0,
                    99,
                )
            if display_name != status.replace("_", " "):
                target.status_display[status] = display_name
            if expiry_text:
                target.status_expiry_text[status] = expiry_text
            if target.id == self.state.player_id and status == "sight_shrouded":
                self.update_fov()
            return [
                f"{target.name} {self._verb(target, 'are', 'is')} now {display_name}."
            ]
        if effect_type == "remove_status":
            target = self.resolve_target(effect.get("target") or "player")
            if not target:
                return []
            status = normalize_id(str(effect.get("status") or ""))
            if status:
                status = STATUS_FLAVOR_ALIASES.get(status, status)
                target.statuses.pop(status, None)
                target.status_display.pop(status, None)
                target.status_expiry_text.pop(status, None)
                if status == "sight_shrouded":
                    target.details.pop("sight_radius", None)
                    if target.id == self.state.player_id:
                        self.update_fov()
                return [
                    f"{target.name} {self._verb(target, 'are', 'is')} no longer {status.replace('_', ' ')}."
                ]
            target.statuses.clear()
            target.status_display.clear()
            target.status_expiry_text.clear()
            target.details.pop("sight_radius", None)
            if target.id == self.state.player_id:
                self.update_fov()
            if target.id == self.state.player_id:
                return ["All statuses leave you."]
            return [f"All statuses leave {target.name}."]
        if effect_type == "summon":
            name = str(
                effect.get("name")
                or effect.get("creature")
                or effect.get("creature_type")
                or "borrowed thing"
            )
            faction = normalize_faction(
                effect.get("faction"), default="ally", neutral_is_ally=True
            )
            count = clamp_int(effect.get("count") or effect.get("quantity") or 1, 1, 6)
            char = str(effect.get("char") or ("a" if faction == "ally" else "e"))[:1]
            hp = clamp_int(effect.get("hp") or 5, 1, 20)
            attack = clamp_int(effect.get("attack") or 2, 0, 8)
            defense = clamp_int(effect.get("defense") or 0, 0, 8)
            tags = set(
                normalize_id(str(tag))
                for tag in coerce_list(effect.get("tags"))
                if str(tag).strip()
            )
            summon_auras = self._normalize_auras(
                effect.get("aura") or effect.get("auras")
            )
            spawned = 0
            for attempt in range(count):
                x, y = (
                    self.effect_position(effect)
                    if attempt == 0
                    else (self.state.player.x, self.state.player.y)
                )
                if not self.can_occupy(x, y):
                    x, y = self.find_open_tile_near(
                        self.state.player.x, self.state.player.y
                    )
                if not self.can_occupy(x, y):
                    continue
                self.spawn_actor(
                    name,
                    char,
                    x,
                    y,
                    hp,
                    attack,
                    defense,
                    faction,
                    "simple" if faction == "enemy" else None,
                    tags=tags,
                    resistances=normalize_numeric_map(effect.get("resistances"), 0, 95),
                    weaknesses=normalize_numeric_map(effect.get("weaknesses"), 0, 200),
                    auras=summon_auras,
                )
                spawned += 1
            if spawned == 0:
                return [f"{name} tries to arrive, but finds no room."]
            return [
                f"{spawned} {name}{'' if spawned == 1 else 's'} {'arrives' if spawned == 1 else 'arrive'}."
            ]
        if effect_type == "spawn_item":
            name = str(effect.get("name") or effect.get("item") or "oddment")
            item_type = str(effect.get("item_type") or effect.get("item") or name)
            x, y = self.effect_position(effect)
            if self.tile_at(x, y) in BLOCKING_TILES:
                player = self.state.player
                x, y = self.find_open_tile_near(player.x, player.y)
            self.spawn_item(
                name,
                str(effect.get("char") or "?")[:1],
                x,
                y,
                item_type,
                clamp_int(effect.get("quantity"), 1, 99),
                material=str(effect.get("material") or "") or None,
                tags=set(
                    normalize_id(str(tag))
                    for tag in coerce_list(effect.get("tags"))
                    if str(tag).strip()
                ),
            )
            return [f"{name} appears."]
        if effect_type == "conjure_item":
            return self._conjure_item(effect)
        if effect_type == "conjure_creature":
            return self._conjure_creature(effect)
        if effect_type == "transform_item":
            raw_target = effect.get("target")
            target_type = normalize_id(str(raw_target or "nearest_item"))
            item = str(effect.get("item") or effect.get("item_type") or "").strip()
            new_name = str(
                effect.get("new_name")
                or effect.get("name")
                or effect.get("new_item_type")
                or "oddment"
            ).strip()
            new_description = str(effect.get("description") or "").strip()
            new_material = str(effect.get("material") or "").strip() or None
            new_tags = [
                normalize_id(str(tag))
                for tag in coerce_list(effect.get("tags"))
                if str(tag).strip()
            ]

            if target_type == "inventory":
                if not item:
                    return []
                current = self.state.inventory.get(item, 0)
                if current > 0:
                    self.state.inventory[item] = current - 1
                    if self.state.inventory[item] <= 0:
                        del self.state.inventory[item]
                    self.state.inventory[new_name] = (
                        self.state.inventory.get(new_name, 0) + 1
                    )
                    return [f"The {item} in your inventory becomes {new_name}."]
                return [f"You have no {item} to transform."]

            target = None
            if raw_target is not None and target_type not in {
                "item",
                "nearest_item",
                "prop",
                "nearest_prop",
                "object",
                "nearest_object",
            }:
                bound = self.resolve_target(raw_target)
                if bound is not None and bound.kind in {"item", "prop"}:
                    target = bound

            player = self.state.player
            if target is None:
                wanted_kinds = {"item"}
                if target_type in {"prop", "nearest_prop"}:
                    wanted_kinds = {"prop"}
                elif target_type in {"object", "nearest_object"}:
                    wanted_kinds = {"item", "prop"}
                candidates = []
                for entity in self.state.entities.values():
                    if entity.kind not in wanted_kinds or not entity.alive:
                        continue
                    if item and not (
                        item.lower() in entity.name.lower()
                        or item.lower() in (entity.item_type or "").lower()
                    ):
                        continue
                    candidates.append(entity)
                if not candidates:
                    label = item or target_type.replace("_", " ")
                    return [f"No {label} found to transform."]
                target = min(candidates, key=lambda e: self.distance(player, e))

            old_name = target.name
            target.name = new_name[:40] or target.name
            if target.kind == "item":
                target.item_type = target.name
            else:
                target.details["transformed_by_magic"] = True
                if new_description:
                    target.description = new_description[:240]
                elif not target.description:
                    target.description = f"It has been transformed into {target.name}."
            if new_material:
                target.material = new_material
            if new_tags:
                target.tags.update(new_tags)
            return [f"The {old_name} transforms into {target.name}."]

        if effect_type == "modify_inventory":
            item = str(effect.get("item") or effect.get("item_type") or "").strip()
            if not item:
                return []
            amount = clamp_int(effect.get("amount"), -99, 99)
            mode = str(effect.get("mode") or "add").lower()
            current = self.state.inventory.get(item, 0)
            if mode == "set":
                new_amount = max(0, amount)
            elif mode == "remove":
                new_amount = max(0, current - abs(amount))
            else:
                new_amount = max(0, current + amount)
            if new_amount:
                self.state.inventory[item] = new_amount
            else:
                self.state.inventory.pop(item, None)
            return [f"Inventory shifts: {item} x{new_amount}."]
        if effect_type == "transform_entity":
            target = self.resolve_target(effect.get("target") or "nearest_enemy")
            if not target:
                return []
            if "name" in effect:
                target.name = str(effect["name"])[:40]
            if "char" in effect:
                target.char = str(effect["char"])[:1] or target.char
            if "faction" in effect:
                target.faction = normalize_faction(
                    effect["faction"], default=target.faction
                )
            if "material" in effect:
                target.material = str(effect["material"])[:32]
            target.max_hp = clamp_int(effect.get("max_hp", target.max_hp), 1, 99)
            target.hp = clamp_int(effect.get("hp", target.hp), 0, target.max_hp)
            target.attack = clamp_int(effect.get("attack", target.attack), 0, 20)
            target.defense = clamp_int(effect.get("defense", target.defense), 0, 20)
            target.tags.update(
                normalize_id(str(tag))
                for tag in coerce_list(effect.get("tags"))
                if str(tag).strip()
            )
            if target.id == self.state.player_id:
                return ["You are transformed."]
            return [f"{target.name} {self._verb(target, 'are', 'is')} transformed."]
        if effect_type == "change_faction":
            target = self.resolve_target(effect.get("target") or "nearest_enemy")
            if not target or target.kind == "item":
                return []
            new_faction = normalize_faction(effect.get("faction"), default="neutral")
            target.faction = new_faction
            target.ai = None if target.faction in {"ally", "player"} else target.ai
            return [f"{target.name} now belongs to {target.faction}."]
        if effect_type in {"add_tag", "remove_tag"}:
            target = self.resolve_target(effect.get("target") or "player")
            tag = normalize_id(str(effect.get("tag") or "strange"))
            if not target:
                return []
            if effect_type == "add_tag":
                target.tags.add(tag)
                return [
                    f"{target.name} {self._verb(target, 'gain', 'gains')} the {tag} tag."
                ]
            target.tags.discard(tag)
            return [
                f"{target.name} {self._verb(target, 'lose', 'loses')} the {tag} tag."
            ]
        if effect_type in {"add_resistance", "add_weakness"}:
            target = self.resolve_target(effect.get("target") or "player")
            if not target:
                return []
            damage_type = normalize_id(
                str(effect.get("damage_type") or effect.get("resistance") or "arcane")
            )
            amount = clamp_int(
                effect.get("amount"), 1, 95 if effect_type == "add_resistance" else 200
            )
            table = (
                target.resistances
                if effect_type == "add_resistance"
                else target.weaknesses
            )
            table[damage_type] = clamp_int(
                table.get(damage_type, 0) + amount,
                0,
                95 if effect_type == "add_resistance" else 200,
            )
            if effect_type == "add_resistance":
                return [
                    f"{target.name} {self._verb(target, 'resist', 'resists')} {damage_type}."
                ]
            return [
                f"{target.name} {self._verb(target, 'are', 'is')} vulnerable to {damage_type}."
            ]
        if effect_type == "set_flag":
            flag = normalize_id(
                str(effect.get("flag") or effect.get("id") or "unnamed_flag")
            )
            self.state.flags[flag] = effect.get("value", True)
            # The model reaches for debt-flavored flags constantly (audit mining
            # 2026-06: 148 of 152 set_flag uses were "future_debt") — a flag is
            # mechanically inert, so make the debt real: a visible stacking
            # curse now, and a collector already on its way.
            if any(
                word in flag
                for word in (
                    "debt",
                    "owed",
                    "owe",
                    "price",
                    "payment",
                    "reckoning",
                    "collector",
                )
            ):
                return self._incur_wild_debt()
            return [f"World flag set: {flag}."]
        if effect_type == "schedule_event":
            event = self._normalize_scheduled_event(effect)
            self.state.event_timers.append(event)
            return [f"Something has been scheduled in {event['turns']} turn(s)."]
        if effect_type == "delay_incoming":
            return self._apply_delay_incoming(effect)
        if effect_type == "accelerate_status":
            return self._apply_accelerate_status(effect)
        if effect_type == "set_behavior":
            return self._apply_set_behavior(effect)
        if effect_type in {"create_trigger", "trigger", "ward"}:
            trigger_name = normalize_trigger_name(
                str(effect.get("trigger") or effect.get("on") or "on_next_spell")
            )
            effects = coerce_list(effect.get("effects") or effect.get("effect"))
            if not effects:
                return ["The trigger has nothing to do and collapses."]
            _TRIGGER_DEFAULT_NAMES = {
                "on_player_hit": "Retaliatory echo",
                "on_player_damaged": "Wound pact",
                "on_damaged": "Wound pact",
                "on_enemy_hit": "Predator's mark",
                "on_enemy_damaged": "Predator's mark",
                "on_enemy_death": "Death-pact",
                "on_lethal_damage": "Last-breath pact",
                "on_curse_gained": "Curse covenant",
                "on_next_spell": "Spell chain",
                "on_player_move": "Footstep echo",
            }
            raw_name = str(effect.get("name") or "").strip()
            default_name = _TRIGGER_DEFAULT_NAMES.get(trigger_name, "A waiting spell")
            # A free-floating reaction ward that names no subject is about its caster. The
            # universal "on_damaged" hook (what "on hit / struck / took damage" now maps to)
            # fires for EVERY combatant, so default its subject to the player -- otherwise a
            # "when I'm hit, lash the attacker" ward would also lash whenever an ally strikes
            # a foe. An explicit target (an ally/enemy id, a tag, "any") opts out; that is how
            # a ward watches an ally or an enemy. Subject-specific hooks already encode a side.
            raw_target = effect.get("target")
            if raw_target is None:
                raw_target = "player" if trigger_name == "on_damaged" else "any"
            trigger = {
                "id": self.next_entity_id("trigger"),
                "name": sanitize_name(raw_name or default_name, default_name),
                "trigger": trigger_name,
                "target": raw_target,
                "charges": clamp_int(effect.get("charges"), 1, 9),
                "duration": effect.get("duration", effect.get("turns", 6)),
                "effects": [dict(raw) for raw in effects[:8] if isinstance(raw, dict)],
            }
            if isinstance(effect.get("when"), dict):
                trigger["when"] = dict(effect["when"])
            if trigger["duration"] != "permanent":
                trigger["expires_turn"] = self.state.turn + clamp_int(
                    trigger["duration"], 1, 999
                )
            self.state.triggers.append(trigger)
            return [f"{trigger['name']} waits for {trigger_name.replace('_', ' ')}."]
        if effect_type == "create_persistent_effect":
            return self._create_persistent_effect(effect)
        if effect_type == "create_promise":
            return self._create_spoken_promise(effect)
        if effect_type == "add_curse":
            message = self._apply_cost({"type": "curse", **effect})
            return [message] if message else []
        if effect_type == "possess":
            target = self.resolve_target(effect.get("target") or "nearest_enemy")
            if not target or target.kind in {"item", "prop"}:
                return ["The possession finds no one to inhabit."]
            return self.swap_control_to(target.id)
        if effect_type == "edit_memory":
            return self._apply_edit_memory(effect)
        if effect_type == "animate_object":
            return self._apply_animate_object(effect)
        if effect_type == "aura":
            return self._apply_aura_effect(effect)
        if effect_type == "add_trait":
            return self._apply_add_trait(effect)
        if effect_type == "message":
            text = str(effect.get("text") or "").strip()
            return [text] if text else []
        return []

    def _normalize_scheduled_event(self, effect: dict[str, Any]) -> dict[str, Any]:
        event = dict(
            effect.get("event") if isinstance(effect.get("event"), dict) else effect
        )
        event.pop("type", None)
        event["turns"] = clamp_int(effect.get("turns", event.get("turns")), 1, 999)
        event["event_type"] = str(
            effect.get("event_type") or event.get("event_type") or "message"
        )

        effects = coerce_list(event.get("effects") or event.get("effect"))
        if effects:
            event["effects"] = [
                dict(raw) for raw in effects[:8] if isinstance(raw, dict)
            ]
            event.pop("effect", None)
        costs = coerce_list(event.get("costs") or event.get("cost"))
        if costs:
            event["costs"] = [dict(raw) for raw in costs[:6] if isinstance(raw, dict)]
            event.pop("cost", None)
        return event

    def _run_scheduled_payload(self, event: dict[str, Any]) -> bool:
        """Run a generalized delayed payload if this timer carries one.

        Legacy event_type timers still flow through engine._trigger_event's old branches when
        no payload arrays are present. New timers can carry normal effects/costs and reuse the
        same application paths as a spell cast.
        """
        effects = [
            raw for raw in coerce_list(event.get("effects")) if isinstance(raw, dict)
        ]
        costs = [
            raw for raw in coerce_list(event.get("costs")) if isinstance(raw, dict)
        ]
        if not effects and not costs:
            return False
        text = str(event.get("text") or event.get("message") or "").strip()
        if text:
            self.state.add_message(text)
        for raw_effect in effects[:8]:
            for message in self._apply_effect(raw_effect):
                self.state.add_message(message)
        for raw_cost in costs[:6]:
            message = self._apply_cost(raw_cost)
            if message:
                self.state.add_message(message)
        return True

    def _damage_would_be_delayed(self, entity: Entity | None) -> bool:
        if entity is None or entity.kind == "item" or entity.hp <= 0:
            return False
        if entity.details.get("_releasing_delayed_damage"):
            return False
        return "delayed_sink" in entity.statuses and isinstance(
            entity.details.get("delayed_damage"), dict
        )

    def _capture_delayed_damage(
        self,
        entity: Entity,
        amount: int,
        damage_type: str,
        source: Entity | None = None,
    ) -> bool:
        if not self._damage_would_be_delayed(entity):
            return False
        sink = entity.details.get("delayed_damage")
        if not isinstance(sink, dict):
            return False
        packets = sink.setdefault("packets", [])
        if not isinstance(packets, list):
            packets = []
            sink["packets"] = packets
        packets.append(
            {
                "amount": clamp_int(amount, 1, 999),
                "damage_type": normalize_id(str(damage_type or "arcane")),
                "source_id": source.id if isinstance(source, Entity) else None,
            }
        )
        name = str(sink.get("name") or "delayed wound")
        self.state.add_message(
            f"{entity.name}'s {name} holds {clamp_int(amount, 1, 999)} {damage_type} damage for later.",
            is_danger=entity.id == self.state.player_id,
        )
        return True

    def _apply_delay_incoming(self, effect: dict[str, Any]) -> list[str]:
        target_str = normalize_id(str(effect.get("target") or "player"))
        targets = self.resolve_target_group(target_str)
        if not targets:
            target = self.resolve_target(target_str)
            targets = [target] if target else []
        targets = [
            target
            for target in targets[:8]
            if target is not None and target.kind != "item" and target.hp > 0
        ]
        if not targets:
            return ["The delayed wound finds no living body to hold it."]
        turns = clamp_int(effect.get("turns", effect.get("duration", 3)), 1, 99)
        label = (
            str(effect.get("name") or "delayed wound").strip()[:40] or "delayed wound"
        )
        for target in targets:
            sink_id = self.next_entity_id("delay")
            target.statuses["delayed_sink"] = "permanent"
            target.status_display["delayed_sink"] = label
            target.status_expiry_text["delayed_sink"] = "The borrowed wound comes due."
            target.details["delayed_damage"] = {
                "id": sink_id,
                "name": label,
                "packets": [],
            }
            self.state.event_timers.append(
                {
                    "turns": turns,
                    "event_type": "release_delayed_damage",
                    "target": target.id,
                    "sink_id": sink_id,
                }
            )
        if len(targets) == 1:
            target = targets[0]
            if target.id == self.state.player_id:
                return [f"Your incoming damage will arrive in {turns} turn(s)."]
            return [f"{target.name}'s incoming damage will arrive in {turns} turn(s)."]
        return [f"Incoming damage is delayed for {len(targets)} target(s)."]

    def _release_delayed_damage(self, event: dict[str, Any]) -> None:
        target_id = str(event.get("target") or "")
        target = self.state.entities.get(target_id)
        if target is None or target.kind == "item" or target.hp <= 0:
            return
        sink = target.details.get("delayed_damage")
        if not isinstance(sink, dict):
            return
        if event.get("sink_id") and sink.get("id") != event.get("sink_id"):
            return
        packets = [
            packet
            for packet in coerce_list(sink.get("packets"))
            if isinstance(packet, dict)
        ]
        target.details.pop("delayed_damage", None)
        target.statuses.pop("delayed_sink", None)
        target.status_display.pop("delayed_sink", None)
        target.status_expiry_text.pop("delayed_sink", None)
        if not packets:
            self.state.add_message(f"{target.name}'s delayed wound fades empty.")
            return
        target.details["_releasing_delayed_damage"] = True
        try:
            self.state.add_message(
                f"{target.name}'s delayed wound comes due.",
                is_danger=target.id == self.state.player_id,
            )
            for packet in packets[:20]:
                if target.hp <= 0:
                    break
                amount = clamp_int(packet.get("amount"), 1, 999)
                damage_type = normalize_id(str(packet.get("damage_type") or "arcane"))
                source = self.state.entities.get(str(packet.get("source_id") or ""))
                source_entity = source if isinstance(source, Entity) else None
                actual = self.calculate_actual_damage(target, amount, damage_type)
                self.state.add_message(
                    f"{target.name} {self._verb(target, 'take', 'takes')} {actual} delayed {damage_type} damage.",
                    is_danger=target.id == self.state.player_id and actual > 0,
                )
                self.damage_entity(target, amount, damage_type, source=source_entity)
        finally:
            target.details.pop("_releasing_delayed_damage", None)

    _ACCELERATED_STATUS_DAMAGE = {
        "poisoned": ("poison", 1),
        "burning": ("fire", 1),
        "bleeding": ("blood", 1),
    }

    def _apply_accelerate_status(self, effect: dict[str, Any]) -> list[str]:
        target = self.resolve_target(effect.get("target") or "nearest_enemy")
        if not target or target.kind == "item" or target.hp <= 0:
            return ["The accelerated affliction finds no living target."]
        status = normalize_id(str(effect.get("status") or "poisoned"))
        status = STATUS_FLAVOR_ALIASES.get(status, status)
        damage_spec = self._ACCELERATED_STATUS_DAMAGE.get(status)
        if damage_spec is None:
            return [f"{status.replace('_', ' ')} has no damaging ticks to accelerate."]
        if status not in target.statuses:
            return [f"{target.name} is not {status.replace('_', ' ')}."]
        turns = max(0, status_duration(target.statuses.get(status)))
        target.statuses.pop(status, None)
        target.status_display.pop(status, None)
        target.status_expiry_text.pop(status, None)
        if turns <= 0:
            return [
                f"{target.name}'s {status.replace('_', ' ')} has already spent itself."
            ]
        damage_type, amount = damage_spec
        self.state.add_message(
            f"{target.name}'s {status.replace('_', ' ')} burns through {turns} tick(s) at once.",
            is_danger=target.id == self.state.player_id,
        )
        for _ in range(turns):
            if target.hp <= 0:
                break
            self.damage_entity(target, amount, damage_type, source=self.state.player)
        target.statuses.pop(status, None)
        target.status_display.pop(status, None)
        target.status_expiry_text.pop(status, None)
        return []

    def _apply_set_behavior(self, effect: dict[str, Any]) -> list[str]:
        behavior = normalize_behavior(
            effect.get("behavior")
            or effect.get("mode")
            or effect.get("ai")
            or effect.get("status")
        )
        if behavior not in SUPPORTED_BEHAVIORS:
            return ["The behavior has no shape the creature can act on."]
        target_text = effect.get("target") or "nearest_enemy"
        targets = self.resolve_target_group(target_text)
        if not targets:
            target = self.resolve_target(target_text)
            targets = [target] if target is not None else []
        targets = [
            target
            for target in targets[:12]
            if target.kind in {"actor", "npc"} and target.hp > 0
        ]
        if not targets:
            return ["No mind is near enough to bend that way."]
        focus_id = self._behavior_focus_id(effect)
        duration = effect.get("duration", effect.get("turns", 3))
        label = str(effect.get("name") or effect.get("label") or "").strip()
        for target in targets:
            upsert_behavior_modifier(
                target,
                behavior,
                duration=duration,
                target_id=focus_id,
                label=label,
            )
        behavior_text = behavior.replace("_", " ")
        if len(targets) == 1:
            return [f"{targets[0].name}'s behavior bends toward {behavior_text}."]
        return [f"{len(targets)} minds bend toward {behavior_text}."]

    def _behavior_focus_id(self, effect: dict[str, Any]) -> str | None:
        for key in (
            "behavior_target",
            "focus",
            "lock_to",
            "duel_target",
            "mimic_target",
            "copy",
            "source",
            "anchor",
        ):
            if key not in effect:
                continue
            target = self.resolve_target(effect.get(key))
            if target is not None and target.kind in {"player", "actor", "npc"}:
                return target.id
        behavior = normalize_behavior(effect.get("behavior") or effect.get("mode"))
        if behavior in {"duel", "mimic"}:
            return self.state.player_id
        return None

    _FLOW_DIRECTIONS = {
        "north": (0, -1),
        "n": (0, -1),
        "up": (0, -1),
        "south": (0, 1),
        "s": (0, 1),
        "down": (0, 1),
        "east": (1, 0),
        "e": (1, 0),
        "right": (1, 0),
        "west": (-1, 0),
        "w": (-1, 0),
        "left": (-1, 0),
        "northeast": (1, -1),
        "north_east": (1, -1),
        "ne": (1, -1),
        "northwest": (-1, -1),
        "north_west": (-1, -1),
        "nw": (-1, -1),
        "southeast": (1, 1),
        "south_east": (1, 1),
        "se": (1, 1),
        "southwest": (-1, 1),
        "south_west": (-1, 1),
        "sw": (-1, 1),
    }

    def _apply_create_flow(self, effect: dict[str, Any]) -> list[str]:
        center_x, center_y = self.effect_position(effect)
        duration = effect.get("duration", effect.get("turns", 4))
        duration_value: int | str = (
            "permanent" if duration == "permanent" else clamp_int(duration, 1, 999)
        )
        points = self._flow_points(effect, center_x, center_y)
        changed = 0
        for tx, ty in points[:200]:
            dx, dy = self._flow_vector_for_tile(effect, tx, ty, center_x, center_y)
            if dx == 0 and dy == 0:
                continue
            self.state.tile_flows[self.tile_key(tx, ty)] = {
                "dx": dx,
                "dy": dy,
                "duration": duration_value,
            }
            changed += 1
        if changed == 0:
            return ["The current cannot find a direction to flow."]
        return [f"A magical current takes hold on {changed} tile(s)."]

    def _flow_points(
        self, effect: dict[str, Any], center_x: int, center_y: int
    ) -> list[tuple[int, int]]:
        tile_specs = effect.get("tiles")
        if isinstance(tile_specs, list):
            points = []
            for spec in tile_specs[:200]:
                if not isinstance(spec, dict):
                    continue
                points.append(
                    (
                        clamp_int(spec.get("x"), 0, self.state.width - 1),
                        clamp_int(spec.get("y"), 0, self.state.height - 1),
                    )
                )
            return unique_points(points)
        shape = normalize_id(str(effect.get("shape") or effect.get("pattern") or ""))
        if shape in {
            "line",
            "beam",
            "path",
            "corridor",
            "ray",
            "bridge",
            "wall",
            "barrier",
            "cone",
            "fan",
            "scatter",
            "spray",
        }:
            return self.shape_points(effect, center_x, center_y)
        radius = clamp_int(effect.get("radius"), 0, 12)
        return self.points_in_radius(center_x, center_y, radius)

    def _flow_vector_for_tile(
        self, effect: dict[str, Any], tx: int, ty: int, center_x: int, center_y: int
    ) -> tuple[int, int]:
        if "dx" in effect or "dy" in effect:
            raw_dx = effect.get("dx", 0)
            raw_dy = effect.get("dy", 0)
            return (
                sign(clamp_int(raw_dx, -1, 1) if raw_dx is not None else 0),
                sign(clamp_int(raw_dy, -1, 1) if raw_dy is not None else 0),
            )
        direction = normalize_id(
            str(effect.get("direction") or effect.get("dir") or "")
        )
        if direction in self._FLOW_DIRECTIONS:
            return self._FLOW_DIRECTIONS[direction]
        mode = normalize_id(str(effect.get("mode") or effect.get("kind") or ""))
        if mode in {"inward", "pull", "gravity", "gravity_well", "toward_center"}:
            return sign(center_x - tx), sign(center_y - ty)
        if mode in {"outward", "push", "repel", "away", "away_from_center"}:
            return sign(tx - center_x), sign(ty - center_y)
        origin = self.resolve_target(effect.get("origin") or effect.get("source"))
        if origin is not None:
            return sign(center_x - origin.x), sign(center_y - origin.y)
        return 0, 0

    def _resolution_ref_error(self, resolution: dict[str, Any]) -> str | None:
        for effect in coerce_list(resolution.get("effects")):
            if isinstance(effect, dict):
                error = self._effect_ref_error(_flatten_effect(effect))
                if error:
                    return error
        return None

    def _effect_ref_error(self, effect: dict[str, Any]) -> str | None:
        """Validate explicit typed refs before mutation.

        Legacy string targets keep their historical forgiving behavior. Dict-shaped refs are
        deliberate contracts, so unknown ids/selectors or out-of-bounds tiles make the cast a
        technical failure before any partial state change occurs.
        """
        for key in _REF_FIELDS:
            if key in effect:
                error = refs.typed_ref_error(self, effect.get(key))
                if error:
                    return f"{key}: {error}"
        for tile in coerce_list(effect.get("tiles")):
            error = refs.typed_ref_error(self, tile)
            if error:
                return f"tiles: {error}"
        for nested in coerce_list(effect.get("effects") or effect.get("effect")):
            if isinstance(nested, dict):
                error = self._effect_ref_error(_flatten_effect(nested))
                if error:
                    return error
        return None

    # ------------------------------------------------------ persistent effects
    _SYMPATHETIC_KINDS = {
        "sympathetic_link",
        "sympathetic",
        "link",
        "bond",
        "pain_link",
    }
    # Hooks that fire on the attacker (matched against the event's SOURCE), so a persistent
    # effect anchored on a striker rides the blows it lands. See engine._fire_damage_triggers.
    _SOURCE_HOOKS = {
        "on_deal_damage",
        "on_player_deal_damage",
        "on_enemy_deal_damage",
    }

    def _create_persistent_effect(self, effect: dict[str, Any]) -> list[str]:
        """An ongoing magical attachment anchored to a concrete entity: it lives ON that
        anchor, fires its nested effects on a hook while charges and duration last, and ENDS
        when the anchor dies. The shared substrate behind sympathetic links and creature-bound
        wards/curses. Two sides, by hook:
          - DEFENDER ('on_hit'): fires when the anchor is struck; effects hit trigger_source
            (the attacker). A retaliation ward / thornmail.
          - ATTACKER ('on_strike'/'on_deal_damage'): fires when the anchor lands a blow;
            effects hit trigger_target (the victim). 'a blade that bleeds whatever I strike',
            anchored on the wielding CHARACTER for now -- the same machinery will anchor on a
            specific weapon ITEM once items carry instance state (match:'weapon').
        Unlike create_trigger (a free-floating conditional packet), this is bound to a life."""
        kind = normalize_id(str(effect.get("kind") or "persistent_effect"))
        if kind in self._SYMPATHETIC_KINDS:
            return self._create_sympathetic_link(effect)
        nested = [
            dict(raw)
            for raw in coerce_list(effect.get("effects") or effect.get("effect"))[:8]
            if isinstance(raw, dict)
        ]
        if not nested:
            return ["The persistent effect has nothing to anchor, and unravels."]
        # Default hook fires when the anchor is struck. "on_hit" normalizes to the universal
        # on_damaged (fires for any entity, then target-filtered to this anchor). An attacker-
        # side hook (on_strike -> on_deal_damage) is matched against the event's SOURCE, so the
        # criterion is still the anchor but now means "when the anchor deals a hit".
        hook = str(
            effect.get("hook") or effect.get("trigger") or effect.get("on") or "on_hit"
        )
        hook_norm = normalize_trigger_name(hook)
        explicit_match = normalize_id(str(effect.get("match") or ""))
        if explicit_match in {"source", "target"}:
            match_role = explicit_match
        else:
            match_role = "source" if hook_norm in self._SOURCE_HOOKS else "target"
        anchor = self.resolve_target(
            str(effect.get("anchor") or effect.get("attached_to") or "player")
        )
        anchor_id = anchor.id if anchor is not None and anchor.kind != "item" else None
        match_target = effect.get("target", anchor_id or "any")
        trigger = self._register_persistent_trigger(
            name=str(effect.get("name") or ""),
            default_name="A bound enchantment",
            kind=kind,
            anchor_id=anchor_id,
            link_partner=None,
            hook=hook,
            target=match_target,
            match=match_role,
            charges=clamp_int(effect.get("charges", 99), 1, 999),
            duration=effect.get("duration", effect.get("turns", 8)),
            effects=nested,
        )
        where = (
            "you"
            if anchor_id == self.state.player_id
            else (anchor.name if anchor is not None else "the waiting air")
        )
        return [f"{trigger['name']} settles over {where} and takes hold."]

    def _create_sympathetic_link(self, effect: dict[str, Any]) -> list[str]:
        """Bind two entities so harm to one echoes onto the other: 'whatever wounds me
        wounds him', 'bind the goblin's pain to the ogre'. Implemented as an anchored
        persistent effect whose hook (on_damaged on the SOURCE) echoes the actual damage,
        scaled by ratio, onto the SINK. mutual=true binds both directions. The link ends
        when either end dies (anchor lifecycle in _tick_triggers)."""
        source = self.resolve_target(
            str(
                effect.get("source")
                or effect.get("from")
                or effect.get("anchor")
                or "player"
            )
        )
        sink = self.resolve_target(
            str(
                effect.get("sink")
                or effect.get("to")
                or effect.get("victim")
                or effect.get("target")
                or "nearest_enemy"
            )
        )
        if source is None or sink is None:
            return ["The link reaches for two souls and finds only one."]
        if source.kind in {"item", "prop"} or sink.kind in {"item", "prop"}:
            return ["Only living things can be bound heart to heart."]
        if source.id == sink.id:
            return ["A thing cannot be bound to itself; the link will not hold."]
        try:
            ratio = max(
                0.1, min(2.0, float(effect.get("ratio", effect.get("share", 1.0))))
            )
        except (TypeError, ValueError):
            ratio = 1.0
        duration = effect.get("duration", effect.get("turns", 8))
        mutual = bool(
            effect.get("mutual") or effect.get("two_way") or effect.get("reciprocal")
        )
        name = sanitize_name(str(effect.get("name") or ""), "Sympathetic link")
        self._register_sympathetic_arc(source, sink, ratio, duration, name)
        if mutual:
            self._register_sympathetic_arc(sink, source, ratio, duration, name)
        src_who = "you" if source.id == self.state.player_id else source.name
        sink_who = "you" if sink.id == self.state.player_id else sink.name
        if mutual:
            return [
                f"{name}: {src_who} and {sink_who} now share every wound between them."
            ]
        return [f"{name}: whatever wounds {src_who} now wounds {sink_who}."]

    def _register_sympathetic_arc(
        self,
        source: "Entity",
        sink: "Entity",
        ratio: float,
        duration: Any,
        name: str,
    ) -> None:
        echo = {
            "type": "damage",
            "target": sink.id,
            "amount": "trigger_amount",
            "amount_ratio": ratio,
            "damage_type": "trigger_damage_type",
        }
        self._register_persistent_trigger(
            name=name,
            default_name="Sympathetic link",
            kind="sympathetic_link",
            anchor_id=source.id,
            link_partner=sink.id,
            hook="on_damaged",
            target=source.id,
            charges=999,
            duration=duration,
            effects=[echo],
        )

    def _register_persistent_trigger(
        self,
        *,
        name: str,
        default_name: str,
        kind: str,
        anchor_id: str | None,
        link_partner: str | None,
        hook: str,
        target: Any,
        charges: int,
        duration: Any,
        effects: list[dict[str, Any]],
        match: str = "target",
    ) -> dict[str, Any]:
        """Register a persistent effect in the shared trigger store. It is an ordinary
        trigger dict plus `kind`, `anchor`, (for links) `link_partner`, and `match` (which
        event role the criterion is checked against -- "target" for defender-side, "source"
        for attacker-side), so it rides the existing _fire_triggers / _tick_triggers / save
        paths for free; the extra keys only drive matching, lifecycle, and UI/context."""
        trigger: dict[str, Any] = {
            "id": self.next_entity_id("persist"),
            "name": sanitize_name(name, default_name),
            "kind": kind,
            "trigger": normalize_trigger_name(hook),
            "target": target if target is not None else "any",
            "charges": clamp_int(charges, 1, 999),
            "duration": duration,
            "effects": effects,
        }
        if match == "source":
            trigger["match"] = "source"
        if anchor_id:
            trigger["anchor"] = anchor_id
        if link_partner:
            trigger["link_partner"] = link_partner
        if duration != "permanent":
            trigger["expires_turn"] = self.state.turn + clamp_int(duration, 1, 999)
        self.state.triggers.append(trigger)
        return trigger

    def _nearest_npc(self) -> "Entity | None":
        player = self.state.player
        npcs = [e for e in self.state.entities.values() if e.kind == "npc" and e.hp > 0]
        if not npcs:
            return None
        return min(npcs, key=lambda e: self.distance(player, e))

    def _apply_edit_memory(self, effect: dict[str, Any]) -> list[str]:
        """Bend a mind: add, remove, or alter what an NPC remembers. Operates on the NPC's
        structured and legacy memory lanes. Forgetting the caster from a hostile NPC also
        calms it -- 'make the guard forget me' should actually end the pursuit, not just
        change small talk."""
        target = self.resolve_target(effect.get("target") or "nearest_enemy")
        if target is None or target.kind != "npc":
            target = self._nearest_npc()
        if target is None:
            return [
                "The spell reaches for a mind, but finds no one here who keeps one."
            ]
        profile = self.state.npc_profiles.get(target.id)
        if profile is None:
            return [f"{target.name} has nothing the spell can take hold of."]

        op = normalize_id(str(effect.get("op") or "alter"))
        text = " ".join(str(effect.get("text") or "").split())[:200]
        subject = " ".join(str(effect.get("subject") or "").split())[:80]
        privacy = normalize_id(str(effect.get("privacy") or "social"))
        if privacy not in {"public", "social", "intimate", "secret"}:
            privacy = "social"
        shareable = bool(
            effect.get("shareable")
            or effect.get("socially_visible")
            or effect.get("spreadable")
        )
        subject_lower = subject.lower()
        player_name = (self.state.player.name or "").lower()
        caster_aliases = {
            "the caster",
            "caster",
            "me",
            "you",
            "the player",
            "the stranger",
            "my face",
            "the spellcaster",
            "the intruder",
        }
        refers_to_caster = (
            subject_lower in caster_aliases
            or (bool(player_name) and player_name in subject_lower)
            or not subject
        )

        def _mentions(mem: str) -> bool:
            padded = f" {mem.lower()} "
            if subject_lower and subject_lower in padded:
                return True
            if refers_to_caster and (
                (player_name and player_name in padded)
                or " you " in padded
                or " your " in padded
            ):
                return True
            return False

        def _record_mentions(record: NPCMemoryRecord) -> bool:
            haystack = " ".join(
                [
                    record.claim,
                    record.subject,
                    " ".join(record.subject_refs),
                    " ".join(record.tags),
                ]
            ).lower()
            padded = f" {haystack} "
            if subject_lower and subject_lower in padded:
                return True
            if refers_to_caster and (
                self.state.player_soul_id in record.subject_refs
                or (player_name and player_name in padded)
                or " you " in padded
                or " your " in padded
                or " player " in padded
            ):
                return True
            return False

        if op in {"remove", "erase", "forget", "wipe"}:
            kept = [m for m in profile.memory if not _mentions(m)]
            profile.memory = kept
            profile.memory_records = [
                record
                for record in profile.memory_records
                if not _record_mentions(record)
            ]
            calmed = False
            if refers_to_caster and target.faction not in {"ally", "player", "neutral"}:
                target.faction = "neutral"
                target.ai = None
                calmed = True
            if calmed:
                return [
                    f"{target.name} blinks, the hunt draining out of their face — whatever they "
                    f"were chasing, the memory of it is simply gone."
                ]
            return [f"A patch of {target.name}'s memory goes smooth and blank."]

        if op in {"add", "implant", "plant", "insert"}:
            if not text:
                return ["The false memory has no shape to take, and slips away."]
            profile.add_memory(
                NPCMemoryRecord(
                    id="",
                    claim=text,
                    provenance="implanted",
                    bucket="observation",
                    subtype="false_memory",
                    subject=subject,
                    subject_refs=[self.state.player_soul_id]
                    if refers_to_caster
                    else [],
                    tags=["implanted", "memory_edit"],
                    place_key=self.state.current_place_key(),
                    turn=self.state.turn,
                    confidence=1.0,
                    salience=clamp_int(effect.get("strength") or 3, 1, 5),
                    privacy=privacy,
                    shareable=shareable,
                    source_event_id=f"memory_edit:{self.state.turn}:{target.id}",
                )
            )
            return [
                f"A memory that never happened settles into {target.name}'s mind as if it always lived there."
            ]

        # alter (default): drop what matched, then plant the new recollection.
        if subject_lower or refers_to_caster:
            profile.memory = [m for m in profile.memory if not _mentions(m)]
            profile.memory_records = [
                record
                for record in profile.memory_records
                if not _record_mentions(record)
            ]
        if text:
            profile.add_memory(
                NPCMemoryRecord(
                    id="",
                    claim=text,
                    provenance="implanted",
                    bucket="observation",
                    subtype="edited_memory",
                    subject=subject,
                    subject_refs=[self.state.player_soul_id]
                    if refers_to_caster
                    else [],
                    tags=["implanted", "memory_edit"],
                    place_key=self.state.current_place_key(),
                    turn=self.state.turn,
                    confidence=1.0,
                    salience=clamp_int(effect.get("strength") or 3, 1, 5),
                    privacy=privacy,
                    shareable=shareable,
                    source_event_id=f"memory_edit:{self.state.turn}:{target.id}",
                )
            )
        return [f"{target.name}'s memory rewrites itself quietly around your words."]

    def _resolve_prop_target(self, effect: dict[str, Any]) -> "Entity | None":
        tid = str(
            effect.get("target")
            or effect.get("object")
            or effect.get("anchor")
            or effect.get("prop")
            or ""
        ).strip()
        ent = self.state.entities.get(tid)
        if ent is not None and ent.kind == "prop":
            return ent
        props = [
            e
            for e in self.state.entities.values()
            if e.kind == "prop" and getattr(e, "alive", True)
        ]
        if not props:
            return None
        player = self.state.player
        return min(props, key=lambda e: self.distance(player, e))

    def _apply_animate_object(self, effect: dict[str, Any]) -> list[str]:
        """Bring an existing object/prop to life as a creature. The prop stops being
        scenery (it is removed) and a new actor stands in its place. Falls back to spawning
        near the player if no prop is present, so the spell never simply fizzles."""
        prop = self._resolve_prop_target(effect)
        faction = normalize_faction(
            effect.get("faction"), default="ally", neutral_is_ally=True
        )
        base = prop.name if prop is not None else "object"
        name = (str(effect.get("name") or f"animated {base}")).strip()[
            :40
        ] or f"animated {base}"
        hp = clamp_int(effect.get("hp") or 8, 1, 30)
        attack = clamp_int(effect.get("attack") or 3, 0, 12)
        defense = clamp_int(effect.get("defense") or 1, 0, 8)
        tags = {
            normalize_id(str(tag))
            for tag in coerce_list(effect.get("tags"))
            if str(tag).strip()
        }
        if prop is not None:
            x, y = prop.x, prop.y
            char = str(effect.get("char") or prop.char or "")[:1] or (
                "a" if faction == "ally" else "e"
            )
            self.state.entities.pop(prop.id, None)
        else:
            x, y = self.find_open_tile_near(self.state.player.x, self.state.player.y)
            char = str(effect.get("char") or ("a" if faction == "ally" else "e"))[:1]
        if not self.can_occupy(x, y):
            x, y = self.find_open_tile_near(x, y)
        self.spawn_actor(
            name,
            char,
            x,
            y,
            hp,
            attack,
            defense,
            faction,
            "simple" if faction == "enemy" else None,
            tags=tags,
        )
        where = prop.name if prop is not None else "a nearby object"
        where = where[:1].upper() + where[1:]
        return [f"{where} shudders, tears loose of its place, and rises as {name}."]

    # ------------------------------------------------------------------ auras
    def _normalize_auras(self, raw: Any) -> list[dict[str, Any]]:
        """Coerce model-emitted aura specs into the canonical, validated form the
        tick loop understands. Every aura MUST carry a real mechanical effect --
        damage, or a status that buffs/debuffs/slows. Anything that resolves to no
        mechanic is dropped, so an aura is never pure narration."""
        if isinstance(raw, dict):
            specs = [raw]
        elif isinstance(raw, list):
            specs = [a for a in raw if isinstance(a, dict)]
        else:
            return []
        out: list[dict[str, Any]] = []
        for a in specs:
            kind = normalize_id(str(a.get("kind") or a.get("mode") or "").strip())
            if kind not in {"damage", "status"}:
                # Infer from the fields the model actually supplied.
                if (
                    a.get("amount") is not None
                    or a.get("damage_type")
                    or a.get("element")
                ):
                    kind = "damage"
                elif a.get("status"):
                    kind = "status"
                else:
                    continue  # no mechanic to anchor -> not a real aura
            spec: dict[str, Any] = {
                "kind": kind,
                "radius": clamp_int(a.get("radius", 2), 1, 8),
                "affects": normalize_id(str(a.get("affects") or "enemies")),
                "label": " ".join(str(a.get("label") or a.get("name") or "").split())[
                    :40
                ],
            }
            if spec["affects"] not in {"enemies", "allies", "all"}:
                spec["affects"] = "enemies"
            if kind == "damage":
                spec["amount"] = clamp_int(a.get("amount", 1), 1, 12)
                spec["damage_type"] = normalize_id(
                    str(a.get("damage_type") or a.get("element") or "force")
                )
            else:
                status = normalize_id(str(a.get("status") or ""))
                if status not in MECHANICAL_STATUSES:
                    status = STATUS_FLAVOR_ALIASES.get(status, "")
                if not status:
                    continue  # an unusable status is no mechanic -> drop
                spec["status"] = status
                spec["duration"] = clamp_int(a.get("duration", 2), 1, 8)
                display = " ".join(
                    str(
                        a.get("display_name") or a.get("label") or a.get("name") or ""
                    ).split()
                )
                if display:
                    spec["display_name"] = display[:40]
            ttl = a.get("turns") or a.get("turns_left") or a.get("lifetime")
            if ttl is not None:
                spec["turns_left"] = clamp_int(ttl, 1, 99)
            out.append(spec)
        return out[:4]

    def _apply_aura_effect(self, effect: dict[str, Any]) -> list[str]:
        """Standalone `aura` effect: anchor a standing emanation to an entity (the
        caster by default) or to a patch of ground. Lets a spell wreathe the player
        in a searing corona, or hex the floor so anyone who lingers there bleeds."""
        auras = self._normalize_auras(
            effect.get("aura") or effect.get("auras") or effect
        )
        if not auras:
            return ["The aura has no real bite to anchor it, and gutters out."]
        if "turns" not in effect and "turns_left" not in effect:
            # A self-cast/ground aura should fade; a creature's aura (handled at
            # spawn) lasts as long as the creature. Default a finite life here.
            ttl = clamp_int(effect.get("duration", 5), 1, 99)
            for aura in auras:
                aura.setdefault("turns_left", ttl)
        target_key = normalize_id(str(effect.get("target") or "").strip())
        wants_tile = (
            target_key in {"tile", "here", "ground", "floor", "the_floor", "underfoot"}
            or effect.get("anchor") in {"tile", "ground", "floor"}
            or ("x" in effect and "y" in effect and not effect.get("target"))
        )
        if wants_tile:
            x, y = self.effect_position(effect)
            key = f"{x},{y}"
            self.state.tile_auras.setdefault(key, []).extend(auras)
            return ["The ground takes on a charged, waiting hush."]
        target = self.resolve_target(effect.get("target") or "player")
        if (
            target is None
            or target.kind in {"item"}
            or not getattr(target, "alive", True)
        ):
            target = self.state.player
        target.auras.extend(auras)
        who = "you" if target.id == self.state.player_id else target.name
        return [f"A standing aura settles around {who}."]

    def _tick_auras(self) -> None:
        """Resolve every standing aura once per turn -- entity-borne (creatures,
        items, props) and ground-anchored alike. Auras with a finite life count
        down and are pruned when spent."""
        if self._stasis_active():
            return
        for owner in list(self.state.entities.values()):
            auras = getattr(owner, "auras", None)
            if not auras or not owner.alive:
                continue
            survivors: list[dict[str, Any]] = []
            for aura in auras:
                self._apply_aura_tick(owner.x, owner.y, owner, aura)
                if self._aura_survives(aura):
                    survivors.append(aura)
            owner.auras = survivors
        for key, auras in list(self.state.tile_auras.items()):
            try:
                tx, ty = (int(part) for part in key.split(","))
            except (ValueError, TypeError):
                self.state.tile_auras.pop(key, None)
                continue
            survivors = []
            for aura in auras:
                self._apply_aura_tick(tx, ty, None, aura)
                if self._aura_survives(aura):
                    survivors.append(aura)
            if survivors:
                self.state.tile_auras[key] = survivors
            else:
                self.state.tile_auras.pop(key, None)

    def _aura_survives(self, aura: dict[str, Any]) -> bool:
        ttl = aura.get("turns_left")
        if ttl is None:
            return True  # tied to its owner's existence, not a clock
        ttl = int(ttl) - 1
        aura["turns_left"] = ttl
        return ttl > 0

    def _resolve_aura_victims(
        self, ox: int, oy: int, owner: "Entity | None", affects: str, radius: int
    ) -> list["Entity"]:
        victims: list[Entity] = []
        owner_has_side = owner is not None and owner.faction in {
            "player",
            "ally",
            "enemy",
        }
        for ent in self.entities_in_radius(ox, oy, radius):
            if ent.kind in {"item", "prop"} or ent.hp <= 0:
                continue
            if owner is not None and ent.id == owner.id:
                continue
            if affects == "all":
                victims.append(ent)
                continue
            if owner_has_side:
                hostile = self.is_hostile_to(owner, ent)
                if (affects == "enemies" and hostile) or (
                    affects == "allies" and not hostile
                ):
                    victims.append(ent)
            else:
                # Ground/neutral source has no side: "allies" means the player's
                # camp, anything else reaches every combatant in range.
                if affects == "allies":
                    if ent.faction in {"player", "ally"}:
                        victims.append(ent)
                else:
                    victims.append(ent)
        return victims

    def _apply_aura_tick(
        self, ox: int, oy: int, owner: "Entity | None", aura: dict[str, Any]
    ) -> None:
        affects = str(aura.get("affects") or "enemies")
        radius = clamp_int(aura.get("radius", 2), 1, 8)
        victims = self._resolve_aura_victims(ox, oy, owner, affects, radius)
        if not victims:
            return
        label = str(aura.get("label") or "").strip()
        if aura.get("kind") == "damage":
            amount = clamp_int(aura.get("amount", 1), 1, 12)
            dtype = normalize_id(str(aura.get("damage_type") or "force"))
            for victim in victims:
                self.damage_entity(victim, amount, dtype, source=owner)
            self._announce_aura(owner, label or f"{dtype} aura", victims)
        else:
            status = normalize_id(str(aura.get("status") or ""))
            if status not in MECHANICAL_STATUSES:
                return
            duration = clamp_int(aura.get("duration", 2), 1, 8)
            display = str(aura.get("display_name") or "").strip()
            for victim in victims:
                current = victim.statuses.get(status)
                current_turns = current if isinstance(current, int) else 0
                victim.statuses[status] = max(current_turns, duration)
                if display and display != status.replace("_", " "):
                    victim.status_display[status] = display
            self._announce_aura(
                owner, label or display or status.replace("_", " "), victims
            )

    def _announce_aura(
        self, owner: "Entity | None", label: str, victims: list["Entity"]
    ) -> None:
        """Keep aura chatter quiet unless it touches the player -- these fire every
        turn, so creature-on-creature emanations stay off the log to avoid spam."""
        player_id = self.state.player_id
        if any(victim.id == player_id for victim in victims):
            source = owner.name if owner is not None else "the charged ground"
            self.state.add_message(
                f"{source}'s {label} bites into you.", is_danger=True
            )
        elif owner is not None and owner.id == player_id:
            names = ", ".join(victim.name for victim in victims)
            self.state.add_message(f"Your {label} washes over {names}.")

    def _apply_add_trait(self, effect: dict[str, Any]) -> list[str]:
        """Mint a narrative trait onto an entity: a descriptive fact with no fixed rule that
        the LLM consumers will weigh later (the resolver when the thing is involved, an NPC
        who notices it, the AI when it acts). The semantic-effects write path -- see
        wildmagic/semantics.py and docs/SEMANTIC_EFFECTS.md. Stored on the entity (so it rides
        into prompts for free) AND in the ledger (so place/faction queries can find it)."""
        target = self.resolve_target(effect.get("target") or "nearest_enemy")
        if (
            target is None
            or target.kind in {"item"}
            and not getattr(target, "alive", True)
        ):
            target = None
        if target is None:
            target = self.resolve_target("nearest_enemy") or self.state.player
        text = " ".join(str(effect.get("text") or effect.get("trait") or "").split())[
            :120
        ]
        if not text:
            return ["The trait has no shape to take, and fades."]
        salience = effect.get("salience")
        # The durable entity-trait lane (Stage 7): writes the entity + the semantic ledger
        # and records a `trait` delta. See wildmagic/operations.py.
        operations.write_trait(
            self,
            target,
            text,
            salience=int(salience) if isinstance(salience, (int, float)) else 4,
        )
        who = "you" if target.id == self.state.player_id else target.name
        return [f"Something about {who} is changed, and the world will remember it."]

    def _create_spoken_promise(self, effect: dict[str, Any]) -> list[str]:
        """Prophecy: the player speaks a future into the ledger. The spoken claim goes
        through the same binding gate as any rumor — concrete words bind, loose words
        stay flavor — and the world honors what binds. Speaking is never free, and
        prophesied treasure is borrowed, not given: it incurs Wild Debt."""
        kind = normalize_id(str(effect.get("kind") or "prophecy")) or "prophecy"
        if kind not in {"prophecy", "threat", "rumor", "place", "person"}:
            kind = "prophecy"
        what = " ".join(str(effect.get("what") or "").split())[:80]
        where = " ".join(str(effect.get("where") or "").split())[:120]
        item_name = " ".join(str(effect.get("item") or "").split())[:60]
        if item_name and not what:
            what = "cache"  # prophesied things wait somewhere holdable
        subject = " ".join(
            str(effect.get("subject") or what or "a spoken prophecy").split()
        )[:80]
        text = " ".join(str(effect.get("text") or effect.get("claim") or "").split())[
            :360
        ]
        if not text:
            text = f"You spoke it into the world: {subject}."
        salience = clamp_int(effect.get("salience") or 3, 1, 5)
        zone = (self.state.zone_x, self.state.zone_y)
        claimed = (
            parse_spatial_hint(
                where, fallback_text=f"{subject} {text}", anchor_zone=zone
            )
            if where
            else None
        )
        digest = hashlib.sha1(
            f"{self.state.turn}|{subject}|{text}|{item_name}".encode("utf-8")
        ).hexdigest()[:12]
        promise = WorldPromise(
            id=f"promise_prophecy_{digest}",
            kind=kind,
            subject=subject,
            text=text,
            tags=[tag for tag in (normalize_id(what), "prophecy") if tag],
            source="spell:prophecy",
            source_turn=self.state.turn,
            origin_zone=zone,
            location=self.state.location_label(),
            salience=salience,
            confidence=0.9,  # the wild heard you say it
            what=what,
            claimed_space=claimed,
            objective=Objective(
                "fetch",
                {
                    "item": item_name,
                    "quantity": clamp_int(effect.get("quantity") or 1, 1, 5),
                },
            )
            if item_name
            else None,
        )
        added = self.add_promises([promise])
        spoken = added[0] if added else promise
        # Engine-authoritative cost floor on top of whatever the resolution charged.
        floor = 3 + salience + (5 if item_name else 0)
        messages = [
            message
            for message in [self._apply_cost({"type": "mana", "amount": floor})]
            if message
        ]
        if item_name:
            messages.extend(self._incur_wild_debt())
        if spoken.binding is not None:
            messages.append("The world makes room for your words.")
        else:
            messages.append(
                "Your words drift into the world, too loose yet to bind it."
            )
        return messages

    def _incur_wild_debt(self) -> list[str]:
        """One rolling Wild Debt: a stacking curse, a collector already on its way, and
        a threat-promise in the ledger so the journal knows something is coming. The
        timer is the temporal executor — it settles the promise when it fires."""
        curse_message = self._apply_cost(
            {
                "type": "curse",
                "id": "wild_debt",
                "name": "Wild Debt",
                "description": "The wild expects repayment. Something is already on its way.",
            }
        )
        stacks = (
            self.state.curses["wild_debt"].stacks
            if "wild_debt" in self.state.curses
            else 1
        )
        debt_promise = next(
            (
                promise
                for promise in self.state.promises
                if promise.id == "promise_wild_debt"
            ),
            None,
        )
        if debt_promise is None:
            self.add_promises(
                [
                    WorldPromise(
                        id="promise_wild_debt",
                        kind="threat",
                        subject="wild debt",
                        text="The wild expects repayment. Something is already on its way.",
                        tags=["debt", "collector"],
                        source="spell:wild_debt",
                        source_turn=self.state.turn,
                        origin_zone=(self.state.zone_x, self.state.zone_y),
                        location=self.state.location_label(),
                        salience=3,
                        confidence=0.9,
                    )
                ]
            )
        else:
            # The ledger reopens: a settled debt un-settles when you borrow again.
            debt_promise.status = "unverified"
            debt_promise.source_turn = self.state.turn
        self.state.event_timers.append(
            {
                "turns": self.rng.randint(8, 15),
                "event_type": "summon",
                "name": "debt collector",
                "char": "D",
                "hp": 8 + 2 * stacks,
                "attack": 3 + stacks,
                "faction": "enemy",
                "promise_id": "promise_wild_debt",
            }
        )
        messages = ["The debt is real. Somewhere, something turns toward you."]
        if curse_message:
            messages.insert(0, curse_message)
        return messages

    def shape_points(
        self, effect: dict[str, Any], fallback_x: int, fallback_y: int
    ) -> list[tuple[int, int]]:
        shape = normalize_id(str(effect.get("shape") or effect.get("pattern") or ""))
        origin = (
            self.resolve_target(
                str(effect.get("origin") or effect.get("from") or "player")
            )
            or self.state.player
        )
        target = self.resolve_target(
            str(effect.get("target") or effect.get("to") or "nearest_enemy")
        )
        end_x, end_y = (target.x, target.y) if target else (fallback_x, fallback_y)
        width = clamp_int(effect.get("width"), 0, 3)
        radius = clamp_int(effect.get("radius"), 1, 12)
        points: list[tuple[int, int]] = []

        if shape in {"line", "beam", "path", "corridor", "ray", "bridge"}:
            for lx, ly in bresenham_line(origin.x, origin.y, end_x, end_y)[1:31]:
                points.extend(self.points_in_radius(lx, ly, width))
            return unique_points(points)

        if shape in {"wall", "barrier"}:
            dx = sign(end_x - origin.x)
            dy = sign(end_y - origin.y)
            if dx == 0 and dy == 0:
                dx = 1
            px, py = -dy, dx
            half = (
                clamp_int(effect.get("length"), 1, 12) if "length" in effect else radius
            )
            for step in range(-half, half + 1):
                wx = end_x + px * step
                wy = end_y + py * step
                points.extend(self.points_in_radius(wx, wy, width))
            return unique_points(
                [(px, py) for px, py in points if self.in_bounds(px, py)]
            )

        if shape in {"cone", "fan"}:
            vx = end_x - origin.x
            vy = end_y - origin.y
            if vx == 0 and vy == 0:
                vx, vy = 1, 0
            mag = max(0.001, math.hypot(vx, vy))
            ux, uy = vx / mag, vy / mag
            min_dot = 0.35
            for tx, ty in self.points_in_radius(origin.x, origin.y, radius):
                if tx == origin.x and ty == origin.y:
                    continue
                qx = tx - origin.x
                qy = ty - origin.y
                qmag = max(0.001, math.hypot(qx, qy))
                if (qx / qmag) * ux + (qy / qmag) * uy >= min_dot:
                    points.append((tx, ty))
            return unique_points(points)

        if shape in {"scatter", "spray"}:
            count = clamp_int(effect.get("count") or effect.get("quantity") or 8, 1, 40)
            candidates = self.points_in_radius(fallback_x, fallback_y, radius)
            self.rng.shuffle(candidates)
            return candidates[:count]

        return []

    def _teleport_destination(self, effect: dict[str, Any]) -> tuple[int, int]:
        """Where a teleport lands. A teleport with no coordinate used to clamp to
        (0,0) and dump the caster in the map corner; instead, resolve a real
        destination: explicit x/y if given (including a nested destination object),
        else the player's marked square, else a random visible floor tile. Never the
        accidental origin."""
        if "x" in effect and "y" in effect:
            return (
                clamp_int(effect.get("x"), 0, self.state.width - 1),
                clamp_int(effect.get("y"), 0, self.state.height - 1),
            )
        for key in ("destination", "to", "position", "dest"):
            dest = effect.get(key)
            if isinstance(dest, dict) and "x" in dest and "y" in dest:
                return (
                    clamp_int(dest.get("x"), 0, self.state.width - 1),
                    clamp_int(dest.get("y"), 0, self.state.height - 1),
                )
        marked = self.selected_target_tile()
        if marked is not None:
            return marked
        return self.random_visible_floor()

    def effect_position(self, effect: dict[str, Any]) -> tuple[int, int]:
        if "x" in effect and "y" in effect:
            return (
                clamp_int(effect.get("x"), 0, self.state.width - 1),
                clamp_int(effect.get("y"), 0, self.state.height - 1),
            )
        # The target/center value may be a legacy string or a typed ref ({"kind":"tile"...},
        # {"kind":"entity"...}, {"selector":...}). bind_position resolves all of them.
        raw = effect.get("target")
        if not raw:
            raw = effect.get("center")
        pos = refs.bind_position(self, refs.normalize_ref(raw))
        if pos is not None:
            return pos
        # A bare-tile player mark named only by the placement key has no occupant, so the
        # ref above resolved to nothing; honor the clicked square rather than the player's feet.
        marked = self.selected_target_tile()
        if marked is not None and (
            self.references_selected_target(raw)
            or self.references_selected_target(effect.get("placement"))
        ):
            return marked
        player = self.state.player
        return player.x, player.y

    def entities_in_radius(self, x: int, y: int, radius: int) -> list[Entity]:
        return [
            entity
            for entity in self.state.entities.values()
            if entity.alive and math.hypot(entity.x - x, entity.y - y) <= radius
        ]

    def points_in_radius(self, x: int, y: int, radius: int) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        for ty in range(y - radius, y + radius + 1):
            for tx in range(x - radius, x + radius + 1):
                if self.in_bounds(tx, ty) and math.hypot(tx - x, ty - y) <= radius:
                    points.append((tx, ty))
        return points

    def push_entity(self, entity: Entity, dx: int, dy: int, distance: int) -> int:
        if dx == 0 and dy == 0:
            return 0
        moved = 0
        from_x, from_y = entity.x, entity.y
        for _ in range(distance):
            tx = entity.x + dx
            ty = entity.y + dy
            if not self.can_occupy(tx, ty):
                break
            entity.x = tx
            entity.y = ty
            moved += 1
            self._apply_tile_entry(entity)
            if entity.hp <= 0:
                break
        if moved:
            entity.details["last_move_delta"] = [entity.x - from_x, entity.y - from_y]
        return moved

    def _conjure_item(self, effect: dict[str, Any]) -> list[str]:
        template = item_template(str(effect.get("template") or "generic_object"))
        count = clamp_int(
            effect.get("count", effect.get("quantity", 1)), 1, template.max_quantity
        )
        name = sanitize_name(
            str(effect.get("name") or template.item_type), template.item_type
        )
        material = sanitize_name(
            str(effect.get("material") or template.material), template.material, 24
        )
        tags = set(template.tags)
        tags.update(
            normalize_id(str(tag))
            for tag in coerce_list(effect.get("tags"))
            if str(tag).strip()
        )
        x, y = self.resolve_placement(effect, prefer_unblocked=False)
        self.spawn_item(
            name,
            sanitize_char(str(effect.get("char") or template.char), template.char),
            x,
            y,
            str(effect.get("item_type") or template.item_type),
            count,
            material=material,
            tags=tags,
        )
        return [f"{name} appears."]

    def _conjure_creature(self, effect: dict[str, Any]) -> list[str]:
        template = creature_template(
            str(effect.get("template") or effect.get("creature_type") or "small_beast")
        )
        count = clamp_int(
            effect.get("count") or effect.get("quantity") or 1, 1, template.max_count
        )
        name = sanitize_name(
            str(effect.get("name") or template.id.replace("_", " ")),
            template.id.replace("_", " "),
        )
        faction = normalize_faction(
            effect.get("faction"), default="ally", neutral_is_ally=True
        )
        char = sanitize_char(str(effect.get("char") or template.char), template.char)
        tags = set(template.tags)
        tags.update(
            normalize_id(str(tag))
            for tag in coerce_list(effect.get("tags"))
            if str(tag).strip()
        )
        resistances = dict(template.resistances)
        resistances.update(normalize_numeric_map(effect.get("resistances"), 0, 95))
        weaknesses = dict(template.weaknesses)
        weaknesses.update(normalize_numeric_map(effect.get("weaknesses"), 0, 200))
        auras = self._normalize_auras(effect.get("aura") or effect.get("auras"))
        spawned = 0
        for index in range(count):
            x, y = self.resolve_placement(effect, prefer_unblocked=True, attempt=index)
            if not self.can_occupy(x, y):
                continue
            self.spawn_actor(
                name,
                char,
                x,
                y,
                clamp_int(effect.get("hp", template.hp), 1, 30),
                clamp_int(effect.get("attack", template.attack), 0, 12),
                clamp_int(effect.get("defense", template.defense), 0, 12),
                faction,
                None if faction in {"ally", "player"} else template.ai,
                tags=tags,
                resistances=resistances,
                weaknesses=weaknesses,
                auras=auras,
            )
            spawned += 1
        if spawned == 0:
            return [f"{name} tries to arrive, but finds no room."]
        return [
            f"{spawned} {name}{'' if spawned == 1 else 's'} {'arrives' if spawned == 1 else 'arrive'}."
        ]

    def resolve_placement(
        self, effect: dict[str, Any], prefer_unblocked: bool, attempt: int = 0
    ) -> tuple[int, int]:
        placement = normalize_id(str(effect.get("placement") or "near_target"))
        if "x" in effect and "y" in effect:
            x = clamp_int(effect.get("x"), 0, self.state.width - 1)
            y = clamp_int(effect.get("y"), 0, self.state.height - 1)
            if not prefer_unblocked or self.can_occupy(x, y):
                return x, y
            return self.find_open_tile_near(x, y)

        # A typed tile/room ref as the target anchors placement on that square directly
        # (e.g. "conjure a wall at {kind: tile, x, y}"). Entity/selector/legacy-string
        # targets fall through to the position logic below so a live occupant's current
        # tile (not a stale coordinate) is used.
        target_ref = refs.normalize_ref(effect.get("target"))
        if target_ref.kind in {"tile", "room"}:
            anchored = refs.bind_position(self, target_ref)
            if anchored is not None:
                ax, ay = anchored
                if not prefer_unblocked or self.can_occupy(ax, ay):
                    return ax, ay
                return self.find_open_tile_near(ax, ay)

        # An explicit player mark on an empty square: resolve_target finds no occupant,
        # so anchor placement on the clicked tile directly (e.g. "conjure a wall there").
        # A live occupant falls through to the normal target path so its current
        # position (not the stale mark) is used.
        marked = self.selected_target_tile()
        if (
            marked is not None
            and self.selected_target_entity() is None
            and (
                self.references_selected_target(placement)
                or self.references_selected_target(effect.get("target"))
            )
        ):
            mx, my = marked
            if not prefer_unblocked or self.can_occupy(mx, my):
                return mx, my
            return self.find_open_tile_near(mx, my)

        target = self.resolve_target(effect.get("target") or "nearest_enemy")
        player = self.state.player
        anchor = target if target is not None else player
        if placement == "target_tile":
            return (
                (anchor.x, anchor.y)
                if not prefer_unblocked
                else self.find_open_tile_near(anchor.x, anchor.y)
            )
        if placement == "near_player":
            return self.find_open_tile_near(player.x, player.y)
        if placement == "visible_floor":
            return self.random_visible_floor()
        if placement == "near_walls":
            near_wall = self.find_open_tile_near_wall(anchor.x, anchor.y, attempt)
            return (
                near_wall
                if near_wall is not None
                else self.find_open_tile_near(anchor.x, anchor.y)
            )
        return self.find_open_tile_near(anchor.x, anchor.y)

    def random_visible_floor(self) -> tuple[int, int]:
        candidates: list[tuple[int, int]] = []
        for key in self.state.visible:
            x, y = parse_tile_key(key)
            if self.can_occupy(x, y):
                candidates.append((x, y))
        if candidates:
            return self.rng.choice(candidates)
        player = self.state.player
        return self.find_open_tile_near(player.x, player.y)

    def find_open_tile_near_wall(
        self, x: int, y: int, attempt: int = 0
    ) -> tuple[int, int] | None:
        candidates: list[tuple[int, int]] = []
        for radius in range(1, 10):
            for ty in range(y - radius, y + radius + 1):
                for tx in range(x - radius, x + radius + 1):
                    if not self.can_occupy(tx, ty):
                        continue
                    if any(
                        self.tile_at(tx + dx, ty + dy) == WALL
                        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]
                    ):
                        candidates.append((tx, ty))
            if candidates:
                self.rng.shuffle(candidates)
                return candidates[attempt % len(candidates)]
        return None

    def find_open_tile_near(self, x: int, y: int) -> tuple[int, int]:
        for radius in range(0, 10):
            points: list[tuple[int, int]] = []
            for ty in range(y - radius, y + radius + 1):
                for tx in range(x - radius, x + radius + 1):
                    points.append((tx, ty))
            self.rng.shuffle(points)
            for tx, ty in points:
                if self.can_occupy(tx, ty):
                    return tx, ty
        return self.state.player.x, self.state.player.y
