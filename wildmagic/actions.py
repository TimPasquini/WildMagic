from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import shlex
from typing import Any

from .config import flesh_enabled, lore_enabled
from .engine import GameEngine
from .flesh import (
    FleshProvider,
    FleshResolution,
    flesh_context_for_promise,
    make_flesh_provider,
    resolve_flesh,
)
from .lore import (
    LoreExtractionProvider,
    LoreExtractionResolution,
    make_lore_provider,
    resolve_lore_extraction,
)
from .normalize import normalize_id
from .promises import WorldPromise
from .promises import Objective, Reward
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
        lore_provider: LoreExtractionProvider | None = None,
        lore_provider_name: str | None = None,
        flesh_provider: FleshProvider | None = None,
        flesh_provider_name: str | None = None,
        replay_mode: bool = False,
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
        resolved_lore_provider_name = lore_provider_name
        if resolved_lore_provider_name is None and provider_name in {"mock", "ollama", "auto"}:
            resolved_lore_provider_name = provider_name
        self.lore_provider = lore_provider or make_lore_provider(resolved_lore_provider_name)
        self.lore_provider_label = getattr(self.lore_provider, "name", "unknown")
        resolved_flesh_provider_name = flesh_provider_name
        if resolved_flesh_provider_name is None and provider_name in {"mock", "ollama", "auto"}:
            resolved_flesh_provider_name = provider_name
        self.flesh_provider = flesh_provider or make_flesh_provider(resolved_flesh_provider_name)
        self.flesh_provider_label = getattr(self.flesh_provider, "name", "unknown")
        # In replay mode, promises and flesh come from the recorded apply points;
        # background producers must stay silent.
        self.replay_mode = replay_mode
        self._lore_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._pending_lore: list[tuple[concurrent.futures.Future[LoreExtractionResolution], dict[str, Any]]] = []
        self._pending_flesh: list[tuple[concurrent.futures.Future[FleshResolution], str]] = []
        self._queued_flesh_ids: set[str] = set()
        # Promise dicts applied to the engine since the last recorded action, snapshotted
        # pre-merge so replay can re-run the deterministic bind/merge at the same point.
        self._promise_apply_buffer: list[dict[str, Any]] = []
        self._flesh_apply_buffer: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []

    def execute_command(
        self,
        command: str,
        replay_wild_magic: dict[str, Any] | None = None,
        replay_dialogue: dict[str, Any] | None = None,
        replay_promises: dict[str, Any] | None = None,
        replay_flesh: dict[str, Any] | None = None,
        record: bool = True,
    ) -> ActionResult:
        self.drain_lore(block=False)
        self._enqueue_flesh_for_bound_promises()
        self.drain_flesh(block=False)
        if replay_promises is not None:
            self.apply_recorded_promises(replay_promises.get("before"))
        if replay_flesh is not None:
            self.apply_recorded_flesh(replay_flesh.get("before"))
        promises_before = self._pop_applied_promises() if record else []
        flesh_before = self._pop_applied_flesh() if record else []
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
            elif verb in {"journal", "rumors", "promises"}:
                action = "journal"
                success = True
                explicit_messages = describe_journal(self.engine)
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
                    explicit_messages = ["Unequip what? Specify a slot or item name."]
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
                    success, technical_failure, dialogue_record = self._talk(message, replay_dialogue)
            elif verb == "quest":
                action = "quest"
                subverb = tokens[1].lower() if len(tokens) > 1 else ""
                if subverb == "list":
                    success = True
                    explicit_messages = []
                    quests = self.engine.quest_log_entries()
                    if not quests:
                        explicit_messages.append("Quest Log is empty.")
                    else:
                        explicit_messages.append("Quest Log:")
                        for idx, q in enumerate(quests, 1):
                            status_label = "[x]" if q.status == "completed" else "[ ]"
                            explicit_messages.append(f"  {idx}. {status_label} {q.name} - {q.description} (Contact: {q.contact}, Location: {q.location})")
                elif subverb == "add":
                    name = tokens[2] if len(tokens) > 2 else "Unknown Quest"
                    desc = tokens[3] if len(tokens) > 3 else ""
                    contact = tokens[4] if len(tokens) > 4 else (self.engine.state.last_talked_npc_name or "None")
                    if len(tokens) > 5:
                        loc = tokens[5]
                    else:
                        state = self.engine.state
                        if state.scenario == "frontier":
                            loc = f"Zone ({state.zone_x},{state.zone_y}) — {state.zone_type}"
                        else:
                            loc = f"Depth {state.depth}/{state.max_depth}"
                    self.engine.add_quest_promise(
                        name=name,
                        description=desc,
                        contact=contact,
                        location=loc,
                        objective=Objective("visit", {"location": loc}),
                        reward=Reward(),
                        tags=["quest", "manual"],
                    )
                    self.engine.state.add_message(f"Quest added: {name}")
                    success = True
                elif subverb == "complete":
                    try:
                        idx = int(tokens[2]) - 1
                        completed = self.engine.complete_quest_by_index(idx)
                        if completed is not None:
                            self.engine.state.add_message(f"Quest marked completed: {completed.name}")
                            success = True
                        else:
                            explicit_messages = ["Invalid quest index."]
                    except (ValueError, IndexError):
                        explicit_messages = ["Quest complete command requires a numeric index, e.g. 'quest complete 1'."]
                elif subverb == "remove":
                    try:
                        idx = int(tokens[2]) - 1
                        removed = self.engine.remove_quest_by_index(idx)
                        if removed is not None:
                            self.engine.state.add_message(f"Quest removed: {removed.name}")
                            success = True
                        else:
                            explicit_messages = ["Invalid quest index."]
                    except (ValueError, IndexError):
                        explicit_messages = ["Quest remove command requires a numeric index, e.g. 'quest remove 1'."]
                else:
                    explicit_messages = ["Unknown quest subcommand. Use 'quest list', 'quest add', 'quest complete <index>', or 'quest remove <index>'."]
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
        self.drain_lore(block=False)
        self._enqueue_flesh_for_bound_promises()
        self.drain_flesh(block=False)
        if replay_promises is not None:
            self.apply_recorded_promises(replay_promises.get("after"))
        if replay_flesh is not None:
            self.apply_recorded_flesh(replay_flesh.get("after"))
        if record:
            action_record = result.to_record()
            promises_after = self._pop_applied_promises()
            flesh_after = self._pop_applied_flesh()
            if promises_before or promises_after:
                action_record["promises"] = {"before": promises_before, "after": promises_after}
            if flesh_before or flesh_after:
                action_record["flesh"] = {"before": flesh_before, "after": flesh_after}
            self.records.append(action_record)
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
        if outcome.technical_failure:
            wild_magic_record["technical_failure"] = True
            wild_magic_record["error"] = "; ".join(outcome.messages)
        return outcome.consumed_turn, outcome.technical_failure, wild_magic_record, context

    def _talk(self, message: str, replay_dialogue: dict[str, Any] | None = None) -> tuple[bool, bool, dict[str, Any] | None]:
        message = message.strip()
        npc = self.engine.find_talk_target()
        if npc is None:
            self.engine.state.add_message("There's no one nearby to talk to.")
            return False, False, None

        if replay_dialogue is not None:
            resolution = DialogueResolution(
                reply=replay_dialogue.get("reply"),
                technical_failure=bool(replay_dialogue.get("technical_failure")),
                error=replay_dialogue.get("error"),
                provider_name=str(replay_dialogue.get("provider") or "replay"),
                raw_response=replay_dialogue.get("raw_response"),
                audit_path=replay_dialogue.get("audit_path"),
            )
            dialogue_record = dict(replay_dialogue)
        else:
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
        if replay_dialogue is not None:
            trade = replay_dialogue.get("trade")
            if isinstance(trade, dict) and not trade.get("technical_failure"):
                trade_data = trade.get("data")
        elif self.engine.should_consider_trade(npc, message, reply):
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
        if replay_dialogue is None:
            lore_context = self.engine.lore_extraction_context(npc, message, reply)
            self._enqueue_lore_extraction(lore_context, dialogue_record)
        return True, False, dialogue_record

    def _enqueue_lore_extraction(self, context: dict[str, Any], dialogue_record: dict[str, Any]) -> None:
        if not lore_enabled():
            dialogue_record["lore"] = {"enabled": False, "promises": []}
            return
        dialogue_record["lore"] = {
            "enabled": True,
            "pending": True,
            "provider": self.lore_provider_label,
            "technical_failure": False,
            "error": None,
            "promises": [],
        }
        if self._lore_executor is None:
            self._lore_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = self._lore_executor.submit(resolve_lore_extraction, self.lore_provider, context)
        self._pending_lore.append((future, dialogue_record))

    def drain_lore(self, block: bool = False) -> None:
        remaining: list[tuple[concurrent.futures.Future[LoreExtractionResolution], dict[str, Any]]] = []
        for future, dialogue_record in self._pending_lore:
            if not block and not future.done():
                remaining.append((future, dialogue_record))
                continue
            try:
                resolution = future.result()
            except Exception as exc:
                resolution = LoreExtractionResolution([], True, str(exc), self.lore_provider_label)
            self.lore_provider_label = resolution.provider_name
            # Snapshot the extraction outputs before add_promises mutates them (binding,
            # merging) so the replay record carries the apply-point inputs.
            self._promise_apply_buffer.extend(promise.to_dict() for promise in resolution.promises)
            added = self.engine.add_promises(resolution.promises)
            lore_record = dialogue_record.setdefault("lore", {})
            lore_record.update(
                {
                    "enabled": True,
                    "pending": False,
                    "provider": resolution.provider_name,
                    "technical_failure": resolution.technical_failure,
                    "error": resolution.error,
                    "raw_response": resolution.raw_response,
                    "audit_path": resolution.audit_path,
                    "promises": [promise.to_dict() for promise in added],
                }
            )
        self._pending_lore = remaining

    def _enqueue_flesh_for_bound_promises(self) -> None:
        """Queue a background decoration draft for each newly bound promise.

        Live only: in replay mode flesh comes from the recorded apply points. Flesh is
        never load-bearing — a promise that never receives it realizes from the
        deterministic skeleton alone.
        """
        if self.replay_mode or not flesh_enabled():
            return
        for promise in self.engine.state.promises:
            if promise.status != "bound" or promise.binding is None:
                continue
            if promise.flesh is not None or promise.id in self._queued_flesh_ids:
                continue
            self._queued_flesh_ids.add(promise.id)
            if self._lore_executor is None:
                self._lore_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = self._lore_executor.submit(resolve_flesh, self.flesh_provider, flesh_context_for_promise(promise))
            self._pending_flesh.append((future, promise.id))

    def drain_flesh(self, block: bool = False) -> None:
        remaining: list[tuple[concurrent.futures.Future[FleshResolution], str]] = []
        for future, promise_id in self._pending_flesh:
            if not block and not future.done():
                remaining.append((future, promise_id))
                continue
            try:
                resolution = future.result()
            except Exception as exc:
                resolution = FleshResolution(None, True, str(exc), self.flesh_provider_label)
            self.flesh_provider_label = resolution.provider_name
            if resolution.flesh:
                applied = self.engine.apply_promise_flesh(promise_id, resolution.flesh)
                if applied is not None:
                    self._flesh_apply_buffer.append({"promise_id": promise_id, "flesh": dict(applied.flesh or {})})
        self._pending_flesh = remaining

    def apply_recorded_flesh(self, raw_events: list[dict[str, Any]] | None) -> None:
        """Inject flesh recorded at this apply point in a live run."""
        for event in raw_events or []:
            if not isinstance(event, dict):
                continue
            promise_id = str(event.get("promise_id") or "")
            applied = self.engine.apply_promise_flesh(promise_id, event.get("flesh"))
            if applied is not None:
                self._flesh_apply_buffer.append({"promise_id": promise_id, "flesh": dict(applied.flesh or {})})

    def _pop_applied_flesh(self) -> list[dict[str, Any]]:
        applied, self._flesh_apply_buffer = self._flesh_apply_buffer, []
        return applied

    def close(self) -> None:
        for future, _dialogue_record in self._pending_lore:
            future.cancel()
        self._pending_lore.clear()
        for future, _promise_id in self._pending_flesh:
            future.cancel()
        self._pending_flesh.clear()
        if self._lore_executor is not None:
            self._lore_executor.shutdown(wait=False, cancel_futures=True)
            self._lore_executor = None

    def apply_recorded_promises(self, raw_promises: list[dict[str, Any]] | None) -> None:
        """Inject promises recorded at this apply point in a live run.

        Binding and merging re-run deterministically against the replayed engine state,
        which matches the live state because apply points are recorded per action.
        """
        promises = [WorldPromise.from_dict(raw) for raw in raw_promises or [] if isinstance(raw, dict)]
        if not promises:
            return
        self._promise_apply_buffer.extend(promise.to_dict() for promise in promises)
        self.engine.add_promises(promises)

    def _pop_applied_promises(self) -> list[dict[str, Any]]:
        applied, self._promise_apply_buffer = self._promise_apply_buffer, []
        return applied

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
        self.drain_lore(block=True)
        self.drain_flesh(block=True)
        # Promises and flesh drained after the last recorded action have no action to
        # attach to; replay injects them after the action loop, before the final summary.
        final_promises = self._pop_applied_promises()
        final_flesh = self._pop_applied_flesh()
        return {
            "version": 3,
            "final_promises": final_promises,
            "final_flesh": final_flesh,
            "flesh_provider": self.flesh_provider_label,
            "seed": self.seed,
            "scenario": self.scenario,
            "provider": self.provider_label,
            "dialogue_provider": self.dialogue_provider_label,
            "trade_provider": self.trade_provider_label,
            "lore_provider": self.lore_provider_label,
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
        "Commands: move north/south/east/west, open, descend, ascend, wait, cast <spell>, talk <message>, use <item>, equip <item>, unequip <slot>, drop <item>, pickup, inspect (or inventory), journal (or rumors), wares (or browse), quit.",
        "Journal: 'journal' lists everything the world has told you - rumors heard, claims corroborated, places found true - with a rough direction when one was given. Free, costs no turn.",
        "Talking: stand next to an NPC and 'talk <what you want to say>' (or 'speak'/'say') to start a conversation - it costs a turn, just like any other action.",
        "Trading: some NPCs deal in goods and gold - 'wares' (or 'browse') lists what they have for trade, a free look. Haggle naturally through 'talk' - if a real offer comes together, you'll get a confirmation prompt to 'accept' (or 'yes') or 'reject' (or 'no') before anything changes hands.",
        "Equipment: weapons, armor, clothing, and charms go in their own slots and add to your attack/defense while worn. Equip with 'equip <item>' (or 'wear'/'wield'); take gear off with 'unequip <slot_or_item>' (or 'remove <item>').",
        "Standard spells (deterministic, no wild magic risk): spark, frost, heal, ward, reveal. Type the name directly, e.g. 'frost' -- 'cast frost' instead asks wild magic to improvise one.",
        "Short movement aliases also work: n, s, e, w. Walk into an enemy to attack it.",
    ]


def describe_journal(engine: GameEngine) -> list[str]:
    entries = engine.journal_entries()
    if not entries:
        return ["Your journal is empty. The world talks - listen to people."]
    lines = ["Journal - what the world has told you:"]
    for index, entry in enumerate(entries, 1):
        source = f" (from {entry['source']})" if entry["source"] and entry["source"] != "unknown" else ""
        line = f"  {index}. [{entry['status']}] {entry['subject']}: {entry['text']}{source}"
        lines.append(line)
        if entry["hint"]:
            lines.append(f"       ~ {entry['hint']}")
    return lines


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
        (e for e in engine.state.entities.values() if e.kind in {"actor", "npc"} and e.faction == "ally" and e.hp > 0),
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
        "quests": [
            {
                "id": quest.id,
                "name": quest.name,
                "description": quest.description,
                "contact": quest.contact,
                "location": quest.location,
                "status": quest.status,
            }
            for quest in engine.quest_log_entries()
        ],
        "promises": [
            promise.to_dict()
            for promise in sorted(state.promises, key=lambda promise: promise.id)
        ],
        "promise_reservations": [
            reservation.to_dict()
            for zone in sorted(state.promise_reservations)
            for reservation in state.promise_reservations[zone]
        ],
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
