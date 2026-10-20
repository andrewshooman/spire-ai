package autospire;

import autospire.brain.Planner;
import autospire.brain.Sim;
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
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.Properties;

/**
 * AutoSpire: combat suggestions or auto-battle for Slay the Spire.
 *
 *   F7  cycle mode: OFF -> SUGGEST (highlights the best play) -> AUTO (plays the turn for you)
 *   F8  pause / resume AUTO
 *
 * AUTO never starts by itself: every launch begins in OFF or SUGGEST (whichever you used last), it is
 * off in Daily Climbs, it waits while you drag or target a card, and choice screens (Armaments, Headbutt,
 * card rewards...) are left to you.
 */
@SpireInitializer
public class AutoSpire implements PostInitializeSubscriber, PostUpdateSubscriber, PostRenderSubscriber {

    public static final Logger log = LogManager.getLogger("AutoSpire");

    enum Mode { OFF, SUGGEST, AUTO }

    static Mode mode = Mode.SUGGEST;
    static boolean paused;
    static boolean autoEndTurn = true;
    static boolean showInAuto = true;
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

    public static void initialize() {
        BaseMod.subscribe(new AutoSpire());
        try {
            Properties d = new Properties();
            d.setProperty("mode", "SUGGEST");
            d.setProperty("autoEndTurn", "true");
            d.setProperty("showInAuto", "true");
            d.setProperty("delayMs", "350");
            config = new SpireConfig("AutoSpire", "config", d);
            mode = "OFF".equals(config.getString("mode")) ? Mode.OFF : Mode.SUGGEST;   // never AUTO at launch
            autoEndTurn = config.getBool("autoEndTurn");
            showInAuto = config.getBool("showInAuto");
            delayMs = Math.max(0, config.getInt("delayMs"));
        } catch (Exception e) {
            log.warn("AutoSpire config not loaded: " + e);
        }
    }

    static void save() {
        if (config == null) return;
        try {
            config.setString("mode", mode == Mode.OFF ? "OFF" : "SUGGEST");
            config.setBool("autoEndTurn", autoEndTurn);
            config.setBool("showInAuto", showInAuto);
            config.setInt("delayMs", delayMs);
            config.save();
        } catch (Exception e) {
            log.warn("AutoSpire config not saved: " + e);
        }
    }

    @Override
    public void receivePostInitialize() {
        ModPanel panel = new ModPanel();
        float x = 380f, y = 720f;
        panel.addUIElement(new ModLabel("AutoSpire  -  F7: Off / Suggest / Auto,  F8: pause auto", x, y,
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
        Texture badge = ImageMaster.loadImage("autospireResources/badge.png");
        BaseMod.registerModBadge(badge, "AutoSpire", "Andrew",
                "Combat suggestions or auto-battle. F7 cycles Off / Suggest / Auto, F8 pauses.", panel);
    }

    @Override
    public void receivePostUpdate() {
        try {
            update();
        } catch (Exception e) {   // a view/helper must never crash the game
            log.warn("AutoSpire update failed: " + e);
            lastPlan = null;
            lastSig = null;
        }
    }

    private static void update() {
        if (Gdx.input.isKeyJustPressed(Input.Keys.F7)) {
            mode = Mode.values()[(mode.ordinal() + 1) % Mode.values().length];
            paused = false;
            save();
        }
        if (Gdx.input.isKeyJustPressed(Input.Keys.F8) && mode == Mode.AUTO) paused = !paused;
        if (mode == Mode.OFF || !GameBridge.ready()) {
            if (mode == Mode.OFF || !inCombat()) lastPlan = null;
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
            lastSig = sig;
        }
        boolean autoOk = mode == Mode.AUTO && !paused && !Settings.isDailyRun;
        status = mode == Mode.AUTO ? (Settings.isDailyRun ? "AUTO is off in Daily Climbs" : paused ? "AUTO paused (F8)" : "")
                : "";
        if (!autoOk || GameBridge.playerBusy() || System.currentTimeMillis() < nextActionAt) return;
        if (lastPlan.endTurn(params)) {
            if (autoEndTurn) GameBridge.endTurn();
        } else if (!GameBridge.play(lastPlan.steps.get(0))) {
            log.info("AutoSpire: planned card no longer playable; replanning");
        }
        lastSig = null;   // re-read the game after every action
        nextActionAt = System.currentTimeMillis() + delayMs;
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
            log.warn("AutoSpire render failed: " + e);
        }
    }

    private static final Color GOLD = new Color(0.95f, 0.78f, 0.32f, 1f);
    private static final Color DIM = new Color(0.8f, 0.78f, 0.7f, 0.85f);

    private static void render(SpriteBatch sb) {
        if (CardCrawlGame.mode != CardCrawlGame.GameMode.GAMEPLAY || !AbstractDungeon.isPlayerInDungeon()) return;
        float s = Settings.scale;
        String head = "AutoSpire: " + mode + (status.isEmpty() ? "" : " - " + status) + "   (F7 mode, F8 pause)";
        FontHelper.renderFontLeftTopAligned(sb, FontHelper.tipBodyFont, head, 24f * s, Settings.HEIGHT - 70f * s, DIM);
        if (mode == Mode.OFF || lastPlan == null || lastSim == null || !inCombat()) return;
        if (mode == Mode.AUTO && !showInAuto) return;
        String line = (lastPlan.endTurn(params) ? "Suggested: end turn" : "Suggested: " + lastPlan.describe(lastSim));
        FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, line, Settings.WIDTH / 2f, Settings.HEIGHT * 0.74f, GOLD);
        if (lastPlan.endTurn(params) || lastPlan.steps.isEmpty()) return;
        Planner.Step first = lastPlan.steps.get(0);
        AbstractCard c = GameBridge.handCard(first.card);
        if (c != null)
            FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "PLAY", c.current_x,
                    c.current_y + c.hb.height / 2f + 34f * s, GOLD);
        AbstractMonster m = GameBridge.monster(first.target);
        if (m != null)
            FontHelper.renderFontCentered(sb, FontHelper.panelNameFont, "TARGET", m.hb.cX,
                    m.hb.y + m.hb.height + 60f * s, GOLD);
    }
}
