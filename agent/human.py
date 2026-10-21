"""human.py -- learn from runs YOU played (PC or Steam Deck), kept apart from the bot's stats.

The game writes a history file for every finished run (SlayTheSpire/runs/[<slot>_]<CHARACTER>/*.run):
card rewards offered/picked, boss relics, rest sites, events, shop buys, HP/gold per floor, the path,
the result. Combat isn't in there, so these runs teach the out-of-combat choices only.

    sync(char)        import new .run files (other save slots than the bot's + evolve/human_inbox/<CHAR>/),
                      store each as evolve/<char>/human/runs/<play_id>.json (bot run format, kind "human")
                      and queue its decisions in evolve/<char>/human/pending.jsonl
    pending(char)     queued (events, reward) for the bot to learn from (evolve.py, before each run)
    runs(char)        imported human runs (evidence + web app)

import_runs.bat [folder]  copies .run files from a folder (e.g. one copied from the Steam Deck) into the
inbox and imports them. The bot's own save slot (BOT_SLOT, default 0) is never imported, and runs whose
seed matches a bot run are skipped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cards as C  # noqa: E402
import gamedata  # noqa: E402
import synergy  # noqa: E402
from model import Model, hp_bucket  # noqa: E402

EVOLVE = HERE.parent / "evolve"
GAME_CHAR = {"ironclad": "IRONCLAD", "silent": "THE_SILENT", "defect": "DEFECT", "watcher": "WATCHER"}
STARTER = {"IRONCLAD": (["Strike_R"] * 5 + ["Defend_R"] * 4 + ["Bash"], ["Burning Blood"]),
           "THE_SILENT": (["Strike_G"] * 5 + ["Defend_G"] * 5 + ["Neutralize", "Survivor"], ["Ring of the Snake"]),
           "DEFECT": (["Strike_B"] * 4 + ["Defend_B"] * 4 + ["Zap", "Dualcast"], ["Cracked Core"]),
           "WATCHER": (["Strike_P"] * 4 + ["Defend_P"] * 4 + ["Eruption", "Vigilance"], ["Pure Water"])}
# where imported runs live: 'human' = runs you played, 'expert' = other players' run files (import_runs.bat --expert)
SOURCES = ("human", "expert")
BOSS_NAMES = {"Champ": "The Champ", "Collector": "The Collector", "Automaton": "Bronze Automaton"}
SEED_CHARS = "0123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


def bot_slot() -> int:
    try:
        return int(os.environ.get("BOT_SLOT", "0"))
    except ValueError:
        return 0


def seed_number(seed: str) -> int:
    """The game's seed string -> the signed 64-bit number in run history files."""
    n = 0
    for ch in seed.upper():
        n = (n * 35 + SEED_CHARS.index(ch)) & (2 ** 64 - 1)
    return n - 2 ** 64 if n >= 2 ** 63 else n


def game_dir() -> Path | None:
    jar = gamedata.find_jar()
    return jar.parent if jar else None


def run_dirs(char: str) -> list[tuple[Path, str]]:
    """Run history folders to import from: every save slot except the bot's, plus the inbox."""
    out = []
    g = game_dir()
    if g and (g / "runs").is_dir():
        # the first profile writes runs/IRONCLAD, profile n writes runs/<n>_IRONCLAD
        for d in sorted((g / "runs").iterdir()):
            m = re.fullmatch(r"(?:(\d+)_)?" + GAME_CHAR[char], d.name)
            if m and d.is_dir():
                slot = int(m.group(1) or 0)
                if slot != bot_slot():
                    out.append((d, f"profile slot {slot}"))
    inbox = EVOLVE / "human_inbox" / GAME_CHAR[char]
    if inbox.is_dir():
        out.append((inbox, "inbox"))
    return out


# ---------------------------------------------------------------- cards from run-file ids

