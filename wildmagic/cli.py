from __future__ import annotations

import argparse
from pathlib import Path
import random
import sys

from .actions import GameSession
from .character import CREATION_POINTS, ORIGINS, STAT_CAP, STATS, build_profile
from .models import CharacterProfile
from .replay import save_replay


def _parse_point_spend(raw: str) -> dict[str, int]:
    """Parse a 'vigor 2 composure 1' style allocation into {stat: points}. Forgiving:
    unrecognized tokens are skipped; validation happens in build_profile."""
    tokens = raw.replace(",", " ").split()
    spend: dict[str, int] = {}
    i = 0
    while i < len(tokens) - 1:
        stat = tokens[i].lower()
        try:
            spend[stat] = spend.get(stat, 0) + int(tokens[i + 1])
            i += 2
        except ValueError:
            i += 1
    return spend


def _match_origin(origins: list, raw: str):
    """Resolve a menu entry (a number, or a substring of the origin's name) to an
    Origin, or None if nothing matches."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return origins[int(raw) - 1]
    except (ValueError, IndexError):
        matches = [o for o in origins if raw.lower() in o.name.lower()]
        return matches[0] if matches else None


def _print_origin_menu(origins: list) -> None:
    """The roster of ready-made characters, each with its derived HP/MP and baseline
    stats, so a player can pick one to play as-is."""
    for index, origin in enumerate(origins, 1):
        base = origin.to_profile()
        print(
            f"  {index}. {origin.name} ({origin.tradition}) — "
            f"HP {base.derive_max_hp()}, MP {base.derive_max_mana()} | "
            f"vigor {base.vigor} / attunement {base.attunement} / "
            f"composure {base.composure}"
        )
        print(f"       {origin.blurb}")


def _customize_character(origins: list) -> CharacterProfile:
    """The guided build: pick an origin, spend points, fill in the free-form fields."""
    raw = input("Origin (number, or Enter for a random one)> ").strip()
    origin = _match_origin(origins, raw) or random.choice(origins)
    print(f"  -> {origin.name}")

    base = origin.to_profile()
    print(
        f"\n{CREATION_POINTS} points to add across {', '.join(STATS)} "
        f"(cap {STAT_CAP} each)."
    )
    print(
        f"Baseline — vigor {base.vigor}, attunement {base.attunement}, "
        f"composure {base.composure}."
    )
    print("Enter like 'vigor 2 composure 1', or blank to keep the baseline.")
    spend: dict[str, int] = {}
    while True:
        raw = input("points> ").strip()
        if not raw:
            spend = {}
            break
        spend = _parse_point_spend(raw)
        try:
            build_profile(origin.id, spend)  # validate the spend only
            break
        except ValueError as exc:
            print(f"  {exc}. Try again.")

    print("\nFree-form (Enter to accept the origin's default):")
    name = input("Name (what others call you): ").strip()
    gender = input("Gender (Male/Female/anything, or blank): ").strip()
    appearance = input("Physical description: ").strip()
    backstory = input("Backstory: ").strip()
    signature = input("Magical signature: ").strip()

    profile = build_profile(
        origin.id,
        spend,
        name=name or None,
        appearance=appearance or None,
        backstory=backstory or None,
        signature=signature or None,
    )
    profile.gender = gender
    return profile


def prompt_character_creation() -> CharacterProfile | None:
    """The new-game character screen. Shows the ready-made characters and lets the
    player pick one by number, 'customize' to build their own, or Enter for a random
    wild mage (returns None so the engine rolls a default)."""
    origins = list(ORIGINS.values())
    print("\n=== Character Creation ===")
    print("Your wild mage — pick one to play, or shape your own:\n")
    _print_origin_menu(origins)
    print(
        "\nEnter a number to play that character as-is, 'customize' to adjust "
        "stats and details,\nor press Enter for a random wild mage."
    )
    choice = input("creation> ").strip()
    if not choice:
        return None
    if choice.lower() in {"customize", "custom", "c"}:
        return _customize_character(origins)
    origin = _match_origin(origins, choice)
    if origin is None:
        print("  (unrecognized choice — rolling a random wild mage)")
        return None
    print(f"  -> {origin.name}")
    return origin.to_profile()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play Wild Magic from the terminal.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--scenario",
        default="dungeon",
        choices=[
            "dungeon",
            "test_chamber",
            "empire_compound",
            "frontier",
            "town",
            "bazaar",
            "warren",
            "archive",
        ],
    )
    parser.add_argument("--provider", default=None, choices=["auto", "mock", "ollama"])
    parser.add_argument(
        "--record", type=Path, default=None, help="Write a replay JSON file at exit."
    )
    parser.add_argument(
        "--script", type=Path, default=None, help="Read commands from a text file."
    )
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="Run one command. Can be passed more than once.",
    )
    parser.add_argument(
        "--no-render", action="store_true", help="Only print command results."
    )
    parser.add_argument(
        "--quickstart",
        action="store_true",
        help="Skip character creation and begin as a random wild mage.",
    )
    args = parser.parse_args(argv)

    commands = load_commands(args)
    interactive = not commands and sys.stdin.isatty()

    # Character creation runs only for an interactive new game; scripted/piped runs
    # and --quickstart fall through to a random default profile so nothing blocks.
    character = None
    if interactive and not args.quickstart:
        character = prompt_character_creation()

    session = GameSession(
        seed=args.seed,
        scenario=args.scenario,
        provider_name=args.provider,
        character=character,
    )
    try:
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
    equipment_view = session.equipment_inventory_view()
    inventory = (
        ", ".join(
            f"{item['name']} x{item['quantity']}" for item in equipment_view["items"]
        )
        or "empty"
    )
    equipment = (
        ", ".join(
            f"{slot['slot']}: {slot['item']}" + (" [focus]" if slot["focused"] else "")
            for slot in equipment_view["slots"]
            if slot["occupied"]
        )
        or "none"
    )
    curses = ", ".join(curse.name for curse in state.curses.values()) or "none"
    if state.scenario == "frontier":
        location = f"Zone ({state.zone_x},{state.zone_y}) [{state.zone_type}]"
    else:
        location = f"Depth {state.depth}/{state.max_depth}"
    footer = [
        "",
        f"Turn {state.turn} | {state.clock_label()} | {location} | HP {player.hp}/{player.max_hp} | MP {player.mana}/{player.max_mana} | XP {state.experience}",
        f"Gold: {equipment_view['gold']}",
        f"Equipment: {equipment}",
        f"Inventory: {inventory}",
        f"Curses: {curses}",
        f"Standing: {standing_summary(state)}",
        "Recent log:",
        *[f"  {message}" for message in state.messages[-6:]],
    ]
    return "\n".join(lines + footer)


def standing_summary(state) -> str:
    """A compact one-line standing readout for the CLI footer: each power's non-zero
    standing axes (the GUI shows the same in draw_standing; full detail via 'standing')."""
    factions = [
        faction
        for faction in state.faction_ledger.factions.values()
        if any(faction.standing.values())
    ]
    if not factions:
        return "unknown to the powers"
    parts = []
    for faction in sorted(factions, key=lambda f: f.id):
        axes = ", ".join(
            f"{axis} {value:+.1f}"
            for axis, value in sorted(faction.standing.items())
            if value
        )
        parts.append(f"{faction.name} ({axes})")
    return " | ".join(parts)


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
    for entity in sorted(
        drawable_entities, key=lambda item: (item.kind == "player", item.kind != "item")
    ):
        revealed = "revealed" in entity.statuses
        if engine.in_bounds(entity.x, entity.y) and (
            entity.id == state.player_id
            or engine.is_visible(entity.x, entity.y)
            or revealed
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
