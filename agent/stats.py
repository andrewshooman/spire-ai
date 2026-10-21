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


def human_summary(char: str) -> dict:
    """Runs the player played (human.py imports), next to the bot's numbers."""
    import human
    rs = human.runs(char)
    bot = [r for r in load_runs(char) if not r.get("aborted")][-100:]

    def reach(xs):
        n = max(1, len(xs))
        return {"act1": _r(sum(r["floor"] > 16 for r in xs) / n, 2), "act2": _r(sum(r["floor"] > 33 for r in xs) / n, 2),
                "win": _r(sum(r.get("victory", False) for r in xs) / n, 2), "avg_floor": _r(mean(r["floor"] for r in xs)) if xs else None}
    out = {"runs": len(rs), "bot": reach(bot), "bot_runs": len(bot)}
    mp = EVOLVE / char / "model.json"
    try:
        out["learned"] = json.loads(mp.read_text(encoding="utf-8"))["meta"].get("human_runs_learned", 0)
    except Exception:
        out["learned"] = 0
    if not rs:
        return out
    picks = Counter(re.sub(r"[+]+$", "", p["chosen"]) for r in rs for p in r.get("picks", []))
    total = sum(picks.values())
    out.update({
        "you": reach(rs), "wins": sum(r.get("victory", False) for r in rs),
        "skip_rate": _r(picks.get("(skip)", 0) / max(1, total), 2),
        "bot_skip_rate": _r(sum(p["chosen"] == "(skip)" for r in bot for p in r.get("picks", []))
                            / max(1, sum(len(r.get("picks", [])) for r in bot)), 2),
        "top_picks": [[k, v] for k, v in picks.most_common(12) if k != "(skip)"][:10],
        "boss_relics": Counter(b["relic"] for r in rs for b in r.get("boss_relics", [])).most_common(8),
        "killers": Counter(r.get("killer") for r in rs if not r.get("victory")).most_common(5),
        "recent": [{"floor": r["floor"], "victory": r.get("victory"), "killer": r.get("killer"),
                    "started": r.get("started"), "ascension": r.get("ascension", 0)} for r in rs[-10:]][::-1],
    })
    return out


# ---------------------------------------------------------------- best cards and synergies (Overview)

# synergy rules come in mirrored pairs (engine -> payoff and back): one named synergy each
SYN_GROUP = {
    "strength->multi": "Strength + multi-hit attacks", "multi->strength": "Strength + multi-hit attacks",
    "strength->payoff": "Strength + Strength payoffs (Heavy Blade, Limit Break...)",
    "exhaust->payoff": "Exhaust engine (Feel No Pain, Dark Embrace...)", "payoff->exhaust": "Exhaust engine (Feel No Pain, Dark Embrace...)",
    "payoff->bulk": "Exhaust engine (Feel No Pain, Dark Embrace...)", "exhume->exhaust": "Exhaust engine (Feel No Pain, Dark Embrace...)",
    "corruption->skills": "Corruption + skills",
    "block->barricade": "Block engine (Barricade, Body Slam...)", "barricade->payoff": "Block engine (Barricade, Body Slam...)",
    "selfdmg->payoff": "Self-damage + payoffs (Rupture...)", "payoff->selfdmg": "Self-damage + payoffs (Rupture...)",
    "status->payoff": "Status cards + payoffs (Evolve, Fire Breathing)", "payoff->status": "Status cards + payoffs (Evolve, Fire Breathing)",
    "vuln->payoff": "Vulnerable + payoffs", "strikes->perfected": "Strikes + Perfected Strike",
    "topdeck->bigcards": "Topdeck + key cards", "copy->powers": "Copy + powers / big hits",
    "doubletap->attacks": "Double Tap + big attacks", "xcost->energy": "X-cost + extra energy",
    "searing->upgrades": "Searing Blow + upgrades",
    "poison->payoff": "Poison + payoffs (Catalyst, Bane...)", "payoff->poison": "Poison + payoffs (Catalyst, Bane...)",
    "shiv->payoff": "Shivs + payoffs (Accuracy, Finisher...)", "payoff->shiv": "Shivs + payoffs (Accuracy, Finisher...)",
    "discard->payoff": "Discard + payoffs (Reflex, Tactician...)", "payoff->discard": "Discard + payoffs (Reflex, Tactician...)",
}


