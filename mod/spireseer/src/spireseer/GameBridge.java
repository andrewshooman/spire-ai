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
import java.util.List;

/** Reads the live combat into the brain's model and performs its choices (the same calls a click makes). */
public final class GameBridge {

    private GameBridge() {}

    static boolean inCombat() {
        return CardCrawlGame.mode == CardCrawlGame.GameMode.GAMEPLAY && AbstractDungeon.isPlayerInDungeon()
                && AbstractDungeon.getCurrRoom() != null
                && AbstractDungeon.getCurrRoom().phase == AbstractRoom.RoomPhase.COMBAT
                && AbstractDungeon.getMonsters() != null && !AbstractDungeon.getMonsters().areMonstersBasicallyDead();
    }

    /** No action, card or effect in flight: the game waits for the player (a screen may be open). */
    static boolean idle() {
        GameActionManager am = AbstractDungeon.actionManager;
        return am.phase == GameActionManager.Phase.WAITING_ON_USER && am.actions.isEmpty() && am.cardQueue.isEmpty();
    }

    /** True when the game waits for the player to play a card, with nothing in flight and no screen open. */
    public static boolean ready() {
        if (!inCombat()) return false;
        AbstractPlayer pl = AbstractDungeon.player;
        if (pl == null || AbstractDungeon.isScreenUp || pl.isDead || pl.endTurnQueued) return false;
        if (!idle()) return false;
        return AbstractDungeon.overlayMenu != null && AbstractDungeon.overlayMenu.endTurnButton.enabled;
    }

    /** The player is in the middle of a click or drag: never act over it. */
    public static boolean playerBusy() {
        AbstractPlayer pl = AbstractDungeon.player;
        return pl != null && (pl.isDraggingCard || pl.inSingleTargetMode);
    }

    /** planner.pw: the amount of the first power whose ID or name is one of `names`. */
    static int pw(ArrayList<AbstractPower> powers, String... names) {
        if (powers == null) return 0;
        for (AbstractPower p : powers)
            for (String n : names) if (n.equals(p.ID) || n.equals(p.name)) return p.amount;
        return 0;
    }

    static boolean hasPower(ArrayList<AbstractPower> powers, String id) {
        if (powers == null) return false;
        for (AbstractPower p : powers) if (id.equals(p.ID)) return true;
        return false;
    }

    static String stance() {
        AbstractPlayer pl = AbstractDungeon.player;
        return pl.stance != null && pl.stance.ID != null && !pl.stance.ID.isEmpty() ? pl.stance.ID : "Neutral";
    }

    /** The card's text in the card database's wording, with its current numbers. */
    static String text(AbstractCard c) {
        return Effects.normalize(c.rawDescription, c.baseDamage, c.baseBlock, c.baseMagicNumber);
    }

    static Effects effects(AbstractCard c) {
        int cost = c.cost == -1 ? -1 : c.costForTurn;
        return Effects.of(text(c), c.type.name(), cost, Effects.baseName(c.name));
    }

    static int cardCost(AbstractCard c) {
        return c.cost == -1 ? -1 : (c.freeToPlay() ? 0 : Math.max(0, c.costForTurn));
    }

    public static Sim read(int stall) {
        return read(stall, AbstractDungeon.player.hand.group);
    }

