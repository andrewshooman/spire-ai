package spireseer.brain;

import java.util.ArrayList;
import java.util.List;

/**
 * A combat turn as plain data: what the planner sees. Filled from the game by GameBridge, or by tests and the
 * parity harness. Field meanings follow Spire AI's planner.build.
 */
public final class Sim {

    /** One card in hand. */
    public static final class Card {
        public int index;              // position in hand
        public String id = "", name = "";
        public int upgrades;
        public String type = "ATTACK"; // ATTACK / SKILL / POWER / STATUS / CURSE
        public int cost;               // -1 = X cost; the cost this turn
        public boolean playable = true;   // the game would let you play it now
        public boolean targeted;       // needs an enemy target
        public Effects fx = new Effects();
        public boolean[] canTarget;    // per monster (null = every living enemy)
        // the game's own numbers, when known: added to the planner's estimate as a fixed bonus (relics like Pen Nib,
        // Wrist Blade, Strike Dummy; scaling cards like Rampage, Claw, Ritual Dagger)
        public double[] dmgBonus;      // per monster
        public double blockBonus;

        public String baseName() { return Effects.baseName(name); }
    }

    public static final class Mon {
        public int index;              // position in the game's monster list
        public String name = "";
        public String intent = "";
        public boolean alive = true;
        public int hp, maxHp, block;
        public int vuln, weak;
        public int atk, hits;          // per-hit damage of its intended attack and the number of hits (0 if not attacking)
        public int poison, artifact, thorns, sharp, malleable, curl, mark, strength, ritual, flight, nob;
        public int invincible;         // damage cap left this turn (0 = none)
        public boolean barricade, intangible, asleep, shifting, stasis;
        public double prio = 1.0;      // kill priority (filled from PRIORITY when 0)
    }

    /** A Defect orb: the game's evoke / passive amounts already include Focus. */
    public static final class Orb {
        public String kind = "";       // Lightning / Frost / Dark / Plasma
        public int evoke, passive;
        public Orb() {}
        public Orb(String kind, int evoke, int passive) { this.kind = kind; this.evoke = evoke; this.passive = passive; }
    }

    public final List<Card> hand = new ArrayList<>();
    public final List<Mon> mons = new ArrayList<>();
    public final List<Orb> orbs = new ArrayList<>();
    public final List<String> deck = new ArrayList<>();   // the master deck's card names (Strike count, Wrath exits)
    public int energy, playerBlock, hp, maxHp, strength, dexterity, turn = 1;
    public boolean playerWeak, playerFrail, intangible, barricade, elite, lightningAll;
    public int doubleAttack, doubleSkill, mantra, orbSlots, focus;
    public String stance = "Neutral";
    public int beatOfDeath, hex, curiosity;
    public int timeWarp = -1;          // Time Eater's card counter (-1 = not in this fight)
    public int stall;                  // turns in a row the enemies' HP did not move

    public Mon mon(int index) {
        for (Mon m : mons) if (m.index == index) return m;
        return null;
    }
}
