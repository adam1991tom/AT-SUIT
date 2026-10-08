// Console: dashboard, chat, help, timers, captions, nodes, admin.
(async () => {
  const { esc, api, post, put, del, upload, guard, toast, when } = AT;
  const main = document.getElementById("main");
  let boot, sock, chat = null, view = null, helpOpen = 0;

  await AT.branding();
  try { boot = await api("/api/bootstrap"); } catch (e) { return showLogin(); }
  if (boot.me.kind === "node") { location.replace("/node"); return; }
  // Admins and techs open on the room workspace, with the timer first. A #/page link (or the Console button there) opens the console.
  if (["admin", "tech"].includes(boot.me.role) && !location.hash && boot.rooms.length) { location.replace("/node"); return; }
  start();

  function showLogin() {
    document.getElementById("login").classList.remove("hidden");
    const f = document.getElementById("loginForm");
    f.onsubmit = async (e) => {
      e.preventDefault();
      try { await post("/api/auth/login", { username: f.username.value, password: f.password.value }); location.reload(); }
      catch (ex) { document.getElementById("loginErr").textContent = ex.message; }
    };
  }

  function topics() {
    return [...boot.sites.map((s) => `site:${s.id}`), ...boot.rooms.map((r) => `room:${r.id}`), `dm:${boot.me.id}`, "fleet",
      ...boot.rooms.map((r) => `timer:${r.id}`), ...boot.rooms.map((r) => `captions:${r.id}`), ...(boot.modules.presenter ? ["presenter"] : [])];
  }

  function start() {
    document.getElementById("shell").classList.remove("hidden");
    document.getElementById("meName").textContent = boot.me.name;
    if (boot.branding.logo_url) { const l = document.getElementById("logo"); l.src = boot.branding.logo_url; l.classList.remove("hidden"); }
    document.querySelectorAll("#nav [data-mod]").forEach((a) => a.classList.toggle("hidden", !boot.modules[a.dataset.mod]));
    document.querySelectorAll("#nav [data-role=admin]").forEach((a) => a.classList.toggle("hidden", boot.me.role !== "admin"));
    document.getElementById("logout").onclick = async (e) => { e.preventDefault(); await post("/api/auth/logout"); location.reload(); };
    sock = AT.socket(topics(), onEvent);
    window.addEventListener("hashchange", route);
    if (boot.modules.comms) refreshHelpCount();
    route();
  }

  function onEvent(evt) {
    if (chat) chat.onEvent(evt);
    if (evt.type === "help.new") { toast(`Help needed in ${evt.data.room_name}: ${evt.data.description || evt.data.category}`, "bad"); refreshHelpCount(); }
    if (evt.type === "help.updated") refreshHelpCount();
    if (evt.type === "rooms.changed") api("/api/bootstrap").then((b) => { boot = b; });
    view && view.onEvent && view.onEvent(evt);
  }

  async function refreshHelpCount() {
    const open = (await api("/api/comms/help?status=open")).length;
    helpOpen = open;
    const el = document.getElementById("helpCount");
    el.textContent = open; el.classList.toggle("hidden", !open);
  }

  function route() {
    const [, name = "dashboard", sub] = location.hash.split("/");
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#/${name}`));
    chat = null; view = null;
    const banner = boot.me.role !== "admin" || boot.licence.valid ? "" : `<div class="banner">Evaluation mode: ${esc(boot.licence.reason)}. Add a licence in Admin → Licence.</div>`;
    main.innerHTML = banner + '<div id="view"></div>';
    const el = document.getElementById("view");
    const presenter = (el, sub) => { view = PresenterPage.mount(el, sub, boot); };
    const views = { dashboard, chat: chatView, help, timers, presenter, captions, fleet, admin };
    (views[name] || dashboard)(el, sub);
  }

  const roomName = (id) => boot.rooms.find((r) => r.id === id)?.name || "";
  const roomOptions = (sel, blank = "No room") => `<option value="">${blank}</option>` + boot.rooms.map((r) => `<option value="${r.id}" ${r.id === sel ? "selected" : ""}>${esc(r.name)}</option>`).join("");

  // ---------------------------------------------------------- dashboard --
  async function dashboard(el) {
    const links = await api("/api/dashboard/links");
    const groups = {};
    for (const l of links) {
      const key = l.board === "admin" ? "Admin" : l.room ? l.room : ({ device: "Devices", tool: "Tools", buttons: "Buttons", timer: "Timers" }[l.kind] || "Links");
      (groups[key] ||= []).push(l);
    }
    // The rooms first: every room's timer, live. The tech workspace has the controls, captions and chat dock.
    const live = {};
    const rooms = boot.modules.timers ? boot.rooms : [];
    for (const r of rooms) live[r.id] = await api(`/api/timers/${r.id}`).catch(() => null);
    const strip = rooms.length ? `<div class="row" style="justify-content:space-between"><h1>Dashboard</h1><span class="row"><a class="btn primary" href="/node">Open the workspace</a>${boot.me.role === "admin" ? '<a class="btn" href="#/admin/links">Edit links</a>' : ""}</span></div>
      <div class="grid" id="dashRooms" style="margin-top:.6rem">${rooms.map((r) => `<div class="panel" data-room="${r.id}"><div class="row" style="justify-content:space-between"><h2>${esc(r.name)}</h2><span class="small"><a href="/timer/${r.id}" target="_blank">Stage ↗</a></span></div>
        <div class="clock" style="font-size:2.6rem">--:--</div><div class="small now" style="font-weight:600"></div><div class="muted small next"></div></div>`).join("")}</div>` : "";
    el.innerHTML = (strip || `<div class="row" style="justify-content:space-between"><h1>Dashboard</h1>${boot.me.role === "admin" ? '<a class="btn" href="#/admin/links">Edit links</a>' : ""}</div>`) + (strip ? '<h2 style="margin-top:1.4rem">Links</h2>' : "") +
      (links.length ? Object.entries(groups).map(([g, ls]) => `<h3 style="margin-top:1.2rem">${esc(g)}</h3><div class="tiles">` +
        ls.map((l) => `<a class="tile" href="${esc(l.url)}" target="_blank" rel="noopener"><span class="dot" data-ping="${l.id}"></span><span class="grow">${esc(l.label)}</span></a>`).join("") + "</div>").join("")
        : '<p class="muted">No links yet. Admins can import the old Homarr board or add links in Admin → Links.</p>');
    const dash = el.querySelector("#dashRooms");
    if (dash) {
      const tick = setInterval(() => {
        if (!document.body.contains(dash)) return clearInterval(tick);
        for (const [id, st] of Object.entries(live)) {
          const card = dash.querySelector(`[data-room="${id}"]`); if (!card || !st) continue;
          const d = AT.timerDisplay(st), c = card.querySelector(".clock");
          c.textContent = d.text; c.className = "clock " + d.cls;
          card.querySelector(".now").textContent = (st.cue?.cue ? st.cue.cue + " · " : "") + (st.title || "") + (st.playback === "pause" ? " (paused)" : "");
          card.querySelector(".next").textContent = (st.next ? `Next: ${st.next.title}` : "") + (st.message_visible ? `  ·  On stage: ${st.message}` : "");
        }
      }, 250);
      view = { onEvent: (e) => { if (e.type === "timer" && e.data?.room_id in live) live[e.data.room_id] = e.data; } };
    }
    try {
      const ping = await api("/api/dashboard/ping");
      el.querySelectorAll("[data-ping]").forEach((d) => d.classList.add(ping[d.dataset.ping] ? "on" : "off"));
    } catch (_) {}
  }

  // --------------------------------------------------------------- chat --
  function chatView(el) {
    chat = Chat.mount(el, { me: boot.me, onUnread: (n) => { const u = document.getElementById("unreadAll"); u.textContent = n; u.classList.toggle("hidden", !n); } });
  }

  // --------------------------------------------------------------- help --
  async function help(el) {
    const render = async () => {
      const rows = await api("/api/comms/help");
      el.innerHTML = `<h1>Help requests</h1><div class="panel"><table><tr><th>When</th><th>Room</th><th>From</th><th>What</th><th>Status</th><th></th></tr>` +
        rows.map((h) => `<tr><td>${when(h.created_at)}</td><td>${esc(h.room_name)}</td><td>${esc(h.requested_by)}</td><td>${esc(h.category)}: ${esc(h.description)}</td>` +
          `<td><span class="pill ${h.status === "open" ? "bad" : h.status === "resolved" ? "good" : "warn"}">${esc(h.status)}</span> ${esc(h.assigned_to)}</td>` +
          `<td class="row">${h.status === "open" ? `<button class="small" data-s="acknowledged" data-id="${h.id}">On my way</button>` : ""}${h.status !== "resolved" ? `<button class="small" data-s="resolved" data-id="${h.id}">Resolved</button>` : ""}</td></tr>`).join("") +
        "</table></div>";
      el.querySelectorAll("[data-s]").forEach((b) => b.onclick = () => guard(() => put(`/api/comms/help/${b.dataset.id}`, { status: b.dataset.s })));
    };
    view = { onEvent: (e) => e.type.startsWith("help.") && render() };
    render();
  }

  // ------------------------------------------------------------- timers --
  async function timers(el) {
    // Preview only: techs run timers and cue lists from their workspace. This
    // page watches every room and routes the remote screens.
    const states = {};
    const views = await api("/api/timers-views?screens=1");
    const viewOpts = (sel) => [...views, { id: "captions", name: "Captions" }].map((v) => `<option value="${esc(v.id)}" ${v.id === sel ? "selected" : ""}>${esc(v.name)}</option>`).join("");
    el.innerHTML = `<h1>Timers</h1><p class="muted">Live preview of every room. Techs run the timer and cue list from their workspace; Companion can drive them with an API key.</p>
      <div class="grid" id="tg"></div>
      <div class="row" style="justify-content:space-between;margin-top:1.4rem"><h2>Screens</h2><span class="muted small">Pick what each remote screen shows. A screen can also choose for itself: tap its top-left corner 5 times.</span></div>
      <div class="panel" id="screens"></div>
      <h2 style="margin-top:1.4rem">Views</h2><div class="panel" id="views"></div>`;
    const grid = el.querySelector("#tg");
    for (const r of boot.rooms) {
      states[r.id] = await api(`/api/timers/${r.id}`);
      grid.insertAdjacentHTML("beforeend", `<div class="panel" data-room="${r.id}">
        <div class="row" style="justify-content:space-between"><h2>${esc(r.name)}</h2><span class="small"><a href="/timer/${r.id}" target="_blank">Stage ↗</a> · <a href="/timer/${r.id}?view=backstage" target="_blank">Backstage ↗</a></span></div>
        <div class="clock" style="font-size:2.6rem">--:--</div><div class="small now" style="font-weight:600"></div><div class="muted small next"></div></div>`);
    }
    const tick = setInterval(() => {
      if (!document.body.contains(grid)) return clearInterval(tick);
      for (const [id, s] of Object.entries(states)) {
        const card = grid.querySelector(`[data-room="${id}"]`); if (!card) continue;
        const d = AT.timerDisplay(s), c = card.querySelector(".clock");
        c.textContent = d.text; c.className = "clock " + d.cls;
        card.querySelector(".now").textContent = (s.cue?.cue ? s.cue.cue + " · " : "") + (s.title || "") + (s.playback === "pause" ? " (paused)" : "");
        card.querySelector(".next").textContent = (s.next ? `Next: ${s.next.title}` : "") + (s.message_visible ? `  ·  On stage: ${s.message}` : "");
      }
    }, 200);

    async function renderScreens() {
      const box = el.querySelector("#screens");
      if (!box) return;
      const [all, rel] = await Promise.all([api("/api/fleet/nodes"), api("/api/fleet/screen-agent").catch(() => ({}))]);
      const screens = all.filter((n) => n.kind === "kiosk"), isAdmin = boot.me.role === "admin";
      const agentVer = (n) => n.info?.agent || "";
      const status = (n) => {
        const v = agentVer(n);
        if (!v) return `<span class="muted">${n.legacy ? esc(n.version) || "old agent" : "browser only"}</span>`;
        return rel.version && v !== rel.version ? `${esc(v)} <span class="pill warn">update to ${esc(rel.version)}</span>` : `${esc(v)} <span class="pill good">up to date</span>`;
      };
      const output = (n) => (n.info?.showing_on || []).map((o) => /^(eDP|LVDS|DSI)/i.test(o) ? "built-in screen" : esc(o)).join(", ");
      box.innerHTML = `<table><tr><th></th><th>Screen</th><th>Address</th><th>Room</th><th>Shows</th><th>Agent</th><th>Last seen</th><th></th></tr>
        ${screens.map((n) => `<tr data-n="${n.id}"><td><span class="dot ${n.online ? "on" : "off"}"></span></td><td><b>${esc(n.name)}</b>${n.legacy ? ' <span class="pill">old agent</span>' : ""}<div class="muted small" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(n.current_url)}">${esc(n.current_url)}</div>${output(n) ? `<div class="muted small">On ${output(n)}</div>` : ""}</td>
          <td class="small">${esc(n.ip)}</td>
          <td><select data-room style="width:auto">${roomOptions(n.room_id, "Choose…")}</select></td>
          <td>${n.legacy ? '<span class="muted small">use Set screen in Nodes</span>' : `<select data-view style="width:auto"><option value="">Choose…</option>${viewOpts(n.screen_view)}<option value="__url" ${(n.screen_view || "").startsWith("url:") ? "selected" : ""}>Web page…</option></select>`}</td>
          <td class="small">${status(n)}</td>
          <td class="small">${n.last_seen ? when(new Date(n.last_seen * 1000).toISOString()) : "never"}</td>
          <td class="row" style="flex-wrap:nowrap"><button class="small" data-ident>Identify</button>${agentVer(n) ? `<button class="small" data-cmd="restart_browser" title="Restart the browser on this screen">Restart</button><button class="small" data-cmd="update" title="Update the screen agent now">Update</button>` : `<button class="small" data-cmd="reload">Reload</button>`}${isAdmin ? '<button class="small" data-cmd="reboot">Reboot</button>' : ""}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">No screens yet. Install the screen agent on a Linux laptop or all-in-one (Admin → Node setup).</td></tr>'}</table>`;
      box.querySelectorAll("[data-n]").forEach((row) => {
        const id = +row.dataset.n, n = screens.find((x) => x.id === id);
        const save = () => {
          const room = row.querySelector("[data-room]").value, v = row.querySelector("[data-view]");
          if (!v) return guard(() => put(`/api/fleet/nodes/${id}`, { room_id: room ? +room : null }));
          let view = v.value;
          if (view === "__url") {
            const url = prompt(`Web page for ${n.name} (http:// or https://)`, (n.screen_view || "").startsWith("url:") ? n.screen_view.slice(4) : "https://");
            if (!url || !/^https?:\/\//.test(url.trim())) return renderScreens();
            view = "url:" + url.trim();
          }
          if (view && (room || view.startsWith("url:"))) guard(() => put(`/api/fleet/nodes/${id}/screen`, { room_id: room ? +room : null, view })).then(() => toast("Screen updated", "good"));
        };
        row.querySelector("[data-room]").onchange = save;
        row.querySelector("[data-view]") && (row.querySelector("[data-view]").onchange = save);
        row.querySelectorAll("[data-cmd]").forEach((b) => b.onclick = () => (b.dataset.cmd !== "reboot" || confirm(`Reboot ${n.name}?`)) && guard(() => post(`/api/fleet/nodes/${id}/command`, { kind: b.dataset.cmd })).then(() => toast("Sent", "good")));
        row.querySelector("[data-ident]").onclick = () => guard(() => post(`/api/fleet/nodes/${id}/command`, { kind: "identify" })).then(() => toast("The screen shows its name for 10 seconds", "good"));
      });
    }

    let editing = null; // the built view open in the form
    async function renderViews() {
      const box = el.querySelector("#views"), isAdmin = boot.me.role === "admin";
      const custom = views.filter((v) => !v.builtin && !v.design);
      const [designs, hcc, bdng] = await Promise.all([api("/api/timers-designs"), api("/api/timers-views/look/hcc"), api("/api/timers-views/look/bdng")]);
      const links = (id) => boot.rooms.slice(0, 4).map((r) => `<a target="_blank" href="/timer/${r.id}?view=${encodeURIComponent(id)}">${esc(r.name)} ↗</a>`).join(" · ");
      const logoCell = (view, slot, url, label) => `<div class="row small" style="gap:.4rem"><span style="min-width:6.5rem">${label}</span>
        ${url ? `<img src="${esc(url)}" alt="" style="height:28px;max-width:140px;object-fit:contain;background:#555;border-radius:4px">` : `<span class="muted">${view === "bdng" && slot === "bottom" ? "none" : "site logo"}</span>`}
        ${isAdmin ? `<input type="file" accept=".png,.jpg,.jpeg,.svg,.webp" data-logo="${view}/${slot}" style="width:auto">${url ? `<button class="small" data-unlogo="${view}/${slot}">Remove</button>` : ""}` : ""}</div>`;
      const D = { name: "", background: "#000000", text: "#ffffff", timer_size: 24, title_size: 4, show_logo: false, show_title: true, show_next: true, show_progress: true, show_clock: false, show_message: true };
      const SHOW = { show_logo: "Logo", show_title: "Title", show_next: "Next cue", show_progress: "Progress bar", show_clock: "Time of day", show_message: "Stage message" };
      box.innerHTML = `<p class="small muted">Built in: ${views.filter((v) => v.builtin && !v.test).map((v) => esc(v.name)).join(", ")}. All of them turn red at danger, flash with Blink, show the stage message over the timer and go black with Blackout. Overlay window is see-through, for a small window on a laptop or a bar across a screen.</p>
        <h3>HCC and BDNG</h3>
        <table><tr><td><b>HCC venue clock</b><div class="small">${links("hcc")}</div></td><td>${logoCell("hcc", "top", hcc.logos.top, "Logo")}</td></tr>
          <tr><td><b>BDNG sponsor clock</b><div class="small">${links("bdng")}</div></td><td>${logoCell("bdng", "top", bdng.logos.top, "Top logo")}${logoCell("bdng", "bottom", bdng.logos.bottom, "Bottom logo")}
            <div class="row small" style="gap:.4rem;margin-top:.3rem"><span style="min-width:6.5rem">Bottom text</span><input data-btext value="${esc(bdng.options.bottom_text || "")}" ${isAdmin ? "" : "disabled"} class="grow">${isAdmin ? '<button class="small" data-savetext>Save</button>' : ""}</div></td></tr></table>
        <p class="small muted">No logo uploaded: HCC and the BDNG top row use the site logo from Admin → General.</p>
        <h3 style="margin-top:1rem">Your own views</h3>
        <table>${designs.map((d) => `<tr><td><b>${esc(d.name)}</b><div class="small">${links(d.id)}</div></td>
          <td>${isAdmin ? `<button class="small" data-edit="${esc(d.slug)}">Edit</button> <button class="small danger" data-deldesign="${esc(d.slug)}">Remove</button>` : ""}</td></tr>`).join("") || '<tr><td class="muted small">None yet.</td></tr>'}</table>
        ${isAdmin ? `<form id="builder" class="panel" style="margin-top:.6rem">
          <div class="row" style="justify-content:space-between"><b data-btitle>Build a view</b><a class="small hidden" data-preview target="_blank">Preview ↗</a></div>
          <div class="row" style="flex-wrap:wrap;margin-top:.4rem"><input name="name" placeholder="Name, e.g. Green room" required class="grow">
            <label class="small row" style="margin:0;gap:.3rem">Background <input type="color" name="background" style="width:2.6rem;padding:0"></label>
            <label class="small row" style="margin:0;gap:.3rem">Text <input type="color" name="text" style="width:2.6rem;padding:0"></label>
            <label class="small row" style="margin:0;gap:.3rem">Timer size <input type="number" name="timer_size" min="5" max="40" style="width:4.5rem"></label>
            <label class="small row" style="margin:0;gap:.3rem">Title size <input type="number" name="title_size" min="1" max="12" style="width:4rem"></label></div>
          <div class="row small" style="flex-wrap:wrap;margin-top:.4rem">Show: ${Object.entries(SHOW).map(([k, v]) => `<label class="row" style="margin:0;gap:.3rem"><input type="checkbox" name="${k}" style="width:auto">${v}</label>`).join("")}</div>
          <div class="row small hidden" data-dlogo style="margin-top:.4rem"></div>
          <div class="row" style="justify-content:flex-end;margin-top:.5rem"><button type="button" class="hidden" data-new>New view</button><button class="primary" data-bsave>Save view</button></div>
          <p class="small muted" style="margin:.3rem 0 0">Sizes are a share of the screen width. Saved views show in the screen picker and in Screens above.</p></form>` : ""}
        <h3 style="margin-top:1rem">Uploaded views</h3>
        <p class="small muted">Custom views made for Ontime work as they are: upload the view's folder as a .zip (with index.html), or a single .html file.</p>
        <table>${custom.map((v) => `<tr><td><b>${esc(v.name)}</b></td><td class="small">${boot.rooms.slice(0, 4).map((r) => `<a target="_blank" href="/room/${r.id}/external/${esc(v.slug)}/">${esc(r.name)} ↗</a>`).join(" · ")}</td>
          <td>${isAdmin ? `<button class="small danger" data-delview="${esc(v.slug)}">Remove</button>` : ""}</td></tr>`).join("") || '<tr><td class="muted small">No custom views yet.</td></tr>'}</table>
        ${isAdmin ? `<form class="row" id="upView" style="margin-top:.6rem"><input name="name" placeholder="View name, e.g. Lower third timer" required class="grow"><input type="file" name="file" accept=".zip,.html,.htm" required style="width:auto"><button class="primary">Upload view</button></form>` : ""}`;
      const again = () => api("/api/timers-views?screens=1").then((v) => { views.splice(0, views.length, ...v); renderScreens(); return renderViews(); });
      box.querySelectorAll("[data-delview]").forEach((b) => b.onclick = () => guard(() => del(`/api/timers-views/${b.dataset.delview}`)).then(() => timers(el)));
      const f = box.querySelector("#upView");
      if (f) f.onsubmit = (e) => { e.preventDefault(); guard(() => upload(`/api/timers-views?name=${encodeURIComponent(f.name.value)}`, f.file.files[0])).then(() => { toast("View added", "good"); timers(el); }); };
      box.querySelectorAll("[data-logo]").forEach((i) => i.onchange = () => i.files[0] && guard(() => upload(`/api/timers-views/look/${i.dataset.logo.split("/")[0]}/logo/${i.dataset.logo.split("/")[1]}`, i.files[0])).then(() => { toast("Logo saved", "good"); renderViews(); }));
      box.querySelectorAll("[data-unlogo]").forEach((b) => b.onclick = () => guard(() => del(`/api/timers-views/look/${b.dataset.unlogo.split("/")[0]}/logo/${b.dataset.unlogo.split("/")[1]}`)).then(renderViews));
      const st = box.querySelector("[data-savetext]");
      if (st) st.onclick = () => guard(() => put("/api/timers-views/look/bdng", { bottom_text: box.querySelector("[data-btext]").value })).then(() => toast("Saved", "good"));
      box.querySelectorAll("[data-deldesign]").forEach((b) => b.onclick = () => confirm("Remove this view? Screens showing it fall back to the stage timer.") && guard(() => del(`/api/timers-designs/${b.dataset.deldesign}`)).then(() => { if (editing === b.dataset.deldesign) editing = null; return again(); }));
      const bf = box.querySelector("#builder");
      if (!bf) return;
      const fill = (d, slug) => {
        editing = slug;
        for (const [k, v] of Object.entries({ ...D, ...d })) if (bf[k]) bf[k].type === "checkbox" ? (bf[k].checked = v) : (bf[k].value = v);
        bf.querySelector("[data-btitle]").textContent = slug ? `Edit ${d.name}` : "Build a view";
        bf.querySelector("[data-new]").classList.toggle("hidden", !slug);
        const pv = bf.querySelector("[data-preview]"), dl = bf.querySelector("[data-dlogo]");
        pv.classList.toggle("hidden", !slug || !boot.rooms.length);
        if (slug && boot.rooms.length) pv.href = `/timer/${boot.rooms[0].id}?view=built:${encodeURIComponent(slug)}`;
        dl.classList.toggle("hidden", !slug);
        if (slug) {
          const url = designs.find((x) => x.slug === slug)?.logo;
          dl.innerHTML = `Logo (top left): ${url ? `<img src="${esc(url)}" alt="" style="height:28px;max-width:140px;object-fit:contain;background:#555;border-radius:4px">` : '<span class="muted">none</span>'}
            <input type="file" accept=".png,.jpg,.jpeg,.svg,.webp" style="width:auto">${url ? '<button type="button" class="small" data-dunlogo>Remove</button>' : ""}`;
          dl.querySelector("input").onchange = (e) => e.target.files[0] && guard(() => upload(`/api/timers-views/look/built:${slug}/logo/logo`, e.target.files[0])).then(() => { toast("Logo saved. Tick Show: Logo to use it.", "good"); renderViews(); });
          dl.querySelector("[data-dunlogo]")?.addEventListener("click", () => guard(() => del(`/api/timers-views/look/built:${slug}/logo/logo`)).then(renderViews));
        }
      };
      const cur = designs.find((x) => x.slug === editing);
      cur ? fill(cur.design, cur.slug) : fill(D, null);
      box.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => { const d = designs.find((x) => x.slug === b.dataset.edit); fill(d.design, d.slug); bf.scrollIntoView({ block: "nearest" }); });
      bf.querySelector("[data-new]").onclick = () => fill(D, null);
      bf.onsubmit = async (e) => {
        e.preventDefault();
        const body = {};
        for (const k of Object.keys(D)) body[k] = bf[k].type === "checkbox" ? bf[k].checked : bf[k].type === "number" ? +bf[k].value : bf[k].value;
        const r = await guard(() => editing ? put(`/api/timers-designs/${editing}`, body) : post("/api/timers-designs", body));
        if (!r) return;
        toast("View saved", "good");
        editing = editing || r.slug;
        await again();
      };
    }

    renderScreens();
    renderViews();
    let pending = null;
    view = { onEvent: (e) => {
      if (e.type === "timer") states[e.data.room_id] = e.data;
      if (e.topic === "fleet" && !pending) pending = setTimeout(() => { pending = null; renderScreens(); }, 1500);
    } };
  }

  // ----------------------------------------------------------- captions --
  async function captions(el) {
    const render = async () => {
      const s = await api("/api/captions/status");
      const e = s.engine;
      el.innerHTML = `<h1>Captions</h1>
        <div class="panel" style="margin-bottom:1rem"><div class="row"><b>Speech engine</b><span class="pill ${e.state === "ready" ? "good" : e.state === "error" ? "bad" : "warn"}">${esc(e.state)}</span>
          <span class="muted small">${esc(e.detail)} ${e.state === "downloading" ? Math.round(e.progress) + "%" : ""}</span><span class="grow"></span>
          <span class="muted small">${e.active_rooms} of ${e.max_rooms} rooms live</span>${boot.me.role === "admin" ? '<button class="small" id="reload">Reload engine</button>' : ""}</div>
          ${e.skipped_vocabulary?.length ? `<p class="muted small">These vocabulary words couldn't be used: ${esc(e.skipped_vocabulary.join(", "))}</p>` : ""}</div>
        <p class="muted">A tech's laptop sends its microphone from the tech workspace. Audience screen: <code>/captions/&lt;room&gt;</code>. Transparent overlay for OBS or vMix: <code>/captions/&lt;room&gt;/overlay</code>.</p>
        <div class="grid">${s.rooms.map((r) => `<div class="panel" data-room="${r.id}">
          <div class="row" style="justify-content:space-between"><h2>${esc(r.name)}</h2><span class="pill ${r.live ? "good" : ""}">${r.live ? "Live from " + esc(r.source) : "Not live"}</span></div>
          <div class="row small"><a href="/captions/${r.id}" target="_blank">Audience ↗</a><a href="/captions/${r.id}/overlay" target="_blank">Overlay ↗</a></div>
          ${boot.me.role === "admin" ? `<label><input type="checkbox" data-f="enabled" ${r.enabled ? "checked" : ""} style="width:auto"> Captions on for this room</label>
          <label><input type="checkbox" data-f="record" ${r.record ? "checked" : ""} style="width:auto"> Save transcripts</label>
          <label>Vocabulary (names, jargon; one per line)</label><textarea data-f="vocabulary" rows="3">${esc(r.vocabulary)}</textarea>
          <div class="row" style="margin-top:.4rem"><button class="small" data-save>Save</button></div>` : ""}
          <div class="row" style="margin-top:.4rem"><input class="grow" placeholder="Test text for the screens" data-test><button class="small" data-send>Send</button><button class="small" data-clear>Clear</button></div></div>`).join("")}</div>
        <h2 style="margin-top:1.5rem">Transcripts</h2><div class="panel" id="tx"></div>`;
      el.querySelector("#reload") && (el.querySelector("#reload").onclick = () => guard(() => post("/api/captions/engine/reload")).then(() => setTimeout(render, 800)));
      el.querySelectorAll("[data-room]").forEach((card) => {
        const id = card.dataset.room;
        card.querySelector("[data-save]") && (card.querySelector("[data-save]").onclick = () => guard(() => put(`/api/captions/rooms/${id}`, {
          enabled: card.querySelector('[data-f=enabled]').checked, record: card.querySelector('[data-f=record]').checked, vocabulary: card.querySelector('[data-f=vocabulary]').value,
        })).then(() => toast("Saved", "good")));
        card.querySelector("[data-send]").onclick = () => guard(() => post(`/api/captions/${id}/test`, { text: card.querySelector("[data-test]").value || "Caption test" }));
        card.querySelector("[data-clear]").onclick = () => guard(() => post(`/api/captions/${id}/clear`));
      });
      const tx = await api("/api/captions/transcripts");
      el.querySelector("#tx").innerHTML = tx.length ? `<table><tr><th>Room</th><th>Started</th><th>Ended</th><th></th></tr>${tx.map((t) => `<tr><td>${esc(t.room)}</td><td>${when(t.started_at)}</td><td>${when(t.ended_at)}</td><td><a href="/api/captions/transcripts/${t.id}">Download</a></td></tr>`).join("")}</table>` : '<p class="muted">None saved yet.</p>';
    };
    view = { onEvent: (e) => { if (e.type === "status" || e.type === "captions.engine") render(); } };
    render();
  }

  // -------------------------------------------------------------- fleet --
  async function fleet(el) {
    let nodes = [];
    const render = async () => {
      nodes = await api("/api/fleet/nodes");
      const isAdmin = boot.me.role === "admin";
      el.innerHTML = `<div class="row" style="justify-content:space-between"><h1>Nodes</h1>${isAdmin ? '<a class="btn" href="#/admin/fleet">Add nodes</a>' : ""}</div>
        <div class="panel"><table><tr><th></th><th>Name</th><th>Kind</th><th>Room</th><th>Address</th><th>Version</th><th>Showing</th><th>Last seen</th><th></th></tr>
        ${nodes.map((n) => `<tr data-n="${n.id}"><td><span class="dot ${n.online ? "on" : "off"}"></span></td><td><b>${esc(n.name)}</b>${n.legacy ? ' <span class="pill">old agent</span>' : ""}${n.kind === "tech" && n.operator && n.room_id ? `<div class="small muted">${esc(n.operator)}${n.mode ? ` · <span class="pill ${n.mode === "backup" ? "warn" : ""}">${n.mode === "backup" ? "Backup" : "Main"}</span>` : ""}</div>` : ""}</td>
          <td>${isAdmin ? `<select data-kind style="width:auto">${["tech", "kiosk", "caption"].map((k) => `<option ${k === n.kind ? "selected" : ""}>${k}</option>`).join("")}</select>` : esc(n.kind)}</td>
          <td>${isAdmin ? `<select data-room style="width:auto">${roomOptions(n.room_id)}</select>` : esc(n.room_name || "")}</td>
          <td class="small">${esc(n.ip)}</td><td class="small">${esc(n.version)}</td><td class="small" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.current_url)}</td>
          <td class="small">${n.last_seen ? when(new Date(n.last_seen * 1000).toISOString()) : "never"}</td>
          <td class="row"><button class="small" data-url>Set screen</button><button class="small" data-cmd="identify">Identify</button>
          ${isAdmin ? '<button class="small" data-cmd="reboot">Reboot</button><button class="small danger" data-delnode>Remove</button>' : ""}</td></tr>`).join("") || '<tr><td colspan="9" class="muted">No nodes yet.</td></tr>'}</table></div>`;
      el.querySelectorAll("[data-n]").forEach((row) => {
        const id = +row.dataset.n, n = nodes.find((x) => x.id === id);
        const save = () => guard(() => put(`/api/fleet/nodes/${id}`, { room_id: row.querySelector("[data-room]").value ? +row.querySelector("[data-room]").value : null, kind: row.querySelector("[data-kind]").value }));
        row.querySelector("[data-room]") && (row.querySelector("[data-room]").onchange = save);
        row.querySelector("[data-kind]") && (row.querySelector("[data-kind]").onchange = save);
        row.querySelector("[data-url]").onclick = async () => {
          const links = n.room_id ? (await api(`/api/dashboard/links?room_id=${n.room_id}`)).filter((l) => l.room_id) : [];
          const hint = links.map((l, i) => `${i + 1}. ${l.label}`).join("\n");
          const ans = prompt(`Screen for ${n.name}: paste a URL${links.length ? " or pick a number\n" + hint : ""}`, n.current_url || "");
          if (!ans) return;
          const url = /^\d+$/.test(ans.trim()) && links[+ans - 1] ? links[+ans - 1].url : ans.trim();
          guard(() => post(`/api/fleet/nodes/${id}/command`, { kind: "set_url", payload: { url } })).then(() => toast("Queued", "good"));
        };
        row.querySelectorAll("[data-cmd]").forEach((b) => b.onclick = () => (b.dataset.cmd !== "reboot" || confirm(`Reboot ${n.name}?`)) && guard(() => post(`/api/fleet/nodes/${id}/command`, { kind: b.dataset.cmd })).then(() => toast("Sent", "good")));
        row.querySelector("[data-delnode]") && (row.querySelector("[data-delnode]").onclick = () => confirm(`Remove ${n.name}? It can enrol again later.`) && guard(() => del(`/api/fleet/nodes/${id}`)).then(render));
      });
    };
    let pending = null;
    view = { onEvent: (e) => { if (e.topic === "fleet" && !pending) pending = setTimeout(() => { pending = null; render(); }, 1500); } };
    render();
  }

  // -------------------------------------------------------------- admin --
  async function admin(el, sub = "general") {
    if (boot.me.role !== "admin") { el.innerHTML = '<p class="muted">Admins only.</p>'; return; }
    if (sub === "import" || sub === "audit") sub = "data";
    const tabs = { general: "General", info: "Info", licence: "Licence", sites: "Sites & rooms", accounts: "People", links: "Links", fleet: "Node setup", overlays: "Overlay laptops", keys: "API keys", data: "Import, backup & audit" };
    el.innerHTML = `<h1>Admin</h1><div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button class="${k === sub ? "on" : ""}" onclick="location.hash='#/admin/${k}'">${v}</button>`).join("")}</div><div id="adm"></div>`;
    const a = el.querySelector("#adm");
    ({ general: admGeneral, info: admInfo, licence: admLicence, sites: admSites, accounts: admAccounts, links: admLinks, fleet: admFleet, overlays: admOverlays, keys: admKeys, data: admData }[sub] || admGeneral)(a);
  }

  const copyText = async (text, what = "Copied") => {
    try { await navigator.clipboard.writeText(text); }
    catch (_) { const t = document.createElement("textarea"); t.value = text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove(); }
    toast(what, "good");
  };
  const bytes = (n) => n == null ? "–" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(1)} GB`;
  const ago = (s) => s == null ? "–" : s < 3600 ? `${Math.floor(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min` : `${Math.floor(s / 86400)} days ${Math.floor(s % 86400 / 3600)} h`;
  const date = (t) => t ? new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
  const kv = (rows) => `<table class="kv">${rows.filter(Boolean).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join("")}</table>`;

  async function admGeneral(a) {
    const s = await api("/api/admin/settings");
    const b = s.branding;
    a.innerHTML = `<div class="grid"><form class="panel" id="brand"><h2>Branding</h2>
      <label>Product name</label><input name="product_name" value="${esc(b.product_name)}">
      <label>Organisation</label><input name="organisation" value="${esc(b.organisation)}">
      <label>Accent colour</label><input name="accent" type="color" value="${esc(b.accent)}" style="height:2.4rem">
      <p class="small muted">Version, build and server details are in <a href="#/admin/info">Info</a>.</p>
      <div class="row" style="margin-top:.8rem"><button class="primary">Save</button></div></form>
      <form class="panel" id="mods"><h2>Modules</h2>${Object.entries(s.modules).map(([m, on]) => `<label><input type="checkbox" name="${m}" ${on ? "checked" : ""} style="width:auto"> ${m}${s.licence.modules.includes(m) ? "" : ' <span class="pill warn">not in licence</span>'}</label>`).join("")}
      <h2 style="margin-top:1rem">Other</h2>
      <label><input type="checkbox" name="legacy" ${s.legacy_fleet_api ? "checked" : ""} style="width:auto"> Accept old kiosk agents (Device Suite API, no sign-in)</label>
      <label>Delete chat messages older than (days, 0 = keep)</label><input name="retention" type="number" min="0" value="${s.message_retention_days}">
      <div class="row" style="margin-top:.8rem"><button class="primary">Save</button></div></form></div>`;
    const bf = a.querySelector("#brand");
    bf.onsubmit = (e) => { e.preventDefault(); guard(() => put("/api/admin/settings", { branding: Object.fromEntries(new FormData(bf)) })).then(() => location.reload()); };
    const mf = a.querySelector("#mods");
    mf.onsubmit = (e) => {
      e.preventDefault();
      const modules = Object.fromEntries(Object.keys(s.modules).map((m) => [m, mf[m].checked]));
      guard(() => put("/api/admin/settings", { modules, legacy_fleet_api: mf.legacy.checked, message_retention_days: +mf.retention.value })).then(() => location.reload());
    };
  }

  async function admInfo(a) {
    const i = await api("/api/admin/info"), sv = i.server, st = i.storage, n = i.nodes, c = i.counts;
    const kinds = { tech: "Tech laptops", screen: "Linux screens", caption: "Caption sources", kiosk: "Old kiosk agents" };
    const on = (b) => b ? `<span class="pill good">${b.online} online</span> of ${b.total}` : '<span class="muted">none</span>';
    const pill = (ok, yes, no) => `<span class="pill ${ok ? "good" : "warn"}">${ok ? yes : no}</span>`;
    a.innerHTML = `<div class="row" style="margin-bottom:1rem"><a class="btn primary" href="/api/admin/info?download=1">Download diagnostics</a><button id="copyInfo">Copy for a support ticket</button><button id="refresh">Refresh</button>
        <span class="muted small">No passwords, keys or tokens are included.</span></div>
      <div class="grid">
      <div class="panel"><h2>This app</h2>${kv([["Product", esc(i.product)], ["Organisation", esc(i.organisation) || '<span class="muted">not set</span>'], ["Version", `<b>${esc(i.version)}</b>`],
        ["Build", esc(i.build.number)], ["Commit", `<code>${esc(i.build.commit)}</code>`], i.build.date && ["Built", esc(new Date(i.build.date).toLocaleString())], ["Database schema", i.schema],
        ["Licence", `${pill(i.licence.valid, "Licensed", "Evaluation")} ${esc(i.licence.licensee)} · <a href="#/admin/licence">details</a>`]])}</div>
      <div class="panel"><h2>Servers</h2><p class="small muted">${i.server.count} server: everything runs on this one.</p>${kv([["Hostname", `<b>${esc(sv.hostname)}</b>`], ["Addresses", sv.ips.map(esc).join(", ") || "–"],
        ["Opened as", `<code>${esc(location.origin)}</code>`], sv.public_url && ["Public address", esc(sv.public_url)], ["Running in", sv.in_docker ? "Docker" : "Python (no container)"],
        ["Up for", ago(sv.uptime_seconds)], sv.system_uptime_seconds != null && ["Machine up for", ago(sv.system_uptime_seconds)], ["Server time", `${esc(new Date(sv.time).toLocaleString())} (${esc(sv.timezone)})`],
        ["Site time zones", Object.entries(sv.site_timezones).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join("<br>")]])}</div>
      <div class="panel"><h2>Nodes</h2>${kv([["All nodes", on(n)], ...Object.entries(kinds).map(([k, v]) => [v, on(n.by_kind[k])]), ["Main PCs", n.tech_main], ["Backup PCs", n.tech_backup],
        ["Live connections", `${i.websockets} <span class="muted small">(browsers, laptops and screens)</span>`]])}</div>
      <div class="panel"><h2>Apps and agents</h2>${kv([["Windows tech app", i.apps.windows_app ? esc(i.apps.windows_app) : '<span class="muted">not published</span>'], ["Screen agent", esc(i.apps.screen_agent || "–")],
        ["Node agent", esc(i.apps.node_agent || "–")], ["Old kiosk agent", esc(i.apps.kiosk_agent || "not published")]])}<p class="small"><a href="#/admin/fleet">Node setup and downloads</a></p></div>
      <div class="panel"><h2>Venue</h2>${kv([["Sites", c.sites], ["Rooms", c.rooms], ["Accounts", `${c.accounts} <span class="muted small">(${Object.entries(c.accounts_by_role).map(([k, v]) => `${v} ${esc(k)}`).join(", ")})</span>`],
        ["Links", c.links], ["API keys", c.api_keys], ["Chat messages", c.messages], ["Help requests", `${c.help_requests} (${c.open_help_requests} open)`]])}</div>
      <div class="panel"><h2>Modules</h2>${kv(Object.entries(i.modules).map(([k, v]) => [k, pill(v, "on", "off")]))}</div>
      <div class="panel"><h2>Captions</h2>${kv([["Engine", `${pill(i.captions.state === "ready", esc(i.captions.state), esc(i.captions.state))}`], i.captions.detail && ["Detail", esc(i.captions.detail)], ["Model", `<span class="small">${esc(i.captions.model)}</span>`],
        ["Rooms captioning", `${i.captions.active_rooms} of ${i.captions.max_rooms} max`]])}</div>
      <div class="panel"><h2>Storage</h2>${kv([["Data folder", `<code>${esc(st.data_dir)}</code>`], ["Database", bytes(st.db_bytes)], ["Uploads", bytes(st.uploads_bytes)], ["Transcripts", bytes(st.transcripts_bytes)],
        ["Presenter files", `${bytes(st.presenter_bytes)} <div class="small muted">${esc(st.presenter_dir)}</div>`], st.disk.total && ["Disk free", `${bytes(st.disk.free)} of ${bytes(st.disk.total)}`],
        ["Last backup", i.last_backup ? `${when(i.last_backup.at)} by ${esc(i.last_backup.actor)}` : '<span class="pill warn">never</span>']])}</div>
      <div class="panel"><h2>Software</h2>${kv([["Python", esc(sv.python)], ["FastAPI", esc(sv.fastapi)], ["Uvicorn", esc(sv.uvicorn)], ["SQLite", esc(sv.sqlite)], ["CPUs", sv.cpus], ["OS", `<span class="small">${esc(sv.os)}</span>`],
        ["Your browser", `<span class="small">${esc(navigator.userAgent)}</span>`]])}</div>
      </div>`;
    a.querySelector("#refresh").onclick = () => admInfo(a);
    a.querySelector("#copyInfo").onclick = () => copyText(`${i.product} ${i.version} (build ${i.build.number}, ${i.build.commit}) on ${sv.hostname}\nOpened as ${location.origin}\n\n${JSON.stringify(i, null, 2)}`, "Copied, paste it into the ticket");
  }

  async function admLicence(a) {
    const l = await api("/api/admin/licence");
    const lim = (k) => { const used = l.usage[k], max = l.limits[k]; return `${used} used of ${max || "unlimited"}${max && used >= max ? ' <span class="pill warn">full</span>' : ""}`; };
    const state = l.valid ? '<span class="pill good">Licensed</span>' : l.installed ? '<span class="pill bad">Not valid</span>' : '<span class="pill warn">Evaluation</span>';
    const extra = Object.entries(l.payload).filter(([k]) => !["licensee", "edition", "expires", "issued", "max_nodes", "max_sites", "modules", "serial", "id"].includes(k));
    a.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))"><div class="panel"><h2>Licence</h2><p>${state} ${l.reason ? `<span class="muted">${esc(l.reason)}</span>` : ""}</p>
      ${kv([["Licensed to", `<b>${esc(l.licensee)}</b>`], ["Edition", esc(l.edition)], ["Serial", l.serial ? `<code>${esc(l.serial)}</code>` : "–"], ["Issued", date(l.issued) || "–"],
        ["Expires", l.expires ? `${date(l.expires)} <span class="pill ${l.days_left < 30 ? "warn" : ""}">${l.days_left} days left</span>` : "Never"],
        ["Modules", l.modules.map((m) => `<span class="pill">${esc(m)}</span>`).join(" ")], ...extra.map(([k, v]) => [k, esc(typeof v === "object" ? JSON.stringify(v) : String(v))])])}</div>
      <div class="panel"><h2>Limits</h2>${kv([["Sites", lim("sites")], ["Nodes", lim("nodes")], ["Rooms", `${l.usage.rooms} (no limit)`]])}
      <h2 style="margin-top:1rem">Check</h2>${kv([["Signature", l.installed ? (l.signature_valid ? '<span class="pill good">valid</span>' : '<span class="pill bad">not valid</span>') : "no key installed"],
        ["Vendor key", `<code>${esc(l.vendor_key_id || "none")}</code> <span class="muted small">${esc(l.vendor_key_source)}</span>`], ["Stored in", `<span class="small">${esc(l.stored_in)}</span>`]])}</div></div>
      <div class="panel" style="margin-top:1rem"><h2>Licence key</h2>${l.raw ? `<textarea rows="3" readonly id="raw">${esc(l.raw)}</textarea><div class="row" style="margin-top:.4rem"><button class="small" id="copyKey">Copy key</button></div>` : '<p class="muted small">No key installed. AT-SUIT runs in evaluation mode: every module, one site, five nodes.</p>'}
      <label style="margin-top:1rem">Install a new key</label><textarea id="key" rows="3" placeholder="Paste the key from your supplier"></textarea><div class="row" style="margin-top:.6rem"><button class="primary" id="save">Install licence</button></div></div>`;
    a.querySelector("#copyKey") && (a.querySelector("#copyKey").onclick = () => copyText(l.raw, "Key copied"));
    a.querySelector("#save").onclick = () => guard(() => put("/api/admin/licence", { key: a.querySelector("#key").value })).then(() => location.reload());
  }

  async function admSites(a) {
    const sites = await api("/api/admin/sites");
    boot = await api("/api/bootstrap");
    a.innerHTML = sites.map((s) => `<div class="panel" style="margin-bottom:1rem" data-site="${s.id}">
      <div class="row"><input data-sn value="${esc(s.name)}" class="grow"><input data-tz value="${esc(s.timezone)}" style="width:12rem"><button class="small" data-save>Save</button>${sites.length > 1 ? '<button class="small danger" data-delsite>Delete site</button>' : ""}</div>
      <table style="margin-top:.6rem"><tr><th>Order</th><th>Room</th><th>Short name</th><th>On</th><th></th></tr>
      ${boot.rooms.filter((r) => r.site_id === s.id).map((r) => `<tr data-r="${r.id}"><td><input data-sort type="number" value="${r.sort}" style="width:4.5rem"></td><td><input data-name value="${esc(r.name)}"></td><td><input data-short value="${esc(r.short_name)}" style="width:7rem"></td>
        <td><input type="checkbox" data-en ${r.enabled ? "checked" : ""} style="width:auto"></td><td class="row"><button class="small" data-rsave>Save</button><button class="small danger" data-rdel>Delete</button></td></tr>`).join("")}
      <tr><td></td><td><input data-newroom placeholder="New room name"></td><td></td><td></td><td><button class="small primary" data-add>Add room</button></td></tr></table></div>`).join("") +
      `<form class="panel row" id="ns"><input name="name" class="grow" placeholder="New site (venue) name" required><button class="primary">Add site</button></form>`;
    a.querySelectorAll("[data-site]").forEach((p) => {
      const sid = +p.dataset.site;
      p.querySelector("[data-save]").onclick = () => guard(() => put(`/api/admin/sites/${sid}`, { name: p.querySelector("[data-sn]").value, timezone: p.querySelector("[data-tz]").value })).then(() => toast("Saved", "good"));
      p.querySelector("[data-delsite]") && (p.querySelector("[data-delsite]").onclick = () => confirm("Delete this site and all its rooms, chat and links?") && guard(() => del(`/api/admin/sites/${sid}`)).then(() => admSites(a)));
      p.querySelector("[data-add]").onclick = () => guard(() => post("/api/admin/rooms", { site_id: sid, name: p.querySelector("[data-newroom]").value })).then(() => admSites(a));
      p.querySelectorAll("[data-r]").forEach((row) => {
        const rid = +row.dataset.r;
        row.querySelector("[data-rsave]").onclick = () => guard(() => put(`/api/admin/rooms/${rid}`, { site_id: sid, name: row.querySelector("[data-name]").value, short_name: row.querySelector("[data-short]").value, sort: +row.querySelector("[data-sort]").value, enabled: row.querySelector("[data-en]").checked })).then(() => toast("Saved", "good"));
        row.querySelector("[data-rdel]").onclick = () => confirm("Delete this room, its chat and its timer?") && guard(() => del(`/api/admin/rooms/${rid}`)).then(() => admSites(a));
      });
    });
    const f = a.querySelector("#ns");
    f.onsubmit = (e) => { e.preventDefault(); guard(() => post("/api/admin/sites", { name: f.name.value })).then(() => admSites(a)); };
  }

  async function admAccounts(a) {
    const [accts, sites] = await Promise.all([api("/api/admin/accounts"), api("/api/admin/sites")]);
    const siteOpts = (sel) => `<option value="">All sites</option>` + sites.map((s) => `<option value="${s.id}" ${s.id === sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");
    const roleOpts = (sel) => ["admin", "tech", "viewer"].map((r) => `<option ${r === sel ? "selected" : ""}>${r}</option>`).join("");
    a.innerHTML = `<div class="panel"><p class="muted small">Admins configure everything. Techs chat, run timers, send captions and control nodes. Viewers can read.</p><table><tr><th>Username</th><th>Name</th><th>Role</th><th>Site</th><th>Active</th><th>New password</th><th></th></tr>
      ${accts.map((u) => `<tr data-u="${u.id}"><td><input data-un value="${esc(u.username)}"></td><td><input data-dn value="${esc(u.display_name)}"></td><td><select data-role>${roleOpts(u.role)}</select></td>
        <td><select data-site>${siteOpts(u.site_id)}</select></td><td><input type="checkbox" data-act ${u.active ? "checked" : ""} style="width:auto"></td><td><input data-pw type="password" placeholder="unchanged"></td>
        <td class="row"><button class="small" data-save>Save</button>${u.id === boot.me.id ? "" : '<button class="small danger" data-del>Delete</button>'}</td></tr>`).join("")}
      <tr id="new"><td><input data-un placeholder="username"></td><td><input data-dn placeholder="Display name"></td><td><select data-role>${roleOpts("tech")}</select></td><td><select data-site>${siteOpts(null)}</select></td><td></td><td><input data-pw type="password" placeholder="password (8+)"></td><td><button class="small primary" data-add>Add</button></td></tr></table></div>`;
    const read = (row) => ({ username: row.querySelector("[data-un]").value, display_name: row.querySelector("[data-dn]").value, role: row.querySelector("[data-role]").value,
      site_id: row.querySelector("[data-site]").value ? +row.querySelector("[data-site]").value : null, active: row.querySelector("[data-act]") ? row.querySelector("[data-act]").checked : true, password: row.querySelector("[data-pw]").value || null });
    a.querySelectorAll("[data-u]").forEach((row) => {
      row.querySelector("[data-save]").onclick = () => guard(() => put(`/api/admin/accounts/${row.dataset.u}`, read(row))).then(() => toast("Saved", "good"));
      row.querySelector("[data-del]") && (row.querySelector("[data-del]").onclick = () => confirm("Delete this account?") && guard(() => del(`/api/admin/accounts/${row.dataset.u}`)).then(() => admAccounts(a)));
    });
    a.querySelector("[data-add]").onclick = () => guard(() => post("/api/admin/accounts", read(a.querySelector("#new")))).then(() => admAccounts(a));
  }

  async function admLinks(a) {
    const links = await api("/api/dashboard/links");
    const kinds = ["link", "timer", "buttons", "kiosk", "device", "tool"];
    const row = (l = {}) => `<tr data-l="${l.id || ""}"><td><input data-label value="${esc(l.label || "")}" placeholder="Label"></td><td><input data-url value="${esc(l.url || "")}" placeholder="http://"></td>
      <td><select data-room>${roomOptions(l.room_id, "Whole site")}</select></td><td><select data-board>${["public", "admin"].map((b) => `<option ${b === l.board ? "selected" : ""}>${b}</option>`).join("")}</select></td>
      <td><select data-kind>${kinds.map((k) => `<option ${k === l.kind ? "selected" : ""}>${k}</option>`).join("")}</select></td>
      <td class="row">${l.id ? '<button class="small" data-save>Save</button><button class="small danger" data-del>Delete</button>' : '<button class="small primary" data-save>Add</button>'}</td></tr>`;
    a.innerHTML = `<div class="panel"><p class="small" style="margin-top:0"><b>What these are:</b> shortcuts to other web pages (a Companion button page, a room's camera control, a tool). Pick <b>Whole site</b> to show a link to everyone on the Dashboard, or pick a <b>room</b> so it appears only in that room's tech workspace, under <b>Links</b>. <b>Admin</b> links are only shown to admins. The <b>kind</b> only decides the heading it is grouped under. You don't have to add any: the workspace works without them.</p>
      <table><tr><th>Label</th><th>URL</th><th>Room</th><th>Board</th><th>Kind</th><th></th></tr>${row()}${links.map(row).join("")}</table></div>`;
    a.querySelectorAll("[data-l]").forEach((tr) => {
      const body = () => ({ label: tr.querySelector("[data-label]").value, url: tr.querySelector("[data-url]").value, board: tr.querySelector("[data-board]").value, kind: tr.querySelector("[data-kind]").value,
        room_id: tr.querySelector("[data-room]").value ? +tr.querySelector("[data-room]").value : null, site_id: boot.sites[0]?.id ?? null });
      const id = tr.dataset.l;
      tr.querySelector("[data-save]").onclick = () => guard(() => id ? put(`/api/dashboard/links/${id}`, body()) : post("/api/dashboard/links", body())).then(() => id ? toast("Saved", "good") : admLinks(a));
      tr.querySelector("[data-del]") && (tr.querySelector("[data-del]").onclick = () => guard(() => del(`/api/dashboard/links/${id}`)).then(() => admLinks(a)));
    });
  }

  async function admFleet(a, part = "windows") {
    const origin = location.origin;
    let sites;
    try { sites = await api("/api/fleet/enrolment"); }
    catch (_) { a.innerHTML = '<div class="panel card"><p class="muted">The fleet module is turned off. Turn it on in <a href="#/admin/general">General</a> to add laptops and screens.</p></div>'; return; }
    const [winApp, info] = await Promise.all([api("/api/nodes/app").catch(() => ({})), api("/api/admin/info")]);
    const code = sites[0]?.enrol_code || "CODE";
    const parts = { windows: "Windows tech app", screens: "Linux screens", ...(boot.modules.presenter ? { sync: "Room sync (presentation laptops)" } : {}), older: "Older agents" };
    if (!parts[part]) part = "windows";
    const cmd = (text) => `<div class="cmd"><code>${esc(text)}</code><button class="small" data-copy="${esc(text)}">Copy</button></div>`;
    const dl = (href, label, primary) => `<a class="btn${primary ? " primary" : ""}" href="${esc(href)}" download>${label}</a>`;
    const nodeJson = JSON.stringify({ server: origin, enrol_code: code }, null, 2);
    const sections = {
      windows: `<h2>AT-SUIT Node for Windows</h2><p class="small muted">The tech workspace app for the laptops in each room: timer, cue list, chat, help calls and pop-ups.</p>
        <div class="row" style="margin:.6rem 0">${winApp.ready ? dl(`/api/nodes/app/${encodeURIComponent(winApp.file)}`, `Download AT-SUIT Node ${esc(winApp.version)} (.exe)`, true) : '<span class="pill warn">No Windows app published yet</span> <span class="small muted">Upload one below.</span>'}</div>
        <h3>Install</h3><ol class="small"><li>Download and run the installer on the laptop. It isn't code-signed yet, so Windows asks once: <b>More info → Run anyway</b>.</li>
          <li>Enter this server's address <code>${esc(origin)}</code>, the enrolment code above and a laptop name. This is a one-off; the laptop stays enrolled.</li>
          <li>Each day a tech types their name, picks the room and chooses <b>Main PC</b> or <b>Backup PC</b>. It resets every morning.</li>
          <li>Pop-ups only show on the backup PC. They're silent and sit on top of everything.</li></ol>
        <h3>Silent install (IT)</h3><p class="small muted">Put this in <code>C:\\ProgramData\\AT-SUIT\\node.json</code>, then run the installer with <code>/S</code>. The app enrols under the PC's name on first start.</p>
        <pre class="small">${esc(nodeJson)}</pre><div class="row"><button class="small" id="dlJson">Download node.json</button><button class="small" data-copy="${esc(nodeJson)}">Copy</button></div>
        <h3>Publish a new version</h3><p class="small muted">The .exe, .blockmap and latest.yml from the GitHub release. Laptops update the next time the app closes, never during a show.</p>
        <div class="row"><input type="file" id="appFiles" class="grow" multiple accept=".exe,.blockmap,.yml"><button class="small" id="upApp">Publish app</button></div>
        <p class="small muted">A browser at <code>${esc(origin)}/node</code> works too, without installing anything.</p>`,
      screens: `<h2>Linux screens</h2><p class="small muted">Laptops and all-in-ones that only show a timer, captions or a view. Agent ${esc(info.apps.screen_agent || "–")}. Screens update themselves.</p>
        <h3>Install</h3><p class="small">Run this once on the screen, as the user logged in to the desktop (X11, automatic login on). Change the name for each screen.</p>
        ${cmd(`curl -fsSL ${origin}/screen-agent/install.sh | bash -s -- --server ${origin} --code ${code} --name HD-STAGE-1 --allow-power`)}
        <p class="small muted">Leave out <code>--allow-power</code> if the console shouldn't be able to reboot it. With HDMI plugged in, it shows only on HDMI. Then pick what it shows in <a href="#/timers">Timers → Screens</a>.</p>
        <h3>Downloads</h3><div class="row">${dl("/screen-agent/install.sh", "install.sh")}${dl("/screen-agent/atsuit_screen.py", "Screen agent (atsuit_screen.py)")}</div>
        <p class="small muted">No agent? Any browser can be a screen: open <code>${esc(origin)}/screen</code> and enter a name and the enrolment code.</p>`,
      sync: `<h2>Room sync for presentation laptops</h2><p class="small muted">Keeps a folder on the room's presentation laptop with every session's approved slides and show files. Needs Python 3.8 or later, nothing else.</p>
        <div class="row" style="margin:.6rem 0">${dl("/api/presenter/room-sync/atsuit_room_sync.py", "Download atsuit_room_sync.py", true)}</div>
        <h3>Set up</h3><ol class="small"><li>Make the room's sync code in <a href="#/presenter">Presenters → Settings</a>. It's not the enrolment code.</li><li>On the laptop run:</li></ol>
        ${cmd(`python atsuit_room_sync.py --server ${origin} --code ROOM-CODE`)}
        <p class="small muted">The first run saves the settings, so after that <code>python atsuit_room_sync.py</code> is enough. Put it in the laptop's startup.</p>`,
      older: `<h2>Node agent (Python)</h2><p class="small muted">Sends captions from a laptop without a browser, or runs a kiosk. Version ${esc(info.apps.node_agent || "–")}. Agents update themselves on their next check-in.</p>
        <div class="row" style="margin:.6rem 0">${dl("/api/admin/downloads/atsuit_node.py", "Download atsuit_node.py")}</div>
        ${cmd(`python atsuit_node.py --server ${origin} --code ${code} --name ATLAP3 --room CC --mic`)}
        <label>Publish a newer agent</label><div class="row"><input type="file" id="agentFile" class="grow" accept=".py"><button class="small" id="upAgent">Publish agent</button></div>
        <h2 style="margin-top:1.4rem">Device Suite kiosk agent</h2><p class="small muted">Kiosks running the old Device Suite agent report here without reinstalling: point their server address at <code>${esc(origin)}</code>. ${info.settings.legacy_fleet_api ? "" : '<span class="pill warn">Turned off in General</span>'} Reboot, shut down and update go over SSH, so upload the fleet key.</p>
        <div class="row" style="margin:.6rem 0">${info.apps.kiosk_agent ? dl("/api/admin/downloads/kiosk-agent", `Download kiosk agent ${esc(info.apps.kiosk_agent)}`) : '<span class="muted small">No kiosk agent release published.</span>'}</div>
        <label>Publish a kiosk agent release (script)</label><div class="row"><input type="file" id="rel" class="grow"><input id="ver" placeholder="version, e.g. 2.0.4" style="width:9rem"><button class="small" id="upRel">Publish</button></div>
        <label>Fleet SSH private key</label><div class="row"><input type="file" id="key" class="grow"><button class="small" id="upKey">Upload key</button></div>`,
    };
    a.innerHTML = `<div class="panel" style="margin-bottom:1rem"><div class="row" style="justify-content:space-between;flex-wrap:wrap"><h2 style="margin:0">Enrolment</h2><span class="small muted">Server address <code>${esc(origin)}</code> <button class="small" data-copy="${esc(origin)}">Copy</button></span></div>
        <p class="small muted">Laptops and screens join with the site's code. A new code stops the old one working for new devices; ones already enrolled carry on.</p>
        ${sites.map((s) => `<div class="row enrol"><b class="grow">${esc(s.name)}</b><code class="code">${esc(s.enrol_code)}</code><button class="small" data-copy="${esc(s.enrol_code)}">Copy</button><button class="small" data-new="${s.id}">New code</button></div>`).join("")}</div>
      <div class="tabs sub">${Object.entries(parts).map(([k, v]) => `<button class="${k === part ? "on" : ""}" data-part="${k}">${v}</button>`).join("")}</div>
      <div class="panel" id="part">${sections[part]}</div>`;
    a.querySelectorAll("[data-part]").forEach((b) => b.onclick = () => admFleet(a, b.dataset.part));
    a.querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => copyText(b.dataset.copy));
    a.querySelectorAll("[data-new]").forEach((b) => b.onclick = () => confirm("Make a new code? The old one stops working for new laptops and screens.") && guard(() => post(`/api/admin/sites/${b.dataset.new}/enrol-code`)).then(() => admFleet(a, part)));
    const on = (id, fn) => { const e = a.querySelector(id); e && (e.onclick = fn); };
    on("#dlJson", () => { const u = URL.createObjectURL(new Blob([nodeJson], { type: "application/json" })), l = document.createElement("a"); l.href = u; l.download = "node.json"; l.click(); setTimeout(() => URL.revokeObjectURL(u), 1000); });
    on("#upApp", () => {
      const files = [...a.querySelector("#appFiles").files];
      if (!files.length) return;
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      guard(() => api("/api/fleet/app", { method: "POST", form: fd })).then((r) => { toast(`AT-SUIT Node ${r.version} published`, "good"); admFleet(a, part); });
    });
    on("#upAgent", () => { const f = a.querySelector("#agentFile").files[0]; f && guard(() => upload("/api/fleet/agent", f)).then((r) => { toast(`Agent ${r.version} published`, "good"); admFleet(a, part); }); });
    on("#upKey", () => { const f = a.querySelector("#key").files[0]; f && guard(() => upload("/api/fleet/ssh-key", f)).then(() => toast("Key saved", "good")); });
    on("#upRel", () => { const f = a.querySelector("#rel").files[0], v = a.querySelector("#ver").value.trim(); f && v && guard(() => upload(`/api/fleet/client-release?version=${encodeURIComponent(v)}`, f)).then(() => { toast("Published", "good"); admFleet(a, part); }); });
  }

  async function admOverlays(a) {
    // Setup only: techs control a room's overlays from their workspace.
    const targets = await api("/api/overlays/targets");
    a.innerHTML = `<div class="panel card" style="max-width:900px"><h2>Overlay laptops</h2>
      <p class="small muted">Each laptop running AT LiveOverlay shows its address and API token under Remote Control in its tray menu. Give it a room and the techs in that room get its controls in their workspace.</p>
      <table>${targets.map((t) => `<tr><td><b>${esc(t.name)}</b></td><td class="small">${esc(t.base_url)}</td><td>${esc(t.room || "No room")}</td><td><button class="small danger" data-del="${t.id}">Remove</button></td></tr>`).join("") || '<tr><td class="muted">None yet.</td></tr>'}</table>
      <form class="row" id="addT" style="margin-top:.6rem;flex-wrap:wrap"><input name="name" placeholder="Name, e.g. CC Lectern" required style="width:12rem">
        <input name="base_url" placeholder="http://10.100.70.88:8765" required class="grow"><input name="token" placeholder="API token" style="width:14rem">
        <select name="room_id" style="width:10rem">${roomOptions(null)}</select><button class="primary">Add</button></form></div>`;
    const f = a.querySelector("#addT");
    f.onsubmit = (e) => { e.preventDefault(); const b = Object.fromEntries(new FormData(f)); b.room_id = b.room_id ? +b.room_id : null; guard(() => post("/api/overlays/targets", b)).then(() => admOverlays(a)); };
    a.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => guard(() => del(`/api/overlays/targets/${b.dataset.del}`)).then(() => admOverlays(a)));
  }

  async function admKeys(a) {
    const keys = await api("/api/admin/api-keys");
    a.innerHTML = `<div class="panel card"><h2>API keys</h2><p class="small muted">For Bitfocus Companion and other automation. Send the key in an <code>X-API-Key</code> header, e.g. <code>POST /api/timers/&lt;room&gt;/toggle</code>. <b><a href="/guide/companion" target="_blank">Companion guide</a></b>: set-up steps, ready-made buttons for presets, blink, messages and overlays, and every endpoint a key can call. Full API reference: <a href="/api/docs" target="_blank">/api/docs</a>.</p>
      <table>${keys.map((k) => `<tr><td>${esc(k.name)}</td><td><code>${esc(k.prefix)}…</code></td><td>${when(k.created_at)}</td><td><button class="small danger" data-del="${k.id}">Revoke</button></td></tr>`).join("")}</table>
      <form class="row" id="nk" style="margin-top:.6rem"><input name="name" class="grow" placeholder="Key name, e.g. Companion" required><button class="primary">Create</button></form><p id="newkey"></p></div>`;
    a.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => confirm("Revoke this key?") && guard(() => del(`/api/admin/api-keys/${b.dataset.del}`)).then(() => admKeys(a)));
    const f = a.querySelector("#nk");
    f.onsubmit = async (e) => { e.preventDefault(); const r = await guard(() => post("/api/admin/api-keys", { name: f.name.value })); await admKeys(a); a.querySelector("#newkey").innerHTML = `Copy this key now, it won't be shown again:<br><code>${esc(r.key)}</code>`; };
  }

  async function admData(a) {
    const items = [
      ["roomcomms", "RoomComms chat", "A zip of the RoomComms data folder (roomcomms.db and .encryption_key). Accounts keep their passwords."],
      ["rooms-txt", "Device Suite rooms.txt", "Room kiosk links (Ontime views, Companion buttons). Lines that aren't ROOM|URL|LABEL; are skipped."],
      ["device-state", "Device Suite state.json", "Known kiosks with their addresses."],
      ["homarr", "Homarr links", "The TSV export of the Homarr USER CONTROL board."],
    ];
    const log = await api("/api/admin/audit");
    const last = log.find((l) => l.action === "backup.download");
    a.innerHTML = `<div class="panel"><h2>Backup</h2><p class="small muted">Database, uploads and the encryption key in one zip. Keep it somewhere safe; it can read every message.</p>
        <div class="row"><a class="btn primary" href="/api/admin/backup" id="bk">Download backup</a><span class="small muted">${last ? `Last backup ${when(last.at)} by ${esc(last.actor)}` : "No backup downloaded yet."}</span></div></div>
      <h2 style="margin-top:1.2rem">Import</h2><p class="small muted">Bring in data from the tools AT-SUIT replaces.</p>
      <div class="grid">${items.map(([k, t, d]) => `<div class="panel" data-k="${k}"><h3>${t}</h3><p class="small muted">${d}</p><input type="file"><button class="small primary" style="margin-top:.5rem">Import</button><pre class="small muted" style="white-space:pre-wrap"></pre></div>`).join("")}</div>
      <div class="panel" style="margin-top:1.2rem"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Audit log</h2><input id="flt" placeholder="Filter" style="width:14rem"></div>
        <table style="margin-top:.6rem"><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr><tbody id="log"></tbody></table></div>`;
    const draw = (q = "") => { a.querySelector("#log").innerHTML = log.filter((l) => !q || `${l.actor} ${l.action} ${l.detail}`.toLowerCase().includes(q)).map((l) => `<tr><td class="small">${when(l.at)}</td><td>${esc(l.actor)}</td><td>${esc(l.action)}</td><td class="small muted">${esc(l.detail)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">Nothing yet.</td></tr>'; };
    draw();
    a.querySelector("#flt").oninput = (e) => draw(e.target.value.trim().toLowerCase());
    a.querySelector("#bk").onclick = () => setTimeout(() => admData(a), 3000);
    a.querySelectorAll("[data-k]").forEach((p) => p.querySelector("button").onclick = async () => {
      const f = p.querySelector("input").files[0]; if (!f) return;
      const r = await guard(() => upload(`/api/admin/import/${p.dataset.k}`, f));
      p.querySelector("pre").textContent = JSON.stringify(r, null, 2);
      boot = await api("/api/bootstrap");
    });
  }

})();
