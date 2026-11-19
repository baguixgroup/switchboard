import base64
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard import install_auth, remote_install
from .conftest import make_home


def envelope(cfg, *, profile='pc', target='win-pc', tool='chatgpt_desktop', expires=None):
    pub = install_auth.public_key(cfg)
    public = serialization.load_der_public_key(base64.b64decode(pub['spki']))
    aes = AESGCM.generate_key(256)
    iv = b'012345678901'
    content = {'username': 'hicham', 'password': 'test-only-secret! $() ;', 'profile': profile, 'target_node': target,
               'tool': tool, 'expires': expires or time.time()+300, 'nonce': __import__('uuid').uuid4().hex}
    b64 = lambda value: base64.b64encode(value).decode()
    return {'key_id': pub['key_id'], 'iv': b64(iv),
            'wrapped_key': b64(public.encrypt(aes, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))),
            'ciphertext': b64(AESGCM(aes).encrypt(iv, json.dumps(content).encode(), b'switchboard-install-auth-v1'))}


def test_credentials_are_bound_encrypted_expiring_and_not_replayable(tmp_path):
    cfg = Config(make_home(tmp_path))
    request = envelope(cfg)
    assert 'test-only-secret' not in json.dumps(request)
    assert 'PRIVATE' not in json.dumps(install_auth.public_key(cfg))
    with pytest.raises(ValueError): install_auth.decrypt(cfg, request, 'pc', 'wrong-machine', 'chatgpt_desktop')
    with pytest.raises(ValueError): install_auth.decrypt(cfg, request, 'pc', 'win-pc', 'other-app')
    credentials = install_auth.decrypt(cfg, request, 'pc', 'win-pc', 'chatgpt_desktop')
    assert credentials['password'].startswith('test-only-secret')
    with pytest.raises(ValueError): install_auth.decrypt(cfg, request, 'pc', 'win-pc', 'chatgpt_desktop')
    with pytest.raises(ValueError): install_auth.decrypt(cfg, envelope(cfg, expires=time.time()-1), 'pc', 'win-pc', 'chatgpt_desktop')
    handle = install_auth.grant(cfg, 'pc', 'win-pc', 'chatgpt_desktop', credentials)
    assert install_auth.consume(cfg, handle, 'pc', 'win-pc', 'chatgpt_desktop') == credentials
    with pytest.raises(ValueError): install_auth.consume(cfg, handle, 'pc', 'win-pc', 'chatgpt_desktop')


async def test_password_form_authenticates_without_exposing_credentials(tmp_path, monkeypatch):
    cfg = Config(make_home(tmp_path))
    cfg.raw['remote_installs'] = {'pc': {'host':'windows', 'target_node':'win-pc', 'platform':'windows'}}
    commands = []
    async def ssh(argv, **kwargs):
        assert 'test-only-secret' not in str(argv)
        assert 'StrictHostKeyChecking=yes' in argv
        env = kwargs.get('env') or {}
        assert env['SWITCHBOARD_SSH_PASSWORD'].startswith('test-only-secret')
        helper = Path(env['SSH_ASKPASS']).read_text()
        assert 'test-only-secret' not in helper and '"$SWITCHBOARD_SSH_PASSWORD"' in helper
        commands.append(argv)
        return (0, 'Authenticated' if len(commands)==1 else '{"ok":true,"output":"Installed"}', '')
    monkeypatch.setattr(remote_install, 'run_cmd', ssh)
    request = {'profile':'pc','target_node':'win-pc','tool':'chatgpt_desktop','credential_envelope':envelope(cfg)}
    checked = await remote_install.execute(cfg, request, check=True)
    assert checked['ok'] and 'auth_handle' in checked and 'test-only-secret' not in json.dumps(checked)
    result = await remote_install.execute(cfg, {'profile':'pc','target_node':'win-pc','tool':'chatgpt_desktop',
        'approved':True,'accept_agreements':True,'auth_handle':checked['auth_handle']})
    assert result['ok'] and len(commands)==2
    with pytest.raises(ValueError): await remote_install.execute(cfg, {'profile':'pc','target_node':'different','approved':True})


async def test_admin_route_cannot_bypass_approval_catalog_or_agreements(tmp_path, monkeypatch):
    cfg = Config(make_home(tmp_path))
    cfg.raw['remote_installs'] = {'pc': {'host':'windows', 'target_node':'win-pc', 'platform':'windows'}}
    async def no_ssh(*a, **kw): pytest.fail('must not run without all approvals')
    monkeypatch.setattr(remote_install,'run_cmd',no_ssh)
    for tool, approved, terms in [('chatgpt',False,True),('chatgpt',True,False),('bad;whoami',True,True)]:
        with pytest.raises(ValueError):
            await remote_install.execute(cfg, {'profile':'pc','target_node':'win-pc','tool':tool,'approved':approved,'accept_agreements':terms})


