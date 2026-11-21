"""Discoverable contracts with explicit setup, billing and data destinations."""
from __future__ import annotations
import importlib.util,ipaddress,math,re,shutil,os
from ..runtime import which
from urllib.parse import urlsplit

TASKS={
 'document.extract':('Read a document',['txt','md','html','csv','json','docx','pdf'],['text','json'],'documents'),
 'document.ocr':('Read scanned text',['png','jpg','tiff','pdf'],['text'],'ocr'),
 'audio.transcribe':('Transcribe audio',['wav','mp3','m4a','webm','ogg'],['json','text','vtt'],'audio'),
 'audio.speak':('Create speech',['text'],['wav'],'speech'),
 'voice.live':('Talk with Laith',['microphone'],['audio','text'],'realtime'),
 'image.generate':('Create an image',['text'],['png','jpg','webp'],'comfyui'),
 'video.generate':('Create a video',['text'],['mp4','webm'],'comfyui'),
 'media.convert':('Convert media',['audio','video','image'],['mp4','wav','png'],'ffmpeg'),
 'ml.embed':('Generate embeddings',['text'],['json'],'ollama'),
 'ml.infer':('Run model inference',['json','text'],['json','text'],'inference'),
}
ADAPTERS={'documents','ocr','audio','speech','realtime','comfyui','ffmpeg','ollama','inference'}

