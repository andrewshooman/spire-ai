"""Card knowledge: starting priors, archetypes, and effects parsed from card text.

Priors only seed the learned weights -- the model moves them with experience.
Effects come from each card's printed text (sts-bench's card database), so the
combat policy can value a play without asking a language model.
"""

from __future__ import annotations

import re
from functools import lru_cache

from sts_bench.tools.card_db import card_text

# ---------------------------------------------------------------- priors

TIERS = {
    "IRONCLAD": {
        1.2: ["Offering", "Shrug It Off", "Pommel Strike", "Battle Trance", "Carnage", "Uppercut",
              "Shockwave", "Feed", "Immolate", "Disarm", "Flame Barrier", "Inflame", "Demon Form",
              "Corruption", "Feel No Pain", "Dark Embrace", "Impervious", "Whirlwind", "Reaper",
              "Thunderclap", "Hemokinesis", "Seeing Red"],
        0.6: ["Twin Strike", "Anger", "Cleave", "Iron Wave", "Headbutt", "Clothesline", "Bludgeon",
              "True Grit", "Burning Pact", "Second Wind", "Ghostly Armor", "Metallicize",
              "Spot Weakness", "Heavy Blade", "Sword Boomerang", "Entrench", "Body Slam", "Rupture",
              "Bloodletting", "Fiend Fire", "Sever Soul", "Power Through", "Pummel", "Dropkick",
              "Rage", "Flex", "Armaments", "Sentinel", "Double Tap", "Juggernaut", "Combust",
              "Brutality", "Barricade", "Limit Break", "Exhume", "Dual Wield", "Intimidate",
              "Warcry", "Infernal Blade", "Blood for Blood", "Rampage"],
        0.0: ["Clash", "Wild Strike", "Reckless Charge", "Perfected Strike", "Searing Blow", "Havoc",
              "Berserk", "Evolve", "Fire Breathing"],
    },
}
# The Silent (community tier lists, A0-ish): seeds the learned card:<name> weights like the Ironclad ones.
TIERS["THE_SILENT"] = {
    # scaling defense and the best commons/uncommons (the Silent survives act 2 on Dexterity, Weak and draw)
    1.6: ["Footwork", "Adrenaline", "Noxious Fumes", "Wraith Form", "Malaise", "Piercing Wail", "Leg Sweep",
          "Backflip", "Acrobatics", "Blade Dance", "After Image", "Dash", "Terror", "Glass Knife"],
    1.2: ["Catalyst", "Well-Laid Plans", "Burst", "Die Die Die", "Predator", "Deadly Poison", "Bouncing Flask",
          "Cloak and Dagger", "Corpse Explosion", "Crippling Cloud", "Dodge and Roll", "Blur", "Escape Plan",
          "Bullet Time", "Accuracy", "Tools of the Trade", "Calculated Gamble", "Dagger Spray", "Riddle with Holes",
          "A Thousand Cuts", "Skewer", "Heel Hook", "Sucker Punch"],
    0.6: ["Quick Slash", "Poisoned Stab", "Dagger Throw", "Slice", "Sneaky Strike", "Flying Knee", "Prepared", "Outmaneuver",
          "Deflect", "Caltrops", "Infinite Blades", "Storm of Steel", "Eviscerate", "Unload", "Finisher", "Reflex",
          "Tactician", "Expertise", "Choke", "All-Out Attack", "Phantasmal Killer", "Nightmare", "Alchemize",
          "Backstab", "Bane", "Concentrate", "Doppelganger"],
    0.0: ["Sadistic Nature", "Envenom", "Grand Finale", "Flechettes", "Distraction", "Setup", "Masterful Stab",
          "Endless Agony"],
}
BASIC = {"Strike": -1.0, "Defend": -0.6}
DEFAULT_CARD_PRIOR = 0.3

