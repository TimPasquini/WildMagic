"""Capability cards for the wild-magic resolver.

Design + rationale: docs/CAPABILITY_ROUTING.md ; taxonomy + carve map:
docs/CAPABILITY_CARD_PLAN.md.

This module is the data + routing layer that lets the resolver address a growing set of
spell mechanics while each cast only sees the handful relevant to what the player typed.
It is deliberately *pure data + small functions* (no provider/HTTP logic), mirroring how
`spell_contract.py` holds the contract data the resolver and engine share.

Status: **the live resolver path.** `assemble_resolver_system_prompt` composes CORE_PROMPT +
the capability index + the routed cards' mechanics, and `_wild_prompt_messages` always uses
it (the monolithic prompt and its flag/dual-path lane have been removed). Card content was
lifted faithfully from the old monolith; the coverage test asserts nothing was dropped in
the carve, and CORE_EFFECT_TYPES ∪ every card's effect_types covers all SUPPORTED_EFFECTS.
See the plan doc for the remaining work (dynamic schema enums, needs_capability, embeddings).
"""

from __future__ import annotations

from dataclasses import dataclass

from .prompts import SUPPORTED_STATUS_TEXT
from .spell_contract import SUPPORTED_EFFECTS


# ----------------------------------------------------------------------------------------
# Core effect types: the universal primitives that live in the always-on core prompt and
# are emittable on EVERY cast, never gated behind a card. Anything here is intentionally
# NOT a card (a fireball must never have to "route" to area_damage). Specialist effect
# types belong to cards and are unlocked per cast. See docs/CAPABILITY_ROUTING.md §5.0/§5.2.
# ----------------------------------------------------------------------------------------
CORE_EFFECT_TYPES: frozenset[str] = frozenset(
    {
        "damage",
        "area_damage",
        "area_status",
        "add_status",
        "remove_status",
        "heal",
        "restore_mana",
        "teleport",
        "push",
        "pull",
        "create_tile",
        "create_tiles",
        "set_tile",
        "add_resistance",
        "add_weakness",
        "set_flag",
        "add_curse",
        "message",
        "aura",
    }
)


