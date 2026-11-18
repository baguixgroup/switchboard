"use strict";
/* Switchboard dashboard: plain JavaScript, no build step. Pages: Ask, History, Plans & credits, Machines & apps, Settings. */

// ---------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (x) => (x == null ? "—" : x === 0 ? "$0" : x < 0.0001 ? "<$0.0001" : "$" + Number(x).toFixed(x < 1 ? 4 : 2));
const fmtWhen = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return new Date(ts * 1000).toLocaleDateString();
}
function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime(), d = (t - Date.now()) / 1000;
  if (d < 0) return "passed";
  if (d < 3600) return "in " + Math.max(1, Math.round(d / 60)) + " min";
  if (d < 86400) return "in " + Math.round(d / 3600) + " h";
  if (d < 7 * 86400) return "in " + Math.round(d / 86400) + " days";
  return "on " + new Date(t).toLocaleDateString();
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const touch = matchMedia("(pointer: coarse)").matches;

// ---------------------------------------------------------------- icons
const P = {
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.6A8 8 0 1 1 21 12z"/>',
  history: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  wallet: '<rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10h18M16 15h2"/>',
  monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  send: '<path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-9 9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  alert: '<path d="M12 3.5l9 16H3z"/><path d="M12 10v4M12 17v.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  spark: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18h2"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5v.01M7 16.5v.01"/>',
  windows: '<path d="M3 5.6l7.5-1.1v7H3zM12.5 4.2L21 3v8.5h-8.5zM3 13h7.5v6.6L3 18.5zM12.5 13H21v8l-8.5-1.2z"/>',
  laptop: '<rect x="5" y="5" width="14" height="10" rx="1.5"/><path d="M2.5 19h19"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/>',
  puzzle: '<path d="M9 3h4v2.5a2 2 0 1 0 4 0V3h4v6h-2.5a2 2 0 1 0 0 4H21v8h-6v-2.5a2 2 0 1 0-4 0V21H3v-8h2.5a2 2 0 1 0 0-4H3V3z"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  bell: '<path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9M10 20a2 2 0 0 0 4 0"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
};
const I = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || ""}</svg>`;

// ---------------------------------------------------------------- names in plain language
const PNAME = { anthropic: "Claude API", openai: "OpenAI API", deepseek: "DeepSeek", xai: "Grok API (xAI)", gemini: "Gemini API",
  github: "GitHub Models", openrouter: "OpenRouter", mistral: "Mistral", ollama: "Ollama (on your machine)", "claude-code": "Claude plan",
  codex: "ChatGPT plan", "gemini-cli": "Google plan", "copilot-cli": "Copilot plan", "grok-build": "SuperGrok plan", mock: "Test model", flaky: "Test model 2" };
const pname = (p) => PNAME[p] || p;
const PLANINFO = {
  "claude-code": ["Claude", "Claude Pro or Max, through Claude Code"],
  codex: ["ChatGPT", "ChatGPT Plus or Pro, through Codex"],
  "gemini-cli": ["Gemini", "Your Google account, through Gemini CLI"],
  "copilot-cli": ["GitHub Copilot", "Copilot Free or Pro, through Copilot CLI"],
  "grok-build": ["Grok", "SuperGrok, through Grok Build (beta)"],
};
const APPNAME = { unity: "Unity Editor", photoshop: "Photoshop", blender: "Blender", playwright: "Browser testing",
  mobile: "Phones & simulators", filesystem: "Files & folders", github: "GitHub", hostinger: "Hostinger", desktop: "Desktop control" };
const appName = (a) => APPNAME[a] || a;
const SPEND = {
  plans_only: ["Plans & free only", "Never spend API credit. Uses your subscriptions' daily limits, free tiers and local models."],
  plans_first: ["Plans first", "Use your plans and free tiers first; spend credit only when they can't do the task or are at their limit."],
  any: ["Best value", "Pick on quality and cost across everything you have, credit included."],
};
const TYPENAME = { prepaid: "Credit", free_tier: "Free limit", subscription: "Plan", payg: "Budget" };

// ---------------------------------------------------------------- API, toasts, modals
let TOKEN = new URLSearchParams(location.search).get("token") || store.get("sb_token") || "";
if (new URLSearchParams(location.search).get("token")) { store.set("sb_token", TOKEN); history.replaceState(null, "", location.pathname + location.hash); }
function setTokenCookie() { if (TOKEN) document.cookie = "sb_token=" + encodeURIComponent(TOKEN) + "; path=/; SameSite=Strict"; }
setTokenCookie();
let tokenPrompt = null;

async function api(path, opts = {}) {
  const headers = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (TOKEN) headers.Authorization = "Bearer " + TOKEN;
  const r = await fetch(path, { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  if (r.status === 401) { await askToken(); return api(path, opts); }
  if (r.status === 204) return null;
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok && !(opts.allow || []).includes(r.status)) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : r.status + " " + r.statusText);
  }
  return data;
}

function askToken() {
  if (tokenPrompt) return tokenPrompt;
  tokenPrompt = new Promise((resolve) => {
    const m = modal({ title: "Connect to your hub", closable: false,
      body: `<p>This device needs your Switchboard token once. You'll find it in <span class="mono">secrets.env</span> on the hub
        (the line <span class="mono">SWITCHBOARD_TOKEN=…</span>), or in the hub's dashboard under <b>Settings → About</b>.</p>
        <label class="field">Token<input id="tokIn" type="password" autocomplete="off" placeholder="Paste the token"></label>
        <p class="tiny muted" style="margin-top:10px">Tip: on your own PC or Mac, open <span class="mono">http://localhost:8766</span> instead. It needs no token.</p>`,
      foot: `<button class="primary" id="tokSave">Connect</button>` });
    const save = () => { const v = $("#tokIn", m.el).value.trim(); if (!v) return; TOKEN = v; store.set("sb_token", v); setTokenCookie(); m.close(); tokenPrompt = null; resolve(); };
    $("#tokSave", m.el).onclick = save;
    $("#tokIn", m.el).onkeydown = (e) => { if (e.key === "Enter") save(); };
    setTimeout(() => $("#tokIn", m.el).focus(), 50);
  });
  return tokenPrompt;
}

function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}
async function guard(fn, btn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
}

function modal({ title, body = "", foot = "", wide = false, closable = true, onClose }) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal${wide ? " wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="modal-head"><h2>${esc(title)}</h2>${closable ? `<button class="ghost icon" data-close aria-label="Close">${I("x")}</button>` : ""}</div>
    <div class="modal-body">${body}</div><div class="modal-foot${foot ? "" : " hide"}">${foot}</div></div>`;
  $("#modals").appendChild(back);
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape" && closable) close(); };
  document.addEventListener("keydown", onKey);
  if (closable) back.addEventListener("click", (e) => { if (e.target === back || e.target.closest("[data-close]")) close(); });
  const el = $(".modal", back);
  return { el, close, setBody: (h) => { $(".modal-body", el).innerHTML = h; }, setFoot: (h) => { const f = $(".modal-foot", el); f.innerHTML = h; f.classList.toggle("hide", !h); } };
}
function copyText(text) {
  (navigator.clipboard && isSecureContext ? navigator.clipboard.writeText(text) : Promise.reject())
    .then(() => toast("Copied"))
    .catch(() => { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Copied"); });
}
const copyBox = (text) => `<div class="copybox"><code>${esc(text)}</code><button class="sm" data-copy="${esc(text)}">${I("copy")} Copy</button></div>`;
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copy]");
  if (c) { copyText(c.dataset.copy); return; }
  const cb = e.target.closest(".cb-copy");
  if (cb) copyText(cb.closest(".codeblock").querySelector("pre").innerText);
});

// ---------------------------------------------------------------- markdown (safe: escapes everything first)
function md(src) {
  const blocks = [];
  let text = String(src || "").replace(/\r\n/g, "\n").replace(/```([^\n`]*)\n([\s\S]*?)```/g, (_, info, code) => {
    blocks.push({ info: info.trim(), code: code.replace(/\n$/, "") });
    return "\n\u0000" + (blocks.length - 1) + "\u0000\n";
  });
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = [];
  let para = [], list = null, quote = [], table = [];
  const flush = () => {
    if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; }
    if (list) { out.push(`<${list.tag}>` + list.items.map((i) => "<li>" + inline(i) + "</li>").join("") + `</${list.tag}>`); list = null; }
    if (quote.length) { out.push("<blockquote>" + quote.map(inline).join("<br>") + "</blockquote>"); quote = []; }
    if (table.length) {
      const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
      out.push("<table>" + rows.map((r, i) => "<tr>" + r.map((c) => (i ? `<td>${c}</td>` : `<th>${c}</th>`)).join("") + "</tr>").join("") + "</table>");
      table = [];
    }
  };
  for (const line of text.split("\n")) {
    const code = line.match(/^\u0000(\d+)\u0000$/);
    if (code) {
      flush();
      const b = blocks[+code[1]];
      out.push(`<div class="codeblock"><div class="cb-head"><span>${esc(b.info || "code")}</span><button class="ghost sm cb-copy">${I("copy")} Copy</button></div><pre><code>${esc(b.code)}</code></pre></div>`);
      continue;
    }
    let m;
    if (/^\s*$/.test(line)) { flush(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) { if (para.length || list) flush(); table.push(line.trim()); continue; }
    if ((m = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/))) {
      const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (para.length || (list && list.tag !== tag) || table.length) flush();
      if (!list) list = { tag, items: [] };
      list.items.push(m[2]);
      continue;
    }
    if ((m = line.match(/^>\s?(.*)$/))) { if (para.length || list) flush(); quote.push(m[1]); continue; }
    if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
    if (list || quote.length || table.length) flush();
    para.push(line);
  }
  flush();
  return out.join("");
}

// ---------------------------------------------------------------- shared state
const S = { status: null, models: [], credits: null, nodes: [], settings: null };
const view = $("#view");
async function loadStatus() {
  S.status = await api("/api/status");
  $("#hubName").textContent = "Hub: " + S.status.node;
  $("#hubDot").className = "dot on";
}
async function loadModels() { S.models = (await api("/api/models")) || []; }
async function loadCredits() { S.credits = await api("/api/credits"); updateBadges(); }
async function loadNodes(refresh = false) { S.nodes = (await api(refresh ? "/api/nodes/refresh" : "/api/nodes", refresh ? { method: "POST" } : {})) || []; }
async function loadSettings() { S.settings = await api("/api/settings"); }
const modelByKey = (k) => S.models.find((m) => m.key === k);
function costKind(plan) {
  const m = modelByKey(plan.provider + ":" + plan.model);
  if (m && (m.features.includes("cli") || m.features.includes("handoff"))) return "plan";
  if ((m && m.price[0] === 0 && m.price[1] === 0 && !m.per_call) || plan.effective_cost_usd === 0) return "free";
  return "paid";
}
const kindBadge = (plan) => {
  const k = costKind(plan);
  if (k === "plan") return `<span class="badge ok">${I("check")} your plan · no credit</span>`;
  if (k === "free") return `<span class="badge ok">free</span>`;
  return `<span class="badge">~${money(plan.effective_cost_usd)}</span>`;
};
function updateBadges() {
  const c = S.credits;
  const n = c ? c.alerts.filter((a) => a.severity !== "info").length : 0;
  $$('[data-count="plans"]').forEach((el) => { el.textContent = n; el.classList.toggle("hide", !n); });
}

