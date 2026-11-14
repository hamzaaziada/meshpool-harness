/* Meshpool desktop UI. Plain JS, no build step. Talks to the local server (server.py) with the per-launch token. */
"use strict";
const MP = window.MP || {token: "", desktop: false};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = n => Number(n || 0).toLocaleString("en-US");
const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
const MOD = isMac ? "⌘" : "Ctrl+";
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - (t || 0));
  if (s < 60) return "now"; if (s < 3600) return Math.floor(s / 60) + "m"; if (s < 86400) return Math.floor(s / 3600) + "h";
  if (s < 86400 * 7) return Math.floor(s / 86400) + "d"; return new Date(t * 1000).toLocaleDateString(undefined, {month: "short", day: "numeric"});
}
// Paths as people read them: ~/Documents/report instead of /Users/name/Documents/report.
// Paths inside the project become buttons that open the file in its app.
function linkFiles(html) {
  const root = String(curRoot() || "").replace(/[\\/]+$/, ""); if (!root) return html;
  const rx = new RegExp(esc(root).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[\\\\/]([^\\n<\"'`)]+?\\.(?:docx|xlsx|pptx|pdf|csv|md|html|txt|png|jpe?g|py|js|ts|json))", "g");
  return html.replace(rx, (m, rel) => `<button class="flink" data-open="${rel}" title="Open ${rel}">${ICON.files}${rel}</button>`);
}
// Windows: PowerShell, Command Prompt or Git Bash for commands you type; Mac and Linux: your login shell.
function termShellPicker() {
  const sh = S.settings?.shells; if (!sh || Object.keys(sh.shells || {}).length < 2) return `<span style="color:var(--ink-3);font-family:var(--mono)">$</span>`;
  const cur = store("term_shell") || sh.default;
  return `<select id="tshell" aria-label="Shell" onchange="store('term_shell', this.value)">${Object.entries(sh.shells).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
}
// "deepseek-v4.1-flash" reads as "DeepSeek V4.1 Flash"; unknown ids are tidied the same way.
// A model id carries its provider (providers.py): "chatgpt:gpt-6.1-sol", "gemini:...", or no prefix for the key endpoint.
const PROV_PREFIX = ["chatgpt", "anthropic", "gemini", "claude", "google"];
function provOf(id) { const [h, ...r] = String(id || "").split(":"); return r.length && PROV_PREFIX.includes(h) ? h : "key"; }
function bareModel(id) { const p = provOf(id); return p === "key" ? String(id || "") : String(id).slice(p.length + 1); }
function modelName(id) {
  const known = {deepseek: "DeepSeek", gpt: "GPT", claude: "Claude", gemini: "Gemini", qwen: "Qwen", glm: "GLM", kimi: "Kimi", llama: "Llama", mistral: "Mistral", grok: "Grok", openai: "OpenAI", minimax: "MiniMax"};
  const p = provOf(id);
  if ((p === "claude" || p === "google") && bareModel(id) === "default") return p === "claude" ? "Claude (your plan)" : "Gemini (your plan)";
  return bareModel(id).split("/").pop().split(/[-_ ]+/).map(w => known[w.toLowerCase()] || (/^v?\d/.test(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1))).join(" ").replace(/(\d) (?=\d)/g, "$1.");
}
function connMsg(d) {
  d = String(d || "");
  if (/reject|401|403/i.test(d)) return "The endpoint didn't accept this key. Check that you copied all of it.";
  if (/HTTP 0|timed out|connection|refused|resolve|getaddrinfo/i.test(d)) return "Can't reach that address. Check the base URL and your internet connection.";
  if (/404/.test(d)) return "That address answered, but it isn't a model endpoint. Check the base URL (it usually ends in /v1).";
  return "The connection test didn't work: " + d.slice(0, 160);
}
function get0(k) { return ((S.settings?.fields || []).find(x => x.key === k) || {}).value; }
function shortPath(p) {
  p = String(p || ""); const h = S.settings?.user_home;
  if (h && (p === h || p.startsWith(h + "/") || p.startsWith(h + "\\"))) p = "~" + p.slice(h.length);
  const parts = p.split(/[\\/]/);
  return p.length > 48 && parts.length > 3 ? "…/" + parts.slice(-2).join("/") : p;
}
const RES = id => document.getElementById(id) || {className: "", textContent: "", innerHTML: ""};   // results outlive a re-render
function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem("mp_" + k)); localStorage.setItem("mp_" + k, JSON.stringify(v)); } catch (e) { return null; } }

const I = (p, w = 16) => `<svg viewBox="0 0 24 24" width="${w}" height="${w}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
const ICON = {
  plus: I('<path d="M12 5v14M5 12h14"/>'), search: I('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  folder: I('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  chev: I('<path d="m6 9 6 6 6-6"/>', 14), chevR: I('<path d="m9 6 6 6-6 6"/>', 14),
  gear: I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  send: I('<path d="M12 19V5M5 12l7-7 7 7"/>'), stop: I('<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>'),
  clip: I('<path d="m21 11-8.6 8.6a5 5 0 0 1-7-7l8.5-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.3-2.3l7.9-8"/>'),
  diff: I('<path d="M12 3v14M5 10h14M5 21h14"/>'), files: I('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>'),
  term: I('<path d="m4 17 6-5-6-5M12 19h8"/>'), globe: I('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  branch: I('<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10M18 9a6 6 0 0 1-6 6H6"/>'),
  more: I('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
  side: I('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
  star: I('<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>', 14),
  check: I('<path d="m5 12 5 5 9-10"/>', 14), x: I('<path d="M6 6l12 12M18 6 6 18"/>'),
  cpu: I('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>', 14),
  bolt: I('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>', 14), shield: I('<path d="M12 3 5 6v6c0 4.4 3 7.6 7 9 4-1.4 7-4.6 7-9V6z"/>', 14),
  list: I('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>', 14), user: I('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>', 14),
  tool: I('<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>', 14),
  edit: I('<path d="M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>', 14), run: I('<path d="m4 17 6-5-6-5M12 19h8"/>', 14),
  read: I('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', 14),
  skill: I('<path d="M12 2 2 7l10 5 10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>', 14),
  refresh: I('<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>', 14), undo: I('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-1"/>', 14),
  chat: I('<path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.4A8 8 0 1 1 21 12z"/>'),
  check2: I('<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>', 14),
  ring: I('<circle cx="12" cy="12" r="9"/>', 14),
  pr: I('<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/><path d="M6 8v8M18 16V9a3 3 0 0 0-3-3h-4m0 0 2-2m-2 2 2 2"/>'),
  eye: I('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  plug: I('<path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0zM12 17v5"/>'),
  file: I('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>', 14), dir: I('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>', 14),
};

async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, {...opts, headers: {"X-Meshpool-Token": MP.token, ...(opts.body ? {"Content-Type": "application/json"} : {}), ...(opts.headers || {})}}); }
  catch (e) { throw new Error("Meshpool isn't responding. If this keeps happening, quit and reopen Meshpool."); }
  let j = null; try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) {
    if (j && j.error && r.status < 500) throw new Error(j.error);
    if (r.status === 403) throw new Error("This page lost its connection to Meshpool. Reload the window.");
    throw new Error("Something went wrong on Meshpool's side. Try again." + (j && j.error ? ` (${j.error})` : ""));
  }
  return j;
}
const post = (path, body) => api(path, {method: "POST", body: JSON.stringify(body || {})});
// Shorten at a word boundary with an ellipsis (the full text stays in the tooltip).
function clip(s, n) { if (s.length <= n) return s; const c = s.slice(0, n); const i = c.lastIndexOf(" "); return (i > n * 0.6 ? c.slice(0, i) : c).replace(/[,;:.]$/, "") + "…"; }

function toast(text, ms = 2600, action) {
  let box = $("#toasts"); if (!box) { box = document.createElement("div"); box.id = "toasts"; box.className = "toasts"; box.setAttribute("role", "status"); document.body.appendChild(box); }
  const t = document.createElement("div"); t.className = "toast"; t.textContent = text; box.appendChild(t);
  if (action) { const b = document.createElement("button"); b.className = "tact"; b.textContent = action.label; b.onclick = () => { t.remove(); action.fn(); }; t.appendChild(b); ms = Math.max(ms, 6000); }
  setTimeout(() => t.remove(), ms);
}


/* In-app confirm and prompt: native webviews do not always show window.confirm or window.prompt. */
function ask({title, text = "", value, ok = "OK", danger = false}) {
  return new Promise(res => {
    const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
    const input = value !== undefined;
    sc.innerHTML = `<div class="dlg" role="dialog" aria-modal="true" aria-label="${esc(title)}"><h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}${input ? `<input id="dlgIn" value="${esc(value)}" spellcheck="false">` : ""}<div class="row"><button class="btn" data-a="0">Cancel</button><button class="btn ${danger ? "danger" : "primary"}" data-a="1">${esc(ok)}</button></div></div>`;
    document.body.appendChild(sc);
    const done = v => { sc.remove(); document.removeEventListener("keydown", key, true); res(v); };
    const val = () => input ? ($("#dlgIn", sc).value.trim() || null) : true;
    const key = e => { if (e.key === "Escape") { e.stopPropagation(); done(input ? null : false); } if (e.key === "Enter") { e.preventDefault(); done(val()); } };
    document.addEventListener("keydown", key, true);
    sc.addEventListener("mousedown", e => { if (e.target === sc) done(input ? null : false); });
    $$("[data-a]", sc).forEach(b => b.onclick = () => done(b.dataset.a === "1" ? val() : (input ? null : false)));
    const f = input ? $("#dlgIn", sc) : $("[data-a='1']", sc); f.focus(); if (input) f.select();
  });
}
function openExternal(url) { post("/api/open-url", {url}).catch(() => window.open(url, "_blank")); }

/* ================================== state ================================== */
const S = {
  settings: null, models: null, projects: [], threads: [], skills: [], harnesses: [], defaults: {},
  cur: null,                         // the open thread: {id, total, view, epoch, ...}
  appMode: store("appmode") || "cowork",
  draft: {project: store("project") || "", harness: "", model: store("model") || "", mode: store("mode") || "ask", plan: false},
  panel: store("panel") || null, steps: store("steps") ?? true, attachments: [], showArchived: false, closedProjects: new Set(store("closed") || []),
};
const MODES = {ask: ["Supervised", "Asks before edits and commands"], edits: ["Auto-accept edits", "Edits go through; commands still ask"], auto: ["Full access", "Edits and commands run without asking"]};

function isDark() { const t = store("theme") || "system"; return t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches); }
function logoSrc() { return isDark() ? "/brand/logo-dark.png" : "/brand/logo-light.png"; }
try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyAppearance()); } catch (e) { /* old webviews */ }
function applyAppearance() {
  document.querySelectorAll(".mlogo").forEach(i => i.src = logoSrc());
  const t = store("theme") || "system", z = store("size") || "normal";
  if (t === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
  if (z === "normal") document.documentElement.removeAttribute("data-size"); else document.documentElement.dataset.size = z;
}
applyAppearance();

/* ================================== shell ================================== */
function shell() {
  $("#root").innerHTML = `
  <div class="app" id="app">
    <aside class="side" aria-label="Threads">
      <div class="side-top">
        <div class="brandrow">
          <img class="mlogo" src="${logoSrc()}" alt="Meshpool">
          <span class="beta" title="Meshpool is in beta. Some things may not work yet; tell us when they don't.">Beta</span>
          <span class="sp"></span>
          <button class="iconbtn" id="hideSide" title="Hide sidebar (${MOD}B)" aria-label="Hide sidebar">${ICON.side}</button>
        </div>
        <div class="modesw" role="tablist" aria-label="Mode">
          <button role="tab" data-mode="cowork" aria-selected="${S.appMode === "cowork"}">${ICON.chat}<span>Work</span></button>
          <button role="tab" data-mode="code" aria-selected="${S.appMode === "code"}">${ICON.term}<span>Code</span></button>
        </div>
        <button class="newbtn" id="newThread">${ICON.plus}<span>New thread</span><kbd>${MOD}N</kbd></button>
        <label class="search">${ICON.search}<input id="q" placeholder="Search threads" aria-label="Search threads"><span class="kbd">${MOD}K</span><button class="qx" id="qx" aria-label="Clear search" hidden>${ICON.x}</button></label>
      </div>
      <div class="side-list" id="sideList"></div>
      <button class="updbar" id="updBar" hidden></button>
      <div class="side-foot">
        <button id="openSettings"><span class="kdot" id="kdot"></span>Settings<span class="kbd" style="margin-left:auto">${MOD},</span></button>
      </div>
    </aside>
    <main class="main">
      <div class="topbar" id="topbar"></div>
      <div class="conv" id="conv"><div class="col" id="col"></div></div>
      <div class="composer-wrap">
        <div class="composer" id="composer">
          <div class="attach-row" id="attachRow"></div>
          <label class="sr" for="input">Message</label>
          <textarea id="input" rows="1" placeholder="Ask Meshpool anything. / for commands, $ for skills"></textarea>
          <div class="cbar" id="cbar"></div>
          <input type="file" id="fileIn" multiple hidden>
        </div>
        <div class="hintline" id="hintline"></div>
      </div>
    </main>
    <aside class="panel" id="panel" hidden><div class="rz" id="rz"></div><div class="ptabs" id="ptabs"></div><div class="pbody" id="pbody"></div></aside>
  </div>`;
  $("#newThread").onclick = newThread;
  $$(".modesw [data-mode]").forEach(b => b.onclick = () => setAppMode(b.dataset.mode));
  $("#openSettings").onclick = () => openSettings();
  $("#updBar").onclick = () => openSettings("about");
  $("#hideSide").onclick = toggleSide;
  const clearQ = () => { $("#q").value = ""; $("#qx").hidden = true; renderSide(); };
  $("#q").oninput = () => { $("#qx").hidden = !$("#q").value; clearTimeout(S.qT); S.qT = setTimeout(renderSide, 160); };
  $("#q").onkeydown = e => { if (e.key === "Escape" && $("#q").value) { e.stopPropagation(); e.preventDefault(); clearQ(); } };
  $("#qx").onclick = () => { clearQ(); $("#q").focus(); };
  setupComposer();
  setupPanelResize();
  document.addEventListener("keydown", globalKeys);
  if (store("sidehidden")) $("#app").classList.add("noside");
  $("#app").classList.toggle("steps", !!S.steps);
  $("#app").dataset.mode = S.appMode;
  let wasNarrow = narrow();
  addEventListener("resize", () => { if (narrow() !== wasNarrow) { wasNarrow = narrow(); $("#app").classList.remove("sideopen"); renderTop(); } });
  document.addEventListener("mousedown", e => { if ($("#app").classList.contains("sideopen") && !e.target.closest(".side") && !e.target.closest("#showSide")) $("#app").classList.remove("sideopen"); });
}
const narrow = () => innerWidth <= 760;
function modeHarness(m = S.appMode) {
  const want = store("harness_" + m) || S.defaults?.[m];
  const ok = id => S.harnesses.some(h => h.id === id && h.available !== false && h.ready !== false);
  return ok(want) ? want : (ok(S.defaults?.[m]) ? S.defaults[m] : (m === "code" ? "meshpool-code" : "meshpool-work"));
}
function modeBadges() {
  for (const b of $$(".modesw [data-mode]")) b.classList.toggle("busy", b.dataset.mode !== S.appMode && (S.threads || []).some(t => (t.kind || "code") === b.dataset.mode && ["working", "waiting"].includes(t.state)));
}
function setAppMode(m) {
  if (m === S.appMode && !S.cur) return;
  if (S.cur?.id) store("last_" + S.appMode, S.cur.id);
  S.appMode = m; store("appmode", m); setTimeout(modeBadges, 0);
  $$(".modesw [data-mode]").forEach(b => b.setAttribute("aria-selected", b.dataset.mode === m));
  $("#app").dataset.mode = m;
  S.draft.harness = modeHarness(m);
  if (S.panel && !PANELS[m].includes(S.panel)) { S.panel = null; store("panel", null); }
  const last = store("last_" + m);
  if (last && S.threads.some(t => t.id === last && !t.archived && (t.kind || "code") === m)) { renderSide(); renderBar(); openThread(last); return; }
  newThread();
}
const GIT_WORD = {"??": "new", "A": "added", "M": "changed", "D": "deleted", "R": "renamed", "AM": "added", "MM": "changed", "U": "conflict", "UU": "conflict"};
function toggleSteps() {
  S.steps = !S.steps; store("steps", S.steps);
  $("#app").classList.toggle("steps", S.steps);
  $$("#col .work").forEach(d => d.open = S.steps || !d.dataset.done || !!$(".appr:not(.done)", d));   // never fold away a pending approval
  $$("#col .think details").forEach(d => d.open = S.steps);
  renderTop(); toast(S.steps ? "Showing every step" : "Steps are folded; click one to open it");
}
function toggleSide() {
  const a = $("#app");
  if (a.classList.contains("autoside")) { S.keepSide = true; a.classList.remove("autoside"); renderTop(); return; }
  if (narrow()) { a.classList.toggle("sideopen"); return; }
  a.classList.toggle("noside"); store("sidehidden", a.classList.contains("noside")); renderTop();
}
function globalKeys(e) {
  const m = isMac ? e.metaKey : e.ctrlKey;
  if (e.key === "Escape") {
    // Esc closes the top-most thing that is open; with nothing open it stops the running task, wherever focus is.
    const open = $("#pop") || $("#ctx") || $(".scrim") || $("#onb");
    closePop(); closeCtx(); const sc = $(".scrim:not(.dlg-scrim)"); if (sc && !$("#onb") && !$(".dlg-scrim")) sc.remove();
    const typing = e.target?.closest?.("input, textarea, select, [contenteditable=true]") && e.target.id !== "input";
    if (!open && !typing && e.target?.id !== "input" && cfg().busy) { e.preventDefault(); stopNow(); }
  }
  if (!m) return;
  const k = e.key.toLowerCase();
  if (k === "n" && !e.shiftKey) { e.preventDefault(); newThread(); }
  else if (k === "k") { e.preventDefault(); if ($("#app").classList.contains("noside")) toggleSide(); $("#q").focus(); }
  else if (k === ",") { e.preventDefault(); openSettings(); }
  else if (k === "b") { e.preventDefault(); toggleSide(); }
  else if (k === "d" && e.shiftKey) { e.preventDefault(); togglePanel("diff"); }
  else if (k === "j") { e.preventDefault(); togglePanel("term"); }
  else if (k === "e") { e.preventDefault(); toggleSteps(); }
}

/* ================================== data loads ================================== */
async function loadSettings() { S.settings = await api("/api/settings"); $("#kdot").className = "kdot" + (S.settings.ready ? " ok" : ""); }
async function loadModels(refresh) {
  try { S.models = await api("/api/models" + (refresh ? "?refresh=1" : "")); } catch (e) { S.models = {models: [], favorites: [], default: "", error: e.message}; }
  if (!S.draft.model) S.draft.model = S.models.default;
}
async function loadProjects() { const r = await api("/api/projects"); S.projects = r.projects; S.defaultProject = r.default; }
async function loadThreads() {
  const was = new Map((S.threads || []).map(t => [t.id, t.state]));
  S.threads = (await api("/api/agent")).sessions;
  S.unseen = S.unseen || new Set(store("unseen") || []);
  for (const t of S.threads) if (["working", "waiting"].includes(was.get(t.id)) && !["working", "waiting"].includes(t.state) && S.cur?.id !== t.id) S.unseen.add(t.id);
  store("unseen", [...S.unseen]);
  renderSide();
  modeBadges();
  const t = S.cur?.view && S.threads.find(x => x.id === S.cur.id);
  if (t && t.title && t.title !== S.cur.view.title && !$("#ttl[contenteditable=true]")) { S.cur.view.title = t.title; renderTop(); }
}
async function loadHarnesses() { const r = await api("/api/harnesses"); S.harnesses = r.harnesses; S.defaults = r.defaults; S.hInfo = r; }
async function loadSkills() { try { S.skills = (await api("/api/skills")).skills; } catch (e) { S.skills = []; } }
const harness = id => S.harnesses.find(h => h.id === id) || S.harnesses[0] || {id: "meshpool-code", name: "Meshpool Code", models: "meshpool"};
const projName = p => (S.projects.find(x => x.path === p) || {}).name || (p || "").split(/[\\/]/).filter(Boolean).pop() || p;

/* ================================== sidebar ================================== */
function renderSide() {
  const q = ($("#q")?.value || "").trim().toLowerCase();
  const el = $("#sideList"); if (!el) return;
  if (q) { return renderSearch(q); }
  const inMode = t => (t.kind || "code") === S.appMode;
  const threads = S.threads.filter(t => !t.archived && inMode(t) && !S.pendingDelete?.has(t.id));
  const pinned = threads.filter(t => t.pinned);
  const byProj = new Map();
  for (const p of S.projects) byProj.set(p.path, []);
  for (const t of threads) { if (t.pinned) continue; if (!byProj.has(t.workdir)) byProj.set(t.workdir, []); byProj.get(t.workdir).push(t); }
  let html = "";
  if (pinned.length) html += `<div class="sec">Pinned</div><ul class="threads" style="padding-left:0">${pinned.map(t => thHTML(t, true)).join("")}</ul>`;
  html += `<div class="sec">Projects<span class="sp"></span><button class="iconbtn" id="addProj" title="Add a folder from this computer" aria-label="Add project folder">${ICON.plus}</button></div>`;
  if (!byProj.size) html += `<div class="empty-side">Meshpool works in your Meshpool Projects folder until you add your own with +.</div>`;
  // Folders with recent work first; empty folders last. Inside a folder, newest thread first.
  const last = l => l.reduce((m, t) => Math.max(m, Number(t.updated) || 0), 0);
  const keepEmpty = p => p === (S.cur?.view?.workdir || S.draft.project) || S.threads.every(t => t.workdir !== p);
  const order = [...byProj].filter(([p, l]) => l.length || keepEmpty(p)).sort((a, z) => last(z[1]) - last(a[1]));
  for (const [path, list] of order) {
    const closed = S.closedProjects.has(path);
    html += `<div class="proj ${closed ? "closed" : ""}" data-p="${esc(path)}">
      <div class="proj-h" title="${esc(path)}"><span class="chev">${ICON.chev}</span>${ICON.dir}<span class="nm">${esc(projName(path))}</span>
        <span class="acts"><button class="iconbtn" data-newin="${esc(path)}" title="New thread in this folder" aria-label="New thread in ${esc(projName(path))}">${ICON.plus}</button><button class="iconbtn" data-pmenu="${esc(path)}" aria-label="Folder options">${ICON.more}</button></span></div>
      ${list.length ? `<ul class="threads">${list.map(t => thHTML(t)).join("")}</ul>` : ""}</div>`;
  }
  const arch = S.threads.filter(t => t.archived && (t.kind || "code") === S.appMode);
  if (arch.length) {
    html += `<div class="sec"><button class="iconbtn" id="togArch" style="padding:0 4px;font-size:11.5px;font-weight:600;color:var(--ink-3)">${S.showArchived ? "Hide" : "Show"} archived (${arch.length})</button></div>`;
    if (S.showArchived) html += `<ul class="threads" style="padding-left:0">${arch.map(t => thHTML(t, true)).join("")}</ul>`;
  }
  // Rewriting identical HTML drops hover, eats a click that lands mid-rewrite, and flickers: skip it.
  if (el.dataset.mode === "list" && el._html === html) return;
  const shape = html.replace(/<span class="tm">[^<]*<\/span>/g, "").replace(/class="dot[^"]*"/g, "").replace(/class="th[^"]*"/g, "");
  if (el.dataset.mode === "list" && el._shape === shape) {
    el._html = html;
    for (const li of $$(".th", el)) {
      const t = S.threads.find(x => x.id === li.dataset.id); if (!t) continue;
      const st = t.state === "working" ? "run" : t.state === "waiting" ? "wait" : S.unseen?.has(t.id) && S.cur?.id !== t.id ? "done" : "";
      li.classList.toggle("on", S.cur?.id === t.id);
      const d = $(".dot", li); if (d) d.className = "dot " + st;
      const tm = $(".tm", li); if (tm && tm.textContent !== ago(t.updated)) tm.textContent = ago(t.updated);
    }
    return;
  }
  el._html = html; el._shape = shape; el.dataset.mode = "list";
  el.innerHTML = html;
  $("#addProj")?.addEventListener("click", pickProject);
  $("#togArch")?.addEventListener("click", () => { S.showArchived = !S.showArchived; renderSide(); });
  $$(".proj-h", el).forEach(h => h.addEventListener("click", e => {
    if (e.target.closest("button")) return;
    const p = h.parentElement.dataset.p;
    if (e.target.closest(".chev")) { S.closedProjects.has(p) ? S.closedProjects.delete(p) : S.closedProjects.add(p); store("closed", [...S.closedProjects]); renderSide(); return; }
    S.closedProjects.delete(p); store("closed", [...S.closedProjects]);
    S.draft.project = p; store("project", p); newThread();
  }));
  $$("[data-newin]", el).forEach(b => b.onclick = () => { S.draft.project = b.dataset.newin; store("project", S.draft.project); newThread(); });
  $$("[data-pmenu]", el).forEach(b => b.onclick = e => projMenu(e, b.dataset.pmenu));
  bindThreads(el);
}
function thHTML(t, withFolder) {
  const st = t.state === "working" ? "run" : t.state === "waiting" ? "wait" : S.unseen?.has(t.id) && S.cur?.id !== t.id ? "done" : "";
  return `<li class="th ${S.cur?.id === t.id ? "on" : ""}" data-id="${esc(t.id)}" title="${esc(t.title)}"><span class="dot ${st}"></span><span class="tt">${esc(t.title)}${withFolder ? `<small class="tf">${esc(projName(t.workdir))}</small>` : ""}</span><span class="tm">${ago(t.updated)}</span><button class="iconbtn more" data-tmenu="${esc(t.id)}" aria-label="Thread options">${ICON.more}</button></li>`;
}
function bindThreads(el) {
  $$(".th", el).forEach(li => li.addEventListener("click", e => { if (e.target.closest("[data-tmenu]")) return; openThread(li.dataset.id); }));
  $$("[data-tmenu]", el).forEach(b => b.onclick = e => { e.stopPropagation(); threadMenu(e, b.dataset.tmenu); });
}
async function renderSearch(q) {
  const el = $("#sideList");
  const local = S.threads.filter(t => t.title.toLowerCase().includes(q));
  let hits = [];
  try { hits = (await api("/api/search?q=" + encodeURIComponent(q))).hits; } catch (e) { /* ignore */ }
  if (($("#q").value || "").trim().toLowerCase() !== q) return;
  const ids = new Set(local.map(t => t.id));
  const more = hits.filter(h => !ids.has(h.id));
  el.dataset.mode = "search";
  el.innerHTML = `<div class="sec">Results</div><ul class="threads" style="padding-left:0">${local.map(t => thHTML(t, true)).join("")}${more.map(h => `<li class="th" data-id="${esc(h.id)}"><span class="dot"></span><span class="tt">${esc(h.title)}<span class="od" style="display:block;font-size:11px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(h.snippet)}</span></span></li>`).join("")}</ul>${!local.length && !more.length ? `<div class="empty-side">Nothing matches.</div>` : ""}`;
  bindThreads(el);
}
function ctxMenu(e, items) {
  closeCtx();
  const m = document.createElement("div"); m.className = "ctx"; m.id = "ctx";
  m.innerHTML = items.map((it, i) => `<button data-i="${i}" class="${it.danger ? "danger" : ""}">${esc(it.label)}</button>`).join("");
  document.body.appendChild(m);
  const r = (e.currentTarget || e.target).getBoundingClientRect();
  m.style.left = Math.min(innerWidth - m.offsetWidth - 8, r.left) + "px"; m.style.top = Math.min(innerHeight - m.offsetHeight - 8, r.bottom + 4) + "px";
  $$("button", m).forEach(b => b.onclick = () => { closeCtx(); items[+b.dataset.i].fn(); });
  setTimeout(() => document.addEventListener("mousedown", ctxAway, {once: true}), 0);
}
function ctxAway(e) { if (!e.target.closest("#ctx")) closeCtx(); }
function closeCtx() { $("#ctx")?.remove(); }
function threadMenu(e, id) {
  const t = S.threads.find(x => x.id === id) || {};
  ctxMenu(e, [
    {label: "Rename", fn: () => renameThread(id)},
    {label: S.steps ? "Hide step details" : "Show step details", fn: () => toggleSteps()},
    {label: t.pinned ? "Unpin" : "Pin", fn: () => threadAct(id, t.pinned ? "unpin" : "pin")},
    {label: t.archived ? "Unarchive" : "Archive", fn: () => threadAct(id, t.archived ? "unarchive" : "archive")},
    {label: "Delete", danger: true, fn: async () => { if (await ask({title: "Delete this thread?", text: "The conversation is removed. Files in your project stay as they are.", ok: "Delete thread", danger: true})) threadAct(id, "delete"); }},
  ]);
}
function projMenu(e, path) {
  ctxMenu(e, [
    {label: "New thread here", fn: () => { S.draft.project = path; newThread(); }},
    {label: isMac ? "Open in Finder" : "Open in File Explorer", fn: () => post("/api/open-path", {path}).catch(err => toast(err.message))},
    {label: "Open in code editor", fn: () => post("/api/open-editor", {path}).then(r => toast(r.message)).catch(err => toast(err.message))},
    {label: "Rename", fn: async () => { const n = await ask({title: "Rename folder", text: "The name shown in Meshpool. The folder on disk keeps its name.", value: projName(path), ok: "Rename"}); if (n) { await post("/api/projects", {action: "rename", path, name: n}); await loadProjects(); renderSide(); } }},
    {label: "Remove from Meshpool", danger: true, fn: async () => {
      const ts = S.threads.filter(t => t.workdir === path && !t.archived);
      if (!await ask({title: `Remove ${projName(path)} from Meshpool?`, text: `The folder and its files stay on your computer.${ts.length ? ` Its ${ts.length} thread${ts.length === 1 ? " is" : "s are"} archived; you can find ${ts.length === 1 ? "it" : "them"} under Show archived.` : ""}`, ok: "Remove folder", danger: true})) return;
      await post("/api/projects", {action: "remove", path});
      for (const t of ts) await post(`/api/threads/${t.id}`, {action: "archive"}).catch(() => {});
      if (S.cur?.view?.workdir === path) newThread();
      await loadProjects(); await loadThreads(); renderSide();
      toast(`${projName(path)} removed`, 6000, {label: "Undo", fn: async () => {
        await post("/api/projects", {action: "add", path}).catch(e => toast(e.message));
        for (const t of ts) await post(`/api/threads/${t.id}`, {action: "unarchive"}).catch(() => {});
        await loadProjects(); await loadThreads(); renderSide(); }});
    }},
  ]);
}
async function threadAct(id, action) {
  if (action === "delete") {                         // hidden at once, deleted after a few seconds unless undone
    S.pendingDelete = new Set([...(S.pendingDelete || []), id]);
    if (["working", "waiting"].includes(S.threads.find(t => t.id === id)?.state)) post(`/api/agent/${id}/stop`, {}).catch(() => {});
    if (S.cur?.id === id) newThread();
    renderSide();
    const t = setTimeout(async () => { S.pendingDelete.delete(id); try { await post(`/api/threads/${id}`, {action}); } catch (e) { toast(e.message); } await loadThreads(); }, 6000);
    toast("Thread deleted", 6000, {label: "Undo", fn: () => { clearTimeout(t); S.pendingDelete.delete(id); renderSide(); toast("Thread restored"); }});
    return;
  }
  try { await post(`/api/threads/${id}`, {action}); } catch (e) { return toast(e.message); }
  if (action === "delete" && S.cur?.id === id) newThread();
  await loadThreads(); renderTop();
  if (action === "archive") toast("Thread archived", 6000, {label: "Undo", fn: () => threadAct(id, "unarchive")});
  else if (action === "delete") toast("Thread deleted");
  else if (action === "unarchive") toast("Thread restored");
}
async function renameThread(id) {
  const t = S.threads.find(x => x.id === id); const n = await ask({title: "Rename thread", value: t?.title || "", ok: "Rename"});
  if (!n) return;
  await post(`/api/threads/${id}`, {action: "rename", title: n}); await loadThreads(); if (S.cur?.id === id && S.cur.view) { S.cur.view.title = n; renderTop(); }
}
// Choose a folder by clicking through folders (used where the computer has no folder dialog).
function browseFolder(start) {
  return new Promise(resolve => {
    const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
    sc.innerHTML = `<div class="dlg fb" role="dialog" aria-modal="true" aria-label="Choose a folder"><h3>Choose a folder</h3>
      <div class="fbcrumbs" id="fbCrumbs"></div>
      <input id="fbIn" spellcheck="false" aria-label="Folder path" placeholder="Or type a folder path and press Enter">
      <ul class="fblist" id="fbList" role="listbox" tabindex="0"></ul>
      <div class="row"><span class="fbsel" id="fbSel"></span><button class="btn" data-a="0">Cancel</button><button class="btn primary" data-a="1">Use this folder</button></div></div>`;
    document.body.appendChild(sc);
    let cur = "", home = "", act = -1;
    const done = v => { sc.remove(); document.removeEventListener("keydown", key, true); if (v) store("fb_last", v); resolve(v); };
    const items = () => $$("#fbList [data-p]", sc);
    const mark = n => { const it = items(); if (!it.length) return; act = Math.max(0, Math.min(it.length - 1, n)); it.forEach((b, k) => b.classList.toggle("act", k === act)); it[act].scrollIntoView({block: "nearest"}); };
    const key = e => {
      if (e.key === "Escape") { e.stopPropagation(); done(""); return; }
      if (e.target.id === "fbIn") { if (e.key === "Enter") { e.preventDefault(); go(e.target.value.trim()); } return; }
      if (e.key === "ArrowDown") { e.preventDefault(); mark(act + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); mark(act - 1); }
      else if (e.key === "Enter" && act >= 0) { e.preventDefault(); go(items()[act].dataset.p); }
      else if (e.key === "Backspace") { e.preventDefault(); const up = $("#fbList [data-up]", sc); if (up) go(up.dataset.p); }
    };
    document.addEventListener("keydown", key, true);
    const go = async path => {
      try {
        const r = await post("/api/projects", {action: "dirs", path});
        cur = r.path; home = r.home; act = -1;
        const sep = r.path.includes("\\") ? "\\" : "/";
        const parts = r.path.split(/[\\/]/).filter(Boolean);
        let acc = r.path.startsWith("/") ? "" : "";
        $("#fbCrumbs", sc).innerHTML = (r.path.startsWith("/") ? `<button data-c="/">/</button>` : "") + parts.map((p, k) => {
          acc = (k === 0 && !r.path.startsWith("/")) ? p + sep : acc + (acc.endsWith(sep) || acc === "" ? "" : sep) + p;
          const full = r.path.startsWith("/") ? "/" + parts.slice(0, k + 1).join("/") : acc;
          return `<button data-c="${esc(full)}" class="${k === parts.length - 1 ? "here" : ""}">${esc(full === home ? "Home" : p)}</button>`;
        }).join(`<span class="sep">›</span>`);
        $$("#fbCrumbs [data-c]", sc).forEach(b => b.onclick = () => go(b.dataset.c));
        $("#fbIn", sc).value = "";
        $("#fbList", sc).innerHTML = (r.parent ? `<li><button data-p="${esc(r.parent)}" data-up="1">${ICON.chev}<span>Up one level</span></button></li>` : "") +
          (r.dirs.length ? r.dirs.map(d => `<li><button data-p="${esc(r.path.replace(/[\\/]$/, "") + sep + d)}">${ICON.dir}<span>${esc(d)}</span></button></li>`).join("") : `<li class="pnote">No folders inside.</li>`);
        items().forEach((b, k) => { b.onclick = () => mark(k); b.ondblclick = () => go(b.dataset.p); });
        const name = r.path === r.home ? "your whole home folder" : (parts[parts.length - 1] || r.path);
        $("#fbSel", sc).textContent = `Selected: ${name}`;
        $("[data-a='1']", sc).textContent = r.path === r.home ? "Use the whole home folder" : `Use ${name}`;
        $("#fbList", sc).focus();
      } catch (e) { toast(e.message); }
    };
    $$("[data-a]", sc).forEach(b => b.onclick = () => done(b.dataset.a === "1" ? cur : ""));
    sc.addEventListener("mousedown", e => { if (e.target === sc) done(""); });
    go(start || store("fb_last") || "");
  });
}
async function pickProject() {
  try {
    let r = await post("/api/projects", {action: "pick"});
    if (r.no_picker) {                 // no folder dialog on this computer: Meshpool's own folder browser
      const path = await browseFolder();
      if (!path) return;
      r = await post("/api/projects", {action: "add", path});
    }
    S.projects = r.projects;
    if (r.picked) { S.draft.project = r.picked; store("project", r.picked); S.closedProjects.delete(r.picked); newThread(); }
    renderSide();
  } catch (e) { toast(e.message); }
}

/* ================================== topbar ================================== */
function renderTop() {
  const v = S.cur?.view; const hidden = $("#app").classList.contains("noside") || $("#app").classList.contains("autoside") || narrow();
  const root = curRoot();
  const ALL = {progress: [ICON.list, "Progress", ""], outputs: [ICON.files, "Files", ""], diff: [ICON.diff, "Diff", `${MOD}⇧D`],
    files: [ICON.files, "Files", ""], term: [ICON.term, "Terminal", `${MOD}J`], git: [ICON.pr, "Git and CI", ""], preview: [ICON.globe, "Preview", ""]};
  const tabs = PANELS[curMode()].map(k => [k, ...ALL[k]]);
  const u = v?.usage; const cx = v?.context;
  const usage = u && u.in ? `${fmt(u.in)} in · ${u.in ? Math.round(100 * u.cached / u.in) : 0}% cached` : cx && cx.size ? `Context ${Math.round(100 * cx.used / cx.size)}%` : "";
  $("#topbar").classList.toggle("newt", !v);
  $("#topbar").innerHTML = `
    ${hidden ? `<button class="tbtn" id="showSide" title="Show sidebar (${MOD}B)" aria-label="Show sidebar">${ICON.side}</button>` : ""}
    <span class="title" id="ttl" title="Double-click to rename">${esc(v ? (v.title || "New thread") : "New thread")}</span>
    ${root ? `<button class="chip" id="projChip" title="${esc(root)}">${ICON.dir}${esc(projName(root))}</button>` : ""}
    <span id="branchChip">${curMode() === "cowork" ? "" : (S.branchHTML || {})[root] || ""}</span>
    <span class="sp"></span>
    ${usage ? `<span class="chip use" title="Tokens in this thread">${esc(usage)}</span>` : ""}

    ${tabs.map(([k, ic, lb, kb]) => `<button class="tbtn" data-panel="${k}" aria-pressed="${S.panel === k && !$("#panel")?.hidden}" title="${lb}${kb ? " (" + kb + ")" : ""}">${ic}<span>${lb}</span></button>`).join("")}
    ${v ? `<button class="tbtn" id="tmore" aria-label="Thread options">${ICON.more}</button>` : ""}`;
  $("#showSide")?.addEventListener("click", toggleSide);
  $("#stepsBtn")?.addEventListener("click", toggleSteps);
  $$("[data-panel]").forEach(b => b.onclick = () => togglePanel(b.dataset.panel));
  $("#tmore")?.addEventListener("click", e => threadMenu(e, v.id));
  $("#projChip")?.addEventListener("click", e => { if (!v) projectPop(e.currentTarget); else post("/api/open-path", {path: root}).catch(err => toast(err.message)); });
  const ttl = $("#ttl");
  if (v) ttl.ondblclick = () => {
    ttl.contentEditable = "true"; ttl.focus(); document.getSelection().selectAllChildren(ttl);
    const done = async save => { ttl.contentEditable = "false"; if (save) { await post(`/api/threads/${v.id}`, {action: "rename", title: ttl.textContent.trim()}); v.title = ttl.textContent.trim(); loadThreads(); } else ttl.textContent = v.title; };
    ttl.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); done(true); } if (e.key === "Escape") done(false); };
    ttl.onblur = () => done(true);
  };
  renderBranch();
}
async function renderBranch() {
  const root = curRoot(); const el = $("#branchChip"); if (!el || !root) return;
  if (curMode() === "cowork") { el.innerHTML = ""; return; }
  try {
    const g = await api("/api/git?root=" + encodeURIComponent(root));
    S.git = g;
    const html = g.repo ? `<button class="chip" id="brChip" title="Branch">${ICON.branch}${esc(g.branch)}${g.dirty ? ` · ${g.dirty} changed` : ""}</button>` : "";
    S.branchHTML = {...(S.branchHTML || {}), [root]: html};
    if (curRoot() === root && el.innerHTML !== html) el.innerHTML = html;
    $("#brChip")?.addEventListener("click", () => togglePanel("git", true));
    if (g.repo && g.github) ciChip(root);
  } catch (e) { el.innerHTML = ""; }
}
const CI_TEXT = {passing: "Checks passing", failing: "Checks failing", running: "Checks running"};
async function ciChip(root, fresh) {
  const c = S.ciCache?.[root];
  let ci = c && !fresh && Date.now() - c.t < 60000 ? c.ci : null;
  if (!ci) { try { ci = await api("/api/git/ci?root=" + encodeURIComponent(root)); } catch (e) { return; } S.ciCache = {...(S.ciCache || {}), [root]: {t: Date.now(), ci}}; }
  const el = $("#branchChip"); if (!el || curRoot() !== root || !CI_TEXT[ci.summary]) return;
  $("#ciChip")?.remove();
  el.insertAdjacentHTML("beforeend", `<button class="chip ci ${ci.summary}" id="ciChip" title="${esc(ci.pr ? "#" + ci.pr.number + " " + ci.pr.title : ci.branch)}"><span class="cidot"></span>${CI_TEXT[ci.summary]}</button>`);
  $("#ciChip").onclick = () => togglePanel("git", true);
}
const PANELS = {cowork: ["progress", "outputs"], code: ["diff", "files", "term", "git", "preview"]};
function curMode() { return S.cur?.view?.kind || S.appMode; }
function curRoot() { return S.cur?.view?.workdir || S.draft.project || S.defaultProject || ""; }

/* ================================== composer ================================== */
function setupComposer() {
  const inp = $("#input");
  inp.addEventListener("input", () => { try { sessionStorage.setItem("mp_draft_" + (S.cur?.id || "new"), inp.value); } catch (e) {} });
  inp.addEventListener("input", () => { inp.style.height = "auto"; inp.style.height = Math.min(260, inp.scrollHeight) + "px"; menuFromInput(); const ib = $("#intrBtn"); if (ib && !S.stopping) ib.disabled = !inp.value.trim() && !S.attachments.length; });
  inp.addEventListener("keydown", e => {
    const pop = $("#pop.inline");
    if (pop && ["ArrowDown", "ArrowUp", "Enter", "Tab"].includes(e.key)) { e.preventDefault(); popKey(e.key); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendNow((isMac ? e.metaKey : e.ctrlKey) && cfg().busy); }
    if (e.key === "Escape" && !pop && cfg().busy && !$(".scrim")) { e.preventDefault(); stopNow(); }
  });
  inp.addEventListener("paste", e => {
    const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addAttachments(files); }
  });
  const c = $("#composer");
  c.addEventListener("dragover", e => { e.preventDefault(); c.classList.add("drag"); });
  c.addEventListener("dragleave", () => c.classList.remove("drag"));
  c.addEventListener("drop", e => { e.preventDefault(); c.classList.remove("drag"); addAttachments([...e.dataTransfer.files]); });
  $("#fileIn").onchange = () => { addAttachments([...$("#fileIn").files]); $("#fileIn").value = ""; };
  renderBar();
}
function cfg() { // the settings that apply to the next message: the open thread's, or the draft's
  const v = S.cur?.view;
  if (v) return {harness: v.harness?.id, model: v.model, mode: v.mode, plan: v.plan, busy: ["working", "waiting"].includes(v.state), starting: v.state === "starting"};
  return {harness: S.draft.harness, model: S.draft.model || S.models?.default, mode: S.draft.mode, plan: S.draft.plan, busy: false};
}
function renderBar() {
  const c = cfg(); const h = harness(c.harness);
  $("#app").classList.toggle("planning", !!c.plan);
  const fits = c.model && (!h.providers || h.providers.includes(provOf(c.model)));
  const modelLabel = fits ? modelName(c.model) + (provOf(c.model) === "chatgpt" ? " · ChatGPT" : "") : (h.runs_on ? "Default · " + h.runs_on : "Agent's own model");
  $("#cbar").innerHTML = `
    <button class="pill" id="hPill" title="Agent: who does the work in this thread">${ICON.cpu}<span class="lbl">${esc(h.name)}</span>${ICON.chev}</button>
    <button class="pill" id="mPill" title="Model">${ICON.bolt}<span class="lbl">${esc(modelLabel)}</span>${ICON.chev}</button>
    <button class="pill" id="aPill" title="${esc(MODES[c.mode]?.[1] || "")}">${ICON.shield}<span class="lbl">${esc(MODES[c.mode]?.[0] || "Supervised")}</span>${ICON.chev}</button>
    <button class="pill ${c.plan ? "on" : ""}" id="pPill" aria-pressed="${!!c.plan}" title="Plan mode: the agent looks around and writes a plan without changing anything">${ICON.list}<span class="lbl">Plan</span></button>
    <button class="pill" id="attBtn" title="Attach images or files">${ICON.clip}</button>
    <span class="sp"></span>
    ${c.busy ? `<button class="send stop ${S.stopping ? "stopping" : ""}" id="stopBtn" title="Stop (Esc)" aria-label="Stop">${S.stopping ? `<span class="spin"></span>` : ICON.stop}</button><button class="intr" id="intrBtn" title="Stop the current step and do this instead (${MOD}Enter)" ${S.stopping || !$("#input")?.value.trim() ? "disabled" : ""}>Send now</button>` : ""}
    <button class="send ${c.busy ? "add" : ""}" id="sendBtn" title="${c.busy ? "Add to this task (Enter). Stop and send now: " + MOD + "Enter" : "Send (Enter)"}" aria-label="${c.busy ? "Add to this task" : "Send"}" ${c.starting ? "disabled" : ""}>${ICON.send}</button>`;
  $("#hPill").onclick = e => harnessPop(e.currentTarget);
  $("#mPill").onclick = e => modelPop(e.currentTarget);
  $("#aPill").onclick = e => accessPop(e.currentTarget);
  $("#pPill").onclick = () => setPlan(!cfg().plan);
  $("#attBtn").onclick = () => $("#fileIn").click();
  $("#sendBtn")?.addEventListener("click", () => sendNow(false));
  $("#stopBtn")?.addEventListener("click", stopNow);
  $("#intrBtn")?.addEventListener("click", () => { if ($("#input").value.trim() || S.attachments.length) sendNow(true); else { $("#input").focus(); toast("Type what to do instead first."); } });
  const root = curRoot();
  $("#hintline").innerHTML = c.busy ? `Enter adds to the task · Esc stops`
    : "";
  $("#chgProj")?.addEventListener("click", e => projectPop(e.currentTarget));
  const inp = $("#input");
  inp.placeholder = c.busy ? "Add to this task…" : S.appMode === "cowork" ? "Ask for research, a document, a spreadsheet, a deck or an analysis" : "Ask Meshpool to build, fix or explain something";
}
async function setPlan(on) {
  if (S.cur) { try { S.cur.view = Object.assign(S.cur.view, await post(`/api/agent/${S.cur.id}/plan`, {on, since: 1e9})); } catch (e) { return toast(e.message); } }
  else S.draft.plan = on;
  renderBar();
}
async function setMode(m) {
  if (S.cur) { try { await post(`/api/agent/${S.cur.id}/mode`, {mode: m, since: 1e9}); S.cur.view.mode = m; } catch (e) { return toast(e.message); } }
  else { S.draft.mode = m; store("mode", m); }
  renderBar();
}
async function setModel(id) {
  if (S.cur) { try { await post(`/api/agent/${S.cur.id}/model`, {model: id, since: 1e9}); S.cur.view.model = id; } catch (e) { return toast(e.message); } }
  else { S.draft.model = id; store("model", id); }
  renderBar();
}
async function addAttachments(files) {
  for (const f of files) {
    if (f.size > 150 * 1024 * 1024) { toast(`${f.name} is larger than 150 MB. Put it in the project folder instead.`); continue; }
    const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); });
    S.attachments.push({name: f.name, mime: f.type || "application/octet-stream", data, image: /^image\/(png|jpe?g|gif|webp)$/.test(f.type)});
  }
  renderAttach();
}
function renderAttach() {
  $("#attachRow").innerHTML = S.attachments.map((a, i) => `<span class="att-chip">${a.image ? `<img src="data:${a.mime};base64,${a.data}" alt="">` : ICON.file}<span>${esc(a.name)}</span><button data-i="${i}" aria-label="Remove ${esc(a.name)}">×</button></span>`).join("");
  $$("#attachRow button").forEach(b => b.onclick = () => { S.attachments.splice(+b.dataset.i, 1); renderAttach(); });
}
async function stopNow() {
  if (!S.cur || S.stopping) return;
  S.stopping = true; renderBar();
  try { await post(`/api/agent/${S.cur.id}/stop`); } catch (e) { toast(e.message); }
  clearTimeout(S.cur.pollT); poll();
  const cur = S.cur, t0 = Date.now();
  const wait = () => { if (S.cur !== cur || !["working", "waiting"].includes(cur.view?.state) || Date.now() - t0 > 20000) { S.stopping = false; renderBar(); if (Date.now() - t0 > 20000 && ["working", "waiting"].includes(cur.view?.state)) toast("Stopping… finishing the current step."); return; } setTimeout(wait, 300); };
  wait();
}
async function sendNow(interrupt) {
  try { sessionStorage.removeItem("mp_draft_" + (S.cur?.id || "new")); } catch (e) {}
  const inp = $("#input"); let text = inp.value.trim();
  if (!text && !S.attachments.length) return;
  const busy = cfg().busy;
  if (text.startsWith("/") && await runSlash(text)) { inp.value = ""; inp.dispatchEvent(new Event("input")); return; }
  if (!S.settings?.ready) { showOnboarding(); return; }
  const atts = S.attachments; S.attachments = []; renderAttach();
  inp.value = ""; inp.style.height = "auto";
  try {
    if (!S.cur) {
      const root = S.draft.project || S.defaultProject;
      const v = await post("/api/agent", {workdir: root, harness: S.draft.harness || modeHarness(), kind: S.appMode, model: S.draft.model, mode: S.draft.mode, plan: S.draft.plan, task: text});
      S.draft.plan = false;
      openThread(v.id, true);
      if (S.appMode === "cowork" && !S.panel && !store("cw_panel_closed") && innerWidth >= 1400) togglePanel("progress", true);
    }
    const files = atts.filter(a => !a.image);
    if (files.length) {
      const saved = [];
      for (const f of files) { const r = await post("/api/attach", {root: curRoot(), name: f.name, data: f.data}); saved.push(r.path); }
      text += `\n\nAttached files (in the project folder): ${saved.join(", ")}`;
    }
    const images = atts.filter(a => a.image).map(a => ({name: a.name, mime: a.mime, data: a.data}));
    if (interrupt) { S.stopping = true; renderBar(); }
    const r = await post(`/api/agent/${S.cur.id}/send`, {text, images, since: 1e9, interrupt: !!interrupt});
    S.stopping = false;
    if (busy && !interrupt && r.sent?.queued) toast(`${harness(cfg().harness).name} gets this when the current step finishes.`);
    S.cur.view.state = "working"; renderBar();
    clearTimeout(S.cur.pollT); poll(); setTimeout(loadThreads, 500);
  } catch (e) { inp.value = text; toast(e.message, 4000); }
}

