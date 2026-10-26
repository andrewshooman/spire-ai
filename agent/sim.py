"""sim.py -- an offline Ironclad combat simulator for fast training and data points.

The real policy code plays: every decision is a game-format state (the same JSON
CommunicationMod sends) parsed by sts-bench and handed to planner.plan_turn, so a
simulated fight exercises exactly the combat logic that plays live. The engine
resolves cards from their printed text (cards.parse_text) plus the Ironclad
specials the text parser can't express, and the monsters are scripted A0 move
patterns for the fights that end most runs (checked against the moves in the logs).

Results never touch the live training data: sim_train.py writes only under
evolve/<character>/sim/.

    fight = Fight(deck, "Hexaghost", hp=60, max_hp=80, seed=7)
    result = fight.run(params)        # {"win", "hp_lost", "turns", ...}
"""

from __future__ import annotations

import copy
import json
import random
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sts_bench.state.schema import parse_message
from sts_bench.tools import card_db

import cards as C
import planner

HERE = Path(__file__).resolve().parent
TEMPLATE = json.loads((HERE.parent / "sts-bench" / "tests" / "fixtures" / "states" / "none-1.json").read_text())

# ---------------------------------------------------------------- cards

COST = {  # base cost, upgraded cost when it differs
    "Anger": 0, "Armaments": 1, "Barricade": (3, 2), "Bash": 2, "Battle Trance": 0, "Berserk": 0,
    "Blood for Blood": (4, 3), "Bloodletting": 0, "Bludgeon": 3, "Body Slam": (1, 0), "Brutality": 0,
    "Burning Pact": 1, "Carnage": 2, "Clash": 0, "Cleave": 1, "Clothesline": 2, "Combust": 1,
    "Corruption": (3, 2), "Dark Embrace": (2, 1), "Defend": 1, "Demon Form": 3, "Disarm": 1, "Double Tap": 1,
    "Dropkick": 1, "Dual Wield": 1, "Entrench": (2, 1), "Evolve": 1, "Exhume": (1, 0), "Feed": 1,
    "Feel No Pain": 1, "Fiend Fire": 2, "Fire Breathing": 1, "Flame Barrier": 2, "Flex": 0, "Ghostly Armor": 1,
    "Havoc": (1, 0), "Headbutt": 1, "Heavy Blade": 2, "Hemokinesis": 1, "Immolate": 2, "Impervious": 2,
    "Infernal Blade": (1, 0), "Inflame": 1, "Intimidate": 0, "Iron Wave": 1, "Juggernaut": 2,
    "Limit Break": 1, "Metallicize": 1, "Offering": 0, "Perfected Strike": 2, "Pommel Strike": 1,
    "Power Through": 1, "Pummel": 1, "Rage": 0, "Rampage": 1, "Reaper": 2, "Reckless Charge": 0,
    "Rupture": 1, "Searing Blow": 2, "Second Wind": 1, "Seeing Red": (1, 0), "Sentinel": 1, "Sever Soul": 2,
    "Shockwave": 2, "Shrug It Off": 1, "Spot Weakness": 1, "Strike": 1, "Sword Boomerang": 1,
    "Thunderclap": 1, "True Grit": 1, "Twin Strike": 1, "Uppercut": 2, "Warcry": 0, "Whirlwind": -1,
    "Wild Strike": 1,
    # statuses / curses
    "Dazed": -2, "Burn": -2, "Wound": -2, "Slimed": 1, "Void": -2,
    "Regret": -2, "Doubt": -2, "Shame": -2, "Decay": -2, "Clumsy": -2, "Parasite": -2, "Injury": -2,
    "Writhe": -2, "Pain": -2, "Normality": -2, "Necronomicurse": -2, "Ascender's Bane": -2,
}
IDS = {"Strike": "Strike_R", "Defend": "Defend_R"}
STATUS = {"Dazed", "Burn", "Wound", "Slimed", "Void"}
ETHEREAL = {"Dazed", "Carnage", "Ghostly Armor", "Void"}
CURSE_NAMES = {"Regret", "Doubt", "Shame", "Decay", "Clumsy", "Parasite", "Injury", "Writhe", "Pain",
               "Normality", "Necronomicurse", "Ascender's Bane"}


def _text(name: str, up: int) -> str:
    by_id, by_name = card_db._index()
    e = by_name.get(name.lower())
    if e is None:
        return ""
    return e["upgraded_text"] if up else e["text"]


def _type(name: str) -> str:
    if name in STATUS:
        return "STATUS"
    if name in CURSE_NAMES:
        return "CURSE"
    by_id, by_name = card_db._index()
    e = by_name.get(name.lower())
    return (e or {}).get("type", "SKILL").upper()


def make_card(name: str) -> dict:
    """A card dict in CommunicationMod's wire format ('Bash+' = upgraded)."""
    base = C.base_name(name)
    up = 1 if name.endswith("+") or re.search(r"\+\d+$", name) else 0
    cost = COST.get(base, 1)
    if isinstance(cost, tuple):
        cost = cost[1] if up else cost[0]
    ctype = _type(base)
    text = _text(base, up)
    if ctype == "ATTACK":
        target = "ALL enemies" not in text and "random enemy" not in text
    elif ctype == "SKILL":
        target = "enemy" in text.lower() and "ALL enemies" not in text
    else:
        target = False
    return {"exhausts": bool(re.search(r"\bExhaust\.", text)), "is_playable": cost >= -1, "cost": cost,
            "name": base + ("+" if up else ""), "id": IDS.get(base, base), "type": ctype,
            "ethereal": base in ETHEREAL, "uuid": str(uuid.uuid4()), "upgrades": up,
            "rarity": "BASIC" if base in ("Strike", "Defend", "Bash") else "COMMON", "has_target": target}


STARTER = ["Strike"] * 5 + ["Defend"] * 4 + ["Bash"]

# ---------------------------------------------------------------- monsters

