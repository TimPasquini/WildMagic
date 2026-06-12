from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import GameEngine


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
    """Turn an NPC's request into a quest promise. The promise ledger owns the log,
    target reservation, and eventual quest item realization."""
    state = engine.state
    profile = state.npc_profiles.get(npc_id)
    if profile is None or not profile.wanted_item or profile.quest_completed:
        return

    item_name = profile.wanted_item
    state.add_message(f"You've heard rumor of a quest item: {item_name}.")
    already_assigned = any(
        promise.kind == "quest" and promise.giver_npc == profile.name
        for promise in state.promises
    )
    if already_assigned:
        return

    if state.scenario == "frontier":
        current_loc = f"Zone ({state.zone_x},{state.zone_y}) - {state.zone_type}"
    else:
        current_loc = f"Depth {state.depth}/{state.max_depth}"

    from .promises import Objective, Reward

    engine.add_quest_promise(
        name=f"{profile.name}'s Request",
        description=f"Deliver {profile.wanted_qty} {item_name} to {profile.name} in exchange for a reward.",
        contact=profile.name,
        location=current_loc,
        objective=Objective("fetch", {"item": item_name, "quantity": profile.wanted_qty}),
        reward=Reward(
            gold=max(0, int(profile.reward_gold or 0)),
            items={profile.reward_item.lower(): profile.reward_qty} if profile.reward_item and profile.reward_qty > 0 else {},
        ),
        source=f"quest:{profile.name}",
        tags=["quest", "fetch", "cache", item_name],
    )
    state.add_message(f"Quest added: {profile.name}'s Request")


def generate_npc_quest(engine: GameEngine, rng: Any) -> dict[str, Any] | None:
    """Generate quest fields for a procedural NPC. The NPC profile still owns the
    immediate request; hearing about it creates a WorldPromise."""
    if rng.random() > 0.40:
        return None

    chosen_item = rng.choice(get_quest_item_keys())
    reward_gold = rng.randint(20, 50)

    reward_item = None
    reward_qty = 0
    if rng.random() < 0.30:
        reward_item = rng.choice(["blood moss", "lockpick", "smoke vial", "mana crystal", "grave salt"])
        reward_qty = 1

    return {
        "wanted_item": chosen_item.title(),
        "wanted_qty": 1,
        "reward_gold": reward_gold,
        "reward_item": reward_item,
        "reward_qty": reward_qty,
    }
