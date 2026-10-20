package autospire.brain;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Whole-turn combat search (a Java port of the core of Spire AI's planner.py): tries orders of the
 * cards in hand within the energy available, simulates damage, block, debuffs, Thorns and kills, and
 * scores the end-of-turn position. Only the first step is used; the game is re-read after every card.
 */
public final class Planner {

    /** Weights; defaults are the values Spire AI's bot learned over ~240 runs. */
    public static final class Params {
        public double damage = 0.67, kill = 5.7, block = 1.76, surplusBlock = 0.09, vuln = 2.8, weak = 0.81;
        public double draw = 3.2, strength = 4.3, power = 4.9, hpCost = 3.4, endThreshold = 0.43;
    }

    public static final class Step {
        public final int card, target;   // hand index, monster index (-1 = none)
        Step(int card, int target) { this.card = card; this.target = target; }
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
            return b + "  (deal " + dealt + ", block " + block + ", take " + taken + (kills > 0 ? ", kill " + kills : "") + ")";
        }
    }

    static Sim.Card card(Sim s, int index) {
        for (Sim.Card c : s.hand) if (c.index == index) return c;
        return null;
    }

    /** Search state for one simulated sequence of plays. */
    private static final class St {
        int energy, block, strGain, permStr, powers, draw;
        int[] hp, mblock, vulnAdd, weakAdd, strDown;
        boolean[] dead, used;
        double retal, selfLoss;

        St copy() {
            St n = new St();
            n.energy = energy; n.block = block; n.strGain = strGain; n.permStr = permStr; n.powers = powers;
            n.draw = draw; n.retal = retal; n.selfLoss = selfLoss;
            n.hp = hp.clone(); n.mblock = mblock.clone(); n.vulnAdd = vulnAdd.clone(); n.weakAdd = weakAdd.clone();
            n.strDown = strDown.clone(); n.dead = dead.clone(); n.used = used.clone();
            return n;
        }
    }

    private final Sim s;
    private final Params p;
    private final int maxNodes;
    private int nodes;
    private Plan best;
    private double inc0;
    private boolean noSafeTarget;

    private Planner(Sim s, Params p, int maxNodes) {
        this.s = s;
        this.p = p;
        this.maxNodes = maxNodes;
    }

    public static Plan plan(Sim s, Params p) {
        return new Planner(s, p, 4000).run();
    }

    private Plan run() {
        St st = new St();
        int n = s.mons.size();
        st.energy = s.energy;
        st.block = s.playerBlock;
        st.hp = new int[n]; st.mblock = new int[n]; st.vulnAdd = new int[n]; st.weakAdd = new int[n];
        st.strDown = new int[n]; st.dead = new boolean[n]; st.used = new boolean[s.hand.size()];
        boolean anyAlive = false, allRetaliate = true;
        for (int i = 0; i < n; i++) {
            Sim.Mon m = s.mons.get(i);
            st.hp[i] = m.hp;
            st.mblock[i] = m.block;
            st.dead[i] = !m.alive || m.hp <= 0;
            if (!st.dead[i]) {
                anyAlive = true;
                if (m.thorns <= 0) allRetaliate = false;
            }
        }
        noSafeTarget = anyAlive && allRetaliate;
        inc0 = incoming(st);
        best = new Plan();
        best.value = best.base = evaluate(st, best, true);
        dfs(st, new ArrayList<>());
        best.nodes = nodes;
        return best;
    }

    private void dfs(St st, List<Step> path) {
        nodes++;
        if (!path.isEmpty()) {
            Plan cand = new Plan();
            double v = evaluate(st.copy(), cand, false);
            if (v > best.value) {
                cand.value = v;
                cand.base = best.base;
                cand.steps.addAll(path);
                cand.nodes = nodes;
                best = cand;
            }
        }
        if (nodes >= maxNodes || allDead(st)) return;
        Set<String> tried = new HashSet<>();
        for (int k = 0; k < s.hand.size(); k++) {
            if (st.used[k]) continue;
            Sim.Card c = s.hand.get(k);
            if (!c.playable || "STATUS".equals(c.type) || "CURSE".equals(c.type)) continue;
            int cost = c.cost < 0 ? st.energy : c.cost;
            if (cost > st.energy || (c.cost < 0 && st.energy == 0 && c.damage.length > 0)) continue;
            List<Integer> targets = new ArrayList<>();
            if (c.targeted) {
                for (int i = 0; i < s.mons.size(); i++)
                    if (!st.dead[i] && (c.canTarget.length <= i || c.canTarget[i])) targets.add(i);
                if (targets.isEmpty()) continue;
            } else {
                targets.add(-1);
            }
            for (int t : targets) {
                String key = c.name + "|" + c.cost + "|" + t;
                if (!tried.add(key)) continue;
                St child = st.copy();
                child.used[k] = true;
                play(child, c, t, cost);
                path.add(new Step(c.index, t < 0 ? -1 : s.mons.get(t).index));
                dfs(child, path);
                path.remove(path.size() - 1);
                if (nodes >= maxNodes) return;
            }
        }
    }

    private boolean allDead(St st) {
        for (boolean d : st.dead) if (!d) return false;
        return true;
    }

    private void play(St st, Sim.Card c, int target, int cost) {
        Effects fx = c.fx;
        st.energy -= cost;
        st.energy += fx.energy;
        int hits = c.cost < 0 ? Math.max(0, cost) * Math.max(1, c.hits) : Math.max(1, c.hits);
        // debuffs land before the damage of the same card in most cards (Bash applies after; close enough)
        if ("ATTACK".equals(c.type) && c.damage.length > 0) {
            List<Integer> order = new ArrayList<>();
            if (c.aoe) {
                for (int h = 0; h < hits; h++)
                    for (int i = 0; i < s.mons.size(); i++) if (!st.dead[i]) order.add(i);
            } else if (c.randomTarget || target < 0) {
                int i = 0;
                for (int h = 0; h < hits; h++) {
                    int guard = 0;
                    while (st.dead[i % s.mons.size()] && guard++ < s.mons.size()) i++;
                    order.add(i % s.mons.size());
                    i++;
                }
            } else {
                for (int h = 0; h < hits; h++) order.add(target);
            }
            for (int i : order) {
                if (st.dead[i]) continue;
                Sim.Mon m = s.mons.get(i);
                int per = (c.damage.length > i ? c.damage[i] : c.damage[0]) + st.strGain;
                if (m.vuln == 0 && st.vulnAdd[i] > 0) per = (int) (per * 1.5);
                hit(st, i, per);
                if (m.thorns > 0) st.retal += m.thorns;
            }
        }
        st.block += c.block;
        for (int i = 0; i < s.mons.size(); i++) {
            boolean affected = (fx.aoe || c.aoe) ? !st.dead[i] : i == target;
            if (!affected) continue;
            st.vulnAdd[i] += fx.vuln;
            st.weakAdd[i] += fx.weak;
            st.strDown[i] += fx.enemyStrDown;
        }
        st.strGain += fx.strength + fx.tempStrength;
        st.permStr += fx.strength;
        st.draw += fx.draw;
        st.selfLoss += fx.selfDamage;
        if (fx.isPower) st.powers++;
    }

    private void hit(St st, int i, int dmg) {
        int through = Math.max(0, dmg - st.mblock[i]);
        st.mblock[i] = Math.max(0, st.mblock[i] - dmg);
        st.hp[i] -= through;
        if (st.hp[i] <= 0) {
            st.hp[i] = 0;
            st.dead[i] = true;
        }
    }

    private double incoming(St st) {
        double inc = 0;
        for (int i = 0; i < s.mons.size(); i++) {
            if (st.dead[i]) continue;
            Sim.Mon m = s.mons.get(i);
            if (m.intentDmg <= 0) continue;
            double per = Math.max(0, m.intentDmg - st.strDown[i]);
            if (m.weak == 0 && st.weakAdd[i] > 0) per = Math.floor(per * 0.75);
            inc += per * Math.max(1, m.intentHits);
        }
        return inc;
    }

    private double evaluate(St st, Plan out, boolean isBase) {
        double v = 0;
        int dealt = 0, kills = 0, hpLeft = 0;
        for (int i = 0; i < s.mons.size(); i++) {
            Sim.Mon m = s.mons.get(i);
            if (!m.alive || m.hp <= 0) continue;
            int removed = m.hp - st.hp[i];
            dealt += removed;
            v += p.damage * removed * m.prio;
            if ("The Champ".equals(m.name) && m.maxHp > 0) {
                double half = m.maxHp / 2.0;
                if (m.hp <= half) v += p.damage * removed * 0.5;   // phase 2: race it
            }
            if (st.dead[i]) {
                kills++;
                v += p.kill * m.prio;
                continue;
            }
            hpLeft += st.hp[i];
            v += p.vuln * 0.5 * Math.min(st.vulnAdd[i], 3);
            double atk = m.intentDmg * Math.max(1, m.intentHits);
            v += p.weak * st.weakAdd[i] * Math.max(atk, 6) / 3.0;
            v += p.block * st.strDown[i] * Math.max(1, m.intentHits) * 1.5;
        }
        double inc = incoming(st);
        double taken = Math.max(0, inc - st.block);
        v -= p.block * taken;
        v += p.surplusBlock * Math.max(0, st.block - inc);
        double retal = st.retal;
        if (noSafeTarget && taken + st.retal + st.selfLoss < s.hp * 0.6) retal *= 0.25;
        v -= p.block * retal / (1 + s.stall);
        v -= p.hpCost * st.selfLoss * (s.hp < 0.3 * s.maxHp ? 2 : 1);
        if (allDead(st)) v += p.block * Math.max(inc0, 5) * 1.5;   // fight over: no more enemy turns
        for (int i = 0; i < s.mons.size(); i++) {                   // The Champ: don't chip it into phase 2 at low HP
            Sim.Mon m = s.mons.get(i);
            if ("The Champ".equals(m.name) && m.alive && m.hp > m.maxHp / 2.0 && !st.dead[i]
                    && st.hp[i] <= m.maxHp / 2.0 && s.stall == 0) {
                double execute = 2 * (16 + m.strength);
                if (s.hp - (taken + st.retal + st.selfLoss) < 1.5 * execute) v -= p.block * 0.6 * execute;
            }
        }
        if (taken + st.retal + st.selfLoss >= s.hp) v -= 500;
        double fight = 1 + Math.min(2, hpLeft / 40.0);
        v += p.strength * st.permStr * fight;
        if (st.powers > 0) v += p.power * st.powers * (s.elite ? 1.5 : 1) * (s.turn <= 3 ? 1 : 0.5) * fight / 2;
        v += p.draw * st.draw;
        out.dealt = dealt;
        out.block = st.block;
        out.taken = (int) Math.round(taken + st.retal + st.selfLoss);
        out.kills = kills;
        return v;
    }
}
