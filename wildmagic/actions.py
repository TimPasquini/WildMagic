from __future__ import annotations

from dataclasses import dataclass, field
import shlex
from typing import Any

from .engine import GameEngine
from .normalize import normalize_id
from .wild_magic import (
    DialogueProvider,
    DialogueResolution,
    MagicResolution,
    TradeProvider,
    TradeResolution,
    WildMagicProvider,
    make_dialogue_provider,
    make_provider,
    make_trade_provider,
    resolve_dialogue,
    resolve_spell,
    resolve_trade_proposal,
)


DIRECTIONS = {
    "north": (0, -1),
    "n": (0, -1),
    "up": (0, -1),
    "south": (0, 1),
    "s": (0, 1),
    "down": (0, 1),
    "west": (-1, 0),
    "w": (-1, 0),
    "left": (-1, 0),
    "east": (1, 0),
    "e": (1, 0),
    "right": (1, 0),
    "northeast": (1, -1),
    "ne": (1, -1),
    "northwest": (-1, -1),
    "nw": (-1, -1),
    "southeast": (1, 1),
    "se": (1, 1),
    "southwest": (-1, 1),
    "sw": (-1, 1),
}

# Aliases for the deterministic standard spells, mapped to the GameEngine method
# that resolves them. These spells require no LLM call and always behave the
# same way -- the reliable backbone a player can lean on between wild casts.
STANDARD_SPELLS = {
    "spark": "cast_standard_bolt",
    "spark_bolt": "cast_standard_bolt",
    "bolt": "cast_standard_bolt",
    "frost": "cast_standard_frost",
    "frost_shard": "cast_standard_frost",
    "shard": "cast_standard_frost",
    "heal": "cast_standard_heal",
    "minor_heal": "cast_standard_heal",
    "ward": "cast_standard_ward",
    "reveal": "cast_standard_reveal",
    "detect": "cast_standard_reveal",
}


@dataclass
class ActionResult:
    command: str
    action: str
    success: bool
    consumed_turn: bool
    turn_before: int
    turn_after: int
    messages: list[str] = field(default_factory=list)
    technical_failure: bool = False
    wild_magic: dict[str, Any] | None = None
    dialogue: dict[str, Any] | None = None
    llm_context: dict[str, Any] | None = None
    should_quit: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "action": self.action,
            "success": self.success,
            "consumed_turn": self.consumed_turn,
            "technical_failure": self.technical_failure,
            "turn_before": self.turn_before,
            "turn_after": self.turn_after,
            "wild_magic": self.wild_magic,
            "dialogue": self.dialogue,
        }


