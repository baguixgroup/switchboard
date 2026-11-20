/* Laith is part of Switchboard: shared providers, authenticated storage and Studio goals. */
window.installLaith = function(pages) {
  let library, chat, routeArg="", desktop={available:false};
  const options={model:"",ability:"conversation",memory:true,knowledge:false,tools:false,billing:"plans_only",budget:0};
  const drafts={};
  const portrait=`<img src="/static/laith.png" alt="Laith, a friendly Atlas lion" class="laith-portrait">`;
  const field=(name,html)=>`<label class="field">${name}${html}</label>`;
  const models=()=>`<option value="">Automatic · follow spending rules</option>${S.models.filter((m)=>!m.features.includes("image_gen")).map((m)=>`<option value="${esc(m.key)}" ${options.model===m.key?"selected":""}>${esc(m.label)}${m.available?"":" · not connected"}</option>`).join("")}`;
  const activeTurn=()=>chat&&chat.turns.find((t)=>["queued","running","waiting"].includes(t.status));
  const pulseLinks=(items,empty)=>items.length?items.map((g)=>`<a class="laith-pulse-item" href="#studio/run/${esc(g.run_id)}"><span>${esc(g.title)}</span>${I("arrow")}</a>`).join(""):`<p class="small muted">${empty}</p>`;
  const tabs=(tab)=>`<div class="tabs">${[["","Conversations"],["memory","Memory"],["abilities","Abilities"],["checkins","Check-ins"]].map(([key,label])=>`<a class="btn sm ${tab===key?"primary":"ghost"}" href="#laith${key?"/"+key:""}">${label}</a>`).join("")}</div>`;

  pages.laith=async function(arg="") {
    const me=rid;routeArg=arg;
    [library,desktop]=await Promise.all([api("/api/laith"),api("/api/local/laith-desktop").catch(()=>({available:false}))]);if(stale(me))return;
    chat=null;
    if(arg.startsWith("chat/")){chat=await api("/api/laith/chats/"+encodeURIComponent(arg.slice(5)));if(stale(me))return;}
    const tab=["memory","abilities","checkins"].includes(arg)?arg:"";
    view.innerHTML=head("Laith","Your friendly Atlas lion. A little warmth, a clearer goal, and help getting there.",`<div class="row"><button id="laithDesktop" ${desktop.available?"":"disabled"} title="${desktop.available?"Your independent desktop companion":"Available in the installed Mac and Windows app"}">${desktop.visible?"Hide Laith":"Show Laith"}</button><button class="primary" id="laithNew">New conversation</button></div>`)+tabs(tab)+`<div id="laithContent"></div>`;
    $("#laithDesktop").onclick=(e)=>{const button=e.currentTarget;return guard(async()=>{const action=desktop.visible?"hide":"show";await api("/api/local/laith-desktop",{body:{action}});desktop.visible=action==="show";button.textContent=desktop.visible?"Hide Laith":"Show Laith";},button);};
    $("#laithNew").onclick=(e)=>newChat("",e.currentTarget);
    if(tab==="memory")renderMemories();else if(tab==="abilities")renderAbilities();else if(tab==="checkins")renderCheckins();else renderConversation();
    if(chat&&activeTurn())setTimeout(()=>{if(!stale(me))pages.laith(arg);},2000);
    else if(tab==="checkins")setTimeout(()=>{if(!stale(me))pages.laith(arg);},15000);
  };

  async function newChat(message="",button){await guard(async()=>{const c=await api("/api/laith/chats",{body:{}});drafts[c.id]=message;location.hash="#laith/chat/"+c.id;},button);}

  function pulsePanel(){
    const p=library.pulse,due=p.reminders.filter((r)=>r.due<=p.now);
    return `<aside class="laith-pulse"><div class="card"><div class="row"><span class="laith-sun">${I("spark")}</span><h3>On your radar</h3></div><p class="tiny muted">From your Switchboard hub · ${new Date(p.now*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</p><h4>Your review</h4>${pulseLinks(p.reviews,"You're all caught up.")}<h4>Needs a hand</h4>${pulseLinks(p.attention,"No team runs need attention.")}<h4>Working toward your goals</h4>${pulseLinks(p.working,"Ready when you are.")}${due.length?`<a class="laith-pulse-item" href="#laith/checkins">${due.length} check-in${due.length===1?"":"s"} due ${I("arrow")}</a>`:""}<div class="laith-machines">${p.machines.map((n)=>`<span class="small"><i class="dot ${n.online?"ok":""}"></i>${esc(n.node)}</span>`).join("")}</div><a class="btn sm ghost" href="#studio">Open Agent Studio ${I("arrow")}</a></div></aside>`;
  }

  function renderConversation(){
    const busy=activeTurn();
    const chats=library.chats.map((c)=>`<a class="laith-chat-link ${chat&&chat.id===c.id?"selected":""}" href="#laith/chat/${esc(c.id)}">${I("chat")}<span>${esc(c.title)}</span></a>`).join("");
    $("#laithContent").innerHTML=`<div class="laith-layout"><aside class="laith-sessions"><p class="tiny muted">YOUR CONVERSATIONS</p>${chats||'<p class="small muted">Your conversations will live here.</p>'}${library.archived_chats.length?`<details class="more"><summary>Archived conversations</summary>${library.archived_chats.map((c)=>`<div class="list-row"><span class="small grow">${esc(c.title)}</span><button class="sm" data-restore-chat="${esc(c.id)}">Restore</button></div>`).join("")}</details>`:""}</aside><section class="laith-main">${chat?renderChat(busy):welcome()}</section>${pulsePanel()}</div>`;
    $$("[data-restore-chat]").forEach((b)=>b.onclick=(e)=>guard(async()=>{await api(`/api/laith/chats/${encodeURIComponent(b.dataset.restoreChat)}/archive`,{body:{archived:false}});pages.laith(routeArg);},e.currentTarget));
    $$("[data-laith-starter]").forEach((b)=>b.onclick=(e)=>newChat(b.dataset.laithStarter,e.currentTarget));
    if(!chat)return;
    const input=$("#laithMessage");input.value=drafts[chat.id]||"";input.oninput=()=>drafts[chat.id]=input.value;
    $("#laithForm").onsubmit=(e)=>{e.preventDefault();sendMessage($("#laithSend"));};
    $("#laithModel").onchange=(e)=>options.model=e.target.value;
    $("#laithAbility").onchange=(e)=>options.ability=e.target.value;
    for(const key of ["memory","knowledge","tools"])$("#laithOpt"+key).onchange=(e)=>options[key]=e.target.checked;
    $("#laithBilling").onchange=(e)=>options.billing=e.target.value;
    $("#laithBudget").onchange=(e)=>options.budget=Number(e.target.value);
    if($("#archiveLaith"))$("#archiveLaith").onclick=(e)=>guard(async()=>{await api(`/api/laith/chats/${chat.id}/archive`,{body:{archived:true}});location.hash="#laith";},e.currentTarget);
    $$("[data-goal-turn]").forEach((b)=>b.onclick=()=>goalModal(b.dataset.goalTurn));
    const transcript=$("#laithTranscript");transcript.scrollTop=transcript.scrollHeight;
  }

  function welcome(){return `<div class="laith-welcome card"><div class="laith-hello"><span class="laith-eyebrow">MEET YOUR COMPANION</span><h2>Hi, I'm Laith.</h2><p>Bring me a rough idea, a tricky sentence, or a goal that needs a team. We'll find the next step together.</p><p class="small muted">Our chats stay here. You choose what I remember.</p></div>${portrait}</div><div class="laith-starters">${[["Make a plan","Help me shape a practical team goal. Ask me what I want to achieve and how we will judge success."],["Create something","Help me develop an idea into a useful deliverable. Start by asking what I want to make."],["Polish my writing","I want help improving some writing while keeping my own voice. Ask me to paste the text."],["Catch me up","Summarize the current team goals, pending approvals and connected machines from the observed Switchboard status."]].map(([title,message])=>`<button class="laith-starter" data-laith-starter="${esc(message)}"><b>${title}</b><span>${I("arrow")}</span></button>`).join("")}</div><p class="small muted">Everyday chat or a manager-led crew: you choose when an idea becomes work.</p>`;}

  function renderChat(busy){
    const turns=chat.turns.filter((t)=>!["prepared","superseded"].includes(t.status));
    return `<div class="laith-chat card"><header class="row"><img class="laith-avatar" src="/static/laith.png" alt="Laith"><div class="grow"><h2>${esc(chat.title)}</h2><span class="small muted">Laith · here to help</span></div>${!busy?'<button class="sm ghost" id="archiveLaith">Archive</button>':""}</header><div class="laith-transcript" id="laithTranscript" aria-live="polite">${turns.length?turns.map((t)=>`<div class="laith-message laith-user"><span class="tiny muted">You</span><div>${esc(t.message)}</div></div><article class="laith-message laith-answer"><div class="row"><b>Laith</b><span class="tiny muted">${esc((t.plan||{}).model_label||"")}</span></div>${t.answer?`<div class="md">${md(t.answer)}</div>`:!["failed","interrupted"].includes(t.status)?'<p class="small muted">Working on your request…</p>':""}${t.error?`<p class="crit small">${esc(t.error)}</p>`:""}${t.status==="waiting"?'<p class="warn small">Finish the linked app task and paste its result there. This conversation will pick it up.</p>':""}<div class="row">${t.task_id?`<a class="small" href="#history/${esc(t.task_id)}">Open task and files</a>`:""}${t.status==="done"?`<button class="sm ghost" data-goal-turn="${esc(t.id)}">Turn into a team goal</button>`:""}</div></article>`).join(""):`<div class="laith-chat-empty">${portrait}<h3>What's on your mind?</h3><p class="muted small">Tell me what you want to work on.</p></div>`}</div><form id="laithForm" class="laith-composer"><div class="row">${field("Model",`<select id="laithModel" ${busy?"disabled":""}>${models()}</select>`)}${field("Ability",`<select id="laithAbility" ${busy?"disabled":""}>${library.abilities.map((a)=>`<option value="${esc(a.id)}" ${a.id===options.ability?"selected":""}>${esc(a.name)}</option>`).join("")}</select>`)}</div><label class="sr-only" for="laithMessage">Message Laith</label><textarea id="laithMessage" placeholder="Message Laith…" rows="3" maxlength="12000" ${busy?"disabled":""}></textarea><div class="row laith-context">${[["memory","Saved memory"],["knowledge","Project knowledge"],["tools","Connected tools"]].map(([key,label])=>`<label><input id="laithOpt${key}" type="checkbox" ${options[key]?"checked":""} ${busy?"disabled":""}> ${label}</label>`).join("")}</div><details class="more"><summary>Spending options</summary><div class="row">${field("Spending",`<select id="laithBilling"><option value="plans_only">Plans and free only</option><option value="plans_first" ${options.billing==="plans_first"?"selected":""}>Plans first; allow API use</option><option value="any" ${options.billing==="any"?"selected":""}>Allow API use</option></select>`)}${field("Request estimate allowance ($)",`<input id="laithBudget" type="number" min="0" max="1000" step="0.01" value="${options.budget}">`)}</div><p class="tiny muted">An estimate allowance gates this request; it is not a guaranteed billing cap.</p></details><div class="row"><span class="tiny muted grow">Selected context is sent to your chosen model. Installations keep their own approval and secure credential forms.</span><button class="primary" id="laithSend" ${busy?"disabled":""}>${busy?"Working…":"Send"} ${I("arrow")}</button></div></form></div>`;
  }

  async function sendMessage(button){
    await guard(async()=>{
      const message=$("#laithMessage").value.trim();if(!message)return;
      const key=chat.id;
      const turn=await api(`/api/laith/chats/${key}/prepare`,{body:{message,model:options.model,ability:options.ability,use_memory:options.memory,use_knowledge:options.knowledge,tools:options.tools?"auto":"none",billing:options.billing,max_cost_usd:options.budget}});
      const send=async()=>{await api(`/api/laith/chats/${key}/send`,{body:{turn_id:turn.id,confirm:true}});drafts[key]="";pages.laith("chat/"+key);};
      if(turn.plan.needs_confirmation||turn.tools_enabled||turn.plan.effective_cost_usd>0){
        const m=modal({title:"Send this request to Laith?",body:`<p><b>${esc(turn.plan.model_label)}</b> · Estimated ${money(turn.plan.effective_cost_usd)}</p><p class="small">${turn.memory_count} saved memories · ${turn.sources.length} knowledge excerpts · ${turn.tools_enabled?"Configured connected tools enabled":"Connected tools off"}</p><p class="small muted">${esc((turn.plan.warnings||[]).join(" "))}</p>`,foot:'<button data-close>Cancel</button><button class="primary" id="confirmLaithSend">Confirm and send</button>'});
        $("#confirmLaithSend",m.el).onclick=(e)=>guard(async()=>{await send();m.close();},e.currentTarget);
      }else await send();
    },button);
  }

  function goalModal(turn){
    const m=modal({title:"Turn this conversation into a goal",body:field("Goal title",'<input id="laithGoalTitle" maxlength="160" placeholder="What should the team deliver?">')+'<p class="small muted">Saves a planned manager-led crew with this exchange as context. Open the goal and choose Start team when you are ready.</p>',foot:'<button data-close>Cancel</button><button id="laithSaveGoal" class="primary">Save planned goal</button>'});
    $("#laithSaveGoal",m.el).onclick=(e)=>guard(async()=>{await api(`/api/laith/chats/${chat.id}/goal`,{body:{turn_id:turn,title:$("#laithGoalTitle",m.el).value}});m.close();location.hash="#studio";},e.currentTarget);
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
