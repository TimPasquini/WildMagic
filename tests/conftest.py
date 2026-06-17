from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--full",
        action="store_true",
        default=False,
        help="run provider-sensitive or long-running tests and do not force mock background providers",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "full: long-running or provider-sensitive test")
    if not config.getoption("--full"):
        # The trim suite should not accidentally wait on a local LLM because a
        # developer's .env points background generation at Ollama.
        os.environ.setdefault("WILDMAGIC_TOWN_PROVIDER", "mock")
        os.environ.setdefault("WILDMAGIC_CANON_PREWARM_ENABLED", "0")
        # Book titles prewarm on-by-default in real play; force off so the trim
        # suite never fires background title calls. Title tests opt back in.
        os.environ.setdefault("WILDMAGIC_BOOK_TITLES", "0")
        # Experimental LLM prop set-dressing is on-by-default when Ollama is
        # reachable; force it off so the trim suite never fires real prop calls
        # in background threads. Tests that exercise it inject MockPropProvider.
        os.environ.setdefault("WILDMAGIC_PROPS_PROVIDER", "off")
        # The deed interpreter (A.2) follows the wild-magic provider by default; force it
        # off so the trim suite uses only the deterministic fallback (tests that exercise
        # the LLM path inject MockDeedInterpreterProvider).
        os.environ.setdefault("WILDMAGIC_DEEDS_PROVIDER", "off")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--full"):
        return
    skip_full = pytest.mark.skip(reason="requires pytest --full")
    for item in items:
        if "full" in item.keywords:
            item.add_marker(skip_full)
