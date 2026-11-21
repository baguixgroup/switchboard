"""Capability permissions, durable dispatch and artifact delivery, without paid APIs."""
import asyncio
import base64
import io
import json
import uuid
import wave
import zipfile
from pathlib import Path

import httpx
import pytest

from switchboard.capabilities import adapters, machine
from switchboard.capabilities.documents import extract, transcript_document, transcript_vtt
from switchboard.capabilities.registry import validate_service
from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.web.app import create_app
from .conftest import MOCK_MODELS, make_home


def engine(path, **kwargs):
    return Engine(Config(make_home(path, models=[dict(m, price=[0, 0]) for m in MOCK_MODELS], **kwargs)))


def service(e, adapter='audio', **extra):
    value=validate_service(dict(id='test-'+adapter,adapter=adapter,base_url='http://localhost:9998',model='whisper-1',local=True,**extra))
    e.config.set_section('capability_services', [value]);return value


def asset(name='input.txt', data=b'The launch owner is Salma. Review on Friday.'):
    return {'name':name,'data_b64':base64.b64encode(data).decode()}


async def finished(e, key):
    for _ in range(300):
        job=e.capabilities.get(key)
        if job['status'] not in ('queued','running'):return job
        await asyncio.sleep(.01)
    pytest.fail('Capability did not finish')


def wav():
    buf=io.BytesIO()
    with wave.open(buf,'wb') as audio:audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0'*3200)
    return buf.getvalue()


@pytest.mark.parametrize('value',[
    {'base_url':'http://example.com'}, {'base_url':'https://user:password@example.com'},
    {'base_url':'https://example.com?api_key=secret'}, {'secret_env':'AWS_SECRET_ACCESS_KEY'},
    {'capabilities':['video.generate']}, {'capabilities':5}, {'unit_cost_usd':float('nan')},
    {'workflow':{'1':{'inputs':{'api_key':'secret'}}}}, {'workflow':{'1':'Bearer secret-token'}},
])
def test_connection_rejects_credentials_and_mismatched_contracts(value):
    with pytest.raises(ValueError):validate_service(dict({'id':'audio','adapter':'audio','base_url':'https://example.com','model':'whisper-1'},**value))


async def test_registry_distinguishes_setup_health_and_verified_delivery(tmp_path, monkeypatch):
    e=engine(tmp_path);s=service(e)
    row=next(r for r in e.capabilities.registry.rows() if r['provider']==s['id'])
    assert row['configured'] and not row['verified'] and row['status']=='configured'
    e.capabilities.registry.observed[row['id']]={'ok':True,'kind':'health','functional':False}
    assert not e.capabilities.registry.find(row['id'])['verified']
    async def reply(*args,**kwargs):return json.dumps({'text':'Decision: launch Friday.','segments':[{'start':0,'end':3,'text':'Decision: launch Friday.','speaker':'Salma'}]}).encode()
    monkeypatch.setattr(adapters,'request',reply)
    file=e.capabilities.upload('meeting.wav',wav());job=await e.capabilities.preview('Transcribe this meeting',asset_ids=[file['id']])
    e.capabilities.approve(job['id']);done=await finished(e,job['id']);assert done['status']=='done',done
    assert e.capabilities.registry.find(row['id'])['verified']
    assert {'transcript.txt','transcript.json','transcript.vtt'}=={f['name'] for f in done['files']}
    assert e.capabilities.meeting(job['id'])['transcript']['segments'][0]['speaker']=='Salma'
    await e.close()


async def test_document_pipeline_delivers_searchable_files_and_chat_history(tmp_path):
    e=engine(tmp_path);h=e.capabilities;chat=e.companion.create_chat('Document review')
    file=h.upload('notes.txt',b'Launch owner Salma. Confirmation requested for Friday.');job=await h.preview('Read this document',asset_ids=[file['id']],chat_id=chat['id'])
    assert job['status']=='awaiting_approval' and not e.running
    h.approve(job['id']);done=await finished(e,job['id']);assert done['status']=='done',done
    task=e.store.get_task(job['id']);assert Path(task['output_path'],'document.txt').read_text().startswith('Launch owner')
    assert task['status']=='done' and task['plan']['usage_cost_unknown'] is False
    assert e.companion.reconcile(e.companion.turns(chat['id'])[0])['status']=='done'
    doc=e.studio.knowledge.put('Meeting input',done['answer'],'notes.txt')
    assert e.studio.knowledge.search('Salma')[0]['document_id']==doc['id']
    with pytest.raises(ValueError):h.approve(job['id'])
    await e.close()


