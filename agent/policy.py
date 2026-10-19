"""PolicyAgent: plays every move in milliseconds, no language model per move.

Combat values each playable card from its printed effects (damage, block,
debuffs, draw, energy, Strength) against the current intents, with weights
the evolution-strategy loop tunes. Every out-of-combat choice (card pick or
skip, path, rest or upgrade, shop, boss relic) is a softmax over learned
feature weights, recorded so the run's outcome can train them afterwards.
Card picks are judged against the whole deck: archetype synergy, missing
roles, duplicates and deck size all feed the score, and the run keeps a
long-term plan (archetype, cards still wanted) that the dashboard shows.

A language model is used only where it adds big-picture judgment: event
choices (Neow included) and boss relics, when enabled. If it is unreachable
the policy falls back to its own heuristics, so a run never stalls on it.
"""

from __future__ import annotations

import json
import random
import re
import urllib.request
from dataclasses import dataclass, field

from sts_bench.actions.schema import (Choose, EndTurn, PlayCard, Proceed, ReturnBack, UsePotion)
from sts_bench.actions.validate import validate
from sts_bench.agents.base import Decision
from sts_bench.state.serialize import cursory_view

import cards as C
from model import Model, hp_bucket

TIP_ONLY = {"key", "click", "wait", "state"}
EMPTY_POTION = "Potion Slot"


