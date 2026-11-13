const {chromium} = require("playwright");
(async () => {
  const out = process.argv[2]; const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = []; p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
  await p.addInitScript(() => { try { localStorage.clear(); localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto("http://127.0.0.1:8799/"); await p.waitForSelector(".hero");
  // composer while busy: add-on, then stop
  // a folder only shows in the mode it has threads in, so switch if needed
  await p.evaluate(() => { if (!document.querySelector(".proj[data-p='/tmp/proj1']")) setAppMode(S.appMode === "code" ? "cowork" : "code"); }); await p.waitForTimeout(400);
  await (await p.$(".proj[data-p='/tmp/proj1'] .proj-h")).hover(); await p.click(".proj[data-p='/tmp/proj1'] [data-newin]");
  await p.fill("#input", "long task please"); await p.keyboard.press("Enter");
  await p.waitForFunction(() => S.cur && S.cur.view && S.cur.view.state === "working", null, {timeout: 30000}); await p.waitForTimeout(2500);
  await p.fill("#input", "also mention the color blue"); await p.screenshot({path: out + "/c1-busy-typing.png"});
  await p.keyboard.press("Enter"); await p.waitForTimeout(1500); await p.screenshot({path: out + "/c2-added.png"});
  await p.keyboard.press("Escape"); const t0 = Date.now();
  await p.waitForFunction(() => S.cur.view.state === "idle", null, {timeout: 30000}); console.log("stopped in", Date.now() - t0, "ms");
  await p.waitForTimeout(800); await p.screenshot({path: out + "/c3-stopped.png"});
  for (const s of ["personal", "scheduled", "web", "connectors", "permissions"]) {
    if (!(await p.$(".scrim"))) await p.click("#openSettings");
    await p.click(`.mnav [data-s=${s}]`); await p.waitForTimeout(900); await p.screenshot({path: out + `/s-${s}.png`});
  }
  await p.evaluate(() => { document.querySelector(".scrim")?.remove(); permStep(); }); await p.waitForTimeout(800); await p.screenshot({path: out + "/p-onboard.png"});
  console.log(errs.length ? errs.join("\n") : "no page errors"); await b.close();
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
