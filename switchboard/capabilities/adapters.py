"""Original adapters to configured services and allowlisted local programs."""
from __future__ import annotations
import asyncio,base64,copy,hashlib,io,json,math,re,tempfile,time,uuid,wave
from pathlib import Path
import httpx
from .documents import extract,transcript_document,transcript_vtt
from ..runtime import which

MAX_BYTES=20_000_000

def decode_asset(asset):
    if set(asset)-{'name','data_b64'} or not isinstance(asset.get('name'),str) or Path(asset['name']).name!=asset['name'] or not re.fullmatch(r'[\w .-]{1,120}',asset['name']) or asset['name'] in ('.','..'):raise ValueError('Use a simple file name.')
    if len(asset.get('data_b64',''))>MAX_BYTES*4//3+8:raise ValueError('File exceeds 20 MB.')
    data=base64.b64decode(asset.get('data_b64',''),validate=True)
    if not data or len(data)>MAX_BYTES:raise ValueError('Choose a nonempty file up to 20 MB.')
    return asset['name'],data

def artifact(name,data):
    if not data or len(data)>MAX_BYTES:raise ValueError('The service returned an empty or oversized result.')
    return {'name':name,'data_b64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}

def validate_media(name,data):
    suffix=Path(name).suffix.lower()
    checks={'.wav':lambda d:d[:4]==b'RIFF' and d[8:12]==b'WAVE','.png':lambda d:d.startswith(b'\x89PNG\r\n\x1a\n'),'.jpg':lambda d:d.startswith(b'\xff\xd8\xff'),'.jpeg':lambda d:d.startswith(b'\xff\xd8\xff'),'.webp':lambda d:d[:4]==b'RIFF' and d[8:12]==b'WEBP','.mp4':lambda d:d[4:8]==b'ftyp','.webm':lambda d:d.startswith(b'\x1aE\xdf\xa3')}
    if suffix not in checks or not checks[suffix](data):raise ValueError('Result does not match the requested media format.')

async def request(method,url,**kwargs):
    async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=8),trust_env=False,follow_redirects=False) as client:
        async with client.stream(method,url,**kwargs) as response:
            if not 200<=response.status_code<300:raise ValueError(f'Service returned HTTP {response.status_code}. Check its connection, access and model configuration.')
            chunks=[];size=0
            async for part in response.aiter_bytes():
                size+=len(part)
                if size>MAX_BYTES:raise ValueError('Service response exceeds 20 MB.')
                chunks.append(part)
            return b''.join(chunks)

