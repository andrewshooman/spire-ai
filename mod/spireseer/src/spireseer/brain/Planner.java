package spireseer.brain;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Whole-turn combat search -- a faithful port of Spire AI's planner.py (the parity test checks it against the
 * Python planner on thousands of real combat states). It tries orders of the cards in hand within the energy,
 * simulates each sequence (damage through block, Vulnerable / Weak, Strength, block vs the intents, enemy
 * mechanics, orbs, stances) and scores the position the turn would end in. Only the first step is played; the game
 * is read again after every card.
 *
 * Enemy mechanics: Barricade, Thorns / Sharp Hide, Malleable, Curl Up, Anger / Enrage (Gremlin Nob), Curiosity
 * (Awakened One), Time Warp (Time Eater), Beat of Death / Invincible (the Heart), Intangible, Artifact, Flight
 * (Byrds), Shifting (Transient), Lagavulin asleep, splitting slimes, The Champ's phase 2, kill priorities.
 */
public final class Planner {
    public static final int MAX_NODES = 3000;          // the Python planner's budget (parity)
    static final double DEX_BLOCK_CARDS = 1.5;
    static final int STALL_FORCE = 4;
    static final double THREAT_RATE = 15.0;
    static final double VAULT_TURN = 14.0;
    static final double POWER_TURN_SHARE = 0.6;
    static final double ORB_FUTURE = 0.7;
    static final String[] ORB = {"Lightning", "Frost", "Dark", "Plasma"};
    static final int[][] ORB_BASE = {{3, 8}, {2, 5}, {6, 6}, {1, 2}};   // {passive, evoke} at 0 Focus
    static final Set<String> SPLITTERS = new HashSet<>(Arrays.asList("Slime Boss", "Acid Slime (L)", "Spike Slime (L)"));
    static final Set<String> WRATH_EXITS = new HashSet<>(Arrays.asList("Vigilance", "Empty Body", "Empty Fist",
            "Empty Mind", "Inner Peace", "Fear No Evil", "Meditate", "Tranquility"));
    public static final Map<String, Double> PRIORITY = new HashMap<>();

    static {
        Object[] p = {"Red Slaver", 1.5, "Donu", 1.6, "Dagger", 1.5, "Mystic", 1.4, "Gremlin Wizard", 1.3, "Orb", 0.55,
                "Bronze Orb", 0.55, "Sentry", 1.1, "Torch Head", 0.6, "Darkling", 0.8, "Repulsor", 0.9, "Spiker", 0.7,
                "Gremlin Leader", 1.1, "Cultist", 1.0, "Orb Walker", 1.3, "Exploder", 1.3, "Transient", 0.3,
                "Bronze Automaton", 1.0};
        for (int i = 0; i < p.length; i += 2) PRIORITY.put((String) p[i], (Double) p[i + 1]);
    }

    /** Combat weights (Spire AI model.COMBAT_DEFAULTS keys); defaults are the Ironclad's learned values. */
    public static final class Params {
        public double damage = 0.51, kill = 2.29, block = 7.3, surplusBlock = 0.063, vuln = 3.9, weak = 0.5;
        public double draw = 3.9, strength = 4.2, power = 4.9, hpCost = 3.3, endThreshold = 0.49, energy = 6.2;
        public double threat = 0.3, potionHp = 0.32;

        public static Params from(Map<String, Double> m) {
            Params p = new Params();
            p.damage = m.getOrDefault("damage", p.damage);
            p.kill = m.getOrDefault("kill", p.kill);
            p.block = m.getOrDefault("block", p.block);
            p.surplusBlock = m.getOrDefault("surplus_block", p.surplusBlock);
            p.vuln = m.getOrDefault("vuln", p.vuln);
            p.weak = m.getOrDefault("weak", p.weak);
            p.draw = m.getOrDefault("draw", p.draw);
            p.strength = m.getOrDefault("strength", p.strength);
            p.power = m.getOrDefault("power", p.power);
            p.hpCost = m.getOrDefault("hp_cost", p.hpCost);
            p.endThreshold = m.getOrDefault("end_threshold", p.endThreshold);
            p.energy = m.getOrDefault("energy", p.energy);
            p.threat = m.getOrDefault("threat", 0.5);
            p.potionHp = m.getOrDefault("potion_hp", p.potionHp);
            return p;
        }
    }

    public static final class Step {
        public final int card, target;   // hand index, monster index (-1 = none)
        public Step(int card, int target) { this.card = card; this.target = target; }
    }

    public static final class Plan {
        public final List<Step> steps = new ArrayList<>();
        public double value, base;
        public int dealt, block, taken, kills, nodes;

        public boolean endTurn(Params p) {
            return steps.isEmpty() || value - base < p.endThreshold;
        }

        public String describe(Sim s) {
            StringBuilder b = new StringBuilder();
            for (Step st : steps) {
                if (b.length() > 0) b.append(", ");
                Sim.Card c = card(s, st.card);
                b.append(c == null ? "?" : c.name);
                Sim.Mon m = s.mon(st.target);
                if (m != null) b.append(" > ").append(m.name);
            }
            if (b.length() == 0) b.append("end turn");
            return b + "  (" + totals() + ")";
        }

        public String totals() {
            return "deal " + dealt + ", block " + block + ", take " + taken + (kills > 0 ? ", kill " + kills : "");
        }
    }

    static Sim.Card card(Sim s, int index) {
        for (Sim.Card c : s.hand) if (c.index == index) return c;
        return null;
    }

    // ---------------------------------------------------------------- simulated state (planner.Mon / planner.Sim)

    static final class M {
        int i;                 // game index
        String name;
        double hp, hp0, block, block0, cap;
        int maxHp, vuln, weak, weak0, newVuln, newWeak, atk, hits, down, downPerm, poison, artifact, thorns, sharp;
        int malleable, curl, nob, flight, mark;
        boolean barricade, intangible, asleep, shifting, dead;
        double prio, nxt, thr;

        M copy() {
            M n = new M();
            n.i = i; n.name = name; n.hp = hp; n.hp0 = hp0; n.block = block; n.block0 = block0; n.cap = cap;
            n.maxHp = maxHp; n.vuln = vuln; n.weak = weak; n.weak0 = weak0; n.newVuln = newVuln; n.newWeak = newWeak;
            n.atk = atk; n.hits = hits; n.down = down; n.downPerm = downPerm; n.poison = poison; n.artifact = artifact;
            n.thorns = thorns; n.sharp = sharp; n.malleable = malleable; n.curl = curl; n.nob = nob; n.flight = flight;
            n.mark = mark; n.barricade = barricade; n.intangible = intangible; n.asleep = asleep; n.shifting = shifting;
            n.dead = dead; n.prio = prio; n.nxt = nxt; n.thr = thr;
            return n;
        }
    }

