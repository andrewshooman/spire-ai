param([switch]$Install)
# Builds AutoSpire.jar: tests the combat brain, compiles against the game + ModTheSpire + BaseMod
# (found in your Steam library, never bundled), packages the jar. -Install copies it into the game's
# mods folder; then tick "AutoSpire" once in the ModTheSpire launcher.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$jdk = Get-ChildItem "C:\Program Files\Java", "C:\Program Files\Eclipse Adoptium", "C:\Program Files\Microsoft" -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path "$($_.FullName)\bin\javac.exe" } | Select-Object -First 1
if (-not $jdk) { throw "No JDK found (needs javac). Install a JDK 11+ (it compiles for the game's Java 8)." }
$javac = "$($jdk.FullName)\bin\javac.exe"; $java = "$($jdk.FullName)\bin\java.exe"; $jar = "$($jdk.FullName)\bin\jar.exe"

$steam = ((Get-ItemProperty "HKCU:\Software\Valve\Steam").SteamPath) -replace '/', '\'
$libs = @($steam)
$vdf = "$steam\steamapps\libraryfolders.vdf"
if (Test-Path $vdf) { $libs += Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' } }
$lib = $libs | Where-Object { Test-Path "$_\steamapps\common\SlayTheSpire\desktop-1.0.jar" } | Select-Object -First 1
if (-not $lib) { throw "Slay the Spire not found in your Steam libraries." }
$game = "$lib\steamapps\common\SlayTheSpire"
$ws = "$lib\steamapps\workshop\content\646570"
$mts = (Get-ChildItem $ws -Recurse -Filter ModTheSpire.jar | Select-Object -First 1).FullName
$basemod = (Get-ChildItem $ws -Recurse -Filter BaseMod.jar | Select-Object -First 1).FullName
if (-not $mts -or -not $basemod) { throw "Subscribe to ModTheSpire and BaseMod in the Steam Workshop first." }

$build = "$here\build"
Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$build\test", "$build\classes" | Out-Null

Write-Host "1/3 testing the combat brain"
$brain = Get-ChildItem "$here\src\autospire\brain\*.java" | ForEach-Object { $_.FullName }
& $javac --release 8 -Xlint:-options -encoding UTF-8 -d "$build\test" @brain "$here\test\autospire\PlannerTest.java"
if ($LASTEXITCODE) { throw "test compile failed" }
& $java -cp "$build\test" autospire.PlannerTest
if ($LASTEXITCODE) { throw "brain tests failed" }

Write-Host "2/3 compiling the mod"
$cp = "$game\desktop-1.0.jar;$mts;$basemod"
$src = Get-ChildItem "$here\src" -Recurse -Filter *.java | ForEach-Object { $_.FullName }
& $javac --release 8 -Xlint:-options -encoding UTF-8 -cp $cp -d "$build\classes" @src
if ($LASTEXITCODE) { throw "compile failed" }
Copy-Item -Recurse "$here\resources\*" "$build\classes\"

Write-Host "3/3 packaging"
& $jar cf "$build\AutoSpire.jar" -C "$build\classes" .
if ($LASTEXITCODE) { throw "jar failed" }
Write-Host "Built $build\AutoSpire.jar"
if ($Install) {
    New-Item -ItemType Directory -Force "$game\mods" | Out-Null
    Copy-Item "$build\AutoSpire.jar" "$game\mods\AutoSpire.jar" -Force
    Write-Host "Installed to $game\mods. Open the ModTheSpire launcher once and tick AutoSpire."
}