class GameSession:
    def __init__(
        self,
        seed: int | None = None,
        scenario: str = "dungeon",
        provider: WildMagicProvider | None = None,
        provider_name: str | None = None,
        dialogue_provider: DialogueProvider | None = None,
        dialogue_provider_name: str | None = None,
        trade_provider: TradeProvider | None = None,
        trade_provider_name: str | None = None,
    ) -> None:
        self.seed = seed
        self.scenario = scenario
        self.engine = GameEngine(seed=seed, scenario=scenario)
        self.provider = provider or make_provider(provider_name)
        self.provider_label = getattr(self.provider, "name", "unknown")
        self.dialogue_provider = dialogue_provider or make_dialogue_provider(dialogue_provider_name)
        self.dialogue_provider_label = getattr(self.dialogue_provider, "name", "unknown")
        self.trade_provider = trade_provider or make_trade_provider(trade_provider_name)
        self.trade_provider_label = getattr(self.trade_provider, "name", "unknown")
        self.records: list[dict[str, Any]] = []

    def execute_command(
        self,
        command: str,
        replay_wild_magic: dict[str, Any] | None = None,
        record: bool = True,
    ) -> ActionResult:
        original_command = command.strip()
        turn_before = self.engine.state.turn
        message_count_before = len(self.engine.state.messages)
        action = "invalid"
        success = False
        technical_failure = False
        wild_magic_record: dict[str, Any] | None = None
        dialogue_record: dict[str, Any] | None = None
        llm_context: dict[str, Any] | None = None
        should_quit = False
        explicit_messages: list[str] | None = None

        if not original_command:
            action = "noop"
            explicit_messages = ["No command entered."]
        else:
            tokens = split_command(original_command)
            verb = tokens[0].lower() if tokens else ""
            if verb in {"quit", "exit"}:
                action = "quit"
                success = True
                should_quit = True
                explicit_messages = ["Leaving the dungeon."]
            elif verb in {"help", "?"}:
                action = "help"
                success = True
                explicit_messages = command_help()
            elif verb in {"inspect", "look", "status", "inventory", "inv", "i"}:
                action = "inspect"
                success = True
                explicit_messages = describe_state(self.engine)
            elif verb in {"wares", "browse", "shop"} and self.engine.state.pending_trade is None:
                action = "wares"
                success = True
                explicit_messages = self._browse_wares()
            elif self.engine.state.pending_trade is not None and verb in {"accept", "yes", "y"}:
                action = "trade_accept"
                success = True
                self.engine.resolve_pending_trade(True)
            elif self.engine.state.pending_trade is not None and verb in {"reject", "decline", "no", "n"}:
                action = "trade_reject"
                success = True
                self.engine.resolve_pending_trade(False)
            elif verb in {"wait", "."}:
                action = "wait"
                success = self.engine.wait_turn()
            elif verb in {"open", "o"}:
                action = "open"
                success = self.engine.open_adjacent_door()
            elif verb in {"descend", "downstairs", ">"}:
                action = "descend"
                success = self.engine.descend_stairs()
            elif verb in {"ascend", "upstairs", "<"}:
                action = "ascend"
                success = self.engine.ascend_stairs()
            elif verb in STANDARD_SPELLS or verb == "f":
                action = "standard_spell"
                method_name = STANDARD_SPELLS.get(verb, "cast_standard_bolt")
                success = getattr(self.engine, method_name)()
            elif verb in {"standard_spell", "spell"}:
                action = "standard_spell"
                spell_name = normalize_id(tokens[1]) if len(tokens) > 1 else ""
                method_name = STANDARD_SPELLS.get(spell_name)
                if method_name:
                    success = getattr(self.engine, method_name)()
                else:
                    explicit_messages = [f"Unknown standard spell: {spell_name or '(missing)'}"]
            elif verb in {"move", "go"}:
                action = "move"
                direction = tokens[1].lower() if len(tokens) > 1 else ""
                success = self._move(direction)
                if direction not in DIRECTIONS:
                    explicit_messages = [f"Unknown direction: {direction or '(missing)'}"]
            elif verb in DIRECTIONS:
                action = "move"
                success = self._move(verb)
            elif verb in {"drop", "discard"}:
                action = "drop"
                item_name = command_argument(original_command, tokens)
                if item_name:
                    success = self.engine.drop_item(item_name)
                else:
                    explicit_messages = ["Drop what? Specify an item name."]
            elif verb in {"pickup", "get", "take", "grab"}:
                action = "pickup"
                self.engine.pick_up_items_at_player()
                success = True
            elif verb in {"use", "consume", "drink", "eat"}:
                action = "use"
                item_name = command_argument(original_command, tokens)
                success = self.engine.use_item(item_name) if item_name else False
                if not item_name:
                    explicit_messages = ["Use what? Specify an item name."]
            elif verb in {"equip", "wear", "wield"}:
                action = "equip"
                item_name = command_argument(original_command, tokens)
                success = self.engine.equip_item(item_name) if item_name else False
                if not item_name:
                    explicit_messages = ["Equip what? Specify an item name."]
            elif verb in {"unequip", "unwield", "remove"}:
                action = "unequip"
                slot_name = command_argument(original_command, tokens)
                success = self.engine.unequip_item(slot_name) if slot_name else False
                if not slot_name:
                    explicit_messages = ["Unequip what? Specify a slot (weapon, armor, charm) or item name."]
            elif verb in {"cast", "wild"}:
                action = "cast"
                spell = command_argument(original_command, tokens)
                if "silenced" in self.engine.state.player.statuses:
                    explicit_messages = ["You are silenced - the spell is swallowed before it can speak."]
                else:
                    success, technical_failure, wild_magic_record, llm_context = self._cast_wild(spell, replay_wild_magic)
            elif verb in {"talk", "speak", "say"}:
                action = "talk"
                message = command_argument(original_command, tokens)
                if "silenced" in self.engine.state.player.statuses:
                    explicit_messages = ["You are silenced - no words come out."]
                elif not message:
                    explicit_messages = ["Say what? Specify what you want to say, e.g. 'talk hello there'."]
                else:
                    success, technical_failure, dialogue_record = self._talk(message)
            else:
                explicit_messages = [f"Unknown command: {verb}"]

        turn_after = self.engine.state.turn
        consumed_turn = turn_after > turn_before
        messages = explicit_messages if explicit_messages is not None else self.engine.state.messages[message_count_before:]
        result = ActionResult(
            command=original_command,
            action=action,
            success=success,
            consumed_turn=consumed_turn,
            turn_before=turn_before,
            turn_after=turn_after,
            messages=messages,
            technical_failure=technical_failure,
            wild_magic=wild_magic_record,
            dialogue=dialogue_record,
            llm_context=llm_context,
            should_quit=should_quit,
        )
        if record:
            self.records.append(result.to_record())
        return result

    def cast_wild(self, spell: str, record: bool = True) -> ActionResult:
        return self.execute_command(f"cast {spell}", record=record)

    def _move(self, direction: str) -> bool:
        if direction not in DIRECTIONS:
            return False
        dx, dy = DIRECTIONS[direction]
        return self.engine.attempt_player_move(dx, dy)

    def _cast_wild(
        self,
        spell: str,
        replay_wild_magic: dict[str, Any] | None,
    ) -> tuple[bool, bool, dict[str, Any], dict[str, Any] | None]:
        spell = spell.strip()
        if not spell:
            return False, False, {
                "spell": "",
                "provider": self.provider_label,
                "technical_failure": False,
                "error": "missing spell text",
                "data": None,
            }, None

        context: dict[str, Any] | None = None
        if replay_wild_magic is not None:
            resolution = MagicResolution(
                data=replay_wild_magic.get("data"),
                technical_failure=bool(replay_wild_magic.get("technical_failure")),
                error=replay_wild_magic.get("error"),
                provider_name=str(replay_wild_magic.get("provider") or "replay"),
                raw_response=replay_wild_magic.get("raw_response"),
                audit_path=replay_wild_magic.get("audit_path"),
            )
        else:
            context = self.engine.context_for_llm(spell)
            resolution = resolve_spell(self.provider, spell, context)

        self.provider_label = resolution.provider_name
        spell_prefix = "*>" if resolution.provider_name == "mock" else ">"
        self.engine.state.add_message(f"{spell_prefix} {spell}")
        wild_magic_record = {
            "spell": spell,
            "provider": resolution.provider_name,
            "technical_failure": resolution.technical_failure,
            "error": resolution.error,
            "data": resolution.data,
            "raw_response": resolution.raw_response,
            "audit_path": resolution.audit_path,
        }
        if resolution.technical_failure or resolution.data is None:
            self.engine.state.add_message(f"Wild magic misfired technically: {resolution.error}")
            return False, True, wild_magic_record, context

        outcome = self.engine.apply_wild_magic_resolution(resolution.data)
        return outcome.consumed_turn, False, wild_magic_record, context

    def _talk(self, message: str) -> tuple[bool, bool, dict[str, Any] | None]:
        message = message.strip()
        npc = self.engine.find_talk_target()
        if npc is None:
            self.engine.state.add_message("There's no one nearby to talk to.")
            return False, False, None

        context = self.engine.dialogue_context_for_llm(npc, message)
        resolution = resolve_dialogue(self.dialogue_provider, npc.name, message, context)
        self.dialogue_provider_label = resolution.provider_name
        dialogue_record = {
            "npc": npc.name,
            "message": message,
            "provider": resolution.provider_name,
            "technical_failure": resolution.technical_failure,
            "error": resolution.error,
            "reply": resolution.reply,
            "raw_response": resolution.raw_response,
            "audit_path": resolution.audit_path,
        }
        if resolution.technical_failure or resolution.reply is None:
            self.engine.state.add_message(f"{npc.name} doesn't seem to hear you. ({resolution.error})")
            return False, True, dialogue_record

        reply = resolution.reply
        trade_data: dict[str, Any] | None = None
        if self.engine.should_consider_trade(npc, message, reply):
            trade_context = self.engine.trade_context_for_llm(npc, message, reply)
            trade_resolution = resolve_trade_proposal(self.trade_provider, npc.name, trade_context)
            self.trade_provider_label = trade_resolution.provider_name
            dialogue_record["trade"] = {
                "provider": trade_resolution.provider_name,
                "technical_failure": trade_resolution.technical_failure,
                "error": trade_resolution.error,
                "data": trade_resolution.data,
                "raw_response": trade_resolution.raw_response,
                "audit_path": trade_resolution.audit_path,
            }
            if not trade_resolution.technical_failure:
                trade_data = trade_resolution.data

        self.engine.apply_dialogue_exchange(npc, message, reply, trade_data)
        return True, False, dialogue_record

    def _browse_wares(self) -> list[str]:
        npc = self.engine.find_talk_target()
        if npc is None:
            return ["There's no one nearby to trade with."]
        profile = self.engine.state.npc_profiles.get(npc.id)
        if profile is None or not profile.wares:
            return [f"{npc.name} has nothing to trade."]
        wares_text = ", ".join(f"{name} x{amount}" for name, amount in sorted(profile.wares.items()))
        return [f"{npc.name} has for trade: {wares_text}"]

    def to_replay(self) -> dict[str, Any]:
        return {
            "version": 1,
            "seed": self.seed,
            "scenario": self.scenario,
            "provider": self.provider_label,
            "dialogue_provider": self.dialogue_provider_label,
            "trade_provider": self.trade_provider_label,
            "actions": self.records,
            "final_summary": summarize_state(self.engine),
        }


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_argument(command: str, tokens: list[str]) -> str:
    if len(tokens) <= 1:
        return ""
    verb = tokens[0]
    if command.lower().startswith(verb.lower()):
        return command[len(verb) :].strip().strip("\"'")
    return " ".join(tokens[1:])


