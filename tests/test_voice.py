"""Speech safety boundaries and local/hub API behavior, without recording a microphone."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import wave

import httpx
import pytest

from switchboard import voice
from switchboard.localproxy import build_proxy_app
from switchboard.web.app import create_app
from .test_studio import make_engine


def wav(frames=16000, *, rate=16000, channels=1, width=2):
    out=io.BytesIO()
    with wave.open(out,'wb') as file:
        file.setnchannels(channels);file.setsampwidth(width);file.setframerate(rate);file.writeframes(bytes(frames*channels*width))
    return out.getvalue()


def local(tmp_path):return voice.LocalVoice(SimpleNamespace(home=tmp_path,node_name='test-mac'))


@pytest.mark.parametrize('recording',[b'not audio',wav(1599),wav(960001),wav(rate=48000),wav(channels=2),wav(width=1),wav()[:-20]])
def test_invalid_recordings_fail_before_loading_any_model(tmp_path,recording):
    service=local(tmp_path)
    with pytest.raises(ValueError):service.transcribe(recording)
    assert service._recognizer is None and not service.folder.exists()


def test_bounded_language_and_unconfigured_runtime_never_send_audio(tmp_path):
    service=local(tmp_path)
    with pytest.raises(ValueError,match='language'):service.transcribe(wav(),'../../unknown')
    with pytest.raises(ValueError,match='Set up'):service.transcribe(wav())


def test_unreadable_model_is_reported_without_crashing_status(tmp_path,monkeypatch):
    service=local(tmp_path)
    monkeypatch.setattr(voice.importlib.util,'find_spec',lambda name:object())
    def denied(path):raise PermissionError('access denied')
    monkeypatch.setattr(Path,'is_file',denied)
    status=service.status()
    assert not status['ready'] and status['state']=='unavailable' and 'cannot read' in status['error']


def test_speech_output_keeps_model_text_out_of_commands_and_cleans_temporary_audio(tmp_path,monkeypatch):
    service=local(tmp_path);monkeypatch.setattr(voice,'sys',SimpleNamespace(platform='darwin'))
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if args[0].endswith('say'):Path(args[args.index('-o')+1]).write_bytes(b'generated speech')
        else:Path(args[-1]).write_bytes(wav())
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(voice.subprocess,'run',run)
    text='-f /private/secrets $(do-not-run) `literal text`'
    result=service.speak(text)
    assert result==wav() and calls[0][1]['input']==text
    assert all(text not in args and not kwargs.get('shell') for args,kwargs in calls)
    assert not Path(calls[-1][0][-1]).exists()
    for bad in ['',None,'x'*3501]:
        with pytest.raises(ValueError):service.speak(bad)


def test_verified_model_installs_only_selected_regular_files(tmp_path,monkeypatch):
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w:bz2') as package:
        for name in ['tiny-encoder.int8.onnx','tiny-decoder.int8.onnx','tiny-tokens.txt','../../unrelated']:
            value=b'example model data';member=tarfile.TarInfo('model/'+name);member.size=len(value);package.addfile(member,io.BytesIO(value))
    raw=archive.getvalue();service=local(tmp_path)
    from switchboard import runtime
    grants=[]
    monkeypatch.setattr(voice,'sys',SimpleNamespace(platform='win32'))
    monkeypatch.setattr(runtime,'grant_node_user_access',lambda path:grants.append(path))
    monkeypatch.setattr(voice,'MODEL_BYTES',len(raw));monkeypatch.setattr(voice,'MODEL_SHA256',hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(voice.importlib.util,'find_spec',lambda name:object())
    class Download:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_bytes(self,*args):yield raw
    class Client(Download):
        def __init__(self,**kwargs):pass
        def stream(self,*args):return Download()
    monkeypatch.setattr(voice.httpx,'Client',Client)
    assert service.install()['ready']
    assert grants==[service.folder]
    assert service.install()['ready'] and grants==[service.folder,service.folder]
    assert sorted(p.name for p in service.folder.iterdir())==['decoder.onnx','encoder.onnx','manifest.json','tokens.txt']
    assert not (tmp_path/'unrelated').exists()
    assert len(json.loads((service.folder/'manifest.json').read_text()))==3
    (service.folder/'manifest.json').unlink();monkeypatch.setattr(voice,'MODEL_SHA256','invalid')
    with pytest.raises(ValueError,match='verification'):service.install()
    assert service.status()['state']=='failed' and not list(service.folder.parent.glob('.speech-*'))


async def test_hub_auth_and_origin_protect_voice_side_effects(tmp_path,monkeypatch):
    engine=make_engine(tmp_path);app=create_app(engine)
    monkeypatch.setattr(app.state.local_voice.voice,'install',lambda:pytest.fail('An unapproved download started'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('10.1.1.2',1234)),base_url='http://hub') as client:
        assert (await client.get('/api/local/voice/status')).status_code==401
        headers={'Authorization':'Bearer test-token'}
        assert (await client.get('/api/local/voice/status',headers=headers)).json()['node']=='hub'
        assert (await client.post('/api/local/voice/setup',headers=headers,json={})).status_code==400
        for origin in ['null','https://evil.example','http://hub:9999']:
            assert (await client.post('/api/local/voice/setup',headers=dict(headers,Origin=origin),json={'confirm':True})).status_code==403
        assert (await client.post('/api/local/voice/transcribe',headers=headers,content=bytes(voice.MAX_AUDIO_BYTES+1))).status_code==413
    await engine.close()


async def test_desktop_relay_transcribes_on_this_computer_not_the_hub(tmp_path,monkeypatch):
    engine=make_engine(tmp_path)
    calls=[]
    def transcribe(self,data,language):calls.append((self.node,data,language));return {'text':'Bonjour Laith','local':True,'node':self.node}
    monkeypatch.setattr(voice.LocalVoice,'transcribe',transcribe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_proxy_app(engine.config)),base_url='http://localhost:8766') as client:
        data=wav()
        result=await client.post('/api/local/voice/transcribe?language=fr',content=data,headers={'Origin':'http://localhost:8766'})
        assert result.json()['text']=='Bonjour Laith' and calls==[('hub',data,'fr')]
        assert (await client.post('/api/local/voice/transcribe',content=data,headers={'Origin':'http://localhost:9999'})).status_code==403
        assert len(calls)==1
    assert not list(tmp_path.rglob('*.wav'))  # recordings never enter the task/output store
    await engine.close()
