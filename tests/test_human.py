"""Importing the player's own runs (human.py) from game history files: conversion, dedupe, learning."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import human  # noqa: E402
from model import Model  # noqa: E402


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not ok:
        raise SystemExit(1)


def fake_run(play_id: str, seed: int, win: bool = True, floor: int = 51, **extra) -> dict:
    d = {
        "play_id": play_id, "seed_played": str(seed), "character_chosen": "IRONCLAD", "victory": win,
        "floor_reached": floor, "killed_by": None if win else "The Champ", "ascension_level": 0,
        "local_time": "20261020150000", "playtime": 1800,
        "card_choices": [
            {"floor": 1, "picked": "Pommel Strike", "not_picked": ["Clash", "Wild Strike"]},
            {"floor": 2, "picked": "SKIP", "not_picked": ["Clash", "Havoc", "Flex"]},
            {"floor": 5, "picked": "Shrug It Off", "not_picked": ["Anger", "Warcry"]},
        ],
        "boss_relics": [{"picked": "SlaversCollar", "not_picked": ["Sozu", "Runic Pyramid"]}],
        "campfire_choices": [{"floor": 6, "key": "SMITH", "data": "Bash"}, {"floor": 15, "key": "REST"}],
        "items_purchased": ["Inflame", "Vajra"], "item_purchase_floors": [3, 3],
        "items_purged": ["Strike_R"], "items_purged_floors": [3],
        "event_choices": [{"floor": 4, "event_name": "Big Fish", "player_choice": "Banana", "cards_removed": []}],
        "relics_obtained": [{"floor": 9, "key": "Anchor"}],
        "path_per_floor": ["M", "M", "$", "?", "M", "R", "M", "E", "T", "M", "M", "E", "M", "M", "R", "B", None],
        "current_hp_per_floor": [75, 70, 70, 72, 60, 80, 70, 50, 50, 45, 40, 30, 28, 28, 52, 40],
        "max_hp_per_floor": [80] * 16, "gold_per_floor": [110] * 16,
        "damage_taken": [{"floor": 16, "enemies": "Hexaghost", "damage": 30, "turns": 6}],
        "master_deck": ["Strike_R"] * 4 + ["Defend_R"] * 4 + ["Bash+1", "Pommel Strike", "Shrug It Off", "Inflame"],
        "relics": ["Burning Blood", "Vajra", "Anchor", "SlaversCollar"],
    }
    d.update(extra)
    return d


def main() -> None:
    check("seed strings map to the history files' numbers", human.seed_number("0") == 0 and human.seed_number("10") == 35)
    m = Model(Path(tempfile.mkdtemp()) / "m.json", "IRONCLAD")
    d = fake_run("p1", 123)
    rec, events, reward = human.convert(d, "ironclad", m)
    kinds = [e["kind"] for e in events]
    check("decisions rebuilt: 3 picks, 1 boss relic, 2 rests", kinds.count("card") == 3 and kinds.count("boss") == 1
          and kinds.count("rest") == 2, str(kinds))
    check("skip recorded as the skip option", events[1]["chosen"] == len(events[1]["options"]) - 1)
    check("boss relic ids become names", rec["boss_relics"][0]["relic"] == "Slaver's Collar", str(rec["boss_relics"]))
    tl = human.Timeline(d, "IRONCLAD")
    tl.advance(999)
    check("deck rebuilt from picks, shop, removal and smith",
          sorted(tl.deck) == sorted(d["master_deck"]), str(sorted(tl.deck)))
    check("a win is the top reward", reward >= 1.0, f"{reward:.2f}")
    flt = fake_run("p2", 124)
    for x in flt["card_choices"] + flt["campfire_choices"]:
        x["floor"] = float(x["floor"])   # the game wrote floors as 1.0 in a real run
    flt["item_purchase_floors"] = [3.0, 3.0]
    check("floors written as decimals still import", len(human.convert(flt, "ironclad", m)[1]) == 6)

    # import from an inbox; bot runs (seed match) and dailies are skipped; nothing from the bot's save slot
    tmp = Path(tempfile.mkdtemp())
    human.EVOLVE = tmp
    human.game_dir = lambda: None
    inbox = tmp / "human_inbox" / "IRONCLAD"
    inbox.mkdir(parents=True)
    bot_seed = "ABC123"
    (tmp / "ironclad" / "runs").mkdir(parents=True)
    (tmp / "ironclad" / "runs" / "run_0001.json").write_text(json.dumps({"run": 1, "seed": bot_seed, "floor": 10}))
    (inbox / "a.run").write_text(json.dumps(fake_run("h1", 999)))
    (inbox / "b.run").write_text(json.dumps(fake_run("h2", 1000, win=False, floor=33)))
    (inbox / "c.run").write_text(json.dumps(fake_run("bot", human.seed_number(bot_seed))))
    (inbox / "d.run").write_text(json.dumps(fake_run("daily", 5, is_daily=True)))
    c = human.sync("ironclad", m, say=lambda *_: None)
    check("imports only the player's runs", c["new"] == 2 and c["bot"] == 1 and c["special"] == 1, str(c))
    again = human.sync("ironclad", m, say=lambda *_: None)
    check("already imported runs are not imported twice", again["new"] == 0)
    items = human.take_pending("ironclad")
    check("two runs queued for learning, queue cleared", len(items) == 2 and not human.take_pending("ironclad"))
    m.meta["act_baseline"] = {str(a): {"b": 0.6, "var": 0.04} for a in (1, 2, 3)}  # a bot that has played a while
    before = m.w("card:Pommel Strike")
    for it in items:
        m.learn(it["events"], it["reward"], lr=0.02, replay=True)
    check("learning from your runs moves the weights", m.w("card:Pommel Strike") != before)
    check("human runs stored apart from the bot's", len(human.runs("ironclad")) == 2
          and len(list((tmp / "ironclad" / "runs").glob("*.json"))) == 1)
    print("HUMAN OK")


if __name__ == "__main__":
    main()
