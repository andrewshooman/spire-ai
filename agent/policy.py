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

import collections
import json
import random
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from sts_bench.actions.schema import (Choose, EndTurn, PlayCard, Proceed, ReturnBack, UsePotion)
from sts_bench.actions.validate import validate
from sts_bench.agents.base import Decision
from sts_bench.runner.session import describe_action
from sts_bench.state.serialize import cursory_view
from sts_bench.tools.card_db import card_text, upgraded_card_text

import cards as C
import evidence
import synergy
import gamedata
import planner
import relics as relics_mod
from model import Model, hp_bucket

TIP_ONLY = {"key", "click", "wait", "state"}
EMPTY_POTION = "Potion Slot"

POTION_KINDS = {
    "defense": ("Block Potion", "Ghost In A Jar", "Ghost in a Jar", "Weak Potion", "Heart of Iron",
                "Essence of Steel", "Liquid Bronze", "Dexterity Potion", "Speed Potion"),
    "buff": ("Strength Potion", "Steroid Potion", "Flex Potion", "Cultist Potion", "Ancient Potion",
             "Focus Potion", "Bottled Miracle", "Essence of Darkness", "Potion of Capacity", "Stance Potion"),
    "damage": ("Fire Potion", "Explosive Potion", "Poison Potion", "Fear Potion", "Cunning Potion"),
    "tempo": ("Energy Potion", "Swift Potion", "Gambler's Brew", "Snecko Oil", "Distilled Chaos",
              "Duplication Potion", "Liquid Memories", "Attack Potion", "Skill Potion", "Power Potion",
              "Colorless Potion", "Elixir", "Blessing of the Forge", "Ambrosia"),
    "heal": ("Blood Potion", "Regen Potion"),
    "juice": ("Fruit Juice",),
    "brew": ("Entropic Brew",),
    "escape": ("Smoke Bomb",),
    "never": ("Fairy in a Bottle",),
}
_POTION_INDEX = {n.lower(): k for k, names in POTION_KINDS.items() for n in names}
PERMANENT_POTIONS = {"Strength Potion", "Cultist Potion", "Ancient Potion", "Fruit Juice", "Potion of Capacity"}


def potion_kind(name: str) -> str:
    return _POTION_INDEX.get((name or "").lower(), "tempo")


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
    llm_ms: int = 0
    llm_cached: int = 0
    llm_log: list = field(default_factory=list)
    fight_log: list = field(default_factory=list)   # deck + HP at each fight's start (simulator calibration)

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
    live: object = None          # live.LiveView, or None when nobody is watching
    llm_shop: bool = True        # one LLM shopping list per shop visit
    llm_cache: dict | None = None  # event answers reused across runs (evolve.py persists it)
    llm_gap: float = 99.0        # ask the LLM about an event only when the top options are this close
    llm_cards: bool = True       # the LLM advises card rewards too
    llm_share: float = 0.7       # explore runs: share of card picks that follow the LLM
    risk: float = 0.0            # explore runs sample a risk appetite: extra pull toward elites in acts 1-2
    flavor: str | None = None    # explore runs sample a build flavor (an archetype) to try early in act 1
    evidence: dict | None = None # this bot's track record (evidence.build), shown to the LLM
    human_evidence: dict | None = None  # the same from runs the player played (human.py)
    ab: dict = field(default_factory=dict)  # per-run A/B switches (explore runs); missing = on


CONFIG = Config()
LLM_BUSY = threading.Event()   # set while the coach holds the GPU: in-game questions skip the LLM
LLM_TIMEOUT = 20.0             # in-game calls give up after this (the policy decides instead)
LLM_COOLDOWN = 120.0           # after a failed call, skip the LLM this long instead of stacking waits
_LLM_DOWN_UNTIL = [0.0]
ON_LLM_FAILURE = None          # evolve.py sets a health check that restarts a hung Ollama


