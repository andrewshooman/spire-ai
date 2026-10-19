"""evolve.py -- train a fast Slay the Spire policy by playing, forever.

Every move is made by the learned policy (policy.py) in milliseconds; the
game's own animation speed is the limit, so use SuperFastMode. Runs come in
cycles (default 10):

  explore runs   sampled choices (softmax temperature) and perturbed combat
                 parameters; after each run REINFORCE and evolution
                 strategies move the weights toward what went deeper
  bench runs     fixed seeds, near-greedy, no learning: the fitness score
  selection      bench average beats or ties the champion -> new champion;
                 drops clearly below it -> model and playbook revert

Every few explore runs a language model coaches: it reads the stats, writes
notes, nudges weights, and adds lessons to the strategy playbook.

Run from C:\\sts-evolve with the sts-bench venv (start.bat does this). The
game connects through CommunicationMod; stop with Ctrl-C and rerun to resume.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sts_bench.agents import SCAFFOLDS  # noqa: E402
from sts_bench.env import CommunicationModEnv, HarnessServer  # noqa: E402
from sts_bench.replay import group  # noqa: E402
from sts_bench.runner import session as _session  # noqa: E402
from sts_bench.runner.async_runner import JobSpec, LogRouter, QueueConfig, _run_job  # noqa: E402
from sts_bench.providers import PROVIDERS  # noqa: E402
from sts_bench.runner.metrics import RunMetrics  # noqa: E402
from sts_bench.smoke import session_dir  # noqa: E402
from sts_bench.trajectory import read_records  # noqa: E402

import dashboard  # noqa: E402
import policy  # noqa: E402
from model import Model  # noqa: E402

# ---------------------------------------------------------------- windows + tips

_symlink_to = Path.symlink_to


def _symlink_or_skip(self, target, target_is_directory=False):
    """sts-bench links logs/<date>/latest.log; Windows refuses symlinks
    without Developer Mode, and the link is only a convenience."""
    try:
        _symlink_to(self, target, target_is_directory)
    except OSError:
        pass


Path.symlink_to = _symlink_or_skip

_choose_command = _session.choose_command
_tip_polls = [0]


def _dismiss_or_wait(state, character, ascension, seed):
    """Tutorial tips accept only clicks: press Confirm, otherwise wait for a click."""
    cmds = state.get("available_commands", [])
    if state.get("in_game") and cmds and set(cmds) <= policy.TIP_ONLY:
        _tip_polls[0] += 1
        if _tip_polls[0] == 1:
            say("tutorial tip on screen: trying to dismiss it; if it stays, click it away in the game")
        if _tip_polls[0] % 3 == 1 and "key" in cmds:
            return "key Confirm"
        return "wait 300" if "wait" in cmds else "state"
    _tip_polls[0] = 0
    return _choose_command(state, character, ascension, seed)


_session.choose_command = _dismiss_or_wait

SCAFFOLDS["policy"] = (policy.PolicyAgent, "sts-evolve learned policy v2 ({character})")
CSV_FIELDS = ["run", "time", "kind", "seed", "victory", "floor", "score", "decisions",
              "forced", "archetype", "duration_s", "note"]
SEED_CHARS = "0123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
LEARNED = "## Learned from runs"
NOT_A_DECISION = {"tip screen", "waiting"}


def say(msg: str) -> None:
    print(f"[evolve {dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def ask_llm(base_url: str, model: str, system: str, user: str, think: bool, timeout: float = 900) -> str:
    if not think:
        system += "\n/no_think"
    return policy.ask_llm(base_url, model, system, user, timeout=timeout)


# ---------------------------------------------------------------- workspace

class Workspace:
    def __init__(self, root: Path, character: str, baseline_strategy: Path):
        self.root = root
        for sub in ("runs", "history"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        self.model_path = root / "model.json"
        self.strategy_path = root / "strategy.md"
        self.baseline_strategy = Path(baseline_strategy)
        if not self.strategy_path.exists():
            shutil.copyfile(baseline_strategy, self.strategy_path)
        self.refresh_playbook()
        self.state_path = root / "state.json"
        self.state = (json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists()
                      else {"run": 0, "champion": None, "cycle_bench": [], "explore_since_coach": 0,
                            "character": character, "cycle": 0})
        self.model = Model(self.model_path, character)

    def refresh_playbook(self) -> None:
        """Keep the playbook body in step with agent/strategy_baseline.md while
        preserving the coach's "Learned from runs" bullets."""
        base = self.baseline_strategy.read_text(encoding="utf-8")
        for path in (self.strategy_path, self.root / "champion_strategy.md"):
            if not path.exists():
                continue
            cur = path.read_text(encoding="utf-8")
            head, sep, learned = cur.partition(LEARNED)
            bhead, _, bdefault = base.partition(LEARNED)
            if head.strip() == bhead.strip():
                continue
            bullets = "\n".join(l for l in learned.splitlines() if l.strip().startswith("-"))
            path.write_text(bhead + LEARNED + "\n" + (bullets or bdefault.strip()) + "\n", encoding="utf-8")

    @property
    def strategy(self) -> str:
        return self.strategy_path.read_text(encoding="utf-8")

    def save(self) -> None:
        self.model.save()
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def rows(self) -> list[dict]:
        p = self.root / "progress.csv"
        if not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def append_row(self, row: dict) -> None:
        p = self.root / "progress.csv"
        new = not p.exists()
        with p.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)

    def runs(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "runs").glob("run_*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        return out

    def snapshot_champion(self) -> None:
        shutil.copyfile(self.model_path, self.root / "champion_model.json")
        shutil.copyfile(self.strategy_path, self.root / "champion_strategy.md")

    def revert_to_champion(self) -> None:
        if (self.root / "champion_model.json").exists():
            shutil.copyfile(self.root / "champion_model.json", self.model_path)
            self.model = Model(self.model_path, self.model.character)
        if (self.root / "champion_strategy.md").exists():
            shutil.copyfile(self.root / "champion_strategy.md", self.strategy_path)
        self.refresh_playbook()


# ---------------------------------------------------------------- run summary

ROOM = {"MonsterRoom": "fight", "MonsterRoomElite": "elite", "MonsterRoomBoss": "boss",
        "RestRoom": "rest", "ShopRoom": "shop", "EventRoom": "event", "TreasureRoom": "chest",
        "TreasureRoomBoss": "boss chest", "NeowRoom": "neow"}


def summarize(path: Path, tracker: policy.RunTracker) -> dict:
    records = list(read_records(path))
    run, floors, decisions = group(records)
    m = RunMetrics.from_records(records)
    victory = bool(m.victory)
    hp, rooms, fights = [], [], []
    elites = bosses = 0
    for i, f in enumerate(floors):
        last = i == len(floors) - 1
        room = ROOM.get(f.floor_type or "", f.floor_type or "?")
        rooms.append(room)
        if f.exit.hp is not None:
            hp.append([f.floor, f.exit.hp, f.exit.max_hp])
        if room in ("fight", "elite", "boss"):
            fights.append({"floor": f.floor, "room": room, "hp_delta": f.scorecard.hp_delta,
                           "max_hp": f.exit.max_hp or 80})
            won = not last or victory
            elites += room == "elite" and won
            bosses += room == "boss" and won
    final = (floors[-1].exit_state or floors[-1].entry_state) if floors else {}
    deck = [c.get("name", "?") + ("+" if c.get("upgrades") else "") for c in final.get("deck", [])]
    relics = [r.get("name", "?") for r in final.get("relics", [])]
    killer = None
    combat = final.get("combat_state") or {}
    if not victory and combat:
        killer = ", ".join(sorted({mo.get("name", "?") for mo in combat.get("monsters", [])
                                   if not mo.get("is_gone")})) or None
    real = [d for d in decisions_flat(decisions) if d.forced_reason not in NOT_A_DECISION]
    lat = [d.latency_ms for d in real if d.latency_ms is not None]
    return {
        "victory": victory, "floor": m.floor_reached or 0, "score": m.score,
        "act": 1 + max(0, ((m.floor_reached or 1) - 1) // 17),
        "decisions": len(real), "forced": sum(1 for d in real if d.forced_reason),
        "avg_move_ms": round(mean(lat), 1) if lat else None,
        "hp": hp, "rooms": rooms, "fights": fights, "elites_killed": int(elites),
        "bosses_killed": int(bosses), "deck": deck, "relics": relics, "killer": killer,
        "death_room": rooms[-1] if rooms and not victory else None,
        "picks": tracker.picks, "path": tracker.path, "rests": tracker.rests,
        "shops": tracker.shops, "events": tracker.game_events, "potions": tracker.potions,
        "boss_relics": tracker.boss_relics, "plan": tracker.plan,
        "plan_history": tracker.plan_history, "counts": tracker.counts,
        "llm_calls": tracker.llm_calls, "trajectory": str(path.resolve()),
    }


def decisions_flat(decisions) -> list:
    if isinstance(decisions, dict):
        return [d for ds in decisions.values() for d in ds]
    return list(decisions)


def fight_fitness(summary: dict, reward: float) -> float:
    losses = [-(f["hp_delta"] or 0) / max(1, f["max_hp"]) for f in summary["fights"]]
    return reward - 0.5 * (mean(losses) if losses else 0.0)


# ---------------------------------------------------------------- coach

def coach(ws: Workspace, args) -> None:
    runs = [r for r in ws.runs() if not r.get("aborted")][-max(args.coach_every * 2, 10):]
    if not runs:
        return
    model = ws.model
    lines = [f"run {r['run']} ({r['kind']}): floor {r['floor']}{' WIN' if r['victory'] else ''}, "
             f"archetype {r.get('plan', {}).get('archetype', '?')}, killed by {r.get('killer') or '-'}, "
             f"elites {r.get('elites_killed', 0)}, deck {len(r.get('deck', []))}" for r in runs]
    seen = sorted({c for r in ws.runs() for p in r.get("picks", []) for c in p["offered"] if c != "(skip)"})
    table = model.card_table([re.sub(r"\+$", "", c) for c in seen])
    top = ", ".join(f"{n} {v:+.2f}" for n, v in table[:12])
    bottom = ", ".join(f"{n} {v:+.2f}" for n, v in table[-8:])
    nodes = ", ".join(f"{k[5:]} {v:+.2f}" for k, v in sorted(model.weights.items()) if k.startswith("node:"))
    deaths = death_table(ws.runs())
    params = ", ".join(f"{k} {v:.2f}" for k, v in model.params.items())
    system = ("You coach a learning Slay the Spire bot. A fast policy plays every move; its card, path, "
              "rest and shop choices are learned weights trained on how deep each run gets. You see its "
              "recent results and current weights. Find what is costing runs and correct it with small, "
              "evidence-based nudges.")
    user = (f"CHARACTER: {model.character}\nRUNS TRAINED: {model.meta['runs_trained']}\n\n"
            f"RECENT RUNS:\n" + "\n".join(lines) +
            f"\n\nHIGHEST CARD WEIGHTS: {top}\nLOWEST CARD WEIGHTS: {bottom}\n"
            f"PATH WEIGHTS (symbol:hp bucket 0 low/1 mid/2 high): {nodes or 'priors only'}\n"
            f"COMBAT PARAMETERS: {params}\n\nWHAT KILLS RUNS (all runs):\n{deaths}\n\n"
            f"PLAYBOOK:\n{ws.strategy[:9000]}\n\n"
            "Reply in exactly three sections:\n"
            "NOTES:\n- 3 to 6 short bullets: what is working, what is killing runs, what to try.\n"
            "NUDGES:\n```json\n{\"card\": {\"Card Name\": 0.3}, \"archetype\": {\"Name\": 0.2}, "
            "\"node\": {\"E:h1\": -0.2}, \"params\": {\"block\": 0.1}}\n```\n"
            "(values between -0.5 and 0.5; use base card names without '+'; params are fractional changes; "
            "leave out anything without evidence)\n"
            "LEARNED:\n- 0 to 3 one-line strategy lessons for the playbook.")
    try:
        say("coach: analyzing recent runs...")
        answer = ask_llm(args.base_url, args.reflect_model, system, user, think=args.coach_think)
    except Exception as exc:
        say(f"coach unavailable ({type(exc).__name__}: {exc}); skipping")
        return
    notes = _section(answer, "NOTES", ("NUDGES", "LEARNED"))
    learned = _section(answer, "LEARNED", ())
    applied = []
    m = re.search(r"\{.*\}", _section(answer, "NUDGES", ("LEARNED",)) or answer, re.DOTALL)
    if m:
        try:
            n = json.loads(m.group(0))
            cards = {re.sub(r"\+\d*$", "", k).strip(): v for k, v in (n.get("card") or {}).items()}
            applied = ws.model.nudge(cards, n.get("archetype"), n.get("node"), n.get("params"))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            pass
    stamp = f"## After run {ws.state['run']} ({dt.datetime.now():%Y-%m-%d %H:%M})"
    with (ws.root / "coach.md").open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp}\n{notes.strip() or '(no notes)'}\n\n"
                 f"Nudges applied: {', '.join(applied) or 'none'}\n\n")
    bullets = [l.strip() for l in learned.splitlines() if l.strip().startswith(("-", "*"))][:3]
    if bullets:
        text = ws.strategy
        head, sep, tail = text.partition(LEARNED)
        existing = [l for l in tail.splitlines() if l.strip().startswith("-")]
        norm = lambda t: re.sub(r"\(run \d+\)|[^a-z0-9 ]", "", t.lower()).strip()
        known = {norm(l) for l in existing}
        fresh = [b.lstrip("-* ").strip() for b in bullets]
        fresh = [b for b in fresh if norm(b) not in known]
        merged = (existing + [f"- {b} (run {ws.state['run']})" for b in fresh])[-25:]
        ws.strategy_path.write_text(head + LEARNED + "\n" + "\n".join(merged) + "\n",
                                    encoding="utf-8")
    say(f"coach: {len(applied)} nudges applied, playbook updated")


