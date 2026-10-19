"""Planner and playbook-rule scenarios on hand-built combat states (no game needed).

Run from C:\\sts-evolve:  sts-bench\\.venv\\Scripts\\python.exe tests\\test_planner.py
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
FIX = ROOT / "sts-bench" / "tests" / "fixtures" / "states"

from sts_bench.actions.schema import EndTurn, PlayCard, UsePotion  # noqa: E402
from sts_bench.state.schema import parse_message  # noqa: E402

import cards as C  # noqa: E402
import planner  # noqa: E402
import policy  # noqa: E402
from model import COMBAT_DEFAULTS, Model  # noqa: E402

P = dict(COMBAT_DEFAULTS)
CARDS = {
    "Strike": ("Strike_R", "ATTACK", 1, True), "Defend": ("Defend_R", "SKILL", 1, False),
    "Bash": ("Bash", "ATTACK", 2, True), "Twin Strike": ("Twin Strike", "ATTACK", 1, True),
    "Flex": ("Flex", "SKILL", 0, False), "Pommel Strike": ("Pommel Strike", "ATTACK", 1, True),
    "Inflame": ("Inflame", "POWER", 1, False), "Combust": ("Combust", "POWER", 1, False),
    "Shrug It Off": ("Shrug It Off", "SKILL", 1, False), "Cleave": ("Cleave", "ATTACK", 1, False),
}


def card(name: str, i: int) -> dict:
    cid, ctype, cost, target = CARDS[name]
    return {"exhausts": False, "is_playable": True, "cost": cost, "name": name, "id": cid, "type": ctype,
            "ethereal": False, "uuid": f"u{i}-{name}", "upgrades": 0, "rarity": "COMMON", "has_target": target}


def monster(name: str, hp: int, dmg: int, hits: int = 1, block: int = 0, powers=()) -> dict:
    return {"name": name, "id": name.replace(" ", ""), "current_hp": hp, "max_hp": hp, "block": block,
            "intent": "ATTACK" if dmg else "BUFF", "half_dead": False, "is_gone": False, "move_id": 1,
            "move_base_damage": dmg, "move_adjusted_damage": dmg, "move_hits": hits,
            "powers": [{"id": p, "name": p, "amount": a} for p, a in powers]}


def state(hand: list[str], monsters: list[dict], hp: int = 70, energy: int = 3, potions=(), room="MonsterRoom"):
    s = copy.deepcopy(json.loads((FIX / "none-1.json").read_text()))
    g = s["game_state"]
    g.update({"class": "IRONCLAD", "room_type": room, "current_hp": hp, "max_hp": 80, "floor": 20, "act": 2})
    c = g["combat_state"]
    c["hand"] = [card(n, i) for i, n in enumerate(hand)]
    c["monsters"] = monsters
    c["turn"] = 2
    c["player"].update(current_hp=hp, max_hp=80, energy=energy, block=0, orbs=[], powers=[])
    g["relics"] = [{"id": "Burning Blood", "name": "Burning Blood", "counter": -1}]
    g["deck"] = [card(n, i) for i, n in enumerate(["Strike"] * 5 + ["Defend"] * 4 + ["Bash"])]
    pots = [{"id": n, "name": n, "can_use": True, "can_discard": True, "requires_target": False} for n in potions]
    empty = {"id": "Potion Slot", "name": "Potion Slot", "can_use": False, "can_discard": False,
             "requires_target": False}
    g["potions"] = (pots + [empty] * 3)[:3]
    return parse_message(s)


def first(msg) -> object:
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P),
                                  llm_events=False)
    agent = policy.PolicyAgent()
    action, note = agent._combat(msg, None)
    return action, note


def names(plan, msg) -> list[str]:
    return [msg.game_state.combat_state.hand[i].name for i, _ in plan.steps]


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    # parser: triggers are not immediate effects
    flex = C.parse_text("Gain 2 Strength. At the end of this turn, lose 2 Strength.", "SKILL", 0)
    check("Flex is temporary Strength", flex.temp_strength == 2 and flex.strength == 0)
    combust = C.parse_text("At the end of your turn, lose 1 HP and deal 5 damage to ALL enemies.", "POWER", 1)
    check("Combust deals no damage on play", combust.damage == 0 and combust.per_turn_damage == 5)
    fb = C.parse_text("Gain 12 Block. Whenever you are attacked this turn, deal 4 damage back.", "SKILL", 2)
    check("Flame Barrier thorns are not attack damage", fb.damage == 0 and fb.thorns == 4)

    # Spheric Guardian: 40 block + Barricade -- the old policy never attacked into it
    msg = state(["Strike", "Strike", "Strike", "Defend", "Defend"],
                [monster("Spheric Guardian", 20, 0, block=40, powers=[("Barricade", -1), ("Artifact", 3)])])
    plan = planner.plan_turn(msg, P, [])
    check("attacks through Barricade block", names(plan, msg).count("Strike") == 3, plan.describe(
        msg.game_state.combat_state.hand, msg.game_state.combat_state.monsters))

    # Gremlin Nob: Skills enrage it -- prefer attacks when the hit is survivable
    msg = state(["Strike", "Strike", "Defend", "Defend", "Strike"],
                [monster("Gremlin Nob", 80, 8, powers=[("Anger", 2)])])
    plan = planner.plan_turn(msg, P, [])
    check("no Skills into Enrage", "Defend" not in names(plan, msg), ", ".join(names(plan, msg)))

    # Spiker thorns: hit the Exploder, not the Spiker
    msg = state(["Strike", "Strike"],
                [monster("Spiker", 40, 0, powers=[("Thorns", 4)]), monster("Exploder", 12, 9)])
    plan = planner.plan_turn(msg, P, [])
    check("avoids the thorns target", all(t == 1 for _, t in plan.steps), str(plan.steps))

    # lethal incoming: block first
    msg = state(["Strike", "Defend", "Defend"], [monster("Jaw Worm", 40, 12)], hp=8)
    plan = planner.plan_turn(msg, P, [])
    check("blocks a lethal hit", names(plan, msg).count("Defend") == 2, ", ".join(names(plan, msg)))

    # Vulnerable before the big hits; Strength before multi-hits
    msg = state(["Strike", "Bash"], [monster("Jaw Worm", 60, 0)], energy=3)
    plan = planner.plan_turn(msg, P, [])
    check("Bash before Strike", names(plan, msg) == ["Bash", "Strike"], ", ".join(names(plan, msg)))
    msg = state(["Twin Strike", "Flex"], [monster("Jaw Worm", 60, 0)], energy=1)
    plan = planner.plan_turn(msg, P, [])
    check("Flex before Twin Strike", names(plan, msg) == ["Flex", "Twin Strike"], ", ".join(names(plan, msg)))

    # Curiosity: hold powers in Awakened One phase 1
    msg = state(["Inflame", "Strike", "Defend"],
                [monster("Awakened One", 300, 10, powers=[("Curiosity", 2)])], energy=2)
    plan = planner.plan_turn(msg, P, [])
    check("holds powers vs Curiosity", "Inflame" not in names(plan, msg), ", ".join(names(plan, msg)))

    # kill priority: Cultists first in the Awakened One fight
    msg = state(["Strike"], [monster("Awakened One", 300, 10), monster("Cultist", 6, 6)], energy=1)
    plan = planner.plan_turn(msg, P, [])
    check("kills the Cultist first", plan.steps and plan.steps[0][1] == 1, str(plan.steps))

    # Time Eater: the 12th card ends the turn and gives it Strength
    msg = state(["Strike", "Strike"], [monster("Time Eater", 400, 0, powers=[("Time Warp", 10)])], energy=2)
    plan = planner.plan_turn(msg, P, [])
    check("does not hit the 12-card limit for chip damage", len(plan.steps) <= 1, str(plan.steps))

    # potions: never die holding one
    msg = state(["Strike"], [monster("Jaw Worm", 40, 30)], hp=10, potions=["Block Potion"])
    action, note = first(msg)
    check("drinks a potion against lethal damage", isinstance(action, UsePotion), note)
    msg = state(["Strike"], [monster("Jaw Worm", 40, 5)], hp=70, potions=["Strength Potion"])
    action, note = first(msg)
    check("keeps potions in a normal hallway fight", not isinstance(action, UsePotion), note)
    msg = state(["Strike"], [monster("Hexaghost", 250, 5)], hp=70, potions=["Strength Potion"],
                room="MonsterRoomBoss")
    action, note = first(msg)
    check("uses buff potions early in boss fights", isinstance(action, UsePotion), note)

    # playbook: per-topic sections keep the learned lessons
    book = (ROOT / "agent" / "strategy_baseline.md").read_text(encoding="utf-8").replace(
        "(The coach adds bullets here as it analyzes runs.)", "- Rest before the boss (run 9)")
    sel = policy.playbook_for(book, "boss_relic", "IRONCLAD")
    check("boss relic prompt: right sections", "Snecko Eye" in sel and "## Ironclad" in sel
          and "Rest before the boss" in sel and "## Act 3" not in sel)
    sel = policy.playbook_for(book, "neow", "IRONCLAD")
    check("Neow prompt has the Neow section", "three combats" in sel or "three enemies" in sel or "1 HP" in sel)

    # Neow ranking
    g = msg.game_state
    sc = {t: policy.PolicyAgent._event_score(t, g, True) for t in
          ("Enemies in your next three combats have 1 HP", "Upgrade a Card", "Obtain a random common Relic")}
    check("Neow: 1-HP blessing > common relic > upgrade",
          sc["Enemies in your next three combats have 1 HP"] > sc["Obtain a random common Relic"] > sc["Upgrade a Card"],
          str(sc))
    # Match and Keep!: finish a known pair instead of flipping the same two cards forever
    from types import SimpleNamespace as NS
    agent = policy.PolicyAgent()
    g = NS(floor=30)
    opts = lambda labels: [NS(choice_index=i, label=l, text=l, disabled=False) for i, l in enumerate(labels)]
    agent._match_and_keep(g, opts(["Bash", "card1", "Strike", "card3", "Bash", "card5"]))   # first flip
    a, note = agent._match_and_keep(g, opts(["Bash", "card1", "Strike", "card3", "Bash", "card5"]))
    check("Match and Keep pairs known cards", a.choice_index in (0, 4), note)
    # per-act credit: an act-2 choice in a run that died early in act 2 is judged against act-2 runs
    m = Model(Path(tempfile.mkdtemp()) / "m.json")
    ev = lambda a, key: {"options": [{key: 1.0}, {"other": 1.0}], "chosen": 0, "probs": [0.5, 0.5], "temp": 1.0, "act": a}
    for _ in range(20):
        m.learn([ev(1, "a1"), ev(2, "a2good")], Model.outcome(40, False))   # deep runs
        m.learn([ev(1, "a1"), ev(2, "a2bad")], Model.outcome(19, False))    # died early in act 2
    check("per-act credit separates act-2 choices", m.w("a2good") > m.w("a2bad"),
          f"good {m.w('a2good'):+.2f} bad {m.w('a2bad'):+.2f}")
    # antithetic ES moves toward the better mirror
    m = Model(Path(tempfile.mkdtemp()) / "m.json")
    before = m.params["block"]
    m.es_update_pair({"block": 0.1}, f_plus=0.8, f_minus=0.5)
    check("antithetic ES follows the better half", m.params["block"] > before)
    print("PLANNER OK")


if __name__ == "__main__":
    main()
