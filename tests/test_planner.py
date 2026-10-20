"""Planner and playbook-rule scenarios on hand-built combat states (no game needed).

Run from C:\\sts-evolve:  sts-bench\\.venv\\Scripts\\python.exe tests\\test_planner.py
"""

from __future__ import annotations

import collections
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
    check("Neow (guide): common relic > upgrade > 1-HP blessing",
          sc["Obtain a random common Relic"] > sc["Upgrade a Card"] > sc["Enemies in your next three combats have 1 HP"],
          str(sc))
    # Match and Keep!: finish a known pair instead of flipping the same two cards forever
    from types import SimpleNamespace as NS
    agent = policy.PolicyAgent()
    g = NS(floor=30)
    opts = lambda labels: [NS(choice_index=i, label=l, text=l, disabled=False) for i, l in enumerate(labels)]
    agent._match_and_keep(g, opts(["Bash", "card1", "Strike", "card3", "Bash", "card5"]))   # first flip
    a, note = agent._match_and_keep(g, opts(["Bash", "card1", "Strike", "card3", "Bash", "card5"]))
    check("Match and Keep pairs known cards", a.choice_index in (0, 4), note)
    # shop: with a starter deck, removing a Strike beats buying another card (policy only, near-greedy)
    shop = parse_message(json.loads((FIX / "shop_screen-1.json").read_text()))
    picks = collections.Counter()
    for _ in range(30):
        policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P),
                                      llm_events=False, temperature=0.3)
        action, note = policy.PolicyAgent()._shop(shop, None)
        picks[note.rsplit("-> ", 1)[-1]] += 1
    check("shop prefers removing a Strike over cards", picks.most_common(1)[0][0] == "buy remove a card", str(picks))

    # smith: a big upgrade (Limit Break loses Exhaust) beats Strike; one LLM call with upgrade text
    grid = json.loads((FIX / "grid-1.json").read_text())
    gs = grid["game_state"]
    gs["class"], gs["act"] = "IRONCLAD", 2
    gs["screen_state"].update({"for_upgrade": True, "for_purge": False, "for_transform": False,
                               "num_cards": 1, "any_number": False, "confirm_up": False, "selected_cards": [],
                               "cards": [card(n, i) for i, n in enumerate(["Strike", "Defend", "Bash", "Pommel Strike"])]})
    gs["screen_state"]["cards"][3].update({"name": "Limit Break", "id": "Limit Break", "type": "SKILL", "has_target": False})
    gs["deck"] = gs["screen_state"]["cards"]
    up = parse_message(grid)
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=False)
    action, note = policy.PolicyAgent()._grid(up, None)
    check("smith upgrades Limit Break first (act 2)", "Limit Break" in note, note)
    seen = {}
    policy.ask_llm = lambda *a, **k: (seen.setdefault("user", a[3]), "1")[1]
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=True)
    action, note = policy.PolicyAgent()._grid(up, None)
    check("LLM sees upgraded text and decides", "upgraded:" in seen.get("user", "") and "(llm)" in note, note)

    # events: Knowing Skull asks once then leaves; never pay (nearly) all remaining HP
    ev = json.loads((FIX / "event-1.json").read_text())
    eg = ev["game_state"]
    eg.update({"class": "IRONCLAD", "current_hp": 70, "max_hp": 80, "floor": 27, "act": 2})
    opts = ["[Riches?] Gain 90 Gold. Lose 8 HP.", "[Success?] Obtain a potion. Lose 6 HP.",
            "[How do I leave?] Lose 6 HP."]
    eg["screen_state"].update(event_name="Knowing Skull", event_id="Knowing Skull", body_text="",
                              options=[{"choice_index": i, "text": t, "label": t.split("]")[0][1:],
                                        "disabled": False} for i, t in enumerate(opts)])
    eg["choice_list"] = ["riches?", "success?", "how do i leave?"]
    skull = parse_message(ev)
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=False)
    agent = policy.PolicyAgent()
    agent._event(skull, None)
    a, note = agent._event(skull, None)
    check("Knowing Skull: second visit leaves", a.choice_index == 2, note)
    eg["current_hp"] = 20
    eg["screen_state"].update(event_name="Forgotten Altar", event_id="Forgotten Altar",
                              options=[{"choice_index": 0, "text": "Gain 5 Max HP. Lose 25 HP.", "label": "Sacrifice",
                                        "disabled": False},
                                       {"choice_index": 1, "text": "Lose 5 HP.", "label": "Desecrate",
                                        "disabled": False}])
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P),
                                  llm_events=False, temperature=0.05)
    a, note = policy.PolicyAgent()._event(parse_message(ev), None)
    check("event HP guard: no lethal sacrifice", a.choice_index == 1, note)

    # shop: the LLM list cannot skip removal while Strikes remain, nor buy a second card
    policy.ask_llm = lambda *a, **k: "1, 2, 3"
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=True)
    a, note = policy.PolicyAgent()._shop(shop, None)
    check("shop: removal before the LLM's card list", note.endswith("buy remove a card"), note)

    # removal target follows the deck: Strike when attacks outnumber block cards, Defend otherwise
    for extra, want in ((["Twin Strike", "Pommel Strike", "Cleave"], "Strike"),
                        (["Shrug It Off", "Shrug It Off", "Shrug It Off"], "Defend")):
        rg = json.loads((FIX / "grid-1.json").read_text())
        d = [card(n, i) for i, n in enumerate(["Strike"] * 5 + ["Defend"] * 4 + ["Bash"] + extra)]
        rg["game_state"].update({"class": "IRONCLAD", "act": 2, "deck": d})
        rg["game_state"]["screen_state"].update(for_purge=True, for_upgrade=False, for_transform=False, num_cards=1,
                                                any_number=False, confirm_up=False, selected_cards=[], cards=d)
        policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=False)
        a, note = policy.PolicyAgent()._grid(parse_message(rg), None)
        check(f"removal picks {want} for this deck", note == f"remove {want}", note)
    from types import SimpleNamespace as NS
    pull = policy.PolicyAgent._shop_pull
    check("map: shops pull at 170+ gold or a curse, push when poor (guide)",
          pull(NS(gold=40, deck=[])) < pull(NS(gold=120, deck=[])) < 0 < pull(NS(gold=200, deck=[])) < pull(NS(gold=300, deck=[]))
          and pull(NS(gold=120, deck=[NS(type=NS(value="CURSE"))])) > pull(NS(gold=120, deck=[])))

    # boss-aware picks and less dilution late; Champ: don't cross half without a deep burst
    gq = NS(act_boss="The Champ", floor=28, act=2, character="IRONCLAD", deck=[NS()] * 30)
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=False)
    ag = policy.PolicyAgent()
    infl = parse_message(state(["Inflame"], [monster("Jaw Worm", 40, 0)])).game_state.combat_state.hand[0]
    strike = parse_message(state(["Strike"], [monster("Jaw Worm", 40, 0)])).game_state.combat_state.hand[0]
    check("before the Champ, scaling cards get a boost", ag._card_bonus(infl, gq) > ag._card_bonus(strike, gq))
    check("big act-2 deck leans toward skipping", ag._skip_bonus(gq) > 0)
    # Byrds: multi-hits knock a flying Byrd down (its attack never lands); single hits do half damage
    msg = state(["Twin Strike", "Strike"], [monster("Byrd", 30, 12, powers=[("Flight", 2)])], energy=1)
    plan = planner.plan_turn(msg, P, [])
    check("Byrd: multi-hit grounds it", names(plan, msg) == ["Twin Strike"], plan.describe(
        msg.game_state.combat_state.hand, msg.game_state.combat_state.monsters))
    # Hex: non-Attack cards cost a Dazed each
    from sts_bench.state.schema import Power
    msg = state(["Shrug It Off"], [monster("Chosen", 95, 0)], energy=1)
    no_hex = planner.plan_turn(msg, P, [])
    msg.game_state.combat_state.player.powers.append(Power(id="Hex", name="Hex", amount=1))
    hexed = planner.plan_turn(msg, P, [])
    check("Hex: Skills cost a Dazed", (hexed.value - hexed.base) < (no_hex.value - no_hex.base),
          f"{no_hex.value - no_hex.base:.1f} -> {hexed.value - hexed.base:.1f}")

    # 1: track record compares takers with passers of the same offer in the same act
    import evidence
    runs = [{"run": i, "floor": fl, "victory": False,
             "picks": [{"floor": 3, "offered": ["Clothesline", "Strike"], "chosen": ch}]}
            for i, (fl, ch) in enumerate([(40, "Clothesline")] * 3 + [(20, "Strike")] * 3)]
    e = evidence.build(runs)
    check("track record: taken vs passed", evidence.card(e, "Clothesline", 1)
          == "[history act 1: taken 3x -> avg floor 40 | passed 3x -> avg floor 20]", evidence.card(e, "Clothesline", 1))
    check("track record hides thin samples", evidence.card(e, "Bash", 1) == "")

    # 3: value estimate learns which states lead deeper; 2: replay leaves baselines alone
    m = Model(Path(tempfile.mkdtemp()) / "m.json")
    for k in range(40):
        hp = 0.9 if k % 2 else 0.3
        m.learn([{"options": [{"x": 1.0}, {"y": 1.0}], "chosen": 0, "probs": [0.5, 0.5], "temp": 1.0, "act": 1,
                  "state": {"hp": hp}}] * 30, 1.5 if hp > 0.5 else 0.4)  # ~30 decisions per real run
    check("value estimate: healthy states predict deeper runs", m.value({"hp": 0.9}) > m.value({"hp": 0.3}) + 0.5,
          f"{m.value({'hp': 0.9}):.2f} vs {m.value({'hp': 0.3}):.2f}")
    before = (json.dumps(m.meta["act_baseline"]), m.meta["runs_trained"])
    m.learn([{"options": [{"x": 1.0}, {"y": 1.0}], "chosen": 1, "probs": [0.5, 0.5], "temp": 1.0, "act": 1}], 0.1, replay=True)
    check("replay updates weights only", before == (json.dumps(m.meta["act_baseline"]), m.meta["runs_trained"]))

    # 4: card features carry context (act, archetype lean, big deck)
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), llm_events=False)
    policy.CONFIG.tracker.plan = {"lean": "Strength"}
    hb = parse_message(state(["Strike"], [monster("Jaw Worm", 40, 0)]))
    feats = policy.PolicyAgent()._card_features(hb.game_state.combat_state.hand[0], hb.game_state, {"Strength": 0.5})
    check("context card features", "cardctx:Strike:a2" in feats and "cardctx:Strike|Strength" in feats, str(sorted(feats)))

    # a failed LLM call trips a cooldown (no stacked timeouts) and calls the health check
    pings = []
    policy.ON_LLM_FAILURE = lambda: pings.append(1)
    real_ask = policy.ask_llm
    policy.ask_llm = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("hung"))
    policy._LLM_DOWN_UNTIL[0] = 0.0
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P))
    ag = policy.PolicyAgent()
    call1, call2 = ag._call_llm("s", "u"), ag._call_llm("s", "u")
    check("LLM failure: cooldown + health check", call1[0] is None and call2 == (None, 0) and pings == [1])
    policy.ask_llm, policy.ON_LLM_FAILURE, policy._LLM_DOWN_UNTIL[0] = real_ask, None, 0.0

    # HP budget before the act 2 boss: low HP avoids fights/elites and favors rest/events
    real_deck = state(["Strike"], [monster("Jaw Worm", 40, 0)]).game_state.deck
    gm = NS(act=2, floor=28, current_hp=30, max_hp=80, gold=50, relics=[], deck=real_deck)
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P))
    rules = policy.PolicyAgent()._map_rules(gm)
    check("pre-boss HP budget: avoid fights, favor rest", rules.get("M", 0) < 0 and rules.get("E", 0) < -1 and rules.get("R", 0) > 0,
          str(rules))

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
