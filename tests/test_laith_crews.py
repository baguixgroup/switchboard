import asyncio
import copy
import json
import time

import httpx
import pytest

from switchboard.companion import Companion
from switchboard.contracts import check_output, team_plan
from switchboard.engine import Engine
from switchboard.providers import RunResult, ProviderError
from switchboard.providers.cli_bridge import CLIProvider
from switchboard.studio import validate_graph
from switchboard.web.app import create_app
from .test_studio import make_engine, wait_for, blueprint, node


TASKS=[{'id':'research','agent':'researcher','title':'Gather evidence','task':'Find the relevant facts.','expected':'A source list.','depends_on':[]},
       {'id':'build','agent':'builder','title':'Create the result','task':'Use the research to deliver the result.','expected':'A concise deliverable.','depends_on':['research']}]


def response(text):return RunResult(text=text,provider='mock',model='mock-small')


@pytest.mark.parametrize('mutate',[
    lambda t:t[0].update(agent='administrator'),
    lambda t:t[0].update(tools='auto'),
    lambda t:t[0].update(depends_on=['build']),
    lambda t:t[1].update(depends_on=['missing']),
    lambda t:t[1].update(id='research'),
    lambda t:t[0].update(expected=''),
])
def test_manager_cannot_delegate_permissions_or_invalid_dependencies(mutate):
    tasks=copy.deepcopy(TASKS);mutate(tasks)
    with pytest.raises(ValueError):team_plan(json.dumps({'tasks':tasks}),['researcher','builder'])


def test_output_checks_are_deterministic():
    check_output('Sources: a useful brief',{'min_words':3,'max_words':5,'required':['Sources']})
    check_output('{"result":"done"}',{'format':'json','json_fields':['result']})
    for output,contract in [('too many words',{'max_words':2}),('No citation',{'required':['Sources']}),('{"wrong":1}',{'json_fields':['result']}),('```json\n{"x":NaN}\n```',{'format':'json'})]:
        with pytest.raises(ValueError):check_output(output,contract)


async def test_approved_manager_plan_dispatches_snapshotted_specialists_then_final_gate(tmp_path,monkeypatch):
    e=make_engine(tmp_path);calls=[]
    async def provider_run(req,tools=None):
        calls.append(req.prompt)
        return response(json.dumps({'tasks':TASKS}) if 'smallest useful crew' in req.prompt else 'Specialist deliverable with evidence.')
    monkeypatch.setattr(e.providers['mock'],'run',provider_run)
    run=e.studio.start('managed-team','Create a source-grounded brief.')
    gate=await wait_for(e,run['id'],{'awaiting_approval','failed'})
    assert gate['status']=='awaiting_approval',gate
    assert len(calls)==1 and gate['waiting_step']=='crew_approval'
    assert 'researcher' in gate['agents'] and len(gate['blueprint']['nodes'])==6
    # Reopen the database with the human gate still pending.
    await e.close();e=make_engine(tmp_path);e.studio.recover();monkeypatch.setattr(e.providers['mock'],'run',provider_run)
    await e.studio.decision(gate['id'],'crew_approval',True)
    final=await wait_for(e,gate['id'],{'awaiting_approval','failed'})
    assert final['status']=='awaiting_approval' and final['waiting_step']=='final_review',final
    assert len(calls)==4 and len(final['blueprint']['nodes'])==8
    assert final['steps']['crew--research']['status']=='done' and final['steps']['crew--build']['status']=='done'
    assert 'Specialist deliverable' in calls[2] and not list(__import__('pathlib').Path(final['output_path']).rglob('crew-handover.md'))
    await e.studio.decision(gate['id'],'final_review',True)
    done=await wait_for(e,gate['id'],{'done','failed'});assert done['status']=='done'
    assert done['steps']['crew--build']['attempts']==1
    await e.close()


async def test_rejected_manager_never_dispatches(tmp_path,monkeypatch):
    e=make_engine(tmp_path);calls=[]
    async def run(*a,**kw):calls.append(1);return response(json.dumps({'tasks':TASKS}))
    monkeypatch.setattr(e.providers['mock'],'run',run)
    started=e.studio.start('managed-team','A small brief')
    await wait_for(e,started['id'],{'awaiting_approval'})
    await e.studio.decision(started['id'],'crew_approval',False)
    assert len(calls)==1 and e.studio.run(started['id'])['status']=='rejected'
    await e.close()


