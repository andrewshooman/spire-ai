"""planner.py -- whole-turn combat search, no language model.

Instead of valuing one card at a time, the planner tries orders of the cards
in hand (within the turn's energy), simulates each sequence -- damage through
block, Vulnerable/Weak, Strength, block vs the intents, enemy mechanics -- and
scores the state the turn would end in. The first card of the best sequence is
played; after it resolves (and new cards are drawn) the turn is planned again.

Enemy mechanics it models (from the monsters' powers and names):
  Barricade (Spheric Guardian)  stripping block is progress, it persists
  Thorns / Sharp Hide           attacking costs HP (Spiker, Guardian defensive)
  Malleable (Snake Plant)       each hit adds block; Curl Up (Louse)
  Anger / Enrage (Gremlin Nob)  every Skill grows its Strength
  Curiosity (Awakened One)      every Power grows its Strength
  Time Warp (Time Eater)        12 cards end the turn; never stop on 10-11
  Beat of Death / Invincible    the Heart: each card costs HP, damage cap
  Intangible (Nemesis)          hits deal 1
  Artifact                      a debuff only strips a stack
  Lagavulin asleep              set up instead of waking it
plus kill priorities from the playbook: Red Slaver, Donu, Reptomancer's
daggers, the Awakened One's cultists, Mystic, Gremlin Wizard first; Bronze Orbs
(they die with the Automaton), Collector's Torch Heads and Darklings last.

The weights (P) are the evolution-strategy combat parameters in model.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cards as C

MAX_NODES = 3000

PRIORITY = {"Red Slaver": 1.5, "Donu": 1.6, "Dagger": 1.5, "Mystic": 1.4, "Gremlin Wizard": 1.3,
            "Orb": 0.55, "Bronze Orb": 0.55, "Sentry": 1.1, "Torch Head": 0.6, "Darkling": 0.8,
            "Repulsor": 0.9, "Spiker": 0.7, "Gremlin Leader": 1.1, "Cultist": 1.0,
            "Orb Walker": 1.3, "Exploder": 1.3, "Transient": 0.3, "Bronze Automaton": 1.0}


def pw(powers, *names) -> int:
    for p in powers or ():
        if p.id in names or p.name in names:
            return p.amount
    return 0


def alive(m) -> bool:
    return not m.is_gone and not m.half_dead and m.current_hp > 0


def intent_hit(m) -> tuple[int, int]:
    """(damage per hit, hits) of the monster's intended attack."""
    if not alive(m) or "ATTACK" not in m.intent.value:
        return 0, 0
    dmg = m.move_adjusted_damage
    if dmg is None or dmg < 0:
        dmg = m.move_base_damage or 0
    return max(0, dmg), max(1, m.move_hits or 1)


class Mon:
    __slots__ = ("i", "name", "hp", "hp0", "max_hp", "block", "block0", "vuln", "weak", "weak0",
                 "new_vuln", "new_weak", "atk", "hits", "down", "down_perm", "poison", "artifact",
                 "barricade", "thorns", "sharp", "malleable", "curl", "intangible", "cap", "prio",
                 "asleep", "nob", "dead", "shifting", "flight")

    def copy(self) -> "Mon":
        n = Mon.__new__(Mon)
        for s in Mon.__slots__:
            setattr(n, s, getattr(self, s))
        return n


class Sim:
    __slots__ = ("energy", "block", "str", "dex", "weak", "frail", "mons", "self_loss", "card_loss",
                 "perm_str", "turn_str", "turn_block", "turn_energy", "turn_dmg", "powers",
                 "draw_value", "enrage", "curiosity", "played", "junk", "self_vuln", "self_weak",
                 "self_frail", "thorns_back", "ended", "retal", "intangible", "double_atk", "double_skill",
                 "heal")

    def copy(self) -> "Sim":
        n = Sim.__new__(Sim)
        for s in Sim.__slots__:
            setattr(n, s, getattr(self, s))
        n.mons = [m.copy() for m in self.mons]
        return n

    def key(self) -> tuple:
        return (self.energy, self.block, self.str, self.dex, self.played,
                tuple((m.hp, m.block, m.vuln, m.weak, m.down, m.artifact, m.poison) for m in self.mons),
                round(self.draw_value, 2), self.perm_str, self.powers, self.self_loss,
                self.double_atk, self.double_skill, self.intangible, round(self.heal, 1))


