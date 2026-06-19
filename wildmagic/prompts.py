from __future__ import annotations

from .models import MECHANICAL_STATUSES
from .semantics import SEMANTIC_PREAMBLE


SUPPORTED_STATUS_TEXT = ", ".join(sorted(MECHANICAL_STATUSES))

# The monolithic resolver SYSTEM_PROMPT was removed 2026-06-13; the wild-magic system
# prompt is now assembled from CORE_PROMPT + routed capability cards in
# wildmagic/capabilities.py. SUPPORTED_STATUS_TEXT (above) is still used by CORE_PROMPT.


def region_prompt_block(region_style: dict | None) -> str:
    """Per-region addendum appended to the resolver system prompt: a style line plus a few
    region-voiced outcome_text samples (examples steer small models harder than
    instructions, at modest token cost)."""
    if not region_style:
        return ""
    lines = [
        "",
        f"Region: the player is in {region_style.get('name', 'unknown country')}.",
    ]
    voice = (region_style.get("voice") or "").strip()
    if voice:
        lines.append(f"Region voice for outcome_text: {voice}")
    examples = region_style.get("examples") or []
    if examples:
        lines.append("outcome_text samples in this region's voice:")
        for example in examples[:3]:
            lines.append(f'- "{example}"')
    return "\n".join(lines) + "\n"


def caster_prompt_block(caster_profile: dict | None) -> str:
    """Per-caster addendum appended to the resolver system prompt, derived from the controlled
    entity's stats and free-form fields. This is how character stats reach the wild
    magic: rather than a separate mechanical pass, the stats *shift the anchors* we
    hand the model — Attunement scales the magnitude bands, Composure scales how hard
    the wild bites back, Vigor steers what kind of costs land — and the appearance /
    signature tint the prose. See docs/CHARACTER_CREATION.md.

    Stats run ~1–6; only off-center values emit guidance, so a middling 3/3/3 caster
    adds nothing and the prompt stays lean."""
    if not caster_profile:
        return ""
    vigor = int(caster_profile.get("vigor", 3))
    attunement = int(caster_profile.get("attunement", 3))
    composure = int(caster_profile.get("composure", 3))

    lines = ["", "Caster attunement to the wild (shift your anchors accordingly):"]

    # Attunement → effect magnitude. Pushes the severity-band numbers up or down.
    if attunement >= 5:
        lines.append(
            "- Strongly attuned: lean effect magnitudes (damage, healing, radius, "
            "duration) to the HIGH end of each severity band, and you may exceed the "
            "listed numbers by up to ~25%. Their wild magic lands hard."
        )
    elif attunement <= 2:
        lines.append(
            "- Weakly attuned: keep effect magnitudes at the LOW end of each severity "
            "band; results are thinner than the wording suggests. Their grip is loose."
        )

    # Composure → volatility / how readily costs and backfires attach.
    if composure <= 2:
        lines.append(
            "- Low composure: the wild answers chaotically. Attach costs and backfires "
            "more readily and make them surprising and gorgeous (a curse, a strange "
            "status, a scheduled reckoning); severity may overshoot what they intended. "
            "Wild magic does not entirely love them."
        )
    elif composure >= 5:
        lines.append(
            "- High composure: the wild answers cleanly. Backfires are rarer and "
            "gentler, costs stay proportionate, and effects land close to intent."
        )

    # Vigor → which costs the body can shoulder.
    if vigor >= 5:
        lines.append(
            "- Hardy: health and other physical costs are fair game — this body can "
            "shoulder them."
        )
    elif vigor <= 2:
        lines.append(
            "- Frail: steer costs away from raw health toward mana, items, or curses."
        )

    signature = (caster_profile.get("signature") or "").strip()
    if signature:
        lines.append(
            f"- Casting signature (let it lightly tint outcome_text, never dominate): {signature}"
        )
    appearance = (caster_profile.get("appearance") or "").strip()
    if appearance:
        lines.append(
            f"- The caster's appearance, if it matters to the scene: {appearance}"
        )

    # Nothing off-center and no flavor → no point spending tokens on a bare header.
    if len(lines) <= 2:
        return ""
    return "\n".join(lines) + "\n"


