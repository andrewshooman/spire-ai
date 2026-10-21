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
agent\evolve.py     Main loop. Cycles (default 10): explore runs learn; 2 bench runs per cycle rotating over 6 seeds,
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

Storage: C:\sts-evolve\sts-bench\logs and C:\sts-evolve\evolve are junctions to F:\SpireAI\logs and
F:\SpireAI\evolve (move-storage.ps1, run with the bot paused). Paths in code are unchanged.

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
16. Web app (agent\webapp.py + agent\web\app.html + agent\stats.py): JSON API over run files/live.json/sim,
    tabs Overview (KPIs, journey bar, trend, milestones, killers) / Live run / Runs (filters + run detail) /
    Insights (blocks, killers with entry HP, A/B, risk, flavor, card lifts, boss relics) / Simulator / Settings.
    Settings -> evolve\settings.json, applied by evolve.py before each run (--web-settings, only with the default
    workdir): character switch (own workspace), ascension, LLM switches, coach_every, ab, pause. Character chips
    read every evolve\<char>. Planner: fight-over bonus + stall guard (run 199 ended 60 turns vs Thorns).
17. Relics learned like cards: policy._relic_features -> relic:/shoprelic:<name>, relicctx:<relic>|<archetype>,
    relicctx:<relic>:a<act>, relicctx:<relic>:big, relicfit:<tag> (value = synergy.relic_fit; prior 1.0) for boss
    and shop relics. Boss relics: training runs ask the LLM only --llm-share of the time (it had made 100% of
    picks: Runic Pyramid/Astrolabe/Coffee Dripper) + novelty bonus 0.5/sqrt(1+times taken); run files record
    'offered' so evidence compares taken vs passed. Shop relics are rarely affordable (150-250 gold).