async def test_provider_text_uses_existing_router_and_records_usage_once(tmp_path):
    e=engine(tmp_path);h=e.capabilities
    job=await h.preview('Write a Python hello world program')
    assert job['task']=='text.generate' and not job['routing_plan']['alternatives']
    h.approve(job['id'],confirm_data_transfer=True);done=await finished(e,job['id'])
    assert done['status']=='done',done
    assert any(f['name']=='response.md' for f in done['files'])
    assert len(e.store._exec('SELECT * FROM usage WHERE task_id=?',(job['id'],)))==1
    await e.close()


async def test_unknown_cloud_cost_and_exact_snapshot_require_review(tmp_path, monkeypatch):
    e=engine(tmp_path);h=e.capabilities
    s=validate_service({'id':'cloud','adapter':'speech','model':'tts-model','base_url':'https://service.example/v1'})
    e.config.secrets[s['secret_env']]='test-private-key';e.config.set_section('capability_services',[s])
    job=await h.preview('Read aloud the launch notes');assert job['estimated_usd'] is None
    s['base_url']='https://different.example/v1';e.config.set_section('capability_services',[s])
    for kwargs in ({},{'confirm_data_transfer':True},{'allow_unpriced':True,'max_cost_usd':1}):
        with pytest.raises(ValueError):h.approve(job['id'],**kwargs)
    seen=[]
    async def reply(method,url,**kwargs):seen.append((url,kwargs));return wav()
    monkeypatch.setattr(adapters,'request',reply)
    h.approve(job['id'],1,True,True);done=await finished(e,job['id'])
    assert done['status']=='done' and seen[0][0]=='https://service.example/v1/audio/speech'
    assert done['usage']['cost_usd'] is None and e.store.get_task(job['id'])['plan']['usage_cost_unknown']
    assert 'test-private-key' not in json.dumps(done)
    await e.close()


async def test_expired_preview_and_modified_input_are_not_dispatched(tmp_path):
    e=engine(tmp_path);h=e.capabilities;file=h.upload('input.txt',b'Original content')
    job=await h.preview('Read document',asset_ids=[file['id']]);job['created']-=601;h.save(job)
    with pytest.raises(ValueError,match='expired'):h.approve(job['id'])
    (h.inputs/file['id']).write_bytes(b'Changed content')
    with pytest.raises(ValueError,match='changed'):await h.preview('Read document',asset_ids=[file['id']])
    assert not h.running
    await e.close()


async def test_agent_execution_deduplicates_and_refuses_unknown_outcome(tmp_path, monkeypatch):
    e=engine(tmp_path);calls=[];original=machine.local_execute
    async def count(*args):calls.append(1);return await original(*args)
    monkeypatch.setattr(machine,'local_execute',count)
    payload={'execution_id':'cap-'+uuid.uuid4().hex,'task':'document.extract','assets':[asset()]}
    first=await machine.execute(e.config,payload);second=await machine.execute(e.config,payload)
    assert first==second and len(calls)==1
    with pytest.raises(ValueError,match='different inputs'):await machine.execute(e.config,dict(payload,params={'text':'changed'}))
    async def fail(*args):raise asyncio.CancelledError()
    monkeypatch.setattr(machine,'local_execute',fail);payload['execution_id']='cap-'+uuid.uuid4().hex
    with pytest.raises(asyncio.CancelledError):await machine.execute(e.config,payload)
    monkeypatch.setattr(machine,'local_execute',count)
    with pytest.raises(ValueError,match='already started'):await machine.execute(e.config,payload)
    assert len(calls)==1
    await e.close()


