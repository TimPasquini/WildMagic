from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .game_data import WILD_ENEMY_TEMPLATES

# A region bundles everything about "where you are": the narrative voice fed to
# the LLM, the creatures and props that spawn, the ambient lines the place
# speaks in, and how strange reality is allowed to get. Regions are geography
# (the overworld zone grid maps onto them); wildness is an orthogonal axis —
# effective wildness = region.wildness_base + dungeon depth — so any region
# gets stranger the deeper you go, and some regions start strange.
#
# Adding a region should mean adding an entry to REGIONS, not touching engine
# code. See docs/EXECUTION_PLAN.md Phase 13 and docs/AESTHETICS_AND_TONE.md.

EnemyTemplate = tuple[
    str, str, int, int, int, str, set[str], dict[str, int], dict[str, int]
]


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    # -- Voice (style line + example swap, injected into the wild-magic prompt) --
    voice: str
    example_outcomes: tuple[str, ...]
    # -- Population --
    enemy_templates: tuple[EnemyTemplate, ...]
    # Probability that an enemy spawn draws from the imperial legion pool
    # instead of the region's own bestiary. Also gates how reliably the
    # Censorate's posted notices appear.
    imperial_presence: float
    # -- Places: (max_depth_inclusive, {prop_category: weight}); first match wins --
    floor_themes: tuple[tuple[int, dict[str, int]], ...]
    # -- Ambience --
    ambient_by_tag: dict[str, tuple[str, ...]]
    ambient_default: tuple[str, ...]
    # (max_wildness_inclusive, lines); first match wins. Spoken when no threat
    # is near — the place itself talking.
    wonder_by_wildness: tuple[tuple[int, tuple[str, ...]], ...]
    # -- The strangeness axis --
    wildness_base: int = 0

    def effective_wildness(self, depth: int) -> int:
        return self.wildness_base + max(0, depth)

    def wonder_lines(self, depth: int) -> tuple[str, ...]:
        wildness = self.effective_wildness(depth)
        for threshold, lines in self.wonder_by_wildness:
            if wildness <= threshold:
                return lines
        return self.wonder_by_wildness[-1][1]

    def prompt_style(self) -> dict[str, Any]:
        """The slice of the region the wild-magic prompt builder consumes."""
        return {
            "name": self.name,
            "voice": self.voice,
            "examples": list(self.example_outcomes),
        }


# Creature sounds shared across regions; a region can override per tag.
_COMMON_SOUNDS_BY_TAG: dict[str, tuple[str, ...]] = {
    "undead": (
        "Somewhere unseen, dry joints click in a slow rhythm.",
        "A voice hums a lullaby with no breath behind it.",
        "Dust sifts from the ceiling in time with footsteps that are not yours.",
    ),
    "beast": (
        "Claws click on stone somewhere, unhurried.",
        "Something large yawns in the dark and settles again.",
        "You hear sniffing, then a thoughtful pause.",
    ),
    "slime": (
        "A wet gurgle sounds in the distance, almost musical.",
        "Something drips upward, once.",
        "You hear a slow, patient sliding.",
    ),
    "spider": (
        "Silk creaks like ship's rigging somewhere overhead.",
        "You hear many legs cross the ceiling, perfectly in step.",
        "A plucked strand of web rings a single soft note.",
    ),
    "construct": (
        "Gears turn somewhere unseen, patient as a clock.",
        "A low hum swells and fades, like something remembering its purpose.",
        "Metal settles with a satisfied tick.",
    ),
    "shadow": (
        "The light here leans away from one corner.",
        "Something cold is counting your footsteps.",
        "Your shadow arrives half a step late.",
    ),
    "empire": (
        "You hear the rhythmic stamp of boots in unison.",
        "A horn sounds three precise notes, then falls silent.",
        "Iron scrapes against iron in perfect time.",
    ),
}


