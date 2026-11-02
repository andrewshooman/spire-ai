package spireseer;

import spireseer.brain.Effects;
import spireseer.brain.Planner;
import java.util.List;
import spireseer.brain.PotionAdvisor;
import spireseer.brain.Sim;

/** Plain-Java checks for the combat brain (no game needed). Mirrors cases from Spire AI's tests/test_planner.py.
 *  Cards are given in the card database's wording (what Effects.normalize makes of the game's text). */
public final class PlannerTest {

    static int failed = 0;

    static void check(String label, boolean ok, String detail) {
        System.out.println((ok ? "PASS " : "FAIL ") + label + (detail.isEmpty() ? "" : "  [" + detail + "]"));
        if (!ok) failed++;
    }

    static Sim.Card card(Sim s, String name, String type, int cost, boolean targeted, String text) {
        Sim.Card c = new Sim.Card();
        c.index = s.hand.size();
        c.name = name;
        c.type = type;
        c.cost = cost;
        c.targeted = targeted;
        c.fx = Effects.of(text, type, cost, Effects.baseName(name));
        s.hand.add(c);
        return c;
    }

    static Sim.Card strike(Sim s) { return card(s, "Strike", "ATTACK", 1, true, "Deal 6 damage."); }
    static Sim.Card defend(Sim s) { return card(s, "Defend", "SKILL", 1, false, "Gain 5 Block."); }