async def test_remote_machine_transport_and_delivery(tmp_path):
    hub=engine(tmp_path/'hub');agent=engine(tmp_path/'agent',name='windows')
    hub.nodes.heartbeat({'node':'windows','capability_tools':{'documents':True}})
    h=hub.capabilities;file=h.upload('remote.txt',b'Processed on a connected machine.');job=await h.preview('Read document',task='document.extract',capability='document.extract:windows',asset_ids=[file['id']]);h.approve(job['id'],confirm_data_transfer=True)
    dispatch=await hub.nodes.next_job('windows',wait=2);assert dispatch.kind=='capability_execute'
    hub.nodes.ack(dispatch.id);result=await agent.executor.execute(dispatch.kind,dispatch.payload);hub.nodes.complete(dispatch.id,{'result':result})
    done=await finished(hub,job['id']);assert done['status']=='done',done
    assert 'connected machine' in done['answer']
    await hub.close();await agent.close()


async def test_comfy_checkpoint_recovery_polls_without_duplicate_submit(tmp_path,monkeypatch):
    e=engine(tmp_path);service(e,'comfyui',workflow={'1':{'inputs':{'text':'{{prompt}}'}}},capabilities=['image.generate'])
    job=await e.capabilities.preview('Create an image');job.update(status='running',checkpoint={'prompt_id':'original-job','phase':'rendering'});e.capabilities.save(job)
    requests=[]
    async def reply(method,url,**kwargs):
        requests.append((method,url))
        if '/history/' in url:return json.dumps({'original-job':{'status':{'status_str':'success'},'outputs':{'1':{'images':[{'filename':'result.png','type':'output'}]}}}}).encode()
        if '/view' in url:return base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        raise AssertionError('Unexpected submission')
    monkeypatch.setattr(adapters,'request',reply);e.capabilities.recover();done=await finished(e,job['id'])
    assert done['status']=='done',done
    assert all('/prompt' not in url for _,url in requests)
    assert Path(e.store.get_task(job['id'])['output_path'],'generated-1.png').is_file()
    await e.close()


async def test_unknown_submission_is_interrupted_and_cancel_never_global_interrupt(tmp_path,monkeypatch):
    e=engine(tmp_path);service(e,'comfyui',workflow={'1':{'inputs':{'text':'{{prompt}}'}}},capabilities=['image.generate'])
    job=await e.capabilities.preview('Create an image');job.update(status='running',checkpoint={'phase':'submitting'});e.capabilities.save(job);e.capabilities.recover()
    assert e.capabilities.get(job['id'])['status']=='interrupted' and not e.running
    job=e.capabilities.get(job['id']);job['checkpoint']['prompt_id']='known';e.capabilities.save(job)
    seen=[]
    async def reply(method,url,**kwargs):seen.append((url,kwargs['json']));return b'{}'
    import switchboard.capabilities.hub as module
    monkeypatch.setattr(module,'request',reply);await e.capabilities.cancel(job['id'])
    assert seen==[('http://localhost:9998/queue',{'delete':['known']})]
    await e.close()


async def test_service_adapters_validate_actual_artifacts_and_payloads(tmp_path,monkeypatch):
    e=engine(tmp_path);seen=[]
    async def reply(method,url,**kwargs):
        seen.append((url,kwargs))
        if url.endswith('/audio/transcriptions'):return b'{"text":"Meet at noon","segments":[{"start":0,"end":2,"text":"Meet at noon"}]}'
        if url.endswith('/audio/speech'):return wav()
        if url.endswith('/api/embed'):return b'{"embeddings":[[0.1,0.2,0.3]]}'
        if url.endswith('/api/generate'):return b'{"response":"Local answer"}'
        return b'{"label":"positive"}'
    monkeypatch.setattr(adapters,'request',reply)
    async def run(adapter,task,params,assets):return await adapters.execute_service(e.config,{'adapter':adapter,'base_url':'http://localhost:9998','local':True,'model':'whisper-1'},task,params,assets,{},lambda v:None)
    transcribed=await run('audio','audio.transcribe',{},[asset('recording.wav',wav())]);assert len(transcribed['artifacts'])==3 and seen[-1][1]['data']['response_format']=='verbose_json'
    spoken=await run('speech','audio.speak',{'text':'Hello'},[]);assert spoken['artifacts'][0]['name']=='speech.wav'
    embedded=await run('ollama','ml.embed',{'text':'Hello'},[]);assert embedded['result']['embeddings'][0]==[.1,.2,.3]
    inferred=await run('inference','ml.infer',{'inputs':{'text':'Good'}},[]);assert inferred['result']['label']=='positive'
    async def invalid(*args,**kwargs):return b'This is a description, not audio.'
    monkeypatch.setattr(adapters,'request',invalid)
    with pytest.raises(ValueError,match='format'):await run('speech','audio.speak',{'text':'Hello'},[])
    await e.close()


