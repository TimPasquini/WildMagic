"""In-game character view — open with `c`. Broadly mirrors creation, but edits the
*existing* player rather than building a new one: update name, gender, physical
description, backstory, and magical signature; regenerate the portrait; and view (not
re-spend) stats. Changes commit to the live player profile on close.
"""

from __future__ import annotations

import os
import random

import pygame

from ..character import ORIGINS
from ..ui_theme import ACCENT, BACKGROUND, GOLD, MUTED, TEXT
from ._widgets import (
    GENDER_OPTIONS,
    draw_gender_field,
    draw_portrait_panel,
    draw_text_field,
)

_FIELDS = ("name", "appearance", "backstory", "signature")
_FIELD_LABELS = {
    "name": "Name (what others call you)",
    "appearance": "Physical description (NPCs see this)",
    "backstory": "Backstory",
    "signature": "Magical signature (tints every spell)",
}


class CharacterViewScene:
    def __init__(self, host) -> None:
        self.host = host
        self.active = False
        self.s: dict = {}
        self.hitboxes: dict[str, pygame.Rect] = {}

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        profile = self.host.engine.state.player.profile
        gender = (profile.gender or "") if profile else ""
        if gender == "Male":
            mode, other = 0, ""
        elif gender == "Female":
            mode, other = 1, ""
        else:
            mode, other = 2, gender  # "" or a custom value -> the Other slot
        self.s = {
            "fields": {
                "name": profile.name if profile else "",
                "appearance": profile.appearance if profile else "",
                "backstory": profile.backstory if profile else "",
                "signature": profile.signature if profile else "",
            },
            "gender_mode": mode,
            "gender_other": other,
            "focus": 0,
            "portrait_request": None,
            "portrait_status": None,
            "portrait_surface": self._load_portrait(profile),
            "portrait_error": "",
        }
        self.active = True

    def _load_portrait(self, profile):
        path = profile.portrait_path if profile else ""
        if path and os.path.exists(path):
            try:
                return pygame.image.load(path).convert_alpha()
            except Exception:
                return None
        return None

    def close(self) -> None:
        self._commit()
        self.active = False
        self.host.input_active = True

    def _commit(self) -> None:
        """Write the edited fields back onto the live player profile + entity."""
        player = self.host.engine.state.player
        profile = player.profile
        if profile is None:
            return
        fields = self.s["fields"]
        profile.name = fields["name"].strip()
        profile.gender = self._gender()
        profile.appearance = fields["appearance"].strip()
        profile.backstory = fields["backstory"].strip()
        profile.signature = fields["signature"].strip()
        # Keep the entity's perceived description in sync with the profile appearance.
        player.description = profile.appearance or None

    # -- helpers ------------------------------------------------------------
    def _gender(self) -> str:
        mode = self.s["gender_mode"]
        if mode < 2:
            return GENDER_OPTIONS[mode]
        return self.s["gender_other"].strip()

    def _focus_order(self) -> list[str]:
        order = [*_FIELDS, "gender"]
        if self.host.portraits.available():
            order.append("portrait")
        order.append("done")
        return order

    def _focused(self) -> str:
        order = self._focus_order()
        return order[self.s["focus"] % len(order)]

    def _set_focus(self, name: str) -> None:
        order = self._focus_order()
        if name in order:
            self.s["focus"] = order.index(name)

    # -- input --------------------------------------------------------------
    def handle_key(self, event: pygame.event.Event) -> None:
        key = event.key
        order = self._focus_order()
        focus = self._focused()
        if key == pygame.K_ESCAPE:
            self.close()
        elif key == pygame.K_F2:
            self._request_portrait()
        elif key == pygame.K_TAB:
            step = -1 if event.mod & pygame.KMOD_SHIFT else 1
            self.s["focus"] = (self.s["focus"] + step) % len(order)
        elif key == pygame.K_DOWN:
            self.s["focus"] = (self.s["focus"] + 1) % len(order)
        elif key == pygame.K_UP:
            self.s["focus"] = (self.s["focus"] - 1) % len(order)
        elif key in (pygame.K_LEFT, pygame.K_RIGHT) and focus == "gender":
            self.s["gender_mode"] = (
                self.s["gender_mode"] + (1 if key == pygame.K_RIGHT else -1)
            ) % len(GENDER_OPTIONS)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._activate(focus)
        elif key == pygame.K_BACKSPACE:
            self._backspace(focus)
        elif event.unicode and event.unicode.isprintable():
            self._type(focus, event.unicode)

    def handle_mouse(self, pos: tuple[int, int]) -> None:
        for cid, rect in list(self.hitboxes.items()):
            if not rect.collidepoint(pos):
                continue
            if cid.startswith("gender_"):
                self.s["gender_mode"] = int(cid.split("_")[1])
                self._set_focus("gender")
            elif cid.startswith("field_"):
                self._set_focus(cid[len("field_") :])
            elif cid == "portrait_btn":
                self._request_portrait()
            elif cid == "done":
                self.close()
            return

    def _activate(self, focus: str) -> None:
        if focus == "done":
            self.close()
        elif focus == "portrait":
            self._request_portrait()
        else:
            order = self._focus_order()
            self.s["focus"] = (self.s["focus"] + 1) % len(order)

    def _type(self, focus: str, ch: str) -> None:
        if focus in self.s["fields"]:
            self.s["fields"][focus] += ch
        elif focus == "gender" and self.s["gender_mode"] == 2:
            self.s["gender_other"] += ch

    def _backspace(self, focus: str) -> None:
        if focus in self.s["fields"]:
            self.s["fields"][focus] = self.s["fields"][focus][:-1]
        elif focus == "gender" and self.s["gender_mode"] == 2:
            self.s["gender_other"] = self.s["gender_other"][:-1]

    # -- portrait -----------------------------------------------------------
    def _request_portrait(self) -> None:
        s = self.s
        if not self.host.portraits.available() or s["portrait_status"] == "working":
            return
        description = s["fields"]["appearance"].strip()
        gender = self._gender()
        if gender:
            description = f"{gender} {description}".strip()
        if not description:
            s["portrait_status"] = "error"
            s["portrait_error"] = "set a physical description first"
            return
        req_id = self.host.portraits.request(
            description, seed=random.randint(1, 2**31 - 1)
        )
        if req_id is None:
            s["portrait_status"] = "error"
            s["portrait_error"] = "portrait generator unavailable"
            return
        s["portrait_request"] = req_id
        s["portrait_status"] = "working"
        s["portrait_error"] = ""

    def update(self) -> None:
        s = self.s
        req_id = s.get("portrait_request")
        if not req_id or s.get("portrait_status") != "working":
            return
        status, info = self.host.portraits.poll(req_id)
        if status == "done" and info:
            try:
                s["portrait_surface"] = pygame.image.load(info).convert_alpha()
                s["portrait_status"] = "done"
                profile = self.host.engine.state.player.profile
                if profile is not None:
                    profile.portrait_path = info
            except Exception as exc:
                s["portrait_status"] = "error"
                s["portrait_error"] = f"could not load image: {exc}"
        elif status == "error":
            s["portrait_status"] = "error"
            s["portrait_error"] = info or "generation failed"

    # -- rendering ----------------------------------------------------------
    def draw(self) -> None:
        host = self.host
        screen = host.screen
        screen.fill(BACKGROUND)
        self.hitboxes = {}
        width = screen.get_width()
        margin = 40
        host.draw_text("CHARACTER", margin, 32, host.tile_font, ACCENT)
        top = 92
        left_x, left_w = margin, 300
        right_w = 300
        right_x = width - right_w - margin
        mid_x = left_x + left_w + 40
        mid_w = right_x - 30 - mid_x
        focus = self._focused()
        self._draw_stats(left_x, top)
        self._draw_fields(mid_x, mid_w, top, focus)
        self._draw_right(right_x, right_w, top, focus)
        host.draw_text(
            "Tab/Arrows: move  ·  type to edit  ·  Enter/Esc: done"
            + ("  ·  F2: portrait" if host.portraits.available() else ""),
            margin,
            screen.get_height() - 40,
            host.small_font,
            MUTED,
        )

    def _draw_stats(self, x: int, y: int) -> None:
        host = self.host
        player = host.engine.state.player
        profile = player.profile
        origin = ORIGINS.get(profile.origin_id) if profile else None
        y = host.draw_text("Stats", x, y, host.ui_font, GOLD) + 4
        if origin:
            y = (
                host.draw_text(
                    f"{origin.name} ({origin.tradition})", x, y, host.small_font, MUTED
                )
                + 8
            )
        rows = [
            ("Vigor", profile.vigor if profile else 0),
            ("Attunement", profile.attunement if profile else 0),
            ("Composure", profile.composure if profile else 0),
        ]
        for label, value in rows:
            y = host.draw_text(f"{label:12} {value}", x, y, host.ui_font, TEXT) + 2
        y += 8
        derived = [
            ("HP", f"{player.hp}/{player.max_hp}"),
            ("MP", f"{player.mana}/{player.max_mana}"),
            ("Attack", str(player.attack)),
            ("Defense", str(player.defense)),
        ]
        for label, value in derived:
            y = host.draw_text(f"{label:12} {value}", x, y, host.small_font, MUTED) + 2

    def _draw_fields(self, x: int, w: int, y: int, focus: str) -> None:
        for field in ("name",):
            rect, y = draw_text_field(
                self.host,
                _FIELD_LABELS[field],
                self.s["fields"][field],
                x,
                y,
                w,
                focus == field,
            )
            self.hitboxes["field_" + field] = rect
        rects, y = draw_gender_field(
            self.host,
            x,
            y,
            focus == "gender",
            self.s["gender_mode"],
            self.s["gender_other"],
        )
        for i, rect in rects.items():
            self.hitboxes[f"gender_{i}"] = rect
        for field in ("appearance", "backstory", "signature"):
            rect, y = draw_text_field(
                self.host,
                _FIELD_LABELS[field],
                self.s["fields"][field],
                x,
                y,
                w,
                focus == field,
            )
            self.hitboxes["field_" + field] = rect

    def _draw_right(self, x: int, w: int, y: int, focus: str) -> None:
        host = self.host
        box = w
        btn, below = draw_portrait_panel(
            self.host,
            x,
            y,
            box,
            available=host.portraits.available(),
            status=self.s.get("portrait_status"),
            surface=self.s.get("portrait_surface"),
            error=self.s.get("portrait_error", ""),
            warming=host.portraits.warming(),
        )
        if btn is not None:
            self.hitboxes["portrait_btn"] = btn
        done = pygame.Rect(x, below, box, 46)
        dfocus = focus == "done"
        if dfocus:
            pygame.draw.rect(host.screen, ACCENT, done, border_radius=6)
            color = BACKGROUND
        else:
            pygame.draw.rect(host.screen, ACCENT, done, width=2, border_radius=6)
            color = ACCENT
        surf = host.tile_font.render("Done", True, color)
        host.screen.blit(surf, (done.centerx - surf.get_width() // 2, done.y + 10))
        self.hitboxes["done"] = done
