from __future__ import annotations

import pygame

from ..actions import describe_world, inventory_item_summary
from ..config import DEFAULT_MODEL, get_config_value, set_config_value
from ..item_palettes import palette_colors
from ..llm_client import fetch_ollama_models
from ..rendering.layout import WINDOW_HEIGHT, WINDOW_WIDTH
from ..rendering.theme import ACCENT, GOLD, MUTED, PANEL_EDGE, TEXT, wrap_text


# ---------------------------------------------------------------------------
# Config menu spec — each entry drives the menu display and .env update
# ---------------------------------------------------------------------------
_CONFIG_SPEC: list[dict] = [
    {
        "key": "WILDMAGIC_MODEL",
        "label": "Model",
        "type": "model",  # special: opens model-list submenu
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
        "values": [
            "0.1",
            "0.2",
            "0.25",
            "0.3",
            "0.4",
            "0.5",
            "0.7",
            "0.9",
            "1.0",
            "1.2",
        ],
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


class MenuScene:
    def __init__(self, host) -> None:
        object.__setattr__(self, "host", host)

    def __getattr__(self, name: str):
        return getattr(self.host, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "host":
            object.__setattr__(self, name, value)
        else:
            setattr(self.host, name, value)

    def _config_value(self, spec: dict) -> str:
        """Current display value for a config spec entry."""
        raw = get_config_value(spec["key"], spec["default"]) or spec["default"]
        if spec["type"] == "toggle":
            return spec["display"].get(raw, raw)
        return raw

    def items(self) -> list[dict]:
        if self.menu_page == "main":
            llm_modes = {
                "embedded": "Embedded",
                "popout": "Popout",
                "hidden": "Hidden",
            }
            return [
                {"label": "Resume", "action": "resume"},
                {
                    "label": f"UI Scale: {self.ui_scale}x",
                    "action": "toggle_ui_scale",
                },
                {
                    "label": f"Fullscreen: {'ON' if self.window.fullscreen else 'OFF'}",
                    "action": "toggle_fullscreen",
                },
                {
                    "label": f"LLM Debug: {llm_modes.get(self.llm_debug_mode, 'Embedded')}",
                    "action": "cycle_llm_debug",
                },
                {"label": "Configuration", "action": "config"},
                {"label": "Quit", "action": "quit"},
            ]
        if self.menu_page == "config":
            items = []
            for spec in _CONFIG_SPEC:
                val = self._config_value(spec)
                items.append(
                    {
                        "label": f"{spec['label']:<22} {val}",
                        "action": "config_item",
                        "spec": spec,
                    }
                )
            items.append({"label": "Back", "action": "back"})
            return items
        if self.menu_page == "model":
            items = [
                {"label": m, "action": "set_model", "model": m}
                for m in self.menu_models
            ]
            if not items:
                items = [{"label": "(no models found)", "action": "noop"}]
            items.append({"label": "Back", "action": "back"})
            return items
        return []

    def handle_key(self, event: pygame.event.Event) -> None:
        if self.menu_page == "world":
            if event.key in (pygame.K_ESCAPE, pygame.K_m):
                self._close_menu()
            return

        if self.menu_page == "inventory":
            equipment_view = self.session.equipment_inventory_view()
            inventory_items = [item["name"] for item in equipment_view["items"]]
            slots = [slot["slot"] for slot in equipment_view["slots"]]

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
                    self.inventory_left_cursor = (self.inventory_left_cursor - 1) % len(
                        slots
                    )
                else:
                    if inventory_items:
                        self.inventory_right_cursor = (
                            self.inventory_right_cursor - 1
                        ) % len(inventory_items)
                return
            elif event.key in (pygame.K_DOWN, pygame.K_j, pygame.K_KP2, pygame.K_s):
                if self.inventory_pane == 0:
                    self.inventory_left_cursor = (self.inventory_left_cursor + 1) % len(
                        slots
                    )
                else:
                    if inventory_items:
                        self.inventory_right_cursor = (
                            self.inventory_right_cursor + 1
                        ) % len(inventory_items)
                return
            elif event.key == pygame.K_f:
                # Toggle the selected equipped slot as the spell focus (left pane only).
                # Routes through the same focus/unfocus commands the CLI uses.
                if self.inventory_pane == 0:
                    slot_view = equipment_view["slots"][self.inventory_left_cursor]
                    slot = slot_view["slot"]
                    if slot_view["occupied"]:
                        if slot_view["focused"]:
                            self.execute_command(f"unfocus {slot}")
                        else:
                            self.execute_command(f"focus {slot}")
                return
            elif event.key == pygame.K_p:
                # Toggle whether the selected carried stack can be spent by wild magic.
                if self.inventory_pane == 1 and inventory_items:
                    item_view = equipment_view["items"][self.inventory_right_cursor]
                    command = "unprotect" if item_view.get("protected") else "protect"
                    self.execute_command(f"{command} {item_view['name']}")
                return
            elif event.key == pygame.K_a:
                # Appraise/identify the selected carried item through the shared command path.
                if self.inventory_pane == 1 and inventory_items:
                    item_view = equipment_view["items"][self.inventory_right_cursor]
                    self.execute_command(f"identify {item_view['name']}")
                return
            elif event.key in (
                pygame.K_RETURN,
                pygame.K_KP_ENTER,
                pygame.K_e,
                pygame.K_u,
            ):
                if self.inventory_pane == 0:
                    slot_view = equipment_view["slots"][self.inventory_left_cursor]
                    if slot_view["occupied"]:
                        self.execute_command(f"unequip {slot_view['slot']}")
                else:
                    if inventory_items:
                        item_name = inventory_items[self.inventory_right_cursor]
                        if event.key == pygame.K_u:
                            self.execute_command(f"unequip {item_name}")
                        else:
                            self.execute_command(f"equip {item_name}")
                new_inventory_items = self.session.equipment_inventory_view()["items"]
                if new_inventory_items:
                    self.inventory_right_cursor = min(
                        self.inventory_right_cursor, len(new_inventory_items) - 1
                    )
                else:
                    self.inventory_right_cursor = 0
                return

        if self.menu_page in {"quests", "journal"}:
            n = (
                len(self.engine.quest_log_entries())
                if self.menu_page == "quests"
                else len(self.engine.journal_entries())
            )
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

        items = self.items()
        n = len(items)
        if event.key in (pygame.K_UP, pygame.K_k, pygame.K_KP8):
            self.menu_cursor = (self.menu_cursor - 1) % n
        elif event.key in (pygame.K_DOWN, pygame.K_j, pygame.K_KP2):
            self.menu_cursor = (self.menu_cursor + 1) % n
        elif event.key in (pygame.K_LEFT, pygame.K_h, pygame.K_KP4):
            self.cycle(items, -1)
        elif event.key in (pygame.K_RIGHT, pygame.K_l, pygame.K_KP6):
            self.cycle(items, +1)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.select(items)
        elif event.key == pygame.K_ESCAPE:
            self._close_menu()

    def cycle(self, items: list[dict], direction: int) -> None:
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

    def select(self, items: list[dict]) -> None:
        if self.menu_cursor >= len(items):
            return
        item = items[self.menu_cursor]
        action = item["action"]
        if action == "resume":
            self._close_menu()
        elif action == "toggle_ui_scale":
            self._toggle_ui_scale()
        elif action == "toggle_fullscreen":
            self._toggle_fullscreen()
        elif action == "cycle_llm_debug":
            order = ["embedded", "popout", "hidden"]
            try:
                index = order.index(self.llm_debug_mode)
            except ValueError:
                index = 0
            self._set_llm_debug_mode(order[(index + 1) % len(order)])
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
                self.cycle(items, +1)
            elif spec["type"] == "cycle":
                self.cycle(items, +1)
            elif spec["type"] == "model":
                self.menu_prev_page = "config"
                self.menu_page = "model"
                self.menu_cursor = 0
                self.menu_models = fetch_ollama_models()
                # pre-select current model
                current = (
                    get_config_value("WILDMAGIC_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
                )
                try:
                    self.menu_cursor = self.menu_models.index(current)
                except ValueError:
                    self.menu_cursor = 0
        elif action == "set_model":
            set_config_value("WILDMAGIC_MODEL", item["model"])
            self.menu_page = "config"
            self.menu_cursor = 0

    def draw(self) -> None:
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self.screen.blit(overlay, (0, 0))

        if self.menu_page == "world":
            box_w = 720
            box_h = 500
            bx = (WINDOW_WIDTH - box_w) // 2
            by = (WINDOW_HEIGHT - box_h) // 2
            padding = 24

            pygame.draw.rect(
                self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6
            )
            pygame.draw.rect(
                self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6
            )

            title_surf = self.ui_font.render("WORLD ATLAS", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))
            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding, by + padding + 22),
                (bx + box_w - padding, by + padding + 22),
                1,
            )

            y = by + padding + 38
            bottom = by + box_h - padding - 18
            for line in describe_world(self.engine):
                if y >= bottom:
                    break
                wrapped = (
                    [line]
                    if not line or (line.startswith("  ") and len(line) <= 34)
                    else wrap_text(line, 78)
                )
                for segment in wrapped:
                    if y >= bottom:
                        break
                    color = GOLD if segment == "The Known World" else TEXT
                    surf = self.small_font.render(segment, True, color)
                    self.screen.blit(surf, (bx + padding, y))
                    y += 16

            hint_surf = self.small_font.render("M/Esc Close", True, MUTED)
            self.screen.blit(hint_surf, (bx + padding, by + box_h - 22))
            return

        if self.menu_page == "inventory":
            equipment_view = self.session.equipment_inventory_view()
            box_w = 700
            box_h = 450
            bx = (WINDOW_WIDTH - box_w) // 2
            by = (WINDOW_HEIGHT - box_h) // 2
            padding = 24

            pygame.draw.rect(
                self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6
            )
            pygame.draw.rect(
                self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6
            )

            title_surf = self.ui_font.render("EQUIPMENT & INVENTORY", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))

            gold_amount = equipment_view["gold"]
            gold_surf = self.ui_font.render(f"Gold: {gold_amount}", True, GOLD)
            self.screen.blit(
                gold_surf, (bx + box_w - padding - gold_surf.get_width(), by + padding)
            )

            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding, by + padding + 22),
                (bx + box_w - padding, by + padding + 22),
                1,
            )

            left_w = 300
            pane_y = by + padding + 36
            list_h = box_h - (padding * 2 + 36 + 24)
            row_h = 28

            left_header = self.ui_font.render("Equipped Gear", True, MUTED)
            self.screen.blit(left_header, (bx + padding, pane_y))
            pane_y_list = pane_y + 24

            for idx, slot_view in enumerate(equipment_view["slots"]):
                slot = slot_view["slot"]
                qy = pane_y_list + idx * row_h
                is_selected = (self.inventory_pane == 0) and (
                    idx == self.inventory_left_cursor
                )

                if is_selected:
                    pygame.draw.rect(
                        self.screen,
                        (50, 55, 70),
                        (bx + padding - 4, qy - 2, left_w, row_h - 4),
                        border_radius=4,
                    )

                item = slot_view["item"]
                item_display = f"{item}" if item else "(empty)"
                focus_mark = " *focus*" if slot_view["focused"] else ""
                display_text = f"{slot:<7} : {item_display}{focus_mark}"

                if item:
                    color = GOLD if is_selected else TEXT
                else:
                    color = ACCENT if is_selected else MUTED

                q_surf = self.ui_font.render(display_text, True, color)
                self.screen.blit(q_surf, (bx + padding, qy))

            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding + left_w + 10, pane_y),
                (bx + padding + left_w + 10, pane_y + list_h),
                1,
            )

            rx = bx + padding + left_w + 24
            right_header = self.ui_font.render("Carried Items", True, MUTED)
            self.screen.blit(right_header, (rx, pane_y))
            pane_y_list = pane_y + 24

            inventory_items = equipment_view["items"]

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
                    item_view = inventory_items[item_idx]
                    qy = pane_y_list + idx * row_h
                    is_selected = (self.inventory_pane == 1) and (
                        item_idx == self.inventory_right_cursor
                    )

                    if is_selected:
                        pygame.draw.rect(
                            self.screen,
                            (50, 55, 70),
                            (
                                rx - 4,
                                qy - 2,
                                box_w - padding * 2 - left_w - 30,
                                row_h - 4,
                            ),
                            border_radius=4,
                        )

                    display_text = inventory_item_summary(
                        item_view,
                        include_palette_label=False,
                    )
                    if is_selected:
                        color = GOLD
                    elif item_view["equippable"]:
                        color = (130, 220, 150)
                    else:
                        color = TEXT

                    self._draw_inventory_item_text(
                        display_text,
                        item_view,
                        rx,
                        qy,
                        color,
                    )

            hint_surf = self.small_font.render(
                "◄► Switch Pane  •  ▲▼ Select  •  Enter/E Equip  •  U Unequip  •  F Focus  •  P Protect  •  A Identify  •  Esc Close",
                True,
                MUTED,
            )
            self.screen.blit(hint_surf, (bx + padding, by + box_h - 22))
            return

        if self.menu_page == "quests":
            # Draw Quest Log Layout
            box_w = 640
            box_h = 400
            bx = (WINDOW_WIDTH - box_w) // 2
            by = (WINDOW_HEIGHT - box_h) // 2
            padding = 24

            pygame.draw.rect(
                self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6
            )
            pygame.draw.rect(
                self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6
            )

            # Title
            title_surf = self.ui_font.render("QUEST LOG", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))
            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding, by + padding + 22),
                (bx + box_w - padding, by + padding + 22),
                1,
            )

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
                        pygame.draw.rect(
                            self.screen,
                            (50, 55, 70),
                            (bx + padding - 4, qy - 2, left_w, row_h - 4),
                            border_radius=4,
                        )

                    status_prefix = "[x]" if q.status == "completed" else "[ ]"
                    display_text = f"{status_prefix} {q.name}"
                    if len(display_text) > 24:
                        display_text = display_text[:21] + "..."

                    color = (
                        GOLD
                        if is_selected
                        else (MUTED if q.status == "completed" else TEXT)
                    )
                    q_surf = self.ui_font.render(display_text, True, color)
                    self.screen.blit(q_surf, (bx + padding, qy))

            # Vertical divider
            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding + left_w + 10, pane_y),
                (bx + padding + left_w + 10, pane_y + list_h),
                1,
            )

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
                status_color = (
                    (0, 200, 100) if q.status == "completed" else (220, 180, 50)
                )
                status_surf = self.small_font.render(
                    f"Status: {q.status.upper()}", True, status_color
                )
                self.screen.blit(status_surf, (rx, ry))
                ry += 20

                # Location
                loc_surf = self.small_font.render(f"Location: {q.location}", True, TEXT)
                self.screen.blit(loc_surf, (rx, ry))
                ry += 20

                # Contact
                contact_surf = self.small_font.render(
                    f"Contact: {q.contact}", True, TEXT
                )
                self.screen.blit(contact_surf, (rx, ry))
                ry += 28

                # Underline
                pygame.draw.line(
                    self.screen, PANEL_EDGE, (rx, ry), (bx + box_w - padding, ry), 1
                )
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
                no_details_surf = self.small_font.render(
                    "Select a quest to see details.", True, MUTED
                )
                self.screen.blit(no_details_surf, (rx, ry))

            # Footer
            hint_surf = self.small_font.render(
                "▲▼/WS Select  •  Esc Close", True, MUTED
            )
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

            pygame.draw.rect(
                self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6
            )
            pygame.draw.rect(
                self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6
            )

            title_surf = self.ui_font.render("JOURNAL", True, ACCENT)
            self.screen.blit(title_surf, (bx + padding, by + padding))
            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding, by + padding + 22),
                (bx + box_w - padding, by + padding + 22),
                1,
            )

            left_w = 260
            pane_y = by + padding + 36
            list_h = box_h - (padding * 2 + 36 + 24)
            row_h = 28

            entries = self.engine.journal_entries()
            if not entries:
                empty_surf = self.ui_font.render(
                    "The world hasn't told you anything yet.", True, MUTED
                )
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
                        pygame.draw.rect(
                            self.screen,
                            (50, 55, 70),
                            (bx + padding - 4, ey - 2, left_w, row_h - 4),
                            border_radius=4,
                        )

                    settled = entry["status"] in {"settled", "proved false"}
                    display_text = f"[{entry['status']}] {entry['subject']}"
                    if len(display_text) > 28:
                        display_text = display_text[:25] + "..."
                    color = GOLD if is_selected else (MUTED if settled else TEXT)
                    e_surf = self.ui_font.render(display_text, True, color)
                    self.screen.blit(e_surf, (bx + padding, ey))

            pygame.draw.line(
                self.screen,
                PANEL_EDGE,
                (bx + padding + left_w + 10, pane_y),
                (bx + padding + left_w + 10, pane_y + list_h),
                1,
            )

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
                status_surf = self.small_font.render(
                    f"Status: {entry['status'].upper()}", True, status_color
                )
                self.screen.blit(status_surf, (rx, ry))
                ry += 20

                if entry["source"] and entry["source"] != "unknown":
                    source_surf = self.small_font.render(
                        f"Heard from: {entry['source'][:28]}", True, TEXT
                    )
                    self.screen.blit(source_surf, (rx, ry))
                    ry += 20

                if entry["hint"]:
                    hint_text_surf = self.small_font.render(
                        entry["hint"][:44], True, (130, 190, 255)
                    )
                    self.screen.blit(hint_text_surf, (rx, ry))
                    ry += 20

                ry += 8
                pygame.draw.line(
                    self.screen, PANEL_EDGE, (rx, ry), (bx + box_w - padding, ry), 1
                )
                ry += 12

                for line in wrap_text(entry["text"], 36):
                    line_surf = self.small_font.render(line, True, TEXT)
                    self.screen.blit(line_surf, (rx, ry))
                    ry += 16
            else:
                rx = bx + padding + left_w + 24
                no_details_surf = self.small_font.render(
                    "Select an entry to read it.", True, MUTED
                )
                self.screen.blit(no_details_surf, (rx, pane_y))

            hint_surf = self.small_font.render(
                "▲▼/WS Select  •  Esc Close", True, MUTED
            )
            self.screen.blit(hint_surf, (bx + padding, by + box_h - 22))
            return

        items = self.items()
        row_h = 32
        padding = 24
        title = {"main": "MENU", "config": "CONFIGURATION", "model": "SELECT MODEL"}[
            self.menu_page
        ]
        box_w = 480
        box_h = padding * 2 + 28 + len(items) * row_h + 20
        bx = (WINDOW_WIDTH - box_w) // 2
        by = (WINDOW_HEIGHT - box_h) // 2

        pygame.draw.rect(
            self.screen, (28, 30, 38), (bx, by, box_w, box_h), border_radius=6
        )
        pygame.draw.rect(
            self.screen, PANEL_EDGE, (bx, by, box_w, box_h), 1, border_radius=6
        )

        # Title
        title_surf = self.ui_font.render(title, True, ACCENT)
        self.screen.blit(title_surf, (bx + padding, by + padding))
        pygame.draw.line(
            self.screen,
            PANEL_EDGE,
            (bx + padding, by + padding + 22),
            (bx + box_w - padding, by + padding + 22),
            1,
        )

        # Items
        for i, item in enumerate(items):
            iy = by + padding + 30 + i * row_h
            is_selected = i == self.menu_cursor
            if is_selected:
                pygame.draw.rect(
                    self.screen,
                    (50, 55, 70),
                    (bx + 8, iy - 4, box_w - 16, row_h - 4),
                    border_radius=4,
                )
            color = GOLD if is_selected else TEXT
            label = item["label"]
            surf = self.ui_font.render(label, True, color)
            self.screen.blit(surf, (bx + padding, iy))

            # Show ◄ ► hint for cycle/toggle items when selected
            if is_selected and item.get("action") == "config_item":
                spec = item["spec"]
                if spec["type"] in ("cycle", "toggle"):
                    hint = self.small_font.render("◄ ► or Enter", True, MUTED)
                    self.screen.blit(
                        hint, (bx + box_w - padding - hint.get_width(), iy + 4)
                    )

        # Footer hint
        hints = {
            "main": "Enter select  •  Esc close",
            "config": "Enter/◄► change  •  Esc back",
            "model": "Enter select  •  Esc back",
        }
        hint_surf = self.small_font.render(hints[self.menu_page], True, MUTED)
        self.screen.blit(hint_surf, (bx + padding, by + box_h - 20))

    def _draw_inventory_item_text(
        self,
        text: str,
        item_view: dict,
        x: int,
        y: int,
        base_color: tuple[int, int, int],
    ) -> None:
        descriptor = str(item_view.get("descriptor") or "").strip()
        palette_id = str(item_view.get("palette_id") or "").strip()
        if (
            not descriptor
            or not palette_id
            or not text.lower().startswith(descriptor.lower())
        ):
            item_surf = self.ui_font.render(text, True, base_color)
            self.screen.blit(item_surf, (x, y))
            return

        colors = palette_colors(palette_id)
        cursor_x = x
        color_index = 0
        for char in text[: len(descriptor)]:
            color = base_color if char.isspace() else colors[color_index % len(colors)]
            if not char.isspace():
                color_index += 1
            surf = self.ui_font.render(char, True, color)
            self.screen.blit(surf, (cursor_x, y))
            cursor_x += surf.get_width()

        rest = text[len(descriptor) :]
        if rest:
            rest_surf = self.ui_font.render(rest, True, base_color)
            self.screen.blit(rest_surf, (cursor_x, y))