def ask_llm(base_url: str, model: str, system: str, user: str, timeout: float = 120.0,
            think: bool = False, max_tokens: int | None = None) -> str:
    """OpenAI-compatible chat call. think=False switches qwen3's reasoning off at the server
    (reasoning_effort none): a '/no_think' tag alone was ignored, so every in-game answer
    spent 500-800 hidden tokens thinking (10-30 s instead of about 0.2 s)."""
    payload = {"model": model, "temperature": 0.3,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if not think:
        payload["reasoning_effort"] = "none"
    if max_tokens:
        payload["max_tokens"] = max_tokens
    body = json.dumps(payload).encode("utf-8")
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
        self._potions_used: dict[tuple, int] = {}
        self._stall = None          # (state signature, turns ended in a row without change)
        self._stall_turn = None
        self._shop_list: dict[int, list[str]] = {}
        self._mk: dict = {}           # Match and Keep! memory: floor, index -> card name, picks
        self._skull: dict[int, int] = {}  # Knowing Skull asks per floor
        self._pos: tuple | None = None    # (act, x, y) of the last map node chosen

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
        if g.combat_state is not None and screen == "NONE" and not cmds & {"play", "end"}:
            if cfg.live is not None:
                cfg.live.update(message, "", "", tr)
            return Decision(action=None, forced_reason="waiting")  # enemy turn or animation
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
        n_events = len(tr.events)
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
        if len(tr.events) > n_events:
            st = self._state_features(g)
            for ev in tr.events[n_events:]:
                ev.setdefault("act", g.act)  # per-act credit assignment in model.learn
                ev.setdefault("state", st)   # input to the learned value estimate
        tr.count("COMBAT" if handler == self._combat else screen)
        if cfg.live is not None:
            try:
                text = describe_action(action, message) if action is not None else f"({decision.forced_reason})"
            except Exception:
                text = str(action)
            cfg.live.update(message, text, note, tr, force=screen in ("GAME_OVER", "BOSS_REWARD"))
        return decision

    def _state_features(self, g) -> dict:
        """Compact run state for the value estimate (all roughly 0..1)."""
        deck = list(g.deck)
        roles = collections.Counter(r for d in deck for r in C.roles(d))
        elites = sum(1 for p in CONFIG.tracker.path if p.get("node") == "E")
        return {
            "hp": g.current_hp / max(1, g.max_hp), "maxhp": g.max_hp / 100, "fl": g.floor / 50,
            "a2": 1.0 if g.act == 2 else 0.0, "a3": 1.0 if g.act >= 3 else 0.0,
            "deck": len(deck) / 30, "basic": sum(1 for d in deck if C.base_name(d.name) in C.BASIC) / 10,
            "up": sum(1 for d in deck if d.upgrades) / 10, "relics": len(g.relics) / 10,
            "gold": min(g.gold, 500) / 300, "curse": sum(1 for d in deck if d.type.value == "CURSE") / 3,
            "scal": roles["scaling"] / 5, "aoe": roles["aoe"] / 3, "blk": roles["block"] / 6,
            "dmg": roles["damage"] / 8, "draw": roles["draw"] / 4, "elites": elites / 5,
        }

    def _advance(self, message):
        for action in (Proceed(), ReturnBack(), EndTurn(), Choose(choice_index=0)):
            if validate(action, message).ok:
                return action
        return None

    # ------------------------------------------------------------------ combat

    def _combat(self, message, decision):
        g, P = message.game_state, CONFIG.params
        c = g.combat_state
        if not any(_alive(m) for m in c.monsters):
            return (EndTurn() if "end" in message.available_commands else None), ""
        tr = CONFIG.tracker
        if c.turn <= 1 and (not tr.fight_log or tr.fight_log[-1]["floor"] != g.floor):
            tr.fight_log.append({"floor": g.floor, "room": g.room_type, "hp": c.player.current_hp,
                                 "max_hp": c.player.max_hp, "deck": self._deck_names(g),
                                 "enemies": [m.name for m in c.monsters],
                                 "relics": [r.name for r in g.relics]})
        # stall guard: enemy HP unchanged from one turn to the next (Thorns enemies the bot won't hit, whatever
        # it plays) means the fight is not progressing; each such turn discounts self-damage further
        sig = (g.floor, tuple(m.current_hp for m in c.monsters))
        if c.turn != self._stall_turn:
            stalled = self._stall is not None and self._stall[0] == sig
            self._stall = (sig, (self._stall[1] + 1) if stalled else 0)
            self._stall_turn = c.turn
        stall = self._stall[1] if self._stall and self._stall[0] == sig else 0
        plan = planner.plan_turn(message, P, self._deck_names(g), stall=stall)
        note = plan.describe(c.hand, c.monsters) + (f" | stalled {stall} turns" if stall else "")
        potion = self._potion(message, plan)
        if potion is not None:
            return potion[0], potion[1] + " | " + note
        threshold = P["end_threshold"] if stall < 2 else 0.0
        if not plan.steps or plan.value - plan.base < threshold:
            return EndTurn(), note + " -> end turn"
        i, t = plan.steps[0]
        return PlayCard(card_index=i, target_index=t), note

    def _potion(self, message, plan):
        """Potions per the playbook: never die holding one, spend them in boss fights,
        one opener in elites, defensive ones when low; Fruit Juice right away."""
        g, P = message.game_state, CONFIG.params
        c = g.combat_state
        usable = [(i, p) for i, p in enumerate(g.potions)
                  if p.id != EMPTY_POTION and p.can_use and potion_kind(p.name) != "never"]
        if not usable:
            return None
        hp = c.player.current_hp
        hp_frac = hp / max(1, c.player.max_hp)
        taken = plan.summary.get("taken", 0)
        boss = g.room_type == "MonsterRoomBoss"
        elite = g.room_type == "MonsterRoomElite"
        empty = sum(1 for p in g.potions if p.id == EMPTY_POTION)
        key = (g.floor, c.turn)
        used_now = self._potions_used.get(key, 0)

        attacks_now = any(c.hand[i].type.value == "ATTACK" for i, _ in plan.steps)

        def wasted(p) -> bool:
            n = p.name
            if n in ("Flex Potion", "Steroid Potion") and not attacks_now:
                return True   # temporary Strength on a turn without attacks
            if n == "Snecko Oil" and len(c.hand) >= 8:
                return True   # draws into a full hand and randomizes costs
            return False

        def pick(kinds, keep_one: bool = False):
            pool = [(s, p) for s, p in usable if not wasted(p)]
            if keep_one:
                # keep one potion for a lethal turn, unless it is permanent power
                pool = [(s, p) for s, p in pool if len(usable) >= 2 or p.name in PERMANENT_POTIONS]
            for kind in kinds:
                for slot, p in pool:
                    if potion_kind(p.name) == kind and not (kind == "escape" and boss):
                        return slot, p
            return None

        choice, why = None, ""
        if pick(["juice"]):
            choice, why = pick(["juice"]), "Fruit Juice is permanent max HP"
        elif empty >= 2 and pick(["brew"]):
            choice, why = pick(["brew"]), "Entropic Brew refills empty slots"
        elif taken >= hp:
            choice, why = pick(["defense", "heal", "damage", "buff", "tempo", "escape"]), "lethal incoming"
        elif boss and taken >= max(20, 0.35 * hp):
            # the big turns: Champ's Execute, Automaton's Hyper Beam, Collector's spike
            choice, why = pick(["defense", "heal"]), "boss big hit"
        elif boss and c.turn <= 2:
            choice, why = pick(["buff", "tempo", "damage"], keep_one=True), "boss fight: potions are free power"
        elif boss and hp_frac < 0.5:
            choice, why = pick(["heal", "defense", "buff", "damage", "tempo"]), "boss fight, HP low"
        elif elite and c.turn == 1 and used_now == 0 and (len(usable) >= 2 or hp_frac < 0.6) \
                and g.floor % 17 < 12:  # close to the boss: keep them for it
            choice, why = pick(["buff", "damage", "tempo"]), "elite opener"
        elif hp_frac < P["potion_hp"] and taken > 0:
            choice, why = pick(["defense", "heal"]), "low HP"
        if choice is None:
            return None
        slot, potion = choice
        target = None
        if potion.requires_target:
            live = [(i, m) for i, m in enumerate(c.monsters) if _alive(m)]
            if not live:
                return None
            if potion_kind(potion.name) == "defense":   # Weak: the biggest attacker
                target = max(live, key=lambda x: _attack(x[1]))[0]
            else:
                target = min(live, key=lambda x: x[1].current_hp)[0]
        self._potions_used[key] = used_now + 1
        CONFIG.tracker.potions.append({"floor": g.floor, "potion": potion.name, "why": why})
        return UsePotion(slot_index=slot, target_index=target), f"use {potion.name} ({why})"

    # ------------------------------------------------------------------ cards

    def _deck_names(self, g) -> list[str]:
        return [d.name for d in g.deck]

    def _relic_features(self, name: str, g, commit: dict, kind: str, prof: dict | None = None) -> dict[str, float]:
        """Relic choice features (boss relics: kind 'relic', shop relics: 'shoprelic'): the relic, the relic in
        this build / act / kind of deck (cost, draw, exhaust, clog, HP, gold buckets), and the deck-fit score,
        so the bot learns which relics pay off in which decks."""
        prof = prof or relics_mod.profile(g)
        f = {f"{kind}:{name}": 1.0, f"relicctx:{name}:a{min(g.act, 3)}": 1.0}
        for b in relics_mod.buckets(prof):
            f[f"relicctx:{name}|{b}"] = 1.0
        lean = (CONFIG.tracker.plan or {}).get("lean")
        if lean and commit.get(lean, 0.0) >= 0.35:
            f[f"relicctx:{name}|{lean}"] = 1.0
        if len(g.deck) >= 28:
            f[f"relicctx:{name}:big"] = 1.0
        if kind == "relic":
            fit = relics_mod.boss_fit(name, prof)[0]
            if fit:
                f["relicfit:boss"] = fit   # how much the deck-fit judgment counts is learned too (prior 1.0)
            return f
        fit, _ = synergy.relic_fit(name, [d.name for d in g.deck])
        if name in ("Lantern", "Happy Flower", "Ice Cream", "Sundial"):
            fit += 0.5 * relics_mod.energy_value(prof)[0]
        if fit:
            tags = sorted(synergy.RELIC_TAGS.get(name, {"special"}))
            for t in tags:
                f[f"relicfit:{t}"] = fit / len(tags)
        return f

    def _card_features(self, card, g, commit: dict) -> dict[str, float]:
        char = g.character
        name = C.base_name(card.name)
        f = {f"card:{name}": 1.0, f"cardctx:{name}:a{min(g.act, 3)}": 1.0}
        lean = (CONFIG.tracker.plan or {}).get("lean")
        if lean and commit.get(lean, 0.0) >= 0.35:
            f[f"cardctx:{name}|{lean}"] = 1.0
        if len(g.deck) >= 28:
            f[f"cardctx:{name}:big"] = 1.0
        sf, _ = synergy.explain(card.name, [d.name for d in g.deck], [r.name for r in g.relics])
        f.update(sf)
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
        if "scaling" in roles and deck_roles.get("scaling", 0) < 2 and g.floor >= 6:
            f["need:scaling"] = 1.0
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
            if CONFIG.flavor in arch and g.act <= 2 and commit.get(CONFIG.flavor, 0) + 0.3 >= commit[lean]:
                lean = CONFIG.flavor  # this explore run is testing that build
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

    @staticmethod
    def _boss_needs_now(g) -> dict:
        """The act boss's needs, stronger in the second half of the act when it is close."""
        needs = C.BOSS_NEEDS.get(g.act_boss, {})
        late = (g.floor % 17) >= 8 or g.floor % 17 == 0
        return {k: v * (1.0 if late else 0.5) for k, v in needs.items()}

    def _card_bonus(self, card, g) -> float:
        b = sum(v for r, v in self._boss_needs_now(g).items() if r in C.roles(card))
        if CONFIG.flavor and g.act <= 2 and C.base_name(card.name) in C.archetypes(g.character).get(CONFIG.flavor, {}):
            b += 1.0 if g.act == 1 else 0.6  # this run tests a build; learning sees how it goes
        return b

    @staticmethod
    def _skip_bonus(g) -> float:
        n = len(g.deck)
        if CONFIG.ab.get("skip_rules", True):
            # data: act 2 boss win rate 56% with 21-25 cards, 43% with 26-30, 31% with 31+
            if n >= 28:
                return 2.0
            if n >= 24 or (g.act >= 2 and n >= 22):
                return 1.0
            return 0.0
        if g.act >= 2 and n >= 32:
            return 1.0
        if g.act >= 2 and n >= 25:
            return 0.6
        return 0.0

    def _earns_slot(self, card, g, commit: dict) -> tuple[bool, str]:
        """In a big deck a card must bring something specific: an engine, real synergy with cards already
        in the deck, the act boss's need, or a key card of the build the deck is committed to."""
        name = C.base_name(card.name)
        if name in C.ENGINE_CARDS:
            return True, "engine card"
        sf, _ = synergy.explain(card.name, [d.name for d in g.deck], [r.name for r in g.relics])
        if sum(v for k, v in sf.items() if k.startswith("synrule:")) >= 2.0:
            return True, "synergy with the deck"
        if any(r in C.roles(card) for r in self._boss_needs_now(g)):
            return True, "covers the boss's need"
        lean = (CONFIG.tracker.plan or {}).get("lean")
        if lean and commit.get(lean, 0) >= 0.5 and C.archetypes(g.character).get(lean, {}).get(name, 0) >= 0.8:
            return True, f"key {lean} card"
        return False, "no engine, synergy or boss need"

    def _weakest(self, g, n: int = 3) -> list[str]:
        pool = {d.name: self._card_worth(d, g) for d in g.deck
                if C.base_name(d.name) not in C.BASIC and d.type.value not in ("CURSE", "STATUS")}
        return [k for k, _ in sorted(pool.items(), key=lambda kv: kv[1])[:n]]

    def _pick_guidance(self, g, commit: dict) -> str:
        syn = ", ".join(f"{a} {v:.0%}" for a, v in sorted(commit.items(), key=lambda kv: -kv[1]) if v > 0)
        needs = ", ".join(k for k, _ in sorted(C.BOSS_NEEDS.get(g.act_boss, {}).items(), key=lambda kv: -kv[1]))
        engines = sorted({C.base_name(d.name) for d in g.deck} & C.ENGINE_CARDS)
        text = (f"Synergies so far: {syn or 'none yet'}. Engines in the deck: {', '.join(engines) or 'none yet'}. "
                "Build around the most powerful cards found so far (engines, rares) rather than a fixed archetype; "
                "early in act 1 take strong flexible cards (damage, area damage, block, draw).")
        if CONFIG.flavor and g.act <= 2:
            text += (f" This training run tests a {CONFIG.flavor} build: favor its key cards when they are "
                     "reasonable picks, so the bot learns how that build performs.")
        if needs:
            text += f" The act boss {g.act_boss} tests: {needs}."
        if g.act == 1:
            text += (" Act 1: pick what saves HP in the next fights (front-loaded damage, area damage); the "
                     "guide skips Heavy Blade, Limit Break, Sword Boomerang, Flex, Wild Strike, Body Slam, Havoc, "
                     "Exhume, Sentinel, Warcry, Clash this early. Synergy notes only count real engine cards, never "
                     "starter Strikes/Defends.")
        n = len(g.deck)
        if CONFIG.ab.get("skip_rules", True) and (g.act >= 2 or n >= 22):
            weak = ", ".join(self._weakest(g)) or "-"
            text += (f" Deck size {n}: every extra card makes the best cards come up less often (winning decks reach "
                     "the act 2 boss with about 25 cards). Take a card only if it is (a) an engine or a payoff for "
                     "synergies the deck already has, (b) an answer to the boss need above, or (c) clearly stronger "
                     f"than the deck's weakest cards ({weak}). Otherwise choose Skip; skipping is often right.")
        elif g.act >= 2 and n >= 25:
            text += f" The deck already has {n} cards: skip unless the card is clearly better than the deck's average card."
        return text

    @staticmethod
    def _syn_note(name: str, g) -> str:
        if not CONFIG.ab.get("syn_notes", True):
            return ""
        _, why = synergy.explain(name, [d.name for d in g.deck], [r.name for r in g.relics])
        return f"({why})" if why else ""

    def _card_reward(self, message, decision):
        g, model, tr = message.game_state, CONFIG.model, CONFIG.tracker
        offered = g.screen.cards
        if g.combat_state is not None and offered:
            k = max(range(len(offered)), key=lambda j: self._card_worth(offered[j], g))
            return Choose(choice_index=k), f"combat card choice: {offered[k].name}"
        commit = self._update_plan(g)
        options = [self._card_features(card, g, commit) for card in offered]
        can_skip = validate(ReturnBack(), message).ok
        if can_skip:
            options.append({f"skip:a{min(g.act, 4)}": 1.0})
        bonus = [self._card_bonus(card, g) for card in offered] + ([self._skip_bonus(g)] if can_skip else [])
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        labels = [card.name for card in offered] + (["(skip)"] if can_skip else [])
        by = "policy"
        # with thinking off the LLM answers in ~0.2 s, so it advises every card pick; explore runs
        # still follow the policy's own sample part of the time so learning keeps exploring
        if CONFIG.llm_events and CONFIG.llm_cards and (not CONFIG.learn or CONFIG.rng.random() < CONFIG.llm_share):
            described = []
            for k, card in enumerate(offered):
                text = (card_text(card) or "").replace("\n", " ")
                described.append(f"{card.name} ({card.type.value.lower()}, cost {card.cost}): {text} "
                                 f"[policy {probs[k]:.0%}] {self._syn_note(card.name, g)} "
                                 f"{evidence.card(CONFIG.evidence, card.name, g.act)} "
                                 f"{evidence.mine(evidence.card(CONFIG.human_evidence, card.name, g.act))}".strip())
            if can_skip:
                described.append(f"Skip this reward: keep the {len(g.deck)}-card deck lean [policy {probs[-1]:.0%}]")
            deck = ", ".join(f"{n} x{k}" if k > 1 else n for n, k in
                             sorted(collections.Counter(d.name for d in g.deck).items()))
            k = self._ask_option(message, described,
                                 f"Card reward on floor {g.floor} (act {g.act}, boss {g.act_boss}). "
                                 f"Deck ({len(g.deck)}): {deck}. {self._pick_guidance(g, commit)} "
                                 f"Pick the card that most improves this deck for the fights ahead, or skip if none does.",
                                 topic="card", act=g.act)
            if k is not None:
                idx, by = k, self._llm_source
        if can_skip and idx < len(offered) and len(g.deck) >= 28 and CONFIG.ab.get("skip_rules", True):
            ok, why = self._earns_slot(offered[idx], g, commit)
            if not ok:
                idx, by = len(offered), f"rule: deck {len(g.deck)} cards, {offered[idx].name} has {why}"
        tr.events.append({"kind": "card", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        tr.picks.append({"floor": g.floor, "offered": labels, "chosen": labels[idx],
                         "p": round(probs[idx], 2), "archetype": tr.plan.get("archetype")})
        ranked = sorted(zip(labels, probs), key=lambda x: -x[1])
        note = f"card pick ({by}): " + ", ".join(f"{n} {p:.0%}" for n, p in ranked) + \
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

        shop_pull = self._shop_pull(g)

        def node_score(sym: str) -> float:
            v = model.w(f"node:{sym}:h{hb}") + model.w(f"nodeact:{sym}:a{act}")
            if sym == "$":
                v += shop_pull
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
        rule = self._map_rules(g)
        for n in nodes:
            sym = n.symbol or "?"
            options.append({f"node:{sym}:h{hb}": 1.0, f"nodeact:{sym}:a{act}": 1.0})
            full = by_pos.get((n.x, n.y))
            future = max((route(ch.x, ch.y) for ch in full.children), default=0.0) if full else 0.0
            bonus.append(0.85 * future + (shop_pull if sym == "$" else 0.0) + rule.get(sym, 0.0))
            labels.append(sym)
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        tr.events.append({"kind": "path", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        tr.path.append({"floor": g.floor + 1, "node": labels[idx], "hp": g.current_hp,
                        "max_hp": g.max_hp})
        if CONFIG.live is not None:
            CONFIG.live.chose_node(g.act, nodes[idx].x, nodes[idx].y)
        self._pos = (g.act, nodes[idx].x, nodes[idx].y)
        names = {"M": "fight", "E": "ELITE", "R": "rest", "$": "shop", "?": "event", "T": "chest"}
        note = "path: " + ", ".join(f"{names.get(l, l)} {p:.0%}" for l, p in zip(labels, probs)) + \
               f" -> {names.get(labels[idx], labels[idx])}"
        return Choose(choice_index=idx), note

    @staticmethod
    def _shop_pull(g) -> float:
        """How much a shop on the path is worth now: gold to spend and curses to remove pull,
        too little gold for a removal (75+) pushes away."""
        curses = sum(1 for d in g.deck if d.type.value == "CURSE")
        if g.gold < 75:
            return -1.2
        pull = 1.4 if g.gold >= 250 else (0.6 if g.gold >= 170 else -0.4)
        if curses:
            pull += 1.4
        return pull

    def _map_rules(self, g) -> dict[str, float]:
        """Playbook rules on top of the learned path weights."""
        out: dict[str, float] = {}
        hp_frac = g.current_hp / max(1, g.max_hp)
        lament = next((r.counter for r in g.relics if r.name == "Neow's Lament"), 0)
        if lament and lament > 0:
            out["E"], out["M"] = 2.5, 0.5   # enemies have 1 HP: take the free elite relic
            return out
        damage = sum(1 for d in g.deck if "damage" in C.roles(d) or "aoe" in C.roles(d))
        # elites are where a run powers up (relics, rare cards): take them in acts 1-2 unless HP is low
        if g.act == 1:
            out["E"] = 1.0 if hp_frac >= 0.5 and damage >= 1 else (0.3 if hp_frac >= 0.5 else -0.6)
            out["E"] += CONFIG.risk if hp_frac >= 0.4 else 0.0
        elif g.act == 2:
            out["E"] = 0.9 if hp_frac >= 0.6 else -0.3
            out["E"] += CONFIG.risk if hp_frac >= 0.5 else 0.0
        elif hp_frac < 0.7:
            out["E"] = -1.0                  # act 3 elites end shaky runs
        if CONFIG.ab.get("event_pull"):
            # A/B: the player's runs took ~5.7 event rooms per run vs the bot's 2.2 and ended with ~5 more
            # relics; test whether pulling toward ? rooms (away from hallway fights) helps the bot too
            out["?"] = out.get("?", 0.0) + 0.8
            out["M"] = out.get("M", 0.0) - 0.3
        # Budget HP for the boss. Act 2 boss losses entered at ~48% HP vs ~86% for wins: runs chained
        # 4-5 hallway fights (12-18 HP each) before the boss and one rest (30% heal) couldn't fix it.
        boss_floor = {1: 16, 2: 33, 3: 50}.get(g.act, 99)
        coffee = any(r.name == "Coffee Dripper" for r in g.relics)  # no resting: protect HP harder
        if g.act >= 2 and boss_floor - g.floor <= 8 and CONFIG.ab.get("hp_budget", True):
            low, mid = (0.6, 0.8) if coffee else (0.5, 0.7)
            if hp_frac < low:
                for sym, b in (("M", -1.2), ("E", -2.0), ("?", 0.6), ("R", 0.8 if not coffee else 0.0), ("$", 0.4)):
                    out[sym] = out.get(sym, 0.0) + b
            elif hp_frac < mid:
                for sym, b in (("M", -0.6), ("E", -1.0), ("?", 0.3), ("R", 0.4 if not coffee else 0.0)):
                    out[sym] = out.get(sym, 0.0) + b
        return out

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
        hp_frac = g.current_hp / max(1, g.max_hp)
        pre_boss = g.floor in (15, 32, 49)
        # data: act 2 boss win rate 10% entering under 50% HP, 83% entering at 85%+
        need = 0.55 if g.act_boss == "Hexaghost" else (0.85 if g.act >= 2 else 0.65)
        if pre_boss:
            rule = {"rest": 4.0 if hp_frac < need else 0.0, "smith": 1.0 if hp_frac >= max(0.8, need) else 0.0}
        elif g.act >= 2 and hp_frac < 0.6:
            # act 2 hallways wear runs down: losses smithed under 60% HP three times as often as wins
            rule = {"rest": 2.5, "smith": 0.0}
        else:
            rule = {"rest": 0.0, "smith": 0.8 if hp_frac >= (0.6 if g.act >= 2 else 0.5) else 0.0}
        for i, opt in enumerate(g.choice_list):
            if opt in ("rest", "smith"):
                options.append({f"{opt}:h{hb}": 1.0}); bonus.append(rule[opt])
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
        options, bonus, labels, prices = [], [], [], []

        def add(f, label, choice, price, b=0.0):
            if price > g.gold:
                return  # never try what we cannot afford
            options.append(f); bonus.append(b); labels.append((label, choice)); prices.append(price)

        junk = [d for d in g.deck if d.type.value == "CURSE" or C.base_name(d.name) in C.BASIC]
        curses = sum(1 for d in g.deck if d.type.value == "CURSE")
        strikes = sum(1 for d in g.deck if C.base_name(d.name) == "Strike")
        cards_bought = sum(1 for x in tr.shops if x["floor"] == g.floor and x["bought"] not in
                           ("(leave)", "remove a card", "purge") and x["bought"] in {c.name for c in s.cards})
        if "purge" in choices and s.purge_available and junk:
            # playbook: removing a curse or a Strike is almost always the best buy
            rule = 2.5 if curses else (1.2 if strikes >= 3 else 0.4)
            add({"shop:purge": 1.0, "shop:price": s.purge_cost / 100}, "remove a card", choices.index("purge"),
                s.purge_cost, rule)
        for card in s.cards:
            n = card.name.lower()
            if n in choices:
                # cards stack up many features; "shop:card" is a learnable bar every bought card must clear,
                # and a second card in the same visit needs a clearly better reason
                f = self._card_features(card, g, commit)
                f["shop:card"] = 1.0
                f["shop:price"] = (card.price or 0) / 100
                add(f, card.name, choices.index(n), card.price or 0, -1.5 * cards_bought)
        for relic in s.relics:
            n = relic.name.lower()
            if n in choices:
                quality = C.SHOP_RELIC_PRIOR.get(relic.name, 0.8) - 0.8 + \
                    relics_mod.conflict(relic.name, {r.name for r in g.relics})[0]
                f = self._relic_features(relic.name, g, commit, "shoprelic")
                f.update({"shop:relic": 1.0, "shop:price": (relic.price or 0) / 100})
                add(f, relic.name, choices.index(n), relic.price or 0, quality)
        if any(p.id == EMPTY_POTION for p in g.potions):
            late = 1.0 if g.act >= 3 or g.floor in (14, 15, 31, 32, 48, 49) else 0.0
            for potion in s.potions:
                n = potion.name.lower()
                if n in choices:
                    add({"shop:potion": 1.0, "shop:price": (potion.price or 0) / 100}, potion.name,
                        choices.index(n), potion.price or 0, late)
        options.append({"shop:leave": 1.0}); bonus.append(0.0); labels.append(("(leave)", None)); prices.append(0)
        if self._last_shop and self._last_shop[:2] == (g.floor, g.gold):
            # the last purchase did not go through (gold unchanged): never retry it
            failed = self._last_shop[2]
            for k, (label, _) in enumerate(labels):
                if label == failed:
                    del options[k], bonus[k], labels[k], prices[k]
                    break
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        by = "policy"
        if CONFIG.llm_events and CONFIG.llm_shop:
            if g.floor not in self._shop_list:
                self._shop_list[g.floor] = self._ask_shop(message, labels, prices, probs)
            wish = self._shop_list[g.floor]
            if wish is not None:
                by = "llm list"
                idx = len(labels) - 1  # leave unless the next wanted item is still here
                removed = any(x["floor"] == g.floor and x["bought"] in ("remove a card", "purge") for x in tr.shops)
                purge = next((k for k, (lab, _) in enumerate(labels) if lab == "remove a card"), None)
                card_names = {c.name for c in s.cards}
                basics = sum(1 for d in g.deck if C.base_name(d.name) in C.BASIC)
                if purge is not None and not removed and (curses or strikes >= 3 or basics >= 5):
                    idx, by = purge, "rule: remove first"   # the LLM kept skipping removal
                else:
                    while wish:
                        item = wish.pop(0)
                        if item in card_names and cards_bought >= 1:
                            continue  # at most one card per visit
                        if relics_mod.conflict(item, {r.name for r in g.relics})[0] <= -2.0:
                            continue  # a relic the ones we own make useless (Sacred Bark with Sozu)
                        hit = next((k for k, (lab, _) in enumerate(labels) if lab == item), None)
                        if hit is not None:
                            idx = hit
                            break
        tr.events.append({"kind": "shop", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        name, choice = labels[idx]
        tr.shops.append({"floor": g.floor, "bought": name, "gold": g.gold, "by": by})
        note = f"shop ({by}): " + ", ".join(f"{l} {p:.0%}" for (l, _), p in
                                           sorted(zip(labels, probs), key=lambda x: -x[1])[:5])
        if choice is None:
            return ReturnBack(), note + " -> leave"
        self._shop_buys[g.floor] = buys + 1
        self._last_shop = (g.floor, g.gold, name)
        return Choose(choice_index=choice), note + f" -> buy {name}"

    def _ask_shop(self, message, labels, prices, probs) -> list[str] | None:
        """One LLM call per visit: an ordered shopping list within the gold. None = LLM unavailable."""
        g, tr = message.game_state, CONFIG.tracker
        items = [(k, lab, prices[k]) for k, (lab, choice) in enumerate(labels) if choice is not None]
        if not items:
            return []
        deck = ", ".join(sorted(d.name for d in g.deck))
        plan = tr.plan or {}
        shop_cards = {c.name: c for c in g.screen.cards}

        def described(lab: str) -> str:
            d = gamedata.relic(lab) or gamedata.potion(lab)
            if lab in shop_cards:
                c = shop_cards[lab]
                d = f"{c.type.value.lower()}, cost {c.cost}: {(card_text(c) or '').strip()}"
            if lab == "remove a card":
                d = "remove one card from the deck for good"
            fit = synergy.relic_fit(lab, [x.name for x in g.deck])[1] if gamedata.relic(lab) else ""
            if gamedata.relic(lab):
                clash = relics_mod.conflict(lab, {r.name for r in g.relics})[1]
                fit = "; ".join(x for x in [fit] + clash if x)
            if lab in shop_cards:
                fit = self._syn_note(lab, g)
            if fit:
                d = f"{d} [{fit.strip('()')}]"
            h = evidence.shop(CONFIG.evidence, lab)
            return (f"{lab}: {d}" if d else lab) + (f" {h}" if h else "")

        listing = "\n".join(f"{n}: {described(lab)} - {price} gold (policy {probs[k]:.0%})"
                            for n, (k, lab, price) in enumerate(items))
        system = shared_system(g.character)
        user = ("SHOP DECISION. Priorities: (1) remove a curse, or a Strike while the deck still has several; "
                "(2) a relic that helps this deck (relics last the whole run and never clog the draw); "
                "(3) at most one card, and only if it is clearly better than the deck's average card and "
                "fills a need (damage, area damage, block, scaling); (4) potions before a boss. "
                "Spending everything on cards makes the deck worse; keeping gold for a later shop is fine. "
                "Reply with the numbers to buy in order of priority, comma separated, total within the gold, "
                "or 'none', then ' | ' and a reason in at most 12 words.\n\n"
                f"{self._context(g)}\n"
                f"RUN PLAN: archetype {plan.get('archetype', 'undecided')}; "
                f"wanted {', '.join(plan.get('wanted', [])) or '-'}\n"
                f"Act {g.act}, floor {g.floor}, boss {g.act_boss}. HP {g.current_hp}/{g.max_hp}. "
                f"Gold {g.gold}. Potions: {', '.join(p.name for p in g.potions)}.\n"
                f"Relics: {'; '.join(gamedata.with_relic(r.name) for r in g.relics)}.\nDeck ({len(g.deck)}): {deck}\n\n"
                f"Shop:\n{listing}\n\nWhat do you buy?")
        answer, ms = self._call_llm(system, user, max_tokens=80)
        if answer is None:
            self._log_llm(g, "shop", f"Shop with {g.gold} gold", [l for _, l, _ in items], "(LLM unavailable)", None, ms)
            return None
        wish, spent = [], 0
        for tok in re.findall(r"\d+", answer.split("|")[0]):
            n = int(tok)
            if 0 <= n < len(items) and items[n][1] not in wish and spent + items[n][2] <= g.gold:
                wish.append(items[n][1])
                spent += items[n][2]
        self._log_llm(g, "shop", f"Shop with {g.gold} gold", [f"{l} ({p}g)" for _, l, p in items],
                      answer[:120], ", ".join(wish) or "(nothing)", ms)
        return wish

    # ------------------------------------------------------------------ selections

    @staticmethod
    def _removal_bias(card, g) -> float:
        """Remove Strikes when the deck already has more attacks than block cards, Defends when it
        has more block; curses always first (their worth is already -5)."""
        name = C.base_name(card.name)
        if name not in ("Strike", "Defend") or card.upgrades:
            return 0.0
        others = [d for d in g.deck if C.base_name(d.name) not in C.BASIC and d.type.value != "CURSE"]
        attacks = sum(1 for d in others if d.type.value == "ATTACK")
        blocks = sum(1 for d in others if "block" in C.roles(d) or C.effects(d).block)
        if attacks > blocks:
            return -1.0 if name == "Strike" else 0.6
        exhaust_use = any(C.base_name(d.name) in ("Corruption", "Second Wind", "Sever Soul", "Feel No Pain",
                                                   "Dark Embrace", "Fiend Fire") for d in g.deck)
        if blocks > attacks and not exhaust_use:
            return -1.0 if name == "Defend" else 0.6
        return -1.0 if name == "Strike" else 0.6  # guide: Strikes go first; Defends feed exhaust

    def _card_worth(self, card, g, upgrade: bool = False) -> float:
        if card.type.value in ("CURSE", "STATUS"):
            return -5.0
        v = CONFIG.model.w(f"card:{C.base_name(card.name)}")
        if upgrade and C.base_name(card.name) == "Bash" and g.act == 1:
            v += 1.5
        plan = CONFIG.tracker.plan
        if plan.get("lean") and C.base_name(card.name) in C.archetypes(g.character).get(plan["lean"], {}):
            v += 0.4
        return v

    def _upgrade_score(self, card, g) -> float:
        v = self._card_worth(card, g, True) + C.UPGRADE_PRIORITY.get(C.base_name(card.name), 0.0)
        return v - (3.0 if card.upgrades and C.base_name(card.name) != "Searing Blow" else 0.0)

    def _upgrade(self, message, pool):
        """Smith: playbook upgrade priorities, then one fast LLM call that sees each card's
        current and upgraded text (the upgrade's actual gain decides it)."""
        g = message.game_state
        ranked = sorted(pool, key=lambda x: -self._upgrade_score(x[1], g))
        seen, options = set(), []
        for i, c in ranked:
            if c.name not in seen and (not c.upgrades or C.base_name(c.name) == "Searing Blow"):
                seen.add(c.name)
                options.append((i, c))
        options = options[:8] or ranked[:1]
        i, card = options[0]
        by = "policy"
        if CONFIG.llm_events and len(options) > 1:
            labels = []
            for _, c in options:
                now, up = (card_text(c) or "").strip(), (upgraded_card_text(c) or "").strip()
                labels.append(f"{c.name}: {now} -> upgraded: {up} [score {self._upgrade_score(c, g):+.1f}]")
            k = self._ask_option(message, labels,
                                 f"Rest site: upgrade one card (act {g.act}, boss {g.act_boss}, deck {len(g.deck)} cards). "
                                 "Pick the upgrade that adds the most to the coming fights.",
                                 topic="card", act=g.act)
            if k is not None:
                (i, card), by = options[k], self._llm_source
        return Choose(choice_index=i), f"upgrade ({by}) {card.name}"

    def _grid(self, message, decision):
        g = message.game_state
        s = g.screen
        chosen = {c.uuid for c in s.selected_cards}
        if "choose" not in message.available_commands:
            return Proceed(), "confirm selection"
        if s.confirm_up and (len(chosen) >= s.num_cards or s.any_number):
            return Proceed(), "confirm selection"
        pool = [(i, c) for i, c in enumerate(s.cards) if c.uuid not in chosen]
        if not pool:
            return self._advance(message), ""
        if s.for_purge or s.for_transform:
            i, card = min(pool, key=lambda x: self._card_worth(x[1], g) + self._removal_bias(x[1], g))
            why = "remove" if s.for_purge else "transform"
        elif s.for_upgrade:
            return self._upgrade(message, pool)
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
            i, card = max(pool, key=lambda x: self._card_worth(x[1], g, True) - (2.0 if x[1].upgrades else 0))
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
        commit = self._update_plan(g)
        # learned like cards: the relic itself, the relic in this build / act, and how much its synergy fit counts
        prof = relics_mod.profile(g)
        options = [self._relic_features(r.name, g, commit, "relic", prof) for r in relics]
        rules = [boss_relic_rule(r.name, g) for r in relics]   # full judgment: guard + reasons for the LLM
        bonus = []
        for r in relics:
            # the deck fit is in the features (relicfit:boss); the bonus carries the guide tier
            b = C.GUIDE_BOSS_RELIC.get(r.name, (0.0, ""))[0]
            if CONFIG.learn:
                # training runs: relics this bot has rarely taken get a try (the LLM kept picking the same few)
                n = len((CONFIG.evidence or {}).get("relic", {}).get((r.name, g.act), []))
                b += 0.5 / (1 + n) ** 0.5
            bonus.append(b)
        can_skip = validate(ReturnBack(), message).ok
        if can_skip:
            options.append({"relic:skip": 1.0})
            bonus.append(0.0)
        idx, probs = model.choose(options, CONFIG.temperature, CONFIG.rng, bonus)
        by = "policy"
        # bench runs always ask the LLM; training runs only llm_share of the time so the learned weights get tried
        if CONFIG.llm_events and (not CONFIG.learn or CONFIG.rng.random() < CONFIG.llm_share):
            ranked = sorted(range(len(relics)), key=lambda k: -probs[k])
            labels = [f"{gamedata.with_relic(relics[k].name)} [policy {probs[k]:.0%}; fit for this deck "
                      f"{rules[k][0]:+.1f}{': ' + rules[k][1] if rules[k][1] else ''}] "
                      f"{evidence.boss_relic(CONFIG.evidence, relics[k].name, g.act)} "
                      f"{evidence.mine(evidence.boss_relic(CONFIG.human_evidence, relics[k].name, g.act))}".rstrip()
                      for k in range(len(relics))]
            pick = self._ask_option(message, labels, "Choose a boss relic. " + relics_mod.describe(prof) +
                                    " Judge each relic against THESE numbers, not its general reputation: energy "
                                    "relics pay off when the deck wants more energy than it has; Runic Pyramid only "
                                    "with ways to spend or exhaust a full hand. Policy ranking: " +
                                    ", ".join(relics[k].name for k in ranked), topic="boss_relic")
            if pick is not None:
                idx, by = pick, "llm"
                if pick < len(relics) and rules[pick][0] <= -2.0:
                    # a relic the playbook rules out for this deck: take the policy's best acceptable option
                    ok = [k for k in range(len(relics)) if rules[k][0] > -2.0]
                    if ok:
                        idx, by = max(ok, key=lambda k: probs[k]), f"rule over llm: {rules[pick][1]}"
        # learn from the outcome whoever chose: the LLM's picks train the relic weights too
        tr.events.append({"kind": "boss_relic", "options": options, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        offered = [r.name for r in relics]
        if can_skip and idx == len(relics):
            tr.boss_relics.append({"floor": g.floor, "relic": "(skip)", "by": by, "offered": offered})
            return ReturnBack(), "boss relic: skip"
        tr.boss_relics.append({"floor": g.floor, "relic": relics[idx].name, "by": by, "offered": offered})
        return Choose(choice_index=idx), f"boss relic ({by}): {relics[idx].name}"

    def _event(self, message, decision):
        g, tr, model = message.game_state, CONFIG.tracker, CONFIG.model
        s = g.screen
        live = [o for o in s.options if not o.disabled and o.choice_index is not None]
        if not live:
            return self._advance(message), ""
        if s.event_name == "Knowing Skull":
            # every ask costs more HP and the event repeats: four runs died asking for Riches
            asked = self._skull.get(g.floor, 0)
            leave = [o for o in live if "leave" in f"{o.label} {o.text}".lower()]
            if leave and (asked >= 1 or g.current_hp < 0.6 * g.max_hp):
                return Choose(choice_index=leave[0].choice_index), "event Knowing Skull: leave (each ask costs more HP)"
            self._skull[g.floor] = asked + 1

        def hp_cost(o) -> int:
            m = re.search(r"(?:lose|take) (\d+) (?:hp|damage)", f"{o.label} {o.text}".lower())
            return int(m.group(1)) if m else 0
        safe = [o for o in live if hp_cost(o) < g.current_hp - max(6, 0.1 * g.max_hp)]
        if safe and len(safe) < len(live):
            live = safe  # never pay (nearly) all remaining HP for an event reward
        if len(live) == 1:
            return Choose(choice_index=live[0].choice_index), f"event: {live[0].label}"
        if s.event_name == "Match and Keep!":
            return self._match_and_keep(g, live)
        neow = s.event_name == "Neow" or g.floor == 0
        heur = [self._event_score(f"{o.label} {o.text}", g, neow) + event_rule(s.event_name, f"{o.label} {o.text}", g)
                for o in live]
        # learned value of each option on top of the playbook heuristic
        feats = [{f"ev:{s.event_name}:{o.label}": 1.0} for o in live]
        idx, probs = model.choose(feats, CONFIG.temperature, CONFIG.rng, heur)
        values = [model.score(f) + h for f, h in zip(feats, heur)]
        best2 = sorted(values, reverse=True)[:2]
        close = neow or len(best2) < 2 or best2[0] - best2[1] < CONFIG.llm_gap
        by = "policy"
        if CONFIG.llm_events and close:
            labels = [f"{o.label} {o.text}".strip() + f" (score {v:+.1f}) "
                      f"{evidence.event(CONFIG.evidence, s.event_name, o.label)} "
                      f"{evidence.mine(evidence.event(CONFIG.human_evidence, s.event_name, o.label))}".rstrip()
                      for o, v in zip(live, values)]
            key = "|".join(["event", s.event_name, *sorted(o.label for o in live),
                            f"h{hp_bucket(g.current_hp, g.max_hp)}", f"a{g.act}"])
            k = self._ask_option(message, labels, f"Event: {s.event_name}. {(s.body_text or '')[:300]}",
                                 topic="neow" if neow else "event", cache_key=None if neow else key,
                                 cache_labels=[o.label for o in live])
            if k is not None:
                idx, by = k, self._llm_source
        tr.events.append({"kind": "event", "options": feats, "chosen": idx, "probs": probs,
                          "temp": CONFIG.temperature})
        pick = live[idx]
        tr.game_events.append({"floor": g.floor, "event": s.event_name, "choice": pick.label, "by": by})
        return Choose(choice_index=pick.choice_index), f"event {s.event_name} ({by}): {pick.label}"

    def _match_and_keep(self, g, live):
        """Memory game: remember every face-up card, finish known pairs, otherwise flip unknowns.
        (The LLM used to take 20-30 s per flip here and loop on the same two cards.)"""
        mk = self._mk if self._mk.get("floor") == g.floor else {"floor": g.floor, "seen": {}, "picks": 0, "first": None}
        self._mk = mk
        for o in live:
            if not re.fullmatch(r"card\d+", o.label):
                mk["seen"][o.choice_index] = o.label
        avail = {o.choice_index for o in live}
        unknown = [o.choice_index for o in live if o.choice_index not in mk["seen"]]
        first = mk["first"] if mk["picks"] % 2 == 1 else None
        pick = None
        if first is not None:
            name = mk["seen"].get(first)
            partner = [i for i, n in mk["seen"].items() if n == name and i != first and i in avail]
            pick = partner[0] if partner else next((i for i in unknown if i != first), None)
        else:
            names: dict[str, list[int]] = {}
            for i, n in mk["seen"].items():
                if i in avail:
                    names.setdefault(n, []).append(i)
            pairs = [v for v in names.values() if len(v) >= 2]
            if pairs:
                pick = pairs[0][0]
            elif unknown:
                pick = unknown[0]
        if pick is None or pick not in avail:
            pick = next(iter(sorted(avail - ({first} if first is not None else set()))), live[0].choice_index)
        mk["picks"] += 1
        mk["first"] = pick
        return Choose(choice_index=pick), f"match and keep: flip {mk['seen'].get(pick, 'unknown card')}"

    # playbook ranking of Neow's blessings
    NEOW_SCORES = ((r"250 gold", 3.0), (r"choose a rare card", 2.8), (r"random rare relic", 2.7),
                   (r"transform (?:2|two) cards", 2.6), (r"rare colorless", 2.5), (r"random common relic", 2.4),
                   (r"remove (?:2|two) cards", 2.3), (r"(?:gain|obtain) 100 gold", 1.9), (r"transform a card", 1.6),
                   (r"upgrade a card", 1.6), (r"colorless card", 1.4), (r"remove a card", 1.3),
                   (r"random rare card", 1.1), (r"max hp \+", 1.0), (r"three combats have 1 hp", 0.9),
                   (r"potion", 0.8), (r"choose a card to obtain", 0.6))

    @classmethod
    def _event_score(cls, text: str, g, neow: bool = False) -> float:
        t = text.lower()
        hp_frac = g.current_hp / max(1, g.max_hp)
        s = 0.0
        if neow:
            # guide ranking: best reward that matches, minus its drawback (max HP < gold < damage < curse)
            s = max((v for pat, v in cls.NEOW_SCORES if re.search(pat, t)), default=0.0)
            if "boss relic" in t:
                s = 1.8 if g.act_boss == "Hexaghost" else 1.0
            if "curse" in t:
                s -= 1.2
            if re.search(r"lose (?:all|\d+) gold", t):
                s -= 0.5
            if "take" in t and "damage" in t:
                s -= 0.6
            if re.search(r"lose \d+ max hp", t):
                s -= 0.1
            return s
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

    _llm_source = "llm"

    def _ask_option(self, message, labels: list[str], header: str, topic: str = "event",
                    cache_key: str | None = None, cache_labels: list[str] | None = None,
                    act: int | None = None) -> int | None:
        g, tr = message.game_state, CONFIG.tracker
        if cache_key and CONFIG.llm_cache is not None and cache_labels:
            hit = CONFIG.llm_cache.get(cache_key)
            if hit in cache_labels:
                tr.llm_cached += 1
                self._llm_source = "llm cache"
                self._log_llm(g, topic, header, labels, f"(cached) {hit}", hit, 0)
                return cache_labels.index(hit)
        plan = tr.plan or {}
        system = shared_system(g.character)
        listing = "\n".join(f"{i}: {label}" for i, label in enumerate(labels))
        user = (f"{topic.replace('_', ' ').upper()} DECISION. RUN PLAN: archetype {plan.get('archetype', 'undecided')}; "
                f"wanted {', '.join(plan.get('wanted', [])) or '-'}\n{self._context(g)}\n\n"
                f"{header}\nOptions:\n{listing}\n\n"
                "Answer as: <option number> | <reason in at most 12 words>")
        answer, ms = self._call_llm(system, user, max_tokens=48)
        if answer is None:
            self._log_llm(g, topic, header, labels, "(LLM unavailable)", None, ms)
            return None
        m = re.search(r"\d+", answer.split("|")[0])
        k = int(m.group(0)) if m else None
        if k is None or not 0 <= k < len(labels):
            self._log_llm(g, topic, header, labels, answer[:120], None, ms)
            return None
        self._llm_source = "llm"
        self._log_llm(g, topic, header, labels, answer[:120], labels[k], ms)
        if cache_key and CONFIG.llm_cache is not None and cache_labels:
            CONFIG.llm_cache[cache_key] = cache_labels[k]
        return k

    def _context(self, g) -> str:
        """What the LLM needs beyond the options: what's ahead, threats left, deck profile, relics."""
        lines = []
        boss_floor = {1: 16, 2: 33, 3: 50}.get(g.act, g.floor + 1)
        ahead = self._map_ahead(g)
        lines.append(f"Floors to the act boss ({g.act_boss}): {max(0, boss_floor - g.floor)}."
                     + (f" Reachable ahead: {ahead}." if ahead else ""))
        fought = {e for f in CONFIG.tracker.fight_log
                  if 1 + max(0, (f["floor"] - 1) // 17) == g.act and f.get("room") == "MonsterRoomElite"
                  for e in f["enemies"]}
        pool = {1: ["Gremlin Nob", "Lagavulin", "Sentry"], 2: ["Gremlin Leader", "Book of Stabbing", "Slaver"],
                3: ["Giant Head", "Nemesis", "Reptomancer"]}.get(g.act, [])
        left = [e for e in pool if not any(e in x for x in fought)]
        if left:
            lines.append(f"Elites still possible this act: {', '.join(left)}.")
        deck = list(g.deck)
        types = collections.Counter(d.type.value for d in deck)
        roles = collections.Counter(r for d in deck for r in C.roles(d))
        costs = [d.cost for d in deck if d.cost >= 0 and d.type.value not in ("STATUS", "CURSE")]
        basics = collections.Counter(C.base_name(d.name) for d in deck if C.base_name(d.name) in C.BASIC)
        lines.append(f"Deck profile: {len(deck)} cards ({types['ATTACK']} attacks, {types['SKILL']} skills, "
                     f"{types['POWER']} powers, {types['CURSE']} curses), avg cost {sum(costs) / max(1, len(costs)):.1f}, "
                     f"{sum(1 for d in deck if d.upgrades)} upgraded; Strikes {basics['Strike']}, Defends {basics['Defend']}; "
                     f"area damage {roles['aoe']}, big damage {roles['damage']}, block {roles['block']}, "
                     f"scaling {roles['scaling']}, draw {roles['draw']}.")
        lines.append("Relics: " + "; ".join(gamedata.with_relic(r.name) for r in g.relics) + ".")
        pots = [p.name for p in g.potions if p.id != EMPTY_POTION]
        lines.append(f"Potions: {', '.join(pots) or 'none'}. Gold {g.gold}.")
        return "\n".join(lines)

    def _map_ahead(self, g) -> str:
        """Nearest floor distance to each room type reachable from the current map node."""
        if not g.map:
            return ""
        by_pos = {(n.x, n.y): n for n in g.map}
        if self._pos and self._pos[0] == g.act and (self._pos[1], self._pos[2]) in by_pos:
            frontier = [by_pos[(self._pos[1], self._pos[2])]]
        else:
            frontier = [n for n in g.map if n.y == 0]
        names = {"E": "elite", "R": "rest site", "$": "shop", "?": "event", "T": "chest", "M": "fight"}
        first: dict[str, int] = {}
        seen = set()
        dist = 0
        while frontier and dist <= 15:
            nxt = []
            for n in frontier:
                if (n.x, n.y) in seen:
                    continue
                seen.add((n.x, n.y))
                if dist > 0 and n.symbol in names:
                    first.setdefault(n.symbol, dist)
                nxt += [by_pos[(c.x, c.y)] for c in n.children if (c.x, c.y) in by_pos]
            frontier, dist = nxt, dist + 1
        order = ["E", "R", "$", "?", "T"]
        return ", ".join(f"{names[s]} in {first[s]}" for s in order if s in first)

    def _call_llm(self, system: str, user: str, timeout: float | None = None,
                  max_tokens: int = 24) -> tuple[str | None, int]:
        tr = CONFIG.tracker
        if LLM_BUSY.is_set() or time.monotonic() < _LLM_DOWN_UNTIL[0]:
            return None, 0
        started = time.monotonic()
        try:
            tr.llm_calls += 1
            answer = ask_llm(CONFIG.llm_base_url, CONFIG.llm_model, system, user, timeout=timeout or LLM_TIMEOUT,
                             max_tokens=max_tokens)
        except Exception:
            answer = None
            _LLM_DOWN_UNTIL[0] = time.monotonic() + LLM_COOLDOWN
            if ON_LLM_FAILURE is not None:
                try:
                    ON_LLM_FAILURE()
                except Exception:
                    pass
        ms = int((time.monotonic() - started) * 1000)
        tr.llm_ms += ms
        return answer, ms

    def _log_llm(self, g, topic: str, header: str, options: list[str], answer: str, choice, ms: int) -> None:
        entry = {"t": time.strftime("%H:%M:%S"), "floor": g.floor, "topic": topic, "q": header[:200],
                 "options": [o[:90] for o in options][:12], "answer": answer, "choice": choice, "ms": ms}
        CONFIG.tracker.llm_log.append(entry)
        if CONFIG.live is not None:
            CONFIG.live.llm_call(entry)


# ---------------------------------------------------------------- playbook helpers

_SHARED: dict = {}


def shared_system(character: str) -> str:
    """One system prompt for every in-game LLM call (the whole playbook + general rules).
    Ollama reuses its cache for an identical prompt prefix, so only the short decision-specific
    user message is processed per call (~0.2 s instead of ~1.5 s re-reading the playbook)."""
    key = (character, hash(CONFIG.strategy))
    if key not in _SHARED:
        _SHARED.clear()
        _SHARED[key] = (f"You make the big-picture choices in a Slay the Spire run as the {character}: "
                        "Neow, card rewards, events, shops, upgrades and boss relics. Use the playbook, the run plan, "
                        "the scores and the synergy notes shown with each option. "
                        f"{evidence.NOTE} Follow the reply format each decision asks for.\n\n"
                        f"PLAYBOOK:\n{(CONFIG.strategy or '')[:14000]}")
    return _SHARED[key]

TOPIC_SECTIONS = {
    "neow": ("Core principles", "Neow"),
    "event": ("Core principles", "Events", "Map, rest sites and shops"),
    "boss_relic": ("Core principles", "Boss relics"),
    "shop": ("Core principles", "Map, rest sites and shops"),
    "card": ("Core principles",),
}


def playbook_sections(text: str) -> dict[str, str]:
    out, cur, buf = {}, "_head", []
    for line in (text or "").splitlines():
        if line.startswith("## "):
            out[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out[cur] = "\n".join(buf).strip()
    return out


def playbook_for(text: str, topic: str, character: str | None = None, limit: int = 6000,
                 act: int | None = None) -> str:
    """Only the sections a decision needs, plus the class and the coach's lessons,
    so the prompt stays short and the learned lessons are never cut off."""
    names = TOPIC_SECTIONS.get(topic)
    if names is None:
        return (text or "")[:limit]
    secs = playbook_sections(text)
    char = {"THE_SILENT": "Silent"}.get(C.character_key(character), (character or "").title())
    keep = [n for n in names if n in secs] + ([char] if char in secs else []) + \
        ([f"Act {act}"] if act and f"Act {act}" in secs else [])
    body = "\n\n".join(f"## {n}\n{secs[n]}" for n in keep)
    learned = secs.get("Learned from runs", "")
    room = max(0, limit - len(learned) - 40)
    return body[:room] + (f"\n\n## Learned from runs\n{learned}" if learned else "")


# Guide advice per event, matched on option text: (event, regex, bonus, condition on (hp_frac, g))
EVENT_RULES = [
    ("Big Fish", r"heal", 1.2, lambda h, g: h < 0.8), ("Big Fish", r"relic", -1.0, None),
    ("Living Wall", r"transform", 0.8, lambda h, g: _basics(g) >= 3), ("Living Wall", r"remove", 0.5, None),
    ("Golden Idol", r"golden idol|take", 1.5, None), ("Golden Idol", r"max hp", 1.5, None),
    ("Golden Idol", r"curse|injury", -1.5, None),
    ("The Cleric", r"heal", 1.0, lambda h, g: h < 0.75), ("Cleric", r"heal", 1.0, lambda h, g: h < 0.75),
    ("Scrap Ooze", r"reach|relic", 0.6, lambda h, g: h >= 0.55), ("Scrap Ooze", r"reach|relic", -1.5, lambda h, g: h < 0.55),
    ("Shining Light", r"upgrade", 0.6, lambda h, g: h >= 0.6), ("Shining Light", r"upgrade", -1.5, lambda h, g: h < 0.6),
    ("The Ssssserpent", r"gold|curse|doubt", -1.5, None), ("World of Goop", r"gold", 0.8, lambda h, g: h >= 0.4),
    ("Wing Statue", r"remove", 1.0, lambda h, g: h >= 0.3), ("Mushrooms", r"fight|stomp", 0.6, lambda h, g: h >= 0.55),
    ("Dead Adventurer", r"search", -1.0, lambda h, g: h < 0.5),
    ("Ancient Writing", r"upgrade all|strike", 1.5, lambda h, g: _basics(g) >= 4),
    ("Augmenter", r"transform 2|transform two", 1.2, None), ("The Colosseum", r"fight|win", 0.8, lambda h, g: h >= 0.5),
    ("Council of Ghosts", r"apparition|accept", 1.5, None), ("Cursed Tome", r"read|book", 0.8, lambda h, g: h >= 0.5),
    ("The Library", r"choose|card", 0.6, lambda h, g: h >= 0.5), ("The Library", r"heal|sleep", 0.8, lambda h, g: h < 0.5),
    ("Masked Bandits", r"fight", 0.8, lambda h, g: h >= 0.5), ("The Nest", r"dagger", 1.2, None),
    ("Pleading Vagrant", r"relic", 1.0, None), ("Vampires(?)", r"bite|vampire", -1.5, None),
    ("Vampires", r"bite|vampire", -1.5, None), ("Old Beggar", r"remove", 0.4, None),
    ("The Mausoleum", r"writhe|curse|open", -1.2, None), ("Forgotten Altar", r"golden idol|offer", 2.0, None),
    ("Mind Bloom", r"rare relic|war", 1.5, None), ("Mind Bloom", r"999", 1.0, lambda h, g: g.floor <= 40),
    ("Mind Bloom", r"awake|cannot heal", -1.5, None), ("Mysterious Sphere", r"open|fight", 0.8, lambda h, g: h >= 0.6),
    ("Sensory Stone", r"3 card|three", 0.8, lambda h, g: h >= 0.4), ("Winding Halls", r"madness", -0.5, None),
]


def _basics(g) -> int:
    return sum(1 for d in g.deck if C.base_name(d.name) in C.BASIC)


def event_rule(event: str, text: str, g) -> float:
    t = text.lower()
    h = g.current_hp / max(1, g.max_hp)
    return sum(b for ev, pat, b, cond in EVENT_RULES
               if ev == event and re.search(pat, t) and (cond is None or cond(h, g)))


def boss_relic_rule(name: str, g) -> tuple[float, str]:
    """Judgment of a boss relic for THIS run: (guide tier + deck fit, deck-specific reasons).
    The fit (relics.boss_fit) looks at energy demand, draw, exhaust outlets, clog risk, gold, HP, potions."""
    tier = C.GUIDE_BOSS_RELIC.get(name, (0.0, ""))[0]
    fit, why = relics_mod.boss_fit(name, relics_mod.profile(g))
    return round(tier + fit, 2), "; ".join(why)