def _shrunk(k: int, n: int, p0: float, a: float = 10.0) -> float:
    """Success rate pulled toward the overall rate, so 3 lucky runs can't top the list."""
    return (k + a * p0) / (n + a)


def _active_rules(deck: list[str], relics: list[str]) -> dict[str, list[str]]:
    """Named synergies a deck has working (a payoff card plus at least one enabler) -> the cards involved."""
    import synergy
    have = synergy.deck_tags(deck, relics)
    out = {}
    for rule, tag, feeders, _, _ in synergy.RULES:
        if have[tag] and any(have[f] for f in feeders):
            who = [synergy.base(c) for c in deck if synergy.base(c) not in synergy.STARTERS
                   and (tag in synergy.card_tags(c) or synergy.card_tags(c) & feeders)]
            out.setdefault(SYN_GROUP.get(rule, rule), []).extend(who)
    return out


def best(char: str) -> dict:
    """Overview: the cards and synergies that go with WINNING (beating the act 2 boss, winning), not with
    going deep. Cards: runs that took a card vs runs offered it that passed (same act). Synergies: decks that
    had one working at the start of act 2 vs decks that didn't, for the bot and for the expert runs."""
    import evidence
    real = [r for r in load_runs(char) if not r.get("aborted")]
    out: dict = {"runs": len(real), "cards": [], "synergies": []}
    if not real:
        return out
    weights = {}
    try:
        weights = json.loads((EVOLVE / char / "model.json").read_text(encoding="utf-8")).get("weights", {})
    except Exception:
        pass
    expert_pick = {}
    try:
        import human
        exp = human.runs(char, "expert")
        off, tak = Counter(), Counter()
        for r in exp:
            for p in r.get("picks", []):
                for o in p.get("offered", []):
                    if o != "(skip)":
                        off[re.sub(r"[+]+$", "", o)] += 1
                if p.get("chosen") not in (None, "(skip)"):
                    tak[re.sub(r"[+]+$", "", p["chosen"])] += 1
        expert_pick = {c: tak[c] / off[c] for c in off if off[c] >= 8}
    except Exception:
        exp = []

    # cards: an offer in acts 1-2 succeeds when the run beat the act 2 boss; in act 3 when it won
    ev = evidence.build(real)
    agg: dict = defaultdict(lambda: [0, 0, 0, 0])   # card -> [taken ok, taken n, passed ok, passed n]
    for key, slot in (("taken", 0), ("passed", 2)):
        for (c, act), xs in ev[key].items():
            ok = sum((x == 2) if act >= 3 else (x >= 1) for x in xs)
            agg[c][slot] += ok
            agg[c][slot + 1] += len(xs)
    tot_ok = sum(v[0] + v[2] for v in agg.values())
    tot_n = sum(v[1] + v[3] for v in agg.values())
    p0 = tot_ok / max(1, tot_n)
    rows = []
    for c, (tk, tn, pk, pn) in agg.items():
        if tn >= 6 and pn >= 6:
            lift = _shrunk(tk, tn, p0) - _shrunk(pk, pn, p0)
            rows.append({"card": c, "taken": tn, "taken_rate": _r(tk / tn, 2), "passed": pn,
                         "passed_rate": _r(pk / pn, 2), "lift": _r(lift, 3),
                         "weight": _r(weights.get(f"card:{c}"), 2) if f"card:{c}" in weights else None,
                         "expert_pick": _r(expert_pick[c], 2) if c in expert_pick else None})
    rows.sort(key=lambda x: -x["lift"])
    out["cards"] = rows[:12]
    out["cards_worst"] = rows[-5:][::-1] if len(rows) > 12 else []
    out["card_base"] = _r(p0, 3)

    # synergies at the start of act 2: does having it go with beating the act 2 boss?
    def tally(decks):   # [(deck, relics, success)]
        t: dict = defaultdict(lambda: [0, 0])
        cards_by: dict = defaultdict(Counter)
        for deck, relics, okk in decks:
            act = _active_rules(deck, relics)
            for rule, who in act.items():
                t[rule][0] += okk
                t[rule][1] += 1
                cards_by[rule].update(set(who))
        return t, cards_by, sum(o for _, _, o in decks), len(decks)
    bot_decks = []
    for r in real:
        f = next((x for x in r.get("fight_log", []) if 18 <= x["floor"] <= 20), None)
        if f:
            bot_decks.append((f["deck"], f.get("relics", []), int(r["floor"] >= 34 or bool(r.get("victory")))))
    exp_decks = []
    try:
        import human
        for r in exp:
            raw = json.loads(Path(r["file"]).read_text(encoding="utf-8", errors="replace"))
            if raw.get("floor_reached", 0) < 18:
                continue
            tl = human.Timeline(raw, human.GAME_CHAR[char])
            tl.advance(18)
            exp_decks.append(([human.card_from_id(c).name for c in tl.deck], list(tl.relics), int(bool(raw.get("victory")))))
    except Exception:
        exp_decks = []
    bt, bcards, bok, bn = tally(bot_decks)
    et, ecards, eok, en = tally(exp_decks)
    syn = []
    for rule in set(bt) | set(et):
        k, n = bt.get(rule, [0, 0])
        if n < 5 and et.get(rule, [0, 0])[1] < 8:
            continue
        ws = [weights[f"synrule:{x}"] for x, g in SYN_GROUP.items() if g == rule and f"synrule:{x}" in weights]
        row = {"label": rule,
               "cards": [c for c, _ in (bcards[rule] + ecards[rule]).most_common(4)],
               "decks": n, "rate": _r(k / n, 2) if n else None,
               "rate_without": _r((bok - k) / (bn - n), 2) if bn - n > 0 else None,
               "lift": _r(_shrunk(k, n, bok / max(1, bn)) - _shrunk(bok - k, bn - n, bok / max(1, bn)), 3) if n else None,
               "weight": _r(mean(ws), 2) if ws else None}
        ek, enn = et.get(rule, [0, 0])
        if enn >= 8:
            row.update({"expert_decks": enn, "expert_win": _r(ek / enn, 2),
                        "expert_win_without": _r((eok - ek) / (en - enn), 2) if en - enn > 0 else None})
        syn.append(row)
    syn.sort(key=lambda x: -(x["lift"] if x["lift"] is not None else -1))
    out["synergies"] = syn[:12]
    out["bot_decks"], out["bot_rate"] = bn, _r(bok / max(1, bn), 2)
    out["expert_decks"], out["expert_win"] = en, _r(eok / max(1, en), 2)
    return out