def death_table(runs: list[dict]) -> str:
    """Who ends runs and how bosses/elites go -- the coach's main evidence."""
    real = [r for r in runs if not r.get("aborted")]
    if not real:
        return "(no runs)"
    killers: dict[str, list[int]] = {}
    for r in real:
        if not r.get("victory") and r.get("killer"):
            killers.setdefault(r["killer"], []).append(r["floor"])
    lines = [f"- {k}: {len(v)} deaths (floors {', '.join(map(str, sorted(v)))})"
             for k, v in sorted(killers.items(), key=lambda kv: -len(kv[1]))[:10]]
    acts = {1: 0, 2: 0, 3: 0}
    for r in real:
        for a in acts:
            if r.get("bosses_killed", 0) >= a:
                acts[a] += 1
    lines.append(f"- act bosses beaten: act1 {acts[1]}/{len(real)}, act2 {acts[2]}/{len(real)}, "
                 f"act3 {acts[3]}/{len(real)}")
    return "\n".join(lines)


def _section(text: str, name: str, stops: tuple) -> str:
    m = re.search(rf"{name}:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    body = m.group(1)
    for s in stops:
        body = re.split(rf"\n\s*{s}:", body, flags=re.IGNORECASE)[0]
    return body.strip()


# ---------------------------------------------------------------- the loop

def plan_run(index: int, args) -> tuple[str, str]:
    pos = index % args.cycle
    first_bench = args.cycle - len(args.bench_seeds)
    if pos >= first_bench:
        return "bench", args.bench_seeds[pos - first_bench]
    return "explore", "".join(random.choice(SEED_CHARS) for _ in range(10))


def select(ws: Workspace, args) -> str:
    floors = ws.state["cycle_bench"]
    ws.state["cycle_bench"] = []
    ws.state["cycle"] += 1
    score = round(mean(floors), 2) if floors else 0.0
    champ = ws.state["champion"]
    shutil.copyfile(ws.model_path, ws.root / "history" / f"model_c{ws.state['cycle']:03d}.json")
    if champ is None or score >= champ["bench_mean"]:
        ws.state["champion"] = {"cycle": ws.state["cycle"], "bench_mean": score, "run": ws.state["run"]}
        ws.snapshot_champion()
        say(f"selection: bench avg {score} -> NEW CHAMPION")
        return f"champion={score}"
    if score < champ["bench_mean"] - args.revert_margin:
        ws.revert_to_champion()
        say(f"selection: bench avg {score} < champion {champ['bench_mean']} - margin -> reverted")
        return f"reverted (bench avg {score})"
    say(f"selection: bench avg {score} within margin of champion {champ['bench_mean']}; keeping")
    return f"kept (bench avg {score})"


def connect(server: HarnessServer, step_timeout: float | None = None) -> tuple[CommunicationModEnv, LogRouter]:
    conn = server.accept(timeout=None)
    router = LogRouter()
    extra = {"step_timeout": step_timeout} if step_timeout else {}
    env = CommunicationModEnv(conn, on_protocol_line=router.line, **extra)
    env.handshake()
    return env, router


def relaunch_game() -> None:
    """Kill only the modded Slay the Spire java process (never other java apps) and start it again."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'java%'\" | "
          "Where-Object { $_.CommandLine -match 'ModTheSpire|desktop-1.0.jar' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    time.sleep(3)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      str(HERE.parent / "launch-game.ps1"), "-DelaySeconds", "2"])


def wait_until_ready(env: CommunicationModEnv, seconds: float) -> dict | None:
    """Nudge a stuck game with `state` until it answers ready (animations can hang for a while)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            env._send("state")
            st = env._await_ready_state(min(20.0, max(1.0, deadline - time.time())))
            env._state = st
            return st
        except Exception:
            continue
    return None


def run_loop(args, server: HarnessServer | None = None, log_dir: Path | None = None) -> int:
    ws = Workspace(Path(args.workdir), args.character.upper(), Path(args.strategy_baseline))
    provider = PROVIDERS["chat"].from_env(model=args.model, base_url=args.base_url)
    own = server is None
    server = server or HarnessServer(port=args.port)
    aborted_streak = 0

    def play_leftover(env, router) -> None:
        """Finish a run already in progress (after a stall or a restart) without learning from it."""
        policy.CONFIG = policy.Config(model=ws.model, tracker=policy.RunTracker(), temperature=0.05,
                                      params=ws.model.es_params(None), learn=False, llm_events=False,
                                      strategy=ws.strategy)
        say("finishing the run already in progress (not used for learning)")
        _run_job(env, router, JobSpec(agent="policy", seed="RESUME", character=args.character,
                                      ascension=args.ascension, provider=provider, api="chat"),
                 QueueConfig(jobs=[], port=server.port, log_dir=log_dir or session_dir()), say)

    try:
        say(f"listening on port {server.port}: launch the game with mods (it connects by itself)")
        env, router = connect(server, getattr(args, 'step_timeout', None))
        if env.state.get("in_game"):
            play_leftover(env, router)
            if env.state.get("in_game"):
                say("the game is still mid-run: abandon it, get to the main menu, and rerun")
                return 1
        say(f"connected; resuming at run {ws.state['run'] + 1} "
            f"({ws.model.meta['runs_trained']} runs trained, temperature {ws.model.temperature():.2f})")
        while args.max_runs <= 0 or ws.state["run"] < args.max_runs:
            index = ws.state["run"]
            kind, seed = plan_run(index, args)
            explore = kind == "explore"
            rng = random.Random()
            eps = ws.model.es_sample(rng) if explore else None
            temp = ws.model.temperature() if explore else 0.05
            policy.CONFIG = policy.Config(
                model=ws.model, tracker=policy.RunTracker(), temperature=temp,
                params=ws.model.es_params(eps), rng=rng, learn=explore,
                llm_events=args.llm_events, llm_model=args.model, llm_base_url=args.base_url,
                strategy=ws.strategy)
            say(f"=== run {index + 1} [{kind}] seed {seed} | temperature {temp:.2f} ===")
            job = JobSpec(agent="policy", seed=seed, character=args.character, ascension=args.ascension,
                          provider=provider, api="chat")
            config = QueueConfig(jobs=[], port=server.port, log_dir=log_dir or session_dir())
            started = time.time()
            result = _run_job(env, router, job, config, say)
            duration = round(time.time() - started, 1)
            if result.error or not result.trajectory.exists():
                # stalled, disconnected or cut short: never learn from it, replay the slot
                aborted_streak += 1
                reason = result.error or "no trajectory"
                say(f"run {index + 1} aborted ({reason}); not used for learning, retrying this slot")
                try:
                    s = summarize(result.trajectory, policy.CONFIG.tracker)
                except Exception:
                    s = {}
                s.update({"run": index + 1, "kind": kind, "seed": seed, "aborted": reason,
                          "started": dt.datetime.fromtimestamp(started).isoformat(timespec="seconds")})
                adir = ws.root / "runs" / "aborted"
                adir.mkdir(parents=True, exist_ok=True)
                (adir / f"run_{index + 1:04d}_{int(started)}.json").write_text(json.dumps(s, indent=1),
                                                                               encoding="utf-8")
                if aborted_streak >= args.max_aborts:
                    say(f"{aborted_streak} aborted runs in a row; stopping -- check the game")
                    return 1
                lost = any(w in reason for w in ("ConnectionClosed", "closed the connection", "Broken pipe",
                                                  "ConnectionReset", "OSError"))
                if not lost and (result.fatal or env.state.get("in_game")):
                    say(f"game looks stuck; nudging it for up to {args.stall_wait}s")
                    st = wait_until_ready(env, args.stall_wait)
                    if st is not None and st.get("in_game"):
                        play_leftover(env, router)
                    lost = st is None or env.state.get("in_game")
                    if lost and not args.relaunch_game:
                        say("the game is stuck mid-run; restart it, then rerun to resume")
                        return 1
                    if lost:
                        say("still stuck: restarting the game")
                        relaunch_game()
                elif lost and args.relaunch_game:
                    say("the game disconnected: restarting it")
                    relaunch_game()
                if lost:
                    say("waiting for the game to reconnect...")
                    env, router = connect(server, getattr(args, 'step_timeout', None))
                    if env.state.get("in_game"):
                        play_leftover(env, router)
                continue
            aborted_streak = 0
            s = summarize(result.trajectory, policy.CONFIG.tracker)
            reward = Model.outcome(s["floor"], s["victory"])
            learned = {}
            if explore and s["decisions"] >= 5:
                learned = ws.model.learn(policy.CONFIG.tracker.events, reward, lr=args.lr)
                es_adv = ws.model.es_update(eps, fight_fitness(s, reward))
                learned["es_advantage"] = es_adv
                ws.state["explore_since_coach"] += 1
            s.update({"run": index + 1, "kind": kind, "seed": seed, "character": args.character.upper(),
                      "started": dt.datetime.fromtimestamp(started).isoformat(timespec="seconds"),
                      "duration_s": duration, "temperature": round(temp, 3), "reward": round(reward, 3),
                      "advantage": round(learned.get("advantage", 0.0), 3),
                      "params": {k: round(v, 3) for k, v in policy.CONFIG.params.items()}})
            (ws.root / "runs" / f"run_{index + 1:04d}.json").write_text(json.dumps(s, indent=1),
                                                                        encoding="utf-8")
            say(f"run {index + 1} done: {'VICTORY' if s['victory'] else 'died on floor ' + str(s['floor'])}"
                f" | {s['plan'].get('archetype', '?')} | {duration:.0f}s"
                + (f" | advantage {learned['advantage']:+.2f}" if learned else ""))
            if kind == "bench":
                ws.state["cycle_bench"].append(s["floor"])
            ws.state["run"] = index + 1
            note = select(ws, args) if (index + 1) % args.cycle == 0 else ""
            ws.append_row({"run": index + 1, "time": s["started"], "kind": kind, "seed": seed,
                           "victory": s["victory"], "floor": s["floor"], "score": s["score"],
                           "decisions": s["decisions"], "forced": s["forced"],
                           "archetype": s["plan"].get("archetype", ""), "duration_s": duration, "note": note})
            ws.save()
            if args.coach_every > 0 and ws.state["explore_since_coach"] >= args.coach_every:
                ws.state["explore_since_coach"] = 0
                coach(ws, args)
                ws.save()
            try:
                dashboard.render(ws.root)
            except Exception as exc:  # the page is a view; never stop training over it
                say(f"dashboard render failed: {type(exc).__name__}: {exc}")
        return 0
    finally:
        if own:
            server.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--character", default="ironclad", choices=("ironclad", "silent", "defect", "watcher"))
    p.add_argument("--ascension", type=int, default=0)
    p.add_argument("--model", default="qwen3:14b", help="LLM for events, boss relics and coaching")
    p.add_argument("--reflect-model", default=None, help="LLM for the coach (default: --model)")
    p.add_argument("--base-url", default="http://localhost:11434/v1")
    p.add_argument("--llm-events", action=argparse.BooleanOptionalAction, default=True,
                   help="let the LLM pick event options and boss relics")
    p.add_argument("--coach-every", type=int, default=5, help="explore runs between coach reviews (0 = off)")
    p.add_argument("--coach-think", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--lr", type=float, default=0.08, help="learning rate for the choice weights")
    p.add_argument("--cycle", type=int, default=10, help="runs per cycle, bench runs included")
    p.add_argument("--bench-seeds", default="BENCHA1,BENCHB2")
    p.add_argument("--revert-margin", type=float, default=1.0,
                   help="floors below the champion's bench average before reverting")
    p.add_argument("--max-runs", type=int, default=0)
    p.add_argument("--stall-wait", type=int, default=180,
                   help="seconds to nudge a stuck game before giving up on it")
    p.add_argument("--relaunch-game", action=argparse.BooleanOptionalAction, default=False,
                   help="restart Slay the Spire by itself when it hangs or disconnects")
    p.add_argument("--max-aborts", type=int, default=3, help="aborted runs in a row before stopping")
    p.add_argument("--step-timeout", type=float, default=None, help="seconds to wait for the game per command")
    p.add_argument("--port", type=int, default=9999)
    p.add_argument("--workdir", default=None, help="default: evolve/<character> next to agent/")
    p.add_argument("--strategy-baseline", default=str(HERE / "strategy_baseline.md"))
    args = p.parse_args()
    args.bench_seeds = [s.strip().upper() for s in args.bench_seeds.split(",") if s.strip()]
    if len(args.bench_seeds) >= args.cycle:
        p.error("--cycle must be larger than the number of bench seeds")
    args.reflect_model = args.reflect_model or args.model
    args.workdir = args.workdir or str(HERE.parent / "evolve" / args.character)
    try:
        raise SystemExit(run_loop(args))
    except KeyboardInterrupt:
        say("stopped; progress is saved -- rerun to resume")


if __name__ == "__main__":
    main()
