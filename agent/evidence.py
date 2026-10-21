"""evidence.py -- the bot's own track record, for the LLM's prompts.

Built from the live run files before each run. Every number compares like with like,
so survivorship doesn't fake a signal (cards taken late only appear in runs that were
already deep):

  cards        runs that TOOK a card vs runs OFFERED it in the same act that passed:
               how often each group beat the act 2 boss, and how many won
  events       each choice of an event vs the other choices of that event
  boss relics  each relic taken vs every boss relic taken at the same act
  shop items   runs that BOUGHT an item vs runs where it was on sale and not bought

Only groups with enough samples are shown ("taken 12x -> act 2 boss 33%, 1 win | passed 9x -> 11%").
Floors aren't the measure: a choice that reaches the act 2 boss only to lose there isn't a better one.
"""

from __future__ import annotations

import re
from collections import defaultdict

MIN_N = 3


def _base(name: str) -> str:
    return re.sub(r"\+\d*$", "", name or "").strip()


def build(runs: list[dict]) -> dict:
    taken: dict = defaultdict(list)     # (card, act) -> final floors
    passed: dict = defaultdict(list)
    ev: dict = defaultdict(list)        # (event, choice) -> final floors
    relic: dict = defaultdict(list)     # (relic, act) -> final floors
    relic_all: dict = defaultdict(list)
    relic_passed: dict = defaultdict(list)   # (relic, act) -> final floors of runs offered it that took another
    bought: dict = defaultdict(list)
    for r in runs:
        if r.get("aborted"):
            continue
        # outcome per run: 2 = won, 1 = beat the act 2 boss, 0 = neither (floors reached don't count)
        fl = 2 if r.get("victory") else 1 if r["floor"] >= 34 else 0
        for p in r.get("picks", []):
            act = 1 + max(0, (p["floor"] - 1) // 17)
            chosen = _base(p["chosen"])
            for o in p["offered"]:
                if o == "(skip)":
                    continue
                (taken if _base(o) == chosen else passed)[(_base(o), act)].append(fl)
        for e in r.get("events", []):
            ev[(e["event"], e["choice"])].append(fl)
        for b in r.get("boss_relics", []):
            act = 1 + max(0, (b["floor"] - 1) // 17)
            relic[(b["relic"], act)].append(fl)
            relic_all[act].append(fl)
            for o in b.get("offered", []):
                if o != b["relic"]:
                    relic_passed[(o, act)].append(fl)
        for s in r.get("shops", []):
            if s["bought"] not in ("(leave)",):
                bought[_base(s["bought"])].append(fl)
    return {"taken": dict(taken), "passed": dict(passed), "event": dict(ev), "relic": dict(relic),
            "relic_all": dict(relic_all), "relic_passed": dict(relic_passed), "bought": dict(bought),
            "runs": len(runs)}


def _pct(xs: list) -> str:
    return f"{sum(x >= 1 for x in xs) / len(xs):.0%}"


def _fmt(xs: list) -> str:
    wins = sum(x == 2 for x in xs)
    return f"{len(xs)}x -> act 2 boss {_pct(xs)}" + (f", {wins} win{'s' if wins > 1 else ''}" if wins else "")


def card(e: dict | None, name: str, act: int) -> str:
    """'[history act 1: taken 12x -> act 2 boss 33% | passed 9x -> act 2 boss 11%]' or '' when too few samples."""
    if not e:
        return ""
    t, p = e["taken"].get((_base(name), act), []), e["passed"].get((_base(name), act), [])
    if len(t) < MIN_N or len(p) < MIN_N:
        return ""
    return f"[history act {act}: taken {_fmt(t)} | passed {_fmt(p)}]"


def mine(text: str) -> str:
    """Relabel a track-record note as coming from the player's own runs (human.py)."""
    return text.replace("[history", "[human player's runs", 1) if text else ""


def expert(text: str) -> str:
    """Relabel a track-record note as coming from other strong players' runs (import_runs.bat --expert)."""
    return text.replace("[history", "[expert A20 players", 1) if text else ""


def event(e: dict | None, name: str, choice: str) -> str:
    if not e:
        return ""
    xs = e["event"].get((name, choice), [])
    return f"[history: chosen {_fmt(xs)}]" if len(xs) >= MIN_N else ""


def boss_relic(e: dict | None, name: str, act: int) -> str:
    if not e:
        return ""
    xs, all_ = e["relic"].get((name, act), []), e["relic_all"].get(act, [])
    passed = e.get("relic_passed", {}).get((name, act), [])
    if len(xs) < MIN_N or len(all_) < 2 * MIN_N:
        return ""
    if len(passed) >= MIN_N:  # like with like: runs offered this relic that took another one
        return f"[history: taken {_fmt(xs)} | passed {_fmt(passed)}]"
    return f"[history: taken {_fmt(xs)} vs all boss relics {_pct(all_)}]"


def shop(e: dict | None, name: str) -> str:
    if not e:
        return ""
    xs = e["bought"].get(_base(name), [])
    return f"[history: bought {_fmt(xs)}]" if len(xs) >= MIN_N else ""


NOTE = ("History numbers are this bot's own earlier runs: how often runs that made that choice beat the act 2 "
        "boss, and how many won (the goal is winning runs, not reaching later floors): "
        "prefer options with a clearly better record when the samples are decent, but small samples are noisy. "
        "'human player's runs' numbers come from an experienced human playing the same character: what they "
        "take often is a good example to follow. 'expert A20 players' numbers come from strong players' runs at the "
        "highest difficulty (about half of them wins): their picks are good examples, their floors run deeper.")
