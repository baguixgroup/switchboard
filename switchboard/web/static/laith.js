/* Laith is part of Tazrivo: shared providers, authenticated storage and Studio goals. */
window.installLaith = function(pages) {
  let library, chat, routeArg="", desktop={available:false}, sessionsOpen=false, sending=false;
  const options={model:"",ability:"conversation",memory:true,knowledge:false,tools:false,billing:"plans_only",budget:0,effort:""};
  const drafts={},attachments={};
  let previewEpoch=0;
  const effortChoices=[["","Auto"],["low","Low"],["medium","Medium"],["high","High"],["xhigh","Extra high"],["max","Max"]];
  const portrait=()=>`<img src="${esc(library?.personalization?.presentation?.image||'/static/laith.png')}" alt="Laith, a friendly Atlas lion" class="laith-portrait">`;
  const field=(name,html)=>`<label class="field">${name}${html}</label>`;
  const models=()=>`<option value="">Automatic</option>${S.models.filter((m)=>m.available&&!m.features.includes("image_gen")&&!m.features.includes("handoff")).map((m)=>`<option value="${esc(m.key)}" ${options.model===m.key?"selected":""}>${esc(m.label)}${m.available?"":" · not connected"}</option>`).join("")}`;
  const activeTurn=()=>chat&&chat.turns.find((t)=>["queued","running","waiting"].includes(t.status));
  const pulseLinks=(items,empty)=>items.length?items.map((g)=>`<a class="laith-pulse-item" href="#studio/run/${esc(g.run_id)}"><span>${esc(g.title)}</span>${I("arrow")}</a>`).join(""):`<p class="small muted">${empty}</p>`;
  const tabs=(tab)=>`<div class="tabs">${[["","Conversations"],["personality","Personality & appearance"],["memory","Memory"],["abilities","Abilities"],["checkins","Check-ins"]].map(([key,label])=>`<a class="btn sm ${tab===key?"primary":"ghost"}" href="#laith${key?"/"+key:""}">${label}</a>`).join("")}</div>`;

  pages.laith=async function(arg="") {
    const me=rid;routeArg=arg;
    [library,desktop]=await Promise.all([api("/api/laith"),api("/api/local/laith-desktop").catch(()=>({available:false}))]);if(stale(me))return;
    chat=null;
    if(arg.startsWith("chat/")){chat=await api("/api/laith/chats/"+encodeURIComponent(arg.slice(5)));if(stale(me))return;}
    const tab=["personality","memory","abilities","checkins"].includes(arg)?arg:"";
    view.innerHTML=head("Laith","",`<div class="row"><button class="ghost sm" id="laithHistory" aria-label="Chat history">${I("history")}</button><button class="ghost sm" id="laithNew" aria-label="New chat">${I("plus")}</button><button class="sm" id="laithDesktop" ${desktop.available?"":"disabled"}>${desktop.visible?"Hide Laith":"Show Laith"}</button><button class="ghost sm" id="laithSettings" aria-label="Chat settings">${I("sliders")}</button></div>`)+(tab?tabs(tab):"")+`<div id="laithContent"></div>`;
    $("#laithDesktop").onclick=(e)=>{const button=e.currentTarget;return guard(async()=>{const action=desktop.visible?"hide":"show";await api("/api/local/laith-desktop",{body:{action}});desktop.visible=action==="show";button.textContent=desktop.visible?"Hide Laith":"Show Laith";},button);};
    $("#laithNew").onclick=()=>{location.hash='#laith';if(!chat)pages.laith('');};
    $("#laithSettings").onclick=chatSettings;
    $("#laithHistory").onclick=()=>{sessionsOpen=!sessionsOpen;if($(".laith-layout"))$(".laith-layout").classList.toggle('sessions-open',sessionsOpen);else location.hash='#laith';};
    if(tab==="personality")renderPersonalization();else if(tab==="memory")renderMemories();else if(tab==="abilities")renderAbilities();else if(tab==="checkins")renderCheckins();else renderConversation();
    applyCharacterLook();
    if(chat&&activeTurn())pollChat(chat.id,me);
    else if(tab==="checkins")setTimeout(()=>{if(!stale(me))pages.laith(arg);},15000);
  };

  function applyCharacterLook(){
    const image=library?.personalization?.presentation?.image||'/static/laith.png';
    $$('.laith-portrait,.laith-launcher img,#laithCharacter img,#laithLiveLook').forEach(el=>{if(el.getAttribute('src')!==image)el.src=image;});
    const badge=$('#laithLookLabel');if(badge)badge.textContent=(library.personalization.presentation.roleName||'Classic lion')+' · '+library.personalization.presentation.form;
  }
  let refreshingProfile=false;
  setInterval(async()=>{
    if(refreshingProfile||document.hidden||!location.hash.startsWith('#laith')||!library)return;
    refreshingProfile=true;
    try{library.personalization=await api('/api/laith/personalization');applyCharacterLook();}catch(e){}finally{refreshingProfile=false;}
  },5000);

  async function newChat(message="",button){await guard(async()=>{const c=await api("/api/laith/chats",{body:{}});drafts[c.id]=message;location.hash="#laith/chat/"+c.id;},button);}

  function visibleTurns(){
    const turns=chat?chat.turns:[];const retired=new Set(turns.filter(t=>!["prepared","superseded"].includes(t.status)).map(t=>t.retry_of));
    return turns.filter(t=>!["prepared","superseded","cancelled"].includes(t.status)&&!retired.has(t.id));
  }
  function transcriptHtml(){
    const turns=visibleTurns();
    return turns.length?turns.map(t=>`<div class="laith-message laith-user"><div>${esc(t.message)}</div></div><article class="laith-message laith-answer"><div class="laith-answer-name">Laith</div>${t.status==="done"?`<div class="md">${md(t.answer||"")}</div>${t.capability_files?`<div class="work-files">${t.capability_files.map(f=>{const url=esc(f.url),name=esc(f.name);return /\.(png|jpg|jpeg|webp)$/.test(f.name)?`<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="Generated result">${name}</a>`:/\.(mp4|webm)$/.test(f.name)?`<div><video controls src="${url}"></video><a href="${url}" download>${name}</a></div>`:f.name.endsWith('.wav')?`<div><audio controls src="${url}"></audio><a href="${url}" download>${name}</a></div>`:`<a class="btn sm" href="${url}" download>${I('download')}${name}</a>`;}).join('')}</div>`:''}`:t.status==="waiting"?`<div class="chat-notice"><b>Waiting for another app</b><p>This model needs you to paste an answer back. Laith can reply here using a connected chat model instead.</p><button class="sm primary" data-retry-turn="${esc(t.id)}">Reply here instead</button></div>`:["queued","running"].includes(t.status)?'<div class="typing" aria-label="Laith is thinking"><i></i><i></i><i></i></div>':`<div class="chat-notice"><p>${esc(t.error||"This reply did not finish. Your message is saved.")}</p><button class="sm" data-retry-turn="${esc(t.id)}">Try again</button></div>`}<div class="laith-message-actions">${t.status==="done"?`<button class="ghost sm" data-copy-turn="${esc(t.id)}">${I("copy")} Copy</button><button class="ghost sm" data-goal-turn="${esc(t.id)}">Make a goal</button>`:""}${t.capability_job?`<a href="#work/job/${esc(t.task_id)}" class="small">Files and progress</a>`:""}${t.task_id?`<a href="#history/${esc(t.task_id)}" class="small muted">View activity</a>`:""}</div></article>`).join(""):`<div class="laith-chat-empty">${portrait()}<h2>What's on your mind?</h2><p>A question, a rough idea, something to write.<br>I'm here to help.</p><div class="laith-starters">${[["Polish some writing","Help me improve this writing: "],["Think it through","Help me think through an idea: "],["Make a plan","Help me make a practical plan for " ]].map(([label,draft])=>`<button class="pill" data-laith-draft="${esc(draft)}">${label}</button>`).join("")}</div></div>`;
  }
  function wireTranscript(){
    $$("[data-retry-turn]").forEach(b=>b.onclick=()=>retryDirect(b.dataset.retryTurn,b));
    $$("[data-goal-turn]").forEach(b=>b.onclick=()=>goalModal(b.dataset.goalTurn));
    $$("[data-copy-turn]").forEach(b=>b.onclick=()=>copyText(chat.turns.find(t=>t.id===b.dataset.copyTurn).answer,b));
    $$("[data-laith-draft]").forEach(b=>b.onclick=()=>{const input=$("#laithMessage");input.value=b.dataset.laithDraft;input.dispatchEvent(new Event('input'));input.focus();});
  }
  function refreshBusy(){
    const busy=activeTurn();const button=$("#laithSend");if(button)button.disabled=!!busy||sending;
    const status=$("#laithStatus");if(status){status.textContent=busy?(busy.status==="waiting"?"Waiting for an app handoff":Date.now()/1000-busy.created>90?"Taking longer than usual · your request is saved":"Laith is thinking…"):"";}
    const character=$("#laithCharacter");if(character){const settings=library.personalization?.settings||{};const last=visibleTurns().slice(-1)[0];const state=busy?'working':settings.quietMode||settings.privateMode?'resting':last&&['failed','interrupted'].includes(last.status)?'attention':'idle';character.dataset.state=state;character.classList.toggle('motion-off',settings.quietMode||settings.privateMode||settings.moodExpressiveness==='off');$("span",character).textContent=state==='working'?'Working · focused':state==='resting'?'Resting · quiet':state==='attention'?'Needs attention':'Here to help';}
  }
  function renderConversation(){
    const chats=library.chats.map(c=>`<a class="laith-chat-link ${chat&&chat.id===c.id?"selected":""}" href="#laith/chat/${esc(c.id)}">${I("chat")}<span>${esc(c.title)}</span></a>`).join("");
    $("#laithContent").innerHTML=`<div class="laith-layout ${sessionsOpen?"sessions-open":""}"><aside class="laith-sessions"><div class="row"><b class="grow">Your chats</b><button class="ghost sm" id="closeLaithSessions" aria-label="Close chat history">${I("close")}</button></div>${chats||'<p class="small muted">Your conversations will live here.</p>'}${library.archived_chats.length?`<details class="more"><summary>Archived</summary>${library.archived_chats.map(c=>`<div class="list-row"><span class="small grow">${esc(c.title)}</span><button class="sm" data-restore-chat="${esc(c.id)}">Restore</button></div>`).join("")}</details>`:""}</aside><section class="laith-main"><div class="laith-chat"><div id="laithCharacter" class="laith-character" role="status"><img src="${esc(library.personalization?.presentation?.image||'/static/laith.png')}" alt="Laith"><span>Here to help</span><a href="#laith/personality">Personalize</a></div><div class="laith-transcript" id="laithTranscript" aria-live="polite" aria-label="Conversation with Laith">${transcriptHtml()}</div><form id="laithForm" class="laith-composer"><div class="laith-input-box"><label class="sr-only" for="laithMessage">Message Laith</label><textarea id="laithMessage" placeholder="Message Laith…" rows="1" maxlength="12000"></textarea><div id="laithAttachments" class="attach-chips"></div><div class="row"><label class="btn ghost icon" title="Attach a file" aria-label="Attach a file">${I("plus")}<input id="laithFile" type="file" hidden></label><button type="button" class="ghost icon" id="laithPaste" title="Add copied text" aria-label="Use copied text">${I("copy")}</button><span class="tiny muted grow">${chat?"Your conversation is saved":"Your plans. Your tools. One conversation."}</span><button class="primary send" id="laithSend" aria-label="Send message">${I("send")}</button></div></div><div class="composer-choices"><button type="button" class="pill" id="laithWho" aria-label="Choose AI model"></button><button type="button" class="pill" id="laithSpend" aria-label="Choose plans or API spending"></button><button type="button" class="pill" id="laithEffort" aria-label="Choose thinking effort"></button></div><div id="laithWillUse" class="willuse" aria-live="polite"></div><div id="laithStatus" class="tiny muted" role="status"></div></form></div></section></div>`;
    $("#closeLaithSessions").onclick=()=>{sessionsOpen=false;$(".laith-layout").classList.remove("sessions-open");};
    $$("[data-restore-chat]").forEach(b=>b.onclick=()=>guard(async()=>{await api(`/api/laith/chats/${encodeURIComponent(b.dataset.restoreChat)}/archive`,{body:{archived:false}});pages.laith(routeArg);},b));
    const input=$("#laithMessage"),draftKey=chat?chat.id:"new";input.value=drafts[draftKey]||"";
    const grow=()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,180)+'px';};grow();
    input.oninput=()=>{drafts[draftKey]=input.value;grow();queueChatPreview();};
    input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!touch){e.preventDefault();sendMessage($("#laithSend"));}};
    $("#laithForm").onsubmit=e=>{e.preventDefault();sendMessage($("#laithSend"));};
    $("#laithPaste").onclick=()=>guard(async()=>{const text=await navigator.clipboard.readText();if(text.length>10000)throw new Error('Copy a shorter excerpt (up to 10,000 characters).');input.value+=(input.value?'\n\n':'')+'Copied text (reference material):\n'+text;input.dispatchEvent(new Event('input'));input.focus();},$("#laithPaste"));
    const renderAttachments=()=>{$("#laithAttachments").innerHTML=(attachments[draftKey]||[]).map((f,i)=>`<span>${esc(f.name)}<button type="button" class="ghost sm" data-laith-remove="${i}" aria-label="Remove file">×</button></span>`).join('');$$('[data-laith-remove]').forEach(b=>b.onclick=()=>{attachments[draftKey].splice(Number(b.dataset.laithRemove),1);renderAttachments();queueChatPreview();});};
    renderAttachments();$("#laithFile").onchange=e=>guard(async()=>{const file=e.target.files[0];if(!file)return;if((attachments[draftKey]||[]).length>=4)throw Error('Choose up to four files.');const uploaded=await window.uploadCapabilityInput(file);(attachments[draftKey]||=([])).push(uploaded);renderAttachments();queueChatPreview();},$("#laithSend"));
    wireTranscript();refreshBusy();updateChatChoices();queueChatPreview();
    $('#laithWho').onclick=()=>chooseChatOption('model');$('#laithSpend').onclick=()=>chooseChatOption('billing');$('#laithEffort').onclick=()=>chooseChatOption('effort');
  }
  function updateChatChoices(){
    const selected=S.models.find(m=>m.key===options.model);
    if($('#laithWho'))$('#laithWho').innerHTML=I('spark')+esc(selected?.label||'Auto');
    if($('#laithSpend'))$('#laithSpend').innerHTML=I('wallet')+esc(SPEND[options.billing][0]);
    if($('#laithEffort'))$('#laithEffort').innerHTML=I('bolt')+'Effort: '+esc(effortChoices.find(([key])=>key===options.effort)?.[1]||'Auto');
  }
  function chooseChatOption(key){
    const choices=key==='model'?[['','Auto · choose for this task'],...S.models.filter(m=>m.available&&!m.features.includes('image_gen')&&!m.features.includes('handoff')).map(m=>[m.key,m.label])]:key==='effort'?effortChoices:Object.entries(SPEND).map(([id,[label,help]])=>[id,label,help]);
    const m=modal({title:key==='model'?'Who should answer?':key==='effort'?'Thinking effort':'What may this spend?',body:`<div class="choice">${choices.map(([value,label,help])=>`<label><input type="radio" name="laithComposerChoice" value="${esc(value)}" ${options[key]===value?'checked':''}><b>${esc(label)}</b>${help?`<span>${esc(help)}</span>`:''}</label>`).join('')}</div>${key==='effort'?'<p class="tiny muted">Auto matches the task. Higher effort can take longer; available levels depend on the model.</p>':''}`});
    $$('input[name="laithComposerChoice"]',m.el).forEach(input=>input.onchange=()=>{options[key]=input.value;updateChatChoices();queueChatPreview();m.close();});
  }
  function queueChatPreview(){
    const epoch=++previewEpoch,input=$('#laithMessage'),el=$('#laithWillUse');if(!input||!el)return;
    const message=input.value.trim();if(message.length<4||activeTurn()){el.textContent='';return;}
    el.textContent='Choosing the best fit…';previewChat(epoch,rid,message,chat?.id||null);
  }
  const previewChat=debounce(async(epoch,me,message,key)=>{
    try{
      const data=await api('/api/laith/preview',{body:{message,chat_id:key,model:options.model,effort:options.effort,ability:options.ability,use_memory:options.memory,use_knowledge:options.knowledge,tools:options.tools?'auto':'none',billing:options.billing,max_cost_usd:options.budget,asset_ids:(attachments[key||'new']||[]).map(f=>f.id)}});
      if(stale(me)||epoch!==previewEpoch||!$('#laithWillUse'))return;
      if(data.setup_required){$('#laithWillUse').textContent='Setup required for this task · open Audio & files to connect a service.';return;}
      if(data.capability){const c=data.capability;$('#laithWillUse').innerHTML=`Will use <b>${esc(c.label)}</b> · ${esc(c.provider)} on ${esc(c.node)} · review destination and cost before running`;return;}
      const p=data.plan,k=costKind(p),funding=k==='plan'?'your plan, no API credit':k==='free'?'free':'about '+money(p.effective_cost_usd);
      $('#laithWillUse').innerHTML=`Will use <b>${esc(p.model_label)}</b> · ${funding}${p.effort?' · '+esc(p.effort)+' effort':''}${p.tools.length?' · with '+p.tools.map(appName).map(esc).join(', '):''}${p.effective_cost_usd>options.budget?' · exceeds the current allowance':''}`;
    }catch(e){if(!stale(me)&&epoch===previewEpoch&&$('#laithWillUse'))$('#laithWillUse').textContent=e.message;}
  },650);
  async function pollChat(key,me){
    setTimeout(async()=>{
      if(stale(me)||!chat||chat.id!==key)return;
      try{
        const current=await api('/api/laith/chats/'+key);if(stale(me)||!chat||chat.id!==key)return;
        const before=JSON.stringify(chat.turns);chat=current;
        if(before!==JSON.stringify(chat.turns)){
          const transcript=$("#laithTranscript");if(!transcript)return;
          const atEnd=transcript.scrollHeight-transcript.scrollTop-transcript.clientHeight<60,scroll=transcript.scrollTop;
          transcript.innerHTML=transcriptHtml();wireTranscript();transcript.scrollTop=atEnd?transcript.scrollHeight:scroll;
        }
        refreshBusy();if(activeTurn())pollChat(key,me);
      }catch(e){if(!stale(me)){$("#laithStatus").textContent='Connection interrupted. Checking again…';pollChat(key,me);}}
    },1800);
  }
  function chatSettings(){
    const tabLinks=`<div class="row"><a class="btn sm" href="#laith/personality">Personality & appearance</a><a class="btn sm" href="#laith/memory">Memory</a><a class="btn sm" href="#laith/abilities">Abilities</a><a class="btn sm" href="#laith/checkins">Check-ins</a></div>`;
    const m=modal({title:'Chat settings',body:`<div class="stack">${field('Reply with',`<select id="laithModel">${models()}</select>`)}${field('Writing style',`<select id="laithAbility">${library.abilities.map(a=>`<option value="${esc(a.id)}" ${a.id===options.ability?'selected':''}>${esc(a.name)}</option>`).join('')}</select>`)}${[["memory","Saved memory"],["knowledge","Project knowledge"],["tools","Connected tools"]].map(([key,label])=>`<label class="switch"><input id="laithOpt${key}" type="checkbox" ${options[key]?'checked':''}>${label}</label>`).join('')}<details class="more"><summary>Spending</summary>${field('Use',`<select id="laithBilling"><option value="plans_only">Plans and free only</option><option value="plans_first" ${options.billing==='plans_first'?'selected':''}>Plans first; allow API use</option><option value="any" ${options.billing==='any'?'selected':''}>Allow API use</option></select>`)}${field('Estimate allowance ($)',`<input id="laithBudget" type="number" min="0" max="1000" step="0.01" value="${options.budget}">`)}<p class="tiny muted">An estimate allowance gates each request; it is not a guaranteed billing cap.</p></details>${tabLinks}${chat&&!activeTurn()?'<button class="ghost sm" id="archiveLaith">Archive this chat</button>':''}<a class="small" href="#work${chat?"/chat/"+esc(chat.id):""}">Audio, files and live voice</a><a class="small" href="#studio">Your goals and approvals</a></div>`,foot:'<button data-close>Done</button>'});
    $("#laithModel",m.el).onchange=e=>{options.model=e.target.value;updateChatChoices();queueChatPreview();};$("#laithAbility",m.el).onchange=e=>{options.ability=e.target.value;queueChatPreview();};
    for(const key of ['memory','knowledge','tools'])$("#laithOpt"+key,m.el).onchange=e=>{options[key]=e.target.checked;queueChatPreview();};
    $("#laithBilling",m.el).onchange=e=>{options.billing=e.target.value;updateChatChoices();queueChatPreview();};$("#laithBudget",m.el).onchange=e=>{options.budget=Number(e.target.value);queueChatPreview();};
    $$("a",m.el).forEach(a=>a.addEventListener('click',m.close));
    if($("#archiveLaith",m.el))$("#archiveLaith",m.el).onclick=()=>guard(async()=>{await api(`/api/laith/chats/${chat.id}/archive`,{body:{archived:true}});m.close();location.hash='#laith';},$("#archiveLaith",m.el));
  }
  async function dispatchTurn(key,turn){
    const send=async()=>{
      try{await api(`/api/laith/chats/${key}/send`,{body:{turn_id:turn.id,confirm:true}});}
      catch(e){const saved=await api('/api/laith/chats/'+key);if(!saved.turns.some(t=>t.id===turn.id&&['queued','running','waiting','done','failed'].includes(t.status)))throw e;}
      drafts[key]='';drafts.new='';await pages.laith('chat/'+key);if(location.hash!=='#laith/chat/'+key)location.hash='#laith/chat/'+key;
    };
    if(turn.plan.needs_confirmation||turn.tools_enabled||turn.plan.effective_cost_usd>0){
      const m=modal({title:'Send this request?',body:`<p><b>${esc(turn.plan.model_label)}</b> · Estimated ${money(turn.plan.effective_cost_usd)}</p><p class="small">${turn.memory_count} saved memories · ${turn.sources.length} knowledge excerpts · ${turn.tools_enabled?'Connected tools enabled':'Connected tools off'}</p><p class="small muted">${esc((turn.plan.warnings||[]).join(' '))}</p>`,foot:'<button data-close>Cancel</button><button class="primary" id="confirmLaithSend">Send</button>'});
      $("#confirmLaithSend",m.el).onclick=()=>guard(async()=>{await send();m.close();},$("#confirmLaithSend",m.el));
    }else await send();
  }
  async function sendMessage(button){
    if(sending||activeTurn())return;sending=true;refreshBusy();
    try{
      const message=$("#laithMessage").value.trim();if(!message)return;
      const key=chat?chat.id:(await api('/api/laith/chats',{body:{}})).id;drafts[key]=message;
      const files=attachments[chat?chat.id:'new']||[];
      const intent=await api('/api/capabilities/intent',{body:{prompt:message,asset_ids:files.map(f=>f.id)}});
      if(intent.use_capability){
        const job=await api('/api/capabilities/preview',{body:{prompt:message,task:intent.task,asset_ids:files.map(f=>f.id),chat_id:key}});
        window.reviewCapability(job,()=>{drafts[key]='';drafts.new='';attachments[key]=[];attachments.new=[];});return;
      }
      const turn=await api(`/api/laith/chats/${key}/prepare`,{body:{message,model:options.model,ability:options.ability,use_memory:options.memory,use_knowledge:options.knowledge,effort:options.effort,tools:options.tools?'auto':'none',billing:options.billing,max_cost_usd:options.budget}});
      await dispatchTurn(key,turn);
    }catch(e){toast(e.message,true);}finally{sending=false;refreshBusy();}
  }
  async function retryDirect(turnId,button){
    await guard(async()=>{const key=chat.id;const turn=await api(`/api/laith/chats/${key}/retry`,{body:{turn_id:turnId}});drafts[key]=turn.message;await dispatchTurn(key,turn);},button);
  }

  function goalModal(turn){
    const m=modal({title:"Turn this conversation into a goal",body:field("Goal title",'<input id="laithGoalTitle" maxlength="160" placeholder="What should the team deliver?">')+'<p class="small muted">Saves a planned manager-led crew with this exchange as context. Open the goal and choose Start team when you are ready.</p>',foot:'<button data-close>Cancel</button><button id="laithSaveGoal" class="primary">Save planned goal</button>'});
    $("#laithSaveGoal",m.el).onclick=(e)=>guard(async()=>{await api(`/api/laith/chats/${chat.id}/goal`,{body:{turn_id:turn,title:$("#laithGoalTitle",m.el).value}});m.close();location.hash="#studio";},e.currentTarget);
  }

  function renderPersonalization(){
    const p=library.personalization;
    if(!p){$('#laithContent').textContent='Update the Tazrivo hub to enable personalization.';return;}
    const choices=(values,current)=>values.map(v=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(v)}</option>`).join('');
    const toggles=[['learnPreferences','Learn preferences from messages I send','Opt in to a small style-request detector. It stores preference values and turn IDs, not message text.'],['reflectPersonality','Apply saved personality preferences','Use saved tone and reply length. Turning this off keeps your preferences.'],['professionMirroring','Apply my active profession','Affects communication style only; never grants tools or credentials.'],['askBeforeAppearanceChanges','Ask before appearance changes','Shows a preview for approval before saving a new appearance.'],['quietMode','Quiet mode','Stops decorative animation and roaming.'],['privateMode','Private personalization mode','Pauses learning and adaptive presentation. Chats and task outputs are still saved by Tazrivo.']];
    const selects=[['personalizationDepth','Learning depth',['minimal','balanced','deep']],['appearanceAdaptation','Appearance adaptation',['off','suggest','approved']],['moodExpressiveness','Mood expressiveness',['off','low','medium']],['roamingActivity','Desktop movement',['off','low','balanced']]];
    $('#laithContent').innerHTML=`<div class="laith-personalization stack"><div class="card"><h2>Your Laith</h2><p class="muted">One shared profile for this Tazrivo hub. Mac and Windows use the desktop companion; Raspberry Pi uses this browser interface. Mobile apps are on hold.</p><div class="laith-live-look"><img id="laithLiveLook" src="${esc(p.presentation.image)}" alt="Laith, Atlas lion"><div><h3 id="laithLookLabel">${esc(p.presentation.roleName||'Classic lion')} · ${esc(p.presentation.form)}</h3><p>Profession style: <b>${p.settings.professionMirroring?'On':'Off'}</b><br>Personality style: <b>${p.settings.reflectPersonality?'On':'Off'}</b><br>Learning: <b>${p.settings.learnPreferences?'On':'Off'}</b></p></div></div><p class="small muted">${esc(p.assets.fallback)}</p></div>
      <div class="card stack"><h2>Choose a look & profession</h2><p class="small muted">Preview the outfit, then apply it. A manual outfit stays visible even when automatic profession adaptation is off. Each look has a distinct standing form and clothing.</p><div class="laith-role-gallery">${p.professions.map(r=>`<button class="laith-role-card ${p.presentation.activeRole===r.id?'selected':''}" data-role-look="${r.id}" aria-label="Preview ${esc(r.name)} look"><img src="${esc(r.image)}" alt="Laith as ${esc(r.name)}" loading="lazy"><b>${esc(r.name)}</b></button>`).join('')}</div></div>
      <div class="card stack"><h2>Choose how Laith talks</h2><p class="small muted">Applying a style turns on personality reflection. It does not enable learning or grant tools.</p><div class="laith-style-presets">${[['Neutral','neutral','balanced'],['Calm & clear','calm','balanced'],['Warm & supportive','warm','balanced'],['Direct & concise','direct','concise'],['Playful','playful','balanced'],['Detailed guide','warm','detailed']].map(([label,tone,length])=>`<button data-style-tone="${tone}" data-style-length="${length}">${label}</button>`).join('')}</div></div>
      <form id="laithProfileForm" class="card stack"><h2>Personalization & privacy</h2>${toggles.map(([key,label,help])=>`<label class="switch"><input type="checkbox" data-personal-setting="${key}" ${p.settings[key]?'checked':''}><span>${label}<small class="muted" style="display:block">${help}</small></span></label>`).join('')}
      <div class="grid">${selects.map(([key,label,values])=>field(label,`<select data-personal-setting="${key}">${choices(values,p.settings[key])}</select>`)).join('')}</div><p class="small muted">Minimal, balanced and deep learning require 4, 3 and 2 separate matching sent messages. Appearance “suggest” requires explicit review; “approved” reuses a previously approved selection. Neither enables unapproved inferred changes. Desktop movement gently slides the existing artwork; walking animation is not available.</p>
      <h3>Professions</h3><div class="grid">${p.professions.map(r=>`<label><input type="checkbox" data-personal-role="${r.id}" ${p.roles.includes(r.id)?'checked':''}> ${esc(r.name)}</label>`).join('')}</div>${field('Active profession',`<select id="laithActiveRole"><option value="">None</option>${p.professions.map(r=>`<option value="${r.id}" ${p.activeRole===r.id?'selected':''}>${esc(r.name)}</option>`).join('')}</select>`)}<p class="small muted">Select the active profession above as one of your professions. Gender presentation is configured independently.</p><button class="primary" type="submit">Save settings</button></form>
      <form id="laithAppearanceForm" class="card stack"><h2>Appearance</h2><p class="small muted">The selected outfit is shown in chat and on your desktop. Choose a profession above for a matching outfit. Unsupported accessory combinations are marked below.</p><div class="grid">${field('Presentation',`<select id="laithPresentation">${choices([...new Set([...p.assets.presentations,p.appearance.presentation])],p.appearance.presentation)}</select>`)}${field('Outfit',`<select id="laithOutfit">${choices(['default','profession'],p.appearance.outfit)}</select>`)}${field('Accessory',`<select id="laithAccessory">${choices(['none','glasses','headphones','book'],p.appearance.accessory)}</select>`)}</div><p class="small">${p.presentation.missingAssets.length?'Unavailable requested details: '+esc(p.presentation.missingAssets.join(', ')):'Selected outfit is available and displayed.'}</p><button type="submit">${p.settings.askBeforeAppearanceChanges?'Preview appearance change':'Save appearance'}</button></form>
      <div class="card stack"><h2>Preferences you control</h2><p class="small muted">Explicit choices override inference. Delete removes the preference and its evidence, and stops relearning that trait until reset. Lock records your choice as authoritative.</p>${Object.values(p.preferences).map(v=>`<div class="list-row"><div class="grow"><b>${esc(v.key)}: ${esc(v.value)}</b><p class="small muted">${esc(v.source)} · ${Math.round(v.confidence*100)}% confidence · ${v.evidenceRefs.length} evidence references · ${v.locked?'Locked':'Unlocked'}<br>Updated ${esc(new Date(v.updated*1000).toLocaleString())}</p></div><button data-edit-preference="${v.key}">Edit / lock</button><button data-delete-preference="${v.key}">Delete</button></div>`).join('')||'<p>No saved personality preferences.</p>'}<form id="laithPreferenceForm" class="row">${field('Trait','<select id="laithTrait">'+choices(Object.keys(p.traitChoices),'responseLength')+'</select>')}${field('Value','<select id="laithTraitValue"></select>')}<label><input type="checkbox" id="laithTraitLock"> Lock</label><button type="submit">Save choice</button></form></div>
      <div class="card stack"><h2>Adaptation suggestions</h2><p class="small muted">Style suggestions need your approval. Confidence measures matching signals, not psychological certainty. Character moods never diagnose your mood.</p>${p.suggestions.map(s=>`<div class="list-row"><div class="grow"><b>${s.kind==='appearance'?'Appearance preview':esc(s.key)+': '+esc(s.value)}</b>${s.preview?`<img class="laith-outfit-preview" src="${esc(s.preview.image)}" alt="Proposed Laith outfit">`:''}<p class="small">${s.kind==='appearance'?esc(Object.entries(s.appearance).map(([k,v])=>k+': '+v).join(' · '))+' · Review the look shown here':Math.round(s.confidence*100)+'% confidence · '+s.evidenceRefs.length+' distinct message references'}</p></div><button class="primary" data-suggestion="${s.id}" data-approve="true">Approve</button><button data-suggestion="${s.id}" data-approve="false">Reject</button></div>`).join('')||'<p>No pending suggestions.</p>'}</div>
      <div class="card"><h2>Reset personalization</h2><p class="small muted">Deletes personality preferences, evidence, suggestions, professions and appearance choices, and restores consent-off defaults. Saved chat history and the separate memory shelf remain.</p><button id="laithResetProfile">Reset personalization…</button></div></div>`;
    const mutate=async(path,body)=>{await api('/api/laith/personalization'+path,{body:{revision:p.revision,...body}});await pages.laith('personality');};
    $$('[data-role-look]').forEach(b=>b.onclick=()=>{
      const role=p.professions.find(r=>r.id===b.dataset.roleLook);
      const m=modal({title:'Use '+role.name+' look?',body:`<div class="laith-role-confirm"><img src="${esc(role.image)}" alt="Laith ${esc(role.name)} outfit"><p>${esc(role.style)}</p><label><input id="laithUseRoleStyle" type="checkbox" checked> Use this profession's communication style</label><p class="small muted">Keeps learning and tool permissions unchanged.</p></div>`,foot:'<button data-close>Cancel</button><button class="primary" id="applyLaithRole">Use this look</button>'});
      $('#applyLaithRole',m.el).onclick=e=>guard(async()=>{await mutate('/role',{role:role.id,confirm:true,useStyle:$('#laithUseRoleStyle',m.el).checked});m.close();},e.currentTarget);
    });
    $$('[data-style-tone]').forEach(b=>b.onclick=()=>guard(()=>mutate('/style',{tone:b.dataset.styleTone,responseLength:b.dataset.styleLength}),b));
    $('#laithProfileForm').onsubmit=e=>{e.preventDefault();guard(async()=>{const settings={};$$('[data-personal-setting]').forEach(el=>settings[el.dataset.personalSetting]=el.type==='checkbox'?el.checked:el.value);await mutate('',{settings,roles:$$('[data-personal-role]:checked').map(el=>el.dataset.personalRole),activeRole:$('#laithActiveRole').value});},$('button[type=submit]',e.currentTarget));};
    $('#laithAppearanceForm').onsubmit=e=>{e.preventDefault();guard(()=>mutate('/appearance',{appearance:{presentation:$('#laithPresentation').value,outfit:$('#laithOutfit').value,accessory:$('#laithAccessory').value}}),$('button[type=submit]',e.currentTarget));};
    const traitValues=()=>{$('#laithTraitValue').innerHTML=choices(p.traitChoices[$('#laithTrait').value],null);};traitValues();$('#laithTrait').onchange=traitValues;
    $('#laithPreferenceForm').onsubmit=e=>{e.preventDefault();guard(()=>mutate('/preferences',{key:$('#laithTrait').value,value:$('#laithTraitValue').value,locked:$('#laithTraitLock').checked}),$('button',e.currentTarget));};
    $$('[data-edit-preference]').forEach(b=>b.onclick=()=>{const v=p.preferences[b.dataset.editPreference];$('#laithTrait').value=v.key;traitValues();$('#laithTraitValue').value=v.value;$('#laithTraitLock').checked=v.locked;$('#laithTraitValue').focus();});
    $$('[data-delete-preference]').forEach(b=>b.onclick=()=>guard(()=>mutate('/preferences/'+encodeURIComponent(b.dataset.deletePreference)+'/delete',{}),b));
    $$('[data-suggestion]').forEach(b=>b.onclick=()=>guard(()=>mutate('/suggestions/'+encodeURIComponent(b.dataset.suggestion)+'/decision',{approve:b.dataset.approve==='true'}),b));
    $('#laithResetProfile').onclick=()=>{const m=modal({title:'Reset Laith personalization?',body:'<p>This deletes personalization and restores learning-off defaults. It does not delete chats or your separate memory shelf.</p>',foot:'<button data-close>Cancel</button><button class="primary" id="confirmLaithReset">Reset personalization</button>'});$('#confirmLaithReset',m.el).onclick=e=>guard(async()=>{await mutate('/reset',{});m.close();},e.currentTarget);};
  }

  function renderMemories(){
    $("#laithContent").innerHTML=`<div class="card"><div class="row"><div class="grow"><h2>A memory shelf you control</h2><p class="small muted">Only notes you explicitly save are added here. Active notes are included when Saved memory is checked. Archiving keeps a note out of future requests; previous requests retain their original context.</p></div><button id="laithRemember" class="primary sm">Save a memory</button></div>${library.memories.length?library.memories.map((m)=>`<div class="list-row"><p class="grow">${esc(m.content)}</p><span class="badge">${m.active?"Active":"Archived"}</span><button class="sm" data-memory="${esc(m.id)}" data-active="${m.active?"false":"true"}">${m.active?"Archive":"Restore"}</button></div>`).join(""):'<div class="empty">Save preferences, useful project context or how you like to work.</div>'}</div>`;
    $("#laithRemember").onclick=()=>{const m=modal({title:"What should Laith remember?",body:field("Your note",'<textarea id="laithMemoryText" rows="4" maxlength="2000" placeholder="For example: I prefer concise answers in French."></textarea>')+'<p class="tiny muted">Keep passwords and tokens in the secure credential forms.</p>',foot:'<button data-close>Cancel</button><button id="saveLaithMemory" class="primary">Save memory</button>'});$("#saveLaithMemory",m.el).onclick=(e)=>guard(async()=>{await api('/api/laith/memories',{body:{content:$("#laithMemoryText",m.el).value}});m.close();pages.laith("memory");},e.currentTarget);};
    $$("[data-memory]").forEach((b)=>b.onclick=(e)=>guard(async()=>{await api(`/api/laith/memories/${b.dataset.memory}/state`,{body:{active:b.dataset.active==="true"}});pages.laith("memory");},e.currentTarget));
  }

  function renderAbilities(){
    $("#laithContent").innerHTML=`<div class="row"><p class="muted grow">Reusable instructions for how Laith helps. Abilities use the model and tool choices you make for each message.</p><button id="newLaithAbility" class="sm primary">New ability</button></div><div class="grid">${library.abilities.map((a)=>`<article class="card"><h3>${esc(a.name)}</h3><p class="small muted">${esc(a.instructions)}</p><button class="sm" data-ability="${esc(a.id)}">Edit ability</button></article>`).join("")}</div>`;
    const edit=(a={})=>{const m=modal({title:a.id?"Edit ability":"New ability",body:`<div class="form">${field("Name",`<input id="laithAbilityName" maxlength="80" value="${esc(a.name||"")}">`)}${field("Instructions",`<textarea id="laithAbilityInstructions" rows="6" maxlength="6000">${esc(a.instructions||"")}</textarea>`)}</div>`,foot:'<button data-close>Cancel</button><button id="saveLaithAbility" class="primary">Save ability</button>'});$("#saveLaithAbility",m.el).onclick=(e)=>guard(async()=>{await api('/api/laith/abilities',{body:{...(a.id?{id:a.id}:{}),name:$("#laithAbilityName",m.el).value,instructions:$("#laithAbilityInstructions",m.el).value}});m.close();pages.laith("abilities");},e.currentTarget);};
    $("#newLaithAbility").onclick=()=>edit();$$("[data-ability]").forEach((b)=>b.onclick=()=>edit(library.abilities.find((a)=>a.id===b.dataset.ability)));
  }

  function renderCheckins(){
    const p=library.pulse;
    $("#laithContent").innerHTML=`<div class="card"><div class="row"><div class="grow"><h2>A gentle nudge</h2><p class="small muted">Persistent in-app check-ins appear here when Laith is open. They do not send messages, call a model or start work automatically.</p></div><button class="sm primary" id="newLaithCheckin">New check-in</button></div>${p.reminders.map((r)=>`<div class="list-row"><div class="grow"><b>${esc(r.title)}</b><p class="small muted">${new Date(r.due*1000).toLocaleString()}${r.interval_hours?` · repeats every ${r.interval_hours} hours`:""}</p></div><span class="badge ${r.due<=p.now?"warn":""}">${r.due<=p.now?"Due":"Upcoming"}</span>${r.due<=p.now?`<button class="sm primary" data-ack="${esc(r.id)}">${r.interval_hours?"Done · next check-in":"Done"}</button>`:""}<button class="sm ghost" data-stop-checkin="${esc(r.id)}">Dismiss</button></div>`).join("")||'<div class="empty">Make room to come back to something that matters.</div>'}</div>`;
    $("#newLaithCheckin").onclick=()=>{const when=new Date(Date.now()+3600000);const local=new Date(when.getTime()-when.getTimezoneOffset()*60000).toISOString().slice(0,16);const m=modal({title:"New in-app check-in",body:`<div class="form">${field("What would you like to revisit?",'<input id="laithReminderTitle" maxlength="180">')}${field("When",`<input id="laithReminderDue" type="datetime-local" value="${local}">`)}${field("Repeat every (hours; 0 = once)",'<input id="laithReminderRepeat" type="number" min="0" max="8760" value="0">')}</div>`,foot:'<button data-close>Cancel</button><button id="saveLaithReminder" class="primary">Save check-in</button>'});$("#saveLaithReminder",m.el).onclick=(e)=>guard(async()=>{await api('/api/laith/reminders',{body:{title:$("#laithReminderTitle",m.el).value,due:new Date($("#laithReminderDue",m.el).value).getTime()/1000,interval_hours:Number($("#laithReminderRepeat",m.el).value)}});m.close();pages.laith("checkins");},e.currentTarget);};
    const ack=(id,stop,button)=>guard(async()=>{await api(`/api/laith/reminders/${id}/acknowledge`,{body:{stop}});pages.laith("checkins");},button);
    $$("[data-ack]").forEach((b)=>b.onclick=()=>ack(b.dataset.ack,false,b));$$("[data-stop-checkin]").forEach((b)=>b.onclick=()=>ack(b.dataset.stopCheckin,true,b));
  }
};
