<#
  Install Nacre on Windows from source.

  From a clone:      powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
  Without a clone:   irm https://raw.githubusercontent.com/Domslayer342/Nacre/main/scripts/install-windows.ps1 | iex

  It downloads the source (if needed) to %LOCALAPPDATA%\Programs\Nacre, creates a virtualenv, installs the
  Python packages and fonts, and adds Start menu (and optionally desktop) shortcuts. Nothing needs admin rights.
#>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\Nacre",
    [string]$Branch = "main",
    [switch]$Desktop,
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is far faster without the progress bar
$Repo = "Domslayer342/Nacre"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# ── Python 3.12+ ──────────────────────────────────────────────────────────────
function Find-Python {
    foreach ($cmd in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = Get-Command $cmd[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $pyArgs = @($cmd | Select-Object -Skip 1)
        try {
            $ok = & $cmd[0] @pyArgs -c "import sys; print(sys.version_info >= (3, 12))" 2>$null
            if ($ok -eq "True") {
                return (& $cmd[0] @pyArgs -c "import sys; print(sys.executable)").Trim()
            }
        } catch { }
    }
    return $null
}

Step "Looking for Python 3.12+"
$Python = Find-Python
if (-not $Python) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step "Installing Python 3.13 with winget"
        winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $Python = Find-Python
    }
    if (-not $Python) {
        throw "Python 3.12 or newer is required. Install it from https://www.python.org/downloads/ (tick 'Add to PATH') and run this again."
    }
}
Write-Host "    $Python"

# ── Source ────────────────────────────────────────────────────────────────────
$here = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { $null }
if ($here -and (Test-Path (Join-Path $here "run.py"))) {
    $Root = $here
    Step "Using the source in $Root"
} else {
    $Root = $InstallDir
    Step "Downloading Nacre ($Branch) to $Root"
    $tmp = Join-Path $env:TEMP ("nacre-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "nacre.zip"
        Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $tmp
        $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        # keep an existing .venv and fonts; replace everything else
        Get-ChildItem $Root -Force | Where-Object { $_.Name -ne ".venv" } | Remove-Item -Recurse -Force
        Copy-Item (Join-Path $src.FullName "*") $Root -Recurse -Force
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ── Virtualenv + packages ─────────────────────────────────────────────────────
$venv = Join-Path $Root ".venv"
$vpy = Join-Path $venv "Scripts\python.exe"
$vpyw = Join-Path $venv "Scripts\pythonw.exe"
if (-not (Test-Path $vpy)) {
    Step "Creating virtualenv"
    & $Python -m venv $venv
}
Step "Installing Python packages (PyQt6 + WebEngine is a large download)"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Step "Fetching fonts"
& $vpy (Join-Path $Root "scripts\fetch_fonts.py")
if ($LASTEXITCODE -ne 0) { throw "Couldn't download the fonts" }

# install them for this user too, so web pages (reader mode, autofill menus) get the icons and Rubik
Step "Installing fonts for $env:USERNAME"
$userFonts = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"
$fontKey = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
New-Item -ItemType Directory -Force -Path $userFonts | Out-Null
if (-not (Test-Path $fontKey)) { New-Item -Path $fontKey -Force | Out-Null }
foreach ($f in Get-ChildItem -LiteralPath (Join-Path $Root "nacre\fonts") -Filter *.ttf) {
    $dest = Join-Path $userFonts $f.Name
    Copy-Item -LiteralPath $f.FullName -Destination $dest -Force
    $name = if ($f.Name -like "Material*") { "Material Symbols Rounded (TrueType)" } else { "Rubik (TrueType)" }
    New-ItemProperty -Path $fontKey -Name $name -Value $dest -PropertyType String -Force | Out-Null
}

Step "Checking that Nacre imports"
& $vpy -c "import sys; sys.path.insert(0, sys.argv[1]); import nacre.app" $Root   # cwd may be anywhere (e.g. system32)
if ($LASTEXITCODE -ne 0) { throw "Nacre failed to import; see the error above" }

# ── Icon + shortcuts ──────────────────────────────────────────────────────────
$ico = Join-Path $Root "assets\nacre.ico"
& $vpy -c @"
import sys
from PyQt6.QtGui import QGuiApplication, QImage
app = QGuiApplication(sys.argv[:1])
img = QImage(sys.argv[1])
img.scaled(256, 256).save(sys.argv[2], 'ICO')
"@ (Join-Path $Root "assets\nacre.png") $ico 2>$null

function New-Shortcut($path) {
    $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $sh.TargetPath = $vpyw
    $sh.Arguments = '"' + (Join-Path $Root "run.py") + '"'
    $sh.WorkingDirectory = $Root
    $sh.Description = "Nacre web browser"
    if (Test-Path $ico) { $sh.IconLocation = $ico }
    $sh.Save()
}

if (-not $NoShortcut) {
    Step "Adding Start menu shortcut"
    New-Shortcut (Join-Path ([Environment]::GetFolderPath("Programs")) "Nacre.lnk")
    if ($Desktop) {
        New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Nacre.lnk")
    }
}

Write-Host ""
Write-Host "Nacre is installed in $Root" -ForegroundColor Green
Write-Host "Start it from the Start menu, or run:"
Write-Host "  & `"$vpyw`" `"$(Join-Path $Root 'run.py')`""
Write-Host "Run this script again to update."
