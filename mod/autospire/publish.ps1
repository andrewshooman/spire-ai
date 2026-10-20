param([switch]$Upload, [string]$Visibility = "private", [string]$ChangeNote = "")
# Prepares the Steam Workshop upload for AutoSpire: builds the jar, fills workshop\config.json and
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
Copy-Item "$here\build\AutoSpire.jar" "$ws\content\AutoSpire.jar"

$cfg = Get-Content "$ws\config.json" -Raw | ConvertFrom-Json
$cfg.title = "AutoSpire - combat suggestions and auto-battle"
$cfg.description = @"
Shows the best play for your turn, or plays it for you.

[b]F7[/b] cycles Off / Suggest / Auto, [b]F8[/b] pauses Auto.

[list]
[*][b]Suggest[/b]: the cards to play are numbered in order (the first one glows green), the target gets the targeting reticle, and a line explains the turn: damage dealt, block, damage you'll take. Potions it would drink are marked USE with the reason; END TURN is marked when nothing is worth playing.
[*][b]Auto[/b]: plays the turn at a watchable pace, drinks potions by the same rules, ends the turn (each switchable in Mods > AutoSpire > Config).
[*]Safe by default: every launch starts in Off or Suggest, Auto is off in Daily Climbs, it never acts while you drag or target a card, and choice screens (Armaments, Headbutt, rewards, map, shops) stay yours.
[/list]

How it decides: a whole-turn search tries the orders of the cards in your hand within your energy, using the game's own computed damage and block for every card and target. It simulates Vulnerable, Weak, Strength, Thorns, kills and the enemies' intents, scores where the turn leaves you, and keeps the best order. Its weights were learned by a self-learning bot over hundreds of runs. Potion rules: never die holding one, Block/heal on big boss hits, burst potions saved for The Champ's second half.

Works with modded cards that use standard card wording. Requires BaseMod.
"@
$cfg.visibility = $Visibility
if ($ChangeNote) { $cfg.changeNote = $ChangeNote }
elseif (-not $cfg.changeNote) { $cfg.changeNote = "First release: combat suggestions, auto-battle, potion advice." }
[IO.File]::WriteAllText("$ws\config.json", ($cfg | ConvertTo-Json -Depth 5))   # no BOM: the uploader reads plain JSON
Write-Host "Workshop folder ready: $ws (visibility: $Visibility)"
Write-Host "Replace $ws\image.jpg with a screenshot of Suggest mode for the Workshop page."

if ($Upload) {
    Push-Location $game
    & "$game\jre\bin\java.exe" -jar mod-uploader.jar upload -w $ws
    Pop-Location
} else {
    Write-Host "Not uploaded. Run again with -Upload when you're ready."
}