ARCHETYPES = {
    "IRONCLAD": {
        "Strength": {"Inflame": 1.0, "Spot Weakness": 1.0, "Demon Form": 1.2, "Limit Break": 0.6,
                     "Heavy Blade": 0.4, "Sword Boomerang": 0.25, "Whirlwind": 0.3, "Twin Strike": 0.2,
                     "Pummel": 0.25, "Flex": 0.2, "Reaper": 0.4},
        "Exhaust": {"Corruption": 1.5, "Feel No Pain": 1.2, "Dark Embrace": 1.2, "Burning Pact": 0.6,
                    "Second Wind": 0.6, "True Grit": 0.5, "Fiend Fire": 0.6, "Sever Soul": 0.5,
                    "Sentinel": 0.4, "Exhume": 0.4, "Offering": 0.3},
        "Block": {"Barricade": 1.5, "Entrench": 1.0, "Body Slam": 1.0, "Impervious": 0.6,
                  "Flame Barrier": 0.5, "Metallicize": 0.6, "Juggernaut": 0.8, "Shrug It Off": 0.3,
                  "Ghostly Armor": 0.4, "Power Through": 0.4},
        "Self-damage": {"Rupture": 1.5, "Hemokinesis": 0.6, "Offering": 0.4, "Combust": 0.6,
                        "Bloodletting": 0.6, "Brutality": 0.6, "Reaper": 0.5, "Blood for Blood": 0.5},
    },
    "THE_SILENT": {
        "Poison": {"Noxious Fumes": 1.2, "Deadly Poison": 0.8, "Bouncing Flask": 1.0, "Catalyst": 1.2,
                   "Corpse Explosion": 1.0, "Poisoned Stab": 0.5, "Crippling Cloud": 0.8},
        "Shiv": {"Blade Dance": 1.0, "Cloak and Dagger": 0.8, "Accuracy": 1.2, "After Image": 0.8,
                 "Infinite Blades": 0.8, "Storm of Steel": 0.8, "Finisher": 0.6},
        "Discard": {"Tactician": 0.8, "Reflex": 0.8, "Acrobatics": 0.8, "Calculated Gamble": 0.8,
                    "Eviscerate": 1.0, "Prepared": 0.5, "Concentrate": 0.5},
    },
    "DEFECT": {
        "Frost": {"Glacier": 1.0, "Coolheaded": 0.8, "Chill": 0.6, "Blizzard": 0.8, "Defragment": 1.2,
                  "Loop": 0.8, "Capacitor": 0.6},
        "Lightning": {"Ball Lightning": 0.8, "Electrodynamics": 1.2, "Thunder Strike": 1.0,
                      "Static Discharge": 1.0, "Tempest": 0.6, "Defragment": 0.8},
        "Claw": {"Claw": 1.2, "All for One": 1.2, "Scrape": 0.8, "FTL": 0.6, "Go for the Eyes": 0.4,
                 "Beam Cell": 0.4},
        "Focus": {"Echo Form": 1.2, "Biased Cognition": 1.2, "Creative AI": 0.8, "Buffer": 0.6},
    },
    "WATCHER": {
        "Stance": {"Eruption": 0.8, "Tantrum": 1.0, "Flurry of Blows": 0.8, "Mental Fortress": 1.2,
                   "Rushdown": 1.2, "Inner Peace": 0.6, "Fear No Evil": 0.6, "Crescendo": 0.5},
        "Scry": {"Third Eye": 0.6, "Cut Through Fate": 0.8, "Sanctity": 0.4, "Foresight": 0.8,
                 "Weave": 0.8, "Nirvana": 0.8},
        "Divinity": {"Prostrate": 0.6, "Worship": 0.8, "Devotion": 1.0, "Blasphemy": 0.8, "Pray": 0.6},
    },
}

BOSS_RELIC_PRIOR = {
    "Coffee Dripper": 1.2, "Cursed Key": 1.2, "Ectoplasm": 1.0, "Fusion Hammer": 1.0,
    "Philosopher's Stone": 1.2, "Runic Dome": 0.6, "Sozu": 1.2, "Velvet Choker": 1.0,
    "Busted Crown": 0.6, "Mark of Pain": 1.0, "Snecko Eye": 0.8, "Runic Pyramid": 1.0,
    "Black Star": 0.8, "Sacred Bark": 0.6, "Pandora's Box": 0.4, "Astrolabe": 0.6,
    "Empty Cage": 0.8, "Tiny House": 0.2, "Calling Bell": 0.2, "Black Blood": 0.8,
    "Ring of the Serpent": 0.8, "Frozen Core": 0.8, "Holy Water": 0.6, "Runic Cube": 0.8,
    "Wrist Blade": 0.4, "Hovering Kite": 0.4, "Slaver's Collar": 0.4, "Lizard Tail": 0.2,
}


