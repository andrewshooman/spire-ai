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
    "Corruption": ("Corruption", "POWER", 3, False), "Second Wind": ("Second Wind", "SKILL", 1, False),
    "Wild Strike": ("Wild Strike", "ATTACK", 1, True), "Power Through": ("Power Through", "SKILL", 1, False),
    "Bludgeon": ("Bludgeon", "ATTACK", 3, True), "Heavy Blade": ("Heavy Blade", "ATTACK", 2, True),
    "Blade Dance": ("Blade Dance", "SKILL", 1, False), "Survivor": ("Survivor", "SKILL", 1, False),
    "Catalyst": ("Catalyst", "SKILL", 1, True), "Bouncing Flask": ("Bouncing Flask", "SKILL", 2, False),
    "Deadly Poison": ("Deadly Poison", "SKILL", 1, True),
    "Wraith Form": ("Wraith Form", "POWER", 3, False), "Double Tap": ("Double Tap", "SKILL", 1, False),
    "Reaper": ("Reaper", "ATTACK", 2, False), "Footwork": ("Footwork", "POWER", 1, False),
    "Defend_G": ("Defend_G", "SKILL", 1, False),
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


def state(hand: list[str], monsters: list[dict], hp: int = 70, energy: int = 3, potions=(), room="MonsterRoom", turn=2):
    s = copy.deepcopy(json.loads((FIX / "none-1.json").read_text()))
    g = s["game_state"]
    g.update({"class": "IRONCLAD", "room_type": room, "current_hp": hp, "max_hp": 80, "floor": 20, "act": 2})
    c = g["combat_state"]
    c["hand"] = [card(n, i) for i, n in enumerate(hand)]
    c["monsters"] = monsters
    c["turn"] = turn
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
    # run 199: a lone Spiker with big Thorns -- the bot ended 60 turns in a row instead of killing it
    msg = state(["Strike", "Defend"], [monster("Spiker", 6, 7, powers=[("Thorns", 15)])])
    plan = planner.plan_turn(msg, P, [])
    check("finishes a lone Thorns enemy", "Strike" in names(plan, msg), plan.describe(
        msg.game_state.combat_state.hand, msg.game_state.combat_state.monsters))
    # run 211: a 54 HP Spiker whose Thorns keep growing -- no safe target, so hit it (big hits first)
    msg = state(["Heavy Blade", "Defend", "Twin Strike"], [monster("Spiker", 54, 5, powers=[("Thorns", 9)])], hp=40)
    plan = planner.plan_turn(msg, P, [])
    check("attacks a lone high-HP Thorns enemy", "Heavy Blade" in names(plan, msg), plan.describe(
        msg.game_state.combat_state.hand, msg.game_state.combat_state.monsters))
    msg = state(["Strike"], [monster("Spiker", 30, 7, powers=[("Thorns", 9)])])
    p0, p3 = planner.plan_turn(msg, P, []), planner.plan_turn(msg, P, [], stall=3)
    check("a stalled fight discounts Thorns", p3.value - p3.base > p0.value - p0.base)

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
    # act 2: potions are for the boss -- no elite opener at good HP with a free slot; with a full belt, yes
    msg = state(["Strike"], [monster("Gremlin Nob", 90, 14)], hp=70, potions=["Strength Potion", "Fire Potion"],
                room="MonsterRoomElite", turn=1)
    action, note = first(msg)
    check("act 2 elite at good HP: keeps potions for the boss", not isinstance(action, UsePotion), note)
    msg = state(["Strike"], [monster("Gremlin Nob", 90, 14)], hp=70,
                potions=["Strength Potion", "Fire Potion", "Swift Potion"], room="MonsterRoomElite", turn=1)
    action, note = first(msg)
    check("act 2 elite with a full belt: opener (a new drop would be wasted)", isinstance(action, UsePotion), note)

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
    check("before the Champ, scaling cards score higher on the boss-need feature",
          ag._boss_need(infl, gq) > ag._boss_need(strike, gq) and ag._boss_need(infl, gq) > 0)
    import human
    gq2 = NS(act_boss="The Champ", floor=28, act=2, character="IRONCLAD", relics=[], gold=99, current_hp=60,
             max_hp=80, deck=[human.card_from_id(c) for c in ["Strike_R"] * 4 + ["Defend_R"] * 4 + ["Bash"]])
    fi = ag._card_features(human.card_from_id("Inflame"), gq2, {})
    mm = Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD")
    check("act 2 picks prepare for its boss: Champ x scaling feature, data-based start (block no longer)",
          fi.get("bossneed:The Champ:scaling") == 1.0 and mm.w("bossneed:The Champ:scaling") == 0.8
          and mm.w("bossneed:The Champ:block") == 0.0, str({k: v for k, v in fi.items() if k.startswith("boss")}))
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
          == "[history act 1: taken 3x -> act 2 boss 100% | passed 3x -> act 2 boss 0%]", evidence.card(e, "Clothesline", 1))
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
    early = NS(act=1, floor=4, current_hp=75, max_hp=80, gold=99, relics=[], deck=real_deck)
    plain = policy.PolicyAgent()._map_rules(early)
    policy.CONFIG.ab = {"event_pull": True}
    pulled = policy.PolicyAgent()._map_rules(early)
    policy.CONFIG.ab = {}
    check("event_pull A/B favors ? rooms over fights", pulled.get("?", 0) > plain.get("?", 0)
          and pulled.get("M", 0) < plain.get("M", 0), f"{plain} -> {pulled}")

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

    # --- act 2 boss fixes (runs 1-199: entry HP, potions, Orbs, Coffee Dripper) and lean decks
    # Bronze Orbs die with the Automaton: hit the boss
    msg = state(["Strike", "Strike", "Strike"], [monster("Bronze Orb", 54, 8), monster("Bronze Automaton", 300, 7, 2)],
                room="MonsterRoomBoss")
    plan = planner.plan_turn(msg, P, [])
    check("Automaton over its Orbs", all(t == 1 for _, t in plan.steps), str(plan.steps))
    # boss potions: no Flex on a turn without attacks, keep the last potion, defense on the big hit
    msg = state(["Defend", "Defend"], [monster("The Champ", 420, 0)], potions=["Flex Potion", "Fire Potion"],
                room="MonsterRoomBoss")
    action, note = first(msg)
    check("no Flex Potion on a turn without attacks", "Flex" not in note, note)
    msg = state(["Strike"], [monster("The Champ", 420, 5)], potions=["Fire Potion"], room="MonsterRoomBoss")
    action, note = first(msg)
    check("keeps its only potion for a lethal turn", not isinstance(action, UsePotion), note)
    msg = state(["Strike"], [monster("The Champ", 200, 27, 2)], hp=60, potions=["Block Potion", "Fire Potion"],
                room="MonsterRoomBoss")
    action, note = first(msg)
    check("Block Potion on the Execute turn", isinstance(action, UsePotion) and "Block Potion" in note, note)
    # The Champ: burst potions wait for phase 2; permanent power is fine on turn 1
    def champ(hp, dmg=0, hits=1):
        m = monster("The Champ", hp, dmg, hits)
        m["max_hp"] = 420
        return m
    msg = state(["Defend"], [champ(420)], potions=["Fire Potion", "Strength Potion"], room="MonsterRoomBoss")
    action, note = first(msg)
    check("Champ opening: Strength Potion, Fire Potion saved", "Strength Potion" in note and "Fire" not in note, note)
    msg = state(["Strike"], [champ(200)], potions=["Fire Potion"], room="MonsterRoomBoss")
    action, note = first(msg)
    check("Champ phase 2: burst potions now", isinstance(action, UsePotion) and "phase 2" in note, note)
    # crossing half HP triggers Anger + Execute: with low HP don't chip it across; with HP to spare, fine
    low = state(["Heavy Blade", "Defend"], [champ(222)], hp=40, energy=2, room="MonsterRoomBoss")
    high = state(["Heavy Blade", "Defend"], [champ(222)], hp=78, energy=2, room="MonsterRoomBoss")
    pl, ph = planner.plan_turn(low, P, []), planner.plan_turn(high, P, [])
    check("Champ: won't push it into phase 2 when two Executes would kill",
          "Heavy Blade" not in names(pl, low) and "Heavy Blade" in names(ph, high),
          f"low HP: {names(pl, low)} | high HP: {names(ph, high)}")
    check("Champ: after a waiting turn it crosses anyway (never stalls)",
          "Heavy Blade" in names(planner.plan_turn(low, P, [], stall=1), low))
    # Coffee Dripper: Burning Blood alone is not healing enough
    g = state(["Strike"], [monster("Jaw Worm", 40, 0)]).game_state
    check("Coffee Dripper ruled out without real healing", policy.boss_relic_rule("Coffee Dripper", g)[0] <= -2.0)
    # big decks: only engines / synergy / boss answers earn a slot
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P))
    agent = policy.PolicyAgent()
    hand = state(["Inflame", "Cleave"], [monster("Jaw Worm", 40, 0)]).game_state.combat_state.hand
    g.act_boss = "Hexaghost"
    check("engine card earns a slot", agent._earns_slot(hand[0], g, {})[0])
    big = NS(act=2, deck=[None] * 30)
    check("skip favored at 30 cards", agent._skip_bonus(big) >= 2.0 and agent._skip_bonus(NS(act=1, deck=[None] * 15)) == 0)
    m = Model(Path(tempfile.mkdtemp()) / "m.json")
    for _ in range(300):
        m.learn([{"options": [{"card:X": 1.0}, {"skip:a2": 1.0}], "chosen": 0, "probs": [0.5, 0.5], "temp": 1.0,
                  "act": 2}], Model.outcome(45, False))
    check("skip weight stays bounded", m.w("skip:a2") >= -1.5, f"{m.w('skip:a2'):+.2f}")
    # act 2 rest sites: rest under 60% HP; before the boss rest under 85%
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), temperature=0.05)

    def rest_choice(floor, hp):
        gm = NS(screen=NS(has_rested=False), choice_list=["rest", "smith"], current_hp=hp, max_hp=80, floor=floor,
                act=2, act_boss="The Champ")
        return policy.PolicyAgent()._rest(NS(game_state=gm), None)[0].choice_index

    check("act 2: rests at 45% HP", rest_choice(25, 36) == 0)
    check("act 2 pre-boss: rests at 78% HP", rest_choice(32, 62) == 0)
    # boss relics judged against the deck: Runic Pyramid needs outlets, energy relics need energy-hungry decks
    def deck_g(cards):
        g = state(["Strike"], [monster("Jaw Worm", 40, 0)]).game_state
        g.deck = state(cards, [monster("Jaw Worm", 40, 0)]).game_state.combat_state.hand
        return g
    starter = ["Strike"] * 5 + ["Defend"] * 4 + ["Bash"]
    clog = deck_g(starter + ["Wild Strike", "Power Through", "Wild Strike"])
    outlets = deck_g(starter + ["Corruption", "Second Wind", "Shrug It Off"])
    pyr = lambda g: policy.boss_relic_rule("Runic Pyramid", g)[0]
    check("Runic Pyramid: good with exhaust outlets, bad with clog", pyr(outlets) > 0 > pyr(clog),
          f"{pyr(outlets):+.2f} vs {pyr(clog):+.2f}")
    cheap = deck_g(starter + ["Shrug It Off", "Cleave", "Pommel Strike"])
    pricey = deck_g(starter + ["Bludgeon", "Heavy Blade", "Bludgeon", "Corruption"])
    key = lambda g: policy.boss_relic_rule("Cursed Key", g)[0]
    check("energy relics worth more to expensive decks", key(pricey) > key(cheap), f"{key(pricey):+.2f} vs {key(cheap):+.2f}")
    # relic vs relic: Sacred Bark after Sozu is useless (run 211), and Sozu would waste an owned Sacred Bark
    import relics as R
    gs = deck_g(starter)
    gs.relics = gs.relics + [NS(name="Sozu", id="Sozu", counter=-1)]
    bark = policy.boss_relic_rule("Sacred Bark", gs)
    check("Sacred Bark ruled out with Sozu", bark[0] <= -2.0 and "Sozu" in bark[1], str(bark))
    check("Sozu penalized when it kills a Sacred Bark", R.conflict("Sozu", {"Sacred Bark"})[0] < 0)
    # Silent: Blade Dance's Shivs are damage (they used to count as nothing)
    msg = state(["Blade Dance", "Defend"], [monster("Louse", 11, 6)], energy=1)
    plan = planner.plan_turn(msg, P, [])
    check("Shivs count: Blade Dance kills an 11 HP Louse", names(plan, msg) == ["Blade Dance"] and plan.summary["kills"] == 1,
          plan.describe(msg.game_state.combat_state.hand, msg.game_state.combat_state.monsters))
    # Silent poison: Catalyst doubles poison already on the enemy; Bouncing Flask is 9 poison, not nothing
    msg = state(["Catalyst", "Defend"], [monster("Jaw Worm", 60, 0, powers=[("Poison", 12)])], energy=1)
    plan = planner.plan_turn(msg, P, [])
    check("Catalyst on a poisoned enemy", names(plan, msg) == ["Catalyst"], ", ".join(names(plan, msg)))
    msg = state(["Bouncing Flask"], [monster("Jaw Worm", 60, 0)], energy=2)
    plan = planner.plan_turn(msg, P, [])
    check("Bouncing Flask counts its 9 poison", names(plan, msg) == ["Bouncing Flask"] and plan.value - plan.base > 15,
          f"{plan.value - plan.base:+.1f}")
    # an enemy its poison will kill at the start of its turn never attacks: no need to block it
    msg = state(["Defend", "Defend"], [monster("Cultist", 5, 30, powers=[("Poison", 6)])], hp=20, energy=2)
    plan = planner.plan_turn(msg, P, [])
    check("poison-doomed enemy: its attack doesn't count", plan.summary["taken"] == 0 and plan.base > -100, str(plan.summary))
    # act 1 rest site at 21% HP: rest, never smith (early Silent run 2)
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json"), params=dict(P), temperature=0.05)
    gm = NS(screen=NS(has_rested=False), choice_list=["rest", "smith"], current_hp=15, max_hp=70, floor=10,
            act=1, act_boss="Hexaghost")
    check("act 1: rests at 21% HP", policy.PolicyAgent()._rest(NS(game_state=gm), None)[0].choice_index == 0)
    # weak starters stay out of act 1 elites until the deck has damage
    early = NS(act=1, floor=5, current_hp=70, max_hp=70, gold=99, relics=[], deck=real_deck, character="THE_SILENT")
    iron = NS(act=1, floor=5, current_hp=70, max_hp=70, gold=99, relics=[], deck=real_deck, character="IRONCLAD")
    check("Silent starter deck avoids act 1 elites; Ironclad still takes them",
          policy.PolicyAgent()._map_rules(early).get("E", 0) < 0 < policy.PolicyAgent()._map_rules(iron).get("E", 0))
    # cards the planner used to undervalue (they sat in hand: Wraith Form 12%, Double Tap 16%, Reaper 17%)
    msg = state(["Wraith Form", "Defend", "Defend", "Defend"], [monster("Book of Stabbing", 120, 6, 6)], hp=40, energy=3)
    plan = planner.plan_turn(msg, P, [])
    check("Wraith Form against a 36-damage turn", names(plan, msg) == ["Wraith Form"], ", ".join(names(plan, msg)))
    msg = state(["Double Tap", "Heavy Blade"], [monster("Jaw Worm", 60, 0)], energy=3)
    plan = planner.plan_turn(msg, P, [])
    check("Double Tap before Heavy Blade hits twice", names(plan, msg) == ["Double Tap", "Heavy Blade"]
          and plan.summary["dealt"] >= 28, plan.describe(msg.game_state.combat_state.hand, msg.game_state.combat_state.monsters))
    hurt = planner.plan_turn(state(["Reaper"], [monster("Louse", 12, 0), monster("Louse", 12, 0)], hp=30, energy=2), P, [])
    full = planner.plan_turn(state(["Reaper"], [monster("Louse", 12, 0), monster("Louse", 12, 0)], hp=80, energy=2), P, [])
    check("Reaper's healing counts when hurt", (hurt.value - hurt.base) > (full.value - full.base) + 5,
          f"{hurt.value - hurt.base:+.1f} vs {full.value - full.base:+.1f}")
    # Dexterity lasts the fight: Footwork over one Defend on a quiet turn, but not when the hit would kill
    calm = state(["Footwork", "Defend"], [monster("Jaw Worm", 44, 5)], energy=1)
    check("Footwork on a quiet turn", names(planner.plan_turn(calm, P, []), calm) == ["Footwork"],
          ", ".join(names(planner.plan_turn(calm, P, []), calm)))
    lethal = state(["Footwork", "Defend"], [monster("Jaw Worm", 44, 11)], hp=8, energy=1)
    check("Defend when the hit would kill", names(planner.plan_turn(lethal, P, []), lethal) == ["Defend"],
          ", ".join(names(planner.plan_turn(lethal, P, []), lethal)))
    # expert pick rates (human.expert_rates) as a learnable feature; Ironclad act 2: fewer hallway fights
    import human
    human._RATES["ironclad"] = {"card": {("Shockwave", 1): 0.6, ("Sword Boomerang", 1): -0.27}, "relic": {"Runic Pyramid": 0.4}}
    ag = policy.PolicyAgent()
    g1 = NS(act=1, floor=3, current_hp=70, max_hp=80, gold=99, relics=[], deck=real_deck, character="IRONCLAD")
    fs = ag._card_features(human.card_from_id("Shockwave"), g1, {})
    fb = ag._card_features(human.card_from_id("Sword Boomerang"), g1, {})
    check("expert pick rate feature: Shockwave up, Sword Boomerang down",
          fs.get("expertpick") == 0.6 and fb.get("expertpick") == -0.27, f"{fs.get('expertpick')} {fb.get('expertpick')}")
    policy.CONFIG.expert_features = False
    check("no expert features while converting expert runs", "expertpick" not in ag._card_features(
        human.card_from_id("Shockwave"), g1, {}))
    policy.CONFIG.expert_features = True
    del human._RATES["ironclad"]
    g2 = NS(act=2, floor=20, current_hp=70, max_hp=80, gold=99, relics=[], deck=real_deck, character="IRONCLAD")
    r2 = ag._map_rules(g2)
    check("Ironclad act 2: away from hallway fights, toward elites when healthy", r2.get("M", 0) < 0 and r2.get("E", 0) > 1, str(r2))
    # card use from the protocol log: hand per turn, plays, HP per fight, co-played pairs
    import cardutil
    def st(turn, hand, hp, floor=1, deck=("Strike", "Bash", "Inflame", "Twin Strike")):
        return "00:00:00.000 << " + json.dumps({"game_state": {"floor": floor, "act": 1, "current_hp": hp,
            "deck": [{"name": d, "type": "ATTACK"} for d in deck],
            "combat_state": {"turn": turn, "player": {"current_hp": hp, "max_hp": 80},
                             "monsters": [{"name": "Jaw Worm"}], "hand": [{"name": h, "type": "ATTACK"} for h in hand]}}})
    lines = [st(1, ["Inflame", "Twin Strike", "Strike"], 80), "00:00:00.100 >> play 1",
             st(1, ["Twin Strike", "Strike"], 80), "00:00:00.200 >> play 1 0",
             st(2, ["Bash", "Strike"], 74), "00:00:00.300 >> end",
             "00:00:00.400 << " + json.dumps({"game_state": {"floor": 1, "current_hp": 70}})]
    fights = cardutil.parse_lines(lines)
    c = cardutil.compact(fights)
    check("card use: turns, plays, HP lost from the log",
          len(fights) == 1 and fights[0]["hp_start"] == 80 and fights[0]["hp_end"] == 70
          and c["played"].get("Inflame") == 1 and c["seen"].get("Bash") == 1 and not c["played"].get("Bash")
          and c["pair_played"].get("Inflame|Twin Strike") == 1, json.dumps(c)[:200])
    # relics feed card synergies and path choices
    import synergy
    sf, why = synergy.explain("Paper Phrog", [], ["Bag of Marbles"])
    sf2, _ = synergy.explain("Heavy Blade", ["Inflame"], ["Vajra"])
    check("relics count as synergy enablers (Vajra feeds Heavy Blade)", sf2.get("synrule:strength->payoff", 0) >= 2, str(sf2))
    check("path relic prior: Black Star makes elites better, Ectoplasm shops worse",
          Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD").w("pathrelic:Black Star:E") > 0
          and Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD").w("pathrelic:Ectoplasm:$") < 0)
    check("path features only for owned relics and the rooms they change",
          policy.PATH_RELIC["Black Star"] == {"E": 0.6} and "M" not in policy.PATH_RELIC["Black Star"])
    # the HP rules shape the whole route: two fights now, one leads on to rest sites, the other to more fights
    mp = copy.deepcopy(json.loads((FIX / "map-2.json").read_text()))
    gm = mp["game_state"]
    gm.update({"current_hp": 26, "max_hp": 80, "floor": 20, "act": 2, "class": "IRONCLAD", "act_boss": "The Champ"})
    node = lambda x, y, sym, ch: {"x": x, "y": y, "symbol": sym, "children": [{"x": cx, "y": cy} for cx, cy in ch], "parents": []}
    gm["map"] = [node(0, 1, "M", [(0, 2)]), node(0, 2, "R", [(0, 3)]), node(0, 3, "R", []),
                 node(1, 1, "M", [(1, 2)]), node(1, 2, "M", [(1, 3)]), node(1, 3, "M", [])]
    gm["screen_state"]["next_nodes"] = [{"symbol": "M", "x": 1, "y": 1}, {"symbol": "M", "x": 0, "y": 1}]
    policy.CONFIG = policy.Config(model=Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD"), tracker=policy.RunTracker(),
                                  temperature=0.01, llm_events=False)
    act_, note = policy.PolicyAgent()._map(parse_message(mp), None)
    check("low HP: of two fights, the one whose route leads to rest sites", getattr(act_, "choice_index", None) == 1, note)
    # anticipation: same rooms in a different order (M M R vs M R M). Judged at today's HP they tie; with HP projected
    # along the route the rest site comes when it is needed (after two fights) and that route wins
    gm["current_hp"] = 48
    mq = Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD")   # like the live model: rest sites only pay when low
    mq.weights.update({"node:R:h1": 0.0, "node:M:h1": 0.0, "node:R:h2": 0.0, "node:M:h2": 0.0,
                       "node:R:h0": 1.0, "node:M:h0": -1.0, "nodeact:R:a2": 0.0, "nodeact:M:a2": 0.0})
    policy.CONFIG = policy.Config(model=mq, tracker=policy.RunTracker(), temperature=0.05, llm_events=False)
    gm["map"] = [node(0, 1, "M", [(0, 2)]), node(0, 2, "M", [(0, 3)]), node(0, 3, "R", []),
                 node(1, 1, "M", [(1, 2)]), node(1, 2, "R", [(1, 3)]), node(1, 3, "M", [])]
    act_, note = policy.PolicyAgent()._map(parse_message(mp), None)
    probs = policy.CONFIG.tracker.events[-1]["probs"]
    check("projected HP: the rest site where it will be needed (after two act 2 fights)", probs[1] > 0.9, note)
    # the campfire before the boss: under the threshold it always rests, whatever the learned weights say
    rs = json.loads((FIX / "rest-1.json").read_text())
    rs["game_state"].update({"floor": 15, "act": 1, "current_hp": 49, "max_hp": 90, "act_boss": "The Guardian"})
    mr = Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD")
    mr.weights.update({"restboss:h1": -1.5, "smithboss:h1": 6.0})      # far beyond what the bounds now allow
    policy.CONFIG = policy.Config(model=mr, tracker=policy.RunTracker(), temperature=0.35, llm_events=False)
    act_, note = policy.PolicyAgent()._rest(parse_message(rs), None)
    check("before the boss under 65% HP: rests even when learning says upgrade",
          act_.choice_index == 0 and "rule: rest before the boss" in note
          and "restboss:h1" in policy.CONFIG.tracker.events[-1]["options"][0], note)
    rs["game_state"]["floor"] = 9
    act_, note = policy.PolicyAgent()._rest(parse_message(rs), None)
    check("mid-act campfires use their own weights (rest:h1 / smith:h1)",
          "rest:h1" in policy.CONFIG.tracker.events[-1]["options"][0], note)
    # overnight Silent runs: low-HP pathing everywhere, Velvet Choker vs Shiv decks, no auto Smoke Bomb
    low = NS(act=1, floor=9, current_hp=20, max_hp=70, gold=99, relics=[], deck=real_deck, character="THE_SILENT")
    r = policy.PolicyAgent()._map_rules(low)
    check("under 40% HP: away from fights, toward rest", r.get("M", 0) < -1 and r.get("E", 0) < -2 and r.get("R", 0) > 1, str(r))
    shiv = deck_g(starter + ["Blade Dance", "Blade Dance", "Shrug It Off"])
    vc = policy.boss_relic_rule("Velvet Choker", shiv)
    check("Velvet Choker is bad for a Shiv deck", vc[0] < -0.8, str(vc))
    msg = state(["Strike"], [monster("Jaw Worm", 40, 30)], hp=10, potions=["Smoke Bomb"])
    action, note = first(msg)
    check("no Smoke Bomb on a lethal turn (escape aborted a run)", not isinstance(action, UsePotion), note)
    print("PLANNER OK")


if __name__ == "__main__":
    main()