def focus_prompt_block(foci: list[dict] | None) -> str:
    """Per-cast addendum: the implement(s) the caster channels through (their spell foci).

    A spell focus is the single strongest flavor lever short of the spell words themselves --
    it should steer imagery, element, and tone -- but it must not override the spell's stated
    intent (a fire-orb focus should not turn "heal me" into a burn). Empty list -> no block, so
    a caster with no focus marked spends no tokens here.

    Power is carried in the focus data but does not yet scale magnitudes; when it should, branch
    on focus["power"] here exactly as Attunement shifts the bands in caster_prompt_block."""
    if not foci:
        return ""
    lines = [
        "",
        "Spell focus (the implement the caster channels through -- weigh it HEAVILY in this",
        "spell's flavor: let it steer imagery, element, and tone. Shape the flavor when",
        "compatible; do NOT override the spell's stated intent):",
    ]
    for focus in foci:
        name = str(focus.get("name") or "a focus").strip()
        lines.append(f"- The caster channels through: {name}.")
        description = str(focus.get("description") or "").strip()
        if description:
            lines.append(f"  What it is: {description}")
        themes = focus.get("themes")
        if themes:
            lines.append(f"  Its themes: {', '.join(str(t) for t in themes)}.")
    return "\n".join(lines) + "\n"


DIALOGUE_SYSTEM_PROMPT = """You are voicing a single non-player character (NPC) in a turn-based tile roguelike.
You will receive a JSON object describing who you are, what you have personally witnessed
recently, your conversation so far, and what the player just said to you.

The world: a vibrant, eclectic patchwork of peoples and surviving old magical traditions
(blood, bone, crystal, song, and more), most of it under the Grand Empire -- an orderly,
courteous, genuinely-not-evil power that outlaws wild sorcery and licenses only "charter
magic" through its initiated charter mages. Ordinary folk hold every shade of opinion:
gratitude for imperial peace and safe roads, quiet nostalgia for the old ways, fear of wild
magic, fear of imperial paperwork. Your character has their own stance, shaped by their
backstory -- but the world they live in is colorful and alive, not grim.

Reply with ONLY the words your character speaks aloud - plain spoken text and nothing else.
Do not include narration, stage directions, action descriptions, asterisks, quotation marks
around the whole reply, your own name as a prefix, markdown, or <think> text.

Guidelines:
- Speak with some color and substance: two to five sentences, like someone who actually
  has things to say rather than a curt one-line brush-off. Let your personality come
  through in *how* you say it - a gossip rambles, a guard captain clips her words but
  still gives you the gist, a peddler can't resist adding one more pitch.
- Answer the player's newest message directly. Do not mirror, paraphrase, or lightly
  rewrite the player's sentence as your reply. You may repeat short concrete nouns
  when needed, but the first sentence should add new information, a clear reaction,
  or a decision from the NPC.
- Avoid opening with stock reflection phrases like "you ask..." or "you say..." unless
  the NPC is deliberately mocking the player; even then, quickly move to an actual answer.
- Stay fully in character. Your "backstory" and "traits" shape your personality, opinions,
  manner of speech, and how warm, wary, gruff, gossiping, or distracted you are.
- Use "things_i_personally_witnessed", "things_i_overheard", "gossip_i_have_heard",
  and "conversation_memory" so you sound aware of the world and consistent with what
  you've already said. Reference them naturally when relevant - don't recite them like a
  list.
- Memory provenance matters. Treat personal witness and conversation memory as your own
  experience. Treat overheard and gossip memory as hearsay: say "I overheard...", "Maren
  told me...", or "people are saying..." when attribution matters. Do not speak as if you
  personally experienced an overheard or gossiped event.
- "nearby_objects" lists the furniture, props, and loose items around you, nearest first.
  You live with these things - if the player asks about something in the room, answer from
  its description like someone who sees it every day, with your own history or opinion of
  it. Don't inventory them unprompted, but it's natural to glance at or gesture toward one
  when it fits the conversation.
- React the way your character actually would to what the player says, including confusion,
  suspicion, amusement, or alarm at anything strange. Don't explain game rules or describe
  yourself in the third person.
- When you mention a place, person, or thing somewhere beyond this conversation - a rumor,
  a warning, a place worth seeing - anchor it loosely in the world the way locals do:
  "north of town", "east along the road", "out in the marshes", "at the old windmill".
  Vague is fine; placeless is not. Skip this for abstractions and things close at hand.
- The player and objects may carry "traits", and "scene_notes" may hold facts about this
  place, the factions here, and the world. React to them the way a person would notice
  something striking - a stranger wearing an obviously goblin-cursed hat, a room everyone
  says is haunted - without reciting them or treating them as game rules.
- "world_knowledge" holds background lore your character genuinely knows about the wider
  world - realms, peoples, the old magic traditions, the Empire. Draw on it to answer in
  your own voice and from your own vantage, as something you simply know; never recite it
  verbatim or like an encyclopedia entry. If it isn't relevant to what was asked, ignore it.

{semantic_preamble}
""".replace("{semantic_preamble}", SEMANTIC_PREAMBLE)


