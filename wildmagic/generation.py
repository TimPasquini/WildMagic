from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
import math
import random
import time
from collections import deque
from typing import Any

from .game_data import (
    CLERK_NOTICES,
    EQUIPMENT_SPECS,
    LEGION_ENEMY_TEMPLATES,
    LOCKED_DOOR_KEYS,
    TRAP_SPECS,
    _BUILDING_SIZES,
    _DEFAULT_BUILDING_SIZE,
    _DEFAULT_NPC_STATS,
    _ROLE_STATS,
    _TOWN_DEFINING_TRAITS,
    _TOWN_GEN_TIMEOUT,
    _TOWN_LOCATIONS,
    _TOWN_SETTLEMENT_TYPES,
    _TOWN_SITUATIONS,
)
from .determinism import stable_seed
from .geometry import _on_bresenham
from .models import (
    BLOCKING_TILES,
    DOOR,
    FLOOR,
    ROAD,
    RUBBLE,
    STAIRS_DOWN,
    STAIRS_UP,
    VINES,
    WALL,
    WATER,
    Entity,
    Room,
    ZoneSnapshot,
)
from .normalize import normalize_id
from .props import PROP_CATEGORIES, get_all_prop_ids, get_nonblocking_prop_ids, get_prop_template
from .regions import region_for_zone


@dataclass(frozen=True)
class SiteBlueprint:
    id: str
    structure: str
    size: tuple[int, int]
    prop_ids: tuple[str, ...] = ()
    npc_role: str | None = None
    npc_tags: tuple[str, ...] = ()
    npc_wares: dict[str, int] | None = None
    hostile_count: int = 0


SITE_BLUEPRINTS: dict[str, SiteBlueprint] = {
    "sacred_site": SiteBlueprint(
        id="sacred_site",
        structure="building",
        size=(7, 5),
        prop_ids=("saint_statue", "votive_candles", "temple_bell", "cracked_font", "offering_bowl"),
        npc_role="site keeper",
        npc_tags=("sacred", "keeper", "promise_bound"),
        npc_wares={"votive candle": 2, "grave salt": 1, "gold": 8},
    ),
    "inhabited_site": SiteBlueprint(
        id="inhabited_site",
        structure="building",
        size=(5, 5),
        prop_ids=("rocking_chair", "writing_desk", "cursed_candle", "tattered_map"),
        npc_role="local keeper",
        npc_tags=("resident", "keeper", "promise_bound"),
        npc_wares={"dried herbs": 2, "gold": 6},
    ),
    "hostile_site": SiteBlueprint(
        id="hostile_site",
        structure="open",
        size=(6, 5),
        prop_ids=("old_campfire_ash", "torn_bedroll", "abandoned_pack"),
        hostile_count=2,
    ),
    "memorial_site": SiteBlueprint(
        id="memorial_site",
        structure="open",
        size=(5, 5),
        prop_ids=("inscribed_gravestone", "offering_bowl", "funeral_pyre_remnants"),
    ),
    "hidden_site": SiteBlueprint(
        id="hidden_site",
        structure="building",
        size=(4, 4),
        prop_ids=("locked_chest", "empty_chest", "abandoned_pack"),
    ),
    "creature_site": SiteBlueprint(
        id="creature_site",
        structure="open",
        size=(6, 5),
        prop_ids=("moss_covered_bones", "old_campfire_ash", "giant_spider_web"),
        hostile_count=1,
    ),
    "authority_site": SiteBlueprint(
        id="authority_site",
        structure="imperial",
        size=(7, 5),
        prop_ids=("posted_notice", "regulation_lantern", "charter_waystone"),
        npc_role="field official",
        npc_tags=("empire", "official", "promise_bound"),
        npc_wares={"sealed form": 1, "gold": 10},
    ),
}


# Floor themes (max_depth -> prop category weights) now live on each Region
# (regions.py) — the gradient is per-region, and effective wildness is
# region.wildness_base + depth. See _floor_theme_weights below.

# Thematically paired prop IDs placed together as a 2-prop "scene".
_PROP_SCENES: list[tuple[str, str]] = [
    ("chalk_circle", "ritual_dagger"),
    ("chalk_circle", "bone_circle"),
    ("summoning_circle", "bone_circle"),
    ("summoning_circle", "sigil_of_warding"),
    ("arcane_mirror", "cracked_scrying_bowl"),
    ("arcane_mirror", "obsidian_mirror"),
    ("celestial_orrery", "astrolabe"),
    ("leaking_mana_crystal", "crystal_monolith"),
    ("arcane_focus_pedestal", "suspended_orb"),
    ("alchemical_still", "bubbling_vat"),
    ("alchemical_still", "distillation_coil"),
    ("alchemical_still", "cracked_retort"),
    ("reagent_cabinet", "mortar_and_pestle"),
    ("specimen_jars", "failed_homunculus"),
    ("electrostatic_coil", "alchemical_still"),
    ("open_sarcophagus", "mummified_remains"),
    ("pile_of_skulls", "bone_throne"),
    ("ossuary_niche", "pile_of_skulls"),
    ("funeral_pyre_remnants", "sealed_burial_urn"),
    ("bone_chime", "wind_organ"),
    ("singing_stones", "ancestor_drum"),
    ("crystal_garden", "crystal_formation"),
    ("crystal_garden", "leaking_mana_crystal"),
    ("blood_tide_basin", "painted_prayer_stones"),
    ("festival_mask", "broken_puppet_stage"),
    ("echo_jar", "sealed_burial_urn"),
    ("posted_notice", "charter_waystone"),
    ("posted_notice", "regulation_lantern"),
    ("survey_marker", "requisition_ledger"),
    ("confiscation_crate", "regulation_lantern"),
    ("shattered_altar", "votive_candles"),
    ("saint_statue", "offering_bowl"),
    ("altar_of_thorns", "burned_effigy"),
    ("altar_of_thorns", "sacrificial_pit"),
    ("reliquary", "saint_statue"),
    ("votive_candles", "prayer_beads"),
    ("rotting_bookshelf", "writing_desk"),
    ("rotting_bookshelf", "scroll_of_formulas"),
    ("weapons_rack", "heavy_anvil"),
    ("weapons_rack", "siege_ballista"),
    ("iron_chains", "wall_manacles"),
    ("old_well", "water_barrel"),
    ("bioluminescent_mushroom", "giant_spider_web"),
    ("giant_spider_web", "pulsing_pod"),
    ("mushroom_ring", "bioluminescent_mushroom"),
    ("crystal_formation", "underground_spring"),
    ("ancient_root", "strangler_fig_roots"),
    ("acid_seep", "bubbling_vat"),
]


