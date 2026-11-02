package spireseer.brain;

import java.util.HashMap;
import java.util.Map;

/**
 * What an enemy does next turn (A0, from the monster AI in the Slay the Spire reference) -- a port of Spire AI's
 * monsters.py. The game only shows this turn's intent; ending a turn in Wrath doubles next turn's hits too, and the
 * planner's threat term needs each enemy's usual attack.
 */
public final class Monsters {
    private Monsters() {}

    /** Average attack total (damage x hits) of each monster's attack moves at A0. */
    public static final Map<String, Double> TYPICAL = new HashMap<>();

    static {
        Object[] t = {"Acid Slime (L)", 13.5, "Acid Slime (M)", 8.5, "Acid Slime (S)", 3.0, "Awakened One", 26.4, "Bear", 13.5,
                "Blue Slaver", 9.5, "Book of Stabbing", 13.5, "Bronze Automaton", 29.5, "Bronze Orb", 8.0, "Byrd", 6.7,
                "Centurion", 15.0, "Chosen", 12.7, "Corrupt Heart", 32.0, "Cultist", 6.0, "Dagger", 17.0, "Darkling", 16.0,
                "Deca", 20.0, "Donu", 20.0, "Exploder", 9.0, "Fat Gremlin", 4.0, "Fungi Beast", 6.0, "Giant Head", 21.5,
                "Gremlin Leader", 18.0, "Gremlin Nob", 10.0, "Gremlin Wizard", 25.0, "Hexaghost", 9.3, "Lagavulin", 18.0,
                "Looter", 11.0, "Mad Gremlin", 4.0, "Mugger", 13.0, "Mystic", 8.0, "Nemesis", 31.5, "Orb Walker", 12.5,
                "Pointy", 10.0, "Red Slaver", 10.5, "Reptomancer", 28.0, "Repulsor", 11.0, "Romeo", 12.5, "Sentry", 9.0,
                "Shelled Parasite", 15.0, "Shield Gremlin", 6.0, "Slime Boss", 35.0, "Snake Plant", 21.0,
                "Sneaky Gremlin", 9.0, "Snecko", 11.5, "Spheric Guardian", 13.3, "Spike Slime (L)", 16.0,
                "Spike Slime (M)", 8.0, "Spike Slime (S)", 5.0, "Spiker", 7.0, "Spire Growth", 19.0, "Spire Shield", 23.0,
                "Spire Spear", 20.0, "Taskmaster", 7.0, "The Champ", 14.0, "The Collector", 18.0, "The Guardian", 20.3,
                "The Maw", 15.0, "Time Eater", 23.5, "Torch Head", 7.0, "Transient", 30.0, "Writhing Mass", 22.7,
                "Jaw Worm", 9.0, "Louse", 6.0, "Red Louse", 6.0, "Green Louse", 6.0, "Gremlin Leader ", 18.0};
        for (int i = 0; i < t.length; i += 2) TYPICAL.put((String) t[i], (Double) t[i + 1]);
    }

    /** Expected attack total of the monster's next move. curTotal = this turn's attack total (0 if not attacking). */
    public static double nextAttack(String name, String intent, int strength, int turn, int playerHp, double curTotal) {
        boolean attacking = curTotal > 0;
        if (intent == null) intent = "";
        switch (name) {
            case "Hexaghost":
                // Activate (turn 1) -> Divider: (your HP / 12 + 1) x 6; then Sear / Tackle / Inflame / Inferno
                if (turn <= 1 && !attacking) return (playerHp / 12 + 1) * 6.0;
                return 9.3 + 2 * strength;
            case "Slime Boss":
                // Goop Spray -> Preparing -> Slam 35, always in that order
                return !attacking && (intent.equals("UNKNOWN") || intent.equals("NONE") || intent.isEmpty()) ? 35.0 : 0.0;
            case "The Guardian":
                if (intent.equals("DEFEND")) return 32.0;
                if (intent.equals("DEBUFF") || intent.equals("STRONG_DEBUFF")) return 20.0;
                if (intent.equals("BUFF")) return 9.0;
                if (attacking && curTotal <= 10) return 16.0;   // Roll Attack -> Twin Slam
                return curTotal >= 30 ? 9.0 : 0.0;              // Fierce Bash -> Vent Steam; Whirlwind -> Charge Up
            case "Bronze Automaton":
                if (turn <= 1) return 14.0 + 2 * strength;
                if (!attacking) return intent.equals("STUN") ? 0.0 : 30.0 + strength;
                return 0.0;
            case "Lagavulin":
                return intent.equals("SLEEP") ? 0.0 : 18.0 + strength;
            case "Transient":
                return curTotal + 10.0;
            default:
                break;
        }
        if (name.equals("Gremlin Nob") && turn <= 1) return 14.0 + strength;   // Bellow -> Rush
        if (attacking) return curTotal;
        Double base = TYPICAL.get(name);
        if (base == null) return intent.equals("SLEEP") || intent.equals("STUN") ? 0.0 : 6.0;
        return base + strength;
    }
}
