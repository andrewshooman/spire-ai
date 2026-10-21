package spireseer.brain;

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
    static final double DEX_BLOCK_CARDS = 1.5;   // block cards played per turn, for the lasting value of Dexterity
    static final double ORB_FUTURE = 0.7;        // orbs keep paying every turn: valued for the rest of the fight
    static final String[] ORB = {"Lightning", "Frost", "Dark", "Plasma"};
    static final int[][] ORB_BASE = {{3, 8}, {2, 5}, {6, 6}, {1, 2}};   // {passive, evoke} at 0 Focus

    /** Weights; defaults are the values Spire AI's bot learned over ~240 runs. */
    public static final class Params {
        public double damage = 0.67, kill = 5.7, block = 1.76, surplusBlock = 0.09, vuln = 2.8, weak = 0.81;
        public double draw = 3.2, strength = 4.3, power = 4.9, hpCost = 3.4, endThreshold = 0.43, energy = 5.5;

        /** From the bot's model.json "params" (keys as in Spire AI's model.COMBAT_DEFAULTS). */
        public static Params from(java.util.Map<String, Double> m) {
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
            return p;
        }
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
            return b + "  (" + totals() + ")";
        }

        /** "deal 20, block 5, take 0, kill 1" for the whole turn. */
        public String totals() {
            return "deal " + dealt + ", block " + block + ", take " + taken + (kills > 0 ? ", kill " + kills : "");
        }
    }

    static Sim.Card card(Sim s, int index) {
        for (Sim.Card c : s.hand) if (c.index == index) return c;
        return null;
    }

    /** Search state for one simulated sequence of plays. */
    private static final class St {
        int energy, block, strGain, permStr, dexGain, powers, draw, doubleAtk, doubleSkill;
        int nextBlock, nextEnergy, nextDraw, turnBlock, turnDamage, orbSlots, focus, permFocus;
        boolean lightningAll;
        List<int[]> orbs = new ArrayList<>();   // {kind (index in ORB), evoke, passive}
        int[] hp, mblock, vulnAdd, weakAdd, strDown, poison;
        boolean[] dead, used;
        boolean intangible;
        double retal, selfLoss, heal;

        St copy() {
            St n = new St();
            n.energy = energy; n.block = block; n.strGain = strGain; n.permStr = permStr; n.dexGain = dexGain; n.powers = powers;
            n.draw = draw; n.retal = retal; n.selfLoss = selfLoss;
            n.doubleAtk = doubleAtk; n.doubleSkill = doubleSkill; n.intangible = intangible; n.heal = heal;
            n.poison = poison.clone();
            n.hp = hp.clone(); n.mblock = mblock.clone(); n.vulnAdd = vulnAdd.clone(); n.weakAdd = weakAdd.clone();
            n.strDown = strDown.clone(); n.dead = dead.clone(); n.used = used.clone();
            n.nextBlock = nextBlock; n.nextEnergy = nextEnergy; n.nextDraw = nextDraw;
            n.turnBlock = turnBlock; n.turnDamage = turnDamage; n.orbSlots = orbSlots; n.focus = focus;
            n.permFocus = permFocus; n.lightningAll = lightningAll;
            for (int[] o : orbs) n.orbs.add(o.clone());
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
        St st = initial();
        best = new Plan();
        best.value = best.base = evaluate(st, best, true);
        dfs(st, new ArrayList<>());
        best.nodes = nodes;
        return best;
    }

    private St initial() {
        St st = new St();
        int n = s.mons.size();
        st.energy = s.energy;
        st.block = s.playerBlock;
        st.hp = new int[n]; st.mblock = new int[n]; st.vulnAdd = new int[n]; st.weakAdd = new int[n];
        st.strDown = new int[n]; st.dead = new boolean[n]; st.used = new boolean[s.hand.size()];
        st.poison = new int[n];
        st.intangible = s.intangible;
        st.doubleAtk = s.doubleAttack;
        st.doubleSkill = s.doubleSkill;
        st.orbSlots = s.orbSlots;
        st.focus = s.focus;
        st.lightningAll = s.lightningAll;
        for (Sim.Orb o : s.orbs) {
            int k = java.util.Arrays.asList(ORB).indexOf(o.kind);
            if (k >= 0) st.orbs.add(new int[]{k, o.evoke, o.passive});
        }
        boolean anyAlive = false, allRetaliate = true;
        for (int i = 0; i < n; i++) {
            Sim.Mon m = s.mons.get(i);
            st.hp[i] = m.hp;
            st.mblock[i] = m.block;
            st.poison[i] = m.poison;
            st.dead[i] = !m.alive || m.hp <= 0;
            if (!st.dead[i]) {
                anyAlive = true;
                if (m.thorns <= 0) allRetaliate = false;
            }
        }
        noSafeTarget = anyAlive && allRetaliate;
        inc0 = incoming(st);
        return st;
    }

    // ---------------------------------------------------------------- reasons for the overlay

    /** One short reason per step of the plan: what that card changes, found by replaying the plan. */
    public static List<String> explain(Sim s, Plan plan, Params p) {
        Planner pl = new Planner(s, p, 0);
        St st = pl.initial();
        List<String> out = new ArrayList<>();
        for (int k = 0; k < plan.steps.size(); k++) {
            Step step = plan.steps.get(k);
            int hk = -1, t = -1;
            for (int i = 0; i < s.hand.size(); i++) if (s.hand.get(i).index == step.card) hk = i;
            for (int i = 0; i < s.mons.size(); i++) if (s.mons.get(i).index == step.target) t = i;
            if (hk < 0) {
                out.add("");
                continue;
            }
            Sim.Card c = s.hand.get(hk);
            int cost = c.cost < 0 ? st.energy : c.cost;
            St before = st.copy();
            double incBefore = pl.incoming(before);
            st.used[hk] = true;
            pl.play(st, c, t, cost);
            boolean laterAttack = false;
            for (int j = k + 1; j < plan.steps.size(); j++) {
                Sim.Card n = card(s, plan.steps.get(j).card);
                if (n != null && "ATTACK".equals(n.type)) laterAttack = true;
            }
            out.add(pl.reason(before, st, c, t, incBefore, laterAttack));
        }
        return out;
    }

    /** Why ending the turn now is best. */
    public static String endReason(Sim s) {
        boolean playable = false, affordable = false;
        for (Sim.Card c : s.hand) {
            if (!c.playable || "STATUS".equals(c.type) || "CURSE".equals(c.type)) continue;
            playable = true;
            if (c.cost <= s.energy) affordable = true;
        }
        if (!playable) return "nothing in hand can be played";
        if (!affordable || s.energy == 0) return "out of energy";
        return "what's left would cost more than it gains";
    }

    private String reason(St before, St after, Sim.Card c, int t, double incBefore, boolean laterAttack) {
        List<String> parts = new ArrayList<>();
        int dmg = 0;
        List<String> killed = new ArrayList<>();
        int vul = 0, weak = 0, down = 0;
        for (int i = 0; i < s.mons.size(); i++) {
            dmg += before.hp[i] - after.hp[i];
            if (!before.dead[i] && after.dead[i]) {
                Sim.Mon m = s.mons.get(i);
                int hit = m.intentDmg * Math.max(1, m.intentHits);
                killed.add(m.name + (hit > 0 ? " (stops its " + hit + ")" : ""));
            }
            vul = Math.max(vul, after.vulnAdd[i] - before.vulnAdd[i]);
            weak = Math.max(weak, after.weakAdd[i] - before.weakAdd[i]);
            down = Math.max(down, after.strDown[i] - before.strDown[i]);
        }
        if (!killed.isEmpty()) parts.add("kills " + String.join(", ", killed));
        else if (dmg > 0) parts.add("deals " + dmg + (c.aoe ? " across all enemies" : t >= 0 ? " to " + s.mons.get(t).name : ""));
        if (vul > 0) parts.add(laterAttack ? "Vulnerable first: the attacks after it hit 50% harder" : "applies " + vul + " Vulnerable");
        if (weak > 0) parts.add("Weak: its attacks deal 25% less");
        if (down > 0) parts.add("enemy loses " + down + " Strength");
        int blk = after.block - before.block;
        if (blk > 0) {
            double need = Math.max(0, incBefore - before.block);
            parts.add(need > 0 ? "blocks " + (int) Math.min(blk, need) + " of the " + (int) incBefore + " coming" : "extra block");
        }
        int str = c.fx.strength + c.fx.tempStrength;
        if (str > 0) parts.add(laterAttack ? "+" + str + " Strength before the attacks" : "+" + str + " Strength");
        if (c.fx.draw > 0) parts.add("draws " + c.fx.draw);
        if (c.fx.energy > 0) parts.add("+" + c.fx.energy + " energy");
        if (c.fx.isPower) parts.add("power: pays off every turn after");
        double thorns = after.retal - before.retal;
        if (thorns > 0) parts.add("takes " + (int) thorns + " from Thorns");
        if (c.fx.selfDamage > 0) parts.add("costs " + c.fx.selfDamage + " HP");
        int pois = 0;
        List<String> doomed = new ArrayList<>();
        for (int i = 0; i < s.mons.size(); i++) {
            pois += Math.max(0, after.poison[i] - before.poison[i]);
            if (!after.dead[i] && after.poison[i] >= after.hp[i] && before.poison[i] < before.hp[i]) doomed.add(s.mons.get(i).name);
        }
        if (c.fx.poisonMult > 0 && pois > 0) parts.add((c.fx.poisonMult == 2 ? "doubles" : "triples") + " its Poison (+" + pois + ")");
        else if (pois > 0) parts.add("adds " + pois + " Poison (keeps ticking each turn)");
        if (!doomed.isEmpty()) parts.add("Poison kills " + String.join(", ", doomed) + " before it attacks");
        if (c.fx.shivs > 0) parts.add("makes " + c.fx.shivs + " Shiv" + (c.fx.shivs > 1 ? "s" : "") + " to play for free");
        if (c.fx.intangible > 0) parts.add("Intangible: every hit deals 1");
        if (c.fx.doubleAttack > 0) parts.add("the next Attack is played twice");
        if (c.fx.doubleSkill > 0) parts.add("the next Skill is played twice");
        if (after.heal > before.heal) parts.add("heals " + (int) (after.heal - before.heal));
        return parts.isEmpty() ? "sets up the rest of the turn" : String.join(", ", parts);
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
        play(st, c, target, cost, false);
    }

    private int totalHp(St st) {
        int t = 0;
        for (int h : st.hp) t += h;
        return t;
    }

    /** The target Shivs and random poison go to: the highest kill priority, then the lowest HP. */
    private int priorityTarget(St st) {
        int best = -1;
        for (int i = 0; i < s.mons.size(); i++) {
            if (st.dead[i]) continue;
            if (best < 0 || s.mons.get(i).prio > s.mons.get(best).prio
                    || (s.mons.get(i).prio == s.mons.get(best).prio && st.hp[i] < st.hp[best])) best = i;
        }
        return best;
    }

    private void play(St st, Sim.Card c, int target, int cost, boolean echo) {
        Effects fx = c.fx;
        boolean again = false;
        if (!echo && "ATTACK".equals(c.type) && st.doubleAtk > 0) { st.doubleAtk--; again = true; }
        else if (!echo && "SKILL".equals(c.type) && st.doubleSkill > 0) { st.doubleSkill--; again = true; }
        int hpBefore = totalHp(st);
        if (!echo) st.energy -= cost;
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
        st.block += c.block > 0 ? c.block + st.dexGain : 0;   // the game's block + Dexterity gained this turn
        for (int i = 0; i < s.mons.size(); i++) {
            boolean affected = (fx.aoe || c.aoe) ? !st.dead[i] : i == target;
            if (!affected) continue;
            st.vulnAdd[i] += fx.vuln;
            st.weakAdd[i] += fx.weak + (fx.xWeak ? xOf(c, cost) : 0);                  // Malaise: X Weak
            st.strDown[i] += fx.enemyStrDown + (fx.xStrDown ? xOf(c, cost) : 0);      // and X Strength
        }
        st.strGain += fx.strength + fx.tempStrength;
        st.permStr += fx.strength;
        st.dexGain += fx.dexterity;
        st.draw += fx.draw;
        st.selfLoss += fx.selfDamage;
        if (fx.isPower) st.powers++;
        // poison: Crippling Cloud on all, Deadly Poison / Corpse Explosion on the target, Bouncing Flask at random
        if (fx.poison > 0) {
            if (fx.poisonAoe) {
                for (int i = 0; i < s.mons.size(); i++) if (!st.dead[i]) st.poison[i] += fx.poison;
            } else {
                int t = target >= 0 && !st.dead[target] ? target : priorityTarget(st);
                if (t >= 0) st.poison[t] += fx.poison;
            }
        }
        if (fx.poisonMult > 0 && target >= 0 && !st.dead[target]) st.poison[target] *= fx.poisonMult;   // Catalyst
        for (int k = 0; k < fx.shivs; k++) {   // Shivs: free 4-damage attacks, counted now
            int t = priorityTarget(st);
            if (t < 0) break;
            Sim.Mon m = s.mons.get(t);
            double per = (4 + s.strength + st.strGain) * (s.playerWeak ? 0.75 : 1.0);
            if (m.vuln > 0 || st.vulnAdd[t] > 0) per *= 1.5;
            hit(st, t, (int) per);
            if (m.thorns > 0) st.retal += m.thorns;
        }
        st.nextBlock += fx.nextBlock;
        st.nextEnergy += fx.nextEnergy;
        st.nextDraw += fx.nextDraw;
        st.turnBlock += fx.turnBlock;
        st.turnDamage += fx.turnDamage;
        if (st.orbSlots > 0 || fx.orbSlots != 0 || !fx.channel.isEmpty() || !fx.channelPerEnemy.isEmpty() || !fx.channelX.isEmpty())
            orbEffects(st, fx, c, target, xOf(c, cost));
        if (fx.intangible > 0) st.intangible = true;
        st.doubleAtk += fx.doubleAttack;
        st.doubleSkill += fx.doubleSkill;
        if (fx.healUnblocked) st.heal += Math.max(0, hpBefore - totalHp(st));   // Reaper
        if (again) play(st, c, target, cost, true);
    }

    private static int xOf(Sim.Card c, int cost) {
        return c.cost < 0 ? Math.max(0, cost) : 0;
    }

    // ---------------------------------------------------------------- Defect orbs

    private int[] newOrb(int k, int focus) {
        int passive = ORB_BASE[k][0], evoke = ORB_BASE[k][1];
        if (k <= 1) return new int[]{k, Math.max(0, evoke + focus), Math.max(0, passive + focus)};   // Lightning, Frost
        if (k == 2) return new int[]{k, evoke, Math.max(0, passive + focus)};                          // Dark
        return new int[]{k, evoke, passive};                                                           // Plasma
    }

    private void lightning(St st, int amount) {   // orb damage ignores Strength, Weak and Vulnerable
        if (st.lightningAll) {
            for (int i = 0; i < s.mons.size(); i++) if (!st.dead[i]) hit(st, i, amount);
        } else {
            int t = priorityTarget(st);
            if (t >= 0) hit(st, t, amount);
        }
    }

    private void evokeOrb(St st, int[] o) {
        if (o[0] == 0) lightning(st, o[1]);
        else if (o[0] == 1) st.block += o[1];
        else if (o[0] == 2) {   // Dark: the lowest-HP enemy
            int t = -1;
            for (int i = 0; i < s.mons.size(); i++) if (!st.dead[i] && (t < 0 || st.hp[i] < st.hp[t])) t = i;
            if (t >= 0) hit(st, t, o[1]);
        } else st.energy += o[1];
    }

    private void channelOrb(St st, int k) {
        if (st.orbSlots <= 0 || k < 0) return;
        if (st.orbs.size() >= st.orbSlots) evokeOrb(st, st.orbs.remove(0));   // full: the oldest is evoked
        st.orbs.add(newOrb(k, st.focus));
    }

    private void orbEffects(St st, Effects fx, Sim.Card c, int target, int x) {
        java.util.List<String> kinds = java.util.Arrays.asList(ORB);
        if (fx.focus != 0) {
            st.focus += fx.focus;
            st.permFocus += fx.focus;
            for (int[] o : st.orbs) {
                if (o[0] <= 1) { o[1] = Math.max(0, o[1] + fx.focus); o[2] = Math.max(0, o[2] + fx.focus); }
                else if (o[0] == 2) o[2] = Math.max(0, o[2] + fx.focus);
            }
        }
        if (fx.orbSlots != 0) {
            st.orbSlots = Math.max(0, st.orbSlots + fx.orbSlots);
            while (st.orbs.size() > st.orbSlots) st.orbs.remove(st.orbs.size() - 1);
        }
        if (fx.lightningAll) st.lightningAll = true;
        if (fx.perOrb && target >= 0 && st.orbs.size() > 1 && c.damage.length > 0) {   // Barrage: one hit per orb
            int per = (c.damage.length > target ? c.damage[target] : c.damage[0]) + st.strGain;
            for (int k = 1; k < st.orbs.size() && !st.dead[target]; k++) hit(st, target, per);
        }
        int times = fx.evoke < 0 ? x : fx.evoke;
        if (times > 0 && !st.orbs.isEmpty()) {
            int[] o = st.orbs.remove(0);
            for (int k = 0; k < times; k++) evokeOrb(st, o);
            if (fx.recursion) channelOrb(st, o[0]);
        }
        for (String k : fx.channel) channelOrb(st, kinds.indexOf(k));
        if (!fx.channelPerEnemy.isEmpty())
            for (int i = 0; i < s.mons.size(); i++) if (!st.dead[i]) channelOrb(st, kinds.indexOf(fx.channelPerEnemy));
        if (!fx.channelX.isEmpty()) for (int k = 0; k < x; k++) channelOrb(st, kinds.indexOf(fx.channelX));
    }

    /** End of turn, before the enemies act: Lightning hits, Frost blocks, Dark grows. */
    private void orbPassives(St st) {
        for (int[] o : st.orbs) {
            if (o[0] == 0) lightning(st, o[2]);
            else if (o[0] == 1) st.block += o[2];
            else if (o[0] == 2) o[1] += o[2];
        }
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
            if (st.poison[i] >= st.hp[i]) continue;   // its Poison kills it at the start of its turn
            double per = Math.max(0, m.intentDmg - st.strDown[i]);
            if (m.weak == 0 && st.weakAdd[i] > 0) per = Math.floor(per * 0.75);
            inc += per * Math.max(1, m.intentHits);
        }
        return inc;
    }

    private int hitsComing(St st) {
        int n = 0;
        for (int i = 0; i < s.mons.size(); i++) {
            Sim.Mon m = s.mons.get(i);
            if (!st.dead[i] && m.intentDmg > 0 && st.poison[i] < st.hp[i]) n += Math.max(1, m.intentHits);
        }
        return n;
    }

    private double evaluate(St st, Plan out, boolean isBase) {
        if (!st.orbs.isEmpty()) {
            st = st.copy();
            orbPassives(st);   // before the enemies act: Frost blocks this turn's hits, Lightning can kill first
        }
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
        if (st.intangible) taken = Math.min(taken, hitsComing(st));   // Intangible: every hit deals 1
        if (st.heal > 0) v += p.hpCost * Math.min(st.heal, Math.max(0, s.maxHp - s.hp + taken));
        for (int i = 0; i < s.mons.size(); i++)   // poison already on it or added: damage that keeps coming
            if (!st.dead[i] && s.mons.get(i).alive && st.poison[i] > 0) v += p.damage * Math.min(st.hp[i], st.poison[i] * 2.5);
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
        v += p.block * st.dexGain * fight * DEX_BLOCK_CARDS;   // Dexterity lasts the fight: +1 on ~1.5 block cards a turn
        int live = 0;
        for (int i = 0; i < s.mons.size(); i++) if (!st.dead[i] && s.mons.get(i).alive) live++;
        if (!st.orbs.isEmpty() || st.permFocus != 0) {   // orbs and Focus keep working every turn
            double light = 0, frost = 0, dark = 0, plasma = 0;
            for (int[] o : st.orbs) {
                if (o[0] == 0) light += o[2]; else if (o[0] == 1) frost += o[2]; else if (o[0] == 2) dark++; else plasma++;
            }
            if (st.lightningAll) light *= Math.max(1, live);
            v += (p.damage * light + p.block * frost + p.damage * 3.0 * dark + p.energy * 0.5 * plasma) * fight * ORB_FUTURE;
            v += p.block * st.permFocus * Math.max(1, st.orbs.size()) * fight * 0.5;
        }
        // "Next turn, ..." riders a little below now; After Image / Noxious Fumes every turn
        v += 0.8 * (p.block * st.nextBlock + p.energy * st.nextEnergy) + 0.5 * p.draw * st.nextDraw;
        v += (p.block * st.turnBlock + p.damage * st.turnDamage * Math.max(1, live)) * fight * 0.7;
        if (st.powers > 0) v += p.power * st.powers * (s.elite ? 1.5 : 1) * (s.turn <= 3 ? 1 : 0.5) * fight / 2;
        v += p.draw * st.draw;
        out.dealt = dealt;
        out.block = st.block;
        out.taken = (int) Math.round(taken + st.retal + st.selfLoss);
        out.kills = kills;
        return v;
    }
}