@dataclass
class Ctx:
    hand: list
    effects: list
    hp: int
    max_hp: int
    turn: int
    elite: bool
    strikes: int
    barricade: bool
    beat_of_death: int
    hex: int
    time_warp: int
    curiosity_amt: int
    playable0: list
    energy0: int
    stall: int = 0        # turns in a row the enemies' HP did not move
    no_safe_target: bool = False  # every living enemy has Thorns / Sharp Hide
    champ_str: int = 0    # The Champ's Strength (sizes its Execute)
    inc0: float = 0.0     # incoming damage before this turn's plays


@dataclass
class Plan:
    steps: list = field(default_factory=list)      # [(hand index, target index or None)]
    value: float = 0.0
    base: float = 0.0
    summary: dict = field(default_factory=dict)
    nodes: int = 0

    def describe(self, hand, monsters) -> str:
        parts = []
        for i, t in self.steps:
            tgt = f">{monsters[t].name}" if t is not None else ""
            parts.append(f"{hand[i].name}{tgt}")
        s = self.summary
        return (f"turn plan {' , '.join(parts) or '(end turn)'} | value {self.value - self.base:+.1f} | "
                f"deal {s.get('dealt', 0):.0f}, block {s.get('block', 0):.0f}, take {s.get('taken', 0):.0f}"
                f"{', KILL ' + str(s['kills']) if s.get('kills') else ''} | searched {self.nodes}")


def build(message, deck_names: list[str]) -> tuple[Sim, Ctx]:
    g = message.game_state
    c = g.combat_state
    p = c.player
    names = [m.name for m in c.monsters if alive(m)]
    awakened = "Awakened One" in names
    sim = Sim()
    sim.energy, sim.block = p.energy, p.block
    sim.str = pw(p.powers, "Strength")
    sim.dex = pw(p.powers, "Dexterity")
    sim.weak = pw(p.powers, "Weakened") > 0
    sim.frail = pw(p.powers, "Frail") > 0
    sim.self_loss = sim.card_loss = sim.retal = 0.0
    sim.perm_str = sim.turn_str = sim.turn_block = sim.turn_energy = sim.turn_dmg = 0
    sim.powers = 0
    sim.draw_value = 0.0
    sim.enrage = sim.curiosity = 0
    sim.played = 0
    sim.junk = 0
    sim.self_vuln = sim.self_weak = sim.self_frail = 0
    sim.thorns_back = 0
    sim.intangible = pw(p.powers, "Intangible", "IntangiblePlayer") > 0
    sim.double_atk = pw(p.powers, "Double Tap")
    sim.double_skill = pw(p.powers, "Burst")
    sim.heal = 0.0
    sim.ended = False
    sim.mons = []
    for i, m in enumerate(c.monsters):
        if not alive(m):
            continue
        x = Mon()
        x.i, x.name = i, m.name
        x.hp = x.hp0 = m.current_hp
        x.max_hp = m.max_hp
        x.block = x.block0 = m.block
        x.vuln = pw(m.powers, "Vulnerable")
        x.weak = x.weak0 = pw(m.powers, "Weakened")
        x.new_vuln = x.new_weak = 0
        x.atk, x.hits = intent_hit(m)
        x.down = x.down_perm = 0
        x.poison = pw(m.powers, "Poison")   # already on it: ticks at the start of its turn
        x.artifact = pw(m.powers, "Artifact")
        x.barricade = pw(m.powers, "Barricade") > 0 or any(q.id == "Barricade" for q in m.powers)
        x.thorns = pw(m.powers, "Thorns")
        x.sharp = pw(m.powers, "Sharp Hide")
        x.malleable = pw(m.powers, "Malleable")
        x.curl = pw(m.powers, "Curl Up")
        x.intangible = pw(m.powers, "Intangible") > 0
        inv = pw(m.powers, "Invincible")
        x.cap = inv if inv > 0 else 10 ** 6
        x.prio = PRIORITY.get(m.name, 1.0)
        if m.name == "Cultist" and awakened:
            x.prio = 1.6
        if m.name in ("Orb", "Bronze Orb") and pw(m.powers, "Stasis"):
            x.prio = 0.85  # holds a card, but every Orb dies with the Automaton: hit the boss
        x.asleep = m.name == "Lagavulin" and m.intent.value == "SLEEP"
        x.nob = pw(m.powers, "Anger", "Enrage")
        x.shifting = pw(m.powers, "Shifting") != 0 or any(p.id == "Shifting" for p in m.powers)
        x.flight = pw(m.powers, "Flight")
        x.dead = False
        sim.mons.append(x)
    hand = list(c.hand)
    ctx = Ctx(
        hand=hand, effects=[C.effects(h) for h in hand],
        hp=p.current_hp, max_hp=p.max_hp, turn=c.turn,
        elite=g.room_type in ("MonsterRoomElite", "MonsterRoomBoss"),
        strikes=sum(1 for n in deck_names if "Strike" in n),
        barricade=pw(p.powers, "Barricade") > 0,
        beat_of_death=max((pw(m.powers, "BeatOfDeath", "Beat of Death") for m in c.monsters if alive(m)), default=0),
        hex=pw(p.powers, "Hex"),
        time_warp=max((pw(m.powers, "Time Warp") for m in c.monsters if alive(m)), default=-1)
        if any(pw(m.powers, "Time Warp") or m.name == "Time Eater" for m in c.monsters if alive(m)) else -1,
        curiosity_amt=max((pw(m.powers, "Curiosity") for m in c.monsters if alive(m)), default=0),
        playable0=[h.is_playable for h in hand], energy0=p.energy,
    )
    return sim, ctx


