from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .actions import GameSession
from .replay import save_replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play Wild Magic from the terminal.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--scenario", default="dungeon", choices=["dungeon", "test_chamber", "empire_compound", "frontier", "town"])
    parser.add_argument("--provider", default=None, choices=["auto", "mock", "ollama"])
    parser.add_argument("--record", type=Path, default=None, help="Write a replay JSON file at exit.")
    parser.add_argument("--script", type=Path, default=None, help="Read commands from a text file.")
    parser.add_argument("--command", action="append", default=[], help="Run one command. Can be passed more than once.")
    parser.add_argument("--no-render", action="store_true", help="Only print command results.")
    args = parser.parse_args(argv)

    session = GameSession(seed=args.seed, scenario=args.scenario, provider_name=args.provider)
    try:
        commands = load_commands(args)
        interactive = not commands and sys.stdin.isatty()

        if interactive and not args.no_render:
            print(render_screen(session))

        if commands:
            for command in commands:
                if not command.strip() or command.lstrip().startswith("#"):
                    continue
                result = session.execute_command(command)
                print(f"> {command}")
                for message in result.messages:
                    print(message)
                if not args.no_render:
                    print(render_screen(session))
                if result.should_quit:
                    break
        elif interactive:
            while True:
                try:
                    command = input("wildmagic> ")
                except EOFError:
                    break
                result = session.execute_command(command)
                for message in result.messages:
                    print(message)
                if not args.no_render:
                    print(render_screen(session))
                if result.should_quit:
                    break
        else:
            for command in sys.stdin:
                command = command.strip()
                if not command or command.startswith("#"):
                    continue
                result = session.execute_command(command)
                print(f"> {command}")
                for message in result.messages:
                    print(message)
                if result.should_quit:
                    break

        if args.record is not None:
            save_replay(session, args.record)
            print(f"Replay saved to {args.record}")
    finally:
        session.close()
    return 0


def load_commands(args: argparse.Namespace) -> list[str]:
    commands: list[str] = []
    if args.script is not None:
        commands.extend(args.script.read_text(encoding="utf-8").splitlines())
    commands.extend(args.command)
    return commands


def render_screen(session: GameSession) -> str:
    lines = render_map(session)
    state = session.engine.state
    player = state.player
    inventory = ", ".join(f"{name} x{amount}" for name, amount in sorted(state.inventory.items())) or "empty"
    curses = ", ".join(curse.name for curse in state.curses.values()) or "none"
    if state.scenario == "frontier":
        location = f"Zone ({state.zone_x},{state.zone_y}) [{state.zone_type}]"
    else:
        location = f"Depth {state.depth}/{state.max_depth}"
    footer = [
        "",
        f"Turn {state.turn} | {location} | HP {player.hp}/{player.max_hp} | MP {player.mana}/{player.max_mana}",
        f"Inventory: {inventory}",
        f"Curses: {curses}",
        "Recent log:",
        *[f"  {message}" for message in state.messages[-6:]],
    ]
    return "\n".join(lines + footer)


def render_map(session: GameSession) -> list[str]:
    engine = session.engine
    state = engine.state
    rows: list[list[str]] = []
    for y, row in enumerate(state.tiles):
        rendered_row: list[str] = []
        for x, tile in enumerate(row):
            if not engine.is_explored(x, y):
                rendered_row.append(" ")
            elif not engine.is_visible(x, y):
                rendered_row.append(dim_tile(tile))
            else:
                rendered_row.append(tile)
        rows.append(rendered_row)
    drawable_entities = [
        entity
        for entity in state.entities.values()
        if entity.kind != "item" or entity.alive
    ]
    for entity in sorted(drawable_entities, key=lambda item: (item.kind == "player", item.kind != "item")):
        revealed = "revealed" in entity.statuses
        if engine.in_bounds(entity.x, entity.y) and (
            entity.id == state.player_id or engine.is_visible(entity.x, entity.y) or revealed
        ):
            rows[entity.y][entity.x] = entity.char
    return ["".join(row) for row in rows]


def dim_tile(tile: str) -> str:
    if tile == "#":
        return "#"
    if tile == ".":
        return ","
    return tile.lower()


if __name__ == "__main__":
    raise SystemExit(main())