async def test_output_rework_keeps_attempt_evidence_and_is_bounded(tmp_path,monkeypatch):
    e=make_engine(tmp_path);calls=[]
    async def run(req,tools=None):
        calls.append(req.prompt);return response('This is far too long for the limit.' if len(calls)==1 else 'Done.')
    monkeypatch.setattr(e.providers['mock'],'run',run)
    spec=e.studio.save_blueprint(blueprint([node('work','agent',agent='builder',prompt='Write one word.',contract={'max_words':1},revisions=1)]))
    run=e.studio.start(spec['id'],'test');done=await wait_for(e,run['id'],{'done','failed'})
    assert done['status']=='done',done
    step=done['steps']['work'];assert len(step['tasks'])==2 and [c['passed'] for c in step['checks']]==[False,True]
    assert 'Feedback:' in calls[1]
    # Unsafe auto-rework with tools is rejected before any request.
    with pytest.raises(ValueError):validate_graph(blueprint([node('a','agent',agent='builder',tools='auto',revisions=1)]))
    async def invalid(*a,**kw):return response('Still too many words')
    monkeypatch.setattr(e.providers['mock'],'run',invalid)
    run=e.studio.start(spec['id'],'test');bad=await wait_for(e,run['id'],{'failed'})
    assert len(bad['steps']['work']['tasks'])==2 and 'Output check failed' in bad['error']
    await e.close()


async def wait_chat(c,key):
    for _ in range(200):
        chat=c.chat(key)
        if chat['turns'][-1]['status'] in ('done','failed','waiting','interrupted'):return chat
        await asyncio.sleep(.01)
    pytest.fail('Laith request did not settle.')


async def test_laith_context_memory_isolation_persistence_and_goal_handoff(tmp_path,monkeypatch):
    e=make_engine(tmp_path);c=e.companion;seen=[]
    async def run(req,tools=None):seen.append(req.prompt);return response('A useful goal brief with success criteria.')
    monkeypatch.setattr(e.providers['mock'],'run',run)
    memory=c.remember('I prefer French explanations.');a=c.create_chat();b=c.create_chat()
    turn=await c.prepare(a['id'],'My secret project name is Mango',model='mock:mock-small')
    await c.send(a['id'],turn['id']);await wait_chat(c,a['id'])
    assert 'French explanations' in seen[-1] and c.memories()[0]['active']==1
    next_turn=await c.prepare(a['id'],'Continue that project',model='mock:mock-small');await c.send(a['id'],next_turn['id']);done=await wait_chat(c,a['id'])
    assert 'Mango' in seen[-1]
    other=await c.prepare(b['id'],'Hello',model='mock:mock-small',use_memory=False);await c.send(b['id'],other['id']);await wait_chat(c,b['id'])
    assert 'Mango' not in seen[-1] and 'French explanations' not in seen[-1]
    c.memory_state(memory['id'],False)
    prepared=await c.prepare(a['id'],'Next message',model='mock:mock-small');assert prepared['memory_count']==0
    reopened=Companion(e);assert reopened.chat(a['id'])['turns'][1]['answer']==done['turns'][1]['answer']
    goal=c.goal_from_chat(a['id'],next_turn['id'],'Deliver the project')
    assert goal['run_id'] is None and goal['blueprint_id']=='managed-team' and not e.studio.runs()
    c.archive_chat(b['id'],True);assert len(c.chats(True))==1
    c.archive_chat(b['id'],False);assert len(c.chats())==2
    await e.close()


