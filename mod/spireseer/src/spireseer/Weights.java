package spireseer;

import com.badlogic.gdx.Gdx;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import spireseer.brain.Planner;

import java.util.HashMap;
import java.util.Map;

/**
 * Combat weights per character, learned by the Spire AI bot and bundled at build time
 * (spireseerResources/weights.json, written by build.ps1 from evolve/<character>/model.json).
 * A character the bot hasn't trained on yet uses the Ironclad weights.
 */
public final class Weights {

    private static final Map<String, Planner.Params> BY_CLASS = new HashMap<>();
    private static final Map<String, Integer> RUNS = new HashMap<>();
    private static boolean loaded;

    private Weights() {}

    static String key(String playerClass) {
        if (playerClass == null) return "ironclad";
        switch (playerClass) {
            case "THE_SILENT": return "silent";
            case "DEFECT": return "defect";
            case "WATCHER": return "watcher";
            case "IRONCLAD": return "ironclad";
            default: return playerClass.toLowerCase();
        }
    }

    static void load() {
        loaded = true;
        try {
            String txt = Gdx.files.internal("spireseerResources/weights.json").readString("UTF-8");
            JsonObject root = new JsonParser().parse(txt).getAsJsonObject();
            for (Map.Entry<String, JsonElement> e : root.entrySet()) {
                JsonObject c = e.getValue().getAsJsonObject();
                Map<String, Double> m = new HashMap<>();
                for (Map.Entry<String, JsonElement> p : c.getAsJsonObject("params").entrySet())
                    m.put(p.getKey(), p.getValue().getAsDouble());
                BY_CLASS.put(e.getKey(), Planner.Params.from(m));
                RUNS.put(e.getKey(), c.has("runs") ? c.get("runs").getAsInt() : 0);
            }
            Spireseer.log.info("Spireseer weights loaded for " + BY_CLASS.keySet());
        } catch (Exception ex) {
            Spireseer.log.warn("Spireseer: no bundled weights, using defaults (" + ex + ")");
        }
    }

    /** The weights for this character, falling back to Ironclad's, then to the built-in defaults. */
    public static Planner.Params forClass(String playerClass) {
        if (!loaded) load();
        Planner.Params p = BY_CLASS.get(key(playerClass));
        if (p == null) p = BY_CLASS.get("ironclad");
        return p == null ? new Planner.Params() : p;
    }

    /** "Silent weights (120 runs)" or "Ironclad weights (Watcher not trained yet)". */
    public static String label(String playerClass) {
        if (!loaded) load();
        String k = key(playerClass);
        String name = Character.toUpperCase(k.charAt(0)) + k.substring(1);
        if (BY_CLASS.containsKey(k)) return name + " weights (" + RUNS.get(k) + " runs)";
        return "Ironclad weights (" + name + " not trained yet)";
    }
}
