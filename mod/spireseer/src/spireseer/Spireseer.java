package spireseer;

import spireseer.brain.Planner;
import spireseer.brain.PotionAdvisor;
import basemod.ReflectionHacks;
import spireseer.brain.Sim;
import basemod.BaseMod;
import basemod.ModLabel;
import basemod.ModLabeledToggleButton;
import basemod.ModPanel;
import basemod.interfaces.PostInitializeSubscriber;
import basemod.interfaces.PostRenderSubscriber;
import basemod.interfaces.PostUpdateSubscriber;
import com.badlogic.gdx.Gdx;
import com.badlogic.gdx.Input;
import com.badlogic.gdx.graphics.Color;
import com.badlogic.gdx.graphics.Texture;
import com.badlogic.gdx.graphics.g2d.SpriteBatch;
import com.evacipated.cardcrawl.modthespire.lib.SpireConfig;
import com.evacipated.cardcrawl.modthespire.lib.SpireInitializer;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.core.Settings;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.helpers.FontHelper;
import com.megacrit.cardcrawl.helpers.ImageMaster;
import com.megacrit.cardcrawl.helpers.Hitbox;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.ui.buttons.EndTurnButton;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.Properties;

/**
 * Spireseer: combat suggestions or auto-battle for Slay the Spire.
 *
 *   F7  cycle mode: OFF -> SUGGEST (highlights the best play) -> AUTO (plays the turn for you)
 *   F8  pause / resume AUTO
 *
 * AUTO never starts by itself: every launch begins in OFF or SUGGEST (whichever you used last), it is
 * off in Daily Climbs, it waits while you drag or target a card, and choice screens (Armaments, Headbutt,
 * card rewards...) are left to you.
 */
@SpireInitializer
public class Spireseer implements PostInitializeSubscriber, PostUpdateSubscriber, PostRenderSubscriber {

    public static final Logger log = LogManager.getLogger("Spireseer");

    enum Mode { OFF, SUGGEST, AUTO }

    static Mode mode = Mode.SUGGEST;
    static boolean paused;
    static boolean autoEndTurn = true;
    static boolean showInAuto = true;
    static boolean autoPotions = true;
    static boolean oneAtATime = false;      // Suggest: only the next card (F9 switches)
    static int delayMs = 350;
    static SpireConfig config;
    static final Planner.Params params = new Planner.Params();

    // last plan, recomputed only when the combat state changes
    private static String lastSig;
    private static Sim lastSim;
    private static Planner.Plan lastPlan;
    private static long nextActionAt;
    // stall guard: enemy HP unchanged from one turn to the next
    private static String stallHp;
    private static int stall, stallTurn = -1;
    private static String status = "";
    private static PotionAdvisor.Advice lastPotion;
    private static int lastPotionTarget = -1;
    private static int potionsThisTurn, potionTurn = -1;
    private static AbstractCard glowed;     // the card we tinted, restored when the suggestion moves
    private static java.util.List<String> lastReasons = new java.util.ArrayList<>();

    public static void initialize() {
        BaseMod.subscribe(new Spireseer());
        try {
            Properties d = new Properties();
            d.setProperty("mode", "SUGGEST");
            d.setProperty("autoEndTurn", "true");
            d.setProperty("showInAuto", "true");
            d.setProperty("delayMs", "350");
            d.setProperty("autoPotions", "true");
            d.setProperty("oneAtATime", "false");
            config = new SpireConfig("Spireseer", "config", d);
            mode = "OFF".equals(config.getString("mode")) ? Mode.OFF : Mode.SUGGEST;   // never AUTO at launch
            autoEndTurn = config.getBool("autoEndTurn");
            showInAuto = config.getBool("showInAuto");
            delayMs = Math.max(0, config.getInt("delayMs"));
            autoPotions = config.getBool("autoPotions");
            oneAtATime = config.getBool("oneAtATime");
        } catch (Exception e) {
            log.warn("Spireseer config not loaded: " + e);
        }
    }

