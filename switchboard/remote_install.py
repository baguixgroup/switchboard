"""Catalog-only administrator installs over a locally configured, host-verified SSH connection."""
from __future__ import annotations

import base64
import json
import re
import os
import tempfile
import shlex
from pathlib import Path

from .inventory import resolve_tool_id, run_cmd, tools_catalog


def routes(config) -> list[dict]:
    return [{"id": key, "target_node": route["target_node"], "platform": route["platform"],
             "label": route.get("label", key)}
            for key, route in (config.raw.get("remote_installs") or {}).items()
            if route.get("target_node") and route.get("platform") and route.get("host")]


def ps_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def windows_script(entry: dict, accept_agreements: bool) -> str:
    """An SSH administrator starts a one-time elevated task in their own desktop session.

    This uses the administrator identity already authenticated by SSH. No stored passwords,
    UAC setting changes, or caller-provided commands. The task is removed in finally.
    """
    command = entry["install"]["windows"]
    if entry.get("agreements", {}).get("windows") and accept_agreements:
        command += " --accept-package-agreements"
    action = "$output = & cmd.exe /d /s /c " + "'" + command.replace("'", "''") + "' 2>&1 | Out-String; if ($LASTEXITCODE) { throw $output }; $result = @{ok=$true;output=$output}"
    if entry.get("download_windows"):
        # The official signed MSIX requires an administrator for its packaged service. Using
        # the interactive admin session also avoids PLM registration errors under SSH session 0.
        downloads = entry["download_windows"]
        action = """
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {'arm64'} else {'x64'}
$urls = ConvertFrom-Json '%URLS%'
$p = Get-AppxPackage -Name '%PACKAGE%'
if ($p) { $result = @{ok=$true;output=('Already installed '+$p.Name+' '+$p.Version)} } else {
$package = Join-Path $jobRoot 'official.msix'
$ProgressPreference='SilentlyContinue'
Invoke-WebRequest $urls.$arch -UserAgent 'Switchboard/remote-install' -OutFile $package
Add-AppxPackage -Path $package -ErrorAction Stop
$p = Get-AppxPackage -Name '%PACKAGE%'
if (-not $p) { throw 'Windows did not register the official app.' }
$result = @{ok=$true;output=('Installed and registered '+$p.Name+' '+$p.Version)}
}
""".replace('%URLS%', json.dumps(downloads).replace("'", "''")).replace('%PACKAGE%', entry['windows_package'])
    # Result files contain installer output only. The task action lives in Task Scheduler,
    # protected by Windows' administrator ACL, not in a user-editable elevated script file.
    worker = "$ErrorActionPreference='Stop'; $jobRoot='__SB_JOB_ROOT__'; try { " + action + " } catch { $result=@{ok=$false;output=$_.Exception.Message} }; $result | ConvertTo-Json -Compress | Set-Content -LiteralPath ($jobRoot+'\\result.json') -Encoding UTF8"
    return r"""
$ErrorActionPreference='Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'The saved SSH account is not an administrator.' }
$id = 'Switchboard-Install-' + [guid]::NewGuid().ToString('N')
$jobRoot = Join-Path $env:ProgramData $id
New-Item -ItemType Directory -Path $jobRoot | Out-Null
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls $jobRoot /inheritance:r /grant ('*'+$sid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null
if ($LASTEXITCODE) { throw 'Could not protect the administrator installation result folder.' }
$resultPath = Join-Path $jobRoot 'result.json'
$worker = '%WORKER%'
$worker = $worker.Replace('__SB_JOB_ROOT__', $jobRoot)
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($worker))
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -EncodedCommand '+$encoded)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
try {
  Register-ScheduledTask -TaskName $id -Action $action -Principal $principal | Out-Null
  Start-ScheduledTask -TaskName $id
  $deadline = (Get-Date).AddMinutes(25)
  while (-not (Test-Path -LiteralPath $resultPath)) {
    if ((Get-Date) -gt $deadline) { throw 'Installation timed out. Check Windows installation status before retrying.' }
    $info = Get-ScheduledTaskInfo -TaskName $id
    $state = (Get-ScheduledTask -TaskName $id).State
    if ($state -ne 'Running' -and $info.LastRunTime.Year -gt 2000 -and $info.LastTaskResult -ne 0) { throw ('Windows could not start the administrator job: '+$info.LastTaskResult+'. The SSH account needs an active Windows desktop session.') }
    Start-Sleep -Seconds 2
  }
  Get-Content -LiteralPath $resultPath -Raw
} finally {
  Unregister-ScheduledTask -TaskName $id -Confirm:$false -ErrorAction SilentlyContinue
}
""".replace('%WORKER%', worker.replace("'", "''"))


