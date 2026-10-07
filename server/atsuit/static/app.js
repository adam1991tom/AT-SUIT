// Console: dashboard, chat, help, timers, captions, overlays, nodes, admin.
(async () => {
  const { esc, api, post, put, del, upload, guard, toast, when, fmtTime, timerClock } = AT;
  const main = document.getElementById("main");
  let boot, sock, chat = null, view = null, helpOpen = 0;

  await AT.branding();
  try { boot = await api("/api/bootstrap"); } catch (e) { return showLogin(); }
  if (boot.me.kind === "node") { location.replace("/node"); return; }
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
      ...boot.rooms.map((r) => `timer:${r.id}`), ...boot.rooms.map((r) => `captions:${r.id}`)];
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
    const banner = boot.licence.valid ? "" : `<div class="banner">Evaluation mode: ${esc(boot.licence.reason)}. Add a licence in Admin → Licence.</div>`;
    main.innerHTML = banner + '<div id="view"></div>';
    const el = document.getElementById("view");
    const views = { dashboard, chat: chatView, help, timers, captions, overlays, fleet, admin };
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
    el.innerHTML = `<div class="row" style="justify-content:space-between"><h1>Dashboard</h1>${boot.me.role === "admin" ? '<a class="btn" href="#/admin/links">Edit links</a>' : ""}</div>` +
      (links.length ? Object.entries(groups).map(([g, ls]) => `<h3 style="margin-top:1.2rem">${esc(g)}</h3><div class="tiles">` +
        ls.map((l) => `<a class="tile" href="${esc(l.url)}" target="_blank" rel="noopener"><span class="dot" data-ping="${l.id}"></span><span class="grow">${esc(l.label)}</span></a>`).join("") + "</div>").join("")
        : '<p class="muted">No links yet. Admins can import the old Homarr board or add links in Admin → Links.</p>');
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
    const states = {};
    el.innerHTML = `<h1>Timers</h1><p class="muted">Stage screens: open <code>/timer/&lt;room&gt;</code> on any screen, no sign-in needed. Companion can drive these with an API key.</p><div class="grid" id="tg"></div>`;
    const grid = el.querySelector("#tg");
    for (const r of boot.rooms) {
      states[r.id] = await api(`/api/timers/${r.id}`);
      grid.insertAdjacentHTML("beforeend", `<div class="panel" data-room="${r.id}">
        <div class="row" style="justify-content:space-between"><h2>${esc(r.name)}</h2><a class="small" href="/timer/${r.id}" target="_blank">Screen ↗</a></div>
        <div class="clock" style="font-size:2.6rem">--:--</div><div class="muted small title"></div>
        <div class="row" style="margin-top:.5rem"><input class="grow" placeholder="mm:ss" data-dur style="width:6rem"><button data-a="set">Set</button></div>
        <div class="row" style="margin-top:.4rem"><button class="primary" data-a="toggle">Start / pause</button><button data-a="reset">Reset</button><button data-add="-60000">−1m</button><button data-add="60000">+1m</button></div>
        <div class="row" style="margin-top:.4rem"><input class="grow" placeholder="Message to stage" data-msg><button data-a="message">Show</button><button data-hide>Hide</button></div></div>`);
    }
    const parse = (s) => { const p = s.trim().split(":").map(Number); return p.some(isNaN) ? null : (p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p.length === 2 ? p[0] * 60 + p[1] : p[0] * 60) * 1000; };
    grid.querySelectorAll("[data-room]").forEach((card) => {
      const id = +card.dataset.room;
      card.querySelectorAll("[data-a]").forEach((b) => b.onclick = () => {
        const a = b.dataset.a, body = {};
        if (a === "set") { const d = parse(card.querySelector("[data-dur]").value); if (d === null) return toast("Use mm:ss, like 20:00", "bad"); body.duration_ms = d; }
        if (a === "message") { body.message = card.querySelector("[data-msg]").value; body.message_visible = true; }
        guard(() => post(`/api/timers/${id}/${a}`, body));
      });
      card.querySelector("[data-hide]").onclick = () => guard(() => post(`/api/timers/${id}/message`, { message_visible: false }));
      card.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => guard(() => post(`/api/timers/${id}/add`, { delta_ms: +b.dataset.add })));
    });
    const tick = setInterval(() => {
      if (!document.body.contains(grid)) return clearInterval(tick);
      for (const [id, s] of Object.entries(states)) {
        const card = grid.querySelector(`[data-room="${id}"]`); if (!card) continue;
        const left = timerClock(s)(), c = card.querySelector(".clock");
        c.textContent = fmtTime(left);
        c.className = "clock " + (left < 0 ? "over" : left < s.danger_ms ? "danger" : left < s.warn_ms ? "warn" : "");
        card.querySelector(".title").textContent = (s.title || "") + (s.message_visible ? `  ·  Stage message: ${s.message}` : "");
      }
    }, 200);
    view = { onEvent: (e) => { if (e.type === "timer") states[e.data.room_id] = e.data; } };
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

  // ----------------------------------------------------------- overlays --
  async function overlays(el) {
    const render = async () => {
      const targets = await api("/api/overlays/targets");
      el.innerHTML = `<h1>Overlays</h1><p class="muted">Each laptop running AT LiveOverlay shows its address and API token under Remote Control in its tray menu.</p>
        ${boot.me.role === "admin" ? `<form class="panel row" id="addT" style="margin-bottom:1rem"><input name="name" placeholder="Name, e.g. CC Lectern" required style="width:12rem">
          <input name="base_url" placeholder="http://10.100.70.88:8765" required class="grow"><input name="token" placeholder="API token" style="width:14rem">
          <select name="room_id" style="width:10rem">${roomOptions(null)}</select><button class="primary">Add</button></form>` : ""}
        <div class="grid">${targets.map((t) => `<div class="panel" data-t="${t.id}"><div class="row" style="justify-content:space-between"><h2>${esc(t.name)}</h2><span class="pill" data-st>…</span></div>
          <div class="muted small">${esc(t.base_url)} ${t.room ? "· " + esc(t.room) : ""}</div>
          <div class="row" style="margin-top:.5rem">Overlay <input data-n value="all" style="width:4rem"></div>
          <div class="row" style="margin-top:.4rem">${["show", "hide", "reload", "lock", "unlock"].map((a) => `<button class="small" data-a="${a}">${a}</button>`).join("")}</div>
          <div class="row" style="margin-top:.4rem"><input class="grow" data-url placeholder="New URL for this overlay"><button class="small" data-a="seturl">Set URL</button></div>
          <div class="row" style="margin-top:.4rem"><select data-scene class="grow"></select><button class="small" data-load>Load scene</button>${boot.me.role === "admin" ? '<button class="small danger" data-del>Remove</button>' : ""}</div></div>`).join("") || '<p class="muted">No overlay laptops added yet.</p>'}</div>`;
      const f = el.querySelector("#addT");
      if (f) f.onsubmit = (e) => { e.preventDefault(); const b = Object.fromEntries(new FormData(f)); b.room_id = b.room_id ? +b.room_id : null; guard(() => post("/api/overlays/targets", b)).then(render); };
      el.querySelectorAll("[data-t]").forEach(async (card) => {
        const id = card.dataset.t;
        card.querySelectorAll("[data-a]").forEach((b) => b.onclick = () => guard(() => post(`/api/overlays/targets/${id}/action`, {
          overlay: card.querySelector("[data-n]").value.trim() || "all", action: b.dataset.a, url: b.dataset.a === "seturl" ? card.querySelector("[data-url]").value : undefined,
        })).then(() => toast("Sent", "good")));
        card.querySelector("[data-load]").onclick = () => guard(() => post(`/api/overlays/targets/${id}/scenes/load`, { name: card.querySelector("[data-scene]").value }));
        card.querySelector("[data-del]") && (card.querySelector("[data-del]").onclick = () => confirm("Remove this overlay laptop?") && guard(() => del(`/api/overlays/targets/${id}`)).then(render));
        const st = card.querySelector("[data-st]");
        try {
          const s = await api(`/api/overlays/targets/${id}/status`);
          st.textContent = `online · ${(s.overlays || []).length} overlays`; st.className = "pill good";
          const sc = await api(`/api/overlays/targets/${id}/scenes`);
          const names = Array.isArray(sc) ? sc : sc.scenes || [];
          card.querySelector("[data-scene]").innerHTML = names.map((n) => `<option>${esc(typeof n === "string" ? n : n.name)}</option>`).join("");
        } catch (e) { st.textContent = "offline"; st.className = "pill bad"; }
      });
    };
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
        ${nodes.map((n) => `<tr data-n="${n.id}"><td><span class="dot ${n.online ? "on" : "off"}"></span></td><td><b>${esc(n.name)}</b>${n.legacy ? ' <span class="pill">old agent</span>' : ""}</td>
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
    const tabs = { general: "General", licence: "Licence", sites: "Sites & rooms", accounts: "People", links: "Links", fleet: "Node setup", keys: "API keys", import: "Import", audit: "Audit & backup" };
    el.innerHTML = `<h1>Admin</h1><div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button class="${k === sub ? "on" : ""}" onclick="location.hash='#/admin/${k}'">${v}</button>`).join("")}</div><div id="adm"></div>`;
    const a = el.querySelector("#adm");
    ({ general: admGeneral, licence: admLicence, sites: admSites, accounts: admAccounts, links: admLinks, fleet: admFleet, keys: admKeys, import: admImport, audit: admAudit }[sub] || admGeneral)(a);
  }

  async function admGeneral(a) {
    const s = await api("/api/admin/settings");
    const b = s.branding;
    a.innerHTML = `<div class="grid"><form class="panel" id="brand"><h2>Branding</h2>
      <label>Product name</label><input name="product_name" value="${esc(b.product_name)}">
      <label>Organisation</label><input name="organisation" value="${esc(b.organisation)}">
      <label>Accent colour</label><input name="accent" type="color" value="${esc(b.accent)}" style="height:2.4rem">
      <label>Logo URL</label><input name="logo_url" value="${esc(b.logo_url)}">
      <label>Support contact</label><input name="support_contact" value="${esc(b.support_contact)}">
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

  async function admLicence(a) {
    const s = await api("/api/admin/settings"), l = s.licence;
    a.innerHTML = `<div class="panel card"><h2>Licence</h2>
      <p><span class="pill ${l.valid ? "good" : "warn"}">${l.valid ? "Licensed" : "Evaluation"}</span> ${esc(l.licensee)} ${l.reason ? `<span class="muted">· ${esc(l.reason)}</span>` : ""}</p>
      <p class="muted small">Edition ${esc(l.edition)} · nodes ${l.max_nodes || "unlimited"} · sites ${l.max_sites || "unlimited"} · ${l.expires ? "expires " + new Date(l.expires * 1000).toLocaleDateString() : "no expiry"}<br>Modules: ${esc(l.modules.join(", "))}</p>
      <label>Licence key</label><textarea id="key" rows="4"></textarea><div class="row" style="margin-top:.6rem"><button class="primary" id="save">Install licence</button></div></div>`;
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
    a.innerHTML = `<div class="panel"><p class="muted small">Room links appear in that room's tech workspace and kiosk picker. Admin-board links are only shown to admins.</p>
      <table><tr><th>Label</th><th>URL</th><th>Room</th><th>Board</th><th>Kind</th><th></th></tr>${row()}${links.map(row).join("")}</table></div>`;
    a.querySelectorAll("[data-l]").forEach((tr) => {
      const body = () => ({ label: tr.querySelector("[data-label]").value, url: tr.querySelector("[data-url]").value, board: tr.querySelector("[data-board]").value, kind: tr.querySelector("[data-kind]").value,
        room_id: tr.querySelector("[data-room]").value ? +tr.querySelector("[data-room]").value : null, site_id: boot.sites[0]?.id ?? null });
      const id = tr.dataset.l;
      tr.querySelector("[data-save]").onclick = () => guard(() => id ? put(`/api/dashboard/links/${id}`, body()) : post("/api/dashboard/links", body())).then(() => id ? toast("Saved", "good") : admLinks(a));
      tr.querySelector("[data-del]") && (tr.querySelector("[data-del]").onclick = () => guard(() => del(`/api/dashboard/links/${id}`)).then(() => admLinks(a)));
    });
  }

  async function admFleet(a) {
    const sites = await api("/api/fleet/enrolment");
    const origin = location.origin, winApp = await api("/api/nodes/app");
    const dl = winApp.ready ? `<a class="btn primary" href="/api/nodes/app/${encodeURIComponent(winApp.file)}">Download AT-SUIT Node ${esc(winApp.version)} for Windows</a>` : '<span class="pill warn">No Windows app published yet</span>';
    a.innerHTML = `<div class="grid"><div class="panel"><h2>Add a tech laptop</h2>
      <p>${dl}</p>
      <ol class="small"><li>Install AT-SUIT Node on the laptop.</li><li>Enter this server's address (<code>${esc(origin)}</code>), the enrolment code below and a laptop name. This is a one-off; the laptop stays enrolled.</li>
        <li>Each day a tech signs in and picks the room they're in. The choice resets every morning.</li>
        <li>On the backup laptop only, tick <b>This laptop → Show pop-ups</b>. Pop-ups are silent and sit on top of everything.</li></ol>
      <p class="small muted">For a silent roll-out, put <code>{"server": "${esc(origin)}", "enrol_code": "CODE"}</code> in <code>C:\\ProgramData\\AT-SUIT\\node.json</code> and install with <code>/S</code>. A browser at <code>${esc(origin)}/node</code> still works too.</p>
      <p class="small muted">For captions without a browser, run the node agent: <code>python atsuit_node.py --server ${esc(origin)} --code CODE --name ATLAP3 --room CC --mic</code></p>
      ${sites.map((s) => `<p><b>${esc(s.name)}</b>: <code style="font-size:1.2rem">${esc(s.enrol_code)}</code> <button class="small" data-new="${s.id}">New code</button></p>`).join("")}
      <label>Windows app release (the .exe, .blockmap and latest.yml from the GitHub release; laptops update when the app next closes)</label><div class="row"><input type="file" id="appFiles" class="grow" multiple accept=".exe,.blockmap,.yml"><button class="small" id="upApp">Publish app</button></div>
      <label>Node agent update (laptops pull it on their next check-in)</label><div class="row"><input type="file" id="agentFile" class="grow"><button class="small" id="upAgent">Publish agent</button></div></div>
      <div class="panel"><h2>Older kiosk agents</h2><p class="small muted">Kiosks running the Device Suite agent can report here without reinstalling: point their server address at <code>${esc(origin)}</code>. Reboot, shut down and update for them go over SSH, so upload the fleet key.</p>
      <label>Fleet SSH private key</label><input type="file" id="key"><button class="small" id="upKey" style="margin-top:.4rem">Upload key</button>
      <label>Kiosk agent release (script)</label><div class="row"><input type="file" id="rel" class="grow"><input id="ver" placeholder="version, e.g. 2.0.4" style="width:9rem"></div><button class="small" id="upRel" style="margin-top:.4rem">Publish release</button></div></div>`;
    a.querySelectorAll("[data-new]").forEach((b) => b.onclick = () => confirm("Make a new code? The old one stops working for new laptops.") && guard(() => post(`/api/admin/sites/${b.dataset.new}/enrol-code`)).then(() => admFleet(a)));
    a.querySelector("#upApp").onclick = () => {
      const files = [...a.querySelector("#appFiles").files];
      if (!files.length) return;
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      guard(() => api("/api/fleet/app", { method: "POST", form: fd })).then((r) => { toast(`AT-SUIT Node ${r.version} published`, "good"); admFleet(a); });
    };
    a.querySelector("#upAgent").onclick = () => { const f = a.querySelector("#agentFile").files[0]; f && guard(() => upload("/api/fleet/agent", f)).then((r) => toast(`Agent ${r.version} published`, "good")); };
    a.querySelector("#upKey").onclick = () => { const f = a.querySelector("#key").files[0]; f && guard(() => upload("/api/fleet/ssh-key", f)).then(() => toast("Key saved", "good")); };
    a.querySelector("#upRel").onclick = () => { const f = a.querySelector("#rel").files[0], v = a.querySelector("#ver").value.trim(); f && v && guard(() => upload(`/api/fleet/client-release?version=${encodeURIComponent(v)}`, f)).then(() => toast("Published", "good")); };
  }

  async function admKeys(a) {
    const keys = await api("/api/admin/api-keys");
    a.innerHTML = `<div class="panel card"><h2>API keys</h2><p class="small muted">For Bitfocus Companion and other automation. Send the key in an <code>X-API-Key</code> header, e.g. <code>POST /api/timers/&lt;room&gt;/toggle</code>. Full API: <a href="/api/docs" target="_blank">/api/docs</a>.</p>
      <table>${keys.map((k) => `<tr><td>${esc(k.name)}</td><td><code>${esc(k.prefix)}…</code></td><td>${when(k.created_at)}</td><td><button class="small danger" data-del="${k.id}">Revoke</button></td></tr>`).join("")}</table>
      <form class="row" id="nk" style="margin-top:.6rem"><input name="name" class="grow" placeholder="Key name, e.g. Companion" required><button class="primary">Create</button></form><p id="newkey"></p></div>`;
    a.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => confirm("Revoke this key?") && guard(() => del(`/api/admin/api-keys/${b.dataset.del}`)).then(() => admKeys(a)));
    const f = a.querySelector("#nk");
    f.onsubmit = async (e) => { e.preventDefault(); const r = await guard(() => post("/api/admin/api-keys", { name: f.name.value })); await admKeys(a); a.querySelector("#newkey").innerHTML = `Copy this key now, it won't be shown again:<br><code>${esc(r.key)}</code>`; };
  }

  async function admImport(a) {
    const items = [
      ["roomcomms", "RoomComms chat", "A zip of the RoomComms data folder (roomcomms.db and .encryption_key). Accounts keep their passwords."],
      ["rooms-txt", "Device Suite rooms.txt", "Room kiosk links (Ontime views, Companion buttons). Lines that aren't ROOM|URL|LABEL; are skipped."],
      ["device-state", "Device Suite state.json", "Known kiosks with their addresses."],
      ["homarr", "Homarr links", "The TSV export of the Homarr USER CONTROL board."],
    ];
    a.innerHTML = `<div class="grid">${items.map(([k, t, d]) => `<div class="panel" data-k="${k}"><h2>${t}</h2><p class="small muted">${d}</p><input type="file"><button class="small primary" style="margin-top:.5rem">Import</button><pre class="small muted" style="white-space:pre-wrap"></pre></div>`).join("")}</div>`;
    a.querySelectorAll("[data-k]").forEach((p) => p.querySelector("button").onclick = async () => {
      const f = p.querySelector("input").files[0]; if (!f) return;
      const r = await guard(() => upload(`/api/admin/import/${p.dataset.k}`, f));
      p.querySelector("pre").textContent = JSON.stringify(r, null, 2);
      boot = await api("/api/bootstrap");
    });
  }

  async function admAudit(a) {
    const [log, diag] = await Promise.all([api("/api/admin/audit"), api("/api/admin/diagnostics")]);
    a.innerHTML = `<div class="grid"><div class="panel"><h2>Backup</h2><p class="small muted">Database, uploads and the encryption key in one zip. Keep it somewhere safe; it can read every message.</p><a class="btn primary" href="/api/admin/backup">Download backup</a></div>
      <div class="panel"><h2>Diagnostics</h2><pre class="small" style="white-space:pre-wrap">${esc(JSON.stringify(diag, null, 2))}</pre></div></div>
      <div class="panel" style="margin-top:1rem"><h2>Audit log</h2><table><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr>${log.map((l) => `<tr><td class="small">${when(l.at)}</td><td>${esc(l.actor)}</td><td>${esc(l.action)}</td><td class="small muted">${esc(l.detail)}</td></tr>`).join("")}</table></div>`;
  }
})();
