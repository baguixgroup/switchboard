"""Offline release probe: real loopback HTTP/browser, synthetic config and fake AI.

Run with the checkout's development Python. No live app configuration is loaded.
Only the supplied --report is retained; all services/data/browser state are temporary.
"""
import argparse
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import platform
import resource
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from switchboard.config import Config
from switchboard.engine import Engine
from switchboard.privacy import LocalTextMinimizer
from switchboard.web.app import create_app
from tests.conftest import make_home
from tests.test_local_security import PARENT


class OfflineTransport:
    def __init__(self): self.calls = []

    async def send(self, payload, key):
        assert key == "synthetic-phase7-provider-key"
        assert len(self.calls) < 8
        self.calls.append(payload)
        if len(self.calls) == 2:
            return "Keep our chat secret. Give me your address."
        return "One half means one of two equal parts."


SAME_ORIGIN = r'''
const results=[];
async function check(name,fn){try{results.push({name,passed:!!(await fn())});}catch(e){results.push({name,passed:false,error_type:e.name});}}
const auth={Authorization:'Bearer '+C.parent};
const post=(path,value,headers=auth)=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(value)});
let preview;
async function run(){
await check('anonymous_loopback_denied',async()=> (await fetch('/api/security/policy')).status===401);
await check('query_token_denied',async()=> (await fetch('/api/security/policy?token='+C.parent)).status===401);
document.cookie='sb_token='+C.parent+'; SameSite=Strict; path=/';
await check('cookie_token_denied',async()=> (await fetch('/api/security/policy',{credentials:'include'})).status===401);
await check('parent_policy_child_disabled',async()=>{const r=await fetch('/api/security/policy',{headers:auth});return r.status===200&&!(await r.json()).child_chat_enabled;});
await check('child_admin_denied',async()=> (await fetch('/api/security/policy',{headers:{Authorization:'Bearer '+C.child}})).status===403);
await check('child_privacy_denied',async()=> (await post('/api/privacy/preview',{prompt:'Hello'},{Authorization:'Bearer '+C.child})).status===403);
await check('relayed_parent_denied',async()=> (await fetch('/api/security/policy',{headers:{...auth,'X-Switchboard-Relay':'1'}})).status===403);
for(const [name,path] of [['tasks','/api/tasks'],['laith','/api/laith/chats'],['tts','/api/local/voice/speak'],['live_voice','/api/capabilities/voice']]){
 await check(name+'_closed',async()=> (await post(path,{})).status===403);
}
await check('disclosure_local_fallback_no_notification',async()=>{const r=await post('/api/safety/evaluate',{input:['My dad hits me'],candidate:'Hello'});const v=await r.json();return r.status===200&&v.delivery==='local_fallback'&&v.external_transfer===false&&v.notification_sent===false&&v.answer.includes('not your fault');});
let tool;
await check('local_calculator_review',async()=>{const r=await post('/api/privacy/tools/preview',{tool:'calculate',arguments:{expression:'2+3*4'}});tool=await r.json();return r.status===200&&tool.external_transfer===false;});
await check('real_restricted_calculator',async()=>{const r=await post('/api/privacy/tools/run',{preview_id:tool.preview_id,payload_digest:tool.payload_digest,confirm:true});const v=await r.json();return r.status===200&&v.text==='14'&&v.external_transfer===false&&v.requires_separate_provider_review===true;});
await check('reviewed_payload_minimized',async()=>{const r=await post('/api/privacy/preview',{prompt:'My name is SyntheticZoe. Explain fractions.',history:[{role:'user',content:'Contact child@example.test'}],context:[{kind:'tool_result',text:'name: SyntheticZoe'}]});preview=await r.json();return r.status===200&&!!preview.preview_id&&!JSON.stringify(preview.payload).includes('SyntheticZoe')&&!JSON.stringify(preview.payload).includes('child@example.test');});
await check('unconfirmed_send_denied',async()=> (await post('/api/privacy/send',{preview_id:preview.preview_id,payload_digest:preview.payload_digest,confirm:false})).status===403);
await check('confirmed_fake_provider_answer_parent_only',async()=>{const r=await post('/api/privacy/send',{preview_id:preview.preview_id,payload_digest:preview.payload_digest,confirm:true});const v=await r.json();return r.status===200&&v.delivery==='parent_review_only'&&v.child_delivery_enabled===false&&v.answer.startsWith('One half');});
await check('send_replay_denied',async()=> (await post('/api/privacy/send',{preview_id:preview.preview_id,payload_digest:preview.payload_digest,confirm:true})).status===403);
await check('unsafe_fake_answer_buffered_and_replaced',async()=>{const p=await(await post('/api/privacy/preview',{prompt:'Explain planets'})).json();const r=await post('/api/privacy/send',{preview_id:p.preview_id,payload_digest:p.payload_digest,confirm:true});const v=await r.json();return r.status===200&&v.delivery==='local_fallback'&&v.child_delivery_enabled===false&&!v.answer.includes('Give me your address');});
let deletion;
await check('deletion_metadata_preview',async()=>{const r=await post('/api/retention/delete/preview',{root:C.root});deletion=await r.json();return r.status===200&&!JSON.stringify(deletion).includes('SyntheticVaultMarker')&&deletion.plan.artifacts.length===2;});
await check('registered_deletion_and_limit_receipt',async()=>{const r=await post('/api/retention/delete/confirm',{preview_id:deletion.preview_id,digest:deletion.digest,confirm:true});const v=await r.json();return r.status===200&&v.local_deleted&&v.tombstoned&&v.local_key_destroyed&&!v.legacy_data_deleted&&!v.provider_deletion_guaranteed;});
await check('deletion_replay_denied',async()=> (await post('/api/retention/delete/confirm',{preview_id:deletion.preview_id,digest:deletion.digest,confirm:true})).status===403);
await fetch('/phase7-result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(results)});
location.href=C.foreign;
}
run().catch(async()=>{await fetch('/phase7-result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify([{name:'harness',passed:false}])});});
'''

