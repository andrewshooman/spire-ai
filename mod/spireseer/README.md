# Spireseer (Slay the Spire mod)

Combat suggestions or auto-battle, in the game, with no Python or LLM needed.

| Key | What it does |
|---|---|
| F7 | Cycle **Off -> Suggest -> Auto** |
| F8 | Pause / resume Auto |
| F9 | Suggest the whole turn (1, 2, 3 with a reason for each card) or only the next card |

- **Suggest** shows the best play for the turn: the cards are numbered 1, 2, 3 in the order to play them (the
  first one glows green), the target gets the game's targeting reticle, and a line explains the turn
  ("Suggested: Bash > Jaw Worm, Strike > Jaw Worm (deal 20, block 0, take 5)"). A potion worth drinking is
  marked USE with the reason; END TURN is marked when nothing is worth playing. You still play every card.
- **Auto** plays the turn for you, one card at a time at a watchable pace, drinks potions by the same rules and
  ends the turn (each switchable in Mods -> Spireseer -> Config).
- Safety: every launch starts in Off or Suggest (never Auto), Auto is off in Daily Climbs, it never acts while
  you drag or target a card, and choice screens (Armaments, Headbutt, rewards, map, shops) are left to you.

The brain is a Java port of Spire AI's whole-turn planner (agent/planner.py): it reads the game's own computed
damage and block for every card and target, simulates Vulnerable/Weak/Strength, Thorns, kills and the enemies'
intents, and starts from the combat weights the Spire AI bot learned over ~240 runs. Card text is parsed for
draw/energy/debuffs, so cards from other mods work when they use standard wording.

## Build and install

```
powershell -ExecutionPolicy Bypass -File mod\spireseer\build.ps1 -Install
```

Needs a JDK 11+ (compiles for the game's Java 8) and ModTheSpire + BaseMod from the Workshop. Then open the
ModTheSpire launcher once and tick **Spireseer** (the bot's `launch-game.ps1` reuses the last mod list).

`build.ps1` runs the brain tests first (`test/spireseer/PlannerTest.java`, no game needed).

## How a play is chosen

1. Read the turn: every card in hand with the game's own computed damage against each enemy and its block
   (Strength, Weak, Vulnerable, relics already applied), energy, each enemy's HP, block, intent and Thorns.
   Card text adds draw, energy, Vulnerable/Weak, Strength and HP costs.
2. Search: try the orders of playable cards within the energy available (up to 4000 sequences), simulating
   damage, block, kills, newly applied Vulnerable/Weak and Strength gained along the way.
3. Score where each sequence leaves you: damage dealt (weighted by kill priority, e.g. Gremlin Wizard first,
   Bronze Orbs last), kills, block against the damage coming in, debuffs, cards drawn, powers and Strength
   (worth more in long fights), HP lost to card costs or Thorns, -500 if the enemies' attack would kill you,
   a bonus for ending the fight, and Champ rules (race it once it drops below half; don't push it below half
   when two Executes would kill you).
4. Keep the best sequence, play its first card, then read the game again (draws and random effects change
   things). End the turn when the best sequence beats ending now by less than a small threshold.

Potions follow Spire AI's rules: never die holding one, Block/heal on a boss's big hits, burst potions for The
Champ's second half, power potions early in boss fights (keeping one back), one opener in elites, defensive
ones when HP is low.

## Publish to the Steam Workshop

```
powershell -ExecutionPolicy Bypass -File mod\spireseer\publish.ps1            # build + prepare only
powershell -ExecutionPolicy Bypass -File mod\spireseer\publish.ps1 -Upload    # upload as a private item
powershell -ExecutionPolicy Bypass -File mod\spireseer\publish.ps1 -Upload -Visibility public -ChangeNote "..."
```

Steam must be running. The first upload creates the Workshop item (its id is saved in workshop\config.json);
later uploads update it. Replace workshop\image.jpg with a screenshot first. Upload private, subscribe to it
from the Workshop page to test, then publish with `-Visibility public`.

## Not yet

Card rewards, map, shops, rest sites and events (out-of-combat automation), and hand-select screens.
