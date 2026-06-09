from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PropTemplate:
    id: str
    char: str
    name: str
    description: str
    blocks: bool = False
    tags: set[str] = field(default_factory=set)

PROP_TEMPLATES = {
    # ── Arcane & Ritual ───────────────────────────────────────────────────────
    "whispering_font": PropTemplate("whispering_font", "Y", "whispering font", "Dark water ripples here without a breeze.", True, {"stone", "water", "magic"}),
    "shattered_altar": PropTemplate("shattered_altar", "T", "shattered altar", "A ruined stone slab smelling faintly of ozone.", True, {"stone", "holy", "broken"}),
    "crystal_monolith": PropTemplate("crystal_monolith", "I", "crystal monolith", "A jagged spar of quartz that hums when you stand near it.", True, {"stone", "crystal", "magic"}),
    "chalk_circle": PropTemplate("chalk_circle", "O", "chalk circle", "Complex geometric lines drawn in bone-white dust.", False, {"powder", "magic", "ritual"}),
    "scattered_grimoire_pages": PropTemplate("scattered_grimoire_pages", "~", "scattered grimoire pages", "Torn parchment covered in frantic, glowing script.", False, {"paper", "flammable", "lore"}),
    "iron_brazier": PropTemplate("iron_brazier", "0", "iron brazier", "Coals smolder stubbornly within.", True, {"metal", "fire", "hot"}),
    "suspended_orb": PropTemplate("suspended_orb", "o", "suspended orb", "A glass sphere hanging in mid-air, filled with swirling gray smoke.", False, {"glass", "magic", "fragile"}),
    "summoning_circle": PropTemplate("summoning_circle", "@", "summoning circle", "A pentagram scorched black into the floor, still warm at the center.", False, {"magic", "ritual", "fire"}),
    "arcane_mirror": PropTemplate("arcane_mirror", "M", "arcane mirror", "A silver-backed glass showing the room from a slightly different angle.", True, {"glass", "metal", "magic", "fragile"}),
    "runic_tablet": PropTemplate("runic_tablet", "=", "runic tablet", "A flat stone etched with dozens of interlocking runes, some still faintly lit.", False, {"stone", "magic", "lore"}),
    "null_stone": PropTemplate("null_stone", "n", "null stone", "A sphere of flat gray stone that seems to swallow all light within a foot of it.", False, {"stone", "magic", "antimagic"}),
    "obsidian_mirror": PropTemplate("obsidian_mirror", "M", "obsidian mirror", "Polished volcanic glass reflecting shapes that are not quite yours.", True, {"stone", "magic", "fragile"}),
    "ritual_dagger": PropTemplate("ritual_dagger", "/", "ritual dagger", "A black-iron blade stabbed into the floor, hilt-up, still vibrating faintly.", False, {"metal", "sharp", "magic", "ritual"}),
    "bone_idol": PropTemplate("bone_idol", "i", "bone idol", "A small figure carved from a single vertebra, hollow-eyed.", False, {"bone", "magic", "cursed"}),
    "leaking_mana_crystal": PropTemplate("leaking_mana_crystal", "*", "leaking mana crystal", "A fractured gem weeping thin threads of blue light onto the floor.", False, {"crystal", "magic", "light"}),
    "celestial_orrery": PropTemplate("celestial_orrery", "Q", "celestial orrery", "A brass model of heavenly bodies still turning on hidden springs.", True, {"metal", "magic", "mechanical"}),
    "cursed_candle": PropTemplate("cursed_candle", "!", "cursed candle", "A black taper that burns without flame, giving off cold instead of warmth.", False, {"wax", "magic", "cursed", "cold"}),
    "sigil_of_warding": PropTemplate("sigil_of_warding", "^", "sigil of warding", "A ward burned into the floor, edges still smoking as though freshly made.", False, {"magic", "ritual", "fire"}),
    "enchanted_loom": PropTemplate("enchanted_loom", "E", "enchanted loom", "A wooden loom trailing thread that glows faintly and resists cutting.", True, {"wood", "magic", "cloth"}),
    "canopic_jar": PropTemplate("canopic_jar", "j", "canopic jar", "A sealed alabaster jar covered in protective sigils. It feels warm.", False, {"stone", "magic", "ritual", "death"}),
    "astrolabe": PropTemplate("astrolabe", "a", "astrolabe", "A brass instrument on a stand, tracking something that isn't the stars.", True, {"metal", "magic", "mechanical"}),
    "bone_circle": PropTemplate("bone_circle", "o", "bone circle", "A ring of finger bones arranged with obsessive care on the floor.", False, {"bone", "magic", "ritual", "death"}),
    "ley_confluence": PropTemplate("ley_confluence", "+", "ley confluence", "A point where invisible lines of force intersect, marked by a hairline crack in every nearby stone.", False, {"magic", "invisible"}),
    "sealed_phylactery": PropTemplate("sealed_phylactery", "P", "sealed phylactery", "A lead box no larger than a fist, sealed with seven wax impressions.", False, {"metal", "magic", "death", "cursed"}),
    "cracked_scrying_bowl": PropTemplate("cracked_scrying_bowl", "c", "cracked scrying bowl", "A shallow silver dish split in two. The water it held has dried to a dark residue.", False, {"metal", "magic", "broken", "water"}),
    "arcane_focus_pedestal": PropTemplate("arcane_focus_pedestal", "T", "arcane focus pedestal", "A stone column with a recessed top, sized to hold something spherical. Now empty.", True, {"stone", "magic"}),
    "spell_trap_rune": PropTemplate("spell_trap_rune", "?", "spell trap rune", "A compressed glyph painted onto the floor in iridescent ink, coiled tight.", False, {"magic", "trap", "ritual"}),

    # ── Ruined & Abandoned ────────────────────────────────────────────────────
    "rotting_bookshelf": PropTemplate("rotting_bookshelf", "B", "rotting bookshelf", "Slumping timber holding only dust and mold.", True, {"wood", "flammable", "broken"}),
    "overturned_cart": PropTemplate("overturned_cart", "C", "overturned cart", "Spilled earth and broken wheels.", True, {"wood", "flammable", "debris"}),
    "rusted_iron_maiden": PropTemplate("rusted_iron_maiden", "U", "rusted iron maiden", "Red rust flakes from the spikes lining its open door.", True, {"metal", "sharp"}),
    "faded_tapestry": PropTemplate("faded_tapestry", "|", "faded tapestry", "Moth-eaten threads depicting a forgotten victory.", False, {"cloth", "flammable"}),
    "crumbling_statue": PropTemplate("crumbling_statue", "S", "crumbling statue", "A faceless figure missing both its arms.", True, {"stone", "broken"}),
    "smashed_crate": PropTemplate("smashed_crate", "#", "smashed crate", "Splintered pine smelling of old spices.", True, {"wood", "flammable", "debris"}),
    "collapsed_shelf": PropTemplate("collapsed_shelf", "\\", "collapsed shelf", "Boards and splinters fanned across the floor like a broken hand.", False, {"wood", "flammable", "debris"}),
    "broken_mirror": PropTemplate("broken_mirror", "m", "broken mirror", "Seven reflections, all slightly wrong.", False, {"glass", "fragile", "broken", "magic"}),
    "torn_bedroll": PropTemplate("torn_bedroll", ",", "torn bedroll", "Mold-eaten fabric, abandoned with suspicious haste.", False, {"cloth", "flammable"}),
    "old_campfire_ash": PropTemplate("old_campfire_ash", ".", "old campfire ash", "Gray dust and blackened stones circled as if for warmth.", False, {"ash", "cold", "debris"}),
    "abandoned_pack": PropTemplate("abandoned_pack", "q", "abandoned pack", "A leather satchel spilling its rotted contents.", False, {"leather", "flammable", "debris"}),
    "warped_door": PropTemplate("warped_door", "D", "warped door", "Removed from its hinges and propped against the wall. The wood has twisted badly.", True, {"wood", "flammable", "broken"}),
    "shattered_lantern": PropTemplate("shattered_lantern", "l", "shattered lantern", "Twisted tin and a few shards of glass on the floor.", False, {"metal", "glass", "broken"}),
    "discarded_armor": PropTemplate("discarded_armor", "A", "discarded armor", "A dented breastplate, still smelling faintly of the man who wore it.", False, {"metal", "broken"}),
    "broken_wheel": PropTemplate("broken_wheel", "W", "broken wheel", "Spokes radiating from a cracked hub. One spoke is missing.", False, {"wood", "broken", "debris"}),
    "old_rope_coil": PropTemplate("old_rope_coil", "r", "old rope coil", "Frayed hemp coiled on a peg. Probably not safe.", False, {"rope", "flammable"}),
    "rusted_portcullis_fragment": PropTemplate("rusted_portcullis_fragment", "#", "rusted portcullis fragment", "A section of iron grillwork, bent in half and discarded.", True, {"metal", "broken", "sharp"}),
    "collapsed_table": PropTemplate("collapsed_table", "f", "collapsed table", "Four legs up, surface smashed inward.", True, {"wood", "flammable", "broken"}),
    "empty_chest": PropTemplate("empty_chest", "[", "empty chest", "A lockbox with the lid thrown back. Whatever was inside is long gone.", True, {"wood", "metal", "broken"}),
    "cracked_flagstone_pile": PropTemplate("cracked_flagstone_pile", ";", "cracked flagstone pile", "Fragments of floor tile stacked as though someone tried to clean up.", True, {"stone", "debris"}),
    "tipped_brazier": PropTemplate("tipped_brazier", "b", "tipped brazier", "An iron brazier on its side, cold coals scattered across the floor.", False, {"metal", "ash", "cold"}),

    # ── Macabre & Somber ──────────────────────────────────────────────────────
    "pile_of_skulls": PropTemplate("pile_of_skulls", "%", "pile of skulls", "Yellowed craniums stacked with unsettling neatness.", False, {"bone", "undead"}),
    "blood_stained_torture_rack": PropTemplate("blood_stained_torture_rack", "X", "blood-stained torture rack", "Heavy leather straps and dark, sticky stains.", True, {"wood", "metal", "blood"}),
    "open_sarcophagus": PropTemplate("open_sarcophagus", "[", "open sarcophagus", "The heavy lid has been shoved aside. It is empty.", True, {"stone", "death"}),
    "hanging_cage": PropTemplate("hanging_cage", "8", "hanging cage", "A rusted gibbet swinging faintly from a broken chain.", False, {"metal", "prison"}),
    "mummified_remains": PropTemplate("mummified_remains", "m", "mummified remains", "Wrapped in brittle linen, clutching nothing.", False, {"flesh", "dry", "flammable"}),
    "fresh_blood_pool": PropTemplate("fresh_blood_pool", "~", "fresh blood pool", "A dark smear that refuses to dry.", False, {"blood", "wet", "liquid"}),
    "shallow_grave": PropTemplate("shallow_grave", ".", "shallow grave", "A thin layer of dirt piled unnervingly neatly.", False, {"earth", "death"}),
    "noose": PropTemplate("noose", ")", "noose", "A rope loop hanging from an iron spike driven into the ceiling.", False, {"rope", "death"}),
    "dissection_table": PropTemplate("dissection_table", "T", "dissection table", "A marble slab with drainage channels cut at precise angles.", True, {"stone", "blood", "death"}),
    "jar_of_teeth": PropTemplate("jar_of_teeth", "j", "jar of teeth", "A glass jar full of human teeth, sorted meticulously by size.", False, {"glass", "bone", "death"}),
    "funeral_pyre_remnants": PropTemplate("funeral_pyre_remnants", ".", "funeral pyre remnants", "Cold ash and bone fragments deliberately arranged in a sunburst pattern.", False, {"ash", "bone", "death", "cold"}),
    "child_sized_coffin": PropTemplate("child_sized_coffin", "[", "child-sized coffin", "Small, plain wood, nailed shut. The wood is fresh.", True, {"wood", "death", "somber"}),
    "preserved_hand": PropTemplate("preserved_hand", "J", "preserved hand", "A severed hand floating in a jar of cloudy fluid.", False, {"glass", "flesh", "death"}),
    "death_mask": PropTemplate("death_mask", "d", "death mask", "A plaster cast of a face wearing no particular expression.", False, {"stone", "death"}),
    "inscribed_gravestone": PropTemplate("inscribed_gravestone", "g", "inscribed gravestone", "A marker bearing only a single letter. The rest has been deliberately removed.", True, {"stone", "death", "lore"}),
    "embalming_station": PropTemplate("embalming_station", "e", "embalming station", "A stone table with iron hooks and drained jars of dark fluid.", True, {"stone", "metal", "death", "blood"}),
    "iron_spike_wall": PropTemplate("iron_spike_wall", "W", "iron spike wall", "Rusted spikes jutting from the wall at eye level.", True, {"metal", "sharp", "blood"}),
    "bone_throne": PropTemplate("bone_throne", "K", "bone throne", "A chair assembled from large bones, mortared together. Something sat here often.", True, {"bone", "death", "undead"}),
    "sealed_burial_urn": PropTemplate("sealed_burial_urn", "u", "sealed burial urn", "A terracotta urn wax-sealed at the top. It rattles when tilted.", False, {"stone", "death", "ash"}),
    "crow_perch": PropTemplate("crow_perch", "p", "crow perch", "A wooden stand with a dead crow still sitting on it, stiff and glossy-eyed.", False, {"wood", "death", "flesh"}),
    "gibbet_chain": PropTemplate("gibbet_chain", "}", "gibbet chain", "A ceiling chain ending in a hook worn smooth from use.", False, {"metal", "death", "prison"}),
    "mass_grave_marker": PropTemplate("mass_grave_marker", "x", "mass grave marker", "A flat stone with dozens of tally marks scratched into it.", False, {"stone", "death", "somber"}),

    # ── Natural & Overgrown ───────────────────────────────────────────────────
    "bioluminescent_mushroom": PropTemplate("bioluminescent_mushroom", "p", "bioluminescent mushroom", "Casting a pale, sickly green glow on the stones.", False, {"plant", "fungus", "light"}),
    "thick_bramble": PropTemplate("thick_bramble", "&", "thick bramble", "Thorny vines gripping the floor tightly.", True, {"plant", "wood", "snaring", "flammable"}),
    "subterranean_pool": PropTemplate("subterranean_pool", "~", "subterranean pool", "Stagnant, icy water gathering in a depression.", False, {"water", "wet", "liquid"}),
    "petrified_tree_trunk": PropTemplate("petrified_tree_trunk", "t", "petrified tree trunk", "Ancient wood turned hard as iron by time.", True, {"wood", "stone"}),
    "pulsing_pod": PropTemplate("pulsing_pod", "O", "pulsing pod", "A leathery sack that breathes in slow rhythm.", True, {"flesh", "alien"}),
    "guano_pile": PropTemplate("guano_pile", "%", "guano pile", "A caustic heap of bat droppings.", False, {"feces", "toxic", "smelly"}),
    "giant_spider_web": PropTemplate("giant_spider_web", "w", "giant spider web", "Thick cables of silk spanning a whole corner, sticky with old husks.", False, {"silk", "snaring", "flammable"}),
    "lichen_column": PropTemplate("lichen_column", "l", "lichen-covered column", "A stone pillar draped entirely in gray-green growth.", True, {"stone", "plant", "fungus"}),
    "mushroom_ring": PropTemplate("mushroom_ring", "o", "mushroom ring", "A perfect circle of pale mushrooms that all lean slightly inward.", False, {"plant", "fungus", "magic"}),
    "underground_spring": PropTemplate("underground_spring", "~", "underground spring", "Water welling from a crack in the floor, ice cold and perfectly clear.", False, {"water", "wet", "liquid", "cold"}),
    "crystal_formation": PropTemplate("crystal_formation", "*", "crystal formation", "Mineral deposits jutting from the wall like teeth, sharp and colorless.", True, {"crystal", "stone", "sharp"}),
    "carnivorous_vine": PropTemplate("carnivorous_vine", "&", "carnivorous vine", "A dark, ropy vine that slowly tracks movement.", True, {"plant", "snaring", "alien"}),
    "moss_covered_bones": PropTemplate("moss_covered_bones", ";", "moss-covered bones", "Bones so old they have become part of the floor, dressed in green.", False, {"bone", "plant", "death"}),
    "cave_coral": PropTemplate("cave_coral", "^", "cave coral", "Pink branching formations growing from the ceiling like frozen lightning.", False, {"stone", "alien", "fragile"}),
    "petrified_nest": PropTemplate("petrified_nest", "n", "petrified nest", "A bird's nest turned to stone, still holding three stone eggs.", False, {"stone", "fragile"}),
    "ancient_root": PropTemplate("ancient_root", "R", "ancient root", "A root thicker than a man's torso pushing through the wall, trailing smaller tendrils.", True, {"wood", "plant", "snaring"}),
    "glowing_moss_patch": PropTemplate("glowing_moss_patch", ".", "glowing moss patch", "Bioluminescent green growth carpeting a section of floor.", False, {"plant", "fungus", "light"}),
    "termite_mound": PropTemplate("termite_mound", "^", "termite mound", "A papery tower of packed earth and insect saliva, riddled with tunnels.", True, {"earth", "flammable", "insect"}),
    "stagnant_tide_pool": PropTemplate("stagnant_tide_pool", "~", "stagnant tide pool", "A bowl-shaped depression holding foul, rust-colored water.", False, {"water", "wet", "toxic", "liquid"}),
    "dried_fungal_bloom": PropTemplate("dried_fungal_bloom", "f", "dried fungal bloom", "A large shelf fungus, long dead, crackling at the edges.", False, {"fungus", "flammable", "dry"}),
    "strangler_fig_roots": PropTemplate("strangler_fig_roots", "r", "strangler fig roots", "A lattice of pale roots that have crept through the mortar and clasped around a pillar.", True, {"plant", "wood", "snaring"}),
    "acid_seep": PropTemplate("acid_seep", "~", "acid seep", "A slow trickle of yellowish fluid eating a channel through the stone floor.", False, {"liquid", "acid", "toxic", "wet"}),
    "fossilized_creature": PropTemplate("fossilized_creature", "F", "fossilized creature", "Something large pressed flat into the stone, limbs splayed, species unidentifiable.", True, {"stone", "death", "alien"}),

    # ── Dungeon Infrastructure ─────────────────────────────────────────────────
    "heavy_anvil": PropTemplate("heavy_anvil", "A", "heavy anvil", "Cold iron, dented from decades of use.", True, {"metal", "heavy"}),
    "weapons_rack": PropTemplate("weapons_rack", "H", "weapons rack", "Holding only cracked hafts and rusted blades.", True, {"wood", "metal", "weapons"}),
    "water_barrel": PropTemplate("water_barrel", "0", "water barrel", "A coopered barrel smelling of algae.", True, {"wood", "water", "liquid"}),
    "iron_chains": PropTemplate("iron_chains", "}", "iron chains", "Heavy links bolted into the masonry.", False, {"metal"}),
    "sewer_grate": PropTemplate("sewer_grate", "#", "sewer grate", "Foul water drains through these rusted iron bars.", False, {"metal", "water"}),
    "warning_sign": PropTemplate("warning_sign", "+", "warning sign", "A wooden board scrawled with crude, bloody runes.", True, {"wood", "flammable"}),
    "pulley_system": PropTemplate("pulley_system", "P", "pulley system", "Ropes and wheels rigged to lift something heavy. The counterweight is missing.", False, {"metal", "rope", "mechanical"}),
    "drain_channel": PropTemplate("drain_channel", "_", "drain channel", "A stone groove cut into the floor at a slight angle, leading to a plugged hole.", False, {"stone", "water"}),
    "mining_cart": PropTemplate("mining_cart", "V", "mining cart", "A small iron cart sitting on rusted tracks, packed with rubble.", True, {"metal", "heavy", "debris"}),
    "mine_support_beam": PropTemplate("mine_support_beam", "|", "mine support beam", "A timber propped at a worrying angle against the ceiling. The wood is bowing.", True, {"wood", "heavy", "flammable"}),
    "torture_wheel": PropTemplate("torture_wheel", "W", "torture wheel", "A wooden wheel with leather binding points at each spoke end.", True, {"wood", "blood", "death"}),
    "old_well": PropTemplate("old_well", "O", "old well", "A covered stone well. The rope has frayed to a single thread.", True, {"stone", "water"}),
    "cistern": PropTemplate("cistern", "C", "cistern", "A large stone tank, cracked and mostly empty, smelling of stale water.", True, {"stone", "water", "heavy"}),
    "iron_grill_floor": PropTemplate("iron_grill_floor", "#", "iron grill floor", "A section of floor replaced with iron grating. You can see darkness below.", False, {"metal", "heavy"}),
    "pressure_plate": PropTemplate("pressure_plate", "_", "pressure plate", "A stone tile sitting slightly lower than those around it.", False, {"stone", "trap", "mechanical"}),
    "portcullis_mechanism": PropTemplate("portcullis_mechanism", "M", "portcullis mechanism", "A winch and gear assembly for raising a gate. The chain is severed.", True, {"metal", "mechanical", "heavy"}),
    "wall_manacles": PropTemplate("wall_manacles", "{", "wall manacles", "Iron shackles bolted into the stone at two heights.", False, {"metal", "prison"}),
    "iron_door_frame": PropTemplate("iron_door_frame", "D", "iron door frame", "A wrought-iron doorframe with no door. The hinges are enormous.", True, {"metal", "heavy"}),
    "reinforced_pillar": PropTemplate("reinforced_pillar", "I", "reinforced pillar", "A stone column banded with iron straps, as though it cracked and someone cared enough to repair it.", True, {"stone", "metal", "heavy"}),
    "broken_catapult_arm": PropTemplate("broken_catapult_arm", "/", "broken catapult arm", "The wooden throwing arm of a catapult, snapped at the joint and left to rot.", True, {"wood", "flammable", "broken", "heavy"}),
    "siege_ballista": PropTemplate("siege_ballista", "G", "siege ballista", "A large crossbow mechanism mounted on a swivel. Unloaded, rusted, aimed at nothing.", True, {"wood", "metal", "weapons", "heavy"}),

    # ── Alchemical & Laboratory ───────────────────────────────────────────────
    "alchemical_still": PropTemplate("alchemical_still", "Y", "alchemical still", "Glass tubing and copper coils dark with residue, smelling of burnt sulfur.", True, {"glass", "metal", "fire", "toxic"}),
    "reagent_cabinet": PropTemplate("reagent_cabinet", "R", "reagent cabinet", "A wooden case of small drawers. Most are empty; some have faint staining.", True, {"wood", "flammable"}),
    "cracked_retort": PropTemplate("cracked_retort", "q", "cracked retort", "A long-necked glass vessel, blackened inside, split cleanly down one side.", False, {"glass", "fragile", "broken", "toxic"}),
    "mortar_and_pestle": PropTemplate("mortar_and_pestle", "u", "mortar and pestle", "Heavy stone stained deep purple by whatever was ground last.", False, {"stone", "heavy"}),
    "specimen_jars": PropTemplate("specimen_jars", "J", "specimen jars", "A shelf of sealed jars containing floating things you cannot identify.", True, {"glass", "liquid", "fragile", "alien"}),
    "bubbling_vat": PropTemplate("bubbling_vat", "V", "bubbling vat", "A stone basin where something still reacts slowly with the air.", True, {"stone", "toxic", "acid", "liquid"}),
    "silver_scales": PropTemplate("silver_scales", "z", "silver scales", "A balance used to weigh something very precise. Both pans are level.", False, {"metal", "mechanical"}),
    "failed_homunculus": PropTemplate("failed_homunculus", "h", "failed homunculus", "A stoppered vessel containing a small, pale, motionless shape.", False, {"glass", "flesh", "magic", "alien", "death"}),
    "distillation_coil": PropTemplate("distillation_coil", "s", "distillation coil", "A copper tube spiraling through a cooling bath of now-green water.", True, {"metal", "water", "toxic"}),
    "scroll_of_formulas": PropTemplate("scroll_of_formulas", "~", "scroll of formulas", "A partially burned scroll covered in precise measurements and warnings.", False, {"paper", "flammable", "lore"}),
    "reagent_spill": PropTemplate("reagent_spill", "~", "reagent spill", "A dried iridescent smear across the floor and half the wall.", False, {"toxic", "liquid", "dry"}),
    "iron_cage_for_specimens": PropTemplate("iron_cage_for_specimens", "8", "iron specimen cage", "A small iron cage with a latch, sized for something cat-sized.", True, {"metal", "prison"}),
    "electrostatic_coil": PropTemplate("electrostatic_coil", "E", "electrostatic coil", "A copper spiral wound around a glass rod, crackling faintly.", True, {"metal", "glass", "lightning", "magic"}),
    "decay_tank": PropTemplate("decay_tank", "D", "decay tank", "A lead-lined trough containing material at an advanced stage of decomposition.", True, {"metal", "death", "toxic", "smelly"}),

    # ── Religious & Devotional ────────────────────────────────────────────────
    "offering_bowl": PropTemplate("offering_bowl", "c", "offering bowl", "A stone bowl holding calcified offerings arranged in a spiral.", False, {"stone", "holy", "ritual"}),
    "votive_candles": PropTemplate("votive_candles", "!", "votive candles", "Dozens of small candles burned to uneven stubs, many fused together.", False, {"wax", "fire", "holy", "flammable"}),
    "prayer_beads": PropTemplate("prayer_beads", "o", "prayer beads", "A long strand of carved wooden beads knotted around a wall peg.", False, {"wood", "holy"}),
    "saint_statue": PropTemplate("saint_statue", "S", "saint statue", "A robed figure with a chipped face, one hand extended palm-up.", True, {"stone", "holy"}),
    "reliquary": PropTemplate("reliquary", "r", "reliquary", "A small glass case holding a fragment of something that may have been bone.", False, {"glass", "holy", "bone", "fragile"}),
    "holy_water_stoup": PropTemplate("holy_water_stoup", "w", "holy water stoup", "A stone basin near a doorway, long since dry.", True, {"stone", "holy", "water"}),
    "icon_of_the_forgotten": PropTemplate("icon_of_the_forgotten", "k", "icon of the forgotten", "A painted panel showing a deity with the face meticulously scratched away.", False, {"wood", "holy", "cursed"}),
    "blasphemous_inscription": PropTemplate("blasphemous_inscription", "b", "blasphemous inscription", "Words carved directly over an older, holier inscription.", False, {"stone", "cursed", "lore"}),
    "burned_effigy": PropTemplate("burned_effigy", "e", "burned effigy", "A crude humanoid shape in wire and charred wood, still leaking ash.", False, {"wire", "ash", "cursed", "fire"}),
    "altar_of_thorns": PropTemplate("altar_of_thorns", "T", "altar of thorns", "A low stone table wrapped entirely in dried, blackened briars.", True, {"stone", "plant", "sharp", "holy", "cursed"}),
    "ossuary_niche": PropTemplate("ossuary_niche", "N", "ossuary niche", "A wall recess stacked with femurs and skulls arranged by size.", True, {"stone", "bone", "death", "holy"}),
    "torn_prayer_banner": PropTemplate("torn_prayer_banner", "|", "torn prayer banner", "Silk fabric covered in gold script, one half missing entirely.", False, {"cloth", "holy", "flammable"}),
    "sacrificial_pit": PropTemplate("sacrificial_pit", "v", "sacrificial pit", "A shallow stone-lined depression in the floor, stained dark. A ring is bolted to its rim.", False, {"stone", "blood", "death", "ritual"}),
    "temple_bell": PropTemplate("temple_bell", "b", "temple bell", "A bronze bell hanging from an iron arm. The clapper is gone.", True, {"metal", "holy"}),
    "cracked_font": PropTemplate("cracked_font", "Y", "cracked font", "A baptismal basin split down the middle. The holy water drained long ago.", True, {"stone", "holy", "broken", "water"}),

    # ── Furniture & Domestic ──────────────────────────────────────────────────
    "overturned_chair": PropTemplate("overturned_chair", "x", "overturned chair", "Four legs skyward, one broken at the joint.", False, {"wood", "flammable", "broken"}),
    "empty_wardrobe": PropTemplate("empty_wardrobe", "W", "empty wardrobe", "Double doors hanging open, hinges groaning. Nothing inside.", True, {"wood", "flammable"}),
    "writing_desk": PropTemplate("writing_desk", "d", "writing desk", "A slanted surface with ink stains and a broken quill. A few torn pages remain.", True, {"wood", "flammable", "lore"}),
    "iron_bed_frame": PropTemplate("iron_bed_frame", "=", "iron bed frame", "Springs and bare iron. Whatever covered it has been removed.", True, {"metal"}),
    "stone_dining_table": PropTemplate("stone_dining_table", "T", "stone dining table", "Too heavy to have been moved by accident. Set with nothing.", True, {"stone", "heavy"}),
    "rocking_chair": PropTemplate("rocking_chair", "c", "rocking chair", "Still moving very slightly, as though someone rose moments ago.", False, {"wood", "flammable"}),
    "empty_wine_rack": PropTemplate("empty_wine_rack", "r", "empty wine rack", "Iron slots sized for bottles, every last one absent.", True, {"metal"}),
    "iron_chandelier": PropTemplate("iron_chandelier", "*", "iron chandelier", "A hanging wrought-iron ring for candles. Empty and swaying.", False, {"metal", "heavy"}),
    "broken_hourglass": PropTemplate("broken_hourglass", "8", "broken hourglass", "Sand on the floor, glass in the sand. Time stopped here.", False, {"glass", "fragile", "broken"}),
    "locked_chest": PropTemplate("locked_chest", "[", "locked chest", "Iron-banded oak, padlocked. No key in sight.", True, {"wood", "metal"}),
    "painting_of_darkness": PropTemplate("painting_of_darkness", "P", "painting of darkness", "A canvas depicting only absolute void, framed in gilt.", False, {"cloth", "magic", "cursed"}),
    "tattered_map": PropTemplate("tattered_map", "~", "tattered map", "A wall-mounted map with most of the coastline chewed away by rats. One location is circled.", False, {"paper", "flammable", "lore"}),
    "study_globe": PropTemplate("study_globe", "G", "study globe", "A brass globe of a world that doesn't match any map you know.", True, {"metal", "lore"}),
    "empty_birdcage": PropTemplate("empty_birdcage", "8", "empty birdcage", "A small gilded cage hanging from a stand. The door has been bent outward.", True, {"metal"}),
    "cracked_hearth": PropTemplate("cracked_hearth", "n", "cracked hearth", "A fireplace split along its back wall, soot-stained and cold.", True, {"stone", "cold", "ash"}),
}

