"""Visible composer choices reach the reviewed plan without executing a preview."""
import asyncio
import httpx
import pytest
from switchboard.web.app import create_app
from .test_studio import make_engine


async def test_preview_preserves_drafts_and_effort_reaches_execution(tmp_path,monkeypatch):
    e=make_engine(tmp_path);c=e.companion;seen=[];original=e.providers['mock'].run
    async def record(request,*args,**kwargs):
        seen.append(request);return await original(request,*args,**kwargs)
    monkeypatch.setattr(e.providers['mock'],'run',record)
    preview=await c.prepare(None,'Explain a Python function',model='mock:mock-large',effort='high',preview=True)
    assert preview['plan']['effort']=='high' and c.chats()==[] and not seen and not e.store.recent_tasks()
    chat=c.create_chat();draft=await c.prepare(chat['id'],'Explain a Python function',model='mock:mock-large',effort='high')
    second=await c.prepare(chat['id'],'Explain a Python function',model='mock:mock-large',effort='low',preview=True)
    assert second['plan']['effort']=='low' and c.turns(chat['id'])[0]['status']=='prepared'
    sent=await c.send(chat['id'],draft['id'],True)
    await e.running[sent['task_id']]
    assert len(seen)==1 and seen[0].effort=='high'
    assert e.store.get_task(sent['task_id'])['plan']['effort']=='high'
    await e.close()


async def test_preview_api_auth_validation_and_capability_discovery_are_read_only(tmp_path):
    e=make_engine(tmp_path);app=create_app(e)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('203.0.113.2',5000)),base_url='http://testserver') as client:
        assert (await client.post('/api/laith/preview',json={'message':'Hello there'})).status_code==401
        client.headers['Authorization']='Bearer test-token'
        request={'message':'Explain a Python function','model':'mock:mock-large','effort':'xhigh','billing':'plans_only'}
        response=await client.post('/api/laith/preview',json=request)
        assert response.status_code==200 and response.json()['plan']['effort']=='xhigh'
        assert (await client.post('/api/laith/preview',json=dict(request,effort='unsupported'))).status_code==422
        upload=e.capabilities.upload('notes.txt',b'Local document data')
        response=await client.post('/api/laith/preview',json={'message':'Read this document','asset_ids':[upload['id']]})
        assert response.json()['capability']['task']=='document.extract'
        assert e.capabilities.recent()==[] and e.companion.chats()==[] and not e.running
        chat=e.companion.create_chat()
        response=await client.post('/api/laith/chats/'+chat['id']+'/prepare',json=request)
        assert response.status_code==200 and response.json()['plan']['effort']=='xhigh'
    await e.close()


async def test_preview_does_not_run_paid_classifier_or_accept_invalid_effort(tmp_path,monkeypatch):
    e=make_engine(tmp_path);e.config.set_section('router',{'classifier':'llm'})
    async def never(*args,**kwargs):pytest.fail('Typing must never run a classifier model.')
    monkeypatch.setattr(e,'plan',never)
    with pytest.raises(ValueError,match='classifier'):await e.companion.prepare(None,'A draft message',preview=True)
    with pytest.raises(ValueError,match='effort'):await e.companion.prepare(None,'A draft message',preview=True,effort='invalid')
    assert e.companion.chats()==[] and not e.store.recent_tasks()
    await e.close()