_FRONTIER = Region(
    id="frontier",
    name="the Hollowmere frontier",
    voice=(
        "This is frontier country under imperial eyes: hedgerows, market roads, old shrines, "
        "buried strata of older magic below. Keep outcomes earthy and vivid -- wonder with mud on its boots."
    ),
    example_outcomes=(
        "The hedge-wind takes your spell and runs with it, laughing through the brambles.",
        "Sparks settle over the old road like seeds deciding where to land.",
        "The shrine-stones lean in to watch, and the moss between them glows ember-orange.",
    ),
    enemy_templates=tuple(WILD_ENEMY_TEMPLATES),
    imperial_presence=0.3,
    floor_themes=(
        (2, {"imperial": 4, "infrastructure": 3, "ruined": 2, "furniture": 1}),
        (
            4,
            {
                "ruined": 3,
                "natural": 3,
                "traditions": 2,
                "infrastructure": 1,
                "imperial": 1,
            },
        ),
        (
            6,
            {
                "traditions": 3,
                "arcane": 3,
                "natural": 2,
                "alchemical": 1,
                "religious": 1,
            },
        ),
        (
            999,
            {
                "arcane": 4,
                "traditions": 3,
                "religious": 2,
                "alchemical": 2,
                "natural": 2,
            },
        ),
    ),
    ambient_by_tag=dict(_COMMON_SOUNDS_BY_TAG),
    ambient_default=(
        "Something moves in the dark, curious.",
        "The deep places are listening, politely.",
        "Far off, water finds a new way down.",
    ),
    wonder_by_wildness=(
        (
            2,
            (
                "A draft carries a smell of lamp oil and fresh paper.",
                "Chalk survey lines cross the floor and stop mid-stroke.",
                "Somewhere above, a bell rings the hour, exactly.",
            ),
        ),
        (
            4,
            (
                "Somewhere above, a market bell rings on the wrong day.",
                "The air tastes faintly of spice and coming rain.",
                "A snatch of song arrives from no particular direction.",
            ),
        ),
        (
            6,
            (
                "The walls hold yesterday's light a moment too long.",
                "A breeze passes, carrying pollen from nothing that grows here.",
                "Very faintly, you hear applause.",
            ),
        ),
        (
            999,
            (
                "The stone underfoot is warm, like something sleeping.",
                "The colors at the edge of your vision rearrange themselves when you turn.",
                "The dark ahead hums a note you almost know.",
            ),
        ),
    ),
    wildness_base=0,
)


_GLASSWILD = Region(
    id="glasswild",
    name="the Glasswild",
    voice=(
        "This is the Glasswild, deep wild country: dreamlike, gently impossible, jewel-bright. "
        "Light lingers, glass grows, distances disagree -- describe outcomes with strange, vivid beauty."
    ),
    example_outcomes=(
        "The spell blooms into a stand of singing glass, each stalk holding a different hour of light.",
        "Your magic pours uphill, delighted, and the moss turns every color it knows.",
        "The wound in the air heals over with crystal, humming your name back at you.",
    ),
    enemy_templates=(
        (
            "glass stag",
            "S",
            12,
            3,
            1,
            "simple",
            {"beast", "glass", "fragile"},
            {"poison": 50},
            {"force": 50},
        ),
        (
            "chime swarm",
            "w",
            6,
            2,
            0,
            "bat",
            {"swarm", "music", "magic"},
            {"physical": 25},
            {"force": 25},
        ),
        (
            "prism serpent",
            "j",
            9,
            3,
            0,
            "simple",
            {"beast", "crystal", "light"},
            {"radiant": 50},
            {"shadow": 25},
        ),
        (
            "dream-fed slime",
            "s",
            11,
            2,
            1,
            "slime",
            {"slime", "magic"},
            {"poison": 50},
            {"frost": 25},
        ),
        (
            "hollow chorister",
            "h",
            7,
            3,
            0,
            "simple",
            {"spirit", "music", "undead"},
            {"physical": 25, "poison": 100},
            {"radiant": 50},
        ),
        (
            "loam shepherd",
            "n",
            14,
            3,
            3,
            "simple",
            {"construct", "plant", "stationary"},
            {"poison": 100},
            {"fire": 50},
        ),
        (
            "hare of hours",
            "r",
            5,
            2,
            0,
            "bat",
            {"beast", "magic", "swift"},
            {},
            {"frost": 25},
        ),
        (
            "bramble cantor",
            "v",
            8,
            2,
            0,
            "goblin",
            {"plant", "music", "caster", "summoner"},
            {"poison": 50},
            {"fire": 25},
        ),
    ),
    imperial_presence=0.05,
    floor_themes=((999, {"arcane": 4, "traditions": 4, "natural": 3, "religious": 1}),),
    ambient_by_tag={
        **_COMMON_SOUNDS_BY_TAG,
        "beast": (
            "Hooves of glass ring once on stone, far off, like a struck bell.",
            "Something many-antlered moves between here and elsewhere.",
            "You hear grazing. There is nothing to graze on. There is now.",
        ),
        "music": (
            "A chord assembles itself from the dripping water.",
            "Something is tuning the air.",
            "The echo of your last step comes back harmonized.",
        ),
    },
    ambient_default=(
        "The Glasswild rearranges something, out of politeness, while you are not looking.",
        "A bright thread of birdsong unspools from underground.",
        "Petals drift past. There are no flowers. The petals seem unbothered.",
    ),
    wonder_by_wildness=(
        (
            999,
            (
                "A second horizon shows briefly above the first, then thinks better of it.",
                "Glass grows here. You can hear it practicing.",
                "Your footprints fill with pale light, then wander off on their own.",
                "Somewhere near, a festival is being remembered by the stones.",
            ),
        ),
    ),
    wildness_base=6,
)


