/* Settings > Company: the company look decks are built in. Add a few existing decks and Meshpool learns the
   colours, fonts, logo, name and slide master (engines/forge/brand.py); every field can be changed here.
   Kept in its own file and hooked into the settings dialog from outside app.js (SECTIONS and renderSection). */
(() => {
  if (typeof SECTIONS === "undefined" || typeof renderSection !== "function") return;
  const at = SECTIONS.findIndex(s => s[0] === "personal");
  SECTIONS.splice(at < 0 ? 1 : at + 1, 0, ["company", "Company and brand"]);

  const css = document.createElement("style");
  css.textContent = `
.co-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 16px}
.co-top select{min-width:220px;border:1px solid var(--line-3);border-radius:var(--r-s);padding:6px 10px;background:var(--surface);color:var(--ink)}
.co-drop{border:1px dashed var(--line-3);border-radius:var(--r-m);padding:16px;text-align:center;color:var(--ink-2);font-size:var(--fs-s);cursor:pointer;margin:0 0 20px}
.co-drop:hover,.co-drop.over{background:var(--hover)}
.co-drop b{color:var(--ink);font-weight:500}
.co-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px;margin:0 0 16px}
.co-sw{display:flex;gap:10px;align-items:center;border:1px solid var(--line);border-radius:var(--r-s);padding:8px 10px}
.co-sw input[type=color]{width:34px;height:34px;border:1px solid var(--line-3);border-radius:var(--r-s);padding:0;background:none;flex:none;cursor:pointer}
.co-sw span{display:flex;flex-direction:column;min-width:0;font-size:var(--fs-s);font-weight:500}
.co-sw small{font-weight:400;color:var(--ink-3);font-size:var(--fs-xs);line-height:1.3}
.co-sw code{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2)}
.co-h{font-size:var(--fs-m);font-weight:600;margin:20px 0 8px}
.co-logo{display:flex;gap:12px;align-items:center;margin:0 0 16px}
.co-logo .im{width:180px;height:56px;border:1px solid var(--line);border-radius:var(--r-s);display:grid;place-items:center;background:#fff;color:#6B6B6B;font-size:var(--fs-xs)}
.co-logo img{max-width:170px;max-height:48px}
.co-row{display:flex;gap:12px;flex-wrap:wrap}.co-row .field{flex:1 1 220px}
.co-opts{display:flex;flex-direction:column;gap:6px;margin:0 0 16px;font-size:var(--fs-s)}
.co-opts label{display:flex;gap:8px;align-items:flex-start}.co-opts small{display:block;color:var(--ink-3);font-size:var(--fs-xs)}
.co-prev{width:100%;max-width:560px;aspect-ratio:16/9;border:1px solid var(--line-3);border-radius:var(--r-s);background:#fff;position:relative;overflow:hidden;color:#111;margin:0 0 16px}
.co-prev .co-t{position:absolute;left:4.5%;top:6%;right:4.5%;font-weight:700;font-size:15px;line-height:1.2}
.co-prev .co-r{position:absolute;left:4.5%;right:4.5%;top:19%;height:1px}
.co-prev .co-bars{position:absolute;left:4.5%;top:25%;width:52%;height:58%;display:flex;align-items:flex-end;gap:6%;border-bottom:1px solid #999}
.co-prev .co-bars i{flex:1;display:flex;flex-direction:column;justify-content:flex-end}
.co-prev .co-side{position:absolute;left:61%;right:4.5%;top:25%;height:58%;padding:3%;font-size:9px;line-height:1.35}
.co-prev .co-side b{display:block;font-size:10px;margin-bottom:4px}
.co-prev .co-hl{margin-top:8px;padding:3px 5px;font-weight:700}
.co-prev .co-call{margin-top:6px;padding:4px 6px;color:#fff;font-weight:600}
.co-prev .co-ft{position:absolute;left:4.5%;right:4.5%;bottom:4%;display:flex;justify-content:space-between;align-items:center;font-size:7px;border-top:1px solid #999;padding-top:3px}
.co-prev .co-ft img{max-height:12px;max-width:70px}
.co-note{font-size:var(--fs-s);color:var(--ink-2);margin:0 0 6px}
.co-note.bad{color:var(--del)}
`;
  document.head.appendChild(css);

  const CO = {data: null, sel: null};
  const hex = v => "#" + String(v || "000000").replace(/^#/, "").toUpperCase();
  const fontsList = ["Arial", "Georgia", "Calibri", "Cambria", "Aptos", "Helvetica", "Segoe UI", "Times New Roman", "Garamond", "Verdana", "Century Gothic", "Franklin Gothic Book", "Gill Sans MT"];

  async function uploadAll(files) {
    const paths = [];
    for (const f of files) {
      const r = await api(`/api/upload?name=${encodeURIComponent(f.name)}`, {method: "POST", body: f, headers: {"Content-Type": "application/octet-stream"}});
      paths.push(r.path);
    }
    return paths;
  }

  function current() { return (CO.data?.profiles || []).find(p => p.id === CO.sel) || null; }

  function readForm(b) {
    const p = current(); if (!p) return null;
    const colors = {...p.colors};
    $$("[data-role]", b).forEach(i => colors[i.dataset.role] = i.value.replace("#", "").toUpperCase());
    return {id: p.id, name: $("#coName", b).value.trim(), footer: $("#coFoot", b).value.trim(), colors,
            fonts: {major: $("#coMajor", b).value.trim() || "Georgia", minor: $("#coMinor", b).value.trim() || "Arial"},
            template_mode: ($("[name=coMode]:checked", b) || {}).value || "forge"};
  }

  function preview(b) {
    const f = readForm(b); const p = current(); if (!f) return;
    const c = k => hex(f.colors[k]);
    const pv = $("#coPrev", b);
    const vals = [42, 52, 64, 80, 100], top = [8, 16, 26, 34, 38];
    pv.style.fontFamily = `"${f.fonts.minor}", Arial, sans-serif`;
    pv.innerHTML = `<div class="co-t" style="font-family:'${esc(f.fonts.major)}',Georgia,serif;color:${c("dk1")}">Revenue grew 18% a year as the new segment scaled</div>
      <div class="co-r" style="background:${c("dk1")}"></div>
      <div class="co-bars">${vals.map((v, i) => `<i style="height:${v}%"><em style="display:block;height:${top[i]}%;background:${c("accent4")}"></em><em style="display:block;flex:1;background:${c("accent1")}"></em></i>`).join("")}</div>
      <div class="co-side" style="background:${c("lt2")};color:${c("dk1")}"><b>The new segment is now 38% of revenue</b>Core grew <strong style="color:${c("accent1")}">7%</strong> a year, in line with the market.
        <div class="co-hl" style="background:${c("accent2")};color:${c("dk1")}">B: bolt-on acquisition · 19%</div>
        <div class="co-call" style="background:${c("dk2")}">So what: recommend option B</div></div>
      <div class="co-ft" style="color:${c("accent6")}"><span>Source: illustrative figures</span><span>${p?.logo_data ? `<img src="${p.logo_data}" alt="">` : esc(f.footer || f.name || "Your company")}</span></div>`;
  }

  function draw(b, close) {
    const D = CO.data, ps = D.profiles;
    if (!CO.sel || !ps.some(p => p.id === CO.sel)) CO.sel = D.active || ps[0]?.id || null;
    const p = current();
    const roles = D.roles;
    b.innerHTML = `${close}<h3>Company and brand</h3>
      <p class="lead">Decks are built in your company's look. Add a few of your existing decks and Meshpool learns the colours, fonts, logo, name and slide master from them. Check what it found and change anything; the deck content is still written fresh each time.</p>
      ${ps.length ? `<div class="co-top"><select id="coSel" aria-label="Company">${ps.map(x => `<option value="${esc(x.id)}" ${x.id === CO.sel ? "selected" : ""}>${esc(x.name || "Unnamed company")}${x.id === D.active ? " (used for new decks)" : ""}</option>`).join("")}</select>
        <label class="switchrow" style="margin:0"><input type="checkbox" id="coActive" ${p && p.id === D.active ? "checked" : ""}> Use for new decks</label>
        <span style="flex:1"></span><button class="btn danger" id="coDel">Delete</button></div>` : ""}
      <div class="co-drop" id="coDrop" role="button" tabindex="0"><b>${ps.length ? "Add another company from its decks" : "Learn your look from your decks"}</b><br>Drop 1 to 5 PowerPoint files here (.pptx or .potx), or click to choose them.<input type="file" id="coFiles" accept=".pptx,.potx,.pptm" multiple hidden></div>
      <div class="res" id="coLearnRes"></div>
      ${p ? `
      ${(p.sources || []).length ? `<p class="co-note">Learned from ${p.sources.map(s => `${esc(s.file)} (${s.slides} slides)`).join(", ")}.</p>` : ""}
      ${(p.notes || []).map(n => `<p class="co-note">${esc(n)}</p>`).join("")}
      <div class="co-row"><label class="field"><span>Company name</span><input id="coName" value="${esc(p.name || "")}" placeholder="Acme Capital"><small>On the cover and, without a logo, in the footer.</small></label>
        <label class="field"><span>Footer line</span><input id="coFoot" value="${esc(p.footer || "")}" placeholder="Strictly confidential"><small>Used in the footer when it is short (30 characters or fewer).</small></label></div>
      <div class="co-h">Logo</div>
      <div class="co-logo"><div class="im">${p.logo_data ? `<img src="${p.logo_data}" alt="Logo">` : "No logo"}</div>
        <button class="btn" id="coLogoBtn">${p.logo_data ? "Replace" : "Upload"}</button>${p.logo_data ? `<button class="btn" id="coLogoDel">Remove</button>` : ""}<input type="file" id="coLogo" accept=".png,.jpg,.jpeg,.gif" hidden></div>
      <div class="co-h">Colours</div>
      <div class="co-grid">${roles.map(r => `<label class="co-sw"><input type="color" data-role="${r.key}" value="${hex(p.colors[r.key])}"><span>${esc(r.label)} <code data-hex="${r.key}">${hex(p.colors[r.key])}</code><small>${esc(r.use)}</small></span></label>`).join("")}</div>
      <div id="coProb">${(p.problems || []).map(x => `<p class="co-note bad">${esc(x)}</p>`).join("")}</div>
      <div class="co-h">Fonts</div>
      <div class="co-row"><label class="field"><span>Headings</span><input id="coMajor" list="coFonts" value="${esc(p.fonts?.major || "")}"></label>
        <label class="field"><span>Body text</span><input id="coMinor" list="coFonts" value="${esc(p.fonts?.minor || "")}"></label>
        <datalist id="coFonts">${fontsList.map(f => `<option value="${f}">`).join("")}</datalist></div>
      <div class="co-h">Layout</div>
      <div class="co-opts">
        <label><input type="radio" name="coMode" value="master" ${p.template_mode === "master" ? "checked" : ""} ${p.has_master ? "" : "disabled"}><span>Your slide master<small>${p.has_master ? "Your master's background and artwork, with Meshpool's page layouts on top." : esc("Not available: " + (p.master_note || "learn from a deck to use its master."))}</small></span></label>
        <label><input type="radio" name="coMode" value="forge" ${p.template_mode !== "master" ? "checked" : ""}><span>Meshpool layout in your colours<small>A clean white master with your colours, fonts and logo.</small></span></label></div>
      <div class="co-h">Preview</div>
      <div class="co-prev" id="coPrev" aria-label="Preview of a slide in this look"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="coSave">Save</button><button class="btn" id="coSample">Build a sample deck</button></div>
      <div class="res" id="coRes"></div>` : `<p class="co-note">Or <button class="linkbtn" id="coBlank">set the colours by hand</button>.</p>`}`;
    wire(b, close);
    if (p) preview(b);
  }

  function wire(b, close) {
    const redraw = () => draw(b, close);
    const drop = $("#coDrop", b), inp = $("#coFiles", b);
    const learn = async files => {
      files = [...files].filter(f => /\.(pptx|potx|pptm)$/i.test(f.name)).slice(0, 5);
      if (!files.length) { RES("coLearnRes").className = "res bad"; RES("coLearnRes").textContent = "Choose PowerPoint files (.pptx or .potx)."; return; }
      RES("coLearnRes").className = "res"; RES("coLearnRes").textContent = `Reading ${files.length} deck${files.length > 1 ? "s" : ""}…`;
      try {
        const paths = await uploadAll(files);
        const r = await post("/api/company/learn", {paths});
        CO.data = r; CO.sel = r.learned; redraw();
        RES("coLearnRes").className = "res ok"; RES("coLearnRes").textContent = "Learned. Check the colours, logo and name below, then save.";
      } catch (e) { RES("coLearnRes").className = "res bad"; RES("coLearnRes").textContent = e.message; }
    };
    drop.onclick = () => inp.click();
    drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inp.click(); } };
    inp.onchange = () => learn(inp.files);
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); learn(e.dataTransfer.files); };
    $("#coBlank", b)?.addEventListener("click", async () => {
      try { const r = await post("/api/company/save", {profile: {name: ""}}); CO.data = r; CO.sel = r.saved; redraw(); }
      catch (e) { toast(e.message); }
    });
    const p = current(); if (!p) return;
    $("#coSel", b).onchange = e => { CO.sel = e.target.value; redraw(); };
    $("#coActive", b).onchange = async e => {
      try { CO.data = await post("/api/company/activate", {id: e.target.checked ? p.id : null}); redraw(); toast(e.target.checked ? "New decks use this look" : "New decks use the Meshpool look"); }
      catch (err) { toast(err.message); }
    };
    $("#coDel", b).onclick = async () => {
      if (!confirm(`Delete ${p.name || "this company"}? Its learned colours, logo and master are removed.`)) return;
      try { CO.data = await post("/api/company/delete", {id: p.id}); CO.sel = null; redraw(); } catch (e) { toast(e.message); }
    };
    $$("[data-role]", b).forEach(i => i.oninput = () => { $(`[data-hex="${i.dataset.role}"]`, b).textContent = i.value.toUpperCase(); preview(b); });
    ["#coName", "#coFoot", "#coMajor", "#coMinor"].forEach(s => $(s, b).oninput = () => preview(b));
    $$("[name=coMode]", b).forEach(r => r.onchange = () => preview(b));
    const save = async () => {
      const r = await post("/api/company/save", {profile: readForm(b)});
      CO.data = r; return r;
    };
    $("#coSave", b).onclick = async () => {
      try { await save(); redraw(); const q = current()?.problems || []; RES("coRes").className = "res " + (q.length ? "bad" : "ok"); RES("coRes").textContent = q.length ? "Saved, but some colours are hard to read; see above." : "Saved. New decks use this look" + (CO.data.active === CO.sel ? "." : " when it is set to be used for new decks."); }
      catch (e) { RES("coRes").className = "res bad"; RES("coRes").textContent = e.message; }
    };
    $("#coSample", b).onclick = async () => {
      RES("coRes").className = "res"; RES("coRes").textContent = "Saving and building a three-slide sample…";
      try {
        await save();
        const r = await post("/api/company/sample", {id: CO.sel});
        redraw();
        RES("coRes").className = "res ok";
        RES("coRes").innerHTML = `Sample ready: <a href="${esc(r.url)}" download="${esc(r.file)}">${esc(r.file)}</a> · <button class="linkbtn" id="coOpen">Open it</button>`;
        $("#coOpen")?.addEventListener("click", () => post("/api/company/open", {id: CO.sel}).catch(e => toast(e.message)));
      } catch (e) { RES("coRes").className = "res bad"; RES("coRes").textContent = e.message; }
    };
    $("#coLogoBtn", b).onclick = () => $("#coLogo", b).click();
    $("#coLogo", b).onchange = async () => {
      const f = $("#coLogo", b).files[0]; if (!f) return;
      try {
        await save();
        CO.data = await api(`/api/company/logo?id=${encodeURIComponent(p.id)}&name=${encodeURIComponent(f.name)}`, {method: "POST", body: f, headers: {"Content-Type": "application/octet-stream"}});
        redraw();
      } catch (e) { toast(e.message, 5000); }
    };
    $("#coLogoDel", b)?.addEventListener("click", async () => {
      try { const r = await post("/api/company/save", {profile: {...readForm(b), remove_logo: true}}); CO.data = r; redraw(); } catch (e) { toast(e.message); }
    });
  }

  const base = renderSection;
  // eslint-disable-next-line no-global-assign
  renderSection = async function (sec) {
    if (sec !== "company") return base(sec);
    const b = $("#mbody");
    const close = `<button class="iconbtn mclose" aria-label="Close settings">${ICON.x}</button>`;
    try { CO.data = await api("/api/company"); }
    catch (e) { b.innerHTML = `${close}<h3>Company and brand</h3><div class="res bad">${esc(e.message)}</div>`; }
    if (CO.data) draw(b, close);
    b.scrollTop = 0;
    if (!b.dataset.coClose) { b.dataset.coClose = "1"; b.addEventListener("click", e => { if (e.target.closest(".mclose")) $(".scrim")?.remove(); }); }
  };
})();
