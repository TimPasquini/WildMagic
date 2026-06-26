from __future__ import annotations

from typing import Any

import pygame

from wildmagic.actions import standing_summary_text
from wildmagic.curses import curse_card
from wildmagic.models import Entity
from wildmagic.rendering.layout import (
    MAP_OFFSET_X,
    MAP_PIXEL_HEIGHT,
    MAP_PIXEL_WIDTH,
    PANEL_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from wildmagic.ui_theme import (
    ACCENT,
    DANGER,
    GOLD,
    MANA,
    MODE_COLORS,
    MODE_GREEN,
    MODE_ORANGE,
    MODE_PURPLE,
    MODE_YELLOW,
    MUTED,
    PANEL,
    PANEL_EDGE,
    SELECTED,
    TEXT,
    blend_color,
    wrap_text,
)


CONTROLS_HINT = (
    "Keyboard controls active - arrows/WASD/keypad move, > descend, < ascend, o open, "
    "g pick up, f cast spark, x investigate, e examine, r read, u free, z rest, "
    "b wares, p possess, l inspect, m atlas, t standing, n followers, h help, c character, "
    "j journal, q quests, i inventory, period or keypad-5 to wait, F7 generation queue, "
    "F8 watch AI, F9 pause AI, F10 step AI, Esc back to Wild Spell. "
    "Tab switches Wild Spell / Controls / Talk; hold Ctrl for a quick control key (Ctrl+c = character)."
)
CONTROLS_HINT_WRAP = 48


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
    "weakened": (150, 120, 110),
    "warded": (120, 180, 255),
    "invisible": (170, 170, 200),
    "regenerating": (100, 220, 150),
    "hasted": (220, 220, 255),
}


def draw_panel(host: Any) -> None:
    x = MAP_OFFSET_X + MAP_PIXEL_WIDTH
    pygame.draw.rect(host.screen, PANEL, (x, 0, PANEL_WIDTH, WINDOW_HEIGHT))
    pygame.draw.line(host.screen, PANEL_EDGE, (x, 0), (x, WINDOW_HEIGHT), 2)
    state = host.engine.state
    player = state.player
    cursor_y = 18
    cursor_y = host.draw_text("Wild Magic", x + 20, cursor_y, host.ui_font, ACCENT)
    if state.scenario == "frontier":
        location = f"Zone ({state.zone_x},{state.zone_y}) — {state.zone_type}"
    else:
        location = f"Depth {state.depth}/{state.max_depth}"
    cursor_y = host.draw_text(
        f"Turn {state.turn}  {location}  Resolver {host.provider_label}",
        x + 20,
        cursor_y + 8,
        host.small_font,
        MUTED,
    )
    cursor_y = host.draw_text(
        state.clock_label(),
        x + 20,
        cursor_y + 2,
        host.small_font,
        MUTED,
    )
    cursor_y = draw_bars(host, x + 20, cursor_y + 16, player)
    cursor_y = draw_gold(host, x + 20, cursor_y + 4)
    cursor_y = draw_experience(host, x + 20, cursor_y + 2)
    cursor_y = draw_statuses(host, x + 20, cursor_y + 10, player)
    cursor_y = draw_visible_enemies(host, x + 20, cursor_y + 8)
    cursor_y = draw_inventory(host, x + 20, cursor_y + 8)
    cursor_y = draw_floor_items(host, x + 20, cursor_y + 6)
    cursor_y = draw_curses(host, x + 20, cursor_y + 6)
    cursor_y = draw_standing(host, x + 20, cursor_y + 6)
    box_height = spell_box_height(host)
    spell_y = WINDOW_HEIGHT - box_height - 46
    log_y = cursor_y + 16
    log_height = max(120, spell_y - log_y - 46)
    draw_log(host, x + 20, log_y, log_height)
    draw_spell_box(host, x + 20, spell_y, box_height)
    if state.game_over:
        draw_game_over_overlay(host)


