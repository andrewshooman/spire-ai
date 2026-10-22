param([switch]$Upload, [string]$Visibility = "private", [string]$ChangeNote = "")
# Prepares the Steam Workshop upload for Spireseer: builds the jar, fills workshop\config.json and
# workshop\content. Nothing is uploaded unless you pass -Upload (Steam must be running and logged in).
#   publish.ps1                     -> build + prepare only
#   publish.ps1 -Upload             -> upload as a PRIVATE item (only you see it; test it from the Workshop)
#   publish.ps1 -Upload -Visibility public -ChangeNote "Potion advice"
# The first upload creates the Workshop item and stores its id in config.json; later uploads update it.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$here\build.ps1"
if ($LASTEXITCODE) { throw "build failed" }

$steam = ((Get-ItemProperty "HKCU:\Software\Valve\Steam").SteamPath) -replace '/', '\'
$libs = @($steam)
$vdf = "$steam\steamapps\libraryfolders.vdf"
if (Test-Path $vdf) { $libs += Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' } }
$lib = $libs | Where-Object { Test-Path "$_\steamapps\common\SlayTheSpire\mod-uploader.jar" } | Select-Object -First 1
if (-not $lib) { throw "Slay the Spire (with mod-uploader.jar) not found." }
$game = "$lib\steamapps\common\SlayTheSpire"

$ws = "$here\workshop"
if (-not (Test-Path "$ws\config.json")) {
    Push-Location $game
    & "$game\jre\bin\java.exe" -jar mod-uploader.jar new -w $ws
    Pop-Location
}
New-Item -ItemType Directory -Force "$ws\content" | Out-Null
Get-ChildItem "$ws\content" | Remove-Item -Force
Copy-Item "$here\build\Spireseer.jar" "$ws\content\Spireseer.jar"

$cfg = Get-Content "$ws\config.json" -Raw | ConvertFrom-Json
$cfg.title = "Spireseer"
$cfg.description = @"
[h1]See the best turn. Or let it play.[/h1]
Spireseer reads your hand, your energy and every enemy's intent, works out the strongest way to play the turn, and shows it to you right on the cards. Learn from it, check your own plan against it, or hand it the controller.

[h2]Two modes[/h2]
[list]
[*][b]Suggest[/b] (default): the cards to play are numbered [b]1, 2, 3[/b] in order, the next one glows green and its target gets the targeting reticle. Every card comes with its reason:
[i]1. Bash > Jaw Worm - deals 8 to Jaw Worm, Vulnerable first: the attacks after it hit 50% harder
2. Strike > Jaw Worm - deals 9 to Jaw Worm
3. Defend - blocks 5 of the 12 coming[/i]
Prefer one step at a time? [b]F9[/b] switches to [b]next card only[/b] ("Next: Bash > Jaw Worm - ..."), re-planned after every card. A potion worth drinking is marked [b]USE[/b] with the reason; [b]END TURN[/b] is marked with why ("out of energy", "what's left would cost more than it gains"). You still play every card.
[*][b]Auto[/b]: plays the turn for you at a watchable pace, drinks potions by the same rules and ends the turn. Each part can be switched off in Mods > Spireseer > Config.
[/list]
[b]F7[/b] cycles Off / Suggest / Auto. [b]F8[/b] pauses Auto instantly. [b]F9[/b] switches whole turn / next card only.

[h2]The AI behind the decisions[/h2]
Spireseer's brain comes from [b]Spire AI[/b], a self-learning bot that has played hundreds of full Ironclad runs on its own, learning from every win and death.
[list]
[*][b]Whole-turn search.[/b] It doesn't pick one card at a time. It tries the possible orders of your playable cards within your energy (up to 4,000 sequences a turn) and simulates each one: damage, block, kills, Vulnerable and Weak landing before the big hits, Strength gained mid-turn, Thorns, and what the enemies will hit you for.
[*][b]The game's own numbers.[/b] Damage and block come straight from the game's calculation for every card against every target, with your relics, powers and debuffs already applied. That is why modded cards work too.
[*][b]Learned judgment.[/b] Each finished turn is scored: damage (weighted by who to kill first: Gremlin Wizard before Gremlin Fat, the Automaton's body before its Orbs), kills, damage blocked, debuffs, card draw, powers and Strength (worth more in long fights), HP you'd lose, and a hard veto on any turn that lets the enemies kill you. The weights of those trade-offs weren't hand-picked: the bot tuned them through evolution-style trials across its runs, keeping the changes that climbed higher.
[*][b]Lessons from its own deaths.[/b] Rules the bot learned the hard way are built in: finish a Thorns enemy instead of stalling forever, race The Champ once he drops below half but don't push him there when two Executes would kill you, save burst potions for that second half, keep one potion back for a lethal turn.
[*][b]It re-thinks after every card.[/b] Draws and random effects change the turn, so the game is read again after each play.
[/list]

[h2]How it was trained[/h2]
[list]
[*][b]Real runs, played by the bot.[/b] Spire AI plays complete runs of the actual game through CommunicationMod, at sped-up game speed, every fight start to finish. Runs come in cycles of ten: eight training runs that explore, two benchmark runs on fixed seeds to measure progress. Each run gets one score: deeper is worth disproportionately more, elites add a little, a win is best.
[*][b]Evolution-style tuning of the combat weights.[/b] A training run plays with the weights nudged in a random direction (say block +8%, damage -5%). The next run replays [i]the same seed[/i] with the opposite nudge, and whichever went deeper pulls the weights its way. Same seed, mirrored nudges: most of the luck cancels out. Over hundreds of runs the weights drifted from the starting guesses to what you get here.
[*][b]What it learned.[/b] Protect HP more, chase raw damage less: the value of each point of block rose about 50%, of each HP a card costs you more than doubled, and raw damage fell by about a third. That matches how its runs actually died: worn down across fights, rarely unable to finish one.
[*][b]Rules from studying its deaths.[/b] Some lessons were found by analysing where runs ended and written in as rules: a run that ended its turn 60 times in a row against a Thorns enemy, the Champ's second half (the top killer), potions wasted on turn one, the Automaton's Orbs soaking up damage, and potions spent on act 2 elites that were needed for the boss (elite openers after act 1 only when you're hurt or your belt is full).
[*][b]Packed into the mod.[/b] The bot's turn planner was ported to Java with the same scoring, so every learned weight means exactly what it did in training. In the game the mod reads each card's real computed damage instead of estimating it. The mod doesn't learn while you play: each release carries the bot's latest weights.
[/list]

[h2]Plays fair[/h2]
[list]
[*]Every launch starts in Off or Suggest, never Auto.
[*]Auto is off in Daily Climbs.
[*]It never acts while you drag or target a card.
[*]Card choices (Armaments, Headbutt), rewards, map, shops and events stay yours.
[/list]

[h2]Characters[/h2]
Each character gets its own learned weights, and the mod picks the set for whoever you play (shown at the top left). The Ironclad set comes from 300+ runs, the Silent set from its own training (growing with every release). The Silent's tools are modeled: poison already on enemies and the poison you add, Catalyst, Bouncing Flask, enemies their poison will kill before they attack, Shivs, Piercing Wail on every attacker, Malaise's X. The Defect's orbs are modeled too: channeling (a full row evokes the oldest), evoking (Dualcast, Multi-Cast, Recursion), Lightning, Frost, Dark and Plasma passives at the end of your turn, Focus and orb slots. So are cards that work differently from plain damage and block: Wraith Form's Intangible, Footwork's Dexterity (worth block for the rest of the fight), Double Tap and Burst, Reaper's healing, and "next turn" cards (Outmaneuver, Dodge and Roll) count for next turn, not this one. The Watcher's stances are modeled as well: Wrath doubles your attacks (and the hits you take), Calm banks 2 energy for when you leave it, Divinity triples attacks, Mantra builds toward it, and Inner Peace, Indignation, Fear No Evil and Halt work by your current stance. It knows when to step out of Wrath before a big hit, and it never plays Blasphemy unless that turn wins the fight. All four characters are covered now; every suggested card says why, including the orbs it channels and the stance it enters.

Requires BaseMod. Works alongside other mods.

[i]Search terms: auto battle, auto play, autoplay, auto chess, autobattler, auto battler, auto combat, AFK, idle, bot, AI, automation, combat helper, turn advisor, best move, suggestions, hints, coach, solver, assistant.[/i]
"@
$cfg.visibility = $Visibility
$cfg | Add-Member -Force -NotePropertyName tags -NotePropertyValue @("Utility", "Qol")
if ($ChangeNote) { $cfg.changeNote = $ChangeNote }
elseif (-not $cfg.changeNote) { $cfg.changeNote = "First release: combat suggestions, auto-battle, potion advice." }
[IO.File]::WriteAllText("$ws\config.json", ($cfg | ConvertTo-Json -Depth 5))   # no BOM: the uploader reads plain JSON
Write-Host "Workshop folder ready: $ws (visibility: $Visibility)"
Write-Host "Preview image: $ws\image.jpg (redraw: python mod\spireseer\art\make_preview.py)"

if ($Upload) {
    Push-Location $game
    & "$game\jre\bin\java.exe" -jar mod-uploader.jar upload -w $ws
    Pop-Location
} else {
    Write-Host "Not uploaded. Run again with -Upload when you're ready."
}