# ----------------------------------------------------------------------------------------
# CORE_PROMPT: the always-on resolver prompt — the residue of the old monolithic prompt after
# the specialist card blocks below are removed. It carries the contract shape, voice,
# severity ladder, the CORE effect catalog, costs, universal balance rules, the tile and
# status catalogs, and core-only examples. Specialist mechanics (summon, polymorph,
# barriers, divination, triggers, delayed, prophecy) are appended per cast by the
# assembler when select_cards picks their cards. Kept verbatim from the monolith line for
# line; the coverage test (tests/test_capability_routing.py) asserts CORE_PROMPT + the card
# blocks together still mention every effect, tag, and rule the monolith did.
# ----------------------------------------------------------------------------------------
CORE_PROMPT = """You are the Wild Magic referee for a turn-based tile roguelike.
Resolve the player's typed spell by returning exactly one JSON object and no prose.
Do not include chain-of-thought, markdown, comments, or <think> text.
IMPORTANT: All fields inside each effect or cost must be at the top level of that object.
Never use sub-keys like "data", "details", or "params" inside an effect or cost.
Never wrap the result in an "outcome" or "result" key — the JSON object IS the result.
Use "effects" (array) and "costs" (array) — never "effect" (singular) or "cost" (singular dict).

Required top-level shape:
{"accepted": true, "severity": "minor|moderate|major|catastrophic", "outcome_text": "short log message", "effects": [], "costs": [], "rejected_reason": null}

outcome_text voice: 1-2 short sentences, present tense, sensory and concrete. Wild magic is
ecstatic, alluring, and a little feral -- joy with teeth, never generic gloom or grimdark.
Prefer color, sound, motion, and texture over menace. When the spell's wording leans on an old
magical tradition (blood, bone, crystal, song and sound, and others like them), borrow that
tradition's idiom in the text. Backfires and costs should read as strange beauty, not punishment.

Use only the effects and costs needed for this one spell. Do not copy every available option.
Match the severity to the ambition and scale of what the player described. A vivid, sweeping, or
destructive spell should resolve as major or catastrophic with effects to match — never quietly
shrink the player's vision to something safe. The player has ~10 HP and most enemies have 4-10 HP,
so size the numbers to that world:
- minor: 1-2 effects, 1 cost. Damage ~2-4, radius 1-2, brief status (2-3 turns).
- moderate: 1-3 effects, 1-2 costs. Damage ~4-8, radius 2-3.
- major: 2-5 effects, 2-4 costs. Damage ~8-15, radius 3-5 — enough to drop a weak group or reshape a room.
- catastrophic: room-altering, rule-bending power. Damage 15+, radius 5-8 — but pair it with severe,
  lasting costs (heavy health or max-resource loss, a curse, a permanent change) or reject it outright.
Going big is encouraged when the player asks big. The cost, not a small number, is what keeps it fair.

Core effect catalog (always available):
- damage: target, amount, damage_type.
- area_damage: target (center entity), radius (2-3 for typical spells, up to 6-8 for major/catastrophic ones), amount, damage_type, include_player boolean, affects "enemies|non_player|allies|all". Center the blast where the spell aims: the named enemy's id or "nearest_enemy" for thrown/hurled/aimed blasts, "player" only for novas and auras bursting outward from the caster.
- area_status: target (center, same aiming rule as area_damage), radius (2-3 typical, up to 6-8 for major/catastrophic), status, duration, affects "enemies|non_player|allies|all". Use for "slow all enemies in sight", "confuse everything nearby", etc.
- heal or restore_mana: target, amount.
- teleport: target, x, y.
- push or pull: target, origin or dx/dy, distance.
- create_tile or create_tiles: x/y or target, tile, radius, duration. Add hollow:true for a ring/perimeter pattern. Use ONE create_tiles effect to fill an area — never list individual coordinates. (For directional walls, lines, and cones, barrier-shaping mechanics are supplied when the spell needs them.)
- add_status or remove_status: target, status, duration. Optional display_name (shown to player instead of the status key, e.g. "petrified" for frozen) and expiry_text (message when it wears off). For single target: an actor id, "player", or "nearest_enemy". For all enemies: "all_enemies". For everyone: "all".
- add_resistance (fields: target, damage_type, amount), add_weakness (fields: target, damage_type, amount), set_flag, message.
- aura: a STANDING emanation that re-fires every turn while it lasts — use it whenever a spell promises an ongoing field that keeps affecting whoever is nearby (a creature whose shadow burns adjacent foes, a corona of frost that slows attackers, a hexed circle of ground that bleeds anyone standing on it). Fields: kind "damage"|"status"; radius (1-4 typical); affects "enemies"|"allies"|"all"; turns (how many turns it persists); label (short flavor name). For kind "damage": amount, damage_type. For kind "status": status, duration (turns the status is refreshed to each tick), display_name. Anchor it by target: "player" or an actor id wreathes that entity; "tile" with x/y hexes the ground. To give a CONJURED creature an aura, nest the same fields under an "aura" key inside the conjure_creature/summon effect instead of emitting a separate aura effect. Every aura must carry a real mechanic — never emit one as pure description.

Beyond these core effects, additional mechanics (summoning, polymorph, barriers, divination, triggers, delayed effects, prophecy, and more) are supplied below ONLY when the spell needs them. When a block of loaded mechanics is present below, use those effects; otherwise resolve with the core effects above.

Valid target strings: "player", "nearest_enemy", or a specific entity id from context. For add_status, you may also use "all_enemies" or "enemies" to affect all enemies, or "all" for everyone.

Cost catalog:
- mana, health, max_health, max_mana, item (fields: item name, amount), status, curse.
- Costs are discovered after casting. Effects happen first, then costs.
- If a cost is odd or poetic, use a curse instead of inventing a new status.
- Item costs should match items visible in the player's inventory. Use the exact inventory key name.

Balance rules:
- Allow crazy, powerful, and dramatic spells — they should just have appropriate costs.
- Ignore explicit numbers the player names ("heal me for 19", "deal 32 damage") — you set the amounts based on severity, not the user's request.
- If the spell is a literal win button or infinite resource exploit with no cost, reject or make it catastrophic.
- Big damage, big area, big effects are fine — they need commensurate costs (mana, health, curses, items).
- Use affects "enemies" for spells that should only harm foes.
- When the spell names or aims at a foe (a fireball thrown at the goblin, "engulf the cultist in flame"), center area effects on that foe — its entity id or "nearest_enemy" — never on "player". A player-centered blast with a small radius misses distant enemies entirely.
- Keep effects local and concrete. Prefer entity ids from context.
- The user JSON includes spell_anchors: visible environmental props sorted toward relevance. When the spell mentions surroundings, materials, objects, altars, braziers, mirrors, water, blood, bone, machinery, notices, cages, plants, webs, crystals, lights, books, bells, shrines, or other scenery, scan spell_anchors before choosing a generic resolution.
- Use actual prop ids from spell_anchors as target/center/origin/placement anchors for create_tiles, area_damage, area_status, summon, conjure_item, conjure_creature, create_trigger, push, or pull. Use a prop's tags/affordances to flavor the mechanics.
- recommended_effect_patterns inside a spell_anchor are copyable skeletons; fill in balanced amount/radius/duration/costs as needed, and prefer those patterns when they match the spell.
- For attacks, usually target creatures and use the prop as the blast center/origin. If an anchor has range_hint, a small blast centered there may miss; use direct damage/status on the creature, or a line/beam from the prop toward nearest_enemy. Example: an iron brazier can center fire area_damage with affects:"enemies"; a mirror can center reveal/confusion; a pool can create mist/ice/water; vines/webs/ropes can add webbed/rooted; a notice/book/tablet can reveal or curse.
- Do not target a prop with damage/status unless the spell explicitly destroys, animates, repairs, or transforms that object. Mention a prop by name in outcome_text when you use it.
- For permanent terrain, omit duration or use "permanent"; otherwise duration must be 1 or more.
- For physically impossible global requests (reverse gravity for everything, turn all walls into X), reject with a creative reason or give a local creative interpretation using available effects.

Useful tiles: floor, wall, door, open_door, stairs_down, stairs_up, water, fire, slick_ice, ice_wall, poison_cloud, vines, rubble, mist. Also accepted: lava/magma→fire, caltrops/thorns/web/net→vines, spikes/debris/bones→rubble, smoke/fog→mist, acid→poison_cloud, iron_bars/barrier→ice_wall.
Tile usage: use vines for tangling hazards (webs, thorns, nets, caltrops), rubble for destructive debris, mist for obscuring clouds, slick_ice for sliding hazards. Always use radius for room/area coverage — e.g. {"type":"create_tiles","tile":"mist","target":"player","radius":5} for filling a room with smoke.
Supported statuses: {supported_statuses}.
Use status only for supported mechanical statuses.
Key behaviors: burning/bleeding/poisoned deal 1 damage/turn; regenerating heals 1 HP/turn; slowed skips every other turn; berserk deals +2 damage but self-damages; empowered deals +2 damage; weakened deals -2 damage (a maimed/withered limb); marked/cursed take extra damage; invisible reduces enemy sensing; confused moves randomly; frightened flees; frozen/stunned/rooted/silenced/webbed are disabling.

Good examples:
{"accepted": true, "severity": "moderate", "outcome_text": "Time thickens around your enemies.", "effects": [{"type": "area_status", "target": "player", "radius": 4, "status": "slowed", "duration": 4, "affects": "enemies"}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "The orb bursts against your foe in a rose of flame, petals of fire licking outward.", "effects": [{"type": "area_damage", "target": "nearest_enemy", "radius": 2, "amount": 5, "damage_type": "fire", "include_player": false, "affects": "enemies"}], "costs": [{"type": "mana", "amount": 5}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "Your bones remember fire.", "effects": [{"type": "add_resistance", "target": "player", "damage_type": "fire", "amount": 50}], "costs": [{"type": "mana", "amount": 6}, {"type": "curse", "id": "fire_debt", "name": "Fire Debt", "description": "Something hot is owed."}], "rejected_reason": null}
{"accepted": true, "severity": "minor", "outcome_text": "Your bones lock like limestone.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "frozen", "display_name": "petrified", "expiry_text": "The stone cracks. You can move.", "duration": 3}], "costs": [{"type": "mana", "amount": 2}], "rejected_reason": null}
{"accepted": true, "severity": "moderate", "outcome_text": "Fire leaps up around you in a bright, eager ring.", "effects": [{"type": "create_tiles", "tile": "fire", "target": "player", "radius": 3, "hollow": true, "duration": 5}], "costs": [{"type": "mana", "amount": 5}], "rejected_reason": null}
{"accepted": true, "severity": "major", "outcome_text": "A roaring gout of flame swallows the foe and everything crowded around it.", "effects": [{"type": "area_damage", "target": "nearest_enemy", "radius": 5, "amount": 12, "damage_type": "fire", "include_player": false, "affects": "enemies"}], "costs": [{"type": "mana", "amount": 8}, {"type": "health", "amount": 2}], "rejected_reason": null}
{"accepted": true, "severity": "catastrophic", "outcome_text": "The floor heaves and splits; the whole room comes down in a roar of stone and dust.", "effects": [{"type": "area_damage", "target": "player", "radius": 7, "amount": 22, "damage_type": "physical", "include_player": false, "affects": "enemies"}, {"type": "create_tiles", "target": "player", "radius": 6, "tile": "rubble", "duration": 0}], "costs": [{"type": "max_health", "amount": 3}, {"type": "curse", "id": "stone_debt", "name": "Stone Debt", "description": "The earth gave once; it will ask for you later."}], "rejected_reason": null}
{"accepted": false, "severity": "catastrophic", "outcome_text": "", "effects": [], "costs": [], "rejected_reason": "Reality refuses to become that convenient."}
""".replace("{supported_statuses}", SUPPORTED_STATUS_TEXT)


