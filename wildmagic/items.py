from __future__ import annotations

from typing import Any

from .equipment import equipment_slot_for_item
from .game_data import DEFAULT_ITEM_USE_SPEC, ITEM_USE_SPECS
from .models import MIST, Entity
from .operations import StateDelta
from .normalize import (
    clamp_int,
    coerce_list,
    normalize_id,
    optional_duration,
    status_duration,
)


class _ItemsMixin:
    """Item/inventory methods mixed into GameEngine."""

    def spawn_item(
        self,
        name: str,
        char: str,
        x: int,
        y: int,
        item_type: str,
        quantity: int = 1,
        material: str | None = None,
        tags: set[str] | None = None,
    ) -> Entity:
        entity = Entity(
            id=self.next_entity_id("item"),
            name=name,
            kind="item",
            x=x,
            y=y,
            char=char,
            item_type=item_type,
            material=material,
            quantity=quantity,
            blocks=False,
            tags=set(tags or ()),
        )
        self.state.entities[entity.id] = entity
        if self._delta_capture:
            self.record_delta(
                StateDelta(
                    op="create_entity",
                    target=entity.id,
                    summary=f"{name} appeared at {x},{y}",
                    details={
                        "kind": "item",
                        "name": name,
                        "item_type": item_type,
                        "x": x,
                        "y": y,
                    },
                )
            )
        return entity

    def use_item(self, item_name: str) -> bool:
        if self.state.game_over:
            return False
        matched = self.find_inventory_item(item_name)
        if matched is None or self.state.inventory.get(matched, 0) < 1:
            self.state.add_message(f"You don't have any {item_name.strip().lower()}.")
            return False
        spec = ITEM_USE_SPECS.get(normalize_id(matched), DEFAULT_ITEM_USE_SPEC)
        consumed = self._apply_item_use_spec(matched, spec)
        if consumed:
            self.consume_inventory_item(matched, 1)
            self.state.stats.items_used += 1
            self.finish_player_turn()
        return consumed

    def drop_item(self, item_name: str) -> bool:
        if self.state.game_over:
            return False
        matched = self.find_inventory_item(item_name)
        if matched is None or self.state.inventory.get(matched, 0) < 1:
            self.state.add_message(f"You don't have any {item_name.strip().lower()}.")
            return False
        self.consume_inventory_item(matched, 1)
        player = self.state.player
        self.spawn_item(matched, "?", player.x, player.y, item_type=matched)
        self.state.add_message(f"You drop {matched}.")
        self.finish_player_turn()
        return True

    def find_inventory_item(self, item_name: str) -> str | None:
        return self.find_item_in(self.state.inventory, item_name)

    def find_item_in(self, container: dict[str, int], item_name: str) -> str | None:
        """Fuzzy name lookup against any item-quantity dict (player inventory, NPC
        wares, ...) -- the same dict shape, so the same matching rules apply."""
        wanted = normalize_id(item_name)
        for key in container:
            if key.lower() == item_name.strip().lower() or normalize_id(key) == wanted:
                return key
        return None

    def consume_inventory_item(
        self, item_name: str, amount: int, container: dict[str, int] | None = None
    ) -> int:
        """Remove up to `amount` of `item_name` from `container` (defaults to the
        player's inventory), auto-deleting the entry once it reaches zero. Works
        identically on `state.inventory` and any `NPCProfile.wares` dict -- both are
        plain item-name -> quantity maps, so trades reuse this without special-casing."""
        target = self.state.inventory if container is None else container
        current = target.get(item_name, 0)
        spent = min(current, max(0, amount))
        remaining = current - spent
        if remaining:
            target[item_name] = remaining
        else:
            target.pop(item_name, None)
        return spent

    def add_inventory_item(
        self, container: dict[str, int], item_name: str, amount: int
    ) -> None:
        """The symmetric counterpart to `consume_inventory_item` -- stacks `amount`
        of `item_name` onto an existing entry (matched fuzzily, so "Gold" and "gold"
        accumulate together) or creates a new one."""
        if amount <= 0:
            return
        existing = self.find_item_in(container, item_name)
        key = existing if existing is not None else item_name
        container[key] = container.get(key, 0) + amount

    def _apply_item_use_spec(self, item_name: str, spec: dict[str, Any]) -> bool:
        if "choices" in spec:
            choices = [
                choice
                for choice in coerce_list(spec.get("choices"))
                if isinstance(choice, dict)
            ]
            if choices:
                spec = self.rng.choice(choices)
        context: dict[str, Any] = {"item": item_name.replace("_", " ")}
        target_clause = ""
        for effect in coerce_list(spec.get("effects")):
            if not isinstance(effect, dict):
                continue
            success, updates = self._apply_item_effect(effect)
            context.update(updates)
            if "target" in updates and "amount" in updates and "damage_type" in updates:
                target_clause = f"{updates['target']} takes {updates['amount']} {updates['damage_type']}."
            if not success and effect.get("required"):
                self.state.add_message(str(spec.get("failure") or "Nothing happens."))
                return False
        context["target_clause"] = (
            target_clause or "No enemy is close enough to be caught in it."
        )
        self.state.add_message(
            str(spec.get("message") or "You use the {item}.").format(**context)
        )
        return True

    def _apply_item_effect(self, effect: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        player = self.state.player
        kind = normalize_id(str(effect.get("kind") or ""))
        amount = self._roll_item_amount(effect)
        if kind == "inert":
            return False, {}
        if kind == "restore_mana":
            gained = min(amount, player.max_mana - player.mana)
            player.mana += gained
            return True, {"amount": gained, "mana": gained}
        if kind == "heal":
            healed = self.heal_entity(player, amount)
            return True, {"amount": healed}
        if kind == "status":
            status = normalize_id(str(effect.get("status") or "marked"))
            player.statuses[status] = max(
                status_duration(player.statuses.get(status)),
                clamp_int(effect.get("duration"), 1, 999),
            )
            return True, {"status": status, "duration": player.statuses[status]}
        if kind == "resistance":
            damage_type = normalize_id(str(effect.get("damage_type") or "physical"))
            player.resistances[damage_type] = clamp_int(
                player.resistances.get(damage_type, 0) + amount, 0, 95
            )
            return True, {"damage_type": damage_type, "amount": amount}
        if kind == "create_tiles":
            tile = str(effect.get("tile") or MIST)
            for tx, ty in self.points_in_radius(
                player.x, player.y, clamp_int(effect.get("radius"), 0, 6)
            ):
                self.set_tile(tx, ty, tile, optional_duration(effect.get("duration")))
            return True, {"tile": tile}
        if kind == "teleport_explored":
            candidates = [
                (x, y)
                for x, y in (
                    (
                        self.rng.randint(0, self.state.width - 1),
                        self.rng.randint(0, self.state.height - 1),
                    )
                    for _ in range(40)
                )
                if self.can_occupy(x, y) and self.is_explored(x, y)
            ]
            if not candidates:
                return False, {}
            x, y = self.rng.choice(candidates)
            self.teleport_entity(player, x, y)
            return True, {"x": x, "y": y}
        if kind in {"damage_nearest", "status_nearest"}:
            target = self.nearest_enemy(
                max_distance=clamp_int(effect.get("range"), 1, 99)
            )
            if not target:
                return False, {}
            if kind == "damage_nearest":
                damage_type = normalize_id(str(effect.get("damage_type") or "physical"))
                actual = self.damage_entity(
                    target, amount, damage_type, source=self.state.player
                )
                return True, {
                    "target": target.name,
                    "amount": actual,
                    "damage_type": damage_type,
                }
            status = normalize_id(str(effect.get("status") or "poisoned"))
            target.statuses[status] = max(
                status_duration(target.statuses.get(status)),
                clamp_int(effect.get("duration"), 1, 999),
            )
            return True, {"target": target.name, "status": status}
        return True, {}

    def _roll_item_amount(self, effect: dict[str, Any]) -> int:
        if "amount_min" in effect or "amount_max" in effect:
            return self.rng.randint(
                clamp_int(effect.get("amount_min"), 0, 99),
                clamp_int(effect.get("amount_max"), 0, 99),
            )
        return clamp_int(effect.get("amount"), 0, 99)

    _EQUIPMENT_SLOT_ALIASES = {
        "weapon": "weapon",
        "wielded": "weapon",
        "hand": "weapon",
        "sword": "weapon",
        "blade": "weapon",
        "armor": "armor",
        "armour": "armor",
        "body": "armor",
        "vest": "armor",
        "shield": "armor",
        "charm": "charm",
        "trinket": "charm",
        "amulet": "charm",
        "ring": "charm",
        "head": "head",
        "hat": "head",
        "helmet": "head",
        "cowl": "head",
        "crown": "head",
        "hood": "head",
        "circlet": "head",
        "cap": "head",
        "mask": "head",
        "helm": "head",
        "chest": "chest",
        "cloak": "chest",
        "robe": "chest",
        "tunic": "chest",
        "shirt": "chest",
        "cape": "chest",
        "legs": "legs",
        "trousers": "legs",
        "pants": "legs",
        "leggings": "legs",
        "breeches": "legs",
        "feet": "feet",
        "boots": "feet",
        "shoes": "feet",
        "hands": "hands",
        "gloves": "hands",
        "gauntlets": "hands",
    }

    def equip_item(self, item_name: str) -> bool:
        if self.state.game_over:
            return False
        matched = self.find_inventory_item(item_name)
        if matched is None or self.state.inventory.get(matched, 0) < 1:
            self.state.add_message(f"You don't have any {item_name.strip().lower()}.")
            return False
        slot = equipment_slot_for_item(matched)
        if not slot:
            self.state.add_message(
                f"The {matched} isn't something you can wear or wield."
            )
            return False
        player = self.state.player
        previous = player.equipment.get(slot)
        self.consume_inventory_item(matched, 1)
        player.equipment[slot] = matched
        if previous:
            self.state.inventory[previous] = self.state.inventory.get(previous, 0) + 1
            self.state.add_message(f"You stow the {previous} and equip the {matched}.")
        else:
            self.state.add_message(f"You equip the {matched}.")
        self.finish_player_turn()
        return True

    def unequip_item(self, slot_name: str) -> bool:
        if self.state.game_over:
            return False
        player = self.state.player
        slot = self._EQUIPMENT_SLOT_ALIASES.get(normalize_id(slot_name))
        if slot is None:
            matched = self.find_inventory_item(slot_name) or slot_name
            slot = next(
                (
                    s
                    for s, item in player.equipment.items()
                    if item and normalize_id(item) == normalize_id(matched)
                ),
                None,
            )
        if slot is None:
            slot = next(
                (
                    s
                    for s, item in player.equipment.items()
                    if item and normalize_id(slot_name) in normalize_id(item)
                ),
                None,
            )
        if slot is None or slot not in player.equipment:
            self.state.add_message("That isn't something you have equipped.")
            return False
        current = player.equipment.get(slot)
        if not current:
            self.state.add_message(f"You have nothing equipped in your {slot} slot.")
            return False
        player.equipment[slot] = None
        self.state.inventory[current] = self.state.inventory.get(current, 0) + 1
        self.state.add_message(f"You unequip the {current}.")
        self.finish_player_turn()
        return True

    def _equipped_slot_by_item(self, name: str) -> str | None:
        """Find the equipment slot holding an item matching `name` (exact normalized first,
        then a substring fall-back), or None. Mirrors how unequip resolves an item to a slot."""
        player = self.state.player
        wanted = normalize_id(name)
        if not wanted:
            return None
        for slot, item in player.equipment.items():
            if item and normalize_id(item) == wanted:
                return slot
        for slot, item in player.equipment.items():
            if item and wanted in normalize_id(item):
                return slot
        return None

    def set_focus(self, target: str) -> bool:
        """Mark an already-equipped item as the spell focus. `target` may name a slot
        (via the slot aliases) or an equipped item. A focus is a mark on existing gear, so
        nothing is equipped/unequipped here. v1 carries a single focus, so a new mark replaces
        the old; `Entity.focus_slots` is a list to leave multi-focus a later policy change."""
        if self.state.game_over:
            return False
        player = self.state.player
        arg = (target or "").strip()
        if not arg:
            self.state.add_message("Focus through what? Name an equipped item or slot.")
            return False
        alias = self._EQUIPMENT_SLOT_ALIASES.get(normalize_id(arg))
        slot = alias if alias is not None else self._equipped_slot_by_item(arg)
        if slot is None:
            self.state.add_message(
                f"You aren't wearing or wielding any '{arg}' to channel through."
            )
            return False
        item = player.equipment.get(slot)
        if not item:
            self.state.add_message(
                f"You have nothing equipped in your {slot} slot to channel through."
            )
            return False
        if player.focus_slots == [slot]:
            self.state.add_message(f"The {item} is already your spell focus.")
            return False
        player.focus_slots = [slot]
        self.state.add_message(f"You attune to the {item} as your spell focus.")
        self.finish_player_turn()
        return True

    def clear_focus(self, target: str | None = None) -> bool:
        """Release a spell focus. With no target, release whatever is marked; with a target,
        release only that slot/item if it is currently the focus."""
        if self.state.game_over:
            return False
        player = self.state.player
        if not player.focus_slots:
            self.state.add_message("You have no spell focus to release.")
            return False
        arg = (target or "").strip()
        if arg:
            alias = self._EQUIPMENT_SLOT_ALIASES.get(normalize_id(arg))
            slot = alias if alias is not None else self._equipped_slot_by_item(arg)
            if slot is None or slot not in player.focus_slots:
                self.state.add_message(f"'{arg}' is not your spell focus.")
                return False
            player.focus_slots = [s for s in player.focus_slots if s != slot]
        else:
            player.focus_slots = []
        self.state.add_message("You let your spell focus go quiet.")
        self.finish_player_turn()
        return True

    def pick_up_items_at_player(self) -> None:
        player = self.state.player
        for entity in list(self.entities_at(player.x, player.y)):
            if entity.kind != "item":
                continue
            item_type = entity.item_type or entity.name
            self.state.inventory[item_type] = (
                self.state.inventory.get(item_type, 0) + entity.quantity
            )
            # Preserve the item's flavor before the Entity (and its description) is gone, so a
            # picked-up item can still be a meaningful spell focus. Keyed by the inventory key;
            # a prior Investigate description outranks this and is kept (see set_item_lore).
            if entity.description:
                self.set_item_lore(
                    item_type, entity.name, entity.description, source="description"
                )
            self.state.add_message(f"You pick up {entity.name}.")
            self.state.stats.items_collected += 1
            del self.state.entities[entity.id]
