param([int]$DelaySeconds = 0)
# Opens modded Slay the Spire directly through ModTheSpire with the last-used
# mod list: no Steam launch dialog, no mod picker.
Start-Sleep -Seconds $DelaySeconds
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
$java = "$game\jre\bin\javaw.exe"
if (-not (Test-Path $java)) { $java = "$game\jre\bin\java.exe" }
Start-Process $java -ArgumentList @("-jar", "`"$($mts.FullName)`"", "--skip-launcher") -WorkingDirectory $game