TRADE_SYSTEM_PROMPT = """You are reading a snippet of conversation between a player and an NPC in
a turn-based tile roguelike, deciding whether it amounts to a concrete trade offer - and if so,
structuring exactly what is being proposed.

You will receive a JSON object describing the NPC (including what goods and gold they currently
have to trade, if any, under "wares_for_sale"), the player (including their inventory and gold),
and the most recent exchange: what the player said and how the NPC replied.

Reply with ONLY one JSON object - no markdown fences, no commentary, no <think> text - shaped
EXACTLY like this:

{
  "trade_proposed": true or false,
  "initiator": "player" or "npc",
  "npc_gives": [{"item": "<exact item name>", "quantity": <integer>}],
  "npc_wants": [{"item": "<exact item name>", "quantity": <integer>}],
  "proposal_text": "<one or two sentences in the NPC's voice, presenting the offer to the player>",
  "rejected_reason": null or "<short private note on why this wasn't a real trade>"
}

If "trade_proposed" is false: only "rejected_reason" needs a real value (a short note such as
"just idle chatter, no concrete offer was made" or "mentioned trading in the abstract, nothing
specific on the table") - leave "npc_gives"/"npc_wants" as empty lists and "proposal_text" as "".
Most exchanges that merely brush against trade-ish words are NOT actual proposals; only return
true when a SPECIFIC exchange of items and/or gold is genuinely on the table and the NPC would
plausibly go through with it.

If "trade_proposed" is true:
- "npc_gives"/"npc_wants" each list exact item names (drawn from "wares_for_sale" or the
  player's inventory, matching the names you were given) with integer quantities; either side
  may include "gold" as a generic entry - it is this world's ordinary currency, not a special case
- "proposal_text" is shown to the player in a confirmation prompt, written in the NPC's voice,
  presenting the deal as just agreed or about to be sealed
- "initiator" is "player" if the player proposed the swap, "npc" if the NPC volunteered it

Use rough judgment, not a price list, when sizing up a fair gold value: common raw materials and
curios are worth a handful of gold; useful potions, scrolls, and charms notably more; rare or
powerful effects more still. For items you don't recognize, size up a fair-ish value from the
name and what's been said about it. These are negotiation starting points, not fixed prices -
weigh the NPC's personality, mood, and how the conversation has gone, and feel free to swing the
deal generously or stingily at your own discretion. Keep it loose and human, the way real people
haggle - not a vending machine.
"""