    /** The combat as the planner sees it, with `hand` as the hand (the real one, or a what-if for choice screens). */
    public static Sim read(int stall, List<AbstractCard> hand) {
        AbstractPlayer pl = AbstractDungeon.player;
        ArrayList<AbstractPower> pp = pl.powers;
        Sim s = new Sim();
        s.energy = EnergyPanel.totalCount;
        s.playerBlock = pl.currentBlock;
        s.hp = pl.currentHealth;
        s.maxHp = pl.maxHealth;
        s.turn = GameActionManager.turn;
        AbstractRoom room = AbstractDungeon.getCurrRoom();
        s.elite = room instanceof MonsterRoomElite || room instanceof MonsterRoomBoss;
        s.stall = stall;
        s.strength = pw(pp, "Strength");
        s.dexterity = pw(pp, "Dexterity");
        s.playerWeak = pw(pp, "Weakened") > 0;
        s.playerFrail = pw(pp, "Frail") > 0;
        s.intangible = pw(pp, "Intangible", "IntangiblePlayer") > 0;
        s.doubleAttack = pw(pp, "Double Tap");
        s.doubleSkill = pw(pp, "Burst");
        s.mantra = pw(pp, "Mantra");
        s.focus = pw(pp, "Focus");
        s.lightningAll = pw(pp, "Electrodynamics", "Electro") > 0;
        s.barricade = pw(pp, "Barricade") > 0 || hasPower(pp, "Barricade");
        s.hex = pw(pp, "Hex");
        s.stance = stance();
        s.orbSlots = pl.maxOrbs;
        if (pl.orbs != null)
            for (com.megacrit.cardcrawl.orbs.AbstractOrb o : pl.orbs)
                if (o != null && o.ID != null && ("Lightning".equals(o.ID) || "Frost".equals(o.ID) || "Dark".equals(o.ID)
                        || "Plasma".equals(o.ID)))
                    s.orbs.add(new Sim.Orb(o.ID, o.evokeAmount, o.passiveAmount));
        for (AbstractCard d : pl.masterDeck.group) s.deck.add(d.name);
        ArrayList<AbstractMonster> ms = AbstractDungeon.getMonsters().monsters;
        int beat = 0, curiosity = 0, warp = -1;
        boolean eater = false;
        for (int i = 0; i < ms.size(); i++) {
            AbstractMonster m = ms.get(i);
            Sim.Mon x = new Sim.Mon();
            ArrayList<AbstractPower> p = m.powers;
            x.index = i;
            x.name = m.name;
            x.intent = m.intent == null ? "" : m.intent.name();
            x.alive = !m.isDeadOrEscaped() && !m.halfDead && m.currentHealth > 0;
            x.hp = m.currentHealth;
            x.maxHp = m.maxHealth;
            x.block = m.currentBlock;
            x.vuln = pw(p, "Vulnerable");
            x.weak = pw(p, "Weakened");
            if (x.alive && x.intent.contains("ATTACK")) {
                x.atk = Math.max(0, m.getIntentDmg());
                Boolean multi = ReflectionHacks.getPrivate(m, AbstractMonster.class, "isMultiDmg");
                Integer amt = ReflectionHacks.getPrivate(m, AbstractMonster.class, "intentMultiAmt");
                x.hits = multi != null && multi && amt != null ? Math.max(1, amt) : 1;
            }
            x.poison = pw(p, "Poison");
            x.artifact = pw(p, "Artifact");
            x.barricade = pw(p, "Barricade") > 0 || hasPower(p, "Barricade");
            x.thorns = pw(p, "Thorns");
            x.sharp = pw(p, "Sharp Hide");
            x.malleable = pw(p, "Malleable");
            x.curl = pw(p, "Curl Up");
            x.intangible = pw(p, "Intangible") > 0;
            x.invincible = Math.max(0, pw(p, "Invincible"));
            x.stasis = pw(p, "Stasis") != 0;
            x.asleep = "Lagavulin".equals(m.name) && "SLEEP".equals(x.intent);
            x.nob = pw(p, "Anger", "Enrage");
            x.shifting = pw(p, "Shifting") != 0 || hasPower(p, "Shifting");
            x.flight = pw(p, "Flight");
            x.mark = pw(p, "Mark", "PathToVictory");
            x.strength = pw(p, "Strength");
            x.ritual = pw(p, "Ritual");
            s.mons.add(x);
            if (x.alive) {
                beat = Math.max(beat, pw(p, "BeatOfDeath", "Beat of Death"));
                curiosity = Math.max(curiosity, pw(p, "Curiosity"));
                if (pw(p, "Time Warp") != 0 || "Time Eater".equals(m.name)) eater = true;
                warp = Math.max(warp, pw(p, "Time Warp"));
            }
        }
        s.beatOfDeath = beat;
        s.curiosity = curiosity;
        s.timeWarp = eater ? warp : -1;
        int strikes = 0;
        for (String d : s.deck) if (d.contains("Strike")) strikes++;
        for (int k = 0; k < hand.size(); k++) {
            AbstractCard c = hand.get(k);
            Sim.Card x = new Sim.Card();
            x.index = k;
            x.id = c.cardID;
            x.name = c.name;
            x.upgrades = c.timesUpgraded;
            x.type = c.type.name();
            x.cost = cardCost(c);
            x.targeted = c.target == AbstractCard.CardTarget.ENEMY || c.target == AbstractCard.CardTarget.SELF_AND_ENEMY;
            x.fx = effects(c);
            x.canTarget = new boolean[ms.size()];
            boolean any = false;
            for (int i = 0; i < ms.size(); i++) {
                boolean alive = s.mons.get(i).alive;
                x.canTarget[i] = alive && safeCanUse(c, pl, ms.get(i));
                any |= x.canTarget[i];
            }
            x.playable = x.targeted ? any : safeCanUse(c, pl, null);
            anchor(x, c, s, ms, strikes);
            s.hand.add(x);
        }
        return s;
    }

