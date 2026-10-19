@rem ===== Spire AI settings =====
@rem Edit, save, then close and reopen Spire start.

@rem Character to train: ironclad, silent, defect or watcher.
@rem Each character keeps its own model and progress in evolve\<character>.
set CHARACTER=ironclad

@rem LLM (Ollama) for big-picture work: Neow, events, card rewards, shops, boss relics, coaching
set MODEL=qwen3:14b

@rem Optional different model for the in-game questions (blank = MODEL). In-game calls run with
@rem thinking off (~0.2 s each); the coach keeps thinking.
set EVENT_MODEL=

@rem Let the LLM advise card rewards and make one shopping list per shop: on or off
set LLM_CARDS=on
set LLM_SHOP=on

@rem Let the LLM choose event options and boss relics: on or off
@rem (off = fully LLM-free play; the policy's own heuristics decide)
set LLM_EVENTS=on

@rem Coach review every N training runs (0 = no coach)
set COACH_EVERY=5

@rem Let the coach think before answering: on (better) or off (faster)
set COACH_THINK=on

@rem Open the game automatically: yes or no
set AUTO_LAUNCH_GAME=yes

@rem Ollama context window in tokens
set CONTEXT=32768

@rem Open the live page (http://127.0.0.1:8765/) in the browser when the bot starts: yes or no
set OPEN_LIVE_VIEW=yes

@rem Super Fast Mode animation speed (applied when the game is launched by Spire start).
@rem 2 was the old setting; higher = more runs per hour. If the game starts hanging, lower it.
set GAME_SPEED=5