    static final class St {
        double energy, block, selfLoss, cardLoss, retal, drawValue, heal, nextBlock, nextEnergy, nextDraw;
        double turnStr, turnBlock, turnEnergy, turnDmg, turnDmg1, turnDraw;
        int str, dex, permStr, permDex, powers, enrage, curiosity, played, junk, selfVuln, selfWeak, selfFrail;
        int thornsBack, doubleAtk, doubleSkill, orbSlots, focus, permFocus, mantra;
        boolean weak, frail, ended, intangible, lightningAll, doomed, vault;
        String stance = "Neutral", stance0 = "Neutral";
        M[] mons;
        List<Object[]> orbs = new ArrayList<>();   // {kind String, evoke Integer, passive Integer}

        St copy() {
            St n = new St();
            n.energy = energy; n.block = block; n.selfLoss = selfLoss; n.cardLoss = cardLoss; n.retal = retal;
            n.drawValue = drawValue; n.heal = heal; n.nextBlock = nextBlock; n.nextEnergy = nextEnergy; n.nextDraw = nextDraw;
            n.turnStr = turnStr; n.turnBlock = turnBlock; n.turnEnergy = turnEnergy; n.turnDmg = turnDmg;
            n.turnDmg1 = turnDmg1; n.turnDraw = turnDraw; n.str = str; n.dex = dex; n.permStr = permStr; n.permDex = permDex;
            n.powers = powers; n.enrage = enrage; n.curiosity = curiosity; n.played = played; n.junk = junk;
            n.selfVuln = selfVuln; n.selfWeak = selfWeak; n.selfFrail = selfFrail; n.thornsBack = thornsBack;
            n.doubleAtk = doubleAtk; n.doubleSkill = doubleSkill; n.orbSlots = orbSlots; n.focus = focus;
            n.permFocus = permFocus; n.mantra = mantra; n.weak = weak; n.frail = frail; n.ended = ended;
            n.intangible = intangible; n.lightningAll = lightningAll; n.doomed = doomed; n.vault = vault;
            n.stance = stance; n.stance0 = stance0;
            n.mons = new M[mons.length];
            for (int k = 0; k < mons.length; k++) n.mons[k] = mons[k].copy();
            for (Object[] o : orbs) n.orbs.add(o.clone());
            return n;
        }

        boolean allDead() {
            for (M m : mons) if (!m.dead) return false;
            return true;
        }

        M mon(int gameIndex) {
            for (M m : mons) if (m.i == gameIndex) return m;
            return null;
        }

        String key() {
            StringBuilder b = new StringBuilder();
            b.append(energy).append(',').append(block).append(',').append(str).append(',').append(dex).append(',')
                    .append(played).append('|');
            for (M m : mons) b.append(m.hp).append(':').append(m.block).append(':').append(m.vuln).append(':')
                    .append(m.weak).append(':').append(m.down).append(':').append(m.artifact).append(':').append(m.poison).append(';');
            b.append('|').append(Math.round(drawValue * 100) / 100.0).append(',').append(permStr).append(',').append(powers)
                    .append(',').append(selfLoss).append(',').append(doubleAtk).append(',').append(doubleSkill).append(',')
                    .append(intangible).append(',').append(Math.round(heal * 10) / 10.0).append('|');
            for (Object[] o : orbs) b.append(o[0]).append(o[1]).append('/').append(o[2]).append(';');
            b.append(focus).append(',').append(orbSlots);
            // stance and turn-ending flags: two orders reaching the same numbers in a different stance are different
            b.append('|').append(stance).append(',').append(mantra).append(',').append(ended).append(',').append(vault)
                    .append(',').append(doomed);
            return b.toString();
        }
    }

    // ---------------------------------------------------------------- context (planner.Ctx)

    private final Sim s;
    private final Params P;
    private final int maxNodes;
    private final List<Sim.Card> hand;
    private final int strikes;
    private double inc0, wrathStuck, champStr;
    private boolean noSafeTarget;
    private int nodes;

    private Planner(Sim s, Params p, int maxNodes) {
        this.s = s;
        this.P = p;
        this.maxNodes = maxNodes;
        this.hand = s.hand;
        int k = 0;
        for (String d : s.deck) if (d.contains("Strike")) k++;
        this.strikes = k;
    }

    static int pwr(int v) { return v; }

    /** planner.build: the starting state. */
    private St build() {
        St st = new St();
        st.energy = s.energy;
        st.block = s.playerBlock;
        st.str = s.strength;
        st.dex = s.dexterity;
        st.weak = s.playerWeak;
        st.frail = s.playerFrail;
        st.intangible = s.intangible;
        st.doubleAtk = s.doubleAttack;
        st.doubleSkill = s.doubleSkill;
        st.stance = st.stance0 = s.stance == null || s.stance.isEmpty() ? "Neutral" : s.stance;
        st.mantra = s.mantra;
        st.orbSlots = s.orbSlots;
        for (Sim.Orb o : s.orbs)
            if (Arrays.asList(ORB).contains(o.kind)) st.orbs.add(new Object[]{o.kind, o.evoke, o.passive});
        st.focus = s.focus;
        st.lightningAll = s.lightningAll;
        boolean awakened = false;
        for (Sim.Mon m : s.mons) if (m.alive && "Awakened One".equals(m.name)) awakened = true;
        List<M> ms = new ArrayList<>();
        for (Sim.Mon m : s.mons) {
            if (!m.alive) continue;
            M x = new M();
            x.i = m.index;
            x.name = m.name;
            x.hp = x.hp0 = m.hp;
            x.maxHp = m.maxHp;
            x.block = x.block0 = m.block;
            x.vuln = m.vuln;
            x.weak = x.weak0 = m.weak;
            x.atk = m.atk;
            x.hits = m.hits;
            x.poison = m.poison;
            x.artifact = m.artifact;
            x.barricade = m.barricade;
            x.thorns = m.thorns;
            x.sharp = m.sharp;
            x.malleable = m.malleable;
            x.curl = m.curl;
            x.intangible = m.intangible;
            x.cap = m.invincible > 0 ? m.invincible : 1e6;
            Double pr = PRIORITY.get(m.name);
            x.prio = pr == null ? 1.0 : pr;
            if ("Cultist".equals(m.name) && awakened) x.prio = 1.6;
            if (("Orb".equals(m.name) || "Bronze Orb".equals(m.name)) && m.stasis) x.prio = 0.85;
            x.asleep = m.asleep;
            x.nob = m.nob;
            x.shifting = m.shifting;
            x.flight = m.flight;
            x.mark = m.mark;
            Double typical = Monsters.TYPICAL.get(m.name);
            double usual = (typical == null ? 6.0 : typical) + m.strength * Math.max(1, x.hits);
            x.thr = Math.max((double) (x.atk * x.hits), usual) + 1.5 * m.ritual * Math.max(1, x.hits);
            if ("Orb".equals(m.name) || "Bronze Orb".equals(m.name) || "Torch Head".equals(m.name) || "Dagger".equals(m.name))
                x.thr *= 0.5;
            ms.add(x);
        }
        st.mons = ms.toArray(new M[0]);
        return st;
    }

    // ---------------------------------------------------------------- one play

