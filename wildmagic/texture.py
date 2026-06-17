"""Layer-1 procedural texture: grammars and tables for instant bulk variety.

This layer guarantees nothing is ever blank and manufactures the seed vocabulary
that LLM materialization (canon.py) consumes. A book placed here has a concrete
grammar-tier name ("a water-stained ledger of weather law") the moment the map
exists; its title, author, and pages stay unmaterialized until read or prewarmed.
"""

from __future__ import annotations

import random
from typing import Any


_BOOK_FORMS = (
    "volume",
    "ledger",
    "treatise",
    "folio",
    "chapbook",
    "codex",
    "commonplace book",
    "primer",
    "registry",
    "breviary",
)

_BOOK_CONDITIONS = (
    "water-stained",
    "soot-darkened",
    "mouse-chewed",
    "carefully rebound",
    "swollen with damp",
    "annotated in two hands",
    "missing its cover",
    "tied shut with twine",
    "smelling of tallow",
    "dog-eared",
)

_BOOK_BINDINGS = (
    "cracked leather",
    "stiff grey board",
    "oiled canvas",
    "scraped vellum",
    "thin pine slats",
    "wine-dark cloth",
)

_BOOK_GENRES = (
    "field guide",
    "court complaint",
    "sermon cycle",
    "trial transcript",
    "household manual",
    "confession",
    "travel diary",
    "guild primer",
    "mourning book",
    "saint's life",
    "tax commentary",
    "recipe book",
    "children's lesson",
    "ship's log",
    "bestiary notes",
    "calendar of omens",
)

_BOOK_DISCIPLINES = (
    "devotional practice",
    "weather law",
    "borderkeeping",
    "funeral custom",
    "river engineering",
    "kitchen physic",
    "market fraud",
    "saintly etiquette",
    "wild-magic cautions",
    "garden rites",
    "census craft",
    "military doctrine",
    "dream interpretation",
    "glassmaking",
    "road maintenance",
    "midwifery",
)

_AUTHOR_ROLES = (
    "retired censor",
    "field nun",
    "junior surveyor",
    "failed playwright",
    "river pilot",
    "market clerk",
    "temple cook",
    "border widow",
    "apprentice thaumaturge",
    "militia quartermaster",
    "village judge",
    "itinerant bell-founder",
    "disgraced tutor",
    "mushroom factor",
)

_AUDIENCES = (
    "novices",
    "children who ask too many questions",
    "provincial magistrates",
    "pilgrims",
    "clerks with poor memories",
    "newly sworn soldiers",
    "wives of absent officials",
    "unlicensed witches",
    "road wardens",
    "households under inspection",
    "ship captains",
    "the author's enemies",
)

_PURPOSES = (
    "to correct a famous mistake",
    "to preserve a forbidden custom",
    "to settle an old argument",
    "to train someone who will never meet the author",
    "to disguise grief as instruction",
    "to flatter an imperial patron",
    "to smuggle a local truth through official language",
    "to warn the careless",
    "to make ordinary labor sound holy",
    "to prove the author was there first",
)

_STANCES = (
    "tender but exacting",
    "furious and over-footnoted",
    "dryly comic",
    "homesick",
    "pious and suspicious",
    "practical to the point of cruelty",
    "secretly romantic",
    "bureaucratic with cracks of awe",
    "guilty",
    "triumphant over a small enemy",
)

_INSTITUTIONS = (
    "Censorate annex",
    "parish school",
    "river guild",
    "border office",
    "household press",
    "grave-keepers' lodge",
    "market court",
    "pilgrim hostel",
    "legionary depot",
    "unlicensed kitchen circle",
    "road chapel",
    "glasshouse archive",
)

_TITLE_SHAPES = (
    "numbered list",
    "complaint",
    "manual",
    "confession",
    "registry",
    "sermon",
    "letter",
    "calendar",
    "trial record",
    "songbook",
    "answer to an insult",
    "instructions for a substitute",
)

_TABOO_LEVELS = ("ordinary", "eccentric", "suppressed", "forbidden")


def _choose_topic(rng: random.Random, topics: list[str]) -> str:
    useful = [topic for topic in topics if str(topic).strip()]
    if useful:
        return str(rng.choice(useful))
    return rng.choice(_BOOK_DISCIPLINES)


def grammar_book(rng: random.Random, topics: list[str], era: str) -> dict[str, Any]:
    """A grammar-tier book entry: instant name + description, no model involved.

    The name is deliberately a category description, not a title — titles carry
    world texture and belong to the LLM at materialization time.
    """
    topic = _choose_topic(rng, topics)
    secondary_topic = rng.choice([item for item in _BOOK_DISCIPLINES if item != topic])
    form = rng.choice(_BOOK_FORMS)
    condition = rng.choice(_BOOK_CONDITIONS)
    binding = rng.choice(_BOOK_BINDINGS)
    genre = rng.choice(_BOOK_GENRES)
    discipline = rng.choice(_BOOK_DISCIPLINES)
    author_role = rng.choice(_AUTHOR_ROLES)
    audience = rng.choice(_AUDIENCES)
    purpose = rng.choice(_PURPOSES)
    stance = rng.choice(_STANCES)
    institution = rng.choice(_INSTITUTIONS)
    title_shape = rng.choice(_TITLE_SHAPES)
    taboo_level = rng.choice(_TABOO_LEVELS)
    # 1-4 subjects are the book's durable metadata: they seed the title call now
    # and will key the lore-card router later. Dedupe while preserving order.
    subjects: list[str] = []
    for subject in (topic, secondary_topic, discipline):
        cleaned = str(subject).strip()
        if cleaned and cleaned not in subjects:
            subjects.append(cleaned)
    return {
        "name": f"{condition} {form} of {topic}",
        "description": f"A {form} bound in {binding}, {condition}. It concerns {topic}.",
        "topic": topic,
        "secondary_topic": secondary_topic,
        "subjects": subjects[:4],
        "form": form,
        "condition": condition,
        "binding": binding,
        "era": era,
        "genre": genre,
        "discipline": discipline,
        "author_role": author_role,
        "audience": audience,
        "purpose": purpose,
        "stance": stance,
        "institution": institution,
        "title_shape": title_shape,
        "taboo_level": taboo_level,
    }
