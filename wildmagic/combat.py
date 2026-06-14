from __future__ import annotations

from collections import deque
from typing import Any

from .game_data import EQUIPMENT_SPECS
from .geometry import sign
from .models import FIRE, FLOOR, POISON_CLOUD, RUBBLE, SLICK_ICE, WALL, WATER, Entity
from .normalize import clamp_int, normalize_id, status_duration
from .semantics import place_anchor


class _CombatMixin:
    """Combat methods mixed into GameEngine."""

    def equipment_bonus(self, entity: Entity, stat: str) -> int:
        total = 0
        for item_name in entity.equipment.values():
            if not item_name:
                continue
            spec = EQUIPMENT_SPECS.get(item_name.strip().lower())
            if spec:
                total += int(spec.get(stat, 0))
        return total

    def effective_attack(self, entity: Entity) -> int:
        return entity.attack + self.equipment_bonus(entity, "attack")

    def effective_defense(self, entity: Entity) -> int:
        return entity.defense + self.equipment_bonus(entity, "defense")

    def calculate_actual_damage(
        self, entity: Entity, amount: int, damage_type: str
    ) -> int:
        if entity.kind == "item" or entity.hp <= 0:
            return 0
        damage_type = normalize_id(damage_type)
        if "marked" in entity.statuses and damage_type not in {"blood"}:
            amount = amount + 2
        if "cursed" in entity.statuses and damage_type not in {"blood"}:
            amount = amount + 1
        if (
            "warded" in entity.statuses
            and damage_type not in {"blood"}
            and self._is_canonical(entity, "warded")
        ):
            amount = max(0, amount - 2)
        return self._modified_damage(entity, amount, damage_type)

    def attack(self, attacker: Entity, defender: Entity) -> None:
        base = max(
            1,
            self.effective_attack(attacker)
            - self.effective_defense(defender)
            + self.rng.randint(0, 2),
        )
        bonus = (
            2
            if ("berserk" in attacker.statuses or "empowered" in attacker.statuses)
            else 0
        )
        # weakened is the mirror of empowered: a maimed/withered limb lands feebler
        # blows. Clamp so a weakened attacker still scratches for at least 1.
        if "weakened" in attacker.statuses:
            bonus -= 2
        amount = max(1, base + bonus)
        actual = self.calculate_actual_damage(defender, amount, "physical")

        # Log combat message only if player is involved or either entity is visible
        if (
            attacker.id == self.state.player_id
            or defender.id == self.state.player_id
            or self.is_visible(attacker.x, attacker.y)
            or self.is_visible(defender.x, defender.y)
        ):
            is_player_dmg = defender.id == self.state.player_id and actual > 0
            self.state.add_message(
                f"{attacker.name} {self._verb(attacker, 'hit', 'hits')} {defender.name} for {actual}.",
                is_danger=is_player_dmg,
            )

        self.damage_entity(defender, amount, "physical", source=attacker)
        if "berserk" in attacker.statuses:
            self.damage_entity(attacker, 1, "blood", source=attacker)
        if defender.hp > 0:
            # Spider webs on hit
            if (
                "spider" in attacker.tags
                and "webbed" not in defender.statuses
                and self.rng.random() < 0.5
            ):
                defender.statuses["webbed"] = 2
                self.state.add_message(
                    f"{defender.name} {self._verb(defender, 'are', 'is')} webbed!"
                )
            # Fungus spreads spores on hit (poisoned)
            if (
                "fungus" in attacker.tags
                and "poisoned" not in defender.statuses
                and self.rng.random() < 0.4
            ):
                defender.statuses["poisoned"] = 3
                self.state.add_message(f"Fungal spores infect {defender.name}!")

    def _is_canonical(self, entity: Entity, status: str) -> bool:
        display = entity.status_display.get(status)
        if not display:
            return True
        if display == status.replace("_", " "):
            return True
        canon_aliases = {
            "frozen": {
                "petrified",
                "stone",
                "crystallized",
                "iced",
                "glaciated",
                "encased",
            },
            "burning": {
                "aflame",
                "alight",
                "on fire",
                "ignited",
                "flaming",
                "ablaze",
                "smoldering",
            },
            "poisoned": {
                "diseased",
                "infected",
                "plagued",
                "venomous",
                "toxic",
                "envenomed",
                "tainted",
            },
            "bleeding": {"lacerated", "wounded", "cut", "hemorrhaging", "bloodied"},
            "warded": {"protected", "shielded", "guarded", "defended"},
        }
        return display.replace(" ", "_") in canon_aliases.get(status, set())

    def damage_entity(
        self,
        entity: Entity,
        amount: int,
        damage_type: str,
        source: Entity | None = None,
    ) -> int:
        if entity.kind == "item" or entity.hp <= 0:
            return 0
        is_player = entity.id == self.state.player_id
        was_taking_damage = self.state._player_taking_damage
        if is_player:
            self.state._player_taking_damage = True
        try:
            actual = self.calculate_actual_damage(entity, amount, damage_type)
            hp_before = entity.hp
            entity.hp -= actual
            if entity.id == self.state.player_id:
                self.state.stats.damage_taken += actual
            elif entity.kind == "actor":
                self.state.stats.damage_dealt += actual
            if actual > 0:
                self._fire_damage_triggers(entity, source, actual, damage_type)
            if entity.hp <= 0:
                # Undead entities have a 30% chance to reform at 1 HP rather than dying.
                if (
                    "undead" in entity.tags
                    and entity.kind == "actor"
                    and entity.id != self.state.player_id
                    and "slain" not in entity.tags
                    and self.rng.random() < 0.3
                ):
                    entity.hp = 1
                    entity.tags.add("slain")
                    self.state.add_message(
                        f"{entity.name} collapses... but begins to stir again!"
                    )
                    return 0
                entity.hp = 0
                entity.blocks = False
                entity.char = "%"
                entity.ai = None
                entity.statuses.clear()
                if entity.id != self.state.player_id:
                    # Write-back: the ground remembers a death. A later divination, an NPC
                    # who lives here, or a spell cast on this spot can weigh it. The loop
                    # that turns mechanical events into future semantic context.
                    slayer = source.name if source is not None else "something unseen"
                    self.record_note(
                        place_anchor(entity.x, entity.y),
                        f"{entity.name} was slain here by {slayer}.",
                        kind="event",
                        source="combat",
                        salience=3,
                        ttl=400,
                    )
                if entity.id == self.state.player_id:
                    self.state.game_over = True
                    self.state.victory = False
                    # The two tones of the game, even in dying: the Empire closes
                    # a file; the wild takes you back.
                    if source is not None and "empire" in source.tags:
                        self.state.death_cause = "empire"
                        self.state.add_message(
                            "You die. The squad re-forms and moves on without comment."
                        )
                        self.state.add_message("Somewhere, a file is stamped CLOSED.")
                    else:
                        self.state.death_cause = "wild"
                        self.state.add_message(
                            "You die. The wild takes its color back, and keeps your echo."
                        )
                elif entity.kind == "npc":
                    # NPCs have no kill stat, loot table, or victory check of their own --
                    # this is the one piece of feedback the whole "you can lose them, and
                    # it matters" premise depends on, so it gets a message of its own.
                    if source is not None:
                        self.state.add_message(
                            f"{entity.name} falls before {source.name}!"
                        )
                    else:
                        self.state.add_message(f"{entity.name} falls.")
                    self._fire_death_triggers(entity, source, hp_before, damage_type)
                else:
                    self.state.add_message(f"{entity.name} dies.")
                    self.state.stats.enemies_killed += 1
                    self._drop_loot(entity)
                    # Slime splits into two smaller ones.
                    if (
                        "slime" in entity.tags
                        and "split" not in entity.tags
                        and entity.max_hp > 2
                    ):
                        self._split_slime(entity)
                    if not self.living_enemies():
                        self.state.victory = True
                        self.state.add_message("For a breath, the floor is yours.")
                    # Death-effect tags.
                    self._on_entity_death(entity)
                    self._fire_death_triggers(entity, source, hp_before, damage_type)
            elif damage_type == "fire":
                if "bleeding" in entity.statuses and self._is_canonical(
                    entity, "bleeding"
                ):
                    entity.statuses.pop("bleeding")
                    entity.hp -= 1
                    wound_subj = (
                        "Your wound is"
                        if entity.id == self.state.player_id
                        else f"{entity.name}'s wound is"
                    )
                    self.state.add_message(
                        f"{wound_subj} cauterized - brutal but effective."
                    )
                else:
                    entity.statuses["burning"] = max(
                        status_duration(entity.statuses.get("burning")), 3
                    )
                if self.tile_at(entity.x, entity.y) == SLICK_ICE:
                    self.set_tile(entity.x, entity.y, WATER, duration=4)
                    self.state.add_message(
                        "The ice melts to water beneath you."
                        if entity.id == self.state.player_id
                        else f"The ice melts away beneath {entity.name}."
                    )
            elif damage_type == "frost":
                if self.tile_at(entity.x, entity.y) == WATER:
                    entity.statuses["frozen"] = max(
                        status_duration(entity.statuses.get("frozen")), 2
                    )
                    self.state.add_message(
                        f"{'You are' if entity.id == self.state.player_id else entity.name + ' is'} frozen solid in the water!"
                    )
                    self.set_tile(entity.x, entity.y, SLICK_ICE, duration=5)
                else:
                    entity.statuses["slowed"] = max(
                        status_duration(entity.statuses.get("slowed")), 2
                    )
            elif damage_type == "lightning":
                if self.tile_at(entity.x, entity.y) == WATER:
                    entity.statuses["stunned"] = max(
                        status_duration(entity.statuses.get("stunned")), 2
                    )
                    self.state.add_message("Lightning courses through the water!")
                    if not self._conducting_lightning:
                        self._conducting_lightning = True
                        try:
                            self._conduct_lightning_through_water(entity)
                        finally:
                            self._conducting_lightning = False
            elif (
                damage_type == "poison"
                and "poisoned" in entity.statuses
                and self._is_canonical(entity, "poisoned")
            ):
                entity.statuses["poisoned"] = min(
                    99, status_duration(entity.statuses.get("poisoned", 0)) + 2
                )
            elif damage_type == "acid":
                if "warded" in entity.statuses and self._is_canonical(entity, "warded"):
                    entity.statuses.pop("warded")
                    name_str = (
                        "your"
                        if entity.id == self.state.player_id
                        else f"{entity.name}'s"
                    )
                    self.state.add_message(f"Acid dissolves {name_str} ward!")
                elif (
                    "stone" in entity.tags
                    or "metal" in entity.tags
                    or "construct" in entity.tags
                ):
                    pass  # Extra damage handled in _modified_damage
                elif self.rng.random() < 0.5:
                    entity.statuses["bleeding"] = max(
                        status_duration(entity.statuses.get("bleeding")), 3
                    )
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    nx, ny = entity.x + dx, entity.y + dy
                    if (
                        self.in_bounds(nx, ny)
                        and self.tile_at(nx, ny) == WALL
                        and self.rng.random() < 0.15
                    ):
                        self.set_tile(nx, ny, RUBBLE)
                        self.state.add_message(
                            "Acid hisses against the stone, eating through the wall."
                        )
                        break
            elif damage_type == "radiant":
                entity.statuses["revealed"] = max(
                    status_duration(entity.statuses.get("revealed")), 4
                )
            elif damage_type == "shadow":
                if "burning" in entity.statuses and self._is_canonical(
                    entity, "burning"
                ):
                    entity.statuses.pop("burning")
                    name_str = (
                        "your"
                        if entity.id == self.state.player_id
                        else f"{entity.name}'s"
                    )
                    self.state.add_message(f"Shadows snuff out {name_str} flames.")
                if self.tile_at(entity.x, entity.y) == FIRE:
                    self.set_tile(entity.x, entity.y, FLOOR)
                    self.state.add_message(
                        "The shadows smother the flames around you."
                        if entity.id == self.state.player_id
                        else f"The shadows smother the flames around {entity.name}."
                    )
            elif damage_type == "force" and source and source.id != entity.id:
                dx = sign(entity.x - source.x)
                dy = sign(entity.y - source.y)
                if dx or dy:
                    moved = self.push_entity(entity, dx, dy, 1)
                    if moved:
                        self.state.add_message(
                            f"{entity.name} {self._verb(entity, 'are', 'is')} knocked back!"
                        )
            return actual
        finally:
            if is_player:
                self.state._player_taking_damage = was_taking_damage

    def _conduct_lightning_through_water(self, origin: Entity) -> None:
        start = (origin.x, origin.y)
        visited = {start}
        queue: deque[tuple[int, int]] = deque([start])
        water_tiles: set[tuple[int, int]] = set()
        while queue and len(water_tiles) < 60:
            cx, cy = queue.popleft()
            water_tiles.add((cx, cy))
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in visited or not self.in_bounds(nx, ny):
                    continue
                visited.add((nx, ny))
                if self.tile_at(nx, ny) == WATER:
                    queue.append((nx, ny))
        for other in list(self.state.entities.values()):
            if (
                other.id == origin.id
                or other.kind not in {"actor", "npc"}
                or other.hp <= 0
            ):
                continue
            if (other.x, other.y) in water_tiles:
                is_player_dmg = (
                    other.id == self.state.player_id
                    and self.calculate_actual_damage(other, 2, "lightning") > 0
                )
                self.state.add_message(
                    f"The current carries the shock to {other.name}!",
                    is_danger=is_player_dmg,
                )
                self.damage_entity(other, 2, "lightning", source=origin)

    def _modified_damage(self, entity: Entity, amount: int, damage_type: str) -> int:
        base = max(0, int(amount))
        if base == 0:
            return 0
        resistance = clamp_int(entity.resistances.get(damage_type), 0, 95)
        weakness = clamp_int(entity.weaknesses.get(damage_type), 0, 200)

        if damage_type == "acid" and any(
            t in entity.tags for t in {"metal", "stone", "construct"}
        ):
            weakness += 50
        elif damage_type == "radiant" and any(
            t in entity.tags for t in {"undead", "shadow", "spirit"}
        ):
            weakness += 50
        elif damage_type == "shadow" and any(
            t in entity.tags for t in {"radiant", "holy", "celestial"}
        ):
            weakness += 50
        elif damage_type == "fire" and any(
            t in entity.tags for t in {"plant", "wood", "flammable", "web"}
        ):
            weakness += 50

        multiplier = max(0.05, (100 - resistance + weakness) / 100)
        actual = int(round(base * multiplier))
        return max(1, actual)

    def heal_entity(self, entity: Entity, amount: int) -> int:
        if entity.kind == "item" or entity.hp <= 0:
            return 0
        before = entity.hp
        entity.hp = min(entity.max_hp, entity.hp + max(0, int(amount)))
        actual = entity.hp - before
        if entity.id == self.state.player_id:
            self.state.stats.hp_healed += actual
        return actual

    def _split_slime(self, parent: Entity) -> None:
        split_hp = max(1, parent.max_hp // 2)
        spawned = 0
        for _ in range(2):
            sx, sy = self.find_open_tile_near(parent.x, parent.y)
            if not self.can_occupy(sx, sy):
                continue
            self.spawn_actor(
                f"small {parent.name}",
                parent.char,
                sx,
                sy,
                hp=split_hp,
                attack=max(1, parent.attack - 1),
                defense=0,
                faction=parent.faction,
                tags=parent.tags | {"split"},
                ai=parent.ai or "simple",
            )
            spawned += 1
        if spawned:
            self.state.add_message(
                f"{parent.name} splits into {spawned} smaller slimes!"
            )

    def _drop_loot(self, entity: Entity) -> None:
        tags = entity.tags
        # 40% drop chance; conjured creatures and constructs don't drop loot
        if "conjured" in tags or self.rng.random() > 0.4:
            return
        loot_by_tag = {
            "undead": ("bone shard", "?", "bone"),
            "beast": ("beast claw", "?", "bone"),
            "humanoid": ("stolen coin", "$", "metal"),
            "slime": ("viscous residue", "~", "slime"),
            "construct": ("metal scrap", "/", "metal"),
        }
        drop_name, drop_char, drop_mat = ("arcane residue", "*", "essence")
        for tag, drop_data in loot_by_tag.items():
            if tag in tags:
                drop_name, drop_char, drop_mat = drop_data
                break
        self.spawn_item(
            drop_name,
            drop_char,
            entity.x,
            entity.y,
            item_type=drop_name,
            material=drop_mat,
        )
        self.state.add_message(f"{entity.name} drops {drop_name}.")

    def _on_entity_death(self, entity: Entity) -> None:
        """Fire death-effect tags when an entity dies."""
        if "explode_on_death" in entity.tags or "bomb" in entity.tags:
            radius = 3
            is_player_dmg = False
            for t in self.entities_in_radius(entity.x, entity.y, radius):
                if t.hp > 0 and t.id != entity.id:
                    if (
                        t.id == self.state.player_id
                        and self.calculate_actual_damage(t, 5, "fire") > 0
                    ):
                        is_player_dmg = True
                    self.damage_entity(t, 5, "fire")
            for tx, ty in self.points_in_radius(entity.x, entity.y, radius):
                self.set_tile(tx, ty, FIRE, duration=3)
            self.state.add_message(
                f"{entity.name} explodes in a gout of flame!", is_danger=is_player_dmg
            )
        if (
            "shatter_on_death" in entity.tags
            or "glass" in entity.tags
            and "fragile" in entity.tags
        ):
            is_player_dmg = False
            for t in self.entities_in_radius(entity.x, entity.y, 2):
                if t.hp > 0 and t.id != entity.id:
                    if (
                        t.id == self.state.player_id
                        and self.calculate_actual_damage(t, 3, "physical") > 0
                    ):
                        is_player_dmg = True
                    self.damage_entity(t, 3, "physical")
            self.state.add_message(
                f"{entity.name} shatters in a shower of shards!",
                is_danger=is_player_dmg,
            )
        if "poison_cloud_on_death" in entity.tags or "plague_on_death" in entity.tags:
            for tx, ty in self.points_in_radius(entity.x, entity.y, 3):
                self.set_tile(tx, ty, POISON_CLOUD, duration=6)
            self.state.add_message(f"{entity.name} dissolves into toxic vapor!")
        if "freeze_on_death" in entity.tags or "ice_burst_on_death" in entity.tags:
            for t in self.entities_in_radius(entity.x, entity.y, 2):
                if t.hp > 0 and t.id != entity.id:
                    t.statuses["frozen"] = max(
                        status_duration(t.statuses.get("frozen")), 3
                    )
            for tx, ty in self.points_in_radius(entity.x, entity.y, 2):
                self.set_tile(tx, ty, SLICK_ICE, duration=5)
            self.state.add_message(f"{entity.name} bursts in a spray of ice!")
        if "spawn_on_death" in entity.tags:
            for _ in range(2):
                sx, sy = self.find_open_tile_near(entity.x, entity.y)
                if self.can_occupy(sx, sy):
                    self.spawn_actor(
                        f"spawn of {entity.name}",
                        "s",
                        sx,
                        sy,
                        hp=max(1, entity.max_hp // 3),
                        attack=max(1, entity.attack - 1),
                        defense=0,
                        faction=entity.faction,
                        ai=entity.ai or "simple",
                        tags={"summoned"},
                    )
            self.state.add_message(f"{entity.name} bursts open - something crawls out!")
