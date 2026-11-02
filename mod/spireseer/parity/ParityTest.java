package spireseer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import spireseer.brain.Effects;

import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Parity with the Python reference (agent/cards.py + agent/planner.py), on fixtures written by dump_fixtures.py:
 *   1. every card text: the Java parser's fields vs Python's
 *   2. real combat states (all classes): the Java planner's plan vs the Python planner's
 * Needs gson on the classpath (it ships inside the game's desktop-1.0.jar). Exit code 1 when below the bars.
 */
public final class ParityTest {

    static String camel(String snake) {
        StringBuilder b = new StringBuilder();
        boolean up = false;
        for (char c : snake.toCharArray()) {
            if (c == '_') { up = true; continue; }
            b.append(up ? Character.toUpperCase(c) : c);
            up = false;
        }
        return b.toString();
    }

    static boolean same(Object java, JsonElement py) {
        if (py.isJsonNull()) return java == null;
        if (java instanceof Boolean) return (Boolean) java == py.getAsBoolean();
        if (java instanceof Integer) return py.getAsDouble() == (Integer) java;
        if (java instanceof Double) return Math.abs(py.getAsDouble() - (Double) java) < 1e-9;
        if (java instanceof String) return py.getAsString().equals(java);
        if (java instanceof List) {
            JsonArray a = py.getAsJsonArray();
            List<?> l = (List<?>) java;
            if (a.size() != l.size()) return false;
            for (int i = 0; i < l.size(); i++) if (!a.get(i).getAsString().equals(l.get(i))) return false;
            return true;
        }
        return false;
    }

    static int effects(Path dir) throws Exception {
        JsonArray rows = new JsonParser().parse(new String(Files.readAllBytes(dir.resolve("effects.json")), StandardCharsets.UTF_8)).getAsJsonArray();
        int bad = 0, n = 0;
        Map<String, Integer> byField = new HashMap<>();
        for (JsonElement el : rows) {
            JsonObject r = el.getAsJsonObject();
            n++;
            Effects e = Effects.of(r.get("text").getAsString(), r.get("type").getAsString(), r.get("cost").getAsInt(), r.get("name").getAsString());
            List<String> diff = new ArrayList<>();
            for (Map.Entry<String, JsonElement> f : r.getAsJsonObject("fx").entrySet()) {
                Field fld;
                try {
                    fld = Effects.class.getField(camel(f.getKey()));
                } catch (NoSuchFieldException ex) {
                    diff.add(f.getKey() + " (missing)");
                    continue;
                }
                Object v = fld.get(e);
                if (!same(v, f.getValue())) diff.add(f.getKey() + " java=" + v + " py=" + f.getValue());
            }
            if (!diff.isEmpty()) {
                bad++;
                for (String d : diff) byField.merge(d.split(" ")[0], 1, Integer::sum);
                if (bad <= 15) System.out.println("  effects " + r.get("name").getAsString() + (r.get("upgraded").getAsBoolean() ? "+" : "") + ": " + diff);
            }
        }
        System.out.println("effects parity: " + (n - bad) + "/" + n + " card texts identical; differing fields " + byField);
        return bad;
    }

    /**
     * The game's own text (localization/eng/cards.json, what the mod reads in a fight) through Effects.normalize vs
     * the database text the parser was written for: numbers set to 7 on both sides, the parsed fields must agree.
     */
    static int gameText(Path dir) throws Exception {
        Path f = dir.resolve("game_cards_eng.json");
        if (!Files.exists(f)) return 0;
        JsonObject loc = new JsonParser().parse(new String(Files.readAllBytes(f), StandardCharsets.UTF_8)).getAsJsonObject();
        JsonArray rows = new JsonParser().parse(new String(Files.readAllBytes(dir.resolve("effects.json")), StandardCharsets.UTF_8)).getAsJsonArray();
        Map<String, JsonObject> byName = new HashMap<>();
        for (Map.Entry<String, JsonElement> e : loc.entrySet()) {
            JsonObject o = e.getValue().getAsJsonObject();
            if (o.has("NAME")) byName.put(o.get("NAME").getAsString(), o);
        }
        int n = 0, bad = 0;
        for (JsonElement el : rows) {
            JsonObject r = el.getAsJsonObject();
            JsonObject g = byName.get(r.get("name").getAsString());
            if (g == null) continue;
            boolean up = r.get("upgraded").getAsBoolean();
            String upd = g.has("UPGRADE_DESCRIPTION") ? g.get("UPGRADE_DESCRIPTION").getAsString() : "";
            String raw = up && !upd.isEmpty() && !upd.equalsIgnoreCase("DEPRECATED") && !upd.equalsIgnoreCase("Deprecated.") ? upd : g.get("DESCRIPTION").getAsString();
            String type = r.get("type").getAsString();
            int cost = r.get("cost").getAsInt();
            Effects a = Effects.parseText(Effects.normalize(raw, 7, 7, 7), type, cost);
            Effects b = Effects.parseText(r.get("text").getAsString(), type, cost);
            n++;
            List<String> diff = new ArrayList<>();
            for (Field fld : Effects.class.getFields()) {
                if (java.lang.reflect.Modifier.isStatic(fld.getModifiers())) continue;
                Object x = shape(fld.get(a)), y = shape(fld.get(b));
                if (x == null ? y != null : !x.equals(y)) diff.add(fld.getName() + " game=" + fld.get(a) + " db=" + fld.get(b));
            }
            if (!diff.isEmpty()) {
                bad++;
                if (bad <= 25) System.out.println("  game text " + r.get("name").getAsString() + (up ? "+" : "") + ": " + diff
                        + "\n      game: " + Effects.normalize(raw, 7, 7, 7) + "\n      db:   " + r.get("text").getAsString());
            }
        }
        System.out.println("game text parity: " + (n - bad) + "/" + n + " card texts read the same from the game's wording");
        return bad;
    }

    /** Which effect, not how much: numbers differ between the two texts (placeholders vs printed values). */
    static Object shape(Object v) {
        if (v instanceof Integer) return (Integer) v == 0 ? "0" : (Integer) v < 0 ? "-" : "+";
        if (v instanceof Double) return (Double) v == 0 ? "0" : (Double) v < 0 ? "-" : "+";
        if (v instanceof List) return new java.util.TreeSet<Object>((List<?>) v).toString();
        return v;
    }

    public static void main(String[] args) throws Exception {
        Path dir = Paths.get(args.length > 0 ? args[0] : "mod/spireseer/parity/fixtures");
        int badFx = effects(dir);
        int badGame = gameText(dir);
        double worst = 1.0;
        if (args.length < 2 || !args[1].equals("effects")) {
            for (String cls : new String[]{"ironclad", "silent", "defect", "watcher"}) {
                Path p = dir.resolve("states_" + cls + ".json");
                if (!Files.exists(p)) continue;
                worst = Math.min(worst, StatesParity.run(cls, p));
            }
        }
        System.out.println("PARITY " + (badFx == 0 && worst >= 0.95 ? "OK" : "LOW"));
        if (badFx > 0 || worst < 0.95) System.exit(1);
    }
}
