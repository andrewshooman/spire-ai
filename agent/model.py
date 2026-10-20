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
    "shop:card": -1.0, "shop:relic": 0.3,
    "relic:skip": -0.5,
}


# Archetype-wide weights apply to every card of an archetype at once; unbounded they snowballed
# (tag:Strength reached +3.8) until nearly every run drafted Strength. Keep them small so the
# individual card weights, synergy rules and the LLM decide.
WEIGHT_BOUNDS = {"tag": (-1.0, 1.0), "syn": (-1.0, 1.5)}


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
        if kind == "cardctx":
            from cards import cardctx_prior
            return cardctx_prior(key)
        if kind in ("synrule", "synneed"):
            import synergy
            return synergy.prior(key) or 0.0
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
        """Exploration for explore runs: decays slowly and never below 0.35, so runs keep
        trying different paths, picks and shops instead of converging on one safe line."""
        return max(0.35, 1.0 * (0.985 ** self.meta["runs_trained"]))

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

    REWARD_VERSION = 3

    @staticmethod
    def outcome(floor: int, victory: bool, elites: int = 0) -> float:
        """Convex in depth so the learner takes risks that power a run up: dying on floor 8
        instead of 16 costs little, while every floor past the Act 1 and Act 2 bosses is worth
        much more; each elite killed (relics, rare cards) adds about three early floors."""
        f = min(floor, 55)
        depth = f / 50.0 + 0.6 * max(0, f - 17) / 34.0 + 0.8 * max(0, f - 34) / 17.0
        return depth + 0.06 * min(elites, 6) + (1.0 if victory else 0.0)

    VALUE_MIN_RUNS = 20   # runs before the learned value estimate replaces the per-act baselines

    def value(self, state: dict | None) -> float | None:
        """Learned estimate of a run's final reward from its state at a decision (HP, deck, relics,
        act...): a linear model fitted on every past decision -> final reward."""
        vw = self.meta.get("value_w")
        if not state or not vw or self.meta.get("value_runs", 0) < self.VALUE_MIN_RUNS:
            return None
        return vw.get("bias", 0.0) + sum(vw.get(k, 0.0) * x for k, x in state.items())

    def fit_value(self, events: list[dict], reward: float, lr: float = 0.02) -> None:
        vw = self.meta.setdefault("value_w", {"bias": 0.0})
        states = [ev["state"] for ev in events if ev.get("state")]
        for _ in range(3):
            for s in states:
                pred = vw.get("bias", 0.0) + sum(vw.get(k, 0.0) * x for k, x in s.items())
                err = reward - pred
                vw["bias"] = vw.get("bias", 0.0) + lr * err
                for k, x in s.items():
                    vw[k] = vw.get(k, 0.0) + lr * err * x
        if states:
            errs = [reward - (vw["bias"] + sum(vw.get(k, 0.0) * x for k, x in s.items())) for s in states]
            self.meta["value_var"] = 0.9 * self.meta.get("value_var", 0.04) + 0.1 * (sum(e * e for e in errs) / len(errs))
            self.meta["value_runs"] = self.meta.get("value_runs", 0) + 1

    def learn(self, events: list[dict], reward: float, lr: float = 0.08, replay: bool = False) -> dict:
        """REINFORCE with a per-decision baseline.

        Each choice is judged against the learned value of the state it was made in (value());
        until that model has seen VALUE_MIN_RUNS runs, against the running average of runs that
        reached the same act (per-act credit). replay=True re-learns an older run: action
        probabilities are recomputed with the current weights and no baseline is updated."""
        acts = self.meta.setdefault("act_baseline", {})
        by_act: dict[int, list[dict]] = {}
        for ev in events:
            by_act.setdefault(int(ev.get("act") or 1), []).append(ev)
        advs: dict[int, float] = {}
        for a in sorted(set(by_act) | {1}):
            st = acts.setdefault(str(a), {"b": None, "var": 0.04})
            base = reward if st["b"] is None else st["b"]
            advs[a] = max(-3.0, min(3.0, (reward - base) / math.sqrt(max(st["var"], 1e-4))))
            if not replay:
                st["b"] = base + 0.1 * (reward - base)
                st["var"] = 0.9 * st["var"] + 0.1 * (reward - base) ** 2
        vstd = math.sqrt(max(self.meta.get("value_var", 0.04), 1e-4))
        changed: dict[str, float] = {}
        used_value = 0
        for a, evs in by_act.items():
            for ev in evs:
                v = self.value(ev.get("state"))
                if v is None:
                    adv = advs[a]
                else:
                    adv = max(-3.0, min(3.0, (reward - v) / vstd))
                    used_value += 1
                chosen, temp = ev["chosen"], max(ev.get("temp", 1.0), 0.15)
                probs = (self.softmax([self.score(f) for f in ev["options"]], temp) if replay else ev["probs"])
                for i, feats in enumerate(ev["options"]):
                    g = ((1.0 if i == chosen else 0.0) - probs[i]) / temp
                    if abs(g) < 1e-6:
                        continue
                    for k, x in feats.items():
                        delta = lr * adv * g * x
                        lo, hi = WEIGHT_BOUNDS.get(k.partition(":")[0], (-6.0, 6.0))
                        self.weights[k] = max(lo, min(hi, self.w(k) + delta))
                        changed[k] = changed.get(k, 0.0) + delta
        if not replay:
            self.fit_value(events, reward)
            base = self.meta["baseline"] if self.meta["baseline"] is not None else reward
            self.meta["baseline"] = base + 0.1 * (reward - base)
            self.meta["var"] = 0.9 * self.meta["var"] + 0.1 * (reward - base) ** 2
            self.meta["runs_trained"] += 1
        return {"advantage": advs.get(1, 0.0), "act_advantage": advs, "value_used": used_value,
                "baseline": self.meta["baseline"], "changed": changed}

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

    def es_update_pair(self, eps: dict[str, float], f_plus: float, f_minus: float, lr: float = 0.3) -> float:
        """Antithetic ES: the same perturbation played as +eps and -eps on the same seed.
        The difference of the two fitnesses cancels seed difficulty, so each pair is a far
        cleaner gradient estimate than two independent noisy runs."""
        diff = f_plus - f_minus
        var = self.meta.setdefault("es_dvar", 0.01)
        adv = max(-3.0, min(3.0, diff / math.sqrt(max(var, 1e-4))))
        for k, e in eps.items():
            if k in self.params:
                self.params[k] = max(0.01, min(50.0, self.params[k] * math.exp(lr * adv * e)))
        self.meta["es_dvar"] = 0.9 * var + 0.1 * diff ** 2
        self.meta["es_pairs"] = self.meta.get("es_pairs", 0) + 1
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
