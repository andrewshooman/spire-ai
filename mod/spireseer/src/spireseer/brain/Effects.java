package spireseer.brain;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * What a card does, read from its printed text -- a line-by-line port of Spire AI's cards.parse_text plus its
 * tables for the cards the text can't express (POWER_TURN, SETUP_TURN, CARD_FIX). It reads the same English the
 * card database has ("Deal 6 damage. Apply 2 Vulnerable."); in the game, {@link #normalize} turns a card's
 * rawDescription (!D!, !B!, !M!, NL, energy icons) into that text. The parity test checks every card text in the
 * database against the Python parser's output field by field.
 */
public final class Effects {
    public int damage, hits = 1, block, vuln, weak, draw, energy, strength, tempStrength, hpLoss;
    public boolean aoe, randomTarget, aoeDebuff, exhaust, power, xCost;
    public int strengthMult = 1;
    public boolean blockScaling, doubleBlock, doubleStrength, conditionalStrength;
    public int selfVuln, selfWeak, selfFrail, enemyStrDown, enemyStrDownTemp, poison, perStrike;
    public String exhaustHand = "";
    public int blockPerExhaust, damagePerExhaust;
    public String discardHand = "";               // Unload: "nonattack"; Calculated Gamble / Storm of Steel: "all"
    public boolean drawPerDiscard, shivPerDiscard;
    public boolean ifVuln;
    public double perTurnStrength, perTurnBlock, perTurnEnergy, perTurnDamage, perTurnSingle, perTurnDraw;
    public int thorns, dex;
    public boolean known;
    public int shivs, poisonMult, intangible, doubleAttack, doubleSkill;
    public boolean healUnblocked;
    // Defect orbs
    public List<String> channel = new ArrayList<>();
    public String channelPerEnemy = "", channelX = "";
    public int evoke, focus, orbSlots;
    public boolean recursion, perOrb, lightningAll;
    // "Next turn, ..." riders
    public double nextBlock, nextEnergy, nextDraw;
    public boolean xStrDown, xWeak;
    // Watcher
    public String stance = "", stanceCond = "";
    public int condN, mantra, wrathBlock;
    public boolean dieNextTurn, endsTurn;
    // cards with their own rules
    public int mark, judgment;
    public boolean wallop, bulletTime, vault, setup;

    public Effects copy() {
        Effects n = new Effects();
        n.damage = damage; n.hits = hits; n.block = block; n.vuln = vuln; n.weak = weak; n.draw = draw; n.energy = energy;
        n.strength = strength; n.tempStrength = tempStrength; n.hpLoss = hpLoss; n.aoe = aoe; n.randomTarget = randomTarget;
        n.aoeDebuff = aoeDebuff; n.exhaust = exhaust; n.power = power; n.xCost = xCost; n.strengthMult = strengthMult;
        n.blockScaling = blockScaling; n.doubleBlock = doubleBlock; n.doubleStrength = doubleStrength;
        n.conditionalStrength = conditionalStrength; n.selfVuln = selfVuln; n.selfWeak = selfWeak; n.selfFrail = selfFrail;
        n.enemyStrDown = enemyStrDown; n.enemyStrDownTemp = enemyStrDownTemp; n.poison = poison; n.perStrike = perStrike;
        n.exhaustHand = exhaustHand; n.blockPerExhaust = blockPerExhaust; n.damagePerExhaust = damagePerExhaust;
        n.discardHand = discardHand; n.drawPerDiscard = drawPerDiscard; n.shivPerDiscard = shivPerDiscard;
        n.ifVuln = ifVuln; n.perTurnStrength = perTurnStrength; n.perTurnBlock = perTurnBlock;
        n.perTurnEnergy = perTurnEnergy; n.perTurnDamage = perTurnDamage; n.perTurnSingle = perTurnSingle;
        n.perTurnDraw = perTurnDraw; n.thorns = thorns; n.dex = dex; n.known = known; n.shivs = shivs;
        n.poisonMult = poisonMult; n.intangible = intangible; n.doubleAttack = doubleAttack; n.doubleSkill = doubleSkill;
        n.healUnblocked = healUnblocked; n.channel = new ArrayList<>(channel); n.channelPerEnemy = channelPerEnemy;
        n.channelX = channelX; n.evoke = evoke; n.focus = focus; n.orbSlots = orbSlots; n.recursion = recursion;
        n.perOrb = perOrb; n.lightningAll = lightningAll; n.nextBlock = nextBlock; n.nextEnergy = nextEnergy;
        n.nextDraw = nextDraw; n.xStrDown = xStrDown; n.xWeak = xWeak; n.stance = stance; n.stanceCond = stanceCond;
        n.condN = condN; n.mantra = mantra; n.wrathBlock = wrathBlock; n.dieNextTurn = dieNextTurn; n.endsTurn = endsTurn;
        n.mark = mark; n.judgment = judgment; n.wallop = wallop; n.bulletTime = bulletTime; n.vault = vault; n.setup = setup;
        return n;
    }

    // ---------------------------------------------------------------- regex helpers (Python semantics)

    private static final Map<String, Pattern> CACHE = new HashMap<>();

    private static Pattern pat(String re, boolean ci) {
        String k = (ci ? "i:" : "s:") + re;
        Pattern p = CACHE.get(k);
        if (p == null) {
            p = Pattern.compile(re, ci ? Pattern.CASE_INSENSITIVE : 0);
            CACHE.put(k, p);
        }
        return p;
    }

    /** Python cards._int: case-insensitive search, first group as int. */
    static int num(String re, String text) {
        return num(re, text, 0);
    }

    static int num(String re, String text, int dflt) {
        Matcher m = pat(re, true).matcher(text);
        return m.find() ? Integer.parseInt(m.group(1)) : dflt;
    }

    static boolean has(String re, String text, boolean ci) {
        return pat(re, ci).matcher(text).find();
    }

    private static final Pattern SENTENCE = Pattern.compile("(?<=\\.)\\s+");
    private static final Pattern TRIGGER = Pattern.compile("^(?:At the (?:start|end) of|Whenever|Every time|If this card is Exhausted|"
            + "When(?:ever)? you (?:draw|lose|gain|play))", Pattern.CASE_INSENSITIVE);

    private static String[] sentences(String t) {
        return SENTENCE.split(t, -1);
    }

    private static String title(String s) {
        return s.isEmpty() ? s : s.substring(0, 1).toUpperCase(Locale.ROOT) + s.substring(1).toLowerCase(Locale.ROOT);
    }

    // ---------------------------------------------------------------- the parser (cards.parse_text)

    public static Effects parseText(String text, String cardType, int cost) {
        Effects e = new Effects();
        String full = text == null ? "" : text;
        e.known = !full.isEmpty();
        e.power = "POWER".equals(cardType);
        e.xCost = cost == -1;
        // _split_triggers: powers and "whenever" riders act later
        StringBuilder nowB = new StringBuilder(), laterB = new StringBuilder();
        for (String s : sentences(full)) {
            StringBuilder b = TRIGGER.matcher(s.trim()).find() ? laterB : nowB;
            if (b.length() > 0) b.append(' ');
            b.append(s);
        }
        String t = nowB.toString(), later = laterB.toString();
        StringBuilder nxtB = new StringBuilder(), restB = new StringBuilder();
        for (String x : sentences(t)) {
            boolean isNext = pat("Next turn", true).matcher(x).lookingAt();
            StringBuilder b = isNext ? nxtB : restB;
            if (b.length() > 0) b.append(' ');
            b.append(x);
        }
        String nxt = nxtB.toString();
        if (!nxt.isEmpty()) {
            t = restB.toString();
            e.nextBlock = num("gain (\\d+) Block", nxt);
            int ne = num("gain (\\d+) Energy", nxt);
            e.nextEnergy = ne != 0 ? ne : (has("gain \\[[RGBWE]\\]", nxt, true) ? 1 : 0);
            e.nextDraw = num("draw (\\d+) (?:additional )?cards?", nxt);
        }
        StringBuilder et = new StringBuilder();
        for (String s : sentences(later))
            if (has("(?:start|end) of your turn", s, true)) {
                if (et.length() > 0) et.append(' ');
                et.append(s);
            }
        String eachTurn = et.toString();
        e.perTurnStrength = num("gain (\\d+) Strength", eachTurn);
        e.perTurnBlock = num("gain (\\d+) Block", eachTurn);
        e.perTurnEnergy = num("gain (\\d+) Energy", eachTurn);
        e.perTurnDamage = num("deal (\\d+) damage to ALL enemies", eachTurn);
        e.perTurnDamage += 2 * num("apply (\\d+) Poison to ALL enemies", eachTurn);       // Noxious Fumes
        e.perTurnBlock += 3 * num("Whenever you play a card, gain (\\d+) Block", later);  // After Image
        e.thorns = num("deal (\\d+) damage back", later);
        Matcher m = pat("Gain (\\d+) (Vulnerable|Weak|Frail)", false).matcher(full);
        if (m.find()) {
            int n = Integer.parseInt(m.group(1));
            if (m.group(2).equals("Vulnerable")) e.selfVuln = n;
            else if (m.group(2).equals("Weak")) e.selfWeak = n;
            else e.selfFrail = n;
        }
        e.exhaust = has("\\bExhaust\\.", t, false);
        e.mark = num("Apply (\\d+) Mark", t);
        e.judgment = num("has (\\d+) or less HP, set", t);
        e.wallop = has("Gain Block equal to unblocked damage", t, true);
        e.bulletTime = has("Reduce the cost of all cards in your hand to 0", t, true);
        e.vault = has("Take an extra turn", t, true);
        e.damage = num("Deal (\\d+) damage", t);
        e.ifVuln = has("If the enemy (?:has|is) Vulnerable", t, true);
        e.perStrike = num("Deals (\\d+) additional damage for ALL your cards containing", t);
        if (has("Exhaust your hand", t, true)) {
            e.exhaustHand = "all";
            e.damagePerExhaust = num("Deal (\\d+) damage for each card Exhausted", t);
            e.damage = 0;
        } else if (has("Exhaust all non-Attack cards in your hand", t, true)) {
            e.exhaustHand = "nonattack";
            e.blockPerExhaust = num("Gain (\\d+) Block for each card Exhausted", t);
        }
        if (has("Discard all non-Attack cards", t, true)) {
            e.discardHand = "nonattack";
        } else if (has("Discard your hand", t, true)) {
            e.discardHand = "all";
            e.drawPerDiscard = has("draw that many", t, true);
            e.shivPerDiscard = has("Shiv[^.]*for each card discarded", t, true);
        }
        m = pat("(?:Enemy|ALL enemies) loses? (\\d+) Strength( this turn)?", true).matcher(t);
        if (m.find()) {
            if (m.group(2) != null) e.enemyStrDownTemp = Integer.parseInt(m.group(1));
            else e.enemyStrDown = Integer.parseInt(m.group(1));
            e.aoeDebuff = e.aoeDebuff || m.group(0).contains("ALL enemies");
        }
        e.poison = num("Apply (\\d+) Poison", t);
        e.intangible = num("Gain (\\d+) Intangible", t);
        m = pat("next (\\d+ )?(Attack|Skill)s? (?:is|are) played twice", true).matcher(t);
        if (m.find()) {
            int n = m.group(1) != null ? Integer.parseInt(m.group(1).trim()) : 1;
            if (m.group(2).equalsIgnoreCase("attack")) e.doubleAttack = n;
            else e.doubleSkill = n;
        }
        e.healUnblocked = has("Heal HP equal to unblocked damage", t, true);
        m = pat("(Double|Triple) the enemy's Poison", true).matcher(t);
        if (m.find()) e.poisonMult = m.group(1).equalsIgnoreCase("double") ? 2 : 3;
        m = pat("Poison to a random enemy (\\d+) times", true).matcher(t);   // Bouncing Flask
        if (m.find()) {
            e.poison *= Integer.parseInt(m.group(1));
            e.randomTarget = true;
        }
        m = pat("Add (\\d+|a) \\*?Shivs?\\b", true).matcher(t);
        if (m.find()) e.shivs = m.group(1).equalsIgnoreCase("a") ? 1 : Integer.parseInt(m.group(1));
        if (has("damage equal to your Block", t, true)) e.blockScaling = true;
        if (has("damage (?:\\w+ )*X times", t, true)) e.hits = 0;   // resolved to energy at play time
        else if (has("\\btwice\\b", t, true)) e.hits = 2;
        else e.hits = Math.max(1, num("damage[^.]*?(\\d+) times", t, 1));
        e.aoe = has("to ALL enemies", t, false);
        e.randomTarget = has("random enemy", t, true);
        Matcher bl = pat("Gain (\\d+) Block", false).matcher(t);
        e.block = 0;
        while (bl.find()) e.block += Integer.parseInt(bl.group(1));
        e.doubleBlock = has("Double your Block", t, true);
        m = pat("Apply (\\d+) Weak and Vulnerable", false).matcher(t);
        if (m.find()) {
            e.vuln = e.weak = Integer.parseInt(m.group(1));
        } else {
            e.vuln = num("Apply (\\d+) Vulnerable", t);
            e.weak = num("Apply (\\d+) Weak", t);
        }
        e.aoeDebuff = e.aoeDebuff || has("(?:Vulnerable|Weak)[^.]*to ALL enemies", t, false);
        // Watcher stances
        m = pat("If you are in Calm, draw (\\d+) cards?, otherwise enter Calm", true).matcher(t);
        if (m.find()) {
            e.stance = "Calm"; e.stanceCond = "calm_draw"; e.condN = Integer.parseInt(m.group(1));
            e.draw = 0;
        }
        m = pat("If you are in Wrath, apply (\\d+) Vulnerable to ALL enemies, otherwise enter Wrath", true).matcher(t);
        if (m.find()) {
            e.stance = "Wrath"; e.stanceCond = "wrath_vuln"; e.condN = Integer.parseInt(m.group(1));
            e.vuln = 0;
        }
        if (e.stance.isEmpty()) {
            if (has("If the enemy intends to Attack, enter Calm", t, true)) {
                e.stance = "Calm"; e.stanceCond = "if_attacking";
            } else if (has("Exit your Stance", t, true)) {
                e.stance = "Neutral";
            } else {
                m = pat("\\bEnter (Wrath|Calm|Divinity)", true).matcher(t);
                if (m.find()) e.stance = title(m.group(1));
            }
        }
        e.mantra = num("Gain (\\d+) Mantra", t);
        e.dieNextTurn = has("Die next turn", t, true);
        e.endsTurn = has("End your turn", t, true);
        m = pat("Gain (\\d+) Block\\. If you are in Wrath, gain (\\d+) additional Block", true).matcher(t);
        if (m.find()) e.wrathBlock = Integer.parseInt(m.group(2));
        e.xStrDown = has("loses? X(?:\\+1)? Strength", t, true);   // Malaise+ reads "X+1"
        e.xWeak = has("Apply X(?:\\+1)? Weak", t, true);
        e.draw = num("Draw (\\d+) cards?", t);
        e.energy = num("Gain (\\d+) Energy", t);
        // Follow-Up style riders depend on the previous card: not counted (the database text the bot reads has no
        // number there either)
        if (has("If the last card played this combat was an? \\w+, gain \\d+ Energy", t, true)) e.energy = 0;
        if (has("At the end of this turn, lose", full, false)) {
            e.tempStrength = num("Gain (\\d+) Strength", t);
        } else if (has("If the enemy intends to attack, gain (\\d+) Strength", t, false)) {
            e.conditionalStrength = true;
            e.strength = num("gain (\\d+) Strength", t);
        } else {
            e.strength = num("Gain (\\d+) Strength", t);
        }
        e.doubleStrength = has("Double your Strength", t, true);
        e.strengthMult = num("Strength affects this card (\\d+) times", t, 1);
        e.hpLoss = num("Lose (\\d+) HP", t);
        e.dex = num("Gain (\\d+) Dexterity", t);
        // Defect orbs
        m = pat("Channel (\\d+) (Lightning|Frost|Dark|Plasma)(?! for each)", true).matcher(t);
        while (m.find()) for (int k = 0; k < Integer.parseInt(m.group(1)); k++) e.channel.add(title(m.group(2)));
        m = pat("Channel 1 (Lightning|Frost|Dark|Plasma) for each enemy", true).matcher(t);
        if (m.find()) e.channelPerEnemy = title(m.group(1));
        m = pat("Channel X (Lightning|Frost|Dark|Plasma)", true).matcher(t);
        if (m.find()) e.channelX = title(m.group(1));
        if (has("Evoke your next Orb X times", t, true)) e.evoke = -1;
        else if (has("Evoke your next Orb twice", t, true)) e.evoke = 2;
        else if (has("Evoke your next Orb", t, true)) e.evoke = 1;
        e.recursion = has("Channel the Orb that was just Evoked", t, true);
        e.focus = num("Gain (\\d+) Focus", t);
        e.orbSlots = num("Gain (\\d+) Orb slot", t) - num("Lose (\\d+) Orb slot", t);
        e.perOrb = has("for each Channeled Orb", t, true);
        e.lightningAll = has("Lightning now hits ALL enemies", full, true);
        if (e.blockPerExhaust != 0) e.block = 0;
        if ("calm_draw".equals(e.stanceCond)) e.draw = 0;   // Inner Peace draws only when already in Calm
        return e;
    }

    // ---------------------------------------------------------------- tables (cards.POWER_TURN / SETUP_TURN / CARD_FIX)

    static final Map<String, double[]> POWER_TURN = new HashMap<>();   // {dmg, single, block, energy, draw, str, nextBlock}
    static final Map<String, double[]> SETUP_TURN = new HashMap<>();
    static final int DMG = 0, SINGLE = 1, BLOCK = 2, ENERGY = 3, DRAW = 4, STR = 5, NEXT_BLOCK = 6;

    private static void pt(Map<String, double[]> m, String name, Object... kv) {
        double[] v = new double[7];
        java.util.Arrays.fill(v, Double.NaN);
        for (int i = 0; i < kv.length; i += 2) v[(Integer) kv[i]] = ((Number) kv[i + 1]).doubleValue();
        m.put(name, v);
    }

    static {
        Map<String, double[]> p = POWER_TURN;
        // Ironclad
        pt(p, "Barricade", BLOCK, 4); pt(p, "Brutality", DRAW, 1, BLOCK, -1); pt(p, "Corruption", ENERGY, 1.5);
        pt(p, "Dark Embrace", DRAW, 0.8); pt(p, "Evolve", DRAW, 0.3); pt(p, "Feel No Pain", BLOCK, 2.5);
        pt(p, "Fire Breathing", DMG, 2); pt(p, "Juggernaut", SINGLE, 7); pt(p, "Rupture", STR, 0.3);
        // Silent
        pt(p, "A Thousand Cuts", DMG, 4); pt(p, "Accuracy", SINGLE, 5); pt(p, "After Image", BLOCK, 4);
        pt(p, "Envenom", SINGLE, 4); pt(p, "Infinite Blades", SINGLE, 4); pt(p, "Tools of the Trade", DRAW, 0.8);
        pt(p, "Well-Laid Plans", DRAW, 0.6); pt(p, "Wraith Form", BLOCK, -1.5, NEXT_BLOCK, 12);
        // Defect
        pt(p, "Buffer", NEXT_BLOCK, 10); pt(p, "Creative AI", SINGLE, 4); pt(p, "Echo Form", SINGLE, 7);
        pt(p, "Heatsinks", DRAW, 0.4); pt(p, "Hello World", SINGLE, 3); pt(p, "Loop", SINGLE, 3);
        pt(p, "Machine Learning", DRAW, 1); pt(p, "Self Repair", BLOCK, 1.5); pt(p, "Static Discharge", SINGLE, 2);
        pt(p, "Storm", SINGLE, 2);
        // Watcher
        pt(p, "Battle Hymn", SINGLE, 7); pt(p, "Deva Form", ENERGY, 2); pt(p, "Devotion", ENERGY, 0.8);
        pt(p, "Establishment", ENERGY, 0.5); pt(p, "Fasting", ENERGY, -1); pt(p, "Foresight", DRAW, 0.6);
        pt(p, "Like Water", BLOCK, 3); pt(p, "Master Reality", DRAW, 0.3); pt(p, "Mental Fortress", BLOCK, 6);
        pt(p, "Nirvana", BLOCK, 2); pt(p, "Rushdown", DRAW, 1.5); pt(p, "Study", DRAW, 1.2);
        // colorless
        pt(p, "Magnetism", SINGLE, 3); pt(p, "Mayhem", SINGLE, 5); pt(p, "Panache", DMG, 4); pt(p, "Sadistic Nature", SINGLE, 2);
        Map<String, double[]> s = SETUP_TURN;
        pt(s, "Alpha", DMG, 6); pt(s, "Conjure Blade", SINGLE, 5); pt(s, "Collect", ENERGY, 0.8); pt(s, "Talk to the Hand", BLOCK, 3);
    }

    private static void fix(Effects e, String base) {
        switch (base) {
            case "Scrawl": e.draw = 4; break;                                         // draw until the hand is full
            case "Chaos": e.channel = new ArrayList<>(java.util.Collections.singletonList("Lightning")); break;
            case "Thunder Strike": e.damage = 7; e.hits = 3; e.randomTarget = true; break;   // 7 per Lightning (~3)
            case "Blizzard": e.damage = 6; e.hits = 1; e.aoe = true; break;          // 2 per Frost channeled (~3)
            case "Simmering Fury": e.nextDraw = 2; e.nextEnergy = 1; break;
            case "Wreath of Flame": e.tempStrength = 4; break;
            case "Phantasmal Killer": e.nextEnergy = 2; break;
            default: break;
        }
    }

    static final java.util.Set<String> FIXED = new java.util.HashSet<>(java.util.Arrays.asList(
            "Scrawl", "Chaos", "Thunder Strike", "Blizzard", "Simmering Fury", "Wreath of Flame", "Phantasmal Killer"));

    private static final Map<String, Effects> MEMO = new HashMap<>();

    /** cards._effects: the parse plus the tables, by the card's base name (no "+"). Cached; callers must not change it. */
    public static Effects of(String text, String cardType, int cost, String baseName) {
        String key = cardType + "|" + cost + "|" + baseName + "|" + text;
        Effects hit = MEMO.get(key);
        if (hit != null) return hit;
        Effects e = parseText(text, cardType, cost);
        double[] table = "POWER".equals(cardType) ? POWER_TURN.get(baseName) : SETUP_TURN.get(baseName);
        if (table != null || FIXED.contains(baseName)) {
            e = e.copy();
            fix(e, baseName);
            if (table != null) {
                if (!Double.isNaN(table[DMG])) e.perTurnDamage = table[DMG];
                if (!Double.isNaN(table[SINGLE])) e.perTurnSingle = table[SINGLE];
                if (!Double.isNaN(table[BLOCK])) e.perTurnBlock = table[BLOCK];
                if (!Double.isNaN(table[ENERGY])) e.perTurnEnergy = table[ENERGY];
                if (!Double.isNaN(table[DRAW])) e.perTurnDraw = table[DRAW];
                if (!Double.isNaN(table[STR])) e.perTurnStrength = table[STR];
                if (!Double.isNaN(table[NEXT_BLOCK])) e.nextBlock = table[NEXT_BLOCK];
                if (!"POWER".equals(cardType)) e.setup = true;
            }
        }
        if (MEMO.size() > 5000) MEMO.clear();
        MEMO.put(key, e);
        return e;
    }

    public static String baseName(String name) {
        return name == null ? "" : name.replaceAll("\\+\\d*$", "").trim();
    }

    // ---------------------------------------------------------------- game text -> database text

    private static final Pattern GLYPH_RUN = Pattern.compile("\\[(?:[A-Z]|Energy)\\](?: \\[(?:[A-Z]|Energy)\\])*");

    /**
     * A card's rawDescription with its numbers filled in, in the database's wording: !D! / !B! / !M! replaced,
     * NL and keyword markers removed, energy icons spelled out the way sts-bench's card_db does ("Gain [R] [R]"
     * -> "Gain 2 Energy").
     */
    public static String normalize(String raw, int damage, int block, int magic) {
        if (raw == null) return "";
        String t = raw.replace("!D!", String.valueOf(damage)).replace("!B!", String.valueOf(block))
                .replace("!M!", String.valueOf(magic));
        t = t.replaceAll("\\s*\\bNL\\b\\s*", " ").replaceAll("[a-z_]+:", "").replace("*", "");
        // energy icons: the game writes [E] (current character); the database writes [R]/[G]/[B]/[W]
        Matcher m = GLYPH_RUN.matcher(t);
        StringBuffer out = new StringBuffer();
        while (m.find()) {
            String run = m.group();
            int count = run.length() - run.replace("[", "").length();
            String before = t.substring(0, m.start()).replaceAll("\\s+$", "").toLowerCase(Locale.ROOT);
            String after = t.substring(m.end()).replaceAll("^\\s+", "").toLowerCase(Locale.ROOT);
            String rep = (before.endsWith(" x") || before.endsWith("x+1") || before.endsWith("less")
                    || before.endsWith("additional") || after.startsWith("equal to")) ? "Energy" : count + " Energy";
            m.appendReplacement(out, Matcher.quoteReplacement(rep));
        }
        m.appendTail(out);
        return out.toString().replaceAll("\\s+", " ").trim();
    }
}
