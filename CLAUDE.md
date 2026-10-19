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
agent\live.py       LiveView: policy writes evolve\<char>\live.json (~1/s, on screen change): HUD data, map + route, combat
                    (hand, enemies, intents), last 40 decisions with "why". serve() = localhost http thread.
agent\dashboard.py  Renders index.html (= dashboard.html) from template agent\page.html: Spire-themed app, tabs Live run
                    (polls live.json every 2 s) / History (inline-SVG charts; history.html refreshed every 30 s).
                    evolve.py serves the workspace at http://127.0.0.1:8765/ (--live-port, 0 = off).
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
1. DONE install.ps1 (copy or in-place, sts-bench pin, venv, CommunicationMod config, mods check, Ollama model,
   shortcuts, self-tests) + README "Setup on a new PC" and "Updating".
2. DONE live view (live.json + server on 8765; dashboard.bat opens it when the bot runs; start.bat opens it
   unless OPEN_LIVE_VIEW=no).
3. DONE Spire-themed page (agent\page.html): HUD, vertical act map with route, enemy panels, card tiles with cost
   orbs and the planned next card highlighted, decision log; History tab restyled.
4. Edge cases fixed: cp1252 bytes from the game (N'loth), grid confirm, Match and Keep! (memory play, no LLM).
   Focus is Ironclad only for now; other classes later.
5. LLM: events ask only when the top options are within --llm-gap, answers cached in evolve\<char>\llm_cache.json,
   event/boss-relic choices are learned (features ev:<event>:<label>, relic:<name>); shop = one LLM shopping list
   per visit (--llm-shop); coach runs in the background (--coach-background) and in-game LLM calls fall back to the
   policy while it holds the GPU (policy.LLM_BUSY).
6. Reward v2 (model.outcome): convex in depth + 0.06/elite so risky power-ups pay; map rules push act 1-2 elites;
   bench selection compares the same reward and reverts only after two bad cycles in a row.

7. Speed: in-game LLM calls send reasoning_effort "none" (qwen3 ignored /no_think and thought 500-800 tokens per
   answer: 10-30 s -> ~0.2 s); the coach still thinks. So the LLM now advises every event (--llm-gap 99) and card
   rewards (--llm-cards; explore runs follow it --llm-share 0.7 of the time). Optional EVENT_MODEL. GAME_SPEED
   (default 5, was 2) is written to SuperFastMode's config by launch-game.ps1 before the game starts. start.bat sets
   OLLAMA_KEEP_ALIVE=-1, flash attention and q8_0 KV cache.
8. Learning v3: per-act REINFORCE baselines (events tagged with act), antithetic ES pairs (+eps then -eps on the
   same seed, ws.state["es_pair"]).
Commit and push after each working change.