def command_help() -> list[str]:
    return [
        "Commands: move north/south/east/west, open, descend, ascend, wait, cast <spell>, talk <message>, use <item>, equip <item>, unequip <slot>, drop <item>, pickup, inspect (or inventory), wares (or browse), quit.",
        "Talking: stand next to an NPC and 'talk <what you want to say>' (or 'speak'/'say') to start a conversation - it costs a turn, just like any other action.",
        "Trading: some NPCs deal in goods and gold - 'wares' (or 'browse') lists what they have for trade, a free look. Haggle naturally through 'talk' - if a real offer comes together, you'll get a confirmation prompt to 'accept' (or 'yes') or 'reject' (or 'no') before anything changes hands.",
        "Equipment: weapons, armor, and charms go in their own slots and add to your attack/defense while worn. Equip with 'equip <item>' (or 'wear'/'wield'); take gear off with 'unequip weapon/armor/charm' (or 'remove <item>').",
        "Standard spells (deterministic, no wild magic risk): spark, frost, heal, ward, reveal. Type the name directly, e.g. 'frost' -- 'cast frost' instead asks wild magic to improvise one.",
        "Short movement aliases also work: n, s, e, w. Walk into an enemy to attack it.",
    ]


def describe_state(engine: GameEngine) -> list[str]:
    state = engine.state
    player = state.player
    inventory = ", ".join(f"{name} x{amount}" for name, amount in sorted(state.inventory.items())) or "empty"
    curses = ", ".join(f"{curse.name} x{curse.stacks}" for curse in state.curses.values()) or "none"
    flags = ", ".join(sorted(state.flags)) or "none"
    statuses = ", ".join(
        f"{player.status_display.get(s, s)}:{v}" if v != "permanent" else f"{player.status_display.get(s, s)}:permanent"
        for s, v in sorted(player.statuses.items())
    ) or "none"
    enemies = []
    for enemy in sorted(engine.living_enemies(), key=lambda entity: entity.id):
        e_status_str = ""
        if enemy.statuses:
            e_parts = ",".join(f"{enemy.status_display.get(k, k)}:{v}" for k, v in sorted(enemy.statuses.items()))
            e_status_str = f" [{e_parts}]"
        enemies.append(f"{enemy.name}({enemy.hp}/{enemy.max_hp}) at {enemy.x},{enemy.y} [{enemy.faction}]{e_status_str}")
    allies = []
    for ally in sorted(
        (e for e in engine.state.entities.values() if e.kind == "actor" and e.faction == "ally" and e.hp > 0),
        key=lambda entity: entity.id,
    ):
        a_status_str = ""
        if ally.statuses:
            a_parts = ",".join(f"{ally.status_display.get(k, k)}:{v}" for k, v in sorted(ally.statuses.items()))
            a_status_str = f" [{a_parts}]"
        tag_str = f" tags:{','.join(sorted(ally.tags))}" if ally.tags else ""
        allies.append(f"{ally.name}({ally.hp}/{ally.max_hp}) at {ally.x},{ally.y}{tag_str}{a_status_str}")
    npcs = []
    for npc in sorted(
        (e for e in engine.state.entities.values() if e.kind == "npc" and engine.is_visible(e.x, e.y)),
        key=lambda entity: entity.id,
    ):
        profile = engine.state.npc_profiles.get(npc.id)
        role = f" the {profile.role}" if profile and profile.role else ""
        npcs.append(f"{npc.name}{role} at {npc.x},{npc.y}")
    props = []
    for prop in sorted(
        (e for e in engine.state.entities.values() if e.kind == "prop" and engine.is_visible(e.x, e.y)),
        key=lambda entity: entity.id,
    ):
        props.append(f"{prop.name} at {prop.x},{prop.y} ({prop.description}) tags:{','.join(sorted(prop.tags))}")
    equipment = ", ".join(f"{slot}: {item}" for slot, item in sorted(player.equipment.items()) if item) or "none"
    resistances = ", ".join(f"{k}:{v}%" for k, v in sorted(player.resistances.items()) if v) or "none"
    weaknesses = ", ".join(f"{k}:{v}%" for k, v in sorted(player.weaknesses.items()) if v) or "none"
    lines = [
        f"Turn {state.turn} | HP {player.hp}/{player.max_hp} | MP {player.mana}/{player.max_mana}",
        f"Depth {state.depth}/{state.max_depth} | Position {player.x},{player.y} | Scenario {state.scenario}",
        f"Visible tiles: {len(state.visible)} | Explored tiles: {len(state.explored)}",
        f"Statuses: {statuses}",
        f"Equipment: {equipment}",
        f"Inventory: {inventory}",
        f"Curses: {curses}",
        f"Flags: {flags}",
        f"Scheduled events: {len(state.event_timers)}",
        f"Triggers: {len(state.triggers)}",
        "Enemies: " + ("; ".join(enemies) if enemies else "none"),
        "Allies: " + ("; ".join(allies) if allies else "none"),
        "NPCs: " + ("; ".join(npcs) if npcs else "none"),
        "Props: " + ("; ".join(props) if props else "none"),
    ]
    if player.resistances:
        lines.append(f"Resistances: {resistances}")
    if player.weaknesses:
        lines.append(f"Weaknesses: {weaknesses}")
    s = state.stats
    lines.append(
        f"Stats: spells {s.spells_cast}/{s.spells_cast + s.spells_failed} | "
        f"kills {s.enemies_killed} | items used {s.items_used} | "
        f"dmg out {s.damage_dealt} | dmg in {s.damage_taken} | "
        f"healed {s.hp_healed} | curses {s.curses_gained} | floor {s.deepest_floor}"
    )
    return lines