    private static void hit(St st, M m, double dmg) {
        if (m.dead) return;
        if (m.intangible) dmg = Math.min(dmg, 1);
        if (m.flight > 0) {   // Byrds: half damage while flying; every hit knocks a stack off
            dmg = Math.floor(dmg / 2);
            m.flight--;
            if (m.flight == 0) m.atk = 0;   // grounded: stunned
        }
        dmg = Math.min(dmg, m.cap);
        if (m.block >= dmg) {
            m.block -= dmg;
        } else {
            double through = dmg - m.block;
            m.block = 0;
            m.hp -= through;
            m.cap -= through;
            if (m.curl != 0 && through > 0) {
                m.block += m.curl;
                m.curl = 0;
            }
        }
        if (m.malleable != 0) {
            m.block += m.malleable;
            m.malleable++;
        }
        if (m.thorns != 0) {
            st.selfLoss += m.thorns;
            st.retal += m.thorns;
        }
        if (m.hp <= 0) {
            m.dead = true;
            m.hp = 0;
        }
    }

    private static void debuff(M m, int vuln, int weak) {
        for (int k = 0; k < 2; k++) {
            int n = k == 0 ? vuln : weak;
            if (n == 0 || m.dead) continue;
            if (m.artifact > 0) {
                m.artifact--;
                continue;
            }
            if (k == 0) { m.vuln += n; m.newVuln += n; }
            else { m.weak += n; m.newWeak += n; }
        }
    }

    private static M priorityTarget(St st) {
        M best = null;
        for (M m : st.mons) {
            if (m.dead) continue;
            if (best == null || m.prio > best.prio || (m.prio == best.prio && m.hp < best.hp)) best = m;
        }
        return best;
    }

    private static double sumHp(St st) {
        double t = 0;
        for (M m : st.mons) t += m.hp;
        return t;
    }

    static double dealtMult(String stance) {
        return "Wrath".equals(stance) ? 2.0 : "Divinity".equals(stance) ? 3.0 : 1.0;
    }

    static double takenMult(String stance) {
        return "Wrath".equals(stance) ? 2.0 : 1.0;
    }

    private boolean hasAttacker(St st) {
        for (M m : st.mons) if (m.atk > 0) return true;
        return false;
    }

    /** planner.play: apply one card; `remaining` = hand indices before it (read only). */
    void play(St st, int idx, int target, List<Integer> remaining, boolean echoCopy, double xEcho) {
        Sim.Card card = hand.get(idx);
        Effects e = card.fx;
        String ctype = card.type;
        double xEnergy = echoCopy ? xEcho : st.energy;
        double cost = echoCopy ? 0 : (card.cost == -1 ? st.energy : Math.max(card.cost, 0));
        st.energy -= cost;
        if (!echoCopy) st.played++;
        boolean echo = false;
        if (!echoCopy && "ATTACK".equals(ctype) && st.doubleAtk > 0) { st.doubleAtk--; echo = true; }
        else if (!echoCopy && "SKILL".equals(ctype) && st.doubleSkill > 0) { st.doubleSkill--; echo = true; }
        double hpBefore = sumHp(st);
        M tgt = target < 0 ? null : st.mon(target);
        if (s.beatOfDeath != 0) st.selfLoss += s.beatOfDeath;
        if (s.hex != 0 && !"ATTACK".equals(ctype)) st.junk -= 2 * s.hex;
        if ("SKILL".equals(ctype)) for (M m : st.mons) if (m.nob != 0 && !m.dead) st.enrage += m.nob;
        if ("STATUS".equals(ctype) || "CURSE".equals(ctype)) st.junk++;
        if (s.timeWarp >= 0 && s.timeWarp + st.played >= 12) st.ended = true;
        boolean vulnOk = !e.ifVuln || (tgt != null && tgt.vuln > 0);
        if (vulnOk) st.energy += e.energy;
        if (e.strength != 0 && (!e.conditionalStrength || (tgt != null && tgt.atk > 0) || (tgt == null && hasAttacker(st)))) {
            st.str += e.strength;
            st.permStr += e.strength;
        }
        if (e.tempStrength != 0) st.str += e.tempStrength;
        if (e.doubleStrength && st.str > 0) {
            st.permStr += st.str;
            st.str *= 2;
        }
        st.dex += e.dex;
        st.permDex += e.dex;
        List<Integer> others = new ArrayList<>();
        for (int j : remaining) if (j != idx) others.add(j);
        int exhausted = 0;
        if ("all".equals(e.exhaustHand)) exhausted = others.size();
        else if ("nonattack".equals(e.exhaustHand))
            for (int j : others) if (!"ATTACK".equals(hand.get(j).type)) exhausted++;
        double frail = st.frail ? 0.75 : 1.0;
        if (e.block != 0) st.block += (int) ((e.block + st.dex) * frail) + card.blockBonus;
        if (e.blockPerExhaust != 0) st.block += (int) (exhausted * (e.blockPerExhaust + st.dex) * frail);
        if (e.doubleBlock) st.block *= 2;
        if (e.bulletTime) for (int j : others) st.energy += Math.max(0, hand.get(j).cost);
        double tgtHp = tgt != null ? tgt.hp : 0;
        if (e.damage != 0 || e.blockScaling || e.damagePerExhaust != 0) {
            double base, hits;
            if (e.blockScaling) { base = st.block; hits = 1; }
            else if (e.damagePerExhaust != 0) { base = e.damagePerExhaust; hits = exhausted; }
            else {
                base = e.damage + e.perStrike * strikes;
                hits = (e.xCost || e.hits == 0) ? xEnergy : e.hits;
            }
            double per = base + st.str * e.strengthMult;
            if (st.weak) per *= 0.75;
            if ("ATTACK".equals(ctype)) per *= dealtMult(st.stance);
            List<M> live = new ArrayList<>();
            for (M m : st.mons) if (!m.dead) live.add(m);
            List<M> victims = new ArrayList<>();
            double share = 1.0;
            if (e.aoe) victims.addAll(live);
            else if (e.randomTarget && !live.isEmpty()) { victims.addAll(live); share = 1.0 / live.size(); }
            else if (tgt != null && !tgt.dead) victims.add(tgt);
            for (M m : victims) {
                if (m.sharp != 0 && "ATTACK".equals(ctype)) {
                    st.selfLoss += m.sharp;
                    st.retal += m.sharp;
                }
                double bonus = card.dmgBonus != null && m.i < card.dmgBonus.length ? card.dmgBonus[m.i] : 0;
                for (int h = 0; h < Math.max(0, (int) hits); h++) {
                    double d = (per + bonus) * (m.vuln > 0 ? 1.5 : 1.0);
                    hit(st, m, share < 1 ? (int) d * share : (int) d);
                }
            }
        }
        if (e.wallop && tgt != null) st.block += Math.max(0, tgtHp - tgt.hp);
        if (e.judgment != 0 && tgt != null && !tgt.dead && tgt.hp <= e.judgment && tgt.cap > 1e5) {
            tgt.hp = 0;
            tgt.dead = true;
        }
        if (e.mark != 0 && tgt != null && !tgt.dead) {   // Pressure Points
            if (tgt.artifact > 0) tgt.artifact--;
            else tgt.mark += e.mark;
            for (M m : st.mons)
                if (m.mark != 0 && !m.dead) {
                    m.hp -= m.intangible ? 1 : Math.min(m.mark, m.cap);
                    if (m.hp <= 0) { m.hp = 0; m.dead = true; }
                }
        }
        if (e.vault) st.vault = true;
        int weak = e.weak + (e.xWeak ? (int) xEnergy : 0);   // Malaise: X Weak
        if (e.vuln != 0 || weak != 0) {
            if (e.aoeDebuff || e.aoe) { for (M m : st.mons) if (!m.dead) debuff(m, e.vuln, weak); }
            else if (tgt != null) debuff(tgt, e.vuln, weak);
        }
        if (!e.stance.isEmpty() || e.mantra != 0 || e.wrathBlock != 0) stanceEffects(st, e, tgt);
        st.nextBlock += e.nextBlock;
        st.nextEnergy += e.nextEnergy;
        st.nextDraw += e.nextDraw;
        for (int k = 0; k < 2; k++) {
            int n = k == 0 ? e.enemyStrDown + (e.xStrDown ? (int) xEnergy : 0) : e.enemyStrDownTemp;
            boolean perm = k == 0;
            if (n == 0) continue;
            List<M> group = new ArrayList<>();
            if (e.aoeDebuff) { for (M m : st.mons) if (!m.dead) group.add(m); }
            else if (tgt != null) group.add(tgt);
            for (M m : group) {
                if (m.artifact > 0) { m.artifact--; continue; }
                m.down += n;
                if (perm) m.downPerm += n;
            }
        }
        if (e.poison != 0) {
            List<M> group = new ArrayList<>();
            if (e.aoeDebuff) { for (M m : st.mons) if (!m.dead) group.add(m); }
            else if (tgt != null && !tgt.dead) group.add(tgt);
            else { M pt = priorityTarget(st); if (pt != null) group.add(pt); }
            for (M m : group) {
                if (m.artifact > 0) m.artifact--;
                else m.poison += e.poison;
            }
        }
        List<Integer> discarded = new ArrayList<>();
        if ("all".equals(e.discardHand)) discarded.addAll(others);
        else if ("nonattack".equals(e.discardHand))
            for (int j : others) if (!"ATTACK".equals(hand.get(j).type)) discarded.add(j);
        int shivs = e.shivs + (e.shivPerDiscard ? discarded.size() : 0);
        for (int j : discarded) {   // discard triggers: Tactician energy, Reflex draw
            String n = Effects.baseName(hand.get(j).name);
            if (n.equals("Tactician")) st.energy += hand.get(j).upgrades > 0 ? 2 : 1;
            else if (n.equals("Reflex")) st.drawValue += hand.get(j).upgrades > 0 ? 3 : 2;
        }
        if (e.drawPerDiscard && !discarded.isEmpty()) st.drawValue += discarded.size() * (st.energy > 0 ? 1.0 : 0.4);
        for (int k = 0; k < shivs; k++) {   // Shivs: 0 cost, 4 (+Strength), counted now on the priority target
            M m = priorityTarget(st);
            if (m == null) break;
            double per = (4 + st.str) * (st.weak ? 0.75 : 1.0);
            hit(st, m, (int) (per * (m.vuln > 0 ? 1.5 : 1.0)));
        }
        if (st.orbSlots != 0 || e.orbSlots != 0 || !e.channel.isEmpty() || !e.channelPerEnemy.isEmpty() || !e.channelX.isEmpty())
            orbEffects(st, e, tgt, (int) xEnergy);
        if (e.poisonMult != 0 && tgt != null && !tgt.dead && tgt.poison != 0) tgt.poison *= e.poisonMult;   // Catalyst
        if (e.draw != 0 && vulnOk) st.drawValue += e.draw * (st.energy > 0 ? 1.0 : 0.4);
        st.cardLoss += e.hpLoss;
        st.selfVuln += e.selfVuln;
        st.selfWeak += e.selfWeak;
        st.selfFrail += e.selfFrail;
        st.thornsBack += e.thorns;
        if ("POWER".equals(ctype) || e.setup) {
            if ("POWER".equals(ctype)) {
                st.powers++;
                st.curiosity += s.curiosity;
            }
            st.turnStr += e.perTurnStrength;
            st.turnBlock += e.perTurnBlock;
            st.turnEnergy += e.perTurnEnergy;
            st.turnDmg += e.perTurnDamage;
            st.turnDmg1 += e.perTurnSingle;
            st.turnDraw += e.perTurnDraw;
        }
        if (e.intangible != 0) st.intangible = true;
        if (e.doubleAttack != 0) st.doubleAtk += e.doubleAttack;
        if (e.doubleSkill != 0) st.doubleSkill += e.doubleSkill;
        if (e.healUnblocked) st.heal += Math.max(0, hpBefore - sumHp(st));   // Reaper
        if (echo) play(st, idx, target, remaining, true, xEnergy);
    }

