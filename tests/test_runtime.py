"""Background PATH recovery, desktop app registration and approved Node prerequisites."""
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from switchboard import inventory, runtime
from switchboard.config import Config
from switchboard.executor import Executor
from switchboard.providers.cli_bridge import CLIProvider

from .conftest import make_home


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\necho detected\n')
    path.chmod(0o755)
    return path


def test_background_path_recovers_nvm_native_claude_and_bundled_codex(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(runtime, 'os_family', lambda: 'macos')
    monkeypatch.setattr(runtime, 'mac_app_dirs', lambda: [tmp_path / 'Applications'])
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    for version in ('v24.9.0', 'v24.21.0'):
        for name in ('node', 'npm', 'npx'):
            executable(tmp_path / '.nvm/versions/node' / version / 'bin' / name)
    claude = executable(tmp_path / '.local/bin/claude')
    codex = executable(tmp_path / 'Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
    assert 'v24.21.0' in runtime.which('node')
    assert not runtime.missing_node_commands()
    assert runtime.which('claude') == str(claude)
    assert runtime.which('codex') == str(codex)
    before = runtime.refresh_path()
    assert runtime.refresh_path() == before
    provider = CLIProvider('claude-code', {'command': 'claude'}, None)
    assert provider.available()


def test_refresh_reads_paths_installed_after_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'os_family', lambda: 'windows')
    monkeypatch.setenv('PATH', '')
    folder = tmp_path / 'new-runtime'
    monkeypatch.setattr(runtime, '_windows_registry_paths', lambda: [str(folder), str(folder).upper()])
    runtime.refresh_path()
    assert str(folder) not in os.environ['PATH']
    folder.mkdir()
    runtime.refresh_path()
    assert str(folder) in os.environ['PATH']
    assert len([p for p in os.environ['PATH'].split(os.pathsep) if p.casefold() == str(folder).casefold()]) == 1


def test_store_desktop_apps_do_not_count_as_plan_clis(monkeypatch):
    monkeypatch.setattr(inventory, 'platform_tag', lambda: 'windows')
    monkeypatch.setattr(inventory, 'which', lambda name: None)
    monkeypatch.setattr(inventory, '_windows_desktop_apps', lambda: {
        'anthropicpbc.claude': 'C:/WindowsApps/Claude', 'chatgpt': 'shell:AppsFolder\\OpenAI.ChatGPT!App'})
    monkeypatch.setattr(inventory.glob, 'glob', lambda pattern: [])
    found = inventory.detect_tools()
    assert found['claude_desktop']['installed'] and found['claude_desktop']['desktop']
    assert found['chatgpt_desktop']['installed'] and found['chatgpt_desktop']['desktop']
    assert not found['claude_code']['installed'] and not found['codex']['installed']


def test_node_requires_npm_and_npx(monkeypatch):
    monkeypatch.setattr(inventory, 'platform_tag', lambda: 'linux')
    monkeypatch.setattr(inventory, 'which', lambda name: '/usr/bin/node' if name == 'node' else None)
    found = inventory.detect_tools()['node']
    assert not found['installed']
    assert 'npm' in found['note'] and 'npx' in found['note']


@pytest.mark.parametrize('approved', [False, True])
async def test_npm_install_prepares_node_only_after_approval(tmp_path, monkeypatch, approved):
    events = []
    monkeypatch.setattr(runtime, 'missing_node_commands', lambda: ['npm', 'npx'])
    monkeypatch.setattr(runtime, 'install_node', lambda: events.append('node') or '/user/node')
    monkeypatch.setattr('switchboard.executor.platform_tag', lambda: 'macos')

    async def shell(command, **kwargs):
        events.append(command)
        return 0, 'installed', ''

    monkeypatch.setattr('switchboard.executor.run_shell', shell)
    executor = Executor(Config(make_home(tmp_path)), None)
    result = await executor.install_tool('gemini_cli', approved)
    if approved:
        assert result['ok'] and events == ['node', 'npm install -g @google/gemini-cli']
    else:
        assert result['needs_approval'] and not events
        assert 'Node.js' in result['command'] and 'npm install' in result['command']


def node_download(monkeypatch, tmp_path, *, bad_hash=False, traversal=False):
    folder = 'node-v26.1.0-win-x64'
    archive = folder + '.zip'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as bundle:
        bundle.writestr('../outside' if traversal else folder + '/node.exe', b'fixture, never executed')
    payload = stream.getvalue()
    checksum = ('0' * 64 if bad_hash else hashlib.sha256(payload).hexdigest())
    requests = []

    def fetch(url, **kwargs):
        requests.append(url)
        if url.endswith('index.json'):
            return io.BytesIO(json.dumps([{'version': 'v27.0.0', 'lts': False}, {'version': 'v26.1.0', 'lts': 'LTS'}]).encode())
        if url.endswith('SHASUMS256.txt'):
            return io.BytesIO(f'{checksum}  {archive}\n'.encode())
        assert url == 'https://nodejs.org/dist/v26.1.0/' + archive
        return io.BytesIO(payload)

    monkeypatch.setattr(runtime, 'os_family', lambda: 'windows')
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'AMD64')
    target = tmp_path / 'runtime/node'
    monkeypatch.setattr(runtime, 'node_root', lambda: target)
    monkeypatch.setattr(runtime, 'missing_node_commands', lambda: [] if target.exists() else ['node', 'npm', 'npx'])
    monkeypatch.setattr(runtime.urllib.request, 'urlopen', fetch)
    monkeypatch.setattr(runtime, 'grant_node_user_access', lambda path: None)
    return target, requests


def test_node_install_uses_official_lts_and_checksums(tmp_path, monkeypatch):
    target, requests = node_download(monkeypatch, tmp_path)
    assert runtime.install_node() == str(target)
    assert (target / 'node.exe').exists()
    assert len(requests) == 3 and all(u.startswith('https://nodejs.org/dist/') for u in requests)


@pytest.mark.parametrize('failure', ['hash', 'traversal'])
def test_node_download_rejects_bad_checksum_and_unsafe_archive(tmp_path, monkeypatch, failure):
    target, _ = node_download(monkeypatch, tmp_path, bad_hash=failure == 'hash', traversal=failure == 'traversal')
    with pytest.raises(RuntimeError, match='checksum|Unsafe'):
        runtime.install_node()
    assert not target.exists()
    assert not (tmp_path / 'outside').exists()


def test_windows_runtime_permissions_grant_the_user_sid_only(tmp_path, monkeypatch):
    calls = []
    sid = 'S-1-5-21-111-222-333-1001'

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='"PC\\user","' + sid + '"', returncode=0)

    monkeypatch.setattr(runtime.subprocess, 'run', run)
    target = tmp_path / 'Switchboard/node'
    runtime.grant_node_user_access(target)
    assert calls[1] == ['icacls', str(target.parent), '/grant', '*' + sid + ':(RX)', '/Q']
    assert calls[2] == ['icacls', str(target), '/grant', '*' + sid + ':(OI)(CI)M', '/T', '/Q']