# ---------------------------------------------------------------- one play

def _hit(sim: Sim, m: Mon, dmg: float) -> None:
    if m.dead:
        return
    if m.intangible:
        dmg = min(dmg, 1)
    if m.flight > 0:  # Byrds: half damage while flying; every hit knocks a stack off
        dmg = dmg // 2
        m.flight -= 1
        if m.flight == 0:
            m.atk = 0  # grounded: stunned, its attack this turn never lands
    dmg = min(dmg, m.cap)
    if m.block >= dmg:
        m.block -= dmg
    else:
        through = dmg - m.block
        m.block = 0
        m.hp -= through
        m.cap -= through
        if m.curl and through > 0:
            m.block += m.curl
            m.curl = 0
    if m.malleable:
        m.block += m.malleable
        m.malleable += 1
    if m.thorns:
        sim.self_loss += m.thorns
        sim.retal += m.thorns
    if m.hp <= 0:
        m.dead = True
        m.hp = 0


def _debuff(m: Mon, vuln: int, weak: int) -> None:
    for kind, n in (("vuln", vuln), ("weak", weak)):
        if not n or m.dead:
            continue
        if m.artifact > 0:
            m.artifact -= 1
            continue
        if kind == "vuln":
            m.vuln += n
            m.new_vuln += n
        else:
            m.weak += n
            m.new_weak += n


