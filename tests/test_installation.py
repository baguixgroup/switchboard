"""Remote installation must require consent and verify real target inventory."""
import asyncio
import json

import httpx
import pytest

from switchboard.config import Config
from switchboard.engine import Engine, MANAGEMENT_TOOLS
from switchboard.executor import Executor
from switchboard.web.app import create_app
from .conftest import make_home


async def test_store_terms_and_detection(tmp_path, monkeypatch):
    installed = False
    commands = []
    monkeypatch.setattr('switchboard.executor.platform_tag', lambda: 'windows')
    monkeypatch.setattr('switchboard.executor.detect_tools', lambda: {'chatgpt_desktop': {'installed': installed}})
    monkeypatch.setattr('switchboard.runtime.which', lambda command: command)
    async def shell(command, **kw):
        nonlocal installed
        commands.append(command)
        installed = True
        return 0, 'Store installed ChatGPT', ''
    monkeypatch.setattr('switchboard.executor.run_shell', shell)
    ex = Executor(Config(make_home(tmp_path)), None)
    pre = await ex.install_tool('chatgpt', False)
    assert pre['needs_approval'] and len(pre['agreements']) == 2
    denied = await ex.install_tool('chatgpt', True)
    assert not denied['ok'] and not commands
    result = await ex.install_tool('chatgpt', True, True)
    assert result['ok'] and result['installed']
    assert '9PLM9XGG6VKS' in commands[0] and '--accept-package-agreements' in commands[0]
    assert '--accept-source-agreements' not in commands[0]
    assert (await ex.install_tool('chatgpt', True))['already_installed']
    assert len(commands) == 1


async def test_background_install_and_proposal(tmp_path, monkeypatch):
    engine = Engine(Config(make_home(tmp_path)))
    inventory = {'node': 'win-pc', 'platform': 'windows', 'tools': {}}
    engine.nodes.heartbeat(inventory)
    release = asyncio.Event()
    calls = []
    async def submit(node, kind, payload, **kw):
        calls.append(payload)
        if not payload.get('approved'):
            return {'needs_approval': True, 'command': 'official installer'}
        await release.wait()
        return {'ok': True, 'output': 'Installed and detected', 'inventory': {**inventory, 'tools': {'chatgpt_desktop': {'installed': True}}}}
    monkeypatch.setattr(engine.nodes, 'submit', submit)
    tb = await engine.build_toolbox(None, engine.config.output_dir, servers=[MANAGEMENT_TOOLS])
    text, error = await tb.call('request_app_install', {'node': 'windows', 'tool': 'ChatGPT'})
    assert not error and json.loads(text)['needs_approval']
    assert not engine.running and not calls[0]['approved']
    assert engine.install_requests[str(engine.config.output_dir)][0]['tool'] == 'chatgpt_desktop'
    transport = httpx.ASGITransport(app=create_app(engine))
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1', headers={'Authorization': f'Bearer {engine.config.token}'}) as client:
        response = await asyncio.wait_for(client.post('/api/tools/install', json={'node': 'windows', 'tool': 'chatgpt', 'approved': True, 'accept_agreements': True}), 1)
        assert response.status_code == 202, response.text
        task_id = response.json()['task_id']
        assert engine.start_install('windows', 'chatgpt') == task_id
        pending = engine.running[task_id]
        release.set()
        await pending
        assert engine.store.get_task(task_id)['status'] == 'done'
        assert engine.nodes.nodes['win-pc']['tools']['chatgpt_desktop']['installed']
        assert calls[-1]['accept_agreements']
    await engine.close()


async def test_install_prompt_attaches_management_gateway(tmp_path):
    engine = Engine(Config(make_home(tmp_path)))
    plan = await engine.plan('Install ChatGPT on my Windows PC')
    assert MANAGEMENT_TOOLS in plan.tools
    disabled = await engine.plan('Install ChatGPT on my Windows PC', tools='none')
    assert MANAGEMENT_TOOLS not in disabled.tools
    await engine.close()


async def test_failed_remote_installer_is_not_reported_done(tmp_path, monkeypatch):
    engine = Engine(Config(make_home(tmp_path)))
    engine.nodes.heartbeat({'node': 'win-pc', 'platform': 'windows'})
    async def failed(*args, **kwargs):
        return {'ok': False, 'needs_user_action': True, 'output': 'Store sign-in is required on win-pc'}
    monkeypatch.setattr(engine.nodes, 'submit', failed)
    task_id = engine.start_install('windows', 'chatgpt', True)
    await engine.running[task_id]
    task = engine.store.get_task(task_id)
    assert task['status'] == 'failed' and 'Store sign-in' in task['error']
    await engine.close()