async def test_app_chat_has_text_history_and_never_watches_images(tmp_path, monkeypatch):
    cfg = Config(make_home(tmp_path, providers={'mock':{'kind':'mock'},'chatgpt-app':{'disabled':False}}))
    engine = Engine(cfg)
    assert 'chatgpt-app' not in engine.credits()['untracked_providers']
    assert any(p['provider']=='chatgpt-app' and p['kind']=='app' for p in engine.plans_status())
    prompt='Explain photosynthesis in a few sentences'
    plan=await engine.plan(prompt,model='chatgpt-app:chat',billing='plans_only')
    task=await engine.run_task('chat-app',prompt,plan,[],[{'role':'assistant','content':'Earlier answer'}])
    assert task['status']=='waiting'
    handoff=task['plan']['handoff']
    assert handoff['task_type']=='chat' and prompt in handoff['prompt'] and 'Earlier answer' in handoff['prompt']
    assert 'image' not in task['result'] and task['cost_usd']==0
    calls=[]
    async def submit(node,kind,payload,**kw): calls.append(kind);return {'ok':True}
    monkeypatch.setattr(engine.nodes,'submit',submit)
    await engine.handoff_open(task['id'],'mac')
    assert calls==['open_app'] and not engine._bg
    engine.complete_writing(task['id'],'Plants convert light into chemical energy.')
    assert engine.store.get_task(task['id'])['status']=='done'
    await engine.close()


async def test_administrator_route_scope_and_inventory_verification(tmp_path,monkeypatch):
    engine=Engine(Config(make_home(tmp_path)))
    engine.nodes.heartbeat({'node':'mac','platform':'macos','install_routes':[{'id':'pc','target_node':'win-pc','platform':'windows','label':'Windows admin'}]})
    engine.nodes.heartbeat({'node':'win-pc','platform':'windows'})
    with pytest.raises(ValueError): engine.start_install('win-pc','chatgpt',True,'mac/unknown')
    calls=[]
    async def submit(node,kind,payload,**kw):
        calls.append((node,kind,payload))
        if kind=='admin_install':return {'ok':True,'output':'Installed'}
        return {'node':'win-pc','tools':{'chatgpt_desktop':{'installed':True}}}
    monkeypatch.setattr(engine.nodes,'submit',submit)
    task_id=engine.start_install('win-pc','chatgpt',True,'mac/pc','opaque-grant')
    await engine.running[task_id]
    task=engine.store.get_task(task_id)
    assert task['status']=='done' and calls[0][0:2]==('mac','admin_install')
    assert calls[0][2]['auth_handle']=='opaque-grant' and 'opaque-grant' not in json.dumps(task)
    await engine.close()


def test_local_key_endpoint_refuses_other_sites(tmp_path):
    from starlette.testclient import TestClient
    from switchboard.localproxy import build_proxy_app
    cfg=Config(make_home(tmp_path,role='agent',name='controller'))
    with TestClient(build_proxy_app(cfg)) as client:
        denied=client.get('/api/local/install-auth-key',headers={'Origin':'https://attacker.example'})
        assert denied.status_code==403
        response=client.get('/api/local/install-auth-key')
        assert response.status_code==200 and response.json()['controller']=='controller'
        assert 'PRIVATE' not in response.text and response.headers['cache-control']=='no-store'


def test_browser_webcrypto_envelope_matches_controller(tmp_path):
    import subprocess
    from switchboard.runtime import which
    node=which('node')
    if not node: pytest.skip('Node is optional for the browser encryption interoperability test')
    cfg=Config(make_home(tmp_path))
    source=(Path(__file__).parents[1]/'switchboard/web/static/app.js').read_text()
    function=source[source.index('async function encryptInstallCredentials('):source.index('function installRequestsHtml(')]
    script="const crypto=require('node:crypto').webcrypto; async function api(){return "+json.dumps(install_auth.public_key(cfg))+";}\n"+function+"\nencryptInstallCredentials({controller:'hub',profile:'pc'},'win-pc','chatgpt_desktop','hicham','browser-test-only').then(x=>process.stdout.write(JSON.stringify(x)));"
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,check=True)
    credentials=install_auth.decrypt(cfg,json.loads(result.stdout),'pc','win-pc','chatgpt_desktop')
    assert credentials=={'username':'hicham','password':'browser-test-only'}
    assert 'browser-test-only' not in result.stdout


async def test_pi_password_authentication_uses_private_ssh_and_sudo_input(tmp_path,monkeypatch):
    cfg=Config(make_home(tmp_path))
    cfg.raw['remote_installs']={'pc':{'host':'pi','target_node':'win-pc','platform':'pi'}}
    calls=[]
    async def ssh(argv,**kw):
        assert 'test-only-secret' not in str(argv)
        assert kw['input_data']==b'test-only-secret! $() ;\n'
        assert 'sudo -S' in argv[-1]
        calls.append(argv)
        return 0,'Authenticated' if len(calls)==1 else 'Installed Git',''
    monkeypatch.setattr(remote_install,'run_cmd',ssh)
    checked=await remote_install.execute(cfg,{'profile':'pc','target_node':'win-pc','tool':'git',
        'credential_envelope':envelope(cfg,tool='git')},check=True)
    result=await remote_install.execute(cfg,{'profile':'pc','target_node':'win-pc','tool':'git',
        'approved':True,'auth_handle':checked['auth_handle']})
    assert result['ok'] and 'apt-get install -y git' in calls[-1][-1]