    static boolean safeCanUse(AbstractCard c, AbstractPlayer pl, AbstractMonster m) {
        try {
            return c.canUse(pl, m);
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * The game's own numbers vs the planner's estimate from the card text: the difference is kept as a fixed bonus
     * (Pen Nib, Wrist Blade, Strike Dummy, Akabeko, Vigor, modded relics...). Only cards in your hand during combat
     * (calculateCardDamage needs a real target).
     */
    static void anchor(Sim.Card x, AbstractCard c, Sim s, ArrayList<AbstractMonster> ms, int strikes) {
        Effects e = x.fx;
        if (c.type == AbstractCard.CardType.ATTACK && e.damage > 0 && !e.blockScaling && e.damagePerExhaust == 0
                && c.baseDamage >= 0 && AbstractDungeon.player.hand.contains(c)) {
            Boolean multi = ReflectionHacks.getPrivate(c, AbstractCard.class, "isMultiDamage");
            double per = e.damage + e.perStrike * strikes + s.strength * e.strengthMult;
            if (s.playerWeak) per *= 0.75;
            per *= "Wrath".equals(s.stance) ? 2.0 : "Divinity".equals(s.stance) ? 3.0 : 1.0;
            x.dmgBonus = new double[ms.size()];
            for (int i = 0; i < ms.size(); i++) {
                Sim.Mon m = s.mons.get(i);
                if (!m.alive) continue;
                c.calculateCardDamage(ms.get(i));
                int game = (multi != null && multi && c.multiDamage != null && c.multiDamage.length > i) ? c.multiDamage[i] : c.damage;
                double raw = game / (m.vuln > 0 ? 1.5 : 1.0);
                double b = raw - per;
                x.dmgBonus[i] = Math.abs(b) < 1.0 ? 0 : Math.round(b);
            }
            c.applyPowers();   // back to the plain (no-target) numbers the hand shows
        }
        if (e.block > 0 && c.baseBlock > 0) {
            double est = (int) ((e.block + s.dexterity) * (s.playerFrail ? 0.75 : 1.0));
            double b = c.block - est;
            x.blockBonus = Math.abs(b) < 1.0 ? 0 : Math.round(b);
        }
    }

    /** A cheap fingerprint of what the planner depends on; replan only when it changes. */
    public static String signature() {
        AbstractPlayer pl = AbstractDungeon.player;
        StringBuilder b = new StringBuilder();
        b.append(EnergyPanel.totalCount).append('|').append(pl.currentBlock).append('|').append(pl.currentHealth).append('|')
                .append(stance()).append('|');
        for (AbstractCard c : pl.hand.group) b.append(c.cardID).append(c.timesUpgraded).append(c.costForTurn).append(',');
        b.append('|');
        for (AbstractPower p : pl.powers) b.append(p.ID).append(p.amount).append(',');
        b.append('|');
        for (AbstractMonster m : AbstractDungeon.getMonsters().monsters)
            b.append(m.currentHealth).append('/').append(m.currentBlock).append('/').append(m.intent).append('/')
                    .append(m.powers.size()).append(',');
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
        if (m == null && (c.target == AbstractCard.CardTarget.ENEMY || c.target == AbstractCard.CardTarget.SELF_AND_ENEMY)) {
            for (AbstractMonster x : ms) if (!x.isDeadOrEscaped() && !x.halfDead) { m = x; break; }
        }
        if (!safeCanUse(c, pl, m)) return false;
        AbstractDungeon.actionManager.cardQueue.add(new CardQueueItem(c, m));
        return true;
    }

    /** Potions and the fight context for PotionAdvisor. */
    public static List<PotionAdvisor.Potion> potions() {
        List<PotionAdvisor.Potion> out = new ArrayList<>();
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
            x.potency = p.getPotency();
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
        c.act = AbstractDungeon.actNum;
        int empty = 0;
        for (AbstractPotion p : AbstractDungeon.player.potions) if (p instanceof PotionSlot) empty++;
        c.emptySlots = empty;
        for (Planner.Step st : plan.steps) {
            AbstractCard card = handCard(st.card);
            if (card != null && card.type == AbstractCard.CardType.ATTACK) c.attacksPlanned = true;
        }
        int alive = 0, lowest = Integer.MAX_VALUE;
        for (Sim.Mon m : s.mons) {
            if (!m.alive) continue;
            alive++;
            lowest = Math.min(lowest, m.hp + m.block);
            if ("The Champ".equals(m.name)) {
                c.champHp = m.hp;
                c.champMaxHp = m.maxHp;
            }
        }
        c.enemies = alive;
        c.lowestEnemy = lowest == Integer.MAX_VALUE ? 0 : lowest;
        return c;
    }

    /** Target for a potion: Weak Potion on the biggest attacker, damage potions on what they can kill, else the weakest. */
    public static int potionTarget(PotionAdvisor.Potion p, Sim s) {
        if (!p.targetRequired) return -1;
        Sim.Mon best = null;
        boolean defense = "defense".equals(PotionAdvisor.kindOf(p));
        for (Sim.Mon m : s.mons) {
            if (!m.alive) continue;
            if (best == null) best = m;
            else if (defense ? m.atk * Math.max(1, m.hits) > best.atk * Math.max(1, best.hits)
                             : m.prio > best.prio || (m.prio == best.prio && m.hp + m.block < best.hp + best.block)) best = m;
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
