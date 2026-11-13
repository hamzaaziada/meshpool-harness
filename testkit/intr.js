const {chromium} = require("playwright");
(async () => {
  const out = process.argv[2]; const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto("http://127.0.0.1:8799/"); await p.waitForSelector(".hero");
  const listed = (await p.evaluate(() => api("/api/harnesses"))).harnesses.map(x => x.id);
  for (const h of ["meshpool-code", "pi", "hermes"]) {
    if (!listed.includes(h)) { console.log(h, "SKIP: not installed here"); continue; }   // e.g. no Pi in a cloud container
    const id = await p.evaluate(async h => (await post("/api/agent", {workdir: "/tmp/proj1", harness: h, mode: "auto", kind: "code"})).id, h);
    await p.evaluate(id => openThread(id, true), id); await p.waitForTimeout(3000);
    await p.fill("#input", "long task please"); await p.keyboard.press("Enter");
    await p.waitForFunction(() => S.cur.view && S.cur.view.state === "working", null, {timeout: 30000}); await p.waitForTimeout(5000);
    await p.fill("#input", "what is the time"); if (h === "pi") await p.screenshot({path: out + "/i1-busy.png"});
    const t0 = Date.now(); await p.click("#intrBtn");
    await p.waitForFunction(() => (S.cur.log || []).some(e => e.type === "assistant" && /time/.test(e.text || "")), null, {timeout: 60000}).catch(() => {});
    const log = await p.evaluate(() => (S.cur.log || []).filter(e => ["user", "assistant", "status", "interrupted"].includes(e.type)).map(e => e.type + ":" + (e.text || "").slice(0, 50)));
    console.log(h, "interrupt -> answered in", Date.now() - t0, "ms |", JSON.stringify(log.slice(-5)));
    if (h === "pi") await p.screenshot({path: out + "/i2-after.png"});
  }
  console.log(errs.length ? errs.join("\n") : "no page errors"); await b.close();
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
