# Launch Nacre once with packaging/smoke.py and report whether it got as far as screenshots.
#   smoke.ps1 <label> <program> [args...]      e.g. smoke.ps1 exe dist\Nacre\Nacre.exe
param([string]$Label, [string]$Program, [Parameter(ValueFromRemainingArguments)][string[]]$Rest)
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$out = (New-Item -ItemType Directory -Force (Join-Path $root "smoke\$Label")).FullName
foreach ($v in "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME") {
    Set-Item "env:$v" (New-Item -ItemType Directory -Force (Join-Path $env:RUNNER_TEMP "$Label-$v")).FullName
}
$env:NACRE_SMOKE_OUT = $out
$env:NACRE_SCRIPT = Join-Path $root "packaging\smoke.py"
$p = if ($Rest) { Start-Process $Program -ArgumentList $Rest -PassThru -RedirectStandardError (Join-Path $out "stderr.txt") -RedirectStandardOutput (Join-Path $out "stdout.txt") }
     else { Start-Process $Program -PassThru }
if (-not $p.WaitForExit(150000)) { $p.Kill(); Set-Content (Join-Path $out "timeout.txt") "timed out" }
$crash = Join-Path $env:XDG_CACHE_HOME "nacre\crash.log"
if (Test-Path $crash) { Copy-Item $crash $out }
$ok = Test-Path (Join-Path $out "smoke.txt")
Write-Host ("[{0}] exit={1} ok={2}" -f $Label, $p.ExitCode, $ok)
if (Test-Path $crash) { Get-Content $crash | Select-Object -First 15 | ForEach-Object { Write-Host "  $_" } }
if ($ok) { Get-Content (Join-Path $out "smoke.txt") | ForEach-Object { Write-Host "  $_" } }
return $ok