def test_document_formats_are_data_and_transcripts_preserve_alignment():
    assert extract('input.html',b'<p>Hello</p><script>steal()</script><style>hidden</style><p>World</p>')['text'].strip()=='Hello\nWorld'
    z=io.BytesIO()
    with zipfile.ZipFile(z,'w') as archive:archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Review required</w:t></w:r></w:p></w:document>')
    assert extract('input.docx',z.getvalue())['text']=='Review required'
    with pytest.raises(ValueError):extract('file.exe',b'not supported')
    with pytest.raises(ValueError):transcript_document({'text':'Words','segments':[{'start':5,'end':1}]})
    transcript=transcript_document({'text':'Hello','segments':[{'start':61.25,'end':62,'speaker':'A','text':'Hello'}]})
    assert '00:01:01.250 --> 00:01:02.000\nA: Hello' in transcript_vtt(transcript)


async def test_api_auth_upload_credentials_and_meeting_revisions(tmp_path):
    e=engine(tmp_path);app=create_app(e)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('203.0.113.2',5000)),base_url='http://testserver') as client:
        assert (await client.get('/api/capabilities')).status_code==401
        client.headers['Authorization']='Bearer test-token'
        assert (await client.post('/api/capabilities/inputs?name=..%2Fescape',content=b'data')).status_code==400
        r=await client.post('/api/capabilities/services',json={'config':{'id':'private','adapter':'speech','base_url':'https://speech.example/v1','model':'tts'},'credential':'do-not-log-this'})
        assert r.status_code==200
        assert 'do-not-log-this' not in json.dumps((await client.get('/api/capabilities')).json())
        assert 'do-not-log-this' not in e.config.path.read_text()
        assert 'do-not-log-this' in (e.config.home/'secrets.env').read_text()
        job={'id':'cap-notes','status':'done','task':'audio.transcribe','result':{'transcript':{'text':'Words','segments':[]}}}
        e.capabilities.save(job)
        folder=e.config.output_dir/'capabilities/cap-notes';folder.mkdir(parents=True)
        assert (await client.put('/api/capabilities/jobs/cap-notes/meeting',json={'notes':'Reviewed','revision':0})).status_code==200
        assert (await client.put('/api/capabilities/jobs/cap-notes/meeting',json={'notes':'Overwrite','revision':0})).status_code==400
        assert (await client.get('/api/capabilities/jobs/cap-notes/meeting')).json()['notes']=='Reviewed'
    await e.close()