def summarize_state(engine: GameEngine) -> dict[str, Any]:
    state = engine.state
    player = state.player
    living_enemies = sorted(engine.living_enemies(), key=lambda entity: entity.id)
    items = sorted(
        [entity for entity in state.entities.values() if entity.kind == "item"],
        key=lambda entity: entity.id,
    )
    return {
        "turn": state.turn,
        "depth": state.depth,
        "max_depth": state.max_depth,
        "game_over": state.game_over,
        "victory": state.victory,
        "player": {
            "x": player.x,
            "y": player.y,
            "hp": player.hp,
            "mana": player.mana,
            "statuses": dict(sorted(player.statuses.items())),
        },
        "visible_count": len(state.visible),
        "explored_count": len(state.explored),
        "inventory": dict(sorted(state.inventory.items())),
        "flags": dict(sorted(state.flags.items())),
        "tile_counts": tile_counts(state.tiles),
        "event_timers": sorted(
            [
                {
                    "turns": event.get("turns"),
                    "event_type": event.get("event_type") or event.get("type"),
                    "name": event.get("name"),
                    "text": event.get("text"),
                }
                for event in state.event_timers
            ],
            key=lambda event: (str(event.get("turns")), str(event.get("event_type")), str(event.get("name"))),
        ),
        "triggers": sorted(
            [
                {
                    "trigger": trigger.get("trigger") or trigger.get("on"),
                    "target": trigger.get("target"),
                    "charges": trigger.get("charges"),
                    "duration": trigger.get("duration"),
                    "name": trigger.get("name"),
                }
                for trigger in state.triggers
            ],
            key=lambda trigger: (str(trigger.get("trigger")), str(trigger.get("target")), str(trigger.get("name"))),
        ),
        "curses": {
            curse_id: {
                "name": curse.name,
                "description": curse.description,
                "stacks": curse.stacks,
            }
            for curse_id, curse in sorted(state.curses.items())
        },
        "living_enemies": [
            {
                "id": enemy.id,
                "name": enemy.name,
                "x": enemy.x,
                "y": enemy.y,
                "hp": enemy.hp,
                "statuses": dict(sorted(enemy.statuses.items())),
                "tags": sorted(enemy.tags),
                "resistances": dict(sorted(enemy.resistances.items())),
                "weaknesses": dict(sorted(enemy.weaknesses.items())),
            }
            for enemy in living_enemies
        ],
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "x": item.x,
                "y": item.y,
                "item_type": item.item_type,
                "material": item.material,
                "quantity": item.quantity,
                "tags": sorted(item.tags),
            }
            for item in items
        ],
        "entity_count": len(state.entities),
        "recent_messages": state.messages[-8:],
    }


def tile_counts(tiles: list[list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in tiles:
        for tile in row:
            counts[tile] = counts.get(tile, 0) + 1
    return dict(sorted(counts.items()))
