// "Add a screen" in the tech workspace, beside the timer. A new Linux screen
// shows a six-digit code when it boots; the tech types it here, picks what
// the screen shows (a timer view, captions, a test pattern or a web page) and
// the screen joins this room and shows it.
//   ATPairing.mount({ roomId, mount?, onPaired?, setViews? })
(function () {
  const css = `
  .pair-mini .pair-code { font-family: var(--brand-font); font-weight: 800; font-size: 1.9rem; letter-spacing: .18em; text-align: center;
    padding: .35rem .5rem; font-variant-numeric: tabular-nums; }
  .pair-mini .pair-code::placeholder { color: #3a4559; letter-spacing: .18em; }
  .pair-mini label.small { display: block; margin: .5rem 0 .2rem; color: var(--muted); font-size: .8rem; }
  .pair-mini .pair-done { margin-top: .5rem; font-size: .9rem; }
  .pair-mini .pair-done b { color: var(--accent); }`;

  function addStyle() {
    if (document.getElementById("pairCss")) return;
    const s = document.createElement("style"); s.id = "pairCss"; s.textContent = css; document.head.appendChild(s);
  }

  const fmt = (v) => { const d = String(v || "").replace(/\D/g, "").slice(0, 6); return d.length > 3 ? `${d.slice(0, 3)} ${d.slice(3)}` : d; };

  async function mount(opts) {
    const { esc, api, post, toast } = AT;
    if (!opts || !opts.roomId) return null;
    addStyle();
    let box = document.getElementById("pairMini");
    if (!box) {
      box = document.createElement("section");
      box.id = "pairMini";
      box.className = "panel mini pair-mini";
      const host = opts.mount || document.querySelector(".dside");
      if (host) host.prepend(box);
      else { const t = document.getElementById("timerBox"); if (!t) return null; t.after(box); }
    }
    box.innerHTML = `
      <div class="row" style="justify-content:space-between"><h3>Add a screen</h3><span class="muted small">this room</span></div>
      <p class="muted small" style="margin:.1rem 0 .4rem">Type the code showing on the screen.</p>
      <input class="pair-code" id="pairCode" inputmode="numeric" autocomplete="off" maxlength="7" placeholder="000 000" aria-label="Code on the screen">
      <label class="small" for="pairView">What it shows</label>
      <select id="pairView"></select>
      <input id="pairUrl" class="hidden" placeholder="https://… the web page" style="margin-top:.4rem">
      <label class="small" for="pairName">Name <span class="muted">(optional)</span></label>
      <input id="pairName" placeholder="e.g. HD-STAGE-LEFT" maxlength="40">
      <div class="row" style="margin-top:.6rem;justify-content:flex-end"><button class="primary" id="pairGo">Add screen</button></div>
      <div class="pair-done" id="pairDone"></div>`;
    const $ = (id) => box.querySelector("#" + id);
    let groups = [];
    try { groups = (await api("/api/screens/layouts")).groups; } catch (_) { groups = []; }
    const flat = groups.flatMap((g) => g.views);
    let last = "stage";
    try { last = localStorage.getItem("atsuit_pair_view") || last; } catch (_) {}
    $("pairView").innerHTML = groups.map((g) => `<optgroup label="${esc(g.name)}">${g.views.map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join("")}</optgroup>`).join("") +
      `<optgroup label="Other"><option value="url:">A web page…</option></optgroup>`;
    if ([...$("pairView").options].some((o) => o.value === last)) $("pairView").value = last;
    $("pairView").onchange = () => $("pairUrl").classList.toggle("hidden", $("pairView").value !== "url:");
    $("pairView").onchange();
    $("pairCode").oninput = () => { const v = fmt($("pairCode").value); if (v !== $("pairCode").value) $("pairCode").value = v; };
    $("pairCode").onkeydown = (e) => { if (e.key === "Enter") go(); };
    if (opts.setViews && flat.length) opts.setViews(flat);

    async function go() {
      const code = $("pairCode").value.replace(/\D/g, "");
      if (code.length !== 6) { $("pairCode").focus(); return toast("Type the 6-digit code from the screen", "bad"); }
      let view = $("pairView").value;
      if (view === "url:") {
        let u = $("pairUrl").value.trim();
        if (!u) return toast("Put in the web page address", "bad");
        if (!/^https?:\/\//i.test(u)) u = "http://" + u;
        view = "url:" + u;
      } else try { localStorage.setItem("atsuit_pair_view", view); } catch (_) {}
      $("pairGo").disabled = true;
      try {
        const r = await post("/api/screens/pair", { code, room_id: opts.roomId, view, name: $("pairName").value.trim() });
        const label = view.startsWith("url:") ? view.slice(4) : (flat.find((v) => v.id === view) || {}).name || view;
        $("pairDone").innerHTML = `<b>${esc(r.name)}</b> added. It's showing ${esc(label)}.`;
        $("pairCode").value = ""; $("pairName").value = "";
        toast(`${r.name} added`, "good");
        opts.onPaired && opts.onPaired(r);
      } catch (e) { toast(e.message, "bad"); $("pairDone").textContent = ""; }
      finally { $("pairGo").disabled = false; }
    }
    $("pairGo").onclick = go;
    return { box, groups };
  }

  window.ATPairing = { mount, fmt };
})();
