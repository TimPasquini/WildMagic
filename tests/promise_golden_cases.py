"""Golden binding cases for the Promise Ledger — written BEFORE implementation as the
executable spec for M2's binding tests (docs/WORLD_PROMISES.md, "promise_eval" cases).

Each case: extraction-shaped input -> expected binding outcome, expressed as plain dicts
so the test layer (not this file) adapts to the final dataclass shapes.

Conventions assumed by these cases:
- utterance zone is (0, 0) unless stated
- `explored` lists zones already generated (binding must avoid them)
- expected["space"] uses SpatialHint fields: mode zone|direction|terrain|wildcard
- expected None means: no binding — the claim stays flavor lore
"""

GOLDEN_BINDING_CASES = [
    {
        "name": "chapel_north_of_town",
        "claim": {
            "text": "There is a chapel north of town.",
            "tags": ["chapel"],
            "where": "north of town",
            "what": "chapel",
            "salience": 4,
            "confidence": 0.7,
        },
        "explored": [(0, 0)],
        "expected": {
            "space": {"mode": "direction", "direction": (0, -1), "anchor_zone": (0, 0)},
            "bound_zone": (0, -1),
            "blueprint": "sacred_site",
        },
    },
    {
        "name": "bandit_camp_east",
        "claim": {
            "text": "Bandits have a camp east of here.",
            "tags": ["bandits", "camp"],
            "where": "east",
            "what": "bandit camp",
            "salience": 4,
            "confidence": 0.7,
        },
        "explored": [(0, 0)],
        "expected": {
            "space": {"mode": "direction", "direction": (1, 0), "anchor_zone": (0, 0)},
            "bound_zone": (1, 0),
            "blueprint": "hostile_site",
        },
    },
    {
        "name": "witch_in_the_woods",
        "claim": {
            "text": "A witch lives somewhere in the woods.",
            "tags": ["witch", "woods"],
            "where": "in the woods",
            "what": "witch",
            "salience": 4,
            "confidence": 0.6,
        },
        "explored": [(0, 0)],
        "expected": {
            "space": {"mode": "terrain", "terrain_tag": "forest"},
            "blueprint": "inhabited_site",
            "npc_bound": True,
        },
    },
    {
        "name": "barrow_no_location",
        "claim": {
            "text": "They buried the old king in a barrow, they say.",
            "tags": ["barrow", "tomb"],
            "where": None,
            "what": "barrow",
            "salience": 3,
            "confidence": 0.5,
        },
        "explored": [(0, 0)],
        "expected": {
            "space": {"mode": "wildcard"},
            "blueprint": "memorial_site",
        },
    },
    {
        "name": "smugglers_cache",
        "claim": {
            "text": "Smugglers keep a cache near the south road.",
            "tags": ["cache", "smugglers"],
            "where": "south",
            "what": "cache",
            "salience": 3,
            "confidence": 0.6,
        },
        "explored": [(0, 0)],
        "expected": {
            "space": {"mode": "direction", "direction": (0, 1), "anchor_zone": (0, 0)},
            "bound_zone": (0, 1),
            "blueprint": "hidden_site",
        },
    },
    {
        "name": "poetic_claim_stays_flavor",
        "claim": {
            "text": "The moon forgets this valley every third night.",
            "tags": ["moon", "valley"],
            "where": None,
            "what": None,
            "salience": 2,
            "confidence": 0.5,
        },
        "explored": [(0, 0)],
        "expected": None,  # no blueprint match -> flavor lore, never realizes
    },
    {
        "name": "low_confidence_stays_flavor",
        "claim": {
            "text": "Maybe there's a shrine north? I really couldn't say.",
            "tags": ["shrine"],
            "where": "north",
            "what": "shrine",
            "salience": 2,
            "confidence": 0.3,
        },
        "explored": [(0, 0)],
        "expected": None,  # confidence floor (~0.4)
    },
    {
        "name": "explored_target_relocates",
        "claim": {
            "text": "There is a chapel north of town.",
            "tags": ["chapel"],
            "where": "north of town",
            "what": "chapel",
            "salience": 4,
            "confidence": 0.7,
        },
        "explored": [(0, 0), (0, -1)],  # the claimed zone is already generated
        "expected": {
            # claimed_space records what was said; bound_space relocates further north.
            "space": {"mode": "direction", "direction": (0, -1), "anchor_zone": (0, 0)},
            "bound_zone": (0, -2),
            "blueprint": "sacred_site",
            "relocated": True,
        },
    },
]