def endpoint(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError('Use a service URL without credentials, query or fragment.')
    if p.scheme=='http':
        try:local=ipaddress.ip_address(p.hostname).is_private or ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:local=p.hostname=='localhost' or '.' not in p.hostname or p.hostname.endswith('.local')
        if not local:raise ValueError('Internet services require HTTPS.')
    return url.rstrip('/')

def validate_service(value):
    if not isinstance(value,dict):raise ValueError('Invalid connection.')
    allowed={'id','label','adapter','base_url','model','voice','secret_env','enabled','local','unit_cost_usd','billing_unit','license','hardware','workflow','capabilities'}
    if set(value)-allowed:raise ValueError('Unsupported connection field.')
    result=dict(value);key=result.get('id','')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}',key):raise ValueError('Use a short connection identifier.')
    if result.get('adapter') not in ADAPTERS-{'documents','ocr','ffmpeg'}:raise ValueError('Choose a supported service adapter.')
    result['base_url']=endpoint(str(result.get('base_url','')))
    if not result.get('local') and not result.get('secret_env'):
        result['secret_env']='OPENAI_API_KEY' if urlsplit(result['base_url']).hostname=='api.openai.com' else 'SWITCHBOARD_SERVICE_'+key.upper().replace('-','_')+'_KEY'
    env=result.get('secret_env','')
    if env and env!='OPENAI_API_KEY' and not re.fullmatch(r'SWITCHBOARD_SERVICE_[A-Z0-9_]+_KEY',env):raise ValueError('Use a dedicated service credential name.')
    for name in ('model','voice','label','license','hardware','billing_unit'):
        if name in result and (not isinstance(result[name],str) or len(result[name])>500):raise ValueError('Invalid connection description.')
    for name in ('enabled','local'):
        if name in result and type(result[name]) is not bool:raise ValueError('Invalid connection switch.')
    if 'unit_cost_usd' in result and (type(result['unit_cost_usd']) not in (int,float) or not math.isfinite(result['unit_cost_usd']) or result['unit_cost_usd']<0):raise ValueError('Invalid unit price.')
    permitted={'audio':{'audio.transcribe'},'speech':{'audio.speak'},'realtime':{'voice.live'},'comfyui':{'image.generate','video.generate'},'ollama':{'ml.embed','ml.infer'},'inference':{'ml.infer'}}
    if not isinstance(result.get('capabilities',[]),list):raise ValueError('Invalid task list.')
    if any(c not in permitted[result['adapter']] for c in result.get('capabilities',[])):raise ValueError('Task does not match the service adapter.')
    def check_workflow(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if re.search(r'(?i)(api.?key|password|authorization|access.?token|secret)',str(key)):raise ValueError('Keep credentials in the secure API key field, never in a workflow.')
                check_workflow(item)
        elif isinstance(value,list):
            for item in value:check_workflow(item)
        elif isinstance(value,str) and re.search(r'(?i)(bearer\s+|sk-[A-Za-z0-9]{12}|https?://[^/ ]+@)',value):raise ValueError('Remove embedded credentials from the workflow.')
    if result.get('workflow'):
        if not isinstance(result['workflow'],dict):raise ValueError('Use an API workflow object.')
        check_workflow(result['workflow'])
    if len(str(result.get('workflow',{})))>200000:raise ValueError('Workflow is too large.')
    caps=result.get('capabilities',[])
    if not isinstance(caps,list) or any(c not in TASKS for c in caps):raise ValueError('Invalid task list.')
    return result

def local_inventory():
    return {'documents':True,'pdf':importlib.util.find_spec('pypdf') is not None,'ocr':bool(which('tesseract')),'pdf_ocr':bool(which('pdftoppm')) and importlib.util.find_spec('pypdf') is not None,'ffmpeg':bool(which('ffmpeg')),'cpu_count':os.cpu_count()}

def services(config):
    # No paid service is enabled implicitly by a consumer subscription.
    return [validate_service(s) for s in config.raw.get('capability_services',[])]

class Registry:
    def __init__(self,engine):self.engine=engine;self.observed={}
    def rows(self):
        e=self.engine;nodes=e.nodes.list();rows=[]
        if not any(n['node']==e.config.node_name for n in nodes):nodes.append({'node':e.config.node_name,'online':True,'capability_tools':local_inventory()})
        for n in nodes:
            tools=n.get('capability_tools',{})
            for task,adapter,flag in [('document.extract','documents','documents'),('document.ocr','ocr','ocr'),('media.convert','ffmpeg','ffmpeg')]:
                rows.append(self.entry(task,adapter,n['node'],bool(n.get('online') and tools.get(flag)),local=True,pdf=tools.get('pdf',False),capacity={k:tools.get(k) for k in ('cpu_count','pdf_ocr')}))
        for service in services(e.config):
            adapter=service['adapter'];caps=service.get('capabilities') or {'audio':['audio.transcribe'],'speech':['audio.speak'],'realtime':['voice.live'],'comfyui':['image.generate','video.generate'],'ollama':['ml.embed','ml.infer'],'inference':['ml.infer']}[adapter]
            for task in caps:
                configured=bool(service.get('enabled',True) and (not service.get('secret_env') or e.config.secret(service['secret_env'])) and (service.get('model') or adapter=='comfyui'))
                if adapter=='comfyui':configured=configured and bool(service.get('workflow'))
                rows.append(self.entry(task,adapter,e.config.node_name,configured,service=service,local=service.get('local',False)))
        present={r['task'] for r in rows}
        for task in TASKS:
            if task not in present:rows.append(self.entry(task,TASKS[task][3],e.config.node_name,False))
        # Existing provider and MCP discovery are part of the same contract.
        for m in e.catalog.all():
            if m.provider not in e.available_providers() or 'handoff' in m.features:continue
            rows.append({'id':'model:'+m.key,'task':'image.generate' if 'image_gen' in m.features else 'text.generate','label':m.label or m.id,'adapter':'provider','provider':m.provider,'node':e.cli_location(m.provider) or e.config.node_name,'status':'configured','configured':True,'verified':bool(self.observed.get('model:'+m.key,{}).get('functional')),'input_formats':['text'],'output_formats':['png','jpg','webp'] if 'image_gen' in m.features else ['text'],'streaming':False,'asynchronous':True,'data_destination':m.provider,'billing':{'kind':'existing_router'},'setup':[],'hardware':'Provider-managed','license':'Provider and account terms','model':m.key})
        for name,locations in e.nodes.mcp_locations().items():
            rows.append({'id':'tool:'+name,'task':'tool.call','label':name,'adapter':'mcp','provider':name,'node':locations[0],'status':'configured','configured':True,'verified':False,'input_formats':['json'],'output_formats':['json'],'streaming':False,'asynchronous':True,'data_destination':locations[0],'billing':{'kind':'tool_specific'},'setup':[],'hardware':'Connected machine','license':'Installed tool terms'})
        return rows
    def entry(self,task,adapter,node,ready,service=None,local=False,pdf=False,capacity=None):
        title,inputs,outputs,_=TASKS[task];service=service or {};key=task+':'+(service.get('id') or node)
        observed=self.observed.get(key)
        return {'id':key,'task':task,'label':title,'adapter':adapter,'provider':service.get('id',adapter),'node':node,'configured':ready,'status':('ready' if observed and observed['ok'] else 'unavailable' if observed else 'configured') if ready and service else 'ready' if ready else 'setup_required','verified':bool(observed and observed.get('functional')),'input_formats':inputs,'output_formats':outputs,'streaming':task=='voice.live','asynchronous':task!='voice.live','data_destination':service.get('base_url',node),'local':local,'billing':{'kind':'local' if local else 'api','unit':service.get('billing_unit','request'),'unit_cost_usd':service.get('unit_cost_usd',0 if local else None)},'setup':[] if ready else ['Connect a compatible service or install the required tool on an online machine.'],'hardware':service.get('hardware','CPU; PDF parser optional' if adapter=='documents' else 'Worker-dependent'),'license':service.get('license','Installed component terms; weights and voices have separate terms'),'pdf':pdf,'capacity':capacity or {},'observed':observed}
    def find(self,key):
        found=next((r for r in self.rows() if r['id']==key),None)
        if not found:raise ValueError('Capability not found.')
        return found
    def service(self,key):return next((s for s in services(self.engine.config) if s['id']==key),None)
