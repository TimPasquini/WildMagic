from __future__ import annotations

from collections import deque
import math
import re
from typing import Any

from .game_data import NPC_PERCEPTION_RADIUS
from .models import BLOCKING_TILES, DOOR, Entity
from .normalize import status_duration


class _AIMixin:
    """AI and NPC behavior methods mixed into GameEngine."""

    def can_sense(self, observer: Entity, target: Entity | None = None) -> bool:
        """Whether `observer` can currently notice `target` (defaulting to the player).

        Generalized from the old player-only `enemy_can_sense_player` so any
        entity can be judged for visibility/range to any other -- the same
        distance/line-of-sight/status rules just no longer assume the player
        is the only thing worth noticing.
        """
        target = target if target is not None else self.state.player
        distance = self.distance(observer, target)
        if "invisible" in target.statuses:
            return distance <= 1.5
        if distance <= 5:
            return True
        if distance <= 11 and self.has_line_of_sight(observer.x, observer.y, target.x, target.y):
            return True
        return "marked" in target.statuses and distance <= 14

    def _select_target(self, actor: Entity, default: Entity) -> Entity:
        """Pick who `actor` should act against this turn.

        Three tiers, in order:

        1. Whoever `actor` is already trading blows with (adjacent) -- engaged
           fighters finish the fight instead of flickering between targets or
           turning their back on a foe mid-swing, regardless of category.
        2. The nearest target it has a *declared* FACTION_HOSTILITIES conflict
           with, sensed or not. A force with a standing conflict came here with
           a mission and marches on the side it's at war with by known location
           -- an Imperial raid doesn't get distracted chasing some rando it
           glimpsed across the square when the town it came to burn is right
           there. (No declared conflicts ever sit in the baseline player/ally
           hostility, so ordinary monsters -- goblins, slimes -- never have any
           `known` candidates here and fall straight through, unchanged.)
        3. The nearest *sensed* target otherwise -- today's perception-gated
           behavior, preserved for baseline player/ally hostility.

        Falls back to `default` (the player) when nothing qualifies at all.
        """
        hostiles = [
            other for other in self.state.entities.values()
            if other.kind in {"player", "actor", "npc"} and self.is_hostile_to(actor, other)
        ]
        if not hostiles:
            return default
        for other in hostiles:
            if self.distance(actor, other) <= 1.5:
                return other
        known = [other for other in hostiles if self._declared_conflict(actor, other)]
        if known:
            return min(known, key=lambda other: self.distance(actor, other))
        sensed = [other for other in hostiles if self.can_sense(actor, other)]
        if sensed:
            return min(sensed, key=lambda other: self.distance(actor, other))
        return default

    def _update_npc_perceptions(self) -> None:
        """Let nearby NPCs notice what just happened, the same way the player does
        via state.messages -- so "aware of what they have seen" stays grounded in
        actual events instead of a separate, hand-authored perception feed.

        Uses message_count rather than len(messages)/slicing: messages is capped at
        80 entries, so a plain negative-index slice can resurface stale lines from
        before the cap kicked in or from turns where the NPC wasn't even nearby.
        message_count is monotonic and tells us exactly how many lines are new.
        """
        state = self.state
        if not state.npc_profiles:
            return
        new_count = state.message_count - self._npc_perception_message_count
        self._npc_perception_message_count = state.message_count
        if new_count <= 0:
            return
        new_messages = state.messages[-new_count:] if new_count <= len(state.messages) else list(state.messages)
        witnessed = [m for m in new_messages if not m.startswith(("> ", "*> "))]
        if not witnessed:
            return
        player = self.state.player
        for entity in self.state.entities.values():
            if entity.kind != "npc" or entity.hp <= 0:
                continue
            profile = self.state.npc_profiles.get(entity.id)
            if profile is None or not self.is_visible(entity.x, entity.y):
                continue
            if max(abs(entity.x - player.x), abs(entity.y - player.y)) > NPC_PERCEPTION_RADIUS:
                continue
            own_dialogue_prefixes = (f"You say to {entity.name}:", f"{entity.name} says:")
            for text in witnessed:
                # An NPC's own exchange with the player already lives in profile.conversation
                # (and is surfaced as recent_conversation) -- recording it again here would
                # just have them "notice" their own words as if overhearing a stranger.
                if text.startswith(own_dialogue_prefixes):
                    continue
                profile.remember(text)

    def _enemy_turns(self) -> None:
        player = self.state.player
        for enemy in list(self.living_enemies()):
            if any(status in enemy.statuses for status in ["stunned", "frozen"]):
                self.state.add_message(f"{enemy.name} cannot act.")
                continue
            if "slowed" in enemy.statuses and self.state.turn % 2 == 1:
                continue
            hasted = "hasted" in enemy.statuses
            action_count = 2 if hasted else 1
            for _ in range(action_count):
                if enemy.hp <= 0 or self.state.game_over:
                    break
                self._enemy_single_action(enemy, player)
        return

    def _enemy_single_action(self, enemy: Entity, player: Entity) -> None:
        if "pacifist" in enemy.tags or "noncombatant" in enemy.tags:
            return
        # Who this enemy actually moves against -- the player by default, but
        # FACTION_HOSTILITIES can put nearer, more pressing targets in range
        # (Imperial soldiers vs. Hollowmere townsfolk, etc).
        target = self._select_target(enemy, player)
        # Scavengers are cowardly by nature: they keep their distance and only
        # turn to fight when flight is impossible (cornered).
        if "scavenger" in enemy.tags and 1.5 < self.distance(enemy, target) <= 6:
            step = self._flee_step(enemy, target.x, target.y)
            if step is not None:
                enemy.x, enemy.y = step
                self._apply_tile_entry(enemy)
                return
        if "frightened" in enemy.statuses and self.distance(enemy, target) <= 8:
            step = self._flee_step(enemy, target.x, target.y)
            if step is not None:
                enemy.x, enemy.y = step
                self._apply_tile_entry(enemy)
            return
        if self.distance(enemy, target) <= 1.5:
            self.attack(enemy, target)
            return
        if any(status in enemy.statuses for status in ["rooted", "webbed"]):
            return
        if "confused" in enemy.statuses:
            dx, dy = self.rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
            if self.can_occupy(enemy.x + dx, enemy.y + dy):
                enemy.x += dx
                enemy.y += dy
                self._apply_tile_entry(enemy)
            return
        # Stationary hazards never give chase; they only ever strike what comes within reach.
        if "stationary" in enemy.tags:
            return
        # A target picked because of a *declared* conflict is a known objective --
        # pursue it by location even if it's currently out of sight (e.g. holed up
        # behind a building's walls); `_select_target` already restricts this to
        # forces actually bound by such a conflict, not perception-based chasing.
        if self.can_sense(enemy, target) or self._declared_conflict(enemy, target):
            # Ranged casters keep their distance and snipe rather than closing in.
            if (
                "ranged" in enemy.tags
                and self.distance(enemy, target) <= 7
                and self.has_line_of_sight(enemy.x, enemy.y, target.x, target.y)
            ):
                self.attack(enemy, target)
                return
            # Summoners spend a turn calling reinforcements instead of approaching.
            if "summoner" in enemy.tags and self._try_enemy_summon(enemy):
                return
            step = self.next_path_step(enemy, target.x, target.y)
            if step is not None:
                enemy.x, enemy.y = step
                self._apply_tile_entry(enemy)
        elif "disciplined" not in enemy.tags:
            dx, dy = self.rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)])
            if (dx or dy) and self.can_occupy(enemy.x + dx, enemy.y + dy):
                enemy.x += dx
                enemy.y += dy
                self._apply_tile_entry(enemy)

    def _try_enemy_summon(self, summoner: Entity) -> bool:
        """A summoner calls a minion of its own kind instead of moving. Returns True if it acted."""
        nearby_minions = [
            e for e in self.living_enemies()
            if e.id != summoner.id and "summoned" in e.tags and self.distance(summoner, e) <= 12
        ]
        if len(nearby_minions) >= 2 or self.rng.random() > 0.3:
            return False
        x, y = self.find_open_tile_near(summoner.x, summoner.y)
        if not self.can_occupy(x, y):
            return False
        name = self.rng.choice(self._SUMMONER_MINIONS)
        self.spawn_actor(
            name, "w", x, y, hp=4, attack=2, defense=0,
            faction="enemy", ai="simple", tags={"summoned", "conjured", "beast"},
        )
        self.state.add_message(f"{summoner.name} calls forth {name}.")
        return True

    def _ally_turns(self) -> None:
        allies = [
            e for e in self.state.entities.values()
            if e.kind in {"actor", "npc"} and e.faction == "ally" and e.hp > 0
        ]
        for ally in allies:
            if any(s in ally.statuses for s in ["stunned", "frozen"]):
                continue
            if "slowed" in ally.statuses and self.state.turn % 2 == 1:
                continue
            if "pacifist" in ally.tags or "noncombatant" in ally.tags:
                continue
            enemies = self.living_enemies()
            # Stationary entities never move; guardian entities only act within their territory.
            if "stationary" in ally.tags:
                nearby = [e for e in enemies if self.distance(ally, e) <= 1.5]
                if nearby:
                    self.attack(ally, min(nearby, key=lambda e: self.distance(ally, e)))
                continue
            if "guardian" in ally.tags:
                guard_range = 3.0
                nearby = [e for e in enemies if self.distance(ally, e) <= guard_range]
                if nearby:
                    target = min(nearby, key=lambda e: self.distance(ally, e))
                    if self.distance(ally, target) <= 1.5:
                        self.attack(ally, target)
                    elif not any(s in ally.statuses for s in ["rooted", "webbed"]):
                        step = self.next_path_step(ally, target.x, target.y)
                        if step is not None:
                            ally.x, ally.y = step
                            self._apply_tile_entry(ally)
                continue
            if not enemies:
                continue
            # Ranged allies attack from distance without closing in.
            if "ranged" in ally.tags:
                ranged_range = 7
                los_enemies = [
                    e for e in enemies
                    if self.distance(ally, e) <= ranged_range
                    and self.has_line_of_sight(ally.x, ally.y, e.x, e.y)
                ]
                if los_enemies:
                    target = min(los_enemies, key=lambda e: self.distance(ally, e))
                    self.attack(ally, target)
                    continue
                # No target in range — advance toward nearest enemy.
                if not any(s in ally.statuses for s in ["rooted", "webbed"]):
                    closest = min(enemies, key=lambda e: self.distance(ally, e))
                    step = self.next_path_step(ally, closest.x, closest.y)
                    if step is not None:
                        ally.x, ally.y = step
                        self._apply_tile_entry(ally)
                continue
            # Default: chase and melee.
            target = min(enemies, key=lambda e: self.distance(ally, e))
            if self.distance(ally, target) <= 1.5:
                self.attack(ally, target)
            elif not any(s in ally.statuses for s in ["rooted", "webbed"]):
                step = self.next_path_step(ally, target.x, target.y)
                if step is not None:
                    ally.x, ally.y = step
                    self._apply_tile_entry(ally)

    def _npc_turns(self) -> None:
        """Ordinary townsfolk have no combat AI -- when something hostile closes
        in, their one instinct is to run. Reuses `_flee_step` (engine.py), the
        exact helper scavengers and frightened enemies already lean on."""
        for npc in [
            e for e in self.state.entities.values()
            if e.kind == "npc" and e.faction not in {"ally", "enemy"} and e.hp > 0
        ]:
            if any(s in npc.statuses for s in ["stunned", "frozen", "rooted", "webbed"]):
                continue
            threats = [
                e for e in self.state.entities.values()
                if e.kind in {"actor", "player", "npc"} and e.hp > 0
                and self.is_hostile_to(e, npc)
                and self.distance(e, npc) <= 6
            ]
            if not threats:
                continue
            nearest = min(threats, key=lambda e: self.distance(e, npc))
            step = self._flee_step(npc, nearest.x, nearest.y)
            if step is not None:
                npc.x, npc.y = step
                self._apply_tile_entry(npc)

    _AURA_RE = re.compile(r"^aura_([a-z]+)(?:_(\d+))?$")


    def _process_entity_behaviors(self) -> None:
        """Process per-turn behavior tags on all living actors."""
        player = self.state.player
        for entity in list(self.state.entities.values()):
            if entity.kind not in {"actor", "player", "npc"} or entity.hp <= 0:
                continue
            for tag in list(entity.tags):
                m = self._AURA_RE.match(tag)
                if not m:
                    continue
                aura_type = m.group(1)
                radius = int(m.group(2)) if m.group(2) else 2
                nearby = [
                    e for e in self.entities_in_radius(entity.x, entity.y, radius)
                    if e.kind in {"actor", "player", "npc"} and e.hp > 0 and e.id != entity.id
                ]
                offensive_targets, beneficial_targets = self._behavior_targets(entity, nearby)
                if aura_type in {"burn", "fire"}:
                    for t in offensive_targets:
                        t.statuses["burning"] = max(status_duration(t.statuses.get("burning")), 2)
                elif aura_type in {"heal", "healing"}:
                    for t in beneficial_targets:
                        self.heal_entity(t, 1)
                elif aura_type in {"fear", "dread"}:
                    for t in offensive_targets:
                        t.statuses["frightened"] = max(status_duration(t.statuses.get("frightened")), 2)
                elif aura_type in {"slow", "sluggish", "weight"}:
                    for t in offensive_targets:
                        t.statuses["slowed"] = max(status_duration(t.statuses.get("slowed")), 2)
                elif aura_type in {"poison", "toxic", "plague"}:
                    for t in offensive_targets:
                        t.statuses["poisoned"] = max(status_duration(t.statuses.get("poisoned")), 3)
                elif aura_type in {"bleed", "bleeding", "wound"}:
                    for t in offensive_targets:
                        t.statuses["bleeding"] = max(status_duration(t.statuses.get("bleeding")), 2)
                elif aura_type in {"reveal", "sight", "detect"}:
                    for t in nearby:
                        t.statuses["revealed"] = max(status_duration(t.statuses.get("revealed")), 2)
                elif aura_type in {"mana", "arcane", "font"}:
                    dist = math.hypot(entity.x - player.x, entity.y - player.y)
                    if dist <= radius and player.mana < player.max_mana:
                        player.mana = min(player.max_mana, player.mana + 1)
                elif aura_type in {"damage", "harm", "pain"}:
                    for t in offensive_targets:
                        self.damage_entity(t, 1, "arcane")
                elif aura_type in {"confuse", "confusion"}:
                    for t in offensive_targets:
                        t.statuses["confused"] = max(status_duration(t.statuses.get("confused")), 2)
                elif aura_type in {"berserk", "rage"}:
                    for t in beneficial_targets:
                        t.statuses["berserk"] = max(status_duration(t.statuses.get("berserk")), 2)
                elif aura_type in {"regen", "regenerate"}:
                    for t in beneficial_targets:
                        self.heal_entity(t, 1)

    def _behavior_targets(self, source: Entity, nearby: list[Entity]) -> tuple[list[Entity], list[Entity]]:
        player_side = {"ally", "player"}
        if source.faction == "enemy":
            offensive = [e for e in nearby if e.faction in player_side or e.id == self.state.player_id]
            beneficial = [e for e in nearby if e.faction == "enemy"]
        elif source.faction in player_side or source.id == self.state.player_id:
            offensive = [e for e in nearby if e.faction == "enemy"]
            beneficial = [e for e in nearby if e.faction in player_side or e.id == self.state.player_id]
        else:
            offensive = nearby
            beneficial = [e for e in nearby if e.faction == source.faction]
        return offensive, beneficial

    def enemy_can_sense_player(self, enemy: Entity) -> bool:
        return self.can_sense(enemy)

    def next_path_step(self, entity: Entity, goal_x: int, goal_y: int) -> tuple[int, int] | None:
        start = (entity.x, entity.y)
        goal = (goal_x, goal_y)
        queue: deque[tuple[int, int]] = deque([start])
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        while queue:
            current = queue.popleft()
            if current == goal:
                break
            for neighbor in self.path_neighbors(entity, current[0], current[1], goal):
                if neighbor in came_from:
                    continue
                came_from[neighbor] = current
                queue.append(neighbor)
        if goal not in came_from:
            return None
        current = goal
        while came_from[current] is not None and came_from[current] != start:
            current = came_from[current]  # type: ignore[index]
        if current == goal and self.blocking_entity_at(goal_x, goal_y) is self.state.player:
            return None
        if current == start:
            return None
        return current

    def _flee_step(self, entity: Entity, from_x: int, from_y: int) -> tuple[int, int] | None:
        neighbors = [(entity.x + 1, entity.y), (entity.x - 1, entity.y), (entity.x, entity.y + 1), (entity.x, entity.y - 1)]
        self.rng.shuffle(neighbors)
        best: tuple[int, int] | None = None
        best_dist = math.hypot(entity.x - from_x, entity.y - from_y)
        for tx, ty in neighbors:
            if not self.can_occupy(tx, ty):
                continue
            d = math.hypot(tx - from_x, ty - from_y)
            if d > best_dist:
                best_dist = d
                best = (tx, ty)
        return best

    def path_neighbors(
        self,
        entity: Entity,
        x: int,
        y: int,
        goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        neighbors = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        self.rng.shuffle(neighbors)
        valid: list[tuple[int, int]] = []
        for tx, ty in neighbors:
            if not self.in_bounds(tx, ty):
                continue
            tile = self.tile_at(tx, ty)
            # Doors are openable — treat as passable for pathfinding (locked ones stay shut).
            if tile in BLOCKING_TILES and tile != DOOR:
                continue
            if tile == DOOR and "locked" in self.tile_tags_at(tx, ty):
                continue
            # Always allow the goal tile so entities can reach their target.
            if (tx, ty) == goal:
                valid.append((tx, ty))
                continue
            blocker = self.blocking_entity_at(tx, ty)
            if blocker is not None and blocker.id != entity.id:
                continue
            valid.append((tx, ty))
        return valid