def play(sim: Sim, ctx: Ctx, idx: int, target: int | None, remaining: list[int],
         _echo: bool = False, _x: int = 0) -> None:
    """Apply one card to the simulated turn; `remaining` is the hand before it (read only).
    _echo: the free second copy from Double Tap / Burst (no energy, same X)."""
    card, e = ctx.hand[idx], ctx.effects[idx]
    ctype = card.type.value
    x_energy = _x if _echo else sim.energy
    cost = 0 if _echo else (sim.energy if card.cost == -1 else max(card.cost, 0))
    sim.energy -= cost
    if not _echo:
        sim.played += 1
    echo = False
    if not _echo and ctype == "ATTACK" and sim.double_atk > 0:
        sim.double_atk -= 1
        echo = True
    elif not _echo and ctype == "SKILL" and sim.double_skill > 0:
        sim.double_skill -= 1
        echo = True
    hp_before = sum(m.hp for m in sim.mons)
    tgt = next((m for m in sim.mons if m.i == target), None)
    if ctx.beat_of_death:
        sim.self_loss += ctx.beat_of_death
    if ctx.hex and ctype != "ATTACK":
        sim.junk -= 2 * ctx.hex  # each non-Attack adds a Dazed to the deck (0.6 junk credit -> net cost)
    if ctype == "SKILL":
        for m in sim.mons:
            if m.nob and not m.dead:
                sim.enrage += m.nob
    if ctype in ("STATUS", "CURSE"):
        sim.junk += 1
    if ctx.time_warp >= 0 and ctx.time_warp + sim.played >= 12:
        sim.ended = True
    vuln_ok = not e.if_vuln or (tgt is not None and tgt.vuln > 0)
    if vuln_ok:
        sim.energy += e.energy
    # strength and dexterity before damage
    if e.strength and (not e.conditional_strength or (tgt is not None and tgt.atk > 0)
                       or (tgt is None and any(m.atk > 0 for m in sim.mons))):
        sim.str += e.strength
        sim.perm_str += e.strength
    if e.temp_strength:
        sim.str += e.temp_strength
    if e.double_strength and sim.str > 0:
        sim.perm_str += sim.str
        sim.str *= 2
    sim.dex += e.dex
    # hand-exhausting cards
    others = [j for j in remaining if j != idx]
    exhausted = 0
    if e.exhaust_hand == "all":
        exhausted = len(others)
    elif e.exhaust_hand == "nonattack":
        exhausted = sum(1 for j in others if ctx.hand[j].type.value != "ATTACK")
    # block
    frail = 0.75 if sim.frail else 1.0
    if e.block:
        sim.block += int((e.block + sim.dex) * frail)
    if e.block_per_exhaust:
        sim.block += int(exhausted * (e.block_per_exhaust + sim.dex) * frail)
    if e.double_block:
        sim.block *= 2
    # damage
    if e.damage or e.block_scaling or e.damage_per_exhaust:
        if e.block_scaling:
            base, hits = sim.block, 1
        elif e.damage_per_exhaust:
            base, hits = e.damage_per_exhaust, exhausted
        else:
            base = e.damage + e.per_strike * ctx.strikes
            hits = x_energy if (e.x_cost or e.hits == 0) else e.hits
        per = base + sim.str * e.strength_mult
        if sim.weak:
            per *= 0.75
        live = [m for m in sim.mons if not m.dead]
        if e.aoe:
            victims = [(m, 1.0) for m in live]
        elif e.random_target and live:
            victims = [(m, 1.0 / len(live)) for m in live]
        else:
            victims = [(tgt, 1.0)] if tgt is not None and not tgt.dead else []
        for m, share in victims:
            if m.sharp and ctype == "ATTACK":
                sim.self_loss += m.sharp
                sim.retal += m.sharp
            for _ in range(max(0, hits)):
                d = per * (1.5 if m.vuln > 0 else 1.0)
                _hit(sim, m, int(d) * share if share < 1 else int(d))
    # debuffs
    if e.vuln or e.weak:
        group = [m for m in sim.mons if not m.dead] if (e.aoe_debuff or e.aoe) else ([tgt] if tgt else [])
        for m in group:
            _debuff(m, e.vuln, e.weak)
    for n, perm in ((e.enemy_str_down, True), (e.enemy_str_down_temp, False)):
        if not n:
            continue
        group = [m for m in sim.mons if not m.dead] if e.aoe_debuff else ([tgt] if tgt else [])
        for m in group:
            if m.artifact > 0:
                m.artifact -= 1
                continue
            m.down += n
            if perm:
                m.down_perm += n
    if e.poison:
        live = [m for m in sim.mons if not m.dead]
        if e.aoe_debuff:                       # Crippling Cloud: all enemies
            group = live
        elif tgt is not None and not tgt.dead:  # Deadly Poison, Poisoned Stab, Corpse Explosion
            group = [tgt]
        else:                                  # Bouncing Flask (random): count it on the priority target
            group = [max(live, key=lambda x: (x.prio, -x.hp))] if live else []
        for m in group:
            if m.artifact > 0:
                m.artifact -= 1
            else:
                m.poison += e.poison
    if getattr(e, "shivs", 0):
        # Shivs cost nothing and hit for 4 (+Strength): count them now, on the priority target
        for _ in range(e.shivs):
            live = [m for m in sim.mons if not m.dead]
            if not live:
                break
            m = max(live, key=lambda x: (x.prio, -x.hp))
            per = (4 + sim.str) * (0.75 if sim.weak else 1.0)
            _hit(sim, m, int(per * (1.5 if m.vuln > 0 else 1.0)))
    if getattr(e, "poison_mult", 0) and tgt is not None and not tgt.dead and tgt.poison:
        tgt.poison *= e.poison_mult            # Catalyst
    if e.draw and vuln_ok:
        sim.draw_value += e.draw * (1.0 if sim.energy > 0 else 0.4)
    sim.card_loss += e.hp_loss
    sim.self_vuln += e.self_vuln
    sim.self_weak += e.self_weak
    sim.self_frail += e.self_frail
    sim.thorns_back += e.thorns
    if ctype == "POWER":
        sim.powers += 1
        sim.curiosity += ctx.curiosity_amt
        sim.turn_str += e.per_turn_strength
        sim.turn_block += e.per_turn_block
        sim.turn_energy += e.per_turn_energy
        sim.turn_dmg += e.per_turn_damage
    if getattr(e, "intangible", 0):
        sim.intangible = True
    if getattr(e, "double_attack", 0):
        sim.double_atk += e.double_attack
    if getattr(e, "double_skill", 0):
        sim.double_skill += e.double_skill
    if getattr(e, "heal_unblocked", False):
        sim.heal += max(0, hp_before - sum(m.hp for m in sim.mons))   # Reaper
    if echo:
        play(sim, ctx, idx, target, remaining, _echo=True, _x=x_energy)


