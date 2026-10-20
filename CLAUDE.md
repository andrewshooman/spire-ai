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
agent\sim.py        Offline Ironclad combat simulator: game-format states -> real planner; scripted A0 monsters
                    (Jaw Worm, Cultist, Nob, Lagavulin, 3 Sentries, Hexaghost, Guardian, Slime Boss (+splits),
                    Spheric Guardian, Snake Plant, Shelled Parasite, Book of Stabbing); ~4000 fights/min.
agent\sim_train.py  sim.bat: bench / train (antithetic ES) / compare / calibrate / apply. Writes ONLY
                    evolve\<char>\sim\ (stats kept apart from live runs); `apply` copies sim_params.json into
                    model.json (bot must be stopped, backup kept). Live runs record fight_log (deck/HP/enemies at
                    each fight start) so `calibrate` compares sim vs real outcomes.
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
9. Shop: 'shop:card' bar + extra-card penalty, removal rule bonus, cards.SHOP_RELIC_PRIOR; LLM shop list with priorities.
10. Exploration: temperature floor 0.35 (0.985^runs); explore runs sample CONFIG.risk (-0.5..1.5 extra elite pull),
    saved as "risk" in run files; the coach sees depth by risk.
11. gamedata.py: relic/potion text from the player's desktop-1.0.jar, cached in evolve\_cache (never committed);
    used in boss-relic and shop prompts (shop cards show cost + text). Smith: cards.UPGRADE_PRIORITY + one LLM call
    with current -> upgraded text.
12. analyze.bat (agent\analyze.py): runs/hr, act reach, killers, depth by risk, shops, relics, LLM use, aborted runs.
13. Simulator: Act 3 hallway (3 Shapes, Orb Walker, Transient); results in the History tab "Offline simulator".
14. Self-improvement: evidence.py track record in LLM prompts (cards: takers vs passers of the same offer in the
    same act; events; boss relics vs all boss relics; shop buys); replay.jsonl buffer (last 150 runs, --replay 3
    older runs re-learned per explore run at --replay-lr 0.3 with current-weight probabilities); learned value
    estimate model.value(state) (linear, fitted on decision states -> final reward; replaces per-act baselines
    after 20 runs; states from policy._state_features); context card features cardctx:<card>:a<act>,
    cardctx:<card>|<archetype>, cardctx:<card>:big. Explore runs also sample a build "flavor" (analyze.bat shows
    depth by flavor); card picks get boss-need bonuses (cards.BOSS_NEEDS).
15. synergy.py: Ironclad role tags for cards/relics, engine->payoff RULES and NEEDS (dead payoffs) as learnable
    features synrule:<rule> / synneed:<tag> (priors from the rules), reasons in card/shop prompts, relic_fit for
    boss/shop relics. Guide (Panacea A20 Ironclad, applied at A0): cards.cardctx_prior (act-1 vs late picks),
    GUIDE_BOSS_RELIC tiers, GUIDE_SHOP_RELIC values, Neow ranking, policy.EVENT_RULES per event, shops at 170+
    gold, Strikes removed before Defends unless block-heavy with no exhaust use; playbook sections rewritten.
    Ascension tuning comes later.
16. Web app (agent\webapp.py + agent\webpp.html + agent\stats.py): JSON API over run files/live.json/sim,
    tabs Overview (KPIs, journey bar, trend, milestones, killers) / Live run / Runs (filters + run detail) /
    Insights (blocks, killers with entry HP, A/B, risk, flavor, card lifts, boss relics) / Simulator / Settings.
    Settings -> evolve\settings.json, applied by evolve.py before each run (--web-settings, only with the default
    workdir): character switch (own workspace), ascension, LLM switches, coach_every, ab, pause. Character chips
    read every evolve\<char>. Planner: fight-over bonus + stall guard (run 199 ended 60 turns vs Thorns).
Next ideas: calibrate the sim (sim.bat calibrate once fight_log has hundreds of fights); set the default risk from
analyze.bat; tune the pre-boss rest threshold per boss from data; decompile monster AI for an exact sim (needs the
user's OK to download Vineflower); Gremlin Leader / Slavers / Champ / Collector / Automaton in the sim.
Commit and push after each working change.