def test_codex_gateway_approval_is_scoped_to_switchboard(tmp_path):
    from switchboard.catalog import ModelInfo
    from switchboard.providers import RunRequest
    from switchboard.providers.cli_bridge import CLIProvider
    cfg = Config(make_home(tmp_path))
    provider = CLIProvider('codex', {'mcp_style': 'codex'}, cfg)
    request = RunRequest(prompt='prepare installation', model=ModelInfo(provider='codex', id='default'),
                         workdir=tmp_path, tool_servers=[MANAGEMENT_TOOLS])
    argv = []
    provider._attach_gateway(argv, request)
    assert 'mcp_servers.switchboard.default_tools_approval_mode="approve"' in argv
    assert not any('danger-full-access' in arg or 'dangerously' in arg for arg in argv)
    untouched = []
    request.tool_servers = []
    provider._attach_gateway(untouched, request)
    assert untouched == []


async def test_false_store_success_uses_official_package(tmp_path, monkeypatch):
    installed = False
    monkeypatch.setattr('switchboard.executor.platform_tag', lambda: 'windows')
    monkeypatch.setattr('switchboard.executor.detect_tools', lambda: {'chatgpt_desktop': {'installed': installed}})
    monkeypatch.setattr('switchboard.runtime.which', lambda command: command)
    async def store(*args, **kwargs):
        return 0, 'Successfully installed', ''
    def msix(entry):
        nonlocal installed
        assert entry['windows_package'] == 'OpenAI.Codex'
        installed = True
        return 'OpenAI.Codex registered'
    monkeypatch.setattr('switchboard.executor.run_shell', store)
    monkeypatch.setattr('switchboard.installers.install_windows_msix', msix)
    result = await Executor(Config(make_home(tmp_path)), None).install_tool('chatgpt', True, True)
    assert result['ok'] and result['installed'] and 'MSIX fallback' in result['output']


def test_msix_rejects_unexpected_package_identity(tmp_path, monkeypatch):
    import io
    import zipfile
    from switchboard.installers import install_windows_msix
    from switchboard.inventory import tools_catalog
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as bundle:
        bundle.writestr('AppxManifest.xml', '<Package><Identity Name="Wrong.App"/></Package>')
    monkeypatch.setattr('switchboard.installers.urllib.request.urlopen', lambda *a, **kw: io.BytesIO(payload.getvalue()))
    monkeypatch.setattr('switchboard.installers.subprocess.run', lambda *a, **kw: pytest.fail('must not install an unexpected package'))
    with pytest.raises(RuntimeError, match='unexpected application identity'):
        install_windows_msix(tools_catalog()['chatgpt_desktop'])


def test_official_download_identifies_switchboard(monkeypatch):
    from switchboard.installers import official_download
    from switchboard import __version__
    seen = []
    def opened(request, **kwargs):
        seen.append((request, kwargs))
        return 'response'
    monkeypatch.setattr('switchboard.installers.urllib.request.urlopen', opened)
    assert official_download('https://persistent.oaistatic.com/package.msix') == 'response'
    assert seen[0][0].get_header('User-agent') == f'Tazrivo/{__version__}'
    assert seen[0][1]['timeout'] == 120


def test_msix_reports_admin_requirement_without_blocking_cleanup(tmp_path, monkeypatch):
    import io
    import zipfile
    from types import SimpleNamespace
    from switchboard.installers import install_windows_msix
    from switchboard.inventory import tools_catalog
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as bundle:
        bundle.writestr('AppxManifest.xml', '<Package><Identity Name="OpenAI.Codex"/></Package>')
    folder=tmp_path/'official-package';folder.mkdir()
    monkeypatch.setattr('switchboard.installers.tempfile.mkdtemp', lambda **kw: str(folder))
    monkeypatch.setattr('switchboard.installers.urllib.request.urlopen', lambda *a, **kw: io.BytesIO(payload.getvalue()))
    monkeypatch.setattr('switchboard.installers.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=1,stdout='',stderr='0x80073D28 administrator privileges required'))
    monkeypatch.setattr('switchboard.installers.shutil.rmtree', lambda *a, **kw: pytest.fail('must not block failure on deleting a locked package'))
    with pytest.raises(RuntimeError,match='administrator approval') as error:
        install_windows_msix(tools_catalog()['chatgpt_desktop'])
    assert 'Add-AppxPackage' in str(error.value) and (folder/'ChatGPT.msix').exists()
