import asyncio
import copy
from pathlib import Path

import httpx
import pytest

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.providers import ProviderError
from switchboard.studio import validate_graph
from switchboard.web.app import create_app
from .conftest import MOCK_MODELS, make_home


def make_engine(path):
    models=[dict(m,price=[0,0]) for m in MOCK_MODELS]
    return Engine(Config(make_home(path,models=models)))


async def wait_for(engine,key,statuses):
    for _ in range(200):
        run=engine.studio.run(key)
        if run['status'] in statuses:return run
        await asyncio.sleep(.02)
    pytest.fail('Team did not reach expected checkpoint: '+str(engine.studio.run(key)))


def blueprint(nodes,**extra):
    return dict(name='Test team',billing='plans_only',max_cost_usd=0,nodes=nodes,**extra)


def node(id,type,depends=(),**config):
    return dict(id=id,name=id,type=type,depends_on=list(depends),config=config)


def test_document_library_starts_empty_and_preserves_user_documents(tmp_path):
    e=make_engine(tmp_path);k=e.studio.knowledge
    assert k.list()==[]
    handbook=k.put('Workspace handbook','Workflow retries preserve completed checkpoints and approvals.','handbook.md')
    results=k.search('workflow retries approval')
    assert results and all(r['citation'].startswith('S') and r['start_line']>=1 for r in results)
    document=k.put('Café notes','café chemistry\nPhotosynthesis uses chlorophyll.','local.txt')
    found=k.search('chlorophyll',documents=[document['id']])
    assert len(found)==1 and found[0]['document_id']==document['id']
    assert all(re.search(r'\bor\b',(r['text']+' '+r['title']).lower()) for r in k.search('" OR 1=1 --'))
    assert len(k.list())==2
    k.delete(handbook['id']);assert len(k.list())==1
    from switchboard.knowledge import Knowledge
    assert [d['id'] for d in Knowledge(e.store).list()]==[document['id']]


@pytest.mark.parametrize('nodes',[
    [node('a','input',['a'])],
    [node('a','input',['missing'])],
    [node('a','input'),node('a','input')],
    [node('a','shell',command='rm -rf /')],
    [node('a','input',text='{{steps.missing.output}}')],
    [node('a','save',filename='../escape.txt')],
    [node('a','save',filename='C:\\secret.txt')],
    [node('a','agent',agent='builder',tools='auto',retries=1)],
])
def test_invalid_graphs_fail_before_any_execution(nodes):
    with pytest.raises(ValueError):validate_graph(blueprint(nodes))


async def test_team_goal_preserves_results_and_pauses_for_human_review(tmp_path):
    e=make_engine(tmp_path);s=e.studio
    goal=s.save_goal({'title':'Write a reliable plan','brief':'Explain workflow retries and approvals','success':'A concrete deliverable with review','blueprint_id':'agent-team'})
    original=s.agents()[0]
    run=s.start_goal(goal['id'])
    s.save_agent(dict(original,instructions='Changed after run started'))
    assert run['agents'][original['id']]['instructions']!=s.agents()[0]['instructions']
    review=await wait_for(e,run['id'],{'awaiting_approval','failed'})
    assert review['status']=='awaiting_approval',review
    assert all(review['steps'][id]['status']=='done' for id in ['sources','research','plan','build','review'])
    assert not list(Path(review['output_path']).rglob('handover.md'))
    task_ids=[review['steps'][id]['task_id'] for id in ['research','plan','build','review']]
    await s.decision(run['id'],'approval',True)
    done=await wait_for(e,run['id'],{'done','failed'})
    assert done['status']=='done' and len(set(task_ids))==4
    assert [done['steps'][id]['task_id'] for id in ['research','plan','build','review']]==task_ids
    assert (Path(done['output_path'])/'files/handover.md').is_file()
    with pytest.raises(ValueError):await s.decision(run['id'],'approval',True)
    assert s.goals()[0]['status']=='done'
    await e.close()


async def test_rejection_never_saves_or_resumes(tmp_path):
    e=make_engine(tmp_path);s=e.studio
    run=s.start('knowledge-brief','Workflow retry settings')
    await wait_for(e,run['id'],{'awaiting_approval'})
    await s.decision(run['id'],'approval',False)
    assert s.run(run['id'])['status']=='rejected'
    with pytest.raises(ValueError):await s.resume(run['id'],True)
    assert not list(Path(run['output_path']).rglob('source-brief.md'))
    await e.close()


