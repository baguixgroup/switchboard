/* Dictation and spoken chat share the regular Laith chat, spending and approval flow. */
window.createLaithVoice=function({send,portrait}){
  let binding=null,active=null,epoch=0,opening=false;
  const language=()=>store.get('laith_voice_language')||'';
  const selectedVoice=()=>store.get('laith_voice_output')||'';
  const phase=(text)=>{if(active?.status)active.status.textContent=text;};
  const alive=(session)=>active===session&&session.epoch===epoch;
  const sleep=(ms)=>new Promise(resolve=>setTimeout(resolve,ms));
  function paint(){
    if(!binding)return;
    binding.dictate.setAttribute('aria-pressed',String(active?.mode==='dictation'));
    binding.dictate.setAttribute('aria-label',active?.mode==='dictation'?'Finish dictation':'Dictate message');
    binding.dictate.title=active?.mode==='dictation'?'Finish dictation':'Dictate message';
    binding.dictate.innerHTML=I(active?.mode==='dictation'?'stop':'mic');
    binding.talk.setAttribute('aria-pressed',String(active?.mode==='chat'));
    binding.dictate.classList.toggle('recording',active?.mode==='dictation');
    binding.talk.classList.toggle('recording',active?.mode==='chat');
  }
  function stop(){
    const previous=active;active=null;epoch++;opening=false;
    if(previous){clearTimeout(previous.deadline);previous.abort?.abort();previous.capture?.stop();previous.cancelSpeech?.();if(previous.outputContext&&previous.outputContext.state!=='closed')previous.outputContext.close().catch(()=>{});previous.panel?.remove();}
    paint();
  }
  function notice(session,text){
    if(!alive(session))return;
    session.capture?.stop();session.capture=null;session.processing=false;phase(text);
    const resume=session.panel.querySelector('[data-voice-resume]');if(resume)resume.hidden=false;
  }
  async function request(path,options={}){
    const headers=TOKEN?{Authorization:'Bearer '+TOKEN}:{};
    if(options.json!==undefined)headers['Content-Type']='application/json';
    else if(options.body)headers['Content-Type']='audio/wav';
    const controller=new AbortController(),abort=()=>controller.abort();let timedOut=false;
    if(options.signal?.aborted)controller.abort();else options.signal?.addEventListener('abort',abort,{once:true});
    const timer=setTimeout(()=>{timedOut=true;controller.abort();},120000);
    try{
      const response=await fetch('/api/local/voice/'+path,{method:options.body||options.json!==undefined?'POST':'GET',headers,
        body:options.json!==undefined?JSON.stringify(options.json):options.body,signal:controller.signal});
      if(options.audio&&response.ok)return await response.arrayBuffer();
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw Error(data.detail||'Local speech is unavailable. Update Tazrivo on this computer.');
      return data;
    }catch(error){if(timedOut)throw Error('Speech took too long to respond. Check the connection, then try again.');throw error;}
    finally{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
  }
  async function ensureReady(mode){
    const data=await request('status');if(data.ready)return true;
    if(!data.available)throw Error('This app needs the current Tazrivo voice update.');
    return new Promise(resolve=>{
      let closed=false,timer;
      const m=modal({title:'Set up speech once',body:`<p>Download a ${Math.ceil(data.download_bytes/1000000)} MB speech model to <b>${esc(data.node)}</b>. Dictation then runs there without an API key or per-minute transcription charge.</p><p class="small muted">Your recordings are processed in memory and are not saved. Voice chat sends the recognized words using your selected chat plan and existing approval settings.</p><p id="speechSetupStatus" role="status">${esc(data.error||'')}</p>`,foot:'<button data-close>Cancel</button><button id="speechDownload" class="primary">Download speech model</button>',onClose:()=>{closed=true;clearTimeout(timer);resolve(false);}});
      $('#speechDownload',m.el).onclick=async()=>{
        const button=$('#speechDownload',m.el);button.disabled=true;
        try{
          await request('setup',{json:{confirm:true}});
          const check=async()=>{
            if(closed)return;
            try{
              const progress=await request('status');
              $('#speechSetupStatus',m.el).textContent=progress.state==='installing'?'Preparing local speech…':`Downloading… ${Math.round(progress.received/1000000)} / ${Math.ceil(progress.total/1000000)} MB`;
              if(progress.ready){button.disabled=false;button.textContent=mode==='chat'?'Continue to voice chat':'Start dictation';button.onclick=()=>{resolve(true);m.close();};$('#speechSetupStatus',m.el).textContent='Speech is ready.';return;}
              if(['failed','unavailable'].includes(progress.state))throw Error(progress.error);
              timer=setTimeout(check,700);
            }catch(error){$('#speechSetupStatus',m.el).textContent=error.message;button.disabled=false;}
          };check();
        }catch(error){$('#speechSetupStatus',m.el).textContent=error.message;button.disabled=false;}
      };
    });
  }
  function wav(chunks,rate){
    const length=chunks.reduce((n,c)=>n+c.length,0),all=new Float32Array(length);let offset=0;
    for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.length;}
    const count=Math.floor(length*16000/rate),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer);
    const text=(position,value)=>[...value].forEach((ch,i)=>view.setUint8(position+i,ch.charCodeAt(0)));
    text(0,'RIFF');view.setUint32(4,36+count*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);
    view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,count*2,true);
    for(let i=0;i<count;i++){
      const from=Math.floor(i*rate/16000),to=Math.min(length,Math.max(from+1,Math.floor((i+1)*rate/16000)));let sample=0;
      for(let j=from;j<to;j++)sample+=all[j];sample=Math.max(-1,Math.min(1,sample/(to-from)));
      view.setInt16(44+i*2,sample<0?sample*32768:sample*32767,true);
    }
    return new Blob([buffer],{type:'audio/wav'});
  }
  async function microphone(session,onSamples){
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw Error('Open the installed app, localhost or HTTPS to use the microphone.');
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(!alive(session)||session.muted){stream.getTracks().forEach(track=>track.stop());return null;}
    let context,node,source;
    const stop=()=>{stream.getTracks().forEach(track=>{track.onended=null;track.stop();});node?.disconnect();source?.disconnect();if(context&&context!==session.outputContext&&context.state!=='closed')context.close().catch(()=>{});};
    try{
      context=session.outputContext||new (window.AudioContext||window.webkitAudioContext)();
      if(!context.audioWorklet||typeof AudioWorkletNode==='undefined')throw Error('Update your system browser to use microphone input in this app.');
      if(!session.workletReady){await context.audioWorklet.addModule('/static/voice-worklet.js');session.workletReady=true;}
      if(!alive(session)||session.muted){stop();return null;}
      source=context.createMediaStreamSource(stream);node=new AudioWorkletNode(context,'laith-microphone');
      node.port.onmessage=event=>{if(alive(session))onSamples(event.data,context.sampleRate);};
      source.connect(node);node.connect(context.destination);await context.resume();
      stream.getAudioTracks()[0].onended=()=>notice(session,'Microphone disconnected. Reconnect it, then Resume.');
      return {stop,rate:context.sampleRate};
    }catch(error){stop();throw error;}
  }
  function panel(session,mode){
    const el=document.createElement('section');el.className='laith-voice-panel';el.setAttribute('role','dialog');el.setAttribute('aria-label',mode==='chat'?'Voice chat with Laith':'Dictation');
    el.innerHTML=`<div class="voice-avatar">${mode==='chat'?portrait():I('mic')}</div><div class="voice-bars" aria-hidden="true">${'<i></i>'.repeat(7)}</div><p class="voice-phase" role="status">Starting microphone…</p><p class="voice-words"></p><div class="row">${mode==='chat'?'<button type="button" data-voice-mute>Mute</button><button type="button" data-voice-resume hidden>Resume</button>':'<button type="button" data-voice-cancel>Cancel</button>'}<button type="button" data-voice-end class="primary">${mode==='chat'?'End conversation':'Finish dictation'}</button></div><p class="tiny muted">${mode==='chat'?'AI-generated device voice · your current chat plan':'Local transcription · review before sending'}</p>`;
    document.body.append(el);session.panel=el;session.status=el.querySelector('.voice-phase');
    el.querySelector('[data-voice-cancel]')?.addEventListener('click',stop);
    return el;
  }
  function level(session,value){session.panel?.style.setProperty('--voice-level',Math.min(1,value*12));}
  function errorText(error){return error.name==='NotAllowedError'?'Microphone access was denied. Allow Tazrivo in your microphone settings, then try again.':error.name==='NotFoundError'?'No microphone was found. Connect one and try again.':error.name==='NotReadableError'?'The microphone is busy. Close the other recording app and try again.':error.message;}
  async function dictate(){
    if(active?.mode==='dictation'){await active.finish?.();return;}
    if(active)stop();
    if(opening)return;opening=true;const startEpoch=epoch;let preparedContext;
    try{
      preparedContext=new(window.AudioContext||window.webkitAudioContext)();preparedContext.resume().catch(()=>{});
      if(!await ensureReady('dictation')||epoch!==startEpoch)return;
      const input=binding?.input;if(!input?.isConnected)return;
      const original=input.value,start=input.selectionStart,end=input.selectionEnd;
      const session={mode:'dictation',epoch:++epoch,abort:new AbortController(),outputContext:preparedContext};active=session;paint();
      const el=panel(session,'dictation');let chunks=[],frames=0,rate=16000,finishing=false;
      session.finish=async()=>{
        if(finishing||!alive(session))return;finishing=true;clearTimeout(session.deadline);session.capture?.stop();session.capture=null;
        phase('Transcribing…');el.querySelector('[data-voice-end]').disabled=true;
        try{
          const result=await request('transcribe?language='+encodeURIComponent(language()),{body:wav(chunks,rate),signal:session.abort.signal});
          if(!alive(session)||!input.isConnected)return;
          if(!result.text){toast('No speech was recognized. Try again.',true);return;}
          if(input.value===original){const left=original.slice(0,start),right=original.slice(end);input.value=(left+(left&&!/\s$/.test(left)?' ':'')+result.text+(right&&!/^\s/.test(right)?' ':'')+right).slice(0,12000);}
          else input.value=(input.value+(input.value?' ':'')+result.text).slice(0,12000);
          input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
        }catch(error){if(alive(session))toast(errorText(error),true);}finally{if(alive(session))stop();}
      };
      el.querySelector('[data-voice-end]').onclick=session.finish;
      session.capture=await microphone(session,(samples,sampleRate)=>{if(finishing)return;chunks.push(samples);frames+=samples.length;rate=sampleRate;level(session,Math.sqrt(samples.reduce((sum,v)=>sum+v*v,0)/samples.length));if(frames/rate>=58.8)session.finish();});
      if(!alive(session))return;phase('Listening… speak your message');session.deadline=setTimeout(session.finish,59000);
    }catch(error){stop();toast(errorText(error),true);}finally{opening=false;if(preparedContext&&active?.outputContext!==preparedContext&&preparedContext.state!=='closed')preparedContext.close().catch(()=>{});}
  }
  async function speak(session,text,lang){
    const clean=text.replace(/```[\s\S]*?```/g,' Code is included in the chat. ').replace(/!\[[^\]]*\]\([^)]*\)/g,'').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/[*#`_>|]/g,'').slice(0,1800);
    if(session.nativeSpeech){
      const bytes=await request('speak',{json:{text:clean,language:language()||lang||''},audio:true,signal:session.abort.signal});
      if(!alive(session))return;
      const buffer=await session.outputContext.decodeAudioData(bytes);if(!alive(session))return;
      return await new Promise((resolve,reject)=>{
        const source=session.outputContext.createBufferSource();source.buffer=buffer;source.connect(session.outputContext.destination);
        let timer;const done=()=>{clearTimeout(timer);session.cancelSpeech=null;source.disconnect();resolve();};
        source.onended=done;session.cancelSpeech=()=>{source.onended=null;source.stop();done();};
        source.start();timer=setTimeout(()=>{source.onended=null;source.stop();source.disconnect();session.cancelSpeech=null;reject(Error('Playback did not finish. Read the reply in chat.'));},Math.min(125000,buffer.duration*1000+10000));
      });
    }
    return new Promise((resolve,reject)=>{
      const utterance=new SpeechSynthesisUtterance(clean);utterance.lang=language()||lang||navigator.language;
      const voices=speechSynthesis.getVoices();utterance.voice=voices.find(v=>v.voiceURI===selectedVoice())||voices.find(v=>v.lang.split('-')[0]===utterance.lang.split('-')[0])||null;
      let timer;const done=()=>{clearTimeout(timer);session.cancelSpeech=null;resolve();};
      utterance.onend=done;utterance.onerror=event=>{clearTimeout(timer);session.cancelSpeech=null;reject(Error(event.error==='not-allowed'?'Allow audio playback, then Resume.':'Speech playback stopped. You can read the reply in chat.'));};
      session.cancelSpeech=()=>{clearTimeout(timer);utterance.onend=null;utterance.onerror=null;speechSynthesis.cancel();resolve();};
      speechSynthesis.speak(utterance);timer=setTimeout(()=>{utterance.onend=null;utterance.onerror=null;speechSynthesis.cancel();session.cancelSpeech=null;reject(Error('Speech playback timed out. Read the reply in chat, then Resume.'));},120000);
    });
  }
  async function listen(session){
    if(!alive(session)||session.muted||session.processing||session.capture||session.openingMic)return;
    session.openingMic=true;
    let chunks=[],frames=0,rate=16000,talking=false,lastSpeech=0,started=Date.now(),processing=false;
    phase('Listening…');session.panel.querySelector('[data-voice-resume]').hidden=true;
    try{session.capture=await microphone(session,async(samples,sampleRate)=>{
      if(processing||session.muted)return;
      rate=sampleRate;const rms=Math.sqrt(samples.reduce((sum,v)=>sum+v*v,0)/samples.length);level(session,rms);
      chunks.push(samples);frames+=samples.length;
      if(rms>.012){talking=true;lastSpeech=Date.now();}
      if(!talking){while(frames>rate*.4&&chunks.length>1)frames-=chunks.shift().length;if(Date.now()-started>30000)notice(session,'Microphone paused. Tap Resume when you are ready.');return;}
      if(Date.now()-lastSpeech<1100&&frames/rate<28)return;
      processing=true;session.processing=true;session.capture?.stop();session.capture=null;level(session,0);phase('Transcribing…');
      try{
        const transcript=await request('transcribe?language='+encodeURIComponent(language()),{body:wav(chunks,rate),signal:session.abort.signal});
        if(!alive(session))return;
        if(!transcript.text){session.processing=false;phase('No speech recognized. Listening again…');await listen(session);return;}
        session.panel.querySelector('.voice-words').textContent=transcript.text;phase('Laith is thinking…');
        const result=await send(transcript.text,session.abort.signal);
        if(!alive(session))return;
        if(!result){notice(session,'Request cancelled. Tap Resume to continue.');return;}
        session.chatId=result.chatId;
        const until=Date.now()+180000;let answer='';
        while(alive(session)&&Date.now()<until){
          const chat=await api('/api/laith/chats/'+encodeURIComponent(result.chatId));if(!alive(session))return;
          const turn=chat.turns.find(t=>t.id===result.turnId);
          if(turn?.status==='done'){answer=turn.answer||'';break;}
          if(turn&&['failed','interrupted','cancelled','waiting'].includes(turn.status))throw Error(turn.error||'This reply needs attention in chat.');
          await sleep(1400);
        }
        if(!alive(session))return;
        if(!answer)throw Error('The reply is taking longer. It is saved in chat. Tap Resume when ready.');
        phase('Laith is speaking…');await speak(session,answer,transcript.language);
        session.processing=false;
        if(alive(session)&&!session.muted)await listen(session);
        else if(alive(session))phase('Microphone muted');
      }catch(error){session.processing=false;if(alive(session))notice(session,errorText(error));}
    });if(alive(session)&&!session.muted&&session.capture)phase('Listening…');}finally{session.openingMic=false;}
  }
  async function talk(){
    if(active?.mode==='chat'){stop();return;}
    if(active)stop();
    if(opening)return;opening=true;const startEpoch=epoch;
    try{
      if(!await ensureReady('chat')||epoch!==startEpoch)return;
      const data=await request('status');
      const browserVoices=window.speechSynthesis?.getVoices()||[];
      if(!data.speech_available&&!browserVoices.length)throw Error('No reply voice is available. Open the Mac/Windows app, or install a device voice on your hub.');
      const m=modal({title:'Voice chat with Laith',body:`<p>Speak naturally. Laith replies aloud using your current model, plan and effort. Existing spending and tool approvals still apply.</p><p class="small muted">Speech recognition runs on ${esc(data.node)}. Microphone recordings are not saved. Recognized messages and replies are saved in this chat.</p><details class="more"><summary>Voice options</summary><label class="field">Language<select id="speechLanguage">${Object.entries(data.languages).map(([key,name])=>`<option value="${key}" ${key===language()?'selected':''}>${esc(name)}</option>`).join('')}</select></label><label class="field">Reply voice<select id="speechOutput"><option value="">Device voice (free)</option>${browserVoices.map(v=>`<option value="${esc(v.voiceURI)}" ${v.voiceURI===selectedVoice()?'selected':''}>${esc(v.name)} · ${esc(v.lang)}</option>`).join('')}</select></label></details>`,foot:'<button data-close>Cancel</button><button id="beginVoiceChat" class="primary">Start conversation</button>'});
      $('#beginVoiceChat',m.el).onclick=async()=>{
        store.set('laith_voice_language',$('#speechLanguage',m.el).value);store.set('laith_voice_output',$('#speechOutput',m.el).value);m.close();
        const session={mode:'chat',epoch:++epoch,muted:false,abort:new AbortController(),chatId:binding?.chatId||'',nativeSpeech:data.speech_available&&!browserVoices.some(v=>v.voiceURI===selectedVoice())};active=session;paint();
        const el=panel(session,'chat');el.querySelector('[data-voice-end]').onclick=stop;
        el.querySelector('[data-voice-mute]').onclick=()=>{
          session.muted=!session.muted;el.querySelector('[data-voice-mute]').textContent=session.muted?'Unmute':'Mute';
          if(session.muted){session.capture?.stop();session.capture=null;phase('Microphone muted');}
          else if(!session.processing&&!session.cancelSpeech){listen(session).catch(error=>notice(session,errorText(error)));}
        };
        el.querySelector('[data-voice-resume]').onclick=()=>{session.muted=false;el.querySelector('[data-voice-mute]').textContent='Mute';listen(session).catch(error=>notice(session,errorText(error)));};
        session.deadline=setTimeout(()=>{stop();toast('Voice conversation ended after 10 minutes. You can start another.');},600000);
        try{
          session.outputContext=new(window.AudioContext||window.webkitAudioContext)();await session.outputContext.resume();
          await listen(session);
        }catch(error){notice(session,errorText(error));}
      };
    }catch(error){toast(errorText(error),true);}finally{opening=false;}
  }
  window.addEventListener('pagehide',stop);
  window.addEventListener('hashchange',()=>{if(active?.mode==='dictation')stop();else if(active?.mode==='chat'&&location.hash!=='#laith/voice'&&location.hash!=='#laith/chat/'+active.chatId)stop();});
  return {bind(value){binding=value;value.dictate.onclick=dictate;value.talk.onclick=talk;paint();},stop,phase,
    expectChat(key){if(active?.mode==='chat')active.chatId=key;},talk,dictate};
};
