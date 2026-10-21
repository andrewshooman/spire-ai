"""cardutil.py -- how well the bot actually USES each card, from its own fights.

A run's protocol log (sts-bench/logs/<date>/<run>.log, next to its trajectory) holds every game state
and command. parse_log() rebuilds each fight turn by turn: cards in hand, cards played, HP lost, the deck.
sync(char) parses each finished run once into evolve/<char>/carduse/run_NNNN.json (compact), and stats()
turns the recent ones into three learnable card-pick features (policy._card_features):

  cardplay   how often the planner plays the card when it's in hand, vs the average card (shrunk):
             a card that sits in hand is worth less than its text suggests
  cardfight  HP saved in fights of the SAME encounter when the card is in the deck (z-score, shrunk):
             per-fight credit, a dense signal next to the once-per-run reward
  cardpair   for an offered card: how much more often it gets played in the same turn as cards already
             in the deck than their separate play rates predict -- synergies the planner really uses

    python agent/cardutil.py --character ironclad      parse every run whose log still exists (backfill)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVOLVE = HERE.parent / "evolve"
RECENT = 150          # runs the stats are built from
SKIP_TYPES = {"STATUS", "CURSE"}


def _base(name: str) -> str:
    return re.sub(r"\+\d*$", "", name or "").strip()


def log_path(trajectory: str) -> Path | None:
    """The protocol log next to a run's trajectory: logs/<date>/trajectories/x.jsonl -> logs/<date>/x.log."""
    if not trajectory:
        return None
    t = Path(trajectory)
    p = t.parent.parent / (t.stem + ".log")
    return p if p.exists() else None


def parse_lines(lines) -> list[dict]:
    """Protocol log lines -> fights: {floor, enemies, hp_start, hp_end, deck, played, turns: [{seen, played}]}.
    Lines look like '18:50:16.257 << {state json}' and '18:50:16.362 >> play 4 0'."""
    fights: list[dict] = []
    cur = None          # the fight being read
    turn = None         # {"n", "seen": Counter, "played": Counter}
    last_hand: list = []
    last_hp = None

    def close_turn():
        nonlocal turn
        if cur is not None and turn is not None and (turn["seen"] or turn["played"]):
            cur["turns"].append({"seen": dict(turn["seen"]), "played": dict(turn["played"])})
        turn = None

    def close_fight(hp_after):
        nonlocal cur
        close_turn()
        if cur is not None:
            cur["hp_end"] = hp_after if hp_after is not None else last_hp
            cur["played"] = sorted({c for t in cur["turns"] for c in t["played"]})
            fights.append(cur)
        cur = None

    for line in lines:
        if " << {" in line:
            try:
                msg = json.loads(line[line.index("{"):])
            except (ValueError, json.JSONDecodeError):
                continue
            g = msg.get("game_state") or {}
            cs = g.get("combat_state")
            if not cs:
                if cur is not None:
                    close_fight(g.get("current_hp"))
                continue
            p = cs.get("player") or {}
            if cur is None or g.get("floor") != cur["floor"]:
                if cur is not None:
                    close_fight(None)
                cur = {"floor": g.get("floor"), "act": g.get("act"),
                       "enemies": sorted({m.get("name", "?") for m in cs.get("monsters", [])}),
                       "hp_start": p.get("current_hp"), "max_hp": p.get("max_hp"),
                       "deck": [_base(c.get("name")) for c in g.get("deck", []) if c.get("type") not in SKIP_TYPES],
                       "turns": []}
            if turn is None or cs.get("turn") != turn["n"]:
                close_turn()
                turn = {"n": cs.get("turn"), "seen": Counter(), "played": Counter()}
            hand = [c for c in cs.get("hand", [])]
            last_hand = hand
            last_hp = p.get("current_hp")
            names = Counter(_base(c.get("name")) for c in hand if c.get("type") not in SKIP_TYPES)
            for n, k in names.items():
                turn["seen"][n] = max(turn["seen"][n], k)
        elif " >> play " in line and cur is not None and turn is not None:
            m = re.search(r">> play (\d+)", line)
            if not m:
                continue
            i = int(m.group(1)) - 1
            if 0 <= i < len(last_hand) and last_hand[i].get("type") not in SKIP_TYPES:
                n = _base(last_hand[i].get("name"))
                turn["played"][n] += 1
                turn["seen"][n] = max(turn["seen"][n], turn["played"][n])
    if cur is not None:
        close_fight(None)
    return fights