@dataclass(frozen=True)
class CapabilityCard:
    """One spell-mechanic family: what selects it, what it unlocks, and the prompt text +
    examples injected when it is selected. See docs/CAPABILITY_ROUTING.md §5.1."""

    name: str
    triggers: tuple[str, ...]  # lowercase substrings for tier-1 keyword routing
    embed_description: str  # natural-language gloss for tier-2 (embedding) routing
    index_line: str  # the ONE line shown in the always-on capability index
    effect_types: tuple[str, ...]  # SUPPORTED_EFFECTS keys this card unlocks (may be empty
    #                                for a prompt-only card that refines a core effect)
    prompt_block: str  # schema fragment + balance rules + limits, injected when selected
    examples: tuple[str, ...] = ()  # 1-2 few-shot JSON examples, injected when selected
    cost_hint: str = ""
    # Composition + scoping (docs §5.0/§5.3/§5.5):
    common_combos: tuple[str, ...] = ()  # specialist partners the engine auto-loads (one hop)
    required_context: tuple[str, ...] = ()  # game-state keys to inject when selected
    version: int = 1  # bump on any schema/balance change; spellbook cache keys on it
    integrated: bool = True  # False = planned card whose engine handler does not exist yet


# ----------------------------------------------------------------------------------------
# Integrated cards: specialist mechanics whose engine handlers already exist. Content is
# lifted from the old monolithic prompt (effect catalog, balance rules, examples) so the carve
# is a move, not a rewrite.
# ----------------------------------------------------------------------------------------

_CONJURE_CREATURE = CapabilityCard(
    name="conjure_creature",
    triggers=(
        "summon", "conjure", "call ", "call a", "call up", "call forth", "call down",
        "raise", "spawn", "elemental", "wolf", "wolves", "spirit", "golem", "construct",
        "swarm", "ward", "totem", "sentinel", "guardian", "minion", "ally", "servant",
        "familiar", "hound", "creature", "beast", "ooze", "slime", "wraith", "demon",
        "skeleton", "scarecrow", "font", "beacon", "anchor", "radiator",
    ),
    embed_description=(
        "Summoning living or semi-living helpers, hazards, wards, and aura-bearers: wolves, "
        "elementals, constructs, swarms, healing totems, burning wards, bomb golems, "
        "sentinels that stay and emit an aura or explode on death."
    ),
    index_line="conjure_creature — summon allies, hazards, wards, and aura-bearers (totems, golems, swarms)",
    effect_types=("summon", "conjure_creature"),
    prompt_block=(
        "- summon: name, faction ('ally' or 'enemy'), hp, attack, defense, char, x, y. All at top level.\n"
        "- conjure_creature: template, name, faction ('ally' or 'enemy'), tags, placement, count. Always include faction.\n"
        "  Creature templates: tiny_swarm, small_beast, humanoid, construct, spirit, slime, summoned_servant, hazard_creature.\n"
        "Behavior tags (add to a summoned/conjured creature's tags array for special per-turn behaviors):\n"
        "  'pacifist' never attacks (healing fonts, wards, aura-only objects); 'ranged' attacks up to 7 tiles;\n"
        "  'guardian' acts only within 3 tiles and never chases; 'stationary' never moves; 'aura_burn_N',\n"
        "  'aura_heal_N', 'aura_fear_N', 'aura_slow_N', 'aura_poison_N', 'aura_bleed_N', 'aura_reveal_N',\n"
        "  'aura_mana_N', 'aura_damage_N', 'aura_confuse_N' emit that effect each turn in radius N;\n"
        "  'explode_on_death', 'shatter_on_death', 'poison_cloud_on_death', 'freeze_on_death', 'spawn_on_death'.\n"
        "For an aura the fixed 'aura_*' tags don't cover (a specific damage_type, an unusual status, a tuned radius/lifetime), nest an \"aura\" object inside the conjure_creature/summon effect instead: {\"aura\": {\"kind\": \"damage\", \"amount\": 2, \"damage_type\": \"shadow\", \"radius\": 1, \"affects\": \"enemies\", \"label\": \"burning shadow\"}}. Use this for spells like 'a hound whose shadow burns nearby foes'.\n"
        "Prefer template-backed conjuration for arbitrary creatures; creative names/materials/tags are fine but mechanics come from the template."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "Two wolves pour out of the spell like spilled ink, tongues lolling, delighted.", "effects": [{"type": "conjure_creature", "template": "small_beast", "name": "shadow wolf", "count": 2, "faction": "ally", "tags": ["wolf", "predator"], "placement": "near_player"}], "costs": [{"type": "mana", "amount": 5}, {"type": "curse", "id": "wild_debt", "name": "Wild Debt", "description": "The wild expects repayment."}], "rejected_reason": null}',
        '{"accepted": true, "severity": "moderate", "outcome_text": "A smouldering ward takes shape. Enemies who approach will burn.", "effects": [{"type": "conjure_creature", "template": "hazard_creature", "name": "burning ward", "faction": "ally", "tags": ["aura_burn_3", "stationary", "ward"], "placement": "near_player", "count": 1}], "costs": [{"type": "mana", "amount": 5}, {"type": "item", "item": "chalk", "amount": 1}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana, sometimes a curse (Wild Debt) for powerful or volatile summons",
    common_combos=("conjure_item",),
)

_CONJURE_ITEM = CapabilityCard(
    name="conjure_item",
    triggers=(
        "conjure", "create item", "spawn", "glass", "tooth", "teeth", "shard",
        "key", "coin", "weapon", "potion", "vial", "trinket", "webbing",
        "transmute", "turn my",
    ),
    embed_description=(
        "Conjuring or transmuting objects and materials: glass teeth, a key, webbing, a "
        "shard, body parts, ritual components; or transforming an item already in the "
        "world / inventory into another."
    ),
    index_line="conjure_item — create or transmute objects, materials, body parts, and loose items",
    effect_types=("conjure_item", "spawn_item", "transform_item", "modify_inventory"),
    prompt_block=(
        "- conjure_item: template, name, material, tags, target, placement, count.\n"
        "  Item templates: generic_object, body_part, glass_shard, ritual_component, weapon_like, food, key_like, treasure.\n"
        "- spawn_item: name, item_type, x, y, char, material, quantity, tags.\n"
        "- transform_item: target (inventory|nearest_item|...), item, new_item_type, material, tags.\n"
        "- modify_inventory: item, mode ('add'|'remove'), amount.\n"
        "Creative names/materials/tags are allowed; mechanics come from the chosen template."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The goblin spits out a brittle little treasure.", "effects": [{"type": "damage", "target": "nearest_enemy", "amount": 3, "damage_type": "physical"}, {"type": "add_status", "target": "nearest_enemy", "status": "bleeding", "duration": 3}, {"type": "conjure_item", "template": "body_part", "name": "glass teeth", "material": "glass", "tags": ["fragile", "tooth"], "target": "nearest_enemy", "placement": "target_tile"}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}',
    ),
    cost_hint="minor-moderate; mana or a small item cost",
)

