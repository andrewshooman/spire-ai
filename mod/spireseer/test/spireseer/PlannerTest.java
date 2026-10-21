package spireseer;

import spireseer.brain.Effects;
import spireseer.brain.Planner;
import java.util.List;
import spireseer.brain.PotionAdvisor;
import spireseer.brain.Sim;

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

    static java.util.List<PotionAdvisor.Potion> pots(String... names) {
        java.util.List<PotionAdvisor.Potion> out = new java.util.ArrayList<>();
        for (String nm : names) {
            PotionAdvisor.Potion p = new PotionAdvisor.Potion();
            p.slot = out.size();
            p.id = nm.replace(" ", "");
            p.name = nm;
            out.add(p);
        }
        return out;
    }

    public static void main(String[] args) {
        Planner.Params P = new Planner.Params();

        // weights from the bot's model.json params
        java.util.Map<String, Double> mp = new java.util.HashMap<>();
        mp.put("block", 2.5);
        mp.put("hp_cost", 1.0);
        Planner.Params fromBot = Planner.Params.from(mp);
        check("bot weights load by name, others keep defaults", fromBot.block == 2.5 && fromBot.hpCost == 1.0 && fromBot.kill == P.kill, "");

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
        List<String> whyBlock = Planner.explain(s, p, P);
        boolean blocks = false;
        for (String w : whyBlock) blocks |= w.startsWith("blocks 5 of the 12 coming");
        check("reason: Defend says what it blocks", blocks, whyBlock.toString());

        // Vulnerable before the big hits
        s = sim(70, 3);
        mon(s, "Jaw Worm", 60, 0, 0, 0);
        strike(s);
        card(s, "Bash", "ATTACK", 2, 8, 0, true, "Deal !D! damage. NL Apply !M! Vulnerable.", 2);
        p = Planner.plan(s, P);
        check("Bash before Strike", names(s, p).startsWith("Bash"), names(s, p));
        java.util.List<String> why = Planner.explain(s, p, P);
        check("reason: Bash is played first for Vulnerable", why.get(0).contains("Vulnerable first"), why.toString());
        check("reason: Strike says how much it deals", why.size() > 1 && why.get(1).startsWith("deals 9"), why.toString());

        // kill the attacker that can be killed now
        s = sim(30, 1);
        mon(s, "Cultist", 50, 6, 1, 0);
        mon(s, "Louse", 5, 7, 1, 0);
        strike(s);
        p = Planner.plan(s, P);
        check("kills what it can", !p.steps.isEmpty() && p.steps.get(0).target == 1, names(s, p));
        why = Planner.explain(s, p, P);
        check("reason: names the kill and the damage it stops", why.get(0).startsWith("kills Louse (stops its 7)"), why.toString());

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

        // ---- Silent mechanics and undervalued cards (ported from the bot's planner)
        e = Effects.parse("Add !M! *Shivs into your hand.", "SKILL", 3);
        check("parser: Blade Dance makes 3 Shivs", e.shivs == 3, "shivs " + e.shivs);
        e = Effects.parse("Apply !M! Poison to a random enemy 3 times.", "SKILL", 3);
        check("parser: Bouncing Flask is 9 poison", e.poison == 9, "poison " + e.poison);
        e = Effects.parse("Apply !M! Poison. NL When the enemy dies, deal damage equal to its Max HP to ALL enemies.", "SKILL", 6);
        check("parser: Corpse Explosion poisons one enemy", e.poison == 6 && !e.poisonAoe, e.poison + " " + e.poisonAoe);
        e = Effects.parse("Apply !M! Poison and 2 Weak to ALL enemies. NL Exhaust.", "SKILL", 4);
        check("parser: Crippling Cloud poisons all", e.poison == 4 && e.poisonAoe, e.poison + " " + e.poisonAoe);
        e = Effects.parse("This turn, your next !M! Attacks are played twice.", "SKILL", 2);
        check("parser: Double Tap+ doubles 2 Attacks, not a 2-hit card", e.doubleAttack == 2 && e.hits == 1, e.doubleAttack + " " + e.hits);

        s = sim(70, 1);
        mon(s, "Louse", 11, 6, 1, 0);
        card(s, "Blade Dance", "SKILL", 1, 0, 0, false, "Add !M! *Shivs into your hand.", 3);
        defend(s);
        p = Planner.plan(s, P);
        check("Shivs count: Blade Dance kills an 11 HP Louse", names(s, p).equals("Blade Dance") && p.kills == 1, p.describe(s));

        s = sim(70, 1);
        mon(s, "Jaw Worm", 60, 0, 0, 0).poison = 12;
        card(s, "Catalyst", "SKILL", 1, 0, 0, true, "Double an enemy's Poison. NL Exhaust.", 0);
        defend(s);
        p = Planner.plan(s, P);
        check("Catalyst on a poisoned enemy", names(s, p).startsWith("Catalyst"), names(s, p));
        check("reason: Catalyst doubles its Poison", Planner.explain(s, p, P).get(0).contains("doubles its Poison"),
              Planner.explain(s, p, P).toString());

        s = sim(20, 2);
        mon(s, "Cultist", 5, 30, 1, 0).poison = 6;
        defend(s); defend(s);
        p = Planner.plan(s, P);
        check("poison-doomed enemy never attacks: no lethal panic", p.taken == 0 && p.base > -100, p.describe(s));

        s = sim(40, 3);
        mon(s, "Book of Stabbing", 120, 6, 6, 0);
        card(s, "Wraith Form", "POWER", 3, 0, 0, false, "Gain !M! Intangible. NL At the end of your turn, lose 1 Dexterity.", 2);
        defend(s); defend(s); defend(s);
        p = Planner.plan(s, P);
        check("Wraith Form against a 36-damage turn", names(s, p).equals("Wraith Form"), names(s, p));

        s = sim(70, 3);
        mon(s, "Jaw Worm", 60, 0, 0, 0);
        card(s, "Double Tap", "SKILL", 1, 0, 0, false, "This turn, your next Attack is played twice.", 0);
        card(s, "Heavy Blade", "ATTACK", 2, 14, 0, true, "Deal !D! damage.", 3);
        p = Planner.plan(s, P);
        check("Double Tap before Heavy Blade hits twice", names(s, p).startsWith("Double Tap") && p.dealt >= 28, p.describe(s));

        Sim hurt = sim(30, 2), full = sim(80, 2);
        for (Sim x : new Sim[]{hurt, full}) {
            x.maxHp = 80;
            mon(x, "Louse", 12, 0, 0, 0);
            mon(x, "Louse", 12, 0, 0, 0);
            Sim.Card r = card(x, "Reaper", "ATTACK", 2, 4, 0, false, "Deal !D! damage to ALL enemies. NL Heal HP equal to unblocked damage. NL Exhaust.", 0);
            r.aoe = true;
        }
        Planner.Plan ph = Planner.plan(hurt, P), pf = Planner.plan(full, P);
        check("Reaper's healing counts when hurt", ph.value - ph.base > pf.value - pf.base + 5,
              String.format("%.1f vs %.1f", ph.value - ph.base, pf.value - pf.base));

        // Dexterity lasts the fight: Footwork over one Defend on a quiet turn, Defend when the hit would kill
        Sim fwQuiet = sim(70, 1);
        mon(fwQuiet, "Jaw Worm", 44, 5, 1, 0);
        card(fwQuiet, "Footwork", "POWER", 1, 0, 0, false, "Gain !M! Dexterity.", 2);
        defend(fwQuiet);
        p = Planner.plan(fwQuiet, P);
        check("Footwork on a quiet turn", names(fwQuiet, p).startsWith("Footwork"), p.describe(fwQuiet));
        Sim fwKill = sim(8, 1);
        mon(fwKill, "Jaw Worm", 44, 11, 1, 0);
        card(fwKill, "Footwork", "POWER", 1, 0, 0, false, "Gain !M! Dexterity.", 2);
        defend(fwKill);
        p = Planner.plan(fwKill, P);
        check("Defend when the hit would kill", names(fwKill, p).startsWith("Defend"), p.describe(fwKill));
        Sim fwBoth = sim(70, 2);
        mon(fwBoth, "Jaw Worm", 44, 20, 1, 0);
        card(fwBoth, "Footwork", "POWER", 1, 0, 0, false, "Gain !M! Dexterity.", 2);
        defend(fwBoth);
        p = Planner.plan(fwBoth, P);
        check("Footwork then Defend blocks 7", names(fwBoth, p).startsWith("Footwork") && p.block == 7, p.describe(fwBoth));

        // potions (Spire AI policy._potion rules)
        PotionAdvisor.Context ctx = new PotionAdvisor.Context();
        ctx.boss = true; ctx.turn = 1; ctx.hp = 60; ctx.maxHp = 80; ctx.champHp = 420; ctx.champMaxHp = 420;
        PotionAdvisor.Advice a = PotionAdvisor.advise(pots("Fire Potion", "Strength Potion"), ctx);
        check("Champ opening: Strength Potion, Fire Potion saved", a != null && a.potion.name.equals("Strength Potion"), a == null ? "none" : a.potion.name);
        ctx.champHp = 200; ctx.turn = 8;
        a = PotionAdvisor.advise(pots("Fire Potion"), ctx);
        check("Champ phase 2: burst potion now", a != null && a.potion.name.equals("Fire Potion"), a == null ? "none" : a.why);
        PotionAdvisor.Context hall = new PotionAdvisor.Context();
        hall.boss = true; hall.turn = 1; hall.hp = 60; hall.maxHp = 80;
        a = PotionAdvisor.advise(pots("Fire Potion"), hall);
        check("boss: keeps its only non-permanent potion", a == null, a == null ? "" : a.potion.name);
        hall.attacksPlanned = false;
        a = PotionAdvisor.advise(pots("Flex Potion", "Fire Potion"), hall);
        check("no Flex Potion on a turn without attacks", a != null && !a.potion.name.equals("Flex Potion"), a == null ? "none" : a.potion.name);
        PotionAdvisor.Context lethal = new PotionAdvisor.Context();
        lethal.hp = 10; lethal.maxHp = 80; lethal.taken = 30; lethal.turn = 3;
        a = PotionAdvisor.advise(pots("Fire Potion", "Block Potion"), lethal);
        check("lethal incoming: Block Potion first", a != null && a.potion.name.equals("Block Potion"), a == null ? "none" : a.potion.name);
        PotionAdvisor.Context calm = new PotionAdvisor.Context();
        calm.hp = 70; calm.maxHp = 80; calm.taken = 5; calm.turn = 2;
        check("hallway fight: keeps potions", PotionAdvisor.advise(pots("Strength Potion"), calm) == null, "");
        a = PotionAdvisor.advise(pots("Smoke Bomb"), lethal);
        check("no Smoke Bomb on a lethal turn", a == null, a == null ? "" : a.potion.name);

        if (failed > 0) {
            System.out.println(failed + " FAILED");
            System.exit(1);
        }
        System.out.println("SPIRESEER BRAIN OK");
    }
}
