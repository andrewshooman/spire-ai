# AutoSpire (Slay the Spire mod)

Combat suggestions or auto-battle, in the game, with no Python or LLM needed.

| Key | What it does |
|---|---|
| F7 | Cycle **Off -> Suggest -> Auto** |
| F8 | Pause / resume Auto |

- **Suggest** shows the best play for the turn ("Suggested: Bash > Jaw Worm, Strike > Jaw Worm (deal 20, block 0,
  take 5)") and marks the card (PLAY) and its target (TARGET). You still play every card yourself.
- **Auto** plays the turn for you, one card at a time at a watchable pace, and ends the turn
  (both adjustable in Mods -> AutoSpire -> Config).
- Safety: every launch starts in Off or Suggest (never Auto), Auto is off in Daily Climbs, it never acts while
  you drag or target a card, and choice screens (Armaments, Headbutt, rewards, map, shops) are left to you.

The brain is a Java port of Spire AI's whole-turn planner (agent/planner.py): it reads the game's own computed
damage and block for every card and target, simulates Vulnerable/Weak/Strength, Thorns, kills and the enemies'
intents, and starts from the combat weights the Spire AI bot learned over ~240 runs. Card text is parsed for
draw/energy/debuffs, so cards from other mods work when they use standard wording.

## Build and install

```
powershell -ExecutionPolicy Bypass -File mod\autospire\build.ps1 -Install
```

Needs a JDK 11+ (compiles for the game's Java 8) and ModTheSpire + BaseMod from the Workshop. Then open the
ModTheSpire launcher once and tick **AutoSpire** (the bot's `launch-game.ps1` reuses the last mod list).

`build.ps1` runs the brain tests first (`test/autospire/PlannerTest.java`, no game needed).

## Not yet

Potions, card rewards, map, shops, rest sites and events (out-of-combat automation), and hand-select
screens. Publishing to the Steam Workshop uses the game's `mod-uploader.jar` with your Steam account.