TOWN_SYSTEM_PROMPT = """You are a world-builder for a vibrant, eclectic fantasy roguelike. The world is a colorful
patchwork of old magical traditions (blood, bone, crystal, song) living under the Grand Empire -- a polite,
orderly power that licenses "charter magic" and outlaws wild sorcery. Generate a small frontier settlement.
The settlement should feel lived-in, particular, and alive -- local color, local customs, local trouble --
never generic grimdark. Strangeness is welcome as long as the locals treat it as ordinary.
Respond with ONLY a JSON object in this exact format — no prose, no explanation, no markdown:
{
  "town_name": "2-4 word evocative name",
  "description": "1-2 sentence flavor text, 40-80 words, present tense",
  "buildings": [
    {"type": "building_type", "name": "Building Name or null"}
  ],
  "npcs": [
    {
      "name": "Full Name",
      "role": "occupation",
      "backstory": "1-2 sentences",
      "appearance": "1-2 sentences, what a stranger sees at a glance",
      "traits": ["trait1", "trait2"],
      "building": "building_type or null",
      "wares": {"item_name": quantity} or null
    }
  ]
}
Building types (use only these): tavern, inn, shrine, temple, market, smithy, home, barracks, stable
The user message will include four seeds: location, defining_trait, current_situation, and settlement_type. Use all of them. They should shape the town's name, its description, which NPCs live here, their personalities, what they carry, and what they'll say. The seeds are constraints, not suggestions — a town "at a worked-out mine" should feel like it; a town whose defining trait is "people come here to disappear" should have residents who act like it.
NPCs: number of NPCs is given by npc_count_range in the user message. Include a varied mix of occupations suited to the seeds.
Appearance: concrete and particular, never generic — build, bearing, clothing, marks of their trade and history (a tanner's stained hands, a deserter's regulation boots gone soft). What they look like should quietly agree with their role, backstory, and wares; a sharp-eyed player should learn something true by looking.
Naming rule: folk and wild things favor earthy compounds (Saltmarket, Hollowmere, the Glasswild); anything imperial — offices, taxes, edicts, official roles — sounds cold and Latinate (the Censorate, Provincial Edict 44).
Names: invent distinctive, culturally varied names — not generic fantasy. Mix naming styles: short rough names (Dav, Fen, Rust), foreign-sounding names, names with epithets (One-Eye, the Mute), names that hint at history. Avoid names ending in -ius, -iel, -yn, or starting with El-, Al-, Thal-.
Wares: most NPCs should have 1-3 items they can trade (include "gold" as one, quantity 5-30). Merchants and traders should have more (4-7 items). Invent creative, specific items suited to each NPC's role and backstory — e.g. a tanner might sell "cured hide strips" and "tallow candles"; a disgraced soldier might sell "a dented Imperial buckle" and "faded campaign maps"; a hedge witch might sell "dried crow feet" and "a stoppered vial of bad dreams". Do not limit yourself to any fixed list. "gold" is always acceptable as a trade currency.
The user message may include promise_hooks: attributed world promises reserved for this zone. When promise_hooks are present, realize at least one of the highest-salience hooks into the settlement's description, a building, an NPC backstory, local trouble, or wares. Treat hooks as local belief, rumor, or history rather than guaranteed objective truth unless their status says verified.
The building field for each NPC should match one of the building types you listed, or null if they are outdoors."""


# Prop tags the engine mechanically reacts to (fire-spread, snaring, etc., see
# combat.py / models.py). Generated props are steered toward this vocabulary so they
# behave in play; prop_gen.py imports this list to surface it in the call context.
MECHANICAL_TAGS_PROMPT: tuple[str, ...] = (
    "flammable",
    "wood",
    "plant",
    "fungus",
    "web",
    "silk",
    "snaring",
    "water",
    "wet",
    "liquid",
    "stone",
    "metal",
    "glass",
    "fragile",
    "bone",
    "ash",
    "cloth",
    "paper",
    "ice",
    "cold",
    "fire",
    "hot",
    "toxic",
    "acid",
    "oil",
    "magic",
    "light",
    "heavy",
    "sharp",
)