async def test_request_preview_binding_confirmation_expiry_and_double_send(tmp_path,monkeypatch):
    e=make_engine(tmp_path);c=e.companion;chat=c.create_chat();other=c.create_chat()
    turn=await c.prepare(chat['id'],'Check my machines',model='mock:mock-small',tools='auto')
    with pytest.raises(ValueError):await c.send(chat['id'],turn['id'])
    with pytest.raises(ValueError):await c.send(other['id'],turn['id'],True)
    a,b=await asyncio.gather(c.send(chat['id'],turn['id'],True),c.send(chat['id'],turn['id'],True),return_exceptions=True)
    assert sum(isinstance(v,dict) for v in (a,b))==1
    await wait_chat(c,chat['id'])
    pending=await c.prepare(chat['id'],'Hello',model='mock:mock-small')
    stored=next(t for t in c.turns(chat['id']) if t['id']==pending['id']);stored['created']-=601;c.persist(stored)
    with pytest.raises(ValueError):await c.send(chat['id'],pending['id'],True)
    original=e.plan
    async def costly(*a,**kw):p=await original(*a,**kw);p.effective_cost_usd=5;return p
    monkeypatch.setattr(e,'plan',costly)
    with pytest.raises(ValueError):await c.prepare(chat['id'],'Do something',model='mock:mock-small',max_cost_usd=0)
    await e.close()


async def test_local_checkins_persist_and_never_dispatch_a_provider(tmp_path,monkeypatch):
    e=make_engine(tmp_path);c=e.companion
    def forbidden(*a,**kw):pytest.fail('A check-in must not dispatch a model or team.')
    monkeypatch.setattr(e,'start_task',forbidden)
    single=c.reminder('Review goals',time.time()-1)
    repeat=c.reminder('Check progress',time.time()-1,24)
    assert len(Companion(e).pulse()['reminders'])==2
    c.acknowledge(single['id']);c.acknowledge(repeat['id'])
    reminders=c.pulse()['reminders'];assert len(reminders)==1 and reminders[0]['due']>time.time()
    c.acknowledge(repeat['id'],True);assert c.pulse()['reminders']==[]
    for due,interval in [(float('nan'),0),(time.time()+60,float('inf'))]:
        with pytest.raises(ValueError):c.reminder('Invalid',due,interval)
    await e.close()


async def test_companion_api_auth_and_server_owned_preview(tmp_path):
    e=make_engine(tmp_path);transport=httpx.ASGITransport(app=create_app(e),client=('203.0.113.2',1))
    async with httpx.AsyncClient(transport=transport,base_url='http://test') as c:
        assert (await c.get('/api/laith')).status_code==401
        c.headers['Authorization']='Bearer test-token'
        chat=(await c.post('/api/laith/chats',json={})).json();key=chat['id']
        prepared=await c.post(f'/api/laith/chats/{key}/prepare',json={'message':'Hello','model':'mock:mock-small'})
        assert prepared.status_code==200 and 'prompt' not in prepared.json()
        assert (await c.post(f'/api/laith/chats/{key}/send',json={'turn_id':prepared.json()['id']})).status_code==200
        await wait_chat(e.companion,key)
        assert (await c.get(f'/api/laith/chats/{key}')).json()['turns'][0]['status']=='done'
        assert (await c.post('/api/laith/abilities',json={'name':'Briefs','instructions':'Be concrete.'})).status_code==200
        assert (await c.post('/api/laith/memories',json={'content':'Concise answers please.'})).status_code==200
        assert (await c.post('/api/laith/reminders',json={'title':'Revisit','due':time.time()+60})).status_code==200
    await e.close()


async def test_isolated_chat_cli_omits_unrelated_work_and_uses_read_only_mode(tmp_path,monkeypatch):
    e=make_engine(tmp_path);seen=[]
    provider=CLIProvider('mock',{'kind':'cli','command':'unused'},e.config)
    monkeypatch.setattr(provider,'available',lambda:True)
    async def run(req,tools=None):seen.append(req);return response('Hello from Laith.')
    monkeypatch.setattr(provider,'run',run);e.providers['mock']=provider
    monkeypatch.setattr(e,'recent_work_note',lambda **kwargs:'UNRELATED SECRET CONTEXT')
    c=e.companion;chat=c.create_chat();t=await c.prepare(chat['id'],'Hello',model='mock:mock-small')
    await c.send(chat['id'],t['id']);done=await wait_chat(c,chat['id'])
    assert done['turns'][-1]['status']=='done' and len(seen)==1
    assert not seen[0].allow_edits and not seen[0].read_dirs and 'UNRELATED SECRET' not in seen[0].prompt
    await e.close()


