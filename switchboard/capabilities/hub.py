"""SQLite-backed approvals, checkpoints and delivery shared with task history."""
from __future__ import annotations
import asyncio,base64,hashlib,json,math,re,time,uuid
from pathlib import Path
from .registry import Registry,services
from .adapters import artifact,decode_asset,execute_service,headers,local_execute,request,MAX_BYTES,validate_media
from ..outputs import safe_join

ACTIVE={'queued','running'}
def uid(prefix):return prefix+'-'+uuid.uuid4().hex

def bounded_json(value,limit=200000):
    raw=json.dumps(value,allow_nan=False,ensure_ascii=False)
    if len(raw)>limit:raise ValueError('Request is too large.')
    return raw

class CapabilityHub:
    def __init__(self,engine):
        self.engine=engine;self.db=engine.store;self.registry=Registry(engine);self.running={};self.slots=asyncio.Semaphore(2)
        for sql in [
            'CREATE TABLE IF NOT EXISTS capability_assets(id TEXT PRIMARY KEY,payload TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS capability_jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS capability_meetings(id TEXT PRIMARY KEY,payload TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS capability_voice(id TEXT PRIMARY KEY,payload TEXT NOT NULL)',
        ]:self.db._exec(sql)
        self.inputs=engine.config.db_path.parent/'capability-inputs';self.inputs.mkdir(mode=0o700,exist_ok=True)
    def save(self,job):self.db._exec('INSERT OR REPLACE INTO capability_jobs VALUES(?,?)',(job['id'],bounded_json(job,3_000_000)))
    def get(self,key):
        rows=self.db._exec('SELECT payload FROM capability_jobs WHERE id=?',(key,))
        if not rows:raise ValueError('Request not found.')
        return json.loads(rows[0]['payload'])
    def recent(self):return [json.loads(r['payload']) for r in self.db._exec('SELECT payload FROM capability_jobs ORDER BY rowid DESC LIMIT 100')]
    def upload(self,name,data):
        if not re.fullmatch(r'[\w .-]{1,120}',name) or Path(name).name!=name or name in ('.','..'):raise ValueError('Use a simple filename.')
        if not data or len(data)>MAX_BYTES:raise ValueError('Choose a nonempty file up to 20 MB.')
        total=self.db._exec('SELECT payload FROM capability_assets')
        if len(total)>=200 or sum(json.loads(r['payload'])['size'] for r in total)+len(data)>500_000_000:raise ValueError('Input storage is full. Use a separate workspace for further files.')
        key=uid('asset');path=self.inputs/key
        with path.open('xb') as out:out.write(data)
        path.chmod(0o600);item={'id':key,'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        self.db._exec('INSERT INTO capability_assets VALUES(?,?)',(key,json.dumps(item)))
        return item
    def asset(self,key):
        rows=self.db._exec('SELECT payload FROM capability_assets WHERE id=?',(key,))
        if not rows:raise ValueError('Input file not found.')
        item=json.loads(rows[0]['payload']);data=safe_join(self.inputs,key).read_bytes()
        if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Input changed after upload. Upload it again.')
        return item,data
    def select_task(self,prompt,assets):
        lower=prompt.casefold();suffix=Path(assets[0]['name']).suffix.lower() if assets else ''
        rules=[('audio.speak',r'\b(read aloud|text.to.speech|speak|say aloud)\b'),('ml.embed',r'\b(embedding|embeddings|vectorize)\b'),('ml.infer',r'\b(inference|classify|classification|predict)\b'),('media.convert',r'\b(convert|extract audio|thumbnail|trim)\b'),('video.generate',r'\b(video|animation)\b'),('image.generate',r'\b(image|picture|illustration)\b')]
        if suffix in ('.wav','.mp3','.m4a','.ogg','.webm') and re.search(r'meeting|transcrib|notes',lower):return 'audio.transcribe'
        if assets and re.search(r'ocr|scanned|scan\b',lower):return 'document.ocr'
        if suffix in ('.png','.jpg','.jpeg','.tif','.tiff') and not re.search(r'create|generat|draw|convert',lower):return 'document.ocr'
        for task,pattern in rules:
            if re.search(pattern,lower):
                if task in ('image.generate','video.generate') and not re.search(r'\b(create|generate|make|build|draw|render|animate|need|want)\b',lower):continue
                return task
        if suffix in ('.txt','.md','.html','.htm','.pdf','.docx','.csv','.json','.yaml','.yml','.rst'):return 'document.extract'
        if suffix in ('.wav','.mp3','.m4a','.ogg'):return 'audio.transcribe'
        if suffix in ('.png','.jpg','.jpeg','.tif','.tiff') and not re.search(r'create|generat|draw',lower):return 'document.ocr'
        return 'text.generate'
    async def preview(self,prompt,task='',capability='',asset_ids=None,params=None,chat_id=None,_prompt_limit=12000):
        if not isinstance(prompt,str) or not 0<len(prompt.strip())<=_prompt_limit:raise ValueError('The request exceeds the permitted prompt length.')
        asset_ids=asset_ids or []
        if not isinstance(asset_ids,list) or len(asset_ids)>4 or any(not isinstance(k,str) for k in asset_ids):raise ValueError('Choose up to four input files.')
        assets=[self.asset(k)[0] for k in asset_ids]
        if sum(a['size'] for a in assets)>MAX_BYTES:raise ValueError('Keep combined inputs under 20 MB.')
        task=task or self.select_task(prompt,assets)
        rows=self.registry.rows();row=self.registry.find(capability) if capability else next((r for r in rows if r['task']==task and r['configured'] and r['status'] not in ('unavailable','setup_required')),None)
        if not row or row['task']!=task or task in ('voice.live','tool.call'):raise ValueError('Set up a compatible connection for this task first.')
        if not row['configured'] or row['status']=='unavailable':raise ValueError('This connection needs setup or an availability check.')
        params=dict(params or {});bounded_json(params)
        if set(params)-{'text','language','recipe','start_seconds','duration_seconds','inputs','parameters'}:raise ValueError('Unsupported task option.')
        params.setdefault('text',prompt)
        if task=='media.convert' and not params.get('recipe'):
            params['recipe']='thumbnail' if re.search('thumbnail',prompt,re.I) else 'video_mp4' if re.search('mp4|trim',prompt,re.I) else 'image_png' if re.search('png',prompt,re.I) else 'extract_audio'
        if task=='document.extract' and assets and assets[0]['name'].lower().endswith('.pdf') and not row.get('pdf'):raise ValueError('Install the documents extra on this machine for PDF extraction.')
        if task in ('image.generate','video.generate') and assets:raise ValueError('This workflow accepts a text prompt. Use a worker workflow with an explicitly supported file input for image-to-image tasks.')
        if chat_id:self.engine.companion._chat(chat_id)
        routing_plan=None
        if row['adapter']=='provider':
            if assets and task!='image.generate':raise ValueError('Read or transcribe attached files before asking a text model about them.')
            plan=await self.engine.plan(prompt,model=row.get('model') if capability else None,images=len(assets),tools='none',billing='plans_only',direct_only=True)
            row=next(r for r in self.registry.rows() if r.get('model')==plan.provider+':'+plan.model)
            if row['task']!=task:raise ValueError('Choose the matching task and model capability.')
            plan.alternatives=[]  # The reviewed provider is the only approved destination.
            routing_plan=plan.to_dict()
        service=self.registry.service(row['provider'])
        # Snapshot the operator-reviewed adapter configuration; secrets remain references.
        config_hash=hashlib.sha256(bounded_json(service or {}).encode()).hexdigest()
        cost=row['billing'].get('unit_cost_usd')
        estimate=routing_plan['effective_cost_usd'] if routing_plan else 0 if row.get('local') else cost if row['billing'].get('unit')=='request' else None
        job={'id':uid('cap'),'created':time.time(),'status':'awaiting_approval','prompt':prompt,'task':task,'capability':row,'params':params,'assets':assets,'service':service,'config_hash':config_hash,'estimated_usd':estimate,'chat_id':chat_id,'checkpoint':{},'events':[],'files':[],'usage':{},'routing_plan':routing_plan}
        self.save(job);return job
    def event(self,job,message):
        job['events']=(job['events']+[{'time':time.time(),'message':message}])[-200:];self.save(job)
    def approve(self,key,max_cost_usd=0,allow_unpriced=False,confirm_data_transfer=False):
        from ..security import require_execution_enabled
        require_execution_enabled(self.engine.config)
        if type(max_cost_usd) not in (int,float) or not math.isfinite(max_cost_usd) or not 0<=max_cost_usd<=1000:raise ValueError('Choose a valid estimate allowance.')
        candidate=self.get(key)
        if candidate.get('chat_id'):
            c=self.engine.companion;chat=c._chat(candidate['chat_id'])
            if chat['archived'] or any(c.reconcile(t)['status'] in ('queued','running','waiting') for t in c.turns(candidate['chat_id'])):raise ValueError('The chat already has an active request, or is archived.')
        with self.db.transaction() as db:
            row=db.execute('SELECT payload FROM capability_jobs WHERE id=?',(key,)).fetchone()
            if not row:raise ValueError('Request not found.')
            job=json.loads(row['payload'])
            if job['status']!='awaiting_approval':raise ValueError('This request was already submitted or needs an explicit retry.')
            if time.time()-job['created']>600:raise ValueError('Preview expired. Prepare it again.')
            cap=job['capability']
            if not cap.get('local') and confirm_data_transfer is not True:raise ValueError('Confirm the displayed data destination first.')
            estimate=job['estimated_usd']
            if estimate is None and not (allow_unpriced is True and max_cost_usd>0):raise ValueError('This service has no reliable price estimate. Explicitly allow unpriced API use with a positive estimate allowance, or use a local service.')
            if estimate is not None and estimate>max_cost_usd:raise ValueError('Estimate exceeds the allowance.')
            # Only one process can consume this reviewed preview.
            job.update(status='queued',approved_at=time.time(),allowance=max_cost_usd)
            db.execute('UPDATE capability_jobs SET payload=? WHERE id=?',(bounded_json(job,3_000_000),key))
        self.engine.store.save_task(key,'queued',job['prompt'],self.task_plan(job))
        if job.get('chat_id'):
            self.engine.companion.persist({'id':uid('turn'),'chat_id':job['chat_id'],'created':time.time(),'status':'queued','message':job['prompt'],'prompt':job['prompt'],'plan':dict(self.task_plan(job),billing='plans_only' if job['capability'].get('local') else 'any'),'task_id':key,'capability_job':True,'tools_enabled':False,'max_cost_usd':max_cost_usd})
        self.spawn(job);return self.get(key)
    def task_plan(self,job):return {'kind':'capability','capability_job':job['id'],'model_label':job['capability']['label'],'provider':job['capability']['provider'],'node':job['capability']['node'],'estimated_usd':job['estimated_usd'],'usage_cost_unknown':job['estimated_usd'] is None,'profile':{'category':job['task'],'complexity':1},'effective_cost_usd':job['estimated_usd'],'effort':None,'reasons':['Reviewed capability request'],'warnings':[],'alternatives':[],'tools':[],'source_label':job['capability']['provider']}
    def spawn(self,job):
        key=job['id']
        async def scheduled():
            async with self.slots:await self.run(key)
        task=asyncio.create_task(scheduled());self.running[key]=task;self.engine.running[key]=task
        def finished(t):
            self.running.pop(key,None);self.engine.running.pop(key,None)
            if not t.cancelled():t.exception()
        task.add_done_callback(finished)
    def recover(self):
        for record in self.db._exec('SELECT payload FROM capability_jobs'):
            job=json.loads(record['payload'])
            if job['status']=='queued' or job['status']=='running' and job['checkpoint'].get('prompt_id'):
                self.engine.store.save_task(job['id'],'queued',job['prompt'],self.task_plan(job));self.spawn(job)
            elif job['status']=='running':
                job.update(status='interrupted',error='The hub stopped during execution. The remote request may have completed. Inspect the worker before explicitly retrying.');self.save(job);self.engine.store.save_task(job['id'],'interrupted',error=job['error'])
    async def run(self,key):
        from ..security import require_execution_enabled
        require_execution_enabled(self.engine.config)
        job=self.get(key);job['status']='running';self.event(job,'Starting '+job['capability']['label']);self.engine.store.save_task(key,'running')
        try:
            cap=job['capability'];payload_assets=[]
            for item in job['assets']:
                meta,data=self.asset(item['id']);payload_assets.append({'name':meta['name'],'data_b64':base64.b64encode(data).decode()})
            def checkpoint(value):job['checkpoint'].update(value);self.event(job,value.get('phase','Working'))
            async def execute():
                if cap['adapter'] in ('documents','ocr','ffmpeg'):
                    return await self.engine.nodes.submit(cap['node'],'capability_execute',{'execution_id':key,'task':job['task'],'params':job['params'],'assets':payload_assets},timeout=880)
                if cap['adapter']=='provider':
                    from ..router import Plan
                    paths=[]
                    source_folder=self.inputs/key;source_folder.mkdir(mode=0o700,exist_ok=True)
                    for item in payload_assets:
                        name,data=decode_asset(item);validate_media(name,data);path=safe_join(source_folder,name);path.write_bytes(data);paths.append(path)
                    task=await self.engine.run_task(key,job['prompt'],Plan.from_dict(job['routing_plan']),paths,[],isolated=True,read_only=True,estimate_allowance=job['allowance'])
                    if task['status']!='done':raise ValueError('The selected model did not finish. Inspect its activity before retrying.')
                    root=Path(task['output_path']);files=[]
                    for path in root.rglob('*'):
                        if not path.is_file() or path.name in ('prompt.md','meta.json','codex-last-message.txt') or path.name.startswith('.'):continue
                        if job['task']=='image.generate' and path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):continue
                        if len(files)>=8:break
                        safe_join(root,path.relative_to(root).as_posix())
                        if path.stat().st_size>MAX_BYTES:raise ValueError('A generated file exceeds the delivery limit.')
                        name=re.sub(r'[^\w .-]','_',path.name)[:105]
                        if any(f['name']==name for f in files):name=str(len(files)+1)+'-'+name
                        files.append(artifact(name,path.read_bytes()))
                    if job['task']=='image.generate' and not files:raise ValueError('No actual generated image was delivered.')
                    return {'text':task.get('result',''),'artifacts':files,'usage':{'cost_usd':task.get('cost_usd'),'recorded_by_router':True}}
                return await execute_service(self.engine.config,job['service'],job['task'],job['params'],payload_assets,dict(job['checkpoint'],job_id=key),checkpoint)
            result=await asyncio.wait_for(execute(),900)
            folder=safe_join(self.engine.config.output_dir,'capabilities/'+key);folder.mkdir(parents=True,exist_ok=True)
            files=result.get('artifacts',[])
            if len(files)>8 or sum(f.get('size',0) for f in files)>MAX_BYTES:raise ValueError('Outputs exceed the result limit.')
            written=[]
            for item in files:
                name,data=decode_asset({'name':item['name'],'data_b64':item['data_b64']})
                if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Result integrity check failed.')
                if Path(name).suffix.lower() in ('.png','.jpg','.jpeg','.webp','.wav','.mp4','.webm'):validate_media(name,data)
                target=safe_join(folder,name);target.write_bytes(data);written.append({'name':name,'size':len(data),'sha256':item['sha256'],'url':f'/api/capabilities/jobs/{key}/files/{name}'})
            if not written:raise ValueError('The task did not produce a deliverable.')
            usage=result.get('usage',{});cost=usage.get('cost_usd')
            if cost is not None and (type(cost) not in (int,float) or not math.isfinite(cost) or cost<0):raise ValueError('Invalid usage receipt.')
            answer=str(result.get('text','Completed.'))[:2_000_000]
            job.update(status='done',finished=time.time(),files=written,usage=usage,result={k:v for k,v in result.items() if k not in ('artifacts','text')},answer=answer)
            self.event(job,'Validated and saved '+str(len(written))+' result files.')
            self.engine.store.save_task(key,'done',result=answer+'\n\n'+ '\n'.join('['+f['name']+']('+f['url']+')' for f in written),output_path=str(folder),cost_usd=cost or 0,plan=dict(self.task_plan(job),usage_cost_unknown=cost is None))
            if not usage.get('recorded_by_router'):self.engine.store.record_usage(cap['provider'],job['task'],None,0,0,cost or 0,key)
            self.registry.observed[cap['id']]={'ok':True,'functional':True,'at':time.time()}
            self.engine._spawn(self.engine._mirror(folder))
        except asyncio.CancelledError:
            job.update(status='interrupted' if job['status']!='cancelled' else 'cancelled',error='Execution stopped. Inspect the worker for any in-flight operation before retrying.');self.save(job);self.engine.store.save_task(key,job['status'],error=job['error']);raise
        except Exception as exc:
            # Do not expose remote exception bodies, URLs with credentials or subprocess arguments.
            known=isinstance(exc,(ValueError,ImportError,TimeoutError))
            message=str(exc)[:800] if known else 'The service could not complete the request. Check its connection and worker logs.'
            job.update(status='failed',error=message);self.event(job,'Request did not complete.');self.engine.store.save_task(key,'failed',error=message)
    async def cancel(self,key):
        job=self.get(key)
        if job['status'] not in ACTIVE|{'awaiting_approval','interrupted'}:raise ValueError('Request is already finished.')
        # ComfyUI interrupt is global: never interrupt someone else's rendering.
        # A queued item can be removed precisely; a rendering task is detached.
        prompt_id=job['checkpoint'].get('prompt_id')
        if prompt_id and job['service']:
            try:await request('POST',job['service']['base_url']+'/queue',headers=headers(self.engine.config,job['service']),json={'delete':[prompt_id]})
            except Exception:pass
        task=self.running.get(key)
        if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
        job=self.get(key);job.update(status='cancelled',error='Stopped waiting. A remote operation may still finish; inspect its worker before retrying.');self.save(job);self.engine.store.save_task(key,'cancelled',error=job['error'])
        return job
    async def retry(self,key,confirm):
        old=self.get(key)
        if old['status'] not in ('failed','interrupted','cancelled') or confirm is not True:raise ValueError('Inspect the earlier operation and explicitly confirm a new request first.')
        return await self.preview(old['prompt'],old['task'],old['capability']['id'],[a['id'] for a in old['assets']],old['params'],old.get('chat_id'))
    def resume(self,key,confirm):
        job=self.get(key)
        if confirm is not True or job['status'] not in ('failed','interrupted','cancelled') or not job['checkpoint'].get('prompt_id'):raise ValueError('Choose an existing worker job to inspect without resubmitting.')
        job.update(status='queued',error=None);self.save(job);self.engine.store.save_task(key,'queued');self.spawn(job);return job
    def meeting(self,key,notes=None,expected_revision=None):
        job=self.get(key)
        if job['status']!='done' or job['task']!='audio.transcribe':raise ValueError('Choose a completed transcript.')
        row=self.db._exec('SELECT payload FROM capability_meetings WHERE id=?',(key,));value=json.loads(row[0]['payload']) if row else {'id':key,'revision':0,'notes':'','transcript':job['result']['transcript']}
        if notes is not None:
            if not isinstance(notes,str) or len(notes)>100000:raise ValueError('Keep notes under 100,000 characters.')
            with self.db.transaction() as db:
                current=db.execute('SELECT payload FROM capability_meetings WHERE id=?',(key,)).fetchone();revision=json.loads(current['payload'])['revision'] if current else 0
                if expected_revision!=revision:raise ValueError('Notes changed elsewhere. Reload before saving.')
                value.update(notes=notes,revision=revision+1);db.execute('INSERT OR REPLACE INTO capability_meetings VALUES(?,?)',(key,bounded_json(value,3_000_000)))
            folder=safe_join(self.engine.config.output_dir,'capabilities/'+key);(folder/'meeting-notes.md').write_text(notes,encoding='utf-8')
        return value
    async def close(self):
        if getattr(self,'voice_cleanup',None):await self.voice_cleanup()
        tasks=list(self.running.values())
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
