"""dashboard.html: everything about the bot on one auto-refreshing page.

Built from the workspace files only (runs/*.json, progress.csv, model.json,
state.json, coach.md, strategy.md), with inline SVG charts and no external
scripts, so it opens straight from disk and works offline.
"""

from __future__ import annotations

import csv
import difflib
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ACT_LINES = ((17, "Act 1 boss"), (34, "Act 2 boss"), (51, "Act 3 boss"))
E = html.escape


def _load(root: Path):
    runs = []
    for p in sorted((root / "runs").glob("run_*.json")):
        try:
            runs.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    rows = []
    if (root / "progress.csv").exists():
        with (root / "progress.csv").open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    model = json.loads((root / "model.json").read_text(encoding="utf-8")) if (root / "model.json").exists() else {}
    state = json.loads((root / "state.json").read_text(encoding="utf-8")) if (root / "state.json").exists() else {}
    return runs, rows, model, state


# ---------------------------------------------------------------- svg helpers

def _scatter(runs, rows, w=900, h=300, pad=42) -> str:
    if not runs:
        return "<p class=muted>No runs yet.</p>"
    n = len(runs)
    top = max([r["floor"] for r in runs] + [20])
    top = min(55, ((top // 10) + 1) * 10)
    x = lambda i: pad + (w - 2 * pad) * (i / max(1, n - 1))
    y = lambda f: h - pad - (h - 2 * pad) * f / top
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Floor reached per run">']
    for f in range(0, top + 1, 10):
        out.append(f'<line class=grid x1={pad} x2={w-pad} y1={y(f):.1f} y2={y(f):.1f} />'
                   f'<text x={pad-8} y={y(f)+4:.1f} text-anchor=end>{f}</text>')
    for f, label in ACT_LINES:
        if f <= top:
            out.append(f'<line class=act x1={pad} x2={w-pad} y1={y(f):.1f} y2={y(f):.1f} />'
                       f'<text class=actlabel x={w-pad} y={y(f)-4:.1f} text-anchor=end>{label}</text>')
    floors = [r["floor"] for r in runs]
    roll = [mean(floors[max(0, i - 9): i + 1]) for i in range(n)]
    out.append('<polyline class=roll points="' + " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(roll)) + '" />')
    champ = []
    by_run = {int(r["run"]): r for r in rows}
    for i, r in enumerate(runs):
        note = by_run.get(r["run"], {}).get("note", "")
        if note.startswith("champion="):
            champ.append(f"{x(i):.1f},{y(float(note.split('=')[1])):.1f}")
    if len(champ) > 1:
        out.append(f'<polyline class=champ points="{" ".join(champ)}" />')
    for i, r in enumerate(runs):
        cls = "win" if r["victory"] else ("bench" if r["kind"] == "bench" else "explore")
        out.append(f'<circle class={cls} cx={x(i):.1f} cy={y(r["floor"]):.1f} r=4>'
                   f'<title>run {r["run"]} ({r["kind"]}) floor {r["floor"]} - '
                   f'{E(r.get("plan", {}).get("archetype", "?"))}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def _bars(items, w=440, h=None, pad=150, fmt="{:+.2f}", cls_fn=None) -> str:
    if not items:
        return "<p class=muted>Nothing yet.</p>"
    h = h or 22 * len(items) + 10
    lo = min(0.0, min(v for _, v in items))
    hi = max(0.01, max(v for _, v in items))
    span = hi - lo or 1
    zero = pad + (w - pad - 50) * (-lo / span)
    out = [f'<svg viewBox="0 0 {w} {h}">']
    for i, (label, v) in enumerate(items):
        yy = 6 + i * 22
        x1 = pad + (w - pad - 50) * ((min(v, 0) - lo) / span)
        x2 = pad + (w - pad - 50) * ((max(v, 0) - lo) / span)
        cls = cls_fn(label, v) if cls_fn else ("pos" if v >= 0 else "neg")
        out.append(f'<text x={pad-6} y={yy+13} text-anchor=end>{E(str(label))[:24]}</text>'
                   f'<rect class={cls} x={x1:.1f} y={yy} width={max(1, x2-x1):.1f} height=16 rx=3 />'
                   f'<text x={x2+4:.1f} y={yy+13}>{fmt.format(v)}</text>')
    out.append(f'<line class=axis x1={zero:.1f} x2={zero:.1f} y1=0 y2={h} /></svg>')
    return "".join(out)


def _histogram(values, top=55, w=900, h=160, pad=30) -> str:
    if not values:
        return "<p class=muted>No deaths yet.</p>"
    counts = Counter(values)
    peak = max(counts.values())
    bw = (w - 2 * pad) / (top + 1)
    out = [f'<svg viewBox="0 0 {w} {h}">']
    for f in range(top + 1):
        c = counts.get(f, 0)
        if c:
            bh = (h - 2 * pad) * c / peak
            out.append(f'<rect class=death x={pad + f*bw:.1f} y={h-pad-bh:.1f} width={bw-1:.1f} height={bh:.1f}>'
                       f'<title>floor {f}: {c} deaths</title></rect>')
    for f, label in ACT_LINES:
        xx = pad + f * bw
        out.append(f'<line class=act x1={xx:.1f} x2={xx:.1f} y1={pad-10} y2={h-pad} />'
                   f'<text class=actlabel x={xx+3:.1f} y={pad-2}>{label}</text>')
    for f in range(0, top + 1, 5):
        out.append(f'<text x={pad + f*bw + bw/2:.1f} y={h-pad+14} text-anchor=middle>{f}</text>')
    out.append("</svg>")
    return "".join(out)


def _sparkline(points, w=420, h=90, pad=8) -> str:
    if len(points) < 2:
        return ""
    xs = [p[0] for p in points]
    x0, x1 = min(xs), max(xs) or 1
    top = max(p[2] or 1 for p in points)
    x = lambda v: pad + (w - 2 * pad) * ((v - x0) / max(1, x1 - x0))
    y = lambda v: h - pad - (h - 2 * pad) * (v / top)
    line = " ".join(f"{x(f):.1f},{y(hp):.1f}" for f, hp, _ in points)
    return (f'<svg viewBox="0 0 {w} {h}"><polyline class=hpline points="{line}" />'
            f'<text x={pad} y={h-1}>floor {x0}</text><text x={w-pad} y={h-1} text-anchor=end>floor {x1}</text></svg>')


# ---------------------------------------------------------------- sections

def _milestones(runs) -> list[tuple[str, str]]:
    found = []

    def first(label, cond):
        for r in runs:
            if cond(r):
                found.append((label, f"run {r['run']}"))
                return
        found.append((label, ""))

    first("First fight won", lambda r: r["floor"] >= 2)
    first("First elite slain", lambda r: r.get("elites_killed", 0) > 0)
    first("Act 1 boss down", lambda r: r["floor"] >= 17)
    first("Reached Act 3", lambda r: r["floor"] >= 35)
    first("Act 2 boss down", lambda r: r["floor"] >= 34)
    first("VICTORY", lambda r: r["victory"])
    for k in (10, 50, 100, 500):
        found.append((f"{k} runs played", f"run {k}" if len(runs) >= k else ""))
    return found


def _diff_html(old: str, new: str) -> str:
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        cls = "add" if line.startswith("+") else "del"
        out.append(f'<div class={cls}>{E(line)}</div>')
    return "".join(out) or "<p class=muted>No changes yet.</p>"


def render(root: Path) -> Path:
    root = Path(root)
    runs, rows, model, state = _load(root)
    weights = model.get("weights", {})
    meta = model.get("meta", {})
    params = model.get("params", {})
    sys.path.insert(0, str(HERE))
    from model import Model  # lazy: needs the venv
    m = Model(root / "model.json", model.get("character", "IRONCLAD"))

    n = len(runs)
    floors = [r["floor"] for r in runs]
    best = max(runs, key=lambda r: (r["victory"], r["floor"])) if runs else None
    last10 = floors[-10:]
    prev10 = floors[-20:-10]
    trend = (mean(last10) - mean(prev10)) if last10 and prev10 else 0.0
    wins = sum(r["victory"] for r in runs)
    act1 = sum(r["floor"] >= 17 for r in runs)
    hours = sum(r.get("duration_s", 0) for r in runs) / 3600
    champ = state.get("champion") or {}
    latest = runs[-1] if runs else None

    # archetypes
    arch = defaultdict(list)
    for r in runs:
        a = (r.get("plan") or {}).get("archetype", "Undecided") or "Undecided"
        arch[a.split(" (")[0]].append(r["floor"])
    arch_rows = "".join(
        f"<tr><td>{E(a)}</td><td>{len(v)}</td><td>{mean(v):.1f}</td><td>{max(v)}</td>"
        f"<td>{m.w('tag:' + a) if a != 'Undecided' else 0:+.2f}</td><td>{m.w('syn:' + a) if a != 'Undecided' else 0:+.2f}</td></tr>"
        for a, v in sorted(arch.items(), key=lambda kv: -mean(kv[1])))

    # card weights for cards actually offered
    seen = sorted({re.sub(r"\+$", "", c) for r in runs for p in r.get("picks", []) for c in p["offered"]
                   if c != "(skip)"})
    table = m.card_table(seen)
    shown = table[:12]
    rest = [t for t in table if t not in shown]
    top_cards = _bars(shown)
    bottom_cards = _bars(rest[-10:][::-1]) if rest else ""
    picked = Counter(p["chosen"] for r in runs for p in r.get("picks", []))
    skip_rate = (picked.get("(skip)", 0) / max(1, sum(picked.values()))) if picked else 0

    # path / rest weights
    path_items = []
    for sym, name in (("M", "fight"), ("E", "elite"), ("R", "rest"), ("$", "shop"), ("?", "event"), ("T", "chest")):
        for hb, hn in ((0, "low HP"), (2, "high HP")):
            path_items.append((f"{name}, {hn}", m.w(f"node:{sym}:h{hb}")))
    rest_items = [(f"{o} @ {hn}", m.w(f"{o}:h{hb}")) for o in ("rest", "smith") for hb, hn in
                  ((0, "low HP"), (1, "mid HP"), (2, "high HP"))]
    param_items = sorted(params.items())

    killers = Counter(r.get("killer") for r in runs if r.get("killer"))
    deaths = [r["floor"] for r in runs if not r["victory"]]

    # latest run panel
    latest_html = "<p class=muted>No runs yet.</p>"
    if latest:
        deck = Counter(latest.get("deck", []))
        deck_html = ", ".join(f"{E(c)}{' x' + str(k) if k > 1 else ''}" for c, k in sorted(deck.items()))
        picks_html = "".join(
            f"<li>F{p['floor']}: <b>{E(p['chosen'])}</b> <span class=muted>from {E(', '.join(p['offered']))} "
            f"(p={p['p']:.2f})</span></li>" for p in latest.get("picks", [])[-12:])
        plan = latest.get("plan") or {}
        path = " ".join({"fight": "&#9876;", "elite": "&#9760;", "boss": "&#128081;", "rest": "&#128293;",
                         "shop": "&#128176;", "event": "?", "chest": "&#128230;", "neow": "&#9733;",
                         "boss chest": "&#128230;"}.get(rm, "&middot;") for rm in latest.get("rooms", []))
        latest_html = f"""
<div class=grid2>
 <div>
  <p class=big>{'VICTORY' if latest['victory'] else 'Died on floor ' + str(latest['floor'])}
   <span class=muted>run {latest['run']} &middot; {latest['kind']} &middot; seed {E(latest['seed'])}
   &middot; {latest.get('duration_s', 0)/60:.1f} min</span></p>
  <p><b>Killed by:</b> {E(latest.get('killer') or '-')} &nbsp; <b>Elites:</b> {latest.get('elites_killed', 0)}
   &nbsp; <b>Reward:</b> {latest.get('reward', 0):.2f} &nbsp; <b>Advantage:</b> {latest.get('advantage', 0):+.2f}</p>
  <p><b>Plan:</b> {E(plan.get('archetype', '?'))} &middot; core {E(', '.join(plan.get('core', [])) or '-')}
   &middot; wanted {E(', '.join(plan.get('wanted', [])) or '-')}</p>
  <p class=path title="path taken">{path}</p>
  <p><b>Deck ({len(latest.get('deck', []))}):</b> {deck_html}</p>
  <p><b>Relics:</b> {E(', '.join(latest.get('relics', [])))}</p>
 </div>
 <div>
  <p><b>HP by floor</b></p>{_sparkline(latest.get('hp', []))}
  <p><b>Card picks</b></p><ul class=picks>{picks_html or '<li class=muted>none</li>'}</ul>
 </div>
</div>"""

    hist_rows = "".join(
        f"<tr class={'win' if r['victory'] else ''}><td>{r['run']}</td><td>{r['kind']}</td><td>{r['floor']}</td>"
        f"<td>{E((r.get('plan') or {}).get('archetype', '?'))}</td><td>{E(r.get('killer') or ('WIN' if r['victory'] else '-'))}</td>"
        f"<td>{r.get('elites_killed', 0)}</td><td>{len(r.get('deck', []))}</td><td>{r.get('duration_s', 0)/60:.1f}</td>"
        f"<td>{r.get('advantage', 0):+.2f}</td></tr>" for r in reversed(runs[-20:]))

    milestones = "".join(f"<div class='badge {'got' if when else ''}'><b>{E(label)}</b><span>{E(when) or 'locked'}</span></div>"
                         for label, when in _milestones(runs))

    coach_text = (root / "coach.md").read_text(encoding="utf-8") if (root / "coach.md").exists() else ""
    coach_last = coach_text.strip().split("## ")[-1] if coach_text.strip() else ""
    strategy = (root / "strategy.md").read_text(encoding="utf-8") if (root / "strategy.md").exists() else ""
    learned = strategy.partition("## Learned from runs")[2].strip()
    champ_strategy = (root / "champion_strategy.md").read_text(encoding="utf-8") \
        if (root / "champion_strategy.md").exists() else strategy

    cycles = [(int(r["run"]), r["note"]) for r in rows if r.get("note")]
    cycle_items = []
    for run, note in cycles:
        mm = re.search(r"([\d.]+)\)?$", note.split("=")[-1])
        if mm:
            tag = " (reverted)" if note.startswith("reverted") else (" *" if note.startswith("champion") else "")
            cycle_items.append((f"run {run}{tag}", float(mm.group(1))))
    cycle_bars = _bars(cycle_items[-12:], fmt="{:.1f}",
                       cls_fn=lambda label, v: "neg" if "reverted" in label else "pos") + \
        "<p class=muted>* new champion</p>"

    kpis = [
        ("Runs", f"{n}", f"{meta.get('runs_trained', 0)} trained"),
        ("Best floor", f"{best['floor'] if best else 0}", f"run {best['run']}" if best else ""),
        ("Wins", f"{wins}", "act 3 beaten"),
        ("Last 10 avg", f"{mean(last10):.1f}" if last10 else "-",
         (f"{'&#9650;' if trend >= 0 else '&#9660;'} {trend:+.1f} vs prev 10") if prev10 else ""),
        ("Champion bench", f"{champ.get('bench_mean', '-')}", f"cycle {champ.get('cycle', '-')}"),
        ("Act 1 cleared", f"{act1}", f"{act1 / max(1, n):.0%} of runs"),
        ("Exploration", f"{max(0.15, 0.97 ** meta.get('runs_trained', 0)):.2f}", "softmax temperature"),
        ("Time played", f"{hours:.1f} h", f"{(hours * 60 / max(1, n)):.1f} min/run"),
    ]
    kpi_html = "".join(f"<div class=kpi><span>{k}</span><b>{v}</b><small>{s}</small></div>" for k, v, s in kpis)

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Spire AI dashboard</title>
<style>
:root{{--bg:#f6f5f1;--card:#fff;--fg:#1d1d1f;--muted:#7a7a80;--line:#e3e1da;--accent:#c2410c;
--explore:#94a3b8;--bench:#ea580c;--win:#16a34a;--champ:#16a34a;--roll:#2563eb;--pos:#2563eb;--neg:#dc2626;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111214;--card:#1b1c1f;--fg:#ececec;--muted:#8b8d93;--line:#2c2e33;
--accent:#fb923c;--explore:#5b6472;--bench:#fb923c;--win:#4ade80;--champ:#4ade80;--roll:#60a5fa;--pos:#60a5fa;--neg:#f87171;color-scheme:dark}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:20px}} h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:0 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}} .kpi span{{color:var(--muted);font-size:12px}}
.kpi b{{display:block;font-size:24px}} .kpi small{{color:var(--muted)}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px}}
svg{{width:100%;height:auto}} svg text{{fill:var(--muted);font-size:11px}}
.grid{{stroke:var(--line)}} .act{{stroke:var(--accent);stroke-dasharray:4 4;opacity:.6}} .actlabel{{fill:var(--accent)}}
.explore{{fill:var(--explore)}} .bench{{fill:var(--bench)}} .win{{fill:var(--win)}}
.roll{{fill:none;stroke:var(--roll);stroke-width:2}} .champ{{fill:none;stroke:var(--champ);stroke-width:2;stroke-dasharray:6 3}}
.pos{{fill:var(--pos)}} .neg{{fill:var(--neg)}} .death{{fill:var(--neg);opacity:.8}} .axis{{stroke:var(--muted)}}
.hpline{{fill:none;stroke:var(--neg);stroke-width:2}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted);font-weight:500}} tr.win td{{color:var(--win);font-weight:600}}
.muted{{color:var(--muted)}} .big{{font-size:20px;font-weight:600;margin:0 0 6px}} .path{{font-size:18px;letter-spacing:2px}}
.badges{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}}
.badge{{border:1px dashed var(--line);border-radius:10px;padding:10px;opacity:.45}} .badge.got{{border-style:solid;border-color:var(--win);opacity:1}}
.badge span{{display:block;color:var(--muted);font-size:12px}}
.picks{{margin:0;padding-left:18px}} .legend span{{margin-right:14px}}
pre{{white-space:pre-wrap;font:13px/1.5 ui-monospace,Consolas,monospace;margin:0}}
.add{{color:var(--win)}} .del{{color:var(--neg);text-decoration:line-through}}
.scroll{{overflow-x:auto}}
</style></head><body><main>
<h1>Spire AI &middot; {E(model.get('character', ''))}</h1>
<p class=muted>Updated {__import__('datetime').datetime.now():%Y-%m-%d %H:%M} &middot; refreshes every 30 s</p>
<div class=kpis>{kpi_html}</div>

<div class=card><h2>Floor reached per run</h2>{_scatter(runs, rows)}
<p class="legend muted"><span style="color:var(--explore)">&#9679; explore</span><span style="color:var(--bench)">&#9679; bench</span>
<span style="color:var(--win)">&#9679; win</span><span style="color:var(--roll)">&#9472; 10-run average</span>
<span style="color:var(--champ)">&#9476; champion bench</span></p></div>

<div class=card><h2>Latest run</h2>{latest_html}</div>

<div class=card><h2>Milestones</h2><div class=badges>{milestones}</div></div>

<div class=grid2>
 <div class=card><h2>Top learned card weights</h2>{top_cards}
  <p class=muted>Skip rate {skip_rate:.0%} of card rewards</p></div>
 <div class=card><h2>Lowest card weights</h2>{bottom_cards or '<p class=muted>Needs more cards seen.</p>'}</div>
</div>

<div class=grid2>
 <div class=card><h2>Archetypes</h2><div class=scroll><table><tr><th>Archetype</th><th>Runs</th><th>Avg floor</th><th>Best</th><th>Pick bias</th><th>Synergy</th></tr>{arch_rows or '<tr><td class=muted colspan=6>none yet</td></tr>'}</table></div></div>
 <div class=card><h2>Bench score by cycle</h2>{cycle_bars}</div>
</div>

<div class=card><h2>Where runs end</h2>{_histogram(deaths)}
<p class=muted>Top killers: {E(', '.join(f'{k} ({v})' for k, v in killers.most_common(6))) or '-'}</p></div>

<div class=grid2>
 <div class=card><h2>Path preferences (learned)</h2>{_bars(path_items)}</div>
 <div class=card><h2>Rest site preferences (learned)</h2>{_bars(rest_items)}
  <h2 style="margin-top:14px">Combat parameters (tuned)</h2>{_bars(param_items, fmt="{:.2f}")}</div>
</div>

<div class=card><h2>Run history</h2><div class=scroll><table><tr><th>#</th><th>Kind</th><th>Floor</th><th>Archetype</th><th>Killed by</th><th>Elites</th><th>Deck</th><th>Min</th><th>Adv</th></tr>{hist_rows}</table></div></div>

<div class=grid2>
 <div class=card><h2>Coach's latest notes</h2><pre>{E(coach_last) or 'The coach reviews every few runs.'}</pre></div>
 <div class=card><h2>Playbook lessons learned</h2><pre>{E(learned) or '(none yet)'}</pre>
  <h2 style="margin-top:14px">Changes since champion</h2>{_diff_html(champ_strategy, strategy)}</div>
</div>
</main></body></html>"""
    out = root / "dashboard.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(render(Path(sys.argv[1] if len(sys.argv) > 1 else ".")))
