from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import time
import traceback
from typing import Any, Protocol

from .actions import (
    DIRECTIONS,
    STANDARD_SPELLS,
    ActionResult,
    GameSession,
    describe_state,
    split_command,
)
from .config import (
    get_agent_model,
    ollama_agent_num_predict,
    ollama_agent_temperature,
    ollama_json_format_enabled,
    ollama_keep_alive,
    ollama_num_ctx,
    ollama_num_gpu,
    ollama_thinking_enabled,
    ollama_timeout_seconds,
    ollama_host,
    set_runtime_config_value,
)
from .cli import render_map
from .llm_client import _post_ollama_chat, strip_thinking
from .models import BLOCKING_TILES, DOOR, OPEN_DOOR, TILE_NAMES
from .replay import save_replay


PERSONAS = ("cautious", "wild", "stress")
SCENARIOS = (
    "dungeon",
    "dungeon",
    "dungeon",
    "test_chamber",
    "town",
    "bazaar",
    "warren",
    "archive",
)
CARDINAL_DIRECTIONS = {
    "north": (0, -1),
    "south": (0, 1),
    "east": (1, 0),
    "west": (-1, 0),
}
EXPEDITION_DIRECTIONS = ("north", "east", "south", "west")
MAX_RANDOM_SEED_BASE = 2_147_483_647
SPELL_FOCI = (
    "enemy control or status effects",
    "terrain or battlefield reshaping",
    "summoning allies or distractions",
    "light, reveal, sensing, or information magic",
    "object transformation or item interaction",
    "delayed consequences, bargains, or curses",
    "movement, escape, pushing, pulling, or repositioning",
    "doors, thresholds, locks, passages, or barriers",
    "water, mist, ice, steam, or extinguishing hazards",
    "fire, heat, smoke, ash, or controlled burning",
    "plants, roots, webs, bindings, or growth",
    "stone, metal, glass, crystal, or brittle surfaces",
    "sound, silence, echoes, vibration, or distraction",
    "shadows, invisibility, concealment, or misdirection",
    "weather, wind, pressure, gravity, or falling",
    "time delay, countdowns, stored effects, or future debt",
    "memory, dreams, names, fear, courage, or morale",
    "healing, shielding, cleansing, or protective tradeoffs",
    "resistances, weaknesses, vulnerability, or elemental tags",
    "faction, attitude, pacification, fear, or alliance shifts",
    "items, inventory, equipment, tools, or consumables",
    "books, writing, maps, symbols, or readable objects",
    "secrets, clues, hidden rooms, traps, or reveal effects",
    "area denial, zones, clouds, hazards, or temporary terrain",
    "single-target precision, marks, pins, or disabling strikes",
    "group targeting, swarms, crowds, chains, or spreading effects",
    "summoned scouts, decoys, guards, mounts, or helpers",
    "resource exchange, sacrifice, curses, wounds, or debts",
    "environmental combos, hazard conversion, or cleanup",
    "NPC interaction, bargains, reputation, or social magic",
    "noncombat utility, traversal, sensing, or problem solving",
    "risk stress test with ambitious but bounded consequences",
)
THEMES = (
    "Focus on terrain-transformation spells this run.",
    "Use ordinary roguelike tactics and cast only when pressured.",
    "Talk to every NPC you meet before fighting anything.",
    "Cast spells involving summoned creatures when you can.",
    "Stress-test boundaries with ambitious but not instantly winning magic.",
    "Try to reach the next area while keeping yourself alive.",
)


def random_seed_base() -> int:
    return random.SystemRandom().randint(1, MAX_RANDOM_SEED_BASE)


PERSONA_GUIDANCE = {
    "cautious": (
        "Prefer survival and progress. Inspect when confused, avoid repeated wild casts at low HP or MP, "
        "use safe spells and movement to fight, and retreat or heal when hurt."
    ),
    "wild": (
        "Cast creative spells often, but still play to progress. Use varied spell ideas, then move or inspect "
        "to observe consequences instead of recasting the same phrase."
    ),
    "stress": (
        "Probe edge cases with ambitious commands, but keep the episode moving. Mix risky spells with movement, "
        "inspection, and ordinary actions so the harness sees varied state."
    ),
}

AGENT_SYSTEM_PROMPT = """
You are an autonomous QA player for Wild Magic, a turn-based ASCII roguelike.

Your job is to play competently enough to keep the simulation moving while generating varied
coverage. The engine and invariant checker decide confirmed bugs; your notes are only leads.

Coverage goals:
- Exercise wild magic regularly. Cast varied, concrete spell ideas that affect enemies,
  terrain, objects, allies, light, status effects, summoning, or delayed events. After a
  wild spell, inspect or move around to observe what changed before casting the same kind
  of spell again.
- Engage enemies instead of wandering past them forever. If an enemy is visible or adjacent,
  use ordinary attacks/movement, spark/frost/ward/heal, or wild spells to fight, control,
  evade, summon help, or reshape the battlefield.
- Explore rooms deliberately. Move into unexplored/open space, open doors, pick up useful
  items, descend or ascend when ready, and use the map/adjacent table instead of choosing
  random directions. Each run has an expedition_direction; after local interactions, resume
  generally traveling that way. If that direction is blocked, use nearby open doors/rooms or
  a side-step until you can make progress that way again.
- Test world-interaction systems. Use inspect/status to understand state; journal to review
  persistent rumors/promises; examine/study/observe to materialize room lore; investigate/search
  rooms, props, clues, or secrets; read books or readable objects once; talk/speak/say to NPCs;
  wares/browse/shop near merchants; accept or reject real trade offers; use/equip/unequip/drop
  items when inventory suggests it.
- Do not treat movement as the default answer. If the last few commands were mostly movement,
  choose a different useful system command such as inspect, examine, investigate, talk, read,
  pickup, a standard spell, or a wild spell. If the last few commands were mostly spells and
  no enemy or urgent object remains, stop casting and return to exploration.
- Exercise the living-world systems. Your deeds (killing imperial soldiers, raising the dead,
  razing buildings, freeing or harming townsfolk) build a legend the world reacts to - but the
  world only updates once per day at 05:00. Periodically "rest until dawn" to let a day pass so
  consequences land, then check "standing" (how the powers regard you, and whether the Empire's
  defenses are weakening) and "followers" (who has come to follow you). On empire_compound,
  fight the imperial soldiers to build a defiant legend and pressure the Empire. Once you have a
  notable legend, "found" an organization of your own (e.g. found the Ashen Hand) and rest more
  to see who pledges to it. Do not rest many times in a row with nothing changing between rests.
- Descend (downstairs) into the dungeon to explore it. If you find people locked in cells,
  stand beside one and "free" them - freeing captives is a memorable deed; some take up arms
  and come to follow you, and a grateful captive may tell you where something worth finding lies.

Command meanings:
- inspect/status/inventory: free state summary; use when unsure what is nearby or what changed.
- journal/rumors/promises: free persistent world-memory summary.
- examine/study/observe: study the current room; may create durable room lore and usually costs time.
- investigate/search [target]: search the room or a named clue/prop/secret more deeply; costs time.
- read [target]: read a nearby book/readable object; first read can create durable text, rereads are free.
- talk/speak/say your own message: converse with an adjacent NPC; can create lore or trade opportunities.
- wares/browse/shop: inspect merchant goods when near a trading NPC.
- pickup/drop/use/equip/unequip: exercise inventory, consumable, and equipment systems.
- standing/followers: free readouts of your reputation, legend, Empire pressure, and retinue.
- found a name: raise your own organization; followers who believe in your cause may pledge to it.
- rest / rest until dawn: pass time and let the world's daily 05:00 events run (deeds become
  consequences, the Empire and resistance react, bonds shift). Rest, then read standing/followers.
- spark/frost/heal/ward/reveal: deterministic standard spells for combat and survival.
- Wild magic command: begin with the word cast, then continue with a specific original
  spell in plain English. Use the current spell_focus as inspiration, but do not reuse
  instruction text, fixed examples, or stock phrases; compose fresh wording from visible
  enemies, terrain, objects, NPCs, needs, and risks.

Play rules:
- Return exactly one JSON object: {"command":"...", "note":null, "bug_suspected":false}
- Choose one legal CLI command from the command surface.
- Do not copy placeholder text like <spell idea>, <message>, <target>, or <item>. Replace
  placeholders with your own specific words based on the current situation.
- Prefer commands that change state or exercise a system: explore, fight, cast, talk, examine,
  investigate, read once, trade, inventory, descend.
- Do not repeat a command that just failed or produced "wall blocks the way".
- If several recent moves were blocked, inspect once or choose a direction marked open/enemy/door.
- Do not read the same book/title repeatedly after it has already shown its text.
- Do not cast the same spell phrase more than twice in a row; vary spell text or observe the result.
- If HP is low, heal, ward, retreat, or use an item rather than continuing a risky loop.
- If MP is low, use ordinary movement/items or wait to recover 1 MP instead of repeated wild
  casts; wild spells can charge health when mana is insufficient.
- Use the local map and adjacent direction table: north is up, east is right.
- If you suspect a bug, set bug_suspected=true and put a concise lead in note, but still choose a useful command.
- Never edit files, run shell commands, or ask for help. You are only playing through CLI commands.
""".strip()