_TRANSFORM_ENTITY = CapabilityCard(
    name="transform_entity",
    triggers=(
        "polymorph", "turn the", "turn it into", "turn him", "turn her", "turn them",
        "transform", "become a", "into a chicken", "into a", "petrify", "petrified",
        "statue", "shrink", "chicken", "frog", "toad", "sheep", "mouse",
    ),
    embed_description=(
        "Turning an existing creature INTO something else — polymorph into a harmless "
        "animal, petrify into a statue, shrink to a mouse: the original threat is replaced, "
        "not duplicated."
    ),
    index_line="transform_entity — turn an existing creature INTO something else (polymorph, petrify, shrink)",
    effect_types=("transform_entity",),
    prompt_block=(
        "- transform_entity: target (the creature id or 'nearest_enemy'), plus the fields that change — "
        "name, char, faction, material, hp, max_hp, attack, defense, tags. Use this to turn an existing "
        "creature INTO something else ('turn the goblin into a chicken', 'polymorph', 'petrify into a "
        "statue'): rename the target and drop its attack/hp so it stops being a threat. Do NOT "
        "conjure_creature a new creature for this — that leaves the original enemy alive and standing "
        "next to a decoration.\n"
        "For maiming a single body part while the creature lives on, use the disfigure mechanics (status-based) instead of transforming the whole creature."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The enemy\'s bones soften into feathers; a bewildered chicken stands where it stood.", "effects": [{"type": "transform_entity", "target": "nearest_enemy", "name": "clucking chicken", "char": "c", "attack": 0, "hp": 1, "max_hp": 1, "tags": ["harmless", "chicken"]}], "costs": [{"type": "mana", "amount": 5}, {"type": "item", "item": "chalk", "amount": 1}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana plus an item or curse — neutralizing a threat outright is strong",
    common_combos=("disfigure",),
)

_DISFIGURE = CapabilityCard(
    name="disfigure",
    triggers=(
        "disfigure", "maim", "mutilate", "cripple", "mangle", "deform", "warp his",
        "warp her", "boil", "melt", "wither", "rot", "necrose", "gangrene", "flay",
        "sever", "shatter his", "shatter her", "harden his", "harden her", "soften his",
        "soften her", "twist his", "twist her", "calcify", "liquefy", "rupture", "palsy",
        # body parts -- the strongest signal a spell is a targeted maiming
        "legs", "leg", "arm", "arms", "hand", "hands", "fingers", "knees", "knee",
        "spine", "brain", "skull", "eyes", "eye", "tongue", "throat", "mouth", "flesh",
        "skin", "hide", "carapace", "bones", "guts", "organs", "heart", "sinew",
        "muscles", "muscle", "limbs", "limb", "veins", "nerves",
    ),
    embed_description=(
        "Maiming one specific part of a living target -- boiling a brain, withering an "
        "arm, turning legs to iron, rotting flesh, sealing a mouth -- so the creature "
        "lives on but is broken in a specific way. Partial and crippling, not whole-body."
    ),
    index_line="disfigure -- maim a body part: cripple/wither/boil/rot/petrify a limb or organ (status-based)",
    effect_types=(),  # resolves via core add_status / damage / add_weakness; uses the 'weakened' status
    prompt_block=(
        "For spells that maim a SPECIFIC body part (legs, arm, brain, eyes, throat, "
        "skin...) leaving the creature alive but broken, translate the part into the "
        "matching status with add_status -- do NOT use transform_entity (that is for "
        "turning a creature wholesale into another creature):\n"
        "  legs/feet/knees -> rooted (immobile, can still attack); whole body to stone -> frozen.\n"
        "  arm/hand/fists/sword-arm/strength -> weakened (the target's own attacks deal -2 damage).\n"
        "  brain/mind/skull -> damage plus stunned or confused.\n"
        "  flesh/wound (rot) -> poisoned; veins/arteries -> bleeding.\n"
        "  mouth/tongue/throat -> silenced; eyes -> confused; nerve/courage -> frightened.\n"
        "  skin/hide/carapace made brittle -> add_weakness (name a damage_type) and/or marked.\n"
        "Choose duration for severity; a permanent maiming uses duration \"permanent\" and "
        "MUST carry a heavy cost. Disfigurement is cruel and major: always pair it with a "
        "real cost (mana plus a curse, health, or max-resource loss). Use display_name to "
        "show the wound (e.g. \"legs of iron\", \"boiled mind\", \"withered arm\") and "
        "expiry_text for when it mends."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "His thighs seize grey and ringing -- legs of cold iron.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "rooted", "display_name": "legs of iron", "expiry_text": "The iron flakes away; he can step again.", "duration": "permanent"}], "costs": [{"type": "mana", "amount": 5}, {"type": "curse", "id": "wild_debt", "name": "Wild Debt", "description": "The wild expects repayment."}], "rejected_reason": null}',
        '{"accepted": true, "severity": "major", "outcome_text": "Steam whistles from his ears; his eyes roll white as his brain boils.", "effects": [{"type": "damage", "target": "nearest_enemy", "amount": 6, "damage_type": "fire"}, {"type": "add_status", "target": "nearest_enemy", "status": "stunned", "display_name": "boiled mind", "duration": 3}], "costs": [{"type": "mana", "amount": 4}, {"type": "health", "amount": 3}], "rejected_reason": null}',
        '{"accepted": true, "severity": "moderate", "outcome_text": "His sword-arm shrivels to a withered stick; the blade wobbles in his grip.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "weakened", "display_name": "withered arm", "expiry_text": "Strength seeps back into his arm.", "duration": 5}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana plus a curse/health/max-resource cost -- permanent maiming is always major",
)

_FACTION_CHARM = CapabilityCard(
    name="faction_charm",
    triggers=(
        "charm", "befriend", "convince the", "make it my", "make them my", "turn the enemy ally",
        "ally for one turn", "defect", "turn against", "oath", "bind it", "make a friend",
        "change side", "take sides", "loyal",
    ),
    embed_description=(
        "Changing whose side a creature is on: charming an enemy into an ally, binding it "
        "with an oath, making a foe's weapon or reflection defect for a turn."
    ),
    index_line="faction_charm — turn a creature to your side (or against its own), bind it with an oath/tag",
    effect_types=("change_faction", "add_tag", "remove_tag"),
    prompt_block=(
        "- change_faction: target, faction ('ally'|'enemy').\n"
        "- add_tag / remove_tag: target, tag. Use tags like 'oath_bound' to mark a charmed creature.\n"
        "Charming an enemy outright is powerful — pair it with a lasting cost (a curse such as Borrowed Trust)."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "A hostile thought changes its coat.", "effects": [{"type": "change_faction", "target": "nearest_enemy", "faction": "ally"}, {"type": "add_tag", "target": "nearest_enemy", "tag": "oath_bound"}], "costs": [{"type": "curse", "id": "borrowed_trust", "name": "Borrowed Trust", "description": "Promises made by magic tend to come due."}], "rejected_reason": null}',
    ),
    cost_hint="major; usually a curse — converting a threat to an ally is a swing",
    common_combos=("transform_entity",),
)

_BARRIER_SHAPING = CapabilityCard(
    name="barrier_shaping",
    triggers=(
        "wall", "barrier", "line of", "in a line", "between me and", "path", "bridge",
        "corridor", "channel", "wall of", "row of", "seal the", "block the", "divide",
        "cone", "beam",
    ),
    embed_description=(
        "Directional terrain shaped as a wall, line, barrier, path, cone, or beam between "
        "the caster and a foe — not a disc centered on the caster."
    ),
    index_line="barrier_shaping — directional terrain (walls, lines, barriers, cones) using create_tiles shape/origin",
    effect_types=(),  # uses create_tiles (a core effect); this card adds the shaping rules
    prompt_block=(
        "Directional terrain — 'wall', 'line', 'barrier', 'between me and X', 'in a line', 'path', "
        "'bridge' — MUST be a create_tiles with shape:'wall' or 'line', origin:'player', and target the "
        "foe (its id or 'nearest_enemy'); also shape:'cone' or 'scatter' for cones and bursts. Do NOT "
        "fall back to a player-centered radius disc, which throws away the direction the player asked for. "
        "Use ONE create_tiles effect for a shape — never list individual coordinates."
    ),
    examples=(
        '{"accepted": true, "severity": "minor", "outcome_text": "Ice unrolls toward your enemy like a silver carpet.", "effects": [{"type": "create_tiles", "shape": "line", "origin": "player", "target": "nearest_enemy", "tile": "slick_ice", "duration": 4}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}',
        '{"accepted": true, "severity": "moderate", "outcome_text": "A sheet of fire springs up in a line, sealing the goblins off from you.", "effects": [{"type": "create_tiles", "shape": "wall", "origin": "player", "target": "nearest_enemy", "tile": "fire", "duration": 4}], "costs": [{"type": "mana", "amount": 5}, {"type": "item", "item": "chalk", "amount": 1}], "rejected_reason": null}',
    ),
    cost_hint="minor-moderate; mana, sometimes chalk for a lasting barrier",
)

_DIVINATION = CapabilityCard(
    name="divination",
    triggers=(
        "reveal", "sense", "detect", "locate", "find", "show me", "track", "mark the",
        "mark every", "see through", "scout", "vision", "glowing", "where the", "hidden",
        "invisible things", "weakness", "weaknesses",
    ),
    embed_description=(
        "Revealing, tracking, locating, or marking: exposing hidden or invisible enemies, "
        "tracking a target, sensing weaknesses, marking foes so they can be sensed."
    ),
    index_line="divination — reveal/track/locate/mark targets (uses add_status 'revealed')",
    effect_types=(),  # uses add_status (a core effect); this card adds the reveal pattern
    prompt_block=(
        "For tracking, glowing-shadow, locate, scry, or reveal spells, use add_status with status "
        "'revealed' on the target (an actor id, 'nearest_enemy', 'all_enemies', or 'all'). A revealed "
        "target can be sensed; use a longer duration for tracking spells. This is the mechanical "
        "expression of 'reveal weaknesses', 'mark for sensing', 'show hidden things'."
    ),
    examples=(
        '{"accepted": true, "severity": "minor", "outcome_text": "A blue shadow pins the target\'s location in your mind.", "effects": [{"type": "add_status", "target": "nearest_enemy", "status": "revealed", "duration": 6}], "costs": [{"type": "mana", "amount": 2}], "rejected_reason": null}',
    ),
    cost_hint="minor; mana",
)

_TRIGGERS_REACTIONS = CapabilityCard(
    name="triggers_reactions",
    triggers=(
        "next time", "whenever", "when an enemy", "when they", "the next attack",
        "next attack against", "react", "reaction", "contingency", "if i am hit",
        "when i bleed", "ward that", "trap that", "retaliate", "counter",
    ),
    embed_description=(
        "Conditional 'next time X happens, Y happens' magic: retaliation wards, traps that "
        "fire on a condition, contingencies armed for a future trigger."
    ),
    index_line="triggers_reactions — armed conditionals ('next time X happens, do Y') via create_trigger",
    effect_types=("create_trigger",),
    prompt_block=(
        "For 'next time X happens, Y happens' spells, use create_trigger. Fields: trigger "
        "('on_next_spell|on_player_hit|on_player_damaged|on_player_move|on_enemy_hit|on_enemy_damaged|"
        "on_enemy_death'), target ('player|nearest_enemy|all_enemies|any'), charges, duration, name, "
        "effects. Trigger effects may use target:'trigger_target' or target:'trigger_source'."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "Your wound learns to answer.", "effects": [{"type": "create_trigger", "name": "thorn-blood answer", "trigger": "on_player_hit", "target": "player", "charges": 1, "duration": 6, "effects": [{"type": "damage", "target": "trigger_source", "amount": 5, "damage_type": "physical"}, {"type": "add_status", "target": "trigger_source", "status": "bleeding", "duration": 3}]}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}',
    ),
    cost_hint="moderate; mana",
    common_combos=("delayed_effects",),
)

_DELAYED_EFFECTS = CapabilityCard(
    name="delayed_effects",
    triggers=(
        "in three turns", "in five turns", "later", "delayed", "after a few", "soon",
        "comes back", "will arrive", "ticking", "fuse", "countdown", "in a moment",
        "next turn", "future", "debt",
    ),
    embed_description=(
        "Effects that pay off on a timer rather than a condition: a summon that arrives in "
        "N turns, a scheduled blast, a delayed heal, a reckoning that comes due."
    ),
    index_line="delayed_effects — scheduled payoffs on a timer (in N turns, ...) via schedule_event",
    effect_types=("schedule_event",),
    prompt_block=(
        "For a delayed payoff or future consequence, use schedule_event. Fields: turns (number), "
        "event_type ('summon|message|damage|heal|status|flood|curse|conjure'), plus event-specific "
        "fields (name, hp, attack, faction, amount, tile, status, etc.). Effects happen now; the "
        "scheduled event happens after `turns`."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "Wounds close. In five turns, something hostile will arrive to collect.", "effects": [{"type": "heal", "target": "player", "amount": 8}, {"type": "schedule_event", "turns": 5, "event_type": "summon", "name": "wrath echo", "char": "W", "hp": 10, "attack": 4, "faction": "enemy"}], "costs": [{"type": "mana", "amount": 3}], "rejected_reason": null}',
    ),
    cost_hint="varies; the delay itself is part of the price (a reckoning arriving later)",
    common_combos=("triggers_reactions",),
)

