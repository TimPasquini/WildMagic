from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import os
import time
from typing import Any
import warnings

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# pygame 2.6.1's pkgdata still imports pkg_resources, which setuptools >= 81 flags as
# deprecated. The import is pygame's own and harmless; silence just that one warning so it
# doesn't clutter startup. Remove once pygame ships a build without pkg_resources.
warnings.filterwarnings("ignore", message=r"pkg_resources is deprecated as an API.*")

import pygame

from .actions import ActionResult, GameSession, describe_state
from .autoplay import (
    AgentObservation,
    OllamaAgent,
    adjacent_options,
    avoid_commands_from_history,
    compact_messages,
    autoplay_run_theme_for_seed,
    expedition_direction_for_seed,
    local_map_view,
    result_summary,
    validate_agent_command,
)
from .game_data import _TOWN_GEN_TIMEOUT
from .normalize import normalize_id
from .portraits import PortraitClient
from . import rendering
from .rendering.llm_debug_window import LlmDebugWindow
from .rendering import (
    ACCENT,
    DANGER,
    GOLD,
    GameFonts,
    LLM_PANEL_WIDTH,
    MAP_OFFSET_X,
    MAP_PIXEL_HEIGHT,
    MAP_PIXEL_WIDTH,
    MANA,
    MODE_COLORS,
    MODE_GREEN,
    MODE_ORANGE,
    MODE_PURPLE,
    MODE_YELLOW,
    MUTED,
    PANEL,
    PANEL_EDGE,
    PANEL_WIDTH,
    SELECTED,
    TEXT,
    TILE_SIZE,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
    GameWindow,
    blend_color,
    is_player_damage_message,
    wrap_text,
)
from .scenes.character_creation_scene import CharacterCreationScene
from .scenes.character_view_scene import CharacterViewScene
from .scenes.menu_scene import MenuScene
from .scenes.standing_scene import StandingScene
from .models import (
    Entity,
)

_MOVE_KEY_MAP: dict[int, str] = {
    # No vi-keys (h/j/k/l) here: j opens the journal, and the others are
    # reserved for future bindings. WASD, arrows, and the keypad move.
    pygame.K_UP: "north",
    pygame.K_w: "north",
    pygame.K_KP8: "north",
    pygame.K_DOWN: "south",
    pygame.K_s: "south",
    pygame.K_KP2: "south",
    pygame.K_LEFT: "west",
    pygame.K_a: "west",
    pygame.K_KP4: "west",
    pygame.K_RIGHT: "east",
    pygame.K_d: "east",
    pygame.K_KP6: "east",
    pygame.K_KP7: "northwest",
    pygame.K_KP9: "northeast",
    pygame.K_KP1: "southwest",
    pygame.K_KP3: "southeast",
}

# Hand-rolled auto-repeat for the text-deletion keys. pygame's own key repeat stays off
# (it double-stepped movement — see set_repeat() in GameUI.__init__), so Backspace/Delete
# repeat is driven off live key state instead: hold this long before it kicks in, then
# erase a character at this cadence while held.
_DELETE_REPEAT_DELAY_MS = 300
_DELETE_REPEAT_INTERVAL_MS = 40


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
        self.expedition_direction = expedition_direction_for_seed(
            None, int(time.time())
        )
        self.autoplay_run_theme = autoplay_run_theme_for_seed(None, int(time.time()))
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
        self.expedition_direction = expedition_direction_for_seed(
            self.ui.session.seed, int(time.time())
        )
        self.autoplay_run_theme = autoplay_run_theme_for_seed(
            self.ui.session.seed, int(time.time())
        )
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
        self.ui.input_cursor = len(command)
        self.ui.input_active = False
        # Autoplay drives the loop itself and needs the result synchronously, so it
        # uses the blocking path rather than the responsive worker-thread one.
        result = self.ui.execute_command_blocking(command)
        if result is not None:
            summary = result_summary(result)
            self.last_result = summary
            self.recent_results.append(summary)
            self.recent_results = self.recent_results[-8:]
            if (
                result.action == "read"
                and result.success
                and self.ui.book_popup is not None
            ):
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
            autoplay_run_theme=self.autoplay_run_theme,
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
        lines = [
            (
                f"AI Watch: {self.status}   heading {self.expedition_direction}   "
                f"theme {self.autoplay_run_theme}   F8 stop  F9 pause  F10 step",
                ACCENT,
            )
        ]
        if self.last_command:
            lines.append((f"> {self.last_command}", TEXT))
        if self.last_note:
            lines.append((self.last_note, MUTED))
        if self.last_error:
            lines.append((self.last_error, DANGER))
        return lines[:4]