COMMAND_SURFACE = """
Known commands: inspect, journal, wait (recover 1 MP), open, descend, ascend, move north/south/east/west,
north, south, east, west, spark, frost, heal, ward, reveal, pickup, drop an item name,
use an item name, equip an item name, unequip a slot or item name, read a nearby target,
examine, investigate a target or area, wares, accept, reject,
standing (how the Empire and the resistance regard you, your legend, and how close the
Empire's defenses are to breaking - free, no turn),
followers (who follows you and the organizations you have founded - free, no turn),
found a name (raise your own banner/organization, e.g. found the Ashen Hand),
free (stand next to someone held in a cell and free/release them - some will join you),
rest (camp to pass time; "rest" is 8 hours, "rest until dawn" sleeps to the next 05:00 -
the world's daily events only happen at 05:00, so resting is how you let the world react),
wild magic: command must start with cast and continue with specific original spell wording,
talk: command must start with talk and continue with your message to an adjacent NPC.
The words "spell idea", "message", "target", "item", and "slot" are descriptions, not
literal command text. Never include angle brackets or instruction phrases in your command.
Return JSON only: {"command":"...", "note":null, "bug_suspected":false}
"""

PLACEHOLDER_FRAGMENTS = {
    "<spell idea>",
    "<wild spell idea>",
    "<message>",
    "<target>",
    "<item>",
    "<slot>",
    "<slot_or_item>",
    "<north|south|east|west>",
}
COPIED_INSTRUCTION_COMMANDS = {
    "cast your own concrete spell idea",
    "cast your own spell idea",
    "cast a spell idea",
    "cast specific original spell wording",
    "cast specific original spell in plain english",
    "cas your own concrete spell idea",
    "cas your own spell idea",
}

EXACT_VERBS = {
    "inspect",
    "look",
    "status",
    "inventory",
    "inv",
    "i",
    "journal",
    "rumors",
    "promises",
    "examine",
    "study",
    "observe",
    "wares",
    "browse",
    "shop",
    "accept",
    "yes",
    "y",
    "reject",
    "decline",
    "no",
    "n",
    "wait",
    ".",
    "open",
    "o",
    "descend",
    "downstairs",
    ">",
    "ascend",
    "upstairs",
    "<",
    "pickup",
    "get",
    "take",
    "grab",
    # Free a bound captive on an adjacent tile (costs a turn).
    "free",
    "release",
    "liberate",
    "unbind",
    "untie",
    # Emergent-world readouts + the daily-tick controls (all free actions, no turn cost).
    "standing",
    "reputation",
    "rep",
    "factions",
    "followers",
    "retinue",
    "bonds",
    "tick",
    "simulate",
    "daytick",
    "quit",
    "exit",
}
TAIL_VERBS = {
    "cast",
    "wild",
    "talk",
    "speak",
    "say",
    "read",
    "peruse",
    "investigate",
    "search",
    "drop",
    "discard",
    "use",
    "consume",
    "drink",
    "eat",
    "equip",
    "wear",
    "wield",
    "unequip",
    "unwield",
    "remove",
    # Pass time so the world's daily Simulator runs (rest), and raise your own banner
    # (found). Optional tail: "rest until dawn" / "found the Ashen Hand", or bare.
    "rest",
    "camp",
    "sleep",
    "found",
    "establish",
}
REQUIRES_TAIL = {"cast", "wild", "talk", "speak", "say"}


@dataclass
class AgentDecision:
    command: str
    note: str | None = None
    bug_suspected: bool = False
    parse_failure: bool = False
    raw_response: str | None = None
    error: str | None = None


@dataclass
class AgentObservation:
    episode: int
    seed: int | None
    scenario: str
    persona: str
    theme: str
    step: int
    turn: int
    new_messages: list[str]
    state_lines: list[str]
    local_map: list[str] = field(default_factory=list)
    adjacent: dict[str, dict[str, Any]] = field(default_factory=dict)
    recent_commands: list[str] = field(default_factory=list)
    recent_results: list[dict[str, Any]] = field(default_factory=list)
    last_result: dict[str, Any] | None = None
    avoid_commands: list[str] = field(default_factory=list)
    expedition_direction: str | None = None
    spell_focus: str | None = None
    nudge: str | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        decision_hints_value = decision_hints(asdict(self))
        prior_casts = prior_cast_commands(self.recent_commands)
        top_context = {
            "orientation": "North is up, east is right. Use local_map and adjacent before choosing movement.",
            "turn": self.turn,
            "state_lines": self.state_lines,
            "local_map": self.local_map,
            "adjacent": self.adjacent,
            "new_messages": self.new_messages,
        }
        data: dict[str, Any] = {
            "immediate_context_read_first": top_context,
            "decision_hints": decision_hints_value,
            "episode": self.episode,
            "seed": self.seed,
            "scenario": self.scenario,
            "persona": self.persona,
            "theme": self.theme,
            "step": self.step,
            "turn": self.turn,
            "new_messages": self.new_messages,
            "state_lines": self.state_lines,
            "local_map": self.local_map,
            "adjacent": self.adjacent,
            "expedition_direction": self.expedition_direction,
            "spell_focus": self.spell_focus,
            "recent_commands_already_done": self.recent_commands,
            "recent_results": self.recent_results,
            "last_result": self.last_result,
            "prior_spells_already_cast_do_not_repeat": prior_casts,
            "avoid_commands": sorted(set([*self.avoid_commands, *prior_casts])),
            "nudge": self.nudge,
        }
        data["command_surface"] = COMMAND_SURFACE.strip()
        data["persona_guidance"] = PERSONA_GUIDANCE.get(self.persona, "")
        data["map_legend"] = (
            "@ you, # wall (cannot walk into), . floor, + door, > stairs down, < stairs up; "
            "letters are creatures/props; blank is unexplored. North is up, east is right."
        )
        data["final_action_guidance_read_last"] = [
            "Return exactly one JSON object with one useful command.",
            "Recent commands and prior spells are things you already did, not examples to copy.",
            "Do not repeat prior spell wording. If casting, invent a new spell relevant to the visible environment, current threat, spell_focus, and MP/HP risk.",
            "Prefer local progress: fight visible enemies, open/explore rooms, investigate/read/talk/pickup when available, then resume the expedition direction.",
            "If MP is empty or low and no danger is urgent, wait to recover MP instead of casting.",
        ]
        return data


def expedition_direction_for_seed(seed: int | None, episode: int = 0) -> str:
    basis = seed if seed is not None else episode
    return EXPEDITION_DIRECTIONS[basis % len(EXPEDITION_DIRECTIONS)]


def spell_focus_for_seed(seed: int | None, episode: int = 0) -> str:
    basis = (seed if seed is not None else episode) + episode
    return SPELL_FOCI[basis % len(SPELL_FOCI)]


