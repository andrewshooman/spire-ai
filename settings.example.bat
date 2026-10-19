@rem ===== Spire AI settings =====
@rem Edit, save, then close and reopen Spire start.

@rem Character to train: ironclad, silent, defect or watcher.
@rem Each character keeps its own model and progress in evolve\<character>.
set CHARACTER=ironclad

@rem LLM (Ollama) for big-picture work only: events, boss relics, coaching
set MODEL=qwen3:14b

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
