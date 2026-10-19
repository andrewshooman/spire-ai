# Spire AI v2 - project context for Claude Code

## Goal
Self-learning Slay the Spire bot. A fast learned policy plays every move (no LLM per move); runs should be quick
(SuperFastMode) and learning NN-style from outcomes, with winning Act 3 as the ultimate goal. The LLM (Ollama) is only
for big-picture work: events/Neow, boss relics, and a coach that analyzes runs every N runs.
User: Andrew (data/AI engineer, stats background). Prefers concise answers and ready-to-run scripts over manual steps.

## Machine
Windows 11, AMD RX 7900 XT 20GB, Ollama models on F:\OllamaModels, model qwen3:14b.
Ollama ONLY uses the GPU with OLLAMA_VULKAN=1 (ROCm detection fails -> 100% CPU). start.bat kills the tray app and
runs `ollama serve` with OLLAMA_VULKAN=1 and OLLAMA_CONTEXT_LENGTH=32768.
Desktop was moved back from OneDrive to C:\Users\sushi\Desktop; OneDrive was removed.
Python always needs PYTHONUTF8=1 (sts-bench reads files with the default encoding -> cp1252 crashes on Windows).

## Layout (C:\sts-evolve is a git repo -> github.com/andrewshooman/spire-ai)
agent\planner.py    Whole-turn combat search: tries card orders within energy, simulates damage/block/debuffs and enemy
                    mechanics (Barricade, Thorns/Sharp Hide, Malleable, Enrage, Curiosity, Time Warp, Artifact,
                    Intangible, Beat of Death, Lagavulin asleep) + kill priorities; ~1-10 ms per decision.
agent\policy.py     PolicyAgent: all moves. Combat = planner first step + playbook potion rules; out-of-combat choices are
                    softmax over learned features (cards/skip, path, rest/smith, shop, boss relic); records events;
                    run plan (archetype, core, wanted). Playbook rules as softmax bonuses (pre-boss rest, Neow's
                    Lament elites, act-3 elite caution, boss-relic context rules, Neow ranking). LLM only in
                    _event/_boss_reward, given only the relevant playbook sections (playbook_for) + policy scores.
agent\model.py      Linear softmax policy + REINFORCE (reward = floor/50 + 1 if win, advantage vs running baseline),
                    ES for combat params, priors, coach nudges, save/load model.json.
agent\cards.py      Priors (Ironclad tiers, archetypes for all 4 classes, boss relic priors), printed-text effect parser.
agent\evolve.py     Main loop. Cycles (default 10): explore runs learn; 2 bench runs on fixed seeds (BENCHA1, BENCHB2),
                    near-greedy; selection keeps/reverts the champion (model.json + strategy.md). Coach every 5 explore
                    runs (NOTES / NUDGES json / LEARNED -> strategy.md "Learned from runs"). Writes runs\run_NNNN.json,
                    progress.csv, state.json, dashboard. Patches: Path.symlink_to (Windows), session.choose_command
                    (tutorial "FTUE" tip screens: key Confirm / wait 300). Registers SCAFFOLDS["policy"].
agent\dashboard.py  Static dashboard.html (inline SVG, auto-refresh 30 s), rendered after every run.
agent\evolve_tools.py  watch/stats/weights/notes/dashboard/report/campaign/baseline/reset (the .bat launchers).
agent\strategy_baseline.md  Playbook by topic (Neow, Combat, Map, Events, Boss relics, Act 1-4, classes). Synced into
                    evolve\<char>\strategy.md on start, keeping the coach's "Learned from runs" bullets.
start.bat (+ settings.bat from settings.example.bat), launch-game.ps1 (javaw ModTheSpire.jar --skip-launcher from the
game folder; reuses the last mod list), install.ps1, *.bat tools, tests\test_e2e.py (fake game + fake LLM, full loop).
sts-bench\  (gitignored) github.com/alavico/sts-bench pinned at 735b90625f63449a275c285c39d02e36a2942bdb; venv at
            sts-bench\.venv. Internals used: async_runner._run_job/JobSpec/QueueConfig/LogRouter, env.HarnessServer +
            CommunicationModEnv, replay.group, trajectory.read_records, RunMetrics, actions.validate, state.serialize.
evolve\<character>\ (gitignored) model.json, champion_*, runs\, history\, progress.csv, coach.md, strategy.md,
            dashboard.html. Logs and trajectories: sts-bench\logs\<date>\.

## Game connection
Mods: BaseMod + CommunicationMod (+ SuperFastMode). CommunicationMod config at
%LOCALAPPDATA%\ModTheSpire\CommunicationMod\config.properties:
command=C\:\\sts-evolve\\sts-bench\\.venv\\Scripts\\python.exe C\:\\sts-evolve\\sts-bench\\relay.py and
runAtGameStart=true. The relay connects to the harness on port 9999, so start the bot before the game.
The game must be at the main menu, not mid-run. A fresh profile shows tutorial tips that only accept clicks.

## Test
sts-bench\.venv\Scripts\python.exe tests\test_e2e.py   (expects "E2E OK"; run after every change)

## Open tasks
1. install.ps1: support a repo cloned directly into C:\sts-evolve (skip the copy when source == destination). Add README
   "Setup on a new PC" (clone -> install.ps1 -> Spire start) and "Updating" (git pull).
2. Live view: PolicyAgent writes live.json (~1/s): floor, act, HP, gold, deck, relics, potions, plan, screen, combat
   (hand with costs, enemies HP/block/intent, energy), last 40 decisions with their "why" notes. evolve.py serves
   the workspace at http://127.0.0.1:8765 (thread); the live page polls every 2 s; dashboard.bat/start.bat open it.
3. Slay the Spire themed redesign: dark stone/parchment, Kreon-style font, vertical act map with room icons, HUD top
   bar (heart HP, gold, relics, potion slots), card tiles with a cost orb, enemy panels with HP bar and intent;
   tabs: Live run / History (existing charts restyled).
4. After real testing: fix edge cases from logs (unhandled screens, loops, forced moves); add tier priors for
   Silent/Defect/Watcher; consider experiments (LLM events on/off, coach on/off, learning rate).
Commit and push after each working change.
