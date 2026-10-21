package spireseer;

import spireseer.brain.Effects;
import spireseer.brain.Planner;
import spireseer.brain.PotionAdvisor;
import spireseer.brain.Sim;
import basemod.ReflectionHacks;
import com.megacrit.cardcrawl.actions.GameActionManager;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardQueueItem;
import com.megacrit.cardcrawl.characters.AbstractPlayer;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.potions.PotionSlot;
import com.megacrit.cardcrawl.powers.AbstractPower;
import com.megacrit.cardcrawl.relics.AbstractRelic;
import com.megacrit.cardcrawl.rooms.AbstractRoom;
import com.megacrit.cardcrawl.rooms.MonsterRoomBoss;
import com.megacrit.cardcrawl.rooms.MonsterRoomElite;
import com.megacrit.cardcrawl.ui.panels.EnergyPanel;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

/** Reads the live combat into the brain's model and performs its choices (the same calls a click makes). */
public final class GameBridge {

    /** Kill priorities from the playbook (Spire AI planner.PRIORITY). */
    private static final Map<String, Double> PRIORITY = new HashMap<>();

    static {
        String[][] p = {{"Red Slaver", "1.5"}, {"Donu", "1.6"}, {"Dagger", "1.5"}, {"Mystic", "1.4"},
                {"Gremlin Wizard", "1.3"}, {"Bronze Orb", "0.55"}, {"Sentry", "1.1"}, {"Torch Head", "0.6"},
                {"Darkling", "0.8"}, {"Repulsor", "0.9"}, {"Spiker", "0.7"}, {"Gremlin Leader", "1.1"},
                {"Orb Walker", "1.3"}, {"Exploder", "1.3"}, {"Transient", "0.3"}};
        for (String[] kv : p) PRIORITY.put(kv[0], Double.parseDouble(kv[1]));
    }

    private GameBridge() {}

    /** True when the game waits for the player in combat with nothing in flight. */
    public static boolean ready() {
        if (CardCrawlGame.mode != CardCrawlGame.GameMode.GAMEPLAY || !AbstractDungeon.isPlayerInDungeon()) return false;
        AbstractPlayer pl = AbstractDungeon.player;
        AbstractRoom room = AbstractDungeon.getCurrRoom();
        if (pl == null || room == null || room.phase != AbstractRoom.RoomPhase.COMBAT) return false;
        if (AbstractDungeon.isScreenUp || pl.isDead || pl.endTurnQueued) return false;
        GameActionManager am = AbstractDungeon.actionManager;
        if (am.phase != GameActionManager.Phase.WAITING_ON_USER || !am.actions.isEmpty() || !am.cardQueue.isEmpty()) return false;
        if (AbstractDungeon.getMonsters() == null || AbstractDungeon.getMonsters().areMonstersBasicallyDead()) return false;
        return AbstractDungeon.overlayMenu != null && AbstractDungeon.overlayMenu.endTurnButton.enabled;
    }

    /** The player is in the middle of a click or drag: never act over it. */
    public static boolean playerBusy() {
        AbstractPlayer pl = AbstractDungeon.player;
        return pl != null && (pl.isDraggingCard || pl.inSingleTargetMode);
    }

    private static int playerPower(String id) {
        AbstractPower p = AbstractDungeon.player.getPower(id);
        return p == null ? 0 : p.amount;
    }

    private static int amount(AbstractMonster m, String id) {
        AbstractPower p = m.getPower(id);
        return p == null ? 0 : p.amount;
    }

