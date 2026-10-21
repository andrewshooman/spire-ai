# Spire AI v2 - a self-learning Slay the Spire bot

A fast learned policy plays every move (no LLM per move). Combat is a whole-turn search
(`planner.py`) that simulates card orders against the enemies' intents and mechanics. After each run,
REINFORCE moves the choice weights (card picks/skips, path, rest vs smith, shop, boss relics) toward
choices from runs that won or beat bosses; evolution strategies tune the combat parameters. Reward =
bosses beaten and wins, never floors: act 1 boss 0.1, act 2 boss 0.5, a win 2.0 (reaching a boss to lose
to it is worth no more than dying earlier going for a stronger deck). An LLM (Ollama) only handles events/Neow/boss relics and a coach review every
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

## Your own runs (separate profile, Steam Deck)

- Play on your own profile (main menu -> profile; the bot uses the first one, `BOT_SLOT=0` in settings.bat).
  Your stats stay separate, `launch-game.ps1` switches the game back to the bot's profile when the bot starts
  it, and the bot waits instead of playing while the game is on your profile.
- Finished runs on your profile are imported before the bot's next run. It learns your card picks and skips,
  boss relics and rest/smith choices as a light data point (weight 0.1 vs its own runs, capped push; Settings tab), and the LLM sees how your
  picks did ("human player's runs"). Combat isn't in the game's run history, so combat isn't learned.
- Steam Deck: copy its run history folder (`~/.local/share/Steam/steamapps/common/SlayTheSpire/runs`; the second profile's runs are in
  `runs/1_IRONCLAD`) to the PC, then `import_runs.bat <that folder>`. Runs the bot played are
  recognized by seed and skipped.
- The Overview tab shows "You vs the bot".

## Expert runs (other players' run files)

```
import_runs.bat --expert <folder of .run files>
```
Imports other players' runs (any character, nested folders, duplicates skipped) into `evolve\<character>\expert\`,
kept apart from your runs and the bot's stats. Each character's bot learns their card picks and boss relics once
at the start of its next run (weight 0.1, capped push; Settings tab "Weight of expert players' runs", 0 = ignore);
their rest choices are not learned (strong decks lose little HP and smith where this bot must rest). The LLM sees
their track record as "expert A20 players". Loaded so far: the vmService Slay-the-Spire-data runs, 565 A20 runs
(Ironclad 157, Silent 156, Defect 156, Watcher 96).

## Files
- `start.bat` - Ollama on GPU + bot + game. Settings in `settings.bat`.
- `dashboard.bat` - the web app at http://127.0.0.1:8765/ (served by the bot while it runs, otherwise by
  dashboard.bat itself): Overview, Live run, Runs (click a run for the full story), Insights, Simulator and
  Settings (character, ascension, LLM switches, coach, A/B, pause; saved to `evolve\settings.json` and applied
  at the start of the next run).
- `watch`, `stats`, `weights`, `notes`, `report [N]`, `campaign`, `baseline`, `reset` - tools.
- `analyze.bat [--since N | --last N]` - one report on the live runs: runs/hr, act reach rates, killers,
  depth by risk level, shop spending, boss relics, card picks, LLM use, aborted runs.
- `sim.bat [bench|train|compare|calibrate|apply]` - offline combat simulator (thousands of fights per
  minute with the real combat planner). Its stats stay in `evolve\<character>\sim\`; `apply` (bot stopped)
  copies the simulator-tuned combat parameters into the live model.
- `agent\` - code: `policy.py` (moves), `planner.py` (combat search), `model.py` (learning),
  `cards.py` (knowledge/parser), `evolve.py` (loop), `dashboard.py`, `strategy_baseline.md` (playbook).
- Tests: `sts-bench\.venv\Scripts\python.exe tests\test_planner.py` and `tests\test_e2e.py`.
- Data per character in `evolve\<character>\` (model.json, runs\, progress.csv, coach.md, strategy.md).
