# Spire AI v2 - a self-learning Slay the Spire bot

A fast learned policy plays every move (no LLM per move). After each run, REINFORCE moves the
choice weights (card picks/skips, path, rest vs smith, shop, boss relics) toward choices from runs
that went deeper; evolution strategies tune the combat parameters. Reward = floor/50, +1 for winning.
An LLM (Ollama) only handles events/boss relics and a coach review every few runs (notes, weight
nudges, playbook lessons). Cycles: explore runs learn; bench runs on fixed seeds pick the champion.

- `start.bat` - Ollama on GPU + bot + game. Settings in `settings.bat`.
- `dashboard.bat` - live dashboard (`evolve\<character>\dashboard.html`).
- `watch`, `stats`, `weights`, `notes`, `report [N]`, `campaign`, `baseline`, `reset` - tools.
- `agent\` - code: `policy.py` (moves), `model.py` (learning), `cards.py` (knowledge/parser),
  `evolve.py` (loop), `dashboard.py`, `strategy_baseline.md` (playbook the coach extends).
- `tests\test_e2e.py` - full loop against a fake game:
  `sts-bench\.venv\Scripts\python.exe tests\test_e2e.py`
- Data per character in `evolve\<character>\` (model.json, runs\, progress.csv, coach.md, strategy.md).
