from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import json
import os
import textwrap
import time
from typing import Any

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

from .actions import ActionResult, GameSession, describe_state
from .autoplay import (
    AgentObservation,
    OllamaAgent,
    adjacent_options,
    avoid_commands_from_history,
    compact_messages,
    expedition_direction_for_seed,
    local_map_view,
    result_summary,
    spell_focus_for_seed,
    validate_agent_command,
)
from .config import DEFAULT_MODEL, audit_dir, get_config_value, set_config_value
from .game_data import _TOWN_GEN_TIMEOUT, EQUIPMENT_SPECS
from .items import infer_equipment_slot
from .normalize import normalize_id
from .wild_magic import fetch_ollama_models
from .models import (
    DOOR,
    FIRE,
    FLOOR,
    ICE_WALL,
    MIST,
    OPEN_DOOR,
    POISON_CLOUD,
    RUBBLE,
    SLICK_ICE,
    STAIRS_DOWN,
    STAIRS_UP,
    TILE_NAMES,
    TILE_TAGS,
    VINES,
    WALL,
    WATER,
    Entity,
)


TILE_SIZE = 18
MAP_PIXEL_WIDTH = 42 * TILE_SIZE
MAP_PIXEL_HEIGHT = 28 * TILE_SIZE
PANEL_WIDTH = 430
LLM_PANEL_WIDTH = 520
MAP_OFFSET_X = LLM_PANEL_WIDTH
WINDOW_WIDTH = LLM_PANEL_WIDTH + MAP_PIXEL_WIDTH + PANEL_WIDTH
WINDOW_HEIGHT = 800
BACKGROUND = (13, 14, 18)
PANEL = (27, 29, 34)
PANEL_EDGE = (62, 66, 76)
TEXT = (224, 223, 214)
MUTED = (151, 153, 160)
ACCENT = (120, 202, 174)
SELECTED = (58, 90, 112)
DANGER = (232, 105, 85)
MANA = (102, 168, 255)
GOLD = (224, 177, 92)
MODE_PURPLE = (160, 124, 226)
MODE_YELLOW = (226, 198, 92)
MODE_GREEN = (118, 208, 130)
MODE_ORANGE = (228, 146, 74)
MODE_COLORS = {"spell": MODE_PURPLE, "talk": MODE_YELLOW, "control": MODE_GREEN, "confirm_trade": MODE_ORANGE}

CONTROLS_HINT = (
    "Keyboard controls active - arrows/WASD/keypad move, > descend, < ascend, o open, "
    "g pick up, f cast spark, x investigate, j journal, q quests, i inventory, "
    "period or keypad-5 to wait, F8 watch AI, F9 pause AI, F10 step AI, Esc back to Wild Spell."
)

_MOVE_KEY_MAP: dict[int, str] = {
    # No vi-keys (h/j/k/l) here: j opens the journal, and the others are
    # reserved for future bindings. WASD, arrows, and the keypad move.
    pygame.K_UP: "north", pygame.K_w: "north", pygame.K_KP8: "north",
    pygame.K_DOWN: "south", pygame.K_s: "south", pygame.K_KP2: "south",
    pygame.K_LEFT: "west", pygame.K_a: "west", pygame.K_KP4: "west",
    pygame.K_RIGHT: "east", pygame.K_d: "east", pygame.K_KP6: "east",
    pygame.K_KP7: "northwest", pygame.K_KP9: "northeast",
    pygame.K_KP1: "southwest", pygame.K_KP3: "southeast",
}
CONTROLS_HINT_WRAP = 48

LLM_AUDIT_FILES = (
    "wild_magic_audit.jsonl",
    "dialogue_audit.jsonl",
    "trade_audit.jsonl",
    "town_audit.jsonl",
    "canon_audit.jsonl",
    "lore_audit.jsonl",
    "flesh_audit.jsonl",
)


class VisualAutoplayController:
    """Lets the autoplay command chooser drive the normal pygame command path."""

    def __init__(self, ui: "GameUI", enabled: bool = False) -> None:
        self.ui = ui
        self.enabled = False
        self.paused = False
        self.step_once = False
        self.delay_seconds = 1.15
        self.executor: concurrent.futures.ThreadPoolExecutor | None = None
        self.future: concurrent.futures.Future | None = None
        self.agent: OllamaAgent | None = None
        self.status = "off"
        self.last_command: str | None = None
        self.last_note: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.command_history: list[str] = []
        self.recent_results: list[dict[str, Any]] = []
        self.step_index = 0
        self.last_message_count = 0
        self.next_command_at = 0.0
        self.thinking_since: float | None = None
        self.book_popup_until: float | None = None
        self.expedition_direction = expedition_direction_for_seed(None, int(time.time()))
        self.spell_focus = spell_focus_for_seed(None, int(time.time()))
        self.death_restart_at: float | None = None
        if enabled:
            self.start()

    def start(self) -> None:
        if self.enabled:
            return
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="wildmagic-ui-autoplay",
        )
        self.agent = OllamaAgent()
        self.enabled = True
        self.paused = False
        self.status = "watching"
        self.last_error = None
        self.death_restart_at = None
        self.next_command_at = time.monotonic() + 0.2

    def stop(self) -> None:
        self.enabled = False
        self.paused = False
        self.step_once = False
        self.status = "off"
        self.future = None
        self.thinking_since = None
        self.death_restart_at = None
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.executor = None
        self.agent = None

    def close(self) -> None:
        self.stop()

    def reset_session_state(self) -> None:
        self.future = None
        self.last_command = None
        self.last_note = None
        self.last_error = None
        self.last_result = None
        self.command_history = []
        self.recent_results = []
        self.step_index = 0
        self.last_message_count = 0
        self.next_command_at = time.monotonic() + 0.2
        self.book_popup_until = None
        self.expedition_direction = expedition_direction_for_seed(self.ui.session.seed, int(time.time()))
        self.spell_focus = spell_focus_for_seed(self.ui.session.seed, int(time.time()))
        self.death_restart_at = None

    def toggle(self) -> None:
        if self.enabled:
            self.stop()
        else:
            self.start()

    def toggle_pause(self) -> None:
        if not self.enabled:
            self.start()
        self.paused = not self.paused
        self.status = "paused" if self.paused else "watching"

    def request_step(self) -> None:
        if not self.enabled:
            self.start()
        self.paused = True
        self.step_once = True
        self.next_command_at = 0.0
        self.status = "stepping"

    def update(self) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if self.book_popup_until is not None and now >= self.book_popup_until:
            self.ui.book_popup = None
            self.book_popup_until = None
        if self.ui.engine.state.game_over:
            if self.ui.engine.state.victory:
                self.paused = True
                self.status = "victory"
                return
            if self.death_restart_at is None:
                self.death_restart_at = now + 2.5
            if now >= self.death_restart_at:
                self.ui.restart_run()
                self.paused = False
                self.status = "watching"
                self.next_command_at = time.monotonic() + 0.4
            else:
                self.status = "death; restarting"
            return
        if self.future is not None:
            if self.future.done():
                self._finish_future()
            else:
                elapsed = now - (self.thinking_since or now)
                self.status = f"thinking {elapsed:.0f}s"
            return
        if self.paused and not self.step_once:
            self.status = "paused"
            return
        if now < self.next_command_at:
            self.status = "watching"
            return
        self._submit_decision()

    def _submit_decision(self) -> None:
        if self.executor is None or self.agent is None:
            self.start()
        if self.executor is None or self.agent is None:
            return
        observation = self._observation()
        self.thinking_since = time.monotonic()
        self.status = "thinking"
        self.future = self.executor.submit(self.agent.choose, observation)

    def _finish_future(self) -> None:
        future = self.future
        self.future = None
        self.thinking_since = None
        command = "wait"
        note: str | None = None
        try:
            decision = future.result() if future is not None else None
            command = validate_agent_command(str(getattr(decision, "command", "wait")))
            note = getattr(decision, "note", None)
            if command.lower() in {"quit", "exit"}:
                note = "AI chose quit; visual watch mode converted it to wait."
                command = "wait"
        except Exception as exc:
            self.last_error = str(exc)
            note = "Agent decision failed; visual watch mode waited."
            command = "wait"

        self.last_command = command
        self.last_note = note
        self.command_history.append(command)
        self.ui.input_text = command
        self.ui.input_active = False
        result = self.ui.execute_command(command)
        if result is not None:
            summary = result_summary(result)
            self.last_result = summary
            self.recent_results.append(summary)
            self.recent_results = self.recent_results[-8:]
            if result.action == "read" and result.success and self.ui.book_popup is not None:
                self.book_popup_until = time.monotonic() + 2.0
        self.step_index += 1
        self.next_command_at = time.monotonic() + self.delay_seconds
        if self.step_once:
            self.step_once = False
            self.paused = True
        self.status = "paused" if self.paused else "watching"

    def _observation(self) -> AgentObservation:
        session = self.ui.session
        state = self.ui.engine.state
        message_count = state.message_count
        new_count = max(0, message_count - self.last_message_count)
        if new_count:
            new_messages = state.messages[-new_count:]
        else:
            new_messages = state.messages[-6:]
        self.last_message_count = message_count
        repeated_command, repeated_count = self._repeated_tail()
        avoid = avoid_commands_from_history(
            self.command_history,
            self.recent_results,
            repeated_command,
            repeated_count,
        )
        return AgentObservation(
            episode=0,
            seed=session.seed,
            scenario=session.scenario,
            persona="visual_watch",
            theme="live pygame watch mode",
            step=self.step_index,
            turn=state.turn,
            new_messages=compact_messages(new_messages),
            state_lines=describe_state(self.ui.engine),
            local_map=local_map_view(session),
            adjacent=adjacent_options(session),
            recent_commands=self.command_history[-8:],
            recent_results=self.recent_results[-6:],
            last_result=self.last_result,
            avoid_commands=avoid,
            expedition_direction=self.expedition_direction,
            spell_focus=self.spell_focus,
            nudge=(
                "You are being watched in the graphical UI. Do not merely wander. Rotate through visible "
                "systems: inspect/examine/investigate rooms, read books, talk to NPCs, fight or control "
                "enemies, pick up/use/equip items, and cast varied wild spells that visibly change the scene. "
                f"When local work is done, resume exploring generally {self.expedition_direction}."
            ),
        )

    def _repeated_tail(self) -> tuple[str, int]:
        if not self.command_history:
            return "", 0
        command = self.command_history[-1]
        count = 0
        for item in reversed(self.command_history):
            if item != command:
                break
            count += 1
        return command, count

    def overlay_lines(self) -> list[tuple[str, tuple[int, int, int]]]:
        if not self.enabled:
            return [("AI Watch: off   F8 start", MUTED)]
        lines = [(
            f"AI Watch: {self.status}   heading {self.expedition_direction}   "
            f"spell {self.spell_focus}   F8 stop  F9 pause  F10 step",
            ACCENT,
        )]
        if self.last_command:
            lines.append((f"> {self.last_command}", TEXT))
        if self.last_note:
            lines.append((self.last_note, MUTED))
        if self.last_error:
            lines.append((self.last_error, DANGER))
        return lines[:4]

LLM_CALL_COLORS = {
    "spell": MODE_PURPLE,
    "dialogue": MODE_YELLOW,
    "trade": MODE_ORANGE,
    "town": GOLD,
    "canon": ACCENT,
    "lore": MANA,
    "flesh": MODE_GREEN,
}