_SALTMARKET = Region(
    id="saltmarket",
    name="the Saltmarket",
    voice=(
        "This is the Saltmarket, a jewel-toned bazaar that runs on barter and rumor: awnings, "
        "spice-smoke, coin and curio. Keep outcomes lush, mercantile, and crowded -- wonder you "
        "could haggle over, magic that smells of saffron and hot brass."
    ),
    example_outcomes=(
        "The spell unrolls like a bolt of impossible cloth, and three merchants are already pricing it.",
        "Coins leap from a dozen purses and orbit your hand, deciding whether they like you.",
        "Where it lands, the cobbles bloom into a stall selling the smell of rain.",
    ),
    enemy_templates=(
        (
            "cutpurse",
            "g",
            8,
            3,
            0,
            "goblin",
            {"humanoid", "thief", "flesh"},
            {},
            {},
        ),
        (
            "tariff-wraith",
            "w",
            7,
            3,
            0,
            "bat",
            {"spirit", "empire", "swift"},
            {"physical": 25},
            {"radiant": 25},
        ),
        (
            "haggling imp",
            "i",
            6,
            2,
            0,
            "goblin",
            {"fiend", "trickster", "caster"},
            {"fire": 25},
            {"radiant": 25},
        ),
        (
            "coin-glutton slime",
            "s",
            11,
            2,
            1,
            "slime",
            {"slime", "magic", "greedy"},
            {"poison": 50},
            {"frost": 25},
        ),
        (
            "spice-drunk brawler",
            "b",
            12,
            4,
            1,
            "simple",
            {"humanoid", "flesh", "brawler"},
            {},
            {"frost": 25},
        ),
        (
            "carpet serpent",
            "j",
            9,
            3,
            0,
            "simple",
            {"beast", "swift", "woven"},
            {"poison": 25},
            {"fire": 50},
        ),
        (
            "ledger-golem",
            "n",
            14,
            3,
            3,
            "stationary",
            {"construct", "empire", "stationary"},
            {"physical": 25, "poison": 100},
            {"force": 50},
        ),
        (
            "rumor-swarm",
            "v",
            5,
            2,
            0,
            "bat",
            {"swarm", "spirit", "swift"},
            {},
            {"force": 25},
        ),
    ),
    imperial_presence=0.25,
    floor_themes=(
        (
            2,
            {
                "saltmarket": 5,
                "furniture": 3,
                "infrastructure": 2,
                "imperial": 2,
                "alchemical": 1,
            },
        ),
        (
            4,
            {
                "saltmarket": 3,
                "alchemical": 3,
                "arcane": 3,
                "furniture": 2,
                "traditions": 2,
                "imperial": 1,
            },
        ),
        (
            999,
            {
                "arcane": 4,
                "saltmarket": 2,
                "traditions": 3,
                "alchemical": 2,
                "religious": 2,
                "natural": 1,
            },
        ),
    ),
    ambient_by_tag={
        **_COMMON_SOUNDS_BY_TAG,
        "empire": (
            "A tax-clerk's bell rings twice, and a hundred stalls go quiet at once.",
            "Somewhere, a seal is being stamped with great patience.",
            "You hear the soft scratch of a ledger pen, closer than it should be.",
        ),
        "thief": (
            "A purse-string parts somewhere with a sound like a plucked harp.",
            "Footsteps match yours for three beats, then are gone.",
            "Coins clink, very deliberately, behind you.",
        ),
    },
    ambient_default=(
        "Haggling rises and falls like surf, never quite stopping.",
        "Spice-smoke drifts past, carrying the names of far ports.",
        "Someone, somewhere, swears they have exactly what you need.",
    ),
    wonder_by_wildness=(
        (
            2,
            (
                "An awning's shadow spells out a fair price, then thinks better of it.",
                "The smell of saffron and hot brass settles over everything.",
                "A merchant offers to sell you back the time you just spent looking.",
            ),
        ),
        (
            4,
            (
                "A stall appears between two others that were always adjacent before.",
                "Your reflection in a brass tray is busy bargaining without you.",
                "Coins in the gutter arrange themselves into a small, hopeful pile.",
            ),
        ),
        (
            999,
            (
                "The bazaar folds a street into your pocket as a free sample.",
                "Every price tag here is written in a language you only read when hungry.",
                "A caravan passes selling distances; the far wall is suddenly nearer.",
            ),
        ),
    ),
    wildness_base=1,
)