    // ---------------------------------------------------------------- Defect orbs

    private static Object[] newOrb(String kind, int focus) {
        int k = Arrays.asList(ORB).indexOf(kind);
        int passive = ORB_BASE[k][0], evoke = ORB_BASE[k][1];
        if (k <= 1) return new Object[]{kind, Math.max(0, evoke + focus), Math.max(0, passive + focus)};
        if (k == 2) return new Object[]{kind, evoke, Math.max(0, passive + focus)};
        return new Object[]{kind, evoke, passive};
    }

    private static void lightning(St st, int amount) {
        if (st.lightningAll) {
            for (M m : st.mons) if (!m.dead) hit(st, m, amount);
        } else {
            M t = priorityTarget(st);
            if (t != null) hit(st, t, amount);
        }
    }

    private static void evoke(St st, Object[] orb) {
        String kind = (String) orb[0];
        int amount = (Integer) orb[1];
        switch (kind) {
            case "Lightning": lightning(st, amount); break;
            case "Frost": st.block += amount; break;
            case "Dark": {
                M low = null;
                for (M m : st.mons) if (!m.dead && (low == null || m.hp < low.hp)) low = m;
                if (low != null) hit(st, low, amount);
                break;
            }
            case "Plasma": st.energy += amount; break;
            default: break;
        }
    }

    private static void channel(St st, String kind) {
        if (st.orbSlots <= 0 || !Arrays.asList(ORB).contains(kind)) return;
        if (st.orbs.size() >= st.orbSlots) evoke(st, st.orbs.remove(0));
        st.orbs.add(newOrb(kind, st.focus));
    }