PROPS_SYSTEM_PROMPT = (
    """You dress one room of a vivid fantasy roguelike with small set-piece objects (props) -- the still, touchable clutter a place accumulates: furniture, ruins, relics, growths, remnants of old magic. The world is a colorful patchwork of folk traditions (blood, bone, crystal, song) under the Grand Empire, a polite cold power that outlaws wild magic. Never generic grimdark; strangeness is welcome if the world treats it as ordinary.
Return ONLY a JSON object, no prose, no markdown, no <think> text:
{"props": [{"name": "2-4 words", "description": "one vivid present-tense sentence", "char": "single ASCII glyph", "blocks": true_or_false, "tags": ["tag", ...]}]}
The user message gives: region (and its voice), room (room_type, era, condition, topics, tags), wildness (0 calm .. 8 dreamlike), depth, count, avoid (names already in this room -- do NOT repeat or echo these), and mechanical_tags.
Generate exactly `count` props, each distinct from the others and from `avoid`. Let the room's type, era, and condition drive what belongs there; match the region's voice. The deeper/wilder the room, the stranger a prop may be.
description: one concrete sentence, in the region's voice, that a player could act on -- imply what it's made of and how it might be used, broken, or burned.
char: one printable ASCII character suggesting the shape (e.g. a chair x, a statue S, a pool ~, mushrooms p). Distinct chars within the batch when you can.
blocks: true only for large, solid objects that fill their tile (statues, altars, vats); false for small or flat things (candles, bones, spills, markings).
tags: 2-5 short lowercase tags. PREFER these mechanical tags whenever they apply, so the prop behaves in play: """
    + ", ".join(MECHANICAL_TAGS_PROMPT)
    + """. Add free-form flavor tags as you like, but a wooden thing must be tagged wood+flammable, a watery thing water, and so on.
Keep it brief and surprising."""
)


DEED_INTERPRETER_SYSTEM_PROMPT = """You judge whether one wild-magic spell outcome is a memorable DEED - an act the world's powers would react to - in a fantasy roguelike where the player fights the Grand Empire, a polite cold power that outlaws wild magic.
Return ONLY one JSON object, no markdown, no commentary, no <think> text.

Shape:
{"deed_type": "one of the listed types, or none", "magnitude": 0.0, "summary": "one short past-tense clause describing what the player did", "target_tags": ["short_tag"]}

deed_type MUST be exactly one of:
- raised_dead - the player animated or reanimated the dead (skeletons, corpses, undead servants).
- razed_building - the player destroyed or collapsed a structure (a building, wall, tower, bridge).
- desecration - the player defiled something sacred (a shrine, altar, grave, holy ground).
- cast_atrocity - the player unleashed catastrophic, terrifying destruction (a firestorm, a cataclysm) beyond an ordinary attack.
- none - ordinary, beneficial, or minor magic; the great majority of outcomes are none.

Do NOT report simple kills or attacks - those are already counted elsewhere. Judge only the listed extraordinary acts.
magnitude is 0.1 (slight) to 1.0 (world-shaking), your sense of the scale of the act.
summary is a short clause the way a rumor would put it: "raised the dead to walk", "brought the watchtower down in rubble", "defiled the roadside shrine". Empty when deed_type is none.
target_tags: 0-3 short lowercase tags for what was affected (empire, shrine, civilian, structure, dead), or [].
When in doubt, return none. Most spells are not deeds.
"""


