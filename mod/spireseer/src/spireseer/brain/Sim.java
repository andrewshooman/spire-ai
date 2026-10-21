package spireseer.brain;

import java.util.ArrayList;
import java.util.List;

/** A combat turn as plain data: what the planner sees. Filled from the game by GameBridge, or by tests. */
public final class Sim {

    /** One card in hand, with the game's own computed numbers (damage per target, block). */
    public static final class Card {
        public int index;              // position in hand
        public String id = "", name = "";
        public String type = "ATTACK"; // ATTACK / SKILL / POWER / STATUS / CURSE
        public int cost;               // -1 = X cost
        public boolean playable = true;
        public boolean targeted;       // needs an enemy target
        public boolean aoe;            // hits every enemy
        public boolean randomTarget;   // e.g. Sword Boomerang
        public int[] damage = new int[0];  // damage per hit against each monster (game-computed)
        public int hits = 1;
        public int block;
        public Effects fx = new Effects();
        public boolean[] canTarget = new boolean[0];
    }

    public static final class Mon {
        public int index;
        public String name = "";
        public int hp, maxHp, block;
        public int vuln, weak;         // turns of Vulnerable / Weak already on it
        public int intentDmg, intentHits; // per-hit damage of its intended attack (0 if not attacking)
        public int thorns;             // Thorns / Sharp Hide
        public int poison;             // Poison on it (ticks at the start of its turn)
        public int strength;
        public boolean alive = true;
        public double prio = 1.0;      // kill priority
    }

    /** A Defect orb: the game's evoke / passive amounts already include Focus. */
    public static final class Orb {
        public String kind = "";       // Lightning / Frost / Dark / Plasma
        public int evoke, passive;
        public Orb() {}
        public Orb(String kind, int evoke, int passive) { this.kind = kind; this.evoke = evoke; this.passive = passive; }
    }

    public final List<Card> hand = new ArrayList<>();
    public final List<Orb> orbs = new ArrayList<>();
    public int orbSlots, focus;
    public boolean lightningAll;       // Electrodynamics
    public final List<Mon> mons = new ArrayList<>();
    public int energy, playerBlock, hp, maxHp, strength, turn = 1;
    public boolean playerWeak;         // our attacks deal 25% less (already in game-computed damage)
    public boolean elite;              // elite or boss room
    public int stall;                  // turns in a row the enemies' HP did not move
    public boolean intangible;         // the player is Intangible this turn (hits deal 1)
    public int doubleAttack, doubleSkill;   // Double Tap / Burst already active

    public Mon mon(int index) {
        for (Mon m : mons) if (m.index == index) return m;
        return null;
    }
}
