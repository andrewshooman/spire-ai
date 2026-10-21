package spireseer.brain;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * What a card does besides damage and block, read from its printed text (rawDescription with the
 * !M! placeholder the game uses for magicNumber). Works for modded cards too when they use the
 * standard wording. Damage and block themselves come from the game's own calculation.
 */
public final class Effects {
    public int vuln, weak, draw, energy, strength, tempStrength, dexterity, selfDamage, enemyStrDown;
    public boolean exhausts, isPower, randomTarget, aoe;
    // Silent and the cards the planner used to undervalue
    public int poison, poisonMult, shivs, intangible, doubleAttack, doubleSkill;
    public boolean poisonAoe, healUnblocked;
    public int hits = 1;

    private static final String N = "(!m!|\\d+)";
    private static final Pattern ENERGY_ICONS = Pattern.compile("gain ((?:\\[[rgbwe]\\] ?)+)");

    private static int find(String text, String regex, int magic) {
        Matcher m = Pattern.compile(regex).matcher(text);
        if (!m.find()) return 0;
        String s = m.group(1);
        if (s.equals("!m!")) return magic;
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    public static Effects parse(String raw, String type, int magic) {
        Effects e = new Effects();
        if (raw == null) return e;
        String t = raw.replace(" NL ", " ").replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
        e.vuln = find(t, "apply " + N + " vulnerable", magic);
        e.weak = find(t, "apply " + N + " weak", magic);
        int both = find(t, "apply " + N + " weak and vulnerable", magic);
        e.weak = Math.max(e.weak, both);
        e.vuln = Math.max(e.vuln, both);
        e.draw = find(t, "draw " + N + " card", magic);
        if (e.draw == 0 && t.contains("draw a card")) e.draw = 1;
        Matcher m = ENERGY_ICONS.matcher(t);
        if (m.find()) {
            String icons = m.group(1);
            for (int i = icons.indexOf('['); i >= 0; i = icons.indexOf('[', i + 1)) e.energy++;
        }
        boolean temporary = t.contains("at the end of this turn, lose") || t.contains("at the end of your turn, lose");
        boolean trigger = t.contains("whenever") || t.contains("at the start of your turn");
        int str = find(t, "gain " + N + " strength", magic);
        if (temporary) e.tempStrength = str;
        else if (!trigger) e.strength = str;
        int dex = find(t, "gain " + N + " dexterity", magic);
        if (!temporary && !trigger) e.dexterity = dex;
        e.selfDamage = find(t, "lose " + N + " hp", magic);
        e.enemyStrDown = find(t, "enemy loses " + N + " strength", magic);
        if (e.enemyStrDown == 0) e.enemyStrDown = find(t, "all enemies lose " + N + " strength", magic);
        e.isPower = "POWER".equals(type);
        e.randomTarget = t.contains("random enemy");
        e.aoe = t.contains("all enemies");
        e.poison = find(t, "apply " + N + " poison", magic);
        int flask = find(t, "poison to a random enemy " + N + " times", magic);   // Bouncing Flask
        if (flask > 0) e.poison *= flask;
        e.poisonAoe = Pattern.compile("poison(?: and (?:!m!|\\d+) weak)? to all enemies").matcher(t).find();
        if (t.matches(".*double (?:an|the) enemy's poison.*")) e.poisonMult = 2;
        if (t.matches(".*triple (?:an|the) enemy's poison.*")) e.poisonMult = 3;
        Matcher sv = Pattern.compile("add (!m!|\\d+|a) \\*?shivs?").matcher(t);
        if (sv.find()) e.shivs = sv.group(1).equals("a") ? 1 : sv.group(1).equals("!m!") ? magic : Integer.parseInt(sv.group(1));
        e.intangible = find(t, "gain " + N + " intangible", magic);
        Matcher dt = Pattern.compile("next (?:(!m!|\\d+) )?(attack|skill)s? (?:is|are) played twice").matcher(t);
        if (dt.find()) {
            int n = dt.group(1) == null ? 1 : dt.group(1).equals("!m!") ? magic : Integer.parseInt(dt.group(1));
            if (dt.group(2).equals("attack")) e.doubleAttack = n; else e.doubleSkill = n;
        }
        e.healUnblocked = t.contains("heal hp equal to unblocked damage");
        if (t.contains("damage twice")) e.hits = 2;
        int times = find(t, N + " times", magic);
        if (times > 0) e.hits = times;
        return e;
    }
}
