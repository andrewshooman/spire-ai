package spireseer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import spireseer.brain.Effects;
import spireseer.brain.Planner;
import spireseer.brain.Sim;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

/** Real combat states (CommunicationMod JSON, as the Python planner reads them) -> Java plan vs Python plan. */
final class StatesParity {

    static int pw(JsonArray powers, String... names) {
        for (JsonElement el : powers) {
            JsonObject p = el.getAsJsonObject();
            String id = p.get("id").getAsString(), name = p.get("name").getAsString();
            for (String n : names) if (n.equals(id) || n.equals(name)) return p.get("amount").getAsInt();
        }
        return 0;
    }

    static boolean hasId(JsonArray powers, String id) {
        for (JsonElement el : powers) if (id.equals(el.getAsJsonObject().get("id").getAsString())) return true;
        return false;
    }

    /** planner.build, from the fixture's raw state. */
    static Sim toSim(JsonObject r) {
        Sim s = new Sim();
        JsonArray pp = r.getAsJsonArray("powers");
        s.energy = r.get("energy").getAsInt();
        s.playerBlock = r.get("block").getAsInt();
        s.hp = r.get("hp").getAsInt();
        s.maxHp = r.get("max_hp").getAsInt();
        s.turn = r.get("turn").getAsInt();
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
        s.barricade = pw(pp, "Barricade") > 0;
        s.hex = pw(pp, "Hex");
        s.stance = "Neutral";
        String room = r.get("room").getAsString();
        s.elite = room.equals("MonsterRoomElite") || room.equals("MonsterRoomBoss");
        s.stall = r.get("stall").getAsInt();
        JsonArray orbs = r.getAsJsonArray("orbs");
        s.orbSlots = orbs.size();
        for (JsonElement el : orbs) {
            JsonObject o = el.getAsJsonObject();
            s.orbs.add(new Sim.Orb(o.get("name").getAsString(), o.get("evoke").getAsInt(), o.get("passive").getAsInt()));
        }
        JsonArray ms = r.getAsJsonArray("monsters");
        int beat = 0, curiosity = 0, warp = -1;
        boolean eater = false;
        for (int i = 0; i < ms.size(); i++) {
            JsonObject m = ms.get(i).getAsJsonObject();
            Sim.Mon x = new Sim.Mon();
            x.index = i;
            x.name = m.get("name").getAsString();
            x.intent = m.get("intent").getAsString();
            x.hp = m.get("hp").getAsInt();
            x.maxHp = m.get("max_hp").getAsInt();
            x.alive = !m.get("gone").getAsBoolean() && !m.get("half_dead").getAsBoolean() && x.hp > 0;
            x.block = m.get("block").getAsInt();
            JsonArray p = m.getAsJsonArray("powers");
            x.vuln = pw(p, "Vulnerable");
            x.weak = pw(p, "Weakened");
            if (x.alive && x.intent.contains("ATTACK")) {
                JsonElement adj = m.get("adj"), base = m.get("base"), hits = m.get("hits");
                int d = adj == null || adj.isJsonNull() || adj.getAsInt() < 0
                        ? (base == null || base.isJsonNull() ? 0 : base.getAsInt()) : adj.getAsInt();
                x.atk = Math.max(0, d);
                x.hits = Math.max(1, hits == null || hits.isJsonNull() ? 1 : Math.max(1, hits.getAsInt()));
            }
            x.poison = pw(p, "Poison");
            x.artifact = pw(p, "Artifact");
            x.barricade = pw(p, "Barricade") > 0 || hasId(p, "Barricade");
            x.thorns = pw(p, "Thorns");
            x.sharp = pw(p, "Sharp Hide");
            x.malleable = pw(p, "Malleable");
            x.curl = pw(p, "Curl Up");
            x.intangible = pw(p, "Intangible") > 0;
            x.invincible = Math.max(0, pw(p, "Invincible"));
            x.stasis = pw(p, "Stasis") != 0;
            x.asleep = x.name.equals("Lagavulin") && x.intent.equals("SLEEP");
            x.nob = pw(p, "Anger", "Enrage");
            x.shifting = pw(p, "Shifting") != 0 || hasId(p, "Shifting");
            x.flight = pw(p, "Flight");
            x.mark = pw(p, "Mark");
            x.strength = pw(p, "Strength");
            x.ritual = pw(p, "Ritual");
            s.mons.add(x);
            if (x.alive) {
                beat = Math.max(beat, pw(p, "BeatOfDeath", "Beat of Death"));
                curiosity = Math.max(curiosity, pw(p, "Curiosity"));
                if (pw(p, "Time Warp") != 0 || x.name.equals("Time Eater")) eater = true;
                warp = Math.max(warp, pw(p, "Time Warp"));
            }
        }
        s.beatOfDeath = beat;
        s.curiosity = curiosity;
        s.timeWarp = eater ? warp : -1;
        JsonArray hand = r.getAsJsonArray("hand");
        for (int k = 0; k < hand.size(); k++) {
            JsonObject h = hand.get(k).getAsJsonObject();
            Sim.Card c = new Sim.Card();
            c.index = k;
            c.name = h.get("name").getAsString();
            c.id = h.get("id").getAsString();
            c.upgrades = h.get("upgrades").getAsInt();
            c.type = h.get("type").getAsString();
            c.cost = h.get("cost").getAsInt();
            c.playable = h.get("playable").getAsBoolean();
            c.targeted = h.get("target").getAsBoolean();
            c.fx = Effects.of(h.get("text").getAsString(), c.type, c.cost, Effects.baseName(c.name));
            s.hand.add(c);
        }
        for (JsonElement d : r.getAsJsonArray("deck")) s.deck.add(d.getAsString());
        return s;
    }