async def test_checkpoint_survives_hub_restart_and_approval_remains_pending(tmp_path):
    e=make_engine(tmp_path);run=e.studio.start('knowledge-brief','workflow retries')
    before=await wait_for(e,run['id'],{'awaiting_approval'})
    await e.close()
    reopened=make_engine(tmp_path);reopened.studio.recover()
    checkpoint=reopened.studio.run(run['id'])
    assert checkpoint['steps']['sources']==before['steps']['sources'] and checkpoint['status']=='awaiting_approval'
    await reopened.studio.decision(run['id'],'approval',True)
    done=await wait_for(reopened,run['id'],{'done'})
    assert done['steps']['sources']['attempts']==1
    await reopened.close()


async def test_conditions_skip_the_wrong_branch(tmp_path):
    e=make_engine(tmp_path);s=e.studio
    yes=node('yes','save',['check'],filename='yes.txt',text='accepted');yes['when']={'step':'check','equals':True}
    no=node('no','save',['check'],filename='no.txt',text='wrong');no['when']={'step':'check','equals':False}
    spec=s.save_blueprint(blueprint([node('check','condition',value='{{input}}',operator='contains',expected='hello'),yes,no]))
    run=s.start(spec['id'],'hello world');done=await wait_for(e,run['id'],{'done','failed'})
    assert done['status']=='done' and done['steps']['no']['status']=='skipped'
    assert (Path(done['output_path'])/'files/yes.txt').read_text()=='accepted'
    assert not (Path(done['output_path'])/'files/no.txt').exists()
    await e.close()


