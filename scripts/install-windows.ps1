# Tazrivo installer for Windows 10/11.
# Run from the project folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Role agent -HubUrl http://192.168.1.50:8765 -Token <hub token>
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Role hub
param(
  [ValidateSet("hub", "agent")][string]$Role = "agent",
  [string]$HubUrl = "",
  [string]$Token = "",
  [string]$Name = ($env:COMPUTERNAME).ToLower()
)
$ErrorActionPreference = "Stop"
$Src = Split-Path -Parent $PSScriptRoot
$Base = Join-Path $env:LOCALAPPDATA "Switchboard"

function Find-Python {
  foreach ($c in @("py -3", "python")) {
    try {
      $v = & cmd /c "$c -c ""import sys;print(sys.version_info[:2] >= (3,10))"" 2>nul"
      if ($v -eq "True") { return $c }
    } catch {}
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host "Installing Python 3.12 with winget..."
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  Write-Host "Python installed. Close and reopen PowerShell, then run this script again."
  exit 1
}

Write-Host "Creating virtual environment in $Base\venv"
New-Item -ItemType Directory -Force -Path $Base | Out-Null
& cmd /c "$py -m venv ""$Base\venv"""
$VenvPy = Join-Path $Base "venv\Scripts\python.exe"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install "$Src[tray,documents,voice]" --quiet
$Sb = Join-Path $Base "venv\Scripts\switchboard.exe"

$initArgs = @("init", "--role", $Role, "--name", $Name)
if ($HubUrl) { $initArgs += @("--hub-url", $HubUrl) }
if ($Token) { $initArgs += @("--token", $Token) }
& $Sb @initArgs

# Put switchboard on the user PATH.
$scripts = Join-Path $Base "venv\Scripts"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$scripts*") {
  [Environment]::SetEnvironmentVariable("Path", "$userPath;$scripts", "User")
  Write-Host "Added $scripts to your PATH (new terminals)."
}

& $Sb service install --role $Role

if ($Role -eq "hub") {
  Write-Host "`nTo let agents on your network reach this hub, allow port 8765 (run in an admin PowerShell):"
  Write-Host '  New-NetFirewallRule -DisplayName "Switchboard hub" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private'
}
Write-Host "`nDone. Output folder: $env:USERPROFILE\Switchboard\Output"
Write-Host "Check with: switchboard doctor"
