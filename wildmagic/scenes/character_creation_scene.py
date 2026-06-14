"""Character creation — a single-screen scene.

Self-contained: owns its state, input, and rendering. The host GameUI provides the
surface, fonts, `draw_text`, the portrait client, and `finish_creation(profile)`. One
screen, three columns: ready-made characters + Random on the left, the editable build
(origin blurb, stats, free-form fields incl. gender) in the middle, and the portrait +
Begin on the right. Mouse-clickable throughout; keyboard Tab/arrows navigate.
"""

from __future__ import annotations

import random

import pygame

from ..character import CREATION_POINTS, ORIGINS, STAT_CAP, STATS, build_profile
from ..ui_theme import (
    ACCENT,
    BACKGROUND,
    GOLD,
    MUTED,
    PANEL_EDGE,
    SELECTED,
    TEXT,
    wrap_text,
)
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
_GENDER_OPTIONS = GENDER_OPTIONS


class CharacterCreationScene:
    def __init__(self, host) -> None:
        self.host = host
        self.active = False
        self.s: dict = {}
        self.hitboxes: dict[str, pygame.Rect] = {}

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self.s = {
            "origins": list(ORIGINS.values()),
            "origin_index": 0,
            "spend": {stat: 0 for stat in STATS},
            "fields": {field: "" for field in _FIELDS},
            "gender_mode": 0,  # index into _GENDER_OPTIONS; 2 == "Other" (custom)
            "gender_other": "",
            "focus": 0,
            "portrait_request": None,
            "portrait_status": None,  # None | working | done | error
            "portrait_surface": None,
            "portrait_path": "",
            "portrait_error": "",
        }
        self.active = True

    # -- derived helpers ----------------------------------------------------
    def _origin(self):
        return self.s["origins"][self.s["origin_index"]]

    def _gender(self) -> str:
        mode = self.s["gender_mode"]
        if mode < 2:
            return _GENDER_OPTIONS[mode]
        return self.s["gender_other"].strip()

    def _focus_order(self) -> list[str]:
        order = ["origins", "random", *STATS, *_FIELDS, "gender"]
        if self.host.portraits.available():
            order.append("portrait")
        order.append("begin")
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
        if key == pygame.K_F2:
            self._request_portrait()
        elif key == pygame.K_TAB:
            step = -1 if event.mod & pygame.KMOD_SHIFT else 1
            self.s["focus"] = (self.s["focus"] + step) % len(order)
        elif key == pygame.K_DOWN:
            self.s["focus"] = (self.s["focus"] + 1) % len(order)
        elif key == pygame.K_UP:
            self.s["focus"] = (self.s["focus"] - 1) % len(order)
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            self._adjust(focus, 1 if key == pygame.K_RIGHT else -1)
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
            if cid.startswith("origin_"):
                self.s["origin_index"] = int(cid.split("_")[1])
                self.s["spend"] = {stat: 0 for stat in STATS}
                self._set_focus("origins")
            elif cid == "random":
                self.host.finish_creation(None)
            elif cid.endswith("_minus"):
                self._adjust(cid[:-6], -1)
                self._set_focus(cid[:-6])
            elif cid.endswith("_plus"):
                self._adjust(cid[:-5], 1)
                self._set_focus(cid[:-5])
            elif cid.startswith("gender_"):
                self.s["gender_mode"] = int(cid.split("_")[1])
                self._set_focus("gender")
            elif cid.startswith("field_"):
                self._set_focus(cid[len("field_") :])
            elif cid == "portrait_btn":
                self._request_portrait()
            elif cid == "begin":
                self._begin()
            return

    def _adjust(self, focus: str, delta: int) -> None:
        s = self.s
        if focus == "origins":
            s["origin_index"] = (s["origin_index"] + delta) % len(s["origins"])
            s["spend"] = {stat: 0 for stat in STATS}
        elif focus in STATS:
            base = self._origin().to_profile()
            if delta > 0:
                spent = sum(s["spend"].values())
                if (
                    spent < CREATION_POINTS
                    and getattr(base, focus) + s["spend"][focus] < STAT_CAP
                ):
                    s["spend"][focus] += 1
            elif s["spend"][focus] > 0:
                s["spend"][focus] -= 1
        elif focus == "gender":
            s["gender_mode"] = (s["gender_mode"] + delta) % len(_GENDER_OPTIONS)

    def _activate(self, focus: str) -> None:
        if focus == "random":
            self.host.finish_creation(None)
        elif focus == "portrait":
            self._request_portrait()
        elif focus in ("begin", "origins"):
            self._begin()
        else:  # Enter on a field/stat moves to the next control
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

    def _begin(self) -> None:
        s = self.s
        origin = self._origin()
        fields = s["fields"]
        profile = build_profile(
            origin.id,
            dict(s["spend"]),
            name=fields["name"] or None,
            appearance=fields["appearance"] or None,
            backstory=fields["backstory"] or None,
            signature=fields["signature"] or None,
        )
        profile.gender = self._gender()
        profile.portrait_path = s.get("portrait_path", "")
        self.host.finish_creation(profile)

    # -- portrait -----------------------------------------------------------
    def _request_portrait(self) -> None:
        s = self.s
        if not self.host.portraits.available():
            s["portrait_status"] = "error"
            s["portrait_error"] = "portrait generator unavailable"
            return
        if s["portrait_status"] == "working":
            return
        description = (
            s["fields"]["appearance"].strip() or self._origin().to_profile().appearance
        )
        gender = self._gender()
        if gender:
            # Gender becomes the first word of the description sent to the portrait LLM.
            description = f"{gender} {description}".strip()
        if not description:
            s["portrait_status"] = "error"
            s["portrait_error"] = "type a physical description first"
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
        s["portrait_surface"] = None

    def update(self) -> None:
        """Poll an in-flight portrait; load the image when it lands."""
        s = self.s
        req_id = s.get("portrait_request")
        if not req_id or s.get("portrait_status") != "working":
            return
        status, info = self.host.portraits.poll(req_id)
        if status == "done" and info:
            try:
                s["portrait_surface"] = pygame.image.load(info).convert_alpha()
                s["portrait_path"] = info
                s["portrait_status"] = "done"
            except Exception as exc:
                s["portrait_status"] = "error"
                s["portrait_error"] = f"could not load image: {exc}"
        elif status == "error":
            s["portrait_status"] = "error"
            s["portrait_error"] = info or "generation failed"

    # -- rendering ----------------------------------------------------------
    def _text(self, text, x, y, font, color):
        return self.host.draw_text(text, x, y, font, color)

    def draw(self) -> None:
        host = self.host
        screen = host.screen
        screen.fill(BACKGROUND)
        self.hitboxes = {}
        width = screen.get_width()
        margin = 40
        self._text("CHARACTER CREATION", margin, 32, host.tile_font, ACCENT)
        top = 92
        left_x, left_w = margin, 300
        right_w = 300
        right_x = width - right_w - margin
        mid_x = left_x + left_w + 40
        mid_w = right_x - 30 - mid_x

        focus = self._focused()
        self._draw_left(left_x, left_w, top, focus)
        self._draw_middle(mid_x, mid_w, top, focus)
        self._draw_right(right_x, right_w, top, focus)

        hint = (
            "Tab/Arrows: move  ·  Left/Right: adjust  ·  type to edit  ·  Enter: begin"
        )
        if host.portraits.available():
            hint += "  ·  F2: portrait"
        self._text(hint, margin, screen.get_height() - 40, host.small_font, MUTED)

    def _draw_left(self, x, w, y, focus) -> None:
        host = self.host
        y = self._text("Ready-made (pick one):", x, y, host.small_font, MUTED) + 4
        for i, origin in enumerate(self.s["origins"]):
            base = origin.to_profile()
            active = i == self.s["origin_index"]
            rect = pygame.Rect(x, y, w, 46)
            if active:
                pygame.draw.rect(self.host.screen, SELECTED, rect, border_radius=4)
            if active and focus == "origins":
                pygame.draw.rect(
                    self.host.screen, ACCENT, rect, width=1, border_radius=4
                )
            self._text(
                origin.name, x + 8, y + 5, host.ui_font, ACCENT if active else TEXT
            )
            self._text(
                f"{origin.tradition} · HP {base.derive_max_hp()} MP {base.derive_max_mana()}",
                x + 8,
                y + 25,
                host.small_font,
                MUTED,
            )
            self.hitboxes[f"origin_{i}"] = rect
            y += 52
        y += 8
        rrect = pygame.Rect(x, y, w, 36)
        rfocus = focus == "random"
        pygame.draw.rect(
            self.host.screen,
            ACCENT if rfocus else PANEL_EDGE,
            rrect,
            width=2,
            border_radius=4,
        )
        self._text(
            "Random wild mage", x + 8, y + 8, host.ui_font, ACCENT if rfocus else TEXT
        )
        self.hitboxes["random"] = rrect
        y += 46
        self._text("...or tune it ->", x, y, host.small_font, MUTED)

    def _draw_middle(self, x, w, y, focus) -> None:
        host = self.host
        origin = self._origin()
        base = origin.to_profile()
        for line in wrap_text(origin.blurb, max(20, w // 8)):
            y = self._text(line, x, y, host.small_font, MUTED)
        y += 10

        remaining = CREATION_POINTS - sum(self.s["spend"].values())
        y = (
            self._text(
                f"Stats — {remaining} point(s) to spend", x, y, host.ui_font, GOLD
            )
            + 6
        )
        for stat in STATS:
            total = getattr(base, stat) + self.s["spend"][stat]
            sfocus = focus == stat
            self._text(
                stat.capitalize(), x, y + 3, host.ui_font, ACCENT if sfocus else TEXT
            )
            minus = pygame.Rect(x + 150, y, 26, 26)
            plus = pygame.Rect(x + 150 + 30 + 150 + 8, y, 26, 26)
            self._draw_button(minus, "-")
            bar = "#" * total + "-" * (STAT_CAP - total)
            self._text(f"[{bar}] {total}", x + 150 + 34, y + 3, host.ui_font, TEXT)
            self._draw_button(plus, "+")
            self.hitboxes[f"{stat}_minus"] = minus
            self.hitboxes[f"{stat}_plus"] = plus
            y += 34
        preview = build_profile(origin.id, dict(self.s["spend"]))
        y = (
            self._text(
                f"HP {preview.derive_max_hp()}   MP {preview.derive_max_mana()}   Atk {preview.derive_attack()}",
                x,
                y + 2,
                host.small_font,
                MUTED,
            )
            + 10
        )

        y = self._field("name", x, y, w, focus, "(a wandering stranger)")
        y = self._gender_field(x, y, focus)
        y = self._field("appearance", x, y, w, focus, base.appearance)
        y = self._field("backstory", x, y, w, focus, base.backstory)
        y = self._field("signature", x, y, w, focus, base.signature)

    def _draw_button(self, rect, label) -> None:
        pygame.draw.rect(self.host.screen, PANEL_EDGE, rect, width=1, border_radius=3)
        surf = self.host.ui_font.render(label, True, TEXT)
        self.host.screen.blit(
            surf,
            (
                rect.centerx - surf.get_width() // 2,
                rect.centery - surf.get_height() // 2,
            ),
        )

    def _field(self, field, x, y, w, focus, default) -> int:
        rect, ny = draw_text_field(
            self.host,
            _FIELD_LABELS[field],
            self.s["fields"][field],
            x,
            y,
            w,
            focus == field,
            default,
        )
        self.hitboxes["field_" + field] = rect
        return ny

    def _gender_field(self, x, y, focus) -> int:
        rects, ny = draw_gender_field(
            self.host,
            x,
            y,
            focus == "gender",
            self.s["gender_mode"],
            self.s["gender_other"],
        )
        for i, rect in rects.items():
            self.hitboxes[f"gender_{i}"] = rect
        return ny

    def _draw_right(self, x, w, y, focus) -> None:
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

        begin = pygame.Rect(x, below, box, 46)
        bfocus = focus == "begin"
        if bfocus:
            pygame.draw.rect(self.host.screen, ACCENT, begin, border_radius=6)
            label_color = BACKGROUND
        else:
            pygame.draw.rect(self.host.screen, ACCENT, begin, width=2, border_radius=6)
            label_color = ACCENT
        surf = host.tile_font.render("Begin  >", True, label_color)
        self.host.screen.blit(
            surf, (begin.centerx - surf.get_width() // 2, begin.y + 10)
        )
        self.hitboxes["begin"] = begin
