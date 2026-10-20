"""synergy.py -- why a card or relic is good *for this deck*.

Learned card weights say how good a card is on average. This module adds the part that
depends on the rest of the deck: Ironclad role tags for cards and relics, rules for which
roles feed which (an engine plus its payoff), and needs that make a payoff card dead
without its enabler (Limit Break with no Strength source).

Every rule is also a learnable feature ("synrule:<name>") whose starting weight comes from
the playbook, so the network learns how much each synergy really matters; the LLM gets the
same reasoning as plain text ("+ Feel No Pain: your Corruption, Second Wind exhaust").
"""

from __future__ import annotations

import re
from collections import Counter

# ---------------------------------------------------------------- role tags (Ironclad)

CARD_TAGS: dict[str, set[str]] = {
    # Strength sources and payoffs
    "Inflame": {"str_src", "power"}, "Spot Weakness": {"str_src"}, "Demon Form": {"str_src", "power", "scaling"},
    "Limit Break": {"str_pay", "str_mult"}, "Flex": {"str_temp"}, "Rupture": {"str_src", "selfdmg_pay", "power"},
    "Heavy Blade": {"str_pay"}, "Twin Strike": {"multi"}, "Sword Boomerang": {"multi"}, "Whirlwind": {"multi", "aoe", "xcost"},
    "Pummel": {"multi"}, "Reaper": {"str_pay", "aoe", "heal"}, "Fiend Fire": {"exhaust_bulk", "str_pay"},
    "Brutality": {"selfdmg", "draw_engine", "power"}, "Berserk": {"power", "energy"},
    # exhaust engine and payoffs
    "Corruption": {"exhaust_engine", "power", "skill_pay"}, "Feel No Pain": {"exhaust_pay", "power"},
    "Dark Embrace": {"exhaust_pay", "power"}, "Second Wind": {"exhaust_bulk"}, "Sever Soul": {"exhaust_bulk"},
    "Burning Pact": {"exhaust_one", "draw"}, "True Grit": {"exhaust_one"}, "Exhume": {"exhaust_need"},
    "Sentinel": {"exhaust_target"}, "Offering": {"selfdmg", "draw", "energy", "exhausts"},
    "Seeing Red": {"energy", "exhausts"}, "Impervious": {"block_big", "exhausts"}, "Shockwave": {"vuln_src", "weak_src", "exhausts"},
    # block engine and payoffs
    "Barricade": {"block_engine", "power"}, "Entrench": {"block_pay"}, "Body Slam": {"block_pay"},
    "Juggernaut": {"block_pay", "power"}, "Metallicize": {"block_gen", "power"}, "Shrug It Off": {"block_gen", "draw"},
    "Flame Barrier": {"block_gen"}, "Ghostly Armor": {"block_gen", "ethereal"}, "Power Through": {"block_gen", "status_maker"},
    "Iron Wave": {"block_gen"}, "Defend": {"block_gen", "exhaust_target"},
    # self damage
    "Hemokinesis": {"selfdmg"}, "Bloodletting": {"selfdmg", "energy"}, "Combust": {"selfdmg", "power", "aoe"},
    "Blood for Blood": {"selfdmg_pay"},
    # statuses
    "Wild Strike": {"status_maker"}, "Reckless Charge": {"status_maker"}, "Immolate": {"status_maker", "aoe"},
    "Evolve": {"status_pay", "power"}, "Fire Breathing": {"status_pay", "power"},
    # vulnerable / weak
    "Bash": {"vuln_src"}, "Uppercut": {"vuln_src", "weak_src"}, "Thunderclap": {"vuln_src", "aoe"},
    "Clothesline": {"weak_src"}, "Intimidate": {"weak_src"}, "Disarm": {"str_down"}, "Dropkick": {"vuln_pay"},
    # misc
    "Perfected Strike": {"strike_pay"}, "Headbutt": {"topdeck"}, "Battle Trance": {"draw"}, "Pommel Strike": {"draw"},
    "Warcry": {"topdeck"}, "Dual Wield": {"copy"}, "Double Tap": {"attack_pay"}, "Armaments": {"upgrade"},
    "Havoc": {"draw"}, "Infernal Blade": {"exhausts"}, "Rampage": {"long_fight"}, "Searing Blow": {"upgrade_pay"},
    "Cleave": {"aoe"}, "Carnage": {"ethereal"}, "Bludgeon": {"big_hit"}, "Feed": {"big_hit"}, "Anger": {"cheap"},
}

