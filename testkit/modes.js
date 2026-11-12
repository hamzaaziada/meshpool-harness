const {chromium} = require("playwright");
(async () => {
  const out = process.argv[2]; const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = []; p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
  await p.addInitScript(() => { try { localStorage.clear(); } catch (e) {} });
  await p.goto("http://127.0.0.1:8799/"); await p.waitForSelector(".hero"); await p.screenshot({path: out + "/m1-cowork-home.png"});
  // cowork thread with the default cowork harness
  await (await p.$(".proj[data-p='/tmp/proj2'] .proj-h")).hover(); await p.click(".proj[data-p='/tmp/proj2'] [data-newin]");
  await p.fill("#input", "Write a word doc report"); await p.keyboard.press("Enter");
  await p.waitForFunction(() => S.cur && S.cur.view && S.cur.view.state === "idle" && (S.cur.log || []).some(e => e.type === "turn_end"), null, {timeout: 120000}); await p.waitForTimeout(1500); await p.screenshot({path: out + "/m2-cowork-done.png"});
  // code mode
  await p.click(".modesw [data-mode=code]"); await p.waitForTimeout(500); await p.screenshot({path: out + "/m3-code-home.png"});
  await (await p.$(".proj[data-p='/tmp/proj1'] .proj-h")).hover(); await p.click(".proj[data-p='/tmp/proj1'] [data-newin]");
  await p.click("[data-panel=git]"); await p.waitForTimeout(4000); await p.screenshot({path: out + "/m4-code-git.png"});
  await p.click("#openSettings"); await p.click(".mnav [data-s=harnesses]"); await p.waitForTimeout(600); await p.screenshot({path: out + "/m5-harness-defaults.png"});
  await p.click(".mnav [data-s=connectors]"); await p.waitForSelector("#cnl"); await p.screenshot({path: out + "/m6-connectors.png"});
  console.log(errs.length ? errs.join("\n") : "no page errors"); await b.close();
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