    static void save() {
        if (config == null) return;
        try {
            config.setString("mode", mode == Mode.OFF ? "OFF" : "SUGGEST");
            config.setBool("autoEndTurn", autoEndTurn);
            config.setBool("showInAuto", showInAuto);
            config.setInt("delayMs", delayMs);
            config.setBool("autoPotions", autoPotions);
            config.setBool("oneAtATime", oneAtATime);
            config.save();
        } catch (Exception e) {
            log.warn("Spireseer config not saved: " + e);
        }
    }

    @Override
    public void receivePostInitialize() {
        ModPanel panel = new ModPanel();
        float x = 380f, y = 720f;
        panel.addUIElement(new ModLabel("Spireseer  -  F7: Off / Suggest / Auto,  F8: pause auto", x, y,
                Settings.CREAM_COLOR, FontHelper.charDescFont, panel, l -> {}));
        panel.addUIElement(new ModLabeledToggleButton("Auto mode ends the turn by itself", x, y - 60f,
                Settings.CREAM_COLOR, FontHelper.charDescFont, autoEndTurn, panel, l -> {}, b -> {
                    autoEndTurn = b.enabled;
                    save();
                }));
        panel.addUIElement(new ModLabeledToggleButton("Show the plan on screen in Auto mode", x, y - 110f,
                Settings.CREAM_COLOR, FontHelper.charDescFont, showInAuto, panel, l -> {}, b -> {
                    showInAuto = b.enabled;
                    save();
                }));
        panel.addUIElement(new ModLabeledToggleButton("Slow auto-play (watchable pace)", x, y - 160f,
                Settings.CREAM_COLOR, FontHelper.charDescFont, delayMs >= 350, panel, l -> {}, b -> {
                    delayMs = b.enabled ? 350 : 60;
                    save();
                }));
        panel.addUIElement(new ModLabeledToggleButton("Auto mode drinks potions (same rules as the suggestions)", x, y - 210f,
                Settings.CREAM_COLOR, FontHelper.charDescFont, autoPotions, panel, l -> {}, b -> {
                    autoPotions = b.enabled;
                    save();
                }));
        panel.addUIElement(new ModLabeledToggleButton("Suggest one card at a time instead of the whole turn (F9)", x, y - 260f,
                Settings.CREAM_COLOR, FontHelper.charDescFont, oneAtATime, panel, l -> {}, b -> {
                    oneAtATime = b.enabled;
                    save();
                }));
        Texture badge = ImageMaster.loadImage("spireseerResources/badge.png");
        BaseMod.registerModBadge(badge, "Spireseer", "Andrew",
                "Combat suggestions or auto-battle. F7 cycles Off / Suggest / Auto, F8 pauses.", panel);
    }

    @Override
    public void receivePostUpdate() {
        try {
            update();
        } catch (Exception e) {   // a view/helper must never crash the game
            log.warn("Spireseer update failed: " + e);
            lastPlan = null;
            lastSig = null;
            glow(null);
        }
    }

    /** Tint the suggested card's glow; put the previous one back to the game's own color. */
    private static void glow(AbstractCard c) {
        if (glowed != null && glowed != c) {
            Color blue = ReflectionHacks.getPrivateStatic(AbstractCard.class, "BLUE_BORDER_GLOW_COLOR");
            if (blue != null) glowed.glowColor = blue.cpy();
            glowed.triggerOnGlowCheck();
        }
        if (c != null) c.glowColor = SUGGEST_GLOW.cpy();
        glowed = c;
    }

