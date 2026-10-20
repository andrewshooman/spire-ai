param([int]$DelaySeconds = 0, [double]$Speed = 0)
# Opens modded Slay the Spire directly through ModTheSpire with the last-used
# mod list: no Steam launch dialog, no mod picker. -Speed (or GAME_SPEED from
# settings.bat) sets Super Fast Mode's animation multiplier before the game starts
# (the mod only reads it at launch).
Start-Sleep -Seconds $DelaySeconds
if ($Speed -le 0 -and $env:GAME_SPEED) { $Speed = [double]$env:GAME_SPEED }
if ($Speed -gt 0) {
    $sfm = "$env:LOCALAPPDATA\ModTheSpire\SuperFastMode\SuperFastModeConfig.properties"
    New-Item -ItemType Directory -Force (Split-Path $sfm) | Out-Null
    $keep = @()
    if (Test-Path $sfm) { $keep = @(Get-Content $sfm | Where-Object { $_ -notmatch '^(deltaMultiplier|isDeltaMultiplied|isInstantLerp)=' }) }
    $keep += "isDeltaMultiplied=true", "deltaMultiplier=$Speed", "isInstantLerp=true"
    Set-Content -Path $sfm -Value $keep -Encoding ASCII
}
function Fail($msg) { Write-Host $msg -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
$steam = (Get-ItemProperty "HKCU:\Software\Valve\Steam" -ErrorAction SilentlyContinue).SteamPath
if (-not $steam) { Fail "Steam not found." }
$steam = $steam -replace '/', '\'
if (-not (Get-Process steam -ErrorAction SilentlyContinue)) { Start-Process "$steam\steam.exe"; Start-Sleep -Seconds 20 }
$libs = @($steam)
$vdf = "$steam\steamapps\libraryfolders.vdf"
if (Test-Path $vdf) {
    $libs += Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' }
}
$lib = $libs | Where-Object { Test-Path "$_\steamapps\common\SlayTheSpire\desktop-1.0.jar" } | Select-Object -First 1
if (-not $lib) { Fail "Couldn't find Slay the Spire in your Steam libraries." }
$game = "$lib\steamapps\common\SlayTheSpire"
$mts = Get-ChildItem "$lib\steamapps\workshop\content\646570" -Recurse -Filter ModTheSpire.jar -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $mts) { Fail "Couldn't find ModTheSpire. Subscribe to it in the Steam Workshop." }
# The bot always plays on its own save slot (BOT_SLOT, default 0 = the first profile), so your own
# profile's stats and run history stay separate. The game opens whichever slot DEFAULT_SLOT names.
$slot = if ($env:BOT_SLOT) { $env:BOT_SLOT } else { "0" }
$slots = "$game\preferences\STSSaveSlots"
if (Test-Path $slots) {
    $txt = [IO.File]::ReadAllText($slots)
    $new = $txt -replace '"DEFAULT_SLOT":\s*"-?\d+"', "`"DEFAULT_SLOT`": `"$slot`""
    if ($new -ne $txt) {
        [IO.File]::WriteAllText($slots, $new)
        [IO.File]::WriteAllText("$slots.backUp", $new)
        Write-Host "Save slot set to $slot for the bot (switch back to your profile from the main menu when you play)."
    }
}
$java = "$game\jre\bin\javaw.exe"
if (-not (Test-Path $java)) { $java = "$game\jre\bin\java.exe" }
Start-Process $java -ArgumentList @("-jar", "`"$($mts.FullName)`"", "--skip-launcher") -WorkingDirectory $game
