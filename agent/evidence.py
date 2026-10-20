"""evidence.py -- the bot's own track record, for the LLM's prompts.

Built from the live run files before each run. Every number compares like with like,
so survivorship doesn't fake a signal (cards taken late only appear in runs that were
already deep):

  cards        runs that TOOK a card vs runs OFFERED it in the same act that passed:
               average final floor of each group
  events       each choice of an event vs the other choices of that event
  boss relics  each relic taken vs every boss relic taken at the same act
  shop items   runs that BOUGHT an item vs runs where it was on sale and not bought

Only groups with enough samples are shown ("taken 12x -> floor 34 | passed 9x -> 26").
"""

from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean

MIN_N = 3


def _base(name: str) -> str:
    return re.sub(r"\+\d*$", "", name or "").strip()


def build(runs: list[dict]) -> dict:
    taken: dict = defaultdict(list)     # (card, act) -> final floors
    passed: dict = defaultdict(list)
    ev: dict = defaultdict(list)        # (event, choice) -> final floors
    relic: dict = defaultdict(list)     # (relic, act) -> final floors
    relic_all: dict = defaultdict(list)
    bought: dict = defaultdict(list)
    for r in runs:
        if r.get("aborted"):
            continue
        fl = r["floor"] + (20 if r.get("victory") else 0)
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
        for s in r.get("shops", []):
            if s["bought"] not in ("(leave)",):
                bought[_base(s["bought"])].append(fl)
    return {"taken": dict(taken), "passed": dict(passed), "event": dict(ev), "relic": dict(relic),
            "relic_all": dict(relic_all), "bought": dict(bought), "runs": len(runs)}


def _fmt(xs: list) -> str:
    return f"{len(xs)}x -> avg floor {mean(xs):.0f}"


def card(e: dict | None, name: str, act: int) -> str:
    """'[history: taken 12x -> avg floor 34 | passed 9x -> 26]' or '' when too few samples.
    (Wins count as floor 70 so a win weighs more than reaching the last boss.)"""
    if not e:
        return ""
    t, p = e["taken"].get((_base(name), act), []), e["passed"].get((_base(name), act), [])
    if len(t) < MIN_N or len(p) < MIN_N:
        return ""
    return f"[history act {act}: taken {_fmt(t)} | passed {_fmt(p)}]"


def event(e: dict | None, name: str, choice: str) -> str:
    if not e:
        return ""
    xs = e["event"].get((name, choice), [])
    return f"[history: chosen {_fmt(xs)}]" if len(xs) >= MIN_N else ""


def boss_relic(e: dict | None, name: str, act: int) -> str:
    if not e:
        return ""
    xs, all_ = e["relic"].get((name, act), []), e["relic_all"].get(act, [])
    if len(xs) < MIN_N or len(all_) < 2 * MIN_N:
        return ""
    return f"[history: taken {_fmt(xs)} vs all boss relics {mean(all_):.0f}]"


def shop(e: dict | None, name: str) -> str:
    if not e:
        return ""
    xs = e["bought"].get(_base(name), [])
    return f"[history: bought {_fmt(xs)}]" if len(xs) >= MIN_N else ""


NOTE = ("History numbers are this bot's own earlier runs (average final floor; a win counts as 70): "
        "prefer options with a clearly better record when the samples are decent, but small samples are noisy.")