# ---------------------------------------------------------------------------
# Config menu spec — each entry drives the menu display and .env update
# ---------------------------------------------------------------------------
_CONFIG_SPEC: list[dict] = [
    {
        "key": "WILDMAGIC_MODEL",
        "label": "Model",
        "type": "model",          # special: opens model-list submenu
        "default": DEFAULT_MODEL,
    },
    {
        "key": "WILDMAGIC_OLLAMA_THINK",
        "label": "Thinking mode",
        "type": "toggle",
        "values": ["0", "1"],
        "display": {"0": "OFF", "1": "ON"},
        "default": "0",
    },
    {
        "key": "WILDMAGIC_OLLAMA_TEMPERATURE",
        "label": "Spell temperature",
        "type": "cycle",
        "values": ["0.1", "0.2", "0.25", "0.3", "0.4", "0.5", "0.7", "0.9", "1.0", "1.2"],
        "default": "0.25",
    },
    {
        "key": "WILDMAGIC_DIALOGUE_TEMPERATURE",
        "label": "Dialogue temperature",
        "type": "cycle",
        "values": ["0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "1.0"],
        "default": "0.7",
    },
    {
        "key": "WILDMAGIC_OLLAMA_NUM_PREDICT",
        "label": "Spell tokens",
        "type": "cycle",
        "values": ["512", "768", "1024", "1536", "2048"],
        "default": "1024",
    },
    {
        "key": "WILDMAGIC_TOWN_NUM_PREDICT",
        "label": "Town gen tokens",
        "type": "cycle",
        "values": ["1024", "1536", "2000", "2500", "3000", "4096"],
        "default": "2000",
    },
    {
        "key": "WILDMAGIC_OLLAMA_TIMEOUT",
        "label": "LLM timeout (s)",
        "type": "cycle",
        "values": ["30", "60", "90", "120", "180", "300"],
        "default": "180",
    },
]

TILE_COLORS = {
    FLOOR: (77, 80, 88),
    WALL: (123, 127, 140),
    DOOR: (176, 122, 74),
    OPEN_DOOR: (154, 126, 91),
    STAIRS_DOWN: (214, 190, 112),
    STAIRS_UP: (214, 190, 112),
    WATER: (70, 145, 195),
    FIRE: (232, 96, 70),
    SLICK_ICE: (156, 210, 224),
    ICE_WALL: (151, 220, 232),
    POISON_CLOUD: (144, 196, 84),
    VINES: (83, 170, 108),
    RUBBLE: (138, 120, 102),
    MIST: (170, 178, 185),
}

ENTITY_COLORS = {
    "player": (246, 240, 200),
    "enemy": (232, 115, 100),
    "ally": (120, 202, 174),
    "neutral": (190, 190, 190),
    "item": (230, 190, 92),
}


class GameUI:
    def __init__(self, autoplay: bool = False) -> None:
        pygame.init()
        pygame.key.set_repeat(350, 35)
        pygame.display.set_caption("Wild Magic")
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        self.clock = pygame.time.Clock()
        self.tile_font = pygame.font.SysFont("consolas", 20, bold=True)
        self.ui_font = pygame.font.SysFont("consolas", 17)
        self.small_font = pygame.font.SysFont("consolas", 14)
        # Book popup: a serif face for printed matter (falls back if absent).
        self.book_title_font = pygame.font.SysFont("georgia,palatino linotype,times new roman", 22, bold=True)
        self.book_font = pygame.font.SysFont("georgia,palatino linotype,times new roman", 16)
        self.book_small_font = pygame.font.SysFont("georgia,palatino linotype,times new roman", 13, italic=True)
        self.session = GameSession(scenario="town")
        self.engine = self.session.engine
        self.input_text = ""
        self.input_active = True
        self.input_mode = "spell"
        self.mode_label_rects: list[tuple[pygame.Rect, str]] = []
        self._last_talk_target_id: int | None = None
        self._last_trade_active: bool = False
        self.provider_label = self.session.provider_label
        self.log_line_rects: list[tuple[pygame.Rect, str]] = []
        self.log_selection_anchor: int | None = None
        self.log_selection_focus: int | None = None
        self.dragging_log_selection = False
        self.log_area = pygame.Rect(MAP_OFFSET_X + MAP_PIXEL_WIDTH + 20, 0, PANEL_WIDTH - 40, 0)
        self.spell_box_rect = pygame.Rect(MAP_OFFSET_X + MAP_PIXEL_WIDTH + 20, WINDOW_HEIGHT - 92, PANEL_WIDTH - 40, 54)

        self.llm_debug_entries: list[dict[str, Any]] = []
        self.llm_debug_started_at = datetime.now(timezone.utc)
        self.llm_debug_seen: set[str] = set()
        self._llm_lines_cache: list[tuple[str, tuple[int, int, int]]] | None = None
        self.llm_block_ranges: list[tuple[int, int]] = []
        self.llm_entry_block_ranges: dict[int, dict[str, tuple[int, int]]] = {}
        self.llm_call_button_rects: list[tuple[pygame.Rect, int]] = []
        self.llm_selected_call_index: int | None = None
        self.llm_selected_call_part = "response"
        self.llm_scroll_offset = 0
        self.llm_autoscroll = True
        self.llm_dragging_scrollbar = False
        self.llm_drag_grab_dy = 0
        self.llm_content_rect = pygame.Rect(0, 0, LLM_PANEL_WIDTH, WINDOW_HEIGHT)
        self.llm_scrollbar_track_rect: pygame.Rect | None = None
        self.llm_scrollbar_thumb_rect: pygame.Rect | None = None
        self._llm_max_scroll = 0
        self.llm_line_rects: list[tuple[pygame.Rect, int]] = []
        self.llm_selection_anchor: int | None = None
        self.llm_selection_focus: int | None = None
        self.dragging_llm_selection = False

        self.inspect_tile: tuple[int, int] | None = None
        self.inspect_button_rects: list[tuple[pygame.Rect, str]] = []
        self.book_popup: dict[str, Any] | None = None

        # Menu state
        self.menu_active = False
        self.menu_page: str = "main"       # "main" | "config" | "model"
        self.menu_cursor: int = 0
        self.menu_prev_page: str = "main"  # for back navigation

        self.log_scroll_offset = 0
        self.log_dragging_scrollbar = False
        self.log_drag_grab_dy = 0
        self.log_scrollbar_track_rect: pygame.Rect | None = None
        self.log_scrollbar_thumb_rect: pygame.Rect | None = None
        self._log_max_scroll = 0

        self.inventory_pane = 0
        self.inventory_left_cursor = 0
        self.inventory_right_cursor = 0
        self.menu_models: list[str] = []   # populated when model page opens
        self.autoplay = VisualAutoplayController(self, enabled=autoplay)

    def _config_value(self, spec: dict) -> str:
        """Current display value for a config spec entry."""
        raw = get_config_value(spec["key"], spec["default"]) or spec["default"]
        if spec["type"] == "toggle":
            return spec["display"].get(raw, raw)
        return raw

    def _open_menu(self) -> None:
        self.menu_active = True
        self.menu_page = "main"
        self.menu_cursor = 0

    def _close_menu(self) -> None:
        self.menu_active = False

    def run(self) -> None:
        running = True
        try:
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type in {pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION}:
                        self.handle_mouse(event)
                    elif event.type == pygame.MOUSEWHEEL:
                        self.handle_mouse_wheel(event)
                    elif event.type == pygame.KEYDOWN:
                        self.handle_key(event)
                self.autoplay.update()
                self.draw()
                pygame.display.flip()
                self.clock.tick(30)
        finally:
            self.autoplay.close()
            self.session.close()
            pygame.quit()

    def handle_key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_F8:
            self.autoplay.toggle()
            return
        if event.key == pygame.K_F9:
            self.autoplay.toggle_pause()
            return
        if event.key == pygame.K_F10:
            self.autoplay.request_step()
            return

        if self.menu_active:
            self._handle_menu_key(event)
            return

        if event.mod & pygame.KMOD_CTRL:
            hovering_llm = self.llm_content_rect.collidepoint(pygame.mouse.get_pos())
            if event.key == pygame.K_c:
                if hovering_llm:
                    self.copy_llm_selection()
                else:
                    self.copy_log_selection()
                return
            if event.key == pygame.K_a:
                if hovering_llm and self._llm_lines_cache:
                    self.llm_selection_anchor = 0
                    self.llm_selection_focus = len(self._llm_lines_cache) - 1
                elif self.log_line_rects:
                    self.log_selection_anchor = 0
                    self.log_selection_focus = len(self.log_line_rects) - 1
                return

        if self.book_popup is not None:
            if event.key == pygame.K_ESCAPE:
                self.book_popup = None
            elif event.key in (pygame.K_LEFT, pygame.K_PAGEUP, pygame.K_a, pygame.K_UP):
                self.book_popup["page"] = max(0, int(self.book_popup.get("page", 0)) - 1)
            elif event.key in (pygame.K_RIGHT, pygame.K_PAGEDOWN, pygame.K_d, pygame.K_DOWN,
                               pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                page = int(self.book_popup.get("page", 0))
                if page + 1 >= int(self.book_popup.get("page_count", 1)):
                    self.book_popup = None
                else:
                    self.book_popup["page"] = page + 1
            return

        if event.key == pygame.K_r and self.engine.state.game_over:
            self.restart_run()
            return

        if self.engine.state.pending_trade is not None:
            if event.key in (pygame.K_y, pygame.K_RETURN, pygame.K_KP_ENTER):
                self.execute_command("accept")
            elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                self.execute_command("reject")
            return

        if (
            event.key in (pygame.K_UP, pygame.K_DOWN)
            and self.llm_selection_anchor is not None
            and self.llm_selection_focus is not None
            and self._move_llm_block_selection(-1 if event.key == pygame.K_UP else 1)
        ):
            return

        if event.key == pygame.K_ESCAPE:
            if self.inspect_tile is not None:
                self.inspect_tile = None
                return
            if self.input_mode == "control":
                self.input_mode = "spell"
                self.input_active = True
                return
            if self.input_text:
                self.input_text = ""
                self.input_active = True
                return
            self._open_menu()
            return

        if self.input_active and self.input_mode != "control":
            if event.key == pygame.K_RETURN:
                self.submit_input()
                return
            if event.key == pygame.K_BACKSPACE:
                self.input_text = self.input_text[:-1]
                return
            if event.key == pygame.K_TAB:
                self.input_active = False
                return
            if event.unicode and event.unicode.isprintable():
                self.input_text += event.unicode
                return

        if self.input_mode != "control" and event.key in {pygame.K_SLASH, pygame.K_RETURN}:
            self.input_active = True
            return
        _move_dir = _MOVE_KEY_MAP.get(event.key)
        if _move_dir is not None:
            _zone_before = (self.engine.state.zone_x, self.engine.state.zone_y)
            self.execute_command(f"move {_move_dir}")
            if (self.engine.state.zone_x, self.engine.state.zone_y) != _zone_before:
                pygame.event.clear((pygame.KEYDOWN, pygame.KEYUP))
        elif event.key in {pygame.K_KP5}:
            self.execute_command("wait")
        elif event.key == pygame.K_GREATER or (event.key == pygame.K_PERIOD and event.mod & pygame.KMOD_SHIFT):
            self.execute_command("descend")
        elif event.key == pygame.K_LESS or (event.key == pygame.K_COMMA and event.mod & pygame.KMOD_SHIFT):
            self.execute_command("ascend")
        elif event.key == pygame.K_PERIOD:
            self.execute_command("wait")
        elif event.key == pygame.K_o:
            self.execute_command("open")
        elif event.key == pygame.K_g:
            self.execute_command("pickup")
        elif event.key == pygame.K_f:
            self.execute_command("spark")
        elif event.key == pygame.K_x:
            self.execute_command(self._investigate_command())
        elif event.key == pygame.K_q:
            self.menu_active = True
            self.menu_page = "quests"
            self.menu_cursor = 0
        elif event.key == pygame.K_j:
            self.menu_active = True
            self.menu_page = "journal"
            self.menu_cursor = 0
        elif event.key == pygame.K_i:
            self.menu_active = True
            self.menu_page = "inventory"
            self.menu_cursor = 0
            self.inventory_pane = 0
            self.inventory_left_cursor = 0
            self.inventory_right_cursor = 0
        self.provider_label = self.session.provider_label

    def handle_mouse(self, event: pygame.event.Event) -> None:
        if self.book_popup is not None:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
                # Click left half = page back, right half = page forward/close.
                page = int(self.book_popup.get("page", 0))
                if event.pos[0] < WINDOW_WIDTH // 2:
                    if page > 0:
                        self.book_popup["page"] = page - 1
                elif page + 1 >= int(self.book_popup.get("page_count", 1)):
                    self.book_popup = None
                else:
                    self.book_popup["page"] = page + 1
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self.book_popup = None
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            mx, my = event.pos
            if MAP_OFFSET_X <= mx < MAP_OFFSET_X + MAP_PIXEL_WIDTH and 0 <= my < MAP_PIXEL_HEIGHT:
                tx = (mx - MAP_OFFSET_X) // TILE_SIZE
                ty = my // TILE_SIZE
                self.inspect_tile = None if self.inspect_tile == (tx, ty) else (tx, ty)
            else:
                self.inspect_tile = None
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.inspect_tile is not None:
                for rect, command in self.inspect_button_rects:
                    if rect.collidepoint(event.pos):
                        self.execute_command(command)
                        return
            self.inspect_tile = None
            if self.log_scrollbar_thumb_rect and self.log_scrollbar_thumb_rect.collidepoint(event.pos):
                self.log_dragging_scrollbar = True
                self.log_drag_grab_dy = event.pos[1] - self.log_scrollbar_thumb_rect.y
                return
            if self.log_scrollbar_track_rect and self.log_scrollbar_track_rect.collidepoint(event.pos):
                thumb = self.log_scrollbar_thumb_rect
                self.log_drag_grab_dy = thumb.height // 2 if thumb else 0
                track = self.log_scrollbar_track_rect
                thumb_height = thumb.height if thumb else 0
                usable = max(1, track.height - thumb_height)
                fraction = (event.pos[1] - self.log_drag_grab_dy - track.y) / usable
                self._log_scroll_to_fraction(fraction)
                self.log_dragging_scrollbar = True
                return
            if self.llm_scrollbar_thumb_rect and self.llm_scrollbar_thumb_rect.collidepoint(event.pos):
                self.llm_dragging_scrollbar = True
                self.llm_drag_grab_dy = event.pos[1] - self.llm_scrollbar_thumb_rect.y
                return
            if self.llm_scrollbar_track_rect and self.llm_scrollbar_track_rect.collidepoint(event.pos):
                thumb = self.llm_scrollbar_thumb_rect
                self.llm_drag_grab_dy = thumb.height // 2 if thumb else 0
                track = self.llm_scrollbar_track_rect
                thumb_height = thumb.height if thumb else 0
                usable = max(1, track.height - thumb_height)
                fraction = (event.pos[1] - self.llm_drag_grab_dy - track.y) / usable
                self._llm_scroll_to_fraction(fraction)
                self.llm_dragging_scrollbar = True
                return
            for rect, entry_index in self.llm_call_button_rects:
                if rect.collidepoint(event.pos):
                    if self._activate_llm_call_button(entry_index):
                        self.dragging_llm_selection = False
                        self.dragging_log_selection = False
                        self.log_selection_anchor = None
                        self.log_selection_focus = None
                        self.input_active = False
                    return
            for rect, mode in self.mode_label_rects:
                if rect.collidepoint(event.pos):
                    self.input_mode = mode
                    self.input_active = True
                    self.dragging_log_selection = False
                    self.log_selection_anchor = None
                    self.log_selection_focus = None
                    self.dragging_llm_selection = False
                    self.llm_selection_anchor = None
                    self.llm_selection_focus = None
                    return
            if self.spell_box_rect.collidepoint(event.pos):
                self.input_active = True
                self.dragging_log_selection = False
                self.log_selection_anchor = None
                self.log_selection_focus = None
                self.dragging_llm_selection = False
                self.llm_selection_anchor = None
                self.llm_selection_focus = None
                return
            llm_index = self.llm_line_index_at(event.pos)
            if llm_index is not None:
                self.llm_selection_anchor = llm_index
                self.llm_selection_focus = llm_index
                self.dragging_llm_selection = True
                self.dragging_log_selection = False
                self.log_selection_anchor = None
                self.log_selection_focus = None
                self.input_active = False
                return
            index = self.log_line_index_at(event.pos)
            if index is not None:
                self.log_selection_anchor = index
                self.log_selection_focus = index
                self.dragging_log_selection = True
                self.dragging_llm_selection = False
                self.llm_selection_anchor = None
                self.llm_selection_focus = None
                self.input_active = False
            else:
                self.dragging_log_selection = False
            return
        if event.type == pygame.MOUSEMOTION and self.log_dragging_scrollbar:
            fraction = self._log_scrollbar_fraction_at(event.pos[1])
            if fraction is not None:
                self._log_scroll_to_fraction(fraction)
            return
        if event.type == pygame.MOUSEMOTION and self.llm_dragging_scrollbar:
            fraction = self._llm_scrollbar_fraction_at(event.pos[1])
            if fraction is not None:
                self._llm_scroll_to_fraction(fraction)
            return
        if event.type == pygame.MOUSEMOTION and self.dragging_llm_selection:
            index = self.llm_line_index_at(event.pos)
            if index is not None:
                self.llm_selection_focus = index
            return
        if event.type == pygame.MOUSEMOTION and self.dragging_log_selection:
            index = self.log_line_index_at(event.pos)
            if index is not None:
                self.log_selection_focus = index
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.log_dragging_scrollbar:
                self.log_dragging_scrollbar = False
                return
            if self.llm_dragging_scrollbar:
                self.llm_dragging_scrollbar = False
                return
            if self.dragging_llm_selection:
                index = self.llm_line_index_at(event.pos)
                if index is not None:
                    self.llm_selection_focus = index
                self.dragging_llm_selection = False
                return
            if self.dragging_log_selection:
                index = self.log_line_index_at(event.pos)
                if index is not None:
                    self.log_selection_focus = index
            self.dragging_log_selection = False

    # ------------------------------------------------------------------
    # Config menu
    # ------------------------------------------------------------------

    def _menu_items(self) -> list[dict]:
        if self.menu_page == "main":
            return [
                {"label": "Resume",        "action": "resume"},
                {"label": "Configuration", "action": "config"},
                {"label": "Quit",          "action": "quit"},
            ]
        if self.menu_page == "config":
            items = []
            for spec in _CONFIG_SPEC:
                val = self._config_value(spec)
                items.append({"label": f"{spec['label']:<22} {val}", "action": "config_item", "spec": spec})
            items.append({"label": "Back", "action": "back"})
            return items
        if self.menu_page == "model":
            items = [{"label": m, "action": "set_model", "model": m} for m in self.menu_models]
            if not items:
                items = [{"label": "(no models found)", "action": "noop"}]
            items.append({"label": "Back", "action": "back"})
            return items
        return []

    def _handle_menu_key(self, event: pygame.event.Event) -> None:
        if self.menu_page == "inventory":
            inventory_items = sorted([item for item in self.engine.state.inventory.keys() if item != "gold"])
            slots = ["weapon", "armor", "charm", "head", "chest", "legs", "feet", "hands"]
            
            if event.key == pygame.K_ESCAPE or event.key == pygame.K_i:
                self._close_menu()
                return
            elif event.key in (pygame.K_LEFT, pygame.K_h, pygame.K_KP4):
                self.inventory_pane = 0
                return
            elif event.key in (pygame.K_RIGHT, pygame.K_l, pygame.K_KP6):
                self.inventory_pane = 1
                return
            elif event.key in (pygame.K_UP, pygame.K_k, pygame.K_KP8, pygame.K_w):
                if self.inventory_pane == 0:
                    self.inventory_left_cursor = (self.inventory_left_cursor - 1) % len(slots)
                else:
                    if inventory_items:
                        self.inventory_right_cursor = (self.inventory_right_cursor - 1) % len(inventory_items)
                return
            elif event.key in (pygame.K_DOWN, pygame.K_j, pygame.K_KP2, pygame.K_s):
                if self.inventory_pane == 0:
                    self.inventory_left_cursor = (self.inventory_left_cursor + 1) % len(slots)
                else:
                    if inventory_items:
                        self.inventory_right_cursor = (self.inventory_right_cursor + 1) % len(inventory_items)
                return
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_e, pygame.K_u):
                if self.inventory_pane == 0:
                    slot = slots[self.inventory_left_cursor]
                    if self.engine.state.player.equipment.get(slot):
                        self.execute_command(f"unequip {slot}")
                else:
                    if inventory_items:
                        item_name = inventory_items[self.inventory_right_cursor]
                        if event.key == pygame.K_u:
                            self.execute_command(f"unequip {item_name}")
                        else:
                            self.execute_command(f"equip {item_name}")
                new_inventory_items = sorted([item for item in self.engine.state.inventory.keys() if item != "gold"])
                if new_inventory_items:
                    self.inventory_right_cursor = min(self.inventory_right_cursor, len(new_inventory_items) - 1)
                else:
                    self.inventory_right_cursor = 0
                return

        if self.menu_page in {"quests", "journal"}:
            n = len(self.engine.quest_log_entries()) if self.menu_page == "quests" else len(self.engine.journal_entries())
            if n == 0:
                if event.key == pygame.K_ESCAPE:
                    self._close_menu()
                return
            if event.key in (pygame.K_UP, pygame.K_k, pygame.K_KP8, pygame.K_w):
                self.menu_cursor = (self.menu_cursor - 1) % n
            elif event.key in (pygame.K_DOWN, pygame.K_j, pygame.K_KP2, pygame.K_s):
                self.menu_cursor = (self.menu_cursor + 1) % n
            elif event.key == pygame.K_ESCAPE:
                self._close_menu()
            return

        items = self._menu_items()
        n = len(items)
        if event.key in (pygame.K_UP, pygame.K_k, pygame.K_KP8):
            self.menu_cursor = (self.menu_cursor - 1) % n
        elif event.key in (pygame.K_DOWN, pygame.K_j, pygame.K_KP2):
            self.menu_cursor = (self.menu_cursor + 1) % n
        elif event.key in (pygame.K_LEFT, pygame.K_h, pygame.K_KP4):
            self._menu_cycle(items, -1)
        elif event.key in (pygame.K_RIGHT, pygame.K_l, pygame.K_KP6):
            self._menu_cycle(items, +1)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._menu_select(items)
        elif event.key == pygame.K_ESCAPE:
            self._close_menu()

    def _menu_cycle(self, items: list[dict], direction: int) -> None:
        """Left/right arrow: cycle a config value in-place."""
        if self.menu_cursor >= len(items):
            return
        item = items[self.menu_cursor]
        if item["action"] != "config_item":
            return
        spec = item["spec"]
        if spec["type"] not in ("cycle", "toggle"):
            return
        values = spec["values"]
        current = get_config_value(spec["key"], spec["default"]) or spec["default"]
        try:
            idx = values.index(current)
        except ValueError:
            idx = 0
        new_idx = (idx + direction) % len(values)
        set_config_value(spec["key"], values[new_idx])

    def _menu_select(self, items: list[dict]) -> None:
        if self.menu_cursor >= len(items):
            return
        item = items[self.menu_cursor]
        action = item["action"]
        if action == "resume":
            self._close_menu()
        elif action == "quit":
            pygame.event.post(pygame.event.Event(pygame.QUIT))
        elif action == "config":
            self.menu_prev_page = "main"
            self.menu_page = "config"
            self.menu_cursor = 0
        elif action == "back":
            if self.menu_page in ("config", "model"):
                self._close_menu()
            else:
                self.menu_page = self.menu_prev_page
                self.menu_cursor = 0
        elif action == "config_item":
            spec = item["spec"]
            if spec["type"] == "toggle":
                self._menu_cycle(items, +1)
            elif spec["type"] == "cycle":
                self._menu_cycle(items, +1)
            elif spec["type"] == "model":
                self.menu_prev_page = "config"
                self.menu_page = "model"
                self.menu_cursor = 0
                self.menu_models = fetch_ollama_models()
                # pre-select current model
                current = get_config_value("WILDMAGIC_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
                try:
                    self.menu_cursor = self.menu_models.index(current)
                except ValueError:
                    self.menu_cursor = 0
        elif action == "set_model":
            set_config_value("WILDMAGIC_MODEL", item["model"])
            self.menu_page = "config"
            self.menu_cursor = 0
    def execute_command(self, command: str) -> ActionResult:
        self.log_scroll_offset = 0
        result = self.session.execute_command(command)
        self._refresh_llm_debug_entries()
        self._llm_lines_cache = None
        self.llm_autoscroll = True
        if result.action == "read" and result.success and result.canon_materialization:
            record = result.canon_materialization.get("record")
            if isinstance(record, dict) and record.get("kind") == "book":
                llm_choices = record.get("llm_choices") if isinstance(record.get("llm_choices"), dict) else {}
                self.book_popup = {
                    "title": str(record.get("title") or "An Untitled Volume"),
                    "author": str(llm_choices.get("author") or ""),
                    "text": str(record.get("text") or ""),
                    "page": 0,
                    "page_count": 1,
                }
        return result

    # ------------------------------------------------------------------

    def restart_run(self) -> None:
        self.session.close()
        self.session = GameSession(scenario="town")
        self.engine = self.session.engine
        self.input_text = ""
        self.log_scroll_offset = 0
        self.input_active = True
        self.input_mode = "spell"
        self.mode_label_rects = []
        self._last_talk_target_id = None
        self._last_trade_active = False
        self.provider_label = self.session.provider_label
        self.llm_debug_entries = []
        self.llm_debug_started_at = datetime.now(timezone.utc)
        self.llm_debug_seen = set()
        self._llm_lines_cache = None
        self.llm_block_ranges = []
        self.llm_entry_block_ranges = {}
        self.llm_call_button_rects = []
        self.llm_selected_call_index = None
        self.llm_selected_call_part = "response"
        self.llm_scroll_offset = 0
        self.llm_autoscroll = True
        self.llm_line_rects = []
        self.llm_selection_anchor = None
        self.llm_selection_focus = None
        self.dragging_llm_selection = False
        self.autoplay.reset_session_state()

    def log_line_index_at(self, pos: tuple[int, int]) -> int | None:
        if not self.log_area.collidepoint(pos):
            return None
        x, y = pos
        for index, (rect, _line) in enumerate(self.log_line_rects):
            expanded = rect.inflate(0, 4)
            expanded.x = self.log_area.x
            expanded.width = self.log_area.width
            if expanded.collidepoint(x, y):
                return index
        if self.log_line_rects:
            if y < self.log_line_rects[0][0].top:
                return 0
            if y > self.log_line_rects[-1][0].bottom:
                return len(self.log_line_rects) - 1
        return None

    def copy_log_selection(self) -> None:
        lines = self.selected_log_lines()
        if not lines:
            lines = [line for _rect, line in self.log_line_rects]
        if not lines:
            return
        text = "\n".join(lines)
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
            self.engine.state.add_message(f"Copied {len(lines)} log line(s).")
        except pygame.error:
            self.engine.state.add_message("Could not access the system clipboard.")

    def selected_log_lines(self) -> list[str]:
        if self.log_selection_anchor is None or self.log_selection_focus is None:
            return []
        start = max(0, min(self.log_selection_anchor, self.log_selection_focus))
        end = min(len(self.log_line_rects) - 1, max(self.log_selection_anchor, self.log_selection_focus))
        if start > end:
            return []
        return [line for _rect, line in self.log_line_rects[start : end + 1]]

    def llm_line_index_at(self, pos: tuple[int, int]) -> int | None:
        if not self.llm_content_rect.collidepoint(pos):
            return None
        x, y = pos
        for rect, abs_index in self.llm_line_rects:
            expanded = rect.inflate(0, 4)
            expanded.x = self.llm_content_rect.x
            expanded.width = self.llm_content_rect.width
            if expanded.collidepoint(x, y):
                return abs_index
        if self.llm_line_rects:
            if y < self.llm_line_rects[0][0].top:
                return self.llm_line_rects[0][1]
            if y > self.llm_line_rects[-1][0].bottom:
                return self.llm_line_rects[-1][1]
        return None

    def copy_llm_selection(self) -> None:
        lines = self.selected_llm_lines()
        if not lines:
            return
        text = "\n".join(lines)
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
            self.engine.state.add_message(f"Copied {len(lines)} line(s) from the LLM debug view.")
        except pygame.error:
            self.engine.state.add_message("Could not access the system clipboard.")

    def selected_llm_lines(self) -> list[str]:
        if self.llm_selection_anchor is None or self.llm_selection_focus is None or not self._llm_lines_cache:
            return []
        start = max(0, min(self.llm_selection_anchor, self.llm_selection_focus))
        end = min(len(self._llm_lines_cache) - 1, max(self.llm_selection_anchor, self.llm_selection_focus))
        if start > end:
            return []
        return [text for text, _color in self._llm_lines_cache[start : end + 1]]

    def _llm_block_index_for_line(self, line_index: int) -> int | None:
        if self._llm_lines_cache is None:
            self._llm_lines_cache = self._build_llm_lines(80)
        for index, (start, end) in enumerate(self.llm_block_ranges):
            if start <= line_index <= end:
                return index
        return None

    def _select_llm_block(self, block_index: int) -> bool:
        if self._llm_lines_cache is None:
            self._llm_lines_cache = self._build_llm_lines(80)
        if not self.llm_block_ranges:
            return False
        block_index = max(0, min(block_index, len(self.llm_block_ranges) - 1))
        start, end = self.llm_block_ranges[block_index]
        self.llm_selection_anchor = start
        self.llm_selection_focus = end
        visible_lines = max(1, self.llm_content_rect.height // (self.small_font.get_linesize() + 1))
        self.llm_scroll_offset = max(0, min(start, max(0, end - visible_lines + 1)))
        self.llm_autoscroll = False
        return True

    def _move_llm_block_selection(self, direction: int) -> bool:
        if self._llm_lines_cache is None:
            self._llm_lines_cache = self._build_llm_lines(80)
        if not self.llm_block_ranges:
            return False
        focus = self.llm_selection_focus if self.llm_selection_focus is not None else self.llm_selection_anchor
        if focus is None:
            return False
        current = self._llm_block_index_for_line(focus)
        if current is None:
            return False
        return self._select_llm_block(current + direction)

    def _recent_llm_call_indices(self) -> list[int]:
        count = len(self.llm_debug_entries)
        start = max(0, count - 10)
        return list(range(count - 1, start - 1, -1))

    def _llm_call_kind(self, entry: dict[str, Any]) -> str:
        raw = normalize_id(str(entry.get("call_type") or "llm"))
        if raw in {"wild_magic", "wild magic"}:
            return "spell"
        if raw in {"dialogue", "trade", "town", "canon", "lore", "flesh"}:
            return raw
        return raw.replace("_", " ") or "llm"

    def _fit_text(self, text: str, font: pygame.font.Font, max_width: int) -> str:
        if font.size(text)[0] <= max_width:
            return text
        ellipsis = "..."
        result = text
        while result and font.size(result + ellipsis)[0] > max_width:
            result = result[:-1]
        return (result + ellipsis) if result else ellipsis

    def _activate_llm_call_button(self, entry_index: int) -> bool:
        if self.llm_selected_call_index == entry_index:
            part = "response" if self.llm_selected_call_part == "prompt" else "prompt"
        else:
            part = "prompt"
        return self._select_llm_entry_part(entry_index, part)

    def _select_llm_entry_part(self, entry_index: int, part: str) -> bool:
        if self._llm_lines_cache is None:
            self._llm_lines_cache = self._build_llm_lines(80)
        ranges = self.llm_entry_block_ranges.get(entry_index)
        if not ranges or part not in ranges:
            return False
        start, end = ranges[part]
        self.llm_selection_anchor = start
        self.llm_selection_focus = end
        visible_lines = max(1, self.llm_content_rect.height // (self.small_font.get_linesize() + 1))
        self.llm_scroll_offset = max(0, min(start, max(0, end - visible_lines + 1)))
        self.llm_autoscroll = False
        self.llm_selected_call_index = entry_index
        self.llm_selected_call_part = part
        return True

    def _investigate_command(self) -> str:
        """The x key: sweep the room, unless a found clue's anchor is in reach —
        standing on or beside the clued thing, x searches it instead. The verb
        itself stays a plain CLI command; this only chooses which one to send."""
        engine = self.engine
        player = engine.state.player
        room = engine.room_profile_at(player.x, player.y)
        if room is not None:
            slot = next((s for s in room.secret_slots if s.get("status") == "clued"), None)
            anchor = str(slot.get("anchor") or "") if slot else ""
            if anchor:
                if normalize_id(anchor) == normalize_id("the floor"):
                    return f"investigate {anchor}"
                for entity in engine.state.entities.values():
                    if (
                        entity.kind == "prop"
                        and max(abs(entity.x - player.x), abs(entity.y - player.y)) <= 1
                        and normalize_id(entity.name) == normalize_id(anchor)
                    ):
                        return f"investigate {anchor}"
        return "investigate"

    def submit_input(self) -> None:
        text = self.input_text.strip()
        if not text:
            return
        self.input_text = ""
        self.input_active = True
        self.log_scroll_offset = 0
        if self.input_mode == "talk":
            self.execute_command(f"talk {text}")
            return
        result = self.session.cast_wild(text)
        if result.wild_magic:
            self.provider_label = str(result.wild_magic.get("provider") or self.session.provider_label)
        self._record_llm_debug_entry(result)

    def _record_llm_debug_entry(self, result: ActionResult) -> None:
        self._refresh_llm_debug_entries()
        self._llm_lines_cache = None
        self.llm_autoscroll = True

    def draw(self) -> None:
        self.screen.fill(BACKGROUND)
        self.draw_llm_panel()
        self.draw_map()
        self.draw_panel()
        self.draw_autoplay_overlay()
        if self.inspect_tile is not None:
            self.draw_inspect_tooltip()
        if self.menu_active:
            self.draw_menu()
        if self.book_popup is not None:
            self.draw_book_popup()

    def draw_autoplay_overlay(self) -> None:
        lines = self.autoplay.overlay_lines()
        if not lines:
            return
        wrapped: list[tuple[str, tuple[int, int, int]]] = []
        for text, color in lines:
            for line in wrap_text(text, 62):
                wrapped.append((line, color))
        line_height = self.small_font.get_linesize() + 2
        width = MAP_PIXEL_WIDTH - 24
        height = 16 + len(wrapped) * line_height
        x = MAP_OFFSET_X + 12
        y = MAP_PIXEL_HEIGHT + 10
        if y + height > WINDOW_HEIGHT - 10:
            y = WINDOW_HEIGHT - height - 10
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((17, 19, 24, 222))
        self.screen.blit(overlay, (x, y))
        pygame.draw.rect(self.screen, PANEL_EDGE, (x, y, width, height), width=1, border_radius=6)
        cursor_y = y + 8
        for text, color in wrapped:
            self.draw_text(text, x + 10, cursor_y, self.small_font, color)
            cursor_y += line_height

    def draw_inspect_tooltip(self) -> None:
        tx, ty = self.inspect_tile  # type: ignore[misc]
        engine = self.engine
        state = engine.state

        if not engine.is_explored(tx, ty):
            self.inspect_tile = None
            return

        lines: list[tuple[str, tuple[int, int, int]]] = []

        # ── Tile ──────────────────────────────────────────────────────────────
        tile = state.tiles[ty][tx]
        tile_name = TILE_NAMES.get(tile, tile).title()
        base_tags = sorted(TILE_TAGS.get(tile, set()))
        dyn_tags = list(state.tile_tags.get(f"{tx},{ty}", []))
        all_tags = base_tags + [t for t in dyn_tags if t not in set(base_tags)]
        lines.append((f"[{tile}] {tile_name}", ACCENT))
        if all_tags:
            lines.append(("  " + ", ".join(all_tags), MUTED))
        room = engine.room_profile_at(tx, ty)
        if room is not None:
            lines.append((f"  {room.room_type} - {room.era}, {room.condition}", TEXT))
            topics = ", ".join(room.topics[:2])
            if topics:
                lines.append((f"  {topics}", MUTED))

        # ── Entities ──────────────────────────────────────────────────────────
        buttons: list[tuple[int, str]] = []  # (line index, command)
        player = state.player

        def _detail_summary(entity_id: str) -> str | None:
            for tier in ("close", "far"):
                record = state.canon_records.get(f"canon_detail_{entity_id}_{tier}")
                if record is not None and record.summary:
                    return record.summary
            return None

        visible = engine.is_visible(tx, ty)
        for entity in sorted(state.entities.values(), key=lambda e: e.id):
            if entity.x != tx or entity.y != ty:
                continue
            if not entity.alive and entity.kind not in {"item", "prop"}:
                continue
            if not visible and "revealed" not in entity.statuses and entity.id != state.player_id:
                continue

            lines.append(("", MUTED))

            if entity.kind == "prop":
                lines.append((f"[{entity.char}] {entity.name.title()}", GOLD))
                if entity.description:
                    for part in wrap_text(entity.description, 34):
                        lines.append((f"  {part}", TEXT))
                if entity.tags:
                    lines.append(("  " + ", ".join(sorted(entity.tags)), MUTED))

            elif entity.kind == "item":
                lines.append((f"[{entity.char}] {entity.name.title()}", GOLD))
                details = [p for p in [entity.item_type, entity.material] if p]
                if details:
                    lines.append(("  " + ", ".join(details), TEXT))
                if entity.tags:
                    lines.append(("  " + ", ".join(sorted(entity.tags)), MUTED))

            elif entity.id == state.player_id:
                lines.append((f"[{entity.char}] You", (246, 240, 200)))
                lines.append((f"  HP {entity.hp}/{entity.max_hp}  MP {entity.mana}/{entity.max_mana}", TEXT))
                if entity.statuses:
                    status_str = ", ".join(
                        entity.status_display.get(k, k) for k in sorted(entity.statuses)
                    )
                    for part in wrap_text(status_str, 34):
                        lines.append((f"  {part}", MUTED))

            elif entity.kind == "npc":
                profile = state.npc_profiles.get(entity.id)
                role_str = f" — {profile.role}" if profile and profile.role else ""
                lines.append((f"[{entity.char}] {entity.name}{role_str}", ACCENT))
                lines.append((f"  HP {entity.hp}/{entity.max_hp}  [{entity.faction}]", TEXT))
                if profile and profile.appearance:
                    for part in wrap_text(profile.appearance, 34):
                        lines.append((f"  {part}", TEXT))

            else:  # actor: enemy / ally / neutral
                ent_color = (
                    DANGER if entity.faction == "enemy"
                    else ACCENT if entity.faction == "ally"
                    else TEXT
                )
                lines.append((f"[{entity.char}] {entity.name}", ent_color))
                lines.append((f"  HP {entity.hp}/{entity.max_hp}  [{entity.faction}]", TEXT))
                if entity.statuses:
                    status_str = ", ".join(
                        entity.status_display.get(k, k) for k in sorted(entity.statuses)
                    )
                    for part in wrap_text(status_str, 34):
                        lines.append((f"  {part}", MUTED))
                if entity.tags:
                    lines.append(("  " + ", ".join(sorted(entity.tags)), MUTED))

            # Learned canon and study/read affordances for everything but you.
            if entity.id != state.player_id:
                summary = _detail_summary(entity.id)
                if summary:
                    for part in wrap_text(summary, 34):
                        lines.append((f"  {part}", (150, 170, 150)))
                distance = max(abs(entity.x - player.x), abs(entity.y - player.y))
                if entity.kind == "prop" and "book" in entity.tags and distance <= 1:
                    buttons.append((len(lines), f"read {entity.name}"))
                    lines.append(("  [ Read ]", (130, 185, 225)))
                buttons.append((len(lines), f"investigate {entity.id}"))
                lines.append(("  [ Investigate ]", (130, 185, 225)))

        if not lines:
            self.inspect_button_rects = []
            return

        # ── Draw box ──────────────────────────────────────────────────────────
        pad = 12
        tooltip_w = 310
        line_h = self.small_font.get_linesize() + 2
        total_h = pad * 2 + sum(4 if t == "" else line_h for t, _ in lines)

        tile_px = MAP_OFFSET_X + tx * TILE_SIZE
        tile_py = ty * TILE_SIZE
        bx = tile_px + TILE_SIZE + 4
        by = tile_py

        if bx + tooltip_w > WINDOW_WIDTH:
            bx = tile_px - tooltip_w - 4
        if by + total_h > WINDOW_HEIGHT:
            by = WINDOW_HEIGHT - total_h
        if by < 0:
            by = 0

        pygame.draw.rect(self.screen, (20, 22, 30), (bx, by, tooltip_w, total_h), border_radius=6)
        pygame.draw.rect(self.screen, PANEL_EDGE, (bx, by, tooltip_w, total_h), 1, border_radius=6)

        button_commands = dict(buttons)
        self.inspect_button_rects = []
        cy = by + pad
        for index, (text, color) in enumerate(lines):
            if text == "":
                cy += 4
                continue
            surf = self.small_font.render(text, True, color)
            self.screen.blit(surf, (bx + pad, cy))
            command = button_commands.get(index)
            if command:
                rect = pygame.Rect(bx + pad, cy - 1, surf.get_width() + 8, line_h)
                pygame.draw.rect(self.screen, (70, 95, 120), rect, 1, border_radius=4)
                self.inspect_button_rects.append((rect, command))
            cy += line_h

    def draw_book_popup(self) -> None:
        """A parchment page for reading books, modal over everything else.
        Long texts paginate; arrows/space/clicks turn pages."""
        assert self.book_popup is not None
        title = str(self.book_popup["title"])
        author = str(self.book_popup["author"])
        text = str(self.book_popup["text"])

        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((10, 8, 4, 180))
        self.screen.blit(overlay, (0, 0))

        parchment = (233, 222, 196)
        parchment_edge = (140, 112, 72)
        ink = (58, 44, 30)
        faded_ink = (120, 100, 72)

        box_w = 600
        box_h = min(640, WINDOW_HEIGHT - 60)
        pad = 36
        wrap_width = 58
        bx = (WINDOW_WIDTH - box_w) // 2
        by = (WINDOW_HEIGHT - box_h) // 2
        title_h = self.book_title_font.get_linesize()
        body_h = self.book_font.get_linesize()
        small_h = self.book_small_font.get_linesize()

        # Body lines with paragraph spacing preserved as blank lines.
        body_lines: list[str] = []
        for paragraph in text.split("\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                if body_lines and body_lines[-1] != "":
                    body_lines.append("")
                continue
            body_lines.extend(wrap_text(paragraph, wrap_width))
            body_lines.append("")
        while body_lines and body_lines[-1] == "":
            body_lines.pop()

        title_lines = wrap_text(title, 42)
        header_h = title_h * len(title_lines) + (small_h + 6 if author else 0) + 18
        footer_h = small_h + 18
        first_capacity = max(4, (box_h - pad * 2 - header_h - footer_h) // body_h)
        rest_capacity = max(4, (box_h - pad * 2 - footer_h) // body_h)

        pages: list[list[str]] = []
        remaining = list(body_lines)
        capacity = first_capacity
        while True:
            page_lines = remaining[:capacity]
            pages.append(page_lines)
            remaining = remaining[capacity:]
            while remaining and remaining[0] == "":
                remaining = remaining[1:]
            if not remaining:
                break
            capacity = rest_capacity
        self.book_popup["page_count"] = len(pages)
        page = max(0, min(int(self.book_popup.get("page", 0)), len(pages) - 1))
        self.book_popup["page"] = page

        pygame.draw.rect(self.screen, parchment, (bx, by, box_w, box_h), border_radius=4)
        pygame.draw.rect(self.screen, parchment_edge, (bx, by, box_w, box_h), 2, border_radius=4)
        pygame.draw.rect(self.screen, parchment_edge, (bx + 6, by + 6, box_w - 12, box_h - 12), 1, border_radius=3)

        cy = by + pad
        if page == 0:
            for line in title_lines:
                surf = self.book_title_font.render(line, True, ink)
                self.screen.blit(surf, (bx + (box_w - surf.get_width()) // 2, cy))
                cy += title_h
            if author:
                surf = self.book_small_font.render(f"— {author}", True, faded_ink)
                self.screen.blit(surf, (bx + (box_w - surf.get_width()) // 2, cy + 2))
                cy += small_h + 6
            cy += 8
            pygame.draw.line(self.screen, parchment_edge, (bx + box_w // 2 - 60, cy), (bx + box_w // 2 + 60, cy), 1)
            cy += 10

        for line in pages[page]:
            if line:
                surf = self.book_font.render(line, True, ink)
                self.screen.blit(surf, (bx + pad, cy))
            cy += body_h

        if len(pages) > 1:
            marker = self.book_small_font.render(f"— {page + 1} of {len(pages)} —", True, faded_ink)
            self.screen.blit(marker, (bx + (box_w - marker.get_width()) // 2, by + box_h - pad // 2 - small_h - 14))
        last_page = page + 1 >= len(pages)
        hint_text = (
            "Esc closes · click or arrows turn the page" if not last_page
            else "Esc or click to close the book"
        )
        hint = self.book_small_font.render(hint_text, True, faded_ink)
        self.screen.blit(hint, (bx + (box_w - hint.get_width()) // 2, by + box_h - pad // 2 - small_h + 2))

    def draw_menu(self) -> None:
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))

        if self.menu_page == "inventory":
            box_w = 700
            box_h = 450
            bx = (WINDOW_WIDTH - box_w) // 2
            by = (WINDOW_HEIGHT - box_h) // 2
            padding = 24

            pygame.draw.rect(self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6)
            pygame.draw.rect(self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6)

            title_surf = self.ui_font.render("EQUIPMENT & INVENTORY", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))

            gold_amount = self.engine.state.inventory.get("gold", 0)
            gold_surf = self.ui_font.render(f"Gold: {gold_amount}", True, GOLD)
            self.screen.blit(gold_surf, (bx + box_w - padding - gold_surf.get_width(), by + padding))

            pygame.draw.line(self.screen, PANEL_EDGE,
                             (bx + padding, by + padding + 22),
                             (bx + box_w - padding, by + padding + 22), 1)

            left_w = 300
            pane_y = by + padding + 36
            list_h = box_h - (padding * 2 + 36 + 24)
            row_h = 28

            player = self.engine.state.player
            slots = ["weapon", "armor", "charm", "head", "chest", "legs", "feet", "hands"]

            left_header = self.ui_font.render("Equipped Gear", True, MUTED)
            self.screen.blit(left_header, (bx + padding, pane_y))
            pane_y_list = pane_y + 24

            for idx, slot in enumerate(slots):
                qy = pane_y_list + idx * row_h
                is_selected = (self.inventory_pane == 0) and (idx == self.inventory_left_cursor)

                if is_selected:
                    pygame.draw.rect(self.screen, (50, 55, 70),
                                     (bx + padding - 4, qy - 2, left_w, row_h - 4), border_radius=4)

                item = player.equipment.get(slot)
                item_display = f"{item}" if item else "(empty)"
                display_text = f"{slot:<7} : {item_display}"

                if item:
                    color = GOLD if is_selected else TEXT
                else:
                    color = ACCENT if is_selected else MUTED

                q_surf = self.ui_font.render(display_text, True, color)
                self.screen.blit(q_surf, (bx + padding, qy))

            pygame.draw.line(self.screen, PANEL_EDGE,
                             (bx + padding + left_w + 10, pane_y),
                             (bx + padding + left_w + 10, pane_y + list_h), 1)

            rx = bx + padding + left_w + 24
            right_header = self.ui_font.render("Carried Items", True, MUTED)
            self.screen.blit(right_header, (rx, pane_y))
            pane_y_list = pane_y + 24

            inventory_items = sorted([item for item in self.engine.state.inventory.keys() if item != "gold"])

            if not inventory_items:
                empty_surf = self.ui_font.render("No items carried.", True, MUTED)
                self.screen.blit(empty_surf, (rx, pane_y_list))
            else:
                max_visible = (list_h - 24) // row_h
                start_right_idx = 0
                if len(inventory_items) > max_visible:
                    if self.inventory_right_cursor >= max_visible:
                        start_right_idx = self.inventory_right_cursor - max_visible + 1

                for idx in range(min(len(inventory_items), max_visible)):
                    item_idx = start_right_idx + idx
                    if item_idx >= len(inventory_items):
                        break
                    item_name = inventory_items[item_idx]
                    qty = self.engine.state.inventory.get(item_name, 0)
                    qy = pane_y_list + idx * row_h
                    is_selected = (self.inventory_pane == 1) and (item_idx == self.inventory_right_cursor)

                    if is_selected:
                        pygame.draw.rect(self.screen, (50, 55, 70),
                                         (rx - 4, qy - 2, box_w - padding * 2 - left_w - 30, row_h - 4), border_radius=4)

                    display_text = f"{item_name} x{qty}"
                    spec = EQUIPMENT_SPECS.get(item_name.strip().lower())
                    is_wearable = spec is not None or (infer_equipment_slot(item_name) is not None)

                    if is_selected:
                        color = GOLD
                    elif is_wearable:
                        color = (130, 220, 150)
                    else:
                        color = TEXT

                    item_surf = self.ui_font.render(display_text, True, color)
                    self.screen.blit(item_surf, (rx, qy))

            hint_surf = self.small_font.render("◄► Switch Pane  •  ▲▼ Select  •  Enter/E Equip  •  Enter/U Unequip  •  Esc Close", True, MUTED)
            self.screen.blit(hint_surf, (bx + padding, by + box_h - 22))
            return

        if self.menu_page == "quests":
            # Draw Quest Log Layout
            box_w = 640
            box_h = 400
            bx = (WINDOW_WIDTH - box_w) // 2
            by = (WINDOW_HEIGHT - box_h) // 2
            padding = 24

            pygame.draw.rect(self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6)
            pygame.draw.rect(self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6)

            # Title
            title_surf = self.ui_font.render("QUEST LOG", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))
            pygame.draw.line(self.screen, PANEL_EDGE,
                             (bx + padding, by + padding + 22),
                             (bx + box_w - padding, by + padding + 22), 1)

            # Left Pane: Quest list
            left_w = 260
            pane_y = by + padding + 36
            list_h = box_h - (padding * 2 + 36 + 24)
            row_h = 28
            
            quests = self.engine.quest_log_entries()
            if not quests:
                empty_surf = self.ui_font.render("No quests in log.", True, MUTED)
                self.screen.blit(empty_surf, (bx + padding, pane_y))
            else:
                max_visible = list_h // row_h
                start_idx = 0
                if len(quests) > max_visible:
                    if self.menu_cursor >= max_visible:
                        start_idx = self.menu_cursor - max_visible + 1
                
                for idx in range(min(len(quests), max_visible)):
                    q_idx = start_idx + idx
                    if q_idx >= len(quests):
                        break
                    q = quests[q_idx]
                    qy = pane_y + idx * row_h
                    is_selected = q_idx == self.menu_cursor
                    
                    if is_selected:
                        pygame.draw.rect(self.screen, (50, 55, 70),
                                         (bx + padding - 4, qy - 2, left_w, row_h - 4), border_radius=4)
                    
                    status_prefix = "[x]" if q.status == "completed" else "[ ]"
                    display_text = f"{status_prefix} {q.name}"
                    if len(display_text) > 24:
                        display_text = display_text[:21] + "..."
                        
                    color = GOLD if is_selected else (MUTED if q.status == "completed" else TEXT)
                    q_surf = self.ui_font.render(display_text, True, color)
                    self.screen.blit(q_surf, (bx + padding, qy))

            # Vertical divider
            pygame.draw.line(self.screen, PANEL_EDGE,
                             (bx + padding + left_w + 10, pane_y),
                             (bx + padding + left_w + 10, pane_y + list_h), 1)

            # Right Pane: Details
            if quests and self.menu_cursor < len(quests):
                q = quests[self.menu_cursor]
                rx = bx + padding + left_w + 24
                ry = pane_y
                
                # Name
                name_surf = self.ui_font.render(q.name, True, GOLD)
                self.screen.blit(name_surf, (rx, ry))
                ry += 28
                
                # Status
                status_color = (0, 200, 100) if q.status == "completed" else (220, 180, 50)
                status_surf = self.small_font.render(f"Status: {q.status.upper()}", True, status_color)
                self.screen.blit(status_surf, (rx, ry))
                ry += 20
                
                # Location
                loc_surf = self.small_font.render(f"Location: {q.location}", True, TEXT)
                self.screen.blit(loc_surf, (rx, ry))
                ry += 20
                
                # Contact
                contact_surf = self.small_font.render(f"Contact: {q.contact}", True, TEXT)
                self.screen.blit(contact_surf, (rx, ry))
                ry += 28
                
                # Underline
                pygame.draw.line(self.screen, PANEL_EDGE, (rx, ry), (bx + box_w - padding, ry), 1)
                ry += 12
                
                # Description
                desc_lines = wrap_text(q.description, 36)
                for line in desc_lines:
                    line_surf = self.small_font.render(line, True, TEXT)
                    self.screen.blit(line_surf, (rx, ry))
                    ry += 16
            else:
                rx = bx + padding + left_w + 24
                ry = pane_y
                no_details_surf = self.small_font.render("Select a quest to see details.", True, MUTED)
                self.screen.blit(no_details_surf, (rx, ry))

            # Footer
            hint_surf = self.small_font.render("▲▼/WS Select  •  Esc Close", True, MUTED)
            self.screen.blit(hint_surf, (bx + padding, by + box_h - 22))
            return

        if self.menu_page == "journal":
            # Journal: everything the world has told you. Same two-pane layout as the
            # quest log; entries are dicts from engine.journal_entries().
            box_w = 640
            box_h = 400
            bx = (WINDOW_WIDTH - box_w) // 2
            by = (WINDOW_HEIGHT - box_h) // 2
            padding = 24

            pygame.draw.rect(self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6)
            pygame.draw.rect(self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6)

            title_surf = self.ui_font.render("JOURNAL", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))
            pygame.draw.line(self.screen, PANEL_EDGE,
                             (bx + padding, by + padding + 22),
                             (bx + box_w - padding, by + padding + 22), 1)

            left_w = 260
            pane_y = by + padding + 36
            list_h = box_h - (padding * 2 + 36 + 24)
            row_h = 28

            entries = self.engine.journal_entries()
            if not entries:
                empty_surf = self.ui_font.render("The world hasn't told you anything yet.", True, MUTED)
                self.screen.blit(empty_surf, (bx + padding, pane_y))
            else:
                max_visible = list_h // row_h
                start_idx = 0
                if len(entries) > max_visible and self.menu_cursor >= max_visible:
                    start_idx = self.menu_cursor - max_visible + 1

                for idx in range(min(len(entries), max_visible)):
                    e_idx = start_idx + idx
                    if e_idx >= len(entries):
                        break
                    entry = entries[e_idx]
                    ey = pane_y + idx * row_h
                    is_selected = e_idx == self.menu_cursor

                    if is_selected:
                        pygame.draw.rect(self.screen, (50, 55, 70),
                                         (bx + padding - 4, ey - 2, left_w, row_h - 4), border_radius=4)

                    settled = entry["status"] in {"settled", "proved false"}
                    display_text = f"[{entry['status']}] {entry['subject']}"
                    if len(display_text) > 28:
                        display_text = display_text[:25] + "..."
                    color = GOLD if is_selected else (MUTED if settled else TEXT)
                    e_surf = self.ui_font.render(display_text, True, color)
                    self.screen.blit(e_surf, (bx + padding, ey))

            pygame.draw.line(self.screen, PANEL_EDGE,
                             (bx + padding + left_w + 10, pane_y),
                             (bx + padding + left_w + 10, pane_y + list_h), 1)

            if entries and self.menu_cursor < len(entries):
                entry = entries[self.menu_cursor]
                rx = bx + padding + left_w + 24
                ry = pane_y

                name_surf = self.ui_font.render(entry["subject"][:32], True, GOLD)
                self.screen.blit(name_surf, (rx, ry))
                ry += 28

                status_colors = {
                    "found true": (0, 200, 100),
                    "settled": (140, 140, 150),
                    "proved false": (200, 90, 90),
                    "corroborated": (130, 190, 255),
                }
                status_color = status_colors.get(entry["status"], (220, 180, 50))
                status_surf = self.small_font.render(f"Status: {entry['status'].upper()}", True, status_color)
                self.screen.blit(status_surf, (rx, ry))
                ry += 20

                if entry["source"] and entry["source"] != "unknown":
                    source_surf = self.small_font.render(f"Heard from: {entry['source'][:28]}", True, TEXT)
                    self.screen.blit(source_surf, (rx, ry))
                    ry += 20

                if entry["hint"]:
                    hint_text_surf = self.small_font.render(entry["hint"][:44], True, (130, 190, 255))
                    self.screen.blit(hint_text_surf, (rx, ry))
                    ry += 20

                ry += 8
                pygame.draw.line(self.screen, PANEL_EDGE, (rx, ry), (bx + box_w - padding, ry), 1)
                ry += 12

                for line in wrap_text(entry["text"], 36):
                    line_surf = self.small_font.render(line, True, TEXT)
                    self.screen.blit(line_surf, (rx, ry))
                    ry += 16
            else:
                rx = bx + padding + left_w + 24
                no_details_surf = self.small_font.render("Select an entry to read it.", True, MUTED)
                self.screen.blit(no_details_surf, (rx, pane_y))

            hint_surf = self.small_font.render("▲▼/WS Select  •  Esc Close", True, MUTED)
            self.screen.blit(hint_surf, (bx + padding, by + box_h - 22))
            return

        items = self._menu_items()
        row_h = 32
        padding = 24
        title = {"main": "MENU", "config": "CONFIGURATION", "model": "SELECT MODEL"}[self.menu_page]
        box_w = 480
        box_h = padding * 2 + 28 + len(items) * row_h + 20
        bx = (WINDOW_WIDTH - box_w) // 2
        by = (WINDOW_HEIGHT - box_h) // 2

        pygame.draw.rect(self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6)
        pygame.draw.rect(self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6)

        # Title
        title_surf = self.ui_font.render(title, True, ACCENT)
        self.screen.blit(title_surf, (bx + padding, by + padding))
        pygame.draw.line(self.screen, PANEL_EDGE,
                         (bx + padding, by + padding + 22),
                         (bx + box_w - padding, by + padding + 22), 1)

        # Items
        for i, item in enumerate(items):
            iy = by + padding + 30 + i * row_h
            is_selected = i == self.menu_cursor
            if is_selected:
                pygame.draw.rect(self.screen, (50, 55, 70),
                                 (bx + 8, iy - 4, box_w - 16, row_h - 4), border_radius=4)
            color = GOLD if is_selected else TEXT
            label = item["label"]
            surf = self.ui_font.render(label, True, color)
            self.screen.blit(surf, (bx + padding, iy))

            # Show ◄ ► hint for cycle/toggle items when selected
            if is_selected and item.get("action") == "config_item":
                spec = item["spec"]
                if spec["type"] in ("cycle", "toggle"):
                    hint = self.small_font.render("◄ ► or Enter", True, MUTED)
                    self.screen.blit(hint, (bx + box_w - padding - hint.get_width(), iy + 4))

        # Footer hint
        hints = {"main": "Enter select  •  Esc close",
                 "config": "Enter/◄► change  •  Esc back",
                 "model": "Enter select  •  Esc back"}
        hint_surf = self.small_font.render(hints[self.menu_page], True, MUTED)
        self.screen.blit(hint_surf, (bx + padding, by + box_h - 20))

    def draw_map(self) -> None:
        state = self.engine.state
        for y, row in enumerate(state.tiles):
            for x, tile in enumerate(row):
                if not self.engine.is_explored(x, y):
                    continue
                color = TILE_COLORS.get(tile, TILE_COLORS[FLOOR])
                if not self.engine.is_visible(x, y):
                    color = dim_color(color)
                self.draw_glyph(tile, x, y, color)
        for entity in sorted(state.entities.values(), key=lambda item: item.kind == "player"):
            if not entity.alive and entity.kind == "item":
                continue
            revealed = "revealed" in entity.statuses
            visible = self.engine.is_visible(entity.x, entity.y)
            if entity.id != state.player_id and not visible and not revealed:
                continue
            color = self.entity_color(entity)
            if revealed and not visible:
                color = dim_color(color)
            self.draw_glyph(entity.char, entity.x, entity.y, color)

    def draw_glyph(self, glyph: str, x: int, y: int, color: tuple[int, int, int]) -> None:
        surface = self.tile_font.render(glyph, True, color)
        rect = surface.get_rect(center=(MAP_OFFSET_X + x * TILE_SIZE + TILE_SIZE // 2, y * TILE_SIZE + TILE_SIZE // 2))
        self.screen.blit(surface, rect)

    def entity_color(self, entity: Entity) -> tuple[int, int, int]:
        if entity.kind == "item":
            return ENTITY_COLORS["item"]
        base = ENTITY_COLORS.get(entity.faction, ENTITY_COLORS["neutral"])
        if not entity.alive:
            return base
        s = entity.statuses
        if "burning" in s:
            return blend_color(base, (232, 96, 70), 0.55)
        if "frozen" in s:
            return blend_color(base, (156, 210, 224), 0.55)
        if "poisoned" in s:
            return blend_color(base, (130, 200, 80), 0.55)
        if "bleeding" in s:
            return blend_color(base, (200, 60, 60), 0.4)
        if "invisible" in s:
            return blend_color(base, BACKGROUND, 0.65)
        return base

    def draw_panel(self) -> None:
        x = MAP_OFFSET_X + MAP_PIXEL_WIDTH
        pygame.draw.rect(self.screen, PANEL, (x, 0, PANEL_WIDTH, WINDOW_HEIGHT))
        pygame.draw.line(self.screen, PANEL_EDGE, (x, 0), (x, WINDOW_HEIGHT), 2)
        state = self.engine.state
        player = state.player
        cursor_y = 18
        cursor_y = self.draw_text("Wild Magic", x + 20, cursor_y, self.ui_font, ACCENT)
        if state.scenario == "frontier":
            location = f"Zone ({state.zone_x},{state.zone_y}) — {state.zone_type}"
        else:
            location = f"Depth {state.depth}/{state.max_depth}"
        cursor_y = self.draw_text(
            f"Turn {state.turn}  {location}  Resolver {self.provider_label}",
            x + 20,
            cursor_y + 8,
            self.small_font,
            MUTED,
        )
        cursor_y = self.draw_bars(x + 20, cursor_y + 16, player)
        cursor_y = self.draw_gold(x + 20, cursor_y + 4)
        cursor_y = self.draw_statuses(x + 20, cursor_y + 10, player)
        cursor_y = self.draw_visible_enemies(x + 20, cursor_y + 8)
        cursor_y = self.draw_inventory(x + 20, cursor_y + 8)
        cursor_y = self.draw_floor_items(x + 20, cursor_y + 6)
        cursor_y = self.draw_curses(x + 20, cursor_y + 6)
        spell_height = self.spell_box_height()
        spell_y = WINDOW_HEIGHT - spell_height - 46
        log_y = cursor_y + 16
        log_height = max(120, spell_y - log_y - 46)
        self.draw_log(x + 20, log_y, log_height)
        self.draw_spell_box(x + 20, spell_y, spell_height)
        if state.game_over:
            overlay = pygame.Surface((MAP_PIXEL_WIDTH, MAP_PIXEL_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 150))
            self.screen.blit(overlay, (MAP_OFFSET_X, 0))
            big_font = pygame.font.SysFont("consolas", 48, bold=True)
            if state.victory:
                message = "YOU ESCAPED"
                color = ACCENT
                sub_text = "Press R to restart"
            elif state.death_cause == "empire":
                message = "CASE CLOSED"
                color = DANGER
                sub_text = "Incident filed, ref. 7-112 — press R to reopen the file"
            else:
                message = "THE WILD TAKES YOU BACK"
                color = DANGER
                sub_text = "Press R — another sorcerer takes up the thread"
            surface = big_font.render(message, True, color)
            rect = surface.get_rect(center=(MAP_OFFSET_X + MAP_PIXEL_WIDTH // 2, MAP_PIXEL_HEIGHT // 2))
            self.screen.blit(surface, rect)
            sub = pygame.font.SysFont("consolas", 18).render(sub_text, True, MUTED)
            sub_rect = sub.get_rect(center=(MAP_OFFSET_X + MAP_PIXEL_WIDTH // 2, MAP_PIXEL_HEIGHT // 2 + 50))
            self.screen.blit(sub, sub_rect)

    def draw_bars(self, x: int, y: int, player: Entity) -> int:
        y = self.draw_stat_bar(x, y, "HP", player.hp, player.max_hp, DANGER)
        y = self.draw_stat_bar(x, y + 8, "MP", player.mana, player.max_mana, MANA)
        return y

    def draw_gold(self, x: int, y: int) -> int:
        """Gold reads as a first-class resource (the setting's default currency,
        the LLM's reference unit when structuring trades) rather than just another
        line buried alphabetically in the inventory - so it gets its own readout,
        in the same accent color the inventory header already uses for it."""
        amount = self.engine.state.inventory.get("gold", 0)
        return self.draw_text(f"Gold: {amount}", x, y, self.small_font, GOLD)

    def draw_stat_bar(self, x: int, y: int, label: str, value: int, maximum: int, color: tuple[int, int, int]) -> int:
        self.draw_text(f"{label} {value}/{maximum}", x, y, self.small_font, TEXT)
        bar_x = x + 86
        bar_y = y + 3
        width = 220
        height = 12
        pygame.draw.rect(self.screen, (48, 50, 58), (bar_x, bar_y, width, height), border_radius=3)
        fill_width = int(width * (value / max(1, maximum)))
        pygame.draw.rect(self.screen, color, (bar_x, bar_y, fill_width, height), border_radius=3)
        return y + 22

    def draw_statuses(self, x: int, y: int, player: Entity) -> int:
        statuses = player.statuses
        if not statuses:
            return y
        STATUS_COLORS = {
            "burning": (232, 96, 70),
            "poisoned": (130, 200, 80),
            "bleeding": (220, 70, 70),
            "frozen": (156, 210, 224),
            "stunned": (220, 220, 100),
            "rooted": (100, 180, 100),
            "webbed": (200, 200, 150),
            "berserk": (220, 80, 80),
            "empowered": (255, 180, 50),
            "warded": (120, 180, 255),
            "invisible": (170, 170, 200),
            "regenerating": (100, 220, 150),
            "hasted": (220, 220, 255),
        }
        y = self.draw_text("Statuses", x, y, self.small_font, MUTED)
        parts = []
        for key, val in sorted(statuses.items()):
            display = player.status_display.get(key, key).replace("_", " ")
            dur = "" if val == "permanent" else f":{val}"
            parts.append((display + dur, STATUS_COLORS.get(key, TEXT)))
        line_parts: list[tuple[str, tuple[int, int, int]]] = []
        line_width = 0
        max_width = PANEL_WIDTH - 50
        for label, color in parts:
            w = self.small_font.size(label + "  ")[0]
            if line_width + w > max_width and line_parts:
                y = self.draw_colored_chips(x, y, line_parts)
                line_parts = []
                line_width = 0
            line_parts.append((label, color))
            line_width += w
        if line_parts:
            y = self.draw_colored_chips(x, y, line_parts)
        return y

    def draw_colored_chips(self, x: int, y: int, parts: list[tuple[str, tuple[int, int, int]]]) -> int:
        cx = x
        for label, color in parts:
            surface = self.small_font.render(label, True, color)
            self.screen.blit(surface, (cx, y))
            cx += surface.get_width() + 10
        return y + self.small_font.get_linesize() + 2

    def draw_visible_enemies(self, x: int, y: int) -> int:
        engine = self.engine
        player = engine.state.player
        all_enemies = engine.living_enemies()
        visible = [e for e in all_enemies if engine.is_visible(e.x, e.y)]
        hidden = len(all_enemies) - len(visible)
        if not all_enemies:
            return y
        y = self.draw_text(
            f"Enemies  {len(visible)} visible" + (f"  {hidden} unseen" if hidden else ""),
            x, y, self.small_font, DANGER if visible else MUTED
        )
        for enemy in sorted(visible, key=lambda e: engine.distance(player, e))[:4]:
            hp_frac = enemy.hp / max(1, enemy.max_hp)
            bar_color = DANGER if hp_frac < 0.4 else GOLD if hp_frac < 0.7 else (160, 200, 140)
            status_chips = " ".join(
                enemy.status_display.get(k, k) for k in sorted(enemy.statuses)[:2]
            )
            suffix = f"  {status_chips}" if status_chips else ""
            label = f"  {enemy.name} {enemy.hp}/{enemy.max_hp}{suffix}"
            y = self.draw_text(label, x, y, self.small_font, bar_color)
        if len(visible) > 4:
            y = self.draw_text(f"  …+{len(visible)-4} more", x, y, self.small_font, MUTED)
        return y

    def draw_floor_items(self, x: int, y: int) -> int:
        engine = self.engine
        player = engine.state.player
        visible_items = [
            e for e in engine.state.entities.values()
            if e.kind == "item" and engine.is_visible(e.x, e.y)
        ]
        if not visible_items:
            return y
        at_feet = [e for e in visible_items if e.x == player.x and e.y == player.y]
        nearby = [e for e in visible_items if e not in at_feet][:4]
        y = self.draw_text("Floor", x, y, self.small_font, GOLD)
        if at_feet:
            names = ", ".join(e.name for e in at_feet[:3])
            y = self.draw_text(f"[here] {names}", x, y, self.small_font, ACCENT)
        for item in nearby:
            dx = item.x - player.x
            dy = item.y - player.y
            dist = int((dx * dx + dy * dy) ** 0.5)
            y = self.draw_text(f"[{dist}] {item.name}", x, y, self.small_font, TEXT)
        return y

    def draw_inventory(self, x: int, y: int) -> int:
        state = self.engine.state
        # Gold gets its own dedicated readout (draw_gold) right next to the HP/MP
        # bars - showing it again here would just be visual noise.
        items = ", ".join(
            f"{name} x{amount}" for name, amount in state.inventory.items() if name != "gold"
        ) or "empty"
        y = self.draw_text("Inventory", x, y, self.small_font, GOLD)
        for line in wrap_text(items, 42):
            y = self.draw_text(line, x, y, self.small_font, TEXT)
        return y

    def draw_curses(self, x: int, y: int) -> int:
        curses = list(self.engine.state.curses.values())
        y = self.draw_text("Curses", x, y, self.small_font, DANGER if curses else MUTED)
        if not curses:
            return self.draw_text("none", x, y, self.small_font, MUTED)
        for curse in curses[-3:]:
            text = f"{curse.name} x{curse.stacks}"
            y = self.draw_text(text, x, y, self.small_font, TEXT)
        return y

    def draw_log(self, x: int, y: int, height: int) -> None:
        self.log_line_rects = []
        scrollbar_width = 10
        self.log_area = pygame.Rect(x, y, PANEL_WIDTH - 40 - scrollbar_width - 2, height)
        pygame.draw.line(self.screen, PANEL_EDGE, (x, y - 8), (WINDOW_WIDTH - 20, y - 8), 1)
        line_y = y
        lines: list[tuple[str, bool, bool]] = []
        line_height = self.small_font.get_linesize() + 2
        max_lines = max(1, height // line_height)
        for message in self.engine.state.messages[-1000:]:
            is_prompt = message.startswith(">") or message.startswith("*>")
            is_danger = is_player_damage_message(message)
            lines.extend((line, is_prompt, is_danger) for line in wrap_text(message, 42))

        total_lines = len(lines)
        self._log_max_scroll = max(0, total_lines - max_lines)
        self.log_scroll_offset = max(0, min(self.log_scroll_offset, self._log_max_scroll))

        start_idx = max(0, total_lines - max_lines - self.log_scroll_offset)
        end_idx = max(0, total_lines - self.log_scroll_offset)
        if total_lines <= max_lines:
            start_idx = 0
            end_idx = total_lines

        visible_lines = lines[start_idx:end_idx]
        selected_indexes = self.selected_log_indexes(len(visible_lines))
        for index, (line, is_prompt, is_danger) in enumerate(visible_lines):
            color = MUTED if is_prompt else (DANGER if is_danger else TEXT)
            rect = pygame.Rect(x - 4, line_y - 1, PANEL_WIDTH - 32 - scrollbar_width - 2, line_height)
            if index in selected_indexes:
                pygame.draw.rect(self.screen, SELECTED, rect, border_radius=3)
            line_y = self.draw_text(line, x, line_y, self.small_font, color)
            self.log_line_rects.append((rect, line))
            if line_y > y + height:
                break

        self.draw_log_scrollbar(x + PANEL_WIDTH - 40 - scrollbar_width, y, scrollbar_width, height, total_lines, max_lines)

    def selected_log_indexes(self, visible_line_count: int) -> set[int]:
        if self.log_selection_anchor is None or self.log_selection_focus is None:
            return set()
        if not self.log_line_rects and visible_line_count == 0:
            return set()
        start = min(self.log_selection_anchor, self.log_selection_focus)
        end = max(self.log_selection_anchor, self.log_selection_focus)
        return {index for index in range(max(0, start), min(visible_line_count - 1, end) + 1)}

    def spell_box_height(self) -> int:
        if self.input_mode == "control":
            visible_lines = len(wrap_text(CONTROLS_HINT, CONTROLS_HINT_WRAP))
        elif self.engine.state.pending_trade is not None:
            # Two item lines ("You receive:" + "You give:") plus the Y/N hint.
            # Fixed at 3 lines — the flavor text stays in the message log above.
            visible_lines = 3
        else:
            visible_lines = min(max(2, len(wrap_text(self.input_text or " ", 42))), 6)
        return 18 + visible_lines * 18

    def draw_mode_box(self, text: str, x: int, y: int, color: tuple[int, int, int], active: bool) -> pygame.Rect:
        """A clickable mode-switch box, tinted with its own color when active and
        faded toward the panel background when not - so the three options (Wild
        Spell/Talk/Controls) read as distinct colored controls at a glance, with
        the current one clearly lit up."""
        surface = self.small_font.render(text, True, TEXT if active else MUTED)
        pad_x, pad_y = 10, 5
        rect = pygame.Rect(x, y, surface.get_width() + pad_x * 2, surface.get_height() + pad_y * 2)
        if active:
            pygame.draw.rect(self.screen, blend_color(PANEL, color, 0.24), rect, border_radius=6)
            pygame.draw.rect(self.screen, color, rect, width=2, border_radius=6)
        else:
            pygame.draw.rect(self.screen, blend_color(PANEL, color, 0.12), rect, width=1, border_radius=6)
        self.screen.blit(surface, (x + pad_x, y + pad_y))
        return rect

    def draw_spell_box(self, x: int, y: int, height: int) -> None:
        width = PANEL_WIDTH - 40
        pygame.draw.line(self.screen, PANEL_EDGE, (x, y - 42), (WINDOW_WIDTH - 20, y - 42), 1)
        box_y = y - 34

        talk_target = self.engine.find_talk_target()
        talk_target_id = talk_target.id if talk_target is not None else None
        if talk_target_id != self._last_talk_target_id:
            if talk_target_id is not None:
                # Just became adjacent to someone talkable - default to Talk mode,
                # but only on this transition, so a deliberate click away from Talk
                # sticks for as long as the player stays next to the same NPC.
                self.input_mode = "talk"
                self.input_active = True
            elif self.input_mode == "talk":
                # Walked out of talking range while in Talk mode - nothing legal to
                # talk to anymore, so fall back to casting.
                self.input_mode = "spell"
                self.input_active = True
            self._last_talk_target_id = talk_target_id

        # Same transition-based pattern as talk-target switching above: force the
        # mode the *moment* a trade appears or resolves, not every frame - so a
        # confirmation can't be dodged by clicking elsewhere, and resolving it
        # (accept/reject) hands control straight back to whatever made sense before.
        trade_active = self.engine.state.pending_trade is not None
        if trade_active != self._last_trade_active:
            if trade_active:
                self.input_mode = "confirm_trade"
                self.input_active = False
                # `talk` can block on up to two sequential LLM calls (6-24s) with
                # the whole event loop frozen. Any Enter/Y the player pressed
                # (or that key-repeat queued) during that wait is still sitting
                # in the queue when this modal claims control on the next frame
                # -- without this, handle_key's confirm_trade gate immediately
                # "accepts" using that stale keypress, before the player ever
                # sees the proposal. Flush it so only fresh input reaches the modal.
                pygame.event.clear((pygame.KEYDOWN, pygame.KEYUP))
            elif self.input_mode == "confirm_trade":
                self.input_mode = "talk" if talk_target is not None else "spell"
                self.input_active = True
            self._last_trade_active = trade_active

        specs = [("spell", "Wild Spell", MODE_PURPLE)]
        if talk_target is not None:
            specs.append(("talk", "Talk", MODE_YELLOW))
        specs.append(("control", "Controls", MODE_GREEN))
        cursor_x = x
        self.mode_label_rects = []
        for mode, label, color in specs:
            rect = self.draw_mode_box(label, cursor_x, box_y, color, self.input_mode == mode)
            self.mode_label_rects.append((rect, mode))
            cursor_x = rect.right + 10
        if trade_active:
            # Deliberately not in `specs` / `mode_label_rects` - this box appears
            # only when a real decision is pending, never as a voluntary tab.
            self.draw_mode_box("Confirm Trade", cursor_x, box_y, MODE_ORANGE, True)

        rect = pygame.Rect(x, y, width, height)
        self.spell_box_rect = rect
        pygame.draw.rect(self.screen, (17, 19, 24), rect, border_radius=6)
        pygame.draw.rect(self.screen, MODE_COLORS[self.input_mode], rect, width=1, border_radius=6)
        if self.input_mode == "control":
            for index, line in enumerate(wrap_text(CONTROLS_HINT, CONTROLS_HINT_WRAP)):
                self.draw_text(line, x + 10, y + 9 + index * 18, self.small_font, MUTED)
            return
        if self.input_mode == "confirm_trade" and self.engine.state.pending_trade is not None:
            trade = self.engine.state.pending_trade

            def _fmt_items(items: list) -> str:
                if not items:
                    return "nothing"
                parts = []
                for entry in items:
                    qty = entry.get("quantity", 1)
                    name = str(entry.get("item", "?"))
                    parts.append(f"{qty} {name}" if qty != 1 else name)
                return ", ".join(parts)

            receive_line = f"You receive:  {_fmt_items(trade.get('npc_gives') or [])}"
            give_line    = f"You give:     {_fmt_items(trade.get('npc_wants') or [])}"
            cursor_y = y + 9
            cursor_y = self.draw_text(receive_line, x + 10, cursor_y, self.ui_font, TEXT)
            cursor_y = self.draw_text(give_line,    x + 10, cursor_y, self.ui_font, TEXT)
            self.draw_text("[Y]es accept    [N]o reject", x + 10, cursor_y + 6, self.small_font, MODE_ORANGE)
            return
        if not self.input_text and self.input_mode == "talk" and talk_target is not None:
            self.draw_text(f"Say something to {talk_target.name}...", x + 10, y + 9, self.ui_font, MUTED)
            return
        shown = self.input_text
        if self.input_active and pygame.time.get_ticks() % 1000 < 500:
            shown += "_"
        lines = wrap_text(shown or " ", 42)
        max_visible_lines = max(1, (height - 18) // 18)
        visible_lines = lines[-max_visible_lines:]
        if len(lines) > max_visible_lines and visible_lines:
            visible_lines[0] = "..." + visible_lines[0][-39:]
        for index, line in enumerate(visible_lines):
            self.draw_text(line, x + 10, y + 9 + index * 18, self.ui_font, TEXT)

    def draw_llm_panel(self) -> None:
        x = 0
        pygame.draw.rect(self.screen, PANEL, (x, 0, LLM_PANEL_WIDTH, WINDOW_HEIGHT))
        pygame.draw.line(self.screen, PANEL_EDGE, (LLM_PANEL_WIDTH, 0), (LLM_PANEL_WIDTH, WINDOW_HEIGHT), 2)
        cursor_y = self.draw_text("LLM Debug", x + 16, 16, self.ui_font, ACCENT)
        buttons_bottom = self.draw_llm_call_buttons(x + 16, cursor_y + 12, LLM_PANEL_WIDTH - 32)
        divider_y = max(cursor_y + 10, buttons_bottom + 8)
        pygame.draw.line(self.screen, PANEL_EDGE, (x + 16, divider_y), (LLM_PANEL_WIDTH - 16, divider_y), 1)
        content_y = divider_y + 10
        content_height = WINDOW_HEIGHT - content_y - 16
        self.draw_llm_content(x + 16, content_y, LLM_PANEL_WIDTH - 32, content_height)

    def draw_llm_call_buttons(self, x: int, y: int, width: int) -> int:
        self._refresh_llm_debug_entries()
        self.llm_call_button_rects = []
        recent = self._recent_llm_call_indices()
        if not recent:
            return y - 8
        gap = 6
        button_h = 24
        button_w = max(40, (width - gap * 4) // 5)
        for slot, entry_index in enumerate(recent):
            entry = self.llm_debug_entries[entry_index]
            col = slot % 5
            row = slot // 5
            rect = pygame.Rect(x + col * (button_w + gap), y + row * (button_h + gap), button_w, button_h)
            kind = self._llm_call_kind(entry)
            color = LLM_CALL_COLORS.get(kind, PANEL_EDGE)
            fill = tuple(max(0, int(channel * 0.28)) for channel in color)
            pygame.draw.rect(self.screen, fill, rect, border_radius=5)
            border = TEXT if entry_index == self.llm_selected_call_index else color
            pygame.draw.rect(self.screen, border, rect, 1, border_radius=5)
            label = self._fit_text(kind, self.small_font, rect.width - 10)
            label_surf = self.small_font.render(label, True, TEXT)
            self.screen.blit(label_surf, (rect.x + 5, rect.y + (rect.height - label_surf.get_height()) // 2))
            self.llm_call_button_rects.append((rect, entry_index))
        rows = 1 + (len(recent) - 1) // 5
        return y + rows * button_h + (rows - 1) * gap

    def draw_llm_content(self, x: int, y: int, width: int, height: int) -> None:
        scrollbar_width = 10
        text_width = max(20, width - scrollbar_width - 6)
        self.llm_content_rect = pygame.Rect(x, y, width, height)

        char_width = max(1, self.small_font.size("M")[0])
        wrap_chars = max(10, text_width // char_width)
        if self._llm_lines_cache is None:
            self._llm_lines_cache = self._build_llm_lines(wrap_chars)
        else:
            now_sec = int(time.monotonic())
            if now_sec != getattr(self, "_llm_cache_sec", -1):
                self._llm_cache_sec = now_sec
                self._llm_lines_cache = self._build_llm_lines(wrap_chars)
        lines = self._llm_lines_cache

        line_height = self.small_font.get_linesize() + 1
        max_visible = max(1, height // line_height)
        self._llm_max_scroll = max(0, len(lines) - max_visible)
        if self.llm_autoscroll:
            self.llm_scroll_offset = self._llm_max_scroll
        self.llm_scroll_offset = max(0, min(self.llm_scroll_offset, self._llm_max_scroll))

        sel_lo, sel_hi = None, None
        if self.llm_selection_anchor is not None and self.llm_selection_focus is not None:
            sel_lo = min(self.llm_selection_anchor, self.llm_selection_focus)
            sel_hi = max(self.llm_selection_anchor, self.llm_selection_focus)

        clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(x, y, width, height))
        self.llm_line_rects = []
        line_y = y
        visible_slice = lines[self.llm_scroll_offset : self.llm_scroll_offset + max_visible + 1]
        for offset, (text, color) in enumerate(visible_slice):
            abs_index = self.llm_scroll_offset + offset
            rect = pygame.Rect(x - 4, line_y - 1, width - scrollbar_width - 2, line_height)
            if sel_lo is not None and sel_lo <= abs_index <= sel_hi:
                pygame.draw.rect(self.screen, SELECTED, rect, border_radius=3)
            if text:
                self.draw_text(text, x, line_y, self.small_font, color)
            self.llm_line_rects.append((rect, abs_index))
            line_y += line_height
        self.screen.set_clip(clip)

        self.draw_llm_scrollbar(x + width - scrollbar_width, y, scrollbar_width, height, len(lines), max_visible)

    def draw_llm_scrollbar(self, x: int, y: int, width: int, height: int, total_lines: int, visible_lines: int) -> None:
        track = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, (20, 22, 27), track, border_radius=4)
        if total_lines <= visible_lines or self._llm_max_scroll <= 0:
            self.llm_scrollbar_track_rect = None
            self.llm_scrollbar_thumb_rect = None
            return
        thumb_height = max(28, int(height * (visible_lines / total_lines)))
        usable = max(1, height - thumb_height)
        thumb_y = y + int(usable * (self.llm_scroll_offset / self._llm_max_scroll))
        thumb = pygame.Rect(x, thumb_y, width, thumb_height)
        thumb_color = ACCENT if self.llm_dragging_scrollbar else PANEL_EDGE
        pygame.draw.rect(self.screen, thumb_color, thumb, border_radius=4)
        self.llm_scrollbar_track_rect = track
        self.llm_scrollbar_thumb_rect = thumb

    def draw_log_scrollbar(self, x: int, y: int, width: int, height: int, total_lines: int, visible_lines: int) -> None:
        track = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, (20, 22, 27), track, border_radius=4)
        if total_lines <= visible_lines or self._log_max_scroll <= 0:
            self.log_scrollbar_track_rect = None
            self.log_scrollbar_thumb_rect = None
            return
        thumb_height = max(28, int(height * (visible_lines / total_lines)))
        usable = max(1, height - thumb_height)
        thumb_y = y + usable - int(usable * (self.log_scroll_offset / self._log_max_scroll))
        thumb = pygame.Rect(x, thumb_y, width, thumb_height)
        thumb_color = ACCENT if self.log_dragging_scrollbar else PANEL_EDGE
        pygame.draw.rect(self.screen, thumb_color, thumb, border_radius=4)
        self.log_scrollbar_track_rect = track
        self.log_scrollbar_thumb_rect = thumb

    def _log_scroll_to_fraction(self, fraction: float) -> None:
        if self._log_max_scroll <= 0:
            return
        fraction = max(0.0, min(1.0, fraction))
        self.log_scroll_offset = int(round((1.0 - fraction) * self._log_max_scroll))

    def _log_scrollbar_fraction_at(self, mouse_y: int) -> float | None:
        track = self.log_scrollbar_track_rect
        thumb = self.log_scrollbar_thumb_rect
        if track is None or thumb is None:
            return None
        usable = track.height - thumb.height
        if usable <= 0:
            return None
        target_thumb_y = mouse_y - self.log_drag_grab_dy
        return (target_thumb_y - track.y) / usable

    def _build_llm_lines_legacy_unused(self, wrap_chars: int) -> list[tuple[str, tuple[int, int, int]]]:
        lines: list[tuple[str, tuple[int, int, int]]] = []

        def emit(text: str, color: tuple[int, int, int]) -> None:
            for raw_line in text.splitlines() or [""]:
                for wrapped in wrap_text(raw_line, wrap_chars):
                    lines.append((wrapped, color))

        emit("Prompt", ACCENT)
        emit("(legacy debug renderer is inactive)", MUTED)
        lines.append(("", MUTED))
        lines.append(("=" * wrap_chars, PANEL_EDGE))

        pending = getattr(self.engine, "_pending_towns", {})
        if pending:
            lines.append(("", MUTED))
            emit("TOWN GENERATION IN PROGRESS", GOLD)
            now = time.monotonic()
            for key in pending:
                ctx = getattr(self.engine, "_pending_town_contexts", {}).get(key, {})
                start = getattr(self.engine, "_pending_town_start_times", {}).get(key, now)
                elapsed = now - start
                remaining = max(0.0, _TOWN_GEN_TIMEOUT - elapsed)
                zx, zy = key
                emit(f"  Zone ({zx}, {zy}) — {remaining:.0f}s remaining", MODE_ORANGE)
                if ctx.get("settlement_type"):
                    emit(f"  Type: {ctx['settlement_type']}", TEXT)
                if ctx.get("location"):
                    emit(f"  Location: {ctx['location']}", TEXT)
                if ctx.get("defining_trait"):
                    emit(f"  Trait: {ctx['defining_trait']}", TEXT)
                if ctx.get("current_situation"):
                    emit(f"  Situation: {ctx['current_situation']}", TEXT)
                lines.append(("", MUTED))
            lines.append(("=" * wrap_chars, PANEL_EDGE))

        if not self.llm_debug_entries:
            lines.append(("", MUTED))
            emit("No spells cast yet — type one in the spell box and press Enter to see it here.", MUTED)
            return lines

        for entry in self.llm_debug_entries:
            lines.append(("", MUTED))
            header = f"— Turn {entry['turn']}  ·  \"{entry['spell']}\"  ·  provider: {entry['provider']}"
            emit(header, DANGER if entry["technical_failure"] else GOLD)
            if entry.get("error"):
                emit(f"error: {entry['error']}", DANGER)

            emit("CONTEXT SENT TO MODEL:", ACCENT)
            context = entry.get("context")
            if context is not None:
                emit(json.dumps(context, indent=2, ensure_ascii=False), TEXT)
            else:
                emit("(context unavailable — this cast came from a replay)", MUTED)

            emit("Response", ACCENT)
            thinking = entry.get("thinking")
            if thinking:
                emit(thinking, MUTED)
            else:
                emit("(model returned no <think> reasoning block)", MUTED)

            emit("Response", ACCENT)
            emit(entry.get("response") or "(no response captured)", TEXT)

        return lines

    def _refresh_llm_debug_entries(self) -> None:
        entries_changed = False
        base_dir = audit_dir()
        for filename in LLM_AUDIT_FILES:
            path = base_dir / filename
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        key = f"{path}:{line_no}"
                        if key in self.llm_debug_seen:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        timestamp = self._parse_audit_timestamp(record.get("timestamp"))
                        if timestamp is not None and timestamp < self.llm_debug_started_at:
                            self.llm_debug_seen.add(key)
                            continue
                        self.llm_debug_seen.add(key)
                        self.llm_debug_entries.append(self._audit_record_to_debug_entry(filename, record))
                        entries_changed = True
            except OSError:
                continue
        if entries_changed:
            self.llm_debug_entries.sort(key=lambda entry: entry.get("timestamp") or "")
            self._llm_lines_cache = None

    def _parse_audit_timestamp(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _audit_record_to_debug_entry(self, filename: str, record: dict[str, Any]) -> dict[str, Any]:
        call_type = filename.removesuffix("_audit.jsonl").replace("_", " ")
        if filename == "wild_magic_audit.jsonl":
            call_type = "wild magic"
        return {
            "timestamp": str(record.get("timestamp") or ""),
            "call_type": call_type,
            "provider": str(record.get("provider") or record.get("provider_requested") or ""),
            "model": str(record.get("model") or ""),
            "technical_failure": bool(record.get("technical_failure")),
            "error": record.get("error"),
            "prompt": self._format_audit_prompt(record),
            "response": self._format_audit_response(record),
        }

    def _format_audit_prompt(self, record: dict[str, Any]) -> str:
        prompt = record.get("prompt")
        if isinstance(prompt, dict):
            messages = prompt.get("messages")
            if isinstance(messages, list) and messages:
                parts: list[str] = []
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    role = str(message.get("role") or "message").upper()
                    content = str(message.get("content") or "")
                    parts.append(f"{role}:\n{content}")
                if parts:
                    return "\n\n".join(parts)
            if "context" in prompt:
                return json.dumps(prompt["context"], indent=2, ensure_ascii=False)
        if "context" in record:
            return json.dumps(record["context"], indent=2, ensure_ascii=False)
        return "(prompt unavailable)"

    def _format_audit_response(self, record: dict[str, Any]) -> str:
        raw = record.get("raw_response")
        if raw is not None:
            return str(raw)
        for key in ("parsed_resolution", "reply", "claims", "record", "flesh", "town"):
            if record.get(key) is not None:
                return json.dumps(record[key], indent=2, ensure_ascii=False)
        return "(no response captured)"

    def _build_llm_lines(self, wrap_chars: int) -> list[tuple[str, tuple[int, int, int]]]:
        self._refresh_llm_debug_entries()
        lines: list[tuple[str, tuple[int, int, int]]] = []
        block_ranges: list[tuple[int, int]] = []
        entry_block_ranges: dict[int, dict[str, tuple[int, int]]] = {}

        def emit(text: str, color: tuple[int, int, int]) -> None:
            for raw_line in text.splitlines() or [""]:
                for wrapped in wrap_text(raw_line, wrap_chars):
                    lines.append((wrapped, color))

        def emit_block(label: str, text: str, color: tuple[int, int, int]) -> tuple[int, int]:
            start = len(lines)
            emit(label, ACCENT)
            emit(text or "(empty)", color)
            block_range = (start, max(start, len(lines) - 1))
            block_ranges.append(block_range)
            lines.append(("", MUTED))
            return block_range

        pending = getattr(self.engine, "_pending_towns", {})
        if pending:
            emit("Town generation in progress", GOLD)
            now = time.monotonic()
            for key in pending:
                ctx = getattr(self.engine, "_pending_town_contexts", {}).get(key, {})
                start = getattr(self.engine, "_pending_town_start_times", {}).get(key, now)
                remaining = max(0.0, _TOWN_GEN_TIMEOUT - (now - start))
                zx, zy = key
                emit(f"  Zone ({zx}, {zy}) - {remaining:.0f}s remaining", MODE_ORANGE)
                if ctx.get("settlement_type"):
                    emit(f"  Type: {ctx['settlement_type']}", TEXT)
                if ctx.get("location"):
                    emit(f"  Location: {ctx['location']}", TEXT)
                if ctx.get("defining_trait"):
                    emit(f"  Trait: {ctx['defining_trait']}", TEXT)
                if ctx.get("current_situation"):
                    emit(f"  Situation: {ctx['current_situation']}", TEXT)
                lines.append(("", MUTED))

        if not self.llm_debug_entries:
            lines.append(("", MUTED))
            emit("No LLM calls captured yet.", MUTED)
            self.llm_block_ranges = []
            self.llm_entry_block_ranges = {}
            return lines

        for entry_index, entry in enumerate(self.llm_debug_entries):
            lines.append(("", MUTED))
            header_bits = [entry.get("call_type") or "llm"]
            if entry.get("provider"):
                header_bits.append(f"provider {entry['provider']}")
            if entry.get("model"):
                header_bits.append(str(entry["model"]))
            if entry.get("timestamp"):
                header_bits.append(str(entry["timestamp"]))
            emit(" | ".join(header_bits), DANGER if entry["technical_failure"] else GOLD)
            if entry.get("error"):
                emit(f"error: {entry['error']}", DANGER)
            entry_block_ranges[entry_index] = {
                "prompt": emit_block("Prompt", str(entry.get("prompt") or ""), TEXT),
                "response": emit_block("Response", str(entry.get("response") or ""), TEXT),
            }

        self.llm_block_ranges = block_ranges
        self.llm_entry_block_ranges = entry_block_ranges
        return lines

    def handle_mouse_wheel(self, event: pygame.event.Event) -> None:
        pos = pygame.mouse.get_pos()
        if self.llm_content_rect.collidepoint(pos):
            self.llm_scroll_offset -= event.y * 3
            self.llm_scroll_offset = max(0, min(self.llm_scroll_offset, self._llm_max_scroll))
            self.llm_autoscroll = self._llm_max_scroll > 0 and self.llm_scroll_offset >= self._llm_max_scroll
        elif self.log_area.collidepoint(pos):
            self.log_scroll_offset += event.y * 3
            self.log_scroll_offset = max(0, min(self.log_scroll_offset, self._log_max_scroll))

    def _llm_scroll_to_fraction(self, fraction: float) -> None:
        if self._llm_max_scroll <= 0:
            return
        fraction = max(0.0, min(1.0, fraction))
        self.llm_scroll_offset = int(round(fraction * self._llm_max_scroll))
        self.llm_autoscroll = self.llm_scroll_offset >= self._llm_max_scroll

    def _llm_scrollbar_fraction_at(self, mouse_y: int) -> float | None:
        track = self.llm_scrollbar_track_rect
        thumb = self.llm_scrollbar_thumb_rect
        if track is None or thumb is None:
            return None
        usable = track.height - thumb.height
        if usable <= 0:
            return None
        target_thumb_y = mouse_y - self.llm_drag_grab_dy
        return (target_thumb_y - track.y) / usable

    def draw_text(
        self,
        text: str,
        x: int,
        y: int,
        font: pygame.font.Font,
        color: tuple[int, int, int],
    ) -> int:
        surface = font.render(text, True, color)
        self.screen.blit(surface, (x, y))
        return y + surface.get_height() + 2


def blend_color(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    return (
        int(a[0] * (1 - t) + b[0] * t),
        int(a[1] * (1 - t) + b[1] * t),
        int(a[2] * (1 - t) + b[2] * t),
    )


def wrap_text(text: str, width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for raw_line in text.splitlines():
        wrapped = textwrap.wrap(raw_line, width=width, replace_whitespace=False) or [""]
        lines.extend(wrapped)
    return lines


def dim_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return (max(20, color[0] // 3), max(20, color[1] // 3), max(24, color[2] // 3))


def is_player_damage_message(message: str) -> bool:
    if getattr(message, "is_danger", False):
        return True

    msg_lower = message.lower()
    
    # 1. Player is hit by someone/something (e.g. "cave spider hits You for 3.")
    # Note: the player's name is "You" in these messages, so it matches "hits you" or "hit you".
    if "hits you" in msg_lower or "hit you" in msg_lower:
        return True
        
    # 2. Player takes damage, suffers, loses health, or dies
    if "you suffer" in msg_lower or "you die" in msg_lower:
        return True
    if "you take" in msg_lower and "damage" in msg_lower:
        return True
    if "you lose" in msg_lower and any(w in msg_lower for w in {"health", "hp", "maximum health", "max health", "max hp"}):
        return True
        
    # 3. Health/HP cost paid (e.g. "Cost: 3 health.")
    if "cost:" in msg_lower and ("health" in msg_lower or "hp" in msg_lower):
        return True
        
    # 4. Harmful status/damage applied to player ("You are poisoned!")
    if "you are " in msg_lower:
        harm_words = {
            "poisoned", "webbed", "frozen", "stunned", "burning", "burned", 
            "shocked", "damaged", "hurt", "wounded", "bleeding", "cursed", 
            "slowed", "confused", "frightened", "knocked back", "held in place"
        }
        if any(w in msg_lower for w in harm_words):
            if not any(pos in msg_lower for pos in {"extinguish", "cauterized", "heal", "recover"}):
                return True
                
    # 5. Specific flavor alerts about player damage/danger
    # e.g., "Your wound is bleeding!" or "Acid dissolves your ward!"
    if "your " in msg_lower:
        your_harm_words = {"wound", "flames", "acid dissolves your", "bleeding", "poisoned", "burning"}
        if any(w in msg_lower for w in your_harm_words):
            if not any(pos in msg_lower for pos in {"extinguish", "cauterized", "heal", "recover"}):
                return True
                
    return False


def run_game(autoplay: bool = False) -> None:
    GameUI(autoplay=autoplay).run()
