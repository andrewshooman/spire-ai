# Spire AI installer: sets up everything the bot needs on this PC.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Dest C:\sts-evolve] [-NoShortcuts] [-NoModel]
#
# Works from a clone anywhere (copies the repo to -Dest) or from a clone made directly
# in -Dest (nothing to copy). Safe to rerun: every step skips what is already done.
param(
    [string]$Dest = "C:\sts-evolve",
    [switch]$NoShortcuts,
    [switch]$NoModel
)
$ErrorActionPreference = "Stop"
$BenchRepo = "https://github.com/alavico/sts-bench"
$BenchCommit = "735b90625f63449a275c285c39d02e36a2942bdb"
$Model = "qwen3:14b"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "   $msg" -ForegroundColor Red; exit 1 }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

$Source = (Resolve-Path $PSScriptRoot).Path.TrimEnd('\')
$Dest = [IO.Path]::GetFullPath($Dest).TrimEnd('\')

# ------------------------------------------------------------------ 1. code
Step "Bot code -> $Dest"
if ($Source -ieq $Dest) {
    Ok "running from $Dest itself: nothing to copy"
} else {
    New-Item -ItemType Directory -Force $Dest | Out-Null
    # mirror the code only; never touch training data (evolve\), the pinned dependency or settings
    robocopy $Source $Dest /E /XD .git sts-bench evolve __pycache__ /XF settings.bat *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "copy failed (robocopy exit $LASTEXITCODE)" }
    Ok "copied from $Source"
}
Set-Location $Dest
if (-not (Test-Path settings.bat)) { Copy-Item settings.example.bat settings.bat; Ok "created settings.bat" }

# ------------------------------------------------------------------ 2. sts-bench
Step "sts-bench (pinned $($BenchCommit.Substring(0,8)))"
if (-not (Have git)) { Fail "git is required: winget install Git.Git" }
if (-not (Test-Path "sts-bench\.git")) {
    git clone --quiet $BenchRepo sts-bench
    if ($LASTEXITCODE) { Fail "git clone failed" }
}
$head = (git -C sts-bench rev-parse HEAD).Trim()
if ($head -ne $BenchCommit) {
    git -C sts-bench fetch --quiet origin
    git -C sts-bench checkout --quiet $BenchCommit
    if ($LASTEXITCODE) { Fail "could not check out $BenchCommit" }
}
Ok "sts-bench at $BenchCommit"

# ------------------------------------------------------------------ 3. python venv
Step "Python environment (sts-bench\.venv, Python 3.11+)"
$py = "sts-bench\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    if (Have uv) {
        uv venv --quiet --python 3.12 sts-bench\.venv
    } elseif (Have py) {
        $ver = (& py -3.12 --version 2>$null); if (-not $ver) { $ver = (& py -3.11 --version 2>$null) }
        if (-not $ver) { Fail "Python 3.11+ not found: winget install Python.Python.3.12" }
        $tag = if ($ver -match '3\.12') { '-3.12' } else { '-3.11' }
        & py $tag -m venv sts-bench\.venv
    } else { Fail "Python 3.11+ not found: winget install Python.Python.3.12" }
}
$env:PYTHONUTF8 = "1"
if (Have uv) { uv pip install --quiet --python $py -e sts-bench } else { & $py -m pip install --quiet -e sts-bench }
if ($LASTEXITCODE) { Fail "installing sts-bench failed" }
Ok (& $py --version)