    private void orbEffects(St st, Effects e, M tgt, int xEnergy) {
        if (e.focus != 0) {
            st.focus += e.focus;
            st.permFocus += e.focus;
            for (Object[] o : st.orbs) {
                if ("Lightning".equals(o[0]) || "Frost".equals(o[0])) {
                    o[1] = Math.max(0, (Integer) o[1] + e.focus);
                    o[2] = Math.max(0, (Integer) o[2] + e.focus);
                } else if ("Dark".equals(o[0])) o[2] = Math.max(0, (Integer) o[2] + e.focus);
            }
        }
        if (e.orbSlots != 0) {
            st.orbSlots = Math.max(0, st.orbSlots + e.orbSlots);
            while (st.orbs.size() > st.orbSlots) st.orbs.remove(st.orbs.size() - 1);
        }
        if (e.lightningAll) st.lightningAll = true;
        if (e.perOrb && tgt != null && st.orbs.size() > 1) {   // Barrage: the first hit was the card's own
            int per = (int) ((e.damage + st.str) * (st.weak ? 0.75 : 1.0) * (tgt.vuln > 0 ? 1.5 : 1.0));
            for (int k = 0; k < st.orbs.size() - 1; k++) if (!tgt.dead) hit(st, tgt, per);
        }
        int times = e.evoke == -1 ? xEnergy : e.evoke;
        if (times != 0 && !st.orbs.isEmpty()) {
            Object[] orb = st.orbs.remove(0);
            for (int k = 0; k < times; k++) evoke(st, orb);
            if (e.recursion) channel(st, (String) orb[0]);
        }
        int live = 0;
        for (M m : st.mons) if (!m.dead) live++;
        List<String> kinds = new ArrayList<>(e.channel);
        if (!e.channelPerEnemy.isEmpty()) for (int k = 0; k < live; k++) kinds.add(e.channelPerEnemy);
        if (!e.channelX.isEmpty()) for (int k = 0; k < xEnergy; k++) kinds.add(e.channelX);
        for (String k : kinds) channel(st, k);
    }

    private static void orbPassives(St st) {
        for (Object[] o : st.orbs) {
            if ("Lightning".equals(o[0])) lightning(st, (Integer) o[2]);
            else if ("Frost".equals(o[0])) st.block += (Integer) o[2];
            else if ("Dark".equals(o[0])) o[1] = (Integer) o[1] + (Integer) o[2];
        }
    }

    // ---------------------------------------------------------------- Watcher stances

    private static void setStance(St st, String next) {
        if (next.equals(st.stance)) return;
        if ("Calm".equals(st.stance)) st.energy += 2;
        if ("Divinity".equals(next)) st.energy += 3;
        st.stance = next;
    }

    private static void stanceEffects(St st, Effects e, M tgt) {
        if (e.wrathBlock != 0 && "Wrath".equals(st.stance)) st.block += e.wrathBlock;           // Halt
        if ("calm_draw".equals(e.stanceCond) && "Calm".equals(st.stance)) {
            st.drawValue += e.condN * (st.energy > 0 ? 1.0 : 0.4);                              // Inner Peace in Calm
        } else if ("wrath_vuln".equals(e.stanceCond) && "Wrath".equals(st.stance)) {
            for (M m : st.mons) if (!m.dead) debuff(m, e.condN, 0);                              // Indignation in Wrath
        } else if ("if_attacking".equals(e.stanceCond) && !(tgt != null && tgt.atk > 0)) {
            // Fear No Evil: only when the target attacks
        } else if (!e.stance.isEmpty()) setStance(st, e.stance);
        if (e.mantra != 0) {
            st.mantra += e.mantra;
            if (st.mantra >= 10) {
                st.mantra -= 10;
                setStance(st, "Divinity");
            }
        }
        if (e.dieNextTurn) st.doomed = true;
        if (e.endsTurn) st.ended = true;
    }

    // ---------------------------------------------------------------- scoring

    private static double[] incoming(St st) {
        double total = 0;
        int hits = 0;
        for (M m : st.mons) {
            if (m.dead || m.atk == 0) continue;
            if (m.poison >= m.hp && m.hp > 0) continue;   // its Poison kills it first
            double per = Math.max(0.0, m.atk - m.down * (m.weak != 0 ? 0.75 : 1.0));
            if (m.shifting) per = Math.max(0.0, per - (m.hp0 - m.hp));
            if (m.weak0 == 0 && m.newWeak != 0) per *= 0.75;
            total += (int) per * m.hits;
            hits += m.hits;
        }
        total *= takenMult(st.stance) / takenMult(st.stance0);
        return new double[]{total, hits};
    }

