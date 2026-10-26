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
import monsters

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
                 "asleep", "nob", "dead", "shifting", "flight", "nxt", "mark", "thr")

    def copy(self) -> "Mon":
        n = Mon.__new__(Mon)
        for s in Mon.__slots__:
            setattr(n, s, getattr(self, s))
        return n


DEX_BLOCK_CARDS = 1.5   # block cards played per turn, for the lasting value of Dexterity
STALL_FORCE = 4         # turns without enemy HP change before damage gets a large bonus
THREAT_RATE = 15.0     # enemy HP your deck removes a turn (what turns HP dealt into turns of its hits saved)
VAULT_TURN = 14.0      # what an extra turn is worth: about this much block or damage
POWER_TURN_SHARE = 0.6  # share of a power's per-turn value counted for each turn left (discount, uneven use)


class Sim:
    __slots__ = ("energy", "block", "str", "dex", "weak", "frail", "mons", "self_loss", "card_loss",
                 "perm_str", "perm_dex", "turn_str", "turn_block", "turn_energy", "turn_dmg", "powers",
                 "draw_value", "enrage", "curiosity", "played", "junk", "self_vuln", "self_weak",
                 "self_frail", "thorns_back", "ended", "retal", "intangible", "double_atk", "double_skill",
                 "heal", "orbs", "orb_slots", "focus", "perm_focus", "lightning_all",
                 "next_block", "next_energy", "next_draw", "stance", "stance0", "mantra", "doomed",
                 "turn_dmg1", "turn_draw", "vault")

    def copy(self) -> "Sim":
        n = Sim.__new__(Sim)
        for s in Sim.__slots__:
            setattr(n, s, getattr(self, s))
        n.mons = [m.copy() for m in self.mons]
        n.orbs = [list(o) for o in self.orbs]
        return n

    def key(self) -> tuple:
        return (self.energy, self.block, self.str, self.dex, self.played,
                tuple((m.hp, m.block, m.vuln, m.weak, m.down, m.artifact, m.poison) for m in self.mons),
                round(self.draw_value, 2), self.perm_str, self.powers, self.self_loss,
                self.double_atk, self.double_skill, self.intangible, round(self.heal, 1),
                tuple(tuple(o) for o in self.orbs), self.focus, self.orb_slots)


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
    wrath_stuck: float = 0.0  # chance the next hand has no way out of Wrath


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
    sim.perm_dex = 0
    sim.perm_str = sim.turn_str = sim.turn_block = sim.turn_energy = sim.turn_dmg = 0
    sim.turn_dmg1 = sim.turn_draw = 0.0
    sim.vault = False
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
    sim.next_block = sim.next_energy = sim.next_draw = 0
    # Watcher: the game doesn't report the stance, the policy tracks it (plan_turn(stance=...))
    sim.stance = sim.stance0 = "Neutral"
    sim.mantra = pw(p.powers, "Mantra")
    sim.doomed = False
    # Defect orbs: the game reports each orb's evoke/passive amounts with Focus already applied
    orbs = getattr(p, "orbs", None) or []
    sim.orb_slots = len(orbs)
    sim.orbs = [[o.name, o.evoke_amount, o.passive_amount] for o in orbs if o.name in ORB_BASE]
    sim.focus = pw(p.powers, "Focus")
    sim.perm_focus = 0
    sim.lightning_all = pw(p.powers, "Electrodynamics", "Electro") > 0
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
        x.nxt = 0.0       # next turn's attack total (monsters.next_attack), set in plan_turn
        x.mark = pw(m.powers, "Mark")
        # its attack per turn for the rest of the fight: this turn's or its usual, plus Ritual / Strength growth
        usual = monsters.TYPICAL.get(m.name, 6.0) + pw(m.powers, "Strength") * max(1, x.hits)
        x.thr = max(float(x.atk * x.hits), usual) + 1.5 * pw(m.powers, "Ritual") * max(1, x.hits)
        if m.name in ("Orb", "Bronze Orb", "Torch Head", "Dagger"):
            x.thr *= 0.5  # they die with their summoner
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
    sim.perm_dex += e.dex
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
    if getattr(e, "bullet_time", False):
        # the rest of the hand costs 0 this turn: as much energy as those cards cost
        sim.energy += sum(max(0, ctx.hand[j].cost) for j in others)
    tgt_hp = tgt.hp if tgt is not None else 0
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
        if ctype == "ATTACK":
            per *= STANCE_DEALT.get(sim.stance, 1.0)   # Wrath x2, Divinity x3
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
    if getattr(e, "wallop", False) and tgt is not None:
        sim.block += max(0, tgt_hp - tgt.hp)
    if getattr(e, "judgment", 0) and tgt is not None and not tgt.dead and tgt.hp <= e.judgment and tgt.cap > 10 ** 5:
        tgt.hp = 0
        tgt.dead = True
    if getattr(e, "mark", 0) and tgt is not None and not tgt.dead:
        # Pressure Points: Mark the target, then every enemy loses HP equal to its Mark (block doesn't stop it)
        if tgt.artifact > 0:
            tgt.artifact -= 1
        else:
            tgt.mark += e.mark
        for m in sim.mons:
            if m.mark and not m.dead:
                m.hp -= min(m.mark, m.cap) if not m.intangible else 1
                if m.hp <= 0:
                    m.hp = 0
                    m.dead = True
    if getattr(e, "vault", False):
        sim.vault = True
    # debuffs
    weak = e.weak + (x_energy if e.x_weak else 0)       # Malaise: X Weak
    if e.vuln or weak:
        group = [m for m in sim.mons if not m.dead] if (e.aoe_debuff or e.aoe) else ([tgt] if tgt else [])
        for m in group:
            _debuff(m, e.vuln, weak)
    if e.stance or e.mantra or e.wrath_block:
        _stance_effects(sim, e, tgt)
    sim.next_block += e.next_block
    sim.next_energy += e.next_energy
    sim.next_draw += e.next_draw
    for n, perm in ((e.enemy_str_down + (x_energy if e.x_str_down else 0), True), (e.enemy_str_down_temp, False)):
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
    if sim.orb_slots or e.orb_slots or e.channel or e.channel_per_enemy or e.channel_x:
        _orb_effects(sim, e, tgt, x_energy)
    if getattr(e, "poison_mult", 0) and tgt is not None and not tgt.dead and tgt.poison:
        tgt.poison *= e.poison_mult            # Catalyst
    if e.draw and vuln_ok:
        sim.draw_value += e.draw * (1.0 if sim.energy > 0 else 0.4)
    sim.card_loss += e.hp_loss
    sim.self_vuln += e.self_vuln
    sim.self_weak += e.self_weak
    sim.self_frail += e.self_frail
    sim.thorns_back += e.thorns
    if ctype == "POWER" or getattr(e, "setup", False):
        if ctype == "POWER":
            sim.powers += 1
            sim.curiosity += ctx.curiosity_amt
        sim.turn_str += e.per_turn_strength
        sim.turn_block += e.per_turn_block
        sim.turn_energy += e.per_turn_energy
        sim.turn_dmg += e.per_turn_damage
        sim.turn_dmg1 += getattr(e, "per_turn_single", 0)
        sim.turn_draw += getattr(e, "per_turn_draw", 0)
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