# ---------------------------------------------------------------- scoring

def incoming(sim: Sim) -> tuple[float, int]:
    total, hits = 0.0, 0
    for m in sim.mons:
        if m.dead or not m.atk:
            continue
        if m.poison >= m.hp > 0:
            continue   # its Poison kills it at the start of its turn, before it attacks
        per = max(0.0, m.atk - m.down * (0.75 if m.weak else 1.0))
        if m.shifting:  # Transient: every point of damage dealt this turn is Strength it loses
            per = max(0.0, per - (m.hp0 - m.hp))
        if m.weak0 == 0 and m.new_weak:
            per *= 0.75
        total += int(per) * m.hits
        hits += m.hits
    return total, hits


def evaluate(sim: Sim, ctx: Ctx, P: dict) -> tuple[float, dict]:
    v = 0.0
    dealt = 0.0
    kills = 0
    enemy_left = 0.0
    inc, inc_hits = incoming(sim)
    # Flame Barrier style thorns hit back every attacker
    if sim.thorns_back and inc_hits:
        for m in sim.mons:
            if not m.dead and m.atk:
                m_hp = m.hp
                m.hp = max(0, m.hp - sim.thorns_back * m.hits)
                dealt += m_hp - m.hp
                v += P["damage"] * (m_hp - m.hp) * m.prio
    for m in sim.mons:
        removed = m.hp0 - m.hp
        mult = m.prio
        if m.asleep and not m.dead:
            mult *= 0.15  # Lagavulin: set up while it sleeps rather than chip it awake
            if removed > 0 and ctx.turn <= 2:
                v -= 3.0
        v += P["damage"] * removed * mult
        dealt += removed
        if m.barricade and not m.dead:
            v += P["damage"] * 0.8 * max(0, m.block0 - m.block)
        if m.dead:
            kills += 1
            v += P["kill"] * m.prio
            continue
        enemy_left += m.hp
        v += P["vuln"] * 0.5 * min(m.new_vuln, 3)
        atk_total = m.atk * m.hits
        v += P["weak"] * m.new_weak * max(atk_total, 6) / 3.0
        if m.down_perm:
            v += P["block"] * m.down_perm * max(1, m.hits) * 1.5
        if m.poison:
            v += P["damage"] * min(m.hp, m.poison * 2.5)
    taken = max(0.0, inc - sim.block)
    if sim.intangible:
        taken = min(taken, float(inc_hits))   # Intangible: every hit deals 1
    if sim.heal:
        # Reaper: healing is worth as much as HP not lost (only up to the HP missing)
        v += P["hp_cost"] * min(sim.heal, max(0, ctx.max_hp - ctx.hp + taken))
    v -= P["block"] * taken
    v += P["surplus_block"] * max(0.0, sim.block - inc) * (6 if ctx.barricade else 1)
    hp_frac = ctx.hp / max(1, ctx.max_hp)
    # self-inflicted damage (Thorns, Sharp Hide...); a stalled fight discounts it, since waiting costs more.
    # When every enemy left retaliates there is no safe target: the hits have to land some time, and Spiker's
    # Thorns only grow, so hitting now costs little extra (run 211 never attacked a 54 HP Spiker and died).
    retal = sim.retal
    if ctx.no_safe_target and taken + sim.self_loss + sim.card_loss < ctx.hp * 0.6:
        retal *= 0.25
    v -= P["block"] * (sim.self_loss - sim.retal + retal) / (1 + ctx.stall)
    if all(m.dead for m in sim.mons):
        # the fight is over: no more enemy turns (ending the turn instead keeps taking hits)
        v += P["block"] * max(ctx.inc0, 5.0) * 1.5
    v -= P["hp_cost"] * sim.card_loss * (2.0 if hp_frac < 0.3 else 1.0)
    for m in sim.mons:
        if m.name != "The Champ":
            continue
        half = m.max_hp / 2
        if m.hp0 <= half:
            # phase 2: it cleansed debuffs, gained Strength and Executes for two big hits -- race it down
            v += P["damage"] * (m.hp0 - m.hp) * 0.5
        elif m.hp <= half and not m.dead and not ctx.stall:
            # crossing half triggers Anger then Execute: fine with HP to spare, costly when two Executes
            # would kill (every recent Champ loss died in phase 2 with the Champ at 107-180 HP)
            execute = 2 * (16 + ctx.champ_str)
            if ctx.hp - (taken + sim.self_loss + sim.card_loss) < 1.5 * execute:
                v -= P["block"] * 0.6 * execute   # the stall guard lifts this after one turn: never a stall
    if taken + sim.self_loss + sim.card_loss >= ctx.hp:
        v -= 500.0
    fight = 1.0 + min(2.0, enemy_left / 40.0)
    v += P["strength"] * sim.perm_str * fight
    v += P["strength"] * sim.turn_str * fight * 1.5
    v += P["block"] * sim.turn_block * fight * 0.7
    v += P["damage"] * sim.turn_dmg * max(1, sum(1 for m in sim.mons if not m.dead)) * fight * 0.7
    v += P["energy"] * sim.turn_energy * fight * 0.5
    if sim.powers:
        asleep = any(m.asleep and not m.dead for m in sim.mons)
        v += P["power"] * sim.powers * (1.5 if ctx.elite or asleep else 1.0) * \
            (1.0 if ctx.turn <= 3 else 0.5) * fight / 2
    if sim.curiosity:
        v -= P["block"] * sim.curiosity * 4 * fight * 1.5  # it keeps the Strength all of phase 1
    if sim.enrage:
        nob_hits = max((m.hits for m in sim.mons if m.nob and not m.dead), default=0) or 1
        v -= P["block"] * sim.enrage * nob_hits * 2.5
    if ctx.time_warp >= 0:
        count = ctx.time_warp + sim.played
        if count >= 12:
            v -= P["strength"] * 2 * fight
        elif count >= 10:
            v -= 4.0
    v += P["draw"] * sim.draw_value
    v += 0.6 * sim.junk
    if sim.self_vuln:
        v -= P["block"] * 0.5 * max(inc, 6.0) * min(sim.self_vuln, 2) * 0.5
    v -= P["damage"] * 3 * sim.self_weak + P["block"] * 2 * sim.self_frail
    return v, {"dealt": dealt, "block": sim.block, "taken": taken + sim.self_loss, "kills": kills}