# Shop / chest relics from an Ironclad point of view (0.8 = neutral). Used as a rule bonus on top of the
# learned shoprelic:<name> weight, so the bot can tell Vajra from Medical Kit before it has data.
SHOP_RELIC_PRIOR = {
    # strong
    "Dead Branch": 1.8, "Paper Phrog": 1.5, "Vajra": 1.5, "Shuriken": 1.4, "Kunai": 1.2, "Pen Nib": 1.3,
    "Ornamental Fan": 1.2, "Bag of Preparation": 1.3, "Lantern": 1.3, "Anchor": 1.3, "Horn Cleat": 1.2,
    "Orichalcum": 1.2, "Happy Flower": 1.3, "Ice Cream": 1.2, "Mummified Hand": 1.1, "Tungsten Rod": 1.3,
    "Torii": 1.2, "Membership Card": 1.3, "Mango": 1.2, "Pear": 1.0, "Strawberry": 0.9, "Girya": 1.1,
    "Self-Forming Clay": 1.2, "Champion Belt": 1.1, "Charon's Ashes": 1.1, "Meat on the Bone": 1.1,
    "Bronze Scales": 1.0, "Letter Opener": 1.1, "Gremlin Horn": 1.0, "Bottled Flame": 1.0,
    "Bottled Lightning": 1.0, "Bottled Tornado": 0.9, "Fossilized Helix": 1.1, "Calipers": 1.0,
    "Pocketwatch": 1.0, "Sundial": 1.0, "Art of War": 1.0, "Blood Vial": 0.9, "Bag of Marbles": 1.1,
    "Red Skull": 1.0, "Thread and Needle": 1.2, "Incense Burner": 1.2, "Du-Vu Doll": 0.9,
    "Magic Flower": 1.0, "Ink Bottle": 0.9, "Nunchaku": 1.0, "The Boot": 0.9, "Lizard Tail": 1.0,
    "Ginger": 0.8, "Turnip": 0.8, "Mercury Hourglass": 1.1, "Stone Calendar": 1.0,
    "Smiling Mask": 0.9, "Hand Drill": 0.8, "War Paint": 0.9, "Whetstone": 1.0, "Old Coin": 0.6,
    "Orrery": 0.9, "Cauldron": 0.7, "Dollys Mirror": 0.9, "Dolly's Mirror": 0.9, "Toolbox": 0.6,
    "The Courier": 0.8, "Clockwork Souvenir": 0.8, "Unceasing Top": 0.7, "Juzu Bracelet": 0.7,
    "Omamori": 0.8, "Regal Pillow": 0.7, "Sling of Courage": 0.9, "Gambling Chip": 0.7,
    "Dream Catcher": 0.6, "Chemical X": 0.6, "Frozen Eye": 0.5, "Lee's Waffle": 1.0, "Matryoshka": 0.9,
    "Shovel": 0.4, "Wing Boots": 0.6, "Singing Bowl": 0.5, "Question Card": 0.7, "Pantograph": 0.9,
    "Peace Pipe": 0.8, "Tiny Chest": 0.6, "Eternal Feather": 0.6, "Maw Bank": 0.6, "Ceramic Fish": 0.4,
    "Golden Idol": 0.6, "Darkstone Periapt": 0.6,
    # weak for Ironclad or situational
    "Medical Kit": 0.3, "Prismatic Shard": 0.3, "Potion Belt": 0.5, "Toy Ornithopter": 0.6,
    "Strange Spoon": 0.7, "Frozen Egg": 0.6, "Molten Egg": 0.8, "Toxic Egg": 0.5, "White Beast Statue": 0.4,
    "Sozu": 0.8, "Brimstone": 1.1, "Mark of Pain": 0.9,
}


