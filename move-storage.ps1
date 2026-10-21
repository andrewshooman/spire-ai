# Moves the bot's growing data off C: and leaves junctions behind, so every path in the code still works.
#   C:\sts-evolve\sts-bench\logs  ->  F:\SpireAI\logs      (game logs + trajectories, the big one)
#   C:\sts-evolve\evolve          ->  F:\SpireAI\evolve    (models, runs, history, settings)
# Copy first, then swap the folder for a junction in one quick step (a paused bot keeps reading
# evolve\settings.json), then delete the old copy only if every file arrived. Run with the bot paused
# or stopped. Safe to run twice.
param([string]$Target = "F:\SpireAI")
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Target | Out-Null
$moves = @(
    @{ src = "C:\sts-evolve\sts-bench\logs"; dst = "$Target\logs" },
    @{ src = "C:\sts-evolve\evolve"; dst = "$Target\evolve" }
)
foreach ($m in $moves) {
    $src, $dst = $m.src, $m.dst
    if ((Test-Path $src) -and ((Get-Item $src).LinkType -eq "Junction")) { Write-Host "$src already points to $((Get-Item $src).Target)"; continue }
    if (-not (Test-Path $src)) {
        New-Item -ItemType Directory -Force $dst | Out-Null
        cmd /c mklink /J "$src" "$dst" | Out-Null
        continue
    }
    Write-Host "Copying $src -> $dst"
    robocopy $src $dst /E /COPY:DAT /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE) for $src; nothing changed on C:" }
    $nSrc = @(Get-ChildItem $src -Recurse -File -Force).Count
    $nDst = @(Get-ChildItem $dst -Recurse -File -Force).Count
    if ($nDst -lt $nSrc) { throw "only $nDst of $nSrc files copied for $src; nothing changed on C:" }
    $old = "$src.old-move"
    Rename-Item $src $old                       # swap: rename + link back to back
    cmd /c mklink /J "$src" "$dst" | Out-Null
    if (-not (Test-Path "$src\*")) { Remove-Item $src; Rename-Item $old $src; throw "link check failed; restored $src" }
    Remove-Item $old -Recurse -Force
    Write-Host "  $src now points to $dst ($nDst files)"
}
Write-Host "Done. The bot, the web app and the tools use the same paths as before."
