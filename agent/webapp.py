"""webapp.py -- the bot's local web app: http://127.0.0.1:8765/

Serves agent/web/app.html plus a small JSON API over the run files, live state and the
run settings. Runs inside the bot (evolve.py starts it) or on its own:

    python agent/webapp.py            (dashboard.bat does this when the bot isn't running)

API (all GET unless noted; ?char=ironclad|silent|defect|watcher):
    /api/characters        workspaces with run counts, wins, best floor, live?
    /api/summary?since=N   overview + insights numbers (stats.summary)
    /api/runs              compact list of every run
    /api/run?n=N           one run in full (picks, shops, events, fights, LLM calls, HP...)
    /api/live              what the bot sees right now (live.json)
    /api/sim               simulator bench, training curve, calibration
    /api/settings          run settings;  POST /api/settings  saves them (next run applies them)

Only listens on 127.0.0.1.
"""

from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stats  # noqa: E402

EVOLVE = HERE.parent / "evolve"
SETTINGS_PATH = EVOLVE / "settings.json"

# what the page may change; evolve.py applies them at the start of the next run
SETTINGS_SCHEMA = {
    "character": {"type": "choice", "options": list(stats.CHARACTERS), "default": "ironclad",
                  "label": "Character", "help": "Switches to that character's own model and history at the next run."},
    "ascension": {"type": "int", "min": 0, "max": 20, "default": 0, "label": "Ascension"},
    "llm_events": {"type": "bool", "default": True, "label": "LLM for Neow, events, boss relics",
                   "help": "Off = the learned policy decides alone (no Ollama needed)."},
    "llm_cards": {"type": "bool", "default": True, "label": "LLM advises card rewards and upgrades"},
    "llm_shop": {"type": "bool", "default": True, "label": "LLM shopping list in shops"},
    "llm_share": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.7, "label": "Share of card picks the LLM makes in training runs"},
    "coach_every": {"type": "int", "min": 0, "max": 50, "default": 5, "label": "Coach review every N training runs (0 = off)"},
    "ab": {"type": "bool", "default": True, "label": "A/B experiments in training runs",
           "help": "Randomly switches HP-budget pathing, LLM card advice, synergy notes, skip rules and the "
                   "event-room pull per run."},
    "human_learn": {"type": "bool", "default": True, "label": "Learn from your own runs",
                    "help": "Runs you play on another profile (save slot) or drop into evolve/human_inbox "
                            "(import_runs.bat <folder>, e.g. from the Steam Deck). Teaches picks, relics and rests."},
    "human_lr": {"type": "float", "min": 0.0, "max": 0.5, "default": 0.1,
                 "label": "Weight of your runs vs the bot's own",
                 "help": "Kept low on purpose: your runs are one data point, not something to copy."},
    "expert_lr": {"type": "float", "min": 0.0, "max": 0.5, "default": 0.1,
                  "label": "Weight of expert players' runs",
                  "help": "Strong players' A20 run files (import_runs.bat --expert <folder>): their card, boss relic "
                          "and rest choices are learned once, and their track record goes into the LLM prompts. 0 = ignore."},
    "pause": {"type": "bool", "default": False, "label": "Pause after the current run",
              "help": "The bot waits at the main menu until this is turned off."},
}


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    return {k: data[k] for k in SETTINGS_SCHEMA if k in data}


def validate(new: dict) -> dict:
    out = {}
    for k, v in new.items():
        spec = SETTINGS_SCHEMA.get(k)
        if spec is None:
            continue
        t = spec["type"]
        if t == "bool":
            out[k] = bool(v)
        elif t == "int":
            out[k] = max(spec["min"], min(spec["max"], int(v)))
        elif t == "float":
            out[k] = max(spec["min"], min(spec["max"], float(v)))
        elif t == "choice" and v in spec["options"]:
            out[k] = v
    return out


def save_settings(new: dict) -> dict:
    cur = load_settings()
    cur.update(validate(new))
    EVOLVE.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)
    return cur


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "SpireAI"

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        char = q.get("char", "ironclad")
        if char not in stats.CHARACTERS:
            return self._json({"error": "unknown character"}, 400)
        try:
            if url.path in ("/", "/index.html"):
                return self._send(200, (HERE / "web" / "app.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/characters":
                return self._json({"characters": stats.characters(), "settings": load_settings()})
            if url.path == "/api/summary":
                return self._json(stats.summary(char, int(q.get("since", 0))))
            if url.path == "/api/runs":
                return self._json(stats.runs_list(char))
            if url.path == "/api/run":
                r = stats.run_detail(char, int(q.get("n", 0)))
                return self._json(r) if r else self._json({"error": "no such run"}, 404)
            if url.path == "/api/live":
                p = EVOLVE / char / "live.json"
                return self._send(200, p.read_bytes() if p.exists() else b"{}", "application/json")
            if url.path == "/api/human":
                return self._json(stats.human_summary(char))
            if url.path == "/api/sim":
                return self._json(stats.sim_data(char))
            if url.path == "/api/settings":
                return self._json({"schema": SETTINGS_SCHEMA, "values": load_settings()})
            self._json({"error": "not found"}, 404)
        except Exception as exc:  # the page is a view; report, never crash the bot
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/api/settings":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
            self._json({"values": save_settings(body), "note": "Applies at the start of the next run."})
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)


def serve(port: int = 8765) -> http.server.ThreadingHTTPServer | None:
    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True, name="webapp").start()
    return httpd


def main() -> None:
    import webbrowser
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Spire AI web app: http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    webbrowser.open(f"http://127.0.0.1:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