PROP_CATEGORIES: dict[str, list[str]] = {
    "arcane": [
        "whispering_font", "shattered_altar", "crystal_monolith", "chalk_circle",
        "scattered_grimoire_pages", "iron_brazier", "suspended_orb", "summoning_circle",
        "arcane_mirror", "runic_tablet", "null_stone", "obsidian_mirror", "ritual_dagger",
        "bone_idol", "leaking_mana_crystal", "celestial_orrery", "cursed_candle",
        "sigil_of_warding", "enchanted_loom", "canopic_jar", "astrolabe", "bone_circle",
        "ley_confluence", "sealed_phylactery", "cracked_scrying_bowl",
        "arcane_focus_pedestal", "spell_trap_rune",
    ],
    "ruined": [
        "rotting_bookshelf", "overturned_cart", "rusted_iron_maiden", "faded_tapestry",
        "crumbling_statue", "smashed_crate", "collapsed_shelf", "broken_mirror",
        "torn_bedroll", "old_campfire_ash", "abandoned_pack", "warped_door",
        "shattered_lantern", "discarded_armor", "broken_wheel", "old_rope_coil",
        "rusted_portcullis_fragment", "collapsed_table", "empty_chest",
        "cracked_flagstone_pile", "tipped_brazier",
    ],
    "macabre": [
        "pile_of_skulls", "blood_stained_torture_rack", "open_sarcophagus", "hanging_cage",
        "mummified_remains", "fresh_blood_pool", "shallow_grave", "noose",
        "dissection_table", "jar_of_teeth", "funeral_pyre_remnants", "child_sized_coffin",
        "preserved_hand", "death_mask", "inscribed_gravestone", "embalming_station",
        "iron_spike_wall", "bone_throne", "sealed_burial_urn", "crow_perch",
        "gibbet_chain", "mass_grave_marker",
    ],
    "natural": [
        "bioluminescent_mushroom", "thick_bramble", "subterranean_pool",
        "petrified_tree_trunk", "pulsing_pod", "guano_pile", "giant_spider_web",
        "lichen_column", "mushroom_ring", "underground_spring", "crystal_formation",
        "carnivorous_vine", "moss_covered_bones", "cave_coral", "petrified_nest",
        "ancient_root", "glowing_moss_patch", "termite_mound", "stagnant_tide_pool",
        "dried_fungal_bloom", "strangler_fig_roots", "acid_seep", "fossilized_creature",
    ],
    "infrastructure": [
        "heavy_anvil", "weapons_rack", "water_barrel", "iron_chains", "sewer_grate",
        "warning_sign", "pulley_system", "drain_channel", "mining_cart",
        "mine_support_beam", "torture_wheel", "old_well", "cistern", "iron_grill_floor",
        "pressure_plate", "portcullis_mechanism", "wall_manacles", "iron_door_frame",
        "reinforced_pillar", "broken_catapult_arm", "siege_ballista",
    ],
    "alchemical": [
        "alchemical_still", "reagent_cabinet", "cracked_retort", "mortar_and_pestle",
        "specimen_jars", "bubbling_vat", "silver_scales", "failed_homunculus",
        "distillation_coil", "scroll_of_formulas", "reagent_spill",
        "iron_cage_for_specimens", "electrostatic_coil", "decay_tank",
    ],
    "religious": [
        "offering_bowl", "votive_candles", "prayer_beads", "saint_statue", "reliquary",
        "holy_water_stoup", "icon_of_the_forgotten", "blasphemous_inscription",
        "burned_effigy", "altar_of_thorns", "ossuary_niche", "torn_prayer_banner",
        "sacrificial_pit", "temple_bell", "cracked_font",
    ],
    "furniture": [
        "overturned_chair", "empty_wardrobe", "writing_desk", "iron_bed_frame",
        "stone_dining_table", "rocking_chair", "empty_wine_rack", "iron_chandelier",
        "broken_hourglass", "locked_chest", "painting_of_darkness", "tattered_map",
        "study_globe", "empty_birdcage", "cracked_hearth",
    ],
}


def get_prop_template(template_id: str) -> PropTemplate | None:
    return PROP_TEMPLATES.get(template_id)

def get_all_prop_ids() -> list[str]:
    return list(PROP_TEMPLATES.keys())

def get_props_by_category(category: str) -> list[str]:
    return PROP_CATEGORIES.get(category, [])

def get_nonblocking_prop_ids() -> list[str]:
    return [pid for pid, t in PROP_TEMPLATES.items() if not t.blocks]
