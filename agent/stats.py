"""stats.py -- everything the web app (and analyze.bat) needs, computed from the run files.

Read-only over evolve/<character>/runs/*.json. Functions return plain JSON-able dicts.
"""

from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
EVOLVE = HERE.parent / "evolve"
CHARACTERS = ("ironclad", "silent", "defect", "watcher")
BOSS_FLOORS = {1: 16, 2: 33, 3: 50}

_CACHE: dict = {}


def _r(x, n=1):
    return round(x, n) if isinstance(x, (int, float)) else x


def load_runs(char: str) -> list[dict]:
    """All finished runs of a character, cached by the newest file's mtime."""
    d = EVOLVE / char / "runs"
    if not d.exists():
        return []
    files = sorted(d.glob("run_*.json"))
    key = (char, len(files), files[-1].stat().st_mtime if files else 0)
    if _CACHE.get(char, (None,))[0] == key:
        return _CACHE[char][1]
    runs = []
    for p in files:
        try:
            runs.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    _CACHE[char] = (key, runs)
    return runs


def characters() -> list[dict]:
    out = []
    for c in CHARACTERS:
        runs = load_runs(c)
        live = EVOLVE / c / "live.json"
        live_age = time.time() - live.stat().st_mtime if live.exists() else None
        out.append({"name": c, "runs": len(runs), "wins": sum(r.get("victory", False) for r in runs),
                    "best": max((r["floor"] for r in runs), default=0),
                    "live": live_age is not None and live_age < 90})
    return out


