"""Bundle the bot's learned combat weights for each character trained on 30+ runs into the mod's resources
(evolve/<character>/model.json "params"); the mod picks the set for the character you play.

Python, not PowerShell: ConvertFrom-Json is case-insensitive and the models hold feature names that differ
only by case (relicctx:Ancient Tea Set|Exhaust vs |exhaust), which made the build stop."""
import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
evolve = here.parent.parent / "evolve"
out = here / "resources" / "spireseerResources" / "weights.json"
weights = {}
for c in ("ironclad", "silent", "defect", "watcher"):
    m = evolve / c / "model.json"
    if not m.exists():
        continue
    runs = len(list((evolve / c / "runs").glob("run_*.json")))
    if runs < 30:
        print(f"  {c}: {runs} runs, not enough yet (uses Ironclad weights)")
        continue
    params = json.loads(m.read_text(encoding="utf-8")).get("params", {})
    weights[c] = {"runs": runs, "params": params}
    print(f"  {c}: weights from {runs} runs")
if weights:
    out.write_text(json.dumps(weights, indent=4), encoding="utf-8")
elif not out.exists():
    print("  no trained weights found; the mod uses its built-in defaults")
sys.exit(0)