    private static void update() {
        if (Gdx.input.isKeyJustPressed(Input.Keys.F7)) {
            mode = Mode.values()[(mode.ordinal() + 1) % Mode.values().length];
            paused = false;
            save();
        }
        if (Gdx.input.isKeyJustPressed(Input.Keys.F8) && mode == Mode.AUTO) paused = !paused;
        if (Gdx.input.isKeyJustPressed(Input.Keys.F9)) {
            oneAtATime = !oneAtATime;
            save();
        }
        if (mode == Mode.OFF || !GameBridge.ready()) {
            if (mode == Mode.OFF || !inCombat()) {
                lastPlan = null;
                lastPotion = null;
                glow(null);
            }
            return;
        }
        // stall guard: count turns in which no enemy lost HP
        int turn = com.megacrit.cardcrawl.actions.GameActionManager.turn;
        if (turn != stallTurn) {
            String hp = GameBridge.monsterHp();
            stall = hp.equals(stallHp) ? stall + 1 : 0;
            stallHp = hp;
            stallTurn = turn;
        }
        String sig = GameBridge.signature();
        if (!sig.equals(lastSig) || lastPlan == null) {
            lastSim = GameBridge.read(stall);
            lastPlan = Planner.plan(lastSim, params);
            lastReasons = Planner.explain(lastSim, lastPlan, params);
            lastSig = sig;
            if (turn != potionTurn) {
                potionTurn = turn;
                potionsThisTurn = 0;
            }
            lastPotion = PotionAdvisor.advise(GameBridge.potions(),
                    GameBridge.potionContext(lastSim, lastPlan, potionsThisTurn));
            lastPotionTarget = lastPotion == null ? -1 : GameBridge.potionTarget(lastPotion.potion, lastSim);
        }
        glow(lastPlan.endTurn(params) || lastPlan.steps.isEmpty() ? null : GameBridge.handCard(lastPlan.steps.get(0).card));
        boolean autoOk = mode == Mode.AUTO && !paused && !Settings.isDailyRun;
        status = mode == Mode.AUTO ? (Settings.isDailyRun ? "AUTO is off in Daily Climbs" : paused ? "AUTO paused (F8)" : "")
                : "";
        if (!autoOk || GameBridge.playerBusy() || System.currentTimeMillis() < nextActionAt) return;
        if (lastPotion != null && autoPotions) {
            if (GameBridge.usePotion(lastPotion.potion.slot, lastPotionTarget)) potionsThisTurn++;
            lastPotion = null;
        } else if (lastPlan.endTurn(params)) {
            if (autoEndTurn) GameBridge.endTurn();
        } else if (!GameBridge.play(lastPlan.steps.get(0))) {
            log.info("Spireseer: planned card no longer playable; replanning");
        }
        lastSig = null;   // re-read the game after every action
        nextActionAt = System.currentTimeMillis() + delayMs;
    }

    /** The game's own targeting reticle around a monster. */
    private static void reticle(SpriteBatch sb, AbstractMonster m) {
        m.reticleAlpha = 1f;
        m.renderReticle(sb);
    }

    private static boolean inCombat() {
        return CardCrawlGame.mode == CardCrawlGame.GameMode.GAMEPLAY && AbstractDungeon.isPlayerInDungeon()
                && AbstractDungeon.getCurrRoom() != null
                && AbstractDungeon.getCurrRoom().phase == com.megacrit.cardcrawl.rooms.AbstractRoom.RoomPhase.COMBAT;
    }

    @Override
    public void receivePostRender(SpriteBatch sb) {
        try {
            render(sb);
        } catch (Exception e) {
            log.warn("Spireseer render failed: " + e);
        }
    }

    private static final Color GOLD = new Color(0.95f, 0.78f, 0.32f, 1f);
    private static final Color DIM = new Color(0.8f, 0.78f, 0.7f, 0.85f);
    private static final Color SUGGEST_GLOW = new Color(0.35f, 1f, 0.45f, 0.25f);   // glow drawn around the card
    private static final Color GREEN = new Color(0.45f, 1f, 0.55f, 1f);
    private static final Color CREAM = new Color(0.93f, 0.89f, 0.8f, 1f);

