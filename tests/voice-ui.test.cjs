const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const flush=()=>new Promise(resolve=>setImmediate(resolve));

class Element {
  constructor(){this.value='';this.isConnected=true;this.children=new Map();this.attributes={};this.hidden=false;this.disabled=false;this.style={setProperty(){}};this.classList={toggle(){}};}
  set innerHTML(value){this.html=value;}
  get innerHTML(){return this.html;}
  querySelector(selector){if(!this.children.has(selector))this.children.set(selector,new Element());return this.children.get(selector);}
  setAttribute(key,value){this.attributes[key]=value;}
  remove(){this.isConnected=false;}
  addEventListener(event,handler){this['on'+event]=handler;}
  dispatchEvent(event){this['on'+event.type]?.(event);}
  focus(){}
}

function harness({permission,transcription,nativeSpeech=false}={}){
  const panels=[],modals=[],streams=[],nodes=[],contexts=[],calls=[],speeches=[],toasts=[],listeners={},storage=new Map();let now=0;
  const body={append(el){panels.push(el);}};
  const stream=()=>{const track={stopped:false,stop(){this.stopped=true;}};const value={track,getTracks:()=>[track],getAudioTracks:()=>[track]};streams.push(value);return value;};
  class Context {
    constructor(){this.sampleRate=48000;this.state='running';this.audioWorklet={addModule:async name=>{assert.equal(name,'/static/voice-worklet.js');}};contexts.push(this);}
    createMediaStreamSource(){return {connect(){},disconnect(){}};}
    async resume(){}
    async close(){this.state='closed';}
    async decodeAudioData(){return {duration:1};}
    createBufferSource(){const source={connect(){},disconnect(){},start(){speeches.push(source);},stop(){}};return source;}
  }
  const globals={Blob,AbortController,Float32Array,DataView,ArrayBuffer,Event,Promise,setTimeout,clearTimeout,
    Date:class extends Date{static now(){return now;}},
    window:{isSecureContext:true,AudioContext:Context,addEventListener:(type,fn)=>listeners[type]=fn},
    navigator:{language:'en-US',mediaDevices:{getUserMedia:async()=>permission?await permission(stream):stream()}},
    document:{body,createElement:()=>new Element()},location:{hash:'#laith/chat/chat-1'},TOKEN:'',
    AudioWorkletNode:class{constructor(){this.port={};nodes.push(this);}connect(){}disconnect(){}},
    SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},
    speechSynthesis:{getVoices:()=>[{voiceURI:'local',lang:'en-US',name:'Device'}],speak(utterance){speeches.push(utterance);},cancel(){speeches.push('cancelled');}},
    store:{get:key=>storage.get(key),set:(key,value)=>storage.set(key,value)},
    I:name=>name,esc:value=>String(value),toast:(text)=>toasts.push(text),
    $:(selector,el)=>el.querySelector(selector),
    modal:({onClose,...options})=>{const el=new Element();const value={el,options,close:()=>onClose?.()};modals.push(value);return value;},
    api:async()=>({turns:[{id:'turn-1',status:'done',answer:'Hello. **Welcome** to your project.\n```js\nalert(1)\n```'}]}),
    fetch:async(url,options)=>{calls.push({url,...options});if(url.endsWith('/status'))return {ok:true,json:async()=>({ready:true,available:true,speech_available:nativeSpeech,node:'test-mac',languages:{'':'Auto',en:'English'}})};if(url.endsWith('/speak'))return {ok:true,arrayBuffer:async()=>new ArrayBuffer(44)};return {ok:true,json:async()=>transcription?await transcription():({text:'Hello Laith',language:'en'})};}
  };
  globals.window.speechSynthesis=globals.speechSynthesis;globals.window.SpeechSynthesisUtterance=globals.SpeechSynthesisUtterance;
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../switchboard/web/static/voice.js'),'utf8'),globals);
  const sent=[];const controller=globals.window.createLaithVoice({send:async text=>{sent.push(text);return {chatId:'chat-1',turnId:'turn-1'};},portrait:()=>''});
  const input=new Element(),dictate=new Element(),talk=new Element();input.selectionStart=input.selectionEnd=0;controller.bind({input,dictate,talk,chatId:'chat-1'});
  return {controller,input,dictate,talk,panels,modals,streams,nodes,contexts,calls,speeches,toasts,sent,globals,
    advance:ms=>now+=ms,emit(value=.05){nodes.at(-1).port.onmessage({data:new Float32Array(2048).fill(value)});}};
}

test('dictation inserts at the cursor, never sends, and closes the microphone before transcription',async()=>{
  const h=harness();h.input.value='Before after';h.input.selectionStart=6;h.input.selectionEnd=7;
  await h.dictate.onclick();for(let i=0;i<4;i++)h.emit();
  await h.panels[0].querySelector('[data-voice-end]').onclick();
  assert.equal(h.input.value,'Before Hello Laith after');assert.deepEqual(h.sent,[]);
  assert.ok(h.streams.every(s=>s.track.stopped));assert.ok(h.contexts.every(c=>c.state==='closed'));
  const audio=h.calls.find(c=>c.url.includes('transcribe'));const view=new DataView(await audio.body.arrayBuffer());
  assert.equal(view.getUint32(24,true),16000);assert.equal(view.getUint16(22,true),1);assert.equal(view.getUint32(40,true),Math.floor(8192/3)*2);
  assert.equal(h.dictate.attributes['aria-pressed'],'false');
});

test('dictation preserves edits made while recognition is running',async()=>{
  let resolve;const h=harness({transcription:()=>new Promise(r=>resolve=r)});
  await h.dictate.onclick();for(let i=0;i<4;i++)h.emit();
  const finishing=h.panels[0].querySelector('[data-voice-end]').onclick();await flush();
  h.input.value='I kept typing';resolve({text:'spoken words'});await finishing;
  assert.equal(h.input.value,'I kept typing spoken words');
});

