from __future__ import annotations

from dataclasses import dataclass, field
import shlex
from typing import Any

from .engine import GameEngine
from .wild_magic import MagicResolution, WildMagicProvider, make_provider, resolve_spell


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
        }


class GameSession:
    def __init__(
        self,
        seed: int | None = None,
        scenario: str = "dungeon",
        provider: WildMagicProvider | None = None,
        provider_name: str | None = None,
    ) -> None:
        self.seed = seed
        self.scenario = scenario
        self.engine = GameEngine(seed=seed, scenario=scenario)
        self.provider = provider or make_provider(provider_name)
        self.provider_label = getattr(self.provider, "name", "unknown")
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
            elif verb in {"inspect", "look", "status"}:
                action = "inspect"
                success = True
                explicit_messages = describe_state(self.engine)
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
            elif verb in {"spark", "spark_bolt", "bolt", "f"}:
                action = "standard_spell"
                success = self.engine.cast_standard_bolt()
            elif verb in {"standard_spell", "spell"}:
                action = "standard_spell"
                spell_name = tokens[1].lower() if len(tokens) > 1 else ""
                if spell_name in {"spark", "spark_bolt", "bolt"}:
                    success = self.engine.cast_standard_bolt()
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
            elif verb in {"cast", "wild"}:
                action = "cast"
                spell = command_argument(original_command, tokens)
                success, technical_failure, wild_magic_record = self._cast_wild(spell, replay_wild_magic)
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
    ) -> tuple[bool, bool, dict[str, Any]]:
        spell = spell.strip()
        if not spell:
            return False, False, {
                "spell": "",
                "provider": self.provider_label,
                "technical_failure": False,
                "error": "missing spell text",
                "data": None,
            }

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
            return False, True, wild_magic_record

        outcome = self.engine.apply_wild_magic_resolution(resolution.data)
        return outcome.consumed_turn, False, wild_magic_record

    def to_replay(self) -> dict[str, Any]:
        return {
            "version": 1,
            "seed": self.seed,
            "scenario": self.scenario,
            "provider": self.provider_label,
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
        "Commands: move north/south/east/west, open, descend, ascend, wait, spark, cast <spell>, inspect, quit.",
        "Short movement aliases also work: n, s, e, w.",
    ]


def describe_state(engine: GameEngine) -> list[str]:
    state = engine.state
    player = state.player
    inventory = ", ".join(f"{name} x{amount}" for name, amount in sorted(state.inventory.items())) or "empty"
    curses = ", ".join(f"{curse.name} x{curse.stacks}" for curse in state.curses.values()) or "none"
    flags = ", ".join(sorted(state.flags)) or "none"
    enemies = [
        f"{enemy.name}({enemy.hp}/{enemy.max_hp}) at {enemy.x},{enemy.y} [{enemy.faction}]"
        for enemy in sorted(engine.living_enemies(), key=lambda entity: entity.id)
    ]
    return [
        f"Turn {state.turn} | HP {player.hp}/{player.max_hp} | MP {player.mana}/{player.max_mana}",
        f"Depth {state.depth}/{state.max_depth} | Position {player.x},{player.y} | Scenario {state.scenario}",
        f"Visible tiles: {len(state.visible)} | Explored tiles: {len(state.explored)}",
        f"Inventory: {inventory}",
        f"Curses: {curses}",
        f"Flags: {flags}",
        f"Scheduled events: {len(state.event_timers)}",
        "Enemies: " + ("; ".join(enemies) if enemies else "none"),
    ]


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
