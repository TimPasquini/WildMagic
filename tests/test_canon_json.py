from __future__ import annotations

import json

import pytest

from wildmagic.canon import parse_canon_json, _repair_truncated_json


def test_parses_clean_json():
    raw = '{"title": "A Book", "text": "the body", "tags": ["one"]}'
    parsed = parse_canon_json(raw)
    assert parsed["title"] == "A Book"
    assert parsed["text"] == "the body"


def test_strips_surrounding_prose_with_regex_fallback():
    raw = 'Sure! Here is the book:\n{"text": "the body"}\nHope that helps.'
    assert parse_canon_json(raw)["text"] == "the body"


# Each case is a JSON object truncated at a different syntactic position, mimicking
# the model running out of output tokens mid-write. All should recover a dict.
@pytest.mark.parametrize("raw", [
    '{"title": "x", "text": "hello world',            # cut mid-string value
    '{"a": "x", "tags": ["one", "two',                # cut mid-array element
    '{"a": "x", "summary"',                            # dangling key, no colon
    '{"a": "x", "b":',                                 # dangling colon, no value
    '{"a": "x",',                                      # trailing comma
    '{"llm": {"author": "Mother Elara"}',             # complete value, missing outer brace
    '{"text": "ends with backslash\\',                # dangling escape char
])
def test_repairs_truncated_json(raw):
    parsed = parse_canon_json(raw)
    assert isinstance(parsed, dict)
    # The repaired text must itself be valid JSON.
    json.dumps(parsed)


def test_truncated_book_keeps_completed_fields():
    # The real overnight failure mode: a full book whose JSON was cut two braces short.
    raw = (
        '{\n  "title": "Sermon on the Drowned Saints",\n'
        '  "summary": "A field nun preserves forbidden saints.",\n'
        '  "text": "The road chapel stands where the market ends.",\n'
        '  "tags": ["forbidden_saints", "field_nun"],\n'
        '  "llm_choices": {"author": "Mother Elara of the Road Chapel"}'
    )
    parsed = parse_canon_json(raw)
    assert parsed["title"] == "Sermon on the Drowned Saints"
    assert parsed["text"].startswith("The road chapel")
    assert "forbidden_saints" in parsed["tags"]


def test_literal_newlines_in_string_value():
    # The most common real failure: the model writes a multi-line book body with
    # raw newlines inside the JSON string instead of escaping them as \n.
    raw = '{"title": "x", "text": "first line\nsecond line\tindented"}'
    parsed = parse_canon_json(raw)
    assert parsed["text"] == "first line\nsecond line\tindented"


def test_trailing_extra_data_after_object():
    raw = '{"text": "the body"}\n\nLet me know if you want another book!'
    assert parse_canon_json(raw)["text"] == "the body"


def test_genuine_garbage_still_raises():
    with pytest.raises((json.JSONDecodeError, ValueError, TypeError)):
        parse_canon_json("there is no json here at all")


def test_repair_returns_none_for_unsalvageable():
    assert _repair_truncated_json("no opening brace") is None
