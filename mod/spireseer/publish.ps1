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
$cfg.title = "Spireseer - see the best turn, or let it play"
$cfg.description = @"
[h1]See the best turn. Or let it play.[/h1]
Spireseer reads your hand, your energy and every enemy's intent, works out the strongest way to play the turn, and shows it to you right on the cards. Learn from it, check your own plan against it, or hand it the controller.

[h2]Two modes[/h2]
[list]
[*][b]Suggest[/b] (default): the cards to play are numbered [b]1, 2, 3[/b] in order, the next one glows green, the target gets the targeting reticle, and one line sums up the turn: [i]"Bash > Jaw Worm, Strike > Jaw Worm (deal 20, block 0, take 5)"[/i]. A potion worth drinking is marked [b]USE[/b] with the reason; [b]END TURN[/b] is marked when nothing is worth playing. You still play every card.
[*][b]Auto[/b]: plays the turn for you at a watchable pace, drinks potions by the same rules and ends the turn. Each part can be switched off in Mods > Spireseer > Config.
[/list]
[b]F7[/b] cycles Off / Suggest / Auto. [b]F8[/b] pauses Auto instantly.

[h2]The AI behind the decisions[/h2]
Spireseer's brain comes from [b]Spire AI[/b], a self-learning bot that has played hundreds of full Ironclad runs on its own, learning from every win and death.
[list]
[*][b]Whole-turn search.[/b] It doesn't pick one card at a time. It tries the possible orders of your playable cards within your energy (up to 4,000 sequences a turn) and simulates each one: damage, block, kills, Vulnerable and Weak landing before the big hits, Strength gained mid-turn, Thorns, and what the enemies will hit you for.
[*][b]The game's own numbers.[/b] Damage and block come straight from the game's calculation for every card against every target, with your relics, powers and debuffs already applied. That is why modded cards work too.
[*][b]Learned judgment.[/b] Each finished turn is scored: damage (weighted by who to kill first: Gremlin Wizard before Gremlin Fat, the Automaton's body before its Orbs), kills, damage blocked, debuffs, card draw, powers and Strength (worth more in long fights), HP you'd lose, and a hard veto on any turn that lets the enemies kill you. The weights of those trade-offs weren't hand-picked: the bot tuned them through evolution-style trials across its runs, keeping the changes that climbed higher.
[*][b]Lessons from its own deaths.[/b] Rules the bot learned the hard way are built in: finish a Thorns enemy instead of stalling forever, race The Champ once he drops below half but don't push him there when two Executes would kill you, save burst potions for that second half, keep one potion back for a lethal turn.
[*][b]It re-thinks after every card.[/b] Draws and random effects change the turn, so the game is read again after each play.
[/list]

[h2]Plays fair[/h2]
[list]
[*]Every launch starts in Off or Suggest, never Auto.
[*]Auto is off in Daily Climbs.
[*]It never acts while you drag or target a card.
[*]Card choices (Armaments, Headbutt), rewards, map, shops and events stay yours.
[/list]

[h2]Characters[/h2]
Tuned on the Ironclad. The search plays every character's cards using the game's own damage and block numbers, but it doesn't yet model the Silent's poison ticking, the Defect's orbs and Focus, or the Watcher's stances, so its advice for those is rougher. Support for them is next.

Requires BaseMod. Works alongside other mods.
"@
$cfg.visibility = $Visibility
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
