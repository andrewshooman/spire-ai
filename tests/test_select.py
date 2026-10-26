"""Champion selection v2: rotating bench seeds, seed-relative scores over 6 bench runs, revert only on a
clear drop twice, and a reverted model is migrated to the current code."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import evolve  # noqa: E402
from model import Model  # noqa: E402

evolve.say = lambda *a, **k: None


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not ok:
        raise SystemExit(1)


def cycle(ws, args, results):
    """Feed one cycle's bench results [(seed, floor)] and run selection."""
    for seed, floor in results:
        ws.state["cycle_bench"].append(floor)
        ws.state.setdefault("cycle_bench_reward", []).append(Model.outcome(floor, False))
        ws.state.setdefault("cycle_bench_seed", []).append(seed)
    ws.state["run"] += 10
    note = evolve.select(ws, args)
    ws.model.save()
    return note


def main() -> None:
    pool = ["BENCHA1", "BENCHB2", "BENCHC3", "BENCHD4", "BENCHE5", "BENCHF6"]
    args = argparse.Namespace(cycle=10, bench_seeds=pool, bench_per_cycle=2, revert_margin=1.0)
    seeds = [evolve.plan_run(i, args) for i in range(60)]
    bench = [s for k, s in seeds if k == "bench"]
    check("each cycle has 2 bench runs, rotating through all 6 seeds",
          len(bench) == 12 and set(bench) == set(pool) and bench[:2] != bench[2:4], str(bench[:6]))

    tmp = Path(tempfile.mkdtemp())
    ws = evolve.Workspace(tmp / "ironclad", "IRONCLAD", ROOT / "agent" / "strategy_baseline.md")
    ws.model.save()
    # seed history: seed A is easy (~floor 40), seed B hard (~floor 20)
    ws.state["bench_hist"] = {"BENCHA1": [Model.outcome(f, False) for f in (38, 42, 40, 41)],
                              "BENCHB2": [Model.outcome(f, False) for f in (19, 21, 20, 22)]}
    notes = [cycle(ws, args, [("BENCHA1", 40), ("BENCHB2", 20)]) for _ in range(2)]
    check("no verdict before 6 bench runs (one lucky pair can't crown a champion)",
          all(n.startswith("measuring") for n in notes), str(notes))
    note = cycle(ws, args, [("BENCHA1", 41), ("BENCHB2", 21)])
    check("champion after 6 bench runs", note.startswith("champion"), note)
    champ_score = ws.state["champion"]["score"]
    note = cycle(ws, args, [("BENCHA1", 36), ("BENCHB2", 18)])
    check("an ordinary dip is noise: kept", note.startswith("kept"), note)

    # the champion file predates the bounds (like the run-130 champion with skip:a2 = -5.5)
    champ = json.loads((ws.root / "champion_model.json").read_text())
    champ["weights"]["skip:a2"] = -5.5
    (ws.root / "champion_model.json").write_text(json.dumps(champ))
    # rewards step at boss kills (v4), so the 6-run window needs two bad cycles to sit clearly below
    bad = [cycle(ws, args, [("BENCHA1", 12), ("BENCHB2", 6)]) for _ in range(3)]
    check("one clearly bad stretch is not enough", bad[0].startswith("kept") and bad[1].startswith("kept")
          and "once" in bad[1], f"{bad[0]} / {bad[1]}")
    check("two clearly bad cycles in a row -> reverted", bad[2].startswith("reverted"), bad[2])
    check("reverted model migrated to the current bounds", ws.model.w("skip:a2") >= -1.5, f"{ws.model.w('skip:a2')}")
    check("after a revert the model is measured afresh", ws.state["bench_window"] == [])
    check("champion score is seed-relative", abs(champ_score) < 0.5, f"{champ_score:+.2f}")
    # the run counter never reuses a run number already on disk (a restored older state.json overwrote runs)
    (ws.root / "runs").mkdir(exist_ok=True)
    (ws.root / "runs" / "run_0090.json").write_text("{}")
    ws.state["run"] = 40
    check("run counter behind the files continues after the highest run", evolve.next_run_index(ws) == 90
          and ws.state["run"] == 90)
    # Settings "Rotate characters": a random character per run from the chosen set
    import random as _r
    rng = _r.Random(1)
    picks = {evolve.rotation_pick({"rotate": "all 4"}, rng) for _ in range(60)}
    check("rotation picks among all 4 characters; off = the Character setting",
          picks == {"ironclad", "silent", "defect", "watcher"} and evolve.rotation_pick({"rotate": "off"}, rng) is None
          and evolve.rotation_pick({}, rng) is None
          and {evolve.rotation_pick({"rotate": "ironclad + silent"}, rng) for _ in range(30)} == {"ironclad", "silent"})
    # warm start: a young Silent model takes the Ironclad's character-neutral weights, not its card weights
    iron = Model(tmp / "iron.json", "IRONCLAD")
    iron.weights.update({"node:E:h2": 1.7, "rest:h0": 2.2, "card:Bash": 3.0})
    iron.params["block"] = 1.9
    iron.save()
    sws = evolve.Workspace(tmp / "silent", "THE_SILENT", ROOT / "agent" / "strategy_baseline.md")
    evolve.warm_start(sws, "silent", src=tmp / "iron.json")
    check("warm start copies path/rest weights and combat weights",
          sws.model.w("node:E:h2") == 1.7 and sws.model.w("rest:h0") == 2.2 and sws.model.params["block"] == 1.9)
    check("warm start leaves card weights to the new character", sws.model.w("card:Bash") != 3.0)
    t_warm = sws.model.temperature()
    check("warm-started model explores less than a blank one", t_warm < Model(tmp / "blank.json", "THE_SILENT").temperature(),
          f"{t_warm:.2f}")
    evolve.warm_start(sws, "silent", src=tmp / "iron.json")
    check("warm start runs once", sws.model.meta["warm_start"]["weights"] == 2)

    # learning v2: the value error is measured before fitting the run (it was the in-sample residual, ~0, so
    # every run's advantage hit the cap and weights drifted to the bounds on coin flips)
    import random as _r
    m = Model(tmp / "lv2.json", "WATCHER")
    rng = _r.Random(3)
    for i in range(60):   # a run: ~40 decisions, outcome a coin flip (act 1 boss beaten or not)
        evs = [{"kind": "card", "act": 1, "options": [{"card:Sash Whip": 1.0}, {"card:Vault": 1.0}],
                "chosen": rng.randrange(2), "probs": [0.5, 0.5], "temp": 1.0,
                "state": {"hp": rng.random(), "deck": 0.3}} for _ in range(40)]
        m.learn(evs, 0.1 if rng.random() < 0.5 else 0.0)
    check("value error scale stays out of sample (no collapse to ~0)", m.meta["value_var"] > 0.001,
          f"{m.meta['value_var']:.5f}")
    drift = abs(m.w("card:Sash Whip") - m.prior("card:Sash Whip"))
    check("coin-flip outcomes leave a card weight near its prior (cap 2, pull back to the prior)", drift < 1.5,
          f"drift {drift:.2f}")
    check("dilute / node weights are bounded", m.w("dilute") >= -1.5 and Model.prior(m, "dilute") >= -1.5)
    # the one-time repair: young non-Ironclad models pulled 70% back to the corrected priors, the Ironclad kept
    for char, runs, keep in (("DEFECT", 65, False), ("IRONCLAD", 214, True)):
        ws2 = evolve.Workspace(tmp / f"lv2_{char}", char, ROOT / "agent" / "strategy_baseline.md")
        ws2.model.meta.pop("learning_v2", None)
        ws2.model.meta["runs_trained"] = runs
        ws2.model.weights.update({"card:Hello World": 3.5, "nodeact:?:a1": 2.9, "skip:a2": -1.5})
        evolve.migrate(ws2)
        hw = ws2.model.w("card:Hello World")
        ok = hw == 3.5 if keep else hw < 1.5 and "skip:a2" not in ws2.model.weights
        check(f"learning v2 migration: {char} {'kept' if keep else 'repaired'}", ok and ws2.model.meta["learning_v2"],
              f"Hello World {hw:.2f}")
    print("SELECT OK")


if __name__ == "__main__":
    main()
