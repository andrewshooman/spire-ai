package spireseer;

import basemod.ReflectionHacks;
import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePostfixPatch;
import com.megacrit.cardcrawl.actions.AbstractGameAction;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardGroup;
import com.megacrit.cardcrawl.characters.AbstractPlayer;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.screens.CardRewardScreen;
import com.megacrit.cardcrawl.screens.select.GridCardSelectScreen;
import com.megacrit.cardcrawl.screens.select.HandCardSelectScreen;
import com.megacrit.cardcrawl.vfx.cardManip.ExhaustCardEffect;
import spireseer.brain.Planner;
import spireseer.brain.Sim;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/**
 * Choice screens inside a fight, decided with the planner: Discovery / Attack, Skill, Power and Colorless potions /
 * choose-one cards (Wish, Stance Potion), hand selections (Armaments, True Grit, Burning Pact, discards, Dual Wield,
 * Nightmare, Well-Laid Plans, Warcry, Setup, Forethought, Recycle, Gambling Chip, Gambler's Brew, Elixir) and grid
 * selections (Scry, Seek, Headbutt, Exhume, Hologram, Liquid Memories, Secret Weapon / Technique, Meditate,
 * Omniscience). Each option is judged by the best turn the planner finds with it, or by what it is worth next turn.
 * The same choice is shown in Suggest mode.
 */
public final class Choices {
    private Choices() {}

    enum Kind { NONE, DISCOVERY, CHOOSE_ONE, HAND, GRID }

    /** What the screen asks for. */
    enum Goal { TO_HAND, PLAY_NOW, UPGRADE, REMOVE, DISCARD_REDRAW, NEXT_TURN, RETAIN, COPY_NOW, COPY_NEXT, RECYCLE, SCRY, UNKNOWN }

    /** The pick and why, for the overlay; null when no choice screen is up. */
    static String suggestion;
    static AbstractCard suggested;

    static AbstractCard pendingGrid;   // consumed by the grid patch below on the next frame

    static Kind kind() {
        if (!GameBridge.inCombat() || !AbstractDungeon.isScreenUp) return Kind.NONE;
        AbstractDungeon.CurrentScreen sc = AbstractDungeon.screen;
        if (sc == AbstractDungeon.CurrentScreen.CARD_REWARD) {
            CardRewardScreen r = AbstractDungeon.cardRewardScreen;
            Boolean disc = ReflectionHacks.getPrivate(r, CardRewardScreen.class, "discovery");
            Boolean one = ReflectionHacks.getPrivate(r, CardRewardScreen.class, "chooseOne");
            if (disc != null && disc) return Kind.DISCOVERY;
            if (one != null && one) return Kind.CHOOSE_ONE;
            return Kind.NONE;
        }
        if (sc == AbstractDungeon.CurrentScreen.HAND_SELECT) return Kind.HAND;
        if (sc == AbstractDungeon.CurrentScreen.GRID) return Kind.GRID;
        return Kind.NONE;
    }

    static String actionName() {
        AbstractGameAction a = AbstractDungeon.actionManager.currentAction;
        return a == null ? "" : a.getClass().getSimpleName();
    }

    static Goal handGoal() {
        String a = actionName();
        HandCardSelectScreen h = AbstractDungeon.handCardSelectScreen;
        String why = h.selectionReason == null ? "" : h.selectionReason.toLowerCase();
        switch (a) {
            case "ArmamentsAction": return Goal.UPGRADE;
            case "ExhaustAction": case "ImmolateAction": case "BendAction": return Goal.REMOVE;
            case "DiscardAction": return Goal.REMOVE;
            case "GamblingChipAction": return Goal.DISCARD_REDRAW;
            case "DualWieldAction": return Goal.COPY_NOW;
            case "NightmareAction": return Goal.COPY_NEXT;
            case "RetainCardsAction": return Goal.RETAIN;
            case "PutOnDeckAction": case "SetupAction": case "ForethoughtAction": case "PutOnBottomOfDeckAction": return Goal.NEXT_TURN;
            case "RecycleAction": return Goal.RECYCLE;
            default: break;
        }
        boolean forUpgrade = Boolean.TRUE.equals(ReflectionHacks.getPrivate(h, HandCardSelectScreen.class, "forUpgrade"));
        if (forUpgrade || why.contains("upgrade")) return Goal.UPGRADE;
        if (why.contains("exhaust") || why.contains("discard")) return Goal.REMOVE;
        if (why.contains("retain")) return Goal.RETAIN;
        if (why.contains("copy") || why.contains("duplicate")) return Goal.COPY_NOW;
        if (why.contains("draw pile") || why.contains("top")) return Goal.NEXT_TURN;
        return Goal.UNKNOWN;
    }