    static Sim.Mon mon(Sim s, String name, int hp, int dmg, int hits, int thorns) {
        Sim.Mon m = new Sim.Mon();
        m.index = s.mons.size();
        m.name = name;
        m.hp = m.maxHp = hp;
        m.atk = dmg;
        m.hits = dmg > 0 ? Math.max(1, hits) : 0;
        m.intent = dmg > 0 ? "ATTACK" : "BUFF";
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


    /** The game's raw text through the normalizer, then the parser (what the mod does in a fight). */
    static Effects parseRaw(String raw, String type, int magic) {
        return Effects.of(Effects.normalize(raw, 0, 0, magic), type, 1, "Test Card");
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
        Effects e = parseRaw("Deal !D! damage. NL Apply !M! Vulnerable.", "ATTACK", 2);
        check("parser: Bash applies 2 Vulnerable", e.vuln == 2, "vuln " + e.vuln);
        e = parseRaw("Gain [R] [R].", "SKILL", 0);
        check("parser: Seeing Red gains 2 energy", e.energy == 2, "energy " + e.energy);
        e = parseRaw("Gain !M! Strength. NL At the end of this turn, lose !M! Strength.", "SKILL", 2);
        check("parser: Flex is temporary Strength", e.tempStrength == 2 && e.strength == 0, e.tempStrength + "/" + e.strength);
        e = parseRaw("Deal !D! damage twice.", "ATTACK", 0);
        check("parser: Twin Strike hits twice", e.hits == 2, "hits " + e.hits);
        e = parseRaw("Deal !D! damage to a random enemy !M! times.", "ATTACK", 3);
        check("parser: Sword Boomerang 3 random hits", e.hits == 3 && e.randomTarget, e.hits + " " + e.randomTarget);
        e = parseRaw("Whenever you play a Skill, gain !M! Strength.", "POWER", 1);
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
        card(s, "Heavy Blade", "ATTACK", 2, true, "Deal 14 damage.");
        defend(s);
        card(s, "Twin Strike", "ATTACK", 1, true, "Deal 5 damage twice.");
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
        card(s, "Bash", "ATTACK", 2, true, "Deal 8 damage. Apply 2 Vulnerable.");
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
        card(s, "Heavy Blade", "ATTACK", 2, true, "Deal 14 damage.");
        defend(s);
        p = Planner.plan(s, P);
        Sim s2 = sim(78, 2);
        Sim.Mon c2 = mon(s2, "The Champ", 222, 0, 0, 0);
        c2.maxHp = 420;
        card(s2, "Heavy Blade", "ATTACK", 2, true, "Deal 14 damage.");
        defend(s2);
        Planner.Plan p2 = Planner.plan(s2, P);
        check("Champ: holds the crossing at low HP only", !names(s, p).contains("Heavy Blade") && names(s2, p2).contains("Heavy Blade"),
              names(s, p) + " | " + names(s2, p2));

        // ---- Silent mechanics and undervalued cards (ported from the bot's planner)
        e = parseRaw("Add !M! *Shivs into your hand.", "SKILL", 3);
        check("parser: Blade Dance makes 3 Shivs", e.shivs == 3, "shivs " + e.shivs);
        e = parseRaw("Apply !M! Poison to a random enemy 3 times.", "SKILL", 3);
        check("parser: Bouncing Flask is 9 poison", e.poison == 9, "poison " + e.poison);
        e = parseRaw("Apply !M! Poison. NL When the enemy dies, deal damage equal to its Max HP to ALL enemies.", "SKILL", 6);
        check("parser: Corpse Explosion poisons one enemy", e.poison == 6 && !e.aoeDebuff, e.poison + " " + e.aoeDebuff);
        e = parseRaw("Apply !M! Poison and 2 Weak to ALL enemies. NL Exhaust.", "SKILL", 4);
        check("parser: Crippling Cloud poisons all", e.poison == 4 && e.aoeDebuff, e.poison + " " + e.aoeDebuff);
        e = parseRaw("This turn, your next !M! Attacks are played twice.", "SKILL", 2);
        check("parser: Double Tap+ doubles 2 Attacks", e.doubleAttack == 2, e.doubleAttack + " " + e.hits);

        s = sim(70, 1);
        mon(s, "Louse", 11, 6, 1, 0);
        card(s, "Blade Dance", "SKILL", 1, false, "Add 3 Shivs into your hand.");
        defend(s);
        p = Planner.plan(s, P);
        check("Shivs count: Blade Dance kills an 11 HP Louse", names(s, p).equals("Blade Dance") && p.kills == 1, p.describe(s));

        s = sim(70, 1);
        mon(s, "Jaw Worm", 60, 0, 0, 0).poison = 12;
        card(s, "Catalyst", "SKILL", 1, true, "Double the enemy's Poison. Exhaust.");
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
        card(s, "Wraith Form", "POWER", 3, false, "Gain 2 Intangible. At the end of your turn, lose 1 Dexterity.");
        defend(s); defend(s); defend(s);
        p = Planner.plan(s, P);
        check("Wraith Form against a 36-damage turn", names(s, p).equals("Wraith Form"), names(s, p));

        s = sim(70, 3);
        mon(s, "Jaw Worm", 60, 0, 0, 0);
        card(s, "Double Tap", "SKILL", 1, false, "This turn, your next Attack is played twice.");
        card(s, "Heavy Blade", "ATTACK", 2, true, "Deal 14 damage.");
        p = Planner.plan(s, P);
        check("Double Tap before Heavy Blade hits twice", names(s, p).startsWith("Double Tap") && p.dealt >= 28, p.describe(s));

        Sim hurt = sim(30, 2), full = sim(80, 2);
        for (Sim x : new Sim[]{hurt, full}) {
            x.maxHp = 80;
            mon(x, "Louse", 12, 0, 0, 0);
            mon(x, "Louse", 12, 0, 0, 0);
            Sim.Card r = card(x, "Reaper", "ATTACK", 2, false, "Deal 4 damage to ALL enemies. Heal HP equal to unblocked damage. Exhaust.");
        }
        Planner.Plan ph = Planner.plan(hurt, P), pf = Planner.plan(full, P);
        check("Reaper's healing counts when hurt", ph.value - ph.base > pf.value - pf.base + 5,
              String.format("%.1f vs %.1f", ph.value - ph.base, pf.value - pf.base));

        // Dexterity lasts the fight: Footwork over one Defend on a quiet turn, Defend when the hit would kill
        Sim fwQuiet = sim(70, 1);
        mon(fwQuiet, "Jaw Worm", 44, 5, 1, 0);
        card(fwQuiet, "Footwork", "POWER", 1, false, "Gain 2 Dexterity.");
        defend(fwQuiet);
        p = Planner.plan(fwQuiet, P);
        check("Footwork on a quiet turn", names(fwQuiet, p).startsWith("Footwork"), p.describe(fwQuiet));
        Sim fwKill = sim(8, 1);
        mon(fwKill, "Jaw Worm", 44, 11, 1, 0);
        card(fwKill, "Footwork", "POWER", 1, false, "Gain 2 Dexterity.");
        defend(fwKill);
        p = Planner.plan(fwKill, P);
        check("Defend when the hit would kill", names(fwKill, p).startsWith("Defend"), p.describe(fwKill));
        Sim fwBoth = sim(70, 2);
        mon(fwBoth, "Jaw Worm", 44, 20, 1, 0);
        card(fwBoth, "Footwork", "POWER", 1, false, "Gain 2 Dexterity.");
        defend(fwBoth);
        p = Planner.plan(fwBoth, P);
        check("Footwork then Defend blocks 7", names(fwBoth, p).startsWith("Footwork") && p.block == 7, p.describe(fwBoth));

        // Defect orbs: channel, evoke, passives, Focus
        Sim dz = sim(70, 1);
        dz.orbSlots = 3;
        mon(dz, "Jaw Worm", 40, 0, 0, 0);
        card(dz, "Zap", "SKILL", 1, false, "Channel 1 Lightning.");
        card(dz, "Defend", "SKILL", 1, false, "Gain 5 Block.");
        p = Planner.plan(dz, P);
        check("Defect: Zap on a quiet turn", names(dz, p).startsWith("Zap"), p.describe(dz));
        Sim dd = sim(70, 1);
        dd.orbSlots = 3;
        dd.orbs.add(new Sim.Orb("Lightning", 8, 3));
        mon(dd, "Jaw Worm", 15, 10, 1, 0);
        card(dd, "Dualcast", "SKILL", 1, false, "Evoke your next Orb twice.");
        card(dd, "Defend", "SKILL", 1, false, "Gain 5 Block.");
        p = Planner.plan(dd, P);
        check("Defect: Dualcast evokes Lightning twice to finish a 15 HP enemy", names(dd, p).startsWith("Dualcast") && p.kills == 1, p.describe(dd));
        Sim df = sim(70, 0);
        df.orbSlots = 3;
        for (int k = 0; k < 3; k++) df.orbs.add(new Sim.Orb("Frost", 5, 2));
        mon(df, "Jaw Worm", 40, 6, 1, 0);
        p = Planner.plan(df, P);
        check("Defect: three Frost orbs block 6 at the end of the turn", p.taken == 0, p.describe(df));
        Sim dg = sim(70, 1);
        dg.orbSlots = 3;
        dg.orbs.add(new Sim.Orb("Lightning", 8, 3));
        dg.orbs.add(new Sim.Orb("Lightning", 8, 3));
        mon(dg, "Jaw Worm", 40, 0, 0, 0);
        card(dg, "Defragment", "POWER", 1, false, "Gain 1 Focus.");
        card(dg, "Defend", "SKILL", 1, false, "Gain 5 Block.");
        p = Planner.plan(dg, P);
        check("Defect: Focus with orbs out beats a Defend on a quiet turn", names(dg, p).startsWith("Defragment"), p.describe(dg));
        // parser fixes: next-turn riders, Malaise X
        Effects om = parseRaw("Next turn, gain [E] [E].", "SKILL", 0);
        check("Outmaneuver's energy comes next turn", om.energy == 0 && om.nextEnergy == 2, om.energy + " / " + om.nextEnergy);
        Effects dr = parseRaw("Gain !B! Block. NL Next turn, gain !M! Block.", "SKILL", 4);
        check("Dodge and Roll: block next turn", dr.nextBlock == 4, "" + dr.nextBlock);
        Effects ml = parseRaw("Enemy loses X Strength. NL Apply X Weak. NL Exhaust.", "SKILL", 0);
        check("Malaise: X Strength and X Weak", ml.xStrDown && ml.xWeak, ml.xStrDown + " " + ml.xWeak);
        Effects ch = parseRaw("Channel 1 Frost for each enemy in combat. NL Exhaust.", "SKILL", 0);
        Effects ra = parseRaw("Channel 1 Lightning. NL Channel 1 Frost. NL Channel 1 Dark. NL Exhaust.", "SKILL", 0);
        check("orb text: Chill per enemy, Rainbow three orbs", "Frost".equals(ch.channelPerEnemy) && ra.channel.size() == 3, ra.channel.toString());

        // Watcher stances (the game gives the stance; card damage and intents already include it)
        Sim w1 = sim(70, 3);
        mon(w1, "Cultist", 48, 0, 0, 0);   // its buff turn: Wrath costs little now
        for (String d : new String[]{"Strike", "Strike", "Strike", "Defend", "Defend", "Defend", "Eruption", "Vigilance",
                "Empty Body", "Inner Peace"}) w1.deck.add(d);   // ways out of Wrath next turn
        card(w1, "Eruption", "ATTACK", 2, true, "Deal 9 damage. Enter Wrath.");
        card(w1, "Strike", "ATTACK", 1, true, "Deal 6 damage.");
        p = Planner.plan(w1, P);
        check("Watcher: Eruption then Strike in Wrath (9 + 12) on a quiet turn", names(w1, p).startsWith("Eruption") && p.dealt >= 21, p.describe(w1));
        Sim w2 = sim(40, 1);
        w2.stance = "Wrath";
        mon(w2, "Cultist", 48, 24, 1, 0);   // 12 doubled by Wrath, as the game shows it
        card(w2, "Empty Body", "SKILL", 1, false, "Gain 7 Block. Exit your Stance.");
        card(w2, "Defend", "SKILL", 1, false, "Gain 5 Block.");
        p = Planner.plan(w2, P);
        check("Watcher in Wrath facing a big hit: Empty Body leaves Wrath and blocks", names(w2, p).startsWith("Empty Body") && p.taken == 5, p.describe(w2));
        Sim w3 = sim(70, 1);
        mon(w3, "Jaw Worm", 40, 5, 1, 0);
        card(w3, "Blasphemy", "SKILL", 1, false, "Retain. Enter Divinity. Die next turn. Exhaust.");
        card(w3, "Defend", "SKILL", 1, false, "Gain 5 Block.");
        p = Planner.plan(w3, P);
        check("Watcher: no Blasphemy when the fight goes on", !names(w3, p).contains("Blasphemy"), p.describe(w3));
        Effects ip = parseRaw("If you are in *Calm, draw !M! cards, otherwise enter *Calm.", "SKILL", 3);
        check("Inner Peace: draw only in Calm, else enter Calm", "calm_draw".equals(ip.stanceCond) && ip.condN == 3 && ip.draw == 0, ip.stanceCond + " " + ip.condN + " " + ip.draw);
        // Suggest reasons cover orbs and stances
        java.util.List<String> rz = Planner.explain(dz, Planner.plan(dz, P), P);
        java.util.List<String> rw = Planner.explain(w1, Planner.plan(w1, P), P);
        check("reasons: Zap channels Lightning, Eruption enters Wrath",
              rz.get(0).contains("channels Lightning") && rw.get(0).contains("enters Wrath"), rz + " / " + rw);
        // ---- Workshop feedback (A_Stingray, 2026-10-26) and the bot's newer planner

        // "It won't play slimes even if that's the only option left": Slimed costs 1 and clears itself
        Sim sl = sim(70, 1);
        mon(sl, "Acid Slime (M)", 28, 7, 1, 0);
        card(sl, "Slimed", "STATUS", 1, false, "Exhaust.");
        p = Planner.plan(sl, P);
        check("feedback: plays Slimed when nothing else is left", names(sl, p).equals("Slimed") && !p.endTurn(P), p.describe(sl));
        Sim sl2 = sim(70, 2);
        mon(sl2, "Acid Slime (M)", 28, 7, 1, 0);
        card(sl2, "Slimed", "STATUS", 1, false, "Exhaust.");
        strike(sl2);
        p = Planner.plan(sl2, P);
        check("feedback: Strike and Slimed with 2 energy", names(sl2, p).contains("Slimed") && names(sl2, p).contains("Strike"), p.describe(sl2));

        // "prioritizes gaining excess block over dealing damage": block only what is coming, then hit
        Sim xb = sim(70, 2);
        mon(xb, "Cultist", 48, 6, 1, 0);
        strike(xb); defend(xb); defend(xb);
        p = Planner.plan(xb, P);
        check("feedback: 6 incoming -> one Defend and a Strike, not two Defends", names(xb, p).contains("Strike") && p.block == 5, p.describe(xb));
        Sim xb2 = sim(70, 3);
        mon(xb2, "Jaw Worm", 40, 0, 0, 0);
        strike(xb2); defend(xb2); defend(xb2);
        p = Planner.planForPlay(xb2, P, 20000);
        check("feedback: nothing incoming -> no Defend at all", !names(xb2, p).contains("Defend"), p.describe(xb2));

        // Gremlin Nob: every Skill gives it Strength
        Sim nob = sim(70, 2);
        mon(nob, "Gremlin Nob", 80, 14, 1, 0).nob = 2;
        strike(nob); defend(nob);
        p = Planner.plan(nob, P);
        check("Nob: attacks instead of a Defend that enrages it", names(nob, p).startsWith("Strike") && !names(nob, p).contains("Defend"), p.describe(nob));

        // endless fight (2600 turns vs a Nob): five Frost orbs must not make a Strike look bad
        Sim fr = sim(60, 3);
        fr.orbSlots = 5;
        for (int k = 0; k < 5; k++) fr.orbs.add(new Sim.Orb("Frost", 7, 4));
        Sim.Mon fn = mon(fr, "Gremlin Nob", 45, 16, 1, 0);
        fn.maxHp = 85;
        fn.nob = 2;
        strike(fr); defend(fr);
        Planner.Params defect = new Planner.Params();
        defect.damage = 0.55; defect.block = 2.0; defect.threat = 0;
        p = Planner.plan(fr, defect);
        check("Frost orbs vs Nob: still attacks (no endless fight)", names(fr, p).contains("Strike"), p.describe(fr));

        // Wrath look-ahead: Eruption into Hexaghost's Divider with no way out is refused
        Sim hx = sim(62, 3);
        hx.turn = 1;
        hx.elite = true;
        Sim.Mon hexa = mon(hx, "Hexaghost", 250, 0, 0, 0);
        hexa.intent = "UNKNOWN";
        card(hx, "Eruption", "ATTACK", 2, true, "Deal 9 damage. Enter Wrath.");
        strike(hx); defend(hx);
        for (String d : new String[]{"Strike", "Strike", "Strike", "Strike", "Defend", "Defend", "Defend", "Defend", "Eruption", "Vigilance"})
            hx.deck.add(d);
        p = Planner.plan(hx, P);
        check("Hexaghost turn 1: no Eruption into Divider", !names(hx, p).contains("Eruption"), p.describe(hx));

        // powers the text can't express are valued per turn left: Deva Form on a boss's quiet turn
        Sim dv = sim(70, 3);
        dv.turn = 1;
        dv.elite = true;
        mon(dv, "The Champ", 420, 0, 0, 0);   // a quiet turn (Defensive Stance)
        card(dv, "Deva Form", "POWER", 3, false, "Ethereal. At the start of your turn, gain and increase this gain by 1.");
        strike(dv); defend(dv);
        Planner.Params watcher = new Planner.Params();   // the Watcher's learned weights (it is the Watcher's card)
        watcher.block = 3.22; watcher.damage = 0.34; watcher.energy = 3.25; watcher.draw = 5.36; watcher.power = 4.06;
        watcher.kill = 3.89; watcher.hpCost = 4.79; watcher.strength = 4.8; watcher.threat = 1.2;
        p = Planner.plan(dv, watcher);
        check("Deva Form on a boss's quiet turn", names(dv, p).equals("Deva Form"), p.describe(dv));

        // Pressure Points: Mark then lose HP equal to Mark (block doesn't stop it)
        Sim pp = sim(70, 2);
        mon(pp, "Jaw Worm", 16, 5, 1, 0).block = 20;
        card(pp, "Pressure Points", "SKILL", 1, true, "Apply 8 Mark. ALL enemies lose HP equal to their Mark.");
        card(pp, "Pressure Points", "SKILL", 1, true, "Apply 8 Mark. ALL enemies lose HP equal to their Mark.");
        p = Planner.plan(pp, P);
        check("Pressure Points twice kills through 20 block", p.kills == 1, p.describe(pp));

        // the game's own damage numbers as a bonus (Pen Nib, Wrist Blade...): 6 + 14 kills a 20 HP enemy
        Sim gb = sim(70, 1);
        mon(gb, "Cultist", 20, 6, 1, 0);
        Sim.Card pen = strike(gb);
        pen.dmgBonus = new double[]{14};
        p = Planner.plan(gb, P);
        check("game damage bonus counts (Strike shown at 20 kills a 20 HP Cultist)", p.kills == 1, p.describe(gb));

        // the stance is part of the search state: Vigilance then Eruption differs from Eruption then Vigilance
        Sim ve = sim(70, 4);
        ve.stance = "Calm";
        mon(ve, "Jaw Worm", 60, 11, 1, 0);
        card(ve, "Vigilance", "SKILL", 2, false, "Gain 8 Block. Enter Calm.");
        card(ve, "Eruption", "ATTACK", 2, true, "Deal 9 damage. Enter Wrath.");
        p = Planner.plan(ve, P);
        check("Watcher in Calm: Eruption (+2 energy) then Vigilance back to Calm", names(ve, p).startsWith("Eruption") && names(ve, p).contains("Vigilance"), p.describe(ve));

        // the game's text: placeholders, NL, keyword stars and energy icons read like the database text
        check("normalize: Bash", Effects.normalize("Deal !D! damage. NL Apply !M! Vulnerable.", 8, 0, 2).equals("Deal 8 damage. Apply 2 Vulnerable."),
              Effects.normalize("Deal !D! damage. NL Apply !M! Vulnerable.", 8, 0, 2));
        check("normalize: Seeing Red's two energy icons", Effects.normalize("Gain [R] [R]. NL Exhaust.", 0, 0, 0).equals("Gain 2 Energy. Exhaust."),
              Effects.normalize("Gain [R] [R]. NL Exhaust.", 0, 0, 0));
        check("normalize: Eruption's keyword star", Effects.normalize("Deal !D! damage. NL Enter *Wrath.", 9, 0, 0).equals("Deal 9 damage. Enter Wrath."), "");
        Effects mal = Effects.of("Enemy loses X+1 Strength. Apply X+1 Weak. Exhaust.", "SKILL", -1, "Malaise");
        check("Malaise+ (X+1) still reads X Strength and X Weak", mal.xStrDown && mal.xWeak, "");

        // act 2: potions are for the boss -- no elite opener at good HP with a free slot
        PotionAdvisor.Context eo = new PotionAdvisor.Context();
        eo.elite = true; eo.turn = 1; eo.hp = 70; eo.maxHp = 80; eo.act = 2; eo.floorInAct = 5; eo.emptySlots = 1;
        check("act 2 elite at good HP with a free slot: keeps potions for the boss",
              PotionAdvisor.advise(pots("Strength Potion", "Fire Potion"), eo) == null, "");

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