async def process(argv,timeout=120):
    proc=await asyncio.create_subprocess_exec(*map(str,argv),stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:out,err=await asyncio.wait_for(proc.communicate(),timeout)
    except (asyncio.TimeoutError,asyncio.CancelledError):
        if proc.returncode is None:proc.kill()
        await proc.communicate();raise
    if proc.returncode:raise ValueError('The selected tool could not process this file. Check its format and installed language/codec support.')
    return out

async def local_execute(task,params,assets):
    if len(assets)!=1:raise ValueError('Choose one input file.')
    name,data=decode_asset(assets[0])
    if task=='document.extract':
        result=await asyncio.to_thread(extract,name,data)
        return {'text':result['text'],'document':result,'artifacts':[artifact('document.txt',result['text'].encode()),artifact('document.json',json.dumps(result,ensure_ascii=False).encode())],'usage':{'cost_usd':0}}
    with tempfile.TemporaryDirectory(prefix='switchboard-media-') as temp:
        folder=Path(temp);source=folder/name;source.write_bytes(data)
        if task=='document.ocr':
            binary=which('tesseract')
            if not binary:raise ValueError('Install Tesseract on the selected machine first.')
            language=params.get('language','eng')
            if not re.fullmatch(r'[a-zA-Z_+]{1,60}',language):raise ValueError('Invalid OCR language.')
            pages=[source]
            if source.suffix.lower()=='.pdf':
                from pypdf import PdfReader
                if len(PdfReader(source).pages)>20:raise ValueError('Use up to 20 scanned pages per OCR request.')
                renderer=which('pdftoppm')
                if not renderer:raise ValueError('PDF OCR needs pdftoppm on the selected machine.')
                await process([renderer,'-png','-scale-to','2000',source,folder/'page'],120);pages=sorted(folder.glob('page-*.png'))
            elif source.suffix.lower() not in ('.png','.jpg','.jpeg','.tif','.tiff','.bmp'):raise ValueError('OCR accepts images or scanned PDF files.')
            parts=[]
            for i,page in enumerate(pages):
                output=await process([binary,page,'stdout','-l',language],90);parts.append('Page '+str(i+1)+'\n'+output.decode('utf-8'))
            text='\n\n'.join(parts)
            if not text.strip() or len(text)>2_000_000:raise ValueError('OCR returned no usable text or too much text.')
            return {'text':text,'artifacts':[artifact('ocr.txt',text.encode())],'usage':{'cost_usd':0}}
        if task!='media.convert':raise ValueError('Unsupported machine capability.')
        binary=which('ffmpeg')
        if not binary:raise ValueError('Install FFmpeg on the selected machine first.')
        recipe=params.get('recipe','extract_audio')
        recipes={'extract_audio':('audio.wav',['-vn','-ac','1','-ar','16000','-c:a','pcm_s16le']),'video_mp4':('video.mp4',['-c:v','libx264','-preset','fast','-c:a','aac','-movflags','+faststart']),'thumbnail':('thumbnail.png',['-frames:v','1']),'image_png':('image.png',['-frames:v','1'])}
        if recipe not in recipes:raise ValueError('Choose a supported conversion.')
        start=params.get('start_seconds',0);duration=params.get('duration_seconds',60)
        if type(start) not in (int,float) or not math.isfinite(start) or not 0<=start<=86400 or type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=3600:raise ValueError('Choose valid clip times.')
        output_name,args=recipes[recipe];target=folder/output_name
        await process([binary,'-nostdin','-v','error','-protocol_whitelist','file,pipe','-ss',start,'-i',source,'-t',duration,'-threads','2',*args,'-y',target],180)
        result=target.read_bytes();validate_media(output_name,result)
        return {'text':'Media conversion completed.','artifacts':[artifact(output_name,result)],'usage':{'cost_usd':0}}

def headers(config,service):
    secret=config.secret(service.get('secret_env'))
    return {'Authorization':'Bearer '+secret} if secret else {}

async def execute_service(config,service,task,params,assets,checkpoint,progress):
    from ..security import require_execution_enabled
    require_execution_enabled(config)
    base=service['base_url'];auth=headers(config,service);model=service.get('model');adapter=service['adapter']
    if adapter=='audio':
        if len(assets)!=1:raise ValueError('Choose one audio file.')
        name,data=decode_asset(assets[0]);options={'model':model,'response_format':'verbose_json' if model=='whisper-1' else 'diarized_json' if 'diarize' in model else 'json'}
        if model=='whisper-1':options['timestamp_granularities[]']='segment'
        if 'diarize' in model:options['chunking_strategy']='auto'
        if params.get('language'):options['language']=str(params['language'])[:20]
        result=json.loads(await request('POST',base+'/audio/transcriptions',headers=auth,data=options,files={'file':(name,data)}));document=transcript_document(result)
        files=[artifact('transcript.json',json.dumps(document,ensure_ascii=False).encode()),artifact('transcript.txt',document['text'].encode())]
        if document['segments']:files.append(artifact('transcript.vtt',transcript_vtt(document).encode()))
        return {'text':document['text'],'transcript':document,'artifacts':files,'usage':{'cost_usd':0 if service.get('local') else None,'provider_usage':result.get('usage',{})}}
    if adapter=='speech':
        text=params.get('text','')
        if not isinstance(text,str) or not 0<len(text)<=4000:raise ValueError('Speech accepts up to 4,000 text characters.')
        result=await request('POST',base+'/audio/speech',headers=auth,json={'model':model,'input':text,'voice':service.get('voice','alloy'),'response_format':'wav'})
        validate_media('speech.wav',result)
        return {'text':'AI-generated speech is ready.','artifacts':[artifact('speech.wav',result)],'usage':{'cost_usd':0 if service.get('local') else None,'characters':len(text)}}
    if adapter in ('ollama','inference'):
        if adapter=='ollama':
            if task=='ml.embed':url=base+'/api/embed';body={'model':model,'input':params.get('text',''),'truncate':False}
            else:url=base+'/api/generate';body={'model':model,'prompt':params.get('text',''),'stream':False}
        else:url=base;body={'inputs':params.get('inputs',params.get('text','')),'parameters':params.get('parameters',{})}
        result=json.loads(await request('POST',url,headers=auth,json=body))
        if not isinstance(result,(dict,list)):raise ValueError('Inference returned an invalid result.')
        if isinstance(result,dict) and result.get('error'):raise ValueError('Inference failed. Check model readiness and input schema.')
        if task=='ml.embed' and (not isinstance(result,dict) or not result.get('embeddings')):raise ValueError('The model did not return embeddings.')
        text=result.get('response','Inference result saved.') if isinstance(result,dict) else 'Inference result saved.'
        return {'text':str(text),'result':result,'artifacts':[artifact('inference.json',json.dumps(result,ensure_ascii=False,allow_nan=False).encode())],'usage':{'cost_usd':0 if service.get('local') else None}}
    if adapter!='comfyui':raise ValueError('This capability uses a live session, not a file job.')
    prompt_id=checkpoint.get('prompt_id')
    if not prompt_id:
        workflow=copy.deepcopy(service.get('workflow'))
        if not isinstance(workflow,dict) or not workflow:raise ValueError('Configure a reviewed workflow on this connection first.')
        def fill(value):
            if isinstance(value,str):return value.replace('{{prompt}}',str(params.get('text','')))
            if isinstance(value,list):return [fill(v) for v in value]
            if isinstance(value,dict):return {k:fill(v) for k,v in value.items()}
            return value
        # Checkpoint before submission: a disconnect here is an unknown outcome,
        # never an excuse to submit the same generation automatically.
        progress({'phase':'submitting'})
        reply=json.loads(await request('POST',base+'/prompt',headers=auth,json={'prompt':fill(workflow),'client_id':checkpoint['job_id']}))
        prompt_id=reply.get('prompt_id')
        if not isinstance(prompt_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',prompt_id):raise ValueError('Worker did not acknowledge the job.')
        progress({'prompt_id':prompt_id,'phase':'rendering'})
    while True:
        response=json.loads(await request('GET',base+'/history/'+prompt_id,headers=auth));history=response.get(prompt_id)
        if history:
            if (history.get('status') or {}).get('status_str')=='error':raise ValueError('The generation worker reported a failure. Inspect its workflow and models.')
            outputs=history.get('outputs',{});files=[]
            for value in outputs.values():
                for group in ('images','gifs','videos'):
                    for item in value.get(group,[]):
                        name=item.get('filename','');subfolder=item.get('subfolder','')
                        if Path(name).name!=name or '/' in name or '\\' in name or '..' in Path(subfolder).parts or subfolder.startswith(('/','\\')) or ':' in subfolder or item.get('type','output')!='output':raise ValueError('Worker returned an unsafe output path.')
                        suffix=Path(name).suffix.lower()
                        if suffix not in ('.png','.jpg','.jpeg','.webp','.mp4','.webm'):continue
                        content=await request('GET',base+'/view',headers=auth,params={'filename':name,'subfolder':subfolder,'type':'output'})
                        validate_media(name,content);files.append(artifact('generated-'+str(len(files)+1)+suffix,content))
                        if len(files)>8:raise ValueError('Worker returned too many output files.')
            expected=('.mp4','.webm') if task=='video.generate' else ('.png','.jpg','.jpeg','.webp')
            if not any(f['name'].endswith(expected) for f in files):raise ValueError('Worker finished without the requested media artifact.')
            return {'text':'Generation completed.','artifacts':files,'usage':{'cost_usd':0 if service.get('local') else None}}
        await asyncio.sleep(1)