def card_from_id(cid: str):
    """'Bash+1' / 'Strike_R' / 'Pommel Strike' -> a card object the policy's feature code understands."""
    from sts_bench.tools import card_db
    m = re.fullmatch(r"(.*?)(?:\+(\d+))?", cid)
    base_id, ups = m.group(1), int(m.group(2) or 0)
    by_id, by_name = card_db._index()
    entry = by_id.get(base_id.upper().replace(" ", "_")) or by_id.get(card_db._squash(base_id)) \
        or by_name.get(base_id.lower())
    name = entry["name"] if entry else base_id
    ctype = (entry["type"] if entry else "Attack").upper()
    cost = synergy.CARD_COST.get(name, -2 if ctype in ("CURSE", "STATUS") else 1)
    return NS(name=name + ("+" * min(ups, 1) if ups else ""), id=base_id, cost=cost, upgrades=ups,
              type=NS(value=ctype), ethereal=False, exhausts=False, rarity="COMMON", is_playable=True)


def _is_card(cid: str) -> bool:
    from sts_bench.tools import card_db
    base_id = re.sub(r"\+\d+$", "", cid)
    by_id, by_name = card_db._index()
    return bool(by_id.get(base_id.upper().replace(" ", "_")) or by_id.get(card_db._squash(base_id))
                or by_name.get(base_id.lower()))


# ---------------------------------------------------------------- one run -> record + decisions