def parse_log(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_lines(fh)


def compact(fights: list[dict]) -> dict:
    """What stats() needs from one run: per card seen/played turns, co-seen/co-played pairs, per fight."""
    seen, played = Counter(), Counter()
    pair_seen, pair_played = Counter(), Counter()
    out_f = []
    for f in fights:
        for t in f["turns"]:
            for n, k in t["seen"].items():
                seen[n] += k
                played[n] += min(k, t["played"].get(n, 0))
            s = sorted(t["seen"])
            pl = set(t["played"])
            for a in range(len(s)):
                for b in range(a + 1, len(s)):
                    key = f"{s[a]}|{s[b]}"
                    pair_seen[key] += 1
                    if s[a] in pl and s[b] in pl:
                        pair_played[key] += 1
        if f.get("hp_start") is not None and f.get("hp_end") is not None:
            out_f.append({"enc": ", ".join(f["enemies"]), "act": f.get("act"), "lost": f["hp_start"] - f["hp_end"],
                          "deck": sorted(set(f["deck"])), "played": f["played"]})
    return {"seen": dict(seen), "played": dict(played), "pair_seen": dict(pair_seen),
            "pair_played": dict(pair_played), "fights": out_f}


def sync(char: str, say=print, home: Path | None = None) -> int:
    """Parse every finished run of this character that has a log and no carduse file yet. Returns how many."""
    home = Path(home) if home else EVOLVE / char
    out = home / "carduse"
    out.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in out.glob("run_*.json")}
    n = 0
    for p in sorted((home / "runs").glob("run_*.json")):
        if p.stem in done:
            continue
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        lp = log_path(r.get("trajectory", ""))
        if lp is None:
            continue
        try:
            data = compact(parse_log(lp))
        except Exception as exc:  # one odd log never stops the rest
            say(f"card use: could not parse {lp.name}: {type(exc).__name__}: {exc}")
            continue
        data["run"] = r.get("run")
        (out / f"{p.stem}.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        n += 1
    if n:
        _CACHE.pop(str(home), None)
    return n


_CACHE: dict[str, tuple[int, dict]] = {}


def _shrunk(k: float, n: float, p0: float, a: float) -> float:
    return (k + a * p0) / (n + a)


def stats(char: str, home: Path | None = None) -> dict:
    """{'play': {card: rate - mean}, 'fight': {card: z}, 'pair': {'A|B': lift}} from the last RECENT runs."""
    home = Path(home) if home else EVOLVE / char
    files = sorted((home / "carduse").glob("run_*.json"))[-RECENT:]
    if not files:
        return {}
    hit = _CACHE.get(str(home))
    if hit and hit[0] == len(files):
        return hit[1]
    seen, played, ps, pp = Counter(), Counter(), Counter(), Counter()
    fights = []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        seen.update(d["seen"])
        played.update(d["played"])
        ps.update(d["pair_seen"])
        pp.update(d["pair_played"])
        fights += d["fights"]
    p0 = sum(played.values()) / max(1, sum(seen.values()))
    rate = {c: _shrunk(played[c], seen[c], p0, 10.0) for c in seen}
    play = {c: round(rate[c] - p0, 3) for c in seen if seen[c] >= 5}
    # pairs: co-played more often than their separate play rates predict (both seen in hand that turn)
    pair = {}
    for key, n in ps.items():
        if n < 8:
            continue
        a, b = key.split("|")
        expect = rate.get(a, p0) * rate.get(b, p0)
        pair[key] = round(_shrunk(pp[key], n, expect, 8.0) - expect, 3)
    # per-fight credit: HP lost vs the encounter's usual, as a z-score; a card's credit = mean z of the fights
    # with it in the deck (shrunk toward 0). Positive = fights with it cost less HP than usual.
    by_enc = defaultdict(list)
    for f in fights:
        by_enc[f["enc"]].append(f["lost"])
    norm = {}
    for e, xs in by_enc.items():
        if len(xs) >= 4:
            m = sum(xs) / len(xs)
            sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 or 1.0
            norm[e] = (m, sd)
    zsum, zn = Counter(), Counter()
    for f in fights:
        if f["enc"] not in norm:
            continue
        m, sd = norm[f["enc"]]
        z = max(-3.0, min(3.0, (m - f["lost"]) / sd))
        for c in f["deck"]:
            zsum[c] += z
            zn[c] += 1
    fight = {c: round(zsum[c] / (zn[c] + 20.0), 3) for c in zn if zn[c] >= 5}
    out = {"play": play, "fight": fight, "pair": pair, "base_play": round(p0, 3), "runs": len(files),
           "fights": len(fights)}
    _CACHE[str(home)] = (len(files), out)
    return out


def features(char: str, card: str, deck: list[str], home: Path | None = None) -> dict[str, float]:
    """Card-utility features for offering `card` to this deck (empty until there is data)."""
    st = stats(char, home)
    if not st:
        return {}
    name = _base(card)
    f: dict[str, float] = {}
    if name in st["play"]:
        f["cardplay"] = st["play"][name]
    if name in st["fight"]:
        f["cardfight"] = st["fight"][name]
    lifts = []
    for d in {_base(x) for x in deck}:
        if d == name:
            continue
        key = "|".join(sorted((name, d)))
        if key in st["pair"]:
            lifts.append(st["pair"][key])
    if lifts:
        top = sorted(lifts, reverse=True)[:3]   # its best partners already in the deck
        f["cardpair"] = round(sum(top) / len(top), 3)
    return f


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Parse run logs into card-use data (play rate, fight credit, pairs).")
    ap.add_argument("--character", default="ironclad")
    a = ap.parse_args()
    n = sync(a.character)
    st = stats(a.character)
    print(f"{a.character}: parsed {n} new runs; stats from {st.get('runs', 0)} runs, {st.get('fights', 0)} fights, "
          f"average play rate {st.get('base_play')}")
    for key, label in (("play", "played when in hand (vs average)"), ("fight", "fight credit (z)")):
        rows = sorted(st.get(key, {}).items(), key=lambda kv: kv[1])
        if rows:
            print(f"  {label}: best {rows[-6:][::-1]}")
            print(f"  {' ' * len(label)}  worst {rows[:6]}")
    pairs = sorted(st.get("pair", {}).items(), key=lambda kv: -kv[1])[:8]
    print(f"  pairs played together: {pairs}")


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
