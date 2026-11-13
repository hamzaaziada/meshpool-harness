// Jitter and stability probe: what a user sees while a task runs. Measures layout shift, how often whole regions are
// rebuilt, element movement, and whether scroll positions, open menus, hover and typing survive the live updates.
// Usage: node jitter.js PORT PROJECT EXTRA_PROJECT_DIR...   (mock model; uses the "long task" rule)
const {chromium} = require("playwright");
const [port, proj, ...extra] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;
const results = [];
const check = (name, ok, detail = "") => { results.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + "  " + String(detail).slice(0, 300)); };

(async () => {
  const b = await chromium.launch();
  const [vw, vh] = (process.env.VP || "1440x900").split("x").map(Number);
  const p = await b.newPage({viewport: {width: vw, height: vh}});
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(() => {
    try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {}
    window.__shift = 0; window.__shifts = [];
    new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) { window.__shift += e.value; window.__shifts.push([Math.round(performance.now()), e.value.toFixed(4), (e.sources || []).map(s => s.node?.id || s.node?.className || s.node?.nodeName).join(",")]); } }).observe({type: "layout-shift", buffered: true});
  });
  await p.goto(BASE + "/"); await p.waitForSelector(".hero");
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  for (const d of [proj, ...extra]) await api("/api/projects", {action: "add", path: d});
  // a few finished threads so the sidebar has content
  for (let i = 0; i < 6; i++) { const v = await api("/api/agent", {workdir: proj, harness: "meshpool-code", kind: "code", mode: "auto"}); await api(`/api/agent/${v.id}/send`, {text: "hello " + i}); }
  await p.waitForTimeout(4000);
  await p.evaluate(() => loadThreads());
  await p.evaluate(path => { if (S.appMode !== "code") setAppMode("code"); newThread(); S.draft.project = path; }, proj);
  await p.fill("#input", "long task please"); await p.keyboard.press("Enter");
  await p.waitForFunction(() => S.cur?.view?.state === "working", null, {timeout: 30000});

  // instrument rebuilds after the run has started
  await p.evaluate(() => {
    window.__mut = {side: 0, top: 0, bar: 0, col: 0, panel: 0};
    const watch = (sel, k) => { const el = document.querySelector(sel); if (!el) return; new MutationObserver(m => { for (const r of m) if (r.type === "childList" && (r.removedNodes.length || r.addedNodes.length)) window.__mut[k]++; }).observe(el, {childList: true, subtree: true}); };
    watch("#sideList", "side"); watch("#top", "top"); watch("#bar", "bar"); watch("#col", "col"); watch("#panel", "panel");
    window.__shift = 0; window.__shifts = [];
  });
  // element movement: sample positions of fixed landmarks every 250 ms for 10 s
  const boxes = [];
  for (let i = 0; i < 40; i++) {
    boxes.push(await p.evaluate(() => Object.fromEntries(["#input", "#top", "#sideList", ".hero", "#col"].map(s => { const r = document.querySelector(s)?.getBoundingClientRect(); return [s, r ? [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] : null]; }))));
    await p.waitForTimeout(250);
  }
  const moved = {};
  for (const s of ["#input", "#top", "#sideList"]) { const set = new Set(boxes.map(b => JSON.stringify(b[s]))); moved[s] = set.size; }
  const m = await p.evaluate(() => ({mut: window.__mut, shift: window.__shift, shifts: window.__shifts.slice(0, 8)}));
  check("layout shift while a task runs (CLS < 0.02)", m.shift < 0.02, JSON.stringify({cls: m.shift.toFixed(4), shifts: m.shifts}));
  check("input box, top bar and sidebar do not move while a task runs", Object.values(moved).every(n => n === 1), JSON.stringify(moved));
  check("sidebar is not rebuilt every few seconds (<= 2 rebuilds in 10 s)", m.mut.side <= 2, JSON.stringify(m.mut));
  check("top bar is not rebuilt every poll (<= 2 in 10 s)", m.mut.top <= 2, JSON.stringify(m.mut));
  check("composer bar is not rebuilt every poll (<= 2 in 10 s)", m.mut.bar <= 2, JSON.stringify(m.mut));

  // sidebar scroll position survives live updates
  await p.evaluate(() => { const s = document.querySelector("#sideList"); s.scrollTop = s.scrollHeight; });
  const sTop = await p.evaluate(() => document.querySelector("#sideList").scrollTop);
  await p.waitForTimeout(9000);
  const sTop2 = await p.evaluate(() => document.querySelector("#sideList").scrollTop);
  check("sidebar keeps its scroll position during a run", sTop === sTop2, `${sTop} -> ${sTop2} (scrollable=${sTop > 0})`);

  // an open folder menu stays open
  const opened = await p.evaluate(() => { const b = document.querySelector("[data-pmenu]"); if (!b) return false; b.click(); return !!document.querySelector(".pop, .menu"); });
  await p.waitForTimeout(9000);
  const still = await p.evaluate(() => !!document.querySelector(".pop, .menu"));
  check("an open menu stays open during a run", !opened || still, `opened=${opened} still=${still}`);
  await p.keyboard.press("Escape");

  // typing in the composer keeps focus and text through updates
  await p.click("#input"); await p.keyboard.type("draft words ", {delay: 120});
  await p.waitForTimeout(3000); await p.keyboard.type("more", {delay: 120});
  const typed = await p.evaluate(() => ({v: document.querySelector("#input").value, f: document.activeElement?.id}));
  check("typing during a run keeps focus and every character", typed.v.endsWith("draft words more") && typed.f === "input", JSON.stringify(typed));

  // conversation: a user who scrolled up is not pulled down
  await p.evaluate(() => { const c = document.querySelector("#conv"); c.scrollTop = 0; });
  await p.waitForTimeout(4000);
  const cTop = await p.evaluate(() => document.querySelector("#conv").scrollTop);
  check("reading earlier messages is not interrupted by auto-scroll", cTop < 50, `scrollTop=${cTop}`);

  // navigation: layout shift and blank frames per user action
  await p.keyboard.press("Escape");
  // After a click the page should reach its final layout in one go. Record every frame for 1.5 s and count how
  // many different layouts the landmarks pass through (1 or 2 is a clean switch; more is visible jumping).
  const step = async (name, fn) => {
    await p.evaluate(() => { window.__frames = []; window.__raf = true;
      const sig = () => { const r = s => { const e = document.querySelector(s); if (!e) return "-"; const b = e.getBoundingClientRect(); return [b.x, b.y, b.width, b.height].map(Math.round).join(","); };
        const tb = [...document.querySelectorAll("#topbar > *")].map(e => { const b = e.getBoundingClientRect(); return Math.round(b.x) + ":" + Math.round(b.width); }).join(" ");
        return [r("#composer"), r("#conv"), r("#sideList"), tb].join(" | "); };
      const loop = () => { if (!window.__raf) return; const s = sig(); const f = window.__frames; if (!f.length || f[f.length - 1] !== s) f.push(s); requestAnimationFrame(loop); }; requestAnimationFrame(loop); });
    await fn(); await p.waitForTimeout(1500);
    const f = await p.evaluate(() => { window.__raf = false; return window.__frames; });
    check(`${name}: settles without jumping (<= 2 layouts)`, f.length <= 3, `${f.length - 1} changes: ` + f.slice(1).map(x => x.slice(0, 140)).join(" || "));
  };
  const ids = await p.evaluate(() => S.threads.filter(t => !t.archived).slice(0, 4).map(t => t.id));
  for (const id of ids) await step(`open thread ${id.slice(0, 6)}`, () => p.click(`.th[data-id="${id}"]`));
  await step("open Diff panel", () => p.click('[data-panel="diff"]'));
  await step("close Diff panel", () => p.click('[data-panel="diff"]'));
  await step("switch to Work mode", () => p.click('.modesw [data-mode="cowork"]'));
  await step("switch to Code mode", () => p.click('.modesw [data-mode="code"]'));
  await step("new thread", () => p.evaluate(() => newThread()));
  await step("toggle Steps", () => p.click("#stepsBtn"));
  check("no page errors", !errs.length, errs.join(" | "));
  const passed = results.filter(r => r[1]).length;
  console.log(`\n${passed}/${results.length} passed`);
  await b.close();
})();