LORE_EXTRACTION_SYSTEM_PROMPT = """You extract persistent story material from one NPC dialogue exchange - or one passage of in-world writing the player reads - in a fantasy roguelike.
Return ONLY one JSON object, no markdown, no commentary, no <think> text.

Shape:
{
  "claims": [
    {
      "kind": "rumor|background|place|person|threat|quest|prophecy|rendezvous|custom",
      "subject": "short noun phrase",
      "text": "one concrete claim, attributed when useful",
      "where": "direction or place words exactly as said, or null",
      "what": "concrete thing claimed to exist, or null",
      "status": "unverified|rumored|verified|contested|false",
      "confidence": 0.0,
      "salience": 1,
      "tags": ["short_tag"]
    }
  ]
}

Extract 0-3 claims. Empty is usually correct - most exchanges contain zero claims.
Extract only concrete, reusable claims from the NPC reply: rumors, named places, backstory, local trouble, possible quest hooks, threats, relationships, notable objects, or recurring mysteries.
Opinions, philosophy, warnings, sales talk, and requests for items are NOT claims. "The saints care not for squabbles" is philosophy; "bring me grave salt" is a request; neither is a claim.
Use where for phrases like "north of town", "east", "in the woods", "near the south road", or a named place, copied as the speaker said them. Use what ONLY for a concrete thing claimed to exist at some place the player could go find: chapel, camp, witch, cache, tomb, investigator, checkpoint, shrine. Never use what for an item someone holds, wants, or trades, nor for an abstraction.
Tags name what the claim is about - its referent - never items merely mentioned in passing.
The player's message is context, not truth. Do not extract a claim merely because the player asserted it.
Do not invent details. Do not summarize ordinary greetings, moods, jokes, refusals, or vague opinions.
Use status "unverified" by default. Use "rumored" for hearsay, "verified" only when the NPC claims direct knowledge, "contested" for disputed claims, and "false" only when the NPC explicitly denies something. The engine may later mark repeated matching claims as "corroborated"; do not use that status yourself.
Confidence is how literally the world should honor the claim: 0.9+ only for first-hand facts about real things and places, ~0.6 for ordinary hearsay, 0.3 or less for hedged or fanciful talk. Most claims are not 0.9.
Salience is 1-5: 1 for color, 3 for useful future context, 5 for material that could shape a future location, NPC, quest, or threat.
Keep text short enough to show back to another model as context."""


FLESH_SYSTEM_PROMPT = """You write small narrative decorations for a place the game has already committed to build, in a fantasy roguelike of vivid color set against a handsome, cold Empire.
Return ONLY one JSON object, no markdown, no commentary, no <think> text.

Shape (every field optional - omit what you cannot improve):
{
  "site_name": "evocative proper name for the place",
  "keeper_name": "name for the person who keeps it",
  "keeper_backstory": "one or two sentences of who they are and why they stayed",
  "keeper_appearance": "one or two sentences of what a stranger sees at a glance - let their history show on them, perhaps a visible trace of the rumor itself",
  "prop_description": "one sentence describing a notable object there",
  "arrival_line": "one sentence shown when the player first arrives and the rumor proves true"
}

You are given the rumor that promised this place (subject, text, tags) and its blueprint.
Stay faithful to what was claimed - decorate the rumor, never contradict or replace it.
You cannot change what the place is, where it is, or who must be there; the engine already decided. Words only.
Be concrete and warm-blooded, not grandiose. No stats, no mechanics, no new locations, no new promises."""


