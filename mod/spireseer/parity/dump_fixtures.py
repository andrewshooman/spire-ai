"""Parity fixtures for the Spireseer Java brain: the Python planner (agent/planner.py) is the reference.

  effects.json  every card text in the card database (base and upgraded) -> the Python parser's Effects fields
  states.json   real combat states from the bot's protocol logs (all four classes) -> the plan the Python
                planner picks with that class's learned weights (first step, whole plan, value over ending now)

Run from the repo root:  sts-bench\\.venv\\Scripts\\python.exe mod\\spireseer\\parity\\dump_fixtures.py [per_class]
The Java side (ParityTest) reads both and reports how often it agrees.
"""
from __future__ import annotations

import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "agent"))

from sts_bench.state.schema import parse_message  # noqa: E402
from sts_bench.tools import card_db  # noqa: E402

import cards as C  # noqa: E402
import planner  # noqa: E402
from model import Model  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures"
CLASSES = {"IRONCLAD": "ironclad", "THE_SILENT": "silent", "DEFECT": "defect", "WATCHER": "watcher"}


def fx_dict(e) -> dict:
    out = {}
    for s in C.Effects.__slots__:
        v = getattr(e, s)
        if isinstance(v, bool):
            v = bool(v)
        out[s] = v
    return out


def dump_effects() -> int:
    _, by_name = card_db._index()
    rows = []
    for d in by_name.values():
        ctype = str(d.get("type") or "").upper()
        base = d["name"]
        for up, text in ((False, d.get("text")), (True, d.get("upgraded_text"))):
            if not text:
                continue
            for cost in (1, -1) if "X" in str(d.get("cost", "")) else (1,):
                e = C._effects(text, ctype, cost, base)
                rows.append({"name": base, "upgraded": up, "type": ctype, "cost": cost, "text": text, "fx": fx_dict(e)})
    (OUT / "effects.json").write_text(json.dumps(rows), encoding="utf-8")
    return len(rows)


def raw_state(msg, text_of) -> dict:
    g = msg.game_state
    c = g.combat_state
    p = c.player

    def powers(ps):
        return [{"id": q.id, "name": q.name, "amount": q.amount} for q in ps or []]

    return {
        "class": g.character, "room": g.room_type, "act": g.act, "floor": g.floor, "turn": c.turn,
        "hp": p.current_hp, "max_hp": p.max_hp, "energy": p.energy, "block": p.block, "powers": powers(p.powers),
        "orbs": [{"name": o.name, "evoke": o.evoke_amount, "passive": o.passive_amount} for o in (p.orbs or [])],
        "monsters": [{"name": m.name, "hp": m.current_hp, "max_hp": m.max_hp, "block": m.block,
                      "intent": m.intent.value, "adj": m.move_adjusted_damage, "base": m.move_base_damage,
                      "hits": m.move_hits, "gone": bool(m.is_gone), "half_dead": bool(m.half_dead),
                      "powers": powers(m.powers)} for m in c.monsters],
        "hand": [{"name": h.name, "id": h.id, "type": h.type.value, "cost": h.cost, "upgrades": h.upgrades,
                  "playable": bool(h.is_playable), "target": bool(h.has_target), "text": text_of(h)} for h in c.hand],
        "deck": [d.name for d in g.deck],
    }


def dump_states(per_class: int) -> dict:
    rng = random.Random(11)
    logs = sorted(glob.glob(str(ROOT / "sts-bench" / "logs" / "2026-*" / "play-*.log")))
    rng.shuffle(logs)
    params = {k: Model(ROOT / "evolve" / v / "model.json", k).params for k, v in CLASSES.items()}
    got = {k: [] for k in CLASSES}
    seen = set()
    for path in logs:
        if all(len(v) >= per_class for v in got.values()):
            break
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        picks = [l for l in lines if " << {" in l and '"combat_state"' in l and '"play"' in l]
        rng.shuffle(picks)
        for l in picks[:40]:
            try:
                raw = json.loads(l[l.index("{"):])
                msg = parse_message(raw)
            except Exception:
                continue
            g = msg.game_state
            cls = g.character
            if cls not in got or len(got[cls]) >= per_class or not g.combat_state or not g.combat_state.hand:
                continue
            key = (cls, g.floor, g.combat_state.turn, tuple(h.name for h in g.combat_state.hand),
                   tuple(m.current_hp for m in g.combat_state.monsters), g.combat_state.player.energy)
            if key in seen:
                continue
            seen.add(key)
            deck = [d.name for d in g.deck]
            stall = rng.choice([0, 0, 0, 0, 1, 5])
            try:
                plan = planner.plan_turn(msg, params[cls], deck, stall=stall)
            except Exception as ex:   # a state the planner can't read: skip it
                print("skip", ex)
                continue
            r = raw_state(msg, lambda h: card_db.card_text(h) or "")
            r["stall"] = stall
            r["params"] = params[cls]
            r["plan"] = {"steps": [[i, -1 if t is None else t] for i, t in plan.steps],
                         "gain": plan.value - plan.base, "value": plan.value, "base": plan.base,
                         "nodes": plan.nodes, "summary": {k: float(v) for k, v in plan.summary.items()}}
            got[cls].append(r)
    for k, v in got.items():
        (OUT / f"states_{CLASSES[k]}.json").write_text(json.dumps(v), encoding="utf-8")
    return {k: len(v) for k, v in got.items()}


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    print("effects", dump_effects())
    print("states", dump_states(int(sys.argv[1]) if len(sys.argv) > 1 else 400))
