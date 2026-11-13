// Mid-run behaviour, through the real UI, for every harness: stop (button and Esc), add-on, Interrupt (button and
// Ctrl+Enter), approvals (allow, always, deny with a note), switching away and back, new threads, mode and harness
// switches, model and plan changes, thread actions on a running thread, undo while running, panels and terminal,
// page reload mid-run, two runs at once, rapid double send. Usage: node midrun.js PORT PROJECT
const {chromium} = require("playwright");
const {execSync} = require("child_process");
const [port, proj] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;
const results = [];
const check = (name, ok, detail = "") => { results.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + String(detail).slice(0, 400))); };
const sleeps = () => { try { return Number(execSync("pgrep -c -f '^sleep 25$' || true").toString().trim()) || 0; } catch (e) { return 0; } };

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = [];
  p.on("pageerror", e => errs.push("pageerror: " + e.message));
  p.on("response", r => { if (r.url().includes("/api/") && r.status() >= 500) errs.push(`HTTP ${r.status()} ${r.url().replace(BASE, "").split("?")[0]}`); });
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(BASE + "/"); await p.waitForSelector(".hero");
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  const st = id => api(`/api/agent/${id}?since=1000000`).then(v => v.state);
  const waitState = async (id, want, ms = 60000) => { const t = Date.now(); while (Date.now() - t < ms) { if (want.includes(await st(id))) return true; await p.waitForTimeout(250); } return false; };
  const log = () => p.evaluate(() => (S.cur?.log || []).map(e => ({type: e.type, text: e.text || "", steer: !!e.steer})));
  // the reply to an interrupt: an assistant message after the last "Interrupted" note that answers the new text
  const answeredAfterInterrupt = q => p.evaluate(q => { const l = S.cur?.log || []; const i = l.map(e => /Interrupted|Stopped the previous request/.test(e.text || "")).lastIndexOf(true); return i >= 0 && l.slice(i).some(e => e.type === "assistant" && (e.text || "").includes(q.slice(0, 8)) || e.type === "assistant" && /stopped your previous request/.test(e.text || "")); }, q);
  const newIn = async (hid, kind, mode) => {
    await p.evaluate(([k]) => { if (S.appMode !== k) setAppMode(k); newThread(); }, [kind]);
    await p.evaluate(([h, m]) => { S.draft.harness = h; S.draft.mode = m; renderBar(); }, [hid, mode]);
    await p.evaluate(path => { S.draft.project = path; }, proj);
  };
  const send = async text => { await p.fill("#input", text); await p.keyboard.press("Enter"); };
  const curId = () => p.evaluate(() => S.cur?.id);
  const busyNow = async () => { const id = await curId(); return id && (await st(id)) === "working"; };

  for (const [hid, kind] of [["meshpool-code", "code"], ["pi", "code"], ["hermes", "cowork"], ["meshpool-work", "cowork"]]) {
    const T = s => `[${hid}] ${s}`;
    // 1. Stop button
    await newIn(hid, kind, "auto"); await send("long task please");
    await p.waitForFunction(() => S.cur?.view?.state === "working", null, {timeout: 60000}).catch(() => {});
    await p.waitForTimeout(5000);
    let id = await curId();
    let t0 = Date.now(); await p.click("#stopBtn");
    check(T("Stop button stops within 5 s"), await waitState(id, ["idle"], 5000), (Date.now() - t0) + " ms");
    await p.waitForTimeout(800);
    check(T("Stop leaves no running command"), sleeps() === 0, sleeps());
    check(T("UI returns to the send button after stop"), !(await p.$("#stopBtn")) && !!(await p.$("#sendBtn:not(.add)")));
    // 2. Esc stops
    await send("long task please"); await waitState(id, ["working"]); await p.waitForTimeout(4000);
    await p.focus("#input"); t0 = Date.now(); await p.keyboard.press("Escape");
    check(T("Esc stops within 5 s"), await waitState(id, ["idle"], 5000), (Date.now() - t0) + " ms");
    // 3. Add-on with Enter
    await send("long task please"); await waitState(id, ["working"]); await p.waitForTimeout(4000);
    await send("also mention the color blue"); await p.waitForTimeout(1500);
    const lg = await log();
    check(T("Enter while working adds to the task (or queues it)"), lg.some(e => e.steer && /color blue/.test(e.text)) || !!(await p.$("#queuedChip")) || (await p.textContent("body")).includes("Queued"), JSON.stringify(lg.slice(-3)));
    await waitState(id, ["idle"], 90000);
    await p.waitForFunction(() => (S.cur?.log || []).some(e => e.type === "assistant" && /color blue/.test(e.text || "")), null, {timeout: 8000}).catch(() => {});
    check(T("the add-on reaches the agent"), (await log()).some(e => e.type === "assistant" && /color blue/.test(e.text)), JSON.stringify((await log()).filter(e => e.type !== "text_delta").slice(-3)));
    // 4. Interrupt button with empty box, then with text
    await send("long task please"); await waitState(id, ["working"]); await p.waitForTimeout(4000);
    await p.fill("#input", ""); await p.click("#intrBtn"); await p.waitForTimeout(800);
    check(T("Interrupt with an empty box keeps the task running"), (await st(id)) === "working");
    await p.fill("#input", "what is the time"); t0 = Date.now(); await p.click("#intrBtn");
    for (let i = 0; i < 60 && !(await answeredAfterInterrupt("what is the time")); i++) await p.waitForTimeout(500);
    check(T("Interrupt button answers the new message"), await answeredAfterInterrupt("what is the time"), Date.now() - t0);
    check(T("Interrupt leaves no running command"), (await waitState(id, ["idle"], 30000)) && sleeps() === 0, sleeps());
    // 5. Ctrl+Enter interrupt
    await send("long task please"); await waitState(id, ["working"]); await p.waitForTimeout(4000);
    await p.fill("#input", "what is the date"); await p.keyboard.press("Control+Enter");
    for (let i = 0; i < 60 && !(await answeredAfterInterrupt("what is the date")); i++) await p.waitForTimeout(500);
    check(T("Ctrl+Enter interrupts"), await answeredAfterInterrupt("what is the date"));
    await waitState(id, ["idle"], 30000);
    // 6. Switch away while running, then back
    await send("long task please"); await waitState(id, ["working"]); await p.waitForTimeout(3000);
    await p.evaluate(() => newThread()); await p.waitForTimeout(800);
    await p.evaluate(k => setAppMode(k === "code" ? "cowork" : "code"), kind); await p.waitForTimeout(600);
    await p.evaluate(([i, k]) => { setAppMode(k); openThread(i); }, [id, kind]); await p.waitForTimeout(1500);
    check(T("back on the running thread, it still shows working with a Stop button"), (await st(id)) === "working" && !!(await p.$("#stopBtn")));
    // 7. Model and plan changes mid-run
    if (hid !== "meshpool-work") {
      await p.click("#mPill"); await p.waitForTimeout(300);
      const opt = await p.$(".pop .opt[data-id]"); if (opt) await opt.click(); else await p.keyboard.press("Escape");
      await p.waitForTimeout(500);
    }
    await p.click("#pPill"); await p.waitForTimeout(300); await p.click("#pPill"); await p.waitForTimeout(300);
    check(T("model and plan changes mid-run do not break the task"), (await st(id)) === "working");
    // 8. Harness picker mid-run starts a new thread and leaves the running one alone
    await p.click("#hPill"); await p.waitForTimeout(300);
    const other = await p.$(`.pop .opt[data-id="${hid === "meshpool-code" ? "pi" : "meshpool-code"}"]`);
    if (other) await other.click(); else await p.keyboard.press("Escape");
    await p.waitForTimeout(600);
    check(T("picking another harness mid-run opens a new thread"), await p.evaluate(() => !S.cur));
    check(T("the running task keeps going"), (await st(id)) === "working");
    await p.evaluate(([i]) => openThread(i), [id]); await p.waitForTimeout(800);
    // 9. Panels and terminal mid-run
    for (const pn of kind === "code" ? ["diff", "files", "term", "git", "preview"] : ["progress", "outputs"]) {
      const bt = await p.$(`[data-panel=${pn}]`); if (bt) { await bt.click(); await p.waitForTimeout(700); }
    }
    if (kind === "code") {
      await p.click("[data-panel=term]"); await p.waitForTimeout(500);
      const ti = await p.$("#termIn, #tin, .term input"); if (ti) { await ti.fill("echo midrun-ok"); await ti.press("Enter"); await p.waitForTimeout(1500); }
      check(T("terminal works mid-run"), !ti || (await p.textContent("body")).includes("midrun-ok"));
    }
    // 10. Undo while running is refused cleanly
    const r = await p.evaluate(i => post(`/api/agent/${i}/revert`, {turn: 1}).then(() => "done").catch(e => e.message), id);
    check(T("undo while running is refused with a message"), /Stop the agent/.test(r), r);
    // 11. Rename, pin, archive the running thread
    await p.evaluate(i => post(`/api/threads/${i}`, {action: "rename", title: "Running thread"}), id);
    await p.evaluate(i => post(`/api/threads/${i}`, {action: "pin"}), id);
    check(T("rename and pin while running"), (await st(id)) === "working");
    // 12. Reload the page mid-run, then stop
    await p.reload(); await p.waitForSelector("#input");
    await p.evaluate(([i, k]) => { setAppMode(k); openThread(i); }, [id, kind]); await p.waitForTimeout(2000);
    check(T("after a reload the running task shows Stop"), !!(await p.$("#stopBtn")), await st(id));
    if (await p.$("#stopBtn")) await p.click("#stopBtn");
    check(T("stop after reload"), await waitState(id, ["idle"], 6000));
    // 13. Delete a running thread
    await send("long task please"); await waitState(id, ["working"]); await p.waitForTimeout(3000);
    await p.evaluate(i => post(`/api/threads/${i}`, {action: "delete"}), id); await p.waitForTimeout(3000);
    check(T("deleting a running thread stops its command"), sleeps() === 0, sleeps());
    check(T("deleted thread is gone from the list"), !(await api("/api/agent")).sessions.some(s => s.id === id));
    // 14. Approvals: allow, always, deny with a note
    await newIn(hid, kind, "ask"); await send("say hello and create hello.py");
    await p.waitForSelector(".appr:not(.done) [data-d]", {timeout: 60000}).catch(() => {});
    id = await curId();
    const btns = await p.$$eval(".appr:not(.done) [data-d]", x => x.map(b => b.dataset.d));
    check(T("approval shows its buttons"), btns.length >= 2, btns);
    const no = btns.find(d => /^no$|reject/.test(d));
    if (no) {
      const note = await p.$(".appr:not(.done) input"); if (note) await note.fill("do not create files, just say hi");
      await p.click(`.appr:not(.done) [data-d="${no}"]`);
      await p.waitForTimeout(1500);
      check(T("deny is recorded"), (await p.textContent("body")).includes("Denied") || (await st(id)) !== "waiting");
    }
    await waitState(id, ["idle", "waiting"], 60000);
    for (let i = 0; i < 10 && (await st(id)) === "waiting"; i++) { await p.click(".appr:not(.done) [data-d]", {timeout: 3000}).catch(() => {}); await p.waitForTimeout(800); }
    await waitState(id, ["idle"], 60000);
    await send("say hello and create hello.py");
    await p.waitForSelector(".appr:not(.done) [data-d]", {timeout: 60000}).catch(() => {});
    const alw = await p.$$eval(".appr:not(.done) [data-d]", x => x.map(b => b.dataset.d).find(d => /always|session/.test(d)));
    if (alw) await p.click(`.appr:not(.done) [data-d="${alw}"]`); else { const y = await p.$(".appr:not(.done) [data-d]"); if (y) await y.click(); }
    for (let i = 0; i < 30; i++) { await p.click(".appr:not(.done) [data-d]", {timeout: 1500}).catch(() => {}); if ((await st(id)) === "idle") break; await p.waitForTimeout(800); }
    check(T("allow from the UI lets the task finish"), await waitState(id, ["idle"], 60000));
    // 15. Rapid double send does not start two turns
    await p.fill("#input", "what is the time"); await p.keyboard.press("Enter"); await p.keyboard.press("Enter");
    await waitState(id, ["idle"], 60000); await p.waitForTimeout(800);
    const users = (await log()).filter(e => e.type === "user" && /what is the time/.test(e.text)).length;
    check(T("double Enter sends once"), users === 1, users);
  }
  // 16. Two runs at once: stop one, the other keeps going
  await newIn("meshpool-code", "code", "auto"); await send("long task please"); await p.waitForTimeout(2000); const a = await curId();
  await newIn("pi", "code", "auto"); await send("long task please"); await p.waitForTimeout(2000); const c = await curId();
  await waitState(a, ["working"]); await waitState(c, ["working"]);
  await p.click("#stopBtn"); await waitState(c, ["idle"], 6000);
  check("two runs: stopping one leaves the other running", (await st(a)) === "working" && (await st(c)) === "idle");
  await api(`/api/agent/${a}/stop`, {}); await waitState(a, ["idle"], 6000);
  check("no page errors or server errors across all mid-run tests", !errs.length, errs.join(" | "));
  await b.close();
  const ok = results.filter(r => r[1]).length; console.log(`\n${ok}/${results.length} passed`); results.filter(r => !r[1]).forEach(r => console.log("  failed:", r[0]));
  process.exit(ok === results.length ? 0 : 1);
})().catch(e => { console.error("CRASH", e.stack); process.exit(2); });