    static Goal gridGoal() {
        switch (actionName()) {
            case "ScryAction": return Goal.SCRY;
            case "DiscardPileToTopOfDeckAction": return Goal.NEXT_TURN;
            case "OmniscienceAction": return Goal.PLAY_NOW;
            default: return Goal.TO_HAND;   // Seek, Exhume, Hologram, Liquid Memories, Secret Weapon/Technique, Meditate...
        }
    }

    // ---------------------------------------------------------------- valuation

    private static double turnValue(List<AbstractCard> hand, int extraEnergy, AbstractCard zeroCost) {
        Sim s = GameBridge.read(Spireseer.stall, hand);
        s.energy += extraEnergy;
        if (zeroCost != null)
            for (Sim.Card c : s.hand) if (hand.get(c.index) == zeroCost && c.cost > 0) c.cost = 0;
        return Planner.plan(s, Spireseer.params, 6000).value;
    }

    /** What a card is worth on a fresh turn on its own (full energy, nothing else in hand). */
    private static double nextTurnValue(AbstractCard card) {
        List<AbstractCard> one = new ArrayList<>();
        one.add(card);
        Sim s = GameBridge.read(Spireseer.stall, one);
        s.energy = AbstractDungeon.player.energy.energyMaster;
        s.playerBlock = 0;
        Planner.Plan p = Planner.plan(s, Spireseer.params, 800);
        return p.value - p.base;
    }

    private static boolean junk(AbstractCard c) {
        return c.type == AbstractCard.CardType.STATUS || c.type == AbstractCard.CardType.CURSE;
    }

    private static boolean basic(AbstractCard c) {
        return c.rarity == AbstractCard.CardRarity.BASIC;
    }

    private static List<AbstractCard> without(List<AbstractCard> hand, AbstractCard c) {
        List<AbstractCard> h = new ArrayList<>(hand);
        h.remove(c);
        return h;
    }

    private static List<AbstractCard> with(List<AbstractCard> hand, AbstractCard c) {
        List<AbstractCard> h = new ArrayList<>(hand);
        h.add(c);
        return h;
    }

    static final class Pick {
        final AbstractCard card;
        final double score;
        final String why;
        Pick(AbstractCard card, double score, String why) { this.card = card; this.score = score; this.why = why; }
    }

    /** The best of `options` for `goal`; hand = the player's hand as it is now (selected cards already out of it). */
    static Pick best(Goal goal, List<AbstractCard> options, List<AbstractCard> hand, boolean zeroCost) {
        Pick best = null;
        double now = goal == Goal.REMOVE || goal == Goal.RECYCLE || goal == Goal.UPGRADE || goal == Goal.COPY_NOW
                || goal == Goal.DISCARD_REDRAW ? turnValue(hand, 0, null) : 0;
        for (AbstractCard c : options) {
            double score;
            String why;
            switch (goal) {
                case TO_HAND: case PLAY_NOW: {
                    AbstractCard copy = c;
                    score = turnValue(with(hand, copy), 0, zeroCost ? copy : null);
                    why = "best card for this turn";
                    break;
                }
                case UPGRADE: {
                    AbstractCard up = c.makeStatEquivalentCopy();
                    if (!up.canUpgrade()) { score = -1e9; why = ""; break; }
                    up.upgrade();
                    up.applyPowers();
                    List<AbstractCard> h = without(hand, c);
                    h.add(up);
                    score = turnValue(h, 0, null) - now + 0.3 * nextTurnValueSafe(up) - 0.3 * nextTurnValueSafe(c);
                    why = "the upgrade that helps most";
                    break;
                }
                case REMOVE: case DISCARD_REDRAW: {
                    double loss = now - turnValue(without(hand, c), 0, null);
                    score = -loss + (junk(c) ? 50 : 0) + (basic(c) ? 1.5 : 0);
                    why = junk(c) ? "gets rid of a " + c.type.name().toLowerCase() : "the card this turn needs least";
                    break;
                }
                case RECYCLE: {
                    int gain = c.costForTurn > 0 ? c.costForTurn : 0;
                    score = turnValue(without(hand, c), gain, null) - now + (junk(c) ? 10 : 0);
                    why = "energy for a card this turn doesn't need";
                    break;
                }
                case COPY_NOW: {
                    AbstractCard copy = c.makeStatEquivalentCopy();
                    score = turnValue(with(hand, copy), 0, null) - now;
                    why = "the copy that helps most this turn";
                    break;
                }
                case NEXT_TURN: case RETAIN: case COPY_NEXT: {
                    score = junk(c) ? -50 : nextTurnValueSafe(c);
                    if (goal == Goal.NEXT_TURN && hand.contains(c)) score -= Math.max(0, turnValue(hand, 0, null) - turnValue(without(hand, c), 0, null));
                    why = goal == Goal.RETAIN ? "worth the most next turn" : "best card for next turn";
                    break;
                }
                case SCRY: {
                    score = junk(c) ? 50 : -nextTurnValueSafe(c);
                    why = "";
                    break;
                }
                default: {
                    score = -nextTurnValueSafe(c);
                    why = "";
                }
            }
            if (best == null || score > best.score) best = new Pick(c, score, why);
        }
        return best;
    }