    private double evaluate(St st, double[] out) {
        if (!st.orbs.isEmpty()) {
            st = st.copy();
            orbPassives(st);
        }
        double v = 0, dealt = 0, enemyLeft = 0;
        int kills = 0;
        double[] ih = incoming(st);
        double inc = ih[0];
        int incHits = (int) ih[1];
        if (st.thornsBack != 0 && incHits != 0)
            for (M m : st.mons)
                if (!m.dead && m.atk != 0) {
                    double h = m.hp;
                    m.hp = Math.max(0, m.hp - st.thornsBack * m.hits);
                    dealt += h - m.hp;
                    v += P.damage * (h - m.hp) * m.prio;
                }
        for (M m : st.mons) {
            double removed = m.hp0 - m.hp;
            double mult = m.prio;
            if (m.asleep && !m.dead) {
                mult *= 0.15;
                if (removed > 0 && s.turn <= 2) v -= 3.0;
            }
            v += P.damage * removed * mult;
            if (removed > 0 && !m.asleep) v += P.block * P.threat * m.thr * Math.min(removed, m.hp0) / THREAT_RATE;
            dealt += removed;
            if (m.barricade && !m.dead) v += P.damage * 0.8 * Math.max(0, m.block0 - m.block);
            if (m.dead) {
                kills++;
                v += P.kill * m.prio;
                continue;
            }
            enemyLeft += m.hp;
            if (SPLITTERS.contains(m.name) && m.hp <= m.maxHp / 2.0 && m.maxHp / 2.0 < m.hp0 && s.stall == 0) {
                v -= P.damage * 0.5 * m.hp * mult;
                enemyLeft += m.hp;
            }
            v += P.vuln * 0.5 * Math.min(m.newVuln, 3);
            double atkTotal = m.atk * m.hits;
            v += P.weak * m.newWeak * Math.max(atkTotal, 6) / 3.0;
            if (m.downPerm != 0) v += P.block * m.downPerm * Math.max(1, m.hits) * 1.5;
            if (m.poison != 0) v += P.damage * Math.min(m.hp, m.poison * 2.5);
        }
        if (st.vault) {
            st.block = Math.min(inc, VAULT_TURN);
            v += P.damage * Math.max(0.0, VAULT_TURN - inc);
        }
        if (s.stall >= STALL_FORCE && dealt > 0) v += P.block * 0.5 * dealt;
        double taken = Math.max(0.0, inc - st.block);
        if (st.intangible) taken = Math.min(taken, incHits);
        if (st.heal != 0) v += P.hpCost * Math.min(st.heal, Math.max(0, s.maxHp - s.hp + taken));
        v -= P.block * taken;
        v += P.surplusBlock * Math.max(0.0, st.block - inc) * (s.barricade ? 6 : 1);
        double hpFrac = s.hp / (double) Math.max(1, s.maxHp);
        double retal = st.retal;
        if (noSafeTarget && taken + st.selfLoss + st.cardLoss < s.hp * 0.6) retal *= 0.25;
        v -= P.block * (st.selfLoss - st.retal + retal) / (1 + s.stall);
        boolean allDead = st.allDead();
        if (allDead) v += P.block * Math.max(inc0, 5.0) * 1.5;
        v -= P.hpCost * st.cardLoss * (hpFrac < 0.3 ? 2.0 : 1.0);
        for (M m : st.mons) {
            if (!"The Champ".equals(m.name)) continue;
            double half = m.maxHp / 2.0;
            if (m.hp0 <= half) v += P.damage * (m.hp0 - m.hp) * 0.5;
            else if (m.hp <= half && !m.dead && s.stall == 0) {
                double execute = 2 * (16 + champStr);
                if (s.hp - (taken + st.selfLoss + st.cardLoss) < 1.5 * execute) v -= P.block * 0.6 * execute;
            }
        }
        if (taken + st.selfLoss + st.cardLoss >= s.hp) v -= 500.0;
        double left0 = 0;
        for (M m : st.mons) if (m.hp0 > 0) left0 += m.hp0;
        double fight = 1.0 + Math.min(2.0, left0 / 40.0);
        v += P.strength * st.permStr * fight;
        if ("Calm".equals(st.stance)) v += P.energy * 2 * 0.6;
        if ("Wrath".equals(st.stance) && wrathStuck != 0 && !allDead) {
            double nxt = 0;
            for (M m : st.mons) if (!m.dead) nxt += m.nxt;
            v -= P.block * 1.0 * wrathStuck * nxt;
        }
        if (st.doomed && !allDead) v -= 500.0;
        v += 0.8 * (P.block * st.nextBlock + P.energy * st.nextEnergy) + 0.5 * P.draw * st.nextDraw;
        if (!st.orbs.isEmpty() || st.permFocus != 0) {
            int live = 0;
            for (M m : st.mons) if (!m.dead) live++;
            live = Math.max(1, live);
            double light = 0, frost = 0, dark = 0, plasma = 0;
            for (Object[] o : st.orbs) {
                if ("Lightning".equals(o[0])) light += (Integer) o[2];
                else if ("Frost".equals(o[0])) frost += (Integer) o[2];
                else if ("Dark".equals(o[0])) dark++;
                else if ("Plasma".equals(o[0])) plasma++;
            }
            if (st.lightningAll) light *= live;
            v += (P.damage * light + P.block * frost + P.damage * 3.0 * dark + P.energy * 0.5 * plasma) * fight * ORB_FUTURE;
            v += P.block * st.permFocus * Math.max(1, st.orbs.size()) * fight * 0.5;
        }
        v += P.block * st.permDex * fight * DEX_BLOCK_CARDS;
        v += P.strength * st.turnStr * fight * 1.5;
        double horizon = Math.min(6.0, Math.max(1.0, left0 / 25.0)) * POWER_TURN_SHARE;
        double left = (s.hp - taken - st.selfLoss - st.cardLoss) / Math.max(1, s.maxHp);
        horizon *= Math.min(1.0, Math.max(0.2, left / 0.4));
        int liveNow = 0;
        for (M m : st.mons) if (!m.dead) liveNow++;
        v += P.block * st.turnBlock * horizon;
        v += P.damage * st.turnDmg * Math.max(1, liveNow) * horizon;
        v += P.damage * st.turnDmg1 * horizon;
        v += P.energy * st.turnEnergy * horizon * 0.8;
        v += P.draw * st.turnDraw * horizon;
        if (st.powers != 0) {
            boolean asleep = false;
            for (M m : st.mons) if (m.asleep && !m.dead) asleep = true;
            v += P.power * st.powers * (s.elite || asleep ? 1.5 : 1.0) * (s.turn <= 3 ? 1.0 : 0.5) * fight / 2;
        }
        if (st.curiosity != 0) v -= P.block * st.curiosity * 4 * fight * 1.5;
        if (st.enrage != 0) {
            int nobHits = 0;
            for (M m : st.mons) if (m.nob != 0 && !m.dead) nobHits = Math.max(nobHits, m.hits);
            if (nobHits == 0) nobHits = 1;
            v -= P.block * st.enrage * nobHits * 2.5;
        }
        if (s.timeWarp >= 0) {
            int count = s.timeWarp + st.played;
            if (count >= 12) v -= P.strength * 2 * fight;
            else if (count >= 10) v -= 4.0;
        }
        v += P.draw * st.drawValue;
        v += 0.6 * st.junk;
        if (st.selfVuln != 0) v -= P.block * 0.5 * Math.max(inc, 6.0) * Math.min(st.selfVuln, 2) * 0.5;
        v -= P.damage * 3 * st.selfWeak + P.block * 2 * st.selfFrail;
        if (out != null) {
            out[0] = dealt;
            out[1] = st.block;
            out[2] = taken + st.selfLoss;
            out[3] = kills;
        }
        return v;
    }

    static double wrathStuck(List<String> deck) {
        int n = Math.max(1, deck.size());
        int k = 0;
        for (String d : deck) if (WRATH_EXITS.contains(Effects.baseName(d))) k++;
        double stuck = 1.0;
        for (int i = 0; i < Math.min(5, n); i++) stuck *= Math.max(0.0, (n - k - i) / (double) (n - i));
        return stuck;
    }

    // ---------------------------------------------------------------- search

    private final Set<String> seen = new HashSet<>();
    private Plan best;
    private double baseValue;

    private boolean canPlay(int i, St st) {
        Sim.Card c = hand.get(i);
        double cost = c.cost == -1 ? st.energy : Math.max(c.cost, 0);
        if (c.cost == -1 && st.energy <= 0 && c.fx.damage != 0) return false;
        if (cost > st.energy) return false;
        if (c.playable) return true;
        return c.cost > s.energy && !"STATUS".equals(c.type) && !"CURSE".equals(c.type);
    }

    private List<Integer> kept(int idx, List<Integer> remaining) {
        Effects e = hand.get(idx).fx;
        List<Integer> out = new ArrayList<>();
        if ("all".equals(e.exhaustHand) || "all".equals(e.discardHand)) return out;
        for (int j : remaining) {
            if (j == idx) continue;
            if (("nonattack".equals(e.exhaustHand) || "nonattack".equals(e.discardHand))
                    && !"ATTACK".equals(hand.get(j).type)) continue;
            out.add(j);
        }
        return out;
    }