# How much an upgrade adds (Ironclad), on top of the card's learned worth: big jumps first
# (cost drops, exhaust removed, +50% effect), marginal +3 damage last.
UPGRADE_PRIORITY = {
    "Bash": 1.0, "Limit Break": 2.0, "Barricade": 1.5, "Corruption": 1.5, "Demon Form": 1.2, "Body Slam": 1.5,
    "Entrench": 1.2, "Armaments": 1.4, "Shockwave": 1.0, "Uppercut": 1.0, "Offering": 1.0, "Battle Trance": 0.8,
    "Dark Embrace": 1.2, "Seeing Red": 1.0, "Exhume": 1.0, "Havoc": 0.8, "Searing Blow": 1.5,
    "Feel No Pain": 0.9, "Inflame": 0.8, "Spot Weakness": 0.8, "Disarm": 0.8, "True Grit": 0.9,
    "Shrug It Off": 0.7, "Flame Barrier": 0.7, "Impervious": 0.6, "Carnage": 0.6, "Whirlwind": 0.7,
    "Heavy Blade": 0.8, "Pommel Strike": 0.6, "Twin Strike": 0.5, "Burning Pact": 0.7, "Rupture": 0.8,
    "Juggernaut": 0.6, "Metallicize": 0.6, "Ghostly Armor": 0.5, "Power Through": 0.5, "Sentinel": 0.6,
    "Infernal Blade": 0.8, "Double Tap": 0.8, "Blood for Blood": 0.6, "Fiend Fire": 0.5, "Hemokinesis": 0.4,
    "Thunderclap": 0.4, "Cleave": 0.4, "Immolate": 0.5, "Reaper": 0.5, "Feed": 0.5, "Rampage": 0.6,
    "Sword Boomerang": 0.5, "Pummel": 0.5, "Flex": 0.4, "Combust": 0.3, "Clothesline": 0.4,
    "Strike": -0.8, "Defend": -0.6,
}


# What each boss tests (playbook), as card roles from roles(): used to steer picks once the boss is known.
# What each act boss asks of the deck: the starting value of the learnable bossneed:<boss>:<role> features
# (every card pick in that act prepares for its boss, at half strength early in the act, full in the second half).
# Set from Ironclad decks that beat each boss vs decks that lost (non-starter cards by role): winning Champ
# decks had more scaling (3.9 vs 2.9) and draw (2.4 vs 1.6) but LESS block (1.5 vs 2.4) and debuffs (2.6 vs 3.8);
# Automaton winners more scaling (4.0 vs 2.7), less AoE; Hexaghost winners more scaling, less block;
# Guardian winners more damage (3.4 vs 2.3). Small samples: the bot keeps learning each pair.
BOSS_NEEDS = {
    "Hexaghost": {"scaling": 0.7, "damage": 0.3}, "The Guardian": {"damage": 0.5, "block": 0.3, "scaling": 0.2},
    "Slime Boss": {"aoe": 0.5, "damage": 0.4, "scaling": 0.2},
    "The Champ": {"scaling": 0.8, "draw": 0.4, "damage": 0.2},
    "Bronze Automaton": {"scaling": 0.7, "damage": 0.3, "aoe": 0.1},
    "The Collector": {"aoe": 0.4, "scaling": 0.5, "damage": 0.2},
    "Awakened One": {"damage": 0.5, "aoe": 0.4, "scaling": 0.3}, "Time Eater": {"damage": 0.5, "block": 0.3},
    "Donu and Deca": {"aoe": 0.5, "debuff": 0.5, "scaling": 0.3},
}
BOSS_ROLES = ("damage", "aoe", "block", "draw", "scaling", "debuff")


# Act-specific card priors from an A20 Ironclad guide (Panacea), seeding the learned
# cardctx:<card>:a<act> features: act 1 = save HP now (front-loaded damage, AoE), acts 2-3 =
# greedier synergy picks (exhaust engine, Barricade, Reaper...).
_ACT1_GOOD = {"Immolate": 1.0, "Feed": 0.8, "Bludgeon": 0.8, "Fiend Fire": 0.6, "Offering": 0.6, "Anger": 0.6,
              "Demon Form": 0.5, "Corruption": 0.4, "Impervious": 0.4, "Power Through": 0.4, "Inflame": 0.4,
              "Pommel Strike": 0.4, "Combust": 0.4, "Battle Trance": 0.4, "Carnage": 0.4, "Hemokinesis": 0.3,
              "Armaments": 0.2, "Sever Soul": 0.2, "Headbutt": 0.2, "Rampage": 0.2, "Clothesline": 0.2,
              "Uppercut": 0.3, "Shrug It Off": 0.3, "Cleave": 0.2, "Thunderclap": 0.2, "Perfected Strike": 0.3,
              "Fire Breathing": 0.1, "Blood for Blood": 0.1}