# ---------------------------------------------------------------- Model tab: what it learned, how it trains

TRACKED = ("rest:h1", "smith:h1", "restboss:h1", "skip:a1", "skip:a2", "expertpick", "cardplay", "cardfight",
           "cardpair", "relicfit:boss", "node:E:h2", "node:M:h1")


def model_view(char: str) -> dict:
    """Everything the Model tab draws: learned weights by kind, their path over the training cycles, the
    combat weights vs their starting values, the score per run, and the card pairs played together."""
    import sys
    sys.path.insert(0, str(HERE))
    from model import COMBAT_DEFAULTS, Model
    mp = EVOLVE / char / "model.json"
    if not mp.exists():
        return {"ok": False}
    m = Model(mp, char.upper())
    w = m.weights

    def top(prefix: str, n: int, strip: bool = True) -> list:
        rows = [(k[len(prefix):] if strip else k, round(v, 2)) for k, v in w.items() if k.startswith(prefix)]
        rows.sort(key=lambda r: -r[1])
        if len(rows) <= 2 * n:
            return rows
        return rows[:n] + rows[-n:]

    rooms = ["M", "E", "R", "$", "?", "T"]
    node = [[round(m.w(f"node:{r}:h{b}"), 2) for b in (0, 1, 2)] for r in rooms]
    nodeact = [[round(m.w(f"nodeact:{r}:a{a}"), 2) for a in (1, 2, 3)] for r in rooms]
    import cards as C
    bosses = list(C.BOSS_NEEDS)
    roles = list(C.BOSS_ROLES)
    bossneed = [[round(m.w(f"bossneed:{b}:{r}"), 2) for r in roles] for b in bosses]
    rest = {k: round(m.w(k), 2) for k in (f"{a}:h{b}" for a in ("rest", "smith", "restboss", "smithboss") for b in (0, 1, 2))}
    scalars = {k: round(m.w(k), 2) for k in ("expertpick", "expertrelic", "cardplay", "cardfight", "cardpair",
                                              "relicfit:boss", "upgraded", "dup", "dilute", "need:aoe", "need:block",
                                              "need:damage", "need:draw", "need:scaling")}
    combat = [{"name": k, "learned": round(v, 3), "start": COMBAT_DEFAULTS.get(k)} for k, v in m.params.items()
              if k in COMBAT_DEFAULTS and k != "energy_cost"]
    # weights across the training cycles (a model snapshot is kept every cycle)
    hist = []
    for hp in sorted((EVOLVE / char / "history").glob("model_c*.json")):
        try:
            hm = json.loads(hp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hw = hm.get("weights", {})
        hist.append({"cycle": int(re.sub(r"\D", "", hp.stem) or 0), "runs": hm.get("meta", {}).get("runs_trained"),
                     "w": {k: round(hw[k], 2) if k in hw else None for k in TRACKED}})
    # score per run (the win-focused reward) and temperature
    runs = [r for r in load_runs(char) if not r.get("aborted")]
    series = [{"run": r["run"], "score": Model.outcome(r["floor"], bool(r.get("victory"))), "kind": r.get("kind"),
               "win": bool(r.get("victory")), "temp": r.get("temperature"), "floor": r["floor"]} for r in runs]
    # card pairs the planner plays together (cardutil): lift over their separate play rates
    pairs = []
    try:
        import cardutil
        st = cardutil.stats(char)
        files = sorted((EVOLVE / char / "carduse").glob("run_*.json"))[-cardutil.RECENT:]
        seen = Counter()
        for fp in files:
            seen.update(json.loads(fp.read_text(encoding="utf-8"))["pair_seen"])
        basics = {"Strike", "Defend"}
        for k, lift in sorted(st.get("pair", {}).items(), key=lambda kv: -kv[1]):
            a, b = k.split("|")
            if a in basics or b in basics:
                continue   # pairs with a basic card are mostly "both were in hand"
            pairs.append({"a": a, "b": b, "lift": lift, "turns": seen.get(k, 0)})
            if len(pairs) >= 15:
                break
        play = sorted(st.get("play", {}).items(), key=lambda kv: -kv[1])
        playrate = {"high": play[:8], "low": play[-8:][::-1], "base": st.get("base_play")}
    except Exception:
        playrate = {}
    meta = m.meta
    return {
        "ok": True, "char": char,
        "cards": top("card:", 12), "synrule": top("synrule:", 8), "relics": top("relic:", 8),
        "rooms": rooms, "node": node, "nodeact": nodeact, "bosses": bosses, "roles": roles, "bossneed": bossneed,
        "rest": rest, "scalars": scalars, "combat": combat, "history": hist, "tracked": list(TRACKED),
        "series": series, "pairs": pairs, "playrate": playrate,
        "training": {"runs_trained": meta.get("runs_trained"), "weights": len(w), "value_runs": meta.get("value_runs"),
                     "temperature": round(m.temperature(), 3), "reward_version": meta.get("reward_version"),
                     "expert_learned": meta.get("expert_runs_learned", 0), "human_learned": meta.get("human_runs_learned", 0),
                     "cycles": len(hist)},
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