    private void dfs(St st, List<Integer> remaining, List<Step> path) {
        nodes++;
        if (!path.isEmpty()) {
            double[] sum = new double[4];
            double val = evaluate(st.copy(), sum);
            if (val > best.value) {
                Plan p = new Plan();
                p.steps.addAll(path);
                p.value = val;
                p.base = baseValue;
                fill(p, sum);
                best = p;
            }
        }
        if (nodes >= maxNodes || st.ended || st.allDead()) return;
        Set<String> tried = new HashSet<>();
        for (int i : remaining) {
            if (!canPlay(i, st)) continue;
            Sim.Card c = hand.get(i);
            String ckey = c.name + "|" + c.upgrades + "|" + c.cost;
            List<Integer> targets = new ArrayList<>();
            if (c.targeted) {
                for (M m : st.mons)
                    if (!m.dead && (c.canTarget == null || (m.i < c.canTarget.length && c.canTarget[m.i]))) targets.add(m.i);
            } else targets.add(-1);
            for (int t : targets) {
                if (!tried.add(ckey + "|" + t)) continue;
                St child = st.copy();
                play(child, i, t, remaining, false, 0);
                List<Integer> rest = kept(i, remaining);
                List<String> names = new ArrayList<>();
                for (int j : rest) names.add(hand.get(j).name + "#" + hand.get(j).upgrades);
                java.util.Collections.sort(names);
                String skey = names + "@" + child.key();
                if (!seen.add(skey)) continue;
                List<Step> np = new ArrayList<>(path);
                np.add(new Step(c.index, t));
                dfs(child, rest, np);
                if (nodes >= maxNodes) return;
            }
        }
    }

    private static void fill(Plan p, double[] sum) {
        p.dealt = (int) Math.round(sum[0]);
        p.block = (int) Math.round(sum[1]);
        p.taken = (int) Math.round(sum[2]);
        p.kills = (int) sum[3];
    }

    private int handPos(int cardIndex) {
        for (int k = 0; k < hand.size(); k++) if (hand.get(k).index == cardIndex) return k;
        return -1;
    }

    private Double replay(St start, List<Step> steps) {
        St st = start.copy();
        List<Integer> remaining = new ArrayList<>();
        for (int k = 0; k < hand.size(); k++) remaining.add(k);
        for (Step step : steps) {
            int i = handPos(step.card);
            if (i < 0 || !remaining.contains(i)) return null;
            Sim.Card c = hand.get(i);
            double cost = c.cost == -1 ? st.energy : Math.max(c.cost, 0);
            if (cost > st.energy) return null;
            if (step.target >= 0) {
                M m = st.mon(step.target);
                if (m == null || m.dead) return null;
            }
            play(st, i, step.target, remaining, false, 0);
            remaining = kept(i, remaining);
        }
        return evaluate(st, null);
    }

    /** Drawing first shows more options; do it when it costs (almost) nothing. */
    private List<Step> drawFirst(Plan plan, St start) {
        List<Step> steps = plan.steps;
        if (steps.size() < 2) return steps;
        for (int k = 0; k < steps.size(); k++) {
            Effects e = hand.get(handPos(steps.get(k).card)).fx;
            if (k == 0 || e.draw == 0 || e.ifVuln) continue;
            List<Step> re = new ArrayList<>();
            re.add(steps.get(k));
            for (int j = 0; j < steps.size(); j++) if (j != k) re.add(steps.get(j));
            Double val = replay(start, re);
            if (val != null && val >= plan.value - Math.max(0.5, 0.05 * Math.abs(plan.value - plan.base))) return re;
            break;
        }
        return steps;
    }

    private St prepare() {
        St st = build();
        inc0 = incoming(st.copy())[0];
        boolean wrathCard = "Wrath".equals(st.stance);
        for (Sim.Card c : hand) if ("Wrath".equals(c.fx.stance)) wrathCard = true;
        if (wrathCard) {
            for (M x : st.mons) {
                Sim.Mon m = s.mon(x.i);
                double cur = x.atk > 0 ? x.atk * x.hits / takenMult(st.stance0) : 0.0;
                x.nxt = Monsters.nextAttack(m.name, m.intent, m.strength, s.turn, s.hp, cur);
            }
            List<String> deck = s.deck.isEmpty() ? new ArrayList<String>() : s.deck;
            if (deck.isEmpty()) for (Sim.Card c : hand) deck.add(c.name);
            wrathStuck = wrathStuck(deck);
        }
        champStr = 0;
        for (Sim.Mon m : s.mons) if (m.alive && "The Champ".equals(m.name)) champStr = m.strength;
        boolean any = false, all = true;
        for (M m : st.mons) {
            if (m.dead) continue;
            any = true;
            if (m.thorns == 0 && m.sharp == 0) all = false;
        }
        noSafeTarget = any && all;
        return st;
    }

    private Plan run() {
        St st = prepare();
        double[] sum = new double[4];
        baseValue = evaluate(st.copy(), sum);
        best = new Plan();
        best.value = best.base = baseValue;
        fill(best, sum);
        List<Integer> all = new ArrayList<>();
        for (int k = 0; k < hand.size(); k++) all.add(k);
        dfs(st, all, new ArrayList<Step>());
        best.nodes = nodes;
        List<Step> steps = drawFirst(best, st);
        if (steps != best.steps) {
            best.steps.clear();
            best.steps.addAll(steps);
        }
        return best;
    }

    public static Plan plan(Sim s, Params p) {
        return plan(s, p, MAX_NODES);
    }

    public static Plan plan(Sim s, Params p, int maxNodes) {
        return new Planner(s, p, maxNodes).run();
    }

    /**
     * The plan to play in the game: the bot's plan, minus trailing cards that add almost nothing (a Defend with
     * nothing coming, the last of several surplus blocks): they cost no HP but read as mistakes and slow Auto down.
     */
    public static Plan planForPlay(Sim s, Params p, int maxNodes) {
        Planner pl = new Planner(s, p, maxNodes);
        Plan best = pl.run();
        St start = pl.prepare();
        // drop any card whose part of the plan is worth less than ending the turn would cost (last ones first)
        boolean changed = true;
        while (changed && !best.steps.isEmpty()) {
            changed = false;
            for (int k = best.steps.size() - 1; k >= 0; k--) {
                List<Step> shorter = new ArrayList<>(best.steps);
                shorter.remove(k);
                Double v = pl.replay(start, shorter);
                if (v == null || best.value - v >= p.endThreshold) continue;
                best.steps.clear();
                best.steps.addAll(shorter);
                best.value = v;
                changed = true;
                break;
            }
        }
        double[] sum = new double[4];
        pl.replaySummary(start, best.steps, sum);
        fill(best, sum);
        return best;
    }

    private void replaySummary(St start, List<Step> steps, double[] sum) {
        St st = start.copy();
        List<Integer> remaining = new ArrayList<>();
        for (int k = 0; k < hand.size(); k++) remaining.add(k);
        for (Step step : steps) {
            int i = handPos(step.card);
            if (i < 0) continue;
            play(st, i, step.target, remaining, false, 0);
            remaining = kept(i, remaining);
        }
        evaluate(st, sum);
    }

    /** The plan's value if this hand were played from this state (no search): for choice screens. */
    public static double value(Sim s, Params p, int maxNodes) {
        return plan(s, p, maxNodes).value;
    }

    // ---------------------------------------------------------------- reasons for the overlay