class GameUI:
    def __init__(self, autoplay: bool = False, fullscreen: bool = False) -> None:
        self.window = GameWindow.create("Wild Magic", fullscreen=fullscreen)
        # GameWindow disables pygame key auto-repeat: this is a turn-based game, so one
        # physical key press must equal exactly one step. pygame repeat previously caused
        # double-steps when KEYUP was buffered behind a slow generation frame.
        self.ui_scale = self.window.ui_scale
        self.display = self.window.display
        self.screen = self.window.screen
        self.clock = self.window.clock
        self.fonts = GameFonts.create()
        self.tile_font = self.fonts.tile
        self.ui_font = self.fonts.ui
        self.small_font = self.fonts.small
        self.book_title_font = self.fonts.book_title
        self.book_font = self.fonts.book_body
        self.book_small_font = self.fonts.book_small
        self.session = GameSession(scenario="town")
        self.engine = self.session.engine
        self.input_text = ""
        self.input_cursor = 0
        self.input_active = True
        self.input_mode = "spell"
        self.mode_label_rects: list[tuple[pygame.Rect, str]] = []
        self._last_auto_talk_target_id: str | None = None
        self._auto_talk_mode = False
        self._last_trade_active: bool = False
        self.provider_label = self.session.provider_label
        self.log_line_rects: list[tuple[pygame.Rect, str]] = []
        self.log_selection_anchor: int | None = None
        self.log_selection_focus: int | None = None
        self.dragging_log_selection = False
        self.log_area = pygame.Rect(
            MAP_OFFSET_X + MAP_PIXEL_WIDTH + 20, 0, PANEL_WIDTH - 40, 0
        )
        self.spell_box_rect = pygame.Rect(
            MAP_OFFSET_X + MAP_PIXEL_WIDTH + 20,
            WINDOW_HEIGHT - 92,
            PANEL_WIDTH - 40,
            54,
        )
        self.input_line_rects: list[tuple[pygame.Rect, int, int, str, int]] = []

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
        self.llm_debug_mode = "embedded"
        self.llm_debug_window: LlmDebugWindow | None = None
        self._full_view_rect = pygame.Rect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        self._compact_view_rect = pygame.Rect(
            LLM_PANEL_WIDTH, 0, WINDOW_WIDTH - LLM_PANEL_WIDTH, WINDOW_HEIGHT
        )

        self.inspect_tile: tuple[int, int] | None = None
        self.inspect_button_rects: list[tuple[pygame.Rect, str]] = []
        self.curse_rects: list[tuple[pygame.Rect, str]] = []
        self.curse_tooltip_id: str | None = None
        self.book_popup: dict[str, Any] | None = None

        # F7 debug overlay: the background generation (canon prewarm) queue.
        self.queue_debug_active = False
        self.queue_debug_scroll = 0
        self._queue_debug_max_scroll = 0

        # Menu state
        self.menu_active = False
        self.menu_page: str = "main"  # "main" | "config" | "model" | "world"
        self.menu_cursor: int = 0
        self.menu_prev_page: str = "main"  # for back navigation
        self.menu_scene = MenuScene(self)

        self.log_scroll_offset = 0
        self.log_dragging_scrollbar = False
        self.log_drag_grab_dy = 0
        self.log_scrollbar_track_rect: pygame.Rect | None = None
        self.log_scrollbar_thumb_rect: pygame.Rect | None = None
        self._log_max_scroll = 0

        self.inventory_pane = 0
        self.inventory_left_cursor = 0
        self.inventory_right_cursor = 0
        self.menu_models: list[str] = []  # populated when model page opens
        self.autoplay = VisualAutoplayController(self, enabled=autoplay)

        # Urgent (player-issued) LLM commands run on a worker thread so the UI stays
        # responsive while one resolves — you can scroll the LLM panel, open the
        # inventory/journal, inspect tiles, etc. New game-actions a player triggers
        # while one is in flight are discarded, not queued.
        self._command_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wm-command"
        )
        self._command_future: concurrent.futures.Future | None = None
        self._command_label: str = ""
        # Coalesce turn-advancing actions to one per frame: a slow (generation) frame can
        # buffer several genuine KEYDOWNs that `pygame.event.get()` then delivers in one
        # batch, which would otherwise surge the player several steps at once. (Key
        # auto-repeat is disabled, so these are only ever real presses, never synthetic.)
        self._acted_this_frame = False
        # Held Backspace/Delete repeat (see _pump_text_delete_repeat). None when no
        # deletion key is held; otherwise the next tick at which a char should be erased.
        self._delete_repeat_at: int | None = None

        # Out-of-process character-portrait generator (SDXL in its own venv). Lazily
        # spawns a worker on first request; absent venv -> available() is False.
        self.portraits = PortraitClient()

        # Character creation runs as a modal scene at startup (see
        # scenes/character_creation_scene.py). The session above already holds a random
        # default player; finishing creation restamps that player in place
        # (GameEngine.restamp_player), so no world is regenerated. Autoplay skips it.
        self.creation_scene = CharacterCreationScene(self)
        # In-game character sheet, opened with `c` (or Ctrl+c). A modal scene like
        # creation; both are checked via _active_scene().
        self.character_view_scene = CharacterViewScene(self)
        self.standing_scene = StandingScene(self)
        if not autoplay:
            self.creation_scene.start()
        self._sync_window_view()

    def _config_value(self, spec: dict) -> str:
        return self.menu_scene._config_value(spec)

    def _open_menu(self) -> None:
        self.menu_active = True
        self.menu_page = "main"
        self.menu_cursor = 0

    def _close_menu(self) -> None:
        self.menu_active = False

    def finish_creation(self, profile, scenario: str = "town") -> None:
        """Apply the chosen profile and starting zone, then dismiss the creation scene.
        If the chosen zone matches the session's (Hollowmere by default), the player is
        restamped in place; otherwise the whole world is rebuilt in the new zone. profile
        =None ('Random') keeps a default rolled character."""
        if scenario != self.session.scenario:
            self._load_session(scenario, profile)
        elif profile is not None:
            self.engine.restamp_player(profile)
        self.creation_scene.active = False
        self.input_active = True

    def run(self) -> None:
        running = True
        try:
            while running:
                self._sync_window_view()
                self._acted_this_frame = False
                for raw_event in pygame.event.get():
                    if self._handle_llm_debug_window_event(raw_event):
                        continue
                    if not self.window.owns_event(raw_event):
                        continue
                    if raw_event.type == pygame.QUIT:
                        running = False
                        continue
                    if raw_event.type == getattr(
                        pygame, "WINDOWCLOSE", None
                    ) and self.window.owns_event(raw_event):
                        running = False
                        continue
                    if self.window.handle_window_event(raw_event):
                        continue
                    event = self._logical_mouse_event(raw_event)
                    if event.type in {
                        pygame.MOUSEBUTTONDOWN,
                        pygame.MOUSEBUTTONUP,
                        pygame.MOUSEMOTION,
                    }:
                        self.handle_mouse(event)
                    elif event.type == pygame.MOUSEWHEEL:
                        self.handle_mouse_wheel(event)
                    elif event.type == pygame.KEYDOWN:
                        self.handle_key(event)
                self._pump_text_delete_repeat()
                self._poll_command_future()
                scene = self._active_scene()
                if scene is not None:
                    scene.update()
                else:
                    self.autoplay.update()
                    # Advance background canon (book titles/pages, saturation) during
                    # idle frames so it doesn't stall between player actions — but not
                    # while a blocking command is mutating state on the worker thread.
                    if not self._awaiting_command():
                        self.session.pump_canon_prewarm()
                try:
                    self._sync_window_view()
                    self.draw()
                except RuntimeError:
                    # An urgent command is mutating engine state on its worker thread
                    # while we render; skip this frame rather than crash. Tolerated
                    # only while one is actually in flight — otherwise it's a real bug.
                    if self._command_future is None:
                        raise
                self.window.present()
                self._draw_llm_debug_window()
        finally:
            self.autoplay.close()
            self._command_executor.shutdown(wait=False, cancel_futures=True)
            self._close_llm_debug_window()
            self.portraits.close()
            self.session.close()
            self.window.close()

    def _logical_mouse_event(self, event: pygame.event.Event) -> pygame.event.Event:
        return self.window.logical_mouse_event(event)

    def _logical_mouse_pos(self) -> tuple[int, int]:
        return self.window.logical_mouse_pos()

    def _toggle_ui_scale(self) -> None:
        self.window.toggle_scale()
        self.ui_scale = self.window.ui_scale
        self.display = self.window.display

    def _toggle_fullscreen(self) -> None:
        self.window.toggle_fullscreen()
        self.display = self.window.display

    def _llm_debug_embedded(self) -> bool:
        return self.llm_debug_mode == "embedded"

    def _set_llm_debug_mode(self, mode: str) -> None:
        if mode not in {"embedded", "popout", "hidden"}:
            mode = "embedded"
        if mode == "embedded":
            self._close_llm_debug_window()
        elif mode == "popout" and self.llm_debug_window is None:
            try:
                self.llm_debug_window = LlmDebugWindow()
            except Exception as exc:
                self.engine.state.add_message(f"Could not open LLM debug window: {exc}")
                mode = "embedded"
        elif mode == "hidden":
            self._close_llm_debug_window()
        self.llm_debug_mode = mode
        self._sync_window_view(force=True)

    def _toggle_llm_debug_popout(self) -> None:
        if self.llm_debug_mode == "popout":
            self._set_llm_debug_mode("embedded")
        else:
            self._set_llm_debug_mode("popout")

    def _close_llm_debug_window(self) -> None:
        if self.llm_debug_window is not None:
            self.llm_debug_window.close()
            self.llm_debug_window = None

    def _sync_window_view(self, *, force: bool = False) -> None:
        base = (
            self._full_view_rect
            if self._llm_debug_embedded()
            else self._compact_view_rect
        )
        if force or self.window.base_view_rect != base:
            self.window.set_base_view_rect(base)
        active = base
        if not self._llm_debug_embedded() and (
            self._active_scene() is not None
            or self.menu_active
            or self.book_popup is not None
            or self.queue_debug_active
        ):
            active = self._full_view_rect
        self.window.set_active_view_rect(active)
        self.ui_scale = self.window.ui_scale
        self.display = self.window.display

    def _draw_llm_debug_window(self) -> None:
        if self.llm_debug_window is not None:
            self.llm_debug_window.draw(self)

    def _handle_llm_debug_window_event(self, event: pygame.event.Event) -> bool:
        window = self.llm_debug_window
        if window is None or not window.owns_event(event):
            return False
        if event.type == getattr(pygame, "WINDOWCLOSE", None):
            self._set_llm_debug_mode("hidden")
            return True
        if event.type in {
            getattr(pygame, "WINDOWRESIZED", None),
            getattr(pygame, "WINDOWSIZECHANGED", None),
        }:
            self._llm_lines_cache = None
            return True
        if event.type == pygame.KEYDOWN:
            self._handle_llm_debug_key(event)
            return True
        if event.type in {
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION,
        }:
            self._handle_llm_debug_mouse(event)
            return True
        if event.type == pygame.MOUSEWHEEL:
            self._handle_llm_debug_wheel(event)
            return True
        return True

    def _handle_llm_debug_key(self, event: pygame.event.Event) -> None:
        if event.key in (pygame.K_ESCAPE, pygame.K_F6):
            self._set_llm_debug_mode("hidden")
            return
        if event.mod & pygame.KMOD_CTRL:
            if event.key == pygame.K_c:
                self.copy_llm_selection()
            elif event.key == pygame.K_a and self._llm_lines_cache:
                self.llm_selection_anchor = 0
                self.llm_selection_focus = len(self._llm_lines_cache) - 1
            return
        if (
            event.key in (pygame.K_UP, pygame.K_DOWN)
            and self.llm_selection_anchor is not None
            and self.llm_selection_focus is not None
        ):
            self._move_llm_block_selection(-1 if event.key == pygame.K_UP else 1)

    def _handle_llm_debug_wheel(self, event: pygame.event.Event) -> None:
        self.llm_scroll_offset -= event.y * 3
        self.llm_scroll_offset = max(
            0, min(self.llm_scroll_offset, self._llm_max_scroll)
        )
        self.llm_autoscroll = (
            self._llm_max_scroll > 0 and self.llm_scroll_offset >= self._llm_max_scroll
        )

    def _handle_llm_debug_mouse(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if (
                self.llm_scrollbar_thumb_rect
                and self.llm_scrollbar_thumb_rect.collidepoint(event.pos)
            ):
                self.llm_dragging_scrollbar = True
                self.llm_drag_grab_dy = event.pos[1] - self.llm_scrollbar_thumb_rect.y
                return
            if (
                self.llm_scrollbar_track_rect
                and self.llm_scrollbar_track_rect.collidepoint(event.pos)
            ):
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
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.llm_dragging_scrollbar:
                self.llm_dragging_scrollbar = False
                return
            if self.dragging_llm_selection:
                index = self.llm_line_index_at(event.pos)
                if index is not None:
                    self.llm_selection_focus = index
                self.dragging_llm_selection = False

    def _awaiting_command(self) -> bool:
        """True while a player-issued LLM command is still resolving on the worker."""
        return self._command_future is not None

    def _poll_command_future(self) -> None:
        """Finalize an urgent command once its worker thread finishes. The state
        mutation happened on the worker; the post-processing (LLM debug refresh, book
        popups, provider label) runs here on the main thread."""
        future = self._command_future
        if future is None or not future.done():
            return
        self._command_future = None
        self._command_label = ""
        try:
            result = future.result()
        except Exception as exc:  # surface, don't crash the UI
            self.engine.state.add_message(f"(the spell unravels: {exc})")
            result = None
        self._after_command(result)

    def _active_scene(self):
        """The modal full-screen scene currently capturing input, or None."""
        for scene in (
            self.creation_scene,
            self.character_view_scene,
            self.standing_scene,
        ):
            if scene.active:
                return scene
        return None

    def _clamp_input_cursor(self) -> None:
        self.input_cursor = max(0, min(self.input_cursor, len(self.input_text)))

    def _backspace_input(self) -> None:
        """Erase the character before the spell/talk insertion cursor."""
        self._clamp_input_cursor()
        if self.input_cursor <= 0:
            return
        self.input_text = (
            self.input_text[: self.input_cursor - 1]
            + self.input_text[self.input_cursor :]
        )
        self.input_cursor -= 1

    def _delete_input(self) -> None:
        """Erase the character after the spell/talk insertion cursor."""
        self._clamp_input_cursor()
        if self.input_cursor >= len(self.input_text):
            return
        self.input_text = (
            self.input_text[: self.input_cursor]
            + self.input_text[self.input_cursor + 1 :]
        )

    def _insert_input_text(self, text: str) -> None:
        self._clamp_input_cursor()
        self.input_text = (
            self.input_text[: self.input_cursor]
            + text
            + self.input_text[self.input_cursor :]
        )
        self.input_cursor += len(text)

    def _focused_text_deleter(self, *, forward: bool = False):
        """The callable that erases one character from whatever text field currently has
        keyboard focus, or None when no field is accepting text. Shared by the
        Backspace/Delete KEYDOWN handlers and the held-key repeat pump so a single tap and
        a hold stay in sync. A modal scene owns input when active; otherwise the spell/talk
        box does, but only when no overlay (menu, book, queue, trade, game-over) is up."""
        scene = self._active_scene()
        if scene is not None:
            return getattr(scene, "backspace_focused", None)
        if (
            self.input_active
            and self.input_mode != "control"
            and not self.menu_active
            and self.book_popup is None
            and not self.queue_debug_active
            and self.engine.state.pending_trade is None
            and not self.engine.state.game_over
        ):
            return self._delete_input if forward else self._backspace_input
        return None

    def _pump_text_delete_repeat(self) -> None:
        """Auto-repeat Backspace/Delete while held. pygame's own key repeat is disabled
        (it double-stepped movement), so we drive just these two keys off live key state:
        the KEYDOWN erases one char, then after a short delay a steady cadence kicks in for
        as long as the key stays down and a text field keeps focus."""
        pressed = pygame.key.get_pressed()
        forward = bool(pressed[pygame.K_DELETE] and not pressed[pygame.K_BACKSPACE])
        held = pressed[pygame.K_BACKSPACE] or pressed[pygame.K_DELETE]
        deleter = self._focused_text_deleter(forward=forward) if held else None
        if deleter is None:
            self._delete_repeat_at = None
            return
        now = pygame.time.get_ticks()
        if self._delete_repeat_at is None:
            # First frame held: the KEYDOWN already erased one char, so just start the
            # delay clock before the repeat kicks in.
            self._delete_repeat_at = now + _DELETE_REPEAT_DELAY_MS
        elif now >= self._delete_repeat_at:
            deleter()
            self._delete_repeat_at = now + _DELETE_REPEAT_INTERVAL_MS

    def _cycle_input_mode(self) -> None:
        """Tab steps Wild Spell -> Controls -> Talk (when an NPC is in range) -> ..."""
        order = ["spell", "control"]
        if self.engine.find_talk_target() is not None:
            order.append("talk")
        current = self.input_mode if self.input_mode in order else "spell"
        self.input_mode = order[(order.index(current) + 1) % len(order)]
        self._auto_talk_mode = False
        # Controls mode frees the letter keys for hotkeys; the others take typed text.
        self.input_active = self.input_mode != "control"

    def _handle_control_key(self, event: pygame.event.Event) -> bool:
        """Letter/movement hotkeys (active in Controls mode or while Ctrl is held).
        Returns True if the key was consumed."""
        key = event.key
        move_dir = _MOVE_KEY_MAP.get(key)
        if move_dir is not None:
            zone_before = (self.engine.state.zone_x, self.engine.state.zone_y)
            self.execute_command(f"move {move_dir}")
            # Crossing a zone regenerates the region — the slowest move. Drop any presses
            # that queued up during that frame so impatient extra taps don't immediately
            # march you several tiles into the freshly revealed zone.
            if (self.engine.state.zone_x, self.engine.state.zone_y) != zone_before:
                pygame.event.clear((pygame.KEYDOWN, pygame.KEYUP))
            return True
        if key == pygame.K_KP5:
            self.execute_command("wait")
        elif key == pygame.K_GREATER or (
            key == pygame.K_PERIOD and event.mod & pygame.KMOD_SHIFT
        ):
            self.execute_command("descend")
        elif key == pygame.K_LESS or (
            key == pygame.K_COMMA and event.mod & pygame.KMOD_SHIFT
        ):
            self.execute_command("ascend")
        elif key == pygame.K_PERIOD:
            self.execute_command("wait")
        elif key == pygame.K_o:
            self.execute_command("open")
        elif key == pygame.K_g:
            self.execute_command("pickup")
        elif key == pygame.K_f:
            self.execute_command("spark")
        elif key == pygame.K_x:
            self.execute_command(self._investigate_command())
        elif key == pygame.K_e:
            self.execute_command("examine")
        elif key == pygame.K_r:
            self.execute_command("read")
        elif key == pygame.K_u:
            self.execute_command("free")
        elif key == pygame.K_z:
            self.execute_command("rest")
        elif key == pygame.K_b:
            self.execute_command("wares")
        elif key == pygame.K_p:
            self.execute_command("possess")
        elif key == pygame.K_l:
            self.execute_command("inspect")
        elif key == pygame.K_m:
            self.menu_active = True
            self.menu_page = "world"
            self.menu_cursor = 0
        elif key == pygame.K_t:
            self.standing_scene.start()
        elif key == pygame.K_n:
            self.execute_command("followers")
        elif key == pygame.K_h:
            self.execute_command("help")
        elif key == pygame.K_c:
            self.character_view_scene.start()
        elif key == pygame.K_q:
            self.menu_active = True
            self.menu_page = "quests"
            self.menu_cursor = 0
        elif key == pygame.K_j:
            self.menu_active = True
            self.menu_page = "journal"
            self.menu_cursor = 0
        elif key == pygame.K_i:
            self.menu_active = True
            self.menu_page = "inventory"
            self.menu_cursor = 0
            self.inventory_pane = 0
            self.inventory_left_cursor = 0
            self.inventory_right_cursor = 0
        else:
            return False
        return True

    def handle_key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_F11 or (
            event.key in (pygame.K_RETURN, pygame.K_KP_ENTER)
            and event.mod & pygame.KMOD_ALT
        ):
            self._toggle_fullscreen()
            return
        if event.key == pygame.K_F6:
            self._toggle_llm_debug_popout()
            return
        scene = self._active_scene()
        if scene is not None:
            scene.handle_key(event)
            return
        if event.key == pygame.K_F7:
            self.queue_debug_active = not self.queue_debug_active
            self.queue_debug_scroll = 0
            return
        if self.queue_debug_active:
            # While the generation-queue overlay is up it owns the keyboard: scroll
            # it and close it, but swallow everything else so the world stays put.
            if event.key == pygame.K_ESCAPE:
                self.queue_debug_active = False
            elif event.key in (pygame.K_UP, pygame.K_PAGEUP, pygame.K_k):
                step = 8 if event.key == pygame.K_PAGEUP else 1
                self.queue_debug_scroll = max(0, self.queue_debug_scroll - step)
            elif event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN, pygame.K_j):
                step = 8 if event.key == pygame.K_PAGEDOWN else 1
                self.queue_debug_scroll = min(
                    self._queue_debug_max_scroll, self.queue_debug_scroll + step
                )
            elif event.key == pygame.K_HOME:
                self.queue_debug_scroll = 0
            elif event.key == pygame.K_END:
                self.queue_debug_scroll = self._queue_debug_max_scroll
            return
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
            # Ctrl acts as a temporary Controls modifier so letter hotkeys (incl. Ctrl+c
            # for the character sheet) work without leaving Wild Spell mode. Copy still
            # works, but only when text is actually selected; Ctrl+A select-all stays.
            hovering_llm = self.llm_content_rect.collidepoint(self._logical_mouse_pos())
            if event.key == pygame.K_c:
                if (
                    self.llm_selection_anchor is not None
                    and self.llm_selection_focus is not None
                ):
                    self.copy_llm_selection()
                    return
                if (
                    self.log_selection_anchor is not None
                    and self.log_selection_focus is not None
                ):
                    self.copy_log_selection()
                    return
                # No selection: fall through so Ctrl+c opens the character sheet.
            elif event.key == pygame.K_a:
                if hovering_llm and self._llm_lines_cache:
                    self.llm_selection_anchor = 0
                    self.llm_selection_focus = len(self._llm_lines_cache) - 1
                    return
                if self.log_line_rects:
                    self.log_selection_anchor = 0
                    self.log_selection_focus = len(self.log_line_rects) - 1
                    return
            if self._handle_control_key(event):
                return
            return

        if self.book_popup is not None:
            if event.key == pygame.K_ESCAPE:
                self.book_popup = None
            elif event.key in (pygame.K_LEFT, pygame.K_PAGEUP, pygame.K_a, pygame.K_UP):
                self.book_popup["page"] = max(
                    0, int(self.book_popup.get("page", 0)) - 1
                )
            elif event.key in (
                pygame.K_RIGHT,
                pygame.K_PAGEDOWN,
                pygame.K_d,
                pygame.K_DOWN,
                pygame.K_RETURN,
                pygame.K_KP_ENTER,
                pygame.K_SPACE,
            ):
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
            if self.engine.has_target():
                self.execute_command("untarget")
                return
            if self.input_mode == "control":
                self.input_mode = "spell"
                self.input_active = True
                return
            if self.input_text:
                self.input_text = ""
                self.input_cursor = 0
                self.input_active = True
                return
            self._open_menu()
            return

        # Tab cycles input modes (Wild Spell / Controls / Talk) in any mode.
        if event.key == pygame.K_TAB:
            self._cycle_input_mode()
            return

        if self.input_active and self.input_mode != "control":
            if event.key == pygame.K_RETURN:
                self.submit_input()
                return
            if event.key == pygame.K_BACKSPACE:
                self._backspace_input()
                return
            if event.key == pygame.K_DELETE:
                self._delete_input()
                return
            if event.unicode and event.unicode.isprintable():
                self._insert_input_text(event.unicode)
                return

        if self.input_mode != "control" and event.key in {
            pygame.K_SLASH,
            pygame.K_RETURN,
        }:
            self.input_active = True
            return
        self._handle_control_key(event)
        self.provider_label = self.session.provider_label

    def _toggle_target(self, tx: int, ty: int) -> None:
        """Click a map square to mark it as the spell target; click the marked square
        again to clear. Both are free actions routed through the command path so they
        record and replay like any other command."""
        state = self.engine.state
        if (state.target_x, state.target_y) == (tx, ty):
            self.execute_command("untarget")
        else:
            self.execute_command(f"target {tx} {ty}")

    def _input_cursor_at_pos(self, pos: tuple[int, int]) -> int:
        if not self.input_line_rects:
            return len(self.input_text)
        x, y = pos
        chosen = self.input_line_rects[-1]
        for entry in self.input_line_rects:
            rect, _start, _end, _text, _prefix_width = entry
            expanded = rect.inflate(0, 6)
            if expanded.collidepoint(x, y):
                chosen = entry
                break
        else:
            if y < self.input_line_rects[0][0].top:
                chosen = self.input_line_rects[0]
            elif y > self.input_line_rects[-1][0].bottom:
                chosen = self.input_line_rects[-1]
        rect, start, end, text, prefix_width = chosen
        if end <= start:
            return start
        text_x = rect.x + prefix_width
        if x <= text_x:
            return start
        if x >= text_x + self.ui_font.size(text)[0]:
            return end
        for offset in range(len(text) + 1):
            left = self.ui_font.size(text[:offset])[0]
            right = self.ui_font.size(text[: offset + 1])[0]
            midpoint = text_x + (left + right) // 2
            if x < midpoint:
                return min(end, start + offset)
        return end

    def _move_input_cursor_to_mouse(self, pos: tuple[int, int]) -> None:
        self.input_cursor = self._input_cursor_at_pos(pos)
        self._clamp_input_cursor()

    def handle_mouse(self, event: pygame.event.Event) -> None:
        scene = self._active_scene()
        if scene is not None:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                scene.handle_mouse(event.pos)
            return
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
            for rect, curse_id in self.curse_rects:
                if rect.collidepoint(event.pos):
                    self.curse_tooltip_id = (
                        None if self.curse_tooltip_id == curse_id else curse_id
                    )
                    return
            if (
                MAP_OFFSET_X <= mx < MAP_OFFSET_X + MAP_PIXEL_WIDTH
                and 0 <= my < MAP_PIXEL_HEIGHT
            ):
                tx = (mx - MAP_OFFSET_X) // TILE_SIZE
                ty = my // TILE_SIZE
                self.inspect_tile = None if self.inspect_tile == (tx, ty) else (tx, ty)
            else:
                self.inspect_tile = None
                self.curse_tooltip_id = None
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.inspect_tile is not None:
                for rect, command in self.inspect_button_rects:
                    if rect.collidepoint(event.pos):
                        self.execute_command(command)
                        return
            self.inspect_tile = None
            mx, my = event.pos
            if (
                MAP_OFFSET_X <= mx < MAP_OFFSET_X + MAP_PIXEL_WIDTH
                and 0 <= my < MAP_PIXEL_HEIGHT
            ):
                tx = (mx - MAP_OFFSET_X) // TILE_SIZE
                ty = my // TILE_SIZE
                self._toggle_target(tx, ty)
                return
            if (
                self.log_scrollbar_thumb_rect
                and self.log_scrollbar_thumb_rect.collidepoint(event.pos)
            ):
                self.log_dragging_scrollbar = True
                self.log_drag_grab_dy = event.pos[1] - self.log_scrollbar_thumb_rect.y
                return
            if (
                self.log_scrollbar_track_rect
                and self.log_scrollbar_track_rect.collidepoint(event.pos)
            ):
                thumb = self.log_scrollbar_thumb_rect
                self.log_drag_grab_dy = thumb.height // 2 if thumb else 0
                track = self.log_scrollbar_track_rect
                thumb_height = thumb.height if thumb else 0
                usable = max(1, track.height - thumb_height)
                fraction = (event.pos[1] - self.log_drag_grab_dy - track.y) / usable
                self._log_scroll_to_fraction(fraction)
                self.log_dragging_scrollbar = True
                return
            if (
                self.llm_scrollbar_thumb_rect
                and self.llm_scrollbar_thumb_rect.collidepoint(event.pos)
            ):
                self.llm_dragging_scrollbar = True
                self.llm_drag_grab_dy = event.pos[1] - self.llm_scrollbar_thumb_rect.y
                return
            if (
                self.llm_scrollbar_track_rect
                and self.llm_scrollbar_track_rect.collidepoint(event.pos)
            ):
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
                    self._auto_talk_mode = False
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
                if self.input_mode not in {"control", "confirm_trade"}:
                    self._move_input_cursor_to_mouse(event.pos)
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
        return self.menu_scene.items()

    def _handle_menu_key(self, event: pygame.event.Event) -> None:
        self.menu_scene.handle_key(event)

    def _menu_cycle(self, items: list[dict], direction: int) -> None:
        self.menu_scene.cycle(items, direction)

    def _menu_select(self, items: list[dict]) -> None:
        self.menu_scene.select(items)

    # Verbs whose resolution makes a blocking LLM call. These run on a worker thread
    # so the UI stays responsive; everything else (move, wait, equip, ...) is instant
    # and runs inline.
    _BLOCKING_VERBS = frozenset(
        {
            "cast",
            "wild",
            "talk",
            "speak",
            "say",
            "examine",
            "study",
            "observe",
            "investigate",
            "search",
            "read",
            "peruse",
        }
    )

    def execute_command(self, command: str) -> ActionResult | None:
        """Player-issued command entry point. Discards the command if an urgent LLM
        command is already resolving (we don't queue actions). Blocking verbs run on a
        worker thread and return None now (finalized later in _poll_command_future);
        instant commands run inline and return their result."""
        if self._awaiting_command():
            return None
        self.log_scroll_offset = 0
        verb = command.strip().split(maxsplit=1)[0].lower() if command.strip() else ""
        if verb in self._BLOCKING_VERBS:
            self._command_label = command.strip()
            self._command_future = self._command_executor.submit(
                self.session.execute_command, command
            )
            return None
        # Drop a second turn-advancing action buffered into the same frame (e.g. rapid
        # presses a slow frame delivered in one event batch). Free actions before the
        # first turn-consuming one still run; the flag is only raised once a turn is spent.
        if self._acted_this_frame:
            return None
        result = self.execute_command_blocking(command)
        if result is not None and result.consumed_turn:
            self._acted_this_frame = True
        return result

    def execute_command_blocking(self, command: str) -> ActionResult:
        """Run a command synchronously (blocking on any LLM call) and post-process.
        Used for instant commands and by autoplay, which drives the loop itself."""
        self.log_scroll_offset = 0
        result = self.session.execute_command(command)
        self._after_command(result)
        return result

    def _after_command(self, result: ActionResult | None) -> None:
        """Main-thread post-processing shared by inline and worker-resolved commands."""
        self._refresh_llm_debug_entries()
        self._llm_lines_cache = None
        self.llm_autoscroll = True
        if result is None:
            return
        if result.wild_magic:
            self.provider_label = str(
                result.wild_magic.get("provider") or self.session.provider_label
            )
        if result.action == "read" and result.success and result.canon_materialization:
            record = result.canon_materialization.get("record")
            if isinstance(record, dict) and record.get("kind") == "book":
                llm_choices = (
                    record.get("llm_choices")
                    if isinstance(record.get("llm_choices"), dict)
                    else {}
                )
                self.book_popup = {
                    "title": str(record.get("title") or "An Untitled Volume"),
                    "author": str(llm_choices.get("author") or ""),
                    "text": str(record.get("text") or ""),
                    "page": 0,
                    "page_count": 1,
                }

    # ------------------------------------------------------------------

    def restart_run(self) -> None:
        self._load_session("town", None)

    def _load_session(self, scenario: str, profile) -> None:
        """Tear down the current session and start a fresh one in the chosen starting
        zone (scenario), stamping the chosen character profile. Used both by restart
        and by character creation when the player picks a non-default start."""
        self.session.close()
        self.session = GameSession(
            scenario=scenario, character=profile, seed=self.session.seed
        )
        self.engine = self.session.engine
        self.input_text = ""
        self.input_cursor = 0
        self.log_scroll_offset = 0
        self.input_active = True
        self.input_mode = "spell"
        self.mode_label_rects = []
        self._last_auto_talk_target_id = None
        self._auto_talk_mode = False
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
        self.standing_scene.active = False
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
        end = min(
            len(self.log_line_rects) - 1,
            max(self.log_selection_anchor, self.log_selection_focus),
        )
        if start > end:
            return []
        return [line for _rect, line in self.log_line_rects[start : end + 1]]

    def llm_line_index_at(self, pos: tuple[int, int]) -> int | None:
        return rendering.llm_line_index_at(self, pos)

    def copy_llm_selection(self) -> None:
        lines = self.selected_llm_lines()
        if not lines:
            return
        text = "\n".join(lines)
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
            self.engine.state.add_message(
                f"Copied {len(lines)} line(s) from the LLM debug view."
            )
        except pygame.error:
            self.engine.state.add_message("Could not access the system clipboard.")

    def selected_llm_lines(self) -> list[str]:
        return rendering.selected_llm_lines(self)

    def _llm_block_index_for_line(self, line_index: int) -> int | None:
        return rendering.llm_block_index_for_line(self, line_index)

    def _select_llm_block(self, block_index: int) -> bool:
        return rendering.select_llm_block(self, block_index)

    def _move_llm_block_selection(self, direction: int) -> bool:
        return rendering.move_llm_block_selection(self, direction)

    def _recent_llm_call_indices(self) -> list[int]:
        return rendering.recent_llm_call_indices(self)

    def _llm_call_kind(self, entry: dict[str, Any]) -> str:
        return rendering.llm_call_kind(entry)

    def _fit_text(self, text: str, font: pygame.font.Font, max_width: int) -> str:
        return rendering.fit_llm_text(text, font, max_width)

    def _activate_llm_call_button(self, entry_index: int) -> bool:
        return rendering.activate_llm_call_button(self, entry_index)

    def _select_llm_entry_part(self, entry_index: int, part: str) -> bool:
        return rendering.select_llm_entry_part(self, entry_index, part)

    def _investigate_command(self) -> str:
        """The x key: sweep the room, unless a found clue's anchor is in reach —
        standing on or beside the clued thing, x searches it instead. The verb
        itself stays a plain CLI command; this only chooses which one to send."""
        engine = self.engine
        player = engine.state.player
        room = engine.room_profile_at(player.x, player.y)
        if room is not None:
            slot = next(
                (s for s in room.secret_slots if s.get("status") == "clued"), None
            )
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
        if self._awaiting_command():
            return  # discard the submit while an urgent command resolves; keep the text
        self.input_text = ""
        self.input_cursor = 0
        self.input_active = True
        self.log_scroll_offset = 0
        command = f"talk {text}" if self.input_mode == "talk" else f"cast {text}"
        self.execute_command(command)

    def draw(self) -> None:
        rendering.draw_game_frame(self)

    def draw_resolving_indicator(self) -> None:
        """A small banner over the map while an urgent command resolves, so the player
        knows the wild magic is listening (and that new actions are being ignored)."""
        rendering.draw_resolving_indicator(
            self.screen, self.small_font, self._command_label
        )

    def draw_autoplay_overlay(self) -> None:
        rendering.draw_autoplay_overlay(
            self.screen, self.small_font, self.autoplay.overlay_lines()
        )

    def draw_inspect_tooltip(self) -> None:
        rendering.draw_inspect_tooltip(self)

    def draw_book_popup(self) -> None:
        """A parchment page for reading books, modal over everything else.
        Long texts paginate; arrows/space/clicks turn pages."""
        rendering.draw_book_popup(self)

    def draw_queue_debug(self) -> None:
        """F7 overlay: the background generation (canon prewarm) queue. Shows what the
        single worker is doing now and queued next, then the whole zone's books with
        their title/pages state in proximity order. Rebuilt live each frame, so it
        updates as the queue drains; the book list scrolls."""
        rendering.draw_queue_debug(self)

    def draw_menu(self) -> None:
        self.menu_scene.draw()

    def draw_map(self) -> None:
        rendering.draw_map(self.screen, self.tile_font, self.engine)

    def draw_panel(self) -> None:
        rendering.draw_hud_panel(self)

    def draw_curse_tooltip(self) -> None:
        rendering.draw_curse_tooltip(self)

    def _visible_hostiles_to_player(self) -> list[Entity]:
        engine = self.engine
        player = engine.state.player
        return [
            entity
            for entity in engine.state.entities.values()
            if entity.id != player.id
            and entity.kind in {"actor", "npc"}
            and entity.hp > 0
            and engine.is_visible(entity.x, entity.y)
            and engine.is_hostile_to(entity, player)
        ]

    def _auto_talk_target(self) -> Entity | None:
        """The conservative UI default: talk only when the scene is calm."""
        engine = self.engine
        player = engine.state.player
        if self._visible_hostiles_to_player():
            return None
        adjacent = [
            entity
            for entity in engine.state.entities.values()
            if entity.kind == "npc"
            and entity.faction == "neutral"
            and engine.can_converse_with(entity)
            and not engine.is_hostile_to(entity, player)
            and max(abs(entity.x - player.x), abs(entity.y - player.y)) <= 1
        ]
        return min(adjacent, key=lambda entity: entity.id) if adjacent else None

    def draw_llm_panel(self) -> None:
        if self._llm_debug_embedded():
            rendering.draw_llm_panel(self)

    def draw_llm_call_buttons(self, x: int, y: int, width: int) -> int:
        return rendering.draw_llm_call_buttons(self, x, y, width)

    def draw_llm_content(self, x: int, y: int, width: int, height: int) -> None:
        rendering.draw_llm_content(self, x, y, width, height)

    def draw_llm_scrollbar(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        total_lines: int,
        visible_lines: int,
    ) -> None:
        rendering.draw_llm_scrollbar(
            self, x, y, width, height, total_lines, visible_lines
        )

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

    def _refresh_llm_debug_entries(self) -> None:
        rendering.refresh_llm_debug_entries(self)

    def _parse_audit_timestamp(self, value: Any) -> datetime | None:
        return rendering.parse_audit_timestamp(value)

    def _audit_record_to_debug_entry(
        self, filename: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        return rendering.audit_record_to_debug_entry(filename, record)

    def _format_audit_prompt(self, record: dict[str, Any]) -> str:
        return rendering.format_audit_prompt(record)

    def _format_audit_response(self, record: dict[str, Any]) -> str:
        return rendering.format_audit_response(record)

    def _build_llm_lines(
        self, wrap_chars: int
    ) -> list[tuple[str, tuple[int, int, int]]]:
        return rendering.build_llm_lines(self, wrap_chars)

    def handle_mouse_wheel(self, event: pygame.event.Event) -> None:
        scene = self._active_scene()
        if scene is not None:
            handler = getattr(scene, "handle_mouse_wheel", None)
            if handler is not None:
                handler(event)
            return
        if self.queue_debug_active:
            self.queue_debug_scroll = max(
                0,
                min(self._queue_debug_max_scroll, self.queue_debug_scroll - event.y),
            )
            return
        pos = self._logical_mouse_pos()
        if self.llm_content_rect.collidepoint(pos):
            self.llm_scroll_offset -= event.y * 3
            self.llm_scroll_offset = max(
                0, min(self.llm_scroll_offset, self._llm_max_scroll)
            )
            self.llm_autoscroll = (
                self._llm_max_scroll > 0
                and self.llm_scroll_offset >= self._llm_max_scroll
            )
        elif self.log_area.collidepoint(pos):
            self.log_scroll_offset += event.y * 3
            self.log_scroll_offset = max(
                0, min(self.log_scroll_offset, self._log_max_scroll)
            )

    def _llm_scroll_to_fraction(self, fraction: float) -> None:
        rendering.llm_scroll_to_fraction(self, fraction)

    def _llm_scrollbar_fraction_at(self, mouse_y: int) -> float | None:
        return rendering.llm_scrollbar_fraction_at(self, mouse_y)

    def draw_text(
        self,
        text: str,
        x: int,
        y: int,
        font: pygame.font.Font,
        color: tuple[int, int, int],
    ) -> int:
        return rendering.draw_text(self.screen, text, x, y, font, color)


def run_game(autoplay: bool = False, fullscreen: bool = False) -> None:
    GameUI(autoplay=autoplay, fullscreen=fullscreen).run()
