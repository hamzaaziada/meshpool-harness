// Click every control in every app state, for every harness: home, a finished thread, a running task (mid-run) and
// a task waiting for approval. Two levels deep: a click that opens a menu, popover or dialog gets each of its items
// clicked too (reopening it each time). After every click the app must show no page error, no server error, and
// still respond. Usage: node crawl_all.js OUT_DIR PORT PROJECT
const {chromium} = require("playwright");
const [out, port, proj] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;
const DENY = /^(quit|delete|remove|remove from meshpool|disconnect|sign in|sign in again|install|connect|use the github login|sign in with github|create a token|add project folder|add a folder|choose folder|folders i choose|my whole home folder|allow all|ask again|stop|interrupt|undo|undo turn|allow|deny|allow all edits.*|allow all commands.*|allow this tool.*|run now|open the skills folder|open.*finder|open.*explorer|open in code editor|open settings|open data folder|send|attach images or files)$/i;
const results = []; let clicks = 0;
const check = (name, ok, detail = "") => { results.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + String(detail).slice(0, 400))); };

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({viewport: {width: 1440, height: 900}});
  const p = await ctx.newPage();
  let errs = [];
  p.on("pageerror", e => errs.push("pageerror: " + e.message));
  p.on("console", m => { if (m.type() === "error" && !/status of 4\d\d|favicon/.test(m.text())) errs.push("console: " + m.text()); });
  p.on("response", r => { if (r.url().includes("/api/") && r.status() >= 500) errs.push(`HTTP ${r.status()} ${r.request().method()} ${r.url().replace(BASE, "").split("?")[0]}`); });
  p.on("dialog", d => d.dismiss());
  p.on("filechooser", fc => fc.setFiles([]).catch(() => {}));
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(BASE + "/"); await p.waitForSelector(".hero");
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);

  const scan = () => p.evaluate(() => {
    const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
    const top = [...document.querySelectorAll(".dlg-scrim, .scrim, .pop, .ctx, .menu")].pop();
    const root = top || document;
    const els = [...root.querySelectorAll("button, [role=button], summary, .opt, input[type=checkbox], .th, .proj-h, a[href]:not([href^=http])")]
      .filter(e => vis(e) && !e.disabled && !e.closest("[hidden]") && !e.closest(".appr"));
    const seen = {}; const out = [];
    els.forEach(e => {
      const label = (e.getAttribute("aria-label") || e.textContent || e.title || "").replace(/\s+/g, " ").trim().slice(0, 60);
      const data = Object.entries(e.dataset).filter(([k]) => k !== "crawl").map(([k, v]) => `${k}=${String(v).slice(0, 30)}`).join(",");
      let key = `${e.tagName}|${e.id}|${data}|${label}`;
      seen[key] = (seen[key] || 0) + 1; if (seen[key] > 1) key += "#" + seen[key];
      e.dataset.crawl = key; out.push({key, label, layer: !!top});
    });
    return out;
  });
  const clickKey = async key => {
    const ok = await p.evaluate(k => { const e = [...document.querySelectorAll("[data-crawl]")].find(x => x.dataset.crawl === k); if (!e) return false; e.scrollIntoView({block: "center"}); return true; }, key);
    if (!ok) return false;
    const loc = p.locator(`[data-crawl="${key.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`).first();
    try { await loc.click({timeout: 2500}); clicks++; return true; } catch (e) { return false; }
  };
  const layerOpen = () => p.evaluate(() => !!document.querySelector(".dlg-scrim, .scrim, .pop, .ctx, .menu"));
  const closeLayers = async () => {
    for (let i = 0; i < 4 && await layerOpen(); i++) {
      if (await p.$(".dlg [data-a='0']")) await p.click(".dlg [data-a='0']").catch(() => {});
      else await p.keyboard.press("Escape");
      await p.waitForTimeout(150);
    }
    await p.evaluate(() => document.querySelectorAll(".scrim, .pop, .ctx, .menu").forEach(x => x.remove()));
  };
  const alive = () => p.evaluate(() => typeof S === "object" && !!document.querySelector("#input")).catch(() => false);

  async function crawl(state, restore) {
    const before = errs.length; const c0 = clicks;
    await restore();
    const list = (await scan()).filter(x => !DENY.test(x.label));
    for (const item of list) {
      await restore();
      const items = await scan(); if (!items.find(x => x.key === item.key)) continue;
      if (!await clickKey(item.key)) continue;
      await p.waitForTimeout(300);
      if (await layerOpen()) {                                    // level 2: everything in the menu/popover/dialog
        const sub = (await scan()).filter(x => x.layer && !DENY.test(x.label) && !/close settings/i.test(x.label));
        for (const s2 of sub.slice(0, 40)) {
          await restore();
          if (!(await scan()).find(x => x.key === item.key) || !await clickKey(item.key)) break;
          await p.waitForTimeout(250);
          if (!(await scan()).find(x => x.key === s2.key)) continue;
          await clickKey(s2.key); await p.waitForTimeout(250);
          await closeLayers();
        }
      }
      await closeLayers();
      if (!await alive()) { check(`${state}: app still responds after "${item.label}"`, false); await p.goto(BASE + "/"); await p.waitForSelector(".hero"); }
    }
    check(`${state}: ${clicks - c0} clicks, no errors`, errs.length === before, errs.slice(before).join(" | "));
  }

  // ---------- home
  await crawl("home (cowork)", async () => { await closeLayers(); await p.evaluate(() => { if (S.cur) newThread(); if (S.appMode !== "cowork") setAppMode("cowork"); }); await p.waitForTimeout(150); });
  await crawl("home (code)", async () => { await closeLayers(); await p.evaluate(() => { if (S.cur) newThread(); if (S.appMode !== "code") setAppMode("code"); }); await p.waitForTimeout(150); });

  for (const [hid, kind] of [["meshpool-code", "code"], ["pi", "code"], ["hermes", "cowork"], ["meshpool-work", "cowork"]]) {
    const mk = async (mode, text) => {
      const v = await api("/api/agent", {workdir: proj, harness: hid, kind, mode});
      await p.evaluate(([id, k]) => { if (S.appMode !== k) setAppMode(k); openThread(id); }, [v.id, kind]);
      await p.waitForTimeout(1500);
      await api(`/api/agent/${v.id}/send`, {text});
      return v.id;
    };
    const state = id => p.evaluate(i => api(`/api/agent/${i}?since=1000000`).then(v => v.state), id);
    const open = async id => { await closeLayers(); await p.evaluate(([i, k]) => { if (S.appMode !== k) setAppMode(k); if (S.cur?.id !== i) openThread(i); }, [id, kind]); await p.waitForTimeout(200); };
    // finished thread
    const done = await mk("auto", "say hello and create hello.py");
    for (let i = 0; i < 120 && await state(done) !== "idle"; i++) await p.waitForTimeout(500);
    await crawl(`[${hid}] finished thread`, () => open(done));
    // running task (mid-run): keep it busy
    let busy = await mk("auto", "long task please");
    const keepBusy = async () => { if (await state(busy) === "idle") { await api(`/api/agent/${busy}/send`, {text: "long task please"}); await p.waitForTimeout(1200); } await open(busy); };
    await p.waitForTimeout(3000);
    await crawl(`[${hid}] mid-run`, keepBusy);
    check(`[${hid}] task still finishes after mid-run clicking`, await (async () => { for (let i = 0; i < 120; i++) { if (await state(busy) === "idle") return true; await p.waitForTimeout(500); } return false; })());
    // waiting for approval
    const wait = await mk("ask", "say hello and create hello.py");
    for (let i = 0; i < 120 && await state(wait) !== "waiting"; i++) await p.waitForTimeout(500);
    check(`[${hid}] reaches the approval state`, await state(wait) === "waiting");
    await crawl(`[${hid}] waiting for approval`, () => open(wait));
    check(`[${hid}] approval still pending and answerable after clicking around`, await state(wait) === "waiting" && !!(await p.$(".appr:not(.done) [data-d]")));
    await p.click(".appr:not(.done) [data-d]");
    for (let i = 0; i < 120 && await state(wait) !== "idle"; i++) { const y = await p.$(".appr:not(.done) [data-d]"); if (y) await y.click().catch(() => {}); await p.waitForTimeout(500); }
    check(`[${hid}] approval answered from the UI, task finishes`, await state(wait) === "idle");
  }
  // settings opened mid-run, every page
  check("total clicks", clicks > 200, clicks);
  await b.close();
  const ok = results.filter(r => r[1]).length; console.log(`\n${ok}/${results.length} passed, ${clicks} clicks`); results.filter(r => !r[1]).forEach(r => console.log("  failed:", r[0]));
  process.exit(ok === results.length ? 0 : 1);
})().catch(e => { console.error("CRASH", e.stack); process.exit(2); });
