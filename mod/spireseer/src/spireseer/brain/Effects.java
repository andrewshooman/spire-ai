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
    // "Next turn, ..." riders: next turn's resources, not this turn's (Outmaneuver used to count as energy now)
    public int nextBlock, nextEnergy, nextDraw;
    // Malaise: X Strength loss and X Weak; After Image / Noxious Fumes as lasting value per turn
    public boolean xStrDown, xWeak;
    public int turnBlock, turnDamage;
    // Defect orbs
    public final java.util.List<String> channel = new java.util.ArrayList<>();   // orbs channeled, in order
    public String channelPerEnemy = "", channelX = "";   // Chill: one per enemy; Tempest: X of them
    public int evoke;                // evoke the next orb N times (-1 = X times: Multi-Cast)
    public boolean recursion;        // Recursion: evoke the next orb, then channel it again
    public int focus, orbSlots;      // Focus gained, orb slots gained (negative = lost)
    public boolean perOrb, lightningAll;   // Barrage: a hit per orb; Electrodynamics: Lightning hits all
    // Watcher stances: "Wrath" / "Calm" / "Divinity" / "Neutral" (exit); conditional: calm_draw (Inner Peace),
    // wrath_vuln (Indignation), if_attacking (Fear No Evil)
    public String stance = "", stanceCond = "";
    public int condN, mantra, wrathBlock;
    public boolean dieNextTurn;

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

    private static String cap(String s) {
        return s.substring(0, 1).toUpperCase(Locale.ROOT) + s.substring(1);
    }

    public static Effects parse(String raw, String type, int magic) {
        Effects e = new Effects();
        if (raw == null) return e;
        String t = raw.replace(" NL ", " ").replace("*", "").replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
        // "Next turn, ..." sentences are next turn's: take them out before reading this turn's effects
        StringBuilder now = new StringBuilder(), next = new StringBuilder();
        for (String sentence : t.split("(?<=\\.)\\s+")) (sentence.startsWith("next turn") ? next : now).append(sentence).append(' ');
        String nt = next.toString();
        t = now.toString().trim();
        if (!nt.isEmpty()) {
            e.nextBlock = find(nt, "gain " + N + " block", magic);
            e.nextDraw = find(nt, "draw " + N + " (?:additional )?cards?", magic);
            Matcher ne = ENERGY_ICONS.matcher(nt);
            if (ne.find()) {
                String icons = ne.group(1);
                for (int i = icons.indexOf('['); i >= 0; i = icons.indexOf('[', i + 1)) e.nextEnergy++;
            } else {
                e.nextEnergy = find(nt, "gain " + N + " energy", magic);
            }
        }
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
        e.xStrDown = t.contains("loses x strength") || t.contains("lose x strength");
        e.xWeak = t.contains("apply x weak");
        e.turnBlock = 3 * find(t, "whenever you play a card, gain " + N + " block", magic);
        e.turnDamage = 2 * find(t, "at the start of your turn, apply " + N + " poison to all enemies", magic);
        if (e.turnDamage > 0) e.poison = 0;   // Noxious Fumes: poison later, every turn -- not now
        // Defect orbs
        Matcher ch = Pattern.compile("channel " + N + " (lightning|frost|dark|plasma)(?! for each)").matcher(t);
        while (ch.find()) {
            int n = ch.group(1).equals("!m!") ? magic : Integer.parseInt(ch.group(1));
            for (int k = 0; k < n; k++) e.channel.add(cap(ch.group(2)));
        }
        Matcher pe = Pattern.compile("channel 1 (lightning|frost|dark|plasma) for each enemy").matcher(t);
        if (pe.find()) e.channelPerEnemy = cap(pe.group(1));
        Matcher cx = Pattern.compile("channel x (lightning|frost|dark|plasma)").matcher(t);
        if (cx.find()) e.channelX = cap(cx.group(1));
        if (t.contains("evoke your next orb x times")) e.evoke = -1;
        else if (t.contains("evoke your next orb twice")) e.evoke = 2;
        else if (t.contains("evoke your next orb")) e.evoke = 1;
        e.recursion = t.contains("channel the orb that was just evoked");
        e.focus = find(t, "gain " + N + " focus", magic);
        e.orbSlots = find(t, "gain " + N + " orb slot", magic) - find(t, "lose " + N + " orb slot", magic);
        e.perOrb = t.contains("for each channeled orb");
        e.lightningAll = t.contains("lightning now hits all enemies");
        if (t.contains("damage twice")) e.hits = 2;
        int times = find(t, N + " times", magic);
        if (times > 0) e.hits = times;
        if (t.contains("evoke your next orb")) e.hits = 1;   // "twice" / "X times" are about evoking, not hits
        // Watcher stances
        Matcher ip = Pattern.compile("if you are in calm, draw " + N + " cards?, otherwise enter calm").matcher(t);
        Matcher ig = Pattern.compile("if you are in wrath, apply " + N + " vulnerable to all enemies, otherwise enter wrath").matcher(t);
        if (ip.find()) {
            e.stance = "Calm"; e.stanceCond = "calm_draw"; e.condN = ip.group(1).equals("!m!") ? magic : Integer.parseInt(ip.group(1));
            e.draw = 0;
        } else if (ig.find()) {
            e.stance = "Wrath"; e.stanceCond = "wrath_vuln"; e.condN = ig.group(1).equals("!m!") ? magic : Integer.parseInt(ig.group(1));
            e.vuln = 0; e.aoe = false;
        } else if (t.contains("if the enemy intends to attack, enter calm")) {
            e.stance = "Calm"; e.stanceCond = "if_attacking";
        } else if (t.contains("exit your stance")) {
            e.stance = "Neutral";
        } else {
            Matcher en = Pattern.compile("\\benter (wrath|calm|divinity)").matcher(t);
            if (en.find()) e.stance = cap(en.group(1));
        }
        e.mantra = find(t, "gain " + N + " mantra", magic);
        e.dieNextTurn = t.contains("die next turn");
        e.wrathBlock = find(t, "if you are in wrath, gain " + N + " additional block", magic);
        return e;
    }
}
