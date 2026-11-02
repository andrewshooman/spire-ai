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
[h1]An auto battler for Slay the Spire. Or a coach, if you'd rather play.[/h1]
Press [b]F7[/b] twice and Spireseer plays your fights: every card, every target, every potion, every choice screen, then ends the turn. It reads your hand, your energy and every enemy's intent and plays the strongest turn it can find. Prefer to play yourself? Suggest mode shows the same plan right on your cards.

[h2]Auto: it plays the whole fight[/h2]
[list]
[*][b]Cards and targets[/b], in the best order, at a watchable or fast pace (Mods > Spireseer > Config).
[*][b]Choice screens inside a fight[/b], decided by the same brain: Discovery and the Attack / Skill / Power / Colorless potions (it tries each offered card in your turn and takes the best), Wish and Stance Potion, Armaments (the upgrade that helps most), True Grit / Burning Pact / Purity / Elixir (statuses and curses first, then what the turn needs least), discards (Survivor, Acrobatics, Prepared, Gambler's Brew), Dual Wield, Nightmare, Well-Laid Plans, Warcry, Setup, Forethought, Recycle, Scry, Seek, Headbutt, Exhume, Hologram, Liquid Memories, Secret Weapon / Technique, Meditate, Omniscience.
[*][b]Potions[/b] by the rules its bot learned: never die holding one, Block or heal on a boss's big hit, burst potions saved for The Champ's second half, one opener in act 1 elites, the last potion kept for a lethal turn.
[*][b]Ends the turn[/b] when nothing left is worth playing, and says why.
[/list]
[b]F7[/b] cycles Off / Suggest / Auto. [b]F8[/b] pauses Auto instantly. Rewards, the map, shops and events stay yours.

[h2]Suggest: the same plan, shown on your cards[/h2]
The cards to play are numbered [b]1, 2, 3[/b], the next one glows green and its target gets the reticle. Every card says why:
[i]1. Bash > Jaw Worm - deals 8 to Jaw Worm, Vulnerable first: the attacks after it hit 50% harder
2. Strike > Jaw Worm - deals 9 to Jaw Worm
3. Defend - blocks 5 of the 12 coming[/i]
Choice screens get a [b]PICK[/b] marker with the reason, potions a [b]USE[/b] marker, and [b]F9[/b] switches to one card at a time.

[h2]The brain[/h2]
Spireseer is the combat planner of [b]Spire AI[/b], a self-learning bot that has played well over 900 full runs across all four characters, ported to Java and checked against the bot card for card: on 2,000 real combat states from its runs the mod picks the same play every time.
[list]
[*][b]Whole-turn search.[/b] It tries the orders of your playable cards within your energy (up to 20,000 sequences a turn, under a millisecond) and simulates each: damage through block, Vulnerable and Weak landing before the big hits, Strength gained mid-turn, kills, and what the enemies will hit you for.
[*][b]Damage that counts.[/b] Damage isn't just HP off an enemy: every point brings its death forward and with it the hits it would still throw, so it won't turtle behind Defends while three Cultists grow stronger. It blocks what is coming, not more.
[*][b]Long fights reward setup.[/b] Powers are worth what they pay each turn for the turns the fight has left: Demon Form, Deva Form, Echo Form, A Thousand Cuts, Wraith Form or Rushdown come out on a boss's quiet turn, not in a hallway fight that ends next turn, and never when the hit would leave you nearly dead.
[*][b]Enemy mechanics.[/b] Gremlin Nob's Enrage (no Skills unless they're worth it), Thorns and Sharp Hide, Malleable, Curl Up, Artifact, flying Byrds, Transient's Shifting, Time Eater's 12 cards, the Heart's Beat of Death and damage cap, Lagavulin asleep, slimes that split at half (cross half deep or not at all), The Champ's second half, and who to kill first.
[*][b]The game's own numbers.[/b] Card text gives the effects; the game's computed damage and block per target tell it what your relics add (Pen Nib, Wrist Blade, Strike Dummy, Vigor...), so modded cards and relics work too.
[*][b]It re-thinks after every card[/b], since draws and random effects change the turn.
[/list]

[h2]Characters[/h2]
Each character gets its own learned weights (the mod picks the set for whoever you play):
[list]
[*][b]Ironclad[/b]: Strength, Limit Break, Heavy Blade, Body Slam, Fiend Fire, Corruption, Barricade, Feel No Pain, Juggernaut.
[*][b]Silent[/b]: poison on enemies and what it kills before they attack, Catalyst, Bouncing Flask, Shivs and Accuracy, Wraith Form, Footwork's Dexterity, Bullet Time, Piercing Wail, Malaise's X.
[*][b]Defect[/b]: channeling (a full row evokes the oldest), Dualcast, Multi-Cast, Recursion, Lightning / Frost / Dark / Plasma passives, Focus and orb slots, Echo Form, Electrodynamics.
[*][b]Watcher[/b]: Wrath doubles your attacks and the hits you take, so it looks at next turn before entering it (no Eruption into Hexaghost's Divider); Calm banks energy, Divinity triples, Mantra builds toward it; Pressure Points' Mark, Judgment, Wallop, Vault's extra turn; never Blasphemy unless that turn wins.
[/list]

[h2]How the bot learned[/h2]
Spire AI plays complete runs through CommunicationMod at sped-up game speed. Its combat weights (what a point of block, damage, draw, energy or Strength is worth) were tuned by evolution-style trials: one run with the weights nudged, a replay of the same seed with the opposite nudge, the better one pulls the weights its way. Rules came from studying where its runs died: endless Thorns stalls, The Champ's Executes, potions wasted early, Wrath before a big hit, slimes split at the wrong time. Each release carries the bot's latest weights; the mod doesn't learn while you play.

[h2]Plays fair[/h2]
[list]
[*]Every launch starts in Off or Suggest, never Auto.
[*]Auto is off in Daily Climbs.
[*]It never acts while you drag or target a card.
[/list]

Requires BaseMod. Works alongside other mods.

[i]Search terms: auto battle, auto play, autoplay, auto battler, autobattler, auto combat, auto fight, AFK, idle, bot, AI, automation, combat helper, turn advisor, best move, suggestions, hints, coach, solver, assistant.[/i]
"@
$cfg.visibility = $Visibility
$cfg | Add-Member -Force -NotePropertyName tags -NotePropertyValue @("Utility", "Qol")
if ($ChangeNote) { $cfg.changeNote = $ChangeNote }
elseif (-not $cfg.changeNote) { $cfg.changeNote = "Auto battler update." }
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