    private static double nextTurnValueSafe(AbstractCard c) {
        try {
            return nextTurnValue(c);
        } catch (Exception e) {
            return 0;
        }
    }

    // ---------------------------------------------------------------- acting

    /** Decide (and in Auto, perform) the next step on the open choice screen. Returns true when it acted. */
    static boolean step(boolean act) {
        suggestion = null;
        suggested = null;
        Kind k = kind();
        if (k == Kind.NONE) return false;
        try {
            switch (k) {
                case DISCOVERY: case CHOOSE_ONE: return reward(k, act);
                case HAND: return hand(act);
                case GRID: return grid(act);
                default: return false;
            }
        } catch (Exception e) {
            Spireseer.log.warn("Spireseer choice screen: " + e);
            return false;
        }
    }

    private static boolean reward(Kind k, boolean act) throws Exception {
        CardRewardScreen r = AbstractDungeon.cardRewardScreen;
        if (r.rewardGroup == null || r.rewardGroup.isEmpty()) return false;
        List<AbstractCard> hand = AbstractDungeon.player.hand.group;
        Pick p = best(k == Kind.DISCOVERY ? Goal.TO_HAND : Goal.PLAY_NOW, r.rewardGroup, hand, true);
        if (p == null) return false;
        suggested = p.card;
        suggestion = "Take " + p.card.name + ": " + p.why;
        if (!act) return false;
        Object skip = ReflectionHacks.getPrivate(r, CardRewardScreen.class, "skipButton");
        Object bowl = ReflectionHacks.getPrivate(r, CardRewardScreen.class, "bowlButton");
        if (skip != null) ((com.megacrit.cardcrawl.ui.buttons.SkipCardButton) skip).hide();
        if (bowl != null) ((com.megacrit.cardcrawl.ui.buttons.SingingBowlButton) bowl).hide();
        if (k == Kind.DISCOVERY) {
            r.discoveryCard = p.card;
        } else {
            p.card.onChoseThisOption();
            AbstractDungeon.effectList.add(new ExhaustCardEffect(p.card));
        }
        Method take = CardRewardScreen.class.getDeclaredMethod("takeReward");
        take.setAccessible(true);
        take.invoke(r);
        AbstractDungeon.closeCurrentScreen();
        return true;
    }

    private static boolean handConfirmReady(HandCardSelectScreen h) {
        Boolean hidden = ReflectionHacks.getPrivate(h.button, com.megacrit.cardcrawl.ui.buttons.CardSelectConfirmButton.class, "isHidden");
        return !h.button.isDisabled && (hidden == null || !hidden);
    }