class _GenerationMixin:
    """Generation methods extracted from GameEngine."""

    def _generate_new_run(self) -> None:
        state = self.state
        state.depth = 1
        self._generate_dungeon_floor(preserve_player=False)
        state.add_message("The dungeon exhales. Wild magic listens.")
        state.add_message("Type a spell in the right panel and press Enter.")
        self.update_fov()

    def _generate_dungeon_floor(self, preserve_player: bool) -> None:
        state = self.state
        existing_player = state.entities.get(state.player_id) if preserve_player else None
        state.tiles = [[WALL for _ in range(state.width)] for _ in range(state.height)]
        state.visible.clear()
        state.explored.clear()
        state.tile_tags.clear()
        state.tile_durations.clear()
        state.entities = {}
        rooms: list[Room] = []
        for _ in range(80):
            w = self.rng.randint(5, 10)
            h = self.rng.randint(4, 8)
            x = self.rng.randint(1, state.width - w - 2)
            y = self.rng.randint(1, state.height - h - 2)
            room = Room(x, y, w, h)
            if any(room.intersects(existing) for existing in rooms):
                continue
            self._carve_room(room)
            if rooms:
                self._carve_corridor(rooms[-1].center, room.center)
            rooms.append(room)
            if len(rooms) >= 8:
                break

        if not rooms:
            fallback = Room(4, 4, 12, 8)
            self._carve_room(fallback)
            rooms.append(fallback)

        px, py = rooms[0].center
        # Non-blocking themed prop in the starting room for immediate atmosphere.
        _theme_weights = self._floor_theme_weights(self.state.depth)
        _themed_ids = [pid for cat in _theme_weights for pid in PROP_CATEGORIES.get(cat, [])]
        _nb_pool = [pid for pid in _themed_ids if not get_prop_template(pid).blocks]
        if not _nb_pool:
            _nb_pool = get_nonblocking_prop_ids()
        for _ in range(20):
            _sx, _sy = self._random_open_tile_in_room(rooms[0])
            if (_sx, _sy) != (px, py):
                self.spawn_prop(self.rng.choice(_nb_pool), _sx, _sy)
                break

        if existing_player is None:
            player = Entity(
                id="player",
                name="You",
                kind="player",
                x=px,
                y=py,
                char="@",
                hp=24,
                max_hp=24,
                mana=14,
                max_mana=14,
                attack=4,
                defense=1,
                blocks=True,
                faction="player",
            )
        else:
            player = existing_player
            player.x = px
            player.y = py
            player.blocks = True
        state.entities[player.id] = player
        if state.depth > 1:
            state.tiles[py][px] = STAIRS_UP

        region = self.region
        for room in rooms[1:]:
            if self.rng.random() < 0.85:
                if self.rng.random() < region.imperial_presence:
                    template = self.rng.choice(LEGION_ENEMY_TEMPLATES)
                else:
                    template = self.rng.choice(list(region.enemy_templates))
                name, char, hp, attack, defense, ai, tags, resistances, weaknesses = template
                x, y = self._random_open_tile_in_room(room)
                self.spawn_actor(
                    name,
                    char,
                    x,
                    y,
                    hp,
                    attack,
                    defense,
                    "enemy",
                    ai,
                    tags=tags,
                    resistances=resistances,
                    weaknesses=weaknesses,
                )
            if self.rng.random() < 0.55:
                item = self.rng.choice(
                    [
                        ("mana crystal", "!", "mana crystal"),
                        ("blood moss", ",", "blood moss"),
                        ("bone charm", "?", "bone charm"),
                        ("healing potion", "!", "healing potion"),
                        ("mana potion", "!", "mana potion"),
                        ("smoke vial", "~", "smoke vial"),
                        ("blink scroll", "?", "blink scroll"),
                    ]
                )
                x, y = self._random_open_tile_in_room(room)
                self.spawn_item(item[0], item[1], x, y, item[2])
            if self.rng.random() < 0.25:
                trap_kind = self.rng.choice(list(TRAP_SPECS))
                x, y = self._random_open_tile_in_room(room)
                if self.tile_at(x, y) == FLOOR and (x, y) != (px, py):
                    self.set_tile(x, y, FLOOR, tags={trap_kind})
            if self.rng.random() < 0.2:
                gear_name = self.rng.choice(list(EQUIPMENT_SPECS))
                slot_glyphs = {
                    "weapon": "/",
                    "charm": "*",
                    "armor": "[",
                    "head": "[",
                    "chest": "[",
                    "legs": "[",
                    "feet": "[",
                    "hands": "[",
                }
                glyph = slot_glyphs.get(EQUIPMENT_SPECS[gear_name]["slot"], "[")
                x, y = self._random_open_tile_in_room(room)
                self.spawn_item(gear_name, glyph, x, y, gear_name)
            self._spawn_props_in_room(room, self.state.depth)

        down_x, down_y = rooms[-1].center
        state.tiles[down_y][down_x] = STAIRS_DOWN
        self._place_doors()
        self._place_locked_door(rooms)

        # The Censorate's paperwork follows the player down, signed by the same
        # increasingly weary official — reliably wherever the Empire reaches,
        # only rarely out in the deep wild.
        notice_chance = 1.0 if region.imperial_presence >= 0.2 else 0.25
        if self.rng.random() < notice_chance:
            notice_text = CLERK_NOTICES[min(state.depth - 1, len(CLERK_NOTICES) - 1)]
            for _ in range(20):
                nx, ny = self._random_open_tile_in_room(self.rng.choice(rooms))
                if (nx, ny) != (px, py):
                    notice = self.spawn_prop("posted_notice", nx, ny)
                    if notice:
                        notice.description = notice_text
                    break

    def _generate_test_chamber(self) -> None:
        state = self.state
        state.tiles = [[WALL for _ in range(state.width)] for _ in range(state.height)]
        chamber = Room(2, 2, 18, 12)
        self._carve_room(chamber)
        for x in range(20, 30):
            state.tiles[7][x] = FLOOR
        self._carve_room(Room(30, 4, 8, 7))
        state.tiles[7][6] = DOOR
        state.tiles[7][20] = DOOR

        player = Entity(
            id="player",
            name="You",
            kind="player",
            x=5,
            y=7,
            char="@",
            hp=24,
            max_hp=24,
            mana=14,
            max_mana=14,
            attack=4,
            defense=1,
            blocks=True,
            faction="player",
        )
        state.entities[player.id] = player
        state.tiles[8][10] = WATER
        state.tiles[6][11] = VINES
        state.tiles[7][13] = RUBBLE
        state.tiles[7][5] = STAIRS_DOWN
        state.tiles[7][18] = STAIRS_DOWN
        self.spawn_actor("test goblin", "g", 10, 7, 8, 3, 0, "enemy", "goblin", tags={"goblin", "flesh"})
        self.spawn_actor(
            "patient slime",
            "s",
            34,
            7,
            10,
            2,
            1,
            "enemy",
            "slime",
            tags={"slime", "ash"},
            resistances={"poison": 50},
            weaknesses={"frost": 25},
        )
        self.spawn_item("mana crystal", "!", 7, 7, "mana crystal")
        self.spawn_item("blood moss", ",", 6, 8, "blood moss")
        state.add_message("The test chamber waits without pretending to be fair.")
        state.add_message("Use CLI commands or type wild spells in the panel.")
        self.update_fov()

    def _generate_empire_compound(self) -> None:
        """A bilaterally-symmetric Imperial garrison — the Grand Empire does not build by accident.

        Every room, corridor, door, and patrol on one side of the central axis has an
        identical mirror on the other. Room/corridor dimensions are kept odd so that
        reflecting a shape's bounds also reflects its center exactly (no off-by-one drift).
        """
        state = self.state
        state.tiles = [[WALL for _ in range(state.width)] for _ in range(state.height)]
        state.visible.clear()
        state.explored.clear()
        state.tile_tags.clear()
        state.tile_durations.clear()
        state.entities = {}

        axis_x = state.width // 2

        courtyard = Room(axis_x - 4, 10, 9, 7)
        self._carve_room(courtyard)

        garrison_roster = [
            ("legion spearman", "l", 9, 3, 1, "legion", {"empire", "human", "soldier", "disciplined"}, {"physical": 15}, {}),
            ("drill initiate", "i", 6, 2, 0, "legion", {"empire", "human", "soldier", "disciplined"}, {}, {"force": 25}),
            ("iron chaplain", "h", 7, 2, 1, "legion", {"empire", "human", "priest", "disciplined"}, {"radiant": 25}, {"poison": 25}),
        ]

        cell_rooms: dict[int, Room] = {}
        for cell_y in (5, 18):
            cell = Room(axis_x + 7, cell_y, 5, 5)
            self._carve_room_mirrored(cell, axis_x)
            self._carve_corridor_mirrored(courtyard.center, cell.center, axis_x)
            cell_rooms[cell_y] = cell
            name, char, hp, attack, defense, ai, tags, resistances, weaknesses = self.rng.choice(garrison_roster)
            ox, oy = self._random_open_tile_in_room(cell)
            self.spawn_actor(name, char, ox, oy, hp, attack, defense, "enemy", ai,
                             tags=set(tags), resistances=dict(resistances), weaknesses=dict(weaknesses))
            self.spawn_actor(name, char, 2 * axis_x - ox, oy, hp, attack, defense, "enemy", ai,
                             tags=set(tags), resistances=dict(resistances), weaknesses=dict(weaknesses))

        for tower_y, partner_cell_y in ((2, 5), (23, 18)):
            tower = Room(axis_x + 14, tower_y, 3, 3)
            self._carve_room_mirrored(tower, axis_x)
            self._carve_corridor_mirrored(tower.center, cell_rooms[partner_cell_y].center, axis_x)

        self._place_doors_mirrored(axis_x, count=4)

        cx, cy = courtyard.center
        player = Entity(
            id="player",
            name="You",
            kind="player",
            x=cx,
            y=cy - 2,
            char="@",
            hp=24,
            max_hp=24,
            mana=14,
            max_mana=14,
            attack=4,
            defense=1,
            blocks=True,
            faction="player",
        )
        state.entities[player.id] = player
        state.tiles[cy + 2][cx] = STAIRS_DOWN
        self.spawn_actor(
            "wall sergeant", "m", cx, cy, 10, 3, 2, "enemy", "legion",
            tags={"empire", "human", "soldier", "officer", "disciplined"},
            resistances={"physical": 15},
        )

        state.add_message("Stone walls rise in perfect symmetry - the Grand Empire does not build by accident.")
        state.add_message("Somewhere ahead, boots strike the ground in unison.")
        self.update_fov()

    def _generate_town_start(self) -> None:
        """Hollowmere: a frontier town standing in the open at the mouth of an old
        dungeon stair -- buildings on bare ground, Caves-of-Qud style, not a walled
        warren of corridors. Talkable townsfolk (each with their own memory and
        backstory) live here -- and the town is not safe: an Imperial raiding party
        is already moving against it when the player arrives."""
        state = self.state
        state.tiles = [[FLOOR for _ in range(state.width)] for _ in range(state.height)]
        state.visible.clear()
        state.explored.clear()
        state.tile_tags.clear()
        state.tile_durations.clear()
        state.entities = {}
        state.npc_profiles = {}

        zone_rng = random.Random(stable_seed(state.rng_seed, "hollowmere"))
        self._scatter_terrain_features(zone_rng)

        inn = Room(3, 10, 8, 6)
        market = Room(30, 10, 8, 6)
        temple = Room(16, 3, 9, 6)
        gatehouse = Room(16, 20, 9, 6)

        for room in (inn, market, temple, gatehouse):
            self._wall_room_perimeter(room)
        # Each building gets a single door facing the open plaza at the town's heart.
        state.tiles[inn.y + inn.h // 2][inn.x + inn.w - 1] = DOOR
        state.tiles[market.y + market.h // 2][market.x] = DOOR
        state.tiles[temple.y + temple.h - 1][temple.x + temple.w // 2] = DOOR
        state.tiles[gatehouse.y][gatehouse.x + gatehouse.w // 2] = DOOR

        px, py = state.width // 2, state.height // 2
        player = Entity(
            id="player",
            name="You",
            kind="player",
            x=px,
            y=py,
            char="@",
            hp=24,
            max_hp=24,
            mana=14,
            max_mana=14,
            attack=4,
            defense=1,
            blocks=True,
            faction="player",
        )
        state.entities[player.id] = player
        if not self.can_occupy(px, py):
            player.x, player.y = self._find_entry_tile(px, py)

        gx, gy = gatehouse.center
        state.tiles[gy][gx] = STAIRS_DOWN

        maren = self.spawn_npc(
            "Old Maren", "M", *self._random_open_tile_in_room(inn),
            role="innkeeper",
            backstory=(
                "Has run the Lantern and Bone inn for thirty years, since long before the "
                "Grand Empire's roads reached this far north. Buries her opinions about the "
                "legion under a tray of drinks and a closed mouth."
            ),
            traits=["gruff", "observant", "secretly soft-hearted"],
            tags={"human", "hollowmere_townsfolk"},
            wanted_item="Glass Eye of Hollowmere",
            wanted_qty=1,
            reward_gold=15,
            reward_item="mana crystal",
            reward_qty=1,
        )
        self.state.npc_profiles[maren.id].remember(
            "Three Imperial scouts passed through at dawn, asking after a wild mage."
        )

        quill = self.spawn_npc(
            "Quill Hatchet", "Q", *self._random_open_tile_in_room(market),
            role="peddler",
            backstory=(
                "Travels the frontier roads buying odd curios and reselling them at triple "
                "the price. Knows which rumors are worth repeating and which ones get a "
                "person's throat cut."
            ),
            traits=["chatty", "shrewd", "easily distracted by anything shiny"],
            tags={"human", "hollowmere_townsfolk"},
            wares={"trinket": 3, "lockpick": 1, "smoke vial": 2, "silk robe": 1, "wizards hat": 1, "leather boots": 1, "gold": 25},
            wanted_item="Imperial Campaign Map",
            wanted_qty=1,
            reward_gold=25,
            reward_item="smoke vial",
            reward_qty=1,
        )
        self.state.npc_profiles[quill.id].remember(
            "Lost a good knife to a cutpurse working the market stalls just yesterday."
        )

        wren = self.spawn_npc(
            "Sister Wren", "S", *self._random_open_tile_in_room(temple),
            role="temple acolyte",
            backstory=(
                "Tends the small shrine to the old earth-saints, half-forgotten since the "
                "Empire brought its own gods north. Worries more about the dungeon's "
                "restless dead than any war of banners."
            ),
            traits=["serene", "watchful", "quietly stubborn"],
            tags={"human", "hollowmere_townsfolk"},
            wanted_item="Amulet of the Old Saints",
            wanted_qty=1,
            reward_gold=10,
            reward_item="grave salt",
            reward_qty=1,
        )
        self.state.npc_profiles[wren.id].remember(
            "The candles in the undercroft keep guttering, as if something below is breathing."
        )

        # Ressa fights -- she's the one named townsfolk who can actually anchor a
        # defense, with the stats and faction (an ally, not neutral) to back it up.
        ressa = self.spawn_npc(
            "Captain Ressa Vane", "C", *self._random_open_tile_in_room(gatehouse),
            role="town guard captain",
            backstory=(
                "Commands the dozen guards who keep the peace and watch the old dungeon "
                "stair. Trusts wild magic about as much as she trusts the Empire - which is "
                "to say, not at all, and she'll tell you so."
            ),
            traits=["wary", "blunt", "fiercely protective of the town"],
            tags={"human", "hollowmere_townsfolk", "soldier"},
            hp=20, attack=5, defense=2, faction="ally",
            wanted_item="Stolen Silver Seal",
            wanted_qty=1,
            reward_gold=40,
        )
        self.state.npc_profiles[ressa.id].remember(
            "Something dragged a sheep carcass up from the dungeon stair last night and left it in the square."
        )

        # The Empire is already here: a squad standing in the open plaza, weapons
        # drawn, when the player arrives -- close enough to the player's own
        # entry point that its three soldiers fan out toward three different
        # buildings on their first moves rather than marching in lockstep on
        # just one. Tagged "empire" via the templates -- FACTION_HOSTILITIES
        # (engine.py) does the rest for free, including the soldiers knowing
        # exactly which doors to make for.
        occupied: set[tuple[int, int]] = {(player.x, player.y)}
        squad_roster = (LEGION_ENEMY_TEMPLATES[0], LEGION_ENEMY_TEMPLATES[0], LEGION_ENEMY_TEMPLATES[1])
        squad_origin = (23, 15)
        for template, (dx, dy) in zip(squad_roster, ((0, 0), (1, 0), (0, 1))):
            spot = (squad_origin[0] + dx, squad_origin[1] + dy)
            if spot in occupied or not self.can_occupy(*spot):
                spot = self.find_open_tile_near(*spot)
            self._spawn_from_template(template, spot[0], spot[1])
            occupied.add(spot)

        spot = self._random_open_ground_tile(zone_rng, occupied)
        if spot is not None:
            self.spawn_actor(
                "goblin cutpurse", "g", spot[0], spot[1], 8, 3, 0, "enemy", "goblin",
                tags={"goblin", "humanoid", "flesh"},
            )
            occupied.add(spot)
        for _ in range(2):
            spot = self._random_open_ground_tile(zone_rng, occupied)
            if spot is None:
                break
            self.spawn_actor(
                "carrion rat", "r", spot[0], spot[1], 4, 2, 0, "enemy", "simple",
                tags={"beast", "vermin", "scavenger"}, resistances={"poison": 50},
            )
            occupied.add(spot)

        state.add_message("Hollowmere clings to the lip of the old dungeon stair, half town and half watchtower.")
        state.add_message("Steel rings out across the square below - Imperial soldiers are already moving on the town.")
        self.update_fov()

    # ------------------------------------------------------------------
    # Frontier: a Qud-style grid of open-country zones you cross by foot,
    # each an open stretch of ground dotted with standalone buildings
    # rather than a wall-filled warren of rooms and corridors.
    # ------------------------------------------------------------------

    def _generate_frontier_start(self) -> None:
        state = self.state
        state.zone_x = 0
        state.zone_y = 0
        state.zones = {}
        state.depth = 1
        state.max_depth = 1
        state.entities = {}
        state.tile_tags = {}
        state.tile_durations = {}
        state.explored = set()

        state.zone_type = self._generate_open_zone(0, 0)

        px, py = state.width // 2, state.height // 2
        player = Entity(
            id="player",
            name="You",
            kind="player",
            x=px,
            y=py,
            char="@",
            hp=24,
            max_hp=24,
            mana=14,
            max_mana=14,
            attack=4,
            defense=1,
            blocks=True,
            faction="player",
        )
        state.entities[player.id] = player
        if not self.can_occupy(px, py):
            player.x, player.y = self._find_entry_tile(px, py)

        state.add_message("Open country stretches in every direction beneath a wide sky.")
        state.add_message("Walk to the edge of the land to cross into the next stretch of it.")
        self.update_fov()

    def _imperial_density(self, zx: int, zy: int) -> float:
        """How strongly the Grand Empire holds a zone — higher to the northeast, lower to the southwest."""
        gradient = (zx + zy) / 8.0
        return max(0.05, min(0.95, 0.5 + gradient))

    def _generate_open_zone(self, zx: int, zy: int) -> str:
        """Open ground dotted with standalone buildings — Caves-of-Qud overworld style, not carved corridors."""
        state = self.state
        state.tiles = [[FLOOR for _ in range(state.width)] for _ in range(state.height)]
        state.visible.clear()
        state.tile_tags.clear()
        state.tile_durations.clear()

        zone_rng = random.Random(stable_seed(state.rng_seed, "frontier_zone", zx, zy))
        imperial_density = self._imperial_density(zx, zy)

        self._scatter_terrain_features(zone_rng)
        buildings = self._place_zone_buildings(zone_rng, imperial_density)
        realized_promises = self._realize_zone_promises(zx, zy, zone_rng, buildings)
        self._populate_zone(zone_rng, buildings, imperial_density)

        if self._zone_is_road(zx, zy):
            self._draw_road_through_zone(zx, zy)
            state.add_message("A dirt road cuts through here, worn flat by countless boots.")

        if imperial_density >= 0.7:
            zone_type = "imperial reach"
            state.add_message("Banners of the Grand Empire snap overhead - the land itself stands at attention.")
        elif imperial_density <= 0.3:
            zone_type = "wilds"
            state.add_message("No order rules out here. The wind moves through open country untouched by the legions.")
        else:
            zone_type = "borderlands"
            state.add_message("The land is a patchwork - wild growth pressing against straight Imperial walls.")
        for promise in realized_promises:
            flesh = getattr(promise, "flesh", None) or {}
            state.add_message(flesh.get("arrival_line") or f"The story was true: {promise.subject} is here.")
        return zone_type

    def _scatter_terrain_features(self, zone_rng: random.Random) -> None:
        """Sprinkle small clusters of natural terrain across the open ground for texture."""
        state = self.state
        width, height = state.width, state.height
        for _ in range(zone_rng.randint(2, 4)):
            kind = zone_rng.choice([VINES, RUBBLE, WATER])
            cx = zone_rng.randint(3, width - 4)
            cy = zone_rng.randint(3, height - 4)
            radius = zone_rng.randint(1, 3)
            for y in range(cy - radius, cy + radius + 1):
                for x in range(cx - radius, cx + radius + 1):
                    if not self.in_bounds(x, y):
                        continue
                    if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius:
                        continue
                    if zone_rng.random() < 0.7 and state.tiles[y][x] == FLOOR:
                        state.tiles[y][x] = kind

    def _place_zone_buildings(self, zone_rng: random.Random, imperial_density: float) -> list[dict[str, Any]]:
        """Place a handful of free-standing, non-overlapping buildings within the open ground.

        A margin keeps every building clear of the outer ring of tiles so the edges of
        the zone — where the player crosses to neighboring zones — always stay walkable.
        """
        state = self.state
        margin = 3
        placed: list[Room] = []
        buildings: list[dict[str, Any]] = []
        attempts = 0
        target = zone_rng.randint(2, 5)
        while len(placed) < target and attempts < 80:
            attempts += 1
            imperial = zone_rng.random() < imperial_density
            if imperial:
                w = zone_rng.choice([5, 7, 9])
                h = zone_rng.choice([5, 7])
            else:
                w = zone_rng.randint(4, 8)
                h = zone_rng.randint(4, 7)
            x = zone_rng.randint(margin, state.width - w - margin)
            y = zone_rng.randint(margin, state.height - h - margin)
            room = Room(x, y, w, h)
            if any(room.intersects(existing) for existing in placed):
                continue
            placed.append(room)
            if imperial:
                self._build_imperial_structure(room)
                buildings.append({"room": room, "kind": "imperial"})
            else:
                self._build_common_structure(room, zone_rng)
                buildings.append({"room": room, "kind": "common"})
        return buildings

    def _build_common_structure(self, room: Room, zone_rng: random.Random) -> None:
        """A plain walled structure — a shack, outpost, or ruin — with one door on a random side."""
        self._wall_room_perimeter(room)
        side = zone_rng.choice(["north", "south", "east", "west"])
        if side == "north":
            door = (zone_rng.randint(room.x + 1, room.x + room.w - 2), room.y)
        elif side == "south":
            door = (zone_rng.randint(room.x + 1, room.x + room.w - 2), room.y + room.h - 1)
        elif side == "west":
            door = (room.x, zone_rng.randint(room.y + 1, room.y + room.h - 2))
        else:
            door = (room.x + room.w - 1, zone_rng.randint(room.y + 1, room.y + room.h - 2))
        self.state.tiles[door[1]][door[0]] = DOOR

    def _realize_zone_promises(
        self,
        zx: int,
        zy: int,
        zone_rng: random.Random,
        buildings: list[dict[str, Any]],
    ) -> list[Any]:
        reservations = list(self.state.promise_reservations.get((zx, zy), []))
        if not reservations:
            return []
        realized: list[Any] = []
        placed_rooms = [building["room"] for building in buildings]
        by_id = {promise.id: promise for promise in self.state.promises}
        for reservation in reservations:
            promise = by_id.get(reservation.promise_id)
            if promise is None or promise.status in {"realized", "fulfilled", "redeemed"}:
                continue
            site = SITE_BLUEPRINTS.get(reservation.blueprint)
            if site is None:
                continue
            room = self._place_promise_room(zone_rng, site, placed_rooms)
            if room is None:
                continue
            placed_rooms.append(room)
            self._build_promise_structure(room, site, zone_rng)
            buildings.append({"room": room, "kind": "promise", "blueprint": site.id, "promise_id": promise.id})
            self._populate_promise_structure(room, site, promise, zone_rng)
            promise.status = "realized"
            promise.realized_in = f"{site.id} at zone ({zx},{zy})"
            realized.append(promise)
        self.state.promise_reservations[(zx, zy)] = [
            reservation for reservation in reservations if reservation.promise_id not in {promise.id for promise in realized}
        ]
        if not self.state.promise_reservations[(zx, zy)]:
            self.state.promise_reservations.pop((zx, zy), None)
        return realized

    def _place_promise_room(
        self,
        zone_rng: random.Random,
        site: SiteBlueprint,
        placed_rooms: list[Room],
    ) -> Room | None:
        state = self.state
        margin = 3
        w, h = site.size
        for _ in range(100):
            x = zone_rng.randint(margin, state.width - w - margin)
            y = zone_rng.randint(margin, state.height - h - margin)
            room = Room(x, y, w, h)
            if any(room.intersects(existing) for existing in placed_rooms):
                continue
            return room
        return None

    def _build_promise_structure(self, room: Room, site: SiteBlueprint, zone_rng: random.Random) -> None:
        if site.structure == "open":
            for y in range(room.y, room.y + room.h):
                for x in range(room.x, room.x + room.w):
                    self.state.tiles[y][x] = FLOOR
            return
        if site.structure == "imperial":
            self._build_imperial_structure(room)
            return
        self._build_common_structure(room, zone_rng)

    def _populate_promise_structure(
        self,
        room: Room,
        site: SiteBlueprint,
        promise: Any,
        zone_rng: random.Random,
    ) -> None:
        flesh = getattr(promise, "flesh", None) or {}
        occupied = {(entity.x, entity.y) for entity in self.state.entities.values()}
        flesh_prop_description = flesh.get("prop_description")
        for prop_id in self._site_props_for_promise(site, promise)[:3]:
            spot = self._random_unoccupied_open_tile_in_room(room, occupied)
            if spot is None:
                continue
            prop = self.spawn_prop(prop_id, spot[0], spot[1])
            if prop is not None:
                occupied.add(spot)
                if flesh_prop_description:
                    prop.description = flesh_prop_description
                    flesh_prop_description = None
        if site.npc_role is not None:
            spot = self._random_unoccupied_open_tile_in_room(room, occupied)
            if spot is not None:
                keeper_name = flesh.get("keeper_name") or self._promise_keeper_name(promise)
                backstory = flesh.get("keeper_backstory") or f"Keeps this place and knows the story that brought you here: {promise.text}"
                self.spawn_npc(
                    keeper_name,
                    "k",
                    spot[0],
                    spot[1],
                    role=site.npc_role,
                    backstory=backstory,
                    traits=["watchful", "story-bound"],
                    tags={"npc", *site.npc_tags},
                    wares=dict(site.npc_wares or {}),
                    hp=12,
                    attack=2,
                    defense=0,
                    faction="neutral",
                )
                occupied.add(spot)
        for _ in range(site.hostile_count):
            spot = self._random_unoccupied_open_tile_in_room(room, occupied)
            if spot is not None:
                self._spawn_from_template(zone_rng.choice(list(self.region.enemy_templates)), spot[0], spot[1])
                occupied.add(spot)
        self._spawn_quest_objective_item(room, promise, occupied)

    def _spawn_quest_objective_item(
        self,
        room: Room,
        promise: Any,
        occupied: set[tuple[int, int]],
    ) -> None:
        # Any promise carrying a fetch objective places its item at the realized site —
        # quest fetch targets and prophesied treasure alike.
        objective = getattr(promise, "objective", None)
        if objective is None or objective.type != "fetch":
            return
        item_name = str(objective.data.get("item") or "").strip()
        if not item_name:
            return
        item_key = item_name.lower()
        if item_key in self.state.inventory:
            return
        if any(entity.kind == "item" and (entity.name.lower() == item_key or entity.item_type == item_key) for entity in self.state.entities.values()):
            return
        spot = self._random_unoccupied_open_tile_in_room(room, occupied)
        if spot is None:
            return
        from .npc_quests import QUEST_ITEMS

        spec = QUEST_ITEMS.get(item_key, {"char": "?", "item_type": "quest_item", "material": None, "tags": {"quest_item"}})
        self.spawn_item(
            name=item_name.title(),
            char=str(spec.get("char") or "?"),
            x=spot[0],
            y=spot[1],
            item_type=item_key,
            quantity=max(1, int(objective.data.get("quantity") or 1)),
            material=spec.get("material"),
            tags=set(spec.get("tags") or {"quest_item"}),
        )
        occupied.add(spot)
        self.state.add_message("A strange feeling washes over you. There is something important nearby...")

    def _random_unoccupied_open_tile_in_room(
        self,
        room: Room,
        occupied: set[tuple[int, int]],
    ) -> tuple[int, int] | None:
        for _ in range(30):
            spot = self._random_open_tile_in_room(room)
            if spot not in occupied and self.can_occupy(spot[0], spot[1]):
                return spot
        return None

    def _site_props_for_promise(self, site: SiteBlueprint, promise: Any) -> list[str]:
        promise_tags = {normalize_id(str(tag)) for tag in getattr(promise, "tags", [])}
        prop_ids = list(site.prop_ids)
        if "bone" in promise_tags or "grave" in promise_tags or "death" in promise_tags:
            prop_ids.extend(["ossuary_niche", "inscribed_gravestone", "bone_chime"])
        if "fire" in promise_tags or "midnight" in promise_tags:
            prop_ids.extend(["votive_candles", "cursed_candle", "iron_brazier"])
        if "empire" in promise_tags or "warrant" in promise_tags:
            prop_ids.extend(["posted_notice", "requisition_ledger", "regulation_lantern"])
        deduped: list[str] = []
        for prop_id in prop_ids:
            if prop_id not in deduped:
                deduped.append(prop_id)
        return deduped

    def _promise_keeper_name(self, promise: Any) -> str:
        subject = normalize_id(getattr(promise, "subject", "keeper")).replace("_", " ").strip()
        words = [word.capitalize() for word in subject.split()[:2] if word]
        return "Keeper " + (" ".join(words) if words else "Maren")

    def _build_imperial_structure(self, room: Room) -> None:
        """A symmetrical Imperial outpost — the Grand Empire does not build by accident.

        Door and central marker both sit on the room's own vertical axis, so the
        structure mirrors itself perfectly without needing a paired twin.
        """
        self._wall_room_perimeter(room)
        axis_x = room.x + room.w // 2
        cy = room.y + room.h // 2
        self.state.tiles[room.y + room.h - 1][axis_x] = DOOR
        if room.w >= 5 and room.h >= 5:
            self.state.tiles[cy][axis_x] = RUBBLE

    def _wall_room_perimeter(self, room: Room) -> None:
        for y in range(room.y, room.y + room.h):
            for x in range(room.x, room.x + room.w):
                on_edge = x in (room.x, room.x + room.w - 1) or y in (room.y, room.y + room.h - 1)
                self.state.tiles[y][x] = WALL if on_edge else FLOOR

    def _populate_zone(self, zone_rng: random.Random, buildings: list[dict[str, Any]], imperial_density: float) -> None:
        state = self.state
        occupied: set[tuple[int, int]] = {(state.player.x, state.player.y)} if state.player_id in state.entities else set()

        for building in buildings:
            room: Room = building["room"]
            if building["kind"] == "imperial":
                for _ in range(zone_rng.randint(1, 2)):
                    spot = self._random_open_tile_in_room(room)
                    if spot in occupied:
                        continue
                    self._spawn_from_template(zone_rng.choice(LEGION_ENEMY_TEMPLATES), spot[0], spot[1])
                    occupied.add(spot)
            elif building["kind"] == "promise":
                continue
            elif zone_rng.random() < 0.5:
                spot = self._random_open_tile_in_room(room)
                if spot not in occupied:
                    self._spawn_from_template(zone_rng.choice(list(self.region.enemy_templates)), spot[0], spot[1])
                    occupied.add(spot)

        for _ in range(zone_rng.randint(1, 3)):
            spot = self._random_open_ground_tile(zone_rng, occupied)
            if spot is None:
                break
            roster = LEGION_ENEMY_TEMPLATES if zone_rng.random() < imperial_density else list(self.region.enemy_templates)
            self._spawn_from_template(zone_rng.choice(roster), spot[0], spot[1])
            occupied.add(spot)

        for _ in range(zone_rng.randint(0, 2)):
            spot = self._random_open_ground_tile(zone_rng, occupied)
            if spot is None:
                break
            name, char, item_type = zone_rng.choice(
                [
                    ("mana crystal", "!", "mana crystal"),
                    ("blood moss", ",", "blood moss"),
                    ("bone charm", "?", "bone charm"),
                ]
            )
            self.spawn_item(name, char, spot[0], spot[1], item_type)
            occupied.add(spot)

    def _spawn_from_template(
        self,
        template: tuple[str, str, int, int, int, str, set[str], dict[str, int], dict[str, int]],
        x: int,
        y: int,
        faction: str = "enemy",
    ) -> Entity:
        name, char, hp, attack, defense, ai, tags, resistances, weaknesses = template
        return self.spawn_actor(
            name, char, x, y, hp, attack, defense, faction, ai,
            tags=set(tags), resistances=dict(resistances), weaknesses=dict(weaknesses),
        )

    def _random_open_ground_tile(
        self, zone_rng: random.Random, avoid: set[tuple[int, int]]
    ) -> tuple[int, int] | None:
        state = self.state
        for _ in range(100):
            x = zone_rng.randint(2, state.width - 3)
            y = zone_rng.randint(2, state.height - 3)
            if (x, y) in avoid or state.tiles[y][x] != FLOOR:
                continue
            if self.can_occupy(x, y):
                return x, y
        return None

    def _draw_road_through_zone(self, zx: int, zy: int) -> None:
        """Stamp ROAD tiles from each road-bearing edge toward the zone center.
        Skips WALL tiles so buildings placed earlier don't get holes punched in them."""
        if not self._zone_is_road(zx, zy):
            return
        state = self.state
        edges = self._road_edges(zx, zy)
        if not edges:
            return
        cx, cy = state.width // 2, state.height // 2
        edge_points: dict[str, tuple[int, int]] = {
            "north": (state.width // 2, 0),
            "south": (state.width // 2, state.height - 1),
            "west": (0, state.height // 2),
            "east": (state.width - 1, state.height // 2),
        }
        for edge in edges:
            ex, ey = edge_points[edge]
            # Horizontal leg from entry to cx, then vertical leg to cy.
            x = ex
            while x != cx:
                if self.in_bounds(x, ey) and state.tiles[ey][x] != WALL:
                    state.tiles[ey][x] = ROAD
                x += 1 if cx > x else -1
            y = ey
            while y != cy:
                if self.in_bounds(cx, y) and state.tiles[y][cx] != WALL:
                    state.tiles[y][cx] = ROAD
                y += 1 if cy > y else -1
        if self.in_bounds(cx, cy) and state.tiles[cy][cx] != WALL:
            state.tiles[cy][cx] = ROAD

    def _generate_llm_town(self, zx: int, zy: int, spec: Any, generation_context: dict[str, Any] | None = None) -> str:
        """Generate an open-zone town from an LLM-produced TownSpec."""
        state = self.state
        state.tiles = [[FLOOR for _ in range(state.width)] for _ in range(state.height)]
        state.visible.clear()
        state.tile_tags.clear()
        state.tile_durations.clear()

        zone_rng = random.Random(stable_seed(state.rng_seed, "llm_town", zx, zy))
        self._scatter_terrain_features(zone_rng)
        # Draw road before placing buildings so buildings can overwrite road tiles where they sit.
        self._draw_road_through_zone(zx, zy)

        # Place buildings from the spec.
        margin = 3
        placed: list[Room] = []
        placed_by_type: dict[str, Room] = {}
        for building_spec in spec.buildings:
            btype = building_spec.type.lower().strip()
            w, h = _BUILDING_SIZES.get(btype, _DEFAULT_BUILDING_SIZE)
            placed_room: Room | None = None
            for _ in range(80):
                x = zone_rng.randint(margin, state.width - w - margin)
                y = zone_rng.randint(margin, state.height - h - margin)
                room = Room(x, y, w, h)
                if any(room.intersects(existing) for existing in placed):
                    continue
                placed_room = room
                break
            if placed_room is None:
                continue
            placed.append(placed_room)
            self._wall_room_perimeter(placed_room)
            self._build_common_structure(placed_room, zone_rng)
            if btype not in placed_by_type:
                placed_by_type[btype] = placed_room

        # Spawn NPCs.
        occupied: set[tuple[int, int]] = {(state.player.x, state.player.y)} if state.player_id in state.entities else set()
        for npc_spec in spec.npcs:
            btype = (npc_spec.building or "").lower().strip()
            room = placed_by_type.get(btype)
            if room is not None:
                spot: tuple[int, int] | None = None
                for _ in range(20):
                    candidate = self._random_open_tile_in_room(room)
                    if candidate not in occupied:
                        spot = candidate
                        break
            else:
                spot = self._random_open_ground_tile(zone_rng, occupied)
            if spot is None:
                continue
            occupied.add(spot)
            role = npc_spec.role.lower().strip()
            stats = _ROLE_STATS.get(role, _DEFAULT_NPC_STATS)
            from .npc_quests import generate_npc_quest
            quest_data = generate_npc_quest(self, zone_rng) or {}
            self.spawn_npc(
                name=npc_spec.name,
                char="@",
                x=spot[0],
                y=spot[1],
                role=npc_spec.role,
                backstory=npc_spec.backstory,
                traits=npc_spec.traits,
                tags={"npc"},
                wares=npc_spec.wares,
                hp=stats["hp"],
                attack=stats["attack"],
                defense=stats["defense"],
                faction="neutral",
                wanted_item=quest_data.get("wanted_item"),
                wanted_qty=quest_data.get("wanted_qty", 0),
                reward_gold=quest_data.get("reward_gold", 0),
                reward_item=quest_data.get("reward_item"),
                reward_qty=quest_data.get("reward_qty", 0),
            )

        state.add_message(f"You arrive at {spec.town_name}.")
        if spec.description:
            state.add_message(spec.description)
        promise_hooks = (generation_context or {}).get("promise_hooks") or []
        top_hook = promise_hooks[0] if promise_hooks and isinstance(promise_hooks, list) and isinstance(promise_hooks[0], dict) else None
        hook_id = str(top_hook.get("id")) if top_hook and top_hook.get("id") else ""
        if hook_id:
            for promise in self.state.promises:
                if promise.id == hook_id and promise.status not in {"realized", "fulfilled", "redeemed"}:
                    promise.status = "realized"
                    promise.realized_in = f"{spec.town_name} ({zx},{zy})"
                    state.add_message(f"An old promise finds a place in {spec.town_name}.")
                    break
            reservations = self.state.promise_reservations.get((zx, zy), [])
            self.state.promise_reservations[(zx, zy)] = [
                reservation for reservation in reservations if reservation.promise_id != hook_id
            ]
            if not self.state.promise_reservations[(zx, zy)]:
                self.state.promise_reservations.pop((zx, zy), None)
        return f"town: {spec.town_name}"

    def _cross_zone_edge(self, target_x: int, target_y: int) -> bool:
        """Step off the edge of the map to arrive at the corresponding edge of the neighboring zone."""
        state = self.state
        width, height = state.width, state.height
        new_zx, new_zy = state.zone_x, state.zone_y
        entry_x, entry_y = target_x, target_y
        crossed = False
        if target_x < 0:
            new_zx -= 1
            entry_x = width - 1
            crossed = True
        elif target_x >= width:
            new_zx += 1
            entry_x = 0
            crossed = True
        if target_y < 0:
            new_zy -= 1
            entry_y = height - 1
            crossed = True
        elif target_y >= height:
            new_zy += 1
            entry_y = 0
            crossed = True
        if not crossed:
            return False
        entry_x = max(0, min(width - 1, entry_x))
        entry_y = max(0, min(height - 1, entry_y))

        self._save_current_zone()
        state.zone_x, state.zone_y = new_zx, new_zy
        new_region_id = region_for_zone(new_zx, new_zy)
        region_changed = new_region_id != state.region_id
        state.region_id = new_region_id
        self._load_or_generate_zone(new_zx, new_zy, entry_x, entry_y)
        if region_changed:
            state.add_message(f"You cross into {self.region.name}. The air is different here.")
        else:
            state.add_message(f"You cross into new territory - the {state.zone_type} of zone ({new_zx}, {new_zy}).")
        return True

    def _save_current_zone(self) -> None:
        state = self.state
        state.zones[(state.zone_x, state.zone_y)] = ZoneSnapshot(
            tiles=[row[:] for row in state.tiles],
            tile_tags={key: list(value) for key, value in state.tile_tags.items()},
            tile_durations=dict(state.tile_durations),
            entities={
                entity_id: entity
                for entity_id, entity in state.entities.items()
                if entity_id != state.player_id
            },
            explored=set(state.explored),
            zone_type=state.zone_type,
        )

    # ------------------------------------------------------------------
    # Road network + town distribution
    # ------------------------------------------------------------------

    def _road_anchor(self, cx: int, cy: int) -> tuple[int, int]:
        """Deterministic anchor point for grid cell (cx, cy), cell size = 8 zones."""
        rng = random.Random((self.state.rng_seed or 0) * 1_000_003 + cx * 100_003 + cy * 9_999_991 + 1)
        return (cx * 8 + rng.randint(2, 5), cy * 8 + rng.randint(2, 5))

    def _zone_is_road(self, zx: int, zy: int) -> bool:
        """True if (zx, zy) lies on the Bresenham line between any pair of adjacent
        road-network anchors. Checks the zone's own 3x3 cell neighbourhood so no
        road segment longer than ~11 tiles can be missed."""
        cx, cy = math.floor(zx / 8), math.floor(zy / 8)
        for dcx in range(-1, 2):
            for dcy in range(-1, 2):
                a = self._road_anchor(cx + dcx, cy + dcy)
                for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    b = self._road_anchor(cx + dcx + ddx, cy + dcy + ddy)
                    if _on_bresenham(a, b, (zx, zy)):
                        return True
        return False

    def _road_edges(self, zx: int, zy: int) -> set[str]:
        """Which edges of zone (zx, zy) carry a road crossing into an adjacent zone."""
        edges: set[str] = set()
        if self._zone_is_road(zx, zy - 1):
            edges.add("north")
        if self._zone_is_road(zx, zy + 1):
            edges.add("south")
        if self._zone_is_road(zx - 1, zy):
            edges.add("west")
        if self._zone_is_road(zx + 1, zy):
            edges.add("east")
        return edges

    def _zone_should_be_town(self, zx: int, zy: int) -> bool:
        """Deterministic: ~30% chance on road zones, ~10% elsewhere."""
        rng = random.Random((self.state.rng_seed or 0) * 1_000_003 + zx * 73_856_093 + zy * 83_492_791 + 2)
        threshold = 0.30 if self._zone_is_road(zx, zy) else 0.10
        return rng.random() < threshold

    # ------------------------------------------------------------------
    # Background town pre-generation
    # ------------------------------------------------------------------

    def _build_town_context(self, zx: int, zy: int) -> dict:
        """Build a procedurally varied context dict for the town LLM prompt."""
        rng = random.Random((self.state.rng_seed or 0) * 1_000_003 + zx * 19_349_663 + zy * 83_492_791 + 3)
        location = rng.choice(_TOWN_LOCATIONS)
        defining_trait = rng.choice(_TOWN_DEFINING_TRAITS)
        current_situation = rng.choice(_TOWN_SITUATIONS)
        stype, npc_min, npc_max = rng.choice(_TOWN_SETTLEMENT_TYPES)
        context = {
            "zone": {"x": zx, "y": zy},
            "world_seed": self.state.rng_seed,
            "npc_count_range": [npc_min, npc_max],
            "location": location,
            "defining_trait": defining_trait,
            "current_situation": current_situation,
            "settlement_type": stype,
        }
        promise_hooks = self.promise_hooks_for_zone((zx, zy), limit=3)
        if promise_hooks:
            context["promise_hooks"] = promise_hooks
        return context

    def _maybe_pregenerate_adjacent_towns(self) -> None:
        """Submit background LLM generation for any adjacent unvisited town zones."""
        zx, zy = self.state.zone_x, self.state.zone_y
        for nx, ny in ((zx + 1, zy), (zx - 1, zy), (zx, zy + 1), (zx, zy - 1)):
            key = (nx, ny)
            if key in self.state.zones:
                continue
            if key in self._pending_towns:
                continue
            if not self._zone_should_be_town(nx, ny):
                continue
            if self._town_executor is None:
                self._town_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            ctx = self._build_town_context(nx, ny)
            self._pending_town_contexts[key] = ctx
            self._pending_town_start_times[key] = time.monotonic()
            self._pending_towns[key] = self._town_executor.submit(
                self.town_provider.generate, nx, ny, ctx
            )

    def _get_town_spec(self, zx: int, zy: int) -> tuple[Any, dict[str, Any]]:
        """Return TownSpec for (zx, zy) — from pending future or generate now. Never blocks >_TOWN_GEN_TIMEOUT."""
        from .wild_magic import MockTownProvider
        key = (zx, zy)
        if self._town_executor is None:
            self._town_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        if key not in self._pending_towns:
            ctx = self._build_town_context(zx, zy)
            self._pending_town_contexts[key] = ctx
            self._pending_town_start_times[key] = time.monotonic()
            self._pending_towns[key] = self._town_executor.submit(
                self.town_provider.generate, zx, zy, ctx
            )
        ctx = self._pending_town_contexts.get(key, {})
        future = self._pending_towns.pop(key)
        self._pending_town_contexts.pop(key, None)
        self._pending_town_start_times.pop(key, None)
        try:
            return future.result(timeout=_TOWN_GEN_TIMEOUT), ctx
        except Exception:
            return MockTownProvider().generate(zx, zy, ctx), ctx

    def _load_or_generate_zone(self, zx: int, zy: int, entry_x: int, entry_y: int) -> None:
        state = self.state
        player = state.entities[state.player_id]
        key = (zx, zy)
        state.entities = {}
        if key in state.zones:
            snapshot = state.zones[key]
            state.tiles = [row[:] for row in snapshot.tiles]
            state.tile_tags = {key_: list(value) for key_, value in snapshot.tile_tags.items()}
            state.tile_durations = dict(snapshot.tile_durations)
            state.explored = set(snapshot.explored)
            state.entities = dict(snapshot.entities)
            state.zone_type = snapshot.zone_type
        else:
            state.explored = set()
            if self._zone_should_be_town(zx, zy):
                spec, town_context = self._get_town_spec(zx, zy)
                state.zone_type = self._generate_llm_town(zx, zy, spec, town_context)
            else:
                state.zone_type = self._generate_open_zone(zx, zy)
        state.entities[player.id] = player
        player.x, player.y = self._find_entry_tile(entry_x, entry_y)
        state.visible.clear()
        self.update_fov()
        self._maybe_pregenerate_adjacent_towns()

    def _find_entry_tile(self, x: int, y: int) -> tuple[int, int]:
        if self.can_occupy(x, y):
            return x, y
        for radius in range(1, 6):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    nx, ny = x + dx, y + dy
                    if self.in_bounds(nx, ny) and self.can_occupy(nx, ny):
                        return nx, ny
        return x, y

    def _carve_room(self, room: Room) -> None:
        for y in range(room.y, room.y + room.h):
            for x in range(room.x, room.x + room.w):
                self.state.tiles[y][x] = FLOOR

    def _carve_corridor(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        x1, y1 = start
        x2, y2 = end
        if self.rng.random() < 0.5:
            self._carve_h_tunnel(x1, x2, y1)
            self._carve_v_tunnel(y1, y2, x2)
        else:
            self._carve_v_tunnel(y1, y2, x1)
            self._carve_h_tunnel(x1, x2, y2)

    def _carve_h_tunnel(self, x1: int, x2: int, y: int) -> None:
        for x in range(min(x1, x2), max(x1, x2) + 1):
            self.state.tiles[y][x] = FLOOR

    def _carve_v_tunnel(self, y1: int, y2: int, x: int) -> None:
        for y in range(min(y1, y2), max(y1, y2) + 1):
            self.state.tiles[y][x] = FLOOR

    def _mirror_room(self, room: Room, axis_x: int) -> Room:
        return Room(2 * axis_x - room.x - room.w + 1, room.y, room.w, room.h)

    def _carve_room_mirrored(self, room: Room, axis_x: int) -> Room:
        """Carve a room and its reflection across the vertical line x=axis_x. Returns the mirror."""
        self._carve_room(room)
        mirrored = self._mirror_room(room, axis_x)
        self._carve_room(mirrored)
        return mirrored

    def _carve_corridor_straight(
        self, start: tuple[int, int], end: tuple[int, int], horizontal_first: bool = True
    ) -> None:
        """Right-angle corridor with a fixed bend order (deterministic, so mirrors match exactly)."""
        x1, y1 = start
        x2, y2 = end
        if horizontal_first:
            self._carve_h_tunnel(x1, x2, y1)
            self._carve_v_tunnel(y1, y2, x2)
        else:
            self._carve_v_tunnel(y1, y2, x1)
            self._carve_h_tunnel(x1, x2, y2)

    def _carve_corridor_mirrored(
        self, start: tuple[int, int], end: tuple[int, int], axis_x: int, horizontal_first: bool = True
    ) -> None:
        self._carve_corridor_straight(start, end, horizontal_first)
        mirrored_start = (2 * axis_x - start[0], start[1])
        mirrored_end = (2 * axis_x - end[0], end[1])
        self._carve_corridor_straight(mirrored_start, mirrored_end, horizontal_first)

    def _place_doors_mirrored(self, axis_x: int, count: int = 4) -> None:
        """Find door candidates strictly right of the axis, then place each alongside its mirror."""
        candidates: list[tuple[int, int]] = []
        for y in range(1, self.state.height - 1):
            for x in range(axis_x + 1, self.state.width - 1):
                if self.state.tiles[y][x] != FLOOR:
                    continue
                horizontal_floor = self.state.tiles[y][x - 1] != WALL and self.state.tiles[y][x + 1] != WALL
                vertical_walls = self.state.tiles[y - 1][x] == WALL and self.state.tiles[y + 1][x] == WALL
                vertical_floor = self.state.tiles[y - 1][x] != WALL and self.state.tiles[y + 1][x] != WALL
                horizontal_walls = self.state.tiles[y][x - 1] == WALL and self.state.tiles[y][x + 1] == WALL
                if (horizontal_floor and vertical_walls) or (vertical_floor and horizontal_walls):
                    candidates.append((x, y))
        self.rng.shuffle(candidates)
        for x, y in candidates[:count]:
            self.state.tiles[y][x] = DOOR
            mirrored_x = 2 * axis_x - x
            if self.state.tiles[y][mirrored_x] == FLOOR:
                self.state.tiles[y][mirrored_x] = DOOR

    def _place_doors(self) -> None:
        candidates: list[tuple[int, int]] = []
        for y in range(1, self.state.height - 1):
            for x in range(1, self.state.width - 1):
                if self.state.tiles[y][x] != FLOOR:
                    continue
                if any(entity.x == x and entity.y == y for entity in self.state.entities.values()):
                    continue
                horizontal_floor = self.state.tiles[y][x - 1] != WALL and self.state.tiles[y][x + 1] != WALL
                vertical_walls = self.state.tiles[y - 1][x] == WALL and self.state.tiles[y + 1][x] == WALL
                vertical_floor = self.state.tiles[y - 1][x] != WALL and self.state.tiles[y + 1][x] != WALL
                horizontal_walls = self.state.tiles[y][x - 1] == WALL and self.state.tiles[y][x + 1] == WALL
                if (horizontal_floor and vertical_walls) or (vertical_floor and horizontal_walls):
                    candidates.append((x, y))
        self.rng.shuffle(candidates)
        for x, y in candidates[: max(2, min(8, len(candidates) // 5))]:
            self.state.tiles[y][x] = DOOR

    def _floor_reachable(self, start: tuple[int, int], goal: tuple[int, int], blocked: set[tuple[int, int]]) -> bool:
        queue: deque[tuple[int, int]] = deque([start])
        seen = {start}
        while queue:
            x, y = queue.popleft()
            if (x, y) == goal:
                return True
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in seen or (nx, ny) in blocked or not self.in_bounds(nx, ny):
                    continue
                tile = self.state.tiles[ny][nx]
                # Doors are always freely passable here -- the `blocked` set is how callers
                # model "what if this particular (locked) door were shut".
                if tile in BLOCKING_TILES and tile != DOOR:
                    continue
                seen.add((nx, ny))
                queue.append((nx, ny))
        return False

    def _place_locked_door(self, rooms: list[Room]) -> None:
        """Lock one door that gates progress toward the stairs, and stash its key upstream of it."""
        if len(rooms) < 3 or self.rng.random() > 0.5:
            return
        start = rooms[0].center
        goal = rooms[-1].center
        door_positions = [
            (x, y)
            for y, row in enumerate(self.state.tiles)
            for x, tile in enumerate(row)
            if tile == DOOR
        ]
        self.rng.shuffle(door_positions)
        for door_xy in door_positions:
            if self._floor_reachable(start, goal, blocked={door_xy}):
                continue
            key_name = self.rng.choice(LOCKED_DOOR_KEYS)
            key_id = f"key_{normalize_id(key_name)}"
            self.set_tile(door_xy[0], door_xy[1], DOOR, tags={"locked", key_id})
            reachable_rooms = [
                room for room in rooms[:-1]
                if self._floor_reachable(start, room.center, blocked={door_xy})
            ]
            key_room = self.rng.choice(reachable_rooms) if reachable_rooms else rooms[0]
            kx, ky = self._random_open_tile_in_room(key_room)
            self.spawn_item(key_name, "k", kx, ky, key_name)
            return

    def _random_open_tile_in_room(self, room: Room) -> tuple[int, int]:
        for _ in range(50):
            x = self.rng.randint(room.x, room.x + room.w - 1)
            y = self.rng.randint(room.y, room.y + room.h - 1)
            if self.can_occupy(x, y):
                return x, y
        return room.center

    def _floor_theme_weights(self, depth: int) -> dict[str, int]:
        themes = self.region.floor_themes
        for max_depth, weights in themes:
            if depth <= max_depth:
                return weights
        return themes[-1][1]

    def _pick_themed_prop_id(self, depth: int) -> str:
        weights = self._floor_theme_weights(depth)
        total = sum(weights.values())
        roll = self.rng.randint(1, total)
        cumulative = 0
        chosen_cat = next(iter(weights))
        for cat, w in weights.items():
            cumulative += w
            if roll <= cumulative:
                chosen_cat = cat
                break
        ids = PROP_CATEGORIES.get(chosen_cat, [])
        return self.rng.choice(ids) if ids else self.rng.choice(get_all_prop_ids())

    def _spawn_props_in_room(self, room: Room, depth: int, allow_scene: bool = True) -> None:
        if allow_scene and self.rng.random() < 0.20:
            # Scenes follow the region's theme gradient too: imperial pairings
            # where the Empire reaches, tradition pairings in the wild. Fall
            # back to any scene if the current themes have no complete pairing.
            weights = self._floor_theme_weights(depth)
            themed_ids = {pid for cat in weights for pid in PROP_CATEGORIES.get(cat, [])}
            themed_scenes = [s for s in _PROP_SCENES if all(pid in themed_ids for pid in s)]
            scene = self.rng.choice(themed_scenes or _PROP_SCENES)
            for pid in scene:
                template = get_prop_template(pid)
                if template:
                    x, y = self._random_open_tile_in_room(room)
                    self.spawn_prop(pid, x, y)
        else:
            count = 1 + (1 if self.rng.random() < 0.40 else 0) + (1 if self.rng.random() < 0.15 else 0)
            for _ in range(count):
                prop_id = self._pick_themed_prop_id(depth)
                x, y = self._random_open_tile_in_room(room)
                self.spawn_prop(prop_id, x, y)

    def _save_dungeon_floor(self, depth: int) -> None:
        state = self.state
        state.dungeon_floors[depth] = ZoneSnapshot(
            tiles=[row[:] for row in state.tiles],
            tile_tags={key: list(value) for key, value in state.tile_tags.items()},
            tile_durations=dict(state.tile_durations),
            entities={
                entity_id: entity
                for entity_id, entity in state.entities.items()
                if entity_id != state.player_id
            },
            explored=set(state.explored),
            zone_type="dungeon",
        )

    def _load_dungeon_floor(self, depth: int, entry_tile: str) -> None:
        state = self.state
        player = state.entities[state.player_id]
        snapshot = state.dungeon_floors[depth]
        state.tiles = [row[:] for row in snapshot.tiles]
        state.tile_tags = {key: list(value) for key, value in snapshot.tile_tags.items()}
        state.tile_durations = dict(snapshot.tile_durations)
        state.explored = set(snapshot.explored)
        state.entities = dict(snapshot.entities)
        state.zone_type = snapshot.zone_type
        
        entry_x, entry_y = player.x, player.y
        found = False
        for y, row in enumerate(state.tiles):
            for x, tile in enumerate(row):
                if tile == entry_tile:
                    entry_x, entry_y = x, y
                    found = True
                    break
            if found:
                break
        
        state.entities[player.id] = player
        player.x, player.y = entry_x, entry_y
