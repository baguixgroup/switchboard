"""Desktop Laith lifecycle, private local controls and shared companion API client.

No GUI toolkit is imported by the tray, agent or hub. The pet has its own process.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import httpx

from .paths import is_frozen, os_family, self_command

ACTIONS={'show','hide','panel','pause','resume','stop','status'}
DEFAULTS={'enabled':False,'size':180,'animation':True,'on_top':True,'chat_id':'','model':''}


def preferences_path(config):return config.db_path.parent/'laith-desktop.json'
def endpoint_path(config):return config.db_path.parent/'laith-desktop-ipc.json'


def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with contextlib.suppress(OSError,AttributeError):os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as out:json.dump(data,out,ensure_ascii=False)
        os.replace(name,path)
    finally:
        with contextlib.suppress(FileNotFoundError):os.unlink(name)


def preferences(config):
    try:
        data=json.loads(preferences_path(config).read_text())
        if not isinstance(data,dict):data={}
    except (OSError,ValueError):data={}
    result=dict(DEFAULTS)
    for key in ('enabled','animation','on_top'):
        if type(data.get(key)) is bool:result[key]=data[key]
    if data.get('size') in (120,180,240):result['size']=data['size']
    for key in ('chat_id','model'):
        if isinstance(data.get(key),str) and len(data[key])<180:result[key]=data[key]
    for key in ('x','y'):
        if type(data.get(key)) is int and abs(data[key])<100000:result[key]=data[key]
    return result


def update_preferences(config,**changes):
    data=preferences(config);data.update(changes);atomic_json(preferences_path(config),data)


def helper_path():
    if not is_frozen():return None
    exe=Path(sys.executable)
    if os_family()=='macos':return exe.parent.parent/'Helpers/Laith.app/Contents/MacOS/Laith'
    if os_family()=='windows':return exe.with_name('Laith.exe')
    return None


def available():
    if os_family() not in ('macos','windows'):return False
    helper=helper_path()
    if helper and os_family()=='macos' and helper.is_file():
        import platform,plistlib
        with (helper.parent.parent/'Info.plist').open('rb') as stream:info=plistlib.load(stream)
        version=lambda v:tuple(int(x) for x in (v.split('.')+['0','0','0'])[:3])
        if version(platform.mac_ver()[0])<version(info.get('LSMinimumSystemVersion','11.0')):return False
    return helper.is_file() if helper else importlib.util.find_spec('PySide6') is not None


def ipc(config,action):
    """Send only a bounded command to a loopback endpoint authenticated by a private token."""
    try:
        path=endpoint_path(config)
        if path.stat().st_size>4096:return None
        state=json.loads(path.read_text())
        port=state.get('port');token=state.get('token')
        if type(port) is not int or not 1024<=port<=65535 or not isinstance(token,str) or len(token)!=64:return None
        with httpx.Client(trust_env=False,timeout=1.5) as client:
            r=client.post(f'http://127.0.0.1:{port}/control',json={'action':action},headers={'Authorization':'Bearer '+token})
        if r.status_code==200:return r.json()
    except (OSError,ValueError,httpx.HTTPError):pass
    return None


def status(config):
    current=ipc(config,'status')
    return {'available':available(),'enabled':preferences(config)['enabled'],
            'running':bool(current),'visible':bool(current and current.get('visible')),
            'panel_visible':bool(current and current.get('panel_visible'))}


def control(config,action,persist=True):
    if action not in ACTIONS:raise ValueError('Unknown desktop companion action.')
    if action=='status':return status(config)
    if not available():raise ValueError('Desktop Laith is available in the Mac and Windows desktop installers.')
    if persist and action in ('show','panel','hide'):update_preferences(config,enabled=action!='hide')
    current=ipc(config,action)
    if current is not None:return dict(current,available=True)
    if action not in ('show','panel'):return {'available':True,'running':False,'visible':False}
    helper=helper_path()
    argv=([str(helper),'--home',str(config.home)] if helper else self_command('--home',str(config.home),'pet','run'))
    if action=='panel':argv+=['--panel']
    log_path=config.db_path.parent/'laith-desktop.log'
    with log_path.open('ab') as out:
        with contextlib.suppress(OSError):os.chmod(log_path,0o600)
        kw={'stdin':subprocess.DEVNULL,'stdout':out,'stderr':out}
        if os_family()=='windows':kw['creationflags']=0x00000008|0x00000200
        else:kw['start_new_session']=True
        subprocess.Popen(argv,**kw)
    return {'available':True,'running':False,'starting':True,'visible':False}


def fit_position(x,y,width,height,screens):
    """Keep the whole companion on a real display, including after monitor removal."""
    choices=[]
    for left,top,w,h in screens:
        nx=max(left,min(x,left+max(0,w-width)));ny=max(top,min(y,top+max(0,h-height)))
        choices.append(((nx-x)**2+(ny-y)**2,nx,ny))
    return min(choices)[1:] if choices else (0,0)


class PetClient:
    def __init__(self,config):
        self.config=config
        port=config.node.get('local_dashboard_port') if config.role=='agent' else None
        base=f'http://127.0.0.1:{int(port)}' if port else config.hub_url
        self.http=httpx.Client(base_url=base,headers={'Authorization':'Bearer '+config.token},timeout=httpx.Timeout(60,connect=8),trust_env=False)

    def request(self,path,body=None):
        r=self.http.get(path) if body is None else self.http.post(path,json=body)
        if r.is_error:
            try:message=r.json().get('detail',r.text)
            except ValueError:message=r.text
            raise ValueError(str(message)[:1200])
        return r.json()

    def overview(self):return {'library':self.request('/api/laith'),'models':self.request('/api/models')}
    def chat(self,key):return self.request('/api/laith/chats/'+key)
    def new_chat(self):return self.request('/api/laith/chats',{'title':'Laith desktop conversation'})
    def prepare(self,key,message,model='',ability='conversation',tools=False,knowledge=False):
        return self.request('/api/laith/chats/'+key+'/prepare',{'message':message,'model':model,'ability':ability,'tools':'auto' if tools else 'none',
             'use_memory':True,'use_knowledge':knowledge,'billing':'plans_only','max_cost_usd':0})
    def send(self,key,turn):return self.request('/api/laith/chats/'+key+'/send',{'turn_id':turn,'confirm':True})
    def retry(self,key,turn):return self.request('/api/laith/chats/'+key+'/retry',{'turn_id':turn})
    def goal(self,key,turn,title):return self.request('/api/laith/chats/'+key+'/goal',{'turn_id':turn,'title':title})


def run(config,panel=False):
    from .desktop_pet_ui import run_pet
    return run_pet(config,panel)
