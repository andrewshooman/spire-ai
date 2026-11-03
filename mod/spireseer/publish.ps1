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
Press [b]F7[/b] twice and Spireseer plays your fights: every card, target, potion and choice screen, then ends the turn. Prefer to play yourself? Suggest mode shows the same plan on your cards.

[h2]Controls[/h2]
[list]
[*][b]F7[/b] cycles Off / Suggest / Auto. [b]F8[/b] pauses Auto. [b]F9[/b] shows one card at a time in Suggest.
[*]Play speed: Mods > Spireseer > Config.
[*]Rewards, the map, shops and events stay yours.
[/list]

[h2]What Auto does[/h2]
[list]
[*][b]Cards and targets[/b] in the best order it can find.
[*][b]Choice screens:[/b] Discovery and card potions (it tries each offered card and takes the best), Armaments, exhaust / discard / retain picks, Scry, Seek, Headbutt, Exhume, Hologram, Wish and more.
[*][b]Potions:[/b] Block or heal on a boss's big hit, burst saved for when it matters, never dies holding one.
[*][b]Ends the turn[/b] when nothing left is worth playing.
[/list]
In Suggest the cards to play are numbered [b]1, 2, 3[/b] with a reason each ([i]Bash > Jaw Worm - Vulnerable first: the attacks after it hit 50% harder[/i]); choice screens get a [b]PICK[/b] marker, potions a [b]USE[/b] marker.

[h2]The brain[/h2]
The combat planner of [b]Spire AI[/b], a self-learning bot with 900+ full runs on all four characters, ported to Java. On 2,000 real combat states from those runs the mod makes the same play as the bot every time.
[list]
[*]Searches up to 20,000 card orders a turn (under a millisecond) and simulates damage, block, Vulnerable/Weak, Strength, kills and incoming hits.
[*]Damage is valued by the enemy hits it prevents, so it doesn't turtle while enemies scale, and it blocks only what is coming.
[*]Powers (Demon Form, Echo Form, Wraith Form...) come out when the fight is long enough to pay them back.
[*]Knows Nob's Enrage, Thorns, Malleable, Curl Up, Artifact, splitting slimes, Time Eater, the Heart's damage cap, The Champ's Execute and more.
[*]Uses the game's own damage numbers, so relics and modded cards count.
[*]Character play: Ironclad Strength and exhaust, Silent poison and Shivs, Defect orbs and Focus, Watcher stances (no Wrath into a big hit). Each character has its own learned weights.
[/list]

[h2]Plays fair[/h2]
Never starts in Auto, off in Daily Climbs, never acts while you drag a card. The mod doesn't learn while you play. Requires BaseMod.

[i]Search terms: auto battle, autoplay, auto battler, auto combat, AFK, bot, AI, combat helper, best move, hints, coach, solver.[/i]
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
