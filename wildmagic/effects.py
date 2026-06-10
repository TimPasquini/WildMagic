from __future__ import annotations

import copy
import math
from typing import Any

from .geometry import bresenham_line, sign, unique_points
from .models import (
    BLOCKING_TILES,
    FLOOR,
    MECHANICAL_STATUSES,
    TILE_NAMES,
    WALL,
    Curse,
    Entity,
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
    tile_from_name,
)
from .spell_contract import STATUS_FLAVOR_ALIASES, validate_resolution
from .templates import creature_template, item_template


class _EffectsMixin:
    """Effect/cost application and placement helpers extracted from GameEngine."""

    def apply_wild_magic_resolution(self, resolution: dict[str, Any]) -> WildMagicOutcome:
        messages: list[str] = []
        if self.state.game_over:
            return WildMagicOutcome(False, False, ["The dead do not cast."])

        validation_error = validate_resolution(resolution)
        if validation_error:
            message = f"Wild magic failed validation: {validation_error}"
            self.state.add_message(message)
            return WildMagicOutcome(False, True, [message])

        accepted = bool(resolution.get("accepted", True))
        outcome_text = str(resolution.get("outcome_text") or resolution.get("outcome") or resolution.get("message") or "").strip()
        if not accepted:
            reason = str(resolution.get("rejected_reason") or "The spell is too vast to fit through you.")
            self.state.add_message(reason)
            self.state.stats.spells_failed += 1
            self.finish_player_turn()
            return WildMagicOutcome(True, False, [reason])

        snapshot = copy.deepcopy(self.state)
        try:
            if outcome_text:
                self.state.add_message(outcome_text)
                messages.append(outcome_text)

            for message in self._fire_triggers("on_next_spell", {"target": self.state.player, "source": self.state.player}):
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

            self.state.stats.spells_cast += 1
            self.finish_player_turn()
            state_errors = self.validate_state()
            if state_errors:
                self.state = snapshot
                message = f"Wild magic failed state validation: {state_errors[0]}"
                self.state.add_message(message)
                return WildMagicOutcome(False, True, [message])
            return WildMagicOutcome(True, False, messages)
        except Exception as exc:
            self.state = snapshot
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
            player.mana = max(0, player.mana - amount)
            return f"Cost: {amount} mana."
        if cost_type in {"health", "hp"}:
            amount = clamp_int(cost.get("amount"), 1, 99)
            self.state.add_message(f"Cost: {amount} health.", is_danger=True)
            self.damage_entity(player, amount, "blood")
            return None
        if cost_type == "max_health":
            amount = clamp_int(cost.get("amount"), 0, 10)
            player.max_hp = max(1, player.max_hp - amount)
            player.hp = min(player.hp, player.max_hp)
            return f"Cost: {amount} maximum health."
        if cost_type == "max_mana":
            amount = clamp_int(cost.get("amount"), 0, 10)
            player.max_mana = max(0, player.max_mana - amount)
            player.mana = min(player.mana, player.max_mana)
            return f"Cost: {amount} maximum mana."
        if cost_type == "item":
            item = str(cost.get("item") or cost.get("item_name") or cost.get("id") or "").strip()
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
            curse_id = str(cost.get("id") or cost.get("name") or "nameless_curse").lower().replace(" ", "_")
            name = str(cost.get("name") or curse_id.replace("_", " ").title())
            description = str(cost.get("description") or "Reality now remembers you incorrectly.")
            if curse_id in self.state.curses:
                self.state.curses[curse_id].stacks += 1
            else:
                self.state.curses[curse_id] = Curse(curse_id, name, description)
            self.state.stats.curses_gained += 1
            return f"Curse gained: {name}."
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
                if curse_id in self.state.curses:
                    self.state.curses[curse_id].stacks += 1
                else:
                    self.state.curses[curse_id] = Curse(
                        curse_id,
                        name,
                        f"Wild magic leaves you with an uncanny condition: {name}.",
                    )
                return f"Cost became a curse: {name}."
            dur_val3: int | str = "permanent" if duration == "permanent" else clamp_int(duration, 1, 999)
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
        effect_type = str(effect.get("type", "")).lower()
        if effect_type == "damage":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return ["The spell claws at empty air."]
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            damage_type = str(effect.get("damage_type") or "arcane")
            actual = self.calculate_actual_damage(target, amount, damage_type)
            is_player_dmg = (target.id == self.state.player_id and actual > 0)
            self.state.add_message(f"{target.name} {self._verb(target, 'take', 'takes')} {actual} {damage_type} damage.", is_danger=is_player_dmg)
            self.damage_entity(target, amount, damage_type, source=self.state.player)
            return []
        if effect_type == "area_damage":
            x, y = self.effect_position(effect)
            radius = clamp_int(effect.get("radius"), 0, 99) if effect.get("radius") is not None else 3
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
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
                actual = self.calculate_actual_damage(entity, amount, damage_type)
                hit.append(f"{entity.name} {self._verb(entity, 'take', 'takes')} {actual} {damage_type}")
                actuals.append(entity)
                if entity.id == self.state.player_id and actual > 0:
                    is_player_dmg = True
            if not hit:
                return ["The blast spends itself on empty stone."]
            self.state.add_message(f"Area spell hits {len(hit)} target(s): {', '.join(hit)}.", is_danger=is_player_dmg)
            for entity in actuals:
                self.damage_entity(entity, amount, damage_type, source=self.state.player)
            return []
        if effect_type == "area_status":
            x, y = self.effect_position(effect)
            radius = clamp_int(effect.get("radius"), 0, 99) if effect.get("radius") is not None else 15
            status = normalize_id(str(effect.get("status") or "strange"))
            display_name = str(effect.get("display_name") or effect.get("name") or "").strip() or status.replace("_", " ")
            expiry_text = str(effect.get("expiry_text") or effect.get("wears_off") or "").strip()
            duration = effect.get("duration", 3)
            affects = normalize_id(str(effect.get("affects") or "enemies"))
            include_player = bool(effect.get("include_player", False))
            if status not in MECHANICAL_STATUSES:
                canonical = STATUS_FLAVOR_ALIASES.get(status)
                if not canonical:
                    return []
                status = canonical
            affected: list[str] = []
            dur_val2: int | str = "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
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
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            actual = self.heal_entity(target, amount)
            if actual == 0:
                if target.id == self.state.player_id:
                    return ["Your wounds are already mended."]
                return [f"{target.name} {self._verb(target, 'are', 'is')} already whole."]
            return [f"{target.name} {self._verb(target, 'heal', 'heals')} {actual} HP."]
        if effect_type == "restore_mana":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            amount = clamp_int(effect.get("amount"), 1, 999) if effect.get("amount") is not None else 5
            before = target.mana
            target.mana = min(target.max_mana, target.mana + amount)
            gained = target.mana - before
            return [f"{target.name} {self._verb(target, 'recover', 'recovers')} {gained} mana."]
        if effect_type == "teleport":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            x = clamp_int(effect.get("x"), 0, self.state.width - 1)
            y = clamp_int(effect.get("y"), 0, self.state.height - 1)
            if self.teleport_entity(target, x, y):
                return [f"{target.name} {self._verb(target, 'snap', 'snaps')} to another tile."]
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
            origin = self.resolve_target(str(effect.get("origin") or "player")) or self.state.player
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
                return [f"{len(moved_names)} target(s) are moved {moved_total} tile(s) total."]
            return ["The force finds no room to move anyone."]
        if effect_type in {"create_tile", "set_tile", "create_tiles"}:
            x, y = self.effect_position(effect)
            tile_name = str(effect.get("tile") or FLOOR).lower()
            tile = tile_from_name(tile_name)
            duration = optional_duration(effect.get("duration"))
            tags = set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            changed = 0
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
                    spec_tags = set(normalize_id(str(tag)) for tag in coerce_list(spec.get("tags", list(tags))) if str(tag).strip())
                    if self.set_tile(tx, ty, spec_tile, spec_duration, spec_tags):
                        changed += 1
                if first_spec_tile is not None:
                    tile = first_spec_tile
            else:
                radius = clamp_int(effect.get("radius"), 0, 99)
                hollow = bool(effect.get("hollow") or effect.get("ring") or effect.get("perimeter"))
                inner_radius = max(0, radius - 1) if hollow else -1
                shape = normalize_id(str(effect.get("shape") or effect.get("pattern") or ""))
                if shape in {"line", "beam", "path", "corridor", "ray", "bridge", "wall", "barrier", "cone", "fan", "scatter", "spray"}:
                    for tx, ty in self.shape_points(effect, x, y)[:200]:
                        if self.set_tile(tx, ty, tile, duration, tags):
                            changed += 1
                else:
                    for tx, ty in self.points_in_radius(x, y, radius)[:200]:
                        if hollow and math.hypot(tx - x, ty - y) <= inner_radius:
                            continue
                        if self.set_tile(tx, ty, tile, duration, tags):
                            changed += 1
            return [f"Terrain changes to {TILE_NAMES.get(tile, 'strange')} on {changed} tile(s)."]
        if effect_type == "add_status":
            target_str = normalize_id(str(effect.get("target") or "nearest_enemy"))
            status = normalize_id(str(effect.get("status") or "strange"))
            display_name = str(effect.get("display_name") or effect.get("name") or "").strip() or status.replace("_", " ")
            expiry_text = str(effect.get("expiry_text") or effect.get("wears_off") or "").strip()
            duration = effect.get("duration", 3)
            dur_val: int | str = "permanent" if duration == "permanent" else clamp_int(duration, 1, 99)
            if status not in MECHANICAL_STATUSES:
                canonical = STATUS_FLAVOR_ALIASES.get(status)
                if not canonical:
                    return []
                status = canonical
            group_targets = self.resolve_target_group(target_str)
            if group_targets:
                for ent in group_targets:
                    ent.statuses[status] = dur_val
                    if display_name != status.replace("_", " "):
                        ent.status_display[status] = display_name
                    if expiry_text:
                        ent.status_expiry_text[status] = expiry_text
                return [f"{display_name.title()} spreads to {len(group_targets)} target(s)."]
            target = self.resolve_target(target_str)
            if not target or target.kind == "item":
                return []
            target.statuses[status] = dur_val
            if display_name != status.replace("_", " "):
                target.status_display[status] = display_name
            if expiry_text:
                target.status_expiry_text[status] = expiry_text
            return [f"{target.name} {self._verb(target, 'are', 'is')} now {display_name}."]
        if effect_type == "remove_status":
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            status = normalize_id(str(effect.get("status") or ""))
            if status:
                target.statuses.pop(status, None)
                return [f"{target.name} {self._verb(target, 'are', 'is')} no longer {status.replace('_', ' ')}."]
            target.statuses.clear()
            if target.id == self.state.player_id:
                return ["All statuses leave you."]
            return [f"All statuses leave {target.name}."]
        if effect_type == "summon":
            name = str(effect.get("name") or effect.get("creature") or effect.get("creature_type") or "borrowed thing")
            faction = normalize_faction(effect.get("faction"), default="ally", neutral_is_ally=True)
            count = clamp_int(effect.get("count") or effect.get("quantity") or 1, 1, 6)
            char = str(effect.get("char") or ("a" if faction == "ally" else "e"))[:1]
            hp = clamp_int(effect.get("hp") or 5, 1, 20)
            attack = clamp_int(effect.get("attack") or 2, 0, 8)
            defense = clamp_int(effect.get("defense") or 0, 0, 8)
            tags = set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            spawned = 0
            for attempt in range(count):
                x, y = self.effect_position(effect) if attempt == 0 else (self.state.player.x, self.state.player.y)
                if not self.can_occupy(x, y):
                    x, y = self.find_open_tile_near(self.state.player.x, self.state.player.y)
                if not self.can_occupy(x, y):
                    continue
                self.spawn_actor(
                    name, char, x, y, hp, attack, defense, faction,
                    "simple" if faction == "enemy" else None,
                    tags=tags,
                    resistances=normalize_numeric_map(effect.get("resistances"), 0, 95),
                    weaknesses=normalize_numeric_map(effect.get("weaknesses"), 0, 200),
                )
                spawned += 1
            if spawned == 0:
                return [f"{name} tries to arrive, but finds no room."]
            return [f"{spawned} {name}{'' if spawned == 1 else 's'} {'arrives' if spawned == 1 else 'arrive'}."]
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
                tags=set(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip()),
            )
            return [f"{name} appears."]
        if effect_type == "conjure_item":
            return self._conjure_item(effect)
        if effect_type == "conjure_creature":
            return self._conjure_creature(effect)
        if effect_type == "transform_item":
            target_type = normalize_id(str(effect.get("target") or "nearest_item"))
            item = str(effect.get("item") or effect.get("item_type") or "").strip()
            new_name = str(effect.get("new_name") or effect.get("new_item_type") or "oddment").strip()
            new_material = str(effect.get("material") or "").strip() or None
            new_tags = [normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip()]

            if not item:
                return []

            if target_type == "inventory":
                current = self.state.inventory.get(item, 0)
                if current > 0:
                    self.state.inventory[item] = current - 1
                    if self.state.inventory[item] <= 0:
                        del self.state.inventory[item]
                    self.state.inventory[new_name] = self.state.inventory.get(new_name, 0) + 1
                    return [f"The {item} in your inventory becomes {new_name}."]
                return [f"You have no {item} to transform."]

            # Find nearest item entity matching the name
            player = self.state.player
            candidates = [
                e for e in self.state.entities.values()
                if e.kind == "item" and e.alive and (item.lower() in e.name.lower() or item.lower() in (e.item_type or "").lower())
            ]
            if not candidates:
                return [f"No {item} found to transform."]
            target = min(candidates, key=lambda e: self.distance(player, e))
            
            target.name = new_name
            target.item_type = new_name
            if new_material:
                target.material = new_material
            if new_tags:
                target.tags.update(new_tags)
            return [f"The {item} on the ground transforms into {new_name}."]

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
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target:
                return []
            if "name" in effect:
                target.name = str(effect["name"])[:40]
            if "char" in effect:
                target.char = str(effect["char"])[:1] or target.char
            if "faction" in effect:
                target.faction = normalize_faction(effect["faction"], default=target.faction)
            if "material" in effect:
                target.material = str(effect["material"])[:32]
            target.max_hp = clamp_int(effect.get("max_hp", target.max_hp), 1, 99)
            target.hp = clamp_int(effect.get("hp", target.hp), 0, target.max_hp)
            target.attack = clamp_int(effect.get("attack", target.attack), 0, 20)
            target.defense = clamp_int(effect.get("defense", target.defense), 0, 20)
            target.tags.update(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
            if target.id == self.state.player_id:
                return ["You are transformed."]
            return [f"{target.name} {self._verb(target, 'are', 'is')} transformed."]
        if effect_type == "change_faction":
            target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
            if not target or target.kind == "item":
                return []
            new_faction = normalize_faction(effect.get("faction"), default="neutral")
            target.faction = new_faction
            target.ai = None if target.faction in {"ally", "player"} else target.ai
            return [f"{target.name} now belongs to {target.faction}."]
        if effect_type in {"add_tag", "remove_tag"}:
            target = self.resolve_target(str(effect.get("target") or "player"))
            tag = normalize_id(str(effect.get("tag") or "strange"))
            if not target:
                return []
            if effect_type == "add_tag":
                target.tags.add(tag)
                return [f"{target.name} {self._verb(target, 'gain', 'gains')} the {tag} tag."]
            target.tags.discard(tag)
            return [f"{target.name} {self._verb(target, 'lose', 'loses')} the {tag} tag."]
        if effect_type in {"add_resistance", "add_weakness"}:
            target = self.resolve_target(str(effect.get("target") or "player"))
            if not target:
                return []
            damage_type = normalize_id(str(effect.get("damage_type") or effect.get("resistance") or "arcane"))
            amount = clamp_int(effect.get("amount"), 1, 95 if effect_type == "add_resistance" else 200)
            table = target.resistances if effect_type == "add_resistance" else target.weaknesses
            table[damage_type] = clamp_int(table.get(damage_type, 0) + amount, 0, 95 if effect_type == "add_resistance" else 200)
            if effect_type == "add_resistance":
                return [f"{target.name} {self._verb(target, 'resist', 'resists')} {damage_type}."]
            return [f"{target.name} {self._verb(target, 'are', 'is')} vulnerable to {damage_type}."]
        if effect_type == "set_flag":
            flag = normalize_id(str(effect.get("flag") or effect.get("id") or "unnamed_flag"))
            self.state.flags[flag] = effect.get("value", True)
            return [f"World flag set: {flag}."]
        if effect_type == "schedule_event":
            event = dict(effect.get("event") if isinstance(effect.get("event"), dict) else effect)
            event.pop("type", None)
            event["turns"] = clamp_int(effect.get("turns", event.get("turns")), 1, 999)
            event["event_type"] = str(effect.get("event_type") or event.get("event_type") or "message")
            self.state.event_timers.append(event)
            return [f"Something has been scheduled in {event['turns']} turn(s)."]
        if effect_type in {"create_trigger", "trigger", "ward"}:
            trigger_name = normalize_trigger_name(str(effect.get("trigger") or effect.get("on") or "on_next_spell"))
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
                "on_next_spell": "Spell chain",
                "on_player_move": "Footstep echo",
            }
            raw_name = str(effect.get("name") or "").strip()
            default_name = _TRIGGER_DEFAULT_NAMES.get(trigger_name, "A waiting spell")
            trigger = {
                "id": self.next_entity_id("trigger"),
                "name": sanitize_name(raw_name or default_name, default_name),
                "trigger": trigger_name,
                "target": effect.get("target", "any"),
                "charges": clamp_int(effect.get("charges"), 1, 9),
                "duration": effect.get("duration", effect.get("turns", 6)),
                "effects": [dict(raw) for raw in effects[:8] if isinstance(raw, dict)],
            }
            if trigger["duration"] != "permanent":
                trigger["expires_turn"] = self.state.turn + clamp_int(trigger["duration"], 1, 999)
            self.state.triggers.append(trigger)
            return [f"{trigger['name']} waits for {trigger_name.replace('_', ' ')}."]
        if effect_type == "add_curse":
            message = self._apply_cost({"type": "curse", **effect})
            return [message] if message else []
        if effect_type == "message":
            text = str(effect.get("text") or "").strip()
            return [text] if text else []
        return []

    def shape_points(self, effect: dict[str, Any], fallback_x: int, fallback_y: int) -> list[tuple[int, int]]:
        shape = normalize_id(str(effect.get("shape") or effect.get("pattern") or ""))
        origin = self.resolve_target(str(effect.get("origin") or effect.get("from") or "player")) or self.state.player
        target = self.resolve_target(str(effect.get("target") or effect.get("to") or "nearest_enemy"))
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
            half = clamp_int(effect.get("length"), 1, 12) if "length" in effect else radius
            for step in range(-half, half + 1):
                wx = end_x + px * step
                wy = end_y + py * step
                points.extend(self.points_in_radius(wx, wy, width))
            return unique_points([(px, py) for px, py in points if self.in_bounds(px, py)])

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

    def effect_position(self, effect: dict[str, Any]) -> tuple[int, int]:
        if "x" in effect and "y" in effect:
            return (
                clamp_int(effect.get("x"), 0, self.state.width - 1),
                clamp_int(effect.get("y"), 0, self.state.height - 1),
            )
        target = self.resolve_target(str(effect.get("target") or effect.get("center") or ""))
        if target:
            return target.x, target.y
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
        return moved

    def _conjure_item(self, effect: dict[str, Any]) -> list[str]:
        template = item_template(str(effect.get("template") or "generic_object"))
        count = clamp_int(effect.get("count", effect.get("quantity", 1)), 1, template.max_quantity)
        name = sanitize_name(str(effect.get("name") or template.item_type), template.item_type)
        material = sanitize_name(str(effect.get("material") or template.material), template.material, 24)
        tags = set(template.tags)
        tags.update(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
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
        template = creature_template(str(effect.get("template") or effect.get("creature_type") or "small_beast"))
        count = clamp_int(effect.get("count") or effect.get("quantity") or 1, 1, template.max_count)
        name = sanitize_name(str(effect.get("name") or template.id.replace("_", " ")), template.id.replace("_", " "))
        faction = normalize_faction(effect.get("faction"), default="ally", neutral_is_ally=True)
        char = sanitize_char(str(effect.get("char") or template.char), template.char)
        tags = set(template.tags)
        tags.update(normalize_id(str(tag)) for tag in coerce_list(effect.get("tags")) if str(tag).strip())
        resistances = dict(template.resistances)
        resistances.update(normalize_numeric_map(effect.get("resistances"), 0, 95))
        weaknesses = dict(template.weaknesses)
        weaknesses.update(normalize_numeric_map(effect.get("weaknesses"), 0, 200))
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
            )
            spawned += 1
        if spawned == 0:
            return [f"{name} tries to arrive, but finds no room."]
        return [f"{spawned} {name}{'' if spawned == 1 else 's'} {'arrives' if spawned == 1 else 'arrive'}."]

    def resolve_placement(self, effect: dict[str, Any], prefer_unblocked: bool, attempt: int = 0) -> tuple[int, int]:
        placement = normalize_id(str(effect.get("placement") or "near_target"))
        if "x" in effect and "y" in effect:
            x = clamp_int(effect.get("x"), 0, self.state.width - 1)
            y = clamp_int(effect.get("y"), 0, self.state.height - 1)
            if not prefer_unblocked or self.can_occupy(x, y):
                return x, y
            return self.find_open_tile_near(x, y)

        target = self.resolve_target(str(effect.get("target") or "nearest_enemy"))
        player = self.state.player
        anchor = target if target is not None else player
        if placement == "target_tile":
            return (anchor.x, anchor.y) if not prefer_unblocked else self.find_open_tile_near(anchor.x, anchor.y)
        if placement == "near_player":
            return self.find_open_tile_near(player.x, player.y)
        if placement == "visible_floor":
            return self.random_visible_floor()
        if placement == "near_walls":
            near_wall = self.find_open_tile_near_wall(anchor.x, anchor.y, attempt)
            return near_wall if near_wall is not None else self.find_open_tile_near(anchor.x, anchor.y)
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

    def find_open_tile_near_wall(self, x: int, y: int, attempt: int = 0) -> tuple[int, int] | None:
        candidates: list[tuple[int, int]] = []
        for radius in range(1, 10):
            for ty in range(y - radius, y + radius + 1):
                for tx in range(x - radius, x + radius + 1):
                    if not self.can_occupy(tx, ty):
                        continue
                    if any(self.tile_at(tx + dx, ty + dy) == WALL for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]):
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


