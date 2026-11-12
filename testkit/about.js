const {chromium} = require("playwright");
(async () => { const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1440, height: 900}}); const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto("http://127.0.0.1:8799/"); await p.waitForSelector(".hero"); await p.click("#openSettings"); await p.click(".mnav [data-s=about]"); await p.waitForTimeout(800);
  await p.screenshot({path: process.argv[2]}); console.log(errs.join("\n") || "no page errors"); await b.close(); })();
