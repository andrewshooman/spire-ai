"""sim_train.py -- offline data points and combat tuning with the simulator.

Everything here lives under evolve/<character>/sim/ and never touches the live
training data (runs/, progress.csv, the dashboard's history, model.json) unless
you run `apply` yourself.

    bench   [--fights N]          current live combat params vs the fight set: win rate,
                                  HP lost, per encounter -> sim/bench.csv + sim/last_bench.json
    train   [--iters 300]         antithetic ES on simulated fights, starting from the live
                                  params; best-on-holdout -> sim/sim_params.json, log -> sim/train.csv
    compare                       live params vs sim_params.json on the holdout fights
    calibrate                     replay real fights (deck + HP at the start, recorded by the live
                                  bot) and compare the simulator's win rate / HP lost with what happened
    apply                         copy sim_params.json into model.json (bot must be stopped;
                                  the old model.json is backed up in sim/)

Fight set: every real run's final deck replayed against the fight that killed it (at the
HP it entered with), plus each deck against the act-1 elites and bosses at full HP.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import random
import re
import shutil
import socket
import sys
import time
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sim  # noqa: E402
from model import Model  # noqa: E402

EVOLVE = HERE.parent / "evolve"
KILLER = {"Gremlin Nob": "Gremlin Nob", "Lagavulin": "Lagavulin", "Sentry": "3 Sentries",
          "Hexaghost": "Hexaghost", "The Guardian": "The Guardian", "Slime Boss": "Slime Boss",
          "Acid Slime (L)": "Slime Boss", "Spike Slime (L)": "Slime Boss", "Acid Slime (M)": "Slime Boss",
          "Spheric Guardian": "Spheric Guardian", "Snake Plant": "Snake Plant",
          "Shelled Parasite": "Shelled Parasite", "Book of Stabbing": "Book of Stabbing"}
CHECKS = ["Gremlin Nob", "Lagavulin", "3 Sentries", "Hexaghost", "The Guardian", "Slime Boss"]


def workspace(character: str) -> Path:
    return EVOLVE / character.lower()


def fight_set(ws: Path, limit: int = 120) -> list[dict]:
    """Real decks from the live runs (read only) turned into simulator fights."""
    fights = []
    for p in sorted((ws / "runs").glob("run_*.json"))[-limit:]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        deck = [d for d in r.get("deck", []) if sim.C.base_name(d) in sim.COST]
        if len(deck) < 8:
            continue
        killer = (r.get("killer") or "").split(",")[0].strip()
        enc = KILLER.get(killer)
        hp_in = next((h for h in reversed(r.get("hp", [])) if h[0] < r["floor"]), [0, 80, 80])
        if enc and hp_in[1] > 0:
            fights.append({"deck": deck, "enc": enc, "hp": hp_in[1], "max_hp": hp_in[2] or 80,
                           "src": f"run {r['run']} death"})
        if r["floor"] <= 20:
            for enc in CHECKS:
                fights.append({"deck": deck, "enc": enc, "hp": 70, "max_hp": 80, "src": f"run {r['run']} check"})
    if not fights:  # no live data yet: starter-deck checks
        for enc in sim.ENCOUNTERS:
            fights.append({"deck": list(sim.STARTER), "enc": enc, "hp": 80, "max_hp": 80, "src": "starter"})
    return fights


def fitness(res: dict) -> float:
    """Win with HP to spare is best; a loss still counts how much of the enemy was beaten down."""
    if res["win"]:
        return 1.0 + 0.5 * res["hp_left"] / max(1, res["max_hp"])
    return 0.5 * (1.0 - res["enemy_left"])


def play(fights: list[dict], params: dict, seeds: list[int], max_nodes: int = 800) -> list[dict]:
    out = []
    for f, s in zip(fights, seeds):
        r = sim.Fight(f["deck"], f["enc"], hp=f["hp"], max_hp=f["max_hp"], seed=s).run(params, max_nodes=max_nodes)
        r["fit"] = fitness(r)
        out.append(r)
    return out


def summary(results: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for r in results:
        by.setdefault(r["encounter"], []).append(r)
    return {"fights": len(results), "win": round(mean(r["win"] for r in results), 3),
            "hp_lost": round(mean(r["hp_lost"] for r in results), 1),
            "fit": round(mean(r["fit"] for r in results), 4),
            "by": {k: {"n": len(v), "win": round(mean(x["win"] for x in v), 2),
                       "hp_lost": round(mean(x["hp_lost"] for x in v), 1)} for k, v in sorted(by.items())}}


def live_params(ws: Path) -> dict:
    m = Model(ws / "model.json", ws.name.upper())
    return dict(m.params)


def append_csv(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def print_summary(label: str, s: dict) -> None:
    print(f"\n{label}: {s['fights']} fights, win {s['win']:.0%}, HP lost {s['hp_lost']}, fitness {s['fit']}")
    for enc, v in s["by"].items():
        print(f"  {enc:18} n={v['n']:4}  win {v['win']:5.0%}  HP lost {v['hp_lost']:5.1f}")


# ---------------------------------------------------------------- commands

def cmd_bench(args) -> None:
    ws = workspace(args.character)
    out = ws / "sim"
    out.mkdir(parents=True, exist_ok=True)
    fights = fight_set(ws)
    rng = random.Random(args.seed)
    picked = [rng.choice(fights) for _ in range(args.fights)]
    params = json.loads((out / "sim_params.json").read_text())["params"] if args.sim_params else live_params(ws)
    t = time.time()
    s = summary(play(picked, params, [rng.randrange(10 ** 9) for _ in picked]))
    s["seconds"] = round(time.time() - t, 1)
    print_summary("sim params" if args.sim_params else "live params", s)
    print(f"  ({s['seconds']} s, {60 * s['fights'] / max(1, s['seconds']):.0f} fights/min)")
    (out / "last_bench.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    append_csv(out / "bench.csv", {"time": dt.datetime.now().isoformat(timespec="seconds"),
                                   "params": "sim" if args.sim_params else "live", "fights": s["fights"],
                                   "win": s["win"], "hp_lost": s["hp_lost"], "fit": s["fit"]})


def cmd_train(args) -> None:
    ws = workspace(args.character)
    out = ws / "sim"
    out.mkdir(parents=True, exist_ok=True)
    fights = fight_set(ws)
    rng = random.Random(args.seed)
    rng.shuffle(fights)
    cut = max(1, len(fights) // 4)
    hold, train = fights[:cut], fights[cut:] or fights
    hold_seeds = [rng.randrange(10 ** 9) for _ in hold]
    params = live_params(ws)
    start = dict(params)
    best = summary(play(hold, params, hold_seeds))["fit"]
    print(f"{len(train)} training fights, {len(hold)} holdout; holdout fitness at start {best:.4f}")
    best_params = dict(params)
    sigma, lr, dvar = args.sigma, args.lr, 0.01
    for it in range(1, args.iters + 1):
        batch = [rng.choice(train) for _ in range(args.batch)]
        seeds = [rng.randrange(10 ** 9) for _ in batch]
        eps = {k: rng.gauss(0.0, sigma) for k in params if k != "energy_cost"}
        plus = {k: v * math.exp(eps.get(k, 0.0)) for k, v in params.items()}
        minus = {k: v * math.exp(-eps.get(k, 0.0)) for k, v in params.items()}
        fp = mean(r["fit"] for r in play(batch, plus, seeds))
        fm = mean(r["fit"] for r in play(batch, minus, seeds))
        diff = fp - fm
        adv = max(-3.0, min(3.0, diff / math.sqrt(max(dvar, 1e-5))))
        dvar = 0.9 * dvar + 0.1 * diff ** 2
        for k, e in eps.items():
            params[k] = max(0.01, min(50.0, params[k] * math.exp(lr * adv * e)))
        row = {"iter": it, "f_plus": round(fp, 4), "f_minus": round(fm, 4), "adv": round(adv, 2)}
        if it % args.eval_every == 0 or it == args.iters:
            h = summary(play(hold, params, hold_seeds))
            row.update({"holdout_fit": h["fit"], "holdout_win": h["win"], "holdout_hp_lost": h["hp_lost"]})
            mark = ""
            if h["fit"] > best:
                best, best_params, mark = h["fit"], dict(params), "  <- best"
                (out / "sim_params.json").write_text(json.dumps(
                    {"params": best_params, "holdout_fit": best, "from_live": start, "iter": it,
                     "time": dt.datetime.now().isoformat(timespec="seconds")}, indent=1), encoding="utf-8")
            print(f"iter {it:4}: holdout fitness {h['fit']:.4f}, win {h['win']:.0%}, HP lost {h['hp_lost']}{mark}")
        append_csv(out / "train.csv", row)
    print(f"\nbest holdout fitness {best:.4f} (start was {summary(play(hold, start, hold_seeds))['fit']:.4f})")
    if (out / "sim_params.json").exists():
        print(f"saved {out / 'sim_params.json'} -- run `sim.bat compare`, then `sim.bat apply` with the bot stopped")
    else:
        print("no improvement over the live params; nothing saved")


def cmd_compare(args) -> None:
    ws = workspace(args.character)
    out = ws / "sim"
    path = out / "sim_params.json"
    if not path.exists():
        sys.exit("No sim_params.json yet: run `sim.bat train` first.")
    rng = random.Random(args.seed + 1)
    fights = fight_set(ws)
    picked = [rng.choice(fights) for _ in range(args.fights)]
    seeds = [rng.randrange(10 ** 9) for _ in picked]
    a = summary(play(picked, live_params(ws), seeds))
    b = summary(play(picked, json.loads(path.read_text())["params"], seeds))
    print_summary("live params", a)
    print_summary("sim params ", b)


ENC_BY_ENEMIES = {("Sentry",): "3 Sentries", ("Gremlin Nob",): "Gremlin Nob", ("Lagavulin",): "Lagavulin",
                  ("Hexaghost",): "Hexaghost", ("The Guardian",): "The Guardian", ("Slime Boss",): "Slime Boss",
                  ("Spheric Guardian",): "Spheric Guardian", ("Snake Plant",): "Snake Plant",
                  ("Shelled Parasite",): "Shelled Parasite", ("Book of Stabbing",): "Book of Stabbing",
                  ("Jaw Worm",): "Jaw Worm", ("Cultist",): "Cultist"}


def real_fights(ws: Path) -> list[dict]:
    """Fights the live bot recorded (fight_log) joined with how they actually ended."""
    out = []
    for p in sorted((ws / "runs").glob("run_*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ends = {f["floor"]: f for f in r.get("fights", [])}
        for fl in r.get("fight_log", []):
            enc = ENC_BY_ENEMIES.get(tuple(sorted(set(fl["enemies"]))))
            end = ends.get(fl["floor"])
            if not enc or not end:
                continue
            won = fl["floor"] != r["floor"] or r.get("victory")
            heal = 6 if won and "Burning Blood" in fl.get("relics", []) else 0
            deck = [d for d in fl["deck"] if sim.C.base_name(d) in sim.COST]
            out.append({"deck": deck, "enc": enc, "hp": fl["hp"], "max_hp": fl["max_hp"], "won": bool(won),
                        "hp_lost": max(0, -(end.get("hp_delta") or 0) + heal), "src": f"run {r['run']}"})
    return out


def cmd_calibrate(args) -> None:
    ws = workspace(args.character)
    fights = real_fights(ws)
    if not fights:
        sys.exit("No recorded fights yet: runs played with the current bot record their fights; check back later.")
    params = live_params(ws)
    rng = random.Random(args.seed)
    rows: dict[str, list] = {}
    for f in fights:
        sims = play([f] * args.reps, params, [rng.randrange(10 ** 9) for _ in range(args.reps)])
        rows.setdefault(f["enc"], []).append((f, sims))
    print(f"{len(fights)} real fights replayed {args.reps}x each\n")
    print(f"{'encounter':18} {'n':>4}  {'real win':>8} {'sim win':>8}   {'real HP lost':>12} {'sim HP lost':>11}")
    report = {}
    for enc, items in sorted(rows.items()):
        real_w = mean(f["won"] for f, _ in items)
        sim_w = mean(mean(x["win"] for x in s) for _, s in items)
        real_h = mean(f["hp_lost"] for f, _ in items)
        sim_h = mean(mean(x["hp_lost"] for x in s) for _, s in items)
        report[enc] = {"n": len(items), "real_win": real_w, "sim_win": sim_w, "real_hp": real_h, "sim_hp": sim_h}
        print(f"{enc:18} {len(items):4}  {real_w:8.0%} {sim_w:8.0%}   {real_h:12.1f} {sim_h:11.1f}")
    out = ws / "sim"
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


def cmd_apply(args) -> None:
    ws = workspace(args.character)
    out = ws / "sim"
    path = out / "sim_params.json"
    if not path.exists():
        sys.exit("No sim_params.json yet: run `sim.bat train` first.")
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", 9999))
        except OSError:
            sys.exit("The bot is running (port 9999 busy): stop start.bat first, or it will overwrite the change.")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(ws / "model.json", out / f"model_backup_{stamp}.json")
    data = json.loads((ws / "model.json").read_text(encoding="utf-8"))
    new = json.loads(path.read_text())["params"]
    data["params"].update({k: v for k, v in new.items() if k in data["params"]})
    data.setdefault("meta", {})["sim_applied"] = stamp
    (ws / "model.json").write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Applied simulator params to {ws / 'model.json'} (backup: sim/model_backup_{stamp}.json)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--character", default="ironclad")
    p.add_argument("--seed", type=int, default=7)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench")
    b.add_argument("--fights", type=int, default=300)
    b.add_argument("--sim-params", action="store_true", help="bench sim_params.json instead of the live params")
    t = sub.add_parser("train")
    t.add_argument("--iters", type=int, default=300)
    t.add_argument("--batch", type=int, default=24)
    t.add_argument("--sigma", type=float, default=0.15)
    t.add_argument("--lr", type=float, default=0.15)
    t.add_argument("--eval-every", type=int, default=25)
    c = sub.add_parser("compare")
    c.add_argument("--fights", type=int, default=400)
    sub.add_parser("apply")
    k = sub.add_parser("calibrate")
    k.add_argument("--reps", type=int, default=5)
    args = p.parse_args()
    {"bench": cmd_bench, "train": cmd_train, "compare": cmd_compare, "apply": cmd_apply,
     "calibrate": cmd_calibrate}[args.cmd](args)


if __name__ == "__main__":
    main()
