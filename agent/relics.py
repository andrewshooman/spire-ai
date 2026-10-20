"""relics.py -- judge relics against THIS run: the deck's energy demand, draw, exhaust outlets,
clog risk, gold, HP and potions, instead of a fixed tier list.

    profile(g)            numbers about the deck and run that relics care about
    boss_fit(name, p)     (bonus, reasons) for a boss relic in this run
    buckets(p)            coarse profile tags; relicctx:<relic>|<tag> features let the bot learn
                          which relics pay off in which kind of deck (prior 0, learned from outcomes)
    describe(p)           one line for the LLM prompt
"""

from __future__ import annotations

import cards as C
import synergy

# +1 energy every turn (with a downside)
ENERGY_RELICS = {"Coffee Dripper", "Cursed Key", "Ectoplasm", "Fusion Hammer", "Philosopher's Stone",
                 "Runic Dome", "Sozu", "Velvet Choker", "Busted Crown", "Mark of Pain", "Slaver's Collar"}
OWNED_ENERGY = ENERGY_RELICS | {"Lantern", "Happy Flower", "Ice Cream", "Sundial", "Art of War"}
EXHAUST_OUTLETS = {"Corruption", "Second Wind", "Fiend Fire", "Burning Pact", "True Grit", "Sever Soul",
                   "Havoc", "Purity"}
HEAL_RELICS = {"Black Blood", "Meat on the Bone", "Bloody Idol", "Bird-Faced Urn", "Toy Ornithopter"}
HEAL_CARDS = {"Reaper", "Feed"}


def profile(g) -> dict:
    deck = [d for d in g.deck]
    names = [d.name for d in deck]
    relics = {r.name for r in g.relics}
    tags = synergy.deck_tags(names, list(relics))
    playable = [d for d in deck if d.type.value not in ("CURSE", "STATUS")]
    costs = [d.cost for d in playable if d.cost >= 0]
    avg = sum(costs) / max(1, len(costs))
    base = [C.base_name(n) for n in names]
    return {
        "n": len(deck), "avg_cost": round(avg, 2),
        "high_cost": sum(1 for c in costs if c >= 2), "zero_cost": sum(1 for c in costs if c == 0),
        "xcost": sum(1 for d in playable if d.cost == -1),
        "draw": sum(1 for d in playable if "draw" in C.roles(d)),
        "energy_gen": tags["energy"] + sum(1 for r in relics if r in OWNED_ENERGY),
        "energy_relics": sum(1 for r in relics if r in OWNED_ENERGY),
        "exhaust_outlets": sum(1 for b in base if b in EXHAUST_OUTLETS),
        "exhaust_pay": tags["exhaust_pay"], "status_makers": tags["status_maker"], "selfdmg": tags["selfdmg"],
        "curses": sum(1 for d in deck if d.type.value == "CURSE"),
        "basics": sum(1 for b in base if b in C.BASIC),
        "powers": sum(1 for d in playable if d.type.value == "POWER"),
        "unupgraded": sum(1 for d in playable if not d.upgrades and C.base_name(d.name) not in C.BASIC),
        "weak_tools": sum(1 for d in playable if C.effects(d).weak or C.effects(d).enemy_str_down
                          or C.effects(d).enemy_str_down_temp),
        "healing": bool(relics & HEAL_RELICS) or any(b in HEAL_CARDS for b in base),
        "gold": g.gold, "hp_frac": round(g.current_hp / max(1, g.max_hp), 2), "act": g.act,
        "potions": sum(1 for p in g.potions if p.id != "Potion Slot"), "slots": len(g.potions),
        "relics": relics,
    }


def energy_demand(p: dict) -> float:
    """Energy a typical turn wants: ~5 cards (more with draw) at the deck's average cost, X-costs soak up extra."""
    cards_seen = 5 + min(3, p["draw"]) * 0.5
    return cards_seen * p["avg_cost"] + 0.75 * min(2, p["xcost"])


def energy_value(p: dict) -> tuple[float, str]:
    """How much +1 energy per turn is worth to this deck."""
    supply = 3 + p["energy_relics"]
    gap = energy_demand(p) - supply
    # a starter deck already "wants" ~5 (gap 2): energy is worth more the further a deck is above that
    v = max(-0.3, min(0.6, 0.25 * (gap - 1.5)))
    if p["energy_relics"] >= 2:
        v -= 0.3
    why = (f"energy: deck wants ~{energy_demand(p):.1f}/turn vs {supply} available" if gap > 0.5
           else f"cheap deck (avg cost {p['avg_cost']:.1f}): extra energy matters less")
    return v, why


