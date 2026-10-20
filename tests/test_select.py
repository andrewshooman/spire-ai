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
    bad = [cycle(ws, args, [("BENCHA1", 12), ("BENCHB2", 6)]) for _ in range(2)]
    check("one clearly bad stretch is not enough", bad[0].startswith("kept") and "once" in bad[0], bad[0])
    check("two clearly bad cycles in a row -> reverted", bad[1].startswith("reverted"), bad[1])
    check("reverted model migrated to the current bounds", ws.model.w("skip:a2") >= -1.5, f"{ws.model.w('skip:a2')}")
    check("after a revert the model is measured afresh", ws.state["bench_window"] == [])
    check("champion score is seed-relative", abs(champ_score) < 0.5, f"{champ_score:+.2f}")
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
    print("SELECT OK")


if __name__ == "__main__":
    main()