async def test_read_only_retry_does_not_rerun_completed_steps(tmp_path,monkeypatch):
    e=make_engine(tmp_path);s=e.studio
    agent=next(a for a in s.agents() if a['id']=='builder');s.save_agent(dict(agent,model='mock:mock-small'))
    original=e.providers['mock'].run;calls=[]
    async def flaky(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:raise ProviderError('Try again','error')
        return await original(*args,**kwargs)
    monkeypatch.setattr(e.providers['mock'],'run',flaky)
    spec=s.save_blueprint(blueprint([node('context','input',text='Kept checkpoint'),node('work','agent',['context'],agent='builder',prompt='{{steps.context.output}}',tools='none',retries=1)]))
    run=s.start(spec['id'],'input');done=await wait_for(e,run['id'],{'done','failed'})
    assert done['status']=='done',done
    assert len(calls)==2 and done['steps']['context']['attempts']==1 and done['steps']['work']['attempts']==2
    assert len(done['steps']['work']['tasks'])==2
    await e.close()


async def test_budget_admission_does_not_send_a_paid_request(tmp_path,monkeypatch):
    e=make_engine(tmp_path);s=e.studio;original=e.plan
    async def expensive(*args,**kwargs):
        p=await original(*args,**kwargs);p.effective_cost_usd=2;return p
    monkeypatch.setattr(e,'plan',expensive)
    async def forbidden(*a,**kw):pytest.fail('Budget must stop before provider call')
    monkeypatch.setattr(e.providers['mock'],'run',forbidden)
    spec=s.save_blueprint(blueprint([node('work','agent',agent='builder',prompt='{{input}}')]))
    run=s.start(spec['id'],'input');failed=await wait_for(e,run['id'],{'failed'})
    assert 'budget' in failed['error'] and not e.store.recent_tasks()
    await e.close()


async def test_stop_during_request_prevents_later_steps(tmp_path,monkeypatch):
    e=make_engine(tmp_path);s=e.studio;entered=asyncio.Event();release=asyncio.Event();original=e.providers['mock'].run
    async def slow(*args,**kwargs):entered.set();await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(e.providers['mock'],'run',slow)
    spec=s.save_blueprint(blueprint([node('work','agent',agent='builder',prompt='{{input}}'),node('save','save',['work'],filename='should-not-exist.md',text='{{steps.work.output}}')]))
    run=s.start(spec['id'],'input');await asyncio.wait_for(entered.wait(),5)
    s.stop(run['id']);release.set();await asyncio.gather(*list(s.jobs.values()))
    assert s.run(run['id'])['status']=='cancelled' and not list(Path(run['output_path']).rglob('should-not-exist.md'))
    await e.close()


async def test_app_handoff_can_finish_and_resume_team(tmp_path):
    cfg=Config(make_home(tmp_path,providers={'mock':{'kind':'mock'},'chatgpt-app':{'disabled':False}}))
    e=Engine(cfg);s=e.studio
    role=next(a for a in s.agents() if a['id']=='builder');s.save_agent(dict(role,model='chatgpt-app:chat'))
    spec=s.save_blueprint(blueprint([node('work','agent',agent='builder',prompt='{{input}}'),node('save','save',['work'],filename='answer.md',text='{{steps.work.output}}')]))
    run=s.start(spec['id'],'Question');waiting=await wait_for(e,run['id'],{'awaiting_confirmation','failed'})
    assert waiting['status']=='awaiting_confirmation'
    await s.decision(run['id'],'work',True)
    app=await wait_for(e,run['id'],{'waiting_app','failed'})
    assert app['status']=='waiting_app'
    child=app['steps']['work']['task_id'];e.complete_writing(child,'User-provided answer')
    await s.resume(run['id']);done=await wait_for(e,run['id'],{'done','failed'})
    assert done['status']=='done' and done['steps']['work']['task_id']==child
    assert (Path(done['output_path'])/'files/answer.md').read_text()=='User-provided answer'
    await e.close()


async def test_studio_api_auth_validation_and_download(tmp_path):
    e=make_engine(tmp_path);s=e.studio
    transport=httpx.ASGITransport(app=create_app(e),client=('203.0.113.2',1234))
    async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as client:
        assert (await client.get('/api/studio')).status_code==401
        client.headers['Authorization']='Bearer test-token'
        assert (await client.get('/api/studio')).status_code==200
        document=await client.post('/api/studio/knowledge',json={'title':'Approval handbook','text':'Approvals require a human review before delivery.','source':'handbook.md'})
        assert document.status_code==200
        bad=await client.post('/api/studio/blueprints',json=blueprint([node('bad','save',filename='../escape')]))
        assert bad.status_code==400
        run=(await client.post('/api/studio/runs',json={'blueprint_id':'knowledge-brief','input':'approvals'})).json()
        await wait_for(e,run['id'],{'awaiting_approval'})
        assert (await client.get('/api/studio/runs/'+run['id']+'/file',params={'path':'source-brief.md'})).status_code==404
        approved=await client.post('/api/studio/runs/'+run['id']+'/decision',json={'step':'approval','approve':True})
        assert approved.status_code==200
        await wait_for(e,run['id'],{'done'})
        assert (await client.get('/api/studio/runs/'+run['id']+'/file',params={'path':'../secret'})).status_code==404
        file=await client.get('/api/studio/runs/'+run['id']+'/file',params={'path':'source-brief.md'})
        assert file.status_code==200 and 'Approval handbook' in file.text
    await e.close()


async def test_recovery_marks_orphan_child_and_requires_explicit_retry(tmp_path):
    e=make_engine(tmp_path);s=e.studio
    # Simulate an on-disk run whose old worker disappeared, without starting a new request.
    spec=s.blueprint('agent-team');now=__import__('time').time()
    run={'id':'run-orphan','goal':'Orphan','input':'input','blueprint':spec,'agents':{a['id']:a for a in s.agents()},
         'created':now,'updated':now,'status':'running','spent_usd':0,'steps':{n['id']:{'status':'pending','attempts':0} for n in spec['nodes']},'events':[],'output_path':str(tmp_path/'out/recovered')}
    run['steps']['sources']={'status':'done','attempts':1,'output':'Kept evidence'}
    run['steps']['research']={'status':'running','attempts':1,'task_id':'orphan-child'}
    e.store.save_task('orphan-child','running','old request')
    s.persist(run);s.recover()
    assert s.run(run['id'])['status']=='interrupted'
    assert e.store.get_task('orphan-child')['status']=='failed'
    with pytest.raises(ValueError):await s.resume(run['id'])
    await s.resume(run['id'],True)
    completed=await wait_for(e,run['id'],{'awaiting_approval','failed'})
    assert completed['status']=='awaiting_approval' and completed['steps']['sources']['attempts']==1
    assert completed['steps']['research']['attempts']==2
    await e.close()


def test_blueprint_revision_prevents_stale_edits(tmp_path):
    e=make_engine(tmp_path);s=e.studio
    original=s.blueprint('agent-team');changed=s.save_blueprint(dict(original,name='Updated team'))
    assert changed['revision']==original['revision']+1
    with pytest.raises(ValueError):s.save_blueprint(dict(original,name='Stale overwrite'))
    assert s.blueprint('agent-team')['name']=='Updated team'