# ---------------------------------------------------------------- search

def plan_turn(message, P: dict, deck_names: list[str], max_nodes: int = MAX_NODES, stall: int = 0) -> Plan:
    sim, ctx = build(message, deck_names)
    ctx.stall = stall
    ctx.inc0 = incoming(sim.copy())[0]
    champ = next((m for m in message.game_state.combat_state.monsters if m.name == "The Champ" and alive(m)), None)
    ctx.champ_str = pw(champ.powers, "Strength") if champ else 0
    alive_mons = [m for m in sim.mons if not m.dead]
    ctx.no_safe_target = bool(alive_mons) and all(m.thorns or m.sharp for m in alive_mons)
    base, base_sum = evaluate(sim.copy(), ctx, P)
    best = Plan(steps=[], value=base, base=base, summary=base_sum)
    seen: set = set()
    nodes = 0

    def can_play(i: int, s: Sim) -> bool:
        card = ctx.hand[i]
        cost = s.energy if card.cost == -1 else max(card.cost, 0)
        if card.cost == -1 and s.energy <= 0 and ctx.effects[i].damage:
            return False
        if cost > s.energy:
            return False
        if ctx.playable0[i]:
            return True
        # unplayable at the start only because energy was short: fine once energy arrives
        return card.cost > ctx.energy0 and card.type.value not in ("STATUS", "CURSE")

    def dfs(s: Sim, remaining: list[int], path: list) -> None:
        nonlocal nodes, best
        nodes += 1
        if path:
            val, summ = evaluate(s.copy(), ctx, P)
            if val > best.value:
                best = Plan(steps=list(path), value=val, base=base, summary=summ)
        if nodes >= max_nodes or s.ended or all(m.dead for m in s.mons):
            return
        tried = set()
        for i in remaining:
            if not can_play(i, s):
                continue
            card = ctx.hand[i]
            key = (card.name, card.upgrades, card.cost)
            targets = [m.i for m in s.mons if not m.dead] if card.has_target else [None]
            for t in targets:
                if (key, t) in tried:
                    continue
                tried.add((key, t))
                child = s.copy()
                play(child, ctx, i, t, remaining)
                rest = _kept(ctx, i, remaining)
                state_key = (tuple(sorted((ctx.hand[j].name, ctx.hand[j].upgrades) for j in rest)), child.key())
                if state_key in seen:
                    continue
                seen.add(state_key)
                dfs(child, rest, path + [(i, t)])
                if nodes >= max_nodes:
                    return

    dfs(sim, list(range(len(ctx.hand))), [])
    best.nodes = nodes
    best.steps = _draw_first(best, sim, ctx, P)
    return best


