# Spire AI v2 - a self-learning Slay the Spire bot

A fast learned policy plays every move (no LLM per move). Combat is a whole-turn search
(`planner.py`) that simulates card orders against the enemies' intents and mechanics. After each run,
REINFORCE moves the choice weights (card picks/skips, path, rest vs smith, shop, boss relics) toward
choices from runs that went deeper; evolution strategies tune the combat parameters. Reward =
floor/50, +1 for winning. An LLM (Ollama) only handles events/Neow/boss relics and a coach review every
few runs (notes, weight nudges, playbook lessons). Cycles: explore runs learn; bench runs on fixed
seeds pick the champion.

## Setup on a new PC
1. Install Steam + Slay the Spire, and subscribe in the Steam Workshop to **ModTheSpire**, **BaseMod**,
   **Communication Mod** and (recommended) **Super Fast Mode**. Launch the game once with mods enabled.
2. Install Git and Python 3.11+ (`winget install Git.Git Python.Python.3.12`). Optional: Ollama for the
   LLM parts (`winget install Ollama.Ollama`).
3. Clone and install:
   ```
   git clone https://github.com/andrewshooman/spire-ai C:\sts-evolve
   cd C:\sts-evolve
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
   The installer clones the pinned sts-bench, creates its Python environment, points Communication Mod
   at the relay, pulls the LLM model, makes desktop shortcuts and runs the self-tests. It is safe to rerun.
   Cloned somewhere else? Run the same command there; it copies the code to `C:\sts-evolve`
   (or pass `-Dest D:\path`). Flags: `-NoShortcuts`, `-NoModel`.
4. Double-click **Spire start**. Keep the game at the main menu, not mid-run.

## Updating
```
cd C:\sts-evolve
git pull
```
Then restart **Spire start**. Training data in `evolve\` and your `settings.bat` are untouched; the
playbook body in `evolve\<character>\strategy.md` is refreshed from `agent\strategy_baseline.md` on
start while the coach's "Learned from runs" bullets are kept. Rerun `install.ps1` only if an update
says the dependency pin changed.

## Files
- `start.bat` - Ollama on GPU + bot + game. Settings in `settings.bat`.
- `dashboard.bat` - dashboard (`evolve\<character>\dashboard.html`).
- `watch`, `stats`, `weights`, `notes`, `report [N]`, `campaign`, `baseline`, `reset` - tools.
- `agent\` - code: `policy.py` (moves), `planner.py` (combat search), `model.py` (learning),
  `cards.py` (knowledge/parser), `evolve.py` (loop), `dashboard.py`, `strategy_baseline.md` (playbook).
- Tests: `sts-bench\.venv\Scripts\python.exe tests\test_planner.py` and `tests\test_e2e.py`.
- Data per character in `evolve\<character>\` (model.json, runs\, progress.csv, coach.md, strategy.md).