    private static String stepName(int k) {
        Planner.Step st = lastPlan.steps.get(k);
        AbstractCard c = GameBridge.handCard(st.card);
        AbstractMonster m = GameBridge.monster(st.target);
        return (c == null ? "?" : c.name) + (m == null ? "" : " > " + m.name);
    }

    private static String reasonAt(int k) {
        return k < lastReasons.size() ? lastReasons.get(k) : "";
    }

    private static void render(SpriteBatch sb) {
        if (CardCrawlGame.mode != CardCrawlGame.GameMode.GAMEPLAY || !AbstractDungeon.isPlayerInDungeon()) return;
        float s = Settings.scale;
        String head = "Spireseer: " + mode + (mode == Mode.OFF ? "" : oneAtATime ? " - next card" : " - whole turn")
                + (status.isEmpty() ? "" : " - " + status) + "   (F7 mode, F8 pause, F9 next card / whole turn)";
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, head, 24f * s, Settings.HEIGHT - 70f * s, DIM);
        if (mode == Mode.OFF || lastPlan == null || lastSim == null || !inCombat()) return;
        if (mode == Mode.AUTO && !showInAuto) return;
        boolean end = lastPlan.endTurn(params) || lastPlan.steps.isEmpty();
        float cx = Settings.WIDTH / 2f, y = Settings.HEIGHT * 0.79f, gap = 30f * s;
        // potion first: its slot gets a marker and the reason
        if (lastPotion != null) {
            AbstractPotion p = GameBridge.potion(lastPotion.potion.slot);
            String pl = "First, drink " + lastPotion.potion.name + ": " + lastPotion.why;
            FontHelper.renderFontCentered(sb, FontHelper.tipBodyFont, pl, cx, y, GREEN);
            y -= gap;
            if (p != null) FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "USE", p.posX, p.posY - 44f * s, GREEN);
            AbstractMonster pm = GameBridge.monster(lastPotionTarget);
            if (pm != null) reticle(sb, pm);
        }
        if (end) {
            FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "End turn: " + Planner.endReason(lastSim), cx, y, GOLD);
            EndTurnButton btn = AbstractDungeon.overlayMenu.endTurnButton;
            Hitbox hb = ReflectionHacks.getPrivate(btn, EndTurnButton.class, "hb");
            if (hb != null) FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "END TURN", hb.cX, hb.y + hb.height + 26f * s, GOLD);
            return;
        }
        int n = lastPlan.steps.size();
        if (oneAtATime) {
            // just the next card and why; the rest is re-planned after it resolves
            FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "Next: " + stepName(0) + "  -  " + reasonAt(0), cx, y, GREEN);
            if (n > 1) FontHelper.renderFontCentered(sb, FontHelper.tipBodyFont,
                    "then " + (n - 1) + " more card" + (n > 2 ? "s" : "") + " this turn  (" + lastPlan.totals() + ")", cx, y - gap, DIM);
        } else {
            FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "This turn: " + lastPlan.totals(), cx, y, GOLD);
            for (int k = 0; k < n; k++)
                FontHelper.renderFontCentered(sb, FontHelper.tipBodyFont, (k + 1) + ". " + stepName(k) + "  -  " + reasonAt(k),
                        cx, y - gap * (k + 1), k == 0 ? GREEN : CREAM);
        }
        // numbers on the cards: the whole order, or just the next one
        for (int k = 0; k < (oneAtATime ? Math.min(1, n) : n); k++) {
            AbstractCard c = GameBridge.handCard(lastPlan.steps.get(k).card);
            if (c != null)
                FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, oneAtATime ? "NEXT" : String.valueOf(k + 1), c.current_x,
                        c.current_y + c.hb.height / 2f + 30f * s, k == 0 ? GREEN : GOLD);
        }
        AbstractMonster m = GameBridge.monster(lastPlan.steps.get(0).target);
        if (m != null && lastPotion == null) reticle(sb, m);
    }
}