def _kept(ctx: Ctx, idx: int, remaining: list[int]) -> list[int]:
    e = ctx.effects[idx]
    if e.exhaust_hand == "all":
        return []
    if e.exhaust_hand == "nonattack":
        return [j for j in remaining if j != idx and ctx.hand[j].type.value == "ATTACK"]
    return [j for j in remaining if j != idx]


def _replay(sim: Sim, ctx: Ctx, P: dict, steps: list) -> float | None:
    s = sim.copy()
    remaining = list(range(len(ctx.hand)))
    for i, t in steps:
        if i not in remaining:
            return None
        card = ctx.hand[i]
        cost = s.energy if card.cost == -1 else max(card.cost, 0)
        if cost > s.energy:
            return None
        if t is not None and not any(m.i == t and not m.dead for m in s.mons):
            return None
        play(s, ctx, i, t, remaining)
        remaining = _kept(ctx, i, remaining)
    return evaluate(s, ctx, P)[0]


def _draw_first(best: Plan, sim: Sim, ctx: Ctx, P: dict) -> list:
    """Drawing first shows more options; do it when it costs (almost) nothing."""
    steps = best.steps
    if len(steps) < 2:
        return steps
    for k, (i, t) in enumerate(steps):
        e = ctx.effects[i]
        if k == 0 or not e.draw or e.if_vuln:
            continue
        reordered = [steps[k]] + steps[:k] + steps[k + 1:]
        val = _replay(sim, ctx, P, reordered)
        if val is not None and val >= best.value - max(0.5, 0.05 * abs(best.value - best.base)):
            return reordered
        break
    return steps