def normalize(d: dict) -> dict:
    """Some game builds write floors as 1.0: make every floor an int."""
    def fix(x):
        if isinstance(x, dict):
            return {k: (int(v) if k == "floor" and isinstance(v, (int, float)) else fix(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [fix(v) for v in x]
        return x
    d = fix(d)
    for k in ("item_purchase_floors", "items_purged_floors", "potions_floor_usage", "potions_floor_spawned"):
        if isinstance(d.get(k), list):
            d[k] = [int(v) for v in d[k] if isinstance(v, (int, float))]
    if isinstance(d.get("floor_reached"), float):
        d["floor_reached"] = int(d["floor_reached"])
    return d


class Timeline:
    """Deck and relics as they were at each floor, rebuilt from the history file."""

    def __init__(self, d: dict, gchar: str):
        d = normalize(d)
        deck, relics = STARTER.get(gchar, ([], []))
        self.deck = list(deck) + (["AscendersBane"] if d.get("ascension_level", 0) >= 10 else [])
        self.relics = list(relics)
        self.changes: dict[int, list] = {}
        add = lambda f, *op: self.changes.setdefault(int(f), []).append(op)
        for p in d.get("card_choices", []):
            if p.get("picked") not in (None, "SKIP", "Singing Bowl"):
                add(p["floor"], "card+", p["picked"])
        for item, f in zip(d.get("items_purchased", []), d.get("item_purchase_floors", [])):
            if _is_card(item):
                add(f, "card+", item)
            elif gamedata.relic_name(item):
                add(f, "relic+", gamedata.relic_name(item))
        for item, f in zip(d.get("items_purged", []), d.get("items_purged_floors", [])):
            add(f, "card-", item)
        for c in d.get("campfire_choices", []):
            if c.get("key") == "SMITH" and c.get("data"):
                add(c["floor"], "up", c["data"])
            elif c.get("key") == "PURGE" and c.get("data"):   # Peace Pipe
                add(c["floor"], "card-", c["data"])
        for e in d.get("event_choices", []):
            for x in e.get("cards_removed", []) + e.get("cards_transformed", []):
                add(e["floor"], "card-", x)
            for x in e.get("cards_obtained", []):
                add(e["floor"], "card+", x)
            for x in e.get("cards_upgraded", []):
                add(e["floor"], "up", x)
            for x in e.get("relics_obtained", []):
                add(e["floor"], "relic+", gamedata.relic_name(x) or x)
            for x in e.get("relics_lost", []):
                add(e["floor"], "relic-", gamedata.relic_name(x) or x)
        for r in d.get("relics_obtained", []):
            add(r["floor"], "relic+", gamedata.relic_name(r["key"]) or r["key"])
            if r["key"] == "Necronomicon":
                add(r["floor"], "card+", "Necronomicurse")
        # Neow (Run History Plus writes neow_bonus_log): removals, transforms, cards, upgrades at floor 0
        neow = d.get("neow_bonus_log") or {}
        for x in neow.get("cardsRemoved", []) + neow.get("cardsTransformed", []):
            add(0, "card-", x)
        for x in neow.get("cardsObtained", []):
            add(0, "card+", x)
        for x in neow.get("cardsUpgraded", []):
            add(0, "up", x)
        for x in neow.get("relicsObtained", []):
            add(0, "relic+", gamedata.relic_name(x) or x)
        for i, b in enumerate(d.get("boss_relics", [])):
            if b.get("picked"):
                add(16 + 17 * i, "relic+", gamedata.relic_name(b["picked"]) or b["picked"])
                if b["picked"] == "Calling Bell":
                    add(16 + 17 * i, "card+", "CurseOfTheBell")
                elif b["picked"] in ("Empty Cage", "Astrolabe"):
                    # the file doesn't say which cards: strong players almost always take Strikes first
                    add(16 + 17 * i, "basic-", 2 if b["picked"] == "Empty Cage" else 3)
                elif b["picked"] == "Pandora's Box":
                    add(16 + 17 * i, "basics-", "")   # every Strike and Defend becomes a random card
        self.applied = -1

    def advance(self, floor: int) -> None:
        """Apply every change from floors before `floor`."""
        for f in sorted(k for k in self.changes if self.applied < k < floor):
            for op in self.changes[f]:
                kind, x = op[0], op[1]
                if kind == "card+":
                    self.deck.append(x)
                elif kind == "card-":
                    base = re.sub(r"\+\d+$", "", x)
                    hit = next((c for c in self.deck if c == x), None) or \
                        next((c for c in self.deck if re.sub(r"\+\d+$", "", c) == base), None)
                    if hit:
                        self.deck.remove(hit)
                elif kind == "up":
                    base = re.sub(r"\+\d+$", "", x)
                    for i, c in enumerate(self.deck):
                        if c == base:
                            self.deck[i] = base + "+1"
                            break
                elif kind == "basic-":
                    for _ in range(x):
                        hit = next((c for c in self.deck if re.match(r"Strike_[RGBP]$", c)), None) or                             next((c for c in self.deck if re.match(r"Defend_[RGBP]$", c)), None)
                        if hit:
                            self.deck.remove(hit)
                elif kind == "basics-":
                    self.deck = [c for c in self.deck if not re.match(r"(Strike|Defend)_[RGBP]$", re.sub(r"\+\d+$", "", c))]
                elif kind == "relic+":
                    self.relics.append(x)
                elif kind == "relic-" and x in self.relics:
                    self.relics.remove(x)
            self.applied = f


def _boss_for(d: dict, act: int) -> str:
    fl = {1: 16, 2: 33, 3: 50}.get(act)
    e = next((x["enemies"] for x in d.get("damage_taken", []) if x["floor"] == fl), "")
    return BOSS_NAMES.get(e, e)


def convert(d: dict, char: str, model: Model, source: str = "human") -> tuple[dict, list[dict], float]:
    """A .run file -> (run record in the bot's format, learning events, reward)."""
    import policy  # late: policy imports a lot
    d = normalize(d)
    gchar = GAME_CHAR[char]
    hp = d.get("current_hp_per_floor", [])
    mx = d.get("max_hp_per_floor", [])
    gold = d.get("gold_per_floor", [])
    path = d.get("path_per_floor", [])
    at = lambda arr, f, default: arr[f - 1] if 0 < f <= len(arr) and arr[f - 1] is not None else default
    tl = Timeline(d, gchar)
    # expert_features off: features built from expert runs must not be learned from those same runs
    policy.CONFIG = policy.Config(model=model, tracker=policy.RunTracker(), learn=False, llm_events=False,
                                  expert_features=False)
    agent = policy.PolicyAgent()
    empty = NS(id="Potion Slot", name="Potion Slot")

    def state(f: int, hp_floor: int) -> NS:
        tl.advance(f)
        act = 1 + max(0, (f - 1) // 17)
        policy.CONFIG.tracker.path = [{"floor": i + 1, "node": n} for i, n in enumerate(path[:f]) if n]
        return NS(deck=[card_from_id(c) for c in tl.deck], relics=[NS(name=r) for r in tl.relics],
                  potions=[empty] * 3, gold=at(gold, hp_floor, 99), current_hp=at(hp, hp_floor, 80),
                  max_hp=at(mx, hp_floor, 80), act=act, floor=f, act_boss=_boss_for(d, act), character=gchar,
                  ascension_level=d.get("ascension_level", 0))

    # every decision in floor order (a card reward and a rest on the same floor keep that order)
    todo = [(p["floor"], 0, "card", p) for p in d.get("card_choices", [])]
    todo += [(16 + 17 * i, 1, "boss", b) for i, b in enumerate(d.get("boss_relics", []))]
    todo += [(c["floor"], 0, "rest", c) for c in d.get("campfire_choices", []) if c.get("key") in ("REST", "SMITH")]
    events, rec = [], {"picks": [], "boss_relics": [], "rests": []}
    for f, _, kind, x in sorted(todo, key=lambda t: (t[0], t[1])):
        if kind == "card":
            g = state(f, f)
            offered = [card_from_id(c) for c in x.get("not_picked", [])]
            picked = x.get("picked")
            if picked not in (None, "SKIP", "Singing Bowl"):
                offered.append(card_from_id(picked))
            if not offered:
                continue
            commit = agent._update_plan(g)
            options = [agent._card_features(c, g, commit) for c in offered] + [{f"skip:a{min(g.act, 4)}": 1.0}]
            chosen = len(offered) - 1 if picked not in (None, "SKIP", "Singing Bowl") else len(offered)
            labels = [c.name for c in offered] + ["(skip)"]
            rec["picks"].append({"floor": f, "offered": labels, "chosen": labels[chosen]})
        elif kind == "boss":
            g = state(f, f)
            names = [gamedata.relic_name(r) or r for r in x.get("not_picked", [])]
            if x.get("picked"):
                names.append(gamedata.relic_name(x["picked"]) or x["picked"])
            commit = agent._update_plan(g)
            options = [agent._relic_features(n, g, commit, "relic") for n in names] + [{"relic:skip": 1.0}]
            chosen = len(names) - 1 if x.get("picked") else len(names)
            rec["boss_relics"].append({"floor": f + 1, "relic": names[chosen] if chosen < len(names) else "(skip)",
                                       "by": source, "offered": names})
        else:
            g = state(f, f - 1)
            hb = hp_bucket(g.current_hp, g.max_hp)
            options = [{f"rest:h{hb}": 1.0}, {f"smith:h{hb}": 1.0}]
            chosen = 0 if x["key"] == "REST" else 1
            rec["rests"].append({"floor": f, "choice": x["key"].lower(), "hp": g.current_hp, "max_hp": g.max_hp})
        events.append({"kind": kind, "options": options, "chosen": chosen, "probs": [1.0 / len(options)] * len(options),
                       "temp": 1.0, "act": g.act, "state": agent._state_features(g)})
    floor = d.get("floor_reached", 0)
    win = bool(d.get("victory"))
    elites = sum(1 for n in path[:floor] if n == "E")
    reward = Model.outcome(floor, win, elites)
    t = d.get("local_time", "")
    rec.update({
        "run": d.get("play_id"), "kind": source, "source": source, "play_id": d.get("play_id"),
        "seed": str(d.get("seed_played")), "floor": floor, "victory": win,
        "killer": None if win else d.get("killed_by"), "elites_killed": elites,
        "bosses_killed": sum(1 for n in path[:floor - 1] if n == "B"), "ascension": d.get("ascension_level", 0),
        "deck": [card_from_id(c).name for c in d.get("master_deck", [])],
        "relics": [gamedata.relic_name(r) or r for r in d.get("relics", [])],
        "hp": [[i + 1, h, at(mx, i + 1, 80)] for i, h in enumerate(hp)],
        "rooms": ["neow"] + [{"M": "fight", "E": "elite", "B": "boss", "R": "rest", "$": "shop", "?": "event",
                              "T": "chest"}.get(n or "", "boss") for n in path],
        "events": [{"floor": e["floor"], "event": e["event_name"], "choice": e["player_choice"], "by": source}
                   for e in d.get("event_choices", [])],
        "shops": [{"floor": f, "bought": card_from_id(i).name if _is_card(i) else (gamedata.relic_name(i) or
                   gamedata.potion_name(i) or i), "gold": None, "by": source}
                  for i, f in zip(d.get("items_purchased", []), d.get("item_purchase_floors", []))]
        + [{"floor": f, "bought": "remove a card", "gold": None, "by": source} for f in d.get("items_purged_floors", [])],
        "started": f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:{t[10:12]}:{t[12:14]}" if len(t) == 14 else None,
        "duration_s": d.get("playtime", 0), "reward": round(reward, 3),
    })
    return rec, events, reward


# ---------------------------------------------------------------- storage

def _home(char: str, source: str = "human") -> Path:
    return EVOLVE / char / source


def _bot_seeds(char: str) -> set[int]:
    out = set()
    for p in (EVOLVE / char / "runs").glob("run_*.json"):
        try:
            s = json.loads(p.read_text(encoding="utf-8")).get("seed") or ""
            out.add(seed_number(s))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return out


def runs(char: str, source: str = "human") -> list[dict]:
    d = _home(char, source) / "runs"
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return sorted(out, key=lambda r: r.get("started") or "")


def sync(char: str = "ironclad", model: Model | None = None, say=print, source: str = "human",
         folders: list[tuple[Path, str]] | None = None) -> dict:
    """Import new history files. Returns counts; queued decisions go to pending.jsonl.
    source 'expert' reads only the given folders (recursively) and keeps the runs apart from yours."""
    home = _home(char, source)
    (home / "runs").mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in (home / "runs").glob("*.json")}
    skipped_file = home / "skipped.json"
    skipped = set(json.loads(skipped_file.read_text(encoding="utf-8"))) if skipped_file.exists() else set()
    bot = None
    model = model or Model(EVOLVE / char / "model.json", GAME_CHAR[char])
    counts = {"new": 0, "bot": 0, "special": 0, "sources": []}
    for folder, label in (folders if folders is not None else run_dirs(char)):
        counts["sources"].append(f"{label}: {folder}")
        for f in sorted(folder.rglob("*.run") if source == "expert" else folder.glob("*.run")):
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            pid = str(d.get("play_id") or f.stem)
            if pid in done or pid in skipped:
                continue
            if d.get("character_chosen") != GAME_CHAR[char]:
                continue   # another character's run: its own import picks it up
            if d.get("is_daily") or d.get("is_endless") or d.get("is_trial"):
                skipped.add(pid); counts["special"] += 1
                continue
            bot = bot if bot is not None else _bot_seeds(char)
            try:
                seed = int(d.get("seed_played", 0))
            except (TypeError, ValueError):
                seed = 0
            if seed in bot:
                skipped.add(pid); counts["bot"] += 1
                continue
            try:
                rec, events, reward = convert(d, char, model, source)
            except Exception as exc:  # one odd file never stops the import
                say(f"  could not read {f.name}: {type(exc).__name__}: {exc}")
                skipped.add(pid)
                continue
            rec["file"] = str(f)
            (home / "runs" / f"{pid}.json").write_text(json.dumps(rec), encoding="utf-8")
            with (home / "pending.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"play_id": pid, "reward": reward, "events": events}) + "\n")
            done.add(pid)
            counts["new"] += 1
    skipped_file.write_text(json.dumps(sorted(skipped)), encoding="utf-8")
    counts["total"] = len(done)
    return counts


_RATES: dict[str, dict] = {}


def expert_rates(game_char: str) -> dict:
    """How often expert players took each card (per act) and boss relic when offered, shrunk toward their
    average so rare offers don't swing: {'card': {(name, act): rate - mean}, 'relic': {name: rate - 1/3}}.
    Empty when no expert runs are loaded for this character."""
    char = next((k for k, v in GAME_CHAR.items() if v == game_char), None)
    if char is None:
        return {}
    if char in _RATES:
        return _RATES[char]
    off: dict = {}
    tak: dict = {}
    roff: dict = {}
    rtak: dict = {}
    for r in runs(char, "expert"):
        for p in r.get("picks", []):
            act = 1 if p["floor"] <= 17 else 2 if p["floor"] <= 34 else 3
            for o in p.get("offered", []):
                if o != "(skip)":
                    k = (C.base_name(o), act)
                    off[k] = off.get(k, 0) + 1
            if p.get("chosen") not in (None, "(skip)"):
                k = (C.base_name(p["chosen"]), act)
                tak[k] = tak.get(k, 0) + 1
        for b in r.get("boss_relics", []):
            for o in b.get("offered", []):
                roff[o] = roff.get(o, 0) + 1
            if b.get("relic") not in (None, "(skip)"):
                rtak[b["relic"]] = rtak.get(b["relic"], 0) + 1
    out: dict = {"card": {}, "relic": {}}
    a = 6.0   # shrinkage: an offer seen 6 times moves halfway from the mean to its own rate
    for act in (1, 2, 3):
        keys = [k for k in off if k[1] == act]
        mean = sum(tak.get(k, 0) for k in keys) / max(1, sum(off[k] for k in keys))
        for k in keys:
            out["card"][k] = round((tak.get(k, 0) + a * mean) / (off[k] + a) - mean, 3)
    for name, n in roff.items():
        out["relic"][name] = round((rtak.get(name, 0) + a / 3) / (n + a) - 1 / 3, 3)
    _RATES[char] = out if out["card"] else {}
    return _RATES[char]


def take_pending(char: str, source: str = "human") -> list[dict]:
    """Queued imported runs to learn from (and clears the queue)."""
    p = _home(char, source) / "pending.jsonl"
    if not p.exists():
        return []
    out = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    p.unlink()
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Import your own Slay the Spire runs for the bot to learn from.")
    ap.add_argument("folder", nargs="?", help="a folder of .run files (e.g. copied from the Steam Deck)")
    ap.add_argument("--character", default="ironclad", choices=list(GAME_CHAR))
    ap.add_argument("--expert", action="store_true",
                    help="the folder holds other players' runs (any character): import them for every character, "
                         "kept apart from your own runs and learned at --expert-lr")
    a = ap.parse_args()
    if a.expert:
        if not a.folder:
            ap.error("--expert needs a folder of .run files")
        src = Path(a.folder)
        for char in GAME_CHAR:
            c = sync(char, source="expert", folders=[(src, "expert")])
            print(f"{char:9s} imported {c['new']} new expert runs ({c['total']} in total), "
                  f"skipped {c['special']} daily/endless/trial")
        print("Each character's bot learns them at the start of its next run (Settings: expert weight).")
        return
    if a.folder:
        src = Path(a.folder)
        inbox = EVOLVE / "human_inbox" / GAME_CHAR[a.character]
        inbox.mkdir(parents=True, exist_ok=True)
        files = list(src.rglob("*.run"))
        for f in files:
            shutil.copy2(f, inbox / f.name)
        print(f"Copied {len(files)} .run files from {src} into {inbox}")
    c = sync(a.character)
    print("Looked in:\n  " + ("\n  ".join(c["sources"]) or "(no run folders besides the bot's slot)"))
    print(f"Imported {c['new']} new runs ({c['total']} in total); skipped {c['bot']} bot runs and "
          f"{c['special']} daily/endless/other-character runs.")
    print("The bot learns from them at the start of its next run (lower weight than its own runs).")


if __name__ == "__main__":
    main()
