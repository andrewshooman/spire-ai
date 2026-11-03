"""Behavior cloning of expert card picks and boss relics.

The bot's own learning can't tell card values apart: ~150 runs a class, almost no wins, so REINFORCE moves
card weights on coin flips (the Watcher took Crush Joints 63% of the time, experts 3%; Inner Peace 29% vs 78%).
Expert A20 runs are the best evidence there is, so this fits the policy's own card / boss-relic features to
the experts' choices: a conditional logit (softmax over the offer + skip, the same model the bot plays with),
maximum likelihood with an L2 pull toward the hand-made priors. The fitted weights become the priors
(evolve/<char>/expert_bc.json, read by Model.prior) and replace the learned card weights once (evolve.migrate);
the bot's own runs keep learning on top.

    python agent/expert_bc.py <character> [--apply]     (prints held-out agreement: current vs fitted)
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cards as C                  # noqa: E402
import human                      # noqa: E402
from model import Model           # noqa: E402

EVOLVE = HERE.parent / "evolve"
KINDS = ("card", "boss")
# not fitted: expertpick/expertrelic are the experts' own rates (absent while converting expert runs)
SKIP_KEYS = ("expertpick", "expertrelic")
L2 = 1.0          # pull toward the prior, per weight (chosen on held-out runs: 0.03-10 tried)
EPOCHS = 300


def path(char: str) -> Path:
    return EVOLVE / char / "expert_bc.json"


def load(char: str) -> dict[str, float]:
    p = path(char)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("weights", {})
    except (OSError, ValueError):
        return {}


def skip_offset(opts: list[dict]) -> float:
    """policy._skip_bonus for this offer: deck size from the 'dilute' feature ((n - 15) / 10)."""
    d = max((o.get("dilute", 0.0) for o in opts), default=0.0)
    n = 15 + d * 10 if d else 15
    act = next((int(k[-1]) for o in opts for k in o if k.startswith("skip:a")), 1)
    if n >= 28:
        return 2.0
    if n >= 24 or (act >= 2 and n >= 22):
        return 1.0
    return 0.0


def decisions(char: str, model: Model) -> list[tuple[str, list[dict], int, list[float]]]:
    """(run id, options, chosen, fixed offsets) for every expert card pick and boss relic, features built by
    the current policy code."""
    out = []
    for r in human.runs(char, "expert"):
        try:
            raw = json.loads(Path(r["file"]).read_text(encoding="utf-8", errors="replace"))
            _, events, _ = human.convert(raw, char, model, "expert")
        except Exception:
            continue
        for e in events:
            if e["kind"] not in KINDS:
                continue
            opts = [{k: v for k, v in o.items() if not k.startswith(SKIP_KEYS)} for o in e["options"]]
            off = [0.0] * len(opts)
            if e["kind"] == "card":
                off[-1] = skip_offset(opts)
            else:   # the guide tier the policy adds as a bonus (policy._boss_reward)
                for i, o in enumerate(opts):
                    name = next((k[6:] for k in o if k.startswith("relic:") and k != "relic:skip"), None)
                    off[i] = C.GUIDE_BOSS_RELIC.get(name, (0.0, ""))[0] if name else 0.0
            out.append((str(r.get("play_id")), opts, e["chosen"], off))
    return out


def evaluate(data, w) -> tuple[float, float]:
    """(top-1 agreement, mean log-likelihood)."""
    hit, ll = 0, 0.0
    for _, opts, ch, off in data:
        s = [sum(w.get(k, 0.0) * x for k, x in o.items()) + off[i] for i, o in enumerate(opts)]
        m = max(s)
        z = sum(math.exp(v - m) for v in s)
        ll += s[ch] - m - math.log(z)
        hit += s.index(m) == ch
    return hit / max(1, len(data)), ll / max(1, len(data))


def fit(data, prior: dict[str, float], start: dict[str, float], l2: float = L2, epochs: int = EPOCHS):
    keys = sorted({k for _, opts, _, _ in data for o in opts for k in o})
    w = {k: start.get(k, prior.get(k, 0.0)) for k in keys}
    m1 = {k: 0.0 for k in keys}
    m2 = {k: 0.0 for k in keys}
    lr, b1, b2 = 0.05, 0.9, 0.999
    n = len(data)
    for t in range(1, epochs + 1):
        g = {k: l2 * (w[k] - prior.get(k, 0.0)) / n for k in keys}
        for _, opts, ch, off in data:
            s = [sum(w[k] * x for k, x in o.items()) + off[i] for i, o in enumerate(opts)]
            m = max(s)
            e = [math.exp(v - m) for v in s]
            z = sum(e)
            for i, o in enumerate(opts):
                p = e[i] / z - (1.0 if i == ch else 0.0)
                for k, x in o.items():
                    g[k] += p * x / n
        for k in keys:
            m1[k] = b1 * m1[k] + (1 - b1) * g[k]
            m2[k] = b2 * m2[k] + (1 - b2) * g[k] * g[k]
            w[k] -= lr * (m1[k] / (1 - b1 ** t)) / (math.sqrt(m2[k] / (1 - b2 ** t)) + 1e-8)
    return w


def upgrades(char: str) -> list[tuple[str, list[dict], int, list[float]]]:
    """Every expert smith: options = the deck's distinct unupgraded cards (feature up:<card>), chosen = the one
    upgraded. Curses, statuses and Searing Blow repeats left out."""
    gchar = human.GAME_CHAR[char]
    out = []
    for r in human.runs(char, "expert"):
        try:
            raw = human.normalize(json.loads(Path(r["file"]).read_text(encoding="utf-8", errors="replace")))
        except Exception:
            continue
        tl = human.Timeline(raw, gchar)
        for c in raw.get("campfire_choices", []):
            if c.get("key") != "SMITH" or not c.get("data"):
                continue
            tl.advance(int(c["floor"]))
            names = []
            for cid in tl.deck:
                if "+" in cid:
                    continue
                card = human.card_from_id(cid)
                if card.type.value in ("CURSE", "STATUS"):
                    continue
                n = human.C.base_name(card.name)
                if n not in names:
                    names.append(n)
            want = human.C.base_name(human.card_from_id(re.sub(r"\+\d+$", "", c["data"])).name)
            if want in names and len(names) > 1:
                out.append((str(r.get("play_id")), [{f"up:{n}": 1.0} for n in names], names.index(want),
                            [0.0] * len(names)))
    return out


def run(char: str, apply: bool = False, say=print) -> dict:
    model = Model(EVOLVE / char / "model.json", human.GAME_CHAR[char])
    model.bc = {}   # fit against the hand-made priors, not an earlier fit
    data = decisions(char, model)
    if len(data) < 200:
        say(f"{char}: only {len(data)} expert decisions, nothing fitted")
        return {}
    ids = sorted({d[0] for d in data})
    random.Random(7).shuffle(ids)
    hold = set(ids[: len(ids) // 5])
    train = [d for d in data if d[0] not in hold]
    test = [d for d in data if d[0] in hold]
    keys = {k for _, opts, _, _ in data for o in opts for k in o}
    prior = {k: model.prior(k) for k in keys}
    cur = {k: model.w(k) for k in keys}
    say(f"{char}: {len(data)} expert decisions ({len(test)} held out)")
    a, ll = evaluate(test, cur)
    say(f"  current weights  agreement {a:.1%}  log-lik {ll:.3f}")
    a, ll = evaluate(test, prior)
    say(f"  priors           agreement {a:.1%}  log-lik {ll:.3f}")
    w = fit(train, prior, prior)
    a, ll = evaluate(test, w)
    say(f"  fitted           agreement {a:.1%}  log-lik {ll:.3f}")
    up = upgrades(char)
    up_train = [d for d in up if d[0] not in hold]
    up_test = [d for d in up if d[0] in hold]
    uw = fit(up_train, {}, {}, l2=2.0, epochs=200)
    chance = sum(1 / len(d[1]) for d in up_test) / max(1, len(up_test))
    say(f"  upgrades: {len(up)} smiths, held-out agreement {evaluate(up_test, uw)[0]:.1%} (chance {chance:.1%})")
    if apply:
        full = fit(data, prior, w, epochs=EPOCHS // 2)
        ufull = fit(up, {}, uw, l2=2.0, epochs=100)
        path(char).write_text(json.dumps({"decisions": len(data), "held_out_agreement": round(a, 3),
                                          "weights": {k: round(v, 3) for k, v in sorted(full.items())},
                                          "upgrade": {k[3:]: round(v, 3) for k, v in sorted(ufull.items())}},
                                         indent=1), encoding="utf-8")
        say(f"  wrote {path(char)}")
    return {"agreement": a, "loglik": ll}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for c in args or ["silent", "defect", "watcher"]:
        run(c, apply="--apply" in sys.argv)