def prior_cast_commands(commands: list[str], limit: int = 8) -> list[str]:
    casts: list[str] = []
    seen: set[str] = set()
    for command in reversed(commands):
        cleaned = " ".join(str(command).split())
        lowered = cleaned.lower()
        if not (lowered.startswith("cast ") or lowered.startswith("wild ")):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        casts.append(cleaned)
        if len(casts) >= limit:
            break
    return list(reversed(casts))


@dataclass
class Finding:
    tier: int
    kind: str
    episode: int
    seed: int | None
    scenario: str
    turn: int
    evidence: dict[str, Any]
    replay_path: str | None = None
    command_path: str | None = None


@dataclass
class StepRecord:
    episode: int
    step: int
    seed: int | None
    scenario: str
    persona: str
    theme: str
    command: str
    agent: dict[str, Any]
    result: dict[str, Any] | None
    messages: list[str]
    observation: dict[str, Any]
    violations: list[dict[str, Any]]
    elapsed_seconds: float
    agent_seconds: float = 0.0


@dataclass
class EpisodeSummary:
    episode: int
    seed: int | None
    scenario: str
    persona: str
    theme: str
    steps: int
    turns: int
    casts: int
    parse_failures: int
    findings: int
    completion_reason: str
    replay_path: str
    command_path: str
    log_path: str
    notes: list[str] = field(default_factory=list)
    cast_technical_failures: int = 0
    canon_technical_failures: int = 0
    rejected_casts: int = 0
    command_counts: dict[str, int] = field(default_factory=dict)
    agent_summary: str | None = None


@dataclass
class CampaignConfig:
    episodes: int | None = None
    hours: float | None = None
    max_turns: int = 120
    max_steps: int | None = None
    episode_minutes: float = 15.0
    scenarios: list[str] = field(default_factory=lambda: list(SCENARIOS))
    personas: list[str] = field(default_factory=lambda: list(PERSONAS))
    seed_base: int = field(default_factory=random_seed_base)
    provider: str | None = "mock"
    agent: str = "stub"
    out: Path = Path("logs/autoplay")
    run_id: str | None = None
    drain_background: bool = False
    stub_commands: list[str] = field(default_factory=list)


class PlayerAgent(Protocol):
    name: str

    def choose(self, observation: AgentObservation) -> AgentDecision: ...


class StubAgent:
    name = "stub"

    def __init__(self, commands: list[str] | None = None) -> None:
        self.commands = commands or [
            "inspect",
            "move east",
            "wait",
            "cast bind the nearest enemy in sticky blue webbing",
            "spark",
            "wait",
        ]
        self.index = 0

    def choose(self, observation: AgentObservation) -> AgentDecision:
        command = self.commands[self.index % len(self.commands)]
        self.index += 1
        return AgentDecision(command=command)


class RandomAgent:
    name = "random"

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.commands = [
            "inspect",
            "wait",
            "open",
            "move north",
            "move south",
            "move east",
            "move west",
            "spark",
            "heal",
            "cast bind the nearest enemy in sticky blue webbing",
            "cast summon a friendly brass moth that bites enemies",
            "cast turn the floor between me and the enemy into slick ice",
            "cast reveal the nearest creature by making its shadow glow blue",
        ]

    def choose(self, observation: AgentObservation) -> AgentDecision:
        command = self.rng.choice(self.commands)
        return AgentDecision(command=command)


class OllamaAgent:
    name = "ollama"

    def __init__(self) -> None:
        self.model = get_agent_model()
        self.base_url = ollama_host("agent")
        self.timeout_seconds = ollama_timeout_seconds("agent")

    def choose(self, observation: AgentObservation) -> AgentDecision:
        error: str | None = None
        raw: str | None = None
        for attempt in range(2):
            messages = self._messages(observation, error)
            payload: dict[str, Any] = {
                "model": self.model,
                "stream": False,
                "messages": messages,
                "think": ollama_thinking_enabled("agent"),
                "options": {
                    "temperature": ollama_agent_temperature(),
                    "num_predict": ollama_agent_num_predict(),
                    "num_ctx": ollama_num_ctx("agent"),
                    "num_gpu": ollama_num_gpu("agent"),
                },
                "keep_alive": ollama_keep_alive("agent"),
            }
            if ollama_json_format_enabled("agent"):
                payload["format"] = "json"
            try:
                data = _post_ollama_chat(self.base_url, payload, self.timeout_seconds)
                raw = str(data.get("message", {}).get("content", ""))
                return parse_agent_response(raw)
            except Exception as exc:
                error = str(exc)
        return AgentDecision(
            command="wait",
            note="agent output failed to parse; falling back to wait",
            parse_failure=True,
            raw_response=raw,
            error=error,
        )

    def summarize(
        self, persona: str, theme: str, turns: int, notes: list[str]
    ) -> str | None:
        prompt = {
            "persona": persona,
            "theme": theme,
            "turns_played": turns,
            "your_notes": notes[-40:],
            "instruction": (
                "You just finished a playtest episode. In 3-6 plain sentences, summarize "
                "the problems you saw and what felt unsatisfying. These are unverified "
                "impressions, not confirmed bugs. Respond with prose, not JSON."
            ),
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an autonomous QA playtester for Wild Magic summarizing an episode.",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            "think": ollama_thinking_enabled("agent"),
            "options": {
                "temperature": ollama_agent_temperature(),
                "num_predict": ollama_agent_num_predict(),
                "num_ctx": ollama_num_ctx("agent"),
                "num_gpu": ollama_num_gpu("agent"),
            },
            "keep_alive": ollama_keep_alive("agent"),
        }
        try:
            data = _post_ollama_chat(self.base_url, payload, self.timeout_seconds)
            summary = strip_thinking(
                str(data.get("message", {}).get("content", ""))
            ).strip()
            return summary or None
        except Exception:
            return None

    def _messages(
        self, observation: AgentObservation, error: str | None
    ) -> list[dict[str, str]]:
        system = AGENT_SYSTEM_PROMPT + "\n\n" + COMMAND_SURFACE.strip()
        payload = observation.to_prompt_dict()
        if error:
            payload["previous_error"] = error
            payload["repair_instruction"] = (
                "Return exactly one JSON object with a valid command."
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
        ]


def local_map_view(
    session: GameSession, radius_x: int = 10, radius_y: int = 6
) -> list[str]:
    rows = render_map(session)
    player = session.engine.state.player
    top = max(0, player.y - radius_y)
    bottom = min(len(rows), player.y + radius_y + 1)
    left = max(0, player.x - radius_x)
    view: list[str] = []
    for y in range(top, bottom):
        row = rows[y]
        view.append(row[left : min(len(row), player.x + radius_x + 1)])
    return view


def truncate_text(text: str, limit: int = 360) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def compact_messages(messages: list[str], limit: int = 8) -> list[str]:
    return [truncate_text(message) for message in messages[-limit:]]


def adjacent_options(session: GameSession) -> dict[str, dict[str, Any]]:
    engine = session.engine
    player = engine.state.player
    options: dict[str, dict[str, Any]] = {}
    for direction, (dx, dy) in CARDINAL_DIRECTIONS.items():
        x = player.x + dx
        y = player.y + dy
        info: dict[str, Any] = {"x": x, "y": y}
        if not engine.in_bounds(x, y):
            info.update({"status": "edge", "reason": "out of bounds"})
            options[direction] = info
            continue
        tile = engine.tile_at(x, y)
        entity = engine.blocking_entity_at(x, y)
        info["tile"] = tile
        info["tile_name"] = TILE_NAMES.get(tile, tile)
        if entity is not None:
            info["entity"] = {
                "id": entity.id,
                "name": entity.name,
                "kind": entity.kind,
                "faction": entity.faction,
                "hp": entity.hp,
            }
            if entity.faction == "enemy":
                info["status"] = "enemy"
                info["suggested_command"] = f"move {direction}"
            else:
                info["status"] = "blocked"
                info["reason"] = f"{entity.name} is in the way"
        elif tile == DOOR:
            info["status"] = "door"
            info["suggested_command"] = "open"
        elif tile == OPEN_DOOR:
            info["status"] = "open"
            info["suggested_command"] = f"move {direction}"
        elif tile in BLOCKING_TILES:
            info["status"] = "blocked"
            info["reason"] = f"{info['tile_name']} blocks movement"
        else:
            info["status"] = "open"
            info["suggested_command"] = f"move {direction}"
        options[direction] = info
    return options