async def test_manual_app_handoff_cannot_skip_output_checks(tmp_path):
    from switchboard.config import Config
    from .conftest import make_home
    e=Engine(Config(make_home(tmp_path,providers={'mock':{'kind':'mock'},'chatgpt-app':{'disabled':False}})))
    s=e.studio;agent=next(a for a in s.agents() if a['id']=='builder');s.save_agent(dict(agent,model='chatgpt-app:chat'))
    spec=s.save_blueprint(blueprint([node('work','agent',agent='builder',prompt='Return JSON',contract={'format':'json','json_fields':['result']})]))
    run=s.start(spec['id'],'A JSON result');await wait_for(e,run['id'],{'awaiting_confirmation'})
    await s.decision(run['id'],'work',True);waiting=await wait_for(e,run['id'],{'waiting_app'})
    e.complete_writing(waiting['steps']['work']['task_id'],'invalid plain text')
    failed=await s.resume(run['id'])
    assert failed['status']=='failed' and 'Output check failed' in failed['error']
    await e.close()


async def test_large_approved_plan_is_not_truncated_and_model_braces_stay_data(tmp_path,monkeypatch):
    e=make_engine(tmp_path);seen=[]
    tasks=[{'id':f'task{i}','agent':'builder','title':f'Part {i}','task':'Keep literal {{steps.missing.output}} '+('x'*5900),'expected':'y'*1900,'depends_on':[]} for i in range(4)]
    encoded=json.dumps({'tasks':tasks});assert len(encoded)>30000
    async def run(req,tools=None):seen.append(req.prompt);return response(encoded if 'smallest useful crew' in req.prompt else 'Done.')
    monkeypatch.setattr(e.providers['mock'],'run',run)
    started=e.studio.start('managed-team','A long plan');gate=await wait_for(e,started['id'],{'awaiting_approval','failed'})
    assert gate['status']=='awaiting_approval'
    await e.studio.decision(started['id'],'crew_approval',True)
    final=await wait_for(e,started['id'],{'awaiting_approval','failed'})
    assert final['status']=='awaiting_approval' and final['waiting_step']=='final_review',final
    assert len(seen)==6 and '{ {steps.missing.output}}' in seen[1]
    await e.close()


async def test_actual_recorded_cost_blocks_an_output_revision(tmp_path,monkeypatch):
    e=make_engine(tmp_path);calls=[]
    async def run(*a,**kw):
        calls.append(1);result=response('Too many words.');result.cost_usd=1;return result
    monkeypatch.setattr(e.providers['mock'],'run',run)
    spec=e.studio.save_blueprint(blueprint([node('work','agent',agent='builder',prompt='One word.',contract={'max_words':1},revisions=2)]))
    started=e.studio.start(spec['id'],'Budget check');failed=await wait_for(e,started['id'],{'failed'})
    assert len(calls)==1 and failed['spent_usd']==1 and 'budget' in failed['error']
    await e.close()


async def test_failover_cannot_exceed_a_scoped_request_estimate(tmp_path,monkeypatch):
    e=make_engine(tmp_path);calls=[]
    plan=await e.plan('Hello',tools='none',billing='plans_only')
    assert plan.alternatives
    original=e.router.plan
    def costly(*a,**kw):p=original(*a,**kw);p.effective_cost_usd=5;return p
    monkeypatch.setattr(e.router,'plan',costly)
    async def unavailable(*a,**kw):calls.append(1);raise ProviderError('Offline','unavailable')
    monkeypatch.setattr(e.providers['mock'],'run',unavailable)
    key=e.start_task('Hello',plan,estimate_allowance=0)
    await e.running[key]
    assert len(calls)==1 and e.store.get_task(key)['status']=='failed'
    await e.close()