# ------------------------------------------------------------------ 4. game mods
Step "Slay the Spire mods"
$steam = (Get-ItemProperty "HKCU:\Software\Valve\Steam" -ErrorAction SilentlyContinue).SteamPath
$libs = @()
if ($steam) {
    $steam = $steam -replace '/', '\'
    $libs += $steam
    $vdf = "$steam\steamapps\libraryfolders.vdf"
    if (Test-Path $vdf) {
        $libs += Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' }
    }
}
$workshop = $libs | ForEach-Object { "$_\steamapps\workshop\content\646570" } | Where-Object { Test-Path $_ } | Select-Object -First 1
$need = @{ "ModTheSpire.jar" = "ModTheSpire"; "BaseMod.jar" = "BaseMod"; "CommunicationMod.jar" = "Communication Mod"; "SuperFastMode.jar" = "Super Fast Mode (optional, much faster runs)" }
foreach ($jar in $need.Keys) {
    $found = $workshop -and (Get-ChildItem $workshop -Recurse -Filter $jar -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($found) { Ok "$($need[$jar])" } else { Warn "missing $($need[$jar]): subscribe to it in the Steam Workshop" }
}

# CommunicationMod runs our relay at game start; keep any other settings in the file
$cfgDir = "$env:LOCALAPPDATA\ModTheSpire\CommunicationMod"
$cfg = "$cfgDir\config.properties"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
$esc = { param($p) ($p -replace '\\', '/') -replace ':', '\:' }
$command = "command=$(& $esc "$Dest\sts-bench\.venv\Scripts\python.exe") $(& $esc "$Dest\sts-bench\relay.py")"
$lines = @()
if (Test-Path $cfg) { $lines = @(Get-Content $cfg | Where-Object { $_ -notmatch '^(command|runAtGameStart)=' }) }
$lines += $command, "runAtGameStart=true"
Set-Content -Path $cfg -Value $lines -Encoding ASCII
Ok "CommunicationMod -> relay.py ($cfg)"

# ------------------------------------------------------------------ 5. LLM (optional)
Step "Ollama ($Model, used for events, boss relics and the coach)"
if (-not (Have ollama)) {
    Warn "Ollama not installed (winget install Ollama.Ollama). The bot still runs with LLM_EVENTS=off and COACH_EVERY=0."
} elseif ($NoModel) {
    Ok "skipped (-NoModel)"
} elseif ((ollama list 2>$null) -match [regex]::Escape($Model)) {
    Ok "$Model present"
} else {
    Warn "pulling $Model (about 9 GB)..."
    ollama pull $Model
}

# ------------------------------------------------------------------ 6. shortcuts
if (-not $NoShortcuts) {
    Step "Desktop shortcuts"
    $desk = [Environment]::GetFolderPath("Desktop")
    $sh = New-Object -ComObject WScript.Shell
    $links = [ordered]@{
        "Spire start"     = @("$Dest\start.bat", "")
        "Spire dashboard" = @("$Dest\dashboard.bat", "")
        "Spire watch"     = @("$Dest\watch.bat", "")
        "Spire stats"     = @("$Dest\stats.bat", "")
        "Spire settings"  = @("$env:WINDIR\system32\notepad.exe", "`"$Dest\settings.bat`"")
        "Spire folder"    = @("$env:WINDIR\explorer.exe", "`"$Dest`"")
    }
    foreach ($name in $links.Keys) {
        $l = $sh.CreateShortcut("$desk\$name.lnk")
        $l.TargetPath = $links[$name][0]
        $l.Arguments = $links[$name][1]
        $l.WorkingDirectory = $Dest
        $l.Save()
    }
    Ok "$($links.Count) shortcuts on $desk"
}

# ------------------------------------------------------------------ 7. self-test
Step "Self-test (fake game, no Slay the Spire needed)"
$ErrorActionPreference = "Continue"   # the tests log to stderr; only exit codes matter here
cmd /c "`"$py`" tests\test_planner.py 2>nul" | Select-Object -Last 1
if ($LASTEXITCODE) { Fail "planner test failed: run  $py tests\test_planner.py  to see why" }
cmd /c "`"$py`" tests\test_sim.py 2>nul" | Select-Object -Last 1
if ($LASTEXITCODE) { Fail "simulator test failed: run  $py tests\test_sim.py  to see why" }
cmd /c "`"$py`" tests\test_e2e.py 2>nul" | Select-Object -Last 1
if ($LASTEXITCODE) { Fail "self-test failed: run  $py tests\test_e2e.py  to see why" }

Write-Host "`nDone. Double-click 'Spire start' (or run start.bat), then let the game reach the main menu." -ForegroundColor Green
