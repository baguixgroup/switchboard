"""Local, bounded speech transcription. Audio is decoded in memory and never saved."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import wave

import httpx

MODEL_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-tiny.tar.bz2'
MODEL_BYTES = 116204861
MODEL_SHA256 = 'c46116994e539aa165266d96b325252728429c12535eb9d8b6a2b10f129e66b1'
MAX_SECONDS = 60
MAX_AUDIO_BYTES = 16000 * 2 * MAX_SECONDS + 4096
LANGUAGES = {'': 'Auto detect', 'en': 'English', 'fr': 'French', 'ar': 'Arabic', 'es': 'Spanish', 'de': 'German', 'it': 'Italian', 'pt': 'Portuguese', 'nl': 'Dutch', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean', 'hi': 'Hindi'}


class LocalVoice:
    def __init__(self, config):
        self.node = config.node_name
        self.folder = config.home / 'voice' / 'whisper-tiny-v1'
        self.progress = {'state': 'idle', 'received': 0, 'total': MODEL_BYTES, 'error': ''}
        self._lock = threading.Lock()
        self._setup_lock = threading.Lock()
        self._recognizer = None
        self._language = None
        self._speech_lock = threading.Lock()

    def status(self):
        runtime = importlib.util.find_spec('sherpa_onnx') is not None and importlib.util.find_spec('numpy') is not None
        progress=dict(self.progress)
        try:ready = runtime and all((self.folder / name).is_file() for name in ('encoder.onnx', 'decoder.onnx', 'tokens.txt', 'manifest.json'))
        except OSError:
            ready=False;progress.update(state='unavailable',error='This desktop session cannot read the speech model. Repair its access or download it again from this app.')
        return dict(progress, ready=ready, available=runtime, node=self.node, local=True,
                    download_bytes=MODEL_BYTES, max_seconds=MAX_SECONDS, languages=LANGUAGES,
                    model='Whisper tiny', cost_usd=0, speech_available=self.speech_available())

    @staticmethod
    def speech_available():
        return sys.platform in ('darwin','win32') or bool(shutil.which('espeak-ng') or shutil.which('espeak'))

    def speak(self, text, language=''):
        """Render device speech into a short-lived file; never invoke a shell with model text."""
        if not isinstance(text,str) or not text.strip() or len(text)>3500:raise ValueError('Choose between 1 and 3,500 characters to read aloud.')
        if language not in LANGUAGES:language=''
        if not self.speech_available():raise ValueError('No device voice is installed. Use a browser voice, or install espeak-ng on the hub.')
        if not self._speech_lock.acquire(blocking=False):raise ValueError('A voice reply is already being prepared. Try again in a moment.')
        try:
            with tempfile.TemporaryDirectory(prefix='tazrivo-speech-') as folder:
                target=Path(folder)/'reply.wav'
                if sys.platform=='darwin':
                    source=Path(folder)/'reply.aiff';args=['/usr/bin/say','-f','-','-o',str(source)]
                    if language:
                        import re
                        listing=subprocess.run(['/usr/bin/say','-v','?'],capture_output=True,text=True,timeout=10,check=True).stdout
                        voices=[]
                        for line in listing.splitlines():
                            match=re.match(r'(.+?)\s+([a-z]{2})_[A-Z]{2}\s+#',line)
                            if match and match[2]==language:voices.append(match[1].strip())
                        preferred={'en':['Daniel','Alex','Samantha'],'fr':['Thomas','Amelie'],'ar':['Maged']}.get(language,[])
                        chosen=next((name for name in preferred if name in voices),voices[0] if voices else None)
                        if chosen:args+=['-v',chosen]
                    subprocess.run(args,input=text,encoding='utf-8',capture_output=True,timeout=90,check=True)
                    subprocess.run(['/usr/bin/afconvert','-f','WAVE','-d','LEI16@16000','-c','1',str(source),str(target)],capture_output=True,timeout=30,check=True)
                elif sys.platform=='win32':
                    import base64
                    script='''$ErrorActionPreference='Stop'; [Console]::InputEncoding=[Text.UTF8Encoding]::new(); $data=[Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$voice=[System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
  $selected=$voice.GetInstalledVoices() | Where-Object {$_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq $data.language} | Select-Object -First 1
  if($selected){$voice.SelectVoice($selected.VoiceInfo.Name)}
  $format=[System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
  $voice.SetOutputToWaveFile($data.output,$format); $voice.Speak($data.text)
} finally {$voice.Dispose()}
'''
                    encoded=base64.b64encode(script.encode('utf-16le')).decode()
                    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                        input=json.dumps({'text':text,'language':language,'output':str(target)},ensure_ascii=False),encoding='utf-8',capture_output=True,timeout=90,check=True,creationflags=0x08000000)
                else:
                    command=shutil.which('espeak-ng') or shutil.which('espeak')
                    subprocess.run([command,'--stdin','-v',language or 'en','-w',str(target)],input=text,encoding='utf-8',capture_output=True,timeout=90,check=True)
                if not target.exists() or not 44<target.stat().st_size<=10_000_000:raise ValueError('Device speech did not produce a playable reply.')
                return target.read_bytes()
        except (subprocess.SubprocessError,OSError):raise ValueError('Device speech could not play this reply. Check installed voices and try again.') from None
        finally:self._speech_lock.release()

    def install(self):
        if not self._setup_lock.acquire(blocking=False):return self.status()
        stage = None
        try:
            if self.status()['ready']:
                if sys.platform=='win32':
                    from .runtime import grant_node_user_access
                    grant_node_user_access(self.folder)
                return self.status()
            if not self.status()['available']:raise ValueError('This installation is missing its speech runtime. Install the current Tazrivo desktop update.')
            self.progress.update(state='downloading', received=0, error='')
            self.folder.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='.speech-', dir=self.folder.parent))
            archive = stage / 'model.tar.bz2'; digest = hashlib.sha256()
            with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True, trust_env=False) as client:
                with client.stream('GET', MODEL_URL) as response:
                    response.raise_for_status()
                    with archive.open('wb') as out:
                        for chunk in response.iter_bytes(256*1024):
                            self.progress['received'] += len(chunk)
                            if self.progress['received'] > MODEL_BYTES:raise ValueError('Speech download was larger than expected.')
                            digest.update(chunk); out.write(chunk)
            if self.progress['received'] != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
                raise ValueError('Speech model verification failed. Try downloading it again.')
            self.progress['state'] = 'installing'
            extracted = stage / 'ready'; extracted.mkdir()
            required = {'tiny-encoder.int8.onnx': 'encoder.onnx', 'tiny-decoder.int8.onnx': 'decoder.onnx', 'tiny-tokens.txt': 'tokens.txt'}
            manifest = {}
            with tarfile.open(archive, 'r:bz2') as source:
                for member in source:
                    name = Path(member.name).name
                    if name not in required:continue
                    if not member.isfile() or member.size > 200_000_000:raise ValueError('Invalid speech model archive.')
                    target = extracted / required[name]
                    if target.exists():raise ValueError('Duplicate speech model file.')
                    with source.extractfile(member) as inp, target.open('wb') as out:shutil.copyfileobj(inp, out)
                    manifest[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
            if len(manifest) != 3:raise ValueError('Speech model files were incomplete.')
            (extracted/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            if self.folder.exists():shutil.rmtree(self.folder)
            os.replace(extracted, self.folder)
            if sys.platform=='win32':
                from .runtime import grant_node_user_access
                grant_node_user_access(self.folder)
            self.progress.update(state='ready', error='')
            return self.status()
        except Exception as exc:
            self.progress.update(state='failed', error='Could not prepare local speech. Check the network and try again.')
            if isinstance(exc, ValueError):self.progress['error'] = str(exc)
            raise ValueError(self.progress['error']) from None
        finally:
            if stage:shutil.rmtree(stage, ignore_errors=True)
            self._setup_lock.release()

    def transcribe(self, data: bytes, language=''):
        if language not in LANGUAGES:raise ValueError('Choose a supported speech language.')
        if len(data) > MAX_AUDIO_BYTES:raise ValueError('Record up to 60 seconds at a time.')
        try:
            with wave.open(io.BytesIO(data), 'rb') as audio:
                frames = audio.getnframes()
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000 or audio.getcomptype() != 'NONE':
                    raise ValueError('Use mono 16 kHz PCM WAV audio.')
                if not 1600 <= frames <= MAX_SECONDS*16000:raise ValueError('Record between 0.1 and 60 seconds.')
                raw = audio.readframes(frames)
                if len(raw) != frames*2:raise ValueError('The recording was incomplete. Try again.')
        except (wave.Error, EOFError):raise ValueError('The recording could not be read. Try again.') from None
        if not self.status()['ready']:raise ValueError('Set up local speech first.')
        if not self._lock.acquire(blocking=False):raise ValueError('Speech is already processing another recording. Try again in a moment.')
        try:
            import numpy as np
            samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768
            if float(np.max(np.abs(samples))) < .002:return {'text':'', 'language':language, 'local':True, 'node':self.node}
            if self._recognizer is None or self._language != language:
                import sherpa_onnx
                manifest = json.loads((self.folder/'manifest.json').read_text())
                for name in ('encoder.onnx', 'decoder.onnx', 'tokens.txt'):
                    if hashlib.sha256((self.folder/name).read_bytes()).hexdigest() != manifest.get(name):
                        (self.folder/'manifest.json').unlink(missing_ok=True)
                        raise ValueError('The speech model needs to be downloaded again.')
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=str(self.folder/'encoder.onnx'), decoder=str(self.folder/'decoder.onnx'),
                    tokens=str(self.folder/'tokens.txt'), num_threads=min(4, os.cpu_count() or 1),
                    language=language, task='transcribe', provider='cpu')
                self._language = language
            stream = self._recognizer.create_stream(); stream.accept_waveform(16000, samples)
            self._recognizer.decode_stream(stream)
            return {'text':stream.result.text.strip()[:12000], 'language':stream.result.lang, 'local':True, 'node':self.node}
        finally:self._lock.release()
