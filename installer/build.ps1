# Build dist\Valoroyce-Setup-<version>.exe. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Uv path\to\uv.exe] [-Iscc path\to\ISCC.exe]
# Needs uv (to fetch the Python that ships inside) and Inno Setup 6.
param(
    [string]$Uv = "",
    [string]$Iscc = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $PSScriptRoot "stage"

if (-not $Uv) {
    $local = Join-Path $repo "uv.exe"
    $Uv = if (Test-Path $local) { $local } else { (Get-Command uv).Source }
}
if (-not $Iscc) {
    $Iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup" }
}
$version = (Select-String -Path (Join-Path $repo "src\valoroyce\__init__.py") -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory $stage | Out-Null

# The program as source; the starter installs it into .venv on first start.
Copy-Item -Recurse (Join-Path $repo "src") (Join-Path $stage "src")
Get-ChildItem -Recurse -Directory -Filter __pycache__ (Join-Path $stage "src") | Remove-Item -Recurse -Force
foreach ($f in "pyproject.toml", "uv.lock", "README.md", "README.en.md", "LICENSE", "config.example.toml") {
    Copy-Item (Join-Path $repo $f) $stage
}
Copy-Item (Join-Path $PSScriptRoot "start.pyw") $stage
# The program built here, so a player's PC needs no build tools and an update no network.
& $Uv build --wheel --quiet --out-dir (Join-Path $stage "wheel") $repo
if ($LASTEXITCODE) { throw "uv build failed" }
Copy-Item $Uv (Join-Path $stage "uv.exe")

# A standalone Python of the version the lock file was made for.
& $Uv python install 3.13 --quiet
if ($LASTEXITCODE) { throw "uv python install failed" }
# --system: without it uv answers with the project's .venv, a stub that cannot be moved.
$python = & $Uv python find --managed-python --system 3.13
if ($LASTEXITCODE) { throw "uv python find failed" }
if (Test-Path (Join-Path (Split-Path -Parent $python) "pyvenv.cfg")) { throw "$python is a virtual environment" }
Copy-Item -Recurse (Split-Path -Parent $python) (Join-Path $stage "python")
Get-ChildItem -Recurse -Directory -Filter __pycache__ (Join-Path $stage "python") | Remove-Item -Recurse -Force
# Not needed at run time; packages go into .venv, not here.
foreach ($d in "include", "libs", "Lib\site-packages", "Lib\test", "Lib\idlelib") {
    $p = Join-Path $stage "python\$d"
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}
New-Item -ItemType Directory (Join-Path $stage "python\Lib\site-packages") | Out-Null

# The icon, from the program's own PNG.
$png = Join-Path $repo "src\valoroyce\data\icon.png"
& $Uv run --no-sync python -c "import sys; from PySide6.QtGui import QGuiApplication, QImage; app = QGuiApplication(['x', '-platform', 'offscreen']); sys.exit(0 if QImage(sys.argv[1]).scaled(256, 256).save(sys.argv[2], 'ICO') else 1)" $png (Join-Path $stage "icon.ico")
if ($LASTEXITCODE) { throw "could not make icon.ico" }

& $Iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot "valoroyce.iss")
if ($LASTEXITCODE) { throw "ISCC failed" }
Get-Item (Join-Path $repo "dist\Valoroyce-Setup-$version.exe") | Select-Object FullName, Length
