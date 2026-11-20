import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from switchboard import desktop_pet as pet
from switchboard.config import Config
from switchboard.localproxy import build_proxy_app
from switchboard.window import valid_page
from .conftest import make_home


def test_visual_preferences_are_private_and_survive_invalid_files(tmp_path):
    cfg=Config(make_home(tmp_path));pet.update_preferences(cfg,enabled=True,size=240,x=-100,y=22)
    saved=pet.preferences(cfg);assert saved['enabled'] and saved['size']==240 and saved['x']==-100
    if os.name!='nt':assert pet.preferences_path(cfg).stat().st_mode&0o777==0o600
    pet.preferences_path(cfg).write_text('{broken')
    assert pet.preferences(cfg)==pet.DEFAULTS
    pet.atomic_json(pet.preferences_path(cfg),{'enabled':'true','size':-500,'x':99999999})
    assert not pet.preferences(cfg)['enabled'] and pet.preferences(cfg)['size']==180 and 'x' not in pet.preferences(cfg)


def test_pet_returns_to_a_real_monitor_after_display_removal():
    assert pet.fit_position(5000,5000,180,180,[(0,0,1920,1080)])==(1740,900)
    assert pet.fit_position(-900,600,180,180,[(-1280,0,1280,1024),(0,0,1920,1080)])==(-900,600)
    assert pet.fit_position(-900,600,180,180,[(0,0,1920,1080)])==(0,600)


def test_desktop_control_accepts_only_bounded_commands_and_hides_without_spawning(tmp_path,monkeypatch):
    cfg=Config(make_home(tmp_path));monkeypatch.setattr(pet,'available',lambda:True);monkeypatch.setattr(pet,'ipc',lambda *args:None)
    def forbidden(*args,**kw):pytest.fail('Hide must not launch a new process.')
    monkeypatch.setattr(pet.subprocess,'Popen',forbidden)
    assert pet.control(cfg,'hide')['running'] is False and not pet.preferences(cfg)['enabled']
    with pytest.raises(ValueError):pet.control(cfg,'run arbitrary command')
    assert valid_page('#history/task-123')=='#history/task-123'
    for value in ['https://example.com','#laith/../secrets','#history/x?redirect=other','#laith/javascript:bad']:
        with pytest.raises(ValueError):valid_page(value)


def test_mac_helper_opens_the_parent_switchboard_app(tmp_path,monkeypatch):
    from switchboard import paths
    main=tmp_path/'Switchboard.app/Contents/MacOS/Switchboard';main.parent.mkdir(parents=True);main.touch()
    helper=tmp_path/'Switchboard.app/Contents/Helpers/Laith.app/Contents/MacOS/Laith'
    monkeypatch.setattr(paths,'is_frozen',lambda:True);monkeypatch.setattr(paths,'os_family',lambda:'macos');monkeypatch.setattr(sys,'executable',str(helper))
    assert paths.self_command('launch','--page','#laith',gui=True)==[str(main),'launch','--page','#laith']


def test_native_pet_client_sends_only_zero_allowance_plan_requests(tmp_path):
    cfg=Config(make_home(tmp_path));client=pet.PetClient(cfg);requests=[]
    def handle(request):
        requests.append(json.loads(request.content));return httpx.Response(200,json={'id':'turn-test'})
    client.http.close();client.http=httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle))
    client.prepare('chat-test','Improve this copied text.',tools=True)
    body=requests[0];assert body['billing']=='plans_only' and body['max_cost_usd']==0 and body['tools']=='auto'
    assert body['message']=='Improve this copied text.'
    client.http.close()


def test_agent_pet_reuses_local_relay_instead_of_new_lan_connection(tmp_path):
    cfg=Config(make_home(tmp_path,role='agent',hub_url='http://192.0.2.10:8765'))
    cfg.raw['node']['local_dashboard_port']=8766
    client=pet.PetClient(cfg)
    assert str(client.http.base_url)=='http://127.0.0.1:8766'
    client.http.close()


async def test_local_pet_controls_reject_cross_site_calls_and_bad_actions(tmp_path,monkeypatch):
    cfg=Config(make_home(tmp_path));calls=[]
    monkeypatch.setattr(pet,'status',lambda cfg:{'available':True,'visible':False})
    monkeypatch.setattr(pet,'control',lambda cfg,action:calls.append(action) or {'visible':True})
    app=build_proxy_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
        assert (await client.get('/api/local/laith-desktop')).json()['available']
        assert (await client.post('/api/local/laith-desktop',json={'action':'show'},headers={'Origin':'https://evil.example'})).status_code==403
        assert (await client.post('/api/local/laith-desktop',json={'action':'show'},headers={'Origin':'null'})).status_code==403
        assert (await client.post('/api/local/laith-desktop',json={'action':'execute'})).status_code==400
        assert (await client.post('/api/local/laith-desktop',json={'action':'show'})).status_code==200
        assert calls==['show']


@pytest.mark.skipif(sys.platform not in ('darwin','win32'),reason='Desktop helper runtime')
def test_real_helper_single_instance_private_ipc_show_hide_and_stop(tmp_path):
    pytest.importorskip('PySide6')
    cfg=Config(make_home(tmp_path,port=65431));env=dict(os.environ,QT_QPA_PLATFORM='offscreen')
    command=[sys.executable,'-m','switchboard','--home',str(cfg.home),'pet','run']
    process=subprocess.Popen(command,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    def settle(predicate):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            value=pet.ipc(cfg,'status')
            if value and predicate(value):return value
            if process.poll() is not None:pytest.fail(process.stderr.read().decode())
            time.sleep(.05)
        pytest.fail('Desktop helper did not reach expected state.')
    try:
        settle(lambda s:s['visible'])
        endpoint=json.loads(pet.endpoint_path(cfg).read_text())
        with httpx.Client(trust_env=False) as client:
            url=f'http://127.0.0.1:{endpoint["port"]}/control'
            assert client.post(url,json={'action':'hide'}).status_code==403
            assert client.post(url,json={'action':'hide'},headers={'Authorization':'Bearer '+endpoint['token'],'Origin':'https://evil.example'}).status_code==403
        pet.control(cfg,'hide');settle(lambda s:not s['visible']);assert not pet.preferences(cfg)['enabled']
        second=subprocess.run(command+['--panel'],env=env,capture_output=True,timeout=12)
        assert second.returncode==0,second.stderr
        settle(lambda s:s['visible'] and s['panel_visible'])
        assert json.loads(pet.endpoint_path(cfg).read_text())['pid']==process.pid
        pet.control(cfg,'stop',False);assert process.wait(timeout=12)==0
        assert not pet.endpoint_path(cfg).exists()
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=12)
