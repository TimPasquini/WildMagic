from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import random
import shlex
from typing import Any

from .canon import (
    CanonProvider,
    CanonResolution,
    make_background_canon_provider,
    make_canon_provider,
    resolve_canon,
)
from .config import (
    book_titles_enabled,
    canon_prewarm_enabled,
    canon_prewarm_limit,
    flesh_enabled,
    lore_enabled,
)
from .curses import curse_card, find_curse_key
from .deed_interpreter import (
    DeedInterpreterProvider,
    make_deed_interpreter_provider,
    outcome_is_deed_candidate,
    resolve_deed_interpretation,
)
from .determinism import stable_seed
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
from .models import CanonRecord, CharacterProfile
from .state_view import equipment_inventory_view, replay_summary_view
from .secrets import (
    choose_anchor,
    choose_reward,
    choose_weakness_hint,
    decoration_menu,
    secret_kind_label,
    slot_turn_cost,
)
from .promises import WorldPromise
from .promises import Objective, Reward
from .lore_router import book_lore_cards, dialogue_lore_cards
from .dialogue import (
    DialogueProvider,
    DialogueResolution,
    make_dialogue_provider,
    resolve_dialogue,
)
from .trade import (
    TradeProvider,
    make_trade_provider,
    resolve_trade_proposal,
)
from .wild_magic import (
    MagicResolution,
    WildMagicProvider,
    make_provider,
    resolve_spell,
)
from .worldgen import REALM_TEMPLATES, world_map_strings


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
    canon_materialization: dict[str, Any] | None = None
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
            "canon_materialization": self.canon_materialization,
        }


@dataclass(frozen=True)
class CanonSaturationJob:
    record_id: str
    kind: str
    context: dict[str, Any]
    superseded_by: str | None = None


# Lore claims a single book may bind, enforced both as the canon CONTRACT quota
# (prompt-side) and as the lore-extraction drain clamp (engine-side).
BOOK_CLAIM_QUOTA = 2

_BOOK_TEXT_CATALOG_FIELDS = (
    "topic",
    "secondary_topic",
    "genre",
    "discipline",
    "author_role",
    "audience",
    "purpose",
    "stance",
    "institution",
    "title_shape",
    "taboo_level",
)


