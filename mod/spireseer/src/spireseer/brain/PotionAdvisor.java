package spireseer.brain;

import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * When to drink which potion -- a port of Spire AI's potion rules (policy._potion): never die holding
 * one, Block/heal on a boss's big hits, burst potions saved for The Champ's second half, power potions
 * early in boss fights (keeping one back), one opener in elites, defensive ones when low.
 */
public final class PotionAdvisor {

    public static final class Potion {
        public int slot;
        public String id = "", name = "";
        public boolean canUse = true, targetRequired;
    }

    public static final class Context {
        public boolean boss, elite;
        public int turn, hp, maxHp, taken, handSize, usedThisTurn, emptySlots, floorInAct, act = 1;
        public boolean attacksPlanned;
        public int champHp = -1, champMaxHp = -1;   // The Champ, when it is in the fight
        public double lowHp = 0.32;
    }

    public static final class Advice {
        public final Potion potion;
        public final String why;
        Advice(Potion p, String why) { this.potion = p; this.why = why; }
    }

    private static final Map<String, String> KIND = new HashMap<>();

    private static void kind(String k, String... names) {
        for (String n : names) KIND.put(key(n), k);
    }

    static {
        kind("defense", "Block Potion", "Ghost In A Jar", "Weak Potion", "Heart of Iron", "Essence of Steel",
                "Liquid Bronze", "Dexterity Potion", "Speed Potion");
        kind("buff", "Strength Potion", "Steroid Potion", "Flex Potion", "Cultist Potion", "Ancient Potion",
                "Focus Potion", "Bottled Miracle", "Essence of Darkness", "Potion of Capacity", "Stance Potion");
        kind("damage", "Fire Potion", "Explosive Potion", "Poison Potion", "Fear Potion", "Cunning Potion");
        kind("tempo", "Energy Potion", "Swift Potion", "Gambler's Brew", "Gamblers Brew", "Snecko Oil", "Distilled Chaos",
                "Duplication Potion", "Liquid Memories", "Attack Potion", "Skill Potion", "Power Potion",
                "Colorless Potion", "Elixir", "Elixir Potion", "Blessing of the Forge", "Ambrosia");
        kind("heal", "Blood Potion", "Regen Potion");
        kind("juice", "Fruit Juice");
        kind("brew", "Entropic Brew");
        kind("escape", "Smoke Bomb");
        kind("never", "Fairy in a Bottle", "Fairy Potion");
    }

    static final java.util.Set<String> PERMANENT = new java.util.HashSet<>(java.util.Arrays.asList(
            key("Strength Potion"), key("Cultist Potion"), key("Ancient Potion"), key("Fruit Juice"), key("Potion of Capacity")));

    static String key(String s) {
        return s == null ? "" : s.toLowerCase(Locale.ROOT).replaceAll("[^a-z]", "");
    }

    public static String kindOf(Potion p) {
        String k = KIND.get(key(p.id));
        if (k == null) k = KIND.get(key(p.name));
        return k == null ? "tempo" : k;
    }

    private static boolean wasted(Potion p, Context c) {
        String k = key(p.id) + "|" + key(p.name);
        if ((k.contains("flexpotion") || k.contains("steroidpotion")) && !c.attacksPlanned) return true;
        return k.contains("sneckooil") && c.handSize >= 8;
    }

    private static boolean permanent(Potion p) {
        return PERMANENT.contains(key(p.id)) || PERMANENT.contains(key(p.name));
    }

    private static Potion pick(List<Potion> usable, Context c, boolean keepOne, String... kinds) {
        for (String want : kinds)
            for (Potion p : usable) {
                if (!kindOf(p).equals(want) || wasted(p, c)) continue;
                if (want.equals("escape") && c.boss) continue;
                if (keepOne && usable.size() < 2 && !permanent(p)) continue;
                return p;
            }
        return null;
    }

    public static Advice advise(List<Potion> all, Context c) {
        List<Potion> usable = new java.util.ArrayList<>();
        for (Potion p : all) if (p.canUse && !kindOf(p).equals("never")) usable.add(p);
        if (usable.isEmpty()) return null;
        double hpFrac = c.hp / (double) Math.max(1, c.maxHp);
        Potion p;
        if ((p = pick(usable, c, false, "juice")) != null) return new Advice(p, "Fruit Juice is permanent max HP");
        if (c.emptySlots >= 2 && (p = pick(usable, c, false, "brew")) != null) return new Advice(p, "Entropic Brew refills empty slots");
        // no Smoke Bomb: escaping gives up the fight's rewards and, for the bot, aborted the run
        if (c.taken >= c.hp && (p = pick(usable, c, false, "defense", "heal", "damage", "buff", "tempo")) != null)
            return new Advice(p, "lethal damage incoming");
        if (c.boss && c.taken >= Math.max(20, 0.35 * c.hp) && (p = pick(usable, c, false, "defense", "heal")) != null)
            return new Advice(p, "boss big hit");
        boolean champ = c.champHp > 0;
        if (champ && c.champHp <= 0.55 * c.champMaxHp && (p = pick(usable, c, false, "damage", "buff", "tempo")) != null)
            return new Advice(p, "Champ's second half: burst it down");
        if (champ && c.turn <= 2) {
            for (Potion q : usable) if (permanent(q) && !wasted(q, c)) return new Advice(q, "permanent power now; burst potions wait for the Champ's second half");
            return null;
        }
        if (c.boss && c.turn <= 2 && (p = pick(usable, c, true, "buff", "tempo", "damage")) != null)
            return new Advice(p, "boss fight: potions are free power");
        if (c.boss && hpFrac < 0.5 && (p = pick(usable, c, false, "heal", "defense", "buff", "damage", "tempo")) != null)
            return new Advice(p, "boss fight, HP low");
        // act 2+: potions are for the boss (the bot's act 2 boss: 53% beaten arriving with 3 potions, 23-29% with
        // 1-2): open an elite only when hurt or when the belt is full
        boolean opener = hpFrac < 0.6 || (c.act <= 1 ? usable.size() >= 2 : c.emptySlots == 0);
        if (c.elite && c.turn == 1 && c.usedThisTurn == 0 && opener && c.floorInAct < 12
                && (p = pick(usable, c, false, "buff", "damage", "tempo")) != null)
            return new Advice(p, "elite opener");
        if (hpFrac < c.lowHp && c.taken > 0 && (p = pick(usable, c, false, "defense", "heal")) != null)
            return new Advice(p, "low HP");
        return null;
    }
}