_ACT1_SKIP = ["Heavy Blade", "Limit Break", "Sword Boomerang", "Flex", "Wild Strike", "Body Slam", "Havoc",
              "Exhume", "Sentinel", "Warcry", "Clash", "Barricade", "Juggernaut"]
_LATE_GOOD = {"Corruption": 0.7, "Dark Embrace": 0.6, "Feel No Pain": 0.6, "Barricade": 0.5, "Reaper": 0.5,
              "Fiend Fire": 0.4, "Second Wind": 0.3, "Offering": 0.5, "Disarm": 0.3, "Evolve": 0.2,
              "Impervious": 0.3, "Demon Form": 0.3, "Battle Trance": 0.2, "Shrug It Off": 0.2}
_LATE_BAD = {"Anger": -0.3, "Perfected Strike": -0.5, "Rampage": -0.2, "Wild Strike": -0.3, "Reckless Charge": -0.3}


def cardctx_prior(key: str) -> float:
    """cardctx:<card>:a<act> priors (other cardctx features start at 0 and are learned)."""
    m = re.match(r"cardctx:(.+):a(\d)$", key)
    if not m:
        return 0.0
    name, act = m.group(1), int(m.group(2))
    if act == 1:
        return _ACT1_GOOD.get(name, -0.6 if name in _ACT1_SKIP else 0.0)
    return _LATE_GOOD.get(name, _LATE_BAD.get(name, 0.0))


# Boss relic judgment from the guide (A0-adjusted): rule bonus on top of learned relic weights.
GUIDE_BOSS_RELIC = {
    "Snecko Eye": (0.6, "S tier: draw 2, great with 2-3 cost cards"),
    "Coffee Dripper": (0.0, "energy, but no resting: 10 of 34 act 2 boss losses had it"), "Cursed Key": (0.3, "A tier: skip chests you don't want"),
    "Philosopher's Stone": (0.1, "B tier"), "Velvet Choker": (0.1, "B tier"), "Astrolabe": (0.0, "C tier"),
    "Pandora's Box": (0.0, "C tier"), "Fusion Hammer": (0.1, "energy; lose smithing"), "Runic Pyramid": (0.1, "great with exhaust/energy"),
    "Slaver's Collar": (-0.4, "energy only in elite/boss fights"), "Mark of Pain": (-0.4, "wounds cost draw"),
    "Sozu": (-0.6, "no potions for the rest of the run"), "Ectoplasm": (-0.2, "no more gold"),
    "Busted Crown": (-0.6, "card rewards shrink to 1"), "Runic Dome": (-0.8, "hides intents the planner needs"),
    "Runic Cube": (-0.4, "low impact"), "Calling Bell": (-0.3, "low impact, 3 curses"), "Black Blood": (-0.3, "low impact"),
    "Empty Cage": (-0.2, "low impact"), "Sacred Bark": (-0.4, "low impact"), "Black Star": (-0.3, "nothing now"),
    "Tiny House": (-0.4, "weakest"),
}

# Shop relic values from the guide (price taken into account), overriding SHOP_RELIC_PRIOR.
GUIDE_SHOP_RELIC = {
    "Dead Branch": 1.9, "Membership Card": 1.7, "Orange Pellets": 1.4, "Anchor": 1.3, "Bag of Preparation": 1.3,
    "Toy Ornithopter": 1.2, "Bag of Marbles": 1.2, "Pantograph": 1.2, "Orrery": 1.2, "Toolbox": 1.2,
    "Meat on the Bone": 1.1, "Brimstone": 1.1, "White Beast Statue": 1.1, "Orichalcum": 1.1, "Pen Nib": 1.0,
    "Oddly Smooth Stone": 1.0, "Nunchaku": 1.0, "Lantern": 1.0, "Akabeko": 1.0, "Bronze Scales": 1.0,
    "Ornamental Fan": 0.85, "Strange Spoon": 0.8, "War Paint": 0.85, "Whetstone": 0.85, "Chemical X": 0.6,
    "Charon's Ashes": 0.8, "Frozen Egg": 0.75, "Sundial": 0.8, "Cauldron": 0.7, "Bottled Lightning": 0.75,
    "Bottled Flame": 0.75, "Bottled Tornado": 0.7, "Torii": 0.8, "Meal Ticket": 0.7, "Blood Vial": 0.7,
    "Champion Belt": 0.7, "Darkstone Periapt": 0.3, "Juzu Bracelet": 0.3, "Strawberry": 0.4, "The Boot": 0.4,
    "Matryoshka": 0.4, "Singing Bowl": 0.3, "Ceramic Fish": 0.2, "Prismatic Shard": 0.2, "Medical Kit": 0.6,
    "The Abacus": 0.9, "Frozen Eye": 0.7, "Sling of Courage": 1.0, "Clockwork Souvenir": 1.0, "Lee's Waffle": 0.9,
    "Hand Drill": 0.5, "Dolly's Mirror": 0.8, "Potion Belt": 0.6,
}