_PROPHECY = CapabilityCard(
    name="prophecy",
    triggers=(
        "prophesy", "prophecy", "i foretell", "somewhere north", "somewhere east",
        "somewhere south", "somewhere west", "speak into existence", "i prophesy",
        "destined", "fate", "beyond this map", "out in the", "waits for me",
    ),
    embed_description=(
        "Speaking a place, person, danger, or treasure into existence somewhere beyond this "
        "map — a chapel that waits to the north, a blade with the caster's name on it."
    ),
    index_line="prophecy — speak a place/person/danger/treasure into the wider world via create_promise",
    effect_types=("create_promise",),
    prompt_block=(
        "For prophecy spells — speaking a place, person, danger, or treasure into existence somewhere "
        "BEYOND this map — use create_promise. Fields: kind ('prophecy|threat|place|person'), subject, "
        "text (the prophecy in the caster's words), what (the concrete thing: chapel, camp, witch, "
        "cache, tomb...), where (direction words: 'north', 'east of here'), item (ONLY if it promises a "
        "specific object the player will claim), quantity, salience (1-5). The engine adds steep costs "
        "(prophesied treasure always incurs Wild Debt). Use ONLY when the spell speaks about the wider "
        "world; effects on THIS map use normal effects."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "You speak the blade into the world\'s debt-book. Somewhere north, steel begins to wait.", "effects": [{"type": "create_promise", "kind": "prophecy", "subject": "a blade that knows my name", "text": "Somewhere north of here, a blade waits with my name on it.", "what": "cache", "where": "north", "item": "named blade", "salience": 4}], "costs": [{"type": "mana", "amount": 6}], "rejected_reason": null}',
    ),
    cost_hint="major; mana — the engine layers Wild Debt on prophesied treasure",
)