    static Planner.Params params(JsonObject p) {
        Map<String, Double> m = new HashMap<>();
        for (Map.Entry<String, JsonElement> e : p.entrySet()) m.put(e.getKey(), e.getValue().getAsDouble());
        return Planner.Params.from(m);
    }

    static double run(String cls, Path path) throws Exception {
        JsonArray rows = new JsonParser().parse(new String(Files.readAllBytes(path), StandardCharsets.UTF_8)).getAsJsonArray();
        int n = 0, first = 0, whole = 0, endAgree = 0, shown = 0;
        double gainErr = 0;
        long t0 = System.nanoTime();
        for (JsonElement el : rows) {
            JsonObject r = el.getAsJsonObject();
            Sim s = toSim(r);
            Planner.Params P = params(r.getAsJsonObject("params"));
            Planner.Plan plan = Planner.plan(s, P);
            JsonObject py = r.getAsJsonObject("plan");
            JsonArray steps = py.getAsJsonArray("steps");
            StringBuilder jb = new StringBuilder(), pb = new StringBuilder();
            for (Planner.Step st : plan.steps) jb.append(st.card).append('>').append(st.target).append(' ');
            for (JsonElement st : steps) pb.append(st.getAsJsonArray().get(0).getAsInt()).append('>').append(st.getAsJsonArray().get(1).getAsInt()).append(' ');
            boolean pyEnd = steps.size() == 0 || py.get("gain").getAsDouble() < P.endThreshold;
            boolean jEnd = plan.endTurn(P);
            String jf = plan.steps.isEmpty() ? "" : plan.steps.get(0).card + ">" + plan.steps.get(0).target;
            String pf = steps.size() == 0 ? "" : steps.get(0).getAsJsonArray().get(0).getAsInt() + ">" + steps.get(0).getAsJsonArray().get(1).getAsInt();
            boolean firstOk = (pyEnd && jEnd) || (!pyEnd && !jEnd && jf.equals(pf));
            n++;
            if (firstOk) first++;
            if (jb.toString().equals(pb.toString())) whole++;
            if (pyEnd == jEnd) endAgree++;
            gainErr += Math.abs((plan.value - plan.base) - py.get("gain").getAsDouble());
            if (!firstOk && shown < 6) {
                shown++;
                System.out.println("  " + cls + " floor " + r.get("floor") + " turn " + r.get("turn") + ": java [" + jb + "] "
                        + String.format("%+.2f", plan.value - plan.base) + " vs python [" + pb + "] "
                        + String.format("%+.2f", py.get("gain").getAsDouble()) + "  hand " + handNames(s));
            }
        }
        double ms = (System.nanoTime() - t0) / 1e6 / Math.max(1, n);
        System.out.println(String.format("%-8s %d states: first step %.1f%%, whole plan %.1f%%, end-turn %.1f%%, mean |gain diff| %.3f, %.2f ms/plan",
                cls, n, 100.0 * first / n, 100.0 * whole / n, 100.0 * endAgree / n, gainErr / n, ms));
        return first / (double) n;
    }

    static String handNames(Sim s) {
        StringBuilder b = new StringBuilder();
        for (Sim.Card c : s.hand) b.append(c.index).append(':').append(c.name).append(c.cost < 0 ? "(X)" : "(" + c.cost + ")").append(' ');
        return b.toString();
    }
}