# ---------------------------------------------------------------- Defect orbs

ORB_BASE = {"Lightning": (3, 8), "Frost": (2, 5), "Dark": (6, 6), "Plasma": (1, 2)}   # (passive, evoke) at 0 Focus
ORB_FUTURE = 0.7   # orbs keep paying every turn: their passives count for the rest of the fight, like Strength


def _orb_new(kind: str, focus: int) -> list:
    """[kind, evoke amount, passive amount] for a freshly channeled orb."""
    passive, evoke = ORB_BASE[kind]
    if kind in ("Lightning", "Frost"):
        return [kind, max(0, evoke + focus), max(0, passive + focus)]
    if kind == "Dark":
        return [kind, evoke, max(0, passive + focus)]
    return [kind, evoke, passive]


def _orb_target(sim: Sim):
    live = [m for m in sim.mons if not m.dead]
    return max(live, key=lambda x: (x.prio, -x.hp)) if live else None


def _lightning(sim: Sim, amount: int) -> None:
    targets = [m for m in sim.mons if not m.dead] if sim.lightning_all else [_orb_target(sim)]
    for m in targets:
        if m is not None:
            _hit(sim, m, amount)   # orb damage ignores Strength, Weak and Vulnerable


def _evoke(sim: Sim, orb: list) -> None:
    kind, amount = orb[0], orb[1]
    if kind == "Lightning":
        _lightning(sim, amount)
    elif kind == "Frost":
        sim.block += amount
    elif kind == "Dark":
        live = [m for m in sim.mons if not m.dead]
        if live:
            _hit(sim, min(live, key=lambda x: x.hp), amount)   # Dark evokes on the lowest-HP enemy
    elif kind == "Plasma":
        sim.energy += amount