SHOP_RELIC_PRIOR.update(GUIDE_SHOP_RELIC)


# Cards worth building a run around when found (engines, rares, run-defining commons).
ENGINE_CARDS = {"Corruption", "Demon Form", "Barricade", "Feel No Pain", "Dark Embrace", "Offering", "Feed",
                "Immolate", "Reaper", "Fiend Fire", "Impervious", "Limit Break", "Bludgeon", "Rupture",
                "Juggernaut", "Brutality", "Berserk", "Battle Trance", "Second Wind", "Evolve", "Whirlwind",
                "Inflame", "Spot Weakness", "Metallicize", "Entrench", "Body Slam", "Double Tap", "Exhume"}


def character_key(character: str | None) -> str:
    c = (character or "").upper().replace(" ", "_")
    return {"SILENT": "THE_SILENT"}.get(c, c)


def base_name(name: str) -> str:
    return re.sub(r"\+\d*$", "", name).strip()


@lru_cache(maxsize=None)
def _tier_index(character: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for value, names in TIERS.get(character, {}).items():
        for n in names:
            out[n] = value
    for arch in ARCHETYPES.get(character, {}).values():
        for n in arch:
            out.setdefault(n, 0.6)
    return out


def card_prior(character: str, name: str, card_type: str | None = None) -> float:
    n = base_name(name)
    if n in BASIC:
        return BASIC[n]
    if card_type in ("CURSE", "STATUS"):
        return -3.0
    return _tier_index(character_key(character)).get(n, DEFAULT_CARD_PRIOR)


def archetypes(character: str) -> dict[str, dict[str, float]]:
    return ARCHETYPES.get(character_key(character), {})


def card_archetypes(character: str, name: str) -> list[str]:
    n = base_name(name)
    return [a for a, keys in archetypes(character).items() if n in keys]


ARCH_SOURCES = {  # an archetype only counts as committed once one of its engine cards is in the deck
    "Strength": {"Inflame", "Spot Weakness", "Demon Form", "Rupture"},
    "Exhaust": {"Corruption", "Feel No Pain", "Dark Embrace"},
    "Block": {"Barricade", "Entrench", "Juggernaut", "Metallicize"},
    "Self-damage": {"Rupture", "Brutality", "Combust", "Offering", "Bloodletting", "Hemokinesis"},
}


def commitment(character: str, deck_names: list[str]) -> dict[str, float]:
    """How far the deck leans into each archetype, 0..1 (at most 0.3 without an engine card)."""
    names = [base_name(n) for n in deck_names]
    out = {}
    for a, keys in archetypes(character).items():
        v = min(1.0, sum(keys.get(n, 0.0) for n in names) / 2.0)
        src = ARCH_SOURCES.get(a)
        if src and not src & set(names):
            v = min(v, 0.3)
        out[a] = v
    return out


# ---------------------------------------------------------------- effects

class Effects:
    __slots__ = ("damage", "hits", "aoe", "random_target", "block", "vuln", "weak", "aoe_debuff",
                 "draw", "energy", "strength", "temp_strength", "hp_loss", "exhaust", "power",
                 "x_cost", "strength_mult", "block_scaling", "double_block", "double_strength",
                 "conditional_strength", "self_vuln", "self_weak", "self_frail", "enemy_str_down",
                 "enemy_str_down_temp", "poison", "per_strike", "exhaust_hand", "block_per_exhaust",
                 "damage_per_exhaust", "if_vuln", "per_turn_strength", "per_turn_block",
                 "per_turn_energy", "per_turn_damage", "thorns", "dex", "known", "shivs", "poison_mult",
                 "intangible", "double_attack", "double_skill", "heal_unblocked")

    def __init__(self):
        self.damage = 0; self.hits = 1; self.aoe = False; self.random_target = False
        self.block = 0; self.vuln = 0; self.weak = 0; self.aoe_debuff = False
        self.draw = 0; self.energy = 0; self.strength = 0; self.temp_strength = 0
        self.hp_loss = 0; self.exhaust = False; self.power = False; self.x_cost = False
        self.strength_mult = 1; self.block_scaling = False; self.double_block = False
        self.double_strength = False; self.conditional_strength = False
        self.self_vuln = 0; self.self_weak = 0; self.self_frail = 0
        self.enemy_str_down = 0; self.enemy_str_down_temp = 0; self.poison = 0
        self.per_strike = 0; self.exhaust_hand = ""; self.block_per_exhaust = 0
        self.damage_per_exhaust = 0; self.if_vuln = False
        self.per_turn_strength = 0; self.per_turn_block = 0; self.per_turn_energy = 0
        self.per_turn_damage = 0; self.thorns = 0; self.dex = 0; self.known = False
        self.shivs = 0   # Silent: Shivs added to the hand (0 cost, 4 damage each)
        self.poison_mult = 0   # Catalyst: multiply the target's Poison
        self.intangible = 0    # Wraith Form / Apparition: incoming hits deal 1
        self.double_attack = 0  # Double Tap: next N Attacks played twice
        self.double_skill = 0   # Burst: next N Skills played twice
        self.heal_unblocked = False  # Reaper: heal the unblocked damage it deals


def _int(pattern: str, text: str, default: int = 0) -> int:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return int(m.group(1)) if m else default


# Sentences that describe a later trigger, not something that happens on play.
_TRIGGER = re.compile(r"^(?:At the (?:start|end) of|Whenever|If this card is Exhausted|"
                      r"When(?:ever)? you (?:draw|lose|gain|play))", re.IGNORECASE)


def _split_triggers(text: str) -> tuple[str, str]:
    """(on-play text, triggered text): powers and 'whenever' riders act later."""
    now, later = [], []
    for s in re.split(r"(?<=\.)\s+", text):
        (later if _TRIGGER.search(s.strip()) else now).append(s)
    return " ".join(now), " ".join(later)


@lru_cache(maxsize=4096)
def parse_text(text: str, card_type: str, cost: int) -> Effects:
    e = Effects()
    full = text or ""
    e.known = bool(full)
    e.power = card_type == "POWER"
    e.x_cost = cost == -1
    t, later = _split_triggers(full)
    # triggered riders: valued as scaling, never as an immediate effect
    each_turn = " ".join(s for s in re.split(r"(?<=\.)\s+", later)
                         if re.search(r"(?:start|end) of your turn", s, re.I))
    e.per_turn_strength = _int(r"gain (\d+) Strength", each_turn)
    e.per_turn_block = _int(r"gain (\d+) Block", each_turn)
    e.per_turn_energy = _int(r"gain (\d+) Energy", each_turn)
    e.per_turn_damage = _int(r"deal (\d+) damage to ALL enemies", each_turn)
    e.thorns = _int(r"deal (\d+) damage back", later)
    m = re.search(r"Gain (\d+) (Vulnerable|Weak|Frail)", full)
    if m:
        setattr(e, {"Vulnerable": "self_vuln", "Weak": "self_weak", "Frail": "self_frail"}[m.group(2)],
                int(m.group(1)))
    e.exhaust = bool(re.search(r"\bExhaust\.", t))
    e.damage = _int(r"Deal (\d+) damage", t)
    e.if_vuln = bool(re.search(r"If the enemy (?:has|is) Vulnerable", t, re.I))
    e.per_strike = _int(r"Deals (\d+) additional damage for ALL your cards containing", t)
    if re.search(r"Exhaust your hand", t, re.I):
        e.exhaust_hand = "all"
        e.damage_per_exhaust = _int(r"Deal (\d+) damage for each card Exhausted", t)
        e.damage = 0
    elif re.search(r"Exhaust all non-Attack cards in your hand", t, re.I):
        e.exhaust_hand = "nonattack"
        e.block_per_exhaust = _int(r"Gain (\d+) Block for each card Exhausted", t)
    m = re.search(r"(?:Enemy|ALL enemies) loses? (\d+) Strength( this turn)?", t, re.I)
    if m:
        if m.group(2):
            e.enemy_str_down_temp = int(m.group(1))
        else:
            e.enemy_str_down = int(m.group(1))
        e.aoe_debuff = e.aoe_debuff or "ALL enemies" in m.group(0)
    e.poison = _int(r"Apply (\d+) Poison", t)
    e.intangible = _int(r"Gain (\d+) Intangible", t)
    m = re.search(r"next (\d+ )?(Attack|Skill)s? (?:is|are) played twice", t, re.I)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        if m.group(2).lower() == "attack":
            e.double_attack = n
        else:
            e.double_skill = n
    e.heal_unblocked = bool(re.search(r"Heal HP equal to unblocked damage", t, re.I))
    m = re.search(r"(Double|Triple) the enemy's Poison", t, re.I)
    if m:
        e.poison_mult = 2 if m.group(1).lower() == "double" else 3
    m = re.search(r"Poison to a random enemy (\d+) times", t, re.I)   # Bouncing Flask
    if m:
        e.poison *= int(m.group(1))
        e.random_target = True
    m = re.search(r"Add (\d+|a) \*?Shivs?\b", t, re.I)
    if m:
        e.shivs = 1 if m.group(1).lower() == "a" else int(m.group(1))
    if re.search(r"damage equal to your Block", t, re.I):
        e.block_scaling = True
    if re.search(r"damage (?:\w+ )*X times", t, re.I):
        e.hits = 0  # resolved to energy at play time
    elif re.search(r"\btwice\b", t, re.I):
        e.hits = 2
    else:
        e.hits = max(1, _int(r"damage[^.]*?(\d+) times", t, 1))
    e.aoe = bool(re.search(r"to ALL enemies", t))
    e.random_target = bool(re.search(r"random enemy", t, re.I))
    e.block = sum(int(x) for x in re.findall(r"Gain (\d+) Block", t))
    e.double_block = bool(re.search(r"Double your Block", t, re.I))
    vw = re.search(r"Apply (\d+) Weak and Vulnerable", t)
    if vw:
        e.vuln = e.weak = int(vw.group(1))
    else:
        e.vuln = _int(r"Apply (\d+) Vulnerable", t)
        e.weak = _int(r"Apply (\d+) Weak", t)
    e.aoe_debuff = bool(re.search(r"(?:Vulnerable|Weak)[^.]*to ALL enemies", t))
    e.draw = _int(r"Draw (\d+) cards?", t)
    e.energy = _int(r"Gain (\d+) Energy", t)
    if re.search(r"At the end of this turn, lose", full):
        e.temp_strength = _int(r"Gain (\d+) Strength", t)
    elif re.search(r"If the enemy intends to attack, gain (\d+) Strength", t):
        e.conditional_strength = True
        e.strength = _int(r"gain (\d+) Strength", t)
    else:
        e.strength = _int(r"Gain (\d+) Strength", t)
    e.double_strength = bool(re.search(r"Double your Strength", t, re.I))
    e.strength_mult = _int(r"Strength affects this card (\d+) times", t, 1)
    e.hp_loss = _int(r"Lose (\d+) HP", t)
    e.dex = _int(r"Gain (\d+) Dexterity", t)
    if e.block_per_exhaust:
        e.block = 0
    return e


def effects(card) -> Effects:
    text = card_text(card) or ""
    return parse_text(text, card.type.value if hasattr(card.type, "value") else str(card.type), card.cost)


def roles(card) -> set[str]:
    e = effects(card)
    out = set()
    if (e.aoe and e.damage) or (e.poison and (e.aoe or e.aoe_debuff)):
        out.add("aoe")   # area poison (Crippling Cloud) answers multi-enemy fights too
    if e.damage * max(e.hits, 1) >= 9 or e.block_scaling:
        out.add("damage")
    if e.block >= 7 or e.double_block:
        out.add("block")
    if e.draw:
        out.add("draw")
    if e.power or e.strength or e.double_strength or e.per_turn_strength or e.poison:
        out.add("scaling")
    if e.vuln or e.weak:
        out.add("debuff")
    return out