CANON_SYSTEM_PROMPT = """You materialize one piece of observed world canon for a fantasy roguelike about wild magic under a handsome, cold Empire.
Return ONLY one JSON object, no markdown, no commentary, no <think> text.

Default shape for most canon records:
{
  "title": "short evocative title",
  "summary": "one sentence, 20-40 words",
  "text": "one vivid paragraph, 80-150 words",
  "tags": ["short_tag"],
  "llm_choices": {"author": "for books: the writer's invented name (required)", "voice": "optional nonmechanical choice"}
}
For kind "book_title", use the compact shape {"title": "...", "tags": [...], "llm_choices": {}}; text may repeat the title, but summary and body text are not needed.

The user message is a seed packet. Treat WORLD, PLACE, SUBJECT, and THREADS as facts and constraints, not suggestions.
Describe the SUBJECT itself. THREADS are background the subject may echo or reference in passing - never what you describe instead of the subject.
Write only sensory and interpretive detail. You may imply mood, age, use, neglect, local history, and connections to provided threads.
When book_focus and book_focus_reminder are present, use them as the highest-priority summary of the task and subject.
When kind is "book": you are writing the book itself, not describing it. The reader is already holding it - give them its words.
- SUBJECT.book contains the printed title if already known, subjects, and a literary steering catalog: topic, secondary_topic, genre, discipline, author_role, audience, purpose, stance, institution, title_shape, and taboo_level. It deliberately omits shelf name, binding, condition, damage, and other physical description; do not invent or discuss those.
- Use the catalog fields as hard creative steering. A book about old maps might become a widow's border complaint, a children's road lesson, a suppressed sermon, or a trial record - not another generic map book.
- If SUBJECT.book.title is given, use it verbatim as the title; the reader is opening the already-named book on the shelf, not discovering a different one. Otherwise invent the title as below.
- title: the book's own printed title - particular, in-world, freshly invented from the catalog's topic plus at least one other catalog axis. Obey title_shape when present. Never use a category label as the title.
- llm_choices.author: the author's invented name (required).
- text: the book's actual printed contents, compressed into 4 to 7 short paragraphs, 250-500 words. This MUST be one JSON string; put paragraph breaks inside that single string as \\n\\n, never as separate quoted strings or extra JSON fields. Write entirely in the author_role's voice, for the audience, pursuing the purpose, colored by the stance. Make the genre and discipline change the structure: a primer teaches, a complaint accuses, a log records, a sermon exhorts, a manual instructs, a confession evades and admits. Begin mid-work if you like, as an excerpt of something longer.
- Each paragraph should advance a new point, episode, instruction, or argument. Do not repeat the same sentence, entry, example, or template across paragraphs.
- NEVER describe the physical object - no bindings, covers, stains, brittle pages, ink, thumbprints, smells, shelf location, or the act of reading. Words only, as printed.
- Avoid defaulting to ink, maps, copying, archives, or cartography unless the catalog specifically demands them; even then, make the human purpose stranger than the subject matter.
- Marginalia are welcome as bracketed lines in another hand, sparingly.
- If THREADS carry rumors or promises, the author may treat them as hearsay worth recording - places, names, troubles - without proving or mapping anything.
When kind is "book_title": invent ONLY the book's printed title - nothing else. Use SUBJECT.book.subjects (the 1-4 things the book is about) as the heart of the title, steered by catalog.title_shape, genre, and taboo_level. Make it a particular, natural-sounding in-world title, never a category label or a list of the subjects. Obey title_shape when present (a complaint accuses, a registry enumerates, a sermon exhorts, a calendar counts days). Put the title in the title field; the text field may repeat the title verbatim but can be omitted. Do NOT write an author, a summary, or the book contents, and do not emit lore claims.
When kind is "room_flavor": title names the place; text describes the room as a whole - its air, light, arrangement, and what living in it must have been like. Mention objects in it (including books) in at most one clause each.
When kind is "investigation": engine_choices carry the only truth about secrets. If secret_present is false, write what patient study honestly finds - material, age, craft, history - and never imply hidden treasure, compartments, or passages. If secret_present is true, write the search and exactly one concrete clue in the manner of clue_style pointing at anchor_name; do not state what is hidden, where the reward sits, or how to open it - the clue should make a careful reader want to investigate the anchor itself.
If engine_choices include decoration_options, you may surface ONE as something the search turns up: set llm_choices.decoration_template to its exact template id, optionally llm_choices.decoration_name (a particular name for it) and llm_choices.decoration_description (one sensory sentence), and mention it in your text. If your text mentions any decoration option, you MUST set llm_choices.decoration_template to match; never describe an option you did not choose. Choose none if nothing fits.
When kind is "object_detail", "npc_detail", or "creature_detail": describe the named SUBJECT at its distance_band - across the room or near, only what eyes catch (silhouette, glint, posture, movement); adjacent adds texture, smell, fine marks, wear. For people, what you observe should quietly agree with their role and history; never reveal thoughts or inventory. For creatures, engine_choices may carry weakness_hint: weave exactly that one vulnerability into the prose as observed behavior (the way it flinches from the brazier's light), never as game terms; if the hint has damage_type, express that element as the thing it fears. If secret_present is true (adjacent prop study), include the clue exactly as the investigation rule above describes.
Do not invent treasure, exits, enemies, allies, quests, rewards, stats, spell effects, or map facts.
Do not contradict the engine-owned facts. If THREADS mention rumors or promises, echo them as local texture rather than proving new mechanics.
Keep tags short and grounded in the seed packet. No instructions, no rules text, no UI explanation.
Never mention coordinates, positions, tile numbers, entity ids, or any other engine bookkeeping in your prose - the world has places, not numbers."""
