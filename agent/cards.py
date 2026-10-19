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
BASIC = {"Strike": -1.0, "Defend": -0.6}
DEFAULT_CARD_PRIOR = 0.3

ARCHETYPES = {
    "IRONCLAD": {
        "Strength": {"Inflame": 1.0, "Spot Weakness": 1.0, "Demon Form": 1.2, "Limit Break": 0.8,
                     "Heavy Blade": 0.8, "Sword Boomerang": 0.6, "Whirlwind": 0.6, "Twin Strike": 0.5,
                     "Pummel": 0.5, "Flex": 0.4, "Reaper": 0.4},
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


def commitment(character: str, deck_names: list[str]) -> dict[str, float]:
    """How far the deck leans into each archetype, 0..1."""
    names = [base_name(n) for n in deck_names]
    return {a: min(1.0, sum(keys.get(n, 0.0) for n in names) / 2.0)
            for a, keys in archetypes(character).items()}


# ---------------------------------------------------------------- effects

class Effects:
    __slots__ = ("damage", "hits", "aoe", "random_target", "block", "vuln", "weak", "aoe_debuff",
                 "draw", "energy", "strength", "temp_strength", "hp_loss", "exhaust", "power",
                 "x_cost", "strength_mult", "block_scaling", "double_block", "double_strength",
                 "conditional_strength", "self_vuln", "self_weak", "self_frail", "enemy_str_down",
                 "enemy_str_down_temp", "poison", "per_strike", "exhaust_hand", "block_per_exhaust",
                 "damage_per_exhaust", "if_vuln", "per_turn_strength", "per_turn_block",
                 "per_turn_energy", "per_turn_damage", "thorns", "dex", "known")

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
    if e.aoe and e.damage:
        out.add("aoe")
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
