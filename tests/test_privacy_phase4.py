"""Reviewed fixed providers and real restricted-worker checks; synthetic only."""
import asyncio
import base64
import hashlib
import json
import socket
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.privacy import LocalTextMinimizer, PrivacyFailure
from switchboard.privacy_gateway import TextGateway, text_policy
from switchboard.privacy_providers import (ANTHROPIC_ENDPOINT, GeminiTextTransport,
    AnthropicTextTransport, endpoint_for, payload_fields)
from switchboard.privacy_sandbox import RestrictedWorker, sandbox_command
from switchboard.privacy_tools import LocalTools, tool_policy
from switchboard.security import SecurityDenied
from switchboard.web.app import create_app
from .conftest import make_home
from .test_local_security import PARENT, headers


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for name in ("SWITCHBOARD_PARENT_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg = Config(make_home(tmp_path / "phase4"))
    cfg.raw["security"] = {"enabled": True,
        "text_gateway": {"enabled": True, "model": "review-model"},
        "local_tools": {"enabled": True, "allowed": ["calculate", "text.statistics", "attachment.extract"]}}
    cfg.secrets.update(SWITCHBOARD_PARENT_TOKEN=PARENT, OPENAI_API_KEY="synthetic-openai-key",
                       ANTHROPIC_API_KEY="synthetic-anthropic-key", GEMINI_API_KEY="synthetic-gemini-key")
    result = Engine(cfg)
    yield result
    result.local_tools.close();result.text_gateway.close();result.store._db.close()


def parent(e):return e.security.authenticate("Bearer " + PARENT)


def require_launcher():
    path = '/usr/bin/sandbox-exec' if sys.platform == 'darwin' else '/usr/bin/bwrap'
    if sys.platform not in ('darwin', 'linux') or not Path(path).is_file():
        pytest.skip('Supported OS isolation launcher is not installed; production fails closed')


async def tool(e, name, arguments):
    p=parent(e);review=await e.local_tools.preview(p, tool=name, arguments=arguments)
    result=await e.local_tools.run(p, preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    return review,result


@pytest.mark.parametrize('provider', ['openai','anthropic','gemini'])
async def test_provider_exact_reviewed_bytes_headers_and_no_tools(engine,monkeypatch,provider,caplog):
    engine.config.raw['security']['text_gateway']['provider']=provider
    gateway=TextGateway(engine.config,engine.security)
    original=httpx.AsyncClient;seen=[]
    def handler(request):
        seen.append(request)
        assert str(request.url)==endpoint_for(gateway.policy)
        assert 'key=' not in str(request.url)
        if provider=='openai':
            assert request.headers['authorization']=='Bearer synthetic-openai-key'
            response={'choices':[{'finish_reason':'stop','message':{'content':'Contact zoe@example.test'}}]}
        elif provider=='anthropic':
            assert request.headers['x-api-key']=='synthetic-anthropic-key'
            assert request.headers['anthropic-version']=='2026-10-21'
            assert 'authorization' not in request.headers
            response={'stop_reason':'end_turn','content':[{'type':'text','text':'Contact zoe@example.test','citations':None}]}
        else:
            assert request.headers['x-goog-api-key']=='synthetic-gemini-key'
            assert 'authorization' not in request.headers
            response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Contact zoe@example.test','thoughtSignature':'opaque-discarded'}]}}]}
        return httpx.Response(200,json=response)
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr('httpx.AsyncClient',client)
    review=await gateway.preview(parent(engine),prompt='My name is Zoe Bennett. Explain fractions.',
        history=[{'role':'user','content':'Phone +1 204 555 0132'}],
        context=[{'kind':'tool_result','text':'My school is Cedar School.'}])
    assert not seen
    response=await gateway.send(parent(engine),preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    assert len(seen)==1
    assert hashlib.sha256(seen[0].content).hexdigest()==review['payload_digest']
    assert json.loads(seen[0].content)==review['payload']
    assert b'Zoe Bennett' not in seen[0].content and b'555 0132' not in seen[0].content
    assert 'zoe@example.test' not in response['answer']
    assert 'opaque-discarded' not in json.dumps(response)
    assert 'Zoe Bennett' not in caplog.text
    assert 'tools' not in review['payload']
    assert engine.store.recent_tasks()==[]
    gateway.close()


@pytest.mark.parametrize('provider', ['anthropic','gemini'])
async def test_new_providers_reject_action_blocks_and_do_not_fall_back(engine,monkeypatch,provider):
    engine.config.raw['security']['text_gateway']['provider']=provider
    gateway=TextGateway(engine.config,engine.security)
    original=httpx.AsyncClient;calls=[]
    def handler(request):
        calls.append(request)
        body={'stop_reason':'tool_use','content':[{'type':'tool_use','name':'calculate','input':{'expression':'2+2'}}]} if provider=='anthropic' else {'candidates':[{'finishReason':'STOP','content':{'parts':[{'functionCall':{'name':'calculate','args':{'expression':'2+2'}}}]}}]}
        return httpx.Response(200,json=body)
    monkeypatch.setattr('httpx.AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    review=await gateway.preview(parent(engine),prompt='Explain fractions.',history=[],context=[])
    with pytest.raises(PrivacyFailure):
        await gateway.send(parent(engine),preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    assert len(calls)==1 and not gateway.pending
    assert not engine.local_tools.pending
    gateway.close()


@pytest.mark.parametrize('provider', ['anthropic','gemini'])
async def test_non_openai_credentials_and_provider_policy_bound_to_approval(engine,provider):
    engine.config.raw['security']['text_gateway']['provider']=provider
    gateway=TextGateway(engine.config,engine.security)
    review=await gateway.preview(parent(engine),prompt='Explain fractions.',history=[],context=[])
    env='ANTHROPIC_API_KEY' if provider=='anthropic' else 'GEMINI_API_KEY'
    engine.config.secrets[env]='changed-account-key'
    with pytest.raises(SecurityDenied):
        await gateway.send(parent(engine),preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    gateway.close()


@pytest.mark.parametrize('update',[{'provider':'evil'}, {'provider':[]}, {'provider':'gemini','model':'../../private'}, {'provider':'gemini','model':42}, {'base_url':'https://evil.example'}])
def test_fixed_provider_selection_rejects_destination_and_model_abuse(engine,update):
    engine.config.raw['security']['text_gateway'].update(update)
    with pytest.raises(ValueError):text_policy(engine.config)


@pytest.mark.parametrize('tool_name,args,expected',[
    ('calculate',{'expression':'2 + 3 * 4'},'14'),
    ('text.statistics',{'text':'one two'},'"words": 2'),
    ('attachment.extract',{'format':'text','data_b64':base64.b64encode(b'Explain fractions.').decode()},'Explain fractions.'),
    ('attachment.extract',{'format':'markdown','data_b64':base64.b64encode(b'# Fractions\nOne half').decode()},'One half'),
    ('attachment.extract',{'format':'csv','data_b64':base64.b64encode(b'name,topic\nZoe,fractions').decode()},'fractions'),
    ('attachment.extract',{'format':'csv','data_b64':base64.b64encode(b'name,topic\n"Dr.\nAda",fractions').decode()},'fractions'),
    ('attachment.extract',{'format':'json','data_b64':base64.b64encode(b'{"name":"Dr. Ada", "topic":"fractions"}').decode()},'fractions'),
])
async def test_real_restricted_worker_tools_return_only_checked_text(engine,tool_name,args,expected,caplog):
    require_launcher()
    review,result=await tool(engine,tool_name,args)
    assert expected in result['text']
    assert 'Zoe' not in result['text'] and 'Ada' not in result['text']
    assert result['external_transfer'] is False and result['requires_separate_provider_review'] is True
    assert 'arguments' not in review and 'data_b64' not in json.dumps(review)
    assert 'Zoe' not in caplog.text and 'Dr. Ada' not in caplog.text
    assert engine.store.recent_tasks()==[] and not list(engine.config.output_dir.iterdir())
    with pytest.raises(SecurityDenied):
        await engine.local_tools.run(parent(engine),preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)


async def test_real_os_worker_cannot_read_host_sentinel_connect_host_or_inherit_secrets(tmp_path,monkeypatch):
    require_launcher()
    sentinel=tmp_path/'outside.txt';sentinel.write_text('synthetic private sentinel')
    monkeypatch.setenv('SWITCHBOARD_WORKER_SENTINEL','synthetic private environment')
    with socket.socket() as server:
        server.bind(('127.0.0.1',0));server.listen()
        result=await RestrictedWorker().run({'sentinel':str(sentinel),'port':server.getsockname()[1]},isolation_check=True)
    assert json.loads(result)=={'read':False,'network':False,'secret_env':False}


@pytest.mark.parametrize('expression',["__import__('os').system('touch hacked')",'open("/etc/passwd").read()','2**100000','[1,2][0]','1/0','1e999','True','9'*300])
async def test_worker_rejects_code_file_access_and_unbounded_arithmetic(expression):
    require_launcher()
    with pytest.raises(PrivacyFailure):
        await RestrictedWorker().run({'tool':'calculate','arguments':{'expression':expression}})


@pytest.mark.parametrize('format,data',[('pdf',b'%PDF'),('text',b'\xff\x00'),('text',b'%PDF-1.7'),('json',b'{broken'),('csv',b'name,topic\nZoe'),('text',b'x'*8001)])
async def test_attachment_unsupported_binary_malformed_and_oversized_fail_closed(engine,format,data):
    require_launcher()
    args={'format':format,'data_b64':base64.b64encode(data).decode()}
    if format=='pdf':
        with pytest.raises(PrivacyFailure):await engine.local_tools.preview(parent(engine),tool='attachment.extract',arguments=args)
    else:
        with pytest.raises(PrivacyFailure):await tool(engine,'attachment.extract',args)


async def test_no_isolation_launcher_means_no_worker_or_unrestricted_fallback(engine,monkeypatch):
    def unavailable(*args,**kwargs):raise PrivacyFailure('sandbox_unavailable')
    def forbidden(*args,**kwargs):pytest.fail('Must not launch an unrestricted process')
    monkeypatch.setattr('switchboard.privacy_sandbox.sandbox_command',unavailable)
    monkeypatch.setattr('asyncio.create_subprocess_exec',forbidden)
    with pytest.raises(PrivacyFailure,match='sandbox_unavailable'):
        await tool(engine,'calculate',{'expression':'2+2'})


async def test_tool_policy_role_owner_digest_revocation_and_expiry(engine):
    root=parent(engine)
    for role,device in [('child',None),('device','node-1')]:
        credential=engine.security.issue(root,subject='opaque',role=role,device=device)['token']
        with pytest.raises(SecurityDenied):await engine.local_tools.preview(engine.security.authenticate('Bearer '+credential),tool='calculate',arguments={'expression':'2+2'})
    review=await engine.local_tools.preview(root,tool='calculate',arguments={'expression':'2+2'})
    with pytest.raises(SecurityDenied):await engine.local_tools.run(root,preview_id=review['preview_id'],payload_digest='0'*64,confirm=True)
    token=engine.security.issue(root,subject='parent-two',role='parent')['token']
    with pytest.raises(SecurityDenied):await engine.local_tools.run(engine.security.authenticate('Bearer '+token),preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    engine.local_tools.now=lambda:review['expires']
    with pytest.raises(SecurityDenied):await engine.local_tools.run(root,preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    assert not engine.local_tools.pending


async def test_local_tool_output_needs_independent_outbound_review_and_legacy_paths_stay_closed(engine):
    require_launcher()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)),base_url='http://localhost') as c:
        response=await c.post('/api/privacy/tools/preview',headers=headers(),json={'tool':'calculate','arguments':{'expression':'2+2'}})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        review=response.json()
        response=await c.post('/api/privacy/tools/run',headers=headers(),json={'preview_id':review['preview_id'],'payload_digest':review['payload_digest'],'confirm':True})
        assert response.status_code==200 and response.json()['text']=='4'
        assert not engine.text_gateway.pending
        for path in ['/api/gateway','/api/tasks','/api/plan','/api/capabilities/voice','/api/tools/install']:
            assert (await c.post(path,headers=headers(),json={})).status_code==403
        for role,device in [('child',None),('device','node-1')]:
            credential=engine.security.issue(parent(engine),subject='opaque',role=role,device=device)['token']
            for path in ['/api/privacy/tools/preview','/api/privacy/tools/run']:
                assert (await c.post(path,headers=headers(credential),json={})).status_code==403
        response=await c.post('/api/privacy/tools/preview',headers=headers(),json={'tool':'shell','arguments':{'command':'raw private marker'}})
        assert response.status_code==400 and 'raw private marker' not in response.text


@pytest.mark.parametrize('raw',[None,[],{'enabled':'true'},{'allowed':['shell']},{'allowed':['calculate','calculate']},{'sandbox_required':False}])
def test_local_tool_policy_cannot_disable_isolation_or_add_arbitrary_actions(engine,raw):
    engine.config.raw['security']['local_tools']=raw
    with pytest.raises(ValueError):tool_policy(engine.config)


@pytest.mark.parametrize('line',['"name": "Dr. Ada",','Nom: Zoé','الاسم: عمر','phone: +1 204 555 0132'])
def test_structured_identifier_lines_are_removed_without_leaving_honorific_fragments(line):
    result=LocalTextMinimizer().minimize((line,))
    assert result.texts==('[removed identifying context]',)


@pytest.mark.parametrize('value',['My name is\nZoe Bennett','Je m\'appelle\nZoé','اسمي\nأحمد','smiti\nYassine'])
def test_multiline_explicit_identifiers_do_not_survive_as_detached_values(value):
    result=LocalTextMinimizer().minimize((value,))
    assert result.texts==('[removed identifying context] [removed identifying context]',)


@pytest.mark.parametrize('name,args',[
    ('calculate',{'expression':'2+2','command':'raw private marker'}),
    ('attachment.extract',{'format':'text','path':'/private/secret'}),
    ('attachment.extract',{'format':'text','data_b64':'not base64!'}),
    ('attachment.extract',{'format':'text','data_b64':base64.b64encode(b'x'*16385).decode()}),
    ('text.statistics',{'text':'x','url':'https://external.example'}),
])
async def test_tool_arguments_cannot_add_commands_paths_urls_or_oversized_files(engine,name,args):
    with pytest.raises(PrivacyFailure):
        await engine.local_tools.preview(parent(engine),tool=name,arguments=args)
    assert not engine.local_tools.pending


async def test_local_tool_policy_privacy_changes_revocation_and_result_checks(engine):
    root=parent(engine)
    review=await engine.local_tools.preview(root,tool='calculate',arguments={'expression':'2+2'})
    engine.config.raw['security']['text_gateway']['known_identifiers']=['new local identifier']
    with pytest.raises(SecurityDenied):
        await engine.local_tools.run(root,preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    engine.config.raw['security']['text_gateway'].pop('known_identifiers')
    grant=engine.security.issue(root,subject='parent-one',role='parent')
    principal=engine.security.authenticate('Bearer '+grant['token'])
    review=await engine.local_tools.preview(principal,tool='calculate',arguments={'expression':'2+2'})
    class Revokes:
        async def run(self,value):
            engine.security.revoke(root,grant['id'])
            return 'must not be returned'
    engine.local_tools.worker=Revokes()
    with pytest.raises(SecurityDenied):
        await engine.local_tools.run(principal,preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    class PrivateResult:
        async def run(self,value):return 'My name is Zoe Bennett. Email zoe@example.test'
    engine.local_tools.worker=PrivateResult()
    _, result=await tool(engine,'calculate',{'expression':'2+2'})
    assert 'Zoe' not in result['text'] and 'zoe@example.test' not in result['text']
    engine.local_tools.close()
    assert not engine.local_tools.pending and not engine.local_tools.timers


def test_linux_launcher_has_required_namespaces_without_host_root_or_environment(monkeypatch,tmp_path):
    from switchboard import privacy_sandbox
    original=Path.is_file
    monkeypatch.setattr(privacy_sandbox.sys,'platform','linux')
    monkeypatch.setattr(Path,'is_file',lambda self: True if str(self)=='/usr/bin/bwrap' else original(self))
    args=sandbox_command(str(tmp_path))
    for flag in ['--unshare-user','--unshare-net','--unshare-pid','--clearenv']:
        assert flag in args or (flag=='--unshare-pid' and '--unshare-all' in args)
    assert '--share-net' not in args
    assert not any(args[i:i+3]==['--ro-bind','/','/'] for i in range(len(args)))
    assert '-I' in args and '-S' in args and '-X' in args


def test_unsupported_platform_never_selects_unrestricted_python(monkeypatch,tmp_path):
    from switchboard import privacy_sandbox
    monkeypatch.setattr(privacy_sandbox.sys,'platform','win32')
    with pytest.raises(PrivacyFailure,match='sandbox_unavailable'):sandbox_command(str(tmp_path))


async def test_local_tools_work_without_enabling_or_configuring_external_ai(engine):
    require_launcher()
    engine.config.raw['security']['text_gateway']={'enabled':False}
    for key in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY']:engine.config.secrets.pop(key)
    gateway=TextGateway(engine.config,engine.security)
    service=LocalTools(engine.config,engine.security,gateway)
    review=await service.preview(parent(engine),tool='calculate',arguments={'expression':'7*6'})
    result=await service.run(parent(engine),preview_id=review['preview_id'],payload_digest=review['payload_digest'],confirm=True)
    assert result['text']=='42' and result['external_transfer'] is False
    assert not gateway.pending
    service.close();gateway.close()


async def test_environment_provider_credentials_are_deny_terms_without_legacy_provider_config(engine,monkeypatch):
    engine.config.raw['providers']={}
    engine.config.secrets.pop('ANTHROPIC_API_KEY')
    monkeypatch.setenv('ANTHROPIC_API_KEY','short-environment-key')
    gateway=TextGateway(engine.config,engine.security)
    review=await gateway.preview(parent(engine),prompt='Explain short-environment-key',history=[],context=[])
    assert 'short-environment-key' not in json.dumps(review['payload'])
    gateway.close()