    public static Sim read(int stall) {
        AbstractPlayer pl = AbstractDungeon.player;
        Sim s = new Sim();
        s.energy = EnergyPanel.totalCount;
        s.playerBlock = pl.currentBlock;
        s.hp = pl.currentHealth;
        s.maxHp = pl.maxHealth;
        s.turn = GameActionManager.turn;
        AbstractRoom room = AbstractDungeon.getCurrRoom();
        s.elite = room instanceof MonsterRoomElite || room instanceof MonsterRoomBoss;
        s.stall = stall;
        s.strength = playerPower("Strength");
        s.playerWeak = playerPower("Weakened") > 0;
        s.intangible = playerPower("IntangiblePlayer") > 0 || playerPower("Intangible") > 0;
        s.doubleAttack = playerPower("Double Tap");
        s.doubleSkill = playerPower("Burst");
        // Defect orbs (empty slots are EmptyOrbSlot)
        s.orbSlots = pl.maxOrbs;
        s.focus = playerPower("Focus");
        s.lightningAll = playerPower("Electro") > 0;
        if (pl.orbs != null)
            for (com.megacrit.cardcrawl.orbs.AbstractOrb o : pl.orbs)
                if (o != null && o.ID != null && ("Lightning".equals(o.ID) || "Frost".equals(o.ID) || "Dark".equals(o.ID)
                        || "Plasma".equals(o.ID)))
                    s.orbs.add(new Sim.Orb(o.ID, o.evokeAmount, o.passiveAmount));
        ArrayList<AbstractMonster> ms = AbstractDungeon.getMonsters().monsters;
        for (int i = 0; i < ms.size(); i++) {
            AbstractMonster m = ms.get(i);
            Sim.Mon x = new Sim.Mon();
            x.index = i;
            x.name = m.name;
            x.alive = !m.isDeadOrEscaped() && !m.halfDead && m.currentHealth > 0;
            x.hp = m.currentHealth;
            x.maxHp = m.maxHealth;
            x.block = m.currentBlock;
            x.vuln = amount(m, "Vulnerable");
            x.weak = amount(m, "Weakened");
            x.strength = amount(m, "Strength");
            x.thorns = amount(m, "Thorns") + amount(m, "Sharp Hide");
            x.poison = amount(m, "Poison");
            String intent = m.intent == null ? "" : m.intent.name();
            if (intent.startsWith("ATTACK")) {
                x.intentDmg = Math.max(0, m.getIntentDmg());
                Integer multi = ReflectionHacks.getPrivate(m, AbstractMonster.class, "intentMultiAmt");
                x.intentHits = multi != null && multi > 1 ? multi : 1;
            }
            Double prio = PRIORITY.get(m.name);
            x.prio = prio == null ? 1.0 : prio;
            s.mons.add(x);
        }
        ArrayList<AbstractCard> hand = pl.hand.group;
        for (int k = 0; k < hand.size(); k++) {
            AbstractCard c = hand.get(k);
            Sim.Card x = new Sim.Card();
            x.index = k;
            x.id = c.cardID;
            x.name = c.name;
            x.type = c.type.name();
            x.cost = c.cost == -1 ? -1 : (c.freeToPlay() ? 0 : Math.max(0, c.costForTurn));
            x.targeted = c.target == AbstractCard.CardTarget.ENEMY || c.target == AbstractCard.CardTarget.SELF_AND_ENEMY;
            x.fx = Effects.parse(c.rawDescription, x.type, c.magicNumber);
            x.hits = x.fx.hits;
            Boolean multi = ReflectionHacks.getPrivate(c, AbstractCard.class, "isMultiDamage");
            x.aoe = (multi != null && multi) || x.fx.aoe;
            x.randomTarget = x.fx.randomTarget;
            x.canTarget = new boolean[ms.size()];
            boolean any = false;
            for (int i = 0; i < ms.size(); i++) {
                AbstractMonster m = ms.get(i);
                boolean alive = s.mons.get(i).alive;
                x.canTarget[i] = alive && c.canUse(pl, m);
                any |= x.canTarget[i];
            }
            x.playable = x.targeted ? any : c.canUse(pl, null);
            if (c.type == AbstractCard.CardType.ATTACK && c.baseDamage >= 0) {
                x.damage = new int[ms.size()];
                for (int i = 0; i < ms.size(); i++) {
                    if (!s.mons.get(i).alive) continue;
                    c.calculateCardDamage(ms.get(i));
                    x.damage[i] = (x.aoe && c.multiDamage != null && c.multiDamage.length > i) ? c.multiDamage[i] : c.damage;
                }
            }
            c.applyPowers();   // back to the plain (no-target) numbers the hand shows
            x.block = c.baseBlock > 0 ? Math.max(0, c.block) : 0;
            s.hand.add(x);
        }
        return s;
    }

    /** A cheap fingerprint of what the planner depends on; replan only when it changes. */
    public static String signature() {
        AbstractPlayer pl = AbstractDungeon.player;
        StringBuilder b = new StringBuilder();
        b.append(EnergyPanel.totalCount).append('|').append(pl.currentBlock).append('|').append(pl.currentHealth).append('|');
        for (AbstractCard c : pl.hand.group) b.append(c.cardID).append(c.timesUpgraded).append(c.costForTurn).append(',');
        b.append('|');
        for (AbstractMonster m : AbstractDungeon.getMonsters().monsters)
            b.append(m.currentHealth).append('/').append(m.currentBlock).append('/').append(m.intent).append(',');
        return b.toString();
    }

    public static String monsterHp() {
        StringBuilder b = new StringBuilder();
        for (AbstractMonster m : AbstractDungeon.getMonsters().monsters) b.append(m.currentHealth).append(',');
        return b.toString();
    }