@dataclass
class Monster:
    name: str
    hp: int
    max_hp: int
    block: int = 0
    powers: dict = field(default_factory=dict)
    move: str = ""
    dmg: int = 0
    hits: int = 0
    intent: str = "UNKNOWN"
    turn: int = 0
    gone: bool = False
    state: dict = field(default_factory=dict)

    def p(self, k: str) -> int:
        return self.powers.get(k, 0)


def _hp(rng, lo, hi):
    return rng.randint(lo, hi)


def spawn(name: str, rng: random.Random) -> list[Monster]:
    """Monsters of one encounter, A0 stats."""
    if name == "Bronze Automaton":  # boss with two Bronze Orbs from the start
        boss = Monster("Bronze Automaton", 300, 300, powers={"Artifact": 3})
        orbs = [Monster("Orb", h, h, powers={"Minion": 1}) for h in (_hp(rng, 52, 58), _hp(rng, 52, 58))]
        return [orbs[0], boss, orbs[1]]
    if name == "3 Shapes":  # act 3: three of Spiker / Repulsor / Exploder
        out = []
        for kind in [rng.choice(["Spiker", "Repulsor", "Exploder"]) for _ in range(3)]:
            out += spawn(kind, rng)
        return out
    if name == "3 Sentries":
        out = []
        for k in range(3):
            m = Monster("Sentry", *(lambda h: (h, h))(_hp(rng, 38, 42)), powers={"Artifact": 1})
            m.state["next"] = "beam" if k == 1 else "bolt"
            out.append(m)
        return out
    table = {
        "Jaw Worm": (40, 44, {}), "Cultist": (48, 54, {}), "Gremlin Nob": (82, 86, {}),
        "Lagavulin": (109, 111, {"Metallicize": 8}), "Hexaghost": (250, 250, {}),
        "The Guardian": (240, 240, {"Mode Shift": 30}), "Slime Boss": (140, 140, {"Split": 1}),
        "Spheric Guardian": (20, 20, {"Barricade": 1, "Artifact": 3}),
        "Snake Plant": (75, 79, {"Malleable": 3}), "Shelled Parasite": (68, 72, {"Plated Armor": 14}),
        "Book of Stabbing": (160, 164, {"Painful Stabs": 1}),
        "Acid Slime (L)": (65, 69, {"Split": 1}), "Spike Slime (L)": (64, 70, {"Split": 1}),
        "Spiker": (42, 56, {"Thorns": 3}), "Repulsor": (29, 35, {}), "Exploder": (30, 30, {"Explosive": 3}),
        "Orb Walker": (90, 96, {"Generic Strength Up": 3}), "Transient": (999, 999, {"Fading": 5, "Shifting": 1}),
        "The Champ": (420, 420, {}),
    }
    lo, hi, powers = table[name]
    h = _hp(rng, lo, hi)
    m = Monster(name, h, h, powers=dict(powers))
    if name == "Spheric Guardian":
        m.block = 40
    if name == "Lagavulin":
        m.block = 8
        m.state["asleep"] = 3
    if name == "Book of Stabbing":
        m.state["stabs"] = 1
    return [m]


ENCOUNTERS = ["Jaw Worm", "Cultist", "Gremlin Nob", "Lagavulin", "3 Sentries", "Hexaghost", "The Guardian",
              "Slime Boss", "Spheric Guardian", "Snake Plant", "Shelled Parasite", "Book of Stabbing",
              "3 Shapes", "Orb Walker", "Transient", "The Champ", "Bronze Automaton"]
ACT = {"Jaw Worm": 1, "Cultist": 1, "Gremlin Nob": 1, "Lagavulin": 1, "3 Sentries": 1, "Hexaghost": 1,
       "The Guardian": 1, "Slime Boss": 1, "Spheric Guardian": 2, "Snake Plant": 2, "Shelled Parasite": 2,
       "Book of Stabbing": 2, "3 Shapes": 3, "Orb Walker": 3, "Transient": 3, "The Champ": 2,
       "Bronze Automaton": 2}
KIND = {"Gremlin Nob": "elite", "Lagavulin": "elite", "3 Sentries": "elite", "Book of Stabbing": "elite",
        "Hexaghost": "boss", "The Guardian": "boss", "Slime Boss": "boss", "The Champ": "boss",
        "Bronze Automaton": "boss"}


def set_move(m: Monster, move: str, intent: str, dmg: int = 0, hits: int = 1) -> None:
    m.move, m.intent, m.dmg, m.hits = move, intent, dmg, (hits if dmg else 0)


