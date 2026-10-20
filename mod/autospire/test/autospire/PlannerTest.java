package autospire;

import autospire.brain.Effects;
import autospire.brain.Planner;
import autospire.brain.Sim;

/** Plain-Java checks for the combat brain (no game needed). Mirrors cases from Spire AI's tests/test_planner.py. */
public final class PlannerTest {

    static int failed = 0;

    static void check(String label, boolean ok, String detail) {
        System.out.println((ok ? "PASS " : "FAIL ") + label + (detail.isEmpty() ? "" : "  [" + detail + "]"));
        if (!ok) failed++;
    }

    static Sim.Card card(Sim s, String name, String type, int cost, int dmg, int block, boolean targeted, String text, int magic) {
        Sim.Card c = new Sim.Card();
        c.index = s.hand.size();
        c.name = name;
        c.type = type;
        c.cost = cost;
        c.targeted = targeted;
        c.fx = Effects.parse(text, type, magic);
        c.hits = c.fx.hits;
        c.aoe = c.fx.aoe;
        c.randomTarget = c.fx.randomTarget;
        c.block = block;
        if (dmg > 0) {
            c.damage = new int[s.mons.size()];
            java.util.Arrays.fill(c.damage, dmg);
        }
        s.hand.add(c);
        return c;
    }

    static Sim.Card strike(Sim s) { return card(s, "Strike", "ATTACK", 1, 6, 0, true, "Deal !D! damage.", 0); }
    static Sim.Card defend(Sim s) { return card(s, "Defend", "SKILL", 1, 0, 5, false, "Gain !B! Block.", 0); }

    static Sim.Mon mon(Sim s, String name, int hp, int dmg, int hits, int thorns) {
        Sim.Mon m = new Sim.Mon();
        m.index = s.mons.size();
        m.name = name;
        m.hp = m.maxHp = hp;
        m.intentDmg = dmg;
        m.intentHits = dmg > 0 ? hits : 0;
        m.thorns = thorns;
        s.mons.add(m);
        return m;
    }

    static Sim sim(int hp, int energy) {
        Sim s = new Sim();
        s.hp = hp;
        s.maxHp = 80;
        s.energy = energy;
        s.turn = 2;
        return s;
    }

    static String names(Sim s, Planner.Plan p) {
        StringBuilder b = new StringBuilder();
        for (Planner.Step st : p.steps) b.append(s.hand.get(st.card).name).append(st.target >= 0 ? ">" + st.target : "").append(" ");
        return b.toString().trim();
    }

    public static void main(String[] args) {
        Planner.Params P = new Planner.Params();

        // text parser
        Effects e = Effects.parse("Deal !D! damage. NL Apply !M! Vulnerable.", "ATTACK", 2);
        check("parser: Bash applies 2 Vulnerable", e.vuln == 2, "vuln " + e.vuln);
        e = Effects.parse("Gain [R] [R].", "SKILL", 0);
        check("parser: Seeing Red gains 2 energy", e.energy == 2, "energy " + e.energy);
        e = Effects.parse("Gain !M! Strength. NL At the end of this turn, lose !M! Strength.", "SKILL", 2);
        check("parser: Flex is temporary Strength", e.tempStrength == 2 && e.strength == 0, e.tempStrength + "/" + e.strength);
        e = Effects.parse("Deal !D! damage twice.", "ATTACK", 0);
        check("parser: Twin Strike hits twice", e.hits == 2, "hits " + e.hits);
        e = Effects.parse("Deal !D! damage to a random enemy !M! times.", "ATTACK", 3);
        check("parser: Sword Boomerang 3 random hits", e.hits == 3 && e.randomTarget, e.hits + " " + e.randomTarget);
        e = Effects.parse("Whenever you play a Skill, gain !M! Strength.", "POWER", 1);
        check("parser: triggered Strength is not immediate", e.strength == 0, "str " + e.strength);

        // run 199: a lone Spiker with big Thorns -- finish it
        Sim s = sim(40, 2);
        mon(s, "Spiker", 6, 7, 1, 15);
        strike(s); defend(s);
        Planner.Plan p = Planner.plan(s, P);
        check("finishes a lone Thorns enemy", names(s, p).contains("Strike"), p.describe(s));

        // run 211: 54 HP Spiker with growing Thorns -- no safe target, hit it (big hit first)
        s = sim(40, 3);
        mon(s, "Spiker", 54, 5, 1, 9);
        card(s, "Heavy Blade", "ATTACK", 2, 14, 0, true, "Deal !D! damage.", 3);
        defend(s);
        card(s, "Twin Strike", "ATTACK", 1, 5, 0, true, "Deal !D! damage twice.", 0);
        p = Planner.plan(s, P);
        check("attacks a lone high-HP Thorns enemy", names(s, p).contains("Heavy Blade"), p.describe(s));

        // hit the safe target, not the Thorns one
        s = sim(70, 2);
        mon(s, "Spiker", 40, 0, 0, 4);
        mon(s, "Exploder", 12, 9, 1, 0);
        strike(s); strike(s);
        p = Planner.plan(s, P);
        boolean safe = true;
        for (Planner.Step st : p.steps) safe &= st.target == 1;
        check("avoids the Thorns target", safe && !p.steps.isEmpty(), names(s, p));

        // lethal incoming: block
        s = sim(8, 3);
        mon(s, "Jaw Worm", 40, 12, 1, 0);
        strike(s); defend(s); defend(s);
        p = Planner.plan(s, P);
        check("blocks a lethal hit", names(s, p).split("Defend", -1).length - 1 == 2, names(s, p));

        // Vulnerable before the big hits
        s = sim(70, 3);
        mon(s, "Jaw Worm", 60, 0, 0, 0);
        strike(s);
        card(s, "Bash", "ATTACK", 2, 8, 0, true, "Deal !D! damage. NL Apply !M! Vulnerable.", 2);
        p = Planner.plan(s, P);
        check("Bash before Strike", names(s, p).startsWith("Bash"), names(s, p));

        // kill the attacker that can be killed now
        s = sim(30, 1);
        mon(s, "Cultist", 50, 6, 1, 0);
        mon(s, "Louse", 5, 7, 1, 0);
        strike(s);
        p = Planner.plan(s, P);
        check("kills what it can", !p.steps.isEmpty() && p.steps.get(0).target == 1, names(s, p));

        // The Champ: don't chip it into phase 2 at low HP; fine with HP to spare
        s = sim(40, 2);
        Sim.Mon champ = mon(s, "The Champ", 222, 0, 0, 0);
        champ.maxHp = 420;
        card(s, "Heavy Blade", "ATTACK", 2, 14, 0, true, "Deal !D! damage.", 3);
        defend(s);
        p = Planner.plan(s, P);
        Sim s2 = sim(78, 2);
        Sim.Mon c2 = mon(s2, "The Champ", 222, 0, 0, 0);
        c2.maxHp = 420;
        card(s2, "Heavy Blade", "ATTACK", 2, 14, 0, true, "Deal !D! damage.", 3);
        defend(s2);
        Planner.Plan p2 = Planner.plan(s2, P);
        check("Champ: holds the crossing at low HP only", !names(s, p).contains("Heavy Blade") && names(s2, p2).contains("Heavy Blade"),
              names(s, p) + " | " + names(s2, p2));

        if (failed > 0) {
            System.out.println(failed + " FAILED");
            System.exit(1);
        }
        System.out.println("AUTOSPIRE BRAIN OK");
    }
}