    public static List<String> explain(Sim s, Plan plan, Params p) {
        Planner pl = new Planner(s, p, 0);
        St st = pl.prepare();
        List<String> out = new ArrayList<>();
        List<Integer> remaining = new ArrayList<>();
        for (int k = 0; k < s.hand.size(); k++) remaining.add(k);
        for (int k = 0; k < plan.steps.size(); k++) {
            Step step = plan.steps.get(k);
            int hk = pl.handPos(step.card);
            if (hk < 0) { out.add(""); continue; }
            St before = st.copy();
            double incBefore = incoming(before)[0];
            pl.play(st, hk, step.target, remaining, false, 0);
            remaining = pl.kept(hk, remaining);
            boolean laterAttack = false;
            for (int j = k + 1; j < plan.steps.size(); j++) {
                Sim.Card n = card(s, plan.steps.get(j).card);
                if (n != null && "ATTACK".equals(n.type)) laterAttack = true;
            }
            out.add(pl.reason(before, st, s.hand.get(hk), step.target, incBefore, laterAttack));
        }
        return out;
    }

    public static String endReason(Sim s) {
        boolean playable = false, affordable = false;
        for (Sim.Card c : s.hand) {
            if (!c.playable) continue;
            playable = true;
            if (c.cost <= s.energy) affordable = true;
        }
        if (!playable) return "nothing in hand can be played";
        if (!affordable || s.energy == 0) return "out of energy";
        return "what's left would cost more than it gains";
    }

    private static String orbNote(String kind) {
        switch (kind) {
            case "Lightning": return " (hits an enemy every turn)";
            case "Frost": return " (blocks every turn)";
            case "Dark": return " (grows, then bursts)";
            case "Plasma": return " (energy every turn)";
            default: return "";
        }
    }

    private String reason(St before, St after, Sim.Card c, int t, double incBefore, boolean laterAttack) {
        List<String> parts = new ArrayList<>();
        Effects fx = c.fx;
        int dmg = 0, vul = 0, weak = 0, down = 0, pois = 0;
        List<String> killed = new ArrayList<>(), doomed = new ArrayList<>();
        for (int k = 0; k < before.mons.length; k++) {
            M b = before.mons[k], a = after.mons[k];
            dmg += (int) Math.round(b.hp - a.hp);
            if (!b.dead && a.dead) {
                int hit = b.atk * Math.max(1, b.hits);
                killed.add(b.name + (hit > 0 ? " (stops its " + hit + ")" : ""));
            }
            vul = Math.max(vul, a.newVuln - b.newVuln);
            weak = Math.max(weak, a.newWeak - b.newWeak);
            down = Math.max(down, a.down - b.down);
            pois += Math.max(0, a.poison - b.poison);
            if (!a.dead && a.poison >= a.hp && b.poison < b.hp) doomed.add(a.name);
        }
        M tm = t >= 0 ? after.mon(t) : null;
        if ("STATUS".equals(c.type) || "CURSE".equals(c.type)) parts.add("clears a " + c.type.toLowerCase() + " card out of your hand");
        if (!killed.isEmpty()) parts.add("kills " + String.join(", ", killed));
        else if (dmg > 0) parts.add("deals " + dmg + (fx.aoe ? " across all enemies" : tm != null ? " to " + tm.name : ""));
        if (vul > 0) parts.add(laterAttack ? "Vulnerable first: the attacks after it hit 50% harder" : "applies " + vul + " Vulnerable");
        if (weak > 0) parts.add("Weak: its attacks deal 25% less");
        if (down > 0) parts.add("enemy loses " + down + " Strength");
        double blk = after.block - before.block;
        if (blk > 0) {
            double need = Math.max(0, incBefore - before.block);
            parts.add(need > 0 ? "blocks " + (int) Math.min(blk, need) + " of the " + (int) incBefore + " coming" : "extra block");
        }
        int str = fx.strength + fx.tempStrength;
        if (str > 0) parts.add(laterAttack ? "+" + str + " Strength before the attacks" : "+" + str + " Strength");
        if (fx.dex > 0) parts.add("+" + fx.dex + " Dexterity for the fight");
        if (fx.draw > 0) parts.add("draws " + fx.draw);
        if (fx.energy > 0) parts.add("+" + fx.energy + " energy");
        if (fx.perTurnDamage + fx.perTurnSingle + fx.perTurnBlock + fx.perTurnEnergy + fx.perTurnDraw + fx.perTurnStrength != 0
                && ("POWER".equals(c.type) || fx.setup)) parts.add("pays off every turn for the rest of the fight");
        else if (fx.power) parts.add("power: lasts the fight");
        double thorns = after.retal - before.retal;
        if (thorns > 0) parts.add("takes " + (int) thorns + " from Thorns");
        if (fx.hpLoss > 0) parts.add("costs " + fx.hpLoss + " HP");
        if (fx.poisonMult > 0 && pois > 0) parts.add((fx.poisonMult == 2 ? "doubles" : "triples") + " its Poison (+" + pois + ")");
        else if (pois > 0) parts.add("adds " + pois + " Poison (keeps ticking each turn)");
        if (!doomed.isEmpty()) parts.add("Poison kills " + String.join(", ", doomed) + " before it attacks");
        if (fx.shivs > 0) parts.add("makes " + fx.shivs + " Shiv" + (fx.shivs > 1 ? "s" : "") + " to play for free");
        if (fx.intangible > 0) parts.add("Intangible: every hit deals 1");
        if (fx.doubleAttack > 0) parts.add("the next Attack is played twice");
        if (fx.doubleSkill > 0) parts.add("the next Skill is played twice");
        if (after.heal > before.heal) parts.add("heals " + (int) (after.heal - before.heal));
        if (fx.mark > 0) parts.add("Mark: every Marked enemy loses its Mark in HP");
        if (fx.vault) parts.add("an extra turn before the enemies act");
        if (!fx.channel.isEmpty()) parts.add("channels " + String.join(" + ", fx.channel) + orbNote(fx.channel.get(0)));
        else if (!fx.channelX.isEmpty()) parts.add("channels X " + fx.channelX + orbNote(fx.channelX));
        else if (!fx.channelPerEnemy.isEmpty()) parts.add("channels a " + fx.channelPerEnemy + " per enemy" + orbNote(fx.channelPerEnemy));
        if (fx.evoke != 0) parts.add("evokes the next orb" + (fx.evoke == 2 ? " twice" : fx.evoke < 0 ? " X times" : ""));
        if (fx.focus > 0) parts.add("+" + fx.focus + " Focus: every orb hits/blocks harder");
        if (fx.orbSlots > 0) parts.add("+" + fx.orbSlots + " orb slots");
        if (!after.stance.equals(before.stance)) {
            String to = after.stance;
            String what = "Wrath".equals(to) ? "attacks deal double (and so do theirs)"
                    : "Calm".equals(to) ? "leaving it later gives 2 energy"
                    : "Divinity".equals(to) ? "attacks deal triple, +3 energy" : "back to neutral";
            parts.add(("Neutral".equals(to) ? "leaves " + before.stance : "enters " + to) + ": " + what
                    + ("Calm".equals(before.stance) ? " (+2 energy from leaving Calm)" : ""));
        }
        if (after.mantra > before.mantra) parts.add("+" + (after.mantra - before.mantra) + " Mantra (10 = Divinity)");
        return parts.isEmpty() ? "sets up the rest of the turn" : String.join(", ", parts);
    }
}
