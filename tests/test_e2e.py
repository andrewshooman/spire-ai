"""End-to-end test: a fake Slay the Spire and a fake LLM drive the real loop.

The fake game walks every screen the policy handles (Neow, map, combat with
turns, rewards, card reward, events, rest + upgrade, shop + purchase, chest,
hand select, game over) and dies at a random floor, so learning, selection,
the coach, run summaries and the dashboard all get exercised.

Run from C:\\sts-evolve:  sts-bench\\.venv\\Scripts\\python.exe tests\\test_e2e.py
"""

from __future__ import annotations

import argparse
import copy
import http.server
import json
import random
import socket
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
BENCH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sts-bench"
FIX = BENCH / "tests" / "fixtures" / "states"

import evolve  # noqa: E402
from sts_bench.env import HarnessServer  # noqa: E402


def fx(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class FakeSpire(threading.Thread):
    def __init__(self, port: int, rng: random.Random):
        super().__init__(daemon=True)
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.rfile = self.sock.makefile("rb")
        self.rng = rng
        self.cur = fx("out_of_game-1.json")
        self.commands: list[str] = []
        self.runs = 0
        self.stall_left = 0      # lines to swallow: simulates the game hanging mid-animation
        self.stalled = False

    def send(self, state: dict) -> None:
        self.cur = state
        self.sock.sendall(json.dumps(state).encode() + b"\n")

    def gs(self, base: dict, floor: int, room: str, **kw) -> dict:
        s = copy.deepcopy(base)
        g = s["game_state"]
        g.update(floor=floor, room_type=room, act=1, current_hp=self.hp, max_hp=80)
        g["class"] = "IRONCLAD"
        g.update(kw)
        return s

    # -- screens
    def neow(self, opts: list[str]) -> dict:
        s = self.gs(fx("event-1.json"), 0, "NeowRoom")
        s["game_state"]["screen_state"]["options"] = [
            {"choice_index": i, "text": t, "label": t, "disabled": False} for i, t in enumerate(opts)]
        s["game_state"]["choice_list"] = [t.lower() for t in opts]
        return s

    def combat(self) -> dict:
        s = self.gs(fx("none-1.json"), self.floor, "MonsterRoomElite" if self.floor == 6 else "MonsterRoom")
        c = s["game_state"]["combat_state"]
        c["turn"] = self.turn
        c["player"].update(energy=self.energy, current_hp=self.hp, max_hp=80)
        c["hand"] = [h for i, h in enumerate(c["hand"]) if i not in self.played]
        for h in c["hand"]:
            h["is_playable"] = h["cost"] <= self.energy
        m = c["monsters"][0]
        m.update(current_hp=max(0, self.monster_hp), intent="ATTACK", move_base_damage=8,
                 move_adjusted_damage=8, move_hits=1, is_gone=self.monster_hp <= 0)
        return s

    def game_over(self) -> dict:
        s = self.gs(fx("none-1.json"), self.floor, "MonsterRoom")
        g = s["game_state"]
        g.update(screen_type="GAME_OVER", screen_state={"score": self.floor * 10, "victory": False},
                 choice_list=[], current_hp=0)
        s["available_commands"] = ["proceed", "key", "click", "wait", "state"]
        return s

    def new_fight(self, floor: int) -> dict:
        self.floor, self.turn, self.energy, self.played, self.monster_hp = floor, 1, 3, set(), 30
        return self.combat()

    def after_fight(self) -> dict:
        if self.floor >= self.death_floor:
            return self.game_over()
        self.rewards = ["GOLD", "POTION", "CARD"]
        return self.reward_screen()

    def reward_screen(self) -> dict:
        s = self.gs(fx("combat_reward-1.json"), self.floor, "MonsterRoom")
        g = s["game_state"]
        base = {r["reward_type"]: r for r in fx("combat_reward-1.json")["game_state"]["screen_state"]["rewards"]}
        g["screen_state"]["rewards"] = [base[r] for r in self.rewards]
        g["choice_list"] = [r.lower() for r in self.rewards]
        return s

    def map_to(self, floor: int, sym: str) -> dict:
        s = self.gs(fx("map-2.json"), floor - 1, "MonsterRoom")
        s["game_state"]["screen_state"]["next_nodes"][0]["symbol"] = sym
        self.next_sym, self.next_floor = sym, floor
        return s

    def enter(self) -> dict:
        f, sym = self.next_floor, self.next_sym
        if sym in ("M", "E"):
            return self.new_fight(f)
        if sym == "?":
            self.floor = f
            s = self.gs(fx("event-1.json"), f, "EventRoom")
            opts = ["[Leave] Nothing happens.", "[Pray] Lose 10 HP. Obtain a random relic.", "[Gamble] Become Cursed. Gain 100 gold."]
            s["game_state"]["screen_state"].update(event_id="Test", event_name="Test Shrine", body_text="A shrine.",
                                                   options=[{"choice_index": i, "text": t, "label": t.split("]")[0][1:], "disabled": False} for i, t in enumerate(opts)])
            s["game_state"]["choice_list"] = ["leave", "pray", "gamble"]
            self.stage = "event"
            return s
        if sym == "R":
            self.floor = f
            self.stage = "rest"
            return self.gs(fx("rest-1.json"), f, "RestRoom")
        if sym == "$":
            self.floor = f
            self.stage = "shoproom"
            s = self.gs(fx("rest-1.json"), f, "ShopRoom")
            s["game_state"].update(screen_type="SHOP_ROOM", screen_state={}, choice_list=["shop"])
            s["available_commands"] = ["choose", "proceed", "key", "click", "wait", "state"]
            return s
        if sym == "T":
            self.floor = f
            self.stage = "chest"
            return self.gs(fx("chest-1.json"), f, "TreasureRoom")
        raise AssertionError(sym)

    ROUTE = ["M", "?", "R", "$", "T", "E", "M", "M"]

    def next_map(self) -> dict:
        nxt = self.floor + 1
        return self.map_to(nxt, self.ROUTE[(nxt - 1) % len(self.ROUTE)])

    def run(self) -> None:
        try:
            while True:
                raw = self.rfile.readline()
                if not raw:
                    return
                line = raw.decode().strip()
                self.commands.append(line)
                if self.stall_left > 0:
                    self.stall_left -= 1
                    continue
                self.send(self.respond(line))
        except OSError:
            return

    def respond(self, line: str) -> dict:
        if line == "ready" or line == "state":
            return self.cur
        if line.startswith(("wait", "key")):
            return self.cur
        if line.startswith("start"):
            self.runs += 1
            self.hp, self.floor, self.stage = 80, 0, "neow1"
            self.death_floor = self.rng.choice([1, 6, 6, 8])
            return self.neow(["[Talk]"])
        g = self.cur.get("game_state") or {}
        st = g.get("screen_type")
        if st == "GAME_OVER":
            return fx("out_of_game-1.json")
        if self.stage == "neow1":
            self.stage = "neow2"
            return self.neow(["Remove a card from your deck", "Obtain a random common Relic",
                              "Max HP +8", "Lose your starting Relic Obtain a random boss Relic"])
        if self.stage == "neow2":
            self.stage = "map"
            return self.gs(fx("map-1.json"), 0, "NeowRoom")
        if st == "MAP":
            if self.floor == 0:
                self.next_sym, self.next_floor = "M", 1
            return self.enter()
        if st == "NONE" and g.get("combat_state"):
            if line.startswith("play"):
                parts = line.split()
                hand_now = [i for i in range(len(fx("none-1.json")["game_state"]["combat_state"]["hand"])) if i not in self.played]
                self.played.add(hand_now[int(parts[1]) - 1])
                self.energy -= 1
                self.monster_hp -= 9
                if self.monster_hp <= 0:
                    return self.after_fight()
                if len(self.played) == 1 and self.floor == 6 and self.turn == 1:
                    s = self.gs(fx("hand_select-1.json"), self.floor, "MonsterRoomElite")
                    s["game_state"]["current_action"] = "ExhaustAction"
                    self.stage = "handselect"
                    return s
                return self.combat()
            if line.startswith("potion"):
                return self.combat()
            if line == "end" and self.runs == 3 and not self.stalled:
                self.stalled = True
                self.stall_left = 4   # end + nudge, fallback + nudge: all unanswered
                return self.cur
            if line == "end":
                self.turn += 1
                self.hp -= 8
                if self.hp <= 0 or self.turn > 4:
                    return self.after_fight() if self.hp > 0 else self.game_over()
                self.energy, self.played = 3, set()
                return self.combat()
        if st == "HAND_SELECT":
            return self.combat()
        if st == "COMBAT_REWARD":
            if line.startswith("choose"):
                k = int(line.split()[1])
                kind = self.rewards[k]
                if kind == "CARD":
                    s = self.gs(fx("card_reward-1.json"), self.floor, "MonsterRoom")
                    return s
                self.rewards.pop(k)
                return self.reward_screen()
            return self.next_map()
        if st == "CARD_REWARD":
            if line.startswith("choose"):
                self.rewards = [r for r in self.rewards if r != "CARD"]
            return self.reward_screen()
        if st == "EVENT":
            return self.next_map()
        if st == "REST":
            if line == "choose 1":
                return self.gs(fx("grid-1.json"), self.floor, "RestRoom")
            if line.startswith("choose"):
                s = self.gs(fx("rest-1.json"), self.floor, "RestRoom")
                s["game_state"]["screen_state"]["has_rested"] = True
                s["available_commands"] = ["proceed", "key", "click", "wait", "state"]
                s["game_state"]["choice_list"] = []
                return s
            return self.next_map()
        if st == "GRID":
            s = self.gs(fx("rest-1.json"), self.floor, "RestRoom")
            s["game_state"]["screen_state"]["has_rested"] = True
            s["available_commands"] = ["proceed", "key", "click", "wait", "state"]
            s["game_state"]["choice_list"] = []
            return s
        if st == "SHOP_ROOM":
            if line.startswith("choose"):
                self.buys = 0
                return self.gs(fx("shop_screen-1.json"), self.floor, "ShopRoom")
            return self.next_map()
        if st == "SHOP_SCREEN":
            if line.startswith("choose") and self.buys < 2:
                self.buys += 1
                return self.gs(fx("shop_screen-1.json"), self.floor, "ShopRoom")
            s = self.gs(fx("rest-1.json"), self.floor, "ShopRoom")
            s["game_state"].update(screen_type="SHOP_ROOM", screen_state={}, choice_list=["shop"])
            s["available_commands"] = ["proceed", "key", "click", "wait", "state"]
            return s
        if st == "CHEST":
            self.rewards = ["RELIC"]
            s = self.gs(fx("combat_reward-2.json"), self.floor, "TreasureRoom")
            return s
        return self.next_map()


class FakeLLM(http.server.BaseHTTPRequestHandler):
    calls: list[str] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = body["messages"][0]["content"]
        if "coach" in system:
            text = ("<think>hmm</think>NOTES:\n- Dies to elites with low HP.\n- Card picks too greedy.\n"
                    "NUDGES:\n```json\n{\"card\": {\"Zap\": 0.2}, \"node\": {\"E:h1\": -0.2}, \"params\": {\"block\": 0.1}}\n```\n"
                    "LEARNED:\n- Rest before elites when under 60% HP.\n")
            FakeLLM.calls.append("coach")
        else:
            text = "1"
            FakeLLM.calls.append("event")
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def main() -> None:
    llm = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp())
    args = argparse.Namespace(
        character="ironclad", ascension=0, model="fake", reflect_model="fake",
        base_url=f"http://127.0.0.1:{llm.server_address[1]}/v1", llm_events=True, coach_every=3,
        coach_think=False, lr=0.08, cycle=5, bench_seeds=["BENCHA1", "BENCHB2"], revert_margin=1.0,
        max_runs=10, port=0, workdir=str(work / "evolve" / "ironclad"),
        stall_wait=30, relaunch_game=False, max_aborts=3, step_timeout=2.0,
        strategy_baseline=str(ROOT / "agent" / "strategy_baseline.md"))
    with HarnessServer(port=0) as server:
        game = FakeSpire(server.port, random.Random(7))
        game.start()
        rc = evolve.run_loop(args, server=server, log_dir=work / "logs")
    ws = Path(args.workdir)
    runs = sorted((ws / "runs").glob("run_*.json"))
    print("rc", rc, "| runs", len(runs), "| llm calls", {k: FakeLLM.calls.count(k) for k in set(FakeLLM.calls)})
    last = json.loads(runs[-1].read_text())
    print("last run: floor", last["floor"], "| rooms", last["rooms"], "| picks", len(last["picks"]),
          "| decisions", last["decisions"], "forced", last["forced"])
    print("progress.csv:\n" + (ws / "progress.csv").read_text())
    print("coach.md head:", (ws / "coach.md").read_text()[:200].replace("\n", " | "))
    print("learned:", (ws / "strategy.md").read_text().partition("## Learned from runs")[2].strip()[:200])
    model = json.loads((ws / "model.json").read_text())
    print("trained", model["meta"]["runs_trained"], "| weights", len(model["weights"]),
          "| sample", sorted(model["weights"].items())[:6])
    print("dashboard bytes", len((ws / "dashboard.html").read_text()))
    commands = set(c.split()[0] for c in game.commands)
    print("wire verbs used:", sorted(commands))
    aborted = sorted((ws / "runs" / "aborted").glob("*.json"))
    print("aborted runs:", [json.loads(a.read_text())["aborted"][:60] for a in aborted])
    assert game.stalled and len(aborted) == 1, "the stalled run must be set aside, not learned from"
    assert rc == 0 and len(runs) == 10 and model["meta"]["runs_trained"] == 6
    assert (ws / "champion_model.json").exists() and (ws / "dashboard.html").exists()
    print("E2E OK")


if __name__ == "__main__":
    main()
