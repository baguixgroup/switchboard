"""Voice endpoints shared by the local desktop relay and authenticated hub."""
from __future__ import annotations
import asyncio
import json
from urllib.parse import urlsplit
from fastapi import Depends,Request
from starlette.responses import JSONResponse,Response
from ..voice import LocalVoice, MAX_AUDIO_BYTES


def same_origin(request):
    origin = request.headers.get('origin')
    if not origin:return True  # native clients have no Origin header
    if origin == 'null':return False
    parsed = urlsplit(origin)
    return parsed.scheme in ('http','https') and parsed.netloc.lower() == request.headers.get('host','').lower()


class VoiceAPI:
    def __init__(self, config):self.voice=LocalVoice(config); self.setup=None

    async def handle(self, request):
        if not same_origin(request):return JSONResponse({'detail':'Open voice from this Tazrivo workspace.'},403)
        path=request.url.path.rsplit('/',1)[-1]
        if path=='status' and request.method=='GET':return JSONResponse(self.voice.status(),headers={'Cache-Control':'no-store'})
        if request.method!='POST':return JSONResponse({'detail':'Method not allowed.'},405)
        try:
            limit=512 if path=='setup' else 20000 if path=='speak' else MAX_AUDIO_BYTES
            data=bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>limit:return JSONResponse({'detail':'Recording or request is too large.'},413)
            if path=='setup':
                if json.loads(data).get('confirm') is not True:raise ValueError('Confirm the local speech download first.')
                if not self.voice.status()['available']:return JSONResponse({'detail':'Update Tazrivo to include its local speech runtime.'},503)
                if self.setup is None or self.setup.done():
                    async def install():
                        try:await asyncio.to_thread(self.voice.install)
                        except ValueError:pass  # shown through status; no unhandled background exception
                    self.setup=asyncio.create_task(install())
                return JSONResponse(self.voice.status(),202)
            if path=='transcribe':
                result=await asyncio.to_thread(self.voice.transcribe,bytes(data),request.query_params.get('language',''))
                return JSONResponse(result,headers={'Cache-Control':'no-store'})
            if path=='speak':
                value=json.loads(data)
                audio=await asyncio.to_thread(self.voice.speak,value.get('text'),value.get('language',''))
                return Response(audio,media_type='audio/wav',headers={'Cache-Control':'no-store'})
            return JSONResponse({'detail':'Unknown voice action.'},404)
        except (ValueError,TypeError,AttributeError) as exc:return JSONResponse({'detail':str(exc)},400)
        except Exception:return JSONResponse({'detail':'Local speech could not finish. Try again or download the speech model again.'},503)


def register(app, config, auth):
    controller=VoiceAPI(config)
    async def endpoint(request:Request):return await controller.handle(request)
    app.add_api_route('/api/local/voice/{action}',endpoint,methods=['GET','POST'],dependencies=[Depends(auth)])
    app.state.local_voice=controller