def choose_move(m: Monster, f: "Fight") -> None:
    """Pick the monster's next intent (A0 patterns)."""
    r, t = f.rng.random(), m.turn
    last = m.state.get("hist", [])
    n = m.name
    if n == "Jaw Worm":
        if t == 0:
            set_move(m, "chomp", "ATTACK", 11)
        elif r < 0.45 and last[-1:] != ["bellow"]:
            set_move(m, "bellow", "DEFEND_BUFF")
        elif r < 0.75 and last[-2:] != ["thrash", "thrash"]:
            set_move(m, "thrash", "ATTACK_DEFEND", 7)
        else:
            set_move(m, "chomp", "ATTACK", 11) if last[-1:] != ["chomp"] else set_move(m, "thrash", "ATTACK_DEFEND", 7)
    elif n == "Cultist":
        set_move(m, "incantation", "BUFF") if t == 0 else set_move(m, "strike", "ATTACK", 6)
    elif n == "Gremlin Nob":
        if t == 0:
            set_move(m, "bellow", "BUFF")
        elif r < 0.33 or last[-2:] == ["rush", "rush"]:
            set_move(m, "bash", "ATTACK_DEBUFF", 6)
        else:
            set_move(m, "rush", "ATTACK", 14)
    elif n == "Lagavulin":
        if m.state.get("asleep", 0) > 0:
            set_move(m, "sleep", "SLEEP")
        elif m.state.get("stunned"):
            set_move(m, "stun", "STUN")
        else:
            k = m.state.get("cycle", 0)
            set_move(m, "siphon", "STRONG_DEBUFF") if k % 3 == 2 else set_move(m, "attack", "ATTACK", 18)
    elif n == "Sentry":
        if m.state["next"] == "beam":
            set_move(m, "beam", "ATTACK", 9)
        else:
            set_move(m, "bolt", "DEBUFF")
    elif n == "Hexaghost":
        seq = ["sear", "tackle", "sear", "inflame", "tackle", "sear", "inferno"]
        if t == 0:
            set_move(m, "activate", "UNKNOWN")
        elif t == 1:
            set_move(m, "divider", "ATTACK", f.hp // 12 + 1, 6)
        else:
            mv = seq[(t - 2) % len(seq)]
            {"sear": lambda: set_move(m, "sear", "ATTACK_DEBUFF", 6),
             "tackle": lambda: set_move(m, "tackle", "ATTACK", 5, 2),
             "inflame": lambda: set_move(m, "inflame", "DEFEND_BUFF"),
             "inferno": lambda: set_move(m, "inferno", "ATTACK_DEBUFF", 2, 6)}[mv]()
    elif n == "The Guardian":
        if m.state.get("defensive"):
            k = m.state.get("dturn", 0)
            set_move(m, "roll", "ATTACK", 9) if k == 0 else set_move(m, "twin", "ATTACK_BUFF", 8, 2)
        else:
            seq = ["charge", "bash", "vent", "whirl"]
            mv = seq[m.state.get("ocycle", 0) % 4]
            {"charge": lambda: set_move(m, "charge", "DEFEND"), "bash": lambda: set_move(m, "bash", "ATTACK", 32),
             "vent": lambda: set_move(m, "vent", "STRONG_DEBUFF"),
             "whirl": lambda: set_move(m, "whirl", "ATTACK", 5, 4)}[mv]()
    elif n == "Slime Boss":
        if m.state.get("split"):
            set_move(m, "split", "UNKNOWN")
        else:
            [lambda: set_move(m, "goop", "STRONG_DEBUFF"), lambda: set_move(m, "prep", "UNKNOWN"),
             lambda: set_move(m, "slam", "ATTACK", 35)][t % 3]()
    elif n.startswith("Acid Slime"):
        big = "(L)" in n
        if m.state.get("split"):
            set_move(m, "split", "UNKNOWN")
        elif r < 0.3:
            set_move(m, "spit", "ATTACK_DEBUFF", 11 if big else 7)
        elif r < 0.7:
            set_move(m, "tackle", "ATTACK", 16 if big else 10)
        else:
            set_move(m, "lick", "DEBUFF")
    elif n.startswith("Spike Slime"):
        big = "(L)" in n
        if m.state.get("split"):
            set_move(m, "split", "UNKNOWN")
        elif r < 0.3 and last[-1:] != ["lick"]:
            set_move(m, "lick", "DEBUFF")
        else:
            set_move(m, "flame", "ATTACK_DEBUFF", 16 if big else 8)
    elif n == "Spheric Guardian":
        if t == 0:
            set_move(m, "activate", "DEFEND")
        elif t == 1:
            set_move(m, "attack_debuff", "ATTACK_DEBUFF", 10)
        elif t % 2 == 0:
            set_move(m, "slam", "ATTACK", 10, 2)
        else:
            set_move(m, "harden", "ATTACK_DEFEND", 10)
    elif n == "Snake Plant":
        if r < 0.35 and last[-1:] != ["spores"]:
            set_move(m, "spores", "STRONG_DEBUFF")
        else:
            set_move(m, "chomp", "ATTACK", 7, 3)
    elif n == "Shelled Parasite":
        if t == 0 and r < 0.5:
            set_move(m, "fell", "ATTACK_DEBUFF", 18)
        elif r < 0.5 and last[-1:] != ["double"]:
            set_move(m, "double", "ATTACK", 6, 2)
        elif last[-1:] != ["suck"]:
            set_move(m, "suck", "ATTACK_BUFF", 10)
        else:
            set_move(m, "double", "ATTACK", 6, 2)
    elif n == "Book of Stabbing":
        if r < 0.15 and last[-1:] != ["single"]:
            set_move(m, "single", "ATTACK", 21)
        else:
            m.state["stabs"] = m.state.get("stabs", 1) + 1
            set_move(m, "multi", "ATTACK", 6, m.state["stabs"])
    elif n == "Spiker":
        if r < 0.5 and last[-1:] != ["spike"]:
            set_move(m, "spike", "BUFF")
        else:
            set_move(m, "cut", "ATTACK", 7)
    elif n == "Repulsor":
        set_move(m, "bash", "ATTACK", 11) if r < 0.25 and last[-1:] != ["bash"] else set_move(m, "repulse", "DEBUFF")
    elif n == "Exploder":
        set_move(m, "explode", "ATTACK", 30) if m.p("Explosive") <= 1 else set_move(m, "slam", "ATTACK", 9)
    elif n == "Orb Walker":
        if r < 0.4 and last[-2:] != ["claw", "claw"]:
            set_move(m, "claw", "ATTACK", 15)
        else:
            set_move(m, "laser", "ATTACK_DEBUFF", 10)
    elif n == "Transient":
        set_move(m, "attack", "ATTACK", 30 + 10 * t)
    elif n == "The Champ":
        if m.hp < m.max_hp // 2 and not m.state.get("angry"):
            set_move(m, "anger", "BUFF")
        elif m.state.get("angry") and last[-1:] != ["execute"]:
            set_move(m, "execute", "ATTACK", 10, 2)
        elif r < 0.15 and m.state.get("stances", 0) < 2 and last[-1:] != ["stance"]:
            set_move(m, "stance", "DEFEND_BUFF")
        elif r < 0.30 and last[-1:] != ["gloat"] and not m.state.get("angry"):
            set_move(m, "gloat", "BUFF")
        elif r < 0.45 and last[-1:] != ["taunt"]:
            set_move(m, "taunt", "DEBUFF")
        elif r < 0.70 and last[-1:] != ["slap"]:
            set_move(m, "slap", "ATTACK_DEBUFF", 12)
        else:
            set_move(m, "slash", "ATTACK", 16)
    elif n == "Bronze Automaton":
        seq = ["flail", "boost", "flail", "boost", "hyper", "stunned"]
        mv = seq[t % len(seq)]
        {"flail": lambda: set_move(m, "flail", "ATTACK", 7, 2), "boost": lambda: set_move(m, "boost", "DEFEND_BUFF"),
         "hyper": lambda: set_move(m, "hyper", "ATTACK", 45), "stunned": lambda: set_move(m, "stunned", "STUN")}[mv]()
    elif n == "Orb":
        if t == 0:
            set_move(m, "stasis", "STRONG_DEBUFF")
        elif r < 0.5:
            set_move(m, "beam", "ATTACK", 8)
        else:
            set_move(m, "support", "DEFEND")
    else:
        set_move(m, "attack", "ATTACK", 8)


# ---------------------------------------------------------------- fight

class Fight:
    MAX_TURNS = 40

    def __init__(self, deck: list[str], encounter: str, hp: int = 80, max_hp: int = 80, seed: int = 0,
                 energy: int = 3):
        self.rng = random.Random(seed)
        self.encounter = encounter
        self.deck_names = [d for d in deck]
        self.hp, self.max_hp, self.base_energy = hp, max_hp, energy
        self.start_hp = hp
        self.monsters = spawn(encounter, self.rng)
        cards = [make_card(n) for n in deck]
        self.rng.shuffle(cards)
        self.draw, self.hand, self.discard, self.exhaust = cards, [], [], []
        self.block = 0
        self.energy = 0
        self.powers: dict[str, int] = {}
        self.turn = 0
        self.log: list[str] = []
        self.flex = 0
        self.rage = 0
        self.thorns = 0
        self.double_tap = 0
        self.no_draw = False
        self.vault = False       # Vault: an extra turn before the monsters act
        self.turn_over = False   # a card ended the turn (Vault, Meditate)
        self.dealt_now = 0       # unblocked damage of the card being resolved (Wallop)
        self.cards_played = 0
        self.rampage: dict[str, int] = {}

    # -------------------------------------------------- helpers
    def alive(self) -> list[Monster]:
        return [m for m in self.monsters if not m.gone and m.hp > 0]

    def p(self, k: str) -> int:
        return self.powers.get(k, 0)

    def draw_cards(self, n: int) -> None:
        for _ in range(n):
            if self.no_draw or len(self.hand) >= 10:
                return
            if not self.draw:
                if not self.discard:
                    return
                self.draw = self.discard
                self.discard = []
                self.rng.shuffle(self.draw)
            c = self.draw.pop()
            self.hand.append(c)
            if c["type"] in ("STATUS", "CURSE") and self.p("Evolve"):
                self.draw_cards(self.p("Evolve"))
            if c["type"] in ("STATUS", "CURSE") and self.p("Fire Breathing"):
                for m in self.alive():
                    self.damage_monster(m, self.p("Fire Breathing"), attack=False)

    def add_card(self, name: str, pile: str) -> None:
        c = make_card(name)
        if pile == "hand" and len(self.hand) < 10:
            self.hand.append(c)
        elif pile == "draw":
            self.draw.insert(self.rng.randint(0, len(self.draw)), c)
        else:
            self.discard.append(c)

    def exhaust_card(self, c: dict) -> None:
        self.exhaust.append(c)
        if self.p("Feel No Pain"):
            self.gain_block(self.p("Feel No Pain"), raw=True)
        if self.p("Dark Embrace"):
            self.draw_cards(self.p("Dark Embrace"))
        if C.base_name(c["name"]) == "Sentinel":
            self.energy += 3 if c["upgrades"] else 2

    def gain_block(self, n: float, raw: bool = False) -> None:
        if not raw:
            n = (n + self.p("Dexterity")) * (0.75 if self.p("Frail") else 1.0)
        n = max(0, int(n))
        if n <= 0:
            return
        self.block += n
        if self.p("Juggernaut") and self.alive():
            self.damage_monster(self.rng.choice(self.alive()), self.p("Juggernaut"), attack=False)

    def lose_hp(self, n: int, from_card: bool = False) -> None:
        if n <= 0:
            return
        self.hp -= n
        if from_card and self.p("Rupture"):
            self.powers["Strength"] = self.p("Strength") + self.p("Rupture")

    def damage_monster(self, m: Monster, dmg: float, attack: bool = True) -> int:
        """Apply one hit; returns unblocked damage."""
        if m.gone or m.hp <= 0:
            return 0
        if attack and m.p("Vulnerable"):
            dmg *= 1.5
        dmg = int(dmg)
        if m.p("Intangible"):
            dmg = min(dmg, 1)
        if m.name == "Lagavulin" and m.state.get("asleep", 0) > 0 and dmg > m.block:
            m.state["asleep"] = 0
            m.state["stunned"] = True
            m.powers.pop("Metallicize", None)
        blocked = min(m.block, dmg)
        m.block -= blocked
        through = dmg - blocked
        m.hp -= through
        if through > 0 and m.p("Shifting"):
            m.powers["Strength"] = m.p("Strength") - through
            m.state["str_back"] = m.state.get("str_back", 0) + through
        if through > 0 and m.p("Curl Up"):
            m.block += m.powers.pop("Curl Up")
        if through > 0 and m.p("Plated Armor"):
            m.powers["Plated Armor"] -= 1
        if attack and m.p("Malleable"):
            m.block += m.p("Malleable")
            m.powers["Malleable"] += 1
        if attack and m.p("Thorns"):
            self.lose_hp(m.p("Thorns"))
        if m.name == "The Guardian" and not m.state.get("defensive") and through > 0:
            m.powers["Mode Shift"] -= through
            if m.powers["Mode Shift"] <= 0:
                m.state["defensive"] = True
                m.state["dturn"] = 0
                m.block += 20
                m.powers["Sharp Hide"] = 3
                set_move(m, "roll", "ATTACK", 9)
        if m.p("Split") and 0 < m.hp <= m.max_hp // 2 and not m.state.get("split"):
            m.state["split"] = True
            set_move(m, "split", "UNKNOWN")
        if m.hp <= 0:
            m.hp = 0
            m.gone = True
            if m.state.get("stolen"):
                self.hand.append(m.state.pop("stolen")) if len(self.hand) < 10 else self.discard.append(m.state.pop("stolen"))
        return through

    # -------------------------------------------------- state for the policy
    def message(self):
        s = copy.deepcopy(TEMPLATE)
        g = s["game_state"]
        g.update({"class": "IRONCLAD", "floor": 20 if ACT.get(self.encounter, 1) == 2 else 8,
                  "act": ACT.get(self.encounter, 1), "current_hp": self.hp, "max_hp": self.max_hp,
                  "room_type": "MonsterRoomBoss" if KIND.get(self.encounter) == "boss" else
                  ("MonsterRoomElite" if KIND.get(self.encounter) == "elite" else "MonsterRoom"),
                  "deck": [make_card(n) for n in self.deck_names], "potions": [],
                  "relics": [{"id": "Burning Blood", "name": "Burning Blood", "counter": -1}], "map": []})
        if self.encounter in ("Hexaghost", "The Guardian", "Slime Boss"):
            g["act_boss"] = self.encounter
        hand = []
        for c in self.hand:
            c = dict(c)
            c["is_playable"] = self.playable(c)
            hand.append(c)
        mons = []
        for m in self.monsters:
            per = 0
            if m.dmg:
                per = (m.dmg + m.p("Strength")) * (0.75 if m.p("Weakened") else 1.0) * (1.5 if self.p("Vulnerable") else 1.0)
            pw = [{"id": k if k != "Enrage" else "Anger", "name": k, "amount": v} for k, v in m.powers.items() if v]
            mons.append({"name": m.name, "id": m.name.replace(" ", ""), "current_hp": max(0, m.hp),
                         "max_hp": m.max_hp, "block": m.block, "intent": m.intent, "half_dead": False,
                         "is_gone": m.gone or m.hp <= 0, "move_id": 1, "move_base_damage": m.dmg or -1,
                         "move_adjusted_damage": int(per) if m.dmg else -1, "move_hits": max(1, m.hits),
                         "powers": pw})
        g["combat_state"] = {
            "draw_pile": self.draw, "discard_pile": self.discard, "exhaust_pile": self.exhaust,
            "cards_discarded_this_turn": 0, "times_damaged": 0, "turn": self.turn, "limbo": [],
            "hand": hand, "monsters": mons,
            "player": {"orbs": [], "current_hp": self.hp, "max_hp": self.max_hp, "block": self.block,
                       "energy": self.energy,
                       "powers": [{"id": k, "name": k, "amount": v} for k, v in self.powers.items() if v]}}
        s["available_commands"] = ["play", "end", "key", "click", "wait", "state"]
        return parse_message(s)

    def cost(self, c: dict) -> int:
        if c["cost"] == -1:
            return self.energy
        if self.p("Corruption") and c["type"] == "SKILL":
            return 0
        return max(0, c["cost"])

    def playable(self, c: dict) -> bool:
        if c["cost"] <= -2 or C.base_name(c["name"]) in CURSE_NAMES:
            return False
        if C.base_name(c["name"]) == "Clash" and any(h["type"] != "ATTACK" for h in self.hand):
            return False
        return self.cost(c) <= self.energy

    # -------------------------------------------------- playing a card
    def play(self, idx: int, target: int | None, card_obj) -> None:
        c = self.hand.pop(idx)
        name = C.base_name(c["name"])
        e = C.effects(card_obj)
        x = self.energy
        self.energy -= self.cost(c)
        self.cards_played += 1
        tgt = self.monsters[target] if target is not None and 0 <= target < len(self.monsters) else None
        if tgt is not None and (tgt.gone or tgt.hp <= 0):
            tgt = self.alive()[0] if self.alive() else None
        if c["type"] == "SKILL":
            for m in self.alive():
                if m.p("Enrage"):
                    m.powers["Strength"] = m.p("Strength") + m.p("Enrage")
        repeat = 2 if (self.double_tap and c["type"] == "ATTACK") else 1
        if repeat == 2:
            self.double_tap -= 1
        for _ in range(repeat):
            self._resolve(c, name, e, tgt, x)
        if c["type"] == "POWER":
            pass
        elif c["exhausts"] or e.exhaust or (self.p("Corruption") and c["type"] == "SKILL") or name == "Slimed":
            self.exhaust_card(c)
        else:
            self.discard.append(c)

    def _resolve(self, c, name, e, tgt, x) -> None:
        up = c["upgrades"]
        # energy / draw first where the card says so
        vuln_ok = not e.if_vuln or (tgt is not None and tgt.p("Vulnerable"))
        if e.hp_loss:
            self.lose_hp(e.hp_loss, from_card=True)
        if vuln_ok and e.energy:
            self.energy += e.energy
        if e.strength and (not e.conditional_strength or (tgt is not None and tgt.dmg)):
            self.powers["Strength"] = self.p("Strength") + e.strength
        if e.temp_strength:
            self.powers["Strength"] = self.p("Strength") + e.temp_strength
            self.flex += e.temp_strength
        if e.double_strength and self.p("Strength") > 0:
            self.powers["Strength"] *= 2
        if e.dex:
            self.powers["Dexterity"] = self.p("Dexterity") + e.dex
        for k, v in (("Vulnerable", e.self_vuln), ("Weakened", e.self_weak), ("Frail", e.self_frail)):
            if v:
                self.powers[k] = self.p(k) + v
        # hand exhausting
        exhausted = 0
        if e.exhaust_hand == "all":
            for h in list(self.hand):
                self.hand.remove(h)
                self.exhaust_card(h)
                exhausted += 1
        elif e.exhaust_hand == "nonattack":
            for h in [h for h in self.hand if h["type"] != "ATTACK"]:
                self.hand.remove(h)
                self.exhaust_card(h)
                exhausted += 1
        # block
        if e.block:
            self.gain_block(e.block)
        if e.block_per_exhaust:
            for _ in range(exhausted):
                self.gain_block(e.block_per_exhaust)
        if e.double_block:
            self.block *= 2
        # damage
        if e.damage or e.block_scaling or e.damage_per_exhaust:
            if e.block_scaling:
                base, hits = self.block, 1
            elif e.damage_per_exhaust:
                base, hits = e.damage_per_exhaust, exhausted
            else:
                base = e.damage + e.per_strike * sum(1 for d in self.deck_names if "Strike" in d)
                base += self.rampage.get(c["uuid"], 0)
                hits = x if (e.x_cost or e.hits == 0) else e.hits
            per = base + self.p("Strength") * e.strength_mult
            if self.p("Weakened"):
                per *= 0.75
            for _ in range(max(0, hits)):
                live = self.alive()
                if not live:
                    break
                if e.aoe:
                    victims = live
                elif e.random_target:
                    victims = [self.rng.choice(live)]
                else:
                    victims = [tgt] if tgt is not None and not tgt.gone else live[:1]
                for m in victims:
                    if m.p("Sharp Hide") and c["type"] == "ATTACK":
                        self.lose_hp(m.p("Sharp Hide"))
                    through = self.damage_monster(m, per)
                    self.dealt_now += through
                    if name == "Reaper":
                        self.hp = min(self.max_hp, self.hp + through)
                    if name == "Feed" and m.gone:
                        self.max_hp += 4 if up else 3
                        self.hp += 4 if up else 3
            if c["type"] == "ATTACK" and self.rage:
                self.gain_block(self.rage, raw=True)
        # debuffs
        group = self.alive() if (e.aoe_debuff or e.aoe) else ([tgt] if tgt is not None and not tgt.gone else [])
        for m in group:
            for k, v in (("Vulnerable", e.vuln), ("Weakened", e.weak)):
                if not v:
                    continue
                if m.p("Artifact"):
                    m.powers["Artifact"] -= 1
                else:
                    m.powers[k] = m.p(k) + v
            for v, perm in ((e.enemy_str_down, True), (e.enemy_str_down_temp, False)):
                if v:
                    if m.p("Artifact"):
                        m.powers["Artifact"] -= 1
                    else:
                        m.powers["Strength"] = m.p("Strength") - v
                        if not perm:
                            m.state["str_back"] = m.state.get("str_back", 0) + v
        if vuln_ok and e.draw:
            self.draw_cards(e.draw)
        self.thorns += e.thorns
        if getattr(e, "wallop", False):
            self.gain_block(self.dealt_now, raw=True)
        self.dealt_now = 0
        if getattr(e, "judgment", 0) and tgt is not None and not tgt.gone and 0 < tgt.hp <= e.judgment:
            tgt.hp = 0
            tgt.gone = True
        if getattr(e, "mark", 0) and tgt is not None and not tgt.gone:
            if tgt.p("Artifact"):
                tgt.powers["Artifact"] -= 1
            else:
                tgt.powers["Mark"] = tgt.p("Mark") + e.mark
            for m in self.alive():
                if m.p("Mark"):
                    m.hp -= m.p("Mark")
                    if m.hp <= 0:
                        m.hp = 0
                        m.gone = True
        if getattr(e, "bullet_time", False):
            self.energy += sum(max(0, h["cost"]) for h in self.hand)
            self.no_draw = True
        if getattr(e, "vault", False):
            self.vault = True
        if getattr(e, "ends_turn", False):
            self.turn_over = True
        # specials the text parser can't express
        if name == "Anger":
            self.add_card("Anger+" if up else "Anger", "discard")
        elif name == "Wild Strike":
            self.add_card("Wound", "draw")
        elif name == "Reckless Charge":
            self.add_card("Dazed", "draw")
        elif name == "Power Through":
            self.add_card("Wound", "hand")
            self.add_card("Wound", "hand")
        elif name == "Immolate":
            self.add_card("Burn", "discard")
        elif name == "Rampage":
            self.rampage[c["uuid"]] = self.rampage.get(c["uuid"], 0) + (8 if up else 5)
        elif name in ("True Grit", "Burning Pact") and self.hand:
            worst = min(self.hand, key=lambda h: (h["type"] not in ("STATUS", "CURSE"),
                                                  C.base_name(h["name"]) not in ("Strike", "Defend")))
            if name == "True Grit" and not up:
                worst = self.rng.choice(self.hand)
            self.hand.remove(worst)
            self.exhaust_card(worst)
        elif name == "Battle Trance":
            self.no_draw = True
        elif name == "Rage":
            self.rage += 5 if up else 3
        elif name == "Double Tap":
            self.double_tap += 2 if up else 1
        elif name == "Seeing Red":
            pass
        elif name == "Havoc" and self.draw:
            top = self.draw.pop()
            self.exhaust_card(top)
        if c["type"] == "POWER":
            if name in ("Barricade", "Corruption"):
                self.powers[name] = 1
            elif name == "Feel No Pain":
                self.powers[name] = self.p(name) + (4 if up else 3)
            elif name == "Dark Embrace":
                self.powers[name] = self.p(name) + 1
            elif name == "Juggernaut":
                self.powers[name] = self.p(name) + (7 if up else 5)
            elif name == "Rupture":
                self.powers[name] = self.p(name) + (2 if up else 1)
            elif name == "Evolve":
                self.powers[name] = self.p(name) + (2 if up else 1)
            elif name == "Fire Breathing":
                self.powers[name] = self.p(name) + (10 if up else 6)
            elif name == "Brutality":
                self.powers[name] = self.p(name) + 1
            if e.per_turn_strength:
                self.powers["Demon Form"] = self.p("Demon Form") + int(round(e.per_turn_strength))
            if e.per_turn_block:
                self.powers["Metallicize"] = self.p("Metallicize") + int(round(e.per_turn_block))
            if e.per_turn_energy:
                self.powers["Berserk"] = self.p("Berserk") + int(round(e.per_turn_energy))
            if e.per_turn_damage:
                self.powers["Combust"] = self.p("Combust") + int(round(e.per_turn_damage))
            for m in self.alive():
                if m.p("Curiosity"):
                    m.powers["Strength"] = m.p("Strength") + m.p("Curiosity")

    # -------------------------------------------------- turns
    def start_turn(self) -> None:
        self.turn += 1
        if not self.p("Barricade"):
            self.block = 0
        self.energy = self.base_energy + self.p("Berserk")
        self.no_draw = False
        self.rage = 0
        self.thorns = 0
        if self.p("Demon Form"):
            self.powers["Strength"] = self.p("Strength") + self.p("Demon Form")
        if self.p("Brutality"):
            self.lose_hp(self.p("Brutality"), from_card=True)
        self.draw_cards(5 + self.p("Brutality"))

    def end_turn(self) -> None:
        for c in list(self.hand):
            n = C.base_name(c["name"])
            if n == "Burn":
                self.lose_hp(max(0, (4 if c["upgrades"] else 2) - self.block))
            if n in ("Regret",):
                self.lose_hp(len(self.hand))
            if n == "Decay":
                self.lose_hp(2)
            if c["ethereal"]:
                self.hand.remove(c)
                self.exhaust_card(c)
        self.discard.extend(self.hand)
        self.hand = []
        if self.p("Metallicize"):
            self.gain_block(self.p("Metallicize"), raw=True)
        if self.p("Combust"):
            self.lose_hp(1)
            for m in self.alive():
                self.damage_monster(m, self.p("Combust"), attack=False)
        if self.flex:
            self.powers["Strength"] = self.p("Strength") - self.flex
            self.flex = 0

    def monsters_act(self) -> None:
        for m in self.monsters:
            if m.gone or m.hp <= 0:
                continue
            if not m.p("Barricade"):
                m.block = 0
            if "Malleable" in m.powers:
                m.powers["Malleable"] = m.state.setdefault("mall_base", 3)
            self.monster_move(m)
            m.state.setdefault("hist", []).append(m.move)
            m.turn += 1
        new = []
        for m in self.monsters:
            if m.move == "split" and not m.gone and m.hp > 0:
                m.gone = True
                kids = {"Slime Boss": ["Acid Slime (L)", "Spike Slime (L)"],
                        "Acid Slime (L)": ["Acid Slime (M)", "Acid Slime (M)"],
                        "Spike Slime (L)": ["Spike Slime (M)", "Spike Slime (M)"]}[m.name]
                for k in kids:
                    new.append(Monster(k, m.hp, m.hp, powers={"Split": 1} if "(L)" in k else {}))
        self.monsters = [m for m in self.monsters if not (m.gone and m.move == "split")] + new
        for m in self.monsters:
            if m.gone or m.hp <= 0:
                continue
            if m.p("Generic Strength Up"):
                m.powers["Strength"] = m.p("Strength") + m.p("Generic Strength Up")
            if m.p("Explosive"):
                m.powers["Explosive"] -= 1
            if m.p("Fading"):
                m.powers["Fading"] -= 1
                if m.powers["Fading"] <= 0:
                    m.hp, m.gone = 0, True
                    continue
            if m.p("Ritual") and m.state.get("ritual_ready"):
                m.powers["Strength"] = m.p("Strength") + m.p("Ritual")
            m.state["ritual_ready"] = True
            if m.p("Metallicize"):
                m.block += m.p("Metallicize")
            if m.p("Plated Armor"):
                m.block += m.p("Plated Armor")
            for k in ("Vulnerable", "Weakened"):
                if m.p(k):
                    m.powers[k] -= 1
            if m.state.get("str_back"):
                m.powers["Strength"] = m.p("Strength") + m.state.pop("str_back")
            choose_move(m, self)
        for k in ("Vulnerable", "Weakened", "Frail"):
            if self.p(k):
                self.powers[k] -= 1

    def hit_player(self, m: Monster, dmg: int, hits: int) -> None:
        per = (dmg + m.p("Strength")) * (0.75 if m.p("Weakened") else 1.0) * (1.5 if self.p("Vulnerable") else 1.0)
        per = max(0, int(per))
        for _ in range(hits):
            blocked = min(self.block, per)
            self.block -= blocked
            through = per - blocked
            self.lose_hp(through)
            if through > 0 and m.p("Painful Stabs"):
                self.add_card("Wound", "discard")
            if self.thorns:
                self.damage_monster(m, self.thorns, attack=False)
            if m.gone or m.hp <= 0:
                break

    def monster_move(self, m: Monster) -> None:
        mv = m.move
        if mv in ("sleep",):
            m.state["asleep"] -= 1
            if m.state["asleep"] <= 0:
                m.powers.pop("Metallicize", None)
            return
        if mv == "stun":
            m.state["stunned"] = False
            return
        if m.dmg:
            self.hit_player(m, m.dmg, max(1, m.hits))
        n = m.name
        if mv == "bellow" and n == "Jaw Worm":
            m.powers["Strength"] = m.p("Strength") + 3
            m.block += 6
        elif mv == "thrash":
            m.block += 5
        elif mv == "incantation":
            m.powers["Ritual"] = 3
            m.state["ritual_ready"] = False
        elif mv == "bellow":  # Gremlin Nob
            m.powers["Enrage"] = 2
        elif mv == "bash" and n == "Gremlin Nob":
            self.powers["Vulnerable"] = self.p("Vulnerable") + 2
        elif mv == "attack" and n == "Lagavulin" or mv == "siphon":
            m.state["cycle"] = m.state.get("cycle", 0) + 1
            if mv == "siphon":
                self.powers["Strength"] = self.p("Strength") - 1
                self.powers["Dexterity"] = self.p("Dexterity") - 1
        elif mv == "bolt":
            self.add_card("Dazed", "discard")
            self.add_card("Dazed", "discard")
            m.state["next"] = "beam"
        elif mv == "beam":
            m.state["next"] = "bolt"
        elif mv == "sear":
            self.add_card("Burn+" if m.state.get("burn_up") else "Burn", "discard")
        elif mv == "inflame":
            m.powers["Strength"] = m.p("Strength") + 2
            m.block += 12
        elif mv == "inferno":
            for _ in range(3):
                self.add_card("Burn+", "discard")
            m.state["burn_up"] = True
            for pile in (self.draw, self.discard):
                for c in pile:
                    if c["name"] == "Burn":
                        c["upgrades"], c["name"] = 1, "Burn+"
        elif mv == "charge":
            m.block += 9
            m.state["ocycle"] = m.state.get("ocycle", 0) + 1
        elif mv in ("bash", "whirl") and n == "The Guardian" or mv == "vent":
            if mv == "vent":
                self.powers["Weakened"] = self.p("Weakened") + 2
                self.powers["Vulnerable"] = self.p("Vulnerable") + 2
            m.state["ocycle"] = m.state.get("ocycle", 0) + 1
        elif mv in ("dmode", "roll"):
            m.state["dturn"] = m.state.get("dturn", 0) + 1
        elif mv == "twin":
            m.state["defensive"] = False
            m.powers.pop("Sharp Hide", None)
            m.state["shift"] = m.state.get("shift", 30) + 10
            m.powers["Mode Shift"] = m.state["shift"]
            m.state["ocycle"] = 3  # offensive again, starting with Whirlwind
        elif mv == "goop":
            for _ in range(3):
                self.add_card("Slimed", "discard")
        elif mv in ("spit",) or (mv == "flame"):
            self.add_card("Slimed", "discard")
            if mv == "flame" and "(L)" in n:
                self.add_card("Slimed", "discard")
            if mv == "spit" and "(L)" in n:
                self.add_card("Slimed", "discard")
        elif mv == "lick":
            k = "Weakened" if n.startswith("Acid") else "Frail"
            self.powers[k] = self.p(k) + (2 if "(L)" in n else 1)
        elif mv == "activate" and n == "Spheric Guardian":
            m.block += 25
        elif mv == "attack_debuff":
            self.powers["Frail"] = self.p("Frail") + 5
        elif mv == "harden":
            m.block += 15
        elif mv == "spores":
            self.powers["Weakened"] = self.p("Weakened") + 2
            self.powers["Frail"] = self.p("Frail") + 2
        elif mv == "fell":
            self.powers["Frail"] = self.p("Frail") + 2
        elif mv == "suck":
            m.hp = min(m.max_hp, m.hp + 10)
        elif mv == "anger":
            m.state["angry"] = True
            for k in ("Vulnerable", "Weakened"):
                m.powers.pop(k, None)
            m.powers["Strength"] = m.p("Strength") + 6
        elif mv == "stance":
            m.block += 15
            m.powers["Metallicize"] = m.p("Metallicize") + 5
            m.state["stances"] = m.state.get("stances", 0) + 1
        elif mv == "gloat":
            m.powers["Strength"] = m.p("Strength") + 2
        elif mv == "taunt":
            self.powers["Weakened"] = self.p("Weakened") + 2
            self.powers["Vulnerable"] = self.p("Vulnerable") + 2
        elif mv == "slap":
            self.powers["Frail"] = self.p("Frail") + 2
            self.powers["Vulnerable"] = self.p("Vulnerable") + 2
        elif mv == "boost":
            m.powers["Strength"] = m.p("Strength") + 3
            m.block += 9
        elif mv == "stasis" and self.draw:
            # steals the best card until the orb dies (approximation: it leaves the draw pile)
            best = max(self.draw, key=lambda c: (c["type"] == "POWER", c["rarity"] != "BASIC", c["cost"]))
            self.draw.remove(best)
            m.state["stolen"] = best
        elif mv == "support":
            for o in self.alive():
                if o.name == "Bronze Automaton":
                    o.block += 12
        elif mv == "spike":
            m.powers["Thorns"] = m.p("Thorns") + 2
        elif mv == "repulse":
            self.add_card("Dazed", "draw")
            self.add_card("Dazed", "draw")
        elif mv == "explode":
            m.hp, m.gone = 0, True
        elif mv == "laser":
            self.add_card("Burn", "discard")
            self.add_card("Burn", "draw")

    # -------------------------------------------------- the loop
    def run(self, params: dict, deck_for_planner: list[str] | None = None, max_nodes: int = 1200) -> dict:
        for m in self.monsters:
            choose_move(m, self)
        while self.hp > 0 and self.alive() and self.turn < self.MAX_TURNS:
            self.start_turn()
            for m in self.monsters:
                if m.name == "Hexaghost" and m.move == "divider":
                    set_move(m, "divider", "ATTACK", self.hp // 12 + 1, 6)
            self.turn_over = False
            for _ in range(25):
                if self.hp <= 0 or not self.alive() or self.turn_over:
                    break
                msg = self.message()
                plan = planner.plan_turn(msg, params, self.deck_names, max_nodes=max_nodes)
                if not plan.steps or plan.value - plan.base < params.get("end_threshold", 0.5):
                    break
                i, t = plan.steps[0]
                card_obj = msg.game_state.combat_state.hand[i]
                if not self.playable(self.hand[i]):
                    break
                self.play(i, t, card_obj)
            if self.hp <= 0 or not self.alive():
                break
            self.end_turn()
            if self.hp <= 0:
                break
            if self.vault:
                self.vault = False   # the extra turn comes before the monsters act
                continue
            self.monsters_act()
        win = self.hp > 0 and not self.alive()
        total = sum(m.max_hp for m in self.monsters) or 1
        left = sum(max(0, m.hp) for m in self.monsters if not m.gone)
        return {"encounter": self.encounter, "win": win, "hp_lost": self.start_hp - max(0, self.hp),
                "enemy_left": round(left / total, 3),
                "hp_left": max(0, self.hp), "max_hp": self.max_hp, "turns": self.turn,
                "cards_played": self.cards_played}
