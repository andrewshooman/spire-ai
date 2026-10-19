"""evolve_tools.py -- watch and assess the bot. The .bat launchers call these.

    watch            live feed of the current run (decisions and why)
    dashboard        rebuild and open the dashboard
    stats            text summary
    weights          learned card weights, path/rest preferences, combat parameters
    notes            coach notes and playbook lessons
    report [N]       sts-bench's HTML report for run N (default: latest)
    campaign         sts-bench's comparison page across all runs
    baseline         scripted bot on the bench seeds (stop start.bat first)
    reset            archive the current character's progress and start fresh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import socket
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evolve  # noqa: E402,F401  (Windows symlink patch)
import dashboard  # noqa: E402
from model import Model  # noqa: E402
from sts_bench.bench import _write_reports  # noqa: E402
from sts_bench.report.model import build_report_data  # noqa: E402
from sts_bench.report.page import render_html  # noqa: E402
from sts_bench.runner.async_runner import JobSpec, QueueConfig, run_jobs  # noqa: E402
from sts_bench.runner.metrics import RunMetrics  # noqa: E402
from sts_bench.smoke import LOG_DIR, run_html_path, session_dir  # noqa: E402
from sts_bench.trajectory import read_records  # noqa: E402

EVOLVE = HERE.parent / "evolve"


def workspace(character: str | None) -> Path:
    if character:
        return EVOLVE / character.lower()
    found = [p for p in EVOLVE.glob("*") if (p / "state.json").exists()] if EVOLVE.exists() else []
    if not found:
        sys.exit("No progress yet: start the bot with start.bat first.")
    return max(found, key=lambda p: (p / "state.json").stat().st_mtime)


def runs(ws: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ws / "runs").glob("run_*.json"))]


def open_page(path: Path) -> None:
    print(f"Opening {path}")
    webbrowser.open(path.resolve().as_uri())


def cmd_watch(args) -> None:
    keep = {"--", "<m"} if not args.all else None
    current, fh = None, None
    print("Following the newest game log (Ctrl-C to stop)...")
    while True:
        logs = sorted(LOG_DIR.glob("*/play-*.log"), key=lambda p: p.stat().st_mtime)
        if logs and logs[-1] != current:
            if fh:
                fh.close()
            current = logs[-1]
            fh = current.open(encoding="utf-8", errors="replace")
            print(f"\n===== {current.name} =====")
            for line in fh.readlines()[-args.lines:]:
                emit(line, keep)
        line = fh.readline() if fh else ""
        if line:
            emit(line, keep)
        else:
            time.sleep(0.3)


def emit(line: str, keep) -> None:
    parts = line.rstrip("\n").split(" ", 2)
    if len(parts) < 3:
        return
    stamp, tag, text = parts
    if keep is not None and tag not in keep:
        return
    label = {"--": "", "<m": "  why: "}.get(tag, f"{tag} ")
    print(f"{stamp[:8]} {label}{text[:300]}", flush=True)


def cmd_dashboard(args) -> None:
    open_page(dashboard.render(workspace(args.character)))


def cmd_stats(args) -> None:
    ws = workspace(args.character)
    rs = runs(ws)
    if not rs:
        sys.exit("No finished runs yet.")
    floors = [r["floor"] for r in rs]
    best = max(rs, key=lambda r: (r["victory"], r["floor"]))
    state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    print(f"{ws.name.upper()}: {len(rs)} runs, best floor {best['floor']} (run {best['run']}), "
          f"wins {sum(r['victory'] for r in rs)}, act 1 cleared {sum(r['floor'] >= 17 for r in rs)}")
    print(f"Average floor: last 10 {mean(floors[-10:]):.1f}"
          + (f" | previous 10 {mean(floors[-20:-10]):.1f}" if len(floors) > 10 else ""))
    champ = state.get("champion") or {}
    print(f"Champion bench average: {champ.get('bench_mean', '-')} (cycle {champ.get('cycle', '-')})")
    mins = [r.get("duration_s", 0) / 60 for r in rs[-10:]]
    print(f"Speed: {mean(mins):.1f} min/run recently")
    print("\nLast 10 runs:")
    for r in rs[-10:]:
        print(f"  #{r['run']:>4} {r['kind']:7} floor {r['floor']:>2} {'WIN ' if r['victory'] else ''}"
              f"{(r.get('plan') or {}).get('archetype', '?'):28} killed by {r.get('killer') or '-'}")


def cmd_weights(args) -> None:
    ws = workspace(args.character)
    m = Model(ws / "model.json")
    seen = sorted({c.rstrip("+") for r in runs(ws) for p in r.get("picks", []) for c in p["offered"] if c != "(skip)"})
    table = m.card_table(seen)
    print(f"Card weights ({len(table)} cards seen, {m.meta['runs_trained']} runs trained):")
    for n, v in table:
        print(f"  {v:+6.2f}  {n}")
    print("\nPath weights:", ", ".join(f"{k[5:]} {v:+.2f}" for k, v in sorted(m.weights.items()) if k.startswith("node:")) or "priors only")
    print("Combat parameters:", ", ".join(f"{k} {v:.2f}" for k, v in m.params.items()))


def cmd_notes(args) -> None:
    ws = workspace(args.character)
    coach = ws / "coach.md"
    print(coach.read_text(encoding="utf-8") if coach.exists() else "No coach notes yet.")
    strategy = (ws / "strategy.md").read_text(encoding="utf-8") if (ws / "strategy.md").exists() else ""
    print("=== PLAYBOOK LESSONS ===")
    print(strategy.partition("## Learned from runs")[2].strip() or "(none yet)")
    print(f"\nFull playbook (edit freely): {ws / 'strategy.md'}")


def cmd_report(args) -> None:
    ws = workspace(args.character)
    rs = runs(ws)
    if not rs:
        sys.exit("No runs yet.")
    r = next((x for x in rs if x["run"] == args.run), None) if args.run else rs[-1]
    if r is None:
        sys.exit(f"No run {args.run}.")
    path = Path(r["trajectory"])
    out = run_html_path(path)
    out.write_text(render_html(build_report_data(list(read_records(path)), None)), encoding="utf-8")
    open_page(out)


def cmd_campaign(args) -> None:
    paths = sorted(LOG_DIR.glob("*/trajectories/*.jsonl"))
    if not paths:
        sys.exit("No runs yet.")
    _, _, html_path, included, _ = _write_reports(paths, None)
    print(f"{included} runs compared.")
    open_page(html_path)


def cmd_baseline(args) -> None:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", 9999))
        except OSError:
            sys.exit("Port 9999 is busy: stop start.bat (Ctrl-C) first.")
    seeds = ["BENCHA1", "BENCHB2"]
    config = QueueConfig(jobs=[JobSpec(agent="scripted", seed=x) for x in seeds], log_dir=session_dir())
    print(f"Scripted baseline on {seeds}. Launch the game with mods now.")
    results = asyncio.run(run_jobs(config, lambda m: print(m, flush=True)))
    floors = [RunMetrics.from_file(r.trajectory).floor_reached or 0 for r in results if r.trajectory.exists()]
    if floors:
        print(f"\nScripted baseline: floors {floors}, avg {mean(floors):.1f}. The bot's bench average should beat this.")


def cmd_reset(args) -> None:
    ws = workspace(args.character)
    archive = EVOLVE / "_archive" / f"{ws.name}-{datetime.now():%Y%m%d-%H%M%S}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if input(f"Archive {ws} to {archive} and start {ws.name} fresh? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    shutil.move(str(ws), str(archive))
    print(f"Archived. The next start begins {ws.name} from scratch.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--character", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--all", action="store_true")
    w.add_argument("--lines", type=int, default=30)
    for name in ("dashboard", "stats", "weights", "notes", "campaign", "baseline", "reset"):
        sub.add_parser(name)
    r = sub.add_parser("report")
    r.add_argument("run", type=int, nargs="?")
    args = p.parse_args()
    try:
        globals()[f"cmd_{args.cmd}"](args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