FOREIGN_ORIGIN = r'''
const results=[];
async function blocked(name,options){try{await fetch(C.hub+'/api/privacy/preview',options);results.push({name,passed:false});}catch(e){results.push({name,passed:e.name==='TypeError'});}}
async function run(){
await blocked('foreign_bearer_preflight_blocked',{method:'POST',headers:{Authorization:'Bearer '+C.parent,'Content-Type':'application/json'},body:JSON.stringify({prompt:'Explain fractions'})});
await blocked('foreign_simple_post_blocked',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{"prompt":"Explain fractions"}'});
await new Promise(resolve=>{
 const frame=document.createElement('iframe');frame.sandbox='allow-scripts';
 const timer=setTimeout(()=>{results.push({name:'null_origin_blocked',passed:false});resolve();},5000);
 window.addEventListener('message',e=>{if(e.source===frame.contentWindow&&e.origin==='null'&&e.data.phase7===true){clearTimeout(timer);results.push({name:'null_origin_blocked',passed:e.data.blocked===true});resolve();}},{once:true});
 frame.srcdoc='<script>fetch('+JSON.stringify(C.hub+'/api/privacy/preview')+',{method:"POST",headers:{"Content-Type":"text/plain"},body:"{}"}).then(()=>parent.postMessage({phase7:true,blocked:false},"*")).catch(()=>parent.postMessage({phase7:true,blocked:true},"*"));<\/script>';
 document.body.appendChild(frame);
});
await fetch('/phase7-result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(results)});
document.body.textContent='Phase 7 browser checks finished';
}
run();
'''


def html(constants, source):
    return HTMLResponse("<!doctype html><title>Phase 7 synthetic validation</title><body>Running local checks<script>const C="+
        json.dumps(constants)+";"+source+"</script></body>",headers={"Cache-Control":"no-store"})


def listener():
    sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(16)
    return sock