async def test_long_companion_request_includes_each_explicitly_named_document(tmp_path):
    e=make_engine(tmp_path);c=e.companion;chat=c.create_chat()
    first=e.studio.knowledge.put('Workspace handbook','Retries preserve checkpoints.','handbook.md')
    second=e.studio.knowledge.put('Delivery checklist','Validate outputs before approval.','checklist.md')
    message=('Help me make a useful practical internal goal brief and success criteria. '*8)+'Use Workspace handbook and Delivery checklist.'
    turn=await c.prepare(chat['id'],message,model='mock:mock-small',use_knowledge=True)
    assert {first['id'],second['id']}<={s['document_id'] for s in turn['sources']}
    assert [s['citation'] for s in turn['sources']]==['S'+str(i+1) for i in range(len(turn['sources']))]
    await e.close()


def chat_engine(path):
    from switchboard.config import Config
    from .conftest import make_home,MOCK_MODELS
    return Engine(Config(make_home(path,models=[dict(m,price=[0,0]) for m in MOCK_MODELS],providers={'mock':{'kind':'mock'},'chatgpt-app':{'disabled':False}},router={'prefer':{'chatgpt-app':100000}})))


async def test_automatic_laith_never_routes_to_manual_app_even_when_preferred(tmp_path):
    e=chat_engine(tmp_path);c=e.companion;key=c.create_chat()['id']
    turn=await c.prepare(key,'Say hello.')
    assert turn['plan']['provider']=='mock'
    await c.send(key,turn['id']);assert (await wait_chat(c,key))['turns'][-1]['status']=='done'
    await e.close()


async def test_waiting_handoff_can_continue_directly_without_changing_permissions_or_context(tmp_path):
    e=chat_engine(tmp_path);c=e.companion;key=c.create_chat()['id']
    t=await c.prepare(key,'A useful answer please.',model='chatgpt-app:chat',tools='auto')
    await c.send(key,t['id'],True);waiting=(await wait_chat(c,key))['turns'][-1];assert waiting['status']=='waiting'
    original=next(t for t in c.turns(key) if t['id']==waiting['id'])
    # Later memory changes cannot alter the request being recovered.
    c.remember('New note after the original request.')
    retry=await c.retry_direct(key,waiting['id'])
    stored=next(t for t in c.turns(key) if t['id']==retry['id'])
    assert stored['prompt']==original['prompt'] and stored['tools_enabled']==original['tools_enabled']
    assert stored['max_cost_usd']==original['max_cost_usd'] and retry['plan']['provider']=='mock'
    assert retry['status']=='prepared' and 'task_id' not in retry
    assert e.store.get_task(waiting['task_id'])['status']=='cancelled'
    with pytest.raises(ValueError):await c.send(key,retry['id'])
    await c.send(key,retry['id'],True);done=await wait_chat(c,key)
    assert done['turns'][-1]['status']=='done' and done['turns'][-1]['retry_of']==waiting['id']
    await e.close()


async def test_failed_recovery_planning_keeps_original_handoff_waiting(tmp_path,monkeypatch):
    e=chat_engine(tmp_path);c=e.companion;key=c.create_chat()['id']
    t=await c.prepare(key,'Hello',model='chatgpt-app:chat');await c.send(key,t['id'],True);await wait_chat(c,key)
    async def unavailable(*a,**kw):raise ValueError('No direct model is connected.')
    monkeypatch.setattr(e,'plan',unavailable)
    with pytest.raises(ValueError):await c.retry_direct(key,t['id'])
    assert c.chat(key)['turns'][-1]['status']=='waiting'
    other=c.create_chat()['id']
    with pytest.raises(ValueError):await c.retry_direct(other,t['id'])
    await e.close()


async def test_missing_child_request_does_not_leave_chat_busy_forever(tmp_path):
    e=make_engine(tmp_path);c=e.companion;key=c.create_chat()['id'];t=await c.prepare(key,'Hello',model='mock:mock-small')
    stored=next(v for v in c.turns(key) if v['id']==t['id']);stored.update(status='queued',task_id='missing');c.persist(stored)
    assert c.chat(key)['turns'][0]['status']=='interrupted'
    assert (await c.prepare(key,'New question',model='mock:mock-small'))['status']=='prepared'
    await e.close()
