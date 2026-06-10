from __future__ import annotations
import random
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import GameEngine

# Predefined special unique quest items and their visual/materials/tags specs
QUEST_ITEMS = {
    "glass eye of hollowmere": {"char": "u", "item_type": "quest_item", "material": "glass", "tags": {"quest_item", "valuable"}},
    "amulet of the old saints": {"char": "?", "item_type": "quest_item", "material": "silver", "tags": {"quest_item", "amulet"}},
    "imperial campaign map": {"char": "?", "item_type": "quest_item", "material": "parchment", "tags": {"quest_item", "map"}},
    "vial of star dew": {"char": "!", "item_type": "quest_item", "material": "glass", "tags": {"quest_item", "elixir"}},
    "sunken crown fragment": {"char": "u", "item_type": "quest_item", "material": "gold", "tags": {"quest_item", "relic"}},
    "stolen silver seal": {"char": "u", "item_type": "quest_item", "material": "silver", "tags": {"quest_item", "seal"}},
    "dried basilisk eye": {"char": "u", "item_type": "quest_item", "material": "organic", "tags": {"quest_item", "curio"}},
    "whispering conch": {"char": "?", "item_type": "quest_item", "material": "shell", "tags": {"quest_item", "curio"}},
    "rusted gate key": {"char": "k", "item_type": "quest_item", "material": "iron", "tags": {"quest_item", "key"}},
}

def get_quest_item_keys() -> list[str]:
    return list(QUEST_ITEMS.keys())

def register_heard_quest_item(engine: GameEngine, npc_id: str) -> None:
    """Track that the player has heard about a quest item and automatically register it."""
    state = engine.state
    profile = state.npc_profiles.get(npc_id)
    if profile is None or not profile.wanted_item or profile.quest_completed:
        return
    
    item_name = profile.wanted_item
    name_lower = item_name.strip().lower()
    
    if name_lower not in state.known_quest_items:
        state.known_quest_items.add(name_lower)
        state.add_message(f"You've heard rumor of a quest item: {item_name}.")
    
    # Automatically register the Quest if not already in the quest list
    from .models import Quest
    already_assigned = any(q.contact == profile.name for q in state.quests)
    if not already_assigned:
        current_loc = ""
        if state.scenario == "frontier":
            current_loc = f"Zone ({state.zone_x},{state.zone_y}) — {state.zone_type}"
        else:
            current_loc = f"Depth {state.depth}/{state.max_depth}"
        
        new_quest = Quest(
            name=f"{profile.name}'s Request",
            description=f"Deliver {profile.wanted_qty} {item_name} to {profile.name} in exchange for a reward.",
            contact=profile.name,
            location=current_loc,
            status="active"
        )
        state.quests.append(new_quest)
        state.add_message(f"Quest added: {new_quest.name}")

def get_outstanding_quest_items(engine: GameEngine) -> list[str]:
    """Get quest items the player has heard about, but doesn't have in inventory,
    and hasn't turned in yet (quests not completed)."""
    state = engine.state
    needed_items = set()
    for profile in state.npc_profiles.values():
        if profile.wanted_item and not profile.quest_completed:
            needed_items.add(profile.wanted_item.lower())

    outstanding = []
    for item in state.known_quest_items:
        if item in needed_items and item not in state.inventory:
            # Check if it's already physically spawned in the current zone
            already_exists = any(
                e.kind == "item" and (e.item_type == item or e.name.lower() == item)
                for e in state.entities.values()
            )
            if not already_exists:
                outstanding.append(item)
    return outstanding

def maybe_spawn_quest_item(engine: GameEngine, avoid: set[tuple[int, int]]) -> bool:
    """With some probability, spawn an outstanding quest item in the zone."""
    outstanding = get_outstanding_quest_items(engine)
    if not outstanding:
        return False
    
    # 35% chance to spawn an outstanding quest item when entering a new zone
    if engine.rng.random() > 0.35:
        return False
        
    chosen_item = engine.rng.choice(outstanding)
    
    # Title-case display name
    display_name = next(
        (k for k in QUEST_ITEMS if k.lower() == chosen_item),
        chosen_item
    ).title()
    
    spec = QUEST_ITEMS.get(chosen_item, {"char": "?", "item_type": "quest_item", "material": None, "tags": {"quest_item"}})
    
    # Find an open tile
    spot = None
    for _ in range(100):
        tx = engine.rng.randint(2, engine.state.width - 3)
        ty = engine.rng.randint(2, engine.state.height - 3)
        if (tx, ty) not in avoid and engine.state.tiles[ty][tx] == "." and engine.can_occupy(tx, ty):
            spot = (tx, ty)
            break
            
    if spot:
        engine.spawn_item(
            name=display_name,
            char=spec["char"],
            x=spot[0],
            y=spot[1],
            item_type=chosen_item,
            quantity=1,
            material=spec.get("material"),
            tags=spec.get("tags")
        )
        avoid.add(spot)
        engine.state.add_message(f"A strange feeling washes over you. There is something important nearby...")
        return True
    return False

def generate_npc_quest(engine: GameEngine, rng: random.Random) -> dict[str, Any] | None:
    """Generate quest fields for a procedural NPC."""
    # 40% chance of a quest
    if rng.random() > 0.40:
        return None
        
    # Pick a random quest item
    item_keys = get_quest_item_keys()
    chosen_item = rng.choice(item_keys)
    display_name = chosen_item.title()
    
    # Reward
    reward_gold = rng.randint(20, 50)
    
    # Optional extra item reward (e.g. blood moss, lockpick, etc)
    reward_item = None
    reward_qty = 0
    if rng.random() < 0.30:
        reward_item = rng.choice(["blood moss", "lockpick", "smoke vial", "mana crystal", "grave salt"])
        reward_qty = 1
        
    return {
        "wanted_item": display_name,
        "wanted_qty": 1,
        "reward_gold": reward_gold,
        "reward_item": reward_item,
        "reward_qty": reward_qty,
    }
