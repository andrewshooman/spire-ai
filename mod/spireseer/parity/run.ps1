# Parity of the Spireseer Java brain with the bot's Python planner (agent/planner.py + agent/cards.py).
# 1. dump_fixtures.py writes fixtures from the bot's protocol logs (real combat states, all four classes) and the
#    card database; the game's own English card texts come from desktop-1.0.jar (kept local, never committed).
# 2. ParityTest: every card text parsed the same, the game's wording read the same, and the same plan on every state.
param([int]$PerClass = 500)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path (Split-Path (Split-Path $here))
$jdk = Get-ChildItem "C:\Program Files\Java", "C:\Program Files\Eclipse Adoptium", "C:\Program Files\Microsoft" -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path "$($_.FullName)\bin\javac.exe" } | Select-Object -First 1
$steam = ((Get-ItemProperty "HKCU:\Software\Valve\Steam").SteamPath) -replace '/', '\'
$game = "$steam\steamapps\common\SlayTheSpire\desktop-1.0.jar"
$env:PYTHONUTF8 = "1"
& "$repo\sts-bench\.venv\Scripts\python.exe" "$here\dump_fixtures.py" $PerClass
if ($LASTEXITCODE) { throw "fixtures failed" }
& "$repo\sts-bench\.venv\Scripts\python.exe" -c @"
import json, zipfile
z = zipfile.ZipFile(r'$game')
json.dump(json.loads(z.read('localization/eng/cards.json').decode('utf-8')), open(r'$here\fixtures\game_cards_eng.json', 'w', encoding='utf-8'))
"@
$out = "$here\..\build\parity"
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null
$src = @(Get-ChildItem "$here\..\src\spireseer\brain\*.java" | ForEach-Object { $_.FullName }) + "$here\ParityTest.java", "$here\StatesParity.java"
& "$($jdk.FullName)\bin\javac.exe" -proc:none -encoding UTF-8 -cp $game -d $out @src
if ($LASTEXITCODE) { throw "compile failed" }
& "$($jdk.FullName)\bin\java.exe" -cp "$out;$game" spireseer.ParityTest "$here\fixtures"
exit $LASTEXITCODE