RELIC_TAGS: dict[str, set[str]] = {
    "Vajra": {"str_src"}, "Girya": {"str_src"}, "Mutagenic Strength": {"str_src"}, "Brimstone": {"str_src"},
    "Red Skull": {"str_src"}, "Snecko Eye": {"draw_engine", "likes_high_cost"}, "Runic Pyramid": {"likes_exhaust_bulk"},
    "Dead Branch": {"exhaust_pay"}, "Charon's Ashes": {"exhaust_pay"}, "Medical Kit": {"status_pay"},
    "Strike Dummy": {"strike_pay"}, "Paper Phrog": {"vuln_pay"}, "Champion Belt": {"vuln_pay"},
    "Self-Forming Clay": {"selfdmg_pay"}, "Runic Cube": {"selfdmg_pay"}, "Mummified Hand": {"power_pay"},
    "Chemical X": {"xcost_pay"}, "Shuriken": {"attack_pay"}, "Kunai": {"skill_pay"}, "Ornamental Fan": {"attack_pay"},
    "Pen Nib": {"big_hit_pay"}, "Anchor": {"block_gen"}, "Horn Cleat": {"block_gen"}, "Orichalcum": {"block_gen"},
    "Thread and Needle": {"block_gen"}, "Bronze Scales": {"block_gen"}, "Calipers": {"block_engine"},
    "Toxic Egg": {"skill_pay"}, "Molten Egg": {"attack_pay"}, "Frozen Egg": {"power_pay"},
    "Velvet Choker": {"hates_many_cards"}, "Fusion Hammer": {"likes_upgraded"}, "Burning Blood": {"heal"},
    "Black Blood": {"heal"}, "Meat on the Bone": {"heal"}, "Bag of Preparation": {"draw"}, "Lantern": {"energy"},
    "Ice Cream": {"energy"}, "Happy Flower": {"energy"}, "Art of War": {"skill_pay"},
}

# (rule name, card tag that benefits, deck tags that feed it, weight per feeder, cap of feeders counted)
RULES = [
    ("strength->multi", "multi", {"str_src", "str_temp"}, 0.35, 3),
    ("strength->payoff", "str_pay", {"str_src"}, 0.5, 3),
    ("multi->strength", "str_src", {"multi", "str_pay"}, 0.25, 4),
    ("exhaust->payoff", "exhaust_pay", {"exhaust_engine", "exhaust_bulk", "exhaust_one", "exhausts", "status_maker"}, 0.3, 5),
    ("payoff->exhaust", "exhaust_engine", {"exhaust_pay", "block_gen"}, 0.25, 6),
    ("payoff->bulk", "exhaust_bulk", {"exhaust_pay", "status_maker"}, 0.35, 3),
    ("corruption->skills", "skill_pay", {"block_gen", "exhaust_one", "draw"}, 0.12, 8),
    ("block->barricade", "block_engine", {"block_gen", "block_pay", "block_big"}, 0.2, 6),
    ("barricade->payoff", "block_pay", {"block_engine", "block_gen", "block_big"}, 0.25, 5),
    ("selfdmg->payoff", "selfdmg_pay", {"selfdmg"}, 0.4, 4),
    ("payoff->selfdmg", "selfdmg", {"selfdmg_pay"}, 0.4, 2),
    ("status->payoff", "status_pay", {"status_maker"}, 0.45, 3),
    ("payoff->status", "status_maker", {"status_pay", "exhaust_bulk"}, 0.35, 3),
    ("vuln->payoff", "vuln_pay", {"vuln_src"}, 0.35, 3),
    ("strikes->perfected", "strike_pay", {"strike"}, 0.12, 8),
    ("topdeck->bigcards", "topdeck", {"draw", "str_mult", "block_engine"}, 0.15, 3),
    ("copy->powers", "copy", {"power", "big_hit"}, 0.15, 4),
    ("doubletap->attacks", "attack_pay", {"big_hit", "multi"}, 0.2, 3),
    ("exhume->exhaust", "exhaust_need", {"exhaust_engine", "exhaust_bulk", "exhausts"}, 0.3, 3),
    ("xcost->energy", "xcost", {"energy", "xcost_pay"}, 0.3, 2),
    ("searing->upgrades", "upgrade_pay", {"upgrade"}, 0.4, 2),
]