18. Act 2 boss + lean decks (75 runs reached the act 2 boss, 44% won; entry HP <50% -> 10%, 85%+ -> 83%):
    rest rule (act 2 rest under 60% HP, pre-boss rest under 85%); Coffee Dripper -2.5 without real healing
    (Burning Blood doesn't count) and overrides the LLM; boss potions: no Flex/Steroid without attacks, no
    Snecko Oil into 8+ cards, keep the last potion, Block/heal on big hits; Bronze Orbs priority 0.55.
    Skips: the LLM had never skipped (0 of 2269) and skip:a2 ran to -5.5 -> bound skip:* to +-1.5, skip bonus
    by deck size, LLM skip test with the deck's weakest cards, 28+ cards: only engine/synergy/boss-need cards
    (_earns_slot); A/B switch skip_rules.
19. relics.py: boss relics judged against the run (profile: avg cost, 2+/0/X-cost cards, energy demand vs supply,
    draw, exhaust outlets, clog makers, self-damage, unupgraded, healing, HP, gold, potions). boss_fit per relic
    (energy relics by energy gap, Pyramid by outlets vs clog, Snecko by cost curve, Ectoplasm by act/gold...);
    policy.boss_relic_rule = guide tier + fit (deck-specific reasons in the LLM label + profile line in the
    prompt). Learnable: relicfit:boss (prior 1) and relicctx:<relic>|cost_hi/cost_lo/draw_hi/exhaust/
    energy_gen/clog/hp_lo/gold_hi for boss and shop relics. Runic Pyramid had won ~50% of offers via a
    generic 'great with exhaust/energy' blurb the LLM repeated for every deck.
20. Thorns stall v2 (run 211, 54 HP Spiker never attacked): when every living enemy retaliates (no safe target)
    the planner counts Thorns/Sharp Hide at 25% (sim.retal); stall counter = turns with enemy HP unchanged,
    whatever was played. Relic conflicts: relics.DEAD_WITH (Sozu kills Sacred Bark/Potion Belt/..., Ectoplasm
    gold relics, Coffee Dripper rest relics) -> -3 (and -1 for killing an owned one), GOOD_WITH pairs; used in
    boss_fit (LLM override at <= -2), shop quality, shop LLM labels and the LLM shopping list.
21. Your own runs (human.py): the game's run history (.run files: card_choices offered/picked, boss_relics,
    campfire_choices, events, purchases/purges, HP/gold per floor) from save slots other than BOT_SLOT
    (runs\<n>_IRONCLAD; floors may be floats -> human.normalize) and evolve\human_inbox\<CHAR> (import_runs.bat <folder>, e.g. Steam Deck). Timeline rebuilds
    deck/relics per floor; card/boss-relic/rest decisions use the policy's own features; stored in
    evolve\<char>\human\runs (never mixed into bot stats); evolve.py learns pending ones before each run
    (model.learn replay=True, lr * --human-lr, now 0.1) and passes human evidence to the LLM ('human player's
    runs'). Bot runs skipped by seed (human.seed_number). launch-game.ps1 sets DEFAULT_SLOT=BOT_SLOT;
    evolve.wait_for_bot_slot() never plays while the game has another profile open. gamedata has id->name.
22. Your runs are a light data point: --human-lr 0.1 (Settings max 0.5) and model.learn(adv_cap=1.0) so one
    human win doesn't push every choice at the maximum. A/B switch event_pull (explore runs): ? rooms +0.8,
    hallway fights -0.3 in _map_rules (the player took 5.7 events/run vs the bot's 2.2, +5 relics).
23. Champion selection v2 (the run-130 champion, crowned by one lucky 2-run bench avg of 40, caused 5 reverts
    that kept wiping learning + migrations): 6 bench seeds BENCHA1..BENCHF6, --bench-per-cycle 2 rotating;
    each bench reward scored vs its seed's history (state bench_hist, residual); model score = mean of the
    last 6 residuals (state bench_window); new champion only on a full window above it; revert only when
    < champion - 2 SE two cycles in a row; evolve.migrate() (bounds clamp + reward version) runs on load
    and after every revert; window resets after a revert. tests	est_select.py.
    The Champ (9 of 43 recent deaths, all in phase 2 at 84% entry HP): potions -- only permanent buffs
    while it is above 55%, burst potions (damage/buff/tempo) below, Block/heal kept for Execute; planner --
    phase 2 damage on it x1.5, crossing half penalized (0.6 x Execute) when two Executes would kill,
    lifted by the stall guard after one turn.
24. mod\spireseer: Spireseer (renamed from AutoSpire: the Workshop already has 'Auto-Spire' mods), Java mod
    (ModTheSpire + BaseMod), F7 Off/Suggest/Auto, F8 pause.
    brain\ = game-free Java port of the planner core (Sim model, Effects text parser, Planner DFS; params =
    the bot's learned combat weights); GameBridge reads live combat (game-computed card damage per target,
    intents via getIntentDmg + ReflectionHacks intentMultiAmt) and plays via cardQueue/CardQueueItem, end turn
    via endTurnButton.disable(true) (same calls as CommunicationMod). Never AUTO at launch, off in Daily
    Climbs, waits on choice screens. build.ps1 [-Install] (JDK 21 --release 8; tests PlannerTest first).
    PotionAdvisor (policy._potion rules) + auto potions; Suggest overlay numbers the cards 1-2-3 (green glow on
    the next), game reticle on the target, USE on potions, END TURN marker. publish.ps1 [-Upload] fills
    workshop\config.json (title/description incl. "The AI behind the decisions") + content; art\make_preview.py
    draws workshop\image.jpg + badge. Not yet: poison/orbs/stances, out-of-combat, hand-select screens.
25. Other characters (Silent first): evolve.warm_start() gives a young non-Ironclad model (<50 runs) the
    Ironclad's TRANSFER weights (node/nodeact/rest/smith/restx/shop/skip/relic/shoprelic/relicctx/relicfit/ev)
    + combat params once, temp_offset 30; card weights stay its own. cards.TIERS['THE_SILENT'] priors;
    Effects.shivs ('Add N Shivs') -> planner hits the priority target for 4+Str each; rest under 45% HP in
    any act; act 1 elites for non-Ironclad only with HP >= 60% and 2+ damage cards. Web app follows the
    character the bot is playing (unless you picked one in the tab), per-character accent, global status pill.
    Poison: planner reads existing Poison, Catalyst (poison_mult), Bouncing Flask (poison x times, priority
    target), Corpse Explosion single-target; an enemy whose Poison >= HP doesn't attack (dies first).
26. Overnight Silent (34 runs: act 1 boss 76%, act 2 boss 3%): low-HP path rule in every act (<40% HP: M/E
    down, R/?/$ up; x2 under 25%); hp_budget left the A/B (always on); never auto Smoke Bomb (escape aborted a
    run); act 2 elites need 70% HP for non-Ironclad; Velvet Choker counts Shiv makers (relics.profile shiv_makers).
27. Turn + macro analysis (35 Silent, 60 Ironclad runs): incoming-damage prediction is exact, ~6% of turns end with
    energy + a playable card, but strong cards sat in hand (Wraith Form 12%, Corruption 17%, Double Tap 16%,
    Reaper 17%). Planner now models player Intangible (hits deal 1), Double Tap / Burst (echo play), Reaper heal.
    Silent deep runs had 2x the area damage and the poison or Accuracy+Cloak engines: synergy.py gained Silent
    tags/RULES/NEEDS (poison_src/pay, shiv_src/pay, discard_src/pay), area poison counts as 'aoe', Silent/Defect/
    Watcher starters excluded as feeders, Quick Slash prior 0.6. The Spireseer mod's planner lacks these (update later).
28. Silent analysis (125 runs: act 1 boss 75%, act 2 boss 10%; act 2 = 81 of 112 deaths, attrition: 12 HP per
    hallway fight in runs that die vs 6 in clears). Fixes: FIGHT_EVENT_GUARD (Masked Bandits under 70% HP,
    Colosseum's second fight under 60%) overrides the LLM (cached 'Fight!' fought at 2/65 HP); planner (Python
    + Java brain) values permanent Dexterity like Strength (DEX_BLOCK_CARDS 1.5); Silent tiers 1.6/1.2/0.6/0.0
    with migration silent_tiers_v1 (+0.4 on learned weights, Footwork reset).
    Expert runs: human.py source 'expert' (import_runs.bat --expert <folder>), all 4 starters, Neow log, Peace
    Pipe, Pandora's/Empty Cage/Astrolabe in the deck rebuild; evolve.learn_expert_runs (--expert-lr 0.1, cards +
    boss relics only), evidence.expert -> "expert A20 players". Loaded: 565 A20 runs (vmService data).
29. Ironclad vs 157 expert runs (bot runs 130+: act 1 boss 88%, act 2 boss 17%, Champ 17% of deaths):
    the bot loved Sword Boomerang/Twin Strike/Perfected Strike/Heavy Blade/Searing Blow (experts 0-6%) and
    skipped Feed/Shockwave/Burning Pact/Impervious/Disarm/Offering. Learnable features expertpick (expert
    pick rate in this act minus their mean, shrunk; prior 3.0, bounds 0..6) and expertrelic (prior 2.0) from
    human.expert_rates; off while converting expert runs (Config.expert_features). Held-out agreement with
    experts' picks: Ironclad 49 -> 52%, Silent 25 -> 30%. Ironclad map: act 1 +0.5 elite pull until 2 elites
    (HP 60%+), act 2 M -0.4 and E +0.3 at 70%+ HP. sts-bench caps raised (max_decisions 3000, max_steps
    8000): two deep runs had aborted at 1000. syn_notes always on; LLM card advice off by default.
30. Goal = won runs, not floors. Reward v4 (Model.outcome): act 1 boss 0.1, act 2 boss 0.5, win 2.0; floors
    and elites count for nothing by themselves. On the version change migrate() re-scores replay.jsonl and
    refits the value estimate (shuffled SGD; kept only if it beats the mean), resets baselines, bench history
    (always re-scored), window and champion. Imported human/expert runs re-scored when learned
    (human.current_reward). analyze.bat leads with wins / act 2 boss / score, A/B with a z-test on act 2 boss
    beaten; LLM track records (evidence.py) give act 2 boss % and wins instead of average floor.
31. Web app: Overview "Best cards and synergies" (stats.best, /api/best; by act 2 boss / wins, bot + experts)
    replaced "You vs the bot"; Settings "Restart after this run" (evolve/restart.request -> exit 75 between
    runs, game closed; start.bat relaunches the game and reruns the bot). Relics: more RELIC_TAGS (count in
    card synergies), path features pathrelic:<relic>:<room> (policy.PATH_RELIC priors).
    Card utility (agent/cardutil.py): each run's protocol log -> evolve/<char>/carduse/run_NNNN.json (hand
    and plays per turn, HP lost per fight); features cardplay (play rate when in hand vs average), cardfight
    (HP vs the encounter's usual when in the deck, z), cardpair (co-played with cards in the deck beyond
    their separate rates); priors 1.0/1.0/1.5, bounds 0..4; synced at start and after every run.
