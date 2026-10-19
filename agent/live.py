"""live.py -- what the bot sees and decides right now, for the live page.

The policy calls LiveView.update() on every decision; it writes live.json in the
workspace at most about once a second (immediately when the screen changes), and
evolve.py serves the workspace over http://127.0.0.1:8765 so the page can poll it.
"""

from __future__ import annotations

import collections
import functools
import http.server
import json
import os
import threading
import time
from pathlib import Path

INTERVAL = 0.8


def _powers(powers) -> list[list]:
    return [[p.name, p.amount] for p in powers or ()]


def _card(c) -> dict:
    return {"name": c.name, "cost": c.cost, "type": c.type.value, "up": c.upgrades,
            "ok": bool(getattr(c, "is_playable", True))}


class LiveView:
    def __init__(self, path: Path | None, info: dict | None = None):
        self.path = Path(path) if path else None
        self.info = dict(info or {})
        self.decisions: collections.deque = collections.deque(maxlen=40)
        self.llm: collections.deque = collections.deque(maxlen=40)   # LLM calls, newest first, across runs
        self.route: list[list[int]] = []     # chosen map nodes this act: [x, y]
        self.route_act = 0
        self.hp: list[list[int]] = []        # [floor, hp, max_hp] this run
        self.started = time.time()
        self._last_write = 0.0
        self._last_screen = ""

    def new_run(self, info: dict) -> None:
        self.info = dict(info)
        self.route, self.route_act, self.hp = [], 0, []
        self.decisions.clear()
        self.started = time.time()

    def llm_call(self, entry: dict) -> None:
        self.llm.appendleft(dict(entry, run=self.info.get("run")))
        self._last_write = 0.0  # show it on the next update

    # ------------------------------------------------------------- recording
    def chose_node(self, act: int, x: int, y: int) -> None:
        if act != self.route_act:
            self.route, self.route_act = [], act
        self.route.append([x, y])

    def update(self, message, action_text: str, why: str, tracker, force: bool = False) -> None:
        if self.path is None or message is None or message.game_state is None:
            return
        g = message.game_state
        screen = g.screen_type.value if g.combat_state is None or g.screen_type.value != "NONE" else "COMBAT"
        hp = g.combat_state.player.current_hp if g.combat_state is not None else g.current_hp
        if self.hp and self.hp[-1][0] == g.floor:
            self.hp[-1] = [g.floor, hp, g.max_hp]
        else:
            self.hp.append([g.floor, hp, g.max_hp])
        if action_text:
            self.decisions.append({"t": time.strftime("%H:%M:%S"), "floor": g.floor, "screen": screen,
                                   "action": action_text, "why": why})
        now = time.monotonic()
        changed = screen != self._last_screen
        self._last_screen = screen
        if not (force or changed or now - self._last_write >= INTERVAL):
            return
        self._last_write = now
        try:
            self._write(self._snapshot(g, screen, tracker))
        except Exception:
            pass  # the live page is a view: never let it disturb a run

    # ------------------------------------------------------------- snapshot
    def _snapshot(self, g, screen: str, tracker) -> dict:
        snap = dict(self.info)
        snap.update({
            "updated": time.time(), "screen": screen, "floor": g.floor, "act": g.act, "act_boss": g.act_boss,
            "hp": g.current_hp, "max_hp": g.max_hp, "gold": g.gold, "ascension": g.ascension_level,
            "character": g.character,
            "deck": [_card(c) for c in g.deck],
            "relics": [[r.name, r.counter] for r in g.relics],
            "potions": [p.name for p in g.potions],
            "plan": tracker.plan if tracker else {},
            "llm_calls": tracker.llm_calls if tracker else 0,
            "llm": list(self.llm),
            "hp_track": self.hp,
            "stats": {"elapsed": round(time.time() - self.started), "decisions": sum((tracker.counts or {}).values())
                      if tracker else 0, "llm_calls": tracker.llm_calls if tracker else 0,
                      "llm_s": round((tracker.llm_ms if tracker else 0) / 1000, 1),
                      "llm_cached": tracker.llm_cached if tracker else 0,
                      "potions_used": len(tracker.potions) if tracker else 0,
                      "picks": len(tracker.picks) if tracker else 0,
                      "skips": sum(1 for p in (tracker.picks if tracker else []) if p.get("chosen") == "(skip)"),
                      "bought": [x["bought"] for x in (tracker.shops if tracker else []) if x["bought"] != "(leave)"]},
            "decisions": list(self.decisions)[::-1],
            "map": {"nodes": [[n.x, n.y, n.symbol, [[ch.x, ch.y] for ch in n.children]] for n in g.map],
                    "route": self.route if self.route_act == g.act else []},
        })
        c = g.combat_state
        if c is not None:
            p = c.player
            snap["combat"] = {
                "turn": c.turn, "energy": p.energy, "block": p.block, "hp": p.current_hp, "max_hp": p.max_hp,
                "powers": _powers(p.powers), "hand": [_card(h) for h in c.hand],
                "draw": len(c.draw_pile), "discard": len(c.discard_pile), "exhaust": len(c.exhaust_pile),
                "monsters": [{"name": m.name, "hp": m.current_hp, "max_hp": m.max_hp, "block": m.block,
                              "intent": m.intent.value,
                              "dmg": (m.move_adjusted_damage if (m.move_adjusted_damage or -1) >= 0
                                      else m.move_base_damage),
                              "hits": m.move_hits, "powers": _powers(m.powers),
                              "gone": bool(m.is_gone or m.current_hp <= 0)} for m in c.monsters],
            }
        return snap

    def _write(self, snap: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, separators=(",", ":")), encoding="utf-8")
        for _ in range(5):  # a reader may hold the file open for a moment on Windows
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.05)


# ----------------------------------------------------------------- server

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(root: Path, port: int) -> http.server.ThreadingHTTPServer | None:
    """Serve the workspace (index.html, live.json, ...) on localhost in a daemon thread."""
    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                                functools.partial(_Quiet, directory=str(root)))
    except OSError:
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True, name="live-server").start()
    return httpd