def _act_reached(floor: int) -> int:
    return 1 + max(0, (floor - 1) // 17)


def run_row(r: dict) -> dict:
    """Compact row for the run list."""
    plan = r.get("plan") or {}
    return {"run": r["run"], "kind": r.get("kind"), "seed": r.get("seed"), "floor": r["floor"],
            "victory": r.get("victory", False), "killer": r.get("killer") or ("WIN" if r.get("victory") else "?"),
            "act": _act_reached(r["floor"]), "elites": r.get("elites_killed", 0), "bosses": r.get("bosses_killed", 0),
            "archetype": (plan.get("archetype") or "").split(" (")[0], "flavor": r.get("flavor"),
            "risk": r.get("risk"), "ab": r.get("ab") or {}, "deck": len(r.get("deck", [])),
            "relics": len(r.get("relics", [])), "minutes": _r(r.get("duration_s", 0) / 60),
            "started": r.get("started"), "boss_relics": [b["relic"] for b in r.get("boss_relics", [])]}


def run_detail(char: str, n: int) -> dict | None:
    p = EVOLVE / char / "runs" / f"run_{n:04d}.json"
    if not p.exists():
        return None
    r = json.loads(p.read_text(encoding="utf-8"))
    r.pop("trajectory", None)
    # fights with enemies (from fight_log) joined to their HP change
    ends = {f["floor"]: f for f in r.get("fights", [])}
    fights = []
    for fl in r.get("fight_log", []):
        e = ends.get(fl["floor"], {})
        fights.append({"floor": fl["floor"], "room": e.get("room") or fl.get("room"),
                       "enemies": sorted(set(fl["enemies"])), "hp_in": fl["hp"], "max_hp": fl["max_hp"],
                       "hp_delta": e.get("hp_delta"), "deck_size": len(fl.get("deck", []))})
    r["fights_detail"] = fights
    r.pop("fight_log", None)
    return r


def _window(xs: list, n: int) -> list:
    return [mean(xs[max(0, i - n + 1): i + 1]) for i in range(len(xs))]


def summary(char: str, since: int = 0) -> dict:
    runs = [r for r in load_runs(char) if r["run"] > since]
    if not runs:
        return {"runs": 0}
    real = [r for r in runs if not r.get("aborted")]
    n = len(real)
    floors = [r["floor"] for r in real]
    wins = [r for r in real if r.get("victory")]
    last = real[-50:]
    # throughput over the last ~day of runs
    try:
        import datetime as dt
        t0 = dt.datetime.fromisoformat(last[0]["started"])
        t1 = dt.datetime.fromisoformat(last[-1]["started"]) + dt.timedelta(seconds=last[-1].get("duration_s", 0))
        per_hr = len(last) / max(1e-6, (t1 - t0).total_seconds() / 3600)
    except Exception:
        per_hr = None
    reach = {f"act{a}": sum(f > BOSS_FLOORS[a] for f in floors) / n for a in (1, 2)}
    reach["act3"] = len(wins) / n
    reach_last = {f"act{a}": sum(r["floor"] > BOSS_FLOORS[a] for r in last) / len(last) for a in (1, 2)}
    reach_last["act3"] = sum(r.get("victory", False) for r in last) / len(last)
    # killers by act with the HP they entered with
    killers: dict = defaultdict(list)
    for r in real:
        if r.get("victory"):
            continue
        k = r.get("killer") or "?"
        hp = next((h for h in reversed(r.get("hp", [])) if h[0] < r["floor"]), None)
        killers[k].append({"floor": r["floor"], "entry_hp": (hp[1] / max(1, hp[2])) if hp else None})
    killer_rows = sorted(({"killer": k, "deaths": len(v), "act": _act_reached(round(mean(x["floor"] for x in v))),
                           "avg_floor": _r(mean(x["floor"] for x in v)),
                           "entry_hp": _r(100 * mean(x["entry_hp"] for x in v if x["entry_hp"] is not None), 0)
                           if any(x["entry_hp"] is not None for x in v) else None}
                          for k, v in killers.items()), key=lambda x: -x["deaths"])
    # experiments (explore runs)
    explore = [r for r in real if r.get("kind") == "explore"]

    def group(key_fn, label):
        g: dict = defaultdict(list)
        for r in explore:
            k = key_fn(r)
            if k is not None:
                g[k].append(r)
        return [{"label": label, "value": str(k), "runs": len(v), "avg_floor": _r(mean(x["floor"] for x in v)),
                 "act1": _r(sum(x["floor"] > 16 for x in v) / len(v), 2),
                 "act2": _r(sum(x["floor"] > 33 for x in v) / len(v), 2), "wins": sum(x.get("victory", False) for x in v)}
                for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))]

    ab_rows = []
    for key in sorted({k for r in explore for k in (r.get("ab") or {})}):
        ab_rows += group(lambda r, key=key: (("on" if r["ab"][key] else "off") if key in (r.get("ab") or {}) else None), key)
    experiments = {"ab": ab_rows,
                   "risk": group(lambda r: r.get("risk"), "risk"),
                   "flavor": group(lambda r: (r.get("flavor") or "none") if "flavor" in r else None, "flavor")}
    # boss relics and card track record
    import evidence
    ev = evidence.build(real)
    rel: dict = defaultdict(list)
    passed: dict = defaultdict(list)
    llm_picks = 0
    for r in real:
        for b in r.get("boss_relics", []):
            rel[b["relic"]].append(r["floor"] + (20 if r.get("victory") else 0))
            llm_picks += b.get("by") == "llm"
    for (k, _), v in ev.get("relic_passed", {}).items():
        passed[k] += v
    relic_rows = sorted(({"relic": k, "taken": len(v), "avg_floor": _r(mean(v)), "passed": len(passed.get(k, [])),
                          "passed_floor": _r(mean(passed[k])) if passed.get(k) else None} for k, v in rel.items()),
                        key=lambda x: -x["taken"])
    cards = []
    for (c, a), t in ev["taken"].items():
        p = ev["passed"].get((c, a), [])
        if len(t) >= 5 and len(p) >= 5:
            cards.append({"card": c, "act": a, "taken": len(t), "passed": len(p),
                          "lift": _r(mean(t) - mean(p)), "taken_floor": _r(mean(t)), "passed_floor": _r(mean(p))})
    cards.sort(key=lambda x: -x["lift"])
    # trend series
    series = [{"run": r["run"], "floor": r["floor"], "kind": r.get("kind"), "win": r.get("victory", False)} for r in real]
    blocks = []
    for i in range(0, n, 25):
        b = real[i:i + 25]
        blocks.append({"from": b[0]["run"], "to": b[-1]["run"], "runs": len(b), "avg_floor": _r(mean(r["floor"] for r in b)),
                       "act1": _r(sum(r["floor"] > 16 for r in b) / len(b), 2),
                       "act2": _r(sum(r["floor"] > 33 for r in b) / len(b), 2), "wins": sum(r.get("victory", False) for r in b)})
    shops = [s for r in last for s in r.get("shops", [])]
    model = {}
    mp = EVOLVE / char / "model.json"
    if mp.exists():
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
            model = {"runs_trained": m["meta"].get("runs_trained"), "value_runs": m["meta"].get("value_runs", 0),
                     "params": {k: _r(v, 2) for k, v in m.get("params", {}).items()}}
        except Exception:
            pass
    state = {}
    sp = EVOLVE / char / "state.json"
    if sp.exists():
        state = json.loads(sp.read_text(encoding="utf-8"))
    milestones = [
        ("First elite", next((r["run"] for r in real if r.get("elites_killed", 0) > 0), None)),
        ("Act 1 boss down", next((r["run"] for r in real if r["floor"] > 16), None)),
        ("Act 2 boss down", next((r["run"] for r in real if r["floor"] > 33), None)),
        ("First win", next((r["run"] for r in real if r.get("victory")), None)),
        ("5 wins", wins[4]["run"] if len(wins) >= 5 else None),
        ("25 wins", wins[24]["run"] if len(wins) >= 25 else None),
    ]
    return {
        "runs": n, "since": since, "wins": len(wins), "win_rate": _r(len(wins) / n, 3),
        "best_floor": max(floors), "avg_floor": _r(mean(floors)), "avg_floor_last50": _r(mean(r["floor"] for r in last)),
        "median_minutes": _r(median(r.get("duration_s", 0) for r in last) / 60), "runs_per_hr": _r(per_hr),
        "reach": {k: _r(v, 3) for k, v in reach.items()}, "reach_last50": {k: _r(v, 3) for k, v in reach_last.items()},
        "killers": killer_rows[:15], "experiments": experiments, "boss_relics": relic_rows[:15],
        "boss_relic_llm_share": _r(llm_picks / max(1, sum(len(v) for v in rel.values())), 2),
        "cards_best": cards[:12], "cards_worst": cards[-12:][::-1], "series": series,
        "rolling": [_r(x) for x in _window(floors, 20)], "blocks": blocks,
        "shops_last50": {"removals": sum(1 for s in shops if s["bought"] in ("remove a card", "purge")),
                         "buys": sum(1 for s in shops if s["bought"] not in ("(leave)", "remove a card", "purge")),
                         "visits": len({(s["floor"], i) for i, r in enumerate(last) for s in r.get("shops", [])})},
        "llm_s_per_run": _r(mean(r.get("llm_s", 0) for r in last)),
        "model": model, "champion": state.get("champion"), "milestones": [{"name": a, "run": b} for a, b in milestones],
        "recent": [run_row(r) for r in real[-12:]][::-1],
    }


def runs_list(char: str) -> list[dict]:
    return [run_row(r) for r in load_runs(char)][::-1]


def sim_data(char: str) -> dict:
    d = EVOLVE / char / "sim"
    out: dict = {}
    for name in ("last_bench", "calibration"):
        p = d / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text(encoding="utf-8"))
    p = d / "train.csv"
    if p.exists():
        with p.open(newline="", encoding="utf-8") as fh:
            out["train"] = [r for r in csv.DictReader(fh) if r.get("holdout_fit")]
    return out