@dataclass
class RunTracker:
    """Everything one run decided, for learning and for the dashboard."""

    events: list = field(default_factory=list)      # learning events (softmax choices)
    picks: list = field(default_factory=list)
    path: list = field(default_factory=list)
    rests: list = field(default_factory=list)
    shops: list = field(default_factory=list)
    game_events: list = field(default_factory=list)
    potions: list = field(default_factory=list)
    boss_relics: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    plan_history: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    llm_calls: int = 0

    def count(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1


@dataclass
class Config:
    model: Model | None = None
    tracker: RunTracker = field(default_factory=RunTracker)
    temperature: float = 1.0
    params: dict = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    learn: bool = True
    llm_events: bool = True
    llm_model: str = "qwen3:14b"
    llm_base_url: str = "http://localhost:11434/v1"
    strategy: str = ""


CONFIG = Config()


def ask_llm(base_url: str, model: str, system: str, user: str, timeout: float = 120.0) -> str:
    body = json.dumps({"model": model, "temperature": 0.3,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode("utf-8")
    req = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer ollama"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _power(powers, pid: str) -> int:
    for p in powers or ():
        if p.id == pid or p.name == pid:
            return p.amount
    return 0


def _alive(monster) -> bool:
    return not monster.is_gone and not monster.half_dead and monster.current_hp > 0


def _attack(monster) -> int:
    if not _alive(monster) or "ATTACK" not in monster.intent.value:
        return 0
    dmg = monster.move_adjusted_damage
    if dmg is None or dmg < 0:
        dmg = monster.move_base_damage or 0
    return max(0, dmg) * max(1, monster.move_hits or 1)


class PolicyAgent:
    def __init__(self, provider=None, **kwargs):
        self._provider = provider
        self._skipped_rewards: dict[int, int] = {}
        self._shop_visited: set[int] = set()
        self._shop_buys: dict[int, int] = {}
        self._last_shop: tuple | None = None
        self._last_line = ""

    # sts-bench calls this after every executed step
    def record(self, line: str) -> None:
        self._last_line = line

    # ------------------------------------------------------------------ entry

    def decide(self, message) -> Decision:
        cfg, tr = CONFIG, CONFIG.tracker
        cmds = set(message.available_commands)
        if cmds and cmds <= TIP_ONLY:
            return Decision(action=None, forced_reason="tip screen")
        g = message.game_state
        if g is None:
            return Decision(action=None, forced_reason="no game state")
        screen = g.screen_type.value
        handler = {
            "EVENT": self._event, "MAP": self._map, "CARD_REWARD": self._card_reward,
            "COMBAT_REWARD": self._combat_reward, "CHEST": self._chest, "REST": self._rest,
            "SHOP_ROOM": self._shop_room, "SHOP_SCREEN": self._shop, "GRID": self._grid,
            "HAND_SELECT": self._hand_select, "BOSS_REWARD": self._boss_reward,
        }.get(screen)
        if screen == "NONE" and g.combat_state is not None and cmds & {"play", "end"}:
            handler = self._combat
        decision = Decision(action=None)
        note = ""
        try:
            if handler is not None:
                result = handler(message, decision)
                action, note = result if isinstance(result, tuple) else (result, "")
            else:
                action = self._advance(message)
        except Exception as exc:  # a policy bug must not kill the run
            action, note = None, f"policy error: {type(exc).__name__}: {exc}"
        if action is not None and not validate(action, message).ok:
            note = (note + " | " if note else "") + "invalid, advancing"
            action = self._advance(message)
        if action is None:
            decision.forced_reason = decision.forced_reason or "no policy move"
        decision.action = action
        if note:
            decision.transcript = [{"role": "assistant", "content": note}]
        tr.count("COMBAT" if handler == self._combat else screen)
        return decision

    def _advance(self, message):
        for action in (Proceed(), ReturnBack(), EndTurn(), Choose(choice_index=0)):
            if validate(action, message).ok:
                return action
        return None

    # ------------------------------------------------------------------ combat

    def _combat(self, message, decision):
        g, P = message.game_state, CONFIG.params
        c = g.combat_state
        potion = self._potion(message)
        if potion is not None:
            return potion
        player = c.player
        energy, block = player.energy, player.block
        alive = [(i, m) for i, m in enumerate(c.monsters) if _alive(m)]
        if not alive:
            return EndTurn() if "end" in message.available_commands else None
        incoming = sum(_attack(m) for _, m in alive)
        ctx = {
            "strength": _power(player.powers, "Strength"),
            "dex": _power(player.powers, "Dexterity"),
            "weak": _power(player.powers, "Weakened") > 0,
            "frail": _power(player.powers, "Frail") > 0,
            "barricade": _power(player.powers, "Barricade") > 0,
            "incoming": incoming, "block": block, "energy": energy, "alive": alive,
            "elite": g.room_type in ("MonsterRoomElite", "MonsterRoomBoss"),
            "turn": c.turn, "hp_frac": player.current_hp / max(1, player.max_hp),
            "enemy_hp": sum(m.current_hp for _, m in alive),
            "attacks_in_hand": sum(1 for h in c.hand if h.type.value == "ATTACK"),
        }
        best = (P["end_threshold"], None, "")
        for i, card in enumerate(c.hand):
            if not card.is_playable:
                continue
            cost = energy if card.cost == -1 else max(card.cost, 0)
            e = C.effects(card)
            targets = [t for t, _ in alive] if card.has_target else [None]
            for t in targets:
                v = self._card_value(card, e, t, cost, ctx, P) - P["energy_cost"] * cost
                if v > best[0]:
                    tname = f" -> {c.monsters[t].name}" if t is not None else ""
                    best = (v, PlayCard(card_index=i, target_index=t), f"play {card.name}{tname} (value {v:.1f})")
        if best[1] is None:
            return EndTurn(), ""
        return best[1], ""

    def _card_value(self, card, e, target, cost, ctx, P) -> float:
        v = 0.0
        alive = ctx["alive"]
        hits = ctx["energy"] if e.hits == 0 else e.hits
        if e.damage or e.block_scaling:
            per_hit = ctx["block"] if e.block_scaling else e.damage + ctx["strength"] * e.strength_mult
            if ctx["weak"]:
                per_hit *= 0.75
            if e.aoe:
                victims = [m for _, m in alive]
                share = 1.0
            elif e.random_target:
                victims = [m for _, m in alive]
                share = 1.0 / len(victims)
            else:
                victims = [m for i, m in alive if i == target] if target is not None else []
                share = 1.0
            for m in victims:
                mult = 1.5 if _power(m.powers, "Vulnerable") > 0 else 1.0
                total = per_hit * hits * mult * share
                through = max(0.0, total - m.block)
                v += P["damage"] * min(through, m.current_hp)
                if through >= m.current_hp:
                    v += P["kill"] + _attack(m)
        gain = 0.0
        if e.block:
            gain = (e.block + ctx["dex"]) * (0.75 if ctx["frail"] else 1.0)
        if e.double_block:
            gain += ctx["block"]
        if gain:
            need = max(0.0, ctx["incoming"] - ctx["block"])
            useful = min(gain, need)
            surplus = P["surplus_block"] * (6 if ctx["barricade"] else 1)
            v += P["block"] * useful + surplus * max(0.0, gain - useful)
        if e.vuln or e.weak:
            debuffed = [m for _, m in alive] if (e.aoe_debuff or e.aoe) else \
                [m for i, m in alive if i == target]
            for m in debuffed:
                if e.vuln:
                    follow = 1.5 if ctx["attacks_in_hand"] > 1 else 1.0
                    v += P["vuln"] * e.vuln * follow * (0.5 if _power(m.powers, "Vulnerable") else 1.0)
                if e.weak:
                    v += P["weak"] * e.weak * _attack(m) / 3.0
        if e.draw:
            v += P["draw"] * e.draw * (1.0 if ctx["energy"] - cost > 0 else 0.4)
        if e.energy:
            v += P["energy"] * e.energy
        fight = 1.0 + min(2.0, ctx["enemy_hp"] / 40.0)
        if e.strength and (not e.conditional_strength or ctx["incoming"] > 0):
            v += P["strength"] * e.strength * fight
        if e.temp_strength:
            v += P["strength"] * 0.4 * e.temp_strength * max(0, ctx["attacks_in_hand"] - 1)
        if e.double_strength:
            v += P["strength"] * ctx["strength"] * fight
        if e.power:
            v += P["power"] * (1.5 if ctx["elite"] else 1.0) * (1.0 if ctx["turn"] <= 3 else 0.5) * fight / 2
        if e.hp_loss:
            v -= P["hp_cost"] * e.hp_loss * (2.0 if ctx["hp_frac"] < 0.3 else 1.0)
        if card.type.value in ("STATUS", "CURSE"):
            v += 0.6  # playing a removable junk card clears the hand
        return v

    def _potion(self, message):
        g, P = message.game_state, CONFIG.params
        c = g.combat_state
        usable = [(i, p) for i, p in enumerate(g.potions) if p.id != EMPTY_POTION and p.can_use]
        if not usable:
            return None
        elite = g.room_type in ("MonsterRoomElite", "MonsterRoomBoss")
        hp_frac = c.player.current_hp / max(1, c.player.max_hp)
        incoming = sum(_attack(m) for m in c.monsters)
        if not ((elite and c.turn == 1) or (hp_frac < P["potion_hp"] and incoming > c.player.block)):
            return None
        slot, potion = usable[0]
        target = None
        if potion.requires_target:
            alive = [(m.current_hp, i) for i, m in enumerate(c.monsters) if _alive(m)]
            if not alive:
                return None
            target = min(alive)[1]
        CONFIG.tracker.potions.append({"floor": g.floor, "potion": potion.name,
                                       "why": "elite/boss opener" if elite and c.turn == 1 else "low HP"})
        return UsePotion(slot_index=slot, target_index=target), f"use {potion.name}"

    # ------------------------------------------------------------------ cards

    def _deck_names(self, g) -> list[str]:
        return [d.name for d in g.deck]

    def _card_features(self, card, g, commit: dict) -> dict[str, float]:
        char = g.character
        name = C.base_name(card.name)
        f = {f"card:{name}": 1.0}
        for a in C.card_archetypes(char, name):
            f[f"tag:{a}"] = 1.0
            if commit.get(a, 0) > 0:
                f[f"syn:{a}"] = commit[a]
        roles = C.roles(card)
        deck_roles: dict[str, int] = {}
        for d in g.deck:
            if C.base_name(d.name) in C.BASIC:
                continue
            for r in C.roles(d):
                deck_roles[r] = deck_roles.get(r, 0) + 1
        if "aoe" in roles and deck_roles.get("aoe", 0) == 0:
            f["need:aoe"] = 1.0
        if "block" in roles and deck_roles.get("block", 0) < 2:
            f["need:block"] = 1.0
        if "damage" in roles and deck_roles.get("damage", 0) < 3 and g.act == 1:
            f["need:damage"] = 1.0
        if "draw" in roles and deck_roles.get("draw", 0) < 2:
            f["need:draw"] = 1.0
        if card.upgrades:
            f["upgraded"] = 1.0
        dup = sum(1 for d in g.deck if C.base_name(d.name) == name)
        if dup:
            f["dup"] = float(dup)
        if len(g.deck) > 15:
            f["dilute"] = (len(g.deck) - 15) / 10.0
        return f

    def _update_plan(self, g) -> dict:
        model = CONFIG.model
        char = g.character
        commit = C.commitment(char, self._deck_names(g))
        arch = C.archetypes(char)
        if arch:
            lean = max(arch, key=lambda a: commit[a] + 0.3 * model.w(f"tag:{a}"))
        else:
            lean = None
        committed = lean if lean and commit[lean] >= 0.35 else None
        have = {C.base_name(n) for n in self._deck_names(g)}
        wanted = []
        if lean:
            keys = arch[lean]
            wanted = sorted((n for n in keys if n not in have),
                            key=lambda n: -(model.w(f"card:{n}") + keys[n]))[:5]
        core = [n for n in have if lean and n in arch[lean]]
        plan = {"archetype": committed or (f"Undecided (leaning {lean})" if lean else "Undecided"),
                "lean": lean, "commitment": round(commit.get(lean, 0.0), 2) if lean else 0.0,
                "core": sorted(core), "wanted": wanted, "floor": g.floor}
        tr = CONFIG.tracker
        if not tr.plan_history or tr.plan_history[-1][1] != plan["archetype"]:
            tr.plan_history.append([g.floor, plan["archetype"]])
        tr.plan = plan
        return commit

    def _card_reward(self, message, decision):
        g, model, tr = message.game_state, CONFIG.model, CONFIG.tracker
        offered = g.screen.cards
        commit = self._update_plan(g)
        options = [self._card_features(card, g, commit) for card in offered]
        can_skip = validate(ReturnBack(), message).ok
        if can_skip:
            options.append({f"skip:a{min(g.act, 4)}": 1.0})
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng)
        labels = [card.name for card in offered] + (["(skip)"] if can_skip else [])
        tr.events.append({"kind": "card", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        tr.picks.append({"floor": g.floor, "offered": labels, "chosen": labels[idx],
                         "p": round(probs[idx], 2), "archetype": tr.plan.get("archetype")})
        ranked = sorted(zip(labels, probs), key=lambda x: -x[1])
        note = "card pick: " + ", ".join(f"{n} {p:.0%}" for n, p in ranked) + \
               f" | plan {tr.plan.get('archetype')} wants {', '.join(tr.plan.get('wanted', [])[:3]) or '-'}"
        if can_skip and idx == len(offered):
            self._skipped_rewards[g.floor] = self._skipped_rewards.get(g.floor, 0) + 1
            return ReturnBack(), note + " -> SKIP"
        name = offered[idx].name.lower()
        choice = g.choice_list.index(name) if name in g.choice_list else idx
        return Choose(choice_index=choice), note + f" -> {offered[idx].name}"

    def _combat_reward(self, message, decision):
        g = message.game_state
        empty_slots = sum(1 for p in g.potions if p.id == EMPTY_POTION)
        skipped = self._skipped_rewards.get(g.floor, 0)
        cards_seen = 0
        for i, r in enumerate(g.screen.rewards):
            kind = r.reward_type
            if kind in ("GOLD", "STOLEN_GOLD", "RELIC"):
                return Choose(choice_index=i), f"take {kind.lower()}"
            if kind == "POTION" and empty_slots > 0:
                return Choose(choice_index=i), "take potion"
        for i, r in enumerate(g.screen.rewards):
            if r.reward_type == "CARD":
                if cards_seen >= skipped:
                    return Choose(choice_index=i), "open card reward"
                cards_seen += 1
        return self._advance(message), "rewards done"

    # ------------------------------------------------------------------ map / rooms

    def _map(self, message, decision):
        g, model, tr = message.game_state, CONFIG.model, CONFIG.tracker
        s = g.screen
        nodes = s.next_nodes
        if not nodes:
            return (Choose(choice_index=0) if g.choice_list else self._advance(message)), "boss"
        hb = hp_bucket(g.current_hp, g.max_hp)
        act = min(g.act, 4)
        by_pos = {(n.x, n.y): n for n in g.map}
        memo: dict = {}

        def node_score(sym: str) -> float:
            v = model.w(f"node:{sym}:h{hb}") + model.w(f"nodeact:{sym}:a{act}")
            if sym == "$":
                v += 0.6 if g.gold >= 150 else (-0.4 if g.gold < 75 else 0.0)
            return v

        def route(x: int, y: int, depth: int = 0) -> float:
            if (x, y) in memo:
                return memo[(x, y)]
            n = by_pos.get((x, y))
            if n is None or depth > 20:
                return 0.0
            best_child = max((route(ch.x, ch.y, depth + 1) for ch in n.children), default=0.0)
            memo[(x, y)] = node_score(n.symbol) + 0.85 * best_child
            return memo[(x, y)]

        options, bonus, labels = [], [], []
        for n in nodes:
            sym = n.symbol or "?"
            options.append({f"node:{sym}:h{hb}": 1.0, f"nodeact:{sym}:a{act}": 1.0})
            full = by_pos.get((n.x, n.y))
            future = max((route(ch.x, ch.y) for ch in full.children), default=0.0) if full else 0.0
            bonus.append(0.85 * future + (0.6 if sym == "$" and g.gold >= 150 else 0.0))
            labels.append(sym)
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        tr.events.append({"kind": "path", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        tr.path.append({"floor": g.floor + 1, "node": labels[idx], "hp": g.current_hp,
                        "max_hp": g.max_hp})
        names = {"M": "fight", "E": "ELITE", "R": "rest", "$": "shop", "?": "event", "T": "chest"}
        note = "path: " + ", ".join(f"{names.get(l, l)} {p:.0%}" for l, p in zip(labels, probs)) + \
               f" -> {names.get(labels[idx], labels[idx])}"
        return Choose(choice_index=idx), note

    def _chest(self, message, decision):
        g = message.game_state
        if not g.screen.chest_open and "open" in g.choice_list:
            return Choose(choice_index=g.choice_list.index("open")), "open chest"
        return self._advance(message), ""

    def _rest(self, message, decision):
        g, model, tr = message.game_state, CONFIG.model, CONFIG.tracker
        if g.screen.has_rested or not g.choice_list:
            return self._advance(message), ""
        hb = hp_bucket(g.current_hp, g.max_hp)
        options, labels, bonus = [], [], []
        static = {"dig": 1.5, "lift": 0.8 if hb > 0 else -1.0, "toke": 0.3}
        for i, opt in enumerate(g.choice_list):
            if opt in ("rest", "smith"):
                options.append({f"{opt}:h{hb}": 1.0}); bonus.append(0.0)
            elif opt in static:
                options.append({f"restx:{opt}": 1.0}); bonus.append(static[opt])
            else:
                continue
            labels.append((i, opt))
        if not options:
            return self._advance(message), ""
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        tr.events.append({"kind": "rest", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        tr.rests.append({"floor": g.floor, "choice": labels[idx][1], "hp": g.current_hp,
                         "max_hp": g.max_hp})
        note = "rest site: " + ", ".join(f"{o} {p:.0%}" for (_, o), p in zip(labels, probs))
        return Choose(choice_index=labels[idx][0]), note

    def _shop_room(self, message, decision):
        g = message.game_state
        if g.floor not in self._shop_visited and g.gold >= 50 and "choose" in message.available_commands:
            self._shop_visited.add(g.floor)
            return Choose(choice_index=0), "enter shop"
        return self._advance(message), "leave shop room"

    def _shop(self, message, decision):
        g, model, tr = message.game_state, CONFIG.model, CONFIG.tracker
        s = g.screen
        buys = self._shop_buys.get(g.floor, 0)
        if buys >= 6:
            return ReturnBack(), "shop: done"
        commit = self._update_plan(g)
        choices = g.choice_list
        options, bonus, labels = [], [], []
        junk = [d for d in g.deck if d.type.value == "CURSE" or C.base_name(d.name) in C.BASIC]
        if "purge" in choices and s.purge_available and junk:
            options.append({"shop:purge": 1.0, "shop:price": s.purge_cost / 100}); bonus.append(0.0)
            labels.append(("purge", choices.index("purge")))
        for card in s.cards:
            n = card.name.lower()
            if n in choices:
                f = self._card_features(card, g, commit)
                f["shop:price"] = (card.price or 0) / 100
                options.append(f); bonus.append(0.0); labels.append((card.name, choices.index(n)))
        for relic in s.relics:
            n = relic.name.lower()
            if n in choices:
                options.append({f"shoprelic:{relic.name}": 1.0, "shop:price": (relic.price or 0) / 100})
                bonus.append(0.0); labels.append((relic.name, choices.index(n)))
        if any(p.id == EMPTY_POTION for p in g.potions):
            for potion in s.potions:
                n = potion.name.lower()
                if n in choices:
                    options.append({"shop:potion": 1.0, "shop:price": (potion.price or 0) / 100})
                    bonus.append(0.0); labels.append((potion.name, choices.index(n)))
        options.append({"shop:leave": 1.0}); bonus.append(0.0); labels.append(("(leave)", None))
        if self._last_shop and self._last_shop[:2] == (g.floor, g.gold):
            # the last purchase did not go through (gold unchanged): never retry it
            failed = self._last_shop[2]
            for k, (label, _) in enumerate(labels):
                if label == failed:
                    del options[k], bonus[k], labels[k]
                    break
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        tr.events.append({"kind": "shop", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        name, choice = labels[idx]
        tr.shops.append({"floor": g.floor, "bought": name, "gold": g.gold})
        note = "shop: " + ", ".join(f"{l} {p:.0%}" for (l, _), p in
                                    sorted(zip(labels, probs), key=lambda x: -x[1])[:5])
        if choice is None:
            return ReturnBack(), note + " -> leave"
        self._shop_buys[g.floor] = buys + 1
        self._last_shop = (g.floor, g.gold, name)
        return Choose(choice_index=choice), note + f" -> buy {name}"

    # ------------------------------------------------------------------ selections

    def _card_worth(self, card, g) -> float:
        if card.type.value in ("CURSE", "STATUS"):
            return -5.0
        v = CONFIG.model.w(f"card:{C.base_name(card.name)}")
        plan = CONFIG.tracker.plan
        if plan.get("lean") and C.base_name(card.name) in C.archetypes(g.character).get(plan["lean"], {}):
            v += 0.4
        return v

    def _grid(self, message, decision):
        g = message.game_state
        s = g.screen
        chosen = {c.uuid for c in s.selected_cards}
        if s.confirm_up and (len(chosen) >= s.num_cards or s.any_number):
            return Proceed(), "confirm selection"
        pool = [(i, c) for i, c in enumerate(s.cards) if c.uuid not in chosen]
        if not pool:
            return self._advance(message), ""
        if s.for_purge or s.for_transform:
            i, card = min(pool, key=lambda x: self._card_worth(x[1], g))
            why = "remove" if s.for_purge else "transform"
        elif s.for_upgrade:
            i, card = max(pool, key=lambda x: self._card_worth(x[1], g) - (1.0 if x[1].upgrades else 0))
            why = "upgrade"
        elif s.any_number:
            junk = [(i, c) for i, c in pool if self._card_worth(c, g) < -0.5]
            if not junk:
                return (Proceed() if s.confirm_up else self._advance(message)), "nothing to pick"
            i, card = junk[0]
            why = "pick junk"
        else:
            i, card = max(pool, key=lambda x: self._card_worth(x[1], g))
            why = "pick"
        return Choose(choice_index=i), f"{why} {card.name}"

    def _hand_select(self, message, decision):
        g = message.game_state
        s = g.screen
        if len(s.selected) >= s.max_cards:
            return Proceed(), "confirm"
        taken = {c.uuid for c in s.selected}
        pool = [(i, c) for i, c in enumerate(s.hand) if c.uuid not in taken]
        if not pool:
            return Proceed(), "confirm"
        action = (g.current_action or "").lower()
        if "upgrade" in action or "armaments" in action:
            i, card = max(pool, key=lambda x: self._card_worth(x[1], g) - (2.0 if x[1].upgrades else 0))
            return Choose(choice_index=i), f"upgrade {card.name}"
        if "retain" in action:
            i, card = max(pool, key=lambda x: self._card_worth(x[1], g))
            return Choose(choice_index=i), f"keep {card.name}"
        i, card = min(pool, key=lambda x: self._card_worth(x[1], g))
        if s.can_pick_zero and self._card_worth(card, g) > 0.5:
            return Proceed(), "keep hand"
        return Choose(choice_index=i), f"select {card.name}"

    # ------------------------------------------------------------------ big picture

    def _boss_reward(self, message, decision):
        g, model, tr = message.game_state, CONFIG.model, CONFIG.tracker
        relics = g.screen.relics
        if CONFIG.llm_events:
            pick = self._ask_option(message, [r.name for r in relics], "Choose a boss relic.")
            if pick is not None:
                tr.boss_relics.append({"floor": g.floor, "relic": relics[pick].name, "by": "llm"})
                return Choose(choice_index=pick), f"boss relic (LLM): {relics[pick].name}"
        options = [{f"relic:{r.name}": 1.0} for r in relics]
        can_skip = validate(ReturnBack(), message).ok
        if can_skip:
            options.append({"relic:skip": 1.0})
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng)
        tr.events.append({"kind": "boss_relic", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        if can_skip and idx == len(relics):
            tr.boss_relics.append({"floor": g.floor, "relic": "(skip)", "by": "policy"})
            return ReturnBack(), "boss relic: skip"
        tr.boss_relics.append({"floor": g.floor, "relic": relics[idx].name, "by": "policy"})
        return Choose(choice_index=idx), f"boss relic: {relics[idx].name}"

    def _event(self, message, decision):
        g, tr = message.game_state, CONFIG.tracker
        s = g.screen
        live = [o for o in s.options if not o.disabled and o.choice_index is not None]
        if not live:
            return self._advance(message), ""
        if len(live) == 1:
            return Choose(choice_index=live[0].choice_index), f"event: {live[0].label}"
        pick, by = None, "heuristic"
        if CONFIG.llm_events:
            k = self._ask_option(message, [f"{o.label} {o.text}".strip() for o in live],
                                 f"Event: {s.event_name}.")
            if k is not None:
                pick, by = live[k], "llm"
        if pick is None:
            pick = max(live, key=lambda o: self._event_score(o.text + " " + o.label, g))
        tr.game_events.append({"floor": g.floor, "event": s.event_name, "choice": pick.label, "by": by})
        return Choose(choice_index=pick.choice_index), f"event {s.event_name} ({by}): {pick.label}"

    @staticmethod
    def _event_score(text: str, g) -> float:
        t = text.lower()
        hp_frac = g.current_hp / max(1, g.max_hp)
        s = 0.0
        if "relic" in t and ("obtain" in t or "gain" in t or "receive" in t):
            s += 2.0
        if "rare card" in t:
            s += 1.5
        if "remove" in t:
            s += 1.5
        if "1 hp" in t:
            s += 2.0
        if "upgrade" in t:
            s += 1.0
        if "max hp" in t:
            s += 1.0
        if "transform" in t:
            s += 0.6
        if "gold" in t:
            s += -0.6 if "lose" in t and "gold" in t.split("lose", 1)[1][:20] else 0.6
        if "heal" in t:
            s += 1.5 * (1 - hp_frac)
        if "curse" in t or "cursed" in t:
            s -= 2.5
        m = re.search(r"(?:lose|take) (\d+) (?:hp|damage)", t)
        if m:
            s -= int(m.group(1)) / 8.0 * (2.0 if hp_frac < 0.4 else 1.0)
        if "leave" in t or "ignore" in t:
            s += 0.1
        return s

    def _ask_option(self, message, labels: list[str], header: str) -> int | None:
        g, tr = message.game_state, CONFIG.tracker
        plan = tr.plan or {}
        system = (f"You make the big-picture choices in a Slay the Spire run as the {g.character}. "
                  "Use the playbook and the run plan. Reply with only the number of your choice.\n\n"
                  f"PLAYBOOK:\n{CONFIG.strategy[:3500]}\n\n"
                  f"RUN PLAN: archetype {plan.get('archetype', 'undecided')}; "
                  f"wanted {', '.join(plan.get('wanted', [])) or '-'}\n/no_think")
        listing = "\n".join(f"{i}: {label}" for i, label in enumerate(labels))
        user = f"{cursory_view(message)}\n\n{header}\nOptions:\n{listing}\n\nAnswer with the option number only."
        try:
            tr.llm_calls += 1
            answer = ask_llm(CONFIG.llm_base_url, CONFIG.llm_model, system, user, timeout=90)
        except Exception:
            return None
        m = re.search(r"\d+", answer)
        if not m:
            return None
        k = int(m.group(0))
        return k if 0 <= k < len(labels) else None