/* ---------- slash commands and $skills ---------- */
const SLASH = [
  ["/new", "Start a new thread", () => newThread()],
  ["/model", "Choose the model", () => modelPop($("#mPill"))],
  ["/agent", "Choose the agent", () => harnessPop($("#hPill"))],
  ["/plan", "Turn plan mode on or off", () => setPlan(!cfg().plan)],
  ["/diff", "Show what changed", () => togglePanel("diff", true)],
  ["/files", "Browse the project's files", () => togglePanel("files", true)],
  ["/terminal", "Open the terminal", () => togglePanel("term", true)],
  ["/git", "Branch, commit, push and pull request", () => togglePanel("git", true)],
  ["/skills", "Manage skills", () => openSettings("skills")],
  ["/connectors", "Connect GitHub, Jira, Notion, Apollo and more", () => openSettings("connectors")],
  ["/schedule", "Scheduled tasks", () => openSettings("scheduled")],
  ["/memory", "Instructions and memory", () => openSettings("personal")],
  ["/steps", "Show or fold every step", () => toggleSteps()],
  ["/settings", "Open settings", () => openSettings()],
];
async function runSlash(text) {
  const cmd = text.split(/\s/)[0].toLowerCase(); const hit = SLASH.find(s => s[0] === cmd);
  if (!hit) return false; hit[2](); return true;
}
function menuFromInput() {
  const inp = $("#input"); const v = inp.value; const pos = inp.selectionStart;
  const before = v.slice(0, pos);
  const slash = /^\/(\w*)$/.exec(before);
  const skill = /(^|\s)\$([\w-]*)$/.exec(before);
  if (slash) {
    const q = slash[1].toLowerCase();
    const items = SLASH.filter(s => s[0].slice(1).startsWith(q)).map(s => ({label: s[0], desc: s[1], fn: () => { inp.value = ""; s[2](); }}));
    return items.length ? inlinePop(items) : closePop();
  }
  if (skill) {
    const q = skill[2].toLowerCase();
    const items = S.skills.filter(s => s.enabled && s.name.toLowerCase().includes(q)).map(s => ({label: "$" + s.name, desc: s.description, fn: () => {
      const start = pos - skill[2].length - 1; inp.value = v.slice(0, start) + "$" + s.name + " " + v.slice(pos); inp.focus();
    }}));
    return items.length ? inlinePop(items) : closePop();
  }
  if ($("#pop.inline")) closePop();
}

