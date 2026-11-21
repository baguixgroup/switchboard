# Build Tazrivo-<version>-Setup.exe (Python included, per-user install).
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# Needs: Python 3.11+ (py launcher or python on PATH) and Inno Setup 6 (winget install JRSoftware.InnoSetup).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
$Version = (Select-String -Path "switchboard\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$Venv = "build\venv-windows"

if (-not (Test-Path "$Venv\Scripts\python.exe")) {
  $py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
  if ($py -eq "py") { & py -3 -m venv $Venv } else { & python -m venv $Venv }
}
$Py = "$Venv\Scripts\python.exe"
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet ".[tray,documents,voice]" "pyinstaller>=6.10"
if ($LASTEXITCODE) { throw "pip install failed" }

& $Py packaging\make_icons.py
& "$Venv\Scripts\pyinstaller.exe" --noconfirm --clean --log-level WARN --distpath dist --workpath build\pyinstaller packaging\switchboard.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$Iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup" }
& $Iscc /Q "/DAppVersion=$Version" "/DSourceDir=$Root\dist\Tazrivo" "/DOutputDir=$Root\dist" `
        "/DIconFile=$Root\build\icons\tazrivo.ico" packaging\windows\switchboard.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "Built dist\Tazrivo-$Version-Setup.exe"