async def test_live_voice_keeps_key_server_side_requires_billing_and_hangs_up(tmp_path,monkeypatch):
    import switchboard.web.capability_api as api
    e=engine(tmp_path);s=validate_service({'id':'voice','adapter':'realtime','base_url':'https://voice.example/v1','model':'realtime-model'})
    e.config.secrets[s['secret_env']]='protected-voice-key';e.config.set_section('capability_services',[s]);app=create_app(e)
    original=httpx.AsyncClient;seen=[]
    def transport(req):
        seen.append(req)
        return httpx.Response(201,headers={'location':'/v1/realtime/calls/call-123'},content=b'v=0\r\nanswer')
    async with original(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
        monkeypatch.setattr(api.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
        body={'capability':'voice.live:voice','sdp':'v=0\r\noffer','confirm':True,'confirm_data_transfer':True,'minutes':1}
        assert (await client.post('/api/capabilities/voice',json=body)).status_code==400
        r=await client.post('/api/capabilities/voice',json=dict(body,max_cost_usd=1,allow_unpriced=True));assert r.status_code==200,r.text
        data=r.json();assert 'protected-voice-key' not in r.text
        assert seen[0].headers['Authorization']=='Bearer protected-voice-key'
        assert (await client.put('/api/capabilities/voice/'+data['id']+'/transcript',json={'segments':[{'role':'user','text':'Hello'}]})).status_code==200
        hanging=[]
        async def hangup(method,url,**kwargs):hanging.append(url);return b''
        monkeypatch.setattr(api,'service_request',hangup)
        ended=await client.delete('/api/capabilities/voice/'+data['id']);assert ended.status_code==200
        assert ended.json()['status']=='closed' and ended.json()['transcript'][0]['text']=='Hello'
        assert hanging==['https://voice.example/v1/realtime/calls/call-123/hangup']
        assert e.store.get_task(data['id'])['plan']['usage_cost_unknown']
    await e.close()


def test_pdf_extracts_real_page_text():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=200)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 30 100 Td (Salma reviews Friday.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(content);data=io.BytesIO();writer.write(data)
    extracted=extract('notes.pdf',data.getvalue());assert 'Salma reviews Friday.' in extracted['text'] and extracted['pages'][0]['page']==1


async def test_local_ocr_and_ffmpeg_use_only_supported_recipes(monkeypatch):
    seen=[]
    monkeypatch.setattr(adapters,'which',lambda name:'/tools/'+name)
    async def process(argv,timeout):
        seen.append(argv)
        if 'tesseract' in str(argv[0]):return b'Local recognized text'
        Path(argv[-1]).write_bytes(wav());return b''
    monkeypatch.setattr(adapters,'process',process)
    ocr=await adapters.local_execute('document.ocr',{'language':'eng'},[asset('scan.png',b'input')]);assert ocr['text']=='Page 1\nLocal recognized text'
    converted=await adapters.local_execute('media.convert',{'recipe':'extract_audio'},[asset('clip.mp4',b'input')]);assert converted['artifacts'][0]['name']=='audio.wav'
    assert '-protocol_whitelist' in seen[-1] and 'file,pipe' in seen[-1] and '-nostdin' in seen[-1]
    with pytest.raises(ValueError):await adapters.local_execute('media.convert',{'recipe':'arbitrary-command'},[asset('clip.mp4',b'input')])
    with pytest.raises(ValueError):await adapters.local_execute('document.ocr',{'language':'eng;exec'},[asset('scan.png',b'input')])


async def test_meeting_draft_requires_provider_review_before_execution(tmp_path):
    e=engine(tmp_path);h=e.capabilities;h.save({'id':'cap-meeting','status':'done','task':'audio.transcribe','result':{'transcript':{'text':'Decision: launch Friday','segments':[]}}})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(e)),base_url='http://testserver') as client:
        r=await client.post('/api/capabilities/jobs/cap-meeting/notes-draft');assert r.status_code==200,r.text
        preview=r.json()['preview'];assert preview['status']=='awaiting_approval' and preview['notes_for']=='cap-meeting'
        assert not e.running and h.meeting('cap-meeting')['notes']==''
        assert (await client.post('/api/capabilities/jobs/'+preview['id']+'/approve',json={'confirm':True})).status_code==400
        approved=await client.post('/api/capabilities/jobs/'+preview['id']+'/approve',json={'confirm':True,'confirm_data_transfer':True});assert approved.status_code==200,approved.text
        assert (await finished(e,preview['id']))['status']=='done'
        assert h.meeting('cap-meeting')['notes']==''
    await e.close()


def test_desktop_relay_preserves_media_ranges_for_playback(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import switchboard.localproxy as proxy
    original=httpx.AsyncClient;seen=[]
    def upstream(request):
        seen.append(request)
        return httpx.Response(206,content=b'RIFF',headers={'content-type':'audio/wav','content-range':'bytes 0-3/32044','accept-ranges':'bytes'})
    monkeypatch.setattr(proxy.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(upstream),**kwargs))
    config=Config(make_home(tmp_path,role='agent'))
    with TestClient(proxy.build_proxy_app(config)) as client:
        response=client.get('/api/capabilities/jobs/cap-test/files/audio.wav',headers={'Range':'bytes=0-3'})
        assert response.status_code==206 and response.content==b'RIFF'
        assert response.headers['content-range']=='bytes 0-3/32044' and response.headers['accept-ranges']=='bytes'
        assert seen[0].headers['range']=='bytes=0-3' and seen[0].headers['authorization']=='Bearer test-token'