def _channel(sim: Sim, kind: str) -> None:
    if sim.orb_slots <= 0 or kind not in ORB_BASE:
        return
    if len(sim.orbs) >= sim.orb_slots:
        _evoke(sim, sim.orbs.pop(0))   # full: the oldest orb is evoked to make room
    sim.orbs.append(_orb_new(kind, sim.focus))


def _orb_effects(sim: Sim, e, tgt, x_energy: int) -> None:
    """Focus, orb slots, evoke and channel effects of one card, in the order the game applies them."""
    if e.focus:
        sim.focus += e.focus
        sim.perm_focus += e.focus
        for o in sim.orbs:
            if o[0] in ("Lightning", "Frost"):
                o[1] = max(0, o[1] + e.focus)
                o[2] = max(0, o[2] + e.focus)
            elif o[0] == "Dark":
                o[2] = max(0, o[2] + e.focus)
    if e.orb_slots:
        sim.orb_slots = max(0, sim.orb_slots + e.orb_slots)
        del sim.orbs[sim.orb_slots:]
    if e.lightning_all:
        sim.lightning_all = True
    if e.per_orb and tgt is not None and len(sim.orbs) > 1:
        per = int((e.damage + sim.str) * (0.75 if sim.weak else 1.0) * (1.5 if tgt.vuln > 0 else 1.0))
        for _ in range(len(sim.orbs) - 1):   # Barrage: the first hit was the card's own damage
            if not tgt.dead:
                _hit(sim, tgt, per)
    times = x_energy if e.evoke == -1 else e.evoke
    if times and sim.orbs:
        orb = sim.orbs.pop(0)
        for _ in range(times):
            _evoke(sim, orb)
        if e.recursion:
            _channel(sim, orb[0])
    live = sum(1 for m in sim.mons if not m.dead)
    for kind in list(e.channel) + [e.channel_per_enemy] * (live if e.channel_per_enemy else 0) + \
            [e.channel_x] * (x_energy if e.channel_x else 0):
        _channel(sim, kind)


def _orb_passives(sim: Sim) -> None:
    """End of turn, before the enemies act: Lightning hits, Frost blocks, Dark grows."""
    for o in sim.orbs:
        if o[0] == "Lightning":
            _lightning(sim, o[2])
        elif o[0] == "Frost":
            sim.block += o[2]
        elif o[0] == "Dark":
            o[1] += o[2]


# ---------------------------------------------------------------- Watcher stances

SPLITTERS = {"Slime Boss", "Acid Slime (L)", "Spike Slime (L)"}   # split into two at their HP when at half or below
STANCE_DEALT = {"Wrath": 2.0, "Divinity": 3.0}     # attack damage dealt
STANCE_TAKEN = {"Wrath": 2.0}                      # attack damage taken


def _set_stance(sim: Sim, new: str) -> None:
    if new == sim.stance:
        return
    if sim.stance == "Calm":
        sim.energy += 2            # leaving Calm
    if new == "Divinity":
        sim.energy += 3
    sim.stance = new


def _stance_effects(sim: Sim, e, tgt) -> None:
    if e.wrath_block and sim.stance == "Wrath":
        sim.block += e.wrath_block                     # Halt
    if e.stance_cond == "calm_draw" and sim.stance == "Calm":
        sim.draw_value += e.cond_n * (1.0 if sim.energy > 0 else 0.4)   # Inner Peace in Calm
    elif e.stance_cond == "wrath_vuln" and sim.stance == "Wrath":
        for m in sim.mons:
            if not m.dead:
                _debuff(m, e.cond_n, 0)                # Indignation in Wrath
    elif e.stance_cond == "if_attacking" and not (tgt is not None and tgt.atk > 0):
        pass                                           # Fear No Evil: only when the target attacks
    elif e.stance:
        _set_stance(sim, e.stance)
    if e.mantra:
        sim.mantra += e.mantra
        if sim.mantra >= 10:
            sim.mantra -= 10
            _set_stance(sim, "Divinity")
    if e.die_next_turn:
        sim.doomed = True                              # Blasphemy
    if e.ends_turn:
        sim.ended = True                               # Meditate


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
    total *= STANCE_TAKEN.get(sim.stance, 1.0) / STANCE_TAKEN.get(sim.stance0, 1.0)
    return total, hits


