"""The learned policy model: a linear softmax policy trained like a tiny neural net.

Every choice (card pick or skip, path node, rest or upgrade, shop buy, boss
relic) is a softmax over options. An option's score is the sum of
weight[feature] * value over its features. After a run, REINFORCE moves the
weights of every choice made toward the options picked in runs that went
deeper than usual (advantage = outcome - running baseline), and away from them
in runs that died early. Combat uses a handful of continuous parameters tuned
by evolution strategies: each explore run plays a slightly perturbed copy, and
the perturbations that took less damage per fight pull the parameters along.

The reward is how far the run got, convex in depth (floors past the Act 1 and
Act 2 bosses count extra), plus 0.06 per elite killed and 1.0 for beating the
act 3 boss -- so risky plays that power a run up are worth an early death now and then.
"""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

from cards import BOSS_RELIC_PRIOR, card_prior

COMBAT_DEFAULTS = {
    "damage": 1.0,        # per point of HP damage dealt
    "kill": 6.0,          # bonus for a kill (plus the damage it would have dealt)
    "block": 1.2,         # per point of block that stops incoming damage
    "surplus_block": 0.1, # per point of block beyond incoming damage
    "vuln": 2.5,          # per Vulnerable stack applied
    "weak": 0.4,          # per Weak stack, scaled by the target's attack
    "draw": 2.0,          # per card drawn
    "energy": 3.0,        # per energy gained
    "strength": 4.0,      # per permanent Strength
    "power": 5.0,         # base value of a power card early in a fight
    "hp_cost": 1.5,       # per HP the card costs you
    "energy_cost": 1.0,   # unused since the whole-turn planner (kept for old model files)
    "end_threshold": 0.5, # end the turn when the best turn plan beats ending by less than this
    "potion_hp": 0.35,    # use potions when HP falls below this fraction
}

NODE_PRIORS = {
    ("M", 0): 0.0, ("M", 1): 0.2, ("M", 2): 0.4,
    ("E", 0): -1.5, ("E", 1): 0.1, ("E", 2): 0.6,
    ("R", 0): 1.2, ("R", 1): 0.5, ("R", 2): -0.1,
    ("$", 0): 0.1, ("$", 1): 0.2, ("$", 2): 0.2,
    ("?", 0): 0.4, ("?", 1): 0.3, ("?", 2): 0.3,
    ("T", 0): 0.5, ("T", 1): 0.5, ("T", 2): 0.5,
}

# act-level path priors from the playbook: act 1 elites pay off, act 3 elites end runs,
# act 3 events are rich
NODEACT_PRIORS = {("E", 1): 0.2, ("E", 3): -0.4, ("?", 3): 0.2, ("$", 3): 0.2}

FEATURE_PRIORS = {
    "need:aoe": 1.0, "need:block": 0.6, "need:damage": 0.8, "need:draw": 0.5, "need:scaling": 0.8,
    "upgraded": 0.3, "dup": -0.4, "dilute": -0.5,
    "skip:a1": -0.8, "skip:a2": -0.2, "skip:a3": 0.2, "skip:a4": 0.3,
    "rest:h0": 2.0, "rest:h1": 0.3, "rest:h2": -1.0,
    "smith:h0": 0.0, "smith:h1": 0.5, "smith:h2": 0.6,
    "shop:purge": 1.5, "shop:leave": 0.0, "shop:potion": -0.2, "shop:price": -0.6,
    "relic:skip": -0.5,
}


def hp_bucket(hp: int, max_hp: int) -> int:
    frac = hp / max(1, max_hp)
    return 0 if frac < 0.4 else 1 if frac < 0.7 else 2