_POSSESSION = CapabilityCard(
    name="possession",
    triggers=(
        "possess", "take over", "take control", "control the", "seize control",
        "see through the eyes", "inhabit", "step into", "ride the", "puppet",
        "become the", "take the body", "wear the", "into its body",
    ),
    embed_description=(
        "Leaving your own body to take control of another creature — possessing an enemy, "
        "riding a beast, seeing and acting through someone else's body while your own is "
        "left behind."
    ),
    index_line="possession — leave your body and take control of another creature (yours is left behind)",
    effect_types=("possess",),
    prompt_block=(
        "- possess: target (the creature id or 'nearest_enemy'). You leave your current body "
        "and pilot the target; its stats, position, and abilities become yours and your old "
        "body is left vacant and vulnerable. Major and risky — pair it with a real cost."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "Your selfhood leaps the gap and lands behind the brute\'s eyes; your old body sways where you left it, suddenly tenantless.", "effects": [{"type": "possess", "target": "nearest_enemy"}], "costs": [{"type": "mana", "amount": 6}, {"type": "curse", "id": "borrowed_body", "name": "Borrowed Body", "description": "A body you did not grow remembers its old owner."}], "rejected_reason": null}',
    ),
    cost_hint="major; mana plus a curse or health — vacating your body is dangerous",
)