def stance_after(message, deck_names: list[str], stance: str, step: tuple) -> str:
    """The stance after playing one card (the policy tracks the stance; the game state doesn't carry it)."""
    sim, ctx = build(message, deck_names)
    sim.stance = sim.stance0 = stance or "Neutral"
    i, t = step
    play(sim, ctx, i, t, list(range(len(ctx.hand))))
    return sim.stance


def evaluate(sim: Sim, ctx: Ctx, P: dict) -> tuple[float, dict]:
    if sim.orbs:
        sim = sim.copy()
        _orb_passives(sim)   # they trigger before the enemies act: Frost blocks this turn's hits
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
        if removed > 0 and not m.asleep:
            # every HP off an enemy brings its death forward by 1/THREAT_RATE turns, and with it its hits: valued like
            # block, so damage can't lose to a Defend against enemies that grow (three Cultists: the Watcher blocked
            # five turns and died; learned damage/block weights had drifted to 0.34 / 3.22)
            v += P["block"] * P.get("threat", 0.5) * m.thr * min(removed, m.hp0) / THREAT_RATE
        dealt += removed
        if m.barricade and not m.dead:
            v += P["damage"] * 0.8 * max(0, m.block0 - m.block)
        if m.dead:
            kills += 1
            v += P["kill"] * m.prio
            continue
        enemy_left += m.hp
        if m.name in SPLITTERS and m.hp <= m.max_hp / 2 < m.hp0 and not ctx.stall:
            # it splits next turn into two copies at its current HP: what is left now counts twice, so a turn that
            # crosses half should cross it deep (or wait for a bigger turn); the reference AI splits at 50% or below
            # (half weight: waiting costs Slams too; the stall guard lifts it after a turn without progress)
            v -= P["damage"] * 0.5 * m.hp * mult
            enemy_left += m.hp
        v += P["vuln"] * 0.5 * min(m.new_vuln, 3)
        atk_total = m.atk * m.hits
        v += P["weak"] * m.new_weak * max(atk_total, 6) / 3.0
        if m.down_perm:
            v += P["block"] * m.down_perm * max(1, m.hits) * 1.5
        if m.poison:
            v += P["damage"] * min(m.hp, m.poison * 2.5)
    if sim.vault:
        # Vault: an extra turn comes before the enemies act. Block played before it is gone by then; the extra turn
        # (3 energy, 5 cards) blocks what it needs and spends the rest on damage
        sim.block = min(inc, VAULT_TURN)
        v += P["damage"] * max(0.0, VAULT_TURN - inc)
    if ctx.stall >= STALL_FORCE and dealt > 0:
        # no enemy HP has moved for several turns: any damage beats another empty turn (whatever the weights say)
        v += P["block"] * 0.5 * dealt
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
    # fight length from the enemies' HP at the start of the turn: measured after this turn's damage, every hit
    # shortened the fight and so cut the lasting value of orbs / Strength / powers -- a Defect with 5 Frost orbs
    # scored Strike at -0.9 against a Gremlin Nob it could never lose to and ended the turn 2600 times
    left0 = sum(m.hp0 for m in sim.mons if m.hp0 > 0)
    fight = 1.0 + min(2.0, left0 / 40.0)
    v += P["strength"] * sim.perm_str * fight
    if sim.stance == "Calm":
        v += P["energy"] * 2 * 0.6        # Calm banked: leaving it next turn gives 2 energy
    if sim.stance == "Wrath" and ctx.wrath_stuck and not all(m.dead for m in sim.mons):
        # Wrath stays: next turn's hits are doubled too unless the next hand can leave it (Hexaghost's quiet turn 1
        # ended in Wrath, then Divider hit for 72). Next turn's attacks come from the monster AI (monsters.py).
        v -= P["block"] * 1.0 * ctx.wrath_stuck * sum(m.nxt for m in sim.mons if not m.dead)
    if sim.doomed and not all(m.dead for m in sim.mons):
        v -= 500.0                        # Blasphemy: you die next turn unless the fight ends now
    # "Next turn, ..." riders: next turn's block / energy / draw, a little less than now
    v += 0.8 * (P["block"] * sim.next_block + P["energy"] * sim.next_energy) + 0.5 * P["draw"] * sim.next_draw
    if sim.orbs or sim.perm_focus:
        # orbs and Focus keep working every turn for the rest of the fight
        live = max(1, sum(1 for m in sim.mons if not m.dead))
        light = sum(o[2] for o in sim.orbs if o[0] == "Lightning") * (live if sim.lightning_all else 1)
        frost = sum(o[2] for o in sim.orbs if o[0] == "Frost")
        dark = sum(1 for o in sim.orbs if o[0] == "Dark")
        plasma = sum(1 for o in sim.orbs if o[0] == "Plasma")
        v += (P["damage"] * light + P["block"] * frost + P["damage"] * 3.0 * dark
              + P["energy"] * 0.5 * plasma) * fight * ORB_FUTURE
        v += P["block"] * sim.perm_focus * max(1, len(sim.orbs)) * fight * 0.5
    # Dexterity lasts the fight like Strength: +1 block on each block card, ~1.5 of them a turn (Footwork sat
    # in hand valued only as a generic power, and the Silent learned to skip it: taken 5 times in 68 offers)
    v += P["block"] * sim.perm_dex * fight * DEX_BLOCK_CARDS
    v += P["strength"] * sim.turn_str * fight * 1.5
    # per-turn powers pay for the turns the fight has left (~25 enemy HP a turn): a boss fight is worth up to 6
    # turns of it, a weak hallway fight about one (the old cap of 3 made Deva Form vs a 250 HP boss a Defend)
    horizon = min(6.0, max(1.0, left0 / 25.0)) * POWER_TURN_SHARE
    # ...when you live to use them: a power that leaves you under 40% HP after the hits is worth less
    left = (ctx.hp - taken - sim.self_loss - sim.card_loss) / max(1, ctx.max_hp)
    horizon *= min(1.0, max(0.2, left / 0.4))
    v += P["block"] * sim.turn_block * horizon
    v += P["damage"] * sim.turn_dmg * max(1, sum(1 for m in sim.mons if not m.dead)) * horizon
    v += P["damage"] * sim.turn_dmg1 * horizon
    v += P["energy"] * sim.turn_energy * horizon * 0.8
    v += P["draw"] * sim.turn_draw * horizon
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