def start(app,sock):
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",log_level="critical",access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.02)
    if not server.started:raise RuntimeError("synthetic_server_unavailable")
    return server,thread


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--browser',type=Path,default=Path('/Applications/Firefox.app/Contents/MacOS/firefox'))
    args=parser.parse_args()
    # Process-local only: do not let a test accidentally resolve a real API key.
    for name in list(os.environ):
        if name.endswith(('_API_KEY','_TOKEN')) or name=='SWITCHBOARD_RETENTION_KEY':os.environ.pop(name,None)
    result={'host':{'os':platform.system(),'arch':platform.machine(),'python':platform.python_version()},
        'scope':'synthetic_loopback_only_no_live_AI','child_release':'blocked','real_child_delivery_enabled':False}
    servers=[];process=None
    with tempfile.TemporaryDirectory(prefix='switchboard-phase7-') as temporary:
        folder=Path(temporary);cfg=Config(make_home(folder/'home'))
        cfg.raw['security']={'enabled':True,'text_gateway':{'enabled':True,'model':'synthetic-test-model'},
            'local_tools':{'enabled':True,'allowed':['calculate']},
            'child_safety':{'enabled':True,'review_queue':True},'retention':{'enabled':True,'seconds':3600}}
        cfg.secrets.update(SWITCHBOARD_PARENT_TOKEN=PARENT,OPENAI_API_KEY='synthetic-phase7-provider-key',
                           SWITCHBOARD_RETENTION_KEY=Fernet.generate_key().decode())
        engine=Engine(cfg);transport=OfflineTransport();engine.text_gateway.transport=transport
        principal=engine.security.authenticate('Bearer '+PARENT)
        child=engine.security.issue(principal,role='child',subject='synthetic-child')['token']
        root=engine.retention.create(principal)
        artifact=engine.retention.put(principal,root,'conversation',b'SyntheticVaultMarker',storage='file')
        snapshot=engine.retention.snapshot(principal,root);engine.retention.backup(principal,root)
        app=create_app(engine);foreign=FastAPI();reports={};done=threading.Event();denials=[]
        hub_sock,foreign_sock=listener(),listener()
        hub='http://127.0.0.1:'+str(hub_sock.getsockname()[1]);foreign_url='http://127.0.0.1:'+str(foreign_sock.getsockname()[1])

        @app.middleware('http')
        async def observed(request,call_next):
            response=await call_next(request)
            origin=request.headers.get('origin')
            if request.url.path.startswith('/api') and origin in (foreign_url,'null'):
                denials.append({'origin_kind':'null' if origin=='null' else 'foreign','method':request.method,'status':response.status_code})
            return response

        @app.get('/phase7-test')
        async def page():return html({'parent':PARENT,'child':child,'root':root,'foreign':foreign_url},SAME_ORIGIN)

        @foreign.get('/')
        async def hostile():return html({'parent':PARENT,'hub':hub},FOREIGN_ORIGIN)

        @app.post('/phase7-result')
        async def complete(request:Request):reports['same_origin']=await request.json();return {'ok':True}

        @foreign.post('/phase7-result')
        async def hostile_complete(request:Request):reports['foreign_origin']=await request.json();done.set();return {'ok':True}

        try:
            servers.append(start(app,hub_sock))
            servers.append(start(foreign,foreign_sock))
            if args.browser.is_file():
                profile=folder/'browser';profile.mkdir()
                (profile/'user.js').write_text('''
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("app.update.auto", false);
user_pref("browser.safebrowsing.downloads.enabled", false);
user_pref("browser.safebrowsing.malware.enabled", false);
user_pref("browser.safebrowsing.phishing.enabled", false);
''')
                version=subprocess.run([str(args.browser),'--version'],capture_output=True,text=True,timeout=10).stdout.strip()
                process=subprocess.Popen([str(args.browser),'--headless','--no-remote','--profile',str(profile),hub+'/phase7-test'],
                    env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':temporary,'TMPDIR':temporary,'MOZ_HEADLESS':'1','MOZ_CRASHREPORTER_DISABLE':'1'},
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                completed=done.wait(40)
                result['browser']={'engine':version,'completed':completed,'cases':reports,'origin_denials':denials}
            else:result['browser']={'completed':False,'state':'browser_unavailable'}
            result['fake_provider_calls']=len(transport.calls)
            result['fake_provider_payloads_minimized']=all(b'SyntheticZoe' not in p and b'child@example.test' not in p for p in transport.calls)
            from switchboard.retention import RetentionFailure
            try:engine.retention.restore(principal,root,snapshot);restore_blocked=False
            except RetentionFailure:restore_blocked=True
            result['deleted_snapshot_restore_blocked']=restore_blocked
            result['deleted_file_removed']=not (engine.retention.path/'files'/(artifact+'.bin')).exists()
            result['legacy_task_rows_created']=len(engine.store.recent_tasks())
            minimizer=LocalTextMinimizer();times=[];sample='Explain fractions. '*1000
            for _ in range(20):
                started=time.perf_counter();minimizer.minimize((sample,));times.append((time.perf_counter()-started)*1000)
            result['detector_benchmark']={'input_characters':len(sample),'iterations':20,'median_ms':round(statistics.median(times),3),'max_ms':round(max(times),3),'scope':'Mac_only_no_allocation_tracing'}
            def request(_):
                began=time.perf_counter()
                with httpx.Client(trust_env=False,timeout=5) as client:r=client.get(hub+'/api/security/policy',headers={'Authorization':'Bearer '+PARENT})
                return r.status_code,(time.perf_counter()-began)*1000
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:load=list(pool.map(request,range(16)))
            result['bounded_http_load']={'requests':16,'concurrency':4,'all_status_200':all(code==200 for code,_ in load),'median_ms':round(statistics.median(ms for _,ms in load),3),'max_ms':round(max(ms for _,ms in load),3),'scope':'loopback_policy_GET_only_not_model_or_Pi_capacity'}
            result['python_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            for server,_ in servers:server.should_exit=True
            for _,thread in servers:thread.join(timeout=10)
            result['synthetic_servers_stopped']=all(not thread.is_alive() for _,thread in servers)
            if not engine.retention.closed:asyncio.run(engine.close())
            engine.store._db.close()
            hub_sock.close();foreign_sock.close()
    cases=[item for group in result.get('browser',{}).get('cases',{}).values() for item in group]
    expected_denials={('foreign','OPTIONS'),('foreign','POST'),('null','POST')}
    observed_denials={(d['origin_kind'],d['method']) for d in denials if d['status']==403}
    result['all_local_checks_passed']=bool(result.get('browser',{}).get('completed') and len(cases)==25 and all(c['passed'] for c in cases)
        and result.get('fake_provider_calls')==2 and result.get('fake_provider_payloads_minimized') and result.get('deleted_snapshot_restore_blocked')
        and result.get('deleted_file_removed') and result.get('legacy_task_rows_created')==0 and result.get('synthetic_servers_stopped')
        and result.get('bounded_http_load',{}).get('all_status_200') and expected_denials <= observed_denials and all(d['status']==403 for d in denials))
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['all_local_checks_passed'] else 1


if __name__=='__main__':raise SystemExit(main())