class Model:
    def __init__(self, path: Path, character: str = "IRONCLAD"):
        self.path = Path(path)
        self.character = character
        self.weights: dict[str, float] = {}
        self.params: dict[str, float] = dict(COMBAT_DEFAULTS)
        self.meta = {"runs_trained": 0, "baseline": None, "var": 0.04,
                     "es_baseline": None, "es_var": 0.01, "created": time.time()}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.weights = data.get("weights", {})
            self.params.update(data.get("params", {}))
            self.meta.update(data.get("meta", {}))
            self.character = data.get("character", character)

    # -------------------------------------------------------------- weights

    def prior(self, key: str) -> float:
        if key in FEATURE_PRIORS:
            return FEATURE_PRIORS[key]
        kind, _, rest = key.partition(":")
        if kind == "card":
            return card_prior(self.character, rest)
        if kind == "syn":
            return 1.5
        if kind == "tag":
            return 0.3
        if kind == "node":  # node:<sym>:h<bucket>
            sym, _, hb = rest.partition(":h")
            return NODE_PRIORS.get((sym, int(hb or 1)), 0.0)
        if kind == "nodeact":  # nodeact:<sym>:a<act>
            sym, _, a = rest.partition(":a")
            return NODEACT_PRIORS.get((sym, int(a or 1)), 0.0)
        if kind == "relic":
            return BOSS_RELIC_PRIOR.get(rest, 0.5)
        if kind == "shoprelic":
            return 0.8
        return 0.0

    def w(self, key: str) -> float:
        if key not in self.weights:
            return self.prior(key)
        return self.weights[key]

    def score(self, features: dict[str, float]) -> float:
        return sum(self.w(k) * v for k, v in features.items())

    def temperature(self) -> float:
        return max(0.15, 1.0 * (0.97 ** self.meta["runs_trained"]))

    @staticmethod
    def softmax(scores: list[float], temperature: float) -> list[float]:
        t = max(temperature, 1e-3)
        top = max(scores)
        exps = [math.exp((s - top) / t) for s in scores]
        total = sum(exps)
        return [e / total for e in exps]

    def choose(self, option_features: list[dict[str, float]], temperature: float,
               rng: random.Random, bonus: list[float] | None = None) -> tuple[int, list[float]]:
        scores = [self.score(f) + (bonus[i] if bonus else 0.0) for i, f in enumerate(option_features)]
        probs = self.softmax(scores, temperature)
        r, acc = rng.random(), 0.0
        for i, p in enumerate(probs):
            acc += p
            if r <= acc:
                return i, probs
        return len(probs) - 1, probs

    # -------------------------------------------------------------- learning

    REWARD_VERSION = 2

    @staticmethod
    def outcome(floor: int, victory: bool, elites: int = 0) -> float:
        """Convex in depth so the learner takes risks that power a run up: dying on floor 8
        instead of 16 costs little, while every floor past the Act 1 and Act 2 bosses is worth
        much more; each elite killed (relics, rare cards) adds about three early floors."""
        f = min(floor, 55)
        depth = f / 50.0 + 0.6 * max(0, f - 17) / 34.0 + 0.8 * max(0, f - 34) / 17.0
        return depth + 0.06 * min(elites, 6) + (1.0 if victory else 0.0)

    def learn(self, events: list[dict], reward: float, lr: float = 0.08) -> dict:
        """REINFORCE: push every recorded choice toward (or away from) what it
        picked, scaled by how this run compares to the running baseline."""
        base = self.meta["baseline"]
        if base is None:
            base = reward
        std = math.sqrt(max(self.meta["var"], 1e-4))
        adv = max(-3.0, min(3.0, (reward - base) / std))
        changed: dict[str, float] = {}
        for ev in events:
            probs, chosen, temp = ev["probs"], ev["chosen"], max(ev.get("temp", 1.0), 0.15)
            for i, feats in enumerate(ev["options"]):
                g = ((1.0 if i == chosen else 0.0) - probs[i]) / temp
                if abs(g) < 1e-6:
                    continue
                for k, v in feats.items():
                    delta = lr * adv * g * v
                    new = max(-6.0, min(6.0, self.w(k) + delta))
                    self.weights[k] = new
                    changed[k] = changed.get(k, 0.0) + delta
        # running baseline / variance of the reward
        self.meta["baseline"] = base + 0.1 * (reward - base)
        self.meta["var"] = 0.9 * self.meta["var"] + 0.1 * (reward - base) ** 2
        self.meta["runs_trained"] += 1
        return {"advantage": adv, "baseline": self.meta["baseline"], "changed": changed}

    def es_sample(self, rng: random.Random, sigma: float = 0.15) -> dict[str, float]:
        return {k: rng.gauss(0.0, sigma) for k in self.params}

    def es_params(self, eps: dict[str, float] | None) -> dict[str, float]:
        if not eps:
            return dict(self.params)
        return {k: v * math.exp(eps.get(k, 0.0)) for k, v in self.params.items()}

    def es_update(self, eps: dict[str, float], fitness: float, lr: float = 0.3) -> float:
        base = self.meta["es_baseline"]
        if base is None:
            base = fitness
        std = math.sqrt(max(self.meta["es_var"], 1e-4))
        adv = max(-3.0, min(3.0, (fitness - base) / std))
        for k, e in eps.items():
            self.params[k] = max(0.01, min(50.0, self.params[k] * math.exp(lr * adv * e)))
        self.meta["es_baseline"] = base + 0.1 * (fitness - base)
        self.meta["es_var"] = 0.9 * self.meta["es_var"] + 0.1 * (fitness - base) ** 2
        return adv

    def nudge(self, card: dict | None = None, archetype: dict | None = None,
              node: dict | None = None, params: dict | None = None, limit: float = 0.5) -> list[str]:
        """Coach nudges: small bounded changes on top of what was learned."""
        applied = []
        clamp = lambda v: max(-limit, min(limit, float(v)))
        for name, v in (card or {}).items():
            k = f"card:{name}"
            self.weights[k] = self.w(k) + clamp(v); applied.append(f"{k} {clamp(v):+.2f}")
        for name, v in (archetype or {}).items():
            k = f"tag:{name}"
            self.weights[k] = self.w(k) + clamp(v); applied.append(f"{k} {clamp(v):+.2f}")
        for name, v in (node or {}).items():
            k = f"node:{name}"
            self.weights[k] = self.w(k) + clamp(v); applied.append(f"{k} {clamp(v):+.2f}")
        for name, v in (params or {}).items():
            if name in self.params:
                self.params[name] = max(0.01, self.params[name] * (1 + clamp(v)))
                applied.append(f"param {name} x{1 + clamp(v):.2f}")
        return applied

    # -------------------------------------------------------------- io

    def to_dict(self) -> dict:
        return {"character": self.character, "weights": self.weights,
                "params": self.params, "meta": self.meta}

    def save(self, path: Path | None = None) -> None:
        p = Path(path or self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(p)

    def card_table(self, names: list[str]) -> list[tuple[str, float]]:
        return sorted(((n, self.w(f"card:{n}")) for n in names), key=lambda x: -x[1])
