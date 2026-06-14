"""Shared pygame UI theme: the colour palette and a couple of pure render helpers.

This lives apart from `ui.py` so scene modules (`wildmagic/scenes/`) can import the
palette and helpers without importing `ui` itself — which would create a cycle, since
`ui` imports the scenes. Layout constants (window/map/panel sizes) stay in `ui.py`;
scenes that need dimensions read them from the live surface instead.
"""

from __future__ import annotations

import textwrap

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
MODE_COLORS = {
    "spell": MODE_PURPLE,
    "talk": MODE_YELLOW,
    "control": MODE_GREEN,
    "confirm_trade": MODE_ORANGE,
}


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