def _book_focus_line(
    catalog: dict[str, str], subjects: list[str], title: str | None, *, pages: bool
) -> str:
    """A concise book-specific anchor placed at the start/end of the prompt packet."""
    subject_text = ", ".join(subject for subject in subjects if subject) or "unknown"
    task = "Write the printed contents" if pages else "Invent only the printed title"
    bits = [
        f"{task} for this book",
        f"subjects: {subject_text}",
    ]
    if title:
        bits.append(f"printed title: {title}")
    for key, label in (
        ("genre", "genre"),
        ("discipline", "discipline"),
        ("author_role", "author"),
        ("audience", "audience"),
        ("purpose", "purpose"),
        ("stance", "stance"),
        ("title_shape", "title shape"),
        ("taboo_level", "taboo"),
    ):
        value = catalog.get(key)
        if value:
            bits.append(f"{label}: {value}")
    bits.append("do not describe the physical book")
    return "; ".join(bits)


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
        canon_provider: CanonProvider | None = None,
        canon_provider_name: str | None = None,
        deed_interpreter_provider: DeedInterpreterProvider | None = None,
        deed_interpreter_provider_name: str | None = None,
        character: CharacterProfile | None = None,
        replay_mode: bool = False,
    ) -> None:
        self.seed = seed
        self.scenario = scenario
        self.engine = GameEngine(
            seed=seed,
            scenario=scenario,
            provider_name=provider_name,
            character=character,
        )
        self.provider = provider or make_provider(provider_name)
        self.provider_label = getattr(self.provider, "name", "unknown")
        self.dialogue_provider = dialogue_provider or make_dialogue_provider(
            dialogue_provider_name
        )
        self.dialogue_provider_label = getattr(
            self.dialogue_provider, "name", "unknown"
        )
        self.trade_provider = trade_provider or make_trade_provider(trade_provider_name)
        self.trade_provider_label = getattr(self.trade_provider, "name", "unknown")
        resolved_lore_provider_name = lore_provider_name
        if resolved_lore_provider_name is None and provider_name in {
            "mock",
            "ollama",
            "auto",
        }:
            resolved_lore_provider_name = provider_name
        self.lore_provider = lore_provider or make_lore_provider(
            resolved_lore_provider_name
        )
        self.lore_provider_label = getattr(self.lore_provider, "name", "unknown")
        resolved_flesh_provider_name = flesh_provider_name
        if resolved_flesh_provider_name is None and provider_name in {
            "mock",
            "ollama",
            "auto",
        }:
            resolved_flesh_provider_name = provider_name
        self.flesh_provider = flesh_provider or make_flesh_provider(
            resolved_flesh_provider_name
        )
        self.flesh_provider_label = getattr(self.flesh_provider, "name", "unknown")
        resolved_canon_provider_name = canon_provider_name
        if resolved_canon_provider_name is None and provider_name in {
            "mock",
            "ollama",
            "auto",
        }:
            resolved_canon_provider_name = provider_name
        self.canon_provider = canon_provider or make_canon_provider(
            resolved_canon_provider_name
        )
        self.canon_provider_label = getattr(self.canon_provider, "name", "unknown")
        self.background_canon_provider = (
            canon_provider
            or make_background_canon_provider(resolved_canon_provider_name)
        )
        # The deed interpreter (A.2): classifies ambiguous spell outcomes into deeds. In
        # replay it is never called (the recorded verdict on the wild-magic action is
        # replayed); make() returns None when the purpose is "off", and the engine then
        # uses only the deterministic fallback.
        resolved_deeds_provider_name = deed_interpreter_provider_name
        if resolved_deeds_provider_name is None:
            # Inherit from the wild-magic provider — by name OR from an explicit provider
            # *object's* .name (so passing MockWildMagicProvider() also keeps the deed
            # interpreter off the network, not just passing provider_name="mock").
            inferred = provider_name or getattr(provider, "name", None)
            if inferred in {"mock", "ollama", "auto", "off", "none"}:
                resolved_deeds_provider_name = inferred
        self.deed_interpreter_provider = (
            deed_interpreter_provider
            if deed_interpreter_provider is not None
            else (
                None
                if replay_mode
                else make_deed_interpreter_provider(resolved_deeds_provider_name)
            )
        )
        self.deed_interpreter_label = getattr(
            self.deed_interpreter_provider, "name", "fallback"
        )
        # In replay mode, promises and flesh come from the recorded apply points;
        # background producers must stay silent.
        self.replay_mode = replay_mode
        self._lore_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._pending_lore: list[
            tuple[
                concurrent.futures.Future[LoreExtractionResolution],
                dict[str, Any],
                int | None,
            ]
        ] = []
        self._pending_flesh: list[
            tuple[concurrent.futures.Future[FleshResolution], str]
        ] = []
        self._pending_canon: list[
            tuple[concurrent.futures.Future[CanonResolution], CanonSaturationJob]
        ] = []
        self._queued_flesh_ids: set[str] = set()
        self._queued_canon_ids: set[str] = set()
        # Promise dicts applied to the engine since the last recorded action, snapshotted
        # pre-merge so replay can re-run the deterministic bind/merge at the same point.
        self._promise_apply_buffer: list[dict[str, Any]] = []
        self._flesh_apply_buffer: list[dict[str, Any]] = []
        self._canon_apply_buffer: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []

    def equipment_inventory_view(self) -> dict[str, Any]:
        """Read-only equipment/inventory model shared by CLI and GUI."""

        return equipment_inventory_view(self.engine)

    def execute_command(
        self,
        command: str,
        replay_wild_magic: dict[str, Any] | None = None,
        replay_dialogue: dict[str, Any] | None = None,
        replay_promises: dict[str, Any] | None = None,
        replay_flesh: dict[str, Any] | None = None,
        replay_canon: dict[str, Any] | None = None,
        record: bool = True,
    ) -> ActionResult:
        self.drain_lore(block=False)
        self._enqueue_flesh_for_bound_promises()
        self.drain_flesh(block=False)
        self.drain_canon_prewarm(block=False)
        self._enqueue_canon_prewarm()
        if replay_promises is not None:
            self.apply_recorded_promises(replay_promises.get("before"))
        if replay_flesh is not None:
            self.apply_recorded_flesh(replay_flesh.get("before"))
        if replay_canon is not None:
            self.apply_recorded_canon(replay_canon.get("before"))
        promises_before = self._pop_applied_promises() if record else []
        flesh_before = self._pop_applied_flesh() if record else []
        canon_before = self._pop_applied_canon() if record else []
        original_command = command.strip()
        turn_before = self.engine.state.turn
        # state.messages is capped to the last 80 entries, so len() stalls once the
        # log is full; the monotonic message_count survives the cap.
        message_count_before = self.engine.state.message_count
        action = "invalid"
        success = False
        technical_failure = False
        wild_magic_record: dict[str, Any] | None = None
        dialogue_record: dict[str, Any] | None = None
        canon_record: dict[str, Any] | None = None
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
            elif verb in {"world", "atlas", "survey"}:
                action = "world"
                success = True
                explicit_messages = describe_world(self.engine)
            elif verb in {"curses", "hexes"}:
                action = "curses"
                success = True
                explicit_messages = describe_curses(self.engine)
            elif verb in {"curse", "hex"}:
                action = "curse"
                success = True
                explicit_messages = describe_curses(
                    self.engine, command_argument(original_command, tokens)
                )
            elif (
                verb in {"clear", "cleanse"}
                and len(tokens) > 1
                and tokens[1].lower()
                in {
                    "curse",
                    "hex",
                }
            ):
                action = "clear_curse"
                target = " ".join(tokens[2:]).strip()
                success, explicit_messages = clear_curse(self.engine, target)
            elif verb in {"standing", "reputation", "rep", "factions"}:
                # Free action: how the world's powers regard you (the emergent-world
                # standing readout). Mirrored in the GUI panel and CLI footer.
                action = "standing"
                success = True
                explicit_messages = describe_standing(self.engine)
            elif verb in {"followers", "retinue", "bonds"}:
                # Free action: who follows you, and how the people you've met feel.
                action = "followers"
                success = True
                explicit_messages = describe_followers(self.engine)
            elif verb in {"found", "establish"}:
                action = "found"
                org_name = command_argument(original_command, tokens)
                if not org_name:
                    explicit_messages = [
                        "Found what? Name it, e.g. 'found the Ashen Hand'."
                    ]
                else:
                    self.engine.found_organization(org_name)
                    success = True
            elif verb in {"free", "release", "liberate", "unbind", "untie"}:
                # Free a bound captive on an adjacent tile (costs a turn). The deed, the
                # bond it seeds, and any secret they share all flow from general systems.
                action = "free"
                success = self.engine.free_captive()
            elif verb in {"tick", "simulate", "daytick"}:
                # Debug trigger for the daily world tick (Phase 0). A free action; the
                # real 05:00 cadence lands in Phase 0.5. Applies unapplied deeds once.
                action = "tick"
                success = True
                applied = self.engine.run_world_tick()
                explicit_messages = [
                    "The world turns. News and deeds are reckoned with."
                    if applied
                    else "The world turns. Nothing new to reckon with."
                ]
            elif verb in {"target", "mark", "aim"}:
                # Free action (no turn): mark a square as the explicit spell target.
                # The wild-magic resolver and the standard spells then aim there.
                action = "target"
                try:
                    tx, ty = int(tokens[1]), int(tokens[2])
                except (IndexError, ValueError):
                    explicit_messages = [
                        "Target where? Use 'target <x> <y>' (or click a square)."
                    ]
                else:
                    if self.engine.set_target(tx, ty):
                        success = True
                        occupant = self.engine.selected_target_entity()
                        label = occupant.name if occupant is not None else "that square"
                        self.engine.state.add_message(f"Target marked: {label}.")
                    else:
                        explicit_messages = [
                            f"Can't target ({tx}, {ty}) - out of bounds."
                        ]
            elif verb in {"untarget", "cleartarget", "clear_target", "unmark"}:
                action = "untarget"
                had_target = self.engine.has_target()
                self.engine.clear_target()
                success = True
                if had_target:
                    self.engine.state.add_message("Target cleared.")
            elif verb in {"examine", "study", "observe"}:
                action = "examine"
                success, technical_failure, canon_record, explicit_messages = (
                    self._examine_current_room(
                        replay_canon,
                    )
                )
            elif verb in {"read", "peruse"}:
                action = "read"
                success, technical_failure, canon_record, explicit_messages = (
                    self._read_book(
                        command_argument(original_command, tokens),
                        replay_canon,
                    )
                )
            elif verb in {"investigate", "search"}:
                action = "investigate"
                success, technical_failure, canon_record, explicit_messages = (
                    self._investigate(
                        command_argument(original_command, tokens),
                        replay_canon,
                    )
                )
            elif (
                verb in {"wares", "browse", "shop"}
                and self.engine.state.pending_trade is None
            ):
                action = "wares"
                success = True
                explicit_messages = self._browse_wares()
            elif self.engine.state.pending_trade is not None and verb in {
                "accept",
                "yes",
                "y",
            }:
                action = "trade_accept"
                success = True
                self.engine.resolve_pending_trade(True)
            elif self.engine.state.pending_trade is not None and verb in {
                "reject",
                "decline",
                "no",
                "n",
            }:
                action = "trade_reject"
                success = True
                self.engine.resolve_pending_trade(False)
            elif verb in {"wait", "."}:
                action = "wait"
                success = self.engine.wait_turn()
            elif verb in {"rest", "camp", "sleep"}:
                action = "rest"
                hours, until_hour, rest_error = _parse_rest_arg(
                    command_argument(original_command, tokens)
                )
                if rest_error is not None:
                    explicit_messages = [rest_error]
                elif until_hour is not None:
                    success = self.engine.camp_rest(until_hour=until_hour)
                else:
                    success = self.engine.camp_rest(hours=hours)
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
                    explicit_messages = [
                        f"Unknown standard spell: {spell_name or '(missing)'}"
                    ]
            elif verb in {"move", "go"}:
                action = "move"
                direction = tokens[1].lower() if len(tokens) > 1 else ""
                success = self._move(direction)
                if direction not in DIRECTIONS:
                    explicit_messages = [
                        f"Unknown direction: {direction or '(missing)'}"
                    ]
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
            elif verb in {"focus", "attune"}:
                action = "focus"
                item_name = command_argument(original_command, tokens)
                success = self.engine.set_focus(item_name) if item_name else False
                if not item_name:
                    explicit_messages = [
                        "Focus through what? Name an equipped item or slot."
                    ]
            elif verb in {"unfocus", "unattune"}:
                action = "unfocus"
                slot_name = command_argument(original_command, tokens)
                success = self.engine.clear_focus(slot_name)
            elif verb in {"possess", "swap", "inhabit"}:
                action = "possess"
                before_id = self.engine.state.player_id
                target = self._find_swap_target(
                    command_argument(original_command, tokens)
                )
                if target is None:
                    explicit_messages = ["No body within reach to inhabit."]
                else:
                    explicit_messages = self.engine.swap_control_to(target.id)
                    success = self.engine.state.player_id != before_id
                    if success:
                        self.engine.finish_player_turn()
            elif verb in {"cast", "wild"}:
                action = "cast"
                spell = command_argument(original_command, tokens)
                if "silenced" in self.engine.state.player.statuses:
                    explicit_messages = [
                        "You are silenced - the spell is swallowed before it can speak."
                    ]
                else:
                    success, technical_failure, wild_magic_record, llm_context = (
                        self._cast_wild(spell, replay_wild_magic)
                    )
            elif verb in {"talk", "speak", "say"}:
                action = "talk"
                message = command_argument(original_command, tokens)
                if "silenced" in self.engine.state.player.statuses:
                    explicit_messages = ["You are silenced - no words come out."]
                elif not message:
                    explicit_messages = [
                        "Say what? Specify what you want to say, e.g. 'talk hello there'."
                    ]
                else:
                    success, technical_failure, dialogue_record = self._talk(
                        message, replay_dialogue
                    )
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
                            explicit_messages.append(
                                f"  {idx}. {status_label} {q.name} - {q.description} (Contact: {q.contact}, Location: {q.location})"
                            )
                elif subverb == "add":
                    name = tokens[2] if len(tokens) > 2 else "Unknown Quest"
                    desc = tokens[3] if len(tokens) > 3 else ""
                    contact = (
                        tokens[4]
                        if len(tokens) > 4
                        else (self.engine.state.last_talked_npc_name or "None")
                    )
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
                            self.engine.state.add_message(
                                f"Quest marked completed: {completed.name}"
                            )
                            success = True
                        else:
                            explicit_messages = ["Invalid quest index."]
                    except (ValueError, IndexError):
                        explicit_messages = [
                            "Quest complete command requires a numeric index, e.g. 'quest complete 1'."
                        ]
                elif subverb == "remove":
                    try:
                        idx = int(tokens[2]) - 1
                        removed = self.engine.remove_quest_by_index(idx)
                        if removed is not None:
                            self.engine.state.add_message(
                                f"Quest removed: {removed.name}"
                            )
                            success = True
                        else:
                            explicit_messages = ["Invalid quest index."]
                    except (ValueError, IndexError):
                        explicit_messages = [
                            "Quest remove command requires a numeric index, e.g. 'quest remove 1'."
                        ]
                else:
                    explicit_messages = [
                        "Unknown quest subcommand. Use 'quest list', 'quest add', 'quest complete <index>', or 'quest remove <index>'."
                    ]
            else:
                explicit_messages = [f"Unknown command: {verb}"]

        turn_after = self.engine.state.turn
        consumed_turn = turn_after > turn_before
        if explicit_messages is not None:
            messages = explicit_messages
        else:
            new_message_count = self.engine.state.message_count - message_count_before
            messages = (
                self.engine.state.messages[-new_message_count:]
                if new_message_count > 0
                else []
            )
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
            canon_materialization=canon_record,
            llm_context=llm_context,
            should_quit=should_quit,
        )
        self.drain_lore(block=False)
        self._enqueue_flesh_for_bound_promises()
        self.drain_flesh(block=False)
        self._enqueue_canon_prewarm()
        self.drain_canon_prewarm(block=False)
        if replay_promises is not None:
            self.apply_recorded_promises(replay_promises.get("after"))
        if replay_flesh is not None:
            self.apply_recorded_flesh(replay_flesh.get("after"))
        if replay_canon is not None and action not in {
            "read",
            "examine",
            "investigate",
        }:
            self.apply_recorded_canon(replay_canon.get("after"))
        if record:
            action_record = result.to_record()
            promises_after = self._pop_applied_promises()
            flesh_after = self._pop_applied_flesh()
            canon_after = self._pop_applied_canon()
            if promises_before or promises_after:
                action_record["promises"] = {
                    "before": promises_before,
                    "after": promises_after,
                }
            if flesh_before or flesh_after:
                action_record["flesh"] = {"before": flesh_before, "after": flesh_after}
            if canon_before or canon_after:
                action_record["canon"] = {"before": canon_before, "after": canon_after}
            self.records.append(action_record)
        return result

    def cast_wild(self, spell: str, record: bool = True) -> ActionResult:
        return self.execute_command(f"cast {spell}", record=record)

    def _find_swap_target(self, name: str) -> Any | None:
        """Pick a body to inhabit for the `possess` command: the named creature if one
        matches, otherwise the nearest inhabitable entity. Husks are eligible, so you
        can leap back into a body you left behind."""
        state = self.engine.state
        player = state.player
        candidates = [
            entity
            for entity in state.entities.values()
            if entity.kind in {"actor", "npc"}
            and entity.alive
            and entity.id != state.player_id
        ]
        wanted = name.strip().lower()
        if wanted:
            named = [entity for entity in candidates if wanted in entity.name.lower()]
            if named:
                candidates = named
        if not candidates:
            return None
        return min(candidates, key=lambda entity: self.engine.distance(player, entity))

    def _present_canon(self, record: CanonRecord) -> list[str]:
        """Canon prose goes through the message log so every frontend shows it —
        the pygame UI renders only state messages, not ActionResult lines.
        Free retells (reuse paths) skip re-logging while the same prose is still
        in recent history, so repeated keypresses don't flood the log. Books log
        only title and summary — their full pages belong to the reading view
        (and to the CLI via the returned lines), not a combat log."""
        lines = _canon_display_lines(record)
        if record.kind == "book":
            log_lines = [line for line in (record.title, record.summary) if line]
        else:
            log_lines = lines
        recent = set(self.engine.state.messages[-30:])
        if log_lines and not all(line in recent for line in log_lines):
            for line in log_lines:
                self.engine.state.add_message(line)
        return lines

    def _examine_current_room(
        self,
        replay_canon: dict[str, Any] | None = None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        room = self.engine.room_profile_at(
            self.engine.state.player.x, self.engine.state.player.y
        )
        if room is None:
            return False, False, None, ["There is no coherent place here to examine."]
        record_id = f"canon_room_{normalize_id(room.id)}"
        existing = self.engine.state.canon_records.get(record_id)
        replayed_materialization = False
        if existing is None and replay_canon is not None:
            self.apply_recorded_canon(replay_canon.get("after"))
            existing = self.engine.state.canon_records.get(record_id)
            replayed_materialization = existing is not None
            replayed_failure = replay_canon.get("materialization")
            if (
                existing is None
                and isinstance(replayed_failure, dict)
                and replayed_failure.get("technical_failure")
            ):
                return (
                    False,
                    True,
                    replayed_failure,
                    [
                        f"The place resists description. ({replayed_failure.get('error')})"
                    ],
                )
        if existing is not None:
            if replayed_materialization:
                self.engine.state.add_message(
                    f"You take time to study {room.room_type}."
                )
                self.engine.finish_player_turn()
                return (
                    True,
                    False,
                    {"record": existing.to_dict(), "replayed": True},
                    self._present_canon(existing),
                )
            return (
                True,
                False,
                {"record": existing.to_dict(), "reused": True},
                self._present_canon(existing),
            )

        context = self._canon_context_for_room(room, record_id)
        resolution = resolve_canon(self.canon_provider, context)
        self.canon_provider_label = resolution.provider_name
        canon_record = {
            "kind": "room_flavor",
            "provider": resolution.provider_name,
            "technical_failure": resolution.technical_failure,
            "error": resolution.error,
            "record": resolution.record.to_dict() if resolution.record else None,
            "raw_response": resolution.raw_response,
            "audit_path": resolution.audit_path,
        }
        if resolution.technical_failure or resolution.record is None:
            return (
                False,
                True,
                canon_record,
                [f"The place resists description. ({resolution.error})"],
            )
        applied = self.engine.add_canon_record(resolution.record)
        self._canon_apply_buffer.append(applied.to_dict())
        self.engine.state.add_message(f"You take time to study {room.room_type}.")
        self.engine.finish_player_turn()
        return True, False, canon_record, self._present_canon(applied)

    def _canon_context_for_room(self, room: Any, record_id: str) -> dict[str, Any]:
        state = self.engine.state
        props = [
            {
                "id": entity.id,
                "name": entity.name,
                "description": entity.description,
                "tags": sorted(entity.tags),
            }
            for entity in state.entities.values()
            if entity.kind == "prop" and room.contains(entity.x, entity.y)
        ][:6]
        threads = self.engine.nearby_canon_records(
            tags=[*room.tags, *room.topics], limit=4
        )
        active_promises = [
            promise.to_dict()
            for promise in state.promises
            if promise.id in room.promise_hooks or set(promise.tags) & set(room.tags)
        ][:2]
        base_tags = sorted({*room.tags, *room.topics, "room_flavor"})
        return {
            "record_id": record_id,
            "kind": "room_flavor",
            "source": "ondemand",
            "turn": state.turn,
            "world": {
                "location": state.location_label(),
                "region": self.engine.region.prompt_style(),
                "scenario": state.scenario,
                "depth": state.depth,
                "zone": {"x": state.zone_x, "y": state.zone_y, "type": state.zone_type},
            },
            "place": {
                "room": room.to_public_dict(),
                "nearby_props": props,
            },
            "subject": {
                "room": room.to_public_dict(),
                "attachment": {"kind": "room", "room_id": room.id},
            },
            "threads": {
                "canon": threads,
                "promises": active_promises,
            },
            "contract": {
                "allowed_outputs": ["title", "summary", "text", "tags", "llm_choices"],
                "claim_quota": 0,
                "forbidden": [
                    "treasure",
                    "exits",
                    "enemies",
                    "allies",
                    "quests",
                    "stats",
                    "map changes",
                ],
            },
            "base_tags": base_tags,
            "allowed_tags": sorted(
                {
                    *base_tags,
                    "empire",
                    "magic",
                    "ritual",
                    "lore",
                    "holy",
                    "death",
                    "water",
                    "plant",
                    "book",
                    "books",
                }
            ),
            "engine_choices": {
                "mechanical_effect": "none",
                "turn_cost": 1,
            },
        }

    def _find_readable_book(self, target: str) -> Any | None:
        """A readable book prop on the player's tile or an adjacent one. With a
        target string, prefer name matches; otherwise the nearest book wins."""
        player = self.engine.state.player
        wanted = normalize_id(target) if target else ""
        candidates = []
        for entity in self.engine.state.entities.values():
            if entity.kind != "prop" or "book" not in entity.tags:
                continue
            distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
            if distance > 1:
                continue
            name_match = bool(wanted) and wanted in normalize_id(entity.name)
            candidates.append((not name_match, distance, entity.id, entity))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:3])
        if wanted and candidates[0][0]:
            return None
        return candidates[0][3]

    def _read_book(
        self,
        target: str,
        replay_canon: dict[str, Any] | None = None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        book = self._find_readable_book(target)
        if book is None:
            if target:
                return (
                    False,
                    False,
                    None,
                    [f"There is no book called '{target}' within reach."],
                )
            return False, False, None, ["There is no book within reach to read."]
        record_id = f"canon_book_{normalize_id(book.id)}"
        existing = self.engine.state.canon_records.get(record_id)
        already_read = bool(book.details.get("read"))

        # Replay path: the pages were materialized live (an on-demand read, or a
        # background prewarm that drained during this action) and recorded at this
        # apply point. Inject them before deciding anything.
        if existing is None and replay_canon is not None:
            self.apply_recorded_canon(replay_canon.get("after"))
            existing = self.engine.state.canon_records.get(record_id)
            replayed_failure = replay_canon.get("materialization")
            if (
                existing is None
                and isinstance(replayed_failure, dict)
                and replayed_failure.get("technical_failure")
            ):
                return (
                    False,
                    True,
                    replayed_failure,
                    [
                        f"The ink swims and refuses to settle. ({replayed_failure.get('error')})"
                    ],
                )

        # If a background prewarm of this book's pages is already in flight, wait for
        # it and reuse it rather than launching a second, divergent generation on the
        # urgent channel — that double-generation is what made a read show different
        # text than the prewarmed pages. (Replay reuses recorded canon, never blocks.)
        if (
            existing is None
            and not self.replay_mode
            and record_id in self._queued_canon_ids
        ):
            self.drain_canon_prewarm(block=True)
            existing = self.engine.state.canon_records.get(record_id)

        canon_record: dict[str, Any] | None = None
        if existing is None:
            # Not prewarmed (and none in flight, or it failed): the player is blocked,
            # so materialize the full pages on the urgent channel now.
            context = self._canon_context_for_book(book, record_id)
            resolution = resolve_canon(self.canon_provider, context)
            self.canon_provider_label = resolution.provider_name
            canon_record = {
                "kind": "book",
                "provider": resolution.provider_name,
                "technical_failure": resolution.technical_failure,
                "error": resolution.error,
                "record": resolution.record.to_dict() if resolution.record else None,
                "raw_response": resolution.raw_response,
                "audit_path": resolution.audit_path,
            }
            if resolution.technical_failure or resolution.record is None:
                return (
                    False,
                    True,
                    canon_record,
                    [f"The ink swims and refuses to settle. ({resolution.error})"],
                )
            existing = self.engine.add_canon_record(resolution.record)
            self._canon_apply_buffer.append(existing.to_dict())

        # The book's canon now exists (prewarmed in the background, freshly
        # materialized above, or replayed). Its title/summary become the in-world
        # identity whether or not the player has spent a turn reading it.
        self._apply_book_canon(book, existing)
        record = canon_record or {"record": existing.to_dict(), "reused": True}
        if already_read:
            # Free reread: the pages are already canon — no turn, no re-extraction.
            return True, False, record, self._present_canon(existing)

        # First read consumes the turn and harvests lore claims from the pages.
        book.details["read"] = True
        self.engine.state.add_message(f"You read {existing.title or book.name}.")
        self.engine.finish_player_turn()
        if not self.replay_mode:
            self._enqueue_lore_extraction(
                self._lore_context_for_book(book, existing),
                record,
                claim_quota=BOOK_CLAIM_QUOTA,
            )
        return True, False, record, self._present_canon(existing)

    def _apply_book_canon(self, book: Any, record: Any) -> None:
        """Materialized title and summary become the book's in-world identity."""
        if record.title:
            book.name = record.title
            book.details["title_materialized"] = True
        if record.summary:
            book.description = record.summary
            book.details["summary_materialized"] = True
        author = (
            record.llm_choices.get("author")
            if isinstance(record.llm_choices, dict)
            else None
        )
        if author and record.summary and author not in record.summary:
            book.description = f"{record.summary} (by {author})"

    def _book_catalog(self, book: Any) -> tuple[dict[str, str], list[str]]:
        """A book's grammar seed split into (scalar catalog fields, subjects list).
        Subjects are the book's durable 1-4-topic metadata; they fall back to the
        catalog's topic axes for books seeded before subjects were recorded."""
        raw_seed: dict[str, Any] = {}
        if isinstance(getattr(book, "details", None), dict):
            candidate = book.details.get("book_seed")
            if isinstance(candidate, dict):
                raw_seed = candidate
        catalog = {
            str(key): str(value)
            for key, value in raw_seed.items()
            if isinstance(value, (str, int, float)) and str(value).strip()
        }
        subjects = [
            str(subject).strip()
            for subject in (raw_seed.get("subjects") or [])
            if str(subject).strip()
        ]
        if not subjects:
            subjects = [
                catalog[key]
                for key in ("topic", "secondary_topic", "discipline")
                if catalog.get(key)
            ]
        return catalog, subjects[:4]

    def _canon_context_for_book(self, book: Any, record_id: str) -> dict[str, Any]:
        state = self.engine.state
        room = self.engine.room_profile_at(book.x, book.y)
        room_dict = room.to_public_dict() if room is not None else None
        room_tags = list(room.tags) if room is not None else []
        room_topics = list(room.topics) if room is not None else []
        book_seed, subjects = self._book_catalog(book)
        seed_tags = {
            normalize_id(str(value))
            for key, value in book_seed.items()
            if key
            in {
                "topic",
                "secondary_topic",
                "genre",
                "discipline",
                "author_role",
                "institution",
                "taboo_level",
            }
        }
        thread_tags = sorted({*book.tags, *room_tags, *room_topics, *seed_tags})
        threads = self.engine.nearby_canon_records(tags=thread_tags, limit=4)
        active_promises = [
            promise.to_dict()
            for promise in state.promises
            if (room is not None and promise.id in room.promise_hooks)
            or set(promise.tags) & set(thread_tags)
        ][:2]
        base_tags = sorted({*book.tags, "book"})
        title_id = f"canon_book_title_{normalize_id(book.id)}"
        title_record = self.engine.state.canon_records.get(title_id)
        materialized_title = title_record.title if title_record else None
        # Static authored world-canon for the THREADS slot, gated by the book's subjects
        # (docs/LORE_CARDS.md §10.2). The book may echo it in passing; it is background.
        lore_threads = [
            card.text
            for card in book_lore_cards(subjects, materialized_title or book.name)
        ]
        thread_block: dict[str, Any] = {
            "canon": threads,
            "promises": active_promises,
        }
        if lore_threads:
            thread_block["lore"] = lore_threads
        literary_catalog = {
            key: book_seed[key]
            for key in _BOOK_TEXT_CATALOG_FIELDS
            if book_seed.get(key)
        }
        book_context = {
            "catalog": literary_catalog,
            "subjects": subjects,
        }
        if materialized_title:
            book_context["title"] = materialized_title
        book_focus = _book_focus_line(
            literary_catalog, subjects, materialized_title, pages=True
        )
        return {
            "book_focus": book_focus,
            "record_id": record_id,
            "kind": "book",
            "source": "ondemand",
            "turn": state.turn,
            "world": {
                "location": state.location_label(),
                "region": self.engine.region.prompt_style(),
                "scenario": state.scenario,
                "depth": state.depth,
                "zone": {"x": state.zone_x, "y": state.zone_y, "type": state.zone_type},
            },
            "place": {
                "room": room_dict,
            },
            "subject": {
                "book": book_context,
            },
            "threads": thread_block,
            "contract": {
                "allowed_outputs": ["title", "summary", "text", "tags", "llm_choices"],
                "claim_quota": BOOK_CLAIM_QUOTA,
                "book_guidance": {
                    "use_catalog_fields": [
                        "genre",
                        "discipline",
                        "author_role",
                        "audience",
                        "purpose",
                        "stance",
                        "institution",
                        "title_shape",
                        "taboo_level",
                    ],
                    "avoid_defaulting_to": [
                        "ink",
                        "maps",
                        "cartography",
                        "book damage",
                    ],
                },
                "forbidden": [
                    "treasure locations",
                    "guaranteed allies",
                    "map exits",
                    "named player rewards",
                    "stats",
                    "spell effects",
                ],
            },
            "base_tags": base_tags,
            "allowed_tags": sorted(
                {
                    *base_tags,
                    *thread_tags,
                    "empire",
                    "magic",
                    "ritual",
                    "lore",
                    "holy",
                    "death",
                    "water",
                    "plant",
                }
            ),
            "engine_choices": {
                "mechanical_effect": "none",
                "turn_cost": 1,
            },
            "engine_private": {
                "attachment": {"kind": "prop", "entity_id": book.id},
            },
            "book_focus_reminder": book_focus,
        }

    def _canon_context_for_book_title(
        self, book: Any, record_id: str
    ) -> dict[str, Any]:
        """A deliberately tiny seed packet for the always-on title call: just the
        subjects and the catalog axes that shape a title. No world/place/threads
        block, so the prompt stays short and the call stays cheap — a title has no
        mechanical stakes, so minimal context is enough."""
        state = self.engine.state
        catalog, subjects = self._book_catalog(book)
        title_catalog = {
            key: catalog[key]
            for key in ("genre", "discipline", "title_shape", "taboo_level", "era")
            if catalog.get(key)
        }
        book_focus = _book_focus_line(title_catalog, subjects, None, pages=False)
        base_tags = sorted({*book.tags, "book", "book_title"})
        return {
            "book_focus": book_focus,
            "record_id": record_id,
            "kind": "book_title",
            "source": "background",
            "turn": state.turn,
            "world": {"region": self.engine.region.prompt_style()},
            "subject": {
                "book": {
                    "id": book.id,
                    "subjects": subjects,
                    "catalog": title_catalog,
                },
                "attachment": {"kind": "prop", "entity_id": book.id},
            },
            "contract": {
                "allowed_outputs": ["title"],
                "claim_quota": 0,
                "forbidden": [
                    "author",
                    "summary",
                    "pages",
                    "stats",
                    "spell effects",
                ],
            },
            "base_tags": base_tags,
            "allowed_tags": base_tags,
            "engine_choices": {"turn_cost": 0},
            "book_focus_reminder": book_focus,
        }

    def _lore_context_for_book(self, book: Any, record: Any) -> dict[str, Any]:
        """Book pages run through the same lore extraction as dialogue; the
        passage stands in for the NPC reply and the source records the book."""
        state = self.engine.state
        author = (
            record.llm_choices.get("author")
            if isinstance(record.llm_choices, dict)
            else None
        )
        title = record.title or book.name
        return {
            "npc": str(author or title),
            "source": f"book:{title}",
            "turn": state.turn,
            "location": state.location_label(),
            "zone": {"x": state.zone_x, "y": state.zone_y, "type": state.zone_type},
            "message": "(the player reads a book)",
            "reply": record.text,
            "existing_lore": self.engine.promises_for_context(
                subject=title, tags=book.tags, limit=5, text_limit=160
            ),
        }

    def _investigate(
        self,
        target: str,
        replay_canon: dict[str, Any] | None = None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        """Knowledge-gated secret search. The engine owns whether a secret
        exists (RoomProfile secret slots placed at generation), what anchors
        it, and what the reward is; the LLM only words the clue. Stages:
        sweep finds the clue, investigating the clued anchor opens it."""
        room = self.engine.room_profile_at(
            self.engine.state.player.x, self.engine.state.player.y
        )
        if room is None:
            return (
                False,
                False,
                None,
                ["There is no coherent place here to investigate."],
            )
        slot = next((s for s in room.secret_slots if s.get("status") != "opened"), None)
        if target:
            return self._investigate_focused(room, slot, target, replay_canon)
        return self._investigate_sweep(room, slot, replay_canon)

    def _investigate_focused(
        self,
        room: Any,
        slot: dict[str, Any] | None,
        target: str,
        replay_canon: dict[str, Any] | None = None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str] | None]:
        """Targeted study of one named thing. Three outcomes: the clued anchor
        opens (deterministic reveal, no provider); a matched visible entity gets
        an LLM detail record; an unmatched name costs nothing."""
        wanted = normalize_id(target)
        anchor = normalize_id(str(slot.get("anchor") or "")) if slot is not None else ""
        if (
            slot is not None
            and slot.get("status") == "clued"
            and wanted
            and anchor
            and (wanted in anchor or anchor in wanted)
        ):
            return self._open_secret(room, slot)
        entity = self._find_investigation_target(target)
        if entity is None:
            return (
                False,
                False,
                None,
                [f"You see no '{target.strip()}' here to investigate."],
            )
        return self._investigate_entity(room, slot, entity, replay_canon)

    def _open_secret(
        self,
        room: Any,
        slot: dict[str, Any],
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        """Deterministic reveal — the engine chose the reward before the clue
        was ever worded; no provider call happens here."""
        engine = self.engine
        reward = dict(slot.get("reward") or {})
        name = str(reward.get("name") or "trinket")
        quantity = max(1, int(reward.get("quantity") or 1))
        engine.state.inventory[name] = engine.state.inventory.get(name, 0) + quantity
        engine.state.stats.items_collected += 1
        slot["status"] = "opened"
        qty_text = f"{quantity} {name}" if quantity > 1 else name
        text = (
            f"Following the {slot.get('clue_style', 'marks')}, you work at "
            f"{slot.get('anchor')} until it gives: {secret_kind_label(slot)}. "
            f"Inside: {qty_text}."
        )
        record = engine.add_canon_record(
            CanonRecord(
                id=f"canon_secret_open_{normalize_id(str(slot.get('id')))}",
                kind="investigation",
                attachment={
                    "kind": "room",
                    "room_id": room.id,
                    "secret_id": slot.get("id"),
                },
                text=text,
                summary=f"Found {qty_text} in {secret_kind_label(slot)}.",
                tags=sorted({"investigation", "secret", *list(room.tags)[:3]}),
                source="engine",
                seed_packet={"secret_id": slot.get("id"), "room_id": room.id},
                engine_choices={
                    "secret_id": slot.get("id"),
                    "reward": reward,
                    "anchor": slot.get("anchor"),
                },
                turn_created=engine.state.turn,
            )
        )
        self._canon_apply_buffer.append(record.to_dict())
        engine.state.add_message(text)
        engine.finish_player_turn()
        return True, False, {"record": record.to_dict(), "engine": True}, [text]

    def _find_investigation_target(self, target: str) -> Any | None:
        """Resolve a visible prop, item, NPC, or creature by entity id (exact,
        as the UI buttons send) or by name fragment, nearest first."""
        engine = self.engine
        player = engine.state.player
        wanted = normalize_id(target)
        if not wanted:
            return None
        candidates = []
        for entity in engine.state.entities.values():
            if entity.id == engine.state.player_id or entity.kind not in {
                "prop",
                "item",
                "npc",
                "actor",
            }:
                continue
            if entity.kind == "actor" and not entity.alive:
                continue
            if not engine.is_visible(entity.x, entity.y):
                continue
            if normalize_id(entity.id) == wanted:
                return entity
            if wanted in normalize_id(entity.name):
                distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
                candidates.append((distance, entity.id, entity))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:2])
        return candidates[0][2]

    def _investigate_entity(
        self,
        room: Any,
        slot: dict[str, Any] | None,
        entity: Any,
        replay_canon: dict[str, Any] | None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        """LLM detail study of one entity. Two record tiers: a look from afar
        and a close study; close supersedes far, and a far record never blocks
        earning the close one by walking up."""
        engine = self.engine
        state = engine.state
        player = state.player
        distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
        adjacent = distance <= 1
        band = (
            "adjacent" if adjacent else ("near" if distance <= 4 else "across the room")
        )
        close_id = f"canon_detail_{normalize_id(entity.id)}_close"
        far_id = f"canon_detail_{normalize_id(entity.id)}_far"
        record_id = close_id if adjacent else far_id

        # Best existing knowledge serves free: close always, far for far looks.
        best = state.canon_records.get(close_id)
        if best is None and not adjacent:
            best = state.canon_records.get(far_id)
        if best is not None:
            return (
                True,
                False,
                {"record": best.to_dict(), "reused": True},
                self._present_canon(best),
            )

        # Adjacent study of the prop anchoring a hidden secret surfaces the clue.
        secret_here = (
            adjacent
            and slot is not None
            and not slot.get("status")
            and entity.kind == "prop"
        )
        if secret_here:
            rng = random.Random(
                stable_seed(state.rng_seed, "secret_choices", str(slot.get("id")))
            )
            props = [
                e
                for e in state.entities.values()
                if e.kind == "prop" and room is not None and room.contains(e.x, e.y)
            ]
            slot.setdefault("anchor", choose_anchor(slot, props, rng))
            slot.setdefault("reward", choose_reward(slot, rng))
            secret_here = normalize_id(str(slot.get("anchor"))) == normalize_id(
                entity.name
            )

        existing = state.canon_records.get(record_id)
        replayed = False
        if existing is None and replay_canon is not None:
            self.apply_recorded_canon(replay_canon.get("after"))
            existing = state.canon_records.get(record_id)
            replayed = existing is not None
            failure = replay_canon.get("materialization")
            if (
                existing is None
                and isinstance(failure, dict)
                and failure.get("technical_failure")
            ):
                return (
                    False,
                    True,
                    failure,
                    [f"You can make nothing more of it. ({failure.get('error')})"],
                )
        if existing is not None:
            if secret_here:
                slot["status"] = "clued"
                slot["clue_record"] = record_id
            if replayed:
                state.add_message(f"You study {entity.name}.")
                engine.finish_player_turn()
                return (
                    True,
                    False,
                    {"record": existing.to_dict(), "replayed": True},
                    self._present_canon(existing),
                )
            return (
                True,
                False,
                {"record": existing.to_dict(), "reused": True},
                self._present_canon(existing),
            )

        context = self._canon_context_for_entity(
            room, slot if secret_here else None, entity, band, record_id
        )
        resolution = resolve_canon(self.canon_provider, context)
        self.canon_provider_label = resolution.provider_name
        canon_record = {
            "kind": context["kind"],
            "provider": resolution.provider_name,
            "technical_failure": resolution.technical_failure,
            "error": resolution.error,
            "record": resolution.record.to_dict() if resolution.record else None,
            "raw_response": resolution.raw_response,
            "audit_path": resolution.audit_path,
        }
        if resolution.technical_failure or resolution.record is None:
            return (
                False,
                True,
                canon_record,
                [f"You can make nothing more of it. ({resolution.error})"],
            )
        applied = engine.add_canon_record(resolution.record)
        # Mirror the replay path (apply_recorded_canon) so live investigate writes item_lore
        # through the same helper rather than a separate code path.
        self._apply_canon_record_side_effects(applied)
        self._canon_apply_buffer.append(applied.to_dict())
        if secret_here:
            slot["status"] = "clued"
            slot["clue_record"] = record_id
        state.add_message(f"You study {entity.name}.")
        engine.finish_player_turn()
        if entity.kind == "npc" and not self.replay_mode:
            quota = int(context.get("contract", {}).get("claim_quota") or 0)
            if quota > 0:
                self._enqueue_lore_extraction(
                    self._lore_context_for_detail(entity, applied),
                    canon_record,
                    claim_quota=quota,
                )
        return True, False, canon_record, self._present_canon(applied)

    def _canon_context_for_entity(
        self,
        room: Any,
        secret_slot: dict[str, Any] | None,
        entity: Any,
        band: str,
        record_id: str,
    ) -> dict[str, Any]:
        engine = self.engine
        state = engine.state
        kind = {
            "prop": "object_detail",
            "item": "object_detail",
            "npc": "npc_detail",
        }.get(entity.kind, "creature_detail")
        subject: dict[str, Any] = {
            "name": entity.name,
            "entity_kind": entity.kind,
            "tags": sorted(entity.tags),
            "material": entity.material,
            "current_description": entity.description,
            "distance_band": band,
            "attachment": {"kind": "entity", "entity_id": entity.id},
        }
        engine_choices: dict[str, Any] = {
            "mechanical_effect": "none",
            "turn_cost": 1,
            "distance_band": band,
        }
        if entity.kind == "item":
            # Carry the inventory key (item_type or name) so the canon side-effect can
            # store the materialized description in item_lore under the same key the
            # inventory/equipment use — even at replay time when the source entity is gone.
            engine_choices["item_inventory_key"] = entity.item_type or entity.name
            engine_choices["item_display_name"] = entity.name
        claim_quota = 0
        if entity.kind == "npc":
            profile = state.npc_profiles.get(entity.id)
            if profile is not None:
                subject["person"] = {
                    "role": profile.role,
                    "appearance": profile.appearance,
                    "traits": list(profile.traits),
                    "wares": sorted(profile.wares) if band == "adjacent" else [],
                }
            claim_quota = 1
        elif entity.kind == "actor":
            rng = random.Random(
                stable_seed(state.rng_seed, "weakness_hint", entity.id, record_id)
            )
            engine_choices["weakness_hint"] = choose_weakness_hint(entity, rng)
            subject["creature"] = {
                "faction": entity.faction,
                "statuses": sorted(entity.statuses),
            }
        if secret_slot is not None:
            engine_choices.update(
                {
                    "secret_present": True,
                    "secret_kind": secret_slot.get("kind"),
                    "clue_style": secret_slot.get("clue_style"),
                    "anchor_name": secret_slot.get("anchor"),
                    "reward_name": (secret_slot.get("reward") or {}).get("name"),
                }
            )
        room_tags = list(room.tags) if room is not None else []
        thread_tags = sorted({*entity.tags, *room_tags})
        base_tags = sorted({*[normalize_id(t) for t in entity.tags if t], kind})
        return {
            "record_id": record_id,
            "kind": kind,
            "source": "ondemand",
            "turn": state.turn,
            "world": {
                "location": state.location_label(),
                "region": engine.region.prompt_style(),
                "scenario": state.scenario,
                "depth": state.depth,
                "zone": {"x": state.zone_x, "y": state.zone_y, "type": state.zone_type},
            },
            "place": {"room": room.to_public_dict() if room is not None else None},
            "subject": subject,
            "threads": {
                "canon": engine.nearby_canon_records(tags=thread_tags, limit=3),
                "promises": [
                    promise.to_dict()
                    for promise in state.promises
                    if set(promise.tags) & set(thread_tags)
                ][:2],
            },
            "contract": {
                "allowed_outputs": ["title", "summary", "text", "tags", "llm_choices"],
                "claim_quota": claim_quota,
                "forbidden": [
                    "inventing secrets or rewards",
                    "stats or numbers",
                    "new items",
                    "exits",
                    "map changes",
                    "details that require touch when distance_band is not adjacent",
                ],
            },
            "base_tags": base_tags,
            "allowed_tags": sorted(
                {*base_tags, *[normalize_id(t) for t in thread_tags if t], "secret"}
            ),
            "engine_choices": engine_choices,
        }

    def _lore_context_for_detail(self, entity: Any, record: Any) -> dict[str, Any]:
        state = self.engine.state
        return {
            "npc": entity.name,
            "source": f"observation:{entity.name}",
            "turn": state.turn,
            "location": state.location_label(),
            "zone": {"x": state.zone_x, "y": state.zone_y, "type": state.zone_type},
            "message": "(the player studies them closely)",
            "reply": record.text,
            "existing_lore": self.engine.promises_for_context(
                subject=entity.name, tags=entity.tags, limit=5, text_limit=160
            ),
        }

    def _investigate_sweep(
        self,
        room: Any,
        slot: dict[str, Any] | None,
        replay_canon: dict[str, Any] | None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        engine = self.engine
        state = engine.state
        if slot is not None and slot.get("status") == "clued":
            clue = state.canon_records.get(str(slot.get("clue_record") or ""))
            if clue is not None:
                return (
                    True,
                    False,
                    {"record": clue.to_dict(), "reused": True},
                    self._present_canon(clue),
                )
        if slot is not None and not slot.get("status"):
            return self._investigate_clue_stage(room, slot, replay_canon)
        return self._investigate_plain_stage(room, replay_canon)

    def _investigate_clue_stage(
        self,
        room: Any,
        slot: dict[str, Any],
        replay_canon: dict[str, Any] | None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        engine = self.engine
        state = engine.state
        record_id = f"canon_secret_clue_{normalize_id(str(slot.get('id')))}"
        # Engine choices are fixed before any prompt is built, deterministically
        # from the run seed and slot id — replay-safe and provider-independent.
        rng = random.Random(
            stable_seed(state.rng_seed, "secret_choices", str(slot.get("id")))
        )
        props = [
            e
            for e in state.entities.values()
            if e.kind == "prop" and room.contains(e.x, e.y)
        ]
        slot.setdefault("anchor", choose_anchor(slot, props, rng))
        slot.setdefault("reward", choose_reward(slot, rng))

        existing = state.canon_records.get(record_id)
        replayed = False
        if existing is None and replay_canon is not None:
            self.apply_recorded_canon(replay_canon.get("after"))
            existing = state.canon_records.get(record_id)
            replayed = existing is not None
            failure = replay_canon.get("materialization")
            if (
                existing is None
                and isinstance(failure, dict)
                and failure.get("technical_failure")
            ):
                return (
                    False,
                    True,
                    failure,
                    [f"Nothing here holds your attention. ({failure.get('error')})"],
                )
        if existing is not None:
            slot["status"] = "clued"
            slot["clue_record"] = record_id
            if replayed:
                self._consume_investigation_turns(room, slot)
                return (
                    True,
                    False,
                    {"record": existing.to_dict(), "replayed": True},
                    self._present_canon(existing),
                )
            return (
                True,
                False,
                {"record": existing.to_dict(), "reused": True},
                self._present_canon(existing),
            )

        context = self._canon_context_for_investigation(room, slot, record_id)
        resolution = resolve_canon(self.canon_provider, context)
        self.canon_provider_label = resolution.provider_name
        canon_record = {
            "kind": "investigation",
            "provider": resolution.provider_name,
            "technical_failure": resolution.technical_failure,
            "error": resolution.error,
            "record": resolution.record.to_dict() if resolution.record else None,
            "raw_response": resolution.raw_response,
            "audit_path": resolution.audit_path,
        }
        if resolution.technical_failure or resolution.record is None:
            return (
                False,
                True,
                canon_record,
                [f"Nothing here holds your attention. ({resolution.error})"],
            )
        applied = engine.add_canon_record(resolution.record)
        self._canon_apply_buffer.append(applied.to_dict())
        slot["status"] = "clued"
        slot["clue_record"] = record_id
        self._consume_investigation_turns(room, slot)
        return True, False, canon_record, self._present_canon(applied)

    def _investigate_plain_stage(
        self,
        room: Any,
        replay_canon: dict[str, Any] | None,
    ) -> tuple[bool, bool, dict[str, Any] | None, list[str]]:
        """No live secret here (none placed, or already opened). The model gets
        secret_present=false and can only describe — by construction nothing
        mechanical can come of it."""
        engine = self.engine
        state = engine.state
        record_id = f"canon_invest_{normalize_id(room.id)}"
        existing = state.canon_records.get(record_id)
        replayed = False
        if existing is None and replay_canon is not None:
            self.apply_recorded_canon(replay_canon.get("after"))
            existing = state.canon_records.get(record_id)
            replayed = existing is not None
            failure = replay_canon.get("materialization")
            if (
                existing is None
                and isinstance(failure, dict)
                and failure.get("technical_failure")
            ):
                return (
                    False,
                    True,
                    failure,
                    [f"Nothing here holds your attention. ({failure.get('error')})"],
                )
        if existing is not None:
            if replayed:
                state.add_message(
                    f"You search the {room.room_type} from corner to corner."
                )
                self._spawn_sweep_decoration(existing)
                engine.finish_player_turn()
                return (
                    True,
                    False,
                    {"record": existing.to_dict(), "replayed": True},
                    self._present_canon(existing),
                )
            return (
                True,
                False,
                {"record": existing.to_dict(), "reused": True},
                self._present_canon(existing),
            )

        context = self._canon_context_for_investigation(room, None, record_id)
        resolution = resolve_canon(self.canon_provider, context)
        self.canon_provider_label = resolution.provider_name
        canon_record = {
            "kind": "investigation",
            "provider": resolution.provider_name,
            "technical_failure": resolution.technical_failure,
            "error": resolution.error,
            "record": resolution.record.to_dict() if resolution.record else None,
            "raw_response": resolution.raw_response,
            "audit_path": resolution.audit_path,
        }
        if resolution.technical_failure or resolution.record is None:
            return (
                False,
                True,
                canon_record,
                [f"Nothing here holds your attention. ({resolution.error})"],
            )
        applied = engine.add_canon_record(resolution.record)
        self._canon_apply_buffer.append(applied.to_dict())
        state.add_message(f"You search the {room.room_type} from corner to corner.")
        self._spawn_sweep_decoration(applied)
        engine.finish_player_turn()
        return True, False, canon_record, self._present_canon(applied)

    def _decoration_spot(self, room: Any, rng: random.Random) -> tuple[int, int] | None:
        candidates = [
            (x, y)
            for y in range(room.y + 1, room.y + room.h - 1)
            for x in range(room.x + 1, room.x + room.w - 1)
            if self.engine.in_bounds(x, y) and self.engine.can_occupy(x, y)
        ]
        if not candidates:
            return None
        rng.shuffle(candidates)
        return candidates[0]

    def _spawn_sweep_decoration(self, record: Any) -> None:
        """Surface the LLM's chosen decoration on the map — engine-validated
        against the menu it offered, idempotent across reuse and replay."""
        choices = record.llm_choices if isinstance(record.llm_choices, dict) else {}
        engine_choices = (
            record.engine_choices if isinstance(record.engine_choices, dict) else {}
        )
        template = normalize_id(str(choices.get("decoration_template") or ""))
        allowed = {
            normalize_id(str(option.get("template") or ""))
            for option in engine_choices.get("decoration_options", [])
            if isinstance(option, dict)
        }
        spot = engine_choices.get("decoration_spot")
        if (
            not template
            or template not in allowed
            or not isinstance(spot, list)
            or len(spot) != 2
        ):
            return
        flag_key = f"decoration_{record.id}"
        if self.engine.state.flags.get(flag_key):
            return
        prop = self.engine.spawn_prop(template, int(spot[0]), int(spot[1]))
        if prop is None:
            return
        name = str(choices.get("decoration_name") or "").strip()
        description = str(choices.get("decoration_description") or "").strip()
        if name:
            prop.name = name
        if description:
            prop.description = description
        self.engine.state.flags[flag_key] = True
        self.engine.state.add_message(f"Your search uncovers {prop.name}.")

    def _consume_investigation_turns(self, room: Any, slot: dict[str, Any]) -> None:
        turns = slot_turn_cost(slot)
        plural = "s" if turns > 1 else ""
        self.engine.state.add_message(
            f"You spend {turns} turn{plural} searching the {room.room_type}; the world does not wait."
        )
        for _ in range(turns):
            self.engine.finish_player_turn()

    def _canon_context_for_investigation(
        self,
        room: Any,
        slot: dict[str, Any] | None,
        record_id: str,
    ) -> dict[str, Any]:
        context = self._canon_context_for_room(room, record_id)
        context["kind"] = "investigation"
        context["base_tags"] = sorted(
            {tag for tag in context["base_tags"] if tag != "room_flavor"}
            | {"investigation"}
        )
        context["allowed_tags"] = sorted(
            {*context["allowed_tags"], "investigation", "secret"}
        )
        if slot is None:
            context["engine_choices"] = {
                "secret_present": False,
                "mechanical_effect": "none",
                "turn_cost": 1,
            }
            # A secretless sweep may still develop the room: the engine offers a
            # menu of fitting non-blocking props and a validated tile; the LLM
            # may surface ONE of them as something the search turns up.
            deco_rng = random.Random(
                stable_seed(self.engine.state.rng_seed, "sweep_decoration", room.id)
            )
            options = decoration_menu(list(room.tags), deco_rng)
            spot = self._decoration_spot(room, deco_rng)
            if options and spot is not None:
                context["engine_choices"]["decoration_options"] = options
                context["engine_private"] = {"decoration_spot": [spot[0], spot[1]]}
                context["contract"]["allowed_outputs"] = [
                    *context["contract"]["allowed_outputs"],
                    "llm_choices.decoration_template (one of decoration_options)",
                    "llm_choices.decoration_name",
                    "llm_choices.decoration_description",
                ]
        else:
            context["engine_choices"] = {
                "secret_present": True,
                "secret_kind": slot.get("kind"),
                "clue_style": slot.get("clue_style"),
                "anchor_name": slot.get("anchor"),
                "reveal_difficulty": slot.get("reveal_difficulty"),
                "reward_name": (slot.get("reward") or {}).get("name"),
                "turn_cost": slot_turn_cost(slot),
            }
        context["contract"] = {
            "allowed_outputs": ["title", "summary", "text", "tags", "llm_choices"],
            "claim_quota": 0,
            "forbidden": [
                "inventing secrets or rewards",
                "stating what is hidden or where",
                "treasure not named in engine_choices",
                "exits",
                "enemies",
                "stats",
                "map changes",
            ],
        }
        return context

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
            return (
                False,
                False,
                {
                    "spell": "",
                    "provider": self.provider_label,
                    "technical_failure": False,
                    "error": "missing spell text",
                    "data": None,
                },
                None,
            )

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
            self.engine.state.add_message(
                f"Wild magic misfired technically: {resolution.error}"
            )
            return False, True, wild_magic_record, context

        resolution_data = dict(resolution.data)
        resolution_data.setdefault("spell", spell)
        outcome = self.engine.apply_wild_magic_resolution(resolution_data)
        wild_magic_record["deltas"] = list(outcome.deltas)
        if outcome.technical_failure:
            wild_magic_record["technical_failure"] = True
            wild_magic_record["error"] = "; ".join(outcome.messages)
        # A.2: was this outcome an ambiguous deed (raised dead, razed a place, desecration,
        # atrocity)? The verdict rides on the wild-magic record so replay reproduces the
        # deed without a model call. (Kills are already deeds via the combat path.)
        deed_record = self._interpret_spell_deed(spell, outcome, replay_wild_magic)
        if deed_record is not None:
            wild_magic_record["deed"] = deed_record
        return (
            outcome.consumed_turn,
            outcome.technical_failure,
            wild_magic_record,
            context,
        )

    def _interpret_spell_deed(
        self,
        spell: str,
        outcome: Any,
        replay_wild_magic: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Classify a wild-magic outcome as a non-combat deed (A.2). On replay, reuse the
        recorded verdict; live, gate cheaply then ask the interpreter (LLM or the
        deterministic fallback). Records the deed on the engine and returns the compact
        verdict to store on the wild-magic action record (or None for "not a deed")."""
        engine = self.engine
        # Replay: reproduce the recorded verdict deterministically, no model call.
        if replay_wild_magic is not None:
            recorded = replay_wild_magic.get("deed")
            if isinstance(recorded, dict) and recorded.get("deed_type"):
                self._record_spell_deed(recorded)
                return recorded
            return None
        if outcome.technical_failure:
            return None
        outcome_text = " ".join(outcome.messages)
        if not outcome_is_deed_candidate(f"{spell} {outcome_text}"):
            return None  # the zero-call common path
        context = {
            "spell": spell,
            "outcome": outcome_text,
            "location": engine.state.location_label(),
            "region": engine.region.name,
            "zone": {
                "x": engine.state.zone_x,
                "y": engine.state.zone_y,
                "type": engine.state.zone_type,
            },
        }
        verdict = resolve_deed_interpretation(self.deed_interpreter_provider, context)
        self.deed_interpreter_label = verdict.provider_name
        if not verdict.deed_type:
            return None
        record = verdict.to_record()
        self._record_spell_deed(record)
        return record

    def _record_spell_deed(self, record: dict[str, Any]) -> None:
        self.engine.record_deed(
            str(record["deed_type"]),
            magnitude=float(record.get("magnitude", 0.3)),
            summary=str(record.get("summary") or ""),
            source="spell",
            target_tags=[str(t) for t in record.get("target_tags") or []],
            interpretation_source=str(record.get("interpretation_source") or "llm"),
        )

    def _talk(
        self, message: str, replay_dialogue: dict[str, Any] | None = None
    ) -> tuple[bool, bool, dict[str, Any] | None]:
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
            lore_cards = dialogue_lore_cards(
                self.engine.state.npc_profiles.get(npc.id),
                message,
                provider_name=getattr(self.dialogue_provider, "name", "mock"),
                region_name=getattr(getattr(self.engine, "region", None), "name", "")
                or "",
            )
            if lore_cards:
                # Static authored canon, kept in its own slot and NEVER fed to lore
                # extraction (docs/LORE_CARDS.md §4.1).
                context["world_knowledge"] = [c.text for c in lore_cards]
            resolution = resolve_dialogue(
                self.dialogue_provider, npc.name, message, context
            )
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
                "lore_cards": [c.name for c in lore_cards],
            }
        if resolution.technical_failure or resolution.reply is None:
            self.engine.state.add_message(
                f"{npc.name} doesn't seem to hear you. ({resolution.error})"
            )
            return False, True, dialogue_record

        reply = resolution.reply
        # Surface the NPC's reply to the player IMMEDIATELY, before the (blocking)
        # trade-structuring call below. Talk runs on a worker thread while the UI redraws
        # every frame, so the reply paints now and the player reads it instead of staring
        # at a wait; the trade prompt, if any, simply follows a moment later.
        self.engine.announce_dialogue_reply(npc, message, reply)
        trade_data: dict[str, Any] | None = None
        if replay_dialogue is not None:
            trade = replay_dialogue.get("trade")
            if isinstance(trade, dict) and not trade.get("technical_failure"):
                trade_data = trade.get("data")
        elif self.engine.should_consider_trade(npc, message, reply):
            trade_context = self.engine.trade_context_for_llm(npc, message, reply)
            trade_resolution = resolve_trade_proposal(
                self.trade_provider, npc.name, trade_context
            )
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

        self.engine.apply_dialogue_exchange(
            npc, message, reply, trade_data, announced=True
        )
        if replay_dialogue is None:
            lore_context = self.engine.lore_extraction_context(npc, message, reply)
            self._enqueue_lore_extraction(lore_context, dialogue_record)
        return True, False, dialogue_record

    def _enqueue_lore_extraction(
        self,
        context: dict[str, Any],
        dialogue_record: dict[str, Any],
        claim_quota: int | None = None,
    ) -> None:
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
        future = self._lore_executor.submit(
            resolve_lore_extraction, self.lore_provider, context
        )
        self._pending_lore.append((future, dialogue_record, claim_quota))

    def drain_lore(self, block: bool = False) -> None:
        remaining: list[
            tuple[
                concurrent.futures.Future[LoreExtractionResolution],
                dict[str, Any],
                int | None,
            ]
        ] = []
        for future, dialogue_record, claim_quota in self._pending_lore:
            if not block and not future.done():
                remaining.append((future, dialogue_record, claim_quota))
                continue
            try:
                resolution = future.result()
            except Exception as exc:
                resolution = LoreExtractionResolution(
                    [], True, str(exc), self.lore_provider_label
                )
            self.lore_provider_label = resolution.provider_name
            # CONTRACT enforcement: claim quotas are clamped here, not negotiated
            # in the prompt — extra claims are dropped before they can bind.
            if claim_quota is not None and len(resolution.promises) > claim_quota:
                resolution.promises = resolution.promises[:claim_quota]
            # Snapshot the extraction outputs before add_promises mutates them (binding,
            # merging) so the replay record carries the apply-point inputs.
            self._promise_apply_buffer.extend(
                promise.to_dict() for promise in resolution.promises
            )
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
                self._lore_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1
                )
            future = self._lore_executor.submit(
                resolve_flesh, self.flesh_provider, flesh_context_for_promise(promise)
            )
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
                resolution = FleshResolution(
                    None, True, str(exc), self.flesh_provider_label
                )
            self.flesh_provider_label = resolution.provider_name
            if resolution.flesh:
                applied = self.engine.apply_promise_flesh(promise_id, resolution.flesh)
                if applied is not None:
                    self._flesh_apply_buffer.append(
                        {"promise_id": promise_id, "flesh": dict(applied.flesh or {})}
                    )
        self._pending_flesh = remaining

    def pump_canon_prewarm(self) -> None:
        """Advance the background canon queue outside of player turns. Frontends with
        an idle loop (the pygame UI) call this every frame so titles and pages keep
        materializing while the player stands still, and the queued job is re-chosen
        by proximity each time a slot frees. Must not run while a player command is
        mutating state on another thread; the UI guards that with `_awaiting_command`.
        No-op in replay (canon there comes from recorded apply points)."""
        if self.replay_mode:
            return
        self.drain_canon_prewarm(block=False)
        self._enqueue_canon_prewarm()

    def canon_queue_snapshot(self) -> dict[str, Any]:
        """A read-only view of the background canon queue, for the debug overlay.
        Cheap (dict/set lookups only, no context building) and main-thread-safe:
        every structure it reads is mutated only on the main thread."""
        state = self.engine.state
        player = state.player

        # The actually-submitted jobs: first not-done is the one the single worker
        # is running, the rest are queued behind it.
        inflight: dict[str, str] = {}
        now_next: list[dict[str, Any]] = []
        running_assigned = False
        for future, job in list(self._pending_canon):
            if future.done():
                status = "done"
            elif not running_assigned:
                status, running_assigned = "running", True
            else:
                status = "queued"
            inflight[job.record_id] = status
            now_next.append(
                {
                    "kind": job.kind,
                    "status": status,
                    "label": self._canon_job_target_name(job),
                }
            )

        def stage_status(record_id: str) -> str:
            if record_id in state.canon_records:
                return "done"
            return inflight.get(
                record_id,
                "queued" if record_id in self._queued_canon_ids else "pending",
            )

        books: list[dict[str, Any]] = []
        for entity in state.entities.values():
            if entity.kind != "prop" or "book" not in entity.tags:
                continue
            title_id = f"canon_book_title_{normalize_id(entity.id)}"
            full_id = f"canon_book_{normalize_id(entity.id)}"
            distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
            nearby = distance <= 8 and self.engine.is_visible(entity.x, entity.y)
            if full_id in state.canon_records:
                pages = "done"
            elif not nearby:
                pages = "far"  # pages prewarm only for nearby visible books
            else:
                pages = stage_status(full_id)
            books.append(
                {
                    "name": entity.name,
                    "distance": distance,
                    "title": stage_status(title_id),
                    "pages": pages,
                }
            )
        books.sort(key=lambda book: (book["distance"], book["name"]))
        return {
            "limit": canon_prewarm_limit(),
            "titles_enabled": book_titles_enabled(),
            "saturation_enabled": canon_prewarm_enabled(),
            "now_next": now_next,
            "books": books,
            "pending_canon": len(self._pending_canon),
            "pending_lore": len(self._pending_lore),
            "pending_flesh": len(self._pending_flesh),
        }

    def _canon_job_target_name(self, job: CanonSaturationJob) -> str:
        """A friendly label for a queued canon job: the book/entity name when the
        job is attached to one, else its record id."""
        subject = job.context.get("subject")
        subject = subject if isinstance(subject, dict) else {}
        book = subject.get("book") if isinstance(subject.get("book"), dict) else None
        if book is not None:
            entity = self.engine.state.entities.get(str(book.get("id")))
            if entity is not None:
                return entity.name
        attachment = subject.get("attachment")
        if not isinstance(attachment, dict):
            private = job.context.get("engine_private")
            private = private if isinstance(private, dict) else {}
            attachment = private.get("attachment")
        attachment = attachment if isinstance(attachment, dict) else {}
        entity = self.engine.state.entities.get(str(attachment.get("entity_id")))
        if entity is not None:
            return entity.name
        return job.record_id

    def _enqueue_canon_prewarm(self) -> None:
        """Keep the background canon route busy. The book pipeline is always-on and
        takes priority over the opt-in saturation set (room flavor, entity detail),
        so the nearest books get readied (title, then pages) first."""
        if self.replay_mode:
            return
        limit = canon_prewarm_limit()
        if limit <= 0:
            return
        if len(self._pending_canon) >= limit:
            return
        for job in self._canon_prewarm_candidates():
            if len(self._pending_canon) >= limit:
                break
            self._queued_canon_ids.add(job.record_id)
            if self._lore_executor is None:
                self._lore_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1
                )
            future = self._lore_executor.submit(
                resolve_canon, self.background_canon_provider, job.context
            )
            self._pending_canon.append((future, job))

    def _canon_prewarm_candidates(self) -> list[CanonSaturationJob]:
        """Book jobs first (always-on, top priority), then the flag-gated saturation
        set (room flavor, entity detail). The list is in priority order; the
        single-worker route fills free slots from the front, so the nearest books
        get readied before anything else, and the queued slot is re-chosen by
        proximity every time a slot frees."""
        candidates: list[CanonSaturationJob] = []
        if book_titles_enabled():
            candidates.extend(self._canon_book_jobs())
        if canon_prewarm_enabled():
            candidates.extend(self._canon_saturation_candidates())
        return candidates

    def _canon_book_jobs(self) -> list[CanonSaturationJob]:
        """The book pipeline, ordered strictly by proximity so the closest books are
        readied first. Each book is taken to full readiness before the next: its
        title materializes (whole zone, so every shelf is readable by name), then —
        for nearby visible books — its full pages, so `read` opens instantly. The
        sort key (distance, stage) interleaves as title→pages per book in distance
        order, and the list is rebuilt each enqueue, so it tracks the player."""
        state = self.engine.state
        player = state.player
        pending: list[tuple[tuple[int, int], str, str, Any, str | None]] = []
        for entity in state.entities.values():
            if entity.kind != "prop" or "book" not in entity.tags:
                continue
            title_id = f"canon_book_title_{normalize_id(entity.id)}"
            full_id = f"canon_book_{normalize_id(entity.id)}"
            if full_id in state.canon_records:
                continue  # fully materialized — nothing left to prewarm
            distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
            if title_id not in state.canon_records:
                if title_id in self._queued_canon_ids:
                    continue
                pending.append(((distance, 0), title_id, "book_title", entity, full_id))
            elif (
                full_id not in self._queued_canon_ids
                and distance <= 8
                and self.engine.is_visible(entity.x, entity.y)
            ):
                # Title is known; prewarm the full pages for nearby visible books.
                pending.append(((distance, 1), full_id, "book", entity, None))
        pending.sort(key=lambda item: (item[0], item[1]))
        jobs: list[CanonSaturationJob] = []
        for _key, record_id, kind, book, superseded_by in pending:
            if kind == "book_title":
                context = self._canon_context_for_book_title(book, record_id)
            else:
                context = self._canon_context_for_book(book, record_id)
                context["source"] = "background"
                context["engine_choices"] = dict(context.get("engine_choices") or {})
                context["engine_choices"]["turn_cost"] = 0
            jobs.append(
                CanonSaturationJob(
                    record_id=record_id,
                    kind=kind,
                    context=context,
                    superseded_by=superseded_by,
                )
            )
        return jobs

    def _canon_saturation_candidates(self) -> list[CanonSaturationJob]:
        candidates: list[tuple[int, str, CanonSaturationJob]] = []
        room = self.engine.room_profile_at(
            self.engine.state.player.x, self.engine.state.player.y
        )
        if room is not None:
            record_id = f"canon_room_{normalize_id(room.id)}"
            if (
                record_id not in self.engine.state.canon_records
                and record_id not in self._queued_canon_ids
            ):
                context = self._canon_context_for_room(room, record_id)
                context["source"] = "background"
                context["engine_choices"] = dict(context.get("engine_choices") or {})
                context["engine_choices"]["turn_cost"] = 0
                candidates.append(
                    (
                        0,
                        record_id,
                        CanonSaturationJob(
                            record_id=record_id, kind="room_flavor", context=context
                        ),
                    )
                )

        for distance, entity in self._canon_entity_detail_candidates():
            record_id = f"canon_detail_{normalize_id(entity.id)}_far"
            room = self.engine.room_profile_at(entity.x, entity.y)
            band = "near" if distance <= 4 else "across the room"
            context = self._canon_context_for_entity(
                room, None, entity, band, record_id
            )
            context["source"] = "background"
            context["engine_choices"] = dict(context.get("engine_choices") or {})
            context["engine_choices"]["turn_cost"] = 0
            context["contract"] = dict(context.get("contract") or {})
            context["contract"]["claim_quota"] = 0
            priority = 10 if entity.kind in {"npc", "actor"} else 30
            candidates.append(
                (
                    priority + distance,
                    record_id,
                    CanonSaturationJob(
                        record_id=record_id,
                        kind=str(context.get("kind") or "object_detail"),
                        context=context,
                        superseded_by=f"canon_detail_{normalize_id(entity.id)}_close",
                    ),
                )
            )

        # Books are no longer here — they run on the always-on book pipeline
        # (_canon_book_jobs), ahead of this flag-gated room/entity saturation set.
        candidates.sort(key=lambda item: (item[0], item[1]))
        return [job for _priority, _record_id, job in candidates]

    def _canon_entity_detail_candidates(self) -> list[tuple[int, Any]]:
        state = self.engine.state
        player = state.player
        candidates = []
        for entity in state.entities.values():
            if entity.id == state.player_id or entity.kind not in {
                "prop",
                "item",
                "npc",
                "actor",
            }:
                continue
            if entity.kind == "actor" and not entity.alive:
                continue
            if entity.kind == "prop" and "book" in entity.tags:
                continue
            far_id = f"canon_detail_{normalize_id(entity.id)}_far"
            close_id = f"canon_detail_{normalize_id(entity.id)}_close"
            if far_id in state.canon_records or close_id in state.canon_records:
                continue
            if far_id in self._queued_canon_ids:
                continue
            if not self.engine.is_visible(entity.x, entity.y):
                continue
            distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
            if distance > 8:
                continue
            candidates.append((distance, entity.id, entity))
        candidates.sort(key=lambda item: item[:2])
        return [(distance, entity) for distance, _entity_id, entity in candidates]

    def drain_canon_prewarm(self, block: bool = False) -> None:
        remaining: list[
            tuple[concurrent.futures.Future[CanonResolution], CanonSaturationJob]
        ] = []
        for future, job in self._pending_canon:
            if not block and not future.done():
                remaining.append((future, job))
                continue
            try:
                resolution = future.result()
            except Exception as exc:
                resolution = CanonResolution(
                    None, True, str(exc), self.canon_provider_label
                )
            self.canon_provider_label = resolution.provider_name
            self._queued_canon_ids.discard(job.record_id)
            if resolution.technical_failure or resolution.record is None:
                continue
            if (
                job.superseded_by
                and job.superseded_by in self.engine.state.canon_records
            ):
                continue
            if job.record_id in self.engine.state.canon_records:
                continue
            applied = self.engine.add_canon_record(resolution.record)
            self._apply_canon_record_side_effects(applied)
            self._canon_apply_buffer.append(applied.to_dict())
        self._pending_canon = remaining

    def apply_recorded_flesh(self, raw_events: list[dict[str, Any]] | None) -> None:
        """Inject flesh recorded at this apply point in a live run."""
        for event in raw_events or []:
            if not isinstance(event, dict):
                continue
            promise_id = str(event.get("promise_id") or "")
            applied = self.engine.apply_promise_flesh(promise_id, event.get("flesh"))
            if applied is not None:
                self._flesh_apply_buffer.append(
                    {"promise_id": promise_id, "flesh": dict(applied.flesh or {})}
                )

    def _pop_applied_flesh(self) -> list[dict[str, Any]]:
        applied, self._flesh_apply_buffer = self._flesh_apply_buffer, []
        return applied

    def apply_recorded_canon(self, raw_records: list[dict[str, Any]] | None) -> None:
        """Inject materialized canon recorded at this apply point in a live run."""
        for raw in raw_records or []:
            if not isinstance(raw, dict):
                continue
            record = self.engine.add_canon_record(CanonRecord.from_dict(raw))
            self._apply_canon_record_side_effects(record)
            self._canon_apply_buffer.append(record.to_dict())

    def _pop_applied_canon(self) -> list[dict[str, Any]]:
        applied, self._canon_apply_buffer = self._canon_apply_buffer, []
        return applied

    def _apply_canon_record_side_effects(self, record: CanonRecord) -> None:
        # Item-detail records carry the inventory key in engine_choices; persist their
        # materialized description into item_lore so it survives pickup. Runs from both the
        # live investigate path and replay's apply_recorded_canon, so the two stay in sync.
        item_key = str((record.engine_choices or {}).get("item_inventory_key") or "")
        if item_key:
            self.engine.set_item_lore(
                item_key,
                str(record.engine_choices.get("item_display_name") or item_key),
                record.summary or record.text,
                source="investigated",
            )
        attachment = record.attachment if isinstance(record.attachment, dict) else {}
        if attachment.get("kind") != "prop":
            return
        entity_id = str(attachment.get("entity_id") or "")
        entity = self.engine.state.entities.get(entity_id)
        if entity is None or entity.kind != "prop" or "book" not in entity.tags:
            return
        if record.kind == "book":
            self._apply_book_canon(entity, record)
        elif record.kind == "book_title":
            # The full pages already carry the canonical title — don't let a late
            # title job overwrite a book that's already been fully materialized.
            full_id = f"canon_book_{normalize_id(entity.id)}"
            if full_id not in self.engine.state.canon_records:
                self._apply_book_title(entity, record)

    def _apply_book_title(self, book: Any, record: CanonRecord) -> None:
        """The materialized title becomes the book's in-world name and marks it as
        readable-by-name; the verbose grammar description is left behind."""
        if record.title:
            book.name = record.title
            book.details["title_materialized"] = True

    def close(self) -> None:
        for future, _dialogue_record, _claim_quota in self._pending_lore:
            future.cancel()
        self._pending_lore.clear()
        for future, _promise_id in self._pending_flesh:
            future.cancel()
        self._pending_flesh.clear()
        for future, _job in self._pending_canon:
            future.cancel()
        self._pending_canon.clear()
        if self._lore_executor is not None:
            self._lore_executor.shutdown(wait=False, cancel_futures=True)
            self._lore_executor = None
        self.engine.close()

    def apply_recorded_promises(
        self, raw_promises: list[dict[str, Any]] | None
    ) -> None:
        """Inject promises recorded at this apply point in a live run.

        Binding and merging re-run deterministically against the replayed engine state,
        which matches the live state because apply points are recorded per action.
        """
        promises = [
            WorldPromise.from_dict(raw)
            for raw in raw_promises or []
            if isinstance(raw, dict)
        ]
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
        wares_text = ", ".join(
            f"{name} x{amount}" for name, amount in sorted(profile.wares.items())
        )
        return [f"{npc.name} has for trade: {wares_text}"]

    def to_replay(self) -> dict[str, Any]:
        self.drain_lore(block=True)
        self.drain_flesh(block=True)
        self.drain_canon_prewarm(block=True)
        # Promises and flesh drained after the last recorded action have no action to
        # attach to; replay injects them after the action loop, before the final summary.
        final_promises = self._pop_applied_promises()
        final_flesh = self._pop_applied_flesh()
        final_canon = self._pop_applied_canon()
        return {
            "version": 3,
            "final_promises": final_promises,
            "final_flesh": final_flesh,
            "final_canon": final_canon,
            "flesh_provider": self.flesh_provider_label,
            "canon_provider": self.canon_provider_label,
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


# Named times the player can rest "until" — mapped to a representative wall-clock hour.
REST_TARGET_HOURS = {
    "dawn": 5.0,
    "sunrise": 6.0,
    "morning": 7.0,
    "noon": 12.0,
    "midday": 12.0,
    "afternoon": 15.0,
    "dusk": 19.0,
    "evening": 19.0,
    "sunset": 19.0,
    "night": 22.0,
    "nightfall": 21.0,
    "midnight": 0.0,
}


def _parse_rest_arg(arg: str) -> tuple[float | None, float | None, str | None]:
    """Parse the argument to 'rest'. Returns (hours, until_hour, error) with exactly one
    of hours/until_hour set (or an error string):
      'rest'                 -> 8 hours (a full night)
      'rest 3' / 'rest 3.5'  -> that many hours
      'rest until dawn'      -> until the next 05:00 (named times in REST_TARGET_HOURS)
      'rest until 14:00'     -> until that wall-clock time
    """
    arg = arg.strip().lower()
    for prefix in ("until ", "til ", "till "):
        if arg.startswith(prefix):
            arg = arg[len(prefix) :].strip()
            break
    if not arg:
        return (8.0, None, None)
    if ":" in arg:
        hh, _, mm = arg.partition(":")
        try:
            hour = (int(hh) + int(mm or 0) / 60) % 24
        except ValueError:
            return (None, None, "Rest until when? Try 'rest until 14:00'.")
        return (None, hour, None)
    if arg in REST_TARGET_HOURS:
        return (None, REST_TARGET_HOURS[arg], None)
    try:
        return (max(0.0, float(arg)), None, None)
    except ValueError:
        return (
            None,
            None,
            "Rest how long? Try 'rest', 'rest 4', or 'rest until dawn'.",
        )


def command_help() -> list[str]:
    return [
        "Commands: move north/south/east/west, open, descend, ascend, wait (recover 1 MP), rest [hours | until <time>] (camp 8h by default), cast <spell>, target <x> <y>, talk <message>, possess [name], examine, read [book], use <item>, equip <item>, unequip <slot>, focus <item> (set spell focus), unfocus, drop <item>, pickup, inspect (or inventory), curses, journal (or rumors), world (or atlas), standing, wares (or browse), quit.",
        "Targeting: click a square (or 'target <x> <y>') to mark it - a free action, no turn. Then 'cast fireball at target', 'teleport to target', etc. aim there, and the standard spark/frost spells hit your marked foe. Click it again, 'untarget', or Esc to clear.",
        "Possessing: 'possess' (or 'swap'/'inhabit') leaps your soul into the nearest body - or 'possess <name>' for a specific one. You become that body entirely: its stats, its hit points, its inventory. The body you leave drops as an inert husk. Costs a turn.",
        "Reading: stand on or next to a book and 'read' (or 'read <name>' to pick one). The first reading takes a turn and fixes the book's title and pages forever; rereading is free. What books claim about the world is hearsay - but the world has a way of honoring what gets written down.",
        "Investigating: 'investigate' (or 'search') studies the room - it costs 1-3 turns while the world keeps moving, and what you learn is permanent. If something here is hidden, careful search turns up a clue; investigate the thing the clue points at ('investigate <name>') to see what it was protecting.",
        "Journal: 'journal' lists everything the world has told you - rumors heard, claims corroborated, places found true - with a rough direction when one was given. Free, costs no turn.",
        "World map: 'world' (or 'atlas') shows the political survey map - realms, the imperial capital, the rival, and where you stand. Free, costs no turn.",
        "Curses: 'curses' lists active curse names, descriptions, and mechanical limits. Curses lift on their own as you gain experience - each breaks once you've earned enough XP while carrying it. Nothing to spend.",
        "Standing: 'standing' (or 'reputation'/'factions') shows how the world's powers regard you - the mark your deeds have left on the Empire and those who oppose it. Free, costs no turn.",
        "Followers: 'followers' (or 'retinue') lists those who have come to follow you and the organizations you've founded; 'found <name>' raises a banner of your own. Free, costs no turn.",
        "Freeing captives: stand next to someone held in a cell and 'free' (or 'release') to strike their chains. What they do then is their own - some take up arms and come to follow you, some simply thank you and go, and a few repay you with what they know.",
        "Talking: stand next to an NPC and 'talk <what you want to say>' (or 'speak'/'say') to start a conversation - it costs a turn, just like any other action.",
        "Trading: some NPCs deal in goods and gold - 'wares' (or 'browse') lists what they have for trade, a free look. Haggle naturally through 'talk' - if a real offer comes together, you'll get a confirmation prompt to 'accept' (or 'yes') or 'reject' (or 'no') before anything changes hands.",
        "Equipment: weapons, armor, clothing, and charms go in their own slots and add to your attack/defense while worn. Equip with 'equip <item>' (or 'wear'/'wield'); take gear off with 'unequip <slot_or_item>' (or 'remove <item>').",
        "Spell focus: mark any one equipped item as your spell focus and the wild-magic resolver weighs it heavily when flavoring your casts. Set it with 'focus <item_or_slot>' (or 'attune'); clear it with 'unfocus'. It stays on your normal gear, so it keeps its usual stats.",
        "Standard spells (deterministic, no wild magic risk): spark, frost, heal, ward, reveal. Type the name directly, e.g. 'frost' -- 'cast frost' instead asks wild magic to improvise one.",
        "Short movement aliases also work: n, s, e, w. Walk into an enemy to attack it.",
    ]


def _canon_display_lines(record: CanonRecord) -> list[str]:
    lines = []
    if record.title:
        lines.append(record.title)
    lines.append(record.text)
    return lines


def describe_curses(engine: GameEngine, query: str = "") -> list[str]:
    state = engine.state
    if not state.curses:
        return ["You carry no curses."]
    curses = state.curses
    if query:
        curse_key = find_curse_key(curses, query)
        if curse_key is None:
            return [f"No curse matches {query!r}."]
        selected = [(curse_key, curses[curse_key])]
    else:
        selected = sorted(curses.items())
    lines = [f"Curses - experience {state.experience}:"]
    for curse_id, curse in selected:
        card = curse_card(curse)
        stack_text = f" x{curse.stacks}" if curse.stacks > 1 else ""
        lines.append(f"  {card['name']}{stack_text} [{card['mode']}]")
        lines.append(f"    {card['description']}")
        if card["mechanical_limits"]:
            lines.append("    Limits: " + "; ".join(card["mechanical_limits"]))
        if card["semantic_prompt"]:
            lines.append(f"    Flavor: {card['semantic_prompt']}")
        lines.append(
            f"    Lifts on its own at {card['xp_to_clear']} XP earned while cursed "
            f"(this stack: {card['clear_progress']}/{card['xp_to_clear']})."
        )
    return lines


def clear_curse(engine: GameEngine, query: str) -> tuple[bool, list[str]]:
    """Curses now lift on their own as you gain experience (see GameEngine.award_experience);
    nothing is spent and no command is needed. This responder only survives the old habit of
    typing 'clear curse <name>': it explains the change and shows where the curse stands."""
    lines = [
        "Curses lift on their own now -- keep earning experience and each will break "
        "once you've gained enough while carrying it. Nothing to spend, nothing to do.",
    ]
    lines.extend(describe_curses(engine, query))
    return True, lines


def describe_journal(engine: GameEngine) -> list[str]:
    entries = engine.journal_entries()
    if not entries:
        return ["Your journal is empty. The world talks - listen to people."]
    lines = ["Journal - what the world has told you:"]
    for index, entry in enumerate(entries, 1):
        source = (
            f" (from {entry['source']})"
            if entry["source"] and entry["source"] != "unknown"
            else ""
        )
        line = f"  {index}. [{entry['status']}] {entry['subject']}: {entry['text']}{source}"
        lines.append(line)
        if entry["hint"]:
            lines.append(f"       ~ {entry['hint']}")
    return lines


def describe_world(engine: GameEngine) -> list[str]:
    state = engine.state
    world = state.world_map
    if world is None:
        return ["No world atlas is available in this scenario."]
    visited = set(state.zones)
    visited.add((state.zone_x, state.zone_y))
    lines = world_map_strings(world, (state.zone_x, state.zone_y), visited)
    placement = world.placement_at(state.zone_x, state.zone_y)
    if placement is None:
        lines.append("")
        lines.append("You are in uncharted wilds within the edge of the known world.")
    else:
        template = REALM_TEMPLATES[placement.realm_id]
        lines.append("")
        lines.append(
            f"Current realm: {template.name} ({placement.role}; {template.tradition})."
        )
    return lines


def describe_standing(engine: GameEngine) -> list[str]:
    """The emergent-world standing readout: how each power regards the player, plus a
    one-line tally of deeds recorded and reckoned. Shared by GUI and CLI (T6)."""
    state = engine.state
    ledger = state.faction_ledger
    if not ledger.factions:
        return ["No powers have taken notice of you yet."]
    lines = ["Standing - how the powers regard you:"]
    for fid in sorted(ledger.factions):
        faction = ledger.factions[fid]
        axes = (
            ", ".join(
                f"{axis} {value:+.1f}"
                for axis, value in sorted(faction.standing.items())
                if value
            )
            or "neutral"
        )
        lines.append(f"  {faction.name} ({faction.mood}): {axes}")
    legend = state.legend_ledger.top_tags(state.player_soul_id, n=4)
    if legend:
        legend_text = ", ".join(f"{tag} {weight:+.1f}" for tag, weight in legend)
        lines.append(f"Legend: {legend_text}")
    # The road to the emperor (D9): the Empire's defenses as a progress read.
    empire = ledger.primary("empire")
    if empire is not None and "defense" in empire.resources:
        if engine.emperor_reachable():
            lines.append(
                "The Empire's defenses are broken - the emperor is within your reach."
            )
        else:
            lines.append(
                f"The Empire's defenses hold (strength {empire.resources['defense']}). "
                "Keep up the pressure to reach the emperor."
            )
    deeds = state.deed_ledger.deeds
    if deeds:
        reckoned = sum(1 for deed in deeds if deed.applied)
        known = sum(1 for deed in deeds if deed.is_public)
        lines.append(
            f"Deeds recorded: {len(deeds)} ({reckoned} reckoned, {known} known abroad)."
        )
    return lines


def describe_followers(engine: GameEngine) -> list[str]:
    """Who follows you and what your organizations are (Phase F). Feelings are shown as
    words, never numbers (the math stays invisible)."""
    state = engine.state
    lines: list[str] = []
    orgs = state.faction_ledger.by_kind("player_org")
    if orgs:
        lines.append("Your organizations:")
        for org in orgs:
            rank = f" ({org.player_rank})" if org.player_rank else ""
            members = sum(
                1
                for profile in state.npc_profiles.values()
                if org.id in profile.bond.affiliations
            )
            lines.append(f"  {org.name}{rank} - {members} sworn")
    followers = engine.followers()
    if followers:
        lines.append("Those who follow you:")
        for _npc_id, profile in followers:
            feeling = ", ".join(profile.bond_feeling()) or "at your side"
            lines.append(f"  {profile.name} the {profile.role} - {feeling}")
    if not lines:
        return ["No one follows you yet. The world is still taking your measure."]
    return lines


def describe_state(engine: GameEngine) -> list[str]:
    state = engine.state
    player = state.player
    equipment_view = equipment_inventory_view(engine)
    inventory = (
        ", ".join(
            f"{item['name']} x{item['quantity']}" for item in equipment_view["items"]
        )
        or "empty"
    )
    curses = (
        ", ".join(f"{curse.name} x{curse.stacks}" for curse in state.curses.values())
        or "none"
    )
    flags = ", ".join(sorted(state.flags)) or "none"
    statuses = (
        ", ".join(
            f"{player.status_display.get(s, s)}:{v}"
            if v != "permanent"
            else f"{player.status_display.get(s, s)}:permanent"
            for s, v in sorted(player.statuses.items())
        )
        or "none"
    )
    enemies = []
    for enemy in sorted(engine.living_enemies(), key=lambda entity: entity.id):
        e_status_str = ""
        if enemy.statuses:
            e_parts = ",".join(
                f"{enemy.status_display.get(k, k)}:{v}"
                for k, v in sorted(enemy.statuses.items())
            )
            e_status_str = f" [{e_parts}]"
        enemies.append(
            f"{enemy.name}({enemy.hp}/{enemy.max_hp}) at {enemy.x},{enemy.y} [{enemy.faction}]{e_status_str}"
        )
    allies = []
    for ally in sorted(
        (
            e
            for e in engine.state.entities.values()
            if e.kind in {"actor", "npc"} and e.faction == "ally" and e.hp > 0
        ),
        key=lambda entity: entity.id,
    ):
        a_status_str = ""
        if ally.statuses:
            a_parts = ",".join(
                f"{ally.status_display.get(k, k)}:{v}"
                for k, v in sorted(ally.statuses.items())
            )
            a_status_str = f" [{a_parts}]"
        tag_str = f" tags:{','.join(sorted(ally.tags))}" if ally.tags else ""
        allies.append(
            f"{ally.name}({ally.hp}/{ally.max_hp}) at {ally.x},{ally.y}{tag_str}{a_status_str}"
        )
    npcs = []
    for npc in sorted(
        (
            e
            for e in engine.state.entities.values()
            if e.kind == "npc"
            and "bound" not in e.tags  # captives get their own floor-wide line below
            and engine.is_visible(e.x, e.y)
        ),
        key=lambda entity: entity.id,
    ):
        profile = engine.state.npc_profiles.get(npc.id)
        role = f" the {profile.role}" if profile and profile.role else ""
        npcs.append(f"{npc.name}{role} at {npc.x},{npc.y}")
    # Bound captives are listed floor-wide (like enemies), not visibility-gated: someone
    # held in a cell is a notable, actionable presence worth knowing about and seeking out
    # ('free' them when adjacent). Surfaces for the player, the GUI panel, and the agent.
    captives = []
    for captive in sorted(
        (
            e
            for e in engine.state.entities.values()
            if e.kind == "npc" and "bound" in e.tags and e.hp > 0
        ),
        key=lambda entity: entity.id,
    ):
        captives.append(
            f"{captive.name} (held, can be freed) at {captive.x},{captive.y}"
        )
    props = []
    for prop in sorted(
        (
            e
            for e in engine.state.entities.values()
            if e.kind == "prop" and engine.is_visible(e.x, e.y)
        ),
        key=lambda entity: entity.id,
    ):
        props.append(
            f"{prop.name} at {prop.x},{prop.y} ({prop.description}) tags:{','.join(sorted(prop.tags))}"
        )
    current_room = engine.room_profile_at(player.x, player.y)
    visible_rooms = engine.visible_room_profiles(limit=5)
    room_lines = []
    for room in visible_rooms:
        topics = ", ".join(room["topics"][:2])
        topic_text = f" topics:{topics}" if topics else ""
        room_lines.append(
            f"{room['type']} [{room['era']}, {room['condition']}]{topic_text}"
        )
    equipment = (
        ", ".join(
            f"{slot['slot']}: {slot['item']}" + (" [focus]" if slot["focused"] else "")
            for slot in equipment_view["slots"]
            if slot["occupied"]
        )
        or "none"
    )
    resistances = (
        ", ".join(f"{k}:{v}%" for k, v in sorted(player.resistances.items()) if v)
        or "none"
    )
    weaknesses = (
        ", ".join(f"{k}:{v}%" for k, v in sorted(player.weaknesses.items()) if v)
        or "none"
    )
    lines = [
        f"Turn {state.turn} | {state.clock_label()} | HP {player.hp}/{player.max_hp} | MP {player.mana}/{player.max_mana} | XP {state.experience}",
        f"Depth {state.depth}/{state.max_depth} | Position {player.x},{player.y} | Scenario {state.scenario}",
        f"Visible tiles: {len(state.visible)} | Explored tiles: {len(state.explored)}",
        f"Statuses: {statuses}",
        f"Gold: {equipment_view['gold']}",
        f"Equipment: {equipment}",
        f"Inventory: {inventory}",
        f"Curses: {curses}",
        f"Flags: {flags}",
        f"Scheduled events: {len(state.event_timers)}",
        f"Triggers: {len(state.triggers)}",
        "Enemies: " + ("; ".join(enemies) if enemies else "none"),
        "Allies: " + ("; ".join(allies) if allies else "none"),
        "Captives: " + ("; ".join(captives) if captives else "none"),
        "NPCs: " + ("; ".join(npcs) if npcs else "none"),
        "Props: " + ("; ".join(props) if props else "none"),
        "Current room: "
        + (
            f"{current_room.room_type} [{current_room.era}, {current_room.condition}]"
            if current_room
            else "none"
        ),
        "Visible rooms: " + ("; ".join(room_lines) if room_lines else "none"),
        f"Canon records: {len(state.canon_records)}",
    ]
    memory_lines: list[str] = []
    for npc_id, profile in sorted(state.npc_profiles.items()):
        if not profile.memory_records:
            continue
        witnessed = sum(
            1
            for record in profile.memory_records
            if record.bucket == "observation"
            and record.provenance in {"firsthand", "implanted"}
        )
        overheard = sum(
            1
            for record in profile.memory_records
            if record.bucket == "overheard" or record.provenance == "overheard"
        )
        gossip = sum(
            1
            for record in profile.memory_records
            if record.bucket == "gossip" or record.provenance == "gossip"
        )
        conversation = sum(
            1 for record in profile.memory_records if record.bucket == "conversation"
        )
        counts = []
        if witnessed:
            counts.append(f"witnessed:{witnessed}")
        if overheard:
            counts.append(f"overheard:{overheard}")
        if gossip:
            counts.append(f"gossip:{gossip}")
        if conversation:
            counts.append(f"conversation:{conversation}")
        if counts:
            memory_lines.append(f"{profile.name} ({', '.join(counts)})")
    if memory_lines:
        lines.append("NPC memory: " + "; ".join(memory_lines))
    if state.gossip_edges:
        lines.append(f"Gossip edges: {len(state.gossip_edges)}")
    if player.resistances:
        lines.append(f"Resistances: {resistances}")
    if player.weaknesses:
        lines.append(f"Weaknesses: {weaknesses}")
    s = state.stats
    lines.append(
        f"Stats: spells {s.spells_cast}/{s.spells_cast + s.spells_failed} | "
        f"kills {s.enemies_killed} | items used {s.items_used} | "
        f"dmg out {s.damage_dealt} | dmg in {s.damage_taken} | "
        f"healed {s.hp_healed} | curses {s.curses_gained} | "
        f"xp {s.experience_gained} | floor {s.deepest_floor}"
    )
    return lines


def summarize_state(engine: GameEngine) -> dict[str, Any]:
    """Structured run snapshot for replay records and final summaries. Assembly lives in
    `state_view` so it shares one read-only state surface with the resolver context."""
    return replay_summary_view(engine)