async def execute(config, payload: dict, *, check: bool = False) -> dict:
    profile = (config.raw.get("remote_installs") or {}).get(str(payload.get("profile"))) or {}
    if not profile or profile.get("target_node") != payload.get("target_node"):
        raise ValueError("This administrator connection is not configured for that machine.")
    host = str(profile.get("host", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", host) or host.startswith('-'):
        raise ValueError("Invalid saved SSH host.")
    platform = profile.get("platform")
    if platform not in ("windows", "pi", "linux", "macos"):
        raise ValueError("Unsupported administrator connection platform.")
    tool = resolve_tool_id(str(payload.get('tool', '')))
    credentials = None
    if check and payload.get('credential_envelope'):
        from .install_auth import decrypt
        if not tool:
            raise ValueError('Select a catalog app before authenticating.')
        credentials = decrypt(config, payload['credential_envelope'], payload['profile'], payload['target_node'], tool)
    elif not check and payload.get('auth_handle'):
        from .install_auth import consume
        credentials = consume(config, payload['auth_handle'], payload['profile'], payload['target_node'], tool)
    argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', host]
    input_data = None
    if not check:
        entry = tools_catalog().get(tool) or {}
        if not payload.get('approved'):
            raise ValueError('Explicit installation approval is required.')
        command = entry.get('install', {}).get(platform) or (entry.get('install', {}).get('linux') if platform == 'pi' else None)
        if not command or entry.get('manual'):
            raise ValueError('Choose an app with an executable installer for this platform from the catalog.')
        if entry.get('agreements', {}).get(platform) and not payload.get('accept_agreements'):
            raise ValueError('Explicit acceptance of the displayed installation terms is required.')
    if platform == 'windows':
        script = ("if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Output 'Administrator SSH authentication succeeded.' } else { Write-Error 'The saved SSH account is not an administrator.'; exit 1 }"
                  if check else windows_script(entry, bool(payload.get('accept_agreements'))))
        # Keep cmd.exe's command line below its 8191-character limit. Catalog data
        # travels over encrypted SSH stdin, never through shell interpolation.
        loader = '$s=[Console]::In.ReadToEnd(); & ([ScriptBlock]::Create($s))'
        argv += ['powershell -NoProfile -NonInteractive -EncodedCommand '+ps_encoded(loader)]
        input_data = script.encode('utf-8')
    else:
        if check:
            command = 'true'
            needs_sudo = True
        else:
            if entry.get('installer') == 'node' or (platform == 'macos' and entry.get('installer') == 'macos_app'):
                raise ValueError('This installer runs in your user account. Choose normal permissions in the installation dialog.')
            needs_sudo = command.startswith('sudo ')
            if needs_sudo:
                command = command[5:]
        remote = 'sh -c ' + shlex.quote(command)
        if needs_sudo:
            remote = ('sudo -S -p '+shlex.quote('')+' ' if credentials else 'sudo -n ') + remote
            if credentials:
                input_data = (credentials['password']+'\n').encode()
        if check:
            remote += " && printf '%s\\n' 'Administrator SSH authentication succeeded.'"
        argv += [remote]
    if credentials:
        # SSH reads the password from askpass in its private environment, not argv, a file,
        # the terminal, the hub or model-visible output. The helper itself contains no secrets.
        if os.name == 'nt':
            raise ValueError('Password authentication currently requires a Mac/Linux controller. Use a saved SSH key on this Windows controller.')
        with tempfile.TemporaryDirectory(prefix='switchboard-askpass-') as folder:
            helper = Path(folder) / 'askpass'
            helper.write_text('#!/bin/sh\nprintf \'%s\\n\' "$SWITCHBOARD_SSH_PASSWORD"\n')
            helper.chmod(0o700)
            env = dict(os.environ, SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE='force', DISPLAY='switchboard:0', SWITCHBOARD_SSH_PASSWORD=credentials['password'])
            argv[argv.index('BatchMode=yes')] = 'BatchMode=no'
            argv[1:1] = ['-l', credentials['username'], '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no', '-o', 'NumberOfPasswordPrompts=1']
            try:
                code, stdout, stderr = await run_cmd(argv, timeout=40 if check else 1700, env=env, input_data=input_data)
            finally:
                env.pop('SWITCHBOARD_SSH_PASSWORD', None)
    else:
        code, stdout, stderr = await run_cmd(argv, timeout=40 if check else 1700, input_data=input_data)
    if credentials:
        stdout = stdout.replace(credentials['password'], '[redacted]')
        stderr = stderr.replace(credentials['password'], '[redacted]')
    if code:
        return {'ok': False, 'needs_auth': True, 'output': (stderr or stdout)[-8000:] or 'SSH authentication failed. Set up this saved connection on the controller machine first.'}
    if check:
        result = {'ok': True, 'output': stdout.strip()}
        if credentials:
            from .install_auth import grant
            result['auth_handle'] = grant(config, payload['profile'], payload['target_node'], tool, credentials)
        return result
    if platform != 'windows':
        return {'ok': True, 'output': (stdout+'\n'+stderr).strip()[-8000:] or 'Installer completed.'}
    try:
        result = json.loads(stdout.strip().lstrip('\ufeff'))
        return {'ok': bool(result.get('ok')), 'output': str(result.get('output', ''))[-8000:]}
    except (ValueError, AttributeError):
        return {'ok': False, 'output': 'The administrator job returned an invalid result. '+stdout[-3000:]}