def boss_fit(name: str, p: dict) -> tuple[float, list[str]]:
    v, why = 0.0, []

    def add(x: float, reason: str) -> None:
        nonlocal v
        v += x
        if reason:
            why.append(reason)

    if name in ENERGY_RELICS:
        ev, ew = energy_value(p)
        add(ev * (0.5 if name == "Slaver's Collar" else 1.0), ew)
    if name == "Runic Pyramid":
        # keeps the whole hand: great when the deck can spend or exhaust it, a trap when it clogs
        add(0.25 * min(3, p["exhaust_outlets"]), f"{p['exhaust_outlets']} exhaust outlets" if p["exhaust_outlets"] else "")
        add(0.2 * min(2, p["energy_gen"]), f"{p['energy_gen']} energy sources to play kept cards" if p["energy_gen"] else "")
        clog = p["status_makers"] + p["curses"] + max(0, p["high_cost"] - 4) + max(0, p["draw"] - 3)
        add(-0.25 * min(4, clog), f"clog risk {clog} (statuses/curses/expensive cards/draw)" if clog else "")
        if not p["exhaust_outlets"] and not p["energy_gen"]:
            add(-0.4, "nothing to empty a full hand (run 199 stalled with 10 kept cards)")
    elif name == "Snecko Eye":
        add(0.5 if p["avg_cost"] >= 1.35 else -0.3, f"avg cost {p['avg_cost']:.1f}")
        add(0.1 * min(4, p["high_cost"]) - 0.15 * min(4, p["zero_cost"]) - 0.4 * min(1, p["xcost"]),
            f"{p['high_cost']} cards cost 2+, {p['zero_cost']} cost 0")
    elif name == "Velvet Choker":
        many = p["zero_cost"] + p["draw"]
        add(-0.15 * min(5, many), f"{many} cheap/draw cards hit the 6-card limit" if many >= 3 else "")
    elif name == "Busted Crown":
        add(0.3 if p["n"] >= 25 else -0.5, "deck already built" if p["n"] >= 25 else "deck still needs card rewards")
        add(-0.3 if p["act"] <= 1 else 0.0, "")
    elif name == "Mark of Pain":
        add(0.2 * min(3, p["exhaust_outlets"] + p["exhaust_pay"]), "exhaust turns Wounds into value" if p["exhaust_outlets"] else "")
        add(-0.2 if p["draw"] <= 2 else 0.0, "Wounds clog a low-draw deck" if p["draw"] <= 2 else "")
    elif name == "Ectoplasm":
        add(-0.4 if p["act"] <= 1 else -0.15, "no more gold: fewer shop removals and relics")
        add(0.15 if p["gold"] >= 250 else 0.0, f"{p['gold']} gold to spend first" if p["gold"] >= 250 else "")
    elif name == "Sozu":
        add(-0.2 - 0.1 * p["potions"], "no more potions (boss fights rely on them)")
    elif name == "Fusion Hammer":
        add(-0.12 * min(6, p["unupgraded"]), f"{p['unupgraded']} cards still unupgraded")
    elif name == "Philosopher's Stone":
        add(0.3 if p["weak_tools"] >= 2 else -0.2, f"{p['weak_tools']} Weak/Strength-down cards")
    elif name == "Coffee Dripper":
        add(-0.6 if p["healing"] else -2.0, "no resting" + ("" if p["healing"] else " and no real healing"))
    elif name == "Runic Dome":
        add(-1.0, "hides enemy intents (the planner needs them)")
    elif name == "Cursed Key":
        add(0.1 if p["exhaust_outlets"] else -0.1, "curses from chests" + (" can be exhausted" if p["exhaust_outlets"] else ""))
    elif name == "Slaver's Collar":
        add(-0.2, "energy only in elite and boss fights")
    elif name in ("Astrolabe", "Pandora's Box"):
        add(0.08 * (p["basics"] - 5), f"{p['basics']} Strikes/Defends to transform")
    elif name == "Empty Cage":
        add(0.06 * (p["basics"] + 2 * p["curses"] - 4) + (0.2 if p["n"] >= 25 else 0.0),
            f"removes 2 of {p['basics']} starters/{p['curses']} curses")
    elif name == "Runic Cube":
        add(0.15 * min(4, p["selfdmg"]) - 0.1, f"{p['selfdmg']} self-damage cards")
    elif name == "Black Blood":
        add(0.2 if p["hp_frac"] < 0.6 else 0.05, "heals 12 after each fight")
    elif name == "Black Star":
        add(0.3 if p["hp_frac"] >= 0.7 and p["act"] <= 2 else -0.2, "elites drop 2 relics (needs HP for elites)")
    elif name == "Sacred Bark":
        add(0.1 * p["slots"] - 0.2, f"{p['slots']} potion slots")
    elif name == "Calling Bell":
        add(0.3 if p["exhaust_outlets"] or "Omamori" in p["relics"] else -0.2, "3 relics + a curse")
    elif name == "Tiny House":
        add(-0.2, "small bundle of rewards")
    if name == "Coffee Dripper" and not p["healing"]:
        v = min(v, -2.0)   # energy never makes up for arriving at the next boss at ~25% HP
    return round(v, 2), why


def buckets(p: dict) -> list[str]:
    out = []
    if p["avg_cost"] >= 1.4:
        out.append("cost_hi")
    elif p["avg_cost"] <= 1.0:
        out.append("cost_lo")
    if p["draw"] >= 4:
        out.append("draw_hi")
    if p["exhaust_outlets"] >= 2:
        out.append("exhaust")
    if p["energy_gen"] >= 2:
        out.append("energy_gen")
    if p["status_makers"] + p["curses"] >= 2:
        out.append("clog")
    if p["hp_frac"] < 0.5:
        out.append("hp_lo")
    if p["gold"] >= 250:
        out.append("gold_hi")
    return out


def describe(p: dict) -> str:
    return (f"Relic-relevant deck numbers: avg cost {p['avg_cost']:.1f} ({p['high_cost']} cards cost 2+, "
            f"{p['zero_cost']} cost 0, {p['xcost']} X-cost); energy wanted per turn ~{energy_demand(p):.1f} vs "
            f"{3 + p['energy_relics']} available; draw cards {p['draw']}; exhaust outlets {p['exhaust_outlets']}; "
            f"status/curse makers {p['status_makers'] + p['curses']}; self-damage cards {p['selfdmg']}; "
            f"unupgraded cards {p['unupgraded']}; real healing {'yes' if p['healing'] else 'no'}; "
            f"HP {p['hp_frac']:.0%}; gold {p['gold']}; potions {p['potions']}/{p['slots']}.")