# cards that leave Wrath (into Calm or Neutral)
WRATH_EXITS = {"Vigilance", "Empty Body", "Empty Fist", "Empty Mind", "Inner Peace", "Fear No Evil", "Meditate",
               "Tranquility"}


def wrath_stuck(deck_names: list[str]) -> float:
    """Chance a 5-card hand drawn from the deck holds no card that leaves Wrath."""
    n = max(1, len(deck_names))
    k = sum(1 for d in deck_names if C.base_name(d) in WRATH_EXITS)
    stuck = 1.0
    for i in range(min(5, n)):
        stuck *= max(0.0, (n - k - i) / (n - i))
    return stuck


# ---------------------------------------------------------------- search

def plan_turn(message, P: dict, deck_names: list[str], max_nodes: int = MAX_NODES, stall: int = 0,
              stance: str = "Neutral") -> Plan:
    sim, ctx = build(message, deck_names)
    sim.stance = sim.stance0 = stance or "Neutral"
    ctx.stall = stall
    ctx.inc0 = incoming(sim.copy())[0]
    if any(e.stance == "Wrath" for e in ctx.effects) or sim.stance == "Wrath":
        c = message.game_state.combat_state
        for x in sim.mons:
            m = c.monsters[x.i]
            cur = x.atk * x.hits / STANCE_TAKEN.get(sim.stance0, 1.0) if x.atk > 0 else 0.0
            x.nxt = monsters.next_attack(m, c.turn, c.player.current_hp, cur)
        ctx.wrath_stuck = wrath_stuck(deck_names or [h.name for h in ctx.hand])
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