test('cancel while microphone permission is pending releases a late stream',async()=>{
  let allow,create;const h=harness({permission:fn=>{create=fn;return new Promise(resolve=>allow=resolve);}});
  const pending=h.dictate.onclick();await flush();h.panels[0].querySelector('[data-voice-cancel]').onclick();allow(create());await pending;
  assert.ok(h.streams[0].track.stopped);assert.equal(h.calls.filter(c=>c.url.includes('transcribe')).length,0);
});

test('cancel discards a late transcript and aborts its request',async()=>{
  let resolve;const h=harness({transcription:()=>new Promise(r=>resolve=r)});
  await h.dictate.onclick();for(let i=0;i<4;i++)h.emit();
  const finishing=h.panels[0].querySelector('[data-voice-end]').onclick();await flush();h.panels[0].querySelector('[data-voice-cancel]').onclick();
  assert.ok(h.calls.at(-1).signal.aborted);resolve({text:'should not be inserted'});await finishing;assert.equal(h.input.value,'');
});

test('voice chat sends one utterance, pauses input during the reply, then resumes listening',async()=>{
  const h=harness();await h.talk.onclick();assert.equal(h.streams.length,0);
  await h.modals[0].el.querySelector('#beginVoiceChat').onclick();for(let i=0;i<4;i++)h.emit();h.advance(1200);h.emit(0);await flush();await flush();
  assert.deepEqual(h.sent,['Hello Laith']);assert.equal(h.streams.length,1);assert.ok(h.streams[0].track.stopped);
  assert.ok(h.speeches[0].text.includes('Welcome'));assert.ok(!h.speeches[0].text.includes('alert(1)'));
  h.speeches[0].onend();await flush();assert.equal(h.streams.length,2);assert.equal(h.streams[1].track.stopped,false);
  h.panels[0].querySelector('[data-voice-end]').onclick();assert.ok(h.streams.every(s=>s.track.stopped));
});

test('End conversation cancels spoken output and prevents restarting the microphone',async()=>{
  const h=harness();await h.talk.onclick();await h.modals[0].el.querySelector('#beginVoiceChat').onclick();
  for(let i=0;i<4;i++)h.emit();h.advance(1200);h.emit(0);await flush();await flush();
  h.panels[0].querySelector('[data-voice-end]').onclick();await flush();
  assert.equal(h.streams.length,1);assert.ok(h.speeches.includes('cancelled'));assert.equal(h.talk.attributes['aria-pressed'],'false');
});

test('native device audio works when the webview exposes no browser voices',async()=>{
  const h=harness({nativeSpeech:true});h.globals.speechSynthesis.getVoices=()=>[];
  await h.talk.onclick();await h.modals[0].el.querySelector('#beginVoiceChat').onclick();
  for(let i=0;i<4;i++)h.emit();h.advance(1200);h.emit(0);await flush();await flush();
  assert.ok(h.calls.some(c=>c.url.endsWith('/speak')));assert.equal(h.speeches.length,1);
  h.speeches[0].onended();await flush();assert.equal(h.streams.length,2);
  h.controller.stop();assert.ok(h.contexts.every(c=>c.state==='closed'));
});

test('mute while permission is pending never records a late stream',async()=>{
  let allow,create;const h=harness({permission:fn=>{create=fn;return new Promise(resolve=>allow=resolve);}});
  await h.talk.onclick();const starting=h.modals[0].el.querySelector('#beginVoiceChat').onclick();await flush();
  h.panels[0].querySelector('[data-voice-mute]').onclick();allow(create());await starting;
  assert.ok(h.streams[0].track.stopped);h.controller.stop();
});

test('switching from voice chat to dictation closes the old capture first',async()=>{
  const h=harness();await h.talk.onclick();await h.modals[0].el.querySelector('#beginVoiceChat').onclick();
  await h.dictate.onclick();assert.equal(h.streams.length,2);assert.ok(h.streams[0].track.stopped);h.controller.stop();assert.ok(h.streams[1].track.stopped);
});

test('model access errors stop setup polling and keep the microphone off',async()=>{
  const h=harness();h.globals.fetch=async()=>({ok:true,json:async()=>({ready:false,available:true,node:'test',download_bytes:117000000,state:'unavailable',error:'Speech model access needs repair.',received:0,total:117000000})});
  const pending=h.dictate.onclick();await flush();const m=h.modals[0];
  await m.el.querySelector('#speechDownload').onclick();await flush();
  assert.ok(m.el.querySelector('#speechSetupStatus').textContent.includes('access needs repair'));
  assert.equal(m.el.querySelector('#speechDownload').disabled,false);assert.equal(h.streams.length,0);
  m.close();await pending;assert.ok(h.contexts.every(c=>c.state==='closed'));
});

test('the audio worklet passes input samples and produces silence rather than feedback',()=>{
  let Processor;const messages=[];
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../switchboard/web/static/voice-worklet.js'),'utf8'),{
    AudioWorkletProcessor:class{constructor(){this.port={postMessage:value=>messages.push(value)};}},
    Float32Array,registerProcessor:(name,constructor)=>{assert.equal(name,'laith-microphone');Processor=constructor;}
  });
  const processor=new Processor(),output=new Float32Array(128).fill(1);
  for(let i=0;i<16;i++)assert.equal(processor.process([[new Float32Array(128).fill(.25)]],[[output]]),true);
  assert.equal(messages.length,1);assert.equal(messages[0].length,2048);assert.ok(messages[0].every(v=>v===.25));assert.ok(output.every(v=>v===0));
});