// ---------------------------------------------------------------- navigation
const NAV = [["ask", "Ask", "chat"], ["history", "History", "history"], ["plans", "Plans & credits", "wallet"],
  ["machines", "Machines & apps", "monitor"], ["settings", "Settings", "sliders"]];
const SHORT = { plans: "Plans", machines: "Machines" };
function buildNav() {
  $("#nav").innerHTML = NAV.map(([id, label, icon]) => `<a href="#${id}" data-page="${id}">${I(icon)}<span>${label}</span>${id === "plans" ? '<span class="count hide" data-count="plans"></span>' : ""}</a>`).join("");
  $("#tabbar").innerHTML = NAV.map(([id, label, icon]) => `<a href="#${id}" data-page="${id}">${I(icon)}<span>${SHORT[id] || label}</span>${id === "plans" ? '<span class="count hide" data-count="plans"></span>' : ""}</a>`).join("");
}
const PAGES = {};
let rid = 0;
const stale = (id) => id !== rid;
function route() {
  rid++;
  const [page, ...rest] = (location.hash.slice(1) || "ask").split("/");
  const name = PAGES[page] ? page : "ask";
  $$("[data-page]").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  document.title = (NAV.find((n) => n[0] === name) || [0, "Switchboard"])[1] + " · Switchboard";
  Promise.resolve(PAGES[name](rest.join("/"))).catch((e) => { view.innerHTML = `<div class="alert critical">${I("alert")}<div><div class="t">Something went wrong</div><div class="m">${esc(e.message)}</div></div></div>`; });
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
const head = (title, sub = "", actions = "") => `<div class="page-head"><div class="grow"><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ""}</div>${actions}</div>`;
const alertHtml = (a) => `<div class="alert ${esc(a.severity)}">${I(a.severity === "info" ? "info" : "alert")}<div><div class="t">${esc(a.title)}</div><div class="m">${esc(a.message)}</div></div></div>`;

// ================================================================ ASK
const ASK = { messages: [], history: [], images: [], who: null, billing: null, mode: null, effort: null, tools: "auto", busy: false, draft: "" };

PAGES.ask = function renderAsk() {
  ASK.billing = ASK.billing || store.get("sb_billing") || (S.status && S.status.router.billing) || "plans_first";
  view.innerHTML = `<div class="ask">
    <div class="row" id="askBar" style="margin-bottom:6px;min-height:30px"></div>
    <div id="askTop"></div>
    <div class="thread" id="thread"></div>
    <div class="composer"><div class="composer-box">
      <textarea id="prompt" rows="1" placeholder="Ask anything, or describe a task…" aria-label="Your message"></textarea>
      <div class="attach-chips" id="chips"></div>
      <div class="composer-bar">
        <button class="ghost icon" id="attach" title="Attach images" aria-label="Attach images">${I("image")}</button>
        <input type="file" id="file" accept="image/*" multiple hidden>
        <button class="pill" id="whoBtn" title="Who should answer?">${I("spark")}<span id="whoLabel"></span></button>
        <button class="pill" id="spendBtn" title="What may this spend?">${I("wallet")}<span id="spendLabel"></span></button>
        <button class="pill" id="optBtn" title="More options">${I("sliders")}<span id="optLabel">Options</span></button>
        <button class="primary send" id="sendBtn" title="Send" aria-label="Send">${I("send")}</button>
      </div></div>
      <div class="willuse" id="willuse"></div>
    </div></div>`;
  renderAskTop();
  renderThread();
  const ta = $("#prompt");
  ta.value = ASK.draft;
  const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 260) + "px"; };
  grow();
  ta.oninput = () => { ASK.draft = ta.value; grow(); previewPlan(); };
  ta.onkeydown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey || (!e.shiftKey && !touch))) { e.preventDefault(); send(); }
  };
  $("#sendBtn").onclick = () => send();
  $("#attach").onclick = () => $("#file").click();
  $("#file").onchange = addImages;
  $("#whoBtn").onclick = pickWho;
  $("#spendBtn").onclick = pickSpend;
  $("#optBtn").onclick = pickOptions;
  updateComposerLabels();
  renderChips();
  if (!touch) ta.focus();
  if (ASK.draft) previewPlan();
};

function updateComposerLabels() {
  const m = ASK.who && modelByKey(ASK.who);
  if ($("#whoLabel")) $("#whoLabel").textContent = m ? m.label : "Automatic";
  if ($("#spendLabel")) $("#spendLabel").textContent = SPEND[ASK.billing][0];
  const extras = [ASK.mode && ASK.mode !== "balanced" ? ASK.mode : "", ASK.effort ? "effort " + ASK.effort : "", Array.isArray(ASK.tools) ? ASK.tools.length + " app(s)" : ""].filter(Boolean);
  if ($("#optLabel")) $("#optLabel").textContent = extras.length ? extras.join(" · ") : "Options";
}

function renderAskTop() {
  const box = $("#askTop");
  if (!box) return;
  let html = "";
  const urgent = ((S.credits && S.credits.alerts) || []).filter((a) => a.severity !== "info").slice(0, 2);
  if (urgent.length && !ASK.messages.length) html += urgent.map(alertHtml).join("") + '<div style="height:10px"></div>';
  const steps = onboardingSteps();
  const done = steps.filter((s) => s.done).length;
  if (done < steps.length && !store.get("sb_onboarded") && !ASK.messages.length) {
    html += `<div class="card" style="margin-bottom:12px"><div class="card-head"><div class="grow"><h2>Getting started</h2><p>${done} of ${steps.length} done</p></div>
      <button class="ghost sm" id="hideOnb">Hide</button></div>
      <div class="progress" style="margin-bottom:10px"><i style="width:${(done / steps.length) * 100}%"></i></div>
      ${steps.filter((s) => !s.done).map((s) => `<div class="list-row"><span class="badge">${I("plus")}</span>
        <div class="grow"><b>${s.title}</b><div class="small muted">${s.help}</div></div><a class="btn sm" href="${s.href}">${s.action}</a></div>`).join("")}
      ${done ? `<div class="small muted" style="margin-top:6px">${I("check")} Done: ${steps.filter((s) => s.done).map((s) => s.title.toLowerCase()).join(", ")}</div>` : ""}</div>`;
  }
  box.innerHTML = html;
  if ($("#hideOnb")) $("#hideOnb").onclick = () => { store.set("sb_onboarded", "1"); renderAskTop(); };
}
function onboardingSteps() {
  const prov = ((S.status && S.status.providers) || []).filter((p) => p.available && p.kind !== "mock");
  return [
    { title: "Connect an AI", help: "Sign in to a plan (Claude, ChatGPT, Gemini, Copilot, Grok) or add an API key.", done: prov.length > 0, href: "#plans", action: "Set up" },
    { title: "Add your other computers", help: "Install the agent on your PC and Mac so Switchboard can use their apps and devices.", done: S.nodes.length > 1, href: "#machines", action: "Connect" },
    { title: "Tell it about your credits and free limits", help: "So it can use expiring credit first and remind you before free limits reset.", done: !!(S.credits && S.credits.sources.length), href: "#plans", action: "Add" },
    { title: "Get alerts on your phone", help: "Free ntfy app: expiring credit, unused free limits, plans hitting their limit.", done: !!(S.settings && S.settings.notify.ntfy_topic), href: "#settings", action: "Turn on" },
  ];
}