# payoff cards that are close to dead without an enabler: (card tag, enabler tags, penalty)
NEEDS = [
    ("str_mult", {"str_src"}, -1.2),          # Limit Break with no Strength
    ("exhaust_need", {"exhaust_engine", "exhaust_bulk", "exhausts", "exhaust_one"}, -0.8),
    ("status_pay", {"status_maker"}, -0.3),    # Evolve / Fire Breathing still fine vs status fights
    ("block_pay", {"block_engine", "block_gen", "block_big"}, -0.5),
    ("selfdmg_pay", {"selfdmg"}, -0.4),
    ("vuln_pay", {"vuln_src"}, -0.4),
]


NEED_TEXT = {"str_mult": "needs a Strength source (none yet)", "exhaust_need": "needs exhausted cards to return",
             "status_pay": "no status cards to trigger it yet", "block_pay": "needs block makers",
             "selfdmg_pay": "no self-damage cards yet", "vuln_pay": "no Vulnerable source yet"}


def base(name: str) -> str:
    return re.sub(r"\+\d*$", "", name or "").strip()


def card_tags(name: str) -> set[str]:
    b = base(name)
    tags = set(CARD_TAGS.get(b, ()))
    if "Strike" in b:
        tags.add("strike")
    return tags


def deck_tags(deck_names: list[str], relic_names: list[str] = ()) -> Counter:
    c: Counter = Counter()
    for n in deck_names:
        c.update(card_tags(n))
    for r in relic_names:
        c.update(RELIC_TAGS.get(r, ()))
    return c


def explain(name: str, deck_names: list[str], relic_names: list[str] = ()) -> tuple[dict[str, float], str]:
    """(features {synrule:...: value}, short reason) for adding `name` to this deck."""
    tags = card_tags(name)
    have = deck_tags(deck_names, relic_names)
    feats: dict[str, float] = {}
    reasons = []
    by_tag: dict[str, list[str]] = {}
    for n in list(deck_names) + list(relic_names):
        for t in card_tags(n) | RELIC_TAGS.get(n, set()):
            by_tag.setdefault(t, []).append(base(n))
    for rule, tag, feeders, w, cap in RULES:
        if tag not in tags:
            continue
        n = min(cap, sum(have[f] for f in feeders))
        if n:
            feats[f"synrule:{rule}"] = float(n)
            who = sorted({x for f in feeders for x in by_tag.get(f, [])})[:3]
            reasons.append(f"+synergy ({rule}) with your {', '.join(who)}")
    for tag, enablers, pen in NEEDS:
        if tag in tags and not any(have[e] for e in enablers):
            feats[f"synneed:{tag}"] = 1.0
            reasons.append(f"- {NEED_TEXT.get(tag, 'missing its enabler')}")
    return feats, "; ".join(reasons)


def prior(key: str) -> float | None:
    """Starting weight for synergy features (model.prior uses it)."""
    kind, _, rest = key.partition(":")
    if kind == "synrule":
        return next((w for r, _, _, w, _ in RULES if r == rest), 0.2)
    if kind == "synneed":
        return next((p for t, _, p in NEEDS if t == rest), -0.5)
    return None