_STRUCTURE_ANIMATION = CapabilityCard(
    name="structure_animation",
    triggers=(
        "animate", "come alive", "bring to life", "bring it to life", "awaken", "rouse",
        "wake the", "make the door", "make the wall", "door", "statue", "gargoyle",
        "furniture", "the chair", "the table", "the brazier", "the gate", "the chains",
        "the pillar", "to life", "rise up and", "give it legs", "tear the", "rip the",
        "pull the", "pry the", "out of the wall",
    ),
    embed_description=(
        "Bringing an existing object or piece of scenery to life as a creature: a door "
        "that tears loose and bites, a statue that steps down to fight, furniture that "
        "stands up and serves."
    ),
    index_line="structure_animation — bring an existing object/prop (door, wall, statue, furniture) to life as a creature",
    effect_types=("animate_object",),
    prompt_block=(
        "- animate_object: target (a prop id from spell_anchors, or omit / 'nearest_object' to "
        "animate the nearest scenery), faction ('ally'|'enemy', default ally), name, char, hp, "
        "attack, defense, tags. The named object stops being scenery and stands up as a creature. "
        "Prefer a real prop id from spell_anchors and name the object in outcome_text. To bring an "
        "EXISTING object or piece of scenery to life ('tear the door loose and make it fight', "
        "'wake the statue'), use animate_object — NOT conjure_creature, which conjures a brand-new "
        "creature from nothing and leaves the real object sitting there untouched."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The brass door tears free of its frame, hinges shrieking, and lumbers to your side with a grudge.", "effects": [{"type": "animate_object", "target": "nearest_object", "name": "angry brass door", "faction": "ally", "hp": 12, "attack": 4, "defense": 3, "char": "D"}], "costs": [{"type": "mana", "amount": 5}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana",
    common_combos=("conjure_creature",),
)

_MEMORY_EDIT = CapabilityCard(
    name="memory_edit",
    triggers=(
        "remember", "forget", "memory", "memories", "recall", "mind", "convince",
        "erase", "implant", "amnesia", "recollect", "believe", "make him think",
        "make her think", "make it think", "wipe", "plant a memory",
    ),
    embed_description=(
        "Spells that change what a person knows or remembers: planting a false memory, "
        "erasing an event, making an NPC forget the caster, rewriting a grudge."
    ),
    index_line="memory_edit — alter, plant, or erase what an NPC remembers or knows",
    effect_types=("edit_memory",),
    prompt_block=(
        "- edit_memory: target (an npc id, or 'nearest_enemy' — applies to the nearest NPC), "
        "op ('add'|'remove'|'alter'), subject (what the memory is about; 'the caster' to mean "
        "the player), text (the new or rewritten memory in the NPC's own frame), strength 1-5. "
        "Removing the caster from a hostile NPC's memory also calms it. Memory edits are major: "
        "they bend a mind — pair with a real cost (a curse, or max-resource loss), and never let "
        "one be a free win-button against a quest gate."
    ),
    examples=(
        '{"accepted": true, "severity": "major", "outcome_text": "The guard\'s eyes go soft; the face he was hunting slides out of his memory like a name off wet ink.", "effects": [{"type": "edit_memory", "target": "nearest_enemy", "op": "remove", "subject": "the caster", "text": "He never saw you here.", "strength": 4}], "costs": [{"type": "curse", "id": "borrowed_forgetting", "name": "Borrowed Forgetting", "description": "What you took from him, you owe."}], "rejected_reason": null}',
    ),
    cost_hint="major+; always a curse or max-resource cost",
    common_combos=("faction_charm",),
    required_context=("target_memories",),
)


CAPABILITY_CARDS: tuple[CapabilityCard, ...] = (
    _CONJURE_CREATURE,
    _CONJURE_ITEM,
    _TRANSFORM_ENTITY,
    _DISFIGURE,
    _FACTION_CHARM,
    _BARRIER_SHAPING,
    _DIVINATION,
    _TRIGGERS_REACTIONS,
    _DELAYED_EFFECTS,
    _PROPHECY,
    _POSSESSION,
    _STRUCTURE_ANIMATION,
    _MEMORY_EDIT,
)


# ----------------------------------------------------------------------------------------
# Planned cards: capabilities we want next. Their engine handlers (and, for several, new
# SUPPORTED_EFFECTS) do NOT exist yet, so they are kept out of CAPABILITY_CARDS and the
# live router. They live here as the design backlog; see docs/CAPABILITY_CARD_PLAN.md.
# ----------------------------------------------------------------------------------------

_SIZE_MODIFICATION = CapabilityCard(
    name="size_modification",
    triggers=(
        "bigger", "smaller", "grow", "enlarge", "enlarged", "giant", "gigantic",
        "colossal", "huge", "swell", "tiny", "miniature", "shrink", "dwindle",
        "miniaturize", "the size of", "no taller than", "twice the size",
    ),
    embed_description=(
        "Changing the SCALE of a creature or object while it stays itself — making a foe "
        "huge and lumbering or shrinking it to a harmless mote, growing an ally larger, "
        "swelling or miniaturizing without changing what it is."
    ),
    index_line="size_modification — scale a creature or object up or down (bigger/smaller) without changing what it is",
    effect_types=("resize_entity",),  # NEW SUPPORTED_EFFECTS key + engine handler (not built)
    prompt_block=(
        "resize_entity: target (creature/object id or 'nearest_enemy'), scale (a multiplier, "
        "e.g. 0.4 to shrink, 2.0 to enlarge), plus the stat shifts that follow from scale "
        "(hp, attack, defense, reach). Growing makes something stronger and slower; shrinking "
        "makes it weaker and easier to ignore. Distinct from transform_entity: the target stays "
        "WHAT it is, only its scale changes. Big swings need a real cost."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The goblin folds inward and inward until a furious thumb-sized speck remains.", "effects": [{"type": "resize_entity", "target": "nearest_enemy", "scale": 0.3, "attack": 0, "defense": 0}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana, plus a curse for dramatic enlargements",
    common_combos=("transform_entity",),
    integrated=False,
)

_GRAVITY_CONTROL = CapabilityCard(
    name="gravity_control",
    triggers=(
        "gravity", "levitate", "levitation", "float", "floating", "weightless",
        "hover", "lift him", "lift her", "lift it", "lift them", "pin him", "pin her",
        "pin them", "pin it", "weigh down", "crush", "crushing", "heavier", "lighter",
        "fall up", "fall upward", "reverse gravity", "press down", "pull down",
        "anchor to the floor", "rise into the air", "slam to the ground", "plummet",
    ),
    embed_description=(
        "Altering weight, falling, and pull: levitating a creature off the floor, pinning "
        "or crushing one under sudden weight, reversing which way is down, making a thing "
        "heavy or feather-light. A STANDING field that persists, not a one-shot shove."
    ),
    index_line="gravity_control — levitate, pin, crush, lighten, or reverse the pull on creatures and a region (standing field)",
    effect_types=("set_gravity",),  # NEW SUPPORTED_EFFECTS key + engine handler (not built)
    prompt_block=(
        "set_gravity: target ('player', a creature id, 'nearest_enemy', or 'tile' + x/y for "
        "a region), mode ('levitate' lifts and disables ground attacks; 'pin'/'crush' roots "
        "and deals 1/turn under weight; 'lighten' speeds/eases movement; 'reverse' flips fall "
        "direction), radius (for a region field), turns (it persists and re-applies each turn). "
        "Distinct from push/pull, which are one-shot impulses: gravity is an ongoing condition. "
        "Reuses the aura tick. A creature held aloft or crushed is strong control — pair with a "
        "real cost; sustained region fields are major."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The brute\'s boots leave the floor; he claws at nothing, pedaling in the air.", "effects": [{"type": "set_gravity", "target": "nearest_enemy", "mode": "levitate", "turns": 4}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana, plus a curse for sustained or region-wide fields",
    common_combos=("barrier_shaping",),
    integrated=False,
)

_PORTAL_GATES = CapabilityCard(
    name="portal_gates",
    triggers=(
        "portal", "gateway", "gate ", "rift", "doorway", "threshold", "rip a hole",
        "tear a hole", "open a passage", "wormhole", "warp gate", "escape hole",
        "linked door", "step through here and out", "fold space to", "shortcut through",
    ),
    embed_description=(
        "Opening a PERSISTENT doorway between two places that stays open and links them: step "
        "in one side, out the other. Escape holes, tactical shortcuts, summoned thresholds — "
        "unlike teleport (a one-shot jump), a portal endures and can be used repeatedly."
    ),
    index_line="portal_gates — open a persistent linked doorway between two tiles (repeatable, unlike one-shot teleport)",
    effect_types=("create_portal",),  # NEW SUPPORTED_EFFECTS key + engine handler (not built)
    prompt_block=(
        "create_portal: anchor (x/y or 'player' for the near mouth) and destination (x/y, or "
        "'known' to land at a remembered/visible location), plus turns (how long it stays open; "
        "omit or 'permanent' only at heavy cost) and two_way (default true). Anything that "
        "enters one mouth steps out the other. Distinct from teleport, which moves a target once "
        "and closes: a portal endures and is repeatable, so it is stronger — never let one open "
        "directly onto an unreached quest gate, and pair lasting portals with a real cost."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The air tears open with a wet sucking sound; through the rift you can see the corridor you fled.", "effects": [{"type": "create_portal", "anchor": "player", "destination": "known", "turns": 6, "two_way": true}], "costs": [{"type": "mana", "amount": 5}, {"type": "curse", "id": "wild_debt", "name": "Wild Debt", "description": "The wild expects repayment."}], "rejected_reason": null}',
    ),
    cost_hint="moderate-major; mana, plus a curse for long-lived or far-reaching gates",
    common_combos=(),
    integrated=False,
)

_PLANT_GROWTH = CapabilityCard(
    name="plant_growth",
    triggers=(
        "vine", "vines", "thorn", "thorns", "thicket", "bramble", "brambles", "root",
        "roots", "creeper", "ivy", "overgrow", "overgrowth", "sprout", "blossom",
        "grow plants", "grow a", "entangle", "ensnaring vines", "grasping roots",
        "wall of thorns", "fruit", "branches", "moss", "weeds", "verdant",
    ),
    embed_description=(
        "Forcing sudden plant growth into the scene: ensnaring vines and grasping roots that "
        "entangle, walls and thickets of thorns that block and cut, fruit or healing blossoms "
        "that sprout. Living terrain that holds, hurts, or feeds."
    ),
    index_line="plant_growth — sprout vines/thorns/roots that entangle, block, or cut (living terrain over tiles + rooted)",
    effect_types=(),  # refinement: resolves via core create_tiles + area_status('rooted'/'webbed') + damage + conjure_item
    prompt_block=(
        "For surging plant growth, compose core effects rather than inventing one: "
        "create_tiles with a thicket/thorn tile to block or fill an area (add hollow:true for a "
        "ring/wall of thorns); area_status with 'rooted' or 'webbed' to entangle creatures where "
        "the growth takes hold; damage or a bleeding status for thorns that cut; conjure_item for "
        "fruit/blossoms that can be picked. Roots can also break or reshape terrain via set_tile. "
        "Fast, lush growth that traps several enemies is strong — give it a fitting cost."
    ),
    examples=(
        '{"accepted": true, "severity": "moderate", "outcome_text": "The flagstones split as thorned vines whip up and lash around their legs.", "effects": [{"type": "create_tiles", "target": "nearest_enemy", "tile": "thicket", "radius": 2}, {"type": "area_status", "target": "nearest_enemy", "status": "rooted", "display_name": "ensnared in vines", "radius": 2, "duration": 3}], "costs": [{"type": "mana", "amount": 4}], "rejected_reason": null}',
    ),
    cost_hint="moderate; mana, sometimes an item — lush entangling growth is real control",
    common_combos=("barrier_shaping",),
    integrated=False,
)

PLANNED_CARDS: tuple[CapabilityCard, ...] = (
    _SIZE_MODIFICATION,
    _GRAVITY_CONTROL,
    _PORTAL_GATES,
    _PLANT_GROWTH,
)


# ----------------------------------------------------------------------------------------
# Routing (tier 1, keyword). docs/CAPABILITY_ROUTING.md §5.3. Recall-biased: we would
# rather load a surplus card than drop the one that makes a compositional spell work.
# ----------------------------------------------------------------------------------------

# Compositional connectives signal multi-mechanic intent ("wall of fire AND make them
# forget me") and raise the selected-set cap.
_CONNECTIVES: tuple[str, ...] = (
    " and ", " while ", " then ", "but also", " except ", " into ", " after ", " before ",
)

_HARD_CEILING = 7


def _keyword_hits(text: str, cards: tuple[CapabilityCard, ...]) -> list[tuple[CapabilityCard, int]]:
    """Cards whose triggers appear in the (lowercased) spell text, with hit counts, ranked
    most-hits-first (a proxy for confidence) and then by registry order for stability."""
    scored: list[tuple[CapabilityCard, int]] = []
    for card in cards:
        hits = sum(1 for trigger in card.triggers if trigger in text)
        if hits:
            scored.append((card, hits))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _dynamic_cap(text: str, has_keyword_hits: bool) -> int:
    """Recall-biased cap: generous on keyword hits, +1 on a compositional connective,
    bounded by a hard ceiling. (Embedding-only matches would start lower; not wired yet.)"""
    cap = 5 if has_keyword_hits else 3
    if any(conn in text for conn in _CONNECTIVES):
        cap += 1
    return min(cap, _HARD_CEILING)


def select_cards(
    spell_text: str,
    *,
    cards: tuple[CapabilityCard, ...] = CAPABILITY_CARDS,
    enable_combos: bool = True,
) -> list[CapabilityCard]:
    """Tier-1 keyword routing + one-hop combo expansion, recall-biased and capped.

    Returns the specialist cards to load for this cast (the always-on core is separate and
    unconditional). Primary keyword hits are kept first so combo bonus cards are what get
    dropped if the cap binds. `enable_combos` is the empirical knob from docs §5.3.
    """
    text = f" {spell_text.lower()} "
    by_name = {card.name: card for card in cards}

    scored = _keyword_hits(text, cards)
    primary = [card for card, _hits in scored]

    selected: list[CapabilityCard] = list(primary)
    if enable_combos:
        seen = {card.name for card in selected}
        for card in primary:  # one hop only — bonus cards do not pull in their own combos
            for combo_name in card.common_combos:
                combo = by_name.get(combo_name)
                if combo is not None and combo.name not in seen:
                    selected.append(combo)
                    seen.add(combo.name)

    cap = _dynamic_cap(text, has_keyword_hits=bool(primary))
    return selected[:cap]


def capability_index(cards: tuple[CapabilityCard, ...] = CAPABILITY_CARDS) -> str:
    """The always-on menu: one line per card, shown every cast so the model knows what
    exists even when a card is not loaded (and can flag a miss via needs_capability)."""
    return "\n".join(f"- {card.index_line}" for card in cards)


def selected_effect_types(
    selected: list[CapabilityCard],
    *,
    core: frozenset[str] = CORE_EFFECT_TYPES,
) -> frozenset[str]:
    """Effect types emittable for a cast = always-on core ∪ selected cards' effect types.
    This is what a per-cast SPELL_RESPONSE_JSON_SCHEMA enum would be narrowed to (Phase 8
    §4 / docs §5.4)."""
    result = set(core)
    for card in selected:
        result.update(card.effect_types)
    return frozenset(result)


def assemble_card_blocks(selected: list[CapabilityCard]) -> str:
    """Concatenate the prompt fragments + examples for the selected cards, to append after
    the core prompt + capability index. Empty string when nothing is selected."""
    chunks: list[str] = []
    for card in selected:
        chunks.append(card.prompt_block)
        chunks.extend(card.examples)
    return "\n".join(chunks)


def assemble_resolver_system_prompt(
    spell_text: str,
    *,
    region_block: str = "",
    caster_block: str = "",
    cards: tuple[CapabilityCard, ...] = CAPABILITY_CARDS,
) -> str:
    """Build the full resolver system prompt: the always-on core, the capability index (the
    menu of mechanics that *can* be loaded), the mechanics blocks for the cards this spell
    routes to, then the region/caster addenda (already rendered by the caller). This is the
    sole resolver-prompt path — see docs/CAPABILITY_ROUTING.md."""
    selected = select_cards(spell_text, cards=cards)
    parts = [
        CORE_PROMPT.rstrip("\n"),
        "",
        "Capability index (mechanics that can be loaded when a spell needs them):",
        capability_index(cards),
    ]
    blocks = assemble_card_blocks(selected)
    if blocks:
        parts += ["", "Mechanics loaded for this spell:", blocks]
    return "\n".join(parts) + "\n" + region_block + caster_block
