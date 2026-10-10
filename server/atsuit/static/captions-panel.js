// Caption settings for one room: what AT-LiveCaption's control panel did,
// for any tech. Tabs: Live, Audio & EQ, Vocabulary, Appearance, Corrections,
// Transcripts. Used inside the workspace's Captions window and on its own at
// /static/captions-control.html?room=<id>.
//   ATCaptionsPanel.mount(element, roomId)   ATCaptionsPanel.attach(roomId)
(function () {
  const css = `
  .capset { margin-top: .6rem; }
  .capset .tabs { margin-bottom: .6rem; }
  .capset .pane { display: none; } .capset .pane.on { display: block; }
  .capset .grid2 { display: grid; grid-template-columns: 9.5rem 1fr; gap: .35rem .6rem; align-items: center; }
  .capset .grid2 label { color: var(--muted); font-size: .85rem; }
  .capset .grid2 input[type=color] { padding: 0; height: 2rem; width: 3.2rem; }
  .capset .grid2 input[type=checkbox] { width: auto; }
  .capset .hint { color: var(--muted); font-size: .8rem; margin: .35rem 0; }
  .capset .slider { display: flex; gap: .6rem; align-items: center; } .capset .slider input { flex: 1; } .capset .slider span { min-width: 4.2rem; text-align: right; font-variant-numeric: tabular-nums; }
  .capset canvas.eq { width: 100%; height: 190px; display: block; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; touch-action: none; cursor: grab; }
  .capset .preview { background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: .6rem .8rem; min-height: 5rem; max-height: 14rem; overflow: auto; font-size: 1.05rem; }
  .capset .preview .partial { opacity: .65; }
  .capset .w { cursor: pointer; border-radius: 3px; } .capset .w:hover { outline: 1px solid var(--accent); }
  .capset .conf-mid { background: rgba(245,158,11,.25); } .capset .conf-low { background: rgba(239,68,68,.3); }
  .capset .hist { border-bottom: 1px solid var(--line); padding: .35rem 0; }
  .capset table { width: 100%; border-collapse: collapse; font-size: .88rem; } .capset td, .capset th { padding: .3rem .35rem; border-bottom: 1px solid var(--line); text-align: left; }
  .capset .surf { display: flex; gap: .3rem; margin-bottom: .5rem; }
  .capset .rec { display: inline-block; width: .7rem; height: .7rem; border-radius: 50%; background: var(--line); margin-right: .35rem; vertical-align: middle; }
  .capset .rec.on { background: var(--bad); box-shadow: 0 0 8px var(--bad); }`;

  const FIELDS = [
    ["font_family", "Font", "font"], ["font_size", "Size (px at 1080p)", "number", 12, 400, 1], ["font_weight", "Weight", "select", ["400", "500", "600", "700", "800", "900"]],
    ["text_align", "Alignment", "select", ["left", "center", "right"]], ["line_height", "Line height", "number", 0.8, 3, 0.05],
    ["text_color", "Text colour", "color"], ["background_color", "Line background", "color"], ["background_opacity", "Background opacity", "range", 0, 1, 0.05],
    ["page_color", "Screen colour", "color"], ["max_lines", "Lines", "number", 1, 20, 1], ["position", "Position", "select", ["bottom", "center", "top"]],
    ["hold_seconds", "Hold (seconds)", "number", 0, 120, 0.5], ["fade_seconds", "Fade (seconds)", "number", 0, 120, 0.1], ["show_disclaimer", "AI disclaimer", "check"],
  ];
  const FONTS = ["'Saira', 'Segoe UI', system-ui, -apple-system, sans-serif", "system-ui, -apple-system, 'Segoe UI', sans-serif", "Arial, Helvetica, sans-serif",
    "Verdana, sans-serif", "'Atkinson Hyperlegible', Verdana, sans-serif", "Georgia, serif", "'Courier New', monospace"];
  const SURF = { audience: "Audience screen", overlay: "Overlay", bar: "Subtitle bar" };

  function addStyle() {
    if (document.getElementById("capsetCss")) return;
    const s = document.createElement("style"); s.id = "capsetCss"; s.textContent = css; document.head.appendChild(s);
  }
  const confClass = (c) => (c >= 0.7 ? "" : c >= 0.4 ? "conf-mid" : "conf-low");

  // The EQ curve, the same RBJ peaking filters the server uses.
  function peakDb(f, f0, g, sr = 16000, q = 1.4) {
    if (!g) return 0;
    const A = Math.pow(10, g / 40), w0 = 2 * Math.PI * f0 / sr, al = Math.sin(w0) / (2 * q), cw = Math.cos(w0);
    const b = [1 + al * A, -2 * cw, 1 - al * A], a = [1 + al / A, -2 * cw, 1 - al / A], w = 2 * Math.PI * f / sr;
    const ev = (c) => { const re = c[0] + c[1] * Math.cos(w) + c[2] * Math.cos(2 * w), im = -(c[1] * Math.sin(w) + c[2] * Math.sin(2 * w)); return Math.hypot(re, im); };
    return 20 * Math.log10(ev(b) / ev(a));
  }

  async function mount(el, roomId) {
    const { esc, api, put, post, del, toast } = AT;
    addStyle();
    el.classList.add("capset");
    el.innerHTML = `
      <div class="tabs sub">${["Live", "Audio & EQ", "Vocabulary", "Appearance", "Corrections", "Transcripts"].map((t, i) => `<button data-tab="${i}" class="${i ? "" : "on"}">${t}</button>`).join("")}</div>
      <div class="pane on" data-pane="0">
        <div class="preview" data-k="preview"><span class="muted">Captions for this room appear here.</span></div>
        <p class="hint">Words the engine wasn't sure of are highlighted here only; caption screens always show plain text. Click a word to correct it.</p>
        <div class="row" style="flex-wrap:wrap"><input data-k="testText" class="grow" placeholder="Test caption: can everyone read this?"><button data-k="testPart" class="small">Send as live line</button><button data-k="test" class="small">Send test line</button><button data-k="clear" class="small danger">Clear the screens</button></div>
        <p class="hint">Caption screens for this room: <a target="_blank" data-k="lnkA">audience</a> · <a target="_blank" data-k="lnkO">overlay</a> · <a target="_blank" data-k="lnkB">subtitle bar</a>. A Linux screen gets one through Add a screen.</p>
      </div>
      <div class="pane" data-pane="1">
        <div class="row" style="justify-content:space-between"><b>Mic gain &amp; graphic EQ</b><button class="small" data-k="eqReset">Reset</button></div>
        <div class="slider" style="margin:.5rem 0"><label style="width:3rem">Gain</label><input type="range" data-k="gain" min="-20" max="20" step="0.5"><span data-k="gainV"></span></div>
        <canvas class="eq" data-k="eq"></canvas>
        <p class="hint">Drag the dots to shape the EQ. Behind them is the live input spectrum (RTA), after gain and EQ. The meter shows the level the recogniser hears.</p>
        <div class="meter" style="margin:.4rem 0"><i data-k="lvl"></i></div>
        <label class="row" style="gap:.4rem;margin-top:.5rem"><input type="checkbox" data-k="music" style="width:auto"> Show music and filler noises ("um um um") as [MUSIC]</label>
        <label class="row" style="gap:.4rem"><input type="checkbox" data-k="acr" style="width:auto"> Join spelled-out acronyms ("U K" → "UK")</label>
        <label class="row" style="gap:.4rem"><input type="checkbox" data-k="scase" style="width:auto"> Sentence case instead of capitals (vocabulary words keep their spelling)</label>
      </div>
      <div class="pane" data-pane="2">
        <textarea data-k="vocab" rows="8" placeholder="One name, term or phrase per line, e.g.&#10;Lovelace&#10;Dermatology&#10;NHS"></textarea>
        <p class="hint">Boosts names, jargon and acronyms for this event, spelled the way they're written. It helps most when the spelling is close to how the word sounds. Words the speech engine can't use are listed below. Rooms already captioning pick up changes at their next pause.</p>
        <div class="slider"><label style="width:7.5rem">Boost strength</label><input type="range" data-k="score" min="0.5" max="6" step="0.5"><span data-k="scoreV"></span></div>
        <p class="hint">Boost strength is one setting for the whole server (every room shares the speech engine).</p>
        <div class="row" style="justify-content:space-between"><label class="row" style="gap:.4rem"><input type="checkbox" data-k="enabled" style="width:auto"> Captions on for this room</label><button class="primary" data-k="saveVocab">Save &amp; apply</button></div>
        <p class="hint" data-k="skipped"></p>
      </div>
      <div class="pane" data-pane="3">
        <div class="surf">${Object.entries(SURF).map(([k, v], i) => `<button class="small ${i ? "" : "on"}" data-surf="${k}">${v}</button>`).join("")}</div>
        <div class="grid2" data-k="fields"></div>
        <div class="row" style="justify-content:space-between;margin-top:.7rem"><a target="_blank" data-k="open">Open this screen</a><span class="row"><button class="small" data-k="reset">Defaults</button><button class="primary" data-k="saveLook">Save</button></span></div>
        <p class="hint">Saving changes every caption screen in this room straight away.</p>
      </div>
      <div class="pane" data-pane="4">
        <p class="hint">Click a misheard word to fix it. That doesn't retrain the engine; it adds the right word to this room's vocabulary so it's boosted next time. <span class="conf-mid">Amber</span> and <span class="conf-low">red</span> words are ones the engine wasn't sure of.</p>
        <div data-k="hist" style="max-height:40vh;overflow:auto"></div>
        <div class="row" style="margin:.5rem 0"><button class="small" data-k="histRefresh">Refresh</button><button class="small" data-k="histClear">Clear list</button></div>
        <b class="small">Corrections made</b><div data-k="corr" class="small"></div>
      </div>
      <div class="pane" data-pane="5">
        <div class="row" style="justify-content:space-between"><span><span class="rec" data-k="recDot"></span><span data-k="recText">Not recording</span></span>
          <span class="row"><button class="small primary" data-k="recStart">Start recording</button><button class="small danger hidden" data-k="recStop">Stop recording</button></span></div>
        <label class="row" style="gap:.4rem;margin-top:.4rem"><input type="checkbox" data-k="record" style="width:auto"> Record automatically whenever the mic starts</label>
        <p class="hint">Finalised captions only, never audio. Start always begins a new file.</p>
        <div data-k="tx"></div>
      </div>`;
    const k = (n) => el.querySelector(`[data-k="${n}"]`);
    let s = await api(`/api/captions/${roomId}/settings`);
    let surface = "audience", tab = 0;

    el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => {
      tab = +b.dataset.tab;
      el.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x === b));
      el.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("on", +p.dataset.pane === tab));
      if (tab === 1) drawEq();
      if (tab === 4) loadHistory();
      if (tab === 5) loadTx();
    }));
    const save = async (body) => { s = await put(`/api/captions/${roomId}/settings`, body); return s; };

    // Live
    const finals = []; let partial = null;
    const wordsHtml = (f) => (f.words && f.words.length ? f.words.map((w) => `<span class="w ${confClass(w.confidence)}" data-w="${esc(w.text)}">${esc(w.text)}</span>`).join(" ") : esc(f.text));
    function renderPreview() {
      const html = finals.slice(-8).map((f) => `<div>${wordsHtml(f)}</div>`).join("") + (partial ? `<div class="partial">${wordsHtml(partial)}</div>` : "");
      k("preview").innerHTML = html || '<span class="muted">Captions for this room appear here.</span>';
      k("preview").scrollTop = 1e6;
    }
    el.addEventListener("click", (e) => { const w = e.target.closest(".w"); if (w) correct(w.dataset.w); });
    try { const r = await api(`/api/captions/${roomId}/recent`); r.finals.forEach((t) => finals.push({ text: t, words: [] })); renderPreview(); } catch (_) {}
    k("lnkA").href = `/captions/${roomId}`; k("lnkO").href = `/captions/${roomId}/overlay`; k("lnkB").href = `/captions/${roomId}?layout=bar`;
    const testText = () => k("testText").value.trim() || "Test caption: can everyone read this?";
    k("test").onclick = () => AT.guard(() => post(`/api/captions/${roomId}/test`, { text: testText(), final: true }));
    k("testPart").onclick = () => AT.guard(() => post(`/api/captions/${roomId}/test`, { text: testText(), final: false }));
    k("clear").onclick = () => AT.guard(() => post(`/api/captions/${roomId}/clear`));

    // Audio & EQ
    const bands = () => s.eq_band_gains_db;
    function setGainUi() { k("gain").value = s.gain_db; k("gainV").textContent = `${(+s.gain_db).toFixed(1)} dB`; }
    setGainUi();
    k("music").checked = !!s.music_label; k("acr").checked = !!s.join_acronyms; k("scase").checked = !!s.sentence_case;
    let gainT = null;
    k("gain").oninput = () => { s.gain_db = +k("gain").value; setGainUi(); clearTimeout(gainT); gainT = setTimeout(() => save({ gain_db: s.gain_db }).catch((e) => toast(e.message, "bad")), 150); };
    k("music").onchange = () => AT.guard(() => save({ music_label: k("music").checked }));
    k("acr").onchange = () => AT.guard(() => save({ join_acronyms: k("acr").checked }));
    k("scase").onchange = () => AT.guard(() => save({ sentence_case: k("scase").checked }));
    k("eqReset").onclick = () => AT.guard(() => save({ gain_db: 0, eq_band_gains_db: bands().map(() => 0) })).then(() => { setGainUi(); drawEq(); });
    let rta = null;
    const cv = k("eq"), F0 = 20, F1 = 8000, DB = s.max_band_gain_db || 12;
    const fx = (f, w) => (Math.log10(f / F0) / Math.log10(F1 / F0)) * w;
    const xf = (x, w) => F0 * Math.pow(F1 / F0, x / w);
    const dy = (db, h) => h / 2 - (db / DB) * (h / 2 - 12);
    function drawEq() {
      const r = cv.getBoundingClientRect(); if (!r.width) return;
      const dpr = window.devicePixelRatio || 1, w = r.width, h = r.height;
      cv.width = w * dpr; cv.height = h * dpr;
      const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      g.strokeStyle = "#2a3342"; g.lineWidth = 1; g.font = "10px system-ui"; g.fillStyle = "#93a0b4";
      [50, 100, 200, 500, 1000, 2000, 5000].forEach((f) => { const x = fx(f, w); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke(); g.fillText(f >= 1000 ? `${f / 1000}k` : f, x + 2, h - 3); });
      [-12, -6, 0, 6, 12].forEach((d) => { const y = dy(d, h); g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); });
      if (rta) {  // the live spectrum, -60..0 dBFS, behind the curve
        const n = rta.length, lo = s.rta_freq_min || 20, hi = s.rta_freq_max || 8000;
        g.fillStyle = "rgba(255,122,26,.18)";
        rta.forEach((db, i) => { const fa = lo * Math.pow(hi / lo, i / n), fb = lo * Math.pow(hi / lo, (i + 1) / n), x0 = fx(Math.max(fa, F0), w), x1 = fx(Math.min(fb, F1), w), bh = Math.max(0, (db + 60) / 60) * h; g.fillRect(x0, h - bh, Math.max(1, x1 - x0 - 1), bh); });
      }
      g.strokeStyle = "#FF7A1A"; g.lineWidth = 2; g.beginPath();
      for (let x = 0; x <= w; x += 2) { const f = xf(x, w); const db = s.eq_bands_hz.reduce((a, f0, i) => a + peakDb(f, f0, bands()[i]), 0); x ? g.lineTo(x, dy(db, h)) : g.moveTo(x, dy(db, h)); }
      g.stroke();
      s.eq_bands_hz.forEach((f0, i) => { g.fillStyle = drag === i ? "#FFB27A" : "#FF7A1A"; g.beginPath(); g.arc(fx(f0, w), dy(bands()[i], h), 6, 0, 7); g.fill(); g.fillStyle = "#e8ebf2"; g.fillText(`${(+bands()[i]).toFixed(1)}`, fx(f0, w) - 8, dy(bands()[i], h) - 9); });
    }
    let drag = null, eqT = null;
    // the whole curve goes with every save, so quick drags of two dots can't lose one
    const saveEq = () => { clearTimeout(eqT); return save({ eq_band_gains_db: [...s.eq_band_gains_db] }).catch((er) => toast(er.message, "bad")); };
    const pos = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top, r.width, r.height]; };
    cv.onpointerdown = (e) => {
      const [x, y, w, h] = pos(e);
      let best = null, bd = 1e9;
      s.eq_bands_hz.forEach((f0, i) => { const d = Math.hypot(fx(f0, w) - x, dy(bands()[i], h) - y); if (d < bd) { bd = d; best = i; } });
      if (bd < 30) { drag = best; cv.setPointerCapture(e.pointerId); }
    };
    cv.onpointermove = (e) => {
      if (drag === null) return;
      const [, y, , h] = pos(e);
      s.eq_band_gains_db[drag] = Math.round(Math.max(-DB, Math.min(DB, ((h / 2 - y) / (h / 2 - 12)) * DB)) * 2) / 2;
      drawEq();
      clearTimeout(eqT); eqT = setTimeout(saveEq, 120);
    };
    cv.onpointerup = cv.onpointercancel = () => { if (drag !== null) saveEq(); drag = null; drawEq(); };
    cv.ondblclick = (e) => { const [x, , w] = pos(e); const i = s.eq_bands_hz.reduce((b, f0, j) => (Math.abs(fx(f0, w) - x) < Math.abs(fx(s.eq_bands_hz[b], w) - x) ? j : b), 0); s.eq_band_gains_db[i] = 0; drawEq(); saveEq(); };

    // Vocabulary
    k("vocab").value = s.vocabulary || ""; k("score").value = s.hotwords_score; k("scoreV").textContent = (+s.hotwords_score).toFixed(1);
    k("enabled").checked = !!s.enabled;
    k("score").oninput = () => (k("scoreV").textContent = (+k("score").value).toFixed(1));
    const showSkipped = () => { const sk = (s.skipped_vocabulary || []); k("skipped").textContent = sk.length ? `Couldn't use: ${sk.join(", ")}` : ""; };
    showSkipped();
    k("saveVocab").onclick = () => AT.guard(() => save({ vocabulary: k("vocab").value, hotwords_score: +k("score").value, enabled: k("enabled").checked }))
      .then(() => { toast("Vocabulary saved. The engine reloads in the background.", "good"); setTimeout(async () => { try { s = await api(`/api/captions/${roomId}/settings`); showSkipped(); } catch (_) {} }, 4000); });

    // Appearance
    function drawFields() {
      const a = s.appearance[surface];
      k("fields").innerHTML = FIELDS.map(([key, label, type, x, y, st]) => {
        const v = a[key]; let input;
        if (type === "select") input = `<select data-f="${key}">${x.map((o) => `<option ${String(o) === String(v) ? "selected" : ""}>${o}</option>`).join("")}</select>`;
        else if (type === "font") input = `<select data-f="${key}">${[...new Set([v, ...FONTS])].map((o) => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(o.split(",")[0].replace(/'/g, ""))}</option>`).join("")}</select>`;
        else if (type === "color") input = v === "transparent" ? `<span class="row" style="gap:.4rem"><input type="color" data-f="${key}" value="#000000" disabled><span class="muted small">transparent</span></span>` : `<input type="color" data-f="${key}" value="${esc(v)}">`;
        else if (type === "check") input = `<input type="checkbox" data-f="${key}" ${v ? "checked" : ""}>`;
        else if (type === "range") input = `<span class="slider"><input type="range" data-f="${key}" min="${x}" max="${y}" step="${st}" value="${v}"><span>${Math.round(v * 100)}%</span></span>`;
        else input = `<input type="number" data-f="${key}" min="${x}" max="${y}" step="${st}" value="${v}">`;
        return `<label>${label}</label>${input}`;
      }).join("");
      k("fields").querySelectorAll('input[type=range]').forEach((r) => (r.oninput = () => (r.nextElementSibling.textContent = `${Math.round(r.value * 100)}%`)));
      k("open").href = surface === "audience" ? `/captions/${roomId}` : surface === "overlay" ? `/captions/${roomId}/overlay` : `/captions/${roomId}?layout=bar`;
    }
    drawFields();
    el.querySelectorAll("[data-surf]").forEach((b) => (b.onclick = () => { surface = b.dataset.surf; el.querySelectorAll("[data-surf]").forEach((x) => x.classList.toggle("on", x === b)); drawFields(); }));
    k("saveLook").onclick = () => {
      const vals = {};
      k("fields").querySelectorAll("[data-f]").forEach((i) => { if (i.disabled) return; const f = FIELDS.find((x) => x[0] === i.dataset.f); vals[i.dataset.f] = f[2] === "check" ? i.checked : ["number", "range"].includes(f[2]) ? +i.value : i.value; });
      AT.guard(() => save({ appearance: { [surface]: vals } })).then(() => { drawFields(); toast(`${SURF[surface]} updated`, "good"); });
    };
    k("reset").onclick = () => confirm(`Put the ${SURF[surface].toLowerCase()} back to its default look?`) && AT.guard(() => save({ reset_appearance: surface })).then(drawFields);

    // Corrections
    async function correct(word) {
      const fixed = prompt(`What should "${word}" have been?`, word);
      if (!fixed || fixed.trim() === word) return;
      const r = await AT.guard(() => post(`/api/captions/${roomId}/corrections`, { original: word, corrected: fixed.trim() }));
      toast(r.added_to_vocabulary ? `"${fixed.trim()}" added to the vocabulary` : "Correction noted (already in the vocabulary)", "good");
      s = await api(`/api/captions/${roomId}/settings`); k("vocab").value = s.vocabulary || "";
      if (tab === 4) loadHistory();
    }
    async function loadHistory() {
      const [h, c] = await Promise.all([api(`/api/captions/${roomId}/history`), api(`/api/captions/${roomId}/corrections`)]);
      k("hist").innerHTML = h.map((f) => `<div class="hist"><span class="muted small">${new Date(f.ts * 1000).toLocaleTimeString()}</span> ${f.words.length ? f.words.map((w) => `<span class="w ${confClass(w.confidence)}" data-w="${esc(w.text)}">${esc(w.text)}</span>`).join(" ") : f.text.split(/\s+/).map((w) => `<span class="w" data-w="${esc(w)}">${esc(w)}</span>`).join(" ")}</div>`).join("") || '<p class="muted">No captions yet.</p>';
      k("corr").innerHTML = c.length ? `<table>${c.map((x) => `<tr><td>${esc(x.original)}</td><td>→ <b>${esc(x.corrected)}</b></td><td class="muted">${esc(x.by || "")}</td></tr>`).join("")}</table>` : '<p class="muted">None yet.</p>';
    }
    k("histRefresh").onclick = loadHistory;
    k("histClear").onclick = () => AT.guard(() => post(`/api/captions/${roomId}/history/clear`)).then(loadHistory);

    // Transcripts
    function recUi(on, name) {
      k("recDot").classList.toggle("on", !!on); k("recText").textContent = on ? `Recording${name ? " · " + name : ""}` : "Not recording";
      k("recStart").classList.toggle("hidden", !!on); k("recStop").classList.toggle("hidden", !on);
    }
    recUi(s.recording);
    k("record").checked = !!s.record;
    k("record").onchange = () => AT.guard(() => save({ record: k("record").checked }));
    k("recStart").onclick = () => AT.guard(() => post(`/api/captions/${roomId}/transcript/start`)).then((r) => { recUi(true, r.filename); loadTx(); });
    k("recStop").onclick = () => AT.guard(() => post(`/api/captions/${roomId}/transcript/stop`)).then(() => { recUi(false); loadTx(); });
    async function loadTx() {
      const tx = await api(`/api/captions/transcripts?room_id=${roomId}`);
      k("tx").innerHTML = tx.length ? `<table><tr><th>Started</th><th>Ended</th><th></th></tr>${tx.map((t) => `<tr><td>${AT.when(t.started_at)}</td><td>${t.ended_at ? AT.when(t.ended_at) : '<span class="pill bad">recording</span>'}</td>
        <td style="white-space:nowrap"><a href="/api/captions/transcripts/${t.id}" target="_blank">Text</a> · <a href="/api/captions/transcripts/${t.id}/export.srt">SRT</a> · <a href="/api/captions/transcripts/${t.id}/export.vtt">VTT</a>${t.ended_at ? ` · <a href="#" data-del="${t.id}">Delete</a>` : ""}</td></tr>`).join("")}</table>` : '<p class="muted">No transcripts for this room yet.</p>';
      k("tx").querySelectorAll("[data-del]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); confirm("Delete this transcript?") && AT.guard(() => del(`/api/captions/transcripts/${a.dataset.del}`)).then(loadTx); }));
    }

    AT.socket([`captions:${roomId}`], (e) => {
      if (e.type === "final") { finals.push({ text: e.data.text, words: e.data.words || [] }); finals.splice(0, Math.max(0, finals.length - 50)); partial = null; renderPreview(); }
      else if (e.type === "partial") { partial = { text: e.data.text, words: e.data.words || [] }; renderPreview(); }
      else if (e.type === "clear") { finals.length = 0; partial = null; renderPreview(); }
      else if (e.type === "level") { k("lvl").style.width = Math.max(0, Math.min(100, (e.data.db + 60) / 60 * 100)) + "%"; if (e.data.rta) { rta = e.data.rta; if (tab === 1 && drag === null) drawEq(); } }
      else if (e.type === "recording") { recUi(e.data.on, e.data.filename); if (tab === 5) loadTx(); }
      else if (e.type === "appearance") { s.appearance = e.data; if (tab === 3) drawFields(); }
    });
    new ResizeObserver(() => tab === 1 && drawEq()).observe(cv);
    return { el };
  }

  // Inside the workspace's Captions window: a "Caption settings" toggle.
  function attach(roomId) {
    const win = document.querySelector('[data-win="captions"]');
    if (!win || win.querySelector(".capset-toggle")) return;
    const bar = document.createElement("div");
    bar.className = "row capset-toggle"; bar.style.marginTop = ".5rem";
    bar.innerHTML = '<button class="small" data-capset>Caption settings ▾</button><span class="muted small">audio, vocabulary, how the screens look, corrections, transcripts</span>';
    const box = document.createElement("div"); box.className = "hidden";
    win.append(bar, box);
    let made = false;
    bar.querySelector("[data-capset]").onclick = async (e) => {
      const open = box.classList.toggle("hidden") === false;
      e.target.textContent = open ? "Caption settings ▴" : "Caption settings ▾";
      if (open && !made) { made = true; try { await mount(box, roomId); } catch (er) { made = false; box.textContent = er.message; } }
    };
  }

  window.ATCaptionsPanel = { mount, attach };
})();