/* ================================== popovers ================================== */
function closePop() { $("#pop")?.remove(); document.removeEventListener("mousedown", popAway); }
function popAway(e) { if (!e.target.closest("#pop") && !e.target.closest(".pill") && !e.target.closest(".chip")) closePop(); }
function openPop(anchor, html, {above = true, width} = {}) {
  closePop();
  const p = document.createElement("div"); p.className = "pop"; p.id = "pop"; p.innerHTML = html; if (width) p.style.width = width + "px";
  document.body.appendChild(p);
  p._place = () => {
    const r = anchor.getBoundingClientRect();
    p.style.left = Math.max(8, Math.min(innerWidth - p.offsetWidth - 8, r.left)) + "px";
    if (above && r.top > p.offsetHeight + 16) p.style.top = (r.top - p.offsetHeight - 6) + "px"; else p.style.top = Math.max(8, Math.min(innerHeight - p.offsetHeight - 8, r.bottom + 6)) + "px";
  };
  p._place();
  setTimeout(() => document.addEventListener("mousedown", popAway), 0);
  return p;
}
function inlinePop(items) {
  const anchor = $("#composer");
  const p = openPop(anchor, `<div class="pl">${items.map((it, i) => `<button class="opt ${i === 0 ? "act" : ""}" data-i="${i}"><span class="ol"><b>${esc(it.label)}</b><span class="od">${esc(it.desc || "")}</span></span></button>`).join("")}</div>`, {width: 420});
  p.classList.add("inline"); S.popItems = items; S.popAct = 0;
  $$(".opt", p).forEach(b => b.onmousedown = e => { e.preventDefault(); closePop(); items[+b.dataset.i].fn(); });
}
function popKey(k) {
  const p = $("#pop.inline"); const opts = $$(".opt", p);
  if (k === "ArrowDown") S.popAct = (S.popAct + 1) % opts.length; else if (k === "ArrowUp") S.popAct = (S.popAct - 1 + opts.length) % opts.length;
  else { const it = S.popItems[S.popAct]; closePop(); it.fn(); return; }
  opts.forEach((o, i) => o.classList.toggle("act", i === S.popAct)); opts[S.popAct].scrollIntoView({block: "nearest"});
}
function harnessPop(anchor) {
  const cur = cfg().harness;
  const other = S.appMode === "code" ? "cowork" : "code";
  const groups = [["any", "Automatic"], [S.appMode, S.appMode === "code" ? "For code" : "For work"], [other, "Other agents"]];
  const html = `<div class="pl">${groups.map(([m, lb]) => {
    const hs = S.harnesses.filter(h => !h.error && h.available !== false && h.mode === m); if (!hs.length) return "";
    return `<div class="grp">${lb}</div>` + hs.map(h => `<button class="opt" data-id="${esc(h.id)}" title="${esc(h.description || "")}" ${h.ready === false ? "style='opacity:.6'" : ""}><span class="ol">${esc(h.name)}${h.ready === false ? " · needs setup" : ""}<span class="od">${esc(h.ready === false ? (h.setup || "") : (h.tagline || h.description || ""))}</span></span>${h.id === cur ? `<span class="on">${ICON.check}</span>` : ""}</button>`).join("");
  }).join("")}</div><div class="pf"><button class="opt" id="hManage">${ICON.plus}<span class="ol">Add or manage agents</span></button></div>`;
  const p = openPop(anchor, html, {width: 380});
  $$(".opt[data-id]", p).forEach(b => b.onclick = async () => {
    closePop(); const id = b.dataset.id;
    if (harness(id).ready === false) { openSettings(harness(id).login ? "harnesses" : "general"); return; }
    if (S.cur) { if (id === S.cur.view.harness.id) return; toast("Each thread keeps its agent. Started a new thread with " + harness(id).name + "."); store("harness_" + S.appMode, id); newThread(); S.draft.harness = id; renderBar(); renderHero(); return; }
    S.draft.harness = id; store("harness_" + S.appMode, id); renderBar(); renderHero();
  });
  $("#hManage").onclick = () => { closePop(); openSettings("harnesses"); };
}
function agentFor(prov) {   // the agent to switch to for a model the current agent cannot run
  const only = {claude: "claude-code", google: "gemini-cli"}[prov]; if (only) return only;
  const mine = S.appMode === "code" ? "meshpool-code" : "meshpool-work";
  return harness(mine).providers?.includes(prov) ? mine : (S.harnesses.find(h => h.providers?.includes(prov) && h.available !== false) || {}).id;
}
async function connectProvider(g) {   // a provider that is not set up yet: sign in, or add its key in Settings
  closePop();
  if (["chatgpt", "claude", "google"].includes(g.id)) {
    try { const r = await post("/api/harness-login", {id: g.id}); toast(r.message); } catch (e) { return toast(e.message); }
    const t0 = Date.now(); const tick = async () => { await loadModels(true); const ok = (S.models.groups || []).find(x => x.id === g.id)?.ready; if (ok) { await loadHarnesses(); await loadSettings(); renderBar(); toast(g.name + " is connected."); } else if (Date.now() - t0 < 600000) setTimeout(tick, 4000); };
    setTimeout(tick, 4000); return;
  }
  openSettings("general");
}
async function pickModel(id) {
  const c = cfg(); const h = harness(c.harness); const prov = provOf(id);
  if (h.providers && !h.providers.includes(prov)) {
    const to = agentFor(prov); if (!to) return toast("No agent here can run these models.");
    const why = `${h.name} cannot run ${(S.models.groups || []).find(g => g.id === prov)?.name || "these"} models`;
    if (S.cur) { toast(`${why}. Started a new thread with ${harness(to).name}.`); store("harness_" + S.appMode, to); newThread(); }
    else toast(`${why}, so this thread uses ${harness(to).name}.`);
    S.draft.harness = to; S.draft.model = id; store("model", id); renderBar(); renderHero(); return;
  }
  if (S.cur && h.type === "acp" && S.cur.view?.harness?.provider && prov !== S.cur.view.harness.provider) {
    toast(`This thread runs ${h.name} on ${S.cur.view.harness.runs_on}. Started a new thread for this model.`);
    newThread(); S.draft.harness = h.id; S.draft.model = id; store("model", id); renderBar(); renderHero(); return;
  }
  setModel(id);
}
function modelPop(anchor) {
  const c = cfg(); const h = harness(c.harness);
  const M = S.models || {models: [], favorites: [], groups: []};
  const own = S.cur && h.models !== "meshpool" ? (S.cur.view?.harness_models || []) : [];   // models a running agent offers itself
  // First choose where the model runs (a tab per provider), then the model.
  const keyName = (M.groups || []).find(g => g.id === "key")?.name || "Meshpool";
  const TABS = [["key", keyName, ["key"]], ["chatgpt", "ChatGPT", ["chatgpt"]], ["claude", "Claude", ["anthropic", "claude"]], ["gemini", "Gemini", ["gemini", "google"]]];
  let tab = (TABS.find(t => t[2].includes(provOf(c.model))) || TABS[0])[0];
  const tabBtn = ([id, name, gs]) => { const on = (M.groups || []).some(g => gs.includes(g.id) && g.ready); return `<button type="button" role="tab" data-tab="${id}" aria-selected="${id === tab}" style="flex:1;padding:6px 4px;border-radius:var(--r-s);border:1px solid ${id === tab ? "var(--pool)" : "var(--line)"};background:${id === tab ? "var(--pool-soft)" : "transparent"};color:var(--ink);font:inherit;font-size:12.5px;cursor:pointer">${esc(name)}${on ? "" : ` <span style="color:var(--ink-3)">· set up</span>`}</button>`; };
  const p = openPop(anchor, `<div class="ph"><div role="tablist" aria-label="Where the model runs" id="mtabs" style="display:flex;gap:4px;margin-bottom:6px"></div><input id="mq" placeholder="Search models" aria-label="Search models"></div><div class="pl" id="ml"></div>
    <div class="pf" style="display:flex;gap:4px"><button class="opt" id="mRefresh" style="flex:1">${ICON.refresh}<span class="ol">Refresh</span></button><button class="opt" id="mManage" style="flex:1">${ICON.gear}<span class="ol">Providers and keys</span></button></div>`, {width: 440});
  const draw = () => {
    $("#mtabs").innerHTML = TABS.map(tabBtn).join("");
    $$("#mtabs [data-tab]").forEach(x => x.onclick = () => { tab = x.dataset.tab; draw(); $("#mq").focus(); });
    const shown = (TABS.find(t => t[0] === tab) || TABS[0])[2];
    const q = $("#mq").value.trim().toLowerCase();
    const hit = m => !q || m.id.toLowerCase().includes(q) || modelName(m.id).toLowerCase().includes(q) || (m.provider || "").toLowerCase().includes(q);
    const row = (m, note) => `<button class="opt" data-id="${esc(m.id)}" ${note ? "style='opacity:.7'" : ""}><span class="ol" title="${esc(m.id)}">${esc(modelName(m.id))}${m.id === M.default ? ` <span class="cap">default</span>` : ""}${(m.caps || []).map(x => ` <span class="cap">${esc(x)}</span>`).join("")}${note ? `<span class="od">${esc(note)}</span>` : ""}</span>${m.id === c.model ? `<span class="on">${ICON.check}</span>` : ""}${provOf(m.id) === "key" ? `<span class="star ${M.favorites.includes(m.id) ? "f" : ""}" data-star="${esc(m.id)}" title="Favorite">${ICON.star}</span>` : ""}</button>`;
    let html = "";
    if (own.length) html += `<div class="grp">${esc(h.name)} on ${esc(h.runs_on || "its account")}</div>` + own.filter(m => !q || (m.name || m.id).toLowerCase().includes(q)).map(m => `<button class="opt" data-own="${esc(m.id)}"><span class="ol">${esc(m.name)}</span>${m.id === c.model || bareModel(c.model) === m.id ? `<span class="on">${ICON.check}</span>` : ""}</button>`).join("");
    const favs = M.models.filter(m => M.favorites.includes(m.id) && hit(m) && shown.includes(provOf(m.id)));
    if (favs.length && !own.length) html += `<div class="grp">Favorites</div>` + favs.map(m => row(m)).join("");
    for (const g of (M.groups || []).filter(g => shown.includes(g.id))) {
      const can = !h.providers || h.providers.includes(g.id);
      const note = can ? "" : `Runs in ${harness(agentFor(g.id) || "").name || "another agent"}`;
      html += `<div class="grp">${esc(g.name)}</div>`;
      if (!g.ready) { if (!q) html += `<button class="opt" data-connect="${esc(g.id)}"><span class="ol">${{chatgpt: "Sign in with ChatGPT", claude: "Sign in with Claude", google: "Sign in with Google"}[g.id] || "Add a key"}<span class="od">${esc(g.only ? `Runs in ${harness(g.only).name}. ` : "")}${esc(["chatgpt", "claude", "google"].includes(g.id) ? "Opens the official sign-in. If you already use " + {chatgpt: "Codex", claude: "Claude Code", google: "Gemini CLI"}[g.id] + " on this computer, it is picked up by itself." : g.setup)}</span></span></button>`; continue; }
      const rows = g.models.filter(hit);
      if (g.error && !q) html += `<div class="pe">${esc(g.error)}</div>`;
      html += rows.map(m => row(m, note || (g.only ? `Runs in ${harness(g.only).name}, which picks the model` : ""))).join("");
    }
    if (!html) html = `<div class="pe">No models match.</div>`;
    $("#ml").innerHTML = html; p._place();
    $$("#ml .opt[data-id]").forEach(b => b.onclick = async e => {
      const st = e.target.closest("[data-star]");
      if (st) { e.stopPropagation(); const on = !M.favorites.includes(st.dataset.star); try { S.models = await post("/api/models/favorite", {id: st.dataset.star, on}); Object.assign(M, S.models); draw(); } catch (err) { toast(err.message); } return; }
      closePop(); pickModel(b.dataset.id);
    });
    $$("#ml .opt[data-own]").forEach(b => b.onclick = () => { closePop(); setModel(b.dataset.own); });
    $$("#ml .opt[data-connect]").forEach(b => b.onclick = () => connectProvider((M.groups || []).find(g => g.id === b.dataset.connect)));
  };
  $("#mq").oninput = draw; draw(); $("#mq").focus();
  $("#mq").onkeydown = e => { if (e.key === "Enter") { const first = $("#ml .opt"); if (first) first.click(); } };
  $("#mRefresh").onclick = async () => { await loadModels(true); Object.assign(M, S.models); draw(); };
  $("#mManage").onclick = () => { closePop(); openSettings("general"); };
}
function accessPop(anchor) {
  const cur = cfg().mode;
  const p = openPop(anchor, `<div class="pl">${Object.entries(MODES).map(([k, [lb, d]]) => `<button class="opt" data-m="${k}"><span class="ol">${lb}<span class="od">${d}</span></span>${k === cur ? `<span class="on">${ICON.check}</span>` : ""}</button>`).join("")}</div>`, {width: 300});
  $$(".opt", p).forEach(b => b.onclick = () => { closePop(); setMode(b.dataset.m); });
}
function projectPop(anchor) {
  const cur = curRoot();
  const list = S.projects.length ? S.projects : [];
  const p = openPop(anchor, `<div class="pl">${list.map(x => `<button class="opt" data-p="${esc(x.path)}"><span class="ol">${esc(x.name)}<span class="od">${esc(x.path)}</span></span>${x.path === cur ? `<span class="on">${ICON.check}</span>` : ""}</button>`).join("")}
    <button class="opt" data-p="${esc(S.defaultProject)}"><span class="ol">Meshpool Projects<span class="od">${esc(S.defaultProject)}</span></span></button></div>
    <div class="pf"><button class="opt" id="pickNew">${ICON.folder}<span class="ol">Choose a folder on this computer</span></button><button class="opt" id="pickHome">${ICON.dir}<span class="ol">Use my whole home folder<span class="od">Every folder and file you can open yourself</span></span></button></div>`, {width: 380});
  $$(".opt[data-p]", p).forEach(b => b.onclick = () => { closePop(); S.draft.project = b.dataset.p; store("project", S.draft.project); renderBar(); renderTop(); if (S.panel) renderPanel(); });
  $("#pickNew").onclick = () => { closePop(); pickProject(); };
  $("#pickHome").onclick = () => { closePop(); useHome(); };
}
async function useHome() {
  if (!await ask({title: "Give Meshpool your whole home folder?", text: "Agents can then read and, with your approval, change any of your files. You can still pick one folder per thread. On a Mac, also allow Full Disk Access in Settings, Permissions to reach Desktop, Documents and Downloads without a prompt each time.", ok: "Use my home folder"})) return;
  try {
    const r = await post("/api/projects", {action: "home"});
    S.projects = r.projects; S.draft.project = r.picked; store("project", r.picked); renderSide(); newThread();
  } catch (e) { toast(e.message); }
}

/* ================================== threads ================================== */
function restoreDraft() {
  let v = ""; try { v = sessionStorage.getItem("mp_draft_" + (S.cur?.id || "new")) || ""; } catch (e) {}
  const inp = $("#input"); if (inp && v && !inp.value) { inp.value = v; inp.dispatchEvent(new Event("input")); }
}
function newThread() {
  if (S.cur) clearTimeout(S.cur.pollT);
  if (S.cur?.view?.workdir) { S.draft.project = S.cur.view.workdir; store("project", S.draft.project); }
  S.cur = null; S.attachments = []; renderAttach();
  S.draft.harness = modeHarness();
  if (!S.draft.project) S.draft.project = S.projects[0]?.path || S.defaultProject;
  renderTop(); renderBar(); renderHero(); renderSide(); renderPanel();
  restoreDraft(); $("#input").focus();
}
function renderHero() {
  const h = harness(S.draft.harness); const cw = S.appMode === "cowork";
  const starters = cw ? [
    ["Summarize this folder into a Word document", "Read the files in this folder and write a one-page summary as a Word document."],
    ["Build a revenue model in Excel", "Build a 3-year revenue model for a SaaS company with 3 pricing tiers, as an Excel file with formulas."],
    ["Make a short PowerPoint deck", "Make a 6-slide PowerPoint deck on why engineering teams should track AI spend per team."],
    ["Read a scanned PDF", "Read the scanned PDF in this folder and turn it into a clean Word document with the same headings and tables."],
    ["Research a question", "Research this and give me a short answer with sources: "],
    ["Analyse a spreadsheet", "Analyse the spreadsheet in this folder: the main trends, anything unusual, and a short summary."],
  ] : [
    ["Explain this codebase", "Give me a tour of this codebase: what it does, how it is structured, and where to start."],
    ["Find and fix a bug", "Run the tests, find what fails, and fix it."],
    ["Review my changes", "$code-review Review my current changes."],
    ["Add a feature", "Plan, then build: "],
    ["Fix the failing CI", "Check why CI is failing on this branch, fix it, and push."],
    ["Open a pull request", "$pull-request"],
  ];
  $("#col").innerHTML = `<div class="hero">
    <h1>${cw ? "What should we get done?" : "What are we building?"}</h1>
    <p class="sub">${cw ? "Research, documents, spreadsheets, decks and data, done for you in" : "Reads, edits and runs your code in"} <button class="linkbtn" id="heroProj" title="Change folder">${esc(projName(curRoot()))}</button></p>
    <div class="starters">${starters.map(([a, b]) => `<button data-s="${esc(b)}">${esc(a)}</button>`).join("")}</div></div>`;
  $("#heroProj").onclick = e => projectPop(e.currentTarget);
  $$(".starters button").forEach(b => b.onclick = () => { const inp = $("#input"); inp.value = b.dataset.s; inp.focus(); inp.dispatchEvent(new Event("input")); });
}
function openThread(id, fresh) {
  if (S.unseen?.delete(id)) store("unseen", [...S.unseen]);
  store("last_" + S.appMode, id);
  $("#app").classList.remove("sideopen");
  if (S.cur) clearTimeout(S.cur.pollT);
  const known = S.threads.find(t => t.id === id);
  S.cur = {id, total: 0, view: fresh ? {id, state: "starting", harness: {id: S.draft.harness}, model: S.draft.model || S.models?.default, mode: S.draft.mode, events: []} : null, epoch: -1, groups: null, live: null, tools: {}, pollT: null, turn: 0};
  if (!fresh && known) S.cur.view = {id, title: known.title, state: known.state, kind: known.kind, workdir: known.workdir, harness: harness(known.harness), events: [], prefill: true};
  $("#col").innerHTML = "";
  closePop(); renderSide(); if (S.cur.view) { renderTop(); renderBar(); } $("#input").value = ""; restoreDraft(); poll();
}
async function poll() {
  const cur = S.cur; if (!cur) return;
  let v;
  try { v = await api(`/api/agent/${cur.id}?since=${cur.total}`); }
  catch (e) { if (S.cur !== cur) return; $("#col").insertAdjacentHTML("beforeend", `<div class="note bad">${esc(e.message)}</div>`); return; }
  if (S.cur !== cur) return;
  if (v.epoch !== cur.epoch && cur.epoch !== -1 && cur.total) { cur.total = 0; cur.epoch = v.epoch; $("#col").innerHTML = ""; cur.live = null; cur.group = null; cur.tools = {}; cur.log = []; cur.next = 0; return poll(); }
  const first = !cur.view || cur.view.prefill || cur.view.state === "starting" && !cur.total;
  cur.epoch = v.epoch; cur.total = v.total;
  const prevState = cur.view?.state;
  cur.view = Object.assign(cur.view || {}, v); delete cur.view.prefill;
  cur.log = cur.log || [];
  for (const ev of v.events) {
    if (typeof ev.i === "number") { if (ev.i < (cur.next || 0)) continue; cur.next = ev.i + 1; }   // two polls in flight
    if (ev.type === "tool_start") { const raw = ev.name || ""; const nm = ev.mcp ? raw.split("__").slice(2).join("__") : raw; ev.label = TOOL_LABEL[nm] || nm.replace(/_/g, " "); }
    cur.log.push(ev); applyEv(ev);
  }
  if (v.kind && v.kind !== S.appMode) { S.appMode = v.kind; store("appmode", v.kind); $("#app").dataset.mode = v.kind; $$(".modesw [data-mode]").forEach(b => b.setAttribute("aria-selected", b.dataset.mode === v.kind)); renderSide(); renderTop(); }
  if (S.panel === "progress" && (v.events.length || prevState !== v.state)) renderPanel();
  const busy = ["starting", "working", "waiting"].includes(v.state);
  let wk = $("#working");
  if (v.state === "working" && !cur.live && !$(".appr:not(.done)")) {
    if (!wk) { wk = document.createElement("div"); wk.id = "working"; wk.className = "working"; wk.innerHTML = `<span class="dot run"></span><span class="wt"></span><span class="el"></span>`; cur.since = v.turn_started ? v.turn_started * 1000 : Date.now(); }
    $(".wt", wk).textContent = String(cur.activity || "Working").split(" ").slice(0, 3).join(" ").replace(/[:,]$/, "");
    $(".el", wk).textContent = Math.round((Date.now() - (cur.since || Date.now())) / 1000) + "s";
    $("#col").appendChild(wk);
  } else { wk?.remove(); if (v.state !== "working") cur.since = null; }
  if (first || prevState !== v.state || (v.title && v.title !== cur.shownTitle)) { cur.shownTitle = v.title; renderTop(); renderBar(); }
  if (first && S.panel) renderPanel();
  scrollConv(first);
  if (!cur.ready) setTimeout(() => { if (S.cur === cur) cur.ready = true; }, 50);
  cur.pollT = setTimeout(poll, busy ? 400 : 3000);
  if (prevState && prevState !== v.state && !busy) { loadThreads(); renderBranch(); if (S.panel === "diff") renderPanel(); }
}
function scrollConv(force) { const c = $("#conv"); if (force || c.scrollHeight - c.scrollTop - c.clientHeight < 200) c.scrollTop = c.scrollHeight; }

