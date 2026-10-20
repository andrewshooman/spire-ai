"""analyze.py -- one report on the live runs for deciding what to change next.

    analyze.bat [--since N] [--last N]

Reads evolve/<character>/runs/*.json (read only) and prints: throughput, how far runs
get (act reach rates), what kills them, depth by risk level, who decided card picks
(LLM vs policy) and how those runs went, shop spending, LLM latency, aborted runs.
Also writes evolve/<character>/analysis.json for comparing nights.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
EVOLVE = HERE.parent / "evolve"


def pct(a: float, b: float) -> str:
    return f"{a / b:.0%}" if b else "-"


def table(rows: list[tuple], head: tuple) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)] if rows else [len(h) for h in head]
    for r in (head, *rows):
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--character", default="ironclad")
    p.add_argument("--since", type=int, default=0, help="only runs after this run number")
    p.add_argument("--last", type=int, default=0, help="only the last N runs")
    a = p.parse_args()
    ws = EVOLVE / a.character
    runs = []
    for f in sorted((ws / "runs").glob("run_*.json")):
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    runs = [r for r in runs if r["run"] > a.since]
    if a.last:
        runs = runs[-a.last:]
    if not runs:
        sys.exit("No runs in that range.")
    n = len(runs)
    out: dict = {"runs": n, "from": runs[0]["run"], "to": runs[-1]["run"]}
    print(f"\n=== Spire AI analysis: runs {runs[0]['run']}-{runs[-1]['run']} ({n} runs) ===")

    # throughput
    t0 = dt.datetime.fromisoformat(runs[0]["started"])
    t1 = dt.datetime.fromisoformat(runs[-1]["started"]) + dt.timedelta(seconds=runs[-1].get("duration_s", 0))
    hours = max(1e-6, (t1 - t0).total_seconds() / 3600)
    llm_s = [r.get("llm_s", 0) for r in runs if "llm_s" in r]
    print(f"\nThroughput: {n / hours:.1f} runs/hr over {hours:.1f} h; median run {median(r.get('duration_s', 0) for r in runs) / 60:.1f} min")
    if llm_s:
        print(f"LLM time per run: {mean(llm_s):.1f} s (calls {mean(r.get('llm_calls', 0) for r in runs):.1f}, "
              f"cached {mean(r.get('llm_cached', 0) for r in runs):.1f})")
    out["runs_per_hr"] = round(n / hours, 2)

    # depth
    floors = [r["floor"] for r in runs]
    reach = {k: sum(f >= v for f in floors) for k, v in (("act1 boss beaten", 17), ("act2 boss beaten", 34), ("reached act3", 35), ("act3 boss beaten", 51))}
    print(f"\nDepth: avg floor {mean(floors):.1f}, best {max(floors)}, wins {sum(r['victory'] for r in runs)}")
    for k, v in reach.items():
        print(f"  {k:17} {v:4}  {pct(v, n)}")
    for kind in ("explore", "bench"):
        fl = [r["floor"] for r in runs if r.get("kind") == kind]
        if fl:
            print(f"  {kind:7} runs: {len(fl):3}, avg floor {mean(fl):.1f}")
    out.update({"avg_floor": round(mean(floors), 2), "best": max(floors), **{k.replace(" ", "_"): v for k, v in reach.items()}})

    # killers
    killers = collections.Counter(r.get("killer") or ("WIN" if r["victory"] else "?") for r in runs)
    print("\nWhat ends runs:")
    table([(k, v, pct(v, n), f"{mean(r['floor'] for r in runs if (r.get('killer') or '') == k):.0f}" if k not in ('WIN', '?') else "")
           for k, v in killers.most_common(12)], ("killer", "runs", "share", "avg floor"))
    out["killers"] = dict(killers.most_common(15))

    # risk appetite (explore runs record it)
    risky = [r for r in runs if "risk" in r and r.get("kind") == "explore"]
    if risky:
        print("\nDepth by risk level (explore runs; extra pull toward act 1-2 elites):")
        by = collections.defaultdict(list)
        for r in risky:
            by[r["risk"]].append(r)
        table([(k, len(v), f"{mean(x['floor'] for x in v):.1f}", f"{mean(x.get('elites_killed', 0) for x in v):.1f}",
                pct(sum(x['floor'] >= 17 for x in v), len(v)), pct(sum(x['floor'] >= 35 for x in v), len(v)))
               for k, v in sorted(by.items())], ("risk", "runs", "avg floor", "elites", "act1 cleared", "act3"))
        out["risk"] = {str(k): round(mean(x["floor"] for x in v), 2) for k, v in by.items()}

    # build flavor tried early (explore runs)
    flav = [r for r in runs if r.get("kind") == "explore" and "flavor" in r]
    if flav:
        print("\nDepth by early build flavor (explore runs):")
        by = collections.defaultdict(list)
        for r in flav:
            by[r["flavor"] or "(none)"].append(r)
        table([(k, len(v), f"{mean(x['floor'] for x in v):.1f}", pct(sum(x['floor'] >= 34 for x in v), len(v)),
                sum(x["victory"] for x in v), (v[-1].get("plan") or {}).get("archetype", ""))
               for k, v in sorted(by.items())], ("flavor", "runs", "avg floor", "act2 cleared", "wins", "last plan"))
        out["flavor"] = {k: round(mean(x["floor"] for x in v), 2) for k, v in by.items()}

    # who picked cards
    pick_src = collections.Counter()
    for r in runs:
        for line in r.get("llm_log", []):
            if line.get("topic") == "card":
                pick_src["llm" if line.get("choice") else "fallback"] += 1
    if pick_src:
        print(f"\nLLM card advice: {dict(pick_src)}")

    # shops
    buys = collections.Counter()
    for r in runs:
        for s in r.get("shops", []):
            b = s["bought"]
            kind = ("leave" if b == "(leave)" else "remove" if b in ("purge", "remove a card")
                    else "other")
            buys[kind] += 1
            if kind == "other":
                buys["item:" + b] += 1
    if buys:
        items = [(k[5:], v) for k, v in buys.most_common() if k.startswith("item:")][:10]
        print(f"\nShops: removals {buys['remove']}, other buys {buys['other']}, leaves {buys['leave']}")
        print("  top buys: " + ", ".join(f"{k} x{v}" for k, v in items))

    # boss relics, card picks
    rel = collections.Counter(b["relic"] for r in runs for b in r.get("boss_relics", []))
    if rel:
        print("\nBoss relics taken: " + ", ".join(f"{k} x{v}" for k, v in rel.most_common(10)))
    picks = collections.Counter(p["chosen"] for r in runs for p in r.get("picks", []))
    total = sum(picks.values())
    if total:
        print(f"Card picks: {total}, skipped {pct(picks.get('(skip)', 0), total)}; most taken: "
              + ", ".join(f"{re.sub(r'[+]$', '', k)} x{v}" for k, v in picks.most_common(8) if k != "(skip)"))

    # aborted
    ab = list((ws / "runs" / "aborted").glob("*.json")) if (ws / "runs" / "aborted").exists() else []
    if ab:
        reasons = collections.Counter(json.loads(f.read_text(encoding="utf-8")).get("aborted", "?")[:60] for f in ab)
        print(f"\nAborted runs (not learned from): {len(ab)}")
        for k, v in reasons.most_common(5):
            print(f"  {v} x {k}")
    out["aborted"] = len(ab)
    fl = sum(len(r.get("fight_log", [])) for r in runs)
    print(f"\nFights recorded for simulator calibration: {fl} (run `sim.bat calibrate` once there are a few hundred)")
    (ws / "analysis.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nSaved {ws / 'analysis.json'}")


if __name__ == "__main__":
    main()