32. Ironclad focus (then Silent). Imported runs survive reverts (human.learned_items -> <source>/learned.jsonl,
    model meta learned_ids; evolve.learn_imported relearns missing ids). Act 2 boss prep (3 potions at the
    boss -> 53% beaten vs 23-29% with 1-2): elite openers in act 2+ only under 60% HP or with a full belt;
    potion buys before the boss (_boss_prep: act 1 floor 10+, act 2 floor 24+) incl. an override of the LLM
    shop list; +0.6 shop pull with 120+ gold and a free slot. Champ (47 logged fights): losses start 26 HP
    lower and deal 38 vs 51 damage a turn (Champ left at 163 HP), not a misplay problem -> BOSS_NEEDS Champ
    gains damage; boss needs are the learnable feature bossneed (prior 1.0) instead of a fixed bonus.
33. Every act's card picks prepare for that act's boss: learnable bossneed:<boss>:<role> features (roles damage/
    aoe/block/draw/scaling/debuff; half strength in the first half of the act), priors cards.BOSS_NEEDS reset
    from Ironclad decks that beat each boss vs lost (Champ: scaling + draw, not block/debuff; Automaton:
    scaling, not AoE; Hexaghost: scaling; Guardian: damage).
Next ideas: calibrate the sim (sim.bat calibrate once fight_log has hundreds of fights); set the default risk from
analyze.bat; tune the pre-boss rest threshold per boss from data; decompile monster AI for an exact sim (needs the
user's OK to download Vineflower); Gremlin Leader / Slavers / Champ / Collector / Automaton in the sim.
Commit and push after each working change.