    private static boolean hand(boolean act) throws Exception {
        HandCardSelectScreen h = AbstractDungeon.handCardSelectScreen;
        AbstractPlayer pl = AbstractDungeon.player;
        Goal goal = handGoal();
        boolean any = Boolean.TRUE.equals(ReflectionHacks.getPrivate(h, HandCardSelectScreen.class, "anyNumber"));
        int need = h.numCardsToSelect;
        int have = h.selectedCards.size();
        List<AbstractCard> options = new ArrayList<>(pl.hand.group);
        boolean done = options.isEmpty() || (!any && have >= need);
        Pick p = null;
        if (!done) {
            p = best(goal, options, pl.hand.group, false);
            if (p != null && (any || h.canPickZero || h.upTo)) {
                // optional picks: only what is clearly worth it (junk out, or a removal that costs nothing)
                boolean worth = goal == Goal.REMOVE || goal == Goal.DISCARD_REDRAW ? p.score > (goal == Goal.DISCARD_REDRAW ? 1.0 : 5.0)
                        : goal == Goal.SCRY ? p.score > 0 : p.score > 0.5 || have == 0 && !h.canPickZero && !any && !h.upTo;
                if (!worth) p = null;
            }
            if (p == null) done = true;
        }
        if (done) {
            suggestion = have == 0 ? "Confirm (no card worth picking)" : "Confirm";
            if (act && handConfirmReady(h)) {
                h.button.hb.clicked = true;
                return true;
            }
            return false;
        }
        suggested = p.card;
        suggestion = verb(goal) + " " + p.card.name + (p.why.isEmpty() ? "" : ": " + p.why);
        if (!act) return false;
        h.hoveredCard = p.card;
        p.card.setAngle(0f, false);
        Method sel = HandCardSelectScreen.class.getDeclaredMethod("selectHoveredCard");
        sel.setAccessible(true);
        sel.invoke(h);
        return true;
    }

    private static boolean gridConfirmReady(GridCardSelectScreen g) {
        Boolean hidden = ReflectionHacks.getPrivate(g.confirmButton, com.megacrit.cardcrawl.ui.buttons.GridSelectConfirmButton.class, "isHidden");
        return !g.confirmButton.isDisabled && (hidden == null || !hidden);
    }

    private static boolean grid(boolean act) {
        GridCardSelectScreen g = AbstractDungeon.gridSelectScreen;
        if (g.forUpgrade || g.forTransform || g.forPurge) return false;   // deck changes outside a fight: yours
        CardGroup group = g.targetGroup;
        if (group == null) return false;
        Integer numCards = ReflectionHacks.getPrivate(g, GridCardSelectScreen.class, "numCards");
        int need = numCards == null ? 1 : numCards;
        int have = g.selectedCards.size();
        Goal goal = gridGoal();
        List<AbstractCard> options = new ArrayList<>();
        for (AbstractCard c : group.group) if (!g.selectedCards.contains(c)) options.add(c);
        boolean optional = g.anyNumber || goal == Goal.SCRY;
        boolean done = options.isEmpty() || (!optional && have >= need) || g.confirmScreenUp;
        Pick p = null;
        if (!done) {
            p = best(goal, options, AbstractDungeon.player.hand.group, actionName().equals("BetterDiscardPileToHandAction")
                    && AbstractDungeon.actionManager.currentAction != null
                    && Boolean.TRUE.equals(ReflectionHacks.getPrivate(AbstractDungeon.actionManager.currentAction,
                    AbstractDungeon.actionManager.currentAction.getClass(), "setCost")));
            if (p != null && optional && p.score <= 0) p = null;
            if (p == null) done = true;
        }
        if (done) {
            suggestion = "Confirm";
            if (act && gridConfirmReady(g)) {
                g.confirmButton.hb.clicked = true;
                return true;
            }
            return false;
        }
        suggested = p.card;
        suggestion = verb(goal) + " " + p.card.name + (p.why.isEmpty() ? "" : ": " + p.why);
        if (!act) return false;
        pendingGrid = p.card;
        return true;
    }

    private static String verb(Goal g) {
        switch (g) {
            case UPGRADE: return "Upgrade";
            case REMOVE: return "Pick";
            case DISCARD_REDRAW: return "Discard";
            case RETAIN: return "Retain";
            case NEXT_TURN: return "Put back";
            case COPY_NOW: case COPY_NEXT: return "Copy";
            case RECYCLE: return "Recycle";
            case SCRY: return "Discard";
            default: return "Take";
        }
    }

    /** Clicks a card on the grid the way CommunicationMod does: hovered and clicked for one frame. */
    @SpirePatch(clz = GridCardSelectScreen.class, method = "updateCardPositionsAndHoverLogic")
    public static class GridHover {
        @SpirePostfixPatch
        public static void Postfix(GridCardSelectScreen __instance) {
            AbstractCard c = pendingGrid;
            if (c == null) return;
            pendingGrid = null;
            ReflectionHacks.setPrivate(__instance, GridCardSelectScreen.class, "hoveredCard", c);
            c.hb.hovered = true;
            c.hb.clicked = true;
        }
    }
}
