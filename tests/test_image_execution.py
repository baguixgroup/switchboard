"""Native images, explicit manual steps, and real structured subscription errors (all offline)."""
import asyncio
import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from switchboard import runtime
from switchboard.catalog import ModelInfo
from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.providers.base import ProviderError, RunRequest
from switchboard.providers.cli_bridge import CLIProvider, parse_reset
from switchboard.router import classify
from switchboard.web.app import create_app

from .conftest import make_home

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==')
LIMIT = "You've hit your weekly limit · resets Nov 23 at 1am (America/Winnipeg)"


def test_structured_weekly_limit_is_readable_and_preserves_reset_zone():
    provider = CLIProvider('claude-code', {}, None)
    error = json.dumps({'is_error': True, 'api_error_status': 429, 'api_error': 'usage_limit_reached',
                        'result': LIMIT, 'usage': {'input_tokens': 0}})
    with pytest.raises(ProviderError) as result:
        provider._raise(error, 1)
    assert result.value.kind == 'rate_limit' and result.value.failover_ok
    assert 'weekly limit' in str(result.value) and 'input_tokens' not in str(result.value)
    reset = parse_reset(LIMIT, datetime(2026, 11, 19, tzinfo=ZoneInfo('UTC')))
    assert reset == datetime(2026, 11, 23, 1, tzinfo=ZoneInfo('America/Winnipeg')).timestamp()


def test_image_feature_must_be_enabled_not_just_listed(tmp_path, monkeypatch):
    runtime._FEATURE_CACHE.clear()
    monkeypatch.setattr(runtime, 'which', lambda command: str(tmp_path / command))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='image_generation stable false\n'))
    assert not runtime.cli_feature_enabled('codex', 'image_generation')
    runtime._FEATURE_CACHE.clear()
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='image_generation stable true\n'))
    assert runtime.cli_feature_enabled('codex', 'image_generation')
    runtime._FEATURE_CACHE.clear()


def test_remote_image_route_requires_advertised_capability(tmp_path):
    cfg = Config(make_home(tmp_path, providers={'codex-images': {'disabled': False}}))
    engine = Engine(cfg)
    engine.providers['codex-images'].available = lambda: False
    inventory = {'node': 'pc', 'platform': 'windows', 'tools': {'codex': {'installed': True}}, 'cli_providers': ['codex']}
    engine.nodes.heartbeat(inventory)
    assert engine.cli_location('codex-images') is None
    inventory['cli_providers'].append('codex-images')
    engine.nodes.heartbeat(inventory)
    assert engine.cli_location('codex-images') == 'pc'


def fake_image_config(tmp_path):
    script = tmp_path / 'image_cli.py'
    script.write_text("import sys,base64\nfrom pathlib import Path\nprompt=sys.argv[sys.argv.index('-p')+1]\n"
                      + "if 'NO_IMAGE' not in prompt: Path('image.png').write_bytes(base64.b64decode(" + repr(base64.b64encode(PNG).decode()) + "))\n"
                      + "print('generated image')\n")
    return {'kind': 'cli', 'command': sys.executable, 'args': [str(script), '-p', '{prompt}'],
            'output_kind': 'image', 'edit_args': ['--sandbox', 'workspace-write'], 'readonly_args': ['--sandbox', 'read-only']}


async def test_image_execution_requires_an_actual_image_file(tmp_path):
    cfg = fake_image_config(tmp_path)
    provider = CLIProvider('fake-images', cfg, None)
    work = tmp_path / 'output'
    work.mkdir()
    req = RunRequest('generate an image', ModelInfo(provider='fake-images', id='default'), workdir=work)
    argv = provider.build_argv(req, req.prompt, False, None)
    assert argv[-1] == 'workspace-write'  # only this task folder, even when general source editing is off
    result = await provider.run(req)
    assert result.cost_usd == 0 and (work / 'image.png').read_bytes() == PNG
    empty = tmp_path / 'empty'
    empty.mkdir()
    req.workdir, req.prompt = empty, 'NO_IMAGE'
    with pytest.raises(ProviderError, match='without producing an image'):
        await provider.run(req)


async def test_codex_image_redirect_and_manual_handoff_confirmation(tmp_path):
    cfg = Config(make_home(tmp_path, providers={'codex-images': {'disabled': False}, 'chatgpt-app': {'disabled': False}}))
    engine = Engine(cfg)
    engine.providers['codex-images'].available = lambda: False
    engine.nodes.heartbeat({'node': 'mac', 'platform': 'macos', 'cli_providers': ['codex-images']})
    assert classify('build an image').category == 'image_gen'
    plan = await engine.plan('build an image', model='codex:default', billing='plans_only')
    assert plan.provider == 'codex-images' and not plan.needs_confirmation
    automatic = await engine.plan('build an image', billing='plans_only')
    assert automatic.provider == 'codex-images'
    with TestClient(create_app(engine), client=('203.0.113.9', 1), headers={'Authorization': 'Bearer test-token'}) as client:
        response = client.post('/api/tasks', json={'prompt': 'build an image', 'model': 'chatgpt-app:images'})
        assert response.status_code == 409 and response.json()['needs_confirmation']


def test_codex_image_and_text_limits_share_one_quota(tmp_path):
    engine = Engine(Config(make_home(tmp_path)))
    engine.mark_limited('codex-images', ProviderError('limit', 'rate_limit', retry_at=4_000_000_000))
    assert {'codex', 'codex-images'} <= engine.limits().keys()