/* ---------- markdown ---------- */
function inline(s) {
  return s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function md(src) {
  const parts = String(src || "").split(/```/);
  return parts.map((p, i) => {
    if (i % 2) return `<pre><code>${esc(p.replace(/^[\w+.#-]*\n/, ""))}</code></pre>`;
    const lines = esc(p).split("\n"); let out = "", list = null, para = [], table = [];
    const flushP = () => { if (para.length) { out += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
    const flushL = () => { if (list) { out += `<${list.t}>${list.items.map(x => `<li>${inline(x)}</li>`).join("")}</${list.t}>`; list = null; } };
    const flushT = () => { if (table.length) { const rows = table.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\s*\||\|\s*$/g, "").split("|").map(c => c.trim())); out += `<table>${rows.map((r, ri) => `<tr>${r.map(c => ri ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`).join("")}</tr>`).join("")}</table>`; table = []; } };
    for (const ln of lines) {
      let m;
      if (/^\s*\|.*\|\s*$/.test(ln)) { flushP(); flushL(); table.push(ln); continue; } else flushT();
      if ((m = /^(#{1,6})\s+(.*)$/.exec(ln))) { flushP(); flushL(); out += `<h3>${inline(m[2])}</h3>`; }
      else if ((m = /^\s*[-*]\s+(.*)$/.exec(ln))) { flushP(); if (!list || list.t !== "ul") { flushL(); list = {t: "ul", items: []}; } list.items.push(m[1]); }
      else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(ln))) { flushP(); if (!list || list.t !== "ol") { flushL(); list = {t: "ol", items: []}; } list.items.push(m[1]); }
      else if ((m = /^&gt;\s?(.*)$/.exec(ln))) { flushP(); flushL(); out += `<blockquote>${inline(m[1])}</blockquote>`; }
      else if (!ln.trim()) { flushP(); flushL(); }
      else { flushL(); para.push(ln); }
    }
    flushP(); flushL(); flushT(); return out;
  }).join("");
}
function diffLines(d) { return String(d || "").split("\n").map(l => { const c = l.startsWith("+++") || l.startsWith("---") ? "d-hunk" : l.startsWith("+") ? "d-add" : l.startsWith("-") ? "d-del" : l.startsWith("@@") ? "d-hunk" : ""; return c ? `<span class="${c}">${esc(l)}</span>` : esc(l); }).join("\n"); }

/* ---------- events ---------- */
const TOOL_ICON = n => /bash|run|exec|command|terminal/.test(n) ? ICON.run : /edit|write|patch/.test(n) ? ICON.edit : /read|grep|glob|search|fetch|snapshot|list/.test(n) ? ICON.read : /skill/.test(n) ? ICON.skill : ICON.tool;
const TOOL_RUN = {bash: "Running", bash_many: "Running", read_file: "Reading", write_file: "Writing", edit: "Editing", grep: "Searching", glob: "Listing", fetch_url: "Reading a page", web_search: "Searching the web", web_research: "Researching", mcp__meshpool_web__web_search: "Searching the web", mcp__meshpool_web__web_fetch: "Reading a page", mcp__meshpool_web__web_research: "Researching", todo_write: "Updating the plan", use_skill: "Loading a skill", office_task: "Working on the file", make_document: "Making a Word document", make_pdf: "Making a PDF", make_spreadsheet: "Building a spreadsheet", make_deck: "Making a deck", analyze_data: "Analysing data", convert_document: "Converting", read_document: "Reading a document"};
const TOOL_BASE = {bash: "Run", bash_many: "Run", write_file: "Write", edit: "Edit", make_document: "Make a Word document", make_pdf: "Make a PDF", make_spreadsheet: "Build a spreadsheet", make_deck: "Make a deck"};
const TOOL_DONE = {make_document: "Made a Word document", make_pdf: "Made a PDF", make_spreadsheet: "Built a spreadsheet", make_deck: "Made a deck", analyze_data: "Analysed data", convert_document: "Converted", read_document: "Read a document", fetch_url: "Read a page", mcp__meshpool_web__web_fetch: "Read a page"};
// What a step is about, in words: a file name, a query or a command; never raw JSON.
function stepSummary(name, ev) {
  const s = String(ev.summary || "");
  if (!s.trim().startsWith("{")) return s;
  try { const a = JSON.parse(s); return String(a.filename || a.path || a.query || a.question || a.url || a.title || a.request || a.expression || "").split("\n")[0].slice(0, 120); }
  catch (e) { return ""; }
}
const TOOL_LABEL = {bash: "Ran", read_file: "Read", write_file: "Wrote", edit: "Edited", grep: "Searched", glob: "Listed", fetch_url: "Fetched", web_search: "Searched the web", web_research: "Researched", mcp__meshpool_web__web_search: "Searched the web", mcp__meshpool_web__web_fetch: "Read", mcp__meshpool_web__web_research: "Researched", mcp__meshpool_assistant__memory_save: "Saved to memory", mcp__meshpool_assistant__run_subtasks: "Ran sub-agents", mcp__meshpool_assistant__schedule_task: "Scheduled", todo_write: "Updated the plan", use_skill: "Loaded skill", office_task: "Office"};
function workGroup() {
  const cur = S.cur;
  if (cur.group && cur.group.isConnected && cur.group === $("#col").lastElementChild) return cur.group;
  const d = document.createElement("details"); d.className = "work"; d.open = true; d.dataset.n = "0";
  d.innerHTML = `<summary><span class="chev">${ICON.chevR}</span><span class="wl">Working</span></summary><div class="rows"></div>`;
  $("#col").appendChild(d); cur.group = d; return d;
}
function bumpGroup(g, done) {
  const n = $$(".tool", g).length; const fails = $$(".tool .st.bad", g).length;
  $(".wl", g).textContent = `${n} step${n === 1 ? "" : "s"}${fails ? `, ${fails} failed` : ""}`;
  if (done) { g.dataset.done = "1"; g.open = !!S.steps || !!$(".appr:not(.done)", g); }
}
function closeGroup() { const g = S.cur.group; if (g && g.isConnected) bumpGroup(g, true); S.cur.group = null; }
function applyEv(ev) {
  const cur = S.cur, col = $("#col");
  const add = (html, cls) => { const d = document.createElement("div"); if (cls) d.className = cls; d.innerHTML = html; col.appendChild(d); return d; };
  if (questionEv(ev, add)) return;
  switch (ev.type) {
    case "queued": {
      const box = $("#attachRow"); $("#queuedChip")?.remove();
      if (ev.n) box.insertAdjacentHTML("afterbegin", `<span class="att-chip" id="queuedChip">${ICON.list}<span>Queued: ${esc(ev.text.slice(0, 60))}${ev.n > 1 ? ` and ${ev.n - 1} more` : ""}</span></span>`);
      break; }
    case "user": {
      if (ev.steer) { closeGroup(); add(`<div class="b"><span class="tag">Added while working</span><br>${esc(ev.text)}</div>`, "msg-user"); $("#queuedChip")?.remove(); break; }
      closeGroup(); cur.thought = null; cur.plan = null; cur.turn += 1; cur.activity = "Thinking";
      add(`<div class="b">${ev.plan ? `<span class="tag">Plan mode</span><br>` : ""}${esc(ev.text)}${ev.images && ev.images.length ? `<span class="att">${ICON.clip} ${ev.images.map(esc).join(", ")}</span>` : ""}</div>`, "msg-user");
      break; }
    case "text_delta":
      if (!cur.live) { closeGroup(); cur.live = add("", "msg-ai"); cur.live.style.whiteSpace = "pre-wrap"; cur.liveText = ""; }
      cur.liveText += ev.text; cur.live.textContent = cur.liveText; cur.activity = "Writing"; break;
    case "stream_reset": if (cur.live) { cur.live.remove(); cur.live = null; } break;
    case "assistant":
      if (cur.live) { if (ev.text) { cur.live.style.whiteSpace = ""; cur.live.innerHTML = linkFiles(md(ev.text)); } else cur.live.remove(); cur.live = null; }
      else if (ev.text) { closeGroup(); add(linkFiles(md(ev.text)), "msg-ai"); }
      cur.lastText = ev.text || cur.lastText;
      break;
    case "tool_start": {
      const g = workGroup();
      const raw = ev.name || "";
      const name = ev.mcp ? raw.split("__").slice(2).join("__") : raw;
      const label = TOOL_RUN[name] || TOOL_LABEL[name] || (ev.mcp ? "Using " : "") + name.replace(/_/g, " ");
      const d = document.createElement("details"); d.className = "tool"; d.id = "tool-" + ev.id; d.dataset.name = name; if (ev.mcp) d.dataset.mcp = "1";
      d.innerHTML = `<summary><span class="ic">${TOOL_ICON(name)}</span><span class="tn">${esc(label)}</span><span class="ts">${esc(stepSummary(name, ev))}</span><span class="st run"></span></summary>${ev.diff ? `<pre>${diffLines(ev.diff)}</pre>` : ""}`;
      $(".rows", g).appendChild(d); cur.tools[ev.id] = d; bumpGroup(g);
      { const sm = stepSummary(name, ev); cur.activity = `${label}${sm ? " " + sm : ""}`; } break; }
    case "tool_end": {
      const d = cur.tools[ev.id] || document.getElementById("tool-" + ev.id); if (!d) break;
      // A step the user stopped, or one Plan mode blocked, did not fail: label it for what happened.
      const out = String(ev.output || ""), stopped = !ev.ok && /not run: stopped by the user|^\[exit 130\]|stopped by the user/i.test(out);
      const planned = !ev.ok && (/plan mode/i.test(out) || (cur.view?.plan && ["write", "edit", "write_file", "bash", "bash_many", "delete", "move"].includes(d.dataset.name)));
      const st = $(".st", d); st.className = "st " + (ev.ok || stopped || planned || ev.denied ? "" : "bad");
      st.textContent = ev.denied ? "not run" : ev.ok ? "" : stopped ? "stopped" : planned ? "not allowed in Plan mode" : "failed";
      const nm = d.dataset.name || "";
      $(".tn", d).textContent = ev.ok ? (TOOL_DONE[nm] || TOOL_LABEL[nm] || (d.dataset.mcp ? "Used " : "") + nm.replace(/_/g, " ")) : (TOOL_BASE[nm] || TOOL_LABEL[nm] || nm.replace(/_/g, " "));
      if (ev.output && !$("pre", d)) d.insertAdjacentHTML("beforeend", `<pre>${esc(ev.output)}</pre>`);
      const g = d.closest(".work"); if (g) bumpGroup(g);
      break; }
    case "approval": {
      const gKeep = cur.group && cur.group.isConnected && cur.group === $("#col").lastElementChild ? cur.group : null;
      for (const t of Object.values(cur.tools || {})) { const st = $(".st.run", t); if (st) { st.textContent = "waiting for your OK"; const nm = t.dataset.name || ""; $(".tn", t).textContent = TOOL_BASE[nm] || TOOL_LABEL[nm] || nm; } }
      closeGroup();
      const OPT = {allow_once: "Allow", allow_always: "Allow for this thread", reject_once: "Deny", reject_always: "Always deny"};
      const opts = ev.options && ev.options.length ? ev.options.map(o => `<button class="btn ${o.kind === "allow_once" ? "primary" : String(o.kind).startsWith("reject") ? "danger" : ""}" data-d="${esc(o.id)}">${esc(OPT[o.kind] || o.label)}</button>`).join("")
        : `<button class="btn primary" data-d="yes">Allow</button><button class="btn" data-d="always">Allow ${ev.kind === "edit" ? "all edits" : ev.kind === "bash" ? "all commands" : "this tool"} in this thread</button><button class="btn danger" data-d="no">Deny</button><input placeholder="Note for the agent (optional)" aria-label="Note for the agent">`;
      const title = String(ev.title || "").replace(/^(run a command|execute terminal|terminal)\s*:.*$/is, "Run this command?").replace(/^Approve edit:\s*(.+)$/i, "Change $1?");
      const g0 = gKeep;
      const d = add(`<b>${esc(title)}</b>${ev.detail ? (ev.kind === "doc" ? `<div class="docprev">${md(ev.detail)}</div>` : `<pre>${ev.kind === "edit" ? diffLines(ev.detail) : esc(ev.detail)}</pre>`) : ""}<div class="acts">${opts}</div>`, "appr");
      if (g0) { $(".rows", g0).appendChild(d); cur.group = g0; bumpGroup(g0); g0.open = true; }   // the Allow buttons must stay visible
      d.id = "appr-" + ev.id;
      $$("button", d).forEach(b => b.onclick = async () => {
        d.dataset.note = $("input", d)?.value || "";
        $$("button", d).forEach(x => x.disabled = true);
        try { await post(`/api/agent/${cur.id}/answer`, {id: ev.id, decision: b.dataset.d, note: $("input", d)?.value || ""}); clearTimeout(cur.pollT); poll(); }
        catch (e) { $$("button", d).forEach(x => x.disabled = false); toast(e.message); }
      });
      scrollConv(true); break; }
    case "approval_done": { for (const t of Object.values(cur.tools || {})) { const st = $(".st.run", t); if (st && /waiting/.test(st.textContent)) { st.textContent = ""; const nm = t.dataset.name || ""; $(".tn", t).textContent = TOOL_RUN[nm] || TOOL_LABEL[nm] || nm; } }
      const d = document.getElementById("appr-" + ev.id); if (d) { d.classList.add("done"); const a = $(".acts", d); $("pre", d)?.remove(); $(".docprev", d)?.remove(); { const bt = $("b", d); if (bt) bt.textContent = bt.textContent.replace(/\?$/, ""); } if (a) a.outerHTML = `<div class="note">— ${ev.decision === "stopped" ? "Stopped before running." : ev.decision === "no" || /deny|reject/.test(ev.decision) ? (d.dataset.note ? `Denied. Your note: ${esc(d.dataset.note)}` : "Denied.") : ev.decision === "always" || /always|session/.test(ev.decision) ? "Allowed for this thread." : "Allowed."}</div>`;
        const g = d.previousElementSibling; if (g?.classList.contains("work") && d === $("#col").lastElementChild) { $(".rows", g).appendChild(d); cur.group = g; } } break; }
    case "thought": {
      if (!cur.thought || !cur.thought.isConnected) { closeGroup(); cur.thought = add(`<details ${S.steps ? "open" : ""}><summary>Thinking</summary><div></div></details>`, "think"); }
      const td = $("div", cur.thought); td.textContent += ev.text; td.scrollTop = td.scrollHeight; cur.activity = "Thinking"; break; }
    case "plan": {
      const html = `<b>Plan</b><ol>${(ev.entries || []).map(e => `<li class="${esc(e.status)}">${esc(e.content)}</li>`).join("")}</ol>`;
      if (cur.plan && cur.plan.isConnected) cur.plan.innerHTML = html; else { closeGroup(); cur.plan = add(html, "planbox"); }
      break; }
    case "context": if (cur.view) cur.view.context = {used: ev.used, size: ev.size}; break;
    case "status": if (!/^Starting MCP|^MCP:/.test(ev.text || "")) add(esc(ev.text), "note"); else cur.mcpNote = ev.text; break;
    case "error": { closeGroup(); const n = add(esc(ev.text) + (/settings/i.test(ev.text || "") ? ` <button class="linkbtn" data-os="1">Open Settings</button>` : ""), "note bad"); $("[data-os]", n)?.addEventListener("click", () => openSettings("general")); break; }
    case "interrupted": {
      settleSteps("stopped"); closeGroup();
      const n = add(esc(ev.text) + (ev.resend ? ` <button class="linkbtn" data-resend="1">Put it back in the message box</button>` : ""), "note warn");
      $("[data-resend]", n)?.addEventListener("click", () => { const inp = $("#input"); inp.value = ev.resend; inp.dispatchEvent(new Event("input")); inp.focus(); });
      break; }
    case "checkpoint": {
      const t = (ev.turns || [])[0];
      if (t && cur.lastFoot && t.changed && t.changed.length && !$("[data-diff]", cur.lastFoot)) { cur.lastFoot.dataset.turn = t.n; cur.lastFoot.insertAdjacentHTML("beforeend", `<button data-diff="${t.n}">${ICON.diff} ${t.changed.length} file${t.changed.length === 1 ? "" : "s"} changed</button><button data-undo="${t.n}">${ICON.undo} Undo</button>`); bindFoot(cur.lastFoot); }
      break; }
    case "turn_end": {
      settleSteps("");
      closeGroup();
      const u = ev.turn || {};
      const foot = add("", "turnfoot"); if (u.calls) foot.title = `${u.calls} model call${u.calls === 1 ? "" : "s"} · ${fmt(u.in)} tokens in · ${fmt(u.out)} out`;
      cur.lastFoot = foot;
      const t = (cur.view?.turns || []).find(x => x.n === cur.turn);
      if (t && t.changed && t.changed.length && !$("[data-diff]", foot)) { foot.insertAdjacentHTML("beforeend", `<button data-diff="${t.n}">${ICON.diff} ${t.changed.length} file${t.changed.length === 1 ? "" : "s"} changed</button><button data-undo="${t.n}">${ICON.undo} Undo</button>`); bindFoot(foot); }
      if (cur.view?.plan && cur.lastText) {
        const b = add(`<button class="btn primary">Build this plan</button><button class="btn">Keep planning</button>`, "buildplan");
        $$("button", b)[0].onclick = async () => { b.remove(); await setPlan(false); $("#input").value = "Go ahead and implement the plan above."; sendNow(); };
        $$("button", b)[1].onclick = () => { b.remove(); $("#input").focus(); };
      }
      break; }
  }
}
/* ---------- ask_user: questions with options ---------- */
// The agent asks 1-4 questions; each has 2-5 options (the recommended one is preselected) and a free-text "Other".
// Answers go to POST /api/agent/<id>/reply {question_id, answers: [{selected: [labels], other: text}]}.
function questionEv(ev, add) {
  if ((ev.type === "tool_start" || ev.type === "tool_end") && ev.name === "ask_user") return true;   // the card shows it
  if (ev.type === "question") { closeGroup(); questionCard(ev, add); S.cur.activity = "Waiting for your answer"; return true; }
  if (ev.type === "question_done") { questionDone(ev); return true; }
  return false;
}
function questionCard(ev, add) {
  const qs = ev.questions || [], cid = "q-" + String(ev.id).replace(/[^\w-]/g, "");
  const many = qs.length > 1;
  const html = `<div class="qhead" id="${cid}-h">${many ? `${qs.length} questions before I go on` : "A question before I go on"}</div>` +
    qs.map((q, i) => `<fieldset class="qq" data-i="${i}" data-multi="${q.multi ? 1 : 0}">
      <legend><span class="qchip">${esc(q.header || `Question ${i + 1}`)}</span><span class="qtext">${esc(q.question)}</span>${q.multi ? `<span class="qhint">Pick any</span>` : ""}</legend>
      <div class="qopts">${(q.options || []).map(o => `<button type="button" class="qopt" aria-pressed="${o.recommended ? "true" : "false"}" data-l="${esc(o.label)}"><span class="ql">${esc(o.label)}${o.recommended ? ` <span class="qrec">Recommended</span>` : ""}</span>${o.description ? `<span class="qd">${esc(o.description)}</span>` : ""}</button>`).join("")}</div>
      <label class="qother"><span>Other</span><input type="text" placeholder="Type your own answer" aria-label="Your own answer to: ${esc(q.question)}"></label>
    </fieldset>`).join("") +
    `<div class="acts"><button type="button" class="btn primary qsend">Send ${many ? "answers" : "answer"}</button><span class="qnote">${ev.timeout_s ? "Or type a reply in the message box." : ""}</span></div>`;
  const d = add(html, "qcard"); d.id = cid; d.setAttribute("role", "group"); d.setAttribute("aria-labelledby", cid + "-h");
  $$(".qq", d).forEach(fs => {
    const multi = fs.dataset.multi === "1", btns = $$(".qopt", fs), inp = $("input", fs);
    btns.forEach(b => b.onclick = () => {
      const on = b.getAttribute("aria-pressed") !== "true";
      if (!multi) { btns.forEach(x => x.setAttribute("aria-pressed", "false")); if (on) inp.value = ""; }
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    inp.oninput = () => { if (!multi && inp.value.trim()) btns.forEach(x => x.setAttribute("aria-pressed", "false")); };
    inp.onkeydown = e => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); $(".qsend", d).click(); } };
  });
  $(".qsend", d).onclick = async () => {
    const answers = $$(".qq", d).map(fs => ({selected: $$(".qopt[aria-pressed=true]", fs).map(b => b.dataset.l), other: $("input", fs).value.trim()}));
    $$("button, input", d).forEach(x => x.disabled = true);
    try { await post(`/api/agent/${S.cur.id}/reply`, {question_id: ev.id, answers}); clearTimeout(S.cur.pollT); poll(); }
    catch (e) { $$("button, input", d).forEach(x => x.disabled = false); toast(e.message); }
  };
  if (!ev.timeout_s) $$("button, input", d).forEach(x => x.disabled = true);   // answered at once (no one to ask)
  scrollConv(true);
  if (ev.timeout_s && document.activeElement === document.body) $(".qopt[aria-pressed=true]", d)?.focus({preventScroll: true});
}
function questionDone(ev) {
  const d = document.getElementById("q-" + String(ev.id).replace(/[^\w-]/g, "")); if (!d || d.classList.contains("done")) return;
  d.classList.add("done");
  const src = ev.source, head = $(".qhead", d);
  if (head) head.textContent = src === "stopped" ? "Question (stopped)" : src === "user" || src === "replay" ? "You answered" : src === "timeout" ? "No answer in time: went with the recommendations" : "Went with the recommendations";
  const ans = ev.answers || [];
  const typed = src === "user" && /^You replied/.test(ev.text || "");
  const rows = ans.length && !typed ? $$(".qq", d).map((fs, i) => {
    const a = ans[i] || {}, q = $(".qtext", fs)?.textContent || "", h = $(".qchip", fs)?.textContent || "";
    const val = [...(a.selected || []), ...(a.other ? [`“${a.other}”`] : [])].join(", ");
    return `<div class="qa"><span class="qchip">${esc(h)}</span><span class="qtext">${esc(q)}</span><b>${esc(val || "-")}</b>${a.default && src === "user" ? `<span class="qhint">recommended</span>` : ""}</div>`;
  }).join("") : `<div class="qa"><span class="qtext qpre">${esc(ev.text || "")}</span></div>`;
  $$(".qq, .acts", d).forEach(x => x.parentNode.removeChild(x));
  d.insertAdjacentHTML("beforeend", rows);
}
// A step whose end never arrived (stopped, or a harness that does not report it) must not say "running" forever.
function settleSteps(label) {
  for (const d of Object.values(S.cur?.tools || {})) {
    const st = $(".st.run", d); if (!st) continue;
    const nm = d.dataset.name || "";
    st.className = "st"; st.textContent = label === "stopped" ? "not run" : "";
    $(".tn", d).textContent = label === "stopped" ? (TOOL_BASE[nm] || TOOL_LABEL[nm] || nm) : (TOOL_DONE[nm] || TOOL_LABEL[nm] || nm);
  }
}
function bindFoot(foot) {
  $$("[data-diff]", foot).forEach(b => b.onclick = () => { S.diffTurn = +b.dataset.diff; togglePanel(curMode() === "cowork" ? "outputs" : "diff", true); });
  $$("[data-undo]", foot).forEach(b => b.onclick = () => undoTurn(+b.dataset.undo));
}
async function undoTurn(n) {
  if (!await ask({title: "Undo this reply?", text: "Files Meshpool changed go back to how they were before it, and the conversation goes back to that point. Replies after it are undone too.", ok: "Undo changes", danger: true})) return;
  const asked = threadLog().filter(e => e.type === "user" && !e.steer)[n - 1]?.text || "";
  try { await post(`/api/agent/${S.cur.id}/revert`, {turn: n, since: 1e9}); const inp = $("#input"); if (asked && !inp.value.trim()) { inp.value = asked; inp.dispatchEvent(new Event("input")); } renderBranch(); clearTimeout(S.cur.pollT); S.cur.total = 0; S.cur.next = 0; S.cur.log = []; S.cur.epoch = -2; $("#col").innerHTML = ""; S.cur.turn = 0; S.cur.group = null; S.cur.live = null; S.cur.tools = {}; poll(); if (S.panel === "diff") renderPanel(); }
  catch (e) { toast(e.message); }
}

/* ================================== right panel ================================== */
const PTABS_ALL = {progress: "Progress", outputs: "Files", diff: "Diff", files: "Files", term: "Terminal", git: "Git and CI", preview: "Preview"};
function togglePanel(name, force) {
  if (!PANELS[curMode()].includes(name)) name = PANELS[curMode()][0];
  if (S.panel === name && !force && curMode() === "cowork") store("cw_panel_closed", true);
  if (force) store("cw_panel_closed", false);
  S.panel = (S.panel === name && !force) ? null : name; store("panel", S.panel);
  renderPanel(); renderTop();
}
function setupPanelResize() {
  const w = store("pw"); if (w) document.documentElement.style.setProperty("--pw", w + "px");
  $("#rz").addEventListener("mousedown", e => {
    e.preventDefault(); const move = ev => { const nw = Math.max(320, Math.min(innerWidth - 520, innerWidth - ev.clientX)); document.documentElement.style.setProperty("--pw", nw + "px"); store("pw", nw); };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", () => document.removeEventListener("mousemove", move), {once: true});
  });
}
function fitPanel() {
  const want = !!S.panel && innerWidth < 1180 && innerWidth > 980 && !S.keepSide;   // only where both cannot fit
  if ($("#app").classList.contains("autoside") !== want) { $("#app").classList.toggle("autoside", want); renderTop(); }
}
function renderPanel() {
  const p = $("#panel");
  if (S.panel && !PANELS[curMode()].includes(S.panel)) { S.panel = PANELS[curMode()][0]; store("panel", S.panel); }
  fitPanel(); if (!S.panel) { p.hidden = true; return; }
  p.hidden = false;
  $("#ptabs").innerHTML = PANELS[curMode()].map(k => [k, PTABS_ALL[k]]).map(([k, lb]) => `<button role="tab" aria-selected="${S.panel === k}" data-t="${k}">${lb}</button>`).join("") + `<span class="sp"></span><button class="iconbtn" id="pclose" aria-label="Close panel">${ICON.x}</button>`;
  $$("#ptabs [data-t]").forEach(b => b.onclick = () => togglePanel(b.dataset.t, true));
  $("#pclose").onclick = () => togglePanel(S.panel);
  const body = $("#pbody"); body.innerHTML = "";
  ({progress: panelProgress, outputs: panelOutputs, diff: panelDiff, files: panelFiles, term: panelTerm, git: panelGit,
    preview: panelPreview})[S.panel](body);
}

/* ---------- Cowork: progress, files made, context ---------- */
function threadLog() { return S.cur?.log || []; }
function progressSteps() {
  const log = threadLog();
  const lastUser = log.map(e => e.type).lastIndexOf("user");
  const turn = log.slice(Math.max(0, lastUser));
  const plan = [...turn].reverse().find(e => e.type === "plan");
  if (plan && (plan.entries || []).length) return {kind: "plan", items: plan.entries.map(e => ({text: e.content, status: e.status}))};
  const tools = {};
  for (const e of turn) {
    if (e.type === "tool_start") { const nm = e.mcp ? String(e.name || "").split("__").slice(2).join("__") : e.name; const sm = stepSummary(nm, e); tools[e.id] = {name: nm, sm, text: `${TOOL_RUN[nm] || e.label || nm}${sm ? " " + sm : ""}`, status: "in_progress"}; }
    if (e.type === "tool_end" && tools[e.id] && e.ok) tools[e.id].text = `${TOOL_DONE[tools[e.id].name] || TOOL_LABEL[tools[e.id].name] || tools[e.id].name}${tools[e.id].sm ? " " + tools[e.id].sm : ""}`;
    if (e.type === "tool_end" && tools[e.id]) tools[e.id].status = e.ok ? "completed" : "failed";
    if (e.type === "interrupted" || e.type === "turn_end") for (const t of Object.values(tools)) if (t.status === "in_progress") t.status = e.type === "interrupted" ? "stopped" : "completed";
  }
  return {kind: "steps", items: Object.values(tools)};
}
const OUT_RX = /(?:Saved(?: the [\w ]+)?|[Ww]rote|[Cc]reated)[: ]+((?:[A-Za-z]:)?[^\n"'`<>():]+?\.(?:docx|xlsx|pptx|pdf|csv|md|png|jpg|html|txt|json))(?![\w.])/g;
function outputFiles() {
  const root = curRoot(); const seen = new Map();
  const rel = p => { const n = String(p).replace(/\\/g, "/"); const r = String(root).replace(/\\/g, "/").replace(/\/$/, ""); return n.startsWith(r + "/") ? n.slice(r.length + 1) : n; };
  for (const t of S.cur?.view?.turns || []) for (const c of t.changed || []) if (c.status !== "deleted") seen.set(c.path, {path: c.path, turn: t.n});
  for (const e of threadLog()) if (e.type === "tool_end" && e.output) for (const m of String(e.output).matchAll(OUT_RX)) { const r = rel(m[1]); if (!r.startsWith("/") && !/^[A-Za-z]:/.test(r)) seen.set(r, {path: r}); }
  return [...seen.values()];
}
function fileIcon(p) { return ICON.files; }
function panelProgress(body) {
  if (!S.cur) { body.innerHTML = `<div class="pnote">Start a task and its progress shows here: the plan, each step as it finishes, the files it makes and what it can reach.</div>`; return; }
  const v = S.cur.view || {}; const pr = progressSteps(); const outs = outputFiles();
  const busy = ["working", "waiting", "starting"].includes(v.state);
  const icon = st => st === "completed" ? `<span class="pi ok">${ICON.check2}</span>` : st === "failed" ? `<span class="pi bad">${ICON.x}</span>` : st === "in_progress" ? `<span class="pi run"><span class="dot run"></span></span>` : `<span class="pi">${ICON.ring}</span>`;
  const users = threadLog().filter(e => e.type === "user");
  const skills = [...new Set(users.flatMap(u => (String(u.text).match(/\$[\w-]+/g) || [])))].filter(x => S.skills.some(k => "$" + k.name === x));
  body.innerHTML = `<div class="cw">
    <section><h4>Progress${busy ? ` <span class="cwbusy">${v.state === "waiting" ? "Waiting for you" : "Working"}</span>` : v.state === "idle" && pr.items.length ? (threadLog().slice(-12).some(e => e.type === "interrupted") ? ` <span class="cwdone">Stopped</span>` : ` <span class="cwdone">Done</span>`) : ""}</h4>
      ${pr.items.length ? `<ol class="stepl">${pr.items.map(it => `<li class="${esc(it.status)}">${icon(it.status)}<span>${esc(it.text)}</span></li>`).join("")}</ol>` : `<div class="pnote" style="padding:4px 0">${busy ? "Getting started..." : "No steps yet for this task."}</div>`}
    </section>
    <section><h4>Files made</h4>${outs.length ? `<ul class="outs">${outs.map(o => `<li><button data-open="${esc(o.path)}" title="Open ${esc(o.path)}"><span class="fi">${fileIcon(o.path)}</span><span class="fp">${esc(o.path.split("/").pop())}<small>${esc(o.path)}</small></span></button></li>`).join("")}</ul>` : `<div class="pnote" style="padding:4px 0">Documents, sheets, decks and other files the task creates show up here.</div>`}</section>
    <section><h4>Context</h4><ul class="ctxl">
      <li>${ICON.dir}<span><b>${esc(projName(curRoot()))}</b><small title="${esc(curRoot())}">${esc(shortPath(curRoot()))}</small></span><button class="linkbtn" id="cwOpen">Open</button></li>
      <li>${ICON.cpu}<span><b>${esc(v.harness?.name || "")}</b><small>${esc(modelName(v.model || ""))}</small></span></li>
      <li>${ICON.plug}<span><b>Connectors</b><small id="cwConn">Loading...</small></span><button class="linkbtn" id="cwConnMgr">Manage</button></li>
      ${skills.length ? `<li>${ICON.skill}<span><b>Skills</b><small>${esc(skills.join(", "))}</small></span></li>` : ""}
    </ul></section></div>`;
  $$("[data-open]", body).forEach(b => b.onclick = () => { toast(`Opening ${b.dataset.open}…`); post("/api/open-file", {root: curRoot(), path: b.dataset.open}).catch(e => toast(e.message)); });
  $("#cwOpen").onclick = () => post("/api/open-path", {path: curRoot()}).catch(e => toast(e.message));
  $("#cwConnMgr").onclick = () => openSettings("connectors");
  (S.connCache ? Promise.resolve(S.connCache) : api("/api/connectors").then(r => (S.connCache = r.connectors))).then(cs => {
    const on = cs.filter(c => c.connected && c.enabled).map(c => c.name); const el = $("#cwConn"); if (el) el.textContent = on.length ? on.join(", ") : "None connected";
  }).catch(() => {});
}
function panelOutputs(body) {
  const outs = S.cur ? outputFiles() : [];
  body.innerHTML = `<div class="pbar"><span class="chip">${ICON.dir}${esc(projName(curRoot()))}</span><span style="flex:1"></span><button class="btn" id="oOpen">Open folder</button></div>
    ${outs.length ? `<ul class="outs" style="padding:10px">${outs.map(o => `<li><button data-open="${esc(o.path)}"><span class="fi">${fileIcon(o.path)}</span><span class="fp">${esc(o.path.split("/").pop())}<small>${esc(o.path)}</small></span></button></li>`).join("")}</ul>` : `<div class="pnote">Files this thread makes or changes show here. Click one to open it.</div>`}
    <div style="border-top:1px solid var(--line-2);flex:1;min-height:160px;display:flex;flex-direction:column"><div class="pnote" style="padding:8px 12px 0">All files in the folder</div><div style="flex:1;overflow:auto"><ul class="tree" id="tree"></ul></div><div class="viewer" id="viewer" hidden></div></div>`;
  $("#oOpen").onclick = () => post("/api/open-path", {path: curRoot()}).catch(e => toast(e.message));
  $$("[data-open]", body).forEach(b => b.onclick = () => post("/api/open-file", {root: curRoot(), path: b.dataset.open}).catch(e => toast(e.message)));
  loadTree($("#tree"), "");
}
async function panelDiff(body) {
  if (!S.cur) { body.innerHTML = `<div class="pnote">This thread hasn't changed any files yet. Each reply's changes show here, and you can undo them.</div>`; return; }
  const turns = S.cur.view?.turns || [];
  const sel = S.diffTurn ?? "";
  body.innerHTML = `<div class="pbar"><select id="dturn" aria-label="Which changes"><option value="">All changes in this thread</option>${turns.slice().reverse().map(t => `<option value="${t.n}" ${+sel === t.n ? "selected" : ""}>Reply ${t.n}${t.title ? ": " + esc(clip(t.title, 50)) : ""}</option>`).join("")}</select>
    <button class="btn" id="dref" title="Refresh">${ICON.refresh}</button>${sel && (turns.find(t => t.n === +sel)?.changed || []).length ? `<button class="btn danger" id="dundo">${ICON.undo} Undo</button>` : ""}</div><div id="dbody" class="pbody"><div class="pnote">Loading...</div></div>`;
  $("#dturn").onchange = () => { S.diffTurn = $("#dturn").value ? +$("#dturn").value : null; renderPanel(); };
  $("#dref").onclick = renderPanel;
  $("#dundo")?.addEventListener("click", () => undoTurn(+sel));
  try {
    const d = await api(`/api/agent/${S.cur.id}/diff${sel ? "?turn=" + sel : ""}`);
    if (!d.files.length) { $("#dbody").innerHTML = `<div class="pnote">${esc(d.note || S.cur.view?.checkpoints_note || sel ? "This reply didn't change any files." : "This thread hasn't changed any files yet. Other changes in this folder are under Git and CI.")}</div>`; return; }
    const add = d.files.reduce((a, f) => a + (f.add || 0), 0), del = d.files.reduce((a, f) => a + (f.del || 0), 0);
    $("#dbody").innerHTML = `<div class="pnote" style="padding:8px 12px">${d.files.length} file${d.files.length === 1 ? "" : "s"} · <span style="color:var(--add)">+${add}</span> <span style="color:var(--del)">−${del}</span></div>
      <ul class="flist">${d.files.map((f, i) => `<li data-i="${i}"><span class="badge ${f.status}">${f.status[0].toUpperCase()}</span><span class="p">${esc(String(f.path).replace(/^"|"$/g, ""))}</span><span class="a">+${f.add || 0}</span><span class="r">−${f.del || 0}</span></li>`).join("")}</ul>
      <pre class="patch" id="patch">${patchHTML(d.patch)}</pre>`;
    $$("#dbody .flist li").forEach(li => li.onclick = () => { const f = d.files[+li.dataset.i]; const el = $(`#patch [data-file="${CSS.escape(f.path)}"]`); el?.scrollIntoView({block: "start"}); });
  } catch (e) { $("#dbody").innerHTML = `<div class="pnote">${esc(e.message)}</div>`; }
}
function patchHTML(p) {
  return String(p || "").split("\n").map(l => {
    if (l.startsWith("--- a/")) return "";
    if (l.startsWith("+++ b/")) { const f = l.slice(6); return `<span class="l f" data-file="${esc(f)}">${esc(f)}</span>`; }
    if (l.startsWith("diff --meshpool")) return `<span class="l f">${esc(l.split(" b/")[1] || l)}</span>`;
    const c = l.startsWith("@@") ? "h" : l.startsWith("+") ? "a" : l.startsWith("-") ? "d" : "";
    return `<span class="l ${c}">${esc(l) || " "}</span>`;
  }).join("");
}
async function panelFiles(body) {
  const root = curRoot(); if (!root) { body.innerHTML = `<div class="pnote">Choose a project folder first.</div>`; return; }
  body.innerHTML = `<div class="pbar"><span class="chip" title="${esc(root)}">${ICON.dir}${esc(projName(root))}</span><span style="flex:1"></span><button class="btn" id="fref">${ICON.refresh}</button></div><div style="flex:1;overflow:auto;min-height:120px"><ul class="tree" id="tree"></ul></div><div class="viewer" id="viewer" hidden></div>`;
  $("#fref").onclick = renderPanel;
  await loadTree($("#tree"), "");
}
async function loadTree(ul0, rel0) {
  const root = curRoot(); if (!root || !ul0) return;
  const load = async (ul, rel) => {
    try {
      const r = await api(`/api/files?root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel)}`);
      ul.innerHTML = r.entries.map(e => `<li><div data-path="${esc(e.path)}" data-dir="${e.dir ? 1 : ""}">${e.dir ? ICON.dir : ICON.file}<span>${esc(e.name)}</span></div>${e.dir ? `<ul hidden></ul>` : ""}</li>`).join("") || `<li class="pnote" style="padding:4px 8px">Empty folder</li>`;
      $$(":scope > li > div", ul).forEach(d => d.onclick = async () => {
        if (d.dataset.dir) { const sub = d.nextElementSibling; sub.hidden = !sub.hidden; if (!sub.hidden && !sub.children.length) await load(sub, d.dataset.path); }
        else openFile(root, d.dataset.path, d);
      });
    } catch (e) { ul.innerHTML = `<li class="pnote">${esc(e.message)}</li>`; }
  };
  await load(ul0, rel0);
}
async function openFile(root, rel, node) {
  $$("#tree div.on").forEach(x => x.classList.remove("on")); node?.classList.add("on");
  const v = $("#viewer"); v.hidden = false; v.innerHTML = `<div class="pnote">Loading...</div>`;
  try {
    const f = await api(`/api/file?root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel)}`);
    const raw = `/raw?t=${encodeURIComponent(MP.token)}&root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel)}`;
    if (f.binary && /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(rel)) { v.innerHTML = `<div class="pbar"><span class="p" style="font-family:var(--mono);font-size:12px">${esc(rel)}</span></div><div class="imgview"><img src="${raw}" alt="${esc(rel)}"></div>`; return; }
    if (f.binary) { v.innerHTML = `<div class="pbar"><span class="p" style="font-family:var(--mono);font-size:12px">${esc(rel)}</span></div><div class="pnote">${esc(f.note || "Binary file. Open it from the folder.")}</div>`; return; }
    v.innerHTML = `<div class="pbar"><span style="font-family:var(--mono);font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(rel)}</span><button class="btn" id="fsave" disabled>Save</button></div><textarea id="ftext" spellcheck="false"></textarea>`;
    const ta = $("#ftext"); ta.value = f.text;
    if (f.readonly) { ta.readOnly = true; $("#fsave").outerHTML = `<span class="pnote" style="padding:0">${esc(f.note || "Read only")}</span>`; return; }
    ta.oninput = () => $("#fsave").disabled = false;
    ta.onkeydown = e => { if ((isMac ? e.metaKey : e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); $("#fsave").click(); } };
    $("#fsave").onclick = async () => { try { await post("/api/file", {root, path: rel, text: ta.value}); $("#fsave").disabled = true; toast("File saved"); } catch (e) { toast(e.message); } };
  } catch (e) { v.innerHTML = `<div class="pnote">${esc(e.message)}</div>`; }
}
function panelTerm(body) {
  const root = curRoot(); if (!root) { body.innerHTML = `<div class="pnote">Choose a project folder first.</div>`; return; }
  S.term = S.term && S.term.root === root ? S.term : {root, html: "", hist: [], hi: 0, id: null};
  body.innerHTML = `<pre class="term-out" id="tout">${S.term.html || `<span class="rc">Commands run in ${esc(shortPath(root))}</span>\n`}</pre><div class="term-in">${termShellPicker()}<input id="tin" placeholder="Run a command" spellcheck="false" autocomplete="off"><button class="btn" id="tstop" hidden>Stop</button></div>`;
  const out = $("#tout"), inp = $("#tin"); out.scrollTop = out.scrollHeight; inp.focus();
  const save = () => { S.term.html = out.innerHTML; };
  inp.onkeydown = async e => {
    if (e.key === "ArrowUp") { e.preventDefault(); if (S.term.hist.length) { S.term.hi = Math.max(0, S.term.hi - 1); inp.value = S.term.hist[S.term.hi] || ""; } return; }
    if (e.key === "ArrowDown") { e.preventDefault(); S.term.hi = Math.min(S.term.hist.length, S.term.hi + 1); inp.value = S.term.hist[S.term.hi] || ""; return; }
    if (e.key !== "Enter") return;
    const cmd = inp.value.trim(); if (!cmd) return;
    if (cmd === "clear") { out.innerHTML = ""; inp.value = ""; save(); return; }
    S.term.hist.push(cmd); S.term.hi = S.term.hist.length; inp.value = "";
    out.insertAdjacentHTML("beforeend", `<span class="cmd">$ ${esc(cmd)}</span>\n`);
    try {
      const t = await post("/api/term", {root, command: cmd, shell: store("term_shell") || S.settings?.shells?.default || ""}); S.term.id = t.id; $("#tstop").hidden = false;
      let since = 0;
      const tick = async () => {
        const r = await api(`/api/term/${t.id}?since=${since}`); since = r.next;
        if (r.lines.length) { out.insertAdjacentText("beforeend", r.lines.join("")); out.scrollTop = out.scrollHeight; }
        if (r.running) setTimeout(tick, 250); else { out.insertAdjacentHTML("beforeend", r.rc ? `<span class="rc">[exit ${r.rc}]</span>\n` : ""); $("#tstop") && ($("#tstop").hidden = true); save(); }
      };
      tick();
    } catch (err) { out.insertAdjacentText("beforeend", err.message + "\n"); save(); }
  };
  $("#tstop").onclick = () => S.term.id && post(`/api/term/${S.term.id}/stop`).catch(() => {});
}
async function panelGit(body) {
  const root = curRoot(); if (!root) { body.innerHTML = `<div class="pnote">Choose a project folder first.</div>`; return; }
  body.innerHTML = `<div class="pnote">Loading...</div>`;
  let g; try { g = await api("/api/git?root=" + encodeURIComponent(root)); } catch (e) { body.innerHTML = `<div class="pnote">${esc(e.message)}</div>`; return; }
  if (!g.git) { body.innerHTML = `<div class="pnote">Git is not installed on this computer.${isMac ? " Open Terminal and run <code>xcode-select --install</code>, then come back." : ""}</div>`; return; }
  if (!g.repo) { body.innerHTML = `<div class="gitbox"><div class="stat">This folder is not a Git repository.</div><div class="row"><button class="btn primary" id="ginit">Start a Git repository here</button></div></div>`; $("#ginit").onclick = () => gitAct({action: "init"}); return; }
  body.innerHTML = `<div class="gitbox">
    <div><h4>Branch</h4><div class="row" style="margin-top:6px"><select id="gbr">${g.branches.map(b => `<option ${b === g.branch ? "selected" : ""}>${esc(b)}</option>`).join("")}</select></div>
      <div class="row" style="margin-top:6px"><input id="gnew" placeholder="New branch name"><button class="btn" id="gcreate">Create</button></div>
      <div class="stat" style="margin-top:6px">${g.upstream ? `${g.ahead} to push, ${g.behind} to pull` : "Not pushed yet"}${g.remote ? ` · ${esc(g.github || g.remote)}` : " · no remote"}</div></div>
    <div><h4>Changes (${g.dirty})</h4><ul class="flist" style="border:1px solid var(--line-2);border-radius:var(--r-s);margin-top:6px;max-height:180px;overflow:auto">${g.files.map(f => `<li><span class="badge modified">${esc(GIT_WORD[String(f.status).trim()] || f.status)}</span><span class="p">${esc(f.path)}</span></li>`).join("") || `<li class="pnote" style="padding:6px 10px">Nothing to commit.</li>`}</ul></div>
    <div><h4>Commit</h4><textarea id="gmsg" rows="2" placeholder="What changed and why" style="margin-top:6px"></textarea><div class="row" style="margin-top:6px"><button class="btn primary" id="gcommit" ${g.dirty ? "" : "disabled"}>Commit all</button><button class="btn" id="gpush" ${g.remote ? "" : "disabled"}>Push</button><button class="btn" id="gpull" ${g.upstream ? "" : "disabled"}>Pull</button></div></div>
    <div id="ciBox"><h4>Pull request, checks and deployments</h4><div class="pnote" style="padding:6px 0">Loading from GitHub...</div></div>
    <div><h4>Open a pull request</h4><input id="gprt" placeholder="Title (defaults to the last commit)" style="margin-top:6px"><textarea id="gprb" rows="3" placeholder="Description" style="margin-top:6px"></textarea><div class="row" style="margin-top:6px"><button class="btn" id="gpr" ${g.github ? "" : "disabled"}>Open pull request</button>${g.github ? "" : `<span class="stat">Needs a GitHub remote.</span>`}</div><div class="stat" id="gres"></div></div>
  </div>`;
  $("#gbr").onchange = () => gitAct({action: "checkout", branch: $("#gbr").value});
  $("#gcreate").onclick = () => gitAct({action: "checkout", branch: $("#gnew").value, create: true});
  $("#gcommit").onclick = () => gitAct({action: "commit", message: $("#gmsg").value});
  $("#gpush").onclick = () => gitAct({action: "push"});
  $("#gpull").onclick = () => gitAct({action: "pull"});
  $("#gpr").onclick = () => gitAct({action: "pr", title: $("#gprt").value, body: $("#gprb").value});
  if (g.github) drawCI(root); else $("#ciBox").innerHTML = `<h4>Pull request, checks and deployments</h4><div class="stat">Show up for repositories on GitHub.</div>`;
}
const CK = {success: ["ok", "Passed"], failure: ["bad", "Failed"], timed_out: ["bad", "Timed out"], cancelled: ["bad", "Cancelled"],
  action_required: ["bad", "Needs action"], skipped: ["", "Skipped"], neutral: ["", "Neutral"]};
function ckState(c) { if (c.status && c.status !== "completed") return ["run", c.status === "queued" ? "Queued" : "Running"]; return CK[c.conclusion] || ["", c.conclusion || "Done"]; }
async function drawCI(root, fresh) {
  const box = $("#ciBox"); if (!box) return;
  let ci;
  try { ci = await api("/api/git/ci?root=" + encodeURIComponent(root)); } catch (e) { box.innerHTML = `<h4>Pull request, checks and deployments</h4><div class="stat">${esc(e.message)}</div>`; return; }
  S.ciCache = {...(S.ciCache || {}), [root]: {t: Date.now(), ci}};
  if (!$("#ciBox")) return;
  const pr = ci.pr; const failingRuns = ci.runs.filter(r => r.conclusion === "failure");
  box.innerHTML = `<h4>Pull request, checks and deployments <button class="iconbtn" id="ciRef" title="Refresh" aria-label="Refresh checks">${ICON.refresh}</button></h4>
    ${!ci.connected ? `<div class="stat">Connect GitHub to see private repositories and to rerun or merge. <button class="linkbtn" id="ciConn">Connect GitHub</button></div>` : ""}
    ${ci.error ? `<div class="stat">${esc(ci.error)}</div>` : ""}
    ${pr ? `<div class="prcard"><a href="${esc(pr.url)}" target="_blank" rel="noopener"><b>#${pr.number} ${esc(pr.title)}</b></a><span class="badge ${pr.state === "open" ? "added" : pr.state === "merged" ? "modified" : "deleted"}">${pr.draft ? "Draft" : pr.state === "open" ? "Open" : pr.state === "merged" ? "Merged" : "Closed"}</span><small>${esc(ci.branch)} into ${esc(pr.base)}${pr.author ? " by " + esc(pr.author) : ""}</small>
      ${pr.state === "open" && ci.connected ? `<div class="row" style="margin-top:6px"><button class="btn" id="ciMerge" ${ci.summary === "failing" ? "disabled title='Fix the failing checks first'" : ""}>Squash and merge</button></div>` : ""}</div>`
      : `<div class="stat">No pull request for ${esc(ci.branch)} yet.</div>`}
    <div class="cil"><b>Checks</b> <span class="stat">${esc(ci.sha)}</span>${ci.checks.length ? `<ul>${ci.checks.map(c => { const [cl, tx] = ckState(c); return `<li><span class="cidot ${cl}"></span><span class="cn">${esc(c.name)}</span><span class="cs">${tx}</span>${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">Details</a>` : ""}</li>`; }).join("")}</ul>` : `<div class="stat">No checks on this commit.</div>`}</div>
    ${ci.runs.length ? `<div class="cil"><b>Workflow runs (CI)</b><ul>${ci.runs.map(r => { const [cl, tx] = ckState(r); return `<li><span class="cidot ${cl}"></span><span class="cn">${esc(r.name)}</span><span class="cs">${tx}</span><a href="${esc(r.url)}" target="_blank" rel="noopener">Logs</a>${r.conclusion === "failure" && ci.connected ? `<button class="linkbtn" data-rerun="${r.id}">Rerun failed</button>` : ""}</li>`; }).join("")}</ul></div>` : ""}
    <div class="cil"><b>Deployments (CD)</b>${ci.deployments.length ? `<ul>${ci.deployments.map(d => `<li><span class="cidot ${d.state === "success" || d.state === "active" ? "ok" : d.state === "failure" || d.state === "error" ? "bad" : d.state === "in_progress" || d.state === "pending" || d.state === "queued" ? "run" : ""}"></span><span class="cn">${esc(d.environment || "deployment")}</span><span class="cs">${esc(d.state)} · ${esc(d.ref || d.sha)}</span>${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">Open</a>` : ""}</li>`).join("")}</ul>` : `<div class="stat">No deployments recorded on GitHub.</div>`}</div>
    ${failingRuns.length ? `<div class="row" style="margin-top:8px"><button class="btn primary" id="ciFix">Ask the agent to fix the failing CI</button></div>` : ""}`;
  $("#ciRef").onclick = () => drawCI(root, true);
  $("#ciConn")?.addEventListener("click", () => openSettings("connectors"));
  $("#ciMerge")?.addEventListener("click", async () => { if (!await ask({title: `Merge #${pr.number}?`, text: `Squash and merge ${ci.branch} into ${pr.base} on GitHub.`, ok: "Merge"})) return; try { await post("/api/git/ci", {root, action: "merge", pr: pr.number}); toast("Pull request merged"); drawCI(root, true); } catch (e) { toast(e.message, 5000); } });
  $$("[data-rerun]", box).forEach(b => b.onclick = async () => { try { await post("/api/git/ci", {root, action: "rerun", run: b.dataset.rerun}); toast("Rerunning the failed jobs"); setTimeout(() => drawCI(root, true), 3000); } catch (e) { toast(e.message, 5000); } });
  $("#ciFix")?.addEventListener("click", () => { const i = $("#input"); i.value = `CI is failing on ${ci.branch}: ${failingRuns.map(r => r.name + " (" + r.url + ")").join(", ")}. Find out why from the logs (use gh or the GitHub tools), fix it, run the tests, commit and push.`; i.focus(); i.dispatchEvent(new Event("input")); });
}
async function gitAct(b) {
  const root = curRoot();
  try {
    const r = await post("/api/git", {root, ...b});
    if (b.action === "pr" && r.pr) { renderPanel(); setTimeout(() => { const el = $("#gres"); if (el) el.innerHTML = `${esc(r.note || "Pull request ready:")} <a href="${esc(r.pr)}" target="_blank" rel="noopener">${esc(r.pr)}</a>`; }, 300); }
    else { toast({checkout: "Switched branch", commit: "Committed", push: "Pushed", pull: "Pulled", init: "Repository created"}[b.action] || "Done"); renderPanel(); }
    renderBranch();
  } catch (e) { toast(e.message, 5000); }
}
function panelPreview(body) {
  const url = store("preview") || "http://localhost:3000";
  body.innerHTML = `<div class="pbar"><input id="purl" value="${esc(url)}" aria-label="Address"><button class="btn" id="pgo">Open</button><button class="btn" id="pext" title="Open in your browser">${ICON.globe}</button></div><div class="pnote" style="padding:6px 12px">Start your app first (for example ask Meshpool to run it), then enter its address.</div><iframe class="preview" id="pframe" title="Preview" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>`;
  const go = () => { let u = $("#purl").value.trim(); if (!/^https?:\/\//.test(u)) u = "http://" + u; store("preview", u); $("#pframe").src = u; };
  $("#pgo").onclick = go; $("#purl").onkeydown = e => { if (e.key === "Enter") go(); };
  $("#pext").onclick = () => openExternal($("#purl").value);
  go();
}

/* ================================== settings ================================== */
const SECTIONS = [["general", "Account and key"], ["appearance", "Appearance and shortcuts"], ["models", "Models"], ["personal", "Instructions and memory"], ["import", "Import chats and context"], ["scheduled", "Scheduled tasks"], ["web", "Web search"], ["connectors", "Connectors"], ["skills", "Skills"], ["harnesses", "Agents"], ["mcp", "Tools"], ["permissions", "Permissions"], ["usage", "Usage"], ["terminal", "Terminal command"], ["advanced", "Advanced"], ["about", "About"]];
SECTIONS.splice(SECTIONS.findIndex(s => s[0] === "skills") + 1, 0, ["plugins", "Plugins"]);
function openSettings(sec = "general") {
  closePop();
  $(".scrim")?.remove();
  const sc = document.createElement("div"); sc.className = "scrim";
  sc.innerHTML = `<div class="modal" role="dialog" aria-label="Settings"><nav class="mnav"><h2>Settings</h2>${SECTIONS.map(([k, lb]) => `<button data-s="${k}" aria-selected="${k === sec}">${lb}</button>`).join("")}<span class="sp"></span>${MP.desktop && !S.settings?.native ? `<button id="quitApp">Quit Meshpool</button>` : ""}</nav><div class="mbody" id="mbody"></div></div>`;
  document.body.appendChild(sc);
  sc.addEventListener("mousedown", e => { if (e.target === sc) sc.remove(); });
  $$(".mnav [data-s]", sc).forEach(b => b.onclick = () => { $$(".mnav [data-s]", sc).forEach(x => x.setAttribute("aria-selected", x === b)); renderSection(b.dataset.s); });
  $("#quitApp")?.addEventListener("click", () => post("/api/quit").then(() => document.body.innerHTML = `<div class="onb"><div class="onb-card"><h1>Meshpool has stopped</h1><p>You can close this window.</p></div></div>`));
  renderSection(sec);
}
async function renderSection(sec) {
  const b = $("#mbody"); b.classList.add("loading");
  new MutationObserver((_, o) => { b.classList.remove("loading"); b.scrollTop = 0; o.disconnect(); }).observe(b, {childList: true});
  const close = `<button class="iconbtn mclose" aria-label="Close settings">${ICON.x}</button>`;
  const done = () => $(".mclose", b)?.addEventListener("click", () => $(".scrim").remove());
  await loadSettings();
  const F = S.settings.fields; const get = k => F.find(x => x.key === k) || {};
  if (sec === "general") {
    const key = get("MESHPOOL_API_KEY"), base = get("MESHPOOL_BASE_URL");
    b.innerHTML = `${close}<h3>Account and key</h3><p class="lead">Meshpool sends model calls to this endpoint with your key. Any OpenAI-compatible endpoint works. The key is stored only in the settings file on this computer.</p>
      <label class="field"><span>Runs on</span><select id="sProv">${PROVIDERS.map(([id, name]) => `<option value="${id}" ${id === (S.settings.provider || "meshpool") ? "selected" : ""}>${name}</option>`).join("")}</select><small id="sProvNote">What the models run on. The agent is chosen separately, in the message box. A Claude subscription runs Claude Code only.</small></label>
      <label class="field"><span>API key</span><span class="keyrow"><input id="sKey" type="password" autocomplete="off" spellcheck="false" placeholder="${key.set ? "Saved, " + esc(key.hint) + ". Paste a new key to replace it." : "Paste your Meshpool key"}"><button class="btn" id="sShow" type="button">Show</button></span></label>
      <label class="field"><span>Base URL</span><input id="sBase" spellcheck="false" value="${esc(base.value || "")}" placeholder="${esc(base.default)}"><small>The OpenAI-compatible endpoint, ending in /v1. Default: ${esc(base.default)}</small></label>
      <label class="field"><span>Default model for new threads</span><select id="sModel"></select><small>Each thread can use its own model, from any provider below, in the model picker in the message box.</small></label>
      <div style="display:flex;gap:8px"><button class="btn primary" id="sSave">Save</button><button class="btn" id="sTest">Test connection</button></div><div class="res" id="sRes"></div>
      <h3 style="font-size:15px;margin:22px 0 4px">Providers</h3><p class="lead">Where models run. Connect as many as you like; each one's models show in the model picker. Claude and Google sign-ins run only in Claude Code and Gemini CLI, as Anthropic's and Google's terms require.</p>
      <ul class="rows2" id="sProvs">${[["chatgpt", "ChatGPT subscription", "Your ChatGPT plan, for every agent except Claude Code and Gemini CLI.", "signin"], ["claude", "Claude subscription", "Your Claude plan. Runs in Claude Code.", "signin"], ["google", "Gemini (Google sign-in)", "Your Google AI plan. Runs in Gemini CLI.", "signin"], ["ANTHROPIC_API_KEY", "Anthropic API key", "Claude models for every agent except Codex and Gemini CLI.", "key"], ["GEMINI_API_KEY", "Gemini API key", "Gemini models for every agent except Codex and Claude Code. Free at aistudio.google.com.", "key"]].map(([id, name, desc, kind]) => {
        if (kind === "signin") return `<li><span class="tg"><b>${name}</b><small>${desc}</small></span>${S.settings.signed_in?.[id] ? `<span class="badge added">Signed in</span><button class="btn" data-psign="${id}">Sign in again</button>` : `<button class="btn primary" data-psign="${id}">Sign in</button>`}</li>`;
        const f = (S.settings.fields || []).find(x => x.key === id) || {};
        return `<li><span class="tg"><b>${name}</b><small>${desc}</small><span class="keyrow" style="margin-top:6px"><input data-pkey="${id}" type="password" autocomplete="off" spellcheck="false" placeholder="${f.set ? "Saved, " + esc(f.hint) + ". Paste a new key to replace it." : "Paste the key"}"><button class="btn" data-pkeysave="${id}" type="button">Save</button></span></span>${f.set ? `<span class="badge added">Saved</span>` : ""}</li>`;
      }).join("")}</ul><div class="res" id="sProvRes"></div>`;
    const fill = () => { const ms = (S.models?.models || []); $("#sModel").innerHTML = ms.map(m => `<option value="${esc(m.id)}" ${m.id === S.models.default ? "selected" : ""}>${esc(modelName(m.id))}</option>`).join(""); };
    if (!S.models) await loadModels(); fill();
    $("#sProv").onchange = async () => {
      const v = $("#sProv").value;
      if (SIGNIN[v] && !S.settings.signed_in?.[v]) { $("#sProv").value = S.settings.provider || "meshpool"; RES("sRes").className = "res bad"; RES("sRes").textContent = `Sign in to ${SIGNIN[v].name} first under Agents (${harness(SIGNIN[v].harness).name}, Sign in), then choose it here.`; return; }
      try { S.settings = await post("/api/provider", {provider: v}); store("harness_code", null); store("harness_cowork", null); await loadHarnesses(); if (!S.cur) { S.draft.harness = modeHarness(); renderHero(); } renderBar(); RES("sRes").className = "res ok"; RES("sRes").textContent = "Saved. New threads use " + harness(modeHarness()).name + " on " + PROVIDERS.find(x => x[0] === v)[1] + "."; } catch (e) { RES("sRes").className = "res bad"; RES("sRes").textContent = e.message; }
    };
    $("#sShow").onclick = () => { const i = $("#sKey"); i.type = i.type === "password" ? "text" : "password"; $("#sShow").textContent = i.type === "password" ? "Show" : "Hide"; };
    $$("[data-psign]", b).forEach(x => x.onclick = async () => {
      const id = x.dataset.psign; try { const r = await post("/api/harness-login", {id}); RES("sProvRes").className = "res" + (r.ok ? "" : " bad"); RES("sProvRes").textContent = r.message; } catch (e) { RES("sProvRes").className = "res bad"; RES("sProvRes").textContent = e.message; return; }
      const t0 = Date.now(); const tick = async () => { if (!RES("sProvRes")) return; await loadSettings(); if (S.settings.signed_in?.[id]) { await loadHarnesses(); await loadModels(true); renderBar(); renderSection("general"); toast("Signed in."); return; } if (Date.now() - t0 < 600000) setTimeout(tick, 3000); };
      setTimeout(tick, 3000);
    });
    $$("[data-pkeysave]", b).forEach(x => x.onclick = async () => {
      const k = x.dataset.pkeysave, v = $(`[data-pkey="${k}"]`, b).value.trim(); if (!v) { $(`[data-pkey="${k}"]`, b).focus(); return; }
      try { await post("/api/settings", {values: {[k]: v}}); await loadSettings(); await loadHarnesses(); await loadModels(true); renderBar(); renderSection("general"); toast("Saved. Its models are in the model picker."); } catch (e) { RES("sProvRes").className = "res bad"; RES("sProvRes").textContent = e.message; }
    });
    const save = async () => {
      const values = {MESHPOOL_BASE_URL: $("#sBase").value.trim()}; if ($("#sKey").value.trim()) values.MESHPOOL_API_KEY = $("#sKey").value.trim();
      await post("/api/settings", {values}); if ($("#sModel").value) await post("/api/models/default", {id: $("#sModel").value});
      await loadSettings(); await loadModels(true); fill();
    };
    $("#sSave").onclick = async () => {
      try {
        await save(); RES("sRes").className = "res"; RES("sRes").textContent = "Saved. Checking the connection…"; renderBar();
        const r = await post("/api/test"); $("#kdot").className = "kdot" + (r.ok ? " ok" : " bad");
        RES("sRes").className = "res " + (r.ok ? "ok" : "bad"); RES("sRes").textContent = r.ok ? "Saved. The connection works." : "Saved, but " + connMsg(r.detail).replace(/^./, c => c.toLowerCase());
      } catch (e) { RES("sRes").className = "res bad"; RES("sRes").textContent = e.message; }
    };
    $("#sTest").onclick = async () => {
      RES("sRes").className = "res"; RES("sRes").textContent = "Testing...";
      try { await save(); const r = await post("/api/test"); RES("sRes").className = "res " + (r.ok ? "ok" : "bad"); RES("sRes").textContent = r.ok ? `Connected. ${r.model} answered in ${r.seconds} s. ${S.models.models.length} models available.` : connMsg(r.detail); renderBar(); }
      catch (e) { RES("sRes").className = "res bad"; RES("sRes").textContent = e.message; }
    };
  } else if (sec === "models") {
    if (!S.models) await loadModels();
    const M = S.models;
    b.innerHTML = `${close}<h3>Models</h3><p class="lead">Every model your key can use on ${esc(M.base_url)}. Star the ones you use most; they show first in the picker.</p>
      <div style="display:flex;gap:8px;margin-bottom:12px"><input id="smq" class="btn" style="flex:1;text-align:left;font-weight:400" placeholder="Search models"><button class="btn" id="smr">${ICON.refresh} Refresh</button></div>
      ${M.error ? `<div class="res bad">${esc(M.error)}</div>` : ""}<ul class="rows2" id="sml"></ul>`;
    const draw = () => {
      const q = $("#smq").value.trim().toLowerCase();
      $("#sml").innerHTML = M.models.filter(m => !q || m.id.toLowerCase().includes(q)).map(m => `<li><span class="tg"><b title="${esc(m.id)}">${esc(modelName(m.id))}</b><small>${esc(m.provider)}${m.context ? ` · ${fmt(m.context)} context` : ""}${(m.caps || []).length ? " · " + m.caps.join(", ") : ""}</small></span>
        ${m.id === M.default ? `<span class="badge modified">Default</span>` : `<button class="btn" data-def="${esc(m.id)}">Make default</button>`}
        <button class="iconbtn" data-fav="${esc(m.id)}" title="Favorite" style="color:${M.favorites.includes(m.id) ? "var(--warn)" : "var(--ink-3)"}">${ICON.star}</button></li>`).join("") || `<li class="pnote">No models.</li>`;
      $$("[data-def]", b).forEach(x => x.onclick = async () => { S.models = await post("/api/models/default", {id: x.dataset.def}); Object.assign(M, S.models); draw(); renderBar(); });
      $$("[data-fav]", b).forEach(x => x.onclick = async () => { S.models = await post("/api/models/favorite", {id: x.dataset.fav, on: !M.favorites.includes(x.dataset.fav)}); Object.assign(M, S.models); draw(); });
    };
    $("#smq").oninput = draw; $("#smr").onclick = async () => { await loadModels(true); Object.assign(M, S.models); renderSection("models"); }; draw();
  } else if (sec === "appearance") {
    const keys = [["Esc", "Stop the running task (or close what is open)"], [`${MOD}N`, "New thread"], [`${MOD}K`, "Search threads"], [`${MOD}B`, "Show or hide the sidebar"], [`${MOD}E`, "Show or fold steps"],
      [`${MOD}⇧D`, "Diff panel"], [`${MOD}J`, "Terminal"], [`${MOD},`, "Settings"], ["Enter", "Send"], ["Shift+Enter", "New line"],
      ["/", "Commands"], ["$", "Skills"], ["Esc", "Close a menu or dialog"], [`${MOD}S`, "Save the open file"]];
    b.innerHTML = `${close}<h3>Appearance and shortcuts</h3><p class="lead">How Meshpool looks on this computer.</p>
      <label class="field"><span>Theme</span><select id="apTheme"><option value="system">Match the system</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
      <label class="field"><span>Text size</span><select id="apSize"><option value="small">Small</option><option value="normal">Normal</option><option value="large">Large</option></select></label>
      <label class="field"><span>Steps</span><select id="apSteps"><option value="1">Show every step as the agent works</option><option value="0">Fold steps into a summary</option></select></label>
      <h3 style="font-size:15px;margin:18px 0 4px">Keyboard shortcuts</h3><div class="keys">${keys.map(([k, d]) => `<kbd>${esc(k)}</kbd><span>${esc(d)}</span>`).join("")}</div>`;
    $("#apTheme").value = store("theme") || "system"; $("#apSize").value = store("size") || "normal"; $("#apSteps").value = S.steps ? "1" : "0";
    $("#apTheme").onchange = () => { store("theme", $("#apTheme").value); applyAppearance(); };
    $("#apSize").onchange = () => { store("size", $("#apSize").value); applyAppearance(); };
    $("#apSteps").onchange = () => { if (($("#apSteps").value === "1") !== !!S.steps) toggleSteps(); };
  } else if (sec === "personal") {
    const a = await api("/api/assistant");
    b.innerHTML = `${close}<h3>Instructions and memory</h3><p class="lead">Every agent reads these at the start of each new thread, so you do not repeat yourself.</p>
      <label class="field" style="max-width:none"><span>Instructions for every thread</span><textarea id="paIns" rows="6" placeholder="For example: Be direct and concise. No em dashes. I work in strategy and finance in the GCC.">${esc(a.instructions)}</textarea><small>How you want answers: tone, format, what to avoid, context about your work.</small></label>
      <label class="switchrow"><input type="checkbox" id="paInsOn" ${a.settings.instructions_on ? "checked" : ""}> Use these instructions</label>
      <label class="field" style="max-width:none;margin-top:14px"><span>Memory</span><textarea id="paMem" rows="8" placeholder="Agents add facts here when you tell them something lasting, or ask them to remember.">${esc(a.memory)}</textarea><small>One fact per line. Edit or delete anything. Agents never save keys or passwords.</small></label>
      <label class="switchrow"><input type="checkbox" id="paMemOn" ${a.settings.memory_on ? "checked" : ""}> Let agents use and add to memory</label>
      <div style="display:flex;gap:8px;margin-top:12px"><button class="btn primary" id="paSave">Save</button></div><div class="res" id="paRes"></div>
      <p class="lead" style="margin-top:18px">Coming from ChatGPT, Claude, Claude Code or Codex? <button class="linkbtn" id="paImport">Import your chats and context</button></p>`;
    $("#paImport").onclick = () => renderSection("import");
    $("#paSave").onclick = async () => {
      try {
        await post("/api/assistant", {action: "instructions", text: $("#paIns").value});
        await post("/api/assistant", {action: "memory", text: $("#paMem").value});
        await post("/api/assistant", {action: "settings", memory_on: $("#paMemOn").checked, instructions_on: $("#paInsOn").checked});
        RES("paRes").className = "res ok"; RES("paRes").textContent = "Saved. New threads use it.";
      } catch (e) { RES("paRes").className = "res bad"; RES("paRes").textContent = e.message; }
    };
  } else if (sec === "import") {
    b.innerHTML = `${close}<div id="impBox"></div>`;
    await contextPanel($("#impBox", b));
  } else if (sec === "scheduled") {
    const draw = a => {
      const proj = S.projects.map(p => `<option value="${esc(p.path)}">${esc(p.name)}</option>`).join("");
      b.innerHTML = `${close}<h3>Scheduled tasks</h3><p class="lead">Work that runs by itself: a morning news brief, a weekly pipeline report, a reminder. Each run opens a new thread and notifies you when it is done. Tasks run while Meshpool is open on this computer. You can also ask any agent to schedule something.</p>
        <ul class="rows2">${a.tasks.map(t => `<li><span class="tg"><b>${esc(t.name)}</b><small>${esc(t.describe)} · ${t.kind === "code" ? "Code" : "Work"}</small><small class="clip2">${esc(t.prompt)}</small></span>
          <label class="switch" title="On or off"><input type="checkbox" data-ton="${esc(t.id)}" ${t.enabled ? "checked" : ""} aria-label="Run ${esc(t.name)}"><span></span></label>
          <button class="btn" data-trun="${esc(t.id)}">Run now</button>${t.last_thread ? `<button class="btn" data-topen="${esc(t.last_thread)}">Last run</button>` : ""}<button class="iconbtn" data-tdel="${esc(t.id)}" aria-label="Delete">${ICON.x}</button></li>`).join("") || `<li class="pnote">No scheduled tasks yet.</li>`}</ul>
        <h3 style="font-size:15px;margin:18px 0 6px">New scheduled task</h3>
        <div class="sform">
          <input id="tsName" placeholder="Name, for example Morning AI news" spellcheck="false">
          <textarea id="tsPrompt" rows="3" placeholder="What should it do? For example: Find the 5 most important AI infrastructure news stories from the last 24 hours and summarize each in two sentences with links."></textarea>
          <div class="srow"><select id="tsWhen"><option value="daily">Every day</option><option value="weekdays">Every weekday</option><option value="weekly">Every week</option><option value="hourly">Every hour</option><option value="once">Once</option></select>
          <select id="tsDay">${["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map(d => `<option value="${d}">${d[0].toUpperCase() + d.slice(1)}</option>`).join("")}</select>
          <input id="tsTime" type="time" value="08:30"><input id="tsAt" type="datetime-local">
          <select id="tsKind"><option value="cowork">Work</option><option value="code">Code</option></select>
          <select id="tsFolder"><option value="">Default folder</option>${proj}</select></div>
          <button class="btn primary" id="tsAdd">Add task</button></div><div class="res" id="tsRes"></div>`;
      done();
      const sync = () => { const w = $("#tsWhen").value; $("#tsDay").hidden = w !== "weekly"; $("#tsTime").hidden = !["daily", "weekdays", "weekly"].includes(w); $("#tsAt").hidden = w !== "once"; };
      $("#tsWhen").onchange = sync; sync();
      const act = async body => { try { draw(await post("/api/assistant", body)); } catch (e) { RES("tsRes").className = "res bad"; RES("tsRes").textContent = e.message; } };
      $("#tsAdd").onclick = () => !$("#tsPrompt").value.trim() ? (RES("tsRes").className = "res bad", RES("tsRes").textContent = "Write what the task should do.", $("#tsPrompt").focus()) : act({action: "task_add", name: $("#tsName").value.trim(), prompt: $("#tsPrompt").value.trim(), when: $("#tsWhen").value, day: $("#tsDay").value, time: $("#tsTime").value, at: $("#tsAt").value, kind: $("#tsKind").value, folder: $("#tsFolder").value});
      $$("[data-ton]", b).forEach(x => x.onchange = () => act({action: "task_update", id: x.dataset.ton, enabled: x.checked}));
      $$("[data-tdel]", b).forEach(x => x.onclick = async () => { if (await ask({title: "Delete this scheduled task?", text: "It won't run again. Threads from earlier runs stay.", ok: "Delete task", danger: true})) act({action: "task_delete", id: x.dataset.tdel}); });
      $$("[data-trun]", b).forEach(x => x.onclick = async () => { try { const r = await post("/api/assistant", {action: "task_run", id: x.dataset.trun}); $(".scrim")?.remove(); await loadThreads(); openThread(r.thread); } catch (e) { toast(e.message); } });
      $$("[data-topen]", b).forEach(x => x.onclick = () => { $(".scrim")?.remove(); openThread(x.dataset.topen); });
    };
    draw(await api("/api/assistant")); return;
  } else if (sec === "about") {
    const a = await api("/api/about");
    b.innerHTML = `${close}<img class="logo" src="${logoSrc()}" alt="Meshpool" style="height:30px;margin-bottom:12px"><h3>Meshpool ${esc(a.version)}${/^0\./.test(a.version) ? " beta" : ""}</h3>
      <p class="lead">Made by ${esc(a.author)}. <a href="https://meshpool.ai" target="_blank" rel="noopener">meshpool.ai</a> · <a href="https://github.com/hamzaaziada/meshpool-harness/releases" target="_blank" rel="noopener">All releases</a></p>
      <div class="updbox" id="updBox"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn" id="updCheck">Check for updates</button><span class="res" id="updRes" style="margin:0"></span></div><div id="updMore"></div></div>
      <h3 style="font-size:15px;margin:16px 0 6px">License</h3><pre class="lic">${esc(a.notice)}</pre>
      <details style="margin-top:10px"><summary class="linkbtn" style="display:inline">Apache License 2.0, full text</summary><pre class="lic">${esc(a.license)}</pre></details>
      <details style="margin-top:8px"><summary class="linkbtn" style="display:inline">Third-party software</summary><pre class="lic">${esc(a.third_party)}</pre></details>`;
    updatePanel(S.upd);
    $("#updCheck").onclick = async () => { RES("updRes").className = "res"; RES("updRes").textContent = "Checking..."; updatePanel(await checkUpdate(true)); };
  } else if (sec === "web") {
    const keys = ["BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY", "SEARXNG_URL"];
    b.innerHTML = `${close}<h3>Web search</h3><p class="lead">Every agent can search the web, read pages and PDFs, and research a question across many sources at once, without opening a browser. It works with no setup through free search engines. For heavy research, add a key below: searches get faster and are never limited.</p>
      ${keys.map(k => { const f = get(k); return `<label class="field"><span>${esc(f.label)}</span>${f.key === "SEARXNG_URL" ? `<input data-wk="${k}" spellcheck="false" value="${esc(f.value || "")}" placeholder="http://localhost:8888">` : `<input data-wk="${k}" type="password" autocomplete="off" spellcheck="false" placeholder="${f.set ? "Saved, " + esc(f.hint) : "Not set"}">`}<small>${esc(f.help)}</small></label>`; }).join("")}
      <div style="display:flex;gap:8px"><button class="btn primary" id="wSave">Save</button><button class="btn" id="wTest">Test search</button></div><div class="res" id="wRes"></div>`;
    $("#wSave").onclick = async () => { const values = {}; $$("[data-wk]", b).forEach(x => { if (x.value.trim() || x.dataset.wk === "SEARXNG_URL") values[x.dataset.wk] = x.value.trim(); }); try { await post("/api/settings", {values}); RES("wRes").className = "res ok"; RES("wRes").textContent = "Saved."; } catch (e) { RES("wRes").className = "res bad"; RES("wRes").textContent = e.message; } };
    $("#wTest").onclick = async () => { RES("wRes").className = "res"; RES("wRes").textContent = "Searching..."; try { const r = await post("/api/web-test"); RES("wRes").className = "res " + (r.ok ? "ok" : "bad"); RES("wRes").textContent = r.ok ? `Search works: ${r.count} results in ${r.seconds} s, first: ${r.first}` : r.error; } catch (e) { RES("wRes").className = "res bad"; RES("wRes").textContent = e.message; } };
  } else if (sec === "connectors") {
    const cats = ["Connected", "Engineering", "Work and documents", "Sales and marketing", "Finance and data", "Yours"];
    const redirect = `${location.origin}/oauth/callback`;
    const say = (t, cls = "") => { RES("cnRes").className = "res " + cls; RES("cnRes").textContent = t; };
    const clientForm = c => `<span class="ownclient" data-cf="${esc(c.id)}"><small>${esc(c.client_help || "This app only signs in apps it knows. Create an OAuth app in its developer settings with the redirect URL below, then paste its client ID (and secret, if it gives you one).")}</small>
      <small>Redirect URL: <code>${esc(redirect)}</code></small>
      <span class="keyrow" style="margin-top:6px"><input data-cid="${esc(c.id)}" placeholder="Client ID" autocomplete="off" spellcheck="false"><input type="password" data-csec="${esc(c.id)}" placeholder="Client secret (if any)" autocomplete="off" spellcheck="false"><button class="btn primary" data-client="${esc(c.id)}">Connect</button></span></span>`;
    const row = c => {
      const cat = c.connected ? "Connected" : c.custom ? "Yours" : c.category || "Yours";
      const own = c.needs_client && !c.own_client && !c.connected;
      const btn = c.connected ? `<label class="switch" title="Use in new threads"><input type="checkbox" data-on="${esc(c.id)}" ${c.enabled ? "checked" : ""} aria-label="Use ${esc(c.name)}"><span></span></label><button class="btn" data-test="${esc(c.id)}">Test</button><button class="btn" data-off="${esc(c.id)}">${c.auth === "none" ? "Turn off" : "Disconnect"}</button>`
        : c.auth === "none" ? `<button class="btn primary" data-turnon="${esc(c.id)}">Turn on</button>`
        : c.auth === "oauth" && !own ? `<button class="btn primary" data-go="${esc(c.id)}">${c.expired ? "Sign in again" : "Connect"}</button>` : "";
      return `<li data-id="${esc(c.id)}" data-cat="${esc(cat)}" data-text="${esc((c.name + " " + c.description + " " + (c.category || "")).toLowerCase())}"><span class="tg"><b>${esc(c.name)}</b>${c.connected ? ` <span class="badge added">${c.auth === "none" ? "On" : "Connected"}</span>` : c.expired ? ` <span class="badge deleted">Sign in again</span>` : c.auth === "none" ? ` <span class="badge">No sign-in</span>` : ""}<small>${esc(c.description)}</small>
        ${c.id === "github" && !c.connected ? `<span class="row" style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">${c.can_sign_in ? `<button class="btn primary" data-ghdev="1">Sign in with GitHub</button>` : ""}<button class="btn ${c.can_sign_in ? "" : "primary"}" data-ghfound="1">Use the GitHub login on this computer</button><a class="btn" href="https://github.com/settings/tokens/new?scopes=repo,workflow,read:org&amp;description=Meshpool" target="_blank" rel="noopener">Create a token</a></span>` : ""}
        ${c.auth === "token" && !c.connected ? `<span class="keyrow" style="margin-top:6px"><input type="password" data-tok="${esc(c.id)}" placeholder="Paste a token" autocomplete="off" spellcheck="false"><button class="btn" data-save="${esc(c.id)}">Save</button></span><small>${esc(c.token_help)}</small>` : ""}
        ${own || S.cnNeedClient === c.id ? clientForm(c) : ""}</span>${btn}</li>`;
    };
    const filter = () => {
      const q = ($("#cnq")?.value || "").trim().toLowerCase(), cat = S.cnCat || "";
      $$("#cnl > li", b).forEach(li => { li.hidden = (q && !li.dataset.text.includes(q)) || (cat && li.dataset.cat !== cat); });
      $$("[data-cncat]", b).forEach(x => x.classList.toggle("primary", x.dataset.cncat === cat));
    };
    const signIn = (r, id) => {                       // poll until the browser sign-in finished
      if (r.needs_client) { S.cnNeedClient = r.needs_client; draw(r.connectors); say(r.message); return; }
      RES("cnRes").className = "res"; RES("cnRes").innerHTML = `Finish signing in in your browser. <a href="${esc(r.url)}" target="_blank" rel="noopener">Open the sign-in page again</a>`;
      const t0 = Date.now();
      const tick = async () => {
        if (!$("#cnl")) return;
        const l = (await api("/api/connectors")).connectors;
        if (l.find(c => c.id === id)?.connected) { S.cnNeedClient = ""; draw(l); say(`${l.find(c => c.id === id).name} is connected. New threads can use it.`, "ok"); return; }
        if (Date.now() - t0 < 300000) setTimeout(tick, 2000);
      };
      setTimeout(tick, 2000);
    };
    const draw = rows => {
      const order = c => (c.connected ? 0 : 1);
      b.innerHTML = `${close}<h3>Connectors</h3><p class="lead">Connect the apps you work in. Every agent can then use them as tools. Sign-in happens between this computer and the app itself: Meshpool keeps the access on this computer only and runs no server in between.</p>
        <div class="cnbar"><input id="cnq" placeholder="Search ${rows.length} apps" spellcheck="false" value="${esc(S.cnQ || "")}"><span class="cnchips"><button class="btn${S.cnCat ? "" : " primary"}" data-cncat="">All</button>${cats.filter(k => rows.some(c => (c.connected ? "Connected" : c.custom ? "Yours" : c.category) === k)).map(k => `<button class="btn" data-cncat="${esc(k)}">${esc(k)}</button>`).join("")}</span></div>
        <div class="res" id="cnRes"></div>
        <ul class="rows2" id="cnl">${[...rows].sort((x, z) => order(x) - order(z)).map(row).join("")}</ul>
        <h3 style="font-size:15px;margin:18px 0 4px">Add another app</h3><p class="lead" style="font-size:12.5px">Any app with a remote MCP server works. Paste its address; Meshpool works out whether it needs nothing, a token or a browser sign-in. For a key that goes in another header, paste it as <code>Header-Name: value</code>.</p>
        <div class="cform"><input id="cxName" placeholder="Name" spellcheck="false"><input id="cxUrl" placeholder="https://mcp.example.com/mcp" spellcheck="false"><input id="cxTok" type="password" placeholder="Token or API key (optional)" autocomplete="off"><button class="btn primary" id="cxAdd">Add</button></div>
        <details style="margin-top:8px"><summary class="linkbtn" style="display:inline;padding-left:0">More options: your own sign-in client</summary><span class="keyrow" style="margin-top:6px"><input id="cxCid" placeholder="Client ID" autocomplete="off" spellcheck="false"><input id="cxSec" type="password" placeholder="Client secret" autocomplete="off" spellcheck="false"></span><small class="muted">For apps that only sign in apps they know. Redirect URL: <code>${esc(redirect)}</code></small></details>`;
      done();
      $("#cnq").oninput = () => { S.cnQ = $("#cnq").value; filter(); };
      $$("[data-cncat]", b).forEach(x => x.onclick = () => { S.cnCat = x.dataset.cncat; filter(); });
      filter();
      $("#cxAdd").onclick = async () => {
        const url = $("#cxUrl").value.trim(); if (!url) { $("#cxUrl").focus(); return; }
        $("#cxAdd").disabled = true; say("Checking the server...");
        const r = await act({action: "custom", name: $("#cxName").value.trim(), url, token: $("#cxTok").value.trim(), client_id: $("#cxCid").value.trim(), client_secret: $("#cxSec").value.trim()}); $("#cxAdd").disabled = false;
        if (!r) return;
        S.connCache = r.connectors; S.cnCat = ""; draw(r.connectors);
        if (r.url || r.needs_client) signIn(r, r.id); else say(r.message || "Added. New threads can use it.", "ok");
      };
      const act = async body => { try { const r = await post("/api/connectors", body); return r; } catch (e) { say(e.message, "bad"); return null; } };
      $$("[data-go]", b).forEach(x => x.onclick = async () => {
        x.disabled = true; x.textContent = "Opening sign-in...";
        const r = await act({action: "connect", id: x.dataset.go});
        if (!r) { x.disabled = false; x.textContent = "Connect"; return; }
        signIn(r, x.dataset.go);
      });
      $$("[data-client]", b).forEach(x => x.onclick = async () => {
        const id = x.dataset.client, cid = $(`[data-cid="${id}"]`, b).value.trim();
        if (!cid) { $(`[data-cid="${id}"]`, b).focus(); return; }
        const r = await act({action: "client", id, client_id: cid, client_secret: $(`[data-csec="${id}"]`, b).value.trim()});
        if (r) signIn(r, id);
      });
      $$("[data-turnon]", b).forEach(x => x.onclick = async () => {
        x.disabled = true;
        const r = await act({action: "enable", id: x.dataset.turnon, on: true}); if (!r) { x.disabled = false; return; }
        draw(r.connectors); say("Checking it...");
        const t = await act({action: "check", id: x.dataset.turnon}); if (t) say(t.ok ? `On: ${t.tools.length} tools. New threads can use it.` : t.error, t.ok ? "ok" : "bad");
      });
      $$("[data-test]", b).forEach(x => x.onclick = async () => {
        x.disabled = true; say("Testing...");
        const t = await act({action: "check", id: x.dataset.test}); x.disabled = false;
        if (t) say(t.ok ? `Works: ${t.tools.length} tools (${t.tools.slice(0, 6).join(", ")}${t.tools.length > 6 ? ", ..." : ""}).` : t.error, t.ok ? "ok" : "bad");
      });
      $("[data-ghfound]", b)?.addEventListener("click", async e => {
        e.target.disabled = true; RES("cnRes").className = "res"; RES("cnRes").textContent = "Looking for a GitHub login on this computer...";
        const r = await act({action: "github_found", id: "github"}); e.target.disabled = false;
        if (r) { S.connCache = r.connectors; draw(r.connectors); RES("cnRes").className = "res ok"; RES("cnRes").textContent = r.message; }
      });
      $("[data-ghdev]", b)?.addEventListener("click", async () => {
        const r = await act({action: "github_device", id: "github"}); if (!r) return;
        RES("cnRes").className = "res"; RES("cnRes").innerHTML = `Enter this code on GitHub: <b class="ucode">${esc(r.user_code)}</b> <a href="${esc(r.url)}" target="_blank" rel="noopener">Open github.com/login/device</a>`;
        const tick = async () => {
          if (!$("#cnl")) return;
          let q; try { q = await post("/api/connectors", {action: "github_poll", id: "github"}); } catch (e) { return; }
          if (q.status === "done") { S.connCache = q.connectors; draw(q.connectors); RES("cnRes").className = "res ok"; RES("cnRes").textContent = `Connected to GitHub${q.login ? " as " + q.login : ""}.`; return; }
          if (q.status === "failed") { RES("cnRes").className = "res bad"; RES("cnRes").textContent = q.error; return; }
          setTimeout(tick, (r.interval || 5) * 1000);
        };
        setTimeout(tick, (r.interval || 5) * 1000);
      });
      $$("[data-save]", b).forEach(x => x.onclick = async () => { const v = $(`[data-tok="${x.dataset.save}"]`, b).value.trim(); if (!v) return; const r = await act({action: "token", id: x.dataset.save, token: v}); if (r) { S.connCache = r.connectors; draw(r.connectors); const t = await act({action: "check", id: x.dataset.save}); if (t) say(t.ok ? `Connected: ${t.tools.length} tools.` : t.error, t.ok ? "ok" : "bad"); } });
      $$("[data-on]", b).forEach(x => x.onchange = async () => { const r = await act({action: "enable", id: x.dataset.on, on: x.checked}); if (r) draw(r.connectors); });
      $$("[data-off]", b).forEach(x => x.onclick = async () => { if (!await ask({title: "Disconnect this app?", text: "Threads stop using it. You can connect it again any time.", ok: "Disconnect", danger: true})) return; const r = await act({action: "disconnect", id: x.dataset.off}); if (r) draw(r.connectors); });
    };
    draw((await api("/api/connectors")).connectors);
    return;
  } else if (sec === "skills") {
    await loadSkills();
    b.innerHTML = `${close}<h3>Skills</h3><p class="lead">Skills are instructions the agent loads when a task matches, or when you type <code>$name</code> in the message box. They use the same SKILL.md format as Claude and Codex, and Meshpool shares them with every agent.</p>
      <div class="dropzone" id="skDrop" tabindex="0" role="button">Drop a skill folder, a .zip or a SKILL.md here, or click to choose files<input type="file" id="skFile" multiple hidden></div>
      <ul class="rows2" id="skl"></ul>
      <details><summary class="btn" style="display:inline-block">Write a new skill</summary>
        <div style="margin-top:12px"><label class="field"><span>Name</span><input id="skName" placeholder="e.g. release-notes"></label>
        <label class="field"><span>When to use it (one line)</span><input id="skDesc" placeholder="Write release notes from the commits since the last tag."></label>
        <label class="field"><span>Instructions</span><textarea id="skBody" class="code" placeholder="Step-by-step instructions the agent follows."></textarea></label>
        <button class="btn primary" id="skCreate">Create skill</button></div></details>
      <div class="res" id="skRes"></div><div style="display:flex;gap:8px;margin-top:10px"><button class="btn" id="skImp">Import from Claude, Codex and other apps</button><button class="btn" id="skOpen">Open the skills folder</button></div><div id="skImpBox" style="margin-top:10px"></div>`;
    $("#skImp").onclick = () => importPanel($("#skImpBox"), r => { if (r) renderSection("skills"); });
    const drawSk = () => {
      $("#skl").innerHTML = S.skills.map(s => `<li><label class="switch"><input type="checkbox" data-id="${esc(s.id)}" ${s.enabled ? "checked" : ""} aria-label="Use ${esc(s.name)}"><span></span></label><span class="tg"><b>$${esc(s.name)}</b><small>${esc(s.description)}</small></span><button class="btn" data-edit="${esc(s.id)}">Edit</button><button class="iconbtn" data-rm="${esc(s.id)}" aria-label="Remove ${esc(s.name)}">${ICON.x}</button></li>`).join("") || `<li class="pnote">No skills yet.</li>`;
      $$("#skl input[data-id]").forEach(i => i.onchange = () => skillAct({action: "toggle", id: i.dataset.id, on: i.checked}));
      $$("#skl [data-rm]").forEach(x => x.onclick = async () => { if (await ask({title: "Remove this skill?", text: "Its folder is deleted from your skills folder. This can't be undone.", ok: "Remove skill", danger: true})) skillAct({action: "remove", id: x.dataset.rm}); });
      $$("#skl [data-edit]").forEach(x => x.onclick = async () => {
        const sk = await api("/api/skill?id=" + encodeURIComponent(x.dataset.edit));
        const li = x.closest("li"); li.insertAdjacentHTML("afterend", `<li><div style="flex:1"><textarea class="code" id="skEd">${esc(sk.text)}</textarea><div style="display:flex;gap:6px;margin-top:6px"><button class="btn primary" id="skEdSave">Save</button><button class="btn" id="skEdCancel">Cancel</button></div></div></li>`);
        $("#skEdSave").onclick = () => skillAct({action: "update", id: sk.id, text: $("#skEd").value});
        $("#skEdCancel").onclick = drawSk;
      });
    };
    const skillAct = async body => { try { S.skills = (await post("/api/skills", body)).skills; drawSk(); RES("skRes").className = "res ok"; RES("skRes").textContent = body.action === "upload" ? "Added." : body.action === "create" ? "Created." : body.action === "update" ? "Saved." : ""; } catch (e) { RES("skRes").className = "res bad"; RES("skRes").textContent = e.message; } };
    drawSk();
    const upload = async (files, paths) => {
      const list = [];
      for (let i = 0; i < files.length; i++) { const f = files[i]; const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); }); list.push({name: (paths && paths[i]) || f.webkitRelativePath || f.name, data}); }
      skillAct({action: "upload", files: list});
    };
    const dz = $("#skDrop");
    dz.onclick = () => $("#skFile").click(); $("#skFile").onchange = () => upload([...$("#skFile").files]);
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); }; dz.ondragleave = () => dz.classList.remove("over");
    dz.ondrop = async e => {
      e.preventDefault(); dz.classList.remove("over");
      const items = [...(e.dataTransfer.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
      if (items.some(x => x.isDirectory)) {
        const files = [], paths = [];
        const walk = (entry, prefix) => new Promise(res => {
          if (entry.isFile) entry.file(f => { files.push(f); paths.push(prefix + f.name); res(); });
          else { const rd = entry.createReader(); const all = []; const read = () => rd.readEntries(async ents => { if (!ents.length) { for (const en of all) await walk(en, prefix + entry.name + "/"); res(); } else { all.push(...ents); read(); } }); read(); }
        });
        for (const it of items) await walk(it, "");
        upload(files, paths);
      } else upload([...e.dataTransfer.files]);
    };
    $("#skCreate").onclick = () => { if (!$("#skName").value.trim() || !$("#skBody").value.trim()) { RES("skRes").className = "res bad"; RES("skRes").textContent = "Give the skill a name and instructions."; return; } skillAct({action: "create", name: $("#skName").value, description: $("#skDesc").value, body: $("#skBody").value}); };
    $("#skOpen").onclick = () => post("/api/skills", {action: "open"});
  } else if (sec === "plugins") { await drawPlugins(b, close);
  } else if (sec === "harnesses") {
    await loadHarnesses();
    const ex = S.hInfo.examples || [];
    b.innerHTML = `${close}<h3>Agents</h3><p class="lead">The agent is who does the work in a thread. What it runs on is your provider (Settings, Account): ${esc((S.settings.providers || {})[S.settings.provider || "meshpool"] || "")}. Meshpool Auto picks a good agent for each task. Each agent runs on the provider of the model you pick in the message box: Codex on an API key or ChatGPT, Claude Code on a Claude sign-in or an Anthropic key, Gemini CLI on a Google sign-in or a Gemini key. Other agents can be added too.</p>
      <div class="dflt">${[["cowork", "Work"], ["code", "Code"]].map(([m, lb]) => `<label class="field"><span>Default for ${lb}</span><select data-dm="${m}">${S.harnesses.filter(h => h.available !== false && !h.error && h.ready !== false).map(h => `<option value="${esc(h.id)}" ${h.id === S.defaults[m] ? "selected" : ""}>${esc(h.name)}${h.mode !== m && h.type !== "auto" ? " · made for " + (h.mode === "code" ? "Code" : "Work") : ""}</option>`).join("")}</select><small>${m === "code" ? "Meshpool Code and Pi both solve most coding tasks; Pi is a little faster and cheaper, Meshpool Code has the full toolset (browser, connectors, skills)." : "Meshpool Work makes Word, Excel and PowerPoint files and does research. Hermes adds long-term memory for ongoing work."}</small></label>`).join("")}</div>
      <ul class="rows2">${S.harnesses.map(h => `<li><span class="tg"><b>${esc(h.name)}</b> <span class="badge modified">${h.type === "auto" ? "Any task" : h.mode === "cowork" ? "Work" : "Code"}</span>${h.available === false ? ` <span class="badge deleted">Not installed</span>` : h.ready === false ? ` <span class="badge deleted">Needs setup</span>` : ""}<small>${esc(h.description || "")}</small>${h.runs_on && h.ready !== false ? `<small>Runs on ${esc(h.runs_on)}.</small>` : ""}${h.ready === false && h.setup ? `<small>${esc(h.setup)}</small>` : ""}${h.install && h.available === false ? `<small>${esc(h.install)}</small>` : ""}</span>${h.login ? (h.signed_in ? `<span class="badge added">Signed in</span><button class="btn" data-login="${esc(h.id)}">Sign in again</button>` : `<button class="btn primary" data-login="${esc(h.id)}" ${h.available === false ? "disabled" : ""}>Sign in</button>`) : h.builtin || h.id === "hermes" ? "" : `<button class="iconbtn" data-rm="${esc(h.id)}" aria-label="Remove ${esc(h.name)}">${ICON.x}</button>`}</li>`).join("")}</ul>
      ${ex.length ? `<h3 style="font-size:15px;margin:18px 0 8px">Add one</h3><div style="display:flex;gap:6px;flex-wrap:wrap">${ex.map(e => `<button class="btn" data-ex="${esc(e.id)}">${esc(e.name)}</button>`).join("")}</div>` : ""}
      <div class="dropzone" id="hDrop" style="margin-top:14px" tabindex="0" role="button">Or drop an agent's .json file here<input type="file" id="hFile" accept=".json" hidden></div><div class="res" id="hRes"></div>`;
    const act = async body => { try { await post("/api/harnesses", body); await loadHarnesses(); renderSection("harnesses"); renderBar(); } catch (e) { RES("hRes").className = "res bad"; RES("hRes").textContent = e.message; } };
    $$("[data-rm]", b).forEach(x => x.onclick = () => act({remove: x.dataset.rm}));
    $$("[data-login]", b).forEach(x => x.onclick = async () => {
      try { const r = await post("/api/harness-login", {id: x.dataset.login}); RES("hRes").className = "res " + (r.ok ? "" : "bad"); RES("hRes").textContent = r.message; }
      catch (e) { RES("hRes").className = "res bad"; RES("hRes").textContent = e.message; return; }
      const id = x.dataset.login, t0 = Date.now();
      const tick = async () => { if (!RES("hRes")) return; await loadHarnesses(); if (harness(id).signed_in) { await loadSettings(); renderSection("harnesses"); renderBar(); toast(harness(id).name + " is signed in."); return; } if (Date.now() - t0 < 600000) setTimeout(tick, 3000); };
      setTimeout(tick, 3000);
    });
    $$("[data-dm]", b).forEach(x => x.onchange = async () => { await act({default_mode: x.dataset.dm, id: x.value}); store("harness_" + x.dataset.dm, null); if (!S.cur) { S.draft.harness = modeHarness(); renderBar(); renderHero(); } });
    $$("[data-ex]", b).forEach(x => x.onclick = () => act({example: x.dataset.ex}));
    const hd = $("#hDrop"); hd.onclick = () => $("#hFile").click();
    $("#hFile").onchange = async () => { for (const f of $("#hFile").files) await act({text: await f.text()}); };
    hd.ondragover = e => { e.preventDefault(); hd.classList.add("over"); }; hd.ondragleave = () => hd.classList.remove("over");
    hd.ondrop = async e => { e.preventDefault(); hd.classList.remove("over"); for (const f of e.dataTransfer.files) await act({text: await f.text()}); };
  } else if (sec === "mcp") {
    const m = await api("/api/mcp");
    b.innerHTML = `${close}<h3>Tools (MCP servers)</h3><p class="lead">Tools every agent can use. Browser use and computer use are built in. New threads pick up changes.</p>
      ${m.error ? `<div class="res bad">${esc(m.error)}</div>` : ""}
      <ul class="rows2">${m.servers.map(s => `<li><label class="switch"><input type="checkbox" data-n="${esc(s.name)}" ${s.enabled ? "checked" : ""} aria-label="Use ${esc(s.name)}"><span></span></label><span class="tg"><b>${esc(s.name)}</b><small>${esc(s.description)}</small><details class="tech"><summary>Technical details</summary><code>${esc(s.target)}</code></details></span></li>`).join("")}</ul>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="mTest">Check servers</button><button class="btn" id="mEdit">Edit the server list</button><button class="btn" id="mImp">Import from Claude, Codex, Cursor and others</button></div><div class="res" id="mRes"></div><div id="mImpBox" style="margin-top:10px"></div>
      <div id="mEd" hidden style="margin-top:12px"><textarea class="code" id="mText">${esc(m.text)}</textarea><button class="btn primary" id="mSave" style="margin-top:8px">Save mcp.json</button><p class="lead" style="font-size:12.5px;margin-top:8px">Same format as Claude Desktop. Write \${NAME} to use a value from the settings file, such as \${GITHUB_TOKEN}.</p></div>`;
    $$("input[data-n]", b).forEach(i => i.onchange = () => post("/api/mcp", {name: i.dataset.n, enabled: i.checked}).catch(e => toast(e.message)));
    $("#mEdit").onclick = () => $("#mEd").hidden = !$("#mEd").hidden;
    $("#mImp").onclick = () => importPanel($("#mImpBox"), r => { if (r) setTimeout(() => renderSection("mcp"), 1500); });
    $("#mSave").onclick = async () => { try { await post("/api/mcp", {text: $("#mText").value}); renderSection("mcp"); } catch (e) { RES("mRes").className = "res bad"; RES("mRes").textContent = e.message; } };
    $("#mTest").onclick = async () => { RES("mRes").className = "res"; RES("mRes").textContent = "Starting each server..."; try { const r = await post("/api/mcp/test", {workdir: curRoot()}); const rows = r.status.filter(s => s.status !== "disabled"); RES("mRes").className = "res " + (rows.some(s => s.status === "error") ? "bad" : "ok"); RES("mRes").innerHTML = rows.map(s => `${esc(s.name)}: ${s.status === "connected" ? `connected, ${s.tools.length} tools` : esc(s.error)}`).join("<br>") || "No servers are on."; } catch (e) { RES("mRes").className = "res bad"; RES("mRes").textContent = e.message; } };
    drawMcpLibrary(b);
  } else if (sec === "permissions") {
    const draw = async body => {
      const r = await post("/api/permissions", body || {});
      b.innerHTML = `${close}<h3>Permissions</h3><p class="lead">${esc(r.note || "")} Meshpool works only in the folders you add.${r.platform === "darwin" ? " When an agent opens a protected folder such as Desktop, Documents or Downloads for the first time, your Mac asks you to allow it." : ""}</p>
        ${r.platform === "darwin" ? `<button class="btn primary" id="permAll" style="margin-bottom:10px">Allow all</button>` : ""}
        <ul class="rows2">${r.rows.map(x => `<li><span class="dot ${x.ok === true ? "" : "wait"}" style="margin-top:7px;${x.ok === true ? "background:var(--add)" : ""}"></span><span class="tg"><b>${esc(x.name)}</b><small>${x.ok === true ? "Allowed. " : x.ok === false ? "Not allowed yet. " : ""}${esc(x.why)}</small></span>${x.ok === true || r.platform !== "darwin" ? "" : `<button class="btn" data-perm="${esc(x.id)}">${x.ok === false ? "Allow" : "Open settings"}</button>`}</li>`).join("")}</ul>
        <h3 style="font-size:15px;margin:18px 0 8px">Folders Meshpool works in</h3>
        <ul class="rows2">${S.projects.map(p => `<li><span class="tg"><b>${esc(p.name)}</b><small>${esc(p.path)}</small></span><button class="iconbtn" data-rmp="${esc(p.path)}" aria-label="Remove">${ICON.x}</button></li>`).join("") || `<li class="pnote">No folders yet.</li>`}</ul>
        <button class="btn" id="addP">${ICON.folder} Add a folder</button>`;
      done();
      $$("[data-perm]", b).forEach(x => x.onclick = () => draw({id: x.dataset.perm}));
      $("#permAll")?.addEventListener("click", () => draw({id: "all"}));
      $$("[data-rmp]", b).forEach(x => x.onclick = async () => { await post("/api/projects", {action: "remove", path: x.dataset.rmp}); await loadProjects(); renderSide(); draw(); });
      $("#addP").onclick = async () => { await pickProject(); await loadProjects(); draw(); };
    };
    await draw(); return;
  } else if (sec === "usage") {
    const u = await api("/api/usage"); const t = u.total;
    b.innerHTML = `${close}<h3>Usage</h3><p class="lead">How much Meshpool's own agents used on this computer. What it cost is on your <a href="https://meshpool.ai" target="_blank" rel="noopener">Meshpool account</a>.</p>
      <div class="statgrid"><div><b>${fmt(t.in)}</b><span>Input tokens</span></div><div><b>${t.in ? Math.round(100 * t.cached / t.in) : 0}%</b><span>Served from cache</span></div><div><b>${fmt(t.out)}</b><span>Output tokens</span></div><div><b>${fmt(t.calls)}</b><span>Model calls</span></div></div>
      <h3 style="font-size:15px;margin:0 0 8px">By model</h3><table class="t"><tr><th>Model</th><th>Threads</th><th>In</th><th>Cached</th><th>Out</th></tr>${Object.entries(u.by_model).map(([m, r]) => `<tr><td title="${esc(m)}">${esc(modelName(m))}</td><td class="n">${r.threads}</td><td class="n">${fmt(r.in)}</td><td class="n">${fmt(r.cached)}</td><td class="n">${fmt(r.out)}</td></tr>`).join("")}</table>`;
  } else if (sec === "terminal") {
    const c = S.settings.cli || {};
    b.innerHTML = `${close}<h3>Terminal command</h3><p class="lead">The same agent works in any terminal. Go to a project folder and type <code>meshpool</code>. Use <code>meshpool -p "..."</code> for one answer, and <code>meshpool acp</code> to plug Meshpool into Zed, JetBrains or other ACP apps.</p>
      <div class="res ${c.installed ? "ok" : ""}">${c.available ? (c.installed ? (c.new_window ? "Installed. Open a new terminal window and type meshpool." : "Installed. In any terminal window, type meshpool.") : "Not on your PATH yet.") : "The meshpool command comes with the Meshpool app for Mac and Windows. On other computers, install it with the one-line installer on the Meshpool download page."}</div>
      ${c.available && !c.installed ? `<button class="btn primary" id="cliBtn" style="margin-top:8px">Install the meshpool command</button>` : ""}`;
    $("#cliBtn")?.addEventListener("click", async () => { try { const r = await post("/api/install-cli"); $(".res", b).textContent = r.message; } catch (e) { $(".res", b).textContent = e.message; } });
  } else if (sec === "advanced") {
    const CH = {MH_AGENT_PERMISSIONS: [["ask", "Supervised"], ["edits", "Auto-accept edits"], ["auto", "Full access"]], MH_STREAM: [["1", "On"], ["0", "Off"]], MH_CACHE_KEY: [["1", "On"], ["0", "Off"]], MH_WIRE: [["chat", "Standard"], ["responses", "Responses API"]], MH_HARNESS_PROFILE: [["minimal", "Recommended"], ["", "Older, stricter checks"]], MH_AUTO_SUBSCRIPTIONS: [["0", "Off"], ["1", "On"]]};
    // Search keys and the GitHub token have their own pages (Web search, Connectors).
    const keys = F.filter(x => !["MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "MH_MODEL", "MH_PROVIDER", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "MH_ONBOARDED", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY", "SEARXNG_URL", "INSTANTLY_API_KEY"].includes(x.key));
    b.innerHTML = `${close}<h3>Advanced</h3><p class="lead">Most people never need to change these.</p>
      ${keys.map(x => CH[x.key] ? `<label class="field"><span>${esc(x.label)}</span><select id="ad_${x.key}">${CH[x.key].map(([v, l]) => `<option value="${esc(v)}" ${(x.value || x.default) === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select><small>${esc(x.help)}</small></label>`
        : `<label class="field"><span>${esc(x.label)}</span><input id="ad_${x.key}" ${x.key === "GITHUB_TOKEN" ? `type="password" placeholder="${x.set ? "Saved, " + esc(x.hint) : "ghp_... or github_pat_..."}"` : `value="${esc(x.value)}" placeholder="${esc(x.default)}"`} spellcheck="false"><small>${esc(x.help)}</small></label>`).join("")}
      <div style="display:flex;gap:8px"><button class="btn primary" id="adSave">Save</button><button class="btn" id="adData">Open the data folder</button></div><div class="res" id="adRes"></div>`;
    $("#adSave").onclick = async () => { const values = {}; keys.forEach(x => { values[x.key] = $("#ad_" + x.key).value; }); try { await post("/api/settings", {values}); RES("adRes").className = "res ok"; RES("adRes").textContent = "Saved."; } catch (e) { RES("adRes").className = "res bad"; RES("adRes").textContent = e.message; } };
    $("#adData").onclick = () => post("/api/open-data");
  }
  done();
}

/* ================================== first run ================================== */
const onbSteps = n => `<div class="onb-steps" aria-label="Step ${n} of 3">${[1, 2, 3].map(i => `<span class="${i <= n ? "on" : ""}"></span>`).join("")}<small>Step ${n} of 3</small></div>`;
// Providers: what the models run on. Agents (harnesses) are picked separately; the default agent follows the provider.
const PROVIDERS = [["meshpool", "Meshpool API key", "Recommended. Every model on one key, 20% cheaper."],
  ["custom", "Another API key", "OpenAI, Anthropic, OpenRouter or any OpenAI-compatible provider."],
  ["chatgpt", "ChatGPT subscription", "Your ChatGPT Plus, Pro or Business plan, for Meshpool's agents, Pi, Hermes and Codex. No API key needed."],
  ["claude", "Claude subscription, runs Claude Code", "Your Claude Pro or Max plan. Anthropic allows it for Claude Code only."]];
const SIGNIN = {chatgpt: {name: "ChatGPT", harness: "codex"}, claude: {name: "Claude", harness: "claude-code"}};
function showOnboarding() {
  if ($("#onb")) return;
  const o = document.createElement("div"); o.className = "onb"; o.id = "onb";
  o.innerHTML = `<div class="onb-card" id="onbKey">
    <img class="logo mlogo" src="${logoSrc()}" alt="Meshpool">
    ${onbSteps(1)}<h1>Welcome to Meshpool</h1><p>Choose what Meshpool runs on. It stays on this computer, and you can change it any time in Settings.</p>
    <div class="scope prov" role="radiogroup" aria-label="What Meshpool runs on">${PROVIDERS.map(([id, name, desc], i) => `<button class="scopeopt" data-prov="${id}" role="radio" aria-checked="${i === 0}"><b>${name}</b><small>${desc}</small></button>`).join("")}</div>
    <div id="oKeyBox">
      <p class="credit" id="oCredit">No key yet? <a href="https://meshpool.ai" target="_blank" rel="noopener">Get one at meshpool.ai</a> and run every model 20% cheaper.</p>
      <label class="field" id="oBaseF" style="max-width:none" hidden><span>Provider address (base URL)</span><input id="oBase" spellcheck="false" placeholder="https://api.openai.com/v1"></label>
      <label class="field" id="oModelF" style="max-width:none" hidden><span>Model</span><input id="oModel" spellcheck="false" placeholder="gpt-4.1-mini"></label>
      <label class="field" style="max-width:none"><span>API key</span><span class="keyrow"><input id="oKey" type="password" autocomplete="off" spellcheck="false" placeholder="Paste your key"><button class="btn" id="oShow" type="button">Show</button></span></label>
    </div>
    <div id="oSubBox" hidden><p class="credit" id="oSubTxt"></p><button class="btn primary" id="oSign">Sign in</button></div>
    <div class="res" id="oRes"></div>
    <div class="onb-go"><button class="bigbtn" id="oGo">Continue</button></div></div>
    <div class="onb-card" id="onbPerm" hidden></div>`;
  document.body.appendChild(o);
  let prov = "meshpool";
  const res = RES("oRes");
  const sub = () => SIGNIN[prov] && (S.harnesses || []).find(h => h.id === SIGNIN[prov].harness);
  const drawSub = () => {
    const h = sub(); const name = SIGNIN[prov]?.name || "";
    const what = prov === "claude" ? " It runs Claude Code, Anthropic's own agent: Anthropic's terms do not let other apps use a Claude sign-in, so Meshpool's own agents need an API key or ChatGPT." : " Meshpool Code, Meshpool Work, Pi, Hermes and Codex all run on it.";
    $("#oSign").hidden = !h || h.signed_in || h.available === false;
    $("#oSubTxt").textContent = !h ? "Loading…" : h.available === false ? `Signing in to ${name} needs Node.js. The desktop app includes it; otherwise install it from nodejs.org.`
      : (h.signed_in ? `You are signed in to ${name} on this computer. Usage comes out of your plan.` : `Sign in with your ${name} account. Usage comes out of your plan, not an API key.`) + what;
    $("#oGo").disabled = !(h && h.signed_in);
  };
  const pick = id => {
    prov = id; res.className = "res"; res.textContent = "";
    $$("[data-prov]", o).forEach(x => x.setAttribute("aria-checked", x.dataset.prov === id));
    const isSub = !!SIGNIN[id];
    $("#oKeyBox").hidden = isSub; $("#oSubBox").hidden = !isSub;
    $("#oBaseF").hidden = $("#oModelF").hidden = id !== "custom"; $("#oCredit").hidden = id !== "meshpool";
    $("#oGo").disabled = false;
    if (isSub) { drawSub(); loadHarnesses().then(drawSub).catch(() => {}); } else $(id === "custom" ? "#oBase" : "#oKey").focus();
  };
  $$("[data-prov]", o).forEach(x => x.onclick = () => pick(x.dataset.prov));
  $("#oKey").focus();
  $("#oShow").onclick = () => { const i = $("#oKey"); i.type = i.type === "password" ? "text" : "password"; $("#oShow").textContent = i.type === "password" ? "Show" : "Hide"; };
  $("#oKey").onkeydown = e => { if (e.key === "Enter") $("#oGo").click(); };
  $("#oKey").oninput = () => { res.textContent = ""; };
  $("#oSign").onclick = async () => {
    const id = SIGNIN[prov].harness;
    try { const r = await post("/api/harness-login", {id}); res.className = "res " + (r.ok ? "" : "bad"); res.textContent = r.message; } catch (e) { res.className = "res bad"; res.textContent = e.message; return; }
    const t0 = Date.now();
    const tick = async () => { if (!$("#oSubBox") || SIGNIN[prov]?.harness !== id) return; await loadHarnesses(); drawSub(); if (sub()?.signed_in) { res.className = "res ok"; res.textContent = "Signed in."; return; } if (Date.now() - t0 < 600000) setTimeout(tick, 3000); };
    setTimeout(tick, 3000);
  };
  const finish = async () => { await loadSettings(); await loadHarnesses(); S.draft.harness = modeHarness(); await loadModels(true); renderBar(); renderHero?.(); $("#onbKey").remove(); permStep(o); };
  $("#oGo").onclick = async () => {
    if (SIGNIN[prov]) {
      if (!sub()?.signed_in) { res.className = "res bad"; res.textContent = "Sign in first."; return; }
      $("#oGo").disabled = true;
      try { await post("/api/provider", {provider: prov}); store("harness_code", null); store("harness_cowork", null); await finish(); } catch (e) { res.className = "res bad"; res.textContent = e.message; $("#oGo").disabled = false; }
      return;
    }
    const key = $("#oKey").value.trim();
    if (!key) { res.className = "res bad"; res.textContent = "Paste your API key first."; $("#oKey").focus(); return; }
    const values = {MESHPOOL_API_KEY: key, MESHPOOL_BASE_URL: ""};
    if (prov === "custom") {
      const base = $("#oBase").value.trim(), model = $("#oModel").value.trim();
      if (!base || !model) { res.className = "res bad"; res.textContent = "Fill in the provider address and a model it serves."; (base ? $("#oModel") : $("#oBase")).focus(); return; }
      values.MESHPOOL_BASE_URL = base; values.MH_MODEL = model;
    }
    $("#oGo").disabled = true; res.className = "res"; res.textContent = "Checking your key…";
    try {
      const r = await post("/api/test", {values: prov === "custom" ? values : {MESHPOOL_API_KEY: key, MESHPOOL_BASE_URL: "https://llm.meshpool.ai/v1"}});   // check first; save only a key that works
      if (!r.ok) { res.className = "res bad"; res.textContent = connMsg(r.detail); $("#oGo").disabled = false; return; }
      await post("/api/settings", {values}); await post("/api/provider", {provider: prov});
      store("harness_code", null); store("harness_cowork", null);
      await finish();
    } catch (e) { res.className = "res bad"; res.textContent = e.message; $("#oGo").disabled = false; }
  };
}

/* Bring your context: what ChatGPT, Claude or Gemini already knows about you, by prompt or by data export, and
   custom GPT / project instructions as skills. */
async function contextPanel(box) {
  if (!box) return;
  let prompt = "", local = {};
  try { prompt = (await api("/api/import-context")).prompt; } catch (e) { /* the server is older: hide the panel */ return; }
  try { local = await api("/api/import-chats"); } catch (e) { local = {}; }
  const srcs = Object.entries(local).filter(([, v]) => v.found);
  const n = x => Number(x || 0).toLocaleString();
  box.innerHTML = `<h3 class="onb-h">Bring your chats and context</h3>
    <p class="muted">Your old chats come over as threads you can search and continue here. What your assistant knows about you becomes instructions and memory. Nothing leaves this computer.</p>
    <ol class="ctx-steps">
      <li><b>Your ChatGPT or Claude chats.</b> Ask for your data export, then drop the .zip here as it arrives (do not unzip it).
        <small class="muted">ChatGPT: Settings, Data controls, Export data. Claude: Settings, Privacy, Export data. Both email you a link within minutes to a day.</small>
        <div class="dropzone" id="ctxDrop" tabindex="0" role="button">Drop the export .zip here, or click to choose it<input type="file" id="ctxZip" accept=".zip" hidden></div>
        <small class="muted">Every chat becomes a thread, with its original date. Claude projects become skills, and lasting facts about you become memory.</small></li>
      ${srcs.length ? `<li><b>Sessions on this computer.</b> ${srcs.map(([k, v]) => `<span class="ctx-src"><span>${esc(v.name)}: ${n(v.found)} session${v.found === 1 ? "" : "s"}${v.imported ? `, ${n(v.imported)} already here` : ""}</span><button class="btn" data-local="${esc(k)}" ${v.imported >= v.found ? "disabled" : ""}>${v.imported >= v.found ? "Imported" : "Import"}</button></span>`).join("")}
        <small class="muted">Each session opens in its project folder, so you can carry on with any agent.</small></li>` : ""}
      <li><b>Or ask your current assistant.</b> Copy this prompt into ChatGPT, Claude or Gemini, then paste its answer below.
        <div><button class="btn" id="ctxCopy">Copy the prompt</button></div>
        <textarea id="ctxText" rows="4" placeholder="Paste the answer here"></textarea>
        <div><button class="btn" id="ctxGo">Import the answer</button></div></li>
      <li><b>Have a custom GPT or a Claude project you rely on?</b> Paste its instructions to make it a skill.
        <input id="ctxName" placeholder="Name, e.g. board-memo-writer">
        <textarea id="ctxIns" rows="3" placeholder="Paste the instructions"></textarea>
        <div><button class="btn" id="ctxSkill">Save as a skill</button></div></li>
    </ol><div class="res" id="ctxRes"></div>`;
  const res = (t, bad, ok) => { const r = $("#ctxRes", box); r.className = "res" + (bad ? " bad" : ok ? " ok" : ""); r.textContent = t; };
  const refresh = async () => { try { if (typeof loadThreads === "function") await loadThreads(); } catch (e) { /* the list refreshes later */ } };
  $("#ctxCopy", box).onclick = async () => { try { await navigator.clipboard.writeText(prompt); res("Prompt copied. Paste it into your assistant, then paste its answer above."); } catch (e) { $("#ctxText", box).value = prompt; res("Copy the prompt from the box, then replace it with the answer."); } };
  $("#ctxGo", box).onclick = async () => {
    const text = $("#ctxText", box).value.trim(); if (!text) return res("Paste the answer first.", true);
    try { const r = await post("/api/import-context", {text}); res(`Imported ${r.memory} facts into memory and ${r.instructions} instructions.` + (r.skipped?.length ? ` Skipped ${r.skipped.length} (secrets or system instructions).` : ""), false, true); } catch (e) { res(e.message, true); }
  };
  const upload = async f => {
    if (!f) return;
    if (!/\.zip$/i.test(f.name)) return res("Choose the .zip file from ChatGPT or Claude, without unzipping it.", true);
    res(`Importing ${f.name} (${(f.size / 1048576).toFixed(0)} MB)… large exports take a minute.`);
    try {
      const r = await api("/api/import-file?chats=1&context=1", {method: "POST", body: f, headers: {"Content-Type": "application/zip"}});
      await refresh();
      res(`${r.kind === "claude" ? "Claude" : "ChatGPT"} export: ${n(r.imported)} chat${r.imported === 1 ? "" : "s"} imported as threads${r.skipped ? ` (${n(r.skipped)} already here)` : ""}.` + (r.context_pending ? ` Meshpool is now learning what they say about you${r.kind === "claude" ? " and adding your projects as skills" : ""}; that shows up in Instructions and memory in a minute.` : ""), false, true);
    } catch (e) { res(e.message, true); }
  };
  const dz = $("#ctxDrop", box);
  dz.onclick = () => $("#ctxZip", box).click(); dz.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#ctxZip", box).click(); } };
  $("#ctxZip", box).onchange = ev => upload(ev.target.files[0]);
  dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); }; dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files[0]); };
  $$("[data-local]", box).forEach(x => x.onclick = async () => {
    x.disabled = true; res(`Importing ${local[x.dataset.local].name} sessions…`);
    try {
      const r = await post("/api/import-chats", {sources: [x.dataset.local]}); await refresh();
      res(`Imported ${n(r.imported)} ${local[x.dataset.local].name} session${r.imported === 1 ? "" : "s"} as threads${r.skipped ? ` (${n(r.skipped)} already here)` : ""}.`, false, true);
      x.textContent = "Imported";
    } catch (e) { x.disabled = false; res(e.message, true); }
  });
  $("#ctxSkill", box).onclick = async () => {
    const instructions = $("#ctxIns", box).value.trim(); if (!instructions) return res("Paste the instructions first.", true);
    try { const r = await post("/api/import-context", {name: $("#ctxName", box).value.trim(), instructions}); res(`Saved the skill ${r.skill}.`, false, true); } catch (e) { res(e.message, true); }
  };
}

/* Settings, Plugins: built-in plugin groups (one switch each) and installed plugins in the Claude Code format.
   Nothing in a plugin runs at install; its MCP servers stay off until the user turns each one on. */
async function drawPlugins(b, close) {
  let d;
  try { d = await api("/api/plugins"); } catch (e) { b.innerHTML = `${close}<h3>Plugins</h3><div class="res bad">${esc(e.message)}</div>`; return; }
  const say = (text, bad) => { RES("plRes").className = "res " + (bad ? "bad" : "ok"); RES("plRes").textContent = text; };
  const act = async body => { try { const r = await post("/api/plugins", body); await loadSkills(); render(r); return r; } catch (e) { say(e.message, true); return null; } };
  const installed = r => { if (!r?.installed?.length) return; const p = r.installed; const sv = p.reduce((n, x) => n + x.servers.length, 0); say(`Installed ${p.map(x => x.name).join(", ")}: ${p.reduce((n, x) => n + x.skills.length, 0)} skills${sv ? `, ${sv} MCP server${sv === 1 ? "" : "s"} (off until you turn them on)` : ""}.` + p.flatMap(x => x.notes).map(n => " " + n).join("")); };
  const group = g => `<li><label class="switch"><input type="checkbox" data-pb="${esc(g.id)}" ${g.enabled ? "checked" : ""} aria-label="Use ${esc(g.name)}"><span></span></label><span class="tg"><b>${esc(g.name)}</b> <span class="badge modified">${g.skills.length} skills</span>${g.partly ? ` <span class="badge modified">Some on</span>` : ""}<small>${esc(g.description)}</small><small>${esc(g.source)}</small><details><summary style="cursor:pointer;font-size:12px;color:var(--ink-3)">Skills it turns on</summary><small>${g.skills.map(s => "$" + esc(s.id)).join(", ")}</small></details></span></li>`;
  const server = s => `<span style="display:flex;gap:8px;align-items:center;margin-top:6px"><label class="switch" title="Run this server"><input type="checkbox" data-ps="${esc(s.name)}" data-cmd="${esc(s.target)}" ${s.enabled ? "checked" : ""} ${s.present ? "" : "disabled"} aria-label="Turn on ${esc(s.name)}"><span></span></label><code>${esc(s.target)}</code></span>`;
  const plugin = p => `<li><label class="switch"><input type="checkbox" data-pi="${esc(p.name)}" ${p.enabled ? "checked" : ""} aria-label="Use ${esc(p.name)}"><span></span></label><span class="tg"><b>${esc(p.name)}</b>${p.version ? ` <span class="badge modified">${esc(p.version)}</span>` : ""}<small>${esc(p.description)}</small>${p.skills.length ? `<small>Skills: ${p.skills.map(s => "$" + esc(s)).join(", ")}</small>` : ""}${(p.server_status || []).map(server).join("")}<small>From ${esc(p.source)}</small></span><button class="iconbtn" data-pr="${esc(p.name)}" aria-label="Remove ${esc(p.name)}">${ICON.x}</button></li>`;
  const render = v => {
    d = v;
    b.innerHTML = `${close}<h3>Plugins</h3><p class="lead">A plugin bundles skills, MCP servers and instructions for one kind of work, in the same format as Claude Code plugins. Nothing in a plugin runs when you install it, and its MCP servers stay off until you turn them on.</p>
      <h3 style="font-size:15px;margin:14px 0 6px">Built in</h3><ul class="rows2">${d.catalog.map(group).join("")}</ul>
      <h3 style="font-size:15px;margin:14px 0 6px">Installed</h3><ul class="rows2">${d.plugins.map(plugin).join("") || `<li class="pnote">No plugins installed yet.</li>`}</ul>
      <div class="keyrow"><input id="plSrc" placeholder="Folder, .zip, git URL or marketplace.json" spellcheck="false"><button class="btn primary" id="plAdd">Install</button></div>
      <div class="dropzone" id="plDrop" style="margin-top:8px" tabindex="0" role="button">Or drop a plugin .zip or a marketplace.json here<input type="file" id="plFile" accept=".zip,.json" hidden></div><div class="res" id="plRes"></div>`;
    $(".mclose", b)?.addEventListener("click", () => $(".scrim").remove());
    $$("[data-pb]", b).forEach(x => x.onchange = () => act({action: "enable", name: x.dataset.pb, on: x.checked}));
    $$("[data-pi]", b).forEach(x => x.onchange = () => act({action: "enable", name: x.dataset.pi, on: x.checked}));
    $$("[data-pr]", b).forEach(x => x.onclick = async () => { if (await ask({title: "Remove this plugin?", text: "Its skills, MCP servers and files are removed. Skills you had before stay.", ok: "Remove plugin", danger: true})) act({action: "remove", name: x.dataset.pr}); });
    $$("[data-ps]", b).forEach(x => x.onchange = async () => {
      if (x.checked && !await ask({title: "Turn on this server?", text: `It runs on this computer: ${x.dataset.cmd}`, ok: "Turn on"})) { x.checked = false; return; }
      try { await post("/api/mcp", {name: x.dataset.ps, enabled: x.checked}); } catch (e) { say(e.message, true); x.checked = !x.checked; }
    });
    $("#plAdd").onclick = async () => { const src = $("#plSrc").value.trim(); if (!src) { $("#plSrc").focus(); return; } say("Installing..."); installed(await act({action: "install", source: src})); };
    const upload = async f => { if (!f) return; const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); }); say("Installing..."); installed(await act({action: "upload", file: {name: f.name, data}})); };
    const dz = $("#plDrop"); dz.onclick = () => $("#plFile").click(); $("#plFile").onchange = () => upload($("#plFile").files[0]);
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); }; dz.ondragleave = () => dz.classList.remove("over");
    dz.ondrop = e => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files[0]); };
  };
  render(d);
}

/* Settings, MCP servers: a library of well-known servers. Adding one puts it in the list switched off; the user fills
   its settings (saved in meshpool.config on this computer) and turns it on. OAuth ones sign in through Connectors. */
async function drawMcpLibrary(b) {
  const box = document.createElement("div"); b.appendChild(box);
  let d;
  try { d = await api("/api/mcp-library"); } catch (e) { box.innerHTML = `<div class="res bad">${esc(e.message)}</div>`; return; }
  const say = (text, bad) => { RES("mlRes").className = "res " + (bad ? "bad" : "ok"); RES("mlRes").textContent = text; };
  const secret = n => /KEY|TOKEN|SECRET|PASSWORD|URI|STRING/.test(n);
  const fields = e => e.settings.map(s => `<label class="field" style="margin-top:6px"><span>${esc(s.name)}</span><input data-ms="${esc(s.name)}" type="${secret(s.name) ? "password" : "text"}" placeholder="${esc(s.hint)}" autocomplete="off" spellcheck="false"></label>`).join("");
  const button = e => e.transport === "connector" || e.transport === "oauth" ? (e.enabled ? "" : `<button class="btn" data-ma="${esc(e.id)}">Connect</button>`)
    : !e.added ? `<button class="btn" data-ma="${esc(e.id)}">Add</button>` : e.enabled ? "" : `<button class="btn primary" data-mo="${esc(e.id)}">${e.settings.length ? "Save and turn on" : "Turn on"}</button>`;
  const row = e => `<li data-me="${esc(e.id)}"><span class="tg"><b>${esc(e.name)}</b> ${e.enabled ? `<span class="badge added">On</span>` : e.added ? `<span class="badge modified">Added, off</span>` : ""}<small>${esc(e.description)}</small>${e.target ? `<code>${esc(e.target)}</code>` : ""}<small>${esc(e.license)} · <a href="${esc(e.docs)}" target="_blank" rel="noopener">Docs</a>${e.settings.length ? ` · Needs ${e.settings.map(s => esc(s.name)).join(", ")}` : ""}</small>${e.added && !e.enabled && e.settings.length && e.transport !== "connector" ? fields(e) : ""}</span>${button(e)}</li>`;
  const draw = () => {
    const cat = S.mcpLibCat || "";
    box.innerHTML = `<h3 style="font-size:15px;margin:18px 0 4px">Library</h3><p class="lead" style="font-size:12.5px">Well-known MCP servers, checked to exist. Adding one puts it in the list above, switched off. Fill in its settings, then turn it on. Keys stay on this computer.</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">${["", ...d.categories].map(c => `<button class="btn${c === cat ? " primary" : ""}" data-mc="${c}">${c ? c[0].toUpperCase() + c.slice(1) : "All"}</button>`).join("")}</div>
      <ul class="rows2">${d.library.filter(e => !cat || e.category === cat).map(row).join("")}</ul><div class="res" id="mlRes"></div>`;
    $$("[data-mc]", box).forEach(x => x.onclick = () => { S.mcpLibCat = x.dataset.mc; draw(); });
    $$("[data-ma]", box).forEach(x => x.onclick = async () => {
      try {
        const r = await post("/api/mcp-library", {action: "add", id: x.dataset.ma});
        if (r.result.connector) { await post("/api/connectors", {action: "connect", id: r.result.connector}); S.connCache = null; d = r; draw(); say("Finish signing in in your browser. New threads can then use it."); return; }
        renderSection("mcp");
      } catch (e) { say(e.message, true); }
    });
    $$("[data-mo]", box).forEach(x => x.onclick = async () => {
      const values = {}; $$(`[data-me="${x.dataset.mo}"] [data-ms]`, box).forEach(i => { if (i.value.trim()) values[i.dataset.ms] = i.value.trim(); });
      try { await post("/api/mcp-library", {action: "configure", id: x.dataset.mo, values}); renderSection("mcp"); } catch (e) { say(e.message, true); }
    });
  };
  draw();
}

/* Bring your setup: skills and MCP servers from Claude, Codex, Cursor and other apps on this computer. */
async function importPanel(box, onDone) {
  box.innerHTML = `<div class="res">Looking for skills and MCP servers in Claude, Codex, Cursor and other apps...</div>`;
  let f;
  try { f = await api("/api/import"); } catch (e) { box.innerHTML = `<div class="res bad">${esc(e.message)}</div>`; return; }
  const rows = [...f.skills, ...f.mcp];
  if (!rows.length) { box.innerHTML = `<div class="res">No skills or MCP servers found in other apps on this computer. You can add skills and servers any time in Settings.</div>`; onDone?.(null); return; }
  const item = r => `<label class="imp"><input type="checkbox" data-key="${esc(r.key)}"><span class="tg"><b>${esc(r.name)}</b> <span class="badge modified">${esc(r.source)}</span>${r.exists ? ` <span class="badge">Already in Meshpool</span>` : ""}<small>${esc(r.description || "")}</small></span></label>`;
  box.innerHTML = `<div class="impsel"><button class="linkbtn" id="impAll">Select all</button><button class="linkbtn" id="impNone">Select none</button></div>${f.skills.length ? `<h3 class="onb-h">Skills (${f.skills.length})</h3><div class="impl">${f.skills.map(item).join("")}</div>` : ""}
    ${f.mcp.length ? `<h3 class="onb-h">Tools from other apps (${f.mcp.length})</h3><div class="impl">${f.mcp.map(item).join("")}</div><p class="lead" style="font-size:12px">They keep their settings from the other app and stay on this computer.</p>` : ""}
    <div style="display:flex;gap:8px;align-items:center;margin-top:10px"><button class="btn primary" id="impGo">Import selected</button></div><div class="res" id="impRes"></div>`;
  $("#impAll", box).onclick = () => $$("[data-key]", box).forEach(x => x.checked = true);
  $("#impNone", box).onclick = () => $$("[data-key]", box).forEach(x => x.checked = false);
  $("#impGo", box).onclick = box._import = async () => {
    const keys = $$("[data-key]", box).filter(x => x.checked).map(x => x.dataset.key);
    if (!keys.length) { $("#impRes", box).className = "res"; $("#impRes", box).textContent = "Nothing selected."; return; }
    $("#impGo", box).disabled = true;
    try {
      const r = await post("/api/import", {keys});
      await loadSkills(); S.connCache = null;
      $("#impRes", box).className = "res " + (r.errors.length ? "bad" : "ok");
      $("#impRes", box).textContent = `Imported ${r.skills.length} skill${r.skills.length === 1 ? "" : "s"} and ${r.mcp.length} MCP server${r.mcp.length === 1 ? "" : "s"}. Every agent can use them in new threads.` + (r.errors.length ? " Problems: " + r.errors.join("; ") : "");
      box.dataset.done = "1"; onDone?.(r);
    } catch (e) { $("#impRes", box).className = "res bad"; $("#impRes", box).textContent = e.message; }
    $("#impGo", box).disabled = false;
  };
}

/* Permissions up front: one step on first launch (and once after an update) that asks for everything at once. */
function permStep(host) {
  const o = host || Object.assign(document.createElement("div"), {className: "onb", id: "onb"});
  if (!host) { if ($("#onb")) return; document.body.appendChild(o); }
  store("permsAsked", 1); store("onbStep", 2);
  const mac = S.settings.platform === "darwin";
  let card = $("#onbPerm", o);
  if (!card) { card = document.createElement("div"); card.className = "onb-card"; card.id = "onbPerm"; o.appendChild(card); }
  card.hidden = false;
  card.innerHTML = `<img class="logo mlogo" src="${logoSrc()}" alt="Meshpool">
    ${onbSteps(2)}<h1>Choose where Meshpool can work</h1><p>Meshpool only works in the places you allow. You can change this any time in Settings → Permissions.</p>
    <div class="scope" role="radiogroup" aria-label="Where Meshpool can work">
      <button class="scopeopt" data-scope="pick" role="radio"><b>Folders I choose</b><small>Add one or more folders now.</small></button>
      <button class="scopeopt" data-scope="home" role="radio"><b>My whole home folder</b><small>Documents, Desktop, Downloads and everything else in your user folder.</small></button>
      <button class="scopeopt" data-scope="later" role="radio"><b>Start in Meshpool Projects</b><small>A folder Meshpool made for you. Add your own folders any time.</small></button>
    </div><div class="res" id="oScope"></div>
    <div id="oPermH"><h3 class="onb-h">What Meshpool can do</h3>
    <ul class="rows2" id="oPerms"></ul></div>
    <div class="onb-go"><button class="bigbtn" id="oAll">Allow all</button><button class="btn" id="oDone">Continue</button></div>`;
  const draw = async body => {
    try {
      const r = await post("/api/permissions", body || {});
      const rows = r.rows.filter(x => x.id !== "automation");
      $("#oPerms", card).innerHTML = rows.map(x => `<li><span class="tg"><b>${esc(x.name)}</b><small>${esc(x.why)}</small></span>${x.ok ? `<span class="badge added">Allowed</span>` : mac || x.id === "notifications" ? `<button class="btn" data-p="${esc(x.id)}">${x.id === "disk" ? "Open settings" : "Allow"}</button>` : `<span class="badge">Not found</span>`}</li>`).join("") || `<li class="pnote">${esc(r.note)}</li>`;
      $$("[data-p]", card).forEach(x => x.onclick = () => draw({id: x.dataset.p}));
      const needs = rows.some(x => !x.ok && (mac || x.id === "notifications"));
      $("#oAll", card).hidden = !needs; $("#oPermH", card).hidden = !rows.length;
      $("#oDone", card).className = needs ? "btn" : "bigbtn";
    } catch (e) { /* the step still works without the list */ }
  };
  $$("[data-scope]", card).forEach(x => x.onclick = async () => {
    $$("[data-scope]", card).forEach(y => y.setAttribute("aria-checked", y === x));
    const res = $("#oScope", card);
    try {
      if (x.dataset.scope === "pick") { await pickProject(); res.className = S.projects.length ? "res ok" : "res"; res.textContent = S.projects.length ? `Added ${S.projects.map(x => x.name || projName(x.path)).join(", ")}. Add more any time from the sidebar.` : "No folder added yet."; }
      else if (x.dataset.scope === "home") { const r = await post("/api/projects", {action: "home"}); S.projects = r.projects; S.draft.project = r.picked; store("project", r.picked); renderSide(); newThread(); res.className = "res ok"; res.textContent = "Meshpool can work anywhere in your home folder. It still asks before changing files unless you allow edits."; }
      else { res.className = "res"; res.textContent = "Tasks start in your Meshpool Projects folder. Add your own folders any time from the sidebar."; }
      if (mac && x.dataset.scope !== "later") draw({id: "folders"});
    } catch (e) { res.className = "res bad"; res.textContent = e.message; }
  });
  $("#oAll", card).onclick = async () => { $("#oAll", card).disabled = true; await draw({id: "all"}); $("#oAll", card).disabled = false; $("#oAll", card).textContent = "Ask again"; };
  document.addEventListener("visibilitychange", () => { if (!document.hidden && $("#onbPerm")) draw(); });
  $("#oDone", card).onclick = () => setupStep(o, card);
  if (!S.projects.length) { const later = $("[data-scope=later]", card); later.setAttribute("aria-checked", true); $("#oScope", card).className = "res"; $("#oScope", card).textContent = "Tasks start in your Meshpool Projects folder. Add your own folders any time from the sidebar."; }
  draw();
}

function setupStep(o, prev) {
  prev?.remove();
  const card = document.createElement("div"); card.className = "onb-card"; card.id = "onbSetup"; o.appendChild(card);
  card.innerHTML = `<img class="logo mlogo" src="${logoSrc()}" alt="Meshpool">
    ${onbSteps(3)}<h1>Bring your setup</h1><p>Optional. Use what you already have in Claude, ChatGPT, Codex or Cursor. You can also do this later in Settings.</p>
    <div id="oImport"></div>
    <div id="oContext"></div>
    <div class="onb-go"><button class="bigbtn" id="oFinish">Start using Meshpool</button></div>`;
  importPanel($("#oImport", card)).then(() => { const b = $("#impGo", card); if (b) b.hidden = true; });
  contextPanel($("#oContext", card));
  store("onbStep", 3);
  $("#oFinish", card).onclick = async () => {
    store("onbDone", 1); post("/api/settings", {values: {MH_ONBOARDED: "1"}}).catch(() => {});        // ticked items are imported on the way out; no extra click
    const box = $("#oImport", card);
    if (box?._import && !box.dataset.done && $$("[data-key]", box).some(x => x.checked)) { $("#oFinish", card).disabled = true; $("#oFinish", card).textContent = "Importing…"; await box._import(); }
    o.remove(); renderBar(); renderHero();
  };
}

/* ================================== updates ================================== */
async function checkUpdate(force) {
  try { S.upd = await post("/api/update/check", {force: !!force}); } catch (e) { S.upd = {ok: false, error: e.message}; }
  const bar = $("#updBar");
  if (bar) { bar.hidden = !(S.upd.ok && S.upd.newer); bar.innerHTML = `<span class="dot"></span>Meshpool ${esc(S.upd.latest || "")} is available<span class="go">Update</span>`; }
  return S.upd;
}
function updatePanel(u) {
  const res = RES("updRes"), more = $("#updMore"); if (!res || !more) return;
  if (!u) { res.textContent = ""; more.innerHTML = ""; return; }
  if (!u.ok) { res.className = "res bad"; res.textContent = u.error; more.innerHTML = ""; return; }
  if (!u.newer) { res.className = "res ok"; res.textContent = `You have the latest version (${u.current}).`; more.innerHTML = ""; return; }
  res.className = "res"; res.textContent = `Meshpool ${u.latest} is available. You have ${u.current}.`;
  const how = u.kind === "mac-app" ? "Downloads the new Mac app and opens it." : u.kind === "dev" ? "This is a git checkout: run git pull." : "Installs in place in under a minute, then restarts.";
  more.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-top:10px">${u.kind === "dev" ? "" : `<button class="btn primary" id="updGo">Update now</button>`}<a class="btn" href="${esc(u.url)}" target="_blank" rel="noopener">What's new</a><small style="color:var(--ink-3)">${esc(how)}</small></div><div class="res" id="updOut"></div>`;
  const btn = $("#updGo"); if (!btn) return;
  btn.onclick = async () => {
    const out = RES("updOut"); btn.disabled = true; btn.textContent = "Updating...";
    out.className = "res"; out.textContent = "Downloading the release. Keep Meshpool open.";
    let r; try { r = await post("/api/update/install", {}); } catch (err) { r = {ok: false, error: err.message}; }
    btn.disabled = false; btn.textContent = "Update now";
    if (!r.ok) { out.className = "res bad"; out.textContent = r.error; return; }
    out.className = "res ok"; out.textContent = r.message;
    if (r.action === "restart") { btn.textContent = "Restart now"; btn.onclick = restartApp; }
    else if (r.action !== "installer") btn.remove();
  };
}
async function restartApp() {
  const out = RES("updOut"); if (out) { out.className = "res"; out.textContent = "Restarting Meshpool..."; }
  try { await post("/api/update/restart", {}); } catch (e) {}
  const t0 = Date.now();
  const wait = async () => { try { const r = await fetch("/healthz", {cache: "no-store"}); if (r.ok && Date.now() - t0 > 2500) { location.reload(); return; } } catch (e) {} if (Date.now() - t0 < 90000) setTimeout(wait, 1000); else if (out) out.textContent = "Meshpool closed. Open it again to use the new version."; };
  setTimeout(wait, 1500);
}

/* ================================== boot ================================== */
(async () => {
  shell();
  document.addEventListener("click", e => { const b = e.target.closest(".flink, .outs [data-open]"); if (!b || !b.classList.contains("flink")) return;
    toast(`Opening ${b.dataset.open}…`); post("/api/open-file", {root: curRoot(), path: b.dataset.open}).catch(err => toast(err.message)); });
  // New items in an open thread ease in; a thread being opened shows at once (no wave of animations).
  new MutationObserver(ms => { if (!S.cur?.ready) return; for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1 && n.id !== "working") n.classList.add("enter"); })
    .observe($("#col"), {childList: true});
  try { await Promise.all([loadSettings(), loadProjects(), loadHarnesses(), loadSkills()]); }
  catch (e) { $("#col").innerHTML = `<div class="hero"><h1>Meshpool could not start</h1><p class="sub">${esc(e.message)}</p></div>`; return; }
  await loadThreads();
  S.draft.harness = modeHarness();
  if (S.draft.project && !S.projects.some(p => p.path === S.draft.project) && S.draft.project !== S.defaultProject) S.draft.project = "";
  newThread();
  if (S.panel) renderPanel();
  if (!S.settings.ready) showOnboarding();
  else {
    loadModels().then(renderBar);
    if (!store("onbDone") && get0("MH_ONBOARDED") !== "1" && !S.threads.length) { if (store("onbStep") === 3 || store("onbStep") === 2) { if (store("onbStep") === 2) { permStep(); } else { const o = Object.assign(document.createElement("div"), {className: "onb", id: "onb"}); document.body.appendChild(o); setupStep(o); } } else if (!store("permsAsked")) permStep(); }
    // 21. a thread waiting for the user's OK opens straight away after a restart
    const waiting = S.threads.find(t => t.state === "waiting" && (t.kind || "code") === S.appMode);
    const last = store("last_" + S.appMode);
    if (waiting) openThread(waiting.id);
    else if (last && S.threads.some(t => t.id === last && !t.archived)) openThread(last);
  }   // set up but onboarding left halfway
  if (MP.desktop) { const ping = () => post("/api/ping").catch(() => {}); ping(); setInterval(ping, 30000); }
  setTimeout(() => checkUpdate(false), 3000);
  window.addEventListener("resize", () => { clearTimeout(S.rzT); S.rzT = setTimeout(fitPanel, 120); });
  document.addEventListener("contextmenu", e => { const ph = e.target.closest?.("#sideList .proj-h"); if (ph) { e.preventDefault(); projMenu({currentTarget: ph, target: ph}, ph.parentElement.dataset.p); return; } });
  document.addEventListener("contextmenu", e => { const li = e.target.closest?.("#sideList .th"); if (!li || !li.dataset.id) return; e.preventDefault(); threadMenu({currentTarget: li, target: li}, li.dataset.id); });
  const conv = $("#conv"); const jd = Object.assign(document.createElement("button"), {className: "jumpdown", hidden: true, textContent: "↓ Latest"});
  conv.parentElement.appendChild(jd); jd.onclick = () => { conv.scrollTop = conv.scrollHeight; };
  conv.addEventListener("scroll", () => { jd.hidden = conv.scrollHeight - conv.scrollTop - conv.clientHeight < 300; });
  setInterval(() => { if (S.threads.some(t => ["working", "waiting"].includes(t.state))) loadThreads(); }, 4000);
})();