def relic_fit(relic: str, deck_names: list[str]) -> tuple[float, str]:
    """How well a relic fits this deck: (bonus, reason)."""
    have = deck_tags(deck_names)
    costs = []
    for n in deck_names:
        c = CARD_COST.get(base(n))
        if c is not None:
            costs.append(c)
    avg = sum(costs) / len(costs) if costs else 1.0
    tags = RELIC_TAGS.get(relic, set())
    if relic == "Snecko Eye":
        return (0.9, f"avg cost {avg:.1f}: many 2-3 cost cards") if avg >= 1.35 else (-0.4, f"cheap deck (avg cost {avg:.1f})")
    if relic == "Runic Pyramid":
        n = have["exhaust_bulk"] + have["exhaust_engine"] + have["energy"]
        return (0.6, "Corruption/Fiend Fire/Second Wind/energy to unclog") if n else (-0.3, "nothing to unclog a full hand")
    if relic == "Velvet Choker":
        n = have["draw"] + have["cheap"] + have["str_temp"]
        return (-0.4, "deck wants to play many cards") if n >= 3 else (0.2, "few cheap/draw cards")
    if relic == "Mummified Hand":
        return (0.3 * min(3, have["power"]), f"{have['power']} powers") if have["power"] else (0.0, "")
    if "exhaust_pay" in tags:
        n = have["exhaust_engine"] + have["exhaust_bulk"] + have["exhausts"] + have["exhaust_one"]
        return (0.2 * min(4, n), f"{n} exhaust cards") if n else (0.0, "")
    if "status_pay" in tags:
        n = have["status_maker"]
        return (0.3 * min(3, n), f"{n} status makers") if n else (-0.2, "no status makers")
    if "selfdmg_pay" in tags:
        n = have["selfdmg"]
        return (0.25 * min(4, n), f"{n} self-damage cards") if n else (0.0, "")
    if "vuln_pay" in tags:
        n = have["vuln_src"]
        return (0.25 * min(3, n), f"{n} Vulnerable sources") if n else (0.0, "")
    if "strike_pay" in tags:
        n = have["strike"]
        return (0.08 * min(8, n), f"{n} Strike cards") if n else (0.0, "")
    if "xcost_pay" in tags:
        return (0.6, "Whirlwind") if have["xcost"] else (-0.5, "no X-cost card")
    if "str_src" in tags:
        n = have["multi"] + have["str_pay"]
        return (0.15 * min(4, n), f"{n} multi-hit/Strength payoffs") if n else (0.0, "")
    if "attack_pay" in tags or "skill_pay" in tags:
        return (0.1, "")
    return (0.0, "")


# energy costs of Ironclad cards (base), for deck cost profiles
CARD_COST = {
    "Anger": 0, "Armaments": 1, "Barricade": 3, "Bash": 2, "Battle Trance": 0, "Berserk": 0, "Blood for Blood": 4,
    "Bloodletting": 0, "Bludgeon": 3, "Body Slam": 1, "Brutality": 0, "Burning Pact": 1, "Carnage": 2, "Clash": 0,
    "Cleave": 1, "Clothesline": 2, "Combust": 1, "Corruption": 3, "Dark Embrace": 2, "Defend": 1, "Demon Form": 3,
    "Disarm": 1, "Double Tap": 1, "Dropkick": 1, "Dual Wield": 1, "Entrench": 2, "Evolve": 1, "Exhume": 1, "Feed": 1,
    "Feel No Pain": 1, "Fiend Fire": 2, "Fire Breathing": 1, "Flame Barrier": 2, "Flex": 0, "Ghostly Armor": 1,
    "Havoc": 1, "Headbutt": 1, "Heavy Blade": 2, "Hemokinesis": 1, "Immolate": 2, "Impervious": 2,
    "Infernal Blade": 1, "Inflame": 1, "Intimidate": 0, "Iron Wave": 1, "Juggernaut": 2, "Limit Break": 1,
    "Metallicize": 1, "Offering": 0, "Perfected Strike": 2, "Pommel Strike": 1, "Power Through": 1, "Pummel": 1,
    "Rage": 0, "Rampage": 1, "Reaper": 2, "Reckless Charge": 0, "Rupture": 1, "Searing Blow": 2, "Second Wind": 1,
    "Seeing Red": 1, "Sentinel": 1, "Sever Soul": 2, "Shockwave": 2, "Shrug It Off": 1, "Spot Weakness": 1,
    "Strike": 1, "Sword Boomerang": 1, "Thunderclap": 1, "True Grit": 1, "Twin Strike": 1, "Uppercut": 2,
    "Warcry": 0, "Whirlwind": 1, "Wild Strike": 1,
}