def result_summary(result: ActionResult, message_limit: int = 3) -> dict[str, Any]:
    return {
        "command": result.command,
        "action": result.action,
        "success": result.success,
        "consumed_turn": result.consumed_turn,
        "technical_failure": result.technical_failure,
        "turn_before": result.turn_before,
        "turn_after": result.turn_after,
        "messages": compact_messages(result.messages, limit=message_limit),
    }


def avoid_commands_from_history(
    history: list[str],
    recent_results: list[dict[str, Any]],
    repeated_command: str,
    repeated_count: int,
) -> list[str]:
    avoid: list[str] = []
    if repeated_command and repeated_count >= 2:
        avoid.append(repeated_command)
    if recent_results:
        last = recent_results[-1]
        messages = " ".join(
            str(message).lower() for message in last.get("messages", [])
        )
        if (
            last.get("success") is False
            or "blocks the way" in messages
            or "unknown command" in messages
        ):
            command = str(last.get("command") or "").strip()
            if command:
                avoid.append(command)
    for command in history[-4:]:
        if history[-4:].count(command) >= 3:
            avoid.append(command)
    return sorted(set(avoid))


def decision_hints(prompt_data: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    adjacent = prompt_data.get("adjacent") or {}
    state_text = "\n".join(str(line) for line in prompt_data.get("state_lines") or [])
    mp_match = re.search(r"\bMP\s+(\d+)/(\d+)", state_text)
    if mp_match:
        mana = int(mp_match.group(1))
        max_mana = int(mp_match.group(2))
        if mana <= 0:
            hints.append(
                "MP is empty: wild spells with mana costs will take HP. Use wait to recover 1 MP."
            )
        elif mana < max_mana:
            hints.append(
                "Waiting recovers 1 MP; consider waiting before more wild casting if no enemy is urgent."
            )
    spell_focus = str(prompt_data.get("spell_focus") or "").strip()
    if spell_focus:
        hints.append(
            f"Current spell focus: {spell_focus}. If casting, invent a fresh situation-specific phrase."
        )
    expedition_direction = (
        str(prompt_data.get("expedition_direction") or "").strip().lower()
    )
    if expedition_direction:
        info = (
            adjacent.get(expedition_direction) if isinstance(adjacent, dict) else None
        )
        if isinstance(info, dict):
            status = info.get("status")
            suggested = info.get("suggested_command")
            if status in {"open", "door", "enemy"} and suggested:
                hints.append(
                    f"Run heading is {expedition_direction}; after local interactions, prefer `{suggested}` to keep exploring."
                )
            elif status == "blocked":
                hints.append(
                    f"Run heading is {expedition_direction}, but it is blocked now; use a side route, door, investigation, or room exit to regain that heading."
                )
        else:
            hints.append(
                f"Run heading is {expedition_direction}; resume that general direction after local interactions."
            )
    open_dirs = [
        direction
        for direction, info in adjacent.items()
        if isinstance(info, dict) and info.get("status") in {"open", "enemy", "door"}
    ]
    if open_dirs:
        hints.append("Useful directions/actions now: " + ", ".join(sorted(open_dirs)))
    blocked_dirs = [
        direction
        for direction, info in adjacent.items()
        if isinstance(info, dict) and info.get("status") == "blocked"
    ]
    if blocked_dirs:
        hints.append(
            "Avoid blocked directions unless intentionally attacking an enemy: "
            + ", ".join(sorted(blocked_dirs))
        )
    avoid = prompt_data.get("avoid_commands") or []
    if avoid:
        hints.append(
            "Do not choose these repeated/failed commands now: "
            + ", ".join(str(command) for command in avoid)
        )
    last = prompt_data.get("last_result") or {}
    if isinstance(last, dict) and last.get("success") is False:
        hints.append(
            "The last command failed; choose a different command that changes position, state, or information."
        )
    if prompt_data.get("nudge"):
        hints.append(str(prompt_data["nudge"]))
    return hints


def wild_rejected(result: ActionResult) -> bool:
    record = result.wild_magic or {}
    data = record.get("data")
    return isinstance(data, dict) and data.get("accepted") is False


def parse_agent_response(raw: str) -> AgentDecision:
    cleaned = strip_thinking(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("agent response was not a JSON object")
    command = validate_agent_command(str(parsed.get("command") or ""))
    note_value = parsed.get("note")
    note = None if note_value is None else str(note_value).strip() or None
    return AgentDecision(
        command=command,
        note=note,
        bug_suspected=bool(parsed.get("bug_suspected")),
        raw_response=raw,
    )


def validate_agent_command(command: str) -> str:
    original = command.strip()
    if not original:
        raise ValueError("command is empty")
    lowered = original.lower()
    if any(fragment in lowered for fragment in PLACEHOLDER_FRAGMENTS):
        raise ValueError(
            "replace placeholder text with a specific command, such as a concrete spell idea or target"
        )
    if lowered in COPIED_INSTRUCTION_COMMANDS:
        raise ValueError(
            "do not copy spell instructions; write a specific original spell after cast"
        )
    tokens = split_command(original)
    if not tokens:
        raise ValueError("command is empty")
    verb = tokens[0].lower()
    if verb in DIRECTIONS:
        return original
    if verb in {"move", "go"}:
        direction = tokens[1].lower() if len(tokens) > 1 else ""
        if direction not in DIRECTIONS:
            raise ValueError(f"unknown move direction: {direction or '(missing)'}")
        return original
    if verb in STANDARD_SPELLS or verb == "f":
        return original
    if verb in TAIL_VERBS:
        if verb in REQUIRES_TAIL and len(tokens) < 2:
            raise ValueError(f"{verb} requires text after the verb")
        return original
    if verb in EXACT_VERBS:
        return original
    raise ValueError(f"unknown command verb: {verb}")


class InvariantChecker:
    def check(
        self, session: GameSession, result: ActionResult, episode: int
    ) -> list[Finding]:
        findings: list[Finding] = []
        base = {
            "episode": episode,
            "seed": session.seed,
            "scenario": session.scenario,
            "turn": result.turn_after,
        }
        if result.technical_failure and result.turn_after != result.turn_before:
            findings.append(
                Finding(
                    tier=1,
                    kind="technical_failure_consumed_turn",
                    evidence=result.to_record(),
                    **base,
                )
            )
        if wild_rejected(result) and not result.consumed_turn:
            findings.append(
                Finding(
                    tier=1,
                    kind="rejected_spell_did_not_consume_turn",
                    evidence=result.to_record(),
                    **base,
                )
            )
        # A backward turn counter is always a bug. A forward jump of more than one turn is a
        # bug too, EXCEPT for actions that legitimately consume many turns: resting/camping
        # skips hours (a full day = TURNS_PER_DAY turns) and investigating costs 1-3 turns.
        # Without this exemption, wiring the agent to "rest" (the only way to reach the daily
        # Simulator) would flag a confirmed bug on every rest.
        multi_turn_action = result.action in {"rest", "investigate"}
        if result.turn_after < result.turn_before or (
            not multi_turn_action and result.turn_after - result.turn_before > 1
        ):
            findings.append(
                Finding(
                    tier=1,
                    kind="turn_counter_jump",
                    evidence=result.to_record(),
                    **base,
                )
            )
        for error in session.engine.validate_state():
            findings.append(
                Finding(
                    tier=1,
                    kind="state_validation_error",
                    evidence={"error": error, "result": result.to_record()},
                    **base,
                )
            )
        for entity in session.engine.state.entities.values():
            if not entity.alive or not entity.blocks:
                continue
            if entity.kind not in {"player", "actor", "npc"}:
                continue
            if not session.engine.in_bounds(entity.x, entity.y):
                continue
            tile = session.engine.tile_at(entity.x, entity.y)
            if tile in BLOCKING_TILES:
                findings.append(
                    Finding(
                        tier=1,
                        kind="blocking_actor_on_blocking_tile",
                        evidence={
                            "entity": entity.to_public_dict(),
                            "tile": tile,
                            "tile_name": TILE_NAMES.get(tile, tile),
                        },
                        **base,
                    )
                )
        if (
            result.action in {"cast", "talk", "examine", "read", "investigate"}
            and result.consumed_turn
            and not result.messages
        ):
            findings.append(
                Finding(
                    tier=2,
                    kind="turn_consumed_without_messages",
                    evidence=result.to_record(),
                    **base,
                )
            )
        return findings


class EpisodeRunner:
    def __init__(
        self,
        config: CampaignConfig,
        run_dir: Path,
        episode_index: int,
        seed: int | None,
        scenario: str,
        persona: str,
        theme: str,
        agent: PlayerAgent,
    ) -> None:
        self.config = config
        self.run_dir = run_dir
        self.episode_index = episode_index
        self.seed = seed
        self.scenario = scenario
        self.persona = persona
        self.theme = theme
        self.agent = agent
        self.checker = InvariantChecker()
        self.command_history: list[str] = []
        self.notes: list[str] = []
        self.findings: list[Finding] = []
        self.step_count = 0
        self.expedition_direction = expedition_direction_for_seed(seed, episode_index)
        self.spell_focus = spell_focus_for_seed(seed, episode_index)
        stem = f"episode_{episode_index:03d}"
        self.step_path = run_dir / f"{stem}.jsonl"
        self.replay_path = run_dir / f"{stem}.replay.json"
        self.command_path = run_dir / f"{stem}.commands.txt"

    def run(self) -> EpisodeSummary:
        session = GameSession(
            seed=self.seed, scenario=self.scenario, provider_name=self.config.provider
        )
        completion_reason = "max_steps"
        start = time.time()
        last_messages = ["Episode started."]
        recent_results: list[dict[str, Any]] = []
        self._recent_positions: deque[tuple[int, int]] = deque(maxlen=12)
        self._last_autorest_step = -25
        nudge: str | None = None
        parse_failures = 0
        repeated_command = ""
        repeated_count = 0
        turns = 0
        casts = 0
        cast_technical_failures = 0
        canon_technical_failures = 0
        rejected_casts = 0
        command_counts: dict[str, int] = {}
        # Agent steps (decisions) are the per-episode budget, not in-game turns: a single
        # "rest until dawn" advances the turn counter by a full day (TURNS_PER_DAY), so a
        # turn-based cap would end the episode on the first rest — and resting is exactly how
        # the agent reaches the daily 05:00 Simulator. ``--max-turns`` sets the step budget
        # (1 step == 1 turn for ordinary play); ``--max-steps`` overrides it explicitly.
        max_steps = self.config.max_steps or max(1, self.config.max_turns)
        try:
            while self.step_count < max_steps:
                if time.time() - start >= self.config.episode_minutes * 60:
                    completion_reason = "episode_wall_clock"
                    break
                if session.engine.state.game_over:
                    completion_reason = self._game_over_reason(session)
                    break
                if self.config.drain_background:
                    self._drain_background(session)
                # Surface a concrete in-reach opportunity (chiefly captives the agent can't
                # otherwise know to seek) unless a higher-priority nudge already stands.
                if nudge is None:
                    nudge = self._opportunity_nudge(session)
                # Periodically steer toward resting so the daily Simulator (and the whole
                # backlash/bonds/standing layer) actually gets exercised.
                if nudge is None and self.step_count % 12 == 0:
                    nudge = self._rest_nudge(session)
                # When captives are held here, point the episode's exploration drift along the
                # *pathfound* route to them (the agent already follows expedition_direction) so
                # it stops pulling the agent into walls; otherwise keep the seeded direction.
                effective_expedition = (
                    self._captive_step_dir(session) or self.expedition_direction
                )
                observation = AgentObservation(
                    episode=self.episode_index,
                    seed=self.seed,
                    scenario=self.scenario,
                    persona=self.persona,
                    theme=self.theme,
                    step=self.step_count + 1,
                    turn=session.engine.state.turn,
                    new_messages=compact_messages(last_messages),
                    state_lines=describe_state(session.engine),
                    local_map=local_map_view(session),
                    adjacent=adjacent_options(session),
                    recent_commands=self.command_history[-6:],
                    recent_results=recent_results[-6:],
                    last_result=recent_results[-1] if recent_results else None,
                    avoid_commands=avoid_commands_from_history(
                        self.command_history,
                        recent_results,
                        repeated_command,
                        repeated_count,
                    ),
                    expedition_direction=effective_expedition,
                    spell_focus=self.spell_focus,
                    nudge=nudge,
                )
                nudge = None
                agent_started = time.perf_counter()
                # Harness auto-rest: the agent reliably ignores the "rest" nudge (LLMs don't
                # act on meta-pleas), so the whole daily Simulator layer (deed application,
                # Empire pressure, backlash minting, bond drift) would never run. Periodically
                # rest *for* it when it's safe and deeds are pending — a deliberate coverage
                # action to actually exercise (and find bugs in) that layer.
                if self._should_autorest(session):
                    decision = AgentDecision(
                        command="rest until dawn",
                        note="auto-rest (harness): exercise the daily Simulator",
                    )
                    self._last_autorest_step = self.step_count
                else:
                    decision = self._choose(observation)
                agent_seconds = time.perf_counter() - agent_started
                parse_failures = parse_failures + 1 if decision.parse_failure else 0
                if parse_failures >= 3:
                    completion_reason = "agent_parse_failures"
                    break
                command = decision.command
                if command == repeated_command:
                    repeated_count += 1
                else:
                    repeated_command = command
                    repeated_count = 1
                if repeated_count == 4:
                    nudge = (
                        f"You have repeated `{command}`. Do not choose it again now; inspect, move through an open "
                        "direction, fight, descend, or cast a different spell."
                    )
                if repeated_count >= 6:
                    self.findings.append(
                        Finding(
                            tier=2,
                            kind="possible_softlock",
                            episode=self.episode_index,
                            seed=self.seed,
                            scenario=self.scenario,
                            turn=session.engine.state.turn,
                            evidence={
                                "command": command,
                                "repeat_count": repeated_count,
                            },
                        )
                    )
                    completion_reason = "possible_softlock"
                    break
                self.command_history.append(command)
                result, elapsed, exception_text = self._execute(session, command)
                self.step_count += 1
                if result is None:
                    self.findings.append(
                        Finding(
                            tier=1,
                            kind="unhandled_exception",
                            episode=self.episode_index,
                            seed=self.seed,
                            scenario=self.scenario,
                            turn=session.engine.state.turn,
                            evidence={"command": command, "traceback": exception_text},
                        )
                    )
                    self._write_step(
                        observation, decision, None, [], elapsed, agent_seconds
                    )
                    completion_reason = "exception"
                    break
                verb = (
                    split_command(command)[0].lower() if split_command(command) else ""
                )
                command_counts[verb] = command_counts.get(verb, 0) + 1
                # No-progress stall: an agent thrashing against a wall varies its move
                # direction, so it slips past the repeated-command softlock check yet goes
                # nowhere. Flag it (tier 2) when many recent steps occupy ≤2 tiles.
                self._recent_positions.append(
                    (session.engine.state.player.x, session.engine.state.player.y)
                )
                if (
                    len(self._recent_positions) == self._recent_positions.maxlen
                    and len(set(self._recent_positions)) <= 2
                ):
                    self.findings.append(
                        Finding(
                            tier=2,
                            kind="movement_stall",
                            episode=self.episode_index,
                            seed=self.seed,
                            scenario=self.scenario,
                            turn=session.engine.state.turn,
                            evidence={
                                "positions": list(self._recent_positions),
                                "last_command": command,
                            },
                        )
                    )
                    self._recent_positions.clear()
                    # Break the loop *physically*, not just with words (the agent tends to
                    # ignore a plain "you're stuck" plea and keep oscillating into a dead end
                    # under its fixed expedition_direction). Re-point exploration at a genuinely
                    # open adjacent direction other than the one it has been repeating.
                    adj = adjacent_options(session)
                    open_dirs = [
                        d
                        for d in EXPEDITION_DIRECTIONS
                        if adj.get(d, {}).get("status") in ("open", "door")
                    ]
                    repeated_dir = next(
                        (d for d in EXPEDITION_DIRECTIONS if d in command.lower()), None
                    )
                    choices = [d for d in open_dirs if d != repeated_dir] or open_dirs
                    if choices:
                        self.expedition_direction = choices[
                            self.step_count % len(choices)
                        ]
                        nudge = (
                            "You are stuck repeating moves in a dead end. Go "
                            f"{self.expedition_direction} now and keep heading that way to "
                            "reach new ground - stop going back the way you came."
                        )
                    else:
                        nudge = (
                            "You are boxed in - stop moving and do something different here "
                            "(inspect, cast a spell, or open/clear an adjacent obstacle)."
                        )
                if result.technical_failure:
                    # Casting failures (the wild resolver) and read/investigate
                    # failures (canon materialization) have different causes, so
                    # they are counted and reported separately.
                    if result.action == "cast":
                        cast_technical_failures += 1
                    else:
                        canon_technical_failures += 1
                if wild_rejected(result):
                    rejected_casts += 1
                violations = self.checker.check(session, result, self.episode_index)
                self.findings.extend(violations)
                if decision.note:
                    self.notes.append(f"turn {result.turn_after}: {decision.note}")
                if decision.bug_suspected:
                    self.findings.append(
                        Finding(
                            tier=3,
                            kind="agent_suspected_bug",
                            episode=self.episode_index,
                            seed=self.seed,
                            scenario=self.scenario,
                            turn=result.turn_after,
                            evidence={
                                "command": command,
                                "note": decision.note,
                                "raw_response": decision.raw_response,
                            },
                        )
                    )
                self._write_step(
                    observation, decision, result, violations, elapsed, agent_seconds
                )
                summary = result_summary(result)
                recent_results.append(summary)
                last_messages = result.messages or ["(no new messages)"]
                if not result.success and not result.consumed_turn:
                    nudge = (
                        f"`{result.command}` did not change the turn. Pick a different command; use the adjacent "
                        "direction table instead of retrying the same blocked action."
                    )
                recent_casts = sum(
                    1
                    for item in self.command_history[-4:]
                    if item.lower().startswith("cast ")
                )
                living_enemies = session.engine.living_enemies()
                visible_enemies = [
                    enemy
                    for enemy in living_enemies
                    if session.engine.is_visible(enemy.x, enemy.y)
                ]
                if recent_casts >= 3 and not visible_enemies:
                    nudge = (
                        f"You have cast several spells and no visible enemy remains. Resume exploration toward "
                        f"{self.expedition_direction}; use rooms, doors, stairs, investigate/read/talk, or movement."
                    )
                if result.should_quit:
                    completion_reason = "quit"
                    break
                if session.engine.state.game_over:
                    completion_reason = self._game_over_reason(session)
                    break
            else:
                completion_reason = "max_steps"
            self._write_commands()
            save_replay(session, self.replay_path)
            turns = session.engine.state.turn
            casts = sum(
                1 for record in session.records if record.get("action") == "cast"
            )
        finally:
            session.close()
        for finding in self.findings:
            finding.replay_path = str(self.replay_path)
            finding.command_path = str(self.command_path)
        agent_summary: str | None = None
        summarize = getattr(self.agent, "summarize", None)
        if callable(summarize) and self.step_count > 0:
            agent_summary = summarize(self.persona, self.theme, turns, self.notes)
        return EpisodeSummary(
            episode=self.episode_index,
            seed=self.seed,
            scenario=self.scenario,
            persona=self.persona,
            theme=self.theme,
            steps=self.step_count,
            turns=turns,
            casts=casts,
            parse_failures=parse_failures,
            findings=len(self.findings),
            completion_reason=completion_reason,
            replay_path=str(self.replay_path),
            command_path=str(self.command_path),
            log_path=str(self.step_path),
            notes=self.notes,
            cast_technical_failures=cast_technical_failures,
            canon_technical_failures=canon_technical_failures,
            rejected_casts=rejected_casts,
            command_counts=command_counts,
            agent_summary=agent_summary,
        )

    def _choose(self, observation: AgentObservation) -> AgentDecision:
        try:
            decision = self.agent.choose(observation)
            decision.command = validate_agent_command(decision.command)
            return decision
        except Exception as exc:
            return AgentDecision(
                command="wait",
                note="agent command failed validation; falling back to wait",
                parse_failure=True,
                error=str(exc),
            )

    def _execute(
        self, session: GameSession, command: str
    ) -> tuple[ActionResult | None, float, str | None]:
        started = time.perf_counter()
        try:
            result = session.execute_command(command)
            return result, time.perf_counter() - started, None
        except Exception:
            return None, time.perf_counter() - started, traceback.format_exc()

    def _write_step(
        self,
        observation: AgentObservation,
        decision: AgentDecision,
        result: ActionResult | None,
        violations: list[Finding],
        elapsed_seconds: float,
        agent_seconds: float = 0.0,
    ) -> None:
        record = StepRecord(
            episode=self.episode_index,
            step=self.step_count,
            seed=self.seed,
            scenario=self.scenario,
            persona=self.persona,
            theme=self.theme,
            command=decision.command,
            agent=asdict(decision),
            result=result.to_record() if result else None,
            messages=list(result.messages) if result else [],
            observation=observation.to_prompt_dict(),
            violations=[finding_to_record(finding) for finding in violations],
            elapsed_seconds=round(elapsed_seconds, 4),
            agent_seconds=round(agent_seconds, 4),
        )
        append_jsonl(self.step_path, asdict(record))

    def _write_commands(self) -> None:
        self.command_path.write_text(
            "\n".join(self.command_history) + ("\n" if self.command_history else ""),
            encoding="utf-8",
        )

    def _nearest_captive(self, session: GameSession):
        """The nearest living bound captive to the player, or None."""
        engine = session.engine
        player = engine.state.player
        captives = [
            e
            for e in engine.state.entities.values()
            if e.kind == "npc" and "bound" in e.tags and e.hp > 0
        ]
        if not captives:
            return None
        return min(
            captives, key=lambda c: max(abs(c.x - player.x), abs(c.y - player.y))
        )

    _VEC_TO_CARDINAL = {
        (0, -1): "north",
        (0, 1): "south",
        (1, 0): "east",
        (-1, 0): "west",
    }

    def _compass_to(self, session: GameSession, target) -> str:
        """Straight-line 8-way compass word from the player to a target, for prose."""
        player = session.engine.state.player
        dx, dy = target.x - player.x, target.y - player.y
        ns = "south" if dy > 0 else "north" if dy < 0 else ""
        ew = "east" if dx > 0 else "west" if dx < 0 else ""
        return (ns + ew) or "nearby"

    def _captive_step_dir(self, session: GameSession) -> str | None:
        """The cardinal move (n/s/e/w) that makes real progress toward the nearest captive —
        computed with the engine's BFS pathfinder, so it routes *around* walls and through
        unlocked doors instead of walking the agent into stone (fix #1). Returns None when
        there is no captive, one is already adjacent, OR no open path exists yet (e.g. the
        cell block is behind a locked door / not yet connected). The no-path case must NOT
        fall back to a straight-line bearing — that points the agent into a wall and it
        thrashes (the cycle-2 finding). With None, exploration falls back to its normal drift
        until a route opens and the pathfinder starts returning real steps again."""
        nearest = self._nearest_captive(session)
        if nearest is None:
            return None
        engine = session.engine
        player = engine.state.player
        if max(abs(nearest.x - player.x), abs(nearest.y - player.y)) <= 1:
            return None
        step = engine.next_path_step(player, nearest.x, nearest.y)
        if step is None:
            return None
        delta = (step[0] - player.x, step[1] - player.y)
        return self._VEC_TO_CARDINAL.get(delta)

    def _opportunity_nudge(self, session: GameSession) -> str | None:
        """Point the agent at a concrete in-reach opportunity it would otherwise walk past —
        chiefly captives, which it cannot purposefully seek without being told where they are.
        An adjacent captive prompts an immediate free; a distant one gets a *pathfound*
        directional instruction (the open route around walls), which also realigns the
        episode's exploration drift (see the expedition override)."""
        nearest = self._nearest_captive(session)
        if nearest is None:
            return None
        player = session.engine.state.player
        if max(abs(nearest.x - player.x), abs(nearest.y - player.y)) <= 1:
            return (
                "Someone is bound in a cell right beside you. 'free' them now - freeing "
                "captives is a memorable deed and some will take up arms and follow you."
            )
        step_dir = self._captive_step_dir(session)
        compass = self._compass_to(session, nearest)
        if step_dir is None:
            # Captives exist but no open path leads there yet (locked/disconnected). Don't
            # name a direction (it would point into a wall); just note it occasionally so the
            # agent keeps exploring for a route rather than fixating.
            if self.step_count % 5 != 0:
                return None
            return (
                f"People are held in cells to the {compass}, but no open path leads there "
                "from here yet - keep exploring to find a way around (a door or passage)."
            )
        return (
            f"People are held in cells to the {compass} (e.g. {nearest.name} at "
            f"{nearest.x},{nearest.y}). The open path runs {step_dir} from here - move "
            f"{step_dir} now, then 'free' them when you are beside one."
        )

    def _should_autorest(self, session: GameSession) -> bool:
        """Whether the harness should rest *for* the agent this step to exercise the daily
        Simulator: at least ~25 steps since the last auto-rest, deeds awaiting the 05:00 tick,
        the game still live, and no enemy within 3 tiles (don't camp in a fight)."""
        if (self.step_count - self._last_autorest_step) < 25:
            return False
        state = session.engine.state
        if state.game_over:
            return False
        if not any(not d.applied for d in state.deed_ledger.deeds):
            return False
        player = state.player
        return not any(
            e.faction == "enemy"
            and e.hp > 0
            and max(abs(e.x - player.x), abs(e.y - player.y)) <= 3
            for e in state.entities.values()
        )

    def _rest_nudge(self, session: GameSession) -> str | None:
        """Nudge the agent to `rest until dawn` when it has deeds the daily 05:00 Simulator
        hasn't reckoned with yet and it's safe to camp. Without this the agent never rests, so
        the whole emergent *daily* layer (standing shifts, backlash, bond drift, posters) goes
        unexercised by autoplay — deeds are recorded but never applied."""
        engine = session.engine
        state = engine.state
        if not any(not d.applied for d in state.deed_ledger.deeds):
            return None
        player = state.player
        if any(
            e.faction == "enemy"
            and e.hp > 0
            and max(abs(e.x - player.x), abs(e.y - player.y)) <= 2
            for e in state.entities.values()
        ):
            return None  # not while enemies are close
        return (
            "You have done deeds the world has not yet reckoned with. Find a safe spot and "
            "'rest until dawn' to let a day pass - then read 'standing' and 'followers' to "
            "see how the Empire and the people have reacted to what you've done."
        )

    def _drain_background(self, session: GameSession) -> None:
        session.drain_lore(block=True)
        session.drain_flesh(block=True)
        session.drain_canon_prewarm(block=True)

    def _game_over_reason(self, session: GameSession) -> str:
        state = session.engine.state
        if state.victory:
            return "victory"
        return f"death:{state.death_cause or 'unknown'}"


class CampaignRunner:
    def __init__(self, config: CampaignConfig) -> None:
        self.config = config
        self.run_id = config.run_id or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        self.run_dir = config.out / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        set_runtime_config_value("WILDMAGIC_AUDIT_DIR", str(self.run_dir))
        self.findings_path = self.run_dir / "findings.jsonl"
        self.report_path = self.run_dir / "report.md"
        self.regression_path = self.run_dir / "regression_seeds.txt"
        self.episode_summaries: list[EpisodeSummary] = []
        self.findings: list[Finding] = []
        self.regression_entries: dict[tuple[int | None, str], set[str]] = {}

    def run(self) -> Path:
        start = time.time()
        if self.config.episodes is not None:
            max_episodes = self.config.episodes
        elif self.config.hours is not None:
            max_episodes = None
        else:
            max_episodes = 1
        episode_index = 1
        while True:
            if (
                self.config.hours is not None
                and time.time() - start >= self.config.hours * 3600
            ):
                break
            if max_episodes is not None and episode_index > max_episodes:
                break
            scenario = self.config.scenarios[
                (episode_index - 1) % len(self.config.scenarios)
            ]
            persona = self.config.personas[
                (episode_index - 1) % len(self.config.personas)
            ]
            theme = THEMES[(episode_index - 1) % len(THEMES)]
            seed = self.config.seed_base + episode_index - 1
            agent = self._make_agent(seed)
            runner = EpisodeRunner(
                self.config,
                self.run_dir,
                episode_index,
                seed,
                scenario,
                persona,
                theme,
                agent,
            )
            try:
                summary = runner.run()
                self.episode_summaries.append(summary)
            except KeyboardInterrupt:
                raise
            except Exception:
                runner.findings.append(
                    Finding(
                        tier=1,
                        kind="harness_error",
                        episode=episode_index,
                        seed=seed,
                        scenario=scenario,
                        turn=-1,
                        evidence={"traceback": traceback.format_exc()},
                    )
                )
            self.findings.extend(runner.findings)
            for finding in runner.findings:
                append_jsonl(self.findings_path, finding_to_record(finding))
            serious_kinds = {
                finding.kind for finding in runner.findings if finding.tier <= 2
            }
            if serious_kinds:
                self.regression_entries.setdefault((seed, scenario), set()).update(
                    serious_kinds
                )
                self.write_regression_seeds()
            self.write_report()
            episode_index += 1
            if self.config.episodes is None and self.config.hours is None:
                break
        return self.report_path

    def _make_agent(self, seed: int | None) -> PlayerAgent:
        if self.config.agent == "ollama":
            return OllamaAgent()
        if self.config.agent == "random":
            return RandomAgent(seed)
        return StubAgent(self.config.stub_commands)

    def write_regression_seeds(self) -> None:
        lines = [
            "# seed\tscenario\tfinding kinds — re-run with: python -m wildmagic.cli --seed <seed> --scenario <scenario>"
        ]
        for (seed, scenario), kinds in sorted(
            self.regression_entries.items(),
            key=lambda item: (str(item[0][0]), item[0][1]),
        ):
            lines.append(f"{seed}\t{scenario}\t{','.join(sorted(kinds))}")
        self.regression_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_report(self) -> None:
        total_turns = sum(summary.turns for summary in self.episode_summaries)
        total_casts = sum(summary.casts for summary in self.episode_summaries)
        deaths = sum(
            1
            for summary in self.episode_summaries
            if summary.completion_reason.startswith("death")
        )
        lines = [
            "# Autoplay Report",
            "",
            f"Run: `{self.run_id}`",
            f"Episodes: {len(self.episode_summaries)}",
            f"Turns: {total_turns}",
            f"Casts: {total_casts}",
            f"Deaths: {deaths}",
            f"Findings: {len(self.findings)}",
            "",
            "## Episodes",
            "",
        ]
        for summary in self.episode_summaries:
            lines.append(
                f"- episode {summary.episode:03d}: seed {summary.seed}, {summary.scenario}/{summary.persona}, "
                f"{summary.turns} turns, {summary.steps} steps, {summary.completion_reason}, "
                f"replay `{Path(summary.replay_path).name}`"
            )
        lines.extend(["", "## Tier 1 Findings", ""])
        tier1 = [finding for finding in self.findings if finding.tier == 1]
        if not tier1:
            lines.append("None.")
        else:
            seen_signatures: set[tuple[str, str]] = set()
            by_kind: dict[str, int] = {}
            for finding in tier1:
                by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
            lines.append(
                "Counts: "
                + ", ".join(
                    f"{kind}={count}" for kind, count in sorted(by_kind.items())
                )
            )
            lines.append("")
            for finding in tier1:
                signature = (finding.kind, crash_signature(finding))
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                replay = finding.replay_path or ""
                detail = f" — `{signature[1]}`" if signature[1] else ""
                lines.append(
                    f"- {finding.kind} in episode {finding.episode:03d} turn {finding.turn}{detail}: "
                    f"`python -m wildmagic.replay {replay}`"
                )
        lines.extend(["", "## Tier 2 Findings (heuristics)", ""])
        tier2 = [finding for finding in self.findings if finding.tier == 2]
        if not tier2:
            lines.append("None.")
        else:
            by_kind2: dict[str, list[Finding]] = {}
            for finding in tier2:
                by_kind2.setdefault(finding.kind, []).append(finding)
            for kind, group in sorted(by_kind2.items()):
                first = group[0]
                spell = finding_spell(first)
                spell_note = f', spell: "{spell}"' if spell else ""
                lines.append(
                    f"- {kind}: {len(group)} occurrence(s), first in episode {first.episode:03d} "
                    f"turn {first.turn} (`{Path(first.replay_path or '').name}`{spell_note})"
                )
        lines.extend(["", "## Agent Leads (tier 3, unverified)", ""])
        tier3 = [finding for finding in self.findings if finding.tier == 3]
        summaries_with_text = [
            summary for summary in self.episode_summaries if summary.agent_summary
        ]
        if not tier3 and not summaries_with_text:
            lines.append("None.")
        else:
            for finding in tier3:
                note = finding.evidence.get("note") or "(no note)"
                command = finding.evidence.get("command") or ""
                lines.append(
                    f"- episode {finding.episode:03d} turn {finding.turn}: {note} (after `{command}`)"
                )
            for summary in summaries_with_text:
                lines.append(
                    f"- episode {summary.episode:03d} summary: {summary.agent_summary}"
                )
        lines.extend(["", "## Stats", ""])
        if self.episode_summaries:
            total_steps = sum(summary.steps for summary in self.episode_summaries)
            total_parse_failures = sum(
                summary.parse_failures for summary in self.episode_summaries
            )
            total_cast_technical = sum(
                summary.cast_technical_failures for summary in self.episode_summaries
            )
            total_canon_technical = sum(
                summary.canon_technical_failures for summary in self.episode_summaries
            )
            total_rejected = sum(
                summary.rejected_casts for summary in self.episode_summaries
            )
            total_canon_actions = sum(
                count
                for summary in self.episode_summaries
                for verb, count in summary.command_counts.items()
                if verb in {"read", "investigate", "examine"}
            )
            lines.append(
                f"- Agent parse-failure rate: {total_parse_failures}/{max(total_steps, 1)} steps"
            )
            lines.append(
                f"- Wild-cast technical failures: {total_cast_technical}/{total_casts} casts"
            )
            lines.append(
                f"- Wild-cast OP rejections: {total_rejected}/{total_casts} casts"
            )
            lines.append(
                f"- Read/investigate (canon) technical failures: "
                f"{total_canon_technical}/{total_canon_actions} read/investigate/examine actions"
            )
            by_reason: dict[str, int] = {}
            for summary in self.episode_summaries:
                by_reason[summary.completion_reason] = (
                    by_reason.get(summary.completion_reason, 0) + 1
                )
            lines.append(
                "- Completion reasons: "
                + ", ".join(
                    f"{key}={value}" for key, value in sorted(by_reason.items())
                )
            )
            verb_totals: dict[str, int] = {}
            for summary in self.episode_summaries:
                for verb, count in summary.command_counts.items():
                    verb_totals[verb] = verb_totals.get(verb, 0) + count
            top_verbs = sorted(
                verb_totals.items(), key=lambda item: item[1], reverse=True
            )[:10]
            if top_verbs:
                lines.append(
                    "- Command distribution (top 10): "
                    + ", ".join(f"{verb}={count}" for verb, count in top_verbs)
                )
            persona_lines = []
            for persona in sorted(
                {summary.persona for summary in self.episode_summaries}
            ):
                group = [
                    summary
                    for summary in self.episode_summaries
                    if summary.persona == persona
                ]
                persona_lines.append(
                    f"{persona}: {len(group)} episode(s), {sum(s.turns for s in group)} turns, {sum(s.findings for s in group)} findings"
                )
            lines.append("- Per persona: " + "; ".join(persona_lines))
        lines.extend(["", "## Agent Notes", ""])
        notes = [note for summary in self.episode_summaries for note in summary.notes]
        if not notes:
            lines.append("None.")
        else:
            for representative, count in cluster_notes(notes)[:30]:
                prefix = f"(x{count}) " if count > 1 else ""
                lines.append(f"- {prefix}{representative}")
        self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_NOTE_STOPWORDS = {
    "the",
    "this",
    "that",
    "with",
    "from",
    "into",
    "when",
    "after",
    "before",
    "while",
    "have",
    "feel",
    "feels",
    "felt",
    "very",
    "just",
    "like",
    "some",
    "what",
    "which",
    "were",
    "been",
    "they",
    "them",
    "their",
    "there",
    "turn",
    "game",
    "spell",
    "spells",
}


def note_keywords(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z']+", text.lower())
        if len(word) > 3 and word not in _NOTE_STOPWORDS
    }


def cluster_notes(notes: list[str], threshold: float = 0.3) -> list[tuple[str, int]]:
    """Greedy keyword-overlap clustering; returns (representative, count) by cluster size."""
    clusters: list[tuple[set[str], list[str]]] = []
    for note in notes:
        keys = note_keywords(note)
        placed = False
        for cluster_keys, items in clusters:
            union = cluster_keys | keys
            if union and len(cluster_keys & keys) / len(union) >= threshold:
                items.append(note)
                cluster_keys |= keys
                placed = True
                break
        if not placed:
            clusters.append((keys, [note]))
    ranked = sorted(clusters, key=lambda cluster: len(cluster[1]), reverse=True)
    return [(items[0], len(items)) for _, items in ranked]


def finding_spell(finding: Finding) -> str | None:
    evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
    record = evidence.get("wild_magic")
    if not isinstance(record, dict):
        nested = evidence.get("result")
        record = nested.get("wild_magic") if isinstance(nested, dict) else None
    if isinstance(record, dict):
        spell = record.get("spell")
        if isinstance(spell, str) and spell:
            return spell
    return None


def crash_signature(finding: Finding) -> str:
    text = (
        finding.evidence.get("traceback")
        if isinstance(finding.evidence, dict)
        else None
    )
    if not isinstance(text, str):
        return ""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line:
            return line[:160]
    return ""


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def finding_to_record(finding: Finding) -> dict[str, Any]:
    return asdict(finding)


def parse_args(argv: list[str] | None = None) -> CampaignConfig:
    parser = argparse.ArgumentParser(description="Run autonomous Wild Magic playtests.")
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--max-turns", type=int, default=120)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--episode-minutes", type=float, default=15.0)
    parser.add_argument(
        "--scenario",
        action="append",
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
    parser.add_argument("--persona", action="append", choices=list(PERSONAS))
    parser.add_argument(
        "--seed-base",
        type=int,
        default=None,
        help="First episode seed. Defaults to a fresh random seed base for each campaign.",
    )
    parser.add_argument(
        "--provider", default="mock", choices=["auto", "mock", "ollama"]
    )
    parser.add_argument("--agent", default="stub", choices=["ollama", "stub", "random"])
    parser.add_argument("--out", type=Path, default=Path("logs/autoplay"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--drain-background", action="store_true")
    parser.add_argument("--stub-command", action="append", default=[])
    args = parser.parse_args(argv)
    return CampaignConfig(
        episodes=args.episodes,
        hours=args.hours,
        max_turns=max(1, args.max_turns),
        max_steps=args.max_steps,
        episode_minutes=max(0.1, args.episode_minutes),
        scenarios=args.scenario or list(SCENARIOS),
        personas=args.persona or list(PERSONAS),
        seed_base=args.seed_base if args.seed_base is not None else random_seed_base(),
        provider=args.provider,
        agent=args.agent,
        out=args.out,
        run_id=args.run_id,
        drain_background=args.drain_background,
        stub_commands=args.stub_command,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    runner = CampaignRunner(config)
    try:
        report_path = runner.run()
    except KeyboardInterrupt:
        runner.write_report()
        report_path = runner.report_path
    print(f"Autoplay report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
