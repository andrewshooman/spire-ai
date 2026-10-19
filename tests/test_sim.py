"""Simulator checks: deterministic, mechanics behave, the trainer and calibration run.

Run from C:\\sts-evolve:  sts-bench\\.venv\\Scripts\\python.exe tests\\test_sim.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import sim  # noqa: E402
import sim_train  # noqa: E402
from model import COMBAT_DEFAULTS  # noqa: E402

P = dict(COMBAT_DEFAULTS)


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    a = sim.Fight(sim.STARTER, "Jaw Worm", seed=3).run(P)
    b = sim.Fight(sim.STARTER, "Jaw Worm", seed=3).run(P)
    check("same seed, same fight", a == b, str(a))
    check("starter deck beats Jaw Worm", a["win"], str(a))

    card = sim.make_card("Bash+")
    check("cards carry cost/target/upgrade", card["cost"] == 2 and card["has_target"] and card["upgrades"] == 1)
    check("Whirlwind is X-cost, no target", sim.make_card("Whirlwind")["cost"] == -1
          and not sim.make_card("Whirlwind")["has_target"])

    f = sim.Fight(sim.STARTER, "Hexaghost", hp=60, seed=1)
    for m in f.monsters:
        sim.choose_move(m, f)
    f.monsters[0].turn = 1
    sim.choose_move(f.monsters[0], f)
    check("Hexaghost Divider hits 6x (HP/12 + 1)", f.monsters[0].dmg == 6 and f.monsters[0].hits == 6,
          f"{f.monsters[0].dmg}x{f.monsters[0].hits}")

    f = sim.Fight(sim.STARTER, "Slime Boss", seed=2)
    boss = f.monsters[0]
    f.damage_monster(boss, 75)
    sim.choose_move(boss, f)
    f.monsters_act()
    names = sorted(m.name for m in f.monsters if not m.gone)
    check("Slime Boss splits at half HP", names == ["Acid Slime (L)", "Spike Slime (L)"], str(names))

    f = sim.Fight(sim.STARTER, "Spheric Guardian", seed=4)
    g = f.monsters[0]
    f.damage_monster(g, 10)
    f.monsters_act()
    check("Spheric Guardian keeps its block (Barricade)", g.block >= 30, f"block {g.block}")

    f = sim.Fight(sim.STARTER, "Gremlin Nob", seed=5)
    nob = f.monsters[0]
    nob.powers["Enrage"] = 2
    f.hand = [sim.make_card("Defend")]
    f.energy = 3
    msg = f.message()
    f.play(0, None, msg.game_state.combat_state.hand[0])
    check("Skills enrage Gremlin Nob", nob.p("Strength") == 2)

    f = sim.Fight(sim.STARTER, "Transient", seed=6)
    tr = f.monsters[0]
    sim.choose_move(tr, f)
    f.damage_monster(tr, 12)
    check("Shifting: damage cuts Transient's Strength this round", tr.p("Strength") == -12)
    for _ in range(5):
        f.block = 999
        f.monsters_act()
    check("Transient fades after 5 rounds", tr.gone)
    f = sim.Fight(sim.STARTER, "Orb Walker", seed=8)
    ow = f.monsters[0]
    sim.choose_move(ow, f)
    f.block = 999
    f.monsters_act()
    check("Orb Walker gains 3 Strength a round", ow.p("Strength") == 3)

    t = time.time()
    n = 60
    res = [sim.Fight(sim.STARTER + ["Pommel Strike", "Shrug It Off"], enc, hp=70, seed=s).run(P, max_nodes=800)
           for s in range(n // len(sim.ENCOUNTERS)) for enc in sim.ENCOUNTERS]
    rate = 60 * len(res) / (time.time() - t)
    check("fast enough for training (>= 200 fights/min)", rate >= 200, f"{rate:.0f} fights/min")

    # trainer + calibration on a throwaway workspace (never the live one)
    ws = Path(tempfile.mkdtemp()) / "ironclad"
    (ws / "runs").mkdir(parents=True)
    run = {"run": 1, "floor": 16, "victory": False, "killer": "Hexaghost", "deck": sim.STARTER + ["Inflame"],
           "hp": [[15, 60, 80], [16, 0, 80]], "fights": [{"floor": 5, "room": "fight", "hp_delta": -9, "max_hp": 80}],
           "fight_log": [{"floor": 5, "room": "MonsterRoom", "hp": 70, "max_hp": 80, "deck": sim.STARTER,
                          "enemies": ["Jaw Worm"], "relics": ["Burning Blood"]}]}
    (ws / "runs" / "run_0001.json").write_text(json.dumps(run))
    fights = sim_train.fight_set(ws)
    check("fight set built from real runs", any(f["enc"] == "Hexaghost" and f["hp"] == 60 for f in fights),
          str(len(fights)))
    real = sim_train.real_fights(ws)
    check("recorded fights join their real outcome", real and real[0]["won"] and real[0]["hp_lost"] == 15, str(real))
    print("SIM OK")


if __name__ == "__main__":
    main()