_WARREN = Region(
    id="warren",
    name="the Warren",
    voice=(
        "This is the Warren, a packed honeycomb of small rooms gnawed into older rooms -- buried "
        "strata, hoarded junk, things nesting in things. Keep outcomes close, cluttered, and "
        "tactile: wonder in a tight space, magic that knocks the shelves over."
    ),
    example_outcomes=(
        "The spell ricochets off four close walls and comes back wearing some of the room.",
        "Dust and old coins leap from every shelf at once, briefly a small bright storm.",
        "The wall gives up a doorway it had been hiding, embarrassed to be caught.",
    ),
    enemy_templates=(
        (
            "warren rat",
            "r",
            4,
            2,
            0,
            "bat",
            {"beast", "vermin", "swift"},
            {"poison": 50},
            {},
        ),
        (
            "hoarder goblin",
            "g",
            9,
            3,
            0,
            "goblin",
            {"goblin", "humanoid", "flesh", "thief"},
            {},
            {},
        ),
        (
            "den slime",
            "s",
            11,
            2,
            1,
            "slime",
            {"slime", "ash"},
            {"poison": 50, "fire": 25},
            {"frost": 25},
        ),
        (
            "shelf spider",
            "x",
            7,
            3,
            0,
            "simple",
            {"beast", "spider", "swift"},
            {"poison": 25},
            {"fire": 50},
        ),
        (
            "midden lurker",
            "h",
            10,
            3,
            1,
            "simple",
            {"humanoid", "flesh", "ambusher"},
            {},
            {"radiant": 25},
        ),
        (
            "rubble crawler",
            "c",
            8,
            2,
            2,
            "simple",
            {"beast", "stone", "armored"},
            {"physical": 25, "poison": 100},
            {"force": 50},
        ),
        (
            "packrat swarm",
            "v",
            5,
            2,
            0,
            "bat",
            {"swarm", "vermin", "swift"},
            {},
            {"force": 25},
        ),
        (
            "bone skeleton",
            "k",
            7,
            3,
            1,
            "simple",
            {"undead", "bone"},
            {"poison": 100, "frost": 50},
            {"radiant": 50},
        ),
    ),
    imperial_presence=0.1,
    floor_themes=(
        (2, {"ruined": 4, "furniture": 3, "infrastructure": 2, "natural": 1}),
        (4, {"ruined": 3, "traditions": 3, "furniture": 2, "natural": 2, "arcane": 1}),
        (
            999,
            {"traditions": 4, "arcane": 3, "ruined": 2, "religious": 2, "natural": 1},
        ),
    ),
    ambient_by_tag={
        **_COMMON_SOUNDS_BY_TAG,
        "vermin": (
            "Small claws scrabble through the wall at your elbow, both ways at once.",
            "Something behind the shelves redistributes its hoard, item by item.",
            "A trickle of dust marks where the ceiling is thinking it over.",
        ),
    },
    ambient_default=(
        "The close air smells of old paper, rat, and rust.",
        "Two rooms over, something settles with a contented clatter.",
        "The walls are near enough that your breath comes back to you.",
    ),
    wonder_by_wildness=(
        (
            3,
            (
                "A doorway you are sure you used is brick now, and politely so.",
                "The junk on a shelf is arranged by a logic you almost recognize.",
                "Your shadow is too big for this room and has to stoop.",
            ),
        ),
        (
            6,
            (
                "The next room is the last room again, refurnished from memory.",
                "Something has been collecting the sounds you make and stacking them neatly.",
                "A draft carries the smell of a market that is many floors and years away.",
            ),
        ),
        (
            999,
            (
                "The Warren grows a room around you while you blink, apologetic.",
                "Every door here leads to the same room, which is fine with the room.",
                "The hoard rearranges into your own face, briefly, then loses interest.",
            ),
        ),
    ),
    wildness_base=2,
)