    /** Queue a card exactly as a click would. Returns false if the game no longer allows it. */
    public static boolean play(Planner.Step st) {
        AbstractPlayer pl = AbstractDungeon.player;
        if (st.card < 0 || st.card >= pl.hand.size()) return false;
        AbstractCard c = pl.hand.group.get(st.card);
        AbstractMonster m = null;
        ArrayList<AbstractMonster> ms = AbstractDungeon.getMonsters().monsters;
        if (st.target >= 0 && st.target < ms.size()) m = ms.get(st.target);
        if (!c.canUse(pl, m)) return false;
        AbstractDungeon.actionManager.cardQueue.add(new CardQueueItem(c, m));
        return true;
    }

    /** Potions and the fight context for PotionAdvisor. */
    public static java.util.List<PotionAdvisor.Potion> potions() {
        java.util.List<PotionAdvisor.Potion> out = new ArrayList<>();
        ArrayList<AbstractPotion> ps = AbstractDungeon.player.potions;
        for (int i = 0; i < ps.size(); i++) {
            AbstractPotion p = ps.get(i);
            if (p == null || p instanceof PotionSlot) continue;
            PotionAdvisor.Potion x = new PotionAdvisor.Potion();
            x.slot = i;
            x.id = p.ID;
            x.name = p.name;
            x.canUse = p.canUse();
            x.targetRequired = p.targetRequired;
            out.add(x);
        }
        return out;
    }

    public static PotionAdvisor.Context potionContext(Sim s, Planner.Plan plan, int usedThisTurn) {
        PotionAdvisor.Context c = new PotionAdvisor.Context();
        AbstractRoom room = AbstractDungeon.getCurrRoom();
        c.boss = room instanceof MonsterRoomBoss;
        c.elite = room instanceof MonsterRoomElite;
        c.turn = s.turn;
        c.hp = s.hp;
        c.maxHp = s.maxHp;
        c.taken = plan.taken;
        c.handSize = s.hand.size();
        c.usedThisTurn = usedThisTurn;
        c.floorInAct = AbstractDungeon.floorNum % 17;
        int empty = 0;
        for (AbstractPotion p : AbstractDungeon.player.potions) if (p instanceof PotionSlot) empty++;
        c.emptySlots = empty;
        for (Planner.Step st : plan.steps) {
            AbstractCard card = handCard(st.card);
            if (card != null && card.type == AbstractCard.CardType.ATTACK) c.attacksPlanned = true;
        }
        for (Sim.Mon m : s.mons)
            if (m.alive && "The Champ".equals(m.name)) {
                c.champHp = m.hp;
                c.champMaxHp = m.maxHp;
            }
        return c;
    }

    /** Target for a potion: Weak Potion on the biggest attacker, damage potions on the weakest enemy. */
    public static int potionTarget(PotionAdvisor.Potion p, Sim s) {
        if (!p.targetRequired) return -1;
        Sim.Mon best = null;
        boolean defense = "defense".equals(PotionAdvisor.kindOf(p));
        for (Sim.Mon m : s.mons) {
            if (!m.alive) continue;
            if (best == null) best = m;
            else if (defense ? m.intentDmg * Math.max(1, m.intentHits) > best.intentDmg * Math.max(1, best.intentHits)
                             : m.hp < best.hp) best = m;
        }
        return best == null ? -1 : best.index;
    }

    /** Drink a potion the way the game's potion button does (same calls as CommunicationMod). */
    public static boolean usePotion(int slot, int target) {
        AbstractPlayer pl = AbstractDungeon.player;
        if (slot < 0 || slot >= pl.potions.size()) return false;
        AbstractPotion p = pl.potions.get(slot);
        if (p instanceof PotionSlot || !p.canUse()) return false;
        AbstractMonster m = p.targetRequired ? monster(target) : null;
        if (p.targetRequired && (m == null || m.isDeadOrEscaped())) return false;
        p.use(p.targetRequired ? m : pl);
        for (AbstractRelic r : pl.relics) r.onUsePotion();
        AbstractDungeon.topPanel.destroyPotion(p.slot);
        return true;
    }

    public static AbstractPotion potion(int slot) {
        ArrayList<AbstractPotion> ps = AbstractDungeon.player.potions;
        return slot >= 0 && slot < ps.size() ? ps.get(slot) : null;
    }

    public static void endTurn() {
        AbstractDungeon.overlayMenu.endTurnButton.disable(true);
    }

    public static AbstractCard handCard(int index) {
        ArrayList<AbstractCard> h = AbstractDungeon.player.hand.group;
        return index >= 0 && index < h.size() ? h.get(index) : null;
    }

    public static AbstractMonster monster(int index) {
        ArrayList<AbstractMonster> ms = AbstractDungeon.getMonsters().monsters;
        return index >= 0 && index < ms.size() ? ms.get(index) : null;
    }
}
