from __future__ import annotations

from typing import Any

import pygame

from wildmagic.models import (
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
    VINES,
    WALL,
    WATER,
    Entity,
)
from wildmagic.rendering.layout import MAP_OFFSET_X, TILE_SIZE
from wildmagic.ui_theme import BACKGROUND, blend_color


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


def draw_map(
    screen: pygame.Surface,
    tile_font: pygame.font.Font,
    engine: Any,
) -> None:
    state = engine.state
    for y, row in enumerate(state.tiles):
        for x, tile in enumerate(row):
            if not engine.is_explored(x, y):
                continue
            color = TILE_COLORS.get(tile, TILE_COLORS[FLOOR])
            if not engine.is_visible(x, y):
                color = _dim_color(color)
            draw_glyph(screen, tile_font, tile, x, y, color)
    for entity in sorted(
        state.entities.values(), key=lambda item: item.kind == "player"
    ):
        if not entity.alive and entity.kind == "item":
            continue
        revealed = "revealed" in entity.statuses
        visible = engine.is_visible(entity.x, entity.y)
        if entity.id != state.player_id and not visible and not revealed:
            continue
        color = entity_color(entity)
        if revealed and not visible:
            color = _dim_color(color)
        draw_glyph(screen, tile_font, entity.char, entity.x, entity.y, color)
    draw_target_reticle(screen, state)


def draw_target_reticle(screen: pygame.Surface, state: Any) -> None:
    """A bright corner-bracket reticle on the explicitly marked spell target."""
    if state.target_x is None or state.target_y is None:
        return
    tx, ty = state.target_x, state.target_y
    px = MAP_OFFSET_X + tx * TILE_SIZE
    py = ty * TILE_SIZE
    color = (255, 120, 90)
    seg = max(4, TILE_SIZE // 3)
    rect = pygame.Rect(px + 1, py + 1, TILE_SIZE - 2, TILE_SIZE - 2)
    # Four L-shaped corner brackets (a clean reticle, never hides the glyph beneath).
    corners = (
        (rect.left, rect.top, 1, 1),
        (rect.right, rect.top, -1, 1),
        (rect.left, rect.bottom, 1, -1),
        (rect.right, rect.bottom, -1, -1),
    )
    for cx, cy, sx, sy in corners:
        pygame.draw.line(screen, color, (cx, cy), (cx + sx * seg, cy), 2)
        pygame.draw.line(screen, color, (cx, cy), (cx, cy + sy * seg), 2)


def draw_glyph(
    screen: pygame.Surface,
    tile_font: pygame.font.Font,
    glyph: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    surface = tile_font.render(glyph, True, color)
    rect = surface.get_rect(
        center=(
            MAP_OFFSET_X + x * TILE_SIZE + TILE_SIZE // 2,
            y * TILE_SIZE + TILE_SIZE // 2,
        )
    )
    screen.blit(surface, rect)


def entity_color(entity: Entity) -> tuple[int, int, int]:
    if entity.kind == "item":
        return ENTITY_COLORS["item"]
    base = ENTITY_COLORS.get(entity.faction, ENTITY_COLORS["neutral"])
    if not entity.alive:
        return base
    statuses = entity.statuses
    if "burning" in statuses:
        return blend_color(base, (232, 96, 70), 0.55)
    if "frozen" in statuses:
        return blend_color(base, (156, 210, 224), 0.55)
    if "poisoned" in statuses:
        return blend_color(base, (130, 200, 80), 0.55)
    if "bleeding" in statuses:
        return blend_color(base, (200, 60, 60), 0.4)
    if "invisible" in statuses:
        return blend_color(base, BACKGROUND, 0.65)
    return base


def _dim_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return (max(20, color[0] // 3), max(20, color[1] // 3), max(24, color[2] // 3))