_STACKS = Region(
    id="stacks",
    name="the Foxed Stacks",
    voice=(
        "This is the Foxed Stacks, a hill-town drowning in hoarded books: reading-rooms, ladders, "
        "marginalia, scholars who have forgotten to leave. Keep outcomes literate and investigative "
        "-- wonder that wants to be footnoted, magic that smells of foxed paper and lamp oil."
    ),
    example_outcomes=(
        "The spell prints itself across the air in a fair scholar's hand, complete with one wrong citation.",
        "Every book in reach opens to the same page, eager to be of use.",
        "The dust rises into a diagram of exactly what you meant, then asks a follow-up question.",
    ),
    enemy_templates=(
        (
            "errata wisp",
            "w",
            5,
            2,
            0,
            "bat",
            {"spirit", "magic", "swift"},
            {},
            {"force": 25},
        ),
        (
            "ink-blot horror",
            "s",
            10,
            3,
            0,
            "slime",
            {"slime", "magic", "ink"},
            {"poison": 50, "shadow": 50},
            {"radiant": 50},
        ),
        (
            "censor's enforcer",
            "l",
            10,
            4,
            1,
            "legion",
            {"empire", "human", "soldier", "disciplined"},
            {"physical": 15},
            {"force": 25},
        ),
        (
            "paper wyrm",
            "j",
            8,
            3,
            0,
            "simple",
            {"beast", "paper", "woven"},
            {"poison": 25},
            {"fire": 75},
        ),
        (
            "marginalia imp",
            "i",
            6,
            2,
            0,
            "goblin",
            {"fiend", "trickster", "caster"},
            {},
            {"radiant": 25},
        ),
        (
            "silence warden",
            "h",
            9,
            3,
            1,
            "simple",
            {"spirit", "undead", "shadow"},
            {"physical": 25, "poison": 100},
            {"radiant": 50},
        ),
        (
            "foxed-folio swarm",
            "v",
            5,
            2,
            0,
            "bat",
            {"swarm", "paper", "swift"},
            {},
            {"fire": 75},
        ),
        (
            "footnote golem",
            "n",
            13,
            3,
            3,
            "stationary",
            {"construct", "paper", "stationary"},
            {"physical": 25, "poison": 100},
            {"fire": 75},
        ),
    ),
    imperial_presence=0.2,
    floor_themes=(
        (2, {"furniture": 4, "arcane": 2, "imperial": 2, "religious": 1}),
        (
            4,
            {
                "arcane": 3,
                "furniture": 3,
                "traditions": 2,
                "religious": 1,
                "imperial": 1,
            },
        ),
        (
            999,
            {
                "arcane": 4,
                "traditions": 3,
                "religious": 2,
                "furniture": 2,
                "alchemical": 1,
            },
        ),
    ),
    ambient_by_tag={
        **_COMMON_SOUNDS_BY_TAG,
        "paper": (
            "Pages turn somewhere with no hand and no hurry.",
            "A quill scratches a margin you cannot find.",
            "The smell of foxed paper thickens, as if a great book just opened.",
        ),
        "empire": (
            "A censor's seal closes somewhere with a sound like a held breath.",
            "You hear a careful page being removed from a binding.",
            "Lamp oil and brass: the reading-room is being inspected.",
        ),
    },
    ambient_default=(
        "The hush here is the deliberate kind, kept by everything at once.",
        "Lamp oil and old vellum hang in the still air.",
        "Somewhere, a reader who never leaves turns another page.",
    ),
    wonder_by_wildness=(
        (
            2,
            (
                "A book leans off its shelf to be noticed, then settles when you look.",
                "Dust motes hold the shape of a sentence, briefly legible.",
                "A margin note in the air finishes the thought you hadn't.",
            ),
        ),
        (
            4,
            (
                "The catalogue here lists a book you are about to need.",
                "Your footnotes have footnotes; you can hear them disagreeing.",
                "A reading-lamp lights itself over the one shelf that matters.",
            ),
        ),
        (
            999,
            (
                "The Stacks reshelve the room around you by a system only they know.",
                "Every book is the same book, read by someone who became the binding.",
                "The marginalia have started writing about you, and the hand is kind.",
            ),
        ),
    ),
    wildness_base=2,
)


REGIONS: dict[str, Region] = {
    _FRONTIER.id: _FRONTIER,
    _GLASSWILD.id: _GLASSWILD,
    _SALTMARKET.id: _SALTMARKET,
    _WARREN.id: _WARREN,
    _STACKS.id: _STACKS,
}

DEFAULT_REGION_ID = _FRONTIER.id


def get_region(region_id: str | None) -> Region:
    return REGIONS.get(region_id or "", REGIONS[DEFAULT_REGION_ID])


def region_for_zone(zx: int, zy: int, world_map: Any | None = None) -> str:
    """Geography: the frontier holds wherever the imperial road network reaches;
    the deep wild begins past it. Crude ring for now — a real region map can
    replace this without touching callers."""
    if world_map is not None:
        role = world_map.role_at(zx, zy) if world_map.contains(zx, zy) else None
        if role == "rival" or role is None:
            return _GLASSWILD.id
        return _FRONTIER.id
    return _GLASSWILD.id if abs(zx) + abs(zy) >= 3 else _FRONTIER.id