def draw_game_over_overlay(host: Any) -> None:
    state = host.engine.state
    overlay = pygame.Surface((MAP_PIXEL_WIDTH, MAP_PIXEL_HEIGHT), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 150))
    host.screen.blit(overlay, (MAP_OFFSET_X, 0))
    big_font = pygame.font.SysFont("consolas", 48, bold=True)
    if state.victory:
        message = "THE EMPIRE FALLS"
        color = ACCENT
        sub_text = "The emperor is dead, the order broken — press R to begin anew"
    elif state.death_cause == "empire":
        message = "CASE CLOSED"
        color = DANGER
        sub_text = "Incident filed, ref. 7-112 — press R to reopen the file"
    else:
        message = "THE WILD TAKES YOU BACK"
        color = DANGER
        sub_text = "Press R — another sorcerer takes up the thread"
    surface = big_font.render(message, True, color)
    rect = surface.get_rect(
        center=(MAP_OFFSET_X + MAP_PIXEL_WIDTH // 2, MAP_PIXEL_HEIGHT // 2)
    )
    host.screen.blit(surface, rect)
    sub = pygame.font.SysFont("consolas", 18).render(sub_text, True, MUTED)
    sub_rect = sub.get_rect(
        center=(MAP_OFFSET_X + MAP_PIXEL_WIDTH // 2, MAP_PIXEL_HEIGHT // 2 + 50)
    )
    host.screen.blit(sub, sub_rect)


def draw_bars(host: Any, x: int, y: int, player: Entity) -> int:
    y = draw_stat_bar(host, x, y, "HP", player.hp, player.max_hp, DANGER)
    y = draw_stat_bar(host, x, y + 8, "MP", player.mana, player.max_mana, MANA)
    return y


def draw_gold(host: Any, x: int, y: int) -> int:
    """Gold is a first-class resource, not just another inventory line."""
    amount = host.engine.state.inventory.get("gold", 0)
    return host.draw_text(f"Gold: {amount}", x, y, host.small_font, GOLD)


def draw_experience(host: Any, x: int, y: int) -> int:
    amount = host.engine.state.experience
    return host.draw_text(f"Experience: {amount}", x, y, host.small_font, ACCENT)


def draw_stat_bar(
    host: Any,
    x: int,
    y: int,
    label: str,
    value: int,
    maximum: int,
    color: tuple[int, int, int],
) -> int:
    host.draw_text(f"{label} {value}/{maximum}", x, y, host.small_font, TEXT)
    bar_x = x + 86
    bar_y = y + 3
    width = 220
    height = 12
    pygame.draw.rect(
        host.screen, (48, 50, 58), (bar_x, bar_y, width, height), border_radius=3
    )
    fill_width = int(width * (value / max(1, maximum)))
    pygame.draw.rect(
        host.screen, color, (bar_x, bar_y, fill_width, height), border_radius=3
    )
    return y + 22


def draw_statuses(host: Any, x: int, y: int, player: Entity) -> int:
    statuses = player.statuses
    if not statuses:
        return y
    y = host.draw_text("Statuses", x, y, host.small_font, MUTED)
    parts = []
    for key, val in sorted(statuses.items()):
        display = player.status_display.get(key, key).replace("_", " ")
        dur = "" if val == "permanent" else f":{val}"
        parts.append((display + dur, STATUS_COLORS.get(key, TEXT)))
    line_parts: list[tuple[str, tuple[int, int, int]]] = []
    line_width = 0
    max_width = PANEL_WIDTH - 50
    for label, color in parts:
        width = host.small_font.size(label + "  ")[0]
        if line_width + width > max_width and line_parts:
            y = draw_colored_chips(host, x, y, line_parts)
            line_parts = []
            line_width = 0
        line_parts.append((label, color))
        line_width += width
    if line_parts:
        y = draw_colored_chips(host, x, y, line_parts)
    return y


def draw_colored_chips(
    host: Any, x: int, y: int, parts: list[tuple[str, tuple[int, int, int]]]
) -> int:
    cursor_x = x
    for label, color in parts:
        surface = host.small_font.render(label, True, color)
        host.screen.blit(surface, (cursor_x, y))
        cursor_x += surface.get_width() + 10
    return y + host.small_font.get_linesize() + 2


def draw_visible_enemies(host: Any, x: int, y: int) -> int:
    engine = host.engine
    player = engine.state.player
    all_enemies = engine.living_enemies()
    visible = [
        entity for entity in all_enemies if engine.is_visible(entity.x, entity.y)
    ]
    hidden = len(all_enemies) - len(visible)
    if not all_enemies:
        return y
    y = host.draw_text(
        f"Enemies  {len(visible)} visible" + (f"  {hidden} unseen" if hidden else ""),
        x,
        y,
        host.small_font,
        DANGER if visible else MUTED,
    )
    for enemy in sorted(visible, key=lambda entity: engine.distance(player, entity))[
        :4
    ]:
        hp_frac = enemy.hp / max(1, enemy.max_hp)
        bar_color = (
            DANGER if hp_frac < 0.4 else GOLD if hp_frac < 0.7 else (160, 200, 140)
        )
        status_chips = " ".join(
            enemy.status_display.get(key, key) for key in sorted(enemy.statuses)[:2]
        )
        suffix = f"  {status_chips}" if status_chips else ""
        label = f"  {enemy.name} {enemy.hp}/{enemy.max_hp}{suffix}"
        y = host.draw_text(label, x, y, host.small_font, bar_color)
    if len(visible) > 4:
        y = host.draw_text(f"  …+{len(visible) - 4} more", x, y, host.small_font, MUTED)
    return y


def draw_floor_items(host: Any, x: int, y: int) -> int:
    engine = host.engine
    player = engine.state.player
    visible_items = [
        entity
        for entity in engine.state.entities.values()
        if entity.kind == "item" and engine.is_visible(entity.x, entity.y)
    ]
    if not visible_items:
        return y
    at_feet = [
        entity
        for entity in visible_items
        if entity.x == player.x and entity.y == player.y
    ]
    nearby = [entity for entity in visible_items if entity not in at_feet][:4]
    y = host.draw_text("Floor", x, y, host.small_font, GOLD)
    if at_feet:
        names = ", ".join(entity.name for entity in at_feet[:3])
        y = host.draw_text(f"[here] {names}", x, y, host.small_font, ACCENT)
    for item in nearby:
        dx = item.x - player.x
        dy = item.y - player.y
        dist = int((dx * dx + dy * dy) ** 0.5)
        y = host.draw_text(f"[{dist}] {item.name}", x, y, host.small_font, TEXT)
    return y


def draw_inventory(host: Any, x: int, y: int) -> int:
    equipment_view = host.session.equipment_inventory_view()
    # Gold gets its own dedicated readout (draw_gold) right next to the HP/MP
    # bars - showing it again here would just be visual noise.
    items = (
        ", ".join(
            f"{item['name']} x{item['quantity']}" for item in equipment_view["items"]
        )
        or "empty"
    )
    y = host.draw_text("Inventory", x, y, host.small_font, GOLD)
    for line in wrap_text(items, 42):
        y = host.draw_text(line, x, y, host.small_font, TEXT)
    return y


def draw_curses(host: Any, x: int, y: int) -> int:
    curses = list(host.engine.state.curses.values())
    host.curse_rects = []
    y = host.draw_text("Curses", x, y, host.small_font, DANGER if curses else MUTED)
    if not curses:
        return host.draw_text("none", x, y, host.small_font, MUTED)
    for curse in curses[-3:]:
        text = f"{curse.name} x{curse.stacks}"
        surface = host.small_font.render(text, True, TEXT)
        rect = pygame.Rect(x, y, surface.get_width(), surface.get_height())
        host.screen.blit(surface, (x, y))
        host.curse_rects.append((rect, curse.id))
        y += host.small_font.get_linesize()
    return y


def draw_curse_tooltip(host: Any) -> None:
    hover_id = None
    mouse = host._logical_mouse_pos()
    for rect, curse_id in host.curse_rects:
        if rect.collidepoint(mouse):
            hover_id = curse_id
            break
    curse_id = host.curse_tooltip_id or hover_id
    if not curse_id:
        return
    curse = host.engine.state.curses.get(curse_id)
    if curse is None:
        host.curse_tooltip_id = None
        return
    card = curse_card(curse)
    lines: list[tuple[str, tuple[int, int, int]]] = [
        (f"{card['name']} x{card['stacks']}", DANGER),
        (card["description"], TEXT),
    ]
    for limit in card["mechanical_limits"]:
        lines.append((limit, GOLD))
    if card["semantic_prompt"]:
        lines.append((card["semantic_prompt"], MUTED))
    lines.append(
        (
            f"Lifts at {card['xp_to_clear']} XP earned "
            f"(this stack: {card['clear_progress']}/{card['xp_to_clear']})",
            ACCENT,
        )
    )
    wrapped: list[tuple[str, tuple[int, int, int]]] = []
    for text, color in lines:
        for part in wrap_text(str(text), 38):
            wrapped.append((part, color))
    pad = 10
    line_h = host.small_font.get_linesize() + 2
    width = 320
    height = pad * 2 + line_h * len(wrapped)
    x = min(mouse[0] + 14, WINDOW_WIDTH - width - 6)
    y = min(mouse[1] + 14, WINDOW_HEIGHT - height - 6)
    pygame.draw.rect(host.screen, (20, 22, 30), (x, y, width, height), border_radius=6)
    pygame.draw.rect(host.screen, PANEL_EDGE, (x, y, width, height), 1, border_radius=6)
    cursor_y = y + pad
    for text, color in wrapped:
        host.draw_text(text, x + pad, cursor_y, host.small_font, color)
        cursor_y += line_h


def draw_standing(host: Any, x: int, y: int) -> int:
    """Compact standing summary; the full readout lives behind the T key."""
    summary = standing_summary_text(host.engine.state)
    has_standing = summary != "unknown to the powers"
    ledger = host.engine.state.faction_ledger
    y = host.draw_text(
        "Standing", x, y, host.small_font, ACCENT if has_standing else MUTED
    )
    for line in wrap_text(summary, 42):
        y = host.draw_text(line, x, y, host.small_font, TEXT if has_standing else MUTED)
    y = host.draw_text("(press T to view full standings)", x, y, host.small_font, MUTED)
    legend = host.engine.legend_words(host.engine.state.player_soul_id, n=4)
    if legend:
        for line in wrap_text("Legend: " + ", ".join(legend), 42):
            y = host.draw_text(line, x, y, host.small_font, MUTED)
    empire = ledger.primary("empire")
    if empire is not None and "defense" in empire.resources:
        if host.engine.emperor_reachable():
            text = "Empire defenses: BROKEN - the emperor is in reach"
            color = ACCENT
        else:
            text = f"Empire defenses: {empire.resources['defense']}"
            color = MUTED
        for line in wrap_text(text, 42):
            y = host.draw_text(line, x, y, host.small_font, color)
    followers = host.engine.followers()
    orgs = host.engine.state.faction_ledger.by_kind("player_org")
    if followers or orgs:
        parts = []
        if followers:
            parts.append(
                f"{len(followers)} follower{'s' if len(followers) != 1 else ''}"
            )
        if orgs:
            parts.append(", ".join(org.name for org in orgs))
        for line in wrap_text("Retinue: " + "; ".join(parts), 42):
            y = host.draw_text(line, x, y, host.small_font, MUTED)
    return y


def draw_log(host: Any, x: int, y: int, height: int) -> None:
    host.log_line_rects = []
    scrollbar_width = 10
    host.log_area = pygame.Rect(x, y, PANEL_WIDTH - 40 - scrollbar_width - 2, height)
    pygame.draw.line(host.screen, PANEL_EDGE, (x, y - 8), (WINDOW_WIDTH - 20, y - 8), 1)
    line_y = y
    lines: list[tuple[str, bool, bool]] = []
    line_height = host.small_font.get_linesize() + 2
    max_lines = max(1, height // line_height)
    for message in host.engine.state.messages[-1000:]:
        is_prompt = message.startswith(">") or message.startswith("*>")
        is_danger = is_player_damage_message(message)
        lines.extend((line, is_prompt, is_danger) for line in wrap_text(message, 42))

    total_lines = len(lines)
    host._log_max_scroll = max(0, total_lines - max_lines)
    host.log_scroll_offset = max(0, min(host.log_scroll_offset, host._log_max_scroll))

    start_idx = max(0, total_lines - max_lines - host.log_scroll_offset)
    end_idx = max(0, total_lines - host.log_scroll_offset)
    if total_lines <= max_lines:
        start_idx = 0
        end_idx = total_lines

    visible_lines = lines[start_idx:end_idx]
    selected_indexes = selected_log_indexes(host, len(visible_lines))
    for index, (line, is_prompt, is_danger) in enumerate(visible_lines):
        color = MUTED if is_prompt else (DANGER if is_danger else TEXT)
        rect = pygame.Rect(
            x - 4, line_y - 1, PANEL_WIDTH - 32 - scrollbar_width - 2, line_height
        )
        if index in selected_indexes:
            pygame.draw.rect(host.screen, SELECTED, rect, border_radius=3)
        line_y = host.draw_text(line, x, line_y, host.small_font, color)
        host.log_line_rects.append((rect, line))
        if line_y > y + height:
            break

    draw_log_scrollbar(
        host,
        x + PANEL_WIDTH - 40 - scrollbar_width,
        y,
        scrollbar_width,
        height,
        total_lines,
        max_lines,
    )


def selected_log_indexes(host: Any, visible_line_count: int) -> set[int]:
    if host.log_selection_anchor is None or host.log_selection_focus is None:
        return set()
    if not host.log_line_rects and visible_line_count == 0:
        return set()
    start = min(host.log_selection_anchor, host.log_selection_focus)
    end = max(host.log_selection_anchor, host.log_selection_focus)
    return {
        index for index in range(max(0, start), min(visible_line_count - 1, end) + 1)
    }


def input_line_slices(host: Any, wrap_chars: int = 42) -> list[tuple[str, int, int]]:
    text = host.input_text
    if not text:
        return [(" ", 0, 0)]
    slices: list[tuple[str, int, int]] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        limit = min(text_length, start + wrap_chars)
        next_start = limit
        end = limit
        if limit < text_length:
            break_at = text.rfind(" ", start + 1, limit + 1)
            if break_at > start:
                end = break_at
                next_start = break_at + 1
        line = text[start:end] or " "
        slices.append((line, start, end))
        start = max(next_start, start + 1)
    return slices


def spell_box_height(host: Any) -> int:
    if host.input_mode == "control":
        visible_lines = len(wrap_text(CONTROLS_HINT, CONTROLS_HINT_WRAP))
    elif host.engine.state.pending_trade is not None:
        # Two item lines ("You receive:" + "You give:") plus the Y/N hint.
        # Fixed at 3 lines — the flavor text stays in the message log above.
        visible_lines = 3
    else:
        visible_lines = min(max(2, len(input_line_slices(host))), 6)
    return 18 + visible_lines * 18


def draw_mode_box(
    host: Any, text: str, x: int, y: int, color: tuple[int, int, int], active: bool
) -> pygame.Rect:
    """Clickable mode-switch box for Wild Spell/Talk/Controls."""
    surface = host.small_font.render(text, True, TEXT if active else MUTED)
    pad_x, pad_y = 10, 5
    rect = pygame.Rect(
        x, y, surface.get_width() + pad_x * 2, surface.get_height() + pad_y * 2
    )
    if active:
        pygame.draw.rect(
            host.screen, blend_color(PANEL, color, 0.24), rect, border_radius=6
        )
        pygame.draw.rect(host.screen, color, rect, width=2, border_radius=6)
    else:
        pygame.draw.rect(
            host.screen,
            blend_color(PANEL, color, 0.12),
            rect,
            width=1,
            border_radius=6,
        )
    host.screen.blit(surface, (x + pad_x, y + pad_y))
    return rect


def draw_spell_box(host: Any, x: int, y: int, height: int) -> None:
    width = PANEL_WIDTH - 40
    pygame.draw.line(
        host.screen, PANEL_EDGE, (x, y - 42), (WINDOW_WIDTH - 20, y - 42), 1
    )
    box_y = y - 34

    talk_target = host.engine.find_talk_target()
    auto_talk_target = host._auto_talk_target()
    auto_talk_target_id = auto_talk_target.id if auto_talk_target is not None else None
    if auto_talk_target_id != host._last_auto_talk_target_id:
        if auto_talk_target_id is not None:
            # In a calm scene, becoming adjacent to a neutral NPC defaults to Talk.
            # Broader talk-to-anyone stays manual so Wild Spell remains the combat
            # default around enemies or ranged conversations.
            host.input_mode = "talk"
            host.input_active = True
            host._auto_talk_mode = True
        elif host.input_mode == "talk" and host._auto_talk_mode:
            # The calm auto-talk case ended, so return to the normal default.
            host.input_mode = "spell"
            host.input_active = True
            host._auto_talk_mode = False
        host._last_auto_talk_target_id = auto_talk_target_id

    # Same transition-based pattern as talk-target switching above: force the
    # mode the *moment* a trade appears or resolves, not every frame - so a
    # confirmation can't be dodged by clicking elsewhere, and resolving it
    # (accept/reject) hands control straight back to whatever made sense before.
    trade_active = host.engine.state.pending_trade is not None
    if trade_active != host._last_trade_active:
        if trade_active:
            host.input_mode = "confirm_trade"
            host.input_active = False
            # `talk` can block on up to two sequential LLM calls (6-24s) with
            # the whole event loop frozen. Any Enter/Y the player pressed
            # (or that key-repeat queued) during that wait is still sitting
            # in the queue when this modal claims control on the next frame
            # -- without this, handle_key's confirm_trade gate immediately
            # "accepts" using that stale keypress, before the player ever
            # sees the proposal. Flush it so only fresh input reaches the modal.
            pygame.event.clear((pygame.KEYDOWN, pygame.KEYUP))
        elif host.input_mode == "confirm_trade":
            host.input_mode = "talk" if auto_talk_target is not None else "spell"
            host.input_active = True
            host._auto_talk_mode = auto_talk_target is not None
        host._last_trade_active = trade_active

    specs = [("spell", "Wild Spell", MODE_PURPLE)]
    if talk_target is not None:
        specs.append(("talk", "Talk", MODE_YELLOW))
    specs.append(("control", "Controls", MODE_GREEN))
    cursor_x = x
    host.mode_label_rects = []
    for mode, label, color in specs:
        rect = draw_mode_box(
            host, label, cursor_x, box_y, color, host.input_mode == mode
        )
        host.mode_label_rects.append((rect, mode))
        cursor_x = rect.right + 10
    if trade_active:
        # Deliberately not in `specs` / `mode_label_rects` - this box appears
        # only when a real decision is pending, never as a voluntary tab.
        draw_mode_box(host, "Confirm Trade", cursor_x, box_y, MODE_ORANGE, True)

    rect = pygame.Rect(x, y, width, height)
    host.spell_box_rect = rect
    host.input_line_rects = []
    pygame.draw.rect(host.screen, (17, 19, 24), rect, border_radius=6)
    pygame.draw.rect(
        host.screen, MODE_COLORS[host.input_mode], rect, width=1, border_radius=6
    )
    if host.input_mode == "control":
        for index, line in enumerate(wrap_text(CONTROLS_HINT, CONTROLS_HINT_WRAP)):
            host.draw_text(line, x + 10, y + 9 + index * 18, host.small_font, MUTED)
        return
    if (
        host.input_mode == "confirm_trade"
        and host.engine.state.pending_trade is not None
    ):
        trade = host.engine.state.pending_trade
        receive_line = (
            f"You receive:  {_format_trade_items(trade.get('npc_gives') or [])}"
        )
        give_line = f"You give:     {_format_trade_items(trade.get('npc_wants') or [])}"
        cursor_y = y + 9
        cursor_y = host.draw_text(receive_line, x + 10, cursor_y, host.ui_font, TEXT)
        cursor_y = host.draw_text(give_line, x + 10, cursor_y, host.ui_font, TEXT)
        host.draw_text(
            "[Y]es accept    [N]o reject",
            x + 10,
            cursor_y + 6,
            host.small_font,
            MODE_ORANGE,
        )
        return
    if not host.input_text and host.input_mode == "talk" and talk_target is not None:
        host.draw_text(
            f"Say something to {talk_target.name}...",
            x + 10,
            y + 9,
            host.ui_font,
            MUTED,
        )
        return
    host._clamp_input_cursor()
    lines = input_line_slices(host)
    max_visible_lines = max(1, (height - 18) // 18)
    cursor_line = len(lines) - 1
    for index, (_line, start, end) in enumerate(lines):
        if start <= host.input_cursor <= end:
            cursor_line = index
            break
    first_line = 0
    if len(lines) > max_visible_lines:
        first_line = min(
            max(0, cursor_line - max_visible_lines + 1),
            len(lines) - max_visible_lines,
        )
    visible_lines = lines[first_line : first_line + max_visible_lines]
    blink = host.input_active and pygame.time.get_ticks() % 1000 < 500
    for index, (line, start, end) in enumerate(visible_lines):
        prefix = "..." if first_line > 0 and index == 0 else ""
        line_y = y + 9 + index * 18
        text_x = x + 10
        display = prefix + line
        host.draw_text(display, text_x, line_y, host.ui_font, TEXT)
        prefix_width = host.ui_font.size(prefix)[0]
        line_rect = pygame.Rect(text_x, line_y, width - 20, 18)
        host.input_line_rects.append((line_rect, start, end, line, prefix_width))
        if blink and start <= host.input_cursor <= end:
            offset = max(0, min(len(line), host.input_cursor - start))
            caret_x = text_x + prefix_width + host.ui_font.size(line[:offset])[0]
            pygame.draw.line(
                host.screen,
                TEXT,
                (caret_x, line_y + 2),
                (caret_x, line_y + host.ui_font.get_height()),
                1,
            )


def _format_trade_items(items: list) -> str:
    if not items:
        return "nothing"
    parts = []
    for entry in items:
        qty = entry.get("quantity", 1)
        name = str(entry.get("item", "?"))
        parts.append(f"{qty} {name}" if qty != 1 else name)
    return ", ".join(parts)


def draw_log_scrollbar(
    host: Any,
    x: int,
    y: int,
    width: int,
    height: int,
    total_lines: int,
    visible_lines: int,
) -> None:
    track = pygame.Rect(x, y, width, height)
    pygame.draw.rect(host.screen, (20, 22, 27), track, border_radius=4)
    if total_lines <= visible_lines or host._log_max_scroll <= 0:
        host.log_scrollbar_track_rect = None
        host.log_scrollbar_thumb_rect = None
        return
    thumb_height = max(28, int(height * (visible_lines / total_lines)))
    usable = max(1, height - thumb_height)
    thumb_y = y + usable - int(usable * (host.log_scroll_offset / host._log_max_scroll))
    thumb = pygame.Rect(x, thumb_y, width, thumb_height)
    thumb_color = ACCENT if host.log_dragging_scrollbar else PANEL_EDGE
    pygame.draw.rect(host.screen, thumb_color, thumb, border_radius=4)
    host.log_scrollbar_track_rect = track
    host.log_scrollbar_thumb_rect = thumb


def is_player_damage_message(message: str) -> bool:
    if getattr(message, "is_danger", False):
        return True

    msg_lower = message.lower()

    # 1. Player is hit by someone/something (e.g. "cave spider hits You for 3.")
    # Note: the player's name is "You" in these messages, so it matches "hits you" or "hit you".
    if "hits you" in msg_lower or "hit you" in msg_lower:
        return True

    # 2. Direct damage phrases.
    damage_words = ("damage", "health", "hp")
    if any(word in msg_lower for word in damage_words):
        damage_verbs = ("take", "suffer", "lose", "cost")
        if any(verb in msg_lower for verb in damage_verbs):
            return True

    # 3. Death.
    return "you die" in msg_lower or "you died" in msg_lower
