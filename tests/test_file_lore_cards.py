from __future__ import annotations

import textwrap

import pytest

from wildmagic.file_lore_cards import (
    FileLoreError,
    load_file_lore_sections,
    parse_lore_file,
)
from wildmagic.lore_cards import LORE_CARDS, eligible_cards, select_lore_cards


def _write_lore_file(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return path


def test_parse_lore_file_reads_file_and_level_metadata(tmp_path):
    path = _write_lore_file(
        tmp_path,
        "monteary.md",
        """
        # Monteary

        ```toml lore
        id = "monteary"
        tags = ["monteary"]
        ```

        ## Level 0

        ```toml meta
        name = "monteary_basics"
        description = "Common horse-realm knowledge."
        triggers = ["horse", "gelding"]
        subjects = ["bloodline"]
        ```

        Everyone has heard of Monteary horses.
        """,
    )

    (section,) = parse_lore_file(path)

    assert section.name == "monteary_basics"
    assert section.topic == "monteary"
    assert section.level == 0
    assert section.threshold == 0
    assert section.tags == ("monteary",)
    assert section.triggers == ("horse", "gelding", "bloodline")
    assert section.description == "Common horse-realm knowledge."
    assert section.text == "Everyone has heard of Monteary horses."


def test_live_registry_uses_file_backed_sections():
    cards_by_name = {card.name: card for card in LORE_CARDS}

    assert cards_by_name["monteary_basics"].source.endswith(
        "content\\lore\\monteary.md"
    ) or (cards_by_name["monteary_basics"].source == "content/lore/monteary.md")
    assert cards_by_name["monteary_basics"].topic == "monteary"
    assert cards_by_name["monteary_basics"].level == 0


def test_level_zero_is_public_but_level_one_is_gated():
    eligible = {card.name for card in eligible_cards({})}

    assert "monteary_basics" in eligible
    assert "monteary_familiar" not in eligible


def test_higher_level_descriptions_are_not_sent_to_router_when_ineligible():
    seen_messages: list[list[dict]] = []

    def route_call(messages):
        seen_messages.append(messages)
        return []

    select_lore_cards(
        {},
        "tell me about Monteary stallions and bloodlines",
        route_call=route_call,
        max_cards=1,
    )

    assert seen_messages
    router_prompt = seen_messages[0][0]["content"]
    assert "monteary_basics" in router_prompt
    assert "monteary_familiar" not in router_prompt
    assert "bloodline" not in router_prompt.lower()


def test_level_one_description_is_visible_when_lore_allows_it():
    seen_messages: list[list[dict]] = []

    def route_call(messages):
        seen_messages.append(messages)
        return []

    select_lore_cards(
        {"monteary": 1},
        "tell me about Monteary stallions and bloodlines",
        route_call=route_call,
        max_cards=1,
    )

    assert seen_messages
    router_prompt = seen_messages[0][0]["content"]
    assert "monteary_familiar" in router_prompt
    assert "politics of bloodlines" in router_prompt


def test_drafts_parse_but_are_excluded_by_default(tmp_path):
    _write_lore_file(
        tmp_path,
        "monteary.md",
        """
        # Monteary

        ```toml lore
        id = "monteary"
        tags = ["monteary"]
        ```

        ## Level 4

        ```toml meta
        name = "monteary_secret"
        description = "Draft secret lore."
        triggers = ["secret"]
        draft = true
        ```

        Draft body.
        """,
    )

    assert load_file_lore_sections(tmp_path) == ()
    sections = load_file_lore_sections(tmp_path, include_drafts=True)
    assert [section.name for section in sections] == ["monteary_secret"]


def test_missing_description_is_rejected(tmp_path):
    path = _write_lore_file(
        tmp_path,
        "bad.md",
        """
        # Bad

        ```toml lore
        id = "bad"
        tags = ["bad"]
        ```

        ## Level 0

        ```toml meta
        triggers = ["bad"]
        ```

        Body.
        """,
    )

    with pytest.raises(FileLoreError, match="description"):
        parse_lore_file(path)


def test_malformed_toml_is_rejected(tmp_path):
    path = _write_lore_file(
        tmp_path,
        "bad.md",
        """
        # Bad

        ```toml lore
        id = "bad"
        tags = ["bad"
        ```

        ## Level 0

        ```toml meta
        description = "Bad lore."
        ```

        Body.
        """,
    )

    with pytest.raises(FileLoreError, match="malformed lore TOML"):
        parse_lore_file(path)


def test_duplicate_names_are_rejected_across_files(tmp_path):
    body = """
    # Topic

    ```toml lore
    id = "topic"
    tags = ["topic"]
    ```

    ## Level 0

    ```toml meta
    name = "duplicate"
    description = "Duplicated section."
    triggers = ["topic"]
    ```

    Body.
    """
    _write_lore_file(tmp_path, "a.md", body)
    _write_lore_file(tmp_path, "b.md", body)

    with pytest.raises(FileLoreError, match="duplicate"):
        load_file_lore_sections(tmp_path)