const SUGGESTIONS = [
  "Write a Python script that renames my photos by the date they were taken",
  "Plan a small Unity game: core loop, scenes and the first scripts",
  "Explain this error and how to fix it:",
  "Draft a friendly email to a client about a project delay",
];
function renderThread() {
  const th = $("#thread");
  if (!th) return;
  const bar = $("#askBar");
  if (bar) {
    bar.innerHTML = ASK.messages.length ? `<div class="grow"></div><button class="ghost sm" id="newChat">${I("edit")} New chat</button>` : "";
    if ($("#newChat")) $("#newChat").onclick = () => { if (ASK.busy) return; ASK.messages = []; ASK.history = []; PAGES.ask(); };
  }
  if (!ASK.messages.length) {
    th.innerHTML = `<div class="hello"><img src="/static/icon-192.png" alt=""><h1>What can I help with?</h1>
      <p class="muted">Switchboard picks the best AI for the task and uses your plans' daily limits before any credit.</p>
      <div class="suggest">${SUGGESTIONS.map((s) => `<button data-suggest="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
    $$("[data-suggest]", th).forEach((b) => (b.onclick = () => { const ta = $("#prompt"); ta.value = b.dataset.suggest; ASK.draft = ta.value; ta.focus(); ta.dispatchEvent(new Event("input")); }));
    return;
  }
  th.innerHTML = ASK.messages.map(msgHtml).join("");
  wireThread(th);
  const last = th.lastElementChild;
  if (last) last.scrollIntoView({ block: "end", behavior: "smooth" });
}
function msgHtml(m, i) {
  if (m.role === "user") return `<div class="msg user"><div class="bubble">${esc(m.text)}${(m.images || []).map((s) => `<img src="${s}" alt="">`).join("")}</div></div>`;
  if (m.role === "confirm") {
    const p = m.plan;
    const why = m.over ? esc(m.over) : `This will cost about <b>${money(p.effective_cost_usd)}</b> with ${esc(p.model_label)}, paid by ${esc(p.source_label)}.`;
    const alt = (p.alternatives || [])[0];
    return `<div class="msg"><div class="confirm" data-i="${i}"><b>${m.over ? "That effort may be more than this needs" : "Check before running"}</b><div class="small" style="margin-top:4px">${why}</div>
      <div class="row">${m.over ? `<button class="primary sm" data-act="lower">Use the recommended effort</button><button class="sm" data-act="run">Keep my choice</button>`
        : `<button class="primary sm" data-act="run">Run it</button>${alt ? `<button class="sm" data-act="alt">Use ${esc(alt.label)} (${money(alt.effective_cost_usd)})</button>` : ""}`}
      <button class="ghost sm" data-act="cancel">Cancel</button></div></div></div>`;
  }
  const p = m.plan || {};
  const where = p.executed ? "" : "";
  let body;
  if (m.state === "working") {
    const plan = S.credits && (S.credits.plans || []).find((x) => x.provider === p.provider);
    body = `<div class="row"><span class="typing"><i></i><i></i><i></i></span><span class="muted small" data-elapsed="${m.startedAt}">Working…</span>
      ${plan && plan.where ? `<span class="badge">on ${esc(plan.where)}</span>` : ""}</div>`;
  } else if (m.state === "waiting") {
    body = `<div class="md">${md(m.task.result)}</div>${handoffHtml(m.task)}`;
  } else if (m.state === "failed") {
    body = `<div class="alert critical">${I("alert")}<div class="grow"><div class="t">That didn't work</div><div class="m" style="white-space:pre-wrap">${esc(m.error)}</div></div></div>
      <div class="row" style="margin-top:10px"><button class="sm" data-retry="${i}">${I("refresh")} Try again</button></div>`;
  } else {
    const t = m.task || {};
    body = `<div class="md">${md(t.result)}</div>${filesHtml(t.id, m.files)}${detailsHtml(t, p)}`;
  }
  const t = m.task || {};
  const exec = (t.plan && t.plan.executed) || "";
  const label = exec && exec !== p.provider + ":" + p.model ? (modelByKey(exec) || { label: exec }).label + " (fallback)" : p.model_label || "Switchboard";
  const meta = m.state === "done" ? `<span>· ${money(t.cost_usd)}</span>` : "";
  return `<div class="msg ai"><div class="body"><div class="msg-head"><span class="who">${esc(label)}</span>${p.provider ? kindBadge(p) : ""}${p.effort ? `<span class="badge">${I("bolt")} ${esc(p.effort)}</span>` : ""}${meta}${where}</div>${body}</div></div>`;
}
const addFileBtn = (id, label = "Add a file") => `<label class="btn sm ghost" style="cursor:pointer">${I("plus")} ${label}<input type="file" hidden data-addfile="${esc(id)}"></label>`;
function handoffHtml(t) {
  const h = (t.plan || {}).handoff || {};
  const desks = S.nodes.filter((n) => n.online && ["windows", "macos"].includes(n.platform));
  const status = h.watching
    ? `<div class="row small muted" style="margin-top:10px"><span class="typing"><i></i><i></i><i></i></span>Watching the Downloads folder on <b>${esc(h.watching)}</b> for your image…</div>`
    : h.watch_note ? `<p class="small warn" style="margin:10px 0 0">${esc(h.watch_note)} Open the app again, or add the image with Add a file.</p>` : "";
  return `<div class="row" style="margin-top:12px">
      ${desks.map((n) => `<button class="sm primary" data-open="${esc(n.node)}" data-task="${esc(t.id)}">${I("external")} Open ${esc(h.label)} on ${esc(n.node)}</button>`).join("")}
      <button class="sm" data-copy="${esc(h.prompt || "")}">${I("copy")} Copy prompt</button>
      ${h.url ? `<a class="btn sm" href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.label)} website ${I("external")}</a>` : ""}
      ${addFileBtn(t.id)}</div>${status}`;
}
function followWaiting(msg) {
  const id = msg.task.id, until = Date.now() + 30 * 60e3;
  let last = JSON.stringify(msg.task.plan.handoff);
  const tick = async () => {
    if (!ASK.messages.includes(msg) || Date.now() > until) return;
    try {
      const t = await api("/api/tasks/" + encodeURIComponent(id));
      if (t.status === "done") {
        msg.state = "done"; msg.task = t;
        msg.files = await api("/api/tasks/" + encodeURIComponent(id) + "/files").catch(() => []);
        ASK.history.push({ role: "user", content: msg.body.prompt }, { role: "assistant", content: t.result });
        toast("Image received");
        if (onPage("ask")) renderThread();
        return;
      }
      const now = JSON.stringify((t.plan || {}).handoff);
      if (now !== last) { last = now; msg.task = t; if (onPage("ask")) renderThread(); }
    } catch (e) { /* keep waiting */ }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
}
const onPage = (name) => (location.hash.slice(1).split("/")[0] || "ask") === name;
async function refreshTask(id) {
  const t = await api("/api/tasks/" + encodeURIComponent(id));
  const msg = ASK.messages.find((m) => m.task && m.task.id === id);
  if (msg) {
    msg.task = t;
    if (t.status === "done" && msg.state === "waiting") ASK.history.push({ role: "user", content: msg.body.prompt }, { role: "assistant", content: t.result });
    msg.state = t.status === "waiting" ? "waiting" : t.status === "failed" ? "failed" : "done";
    msg.files = await api("/api/tasks/" + encodeURIComponent(id) + "/files").catch(() => []);
  }
  if (onPage("ask")) renderThread();
  else if (location.hash.startsWith("#history/")) route();
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-open]");
  if (!b) return;
  guard(async () => {
    const r = await api(`/api/tasks/${encodeURIComponent(b.dataset.task)}/handoff/open`, { body: { node: b.dataset.open } });
    toast(r.message || "Opened");
    refreshTask(b.dataset.task);
  }, b);
});
document.addEventListener("change", (e) => {
  const inp = e.target.closest("input[data-addfile]");
  if (!inp || !inp.files.length) return;
  const f = inp.files[0], id = inp.dataset.addfile;
  if (f.size > 25e6) { toast("Files up to 25 MB can be added.", true); return; }
  const r = new FileReader();
  r.onload = () => guard(async () => {
    await api(`/api/tasks/${encodeURIComponent(id)}/files`, { body: { name: f.name, b64: String(r.result) } });
    toast(`Added ${f.name}`);
    refreshTask(id);
  });
  r.readAsDataURL(f);
});
function filesHtml(id, files) {
  const list = (files || []).filter((f) => !["prompt.md", "response.md"].includes(f.path));
  if (!id || !list.length) return "";
  const url = (f) => `/api/tasks/${encodeURIComponent(id)}/file?path=${encodeURIComponent(f.path)}`;
  return `<div class="files">${list.map((f) => (f.kind === "image"
    ? `<a href="${url(f)}" target="_blank" rel="noopener"><img src="${url(f)}" alt="${esc(f.path)}" loading="lazy"></a>`
    : `<a class="file" href="${url(f)}" download>${I("download")} ${esc(f.path.replace(/^files\//, ""))}</a>`)).join("")}</div>`;
}
function detailsHtml(t, p) {
  const calls = ((t.plan && t.plan.tool_calls) || []);
  return `<details class="more"><summary>${I("info")} Details</summary>
    <ul>${(p.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}
    ${(p.warnings || []).map((w) => `<li class="warn">${esc(w)}</li>`).join("")}
    ${calls.length ? `<li>Used ${calls.length} tool call(s): ${calls.slice(0, 6).map((c) => esc(c.tool)).join(", ")}</li>` : ""}
    ${t.output_path ? `<li>Saved to <span class="mono">${esc(t.output_path)}</span></li>` : ""}</ul>
    ${t.id ? `<div style="margin-top:6px">${addFileBtn(t.id, "Add a file to this task")}</div>` : ""}</details>`;
}
function wireThread(th) {
  $$(".confirm", th).forEach((box) => {
    const m = ASK.messages[+box.dataset.i];
    $$("[data-act]", box).forEach((b) => (b.onclick = async () => {
      const act = b.dataset.act;
      ASK.messages.splice(+box.dataset.i, 1);
      if (act === "cancel") { ASK.busy = false; renderThread(); return; }
      let body = m.body, plan = m.plan;
      try {
        if (act === "lower") { body = { ...body, effort: null }; plan = await api("/api/plan", { body }); }
        if (act === "alt") { body = { ...body, model: m.plan.alternatives[0].key }; plan = await api("/api/plan", { body }); }
      } catch (e) { toast(e.message, true); renderThread(); return; }
      runPlan(body, plan);
    }));
  });
  $$("[data-retry]", th).forEach((b) => (b.onclick = () => {
    const m = ASK.messages[+b.dataset.retry];
    ASK.messages.splice(+b.dataset.retry, 1);
    runPlan(m.body, m.plan);
  }));
}
setInterval(() => $$("[data-elapsed]").forEach((el) => { el.textContent = "Working… " + Math.round((Date.now() - +el.dataset.elapsed) / 1000) + "s"; }), 1000);

function buildBody(text) {
  const m = ASK.who && modelByKey(ASK.who);
  return { prompt: text, model: m ? m.key : null, effort: ASK.effort, mode: ASK.mode, billing: ASK.billing,
    tools: ASK.tools, images: ASK.images.map((x) => x.data) };
}
async function send() {
  const ta = $("#prompt");
  const text = (ta ? ta.value : "").trim();
  if (!text || ASK.busy) return;
  const body = buildBody(text);
  ASK.busy = true;
  ASK.messages.push({ role: "user", text, images: ASK.images.map((x) => x.data) });
  ASK.draft = ""; ASK.images = [];
  if (ta) { ta.value = ""; ta.style.height = "auto"; }
  $("#willuse").textContent = "";
  renderChips();
  renderAskTop();
  renderThread();
  let plan;
  try { plan = await api("/api/plan", { body }); }
  catch (e) { ASK.messages.push({ role: "ai", state: "failed", error: e.message, body }); ASK.busy = false; renderThread(); return; }
  const over = (plan.warnings || []).find((w) => w.includes("overkill"));
  if (over || plan.needs_confirmation) { ASK.messages.push({ role: "confirm", plan, body, over }); renderThread(); return; }
  runPlan(body, plan);
}
async function runPlan(body, plan) {
  ASK.busy = true;
  const msg = { role: "ai", state: "working", plan, body, startedAt: Date.now() };
  ASK.messages.push(msg);
  renderThread();
  try {
    const r = await api("/api/tasks", { body: { ...body, plan, confirm: true, history: ASK.history } });
    let t;
    for (;;) {
      await new Promise((res) => setTimeout(res, 1500));
      t = await api("/api/tasks/" + encodeURIComponent(r.task_id));
      if (t.status === "done" || t.status === "failed" || t.status === "waiting") break;
    }
    if (t.status === "waiting") { msg.state = "waiting"; msg.task = t; msg.files = []; followWaiting(msg); }
    else if (t.status === "failed") { msg.state = "failed"; msg.error = t.error; }
    else {
      msg.state = "done"; msg.task = t;
      msg.files = await api("/api/tasks/" + encodeURIComponent(t.id) + "/files").catch(() => []);
      ASK.history.push({ role: "user", content: body.prompt }, { role: "assistant", content: t.result });
    }
  } catch (e) { msg.state = "failed"; msg.error = e.message; }
  ASK.busy = false;
  if (location.hash.slice(1).split("/")[0] === "ask" || !location.hash) renderThread();
  loadCredits().catch(() => {});
}
const previewPlan = debounce(async () => {
  const el = $("#willuse"), ta = $("#prompt");
  if (!el || !ta) return;
  const text = ta.value.trim();
  if (text.length < 4 || (S.settings && S.settings.router.classifier === "llm")) { el.textContent = ""; return; }
  try {
    const p = await api("/api/plan", { body: buildBody(text) });
    const k = costKind(p);
    el.innerHTML = `Will use <b>${esc(p.model_label)}</b> · ${k === "plan" ? "your plan, no credit" : k === "free" ? "free" : "about " + money(p.effective_cost_usd)}${p.effort ? " · " + esc(p.effort) + " effort" : ""}${p.tools.length ? " · with " + p.tools.map(appName).join(", ") : ""}`;
  } catch (e) { el.innerHTML = `<span class="warn">${esc(e.message)}</span>`; }
}, 650);

function addImages(e) {
  const files = [...e.target.files].slice(0, 6);
  files.forEach((f) => { const r = new FileReader(); r.onload = () => { ASK.images.push({ name: f.name, data: r.result }); renderChips(); }; r.readAsDataURL(f); });
  e.target.value = "";
}
function renderChips() {
  const el = $("#chips");
  if (!el) return;
  el.innerHTML = ASK.images.map((x, i) => `<span><img src="${x.data}" alt="">${esc(x.name.slice(0, 24))}<button class="ghost sm icon" data-rm="${i}" aria-label="Remove">${I("x")}</button></span>`).join("");
  $$("[data-rm]", el).forEach((b) => (b.onclick = () => { ASK.images.splice(+b.dataset.rm, 1); renderChips(); }));
}

function pickWho() {
  const groups = { plan: [], free: [], paid: [], off: [] };
  for (const m of S.models) {
    const k = !m.available ? "off" : m.features.includes("cli") ? "plan" : m.price[0] === 0 && m.price[1] === 0 && !m.per_call ? "free" : "paid";
    groups[k].push(m);
  }
  const opt = (m, off) => `<div class="opt${ASK.who === m.key ? " on" : ""}${off ? " off" : ""}" data-key="${off ? "" : esc(m.key)}">
    <div class="avatar">${esc((pname(m.provider) || "?")[0])}</div><div class="grow"><div><b>${esc(m.label)}</b></div>
    <div class="sub">${esc(pname(m.provider))}${m.features.includes("cli") || (m.price[0] === 0 && m.price[1] === 0) ? "" : ` · $${m.price[0]} / $${m.price[1]} per million tokens`}${m.verified ? "" : " · price not yet checked"}${off ? " · not set up" : ""}</div></div></div>`;
  const section = (title, list, off = false) => (list.length ? `<div class="group-label">${title}</div>` + list.map((m) => opt(m, off)).join("") : "");
  const md = modal({ title: "Who should answer?", wide: true,
    body: `<div class="opt${!ASK.who ? " on" : ""}" data-key="auto"><div class="avatar">${I("spark")}</div><div class="grow"><b>Automatic (recommended)</b>
      <div class="sub">Switchboard picks the best fit for each task and your spending setting.</div></div></div>
      ${section("Your plans · no credit", groups.plan)}${section("Free", groups.free)}${section("Pay per use", groups.paid)}${section("Not set up yet", groups.off, true)}` });
  $$(".opt[data-key]", md.el).forEach((o) => (o.onclick = () => {
    if (!o.dataset.key) return;
    ASK.who = o.dataset.key === "auto" ? null : o.dataset.key;
    md.close(); updateComposerLabels(); previewPlan();
  }));
}
function pickSpend() {
  const md = modal({ title: "What may this spend?",
    body: `<div class="choice">${Object.entries(SPEND).map(([k, [t, d]]) => `<label><input type="radio" name="spend" value="${k}" ${ASK.billing === k ? "checked" : ""}><b>${t}</b><span>${d}</span></label>`).join("")}</div>
      <p class="tiny muted" style="margin-top:12px">Your default is set in Settings. This choice is remembered on this device.</p>` });
  $$("input[name=spend]", md.el).forEach((r) => (r.onchange = () => { ASK.billing = r.value; store.set("sb_billing", r.value); md.close(); updateComposerLabels(); previewPlan(); }));
}
function pickOptions() {
  const apps = [...new Set(S.nodes.filter((n) => n.online).flatMap((n) => Object.entries(n.mcp || {}).filter(([, st]) => st.state !== "error").map(([s]) => s)))];
  const seg = (name, items, cur) => `<div class="seg" data-seg="${name}">${items.map(([v, l]) => `<button data-v="${v}" class="${(cur || "") === v ? "on" : ""}">${l}</button>`).join("")}</div>`;
  const md = modal({ title: "Options",
    body: `<div class="stack">
      <div><b>Quality or savings</b><p class="small muted">How to weigh a better answer against cost.</p>${seg("mode", [["", "Balanced"], ["best", "Best quality"], ["cheapest", "Cheapest"]], ASK.mode)}</div>
      <div><b>Thinking effort</b><p class="small muted">Automatic picks it from how hard the task looks. Higher is slower and costs more.</p>${seg("effort", [["", "Automatic"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["xhigh", "Extra high"], ["max", "Max"]], ASK.effort)}</div>
      <div><b>Apps it may use</b><p class="small muted">Connected apps on your machines (Unity, Photoshop, phones…).</p>
        ${apps.length ? `<label class="switch"><input type="checkbox" id="toolsAuto" ${!Array.isArray(ASK.tools) ? "checked" : ""}> Choose automatically</label>
        <div id="toolList" class="row" style="margin-top:6px">${apps.map((a) => `<label class="badge" style="cursor:pointer;padding:6px 10px"><input type="checkbox" value="${esc(a)}" ${Array.isArray(ASK.tools) && ASK.tools.includes(a) ? "checked" : ""}> ${esc(appName(a))}</label>`).join("")}</div>`
        : `<p class="small">No apps connected yet. <a href="#machines/apps" data-close>Connect apps</a></p>`}</div></div>`,
    foot: `<button class="primary" data-close>Done</button>`,
    onClose: () => {
      updateComposerLabels(); previewPlan();
    } });
  $$("[data-seg]", md.el).forEach((s) => $$("button", s).forEach((b) => (b.onclick = () => {
    $$("button", s).forEach((x) => x.classList.toggle("on", x === b));
    ASK[s.dataset.seg] = b.dataset.v || null;
  })));
  const sync = () => {
    if (!$("#toolsAuto", md.el)) return;
    const auto = $("#toolsAuto", md.el).checked;
    $("#toolList", md.el).style.opacity = auto ? 0.5 : 1;
    const picked = $$("#toolList input:checked", md.el).map((x) => x.value);
    ASK.tools = auto ? "auto" : picked.length ? picked : "none";
  };
  $$("input[type=checkbox]", md.el).forEach((c) => (c.onchange = sync));
  sync();
}

// ================================================================ HISTORY
PAGES.history = async function renderHistory(arg) {
  const me = rid;
  if (arg) return renderTask(arg);
  view.innerHTML = head("History", "Everything you've asked, with the answers and files.", `<input id="hSearch" type="search" placeholder="Search…" style="min-width:220px">`) + `<div id="hList" class="card"><span class="muted">Loading…</span></div>`;
  const tasks = (await api("/api/tasks?limit=200")) || [];
  if (stale(me)) return;
  const draw = (q = "") => {
    const list = tasks.filter((t) => !q || (t.prompt || "").toLowerCase().includes(q.toLowerCase()));
    $("#hList").innerHTML = list.length ? list.map((t) => {
      const st = t.status === "done" ? `<span class="badge ok">${I("check")} done</span>` : t.status === "failed" ? '<span class="badge crit">failed</span>' : t.status === "waiting" ? '<span class="badge warn">waiting for your image</span>' : `<span class="badge info">${esc(t.status)}</span>`;
      const exec = (t.plan && (t.plan.executed || (t.plan.provider + ":" + t.plan.model))) || "";
      const ml = (modelByKey(exec) || { label: (t.plan && t.plan.model_label) || exec }).label;
      return `<a class="list-row" href="#history/${encodeURIComponent(t.id)}" style="color:inherit;text-decoration:none">
        <div class="grow"><div class="ellipsis"><b>${esc((t.prompt || "").slice(0, 160))}</b></div><div class="small muted">${esc(ml)} · ${money(t.cost_usd)} · ${ago(t.ts)}</div></div>${st}</a>`;
    }).join("") : `<div class="empty"><b>${q ? "Nothing matches" : "No tasks yet"}</b>${q ? "Try another word." : 'Ask something on the <a href="#ask">Ask</a> page.'}</div>`;
  };
  draw();
  $("#hSearch").oninput = (e) => draw(e.target.value);
};
async function renderTask(id) {
  const me = rid;
  const t = await api("/api/tasks/" + encodeURIComponent(id));
  const files = await api("/api/tasks/" + encodeURIComponent(id) + "/files").catch(() => []);
  if (stale(me)) return;
  const p = t.plan || {};
  view.innerHTML = `<div class="row" style="margin-bottom:14px"><a class="btn sm" href="#history">${I("back")} History</a><div class="grow"></div>
      <button class="sm" id="again">${I("refresh")} Ask again</button><button class="sm primary" id="cont">${I("chat")} Continue this chat</button></div>
    <div class="msg user"><div class="bubble">${esc(t.prompt)}</div></div>
    <div class="msg ai"><div class="body"><div class="msg-head"><span class="who">${esc(p.model_label || "")}</span>${p.provider ? kindBadge(p) : ""}<span>· ${money(t.cost_usd)} · ${ago(t.ts)}</span></div>
      ${t.status === "failed" ? `<div class="alert critical">${I("alert")}<div class="m" style="white-space:pre-wrap">${esc(t.error)}</div></div>` : `<div class="md">${md(t.result)}</div>`}
      ${t.status === "waiting" ? handoffHtml(t) : ""}
      ${filesHtml(t.id, files)}${detailsHtml(t, p)}</div></div>`;
  $("#again").onclick = () => { ASK.draft = t.prompt; location.hash = "#ask"; };
  $("#cont").onclick = () => {
    ASK.messages = [{ role: "user", text: t.prompt }, { role: "ai", state: t.status === "done" ? "done" : "failed", plan: p, task: t, files, error: t.error }];
    ASK.history = t.status === "done" ? [{ role: "user", content: t.prompt }, { role: "assistant", content: t.result }] : [];
    location.hash = "#ask";
  };
}

// ================================================================ PLANS & CREDITS
PAGES.plans = async function renderPlans() {
  const me = rid;
  view.innerHTML = head("Plans & credits", "Your subscriptions' daily limits, prepaid credit and free allowances.") + '<div class="muted">Loading…</div>';
  await Promise.all([loadCredits(), loadNodes()]);
  if (stale(me)) return;
  const c = S.credits;
  const spent = (c.spend_this_month || []).reduce((a, r) => a + (r.usd || 0), 0);
  const ready = (c.plans || []).filter((p) => p.status === "ready").length;
  const urgent = c.alerts.filter((a) => a.severity !== "info").length;
  view.innerHTML = head("Plans & credits", "Your subscriptions' daily limits, prepaid credit and free allowances.",
    `<button class="sm" id="balRefresh">${I("refresh")} Refresh balances</button>`) +
    `<div class="stats">
      <div class="stat"><div class="v">${money(spent)}</div><div class="l">Spent this month</div></div>
      <div class="stat"><div class="v">${ready} of ${(c.plans || []).length}</div><div class="l">Plans ready</div></div>
      <div class="stat"><div class="v ${urgent ? "warn" : ""}">${urgent}</div><div class="l">Things to act on</div></div>
      <div class="stat"><div class="v" style="font-size:16px;padding-top:6px">${esc(SPEND[c.billing] ? SPEND[c.billing][0] : c.billing)}</div><div class="l">Default spending (<a href="#settings">change</a>)</div></div>
    </div>
    ${c.alerts.length ? `<div class="section-title"><h2>Needs your attention</h2></div>${c.alerts.map(alertHtml).join("")}` : ""}
    <div class="section-title"><h2>Your plans</h2><span class="muted small">Daily and weekly limits, no credit</span></div>
    <div class="grid">${(c.plans || []).map(planCard).join("")}</div>
    <div class="section-title"><h2>Credits, free limits and budgets</h2><div class="grow"></div><button class="sm primary" id="addCredit">${I("plus")} Add</button></div>
    ${c.sources.length ? `<div class="grid">${c.sources.map(sourceCard).join("")}</div>` : `<div class="empty"><b>Nothing added yet</b>Add promo credit with an expiry date, a free daily limit, a plan's quota or a monthly budget.<br>Switchboard then spends expiring credit first and reminds you before free limits reset.</div>`}
    ${c.untracked_providers.length ? `<p class="small muted" style="margin-top:10px">Also connected, billed per use with no limit set: ${c.untracked_providers.map(pname).map(esc).join(", ")}.</p>` : ""}
    ${(c.spend_this_month || []).length ? `<div class="section-title"><h2>Spending this month</h2></div><div class="card scroll-x"><table class="plain"><tr><th>Model</th><th>Calls</th><th>Cost</th></tr>
      ${c.spend_this_month.map((r) => `<tr><td>${esc((modelByKey(r.provider + ":" + r.model) || { label: r.model }).label)} <span class="muted small">${esc(pname(r.provider))}</span></td><td>${r.calls}</td><td>${money(r.usd)}</td></tr>`).join("")}</table></div>` : ""}`;
  $("#balRefresh").onclick = (e) => guard(async () => { await api("/api/credits/refresh", { method: "POST" }); toast("Balances refreshed"); PAGES.plans(); }, e.currentTarget);
  $("#addCredit").onclick = addCreditWizard;
  $$("[data-setup]").forEach((b) => (b.onclick = () => planSetup(b.dataset.setup)));
  $$("[data-signin]").forEach((b) => (b.onclick = () => planSignIn(b.dataset.signin, b)));
  $$("[data-clear]").forEach((b) => (b.onclick = () => guard(async () => { await api(`/api/plans/${b.dataset.clear}/limit`, { method: "DELETE" }); toast("Marked as available again"); PAGES.plans(); }, b)));
  $$("[data-delsrc]").forEach((b) => (b.onclick = () => {
    const m = modal({ title: "Remove this?", body: `<p>Stop tracking <b>${esc(b.dataset.label)}</b>? This doesn't change anything on the provider's side.</p>`,
      foot: `<button data-close>Keep</button><button class="primary" id="yes">Remove</button>` });
    $("#yes", m.el).onclick = () => guard(async () => { await api("/api/credits/sources/" + encodeURIComponent(b.dataset.delsrc), { method: "DELETE" }); m.close(); toast("Removed"); PAGES.plans(); });
  }));
};
function planCard(p) {
  const [name, sub] = PLANINFO[p.provider] || [p.label, ""];
  const badge = p.status === "ready" ? `<span class="badge ok"><span class="dot on"></span>Ready</span>` : p.status === "limit reached" ? '<span class="badge warn">At its limit</span>' : '<span class="badge">Not set up</span>';
  const line = p.status === "ready" ? `Runs on <b>${esc(p.where)}</b>` : p.status === "limit reached" ? `Resets <b>${esc(fmtWhen(p.resets))}</b>. Other plans take over until then.` : "Install it on one of your machines, then sign in once.";
  const btns = p.status === "not installed" ? `<button class="sm primary" data-setup="${esc(p.provider)}">Set up</button>`
    : `${p.can_sign_in ? `<button class="sm" data-signin="${esc(p.provider)}">Sign in</button>` : ""}${p.status === "limit reached" ? `<button class="sm ghost" data-clear="${esc(p.provider)}">It has reset</button>` : ""}<button class="sm ghost" data-setup="${esc(p.provider)}">Manage</button>`;
  return `<div class="card"><div class="row" style="flex-wrap:nowrap"><div class="avatar">${esc(name[0])}</div><div class="grow"><h3>${esc(name)}</h3><div class="small muted">${esc(sub)}</div></div>${badge}</div>
    <p class="small" style="margin:12px 0">${line}</p><div class="row">${btns}</div></div>`;
}
function sourceCard(s) {
  let amount = "";
  if (s.remaining != null) {
    const left = s.unit === "usd" ? money(s.remaining) : `${Math.max(0, s.remaining)} ${s.unit.replace("_", " ")}`;
    const total = s.amount != null ? (s.unit === "usd" ? money(s.amount) : s.amount) : s.monthly_budget != null ? money(s.monthly_budget) + "/month" : "";
    const pct = s.amount ? Math.max(0, Math.min(100, (s.remaining / s.amount) * 100)) : s.monthly_budget ? Math.max(0, Math.min(100, (s.remaining / s.monthly_budget) * 100)) : null;
    amount = `<div class="row" style="margin-top:10px"><b class="grow">${left} left</b>${total ? `<span class="small muted">of ${total}</span>` : ""}</div>
      ${pct != null ? `<div class="progress ${pct < 15 ? "crit" : pct < 35 ? "warn" : ""}" style="margin-top:6px"><i style="width:${pct}%"></i></div>` : ""}`;
  } else if (s.type === "payg") amount = `<div class="small" style="margin-top:10px">${money(s.used)} spent this month</div>`;
  else amount = `<div class="small muted" style="margin-top:10px">${s.used} used${s.amount == null ? " (limit unknown: tracking only)" : ""}</div>`;
  const when = s.expires ? `<span class="${s.urgency >= 0.8 ? "crit" : s.urgency >= 0.5 ? "warn" : "muted"}">Expires ${relTime(s.expires)}</span>`
    : s.resets ? `<span class="muted">Resets ${relTime(s.resets)}</span>` : "";
  return `<div class="card"><div class="row" style="flex-wrap:nowrap"><div class="grow"><h3 class="ellipsis">${esc(s.label)}</h3><div class="small muted">${esc(pname(s.provider))}</div></div>
    <span class="badge ${s.available ? "" : "crit"}">${esc(TYPENAME[s.type] || s.type)}</span>
    <button class="ghost icon sm" data-delsrc="${esc(s.id)}" data-label="${esc(s.label)}" aria-label="Remove">${I("trash")}</button></div>
    ${amount}<div class="small" style="margin-top:8px">${when}${s.note && !/quota unknown/.test(s.note) ? ` <span class="muted">· ${esc(s.note)}</span>` : ""}</div></div>`;
}
function addCreditWizard() {
  const providers = [...new Set(S.models.map((m) => m.provider))].filter((p) => !["mock", "flaky"].includes(p));
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const m = modal({ title: "Add credit or a limit", wide: true, body: "" });
  const step1 = () => {
    m.setBody(`<p class="muted">What do you want Switchboard to track?</p><div class="choice">
      ${[["prepaid", "Promo or prepaid credit", "Money already on an account, maybe with an expiry date."],
         ["free_tier", "Free daily or monthly limit", "A free allowance that resets, like Gemini's or GitHub Models' free tier."],
         ["subscription", "Subscription plan", "A plan like Claude Max, ChatGPT Plus or Copilot, used through its app."],
         ["payg", "Monthly budget", "A spending cap for a pay-as-you-go account."]].map(([v, t, d]) => `<label><input type="radio" name="ctype" value="${v}"><b>${t}</b><span>${d}</span></label>`).join("")}</div>`);
    $$("input[name=ctype]", m.el).forEach((r) => (r.onchange = () => step2(r.value)));
  };
  const step2 = (type) => {
    const plansOnly = type === "subscription";
    const list = providers.filter((p) => (plansOnly ? PLANINFO[p] : type === "free_tier" ? true : !PLANINFO[p]));
    m.setBody(`<div class="form">
      <label class="field">Service<select id="cProv">${list.map((p) => `<option value="${esc(p)}">${esc(pname(p))}</option>`).join("")}</select></label>
      <label class="field">Name <span class="help">Optional, e.g. "OpenAI promo"</span><input id="cLabel"></label>
      ${type === "payg" ? `<label class="field">Monthly budget ($)<input id="cBudget" type="number" min="0" step="any" placeholder="30"></label>`
        : `<label class="field">${type === "prepaid" ? "Amount left ($)" : "Limit per period"}<input id="cAmount" type="number" min="0" step="any" placeholder="${type === "prepaid" ? "18" : "250"}">
          ${type !== "prepaid" ? `<span class="help">Leave empty if you don't know it; Switchboard will still track usage and limits.</span>` : ""}</label>`}
      ${type === "free_tier" || type === "subscription" ? `<label class="field">Counted in<select id="cUnit"><option value="requests">Requests</option><option value="premium_requests">Premium requests</option><option value="tokens">Tokens</option></select></label>
        <label class="field">Resets<select id="cReset"><option value="day">Every day</option><option value="week">Every week</option><option value="month">Every month</option><option value="5h">Every 5 hours</option><option value="">Never</option></select></label>` : ""}
      ${type === "prepaid" ? `<label class="field">Expires <span class="help">Optional</span><input id="cExp" type="date"></label>` : ""}
      ${type === "subscription" ? `<label class="field">Renews on <span class="help">Optional</span><input id="cRenew" type="date"></label>` : ""}
    </div><p class="tiny muted" style="margin-top:12px">Time zone for resets: ${esc(tz)}.</p>`);
    m.setFoot(`<button id="cBack">Back</button><button class="primary" id="cSave">Save</button>`);
    $("#cBack", m.el).onclick = () => { m.setFoot(""); step1(); };
    $("#cSave", m.el).onclick = (e) => guard(async () => {
      const v = (id) => { const el = $(id, m.el); return el ? el.value.trim() : ""; };
      const provider = v("#cProv");
      const body = { id: `${provider}-${type}-${Date.now().toString(36).slice(-4)}`, provider, type, label: v("#cLabel") || `${pname(provider)} ${TYPENAME[type].toLowerCase()}` };
      if (v("#cAmount")) body.amount = Number(v("#cAmount"));
      if (v("#cBudget")) body.monthly_budget = Number(v("#cBudget"));
      if (v("#cUnit")) body.unit = v("#cUnit");
      if (v("#cExp")) body.expires = v("#cExp");
      if (v("#cRenew")) body.renews = v("#cRenew");
      if (v("#cReset")) body.reset = { every: v("#cReset"), tz };
      if (type === "payg" && !body.monthly_budget) throw new Error("Enter a monthly budget.");
      if (type === "prepaid" && body.amount == null) throw new Error("Enter the amount left.");
      if (provider === "deepseek" || provider === "openrouter") body.auto_balance = type === "prepaid" && body.amount == null;
      await api("/api/credits/sources", { body });
      m.close(); toast("Saved"); PAGES.plans();
    }, e.currentTarget);
  };
  step1();
}
function nodeHasTool(n, toolId, provider) { return (n.cli_providers || []).includes(provider) || ((n.tools || {})[toolId] || {}).installed; }
function planSetup(provider) {
  const p = (S.credits.plans || []).find((x) => x.provider === provider) || {};
  const [name, sub] = PLANINFO[provider] || [p.label, ""];
  const online = S.nodes.filter((n) => n.online);
  const m = modal({ title: `Set up ${name}`, wide: true,
    body: `<p class="muted">${esc(sub)}. It uses your subscription's limits, not API credit.</p>
      <ol class="steps"><li><b>Install it on a computer</b><p class="small muted">Any of your machines works. Pick the one you're signed in to the most.</p>
        ${online.map((n) => `<div class="list-row"><div class="avatar">${I(osIcon(n.platform))}</div><div class="grow"><b>${esc(n.node)}</b><div class="small muted">${esc(osName(n.platform))}</div></div>
          ${nodeHasTool(n, p.tool_id, provider) ? `<span class="badge ok">${I("check")} Installed</span>` : `<button class="sm primary" data-inst="${esc(n.node)}">Install</button>`}</div>`).join("")}</li>
      <li><b>Sign in with your plan account</b><p class="small muted">Opens the sign-in on that computer. Finish it in the browser window there.</p>
        <button class="sm" id="signNow" ${p.where ? "" : "disabled"}>Sign in on ${esc(p.where || "a computer")}</button><div id="signOut"></div></li></ol>` });
  $$("[data-inst]", m.el).forEach((b) => (b.onclick = () => installTool(b.dataset.inst, p.tool_id, b, () => { loadNodes(true).then(loadCredits).then(() => { m.close(); planSetup(provider); }); })));
  if ($("#signNow", m.el)) $("#signNow", m.el).onclick = (e) => planSignIn(provider, e.currentTarget, $("#signOut", m.el));
}
async function planSignIn(provider, btn, outEl) {
  await guard(async () => {
    const r = await api(`/api/plans/${provider}/login`, { body: {} });
    const html = `<div class="alert ${r.ok ? "info" : "warning"}" style="margin-top:10px">${I("info")}<div><div class="m">${esc(r.message || "")}</div>
      ${r.output ? `<pre class="out" style="margin-top:8px">${esc(r.output)}</pre>` : ""}</div></div>`;
    if (outEl) outEl.innerHTML = html;
    else modal({ title: "Sign in", body: html, foot: `<button class="primary" data-close>OK</button>` });
  }, btn);
}

// ================================================================ MACHINES & APPS
const osIcon = (p) => (p === "windows" ? "windows" : p === "macos" ? "laptop" : "chip");
const osName = (p) => ({ windows: "Windows", macos: "Mac", pi: "Raspberry Pi", linux: "Linux" }[p] || p || "");
PAGES.machines = async function renderMachines(tab) {
  tab = tab || "list";
  view.innerHTML = head("Machines & apps", "Your computers, their devices, and the apps AI can use on them.", `<button class="sm" id="rescan">${I("refresh")} Rescan</button>`) +
    `<div class="tabs">${[["list", "Machines"], ["apps", "Connected apps"], ["install", "Install apps"]].map(([k, l]) => `<button class="${k === tab ? "on" : ""}" data-tab="${k}">${l}</button>`).join("")}</div><div id="mBody" class="muted">Loading…</div>`;
  $$("[data-tab]").forEach((b) => (b.onclick = () => { location.hash = "#machines/" + b.dataset.tab; }));
  $("#rescan").onclick = (e) => guard(async () => { await loadNodes(true); toast("Rescanned"); PAGES.machines(tab); }, e.currentTarget);
  const me = rid;
  await loadNodes();
  if (stale(me)) return;
  if (tab === "apps") return renderApps();
  if (tab === "install") return renderInstall();
  $("#mBody").className = "";
  $("#mBody").innerHTML = `<div class="grid wide">${S.nodes.map(machineCard).join("")}
    <button class="card" id="connectNew" style="border-style:dashed;min-height:150px;flex-direction:column;color:var(--muted)">${I("plus")}<b style="color:var(--ink-2)">Connect a computer</b><span class="small">Windows, Mac or Raspberry Pi</span></button></div>`;
  $("#connectNew").onclick = connectWizard;
  $$("[data-dev]").forEach((b) => (b.onclick = () => deviceAction(b.dataset.node, b.dataset.dev, b.dataset.act, b)));
};
function machineCard(n) {
  const plans = (n.cli_providers || []).filter((p) => PLANINFO[p]).map((p) => PLANINFO[p][0]);
  const apps = Object.keys(n.mcp || {});
  const status = n.online ? '<span class="badge ok"><span class="dot on"></span>Online</span>' : `<span class="badge">Offline${n.last_seen ? " · " + ago(n.last_seen) : ""}</span>`;
  const devIcon = (d) => (d.kind === "server" ? "server" : d.kind === "network" ? "monitor" : "phone");
  const acts = (d) => d.kind === "android" ? [["screenshot", "Screenshot"], ["logs", "Logs"]] : d.kind === "ios-simulator" ? [["screenshot", "Screenshot"]] : d.kind === "server" ? [["check", "Check"]] : d.id.startsWith("net:") ? [["ping", "Check"]] : [];
  return `<div class="card"><div class="row" style="flex-wrap:nowrap"><div class="avatar">${I(osIcon(n.platform))}</div><div class="grow"><h3>${esc(n.node)}</h3>
      <div class="small muted">${esc(osName(n.platform))}${n.local ? " · Hub" : ""}</div></div>${status}</div>
    <dl class="kv" style="margin-top:12px"><dt>Output</dt><dd class="mono">${esc(n.output_dir || "—")}</dd>
      <dt>Plans</dt><dd>${plans.length ? plans.map(esc).join(", ") : '<span class="muted">none installed</span>'}</dd>
      <dt>Apps</dt><dd>${apps.length ? apps.map((a) => `${esc(appName(a))}${n.mcp[a].state === "error" ? ' <span class="crit">(error)</span>' : ""}`).join(", ") : '<span class="muted">none</span>'}</dd></dl>
    ${(n.devices || []).length ? `<div style="margin-top:12px">${n.devices.map((d) => `<div class="list-row">${I(devIcon(d))}<div class="grow"><b>${esc(d.name || d.id)}</b><div class="small muted">${esc(d.state || "")}</div></div>
      ${acts(d).map(([a, l]) => `<button class="sm" data-node="${esc(n.node)}" data-dev="${esc(d.id)}" data-act="${a}">${l}</button>`).join("")}</div>`).join("")}</div>` : ""}</div>`;
}
async function deviceAction(node, dev, act, btn) {
  await guard(async () => {
    const r = await api("/api/devices/action", { body: { node, device: dev, action: act } });
    const imgs = (r.images || []).map((im) => `<img src="data:image/png;base64,${im.b64}" alt="Screenshot" style="max-width:100%;border-radius:10px;border:1px solid var(--line)">`).join("");
    if (act === "check" || act === "ping") { toast((r.ok ? "" : "Failed: ") + (r.output || "").slice(0, 160), !r.ok); return; }
    modal({ title: act === "screenshot" ? "Screenshot" : "Logs", wide: true, body: imgs || `<pre class="out">${esc(r.output || "(empty)")}</pre>` });
  }, btn);
}
async function connectWizard() {
  const c = await api("/api/connect");
  const m = modal({ title: "Connect a computer", wide: true, body: "" });
  const os = { windows: "Windows", macos: "Mac", pi: "Raspberry Pi / Linux" };
  let code = null, timer = null, adv = false;
  const dl = (k) => (c.downloads || {})[k];
  const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  const draw = (k) => {
    const file = dl(k);
    m.setBody(`<div class="tabs">${Object.entries(os).map(([id, l]) => `<button data-os="${id}" class="${id === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <ol class="steps">
        <li>${file ? `Download and install <a href="${esc(file.url)}" download>${esc(file.name)}</a> on that computer.`
          : `Install the Switchboard app on that computer${c.releases_url ? ` (<a href="${esc(c.releases_url)}" target="_blank" rel="noopener">download it here</a>)` : ""}.`}
          ${k === "macos" ? '<div class="tiny muted">First open: right-click Switchboard in Applications, then Open (it isn\'t from the App Store).</div>'
            : k === "windows" ? '<div class="tiny muted">If Windows SmartScreen appears: More info, then Run anyway.</div>'
            : `<div class="tiny muted">Then run <span class="mono">switchboard setup</span> in its terminal.</div>`}</li>
        <li>Open Switchboard, choose <b>Connect to my hub</b>. It finds this hub on its own.</li>
        <li>Enter this pairing code:
          <div class="row" style="margin-top:8px">${code && code.code
            ? `<span class="mono" style="font-size:28px;font-weight:700;letter-spacing:8px">${esc(code.code)}</span><span class="small muted" id="pairLeft">valid ${fmt(code.expires_in)}</span>`
            : `<button class="primary" id="newCode">Show a pairing code</button>`}</div>
          <div class="tiny muted" style="margin-top:6px">Works once, for 10 minutes. If it doesn't find the hub, type the address ${c.hub_urls.map(esc).join(" or ")}.</div></li>
        <li>That's it. The computer appears here within a minute${k === "pi" ? "" : ", with a Switchboard icon in its " + (k === "windows" ? "system tray" : "menu bar")}.</li></ol>
      <details ${adv ? "open" : ""} id="advanced"><summary class="small muted">Install from the command line instead</summary>
        <p class="small" style="margin-top:8px">${esc(c.steps[k][0])}</p>${copyBox(c.steps[k][1])}
        <p class="tiny muted">The token in the command lets that computer join; keep it private.</p></details>`);
    $$("[data-os]", m.el).forEach((b) => (b.onclick = () => draw(b.dataset.os)));
    $("#advanced", m.el).ontoggle = (e) => (adv = e.target.open);
    const nb = $("#newCode", m.el);
    if (nb) nb.onclick = async () => {
      code = await api("/api/pair/code", { method: "POST", body: {} });
      clearInterval(timer);
      timer = setInterval(() => {
        if (!document.body.contains(m.el)) return clearInterval(timer);
        code.expires_in -= 1;
        if (code.expires_in <= 0) { code = null; clearInterval(timer); draw(k); return; }
        const left = $("#pairLeft", m.el); if (left) left.textContent = "valid " + fmt(code.expires_in);
      }, 1000);
      draw(k);
    };
  };
  const cur = await api("/api/pair/code");
  if (cur.code) code = cur;
  draw(navigator.platform && /mac/i.test(navigator.platform) ? "macos" : /linux|arm/i.test(navigator.platform) ? "pi" : "windows");
}
async function renderApps() {
  const me = rid;
  const ov = await api("/api/mcp/overview");
  if (stale(me) || !$("#mBody")) return;
  const online = ov.nodes.filter((n) => n.online);
  const running = ov.servers;
  $("#mBody").className = "";
  $("#mBody").innerHTML = `<div class="section-title"><h2>Connected</h2></div>
    ${running.length ? `<div class="card">${running.map((s) => `<div class="list-row"><div class="avatar">${I("puzzle")}</div><div class="grow"><b>${esc(appName(s.name))}</b>
        <div class="small muted">on ${esc(s.node)} · ${s.tools} tools${s.error ? ` · <span class="crit">${esc(s.error)}</span>` : ""}</div></div>
        <span class="badge ${s.state === "error" ? "crit" : s.state === "running" ? "ok" : ""}">${esc(s.state === "idle" ? "ready" : s.state)}</span>
        <button class="sm ghost" data-restart="${esc(s.node)}|${esc(s.name)}">Restart</button><button class="sm ghost danger" data-remove="${esc(s.node)}|${esc(s.name)}">Remove</button></div>`).join("")}</div>`
      : `<div class="empty"><b>No apps connected yet</b>Connect Unity, Photoshop, Blender, a browser for testing, your phones and more, so AI can use them.</div>`}
    <div class="section-title"><h2>Add an app</h2></div>
    <div class="grid">${ov.catalog.map((a) => `<div class="card"><div class="row" style="flex-wrap:nowrap"><div class="avatar">${esc(appName(a.id)[0])}</div><div class="grow"><h3>${esc(appName(a.id))}</h3>
        <div class="small muted">${a.platforms ? a.platforms.map(osName).join(", ") + " only" : "Any computer"}</div></div></div>
        <p class="small" style="margin:10px 0">${esc(a.description)}</p><button class="sm primary" data-add="${esc(a.id)}">${I("plus")} Add</button></div>`).join("")}
      <div class="card"><div class="row"><div class="avatar">${I("plus")}</div><div class="grow"><h3>Something else</h3><div class="small muted">Any MCP server</div></div></div>
        <p class="small" style="margin:10px 0">Paste the start command (or web address) from the app's instructions.</p><button class="sm" data-add="">${I("plus")} Add custom</button></div></div>`;
  $$("[data-add]").forEach((b) => (b.onclick = () => addAppModal(ov.catalog.find((a) => a.id === b.dataset.add), online)));
  $$("[data-restart]").forEach((b) => (b.onclick = () => guard(async () => { const [n, s] = b.dataset.restart.split("|"); const r = await api(`/api/mcp/servers/${n}/${s}/restart`, { method: "POST" }); toast(r.ok ? "Restarted" : "Problem: " + r.error, !r.ok); renderApps(); }, b)));
  $$("[data-remove]").forEach((b) => (b.onclick = () => {
    const [n, s] = b.dataset.remove.split("|");
    const m = modal({ title: "Remove app?", body: `<p>Disconnect <b>${esc(appName(s))}</b> from ${esc(n)}? You can add it again any time.</p>`, foot: `<button data-close>Cancel</button><button class="primary" id="yes">Remove</button>` });
    $("#yes", m.el).onclick = () => guard(async () => { await api(`/api/mcp/servers/${n}/${s}`, { method: "DELETE" }); m.close(); toast("Removed"); renderApps(); });
  }));
}
function addAppModal(app, online) {
  const custom = !app;
  const eligible = online.filter((n) => !app || !app.platforms || app.platforms.includes(n.platform));
  const hubFirst = app && ["github", "hostinger"].includes(app.id);
  const nodeOpts = (hubFirst ? [...eligible].sort((a, b) => (b.node === S.status.node) - (a.node === S.status.node)) : eligible).map((n) => `<option value="${esc(n.node)}">${esc(n.node)} (${esc(osName(n.platform))})</option>`).join("");
  const m = modal({ title: custom ? "Add a custom app" : "Add " + appName(app.id), wide: true,
    body: !eligible.length ? `<div class="empty"><b>No suitable computer is online</b>${app && app.platforms ? appName(app.id) + " needs " + app.platforms.map(osName).join(" or ") + "." : ""} <a href="#machines">Connect one</a>.</div>` : `<div class="stack">
      ${custom ? `<label class="field">Name<input id="aName" placeholder="my-app"></label>` : `<p class="muted">${esc(app.description)}</p>`}
      <label class="field">Computer<select id="aNode">${nodeOpts}</select></label>
      ${app && app.needs_setup ? `<div class="alert info">${I("info")}<div><div class="t">One-time setup inside the app</div><div class="m">${esc(app.setup || "")}${app.repo ? ` <a href="${esc(app.repo)}" target="_blank" rel="noopener">Instructions ${I("external")}</a>` : ""}</div></div></div>` : ""}
      ${app && app.setup && !app.needs_setup ? `<div class="alert info">${I("info")}<div class="m">${esc(app.setup)}</div></div>` : ""}
      ${custom || (app && app.needs_setup) ? `<label class="field">Start command <span class="help">${custom ? "e.g. npx -y some-mcp-server, or leave empty and use a web address" : "Shown by the app's MCP window after setup"}</span><input id="aCmd" class="mono" placeholder="uvx some-server"></label>` : `<p class="small muted">Runs: <span class="mono">${esc(app.command || "")}</span></p>`}
      ${custom ? `<label class="field">or web address (URL)<input id="aUrl" placeholder="https://…/mcp"></label>` : ""}
      ${app && app.env.length ? `<p class="small">Uses ${app.env.map((e) => `<span class="mono">${esc(e)}</span>`).join(", ")} from <a href="#settings" data-close>Settings → API keys</a> on that computer.</p>` : ""}
      ${app && app.requires.length ? `<p class="tiny muted">Needs on that computer: ${app.requires.map(esc).join(", ")}. Missing pieces can be installed from <a href="#machines/install" data-close>Install apps</a>.</p>` : ""}</div>`,
    foot: eligible.length ? `<button data-close>Cancel</button><button class="primary" id="aGo">Add</button>` : "" });
  if (!eligible.length) return;
  $("#aGo", m.el).onclick = (e) => guard(async () => {
    const v = (id) => ($(id, m.el) ? $(id, m.el).value.trim() : "");
    const body = { node: v("#aNode"), name: custom ? v("#aName") : app.id };
    if (v("#aCmd")) body.command = v("#aCmd");
    if (v("#aUrl")) body.url = v("#aUrl");
    if (!body.name) throw new Error("Give it a name.");
    if ((custom || (app && app.needs_setup)) && !body.command && !body.url) throw new Error("Paste the start command first.");
    e.currentTarget.textContent = "Starting…";
    const r = await api("/api/mcp/servers", { body });
    m.close();
    toast(r.ok ? `${appName(body.name)} connected (${r.tools} tools)` : `Added, but it didn't start: ${r.error || "unknown error"}`, !r.ok);
    renderApps();
  }, e.currentTarget);
}
async function renderInstall() {
  const me = rid;
  const rows = (await api("/api/advisor")) || [];
  if (stale(me) || !$("#mBody")) return;
  $("#mBody").className = "";
  $("#mBody").innerHTML = rows.length ? `<p class="muted" style="margin-top:0">Useful apps that aren't installed on every computer yet. Installs ask for confirmation first.</p>
    <div class="grid wide">${rows.map((t) => `<div class="card"><h3>${esc(t.name)}</h3>${t.helps.length ? `<div class="small muted">Helps with ${t.helps.map((h) => esc(h.replace("_", " "))).join(", ")}</div>` : ""}
      <div style="margin-top:10px">${t.install.map((i) => `<div class="list-row"><div class="grow"><b>${esc(i.node)}</b><div class="small muted">${esc(osName(i.platform))}</div></div>
        ${i.manual ? `<span class="small muted" style="max-width:60%">${esc(i.command)}</span>` : `<button class="sm" data-install="${esc(i.node)}|${esc(t.id)}">Install</button>`}</div>`).join("")}</div></div>`).join("")}</div>`
    : `<div class="empty"><b>All set</b>Everything in the catalog is installed on your online computers.</div>`;
  $$("[data-install]").forEach((b) => (b.onclick = () => { const [n, t] = b.dataset.install.split("|"); installTool(n, t, b, () => renderInstall()); }));
}
async function installTool(node, tool, btn, after) {
  await guard(async () => {
    const pre = await api("/api/tools/install", { body: { node, tool, approved: false } });
    if (!pre.needs_approval) { modal({ title: pre.manual ? "Install it yourself" : "Can't install", body: `<p>${esc(pre.output || pre.command || "")}</p>`, foot: `<button class="primary" data-close>OK</button>` }); return; }
    const m = modal({ title: `Install on ${node}?`, body: `<p>Switchboard will run this on <b>${esc(node)}</b>:</p>${copyBox(pre.command)}<p class="tiny muted" style="margin-top:8px">It may take a few minutes.</p>`,
      foot: `<button data-close>Cancel</button><button class="primary" id="go">Install</button>` });
    $("#go", m.el).onclick = (e) => guard(async () => {
      e.currentTarget.textContent = "Installing…";
      const r = await api("/api/tools/install", { body: { node, tool, approved: true } });
      m.close();
      if (r.ok) toast("Installed"); else modal({ title: "Install didn't finish", wide: true, body: `<pre class="out">${esc(r.output || "")}</pre>` });
      after && after();
    }, e.currentTarget);
  }, btn);
}

// ================================================================ SETTINGS
const MAIN_KEYS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY", "GITHUB_TOKEN"];
const keyRows = (list) => list.map((k) => `<div class="list-row"><div class="grow"><b>${esc(k.label)}</b><div class="small muted">${k.set ? '<span class="ok">Connected</span>' : "Not set"}${k.url ? ` · <a href="${esc(k.url)}" target="_blank" rel="noopener">Get a key ${I("external")}</a>` : ""}</div></div>
      <input type="password" autocomplete="off" placeholder="${k.set ? "Replace key" : "Paste key"}" data-key="${esc(k.name)}" style="width:min(240px,45vw)">
      <button class="sm" data-savekey="${esc(k.name)}">Save</button>${k.set ? `<button class="sm ghost danger" data-delkey="${esc(k.name)}" aria-label="Remove">${I("trash")}</button>` : ""}</div>`).join("");

PAGES.settings = async function renderSettings() {
  const me = rid;
  view.innerHTML = head("Settings") + '<div class="muted">Loading…</div>';
  const [st, keys] = await Promise.all([api("/api/settings"), api("/api/keys")]);
  if (stale(me)) return;
  S.settings = st;
  const r = st.router, perm = st.permissions, n = st.notify;
  const seg = (name, items, cur) => `<div class="seg" data-set="${name}">${items.map(([v, l]) => `<button data-v="${v}" class="${String(cur) === String(v) ? "on" : ""}">${l}</button>`).join("")}</div>`;
  view.innerHTML = head("Settings", "Everything is saved as you change it.") + `
  <div class="card"><div class="card-head">${I("key")}<div class="grow"><h2>AI accounts (API keys)</h2><p>Only needed for pay-per-use APIs. Plans like Claude Max or ChatGPT Plus are set up under <a href="#plans">Plans</a>, with no key.</p></div></div>
    ${keyRows(keys.filter((k) => k.set || MAIN_KEYS.includes(k.name)))}
    ${keys.some((k) => !k.set && !MAIN_KEYS.includes(k.name)) ? `<details class="more"><summary>${I("plus")} More services</summary>${keyRows(keys.filter((k) => !k.set && !MAIN_KEYS.includes(k.name)))}</details>` : ""}
    ${"" && keys.map((k) => `<div class="list-row"><div class="grow"><b>${esc(k.label)}</b><div class="small muted">${k.set ? '<span class="ok">Connected</span>' : "Not set"}${k.url ? ` · <a href="${esc(k.url)}" target="_blank" rel="noopener">Get a key ${I("external")}</a>` : ""}</div></div>
      <input type="password" autocomplete="off" placeholder="${k.set ? "Replace key" : "Paste key"}" data-key="${esc(k.name)}" style="width:min(240px,45vw)">
      <button class="sm" data-savekey="${esc(k.name)}">Save</button>${k.set ? `<button class="sm ghost danger" data-delkey="${esc(k.name)}" aria-label="Remove">${I("trash")}</button>` : ""}</div>`).join("")}
    <p class="tiny muted" style="margin-top:10px">Keys are stored only on your hub (in secrets.env) and are never shown again.</p></div>

  <div class="card"><div class="card-head">${I("spark")}<div class="grow"><h2>How Switchboard chooses</h2></div></div><div class="stack">
    <div><b>Default spending</b><div class="choice" style="margin-top:8px">${Object.entries(SPEND).map(([k, [t, d]]) => `<label><input type="radio" name="billing" value="${k}" ${r.billing === k ? "checked" : ""}><b>${t}</b><span>${d}</span></label>`).join("")}</div></div>
    <div><b>Quality or savings</b><div style="margin-top:6px">${seg("router.mode", [["balanced", "Balanced"], ["best", "Best quality"], ["cheapest", "Cheapest"]], r.mode)}</div></div>
    <label class="switch"><input type="checkbox" data-bool="router.auto_downgrade_effort" ${r.auto_downgrade_effort ? "checked" : ""}><span>Lower the effort automatically when you pick more than a task needs</span></label>
    <label class="switch"><input type="checkbox" data-bool="router.failover" ${r.failover !== false ? "checked" : ""}><span>If an AI is busy, out of credit or at its limit, try the next best one</span></label>
    <label class="field" style="max-width:320px">Ask me before tasks that cost more than ($)<input type="number" min="0" step="0.05" data-num="router.confirm_above_usd" value="${r.confirm_above_usd ?? 0.5}"></label></div></div>

  <div class="card"><div class="card-head">${I("shield")}<div class="grow"><h2>What AI may do by itself</h2><p>You can always do these things yourself from this dashboard.</p></div></div><div class="stack">
    <div><b>Phones and devices</b><div style="margin-top:6px">${seg("permissions.device_actions", [["none", "Nothing"], ["read_only", "Look only (screenshots, logs)"], ["all", "Everything (install, launch)"]], perm.device_actions)}</div></div>
    <div><b>Servers</b><div style="margin-top:6px">${seg("permissions.server_actions", [["none", "Nothing"], ["read_only", "Check only"], ["all", "Deploy and run commands"]], perm.server_actions)}</div></div>
    <label class="switch"><input type="checkbox" data-bool="permissions.cli_agents_can_edit" ${perm.cli_agents_can_edit ? "checked" : ""}><span>Let plan apps (Claude Code, Codex…) create and edit files in the task folder</span></label>
    <label class="switch"><input type="checkbox" data-bool="permissions.install_tools" ${perm.install_tools ? "checked" : ""}><span>Let AI install apps without asking</span></label></div></div>

  <div class="card"><div class="card-head">${I("bell")}<div class="grow"><h2>Alerts on your phone</h2><p>Expiring credit, unused free limits, plans hitting their limit.</p></div></div>
    <ol class="steps"><li>Install the free <b>ntfy</b> app (Android, iPhone) or open <a href="https://ntfy.sh/app" target="_blank" rel="noopener">ntfy.sh/app</a>.</li>
      <li>Choose a hard-to-guess topic name and subscribe to it in the app.<div class="row" style="margin-top:8px"><input id="ntfyTopic" value="${esc(n.ntfy_topic || "")}" placeholder="switchboard-…" style="min-width:240px">
        <button class="sm" id="ntfyGen">Suggest one</button><button class="sm primary" id="ntfySave">Save</button><button class="sm" id="ntfyTest" ${n.ntfy_topic ? "" : "disabled"}>Send test</button></div></li></ol></div>

  <div class="card"><div class="card-head">${I("chip")}<div class="grow"><h2>Models and prices</h2><p>AI companies add models and change prices often. Check now to update the list from your accounts.</p></div>
      <button class="sm" id="syncBtn">${I("refresh")} Check now</button></div><div id="syncOut"></div>
    <details class="more"><summary>${I("info")} All models (${S.models.length})</summary><div class="scroll-x"><table class="plain"><tr><th>Model</th><th>Service</th><th>$ in / out per 1M</th><th></th></tr>
      ${S.models.map((m) => `<tr><td>${esc(m.label)}</td><td>${esc(pname(m.provider))}</td><td>${m.features.includes("cli") ? "plan" : `${m.price[0]} / ${m.price[1]}`}</td><td>${m.available ? '<span class="badge ok">ready</span>' : '<span class="badge">not set up</span>'}</td></tr>`).join("")}</table></div></details></div>

  <div class="card"><div class="card-head">${I("download")}<div class="grow"><h2>Install as an app</h2></div>${deferredInstall ? '<button class="sm primary" id="installHere">Install on this device</button>' : ""}</div>
    <ul class="small" style="margin:0;padding-left:18px">
      <li><b>Windows or Mac with the agent:</b> open <span class="mono">http://localhost:8766</span>, then use the browser's install button (Chrome/Edge: the icon in the address bar). No token needed there.</li>
      <li><b>iPhone / iPad:</b> Share → Add to Home Screen. <b>Android:</b> browser menu → Add to Home screen / Install app.</li>
      <li><b>Away from home:</b> use Tailscale on the hub and your phone; with Tailscale's HTTPS address the app installs everywhere.</li></ul>
    <div class="row" style="margin-top:12px"><span class="small muted">Appearance</span>${seg("theme", [["", "Automatic"], ["light", "Light"], ["dark", "Dark"]], store.get("sb_theme") || "")}</div></div>

  <div class="card"><div class="card-head">${I("info")}<div class="grow"><h2>About</h2></div></div><dl class="kv" id="about">
    <dt>Hub</dt><dd>${esc(st.node.name)}</dd><dt>Version</dt><dd>${esc(S.status ? S.status.version || "" : "")}</dd><dt>Output folder</dt><dd class="mono">${esc(st.output_dir)}</dd></dl>
    <button class="sm" id="showConn" style="margin-top:10px">Show hub address and token</button><div id="conn"></div></div>`;

  const save = async (patch) => { try { await api("/api/settings", { method: "PUT", body: patch }); S.settings = await api("/api/settings"); toast("Saved"); } catch (e) { toast(e.message, true); } };
  const patchFor = (path, value) => { const [sec, key] = path.split("."); return { [sec]: { [key]: value } }; };
  $$('input[name="billing"]').forEach((x) => (x.onchange = () => save({ router: { billing: x.value } })));
  $$("[data-set]").forEach((s) => $$("button", s).forEach((b) => (b.onclick = () => {
    $$("button", s).forEach((x) => x.classList.toggle("on", x === b));
    if (s.dataset.set === "theme") { store.set("sb_theme", b.dataset.v || null); applyTheme(); return; }
    save(patchFor(s.dataset.set, b.dataset.v));
  })));
  $$("[data-bool]").forEach((c) => (c.onchange = () => save(patchFor(c.dataset.bool, c.checked))));
  $$("[data-num]").forEach((c) => (c.onchange = () => save(patchFor(c.dataset.num, Number(c.value)))));
  $$("[data-savekey]").forEach((b) => (b.onclick = () => guard(async () => {
    const inp = $(`input[data-key="${b.dataset.savekey}"]`);
    if (!inp.value.trim()) throw new Error("Paste the key first.");
    await api("/api/keys", { method: "PUT", body: { name: b.dataset.savekey, value: inp.value.trim() } });
    toast("Key saved"); await Promise.all([loadStatus(), loadModels()]); PAGES.settings();
  }, b)));
  $$("[data-delkey]").forEach((b) => (b.onclick = () => {
    const m = modal({ title: "Remove key?", body: "<p>Switchboard will stop using this account.</p>", foot: '<button data-close>Cancel</button><button class="primary" id="yes">Remove</button>' });
    $("#yes", m.el).onclick = () => guard(async () => { await api("/api/keys/" + b.dataset.delkey, { method: "DELETE" }); m.close(); toast("Removed"); await loadStatus(); PAGES.settings(); });
  }));
  $("#ntfyGen").onclick = () => { $("#ntfyTopic").value = "switchboard-" + Math.random().toString(36).slice(2, 8) + Math.random().toString(36).slice(2, 6); };
  $("#ntfySave").onclick = async () => { await save({ notify: { ntfy_topic: $("#ntfyTopic").value.trim() } }); $("#ntfyTest").disabled = !$("#ntfyTopic").value.trim(); };
  $("#ntfyTest").onclick = (e) => guard(async () => { const r = await api("/api/notify/test", { method: "POST" }); toast(r.ok ? "Test sent. Check your phone." : "Couldn't send. Check the topic and try again.", !r.ok); }, e.currentTarget);
  $("#syncBtn").onclick = (e) => guard(async () => {
    $("#syncOut").innerHTML = '<p class="muted small">Checking your accounts…</p>';
    const rep = await api("/api/models/sync", { body: { write: false } });
    const errs = Object.entries(rep.providers || {}).filter(([, v]) => v.error);
    const changes = rep.added.length + rep.missing.length + rep.repriced.length;
    $("#syncOut").innerHTML = `<div class="alert ${changes ? "info" : ""}">${I("info")}<div class="grow"><div class="t">${changes ? `${changes} change(s) found` : "Everything is up to date"}</div>
      <div class="m">${rep.added.length ? `New: ${rep.added.slice(0, 12).map(esc).join(", ")}${rep.added.length > 12 ? "…" : ""}<br>` : ""}${rep.missing.length ? `Not available to your keys (will be hidden): ${rep.missing.map(esc).join(", ")}<br>` : ""}${rep.repriced.length ? `Price changes: ${rep.repriced.length}<br>` : ""}${errs.length ? `<span class="warn">Couldn't check: ${errs.map(([k]) => esc(pname(k))).join(", ")}</span>` : ""}${Object.keys(rep.providers || {}).length ? "" : "Add an API key first: plans don't need this."}</div>
      ${changes ? '<button class="sm primary" id="syncSave" style="margin-top:8px">Apply these changes</button>' : ""}</div></div>`;
    if ($("#syncSave")) $("#syncSave").onclick = (ev) => guard(async () => { await api("/api/models/sync", { body: { write: true } }); await loadModels(); toast("Model list updated"); PAGES.settings(); }, ev.currentTarget);
  }, e.currentTarget);
  $("#showConn").onclick = (e) => guard(async () => {
    const c = await api("/api/connect");
    $("#conn").innerHTML = `<dl class="kv" style="margin-top:10px"><dt>Address</dt><dd>${c.hub_urls.map(esc).join("<br>")}</dd><dt>Token</dt><dd>${copyBox(c.token || "(not set)")}</dd></dl>`;
    e.currentTarget.remove();
  }, e.currentTarget);
  if ($("#installHere")) $("#installHere").onclick = () => doInstall();
};

// ---------------------------------------------------------------- app install, theme, start-up
let deferredInstall = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredInstall = e; $("#installBtn").classList.remove("hide"); });
async function doInstall() {
  if (!deferredInstall) return;
  deferredInstall.prompt();
  await deferredInstall.userChoice;
  deferredInstall = null;
  $("#installBtn").classList.add("hide");
}
$("#installBtn").onclick = doInstall;
function applyTheme() { const t = store.get("sb_theme"); if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});

(async function start() {
  applyTheme();
  buildNav();
  try {
    await loadStatus();
    await Promise.all([loadModels(), loadCredits(), loadSettings()]);
  } catch (e) {
    $("#hubDot").className = "dot crit";
    $("#hubName").textContent = "Hub not reachable";
    view.innerHTML = `<div class="alert critical">${I("alert")}<div><div class="t">Can't reach your Switchboard hub</div><div class="m">${esc(e.message)}. Check that the hub is on and connected, then reload.</div></div></div>`;
    return;
  }
  route();
  loadNodes().then(() => { if (!location.hash || location.hash === "#ask") renderAskTop(); }).catch(() => {});
  setInterval(() => { loadCredits().catch(() => {}); loadStatus().catch(() => { $("#hubDot").className = "dot crit"; }); }, 60000);
})();
