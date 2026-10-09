// Presenters (from AT-Presenter): events, running order, presenter links,
// file review, schedule import and settings. Mounted by app.js at #/presenter.
const PresenterPage = (() => {
  const { esc, api, post, put, del, guard, toast, when } = AT;
  const STATUS = { pending: ["Waiting for review", "warn"], approved: ["Approved", "good"], rejected: ["Rejected", "bad"] };
  const KINDS = ["presentation", "video", "picture", "audio", "other"];
  const fmtSize = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
  const hhmm = (iso) => (iso && iso.includes("T") ? iso.slice(11, 16) : iso || "");
  const day = (iso) => (iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : "");
  const dayName = (d) => (d ? new Date(d + "T12:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }) : "No date yet");
  const link = (token) => `${location.origin}/present/${token}`;
  const sendFile = (path, file) => { const f = new FormData(); f.append("file", file); return api(path, { method: "POST", form: f }); };
  const pick = (accept = "") => new Promise((res) => { const i = document.createElement("input"); i.type = "file"; i.accept = accept; i.onchange = () => res(i.files[0]); i.click(); });

  function mount(el, sub, boot) {
    const isAdmin = boot.me.role === "admin", canEvents = isAdmin || boot.me.role === "manager";
    let events = [], eventId = null, ev = null, tab = sub || "order", open = new Set();
    try { eventId = +localStorage.getItem("atsuit_pr_event") || null; } catch (_) {}

    async function load() {
      events = await api("/api/presenter/events");
      if (!events.some((e) => e.id === eventId)) eventId = events[0]?.id || null;
      ev = eventId ? await api(`/api/presenter/events/${eventId}`) : null;
      render();
    }

    function render() {
      const tabs = { order: "Running order", review: "File review", import: "Import", ...(isAdmin ? { settings: "Settings" } : {}) };
      const pending = events.find((e) => e.id === eventId)?.pending || 0;
      el.innerHTML = `<div class="row" style="justify-content:space-between"><h1>Presenters</h1>
          <div class="row">${events.length ? `<select id="prEvent" style="width:auto">${events.map((e) => `<option value="${e.id}" ${e.id === eventId ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select>` : ""}
          ${canEvents ? `${ev ? '<button id="prEdit">Edit event</button>' : ""}<button id="prNew" class="primary">New event</button>` : ""}
          ${ev ? `<a class="btn" href="/api/presenter/events/${ev.id}/schedule.csv">Schedule CSV</a>` : ""}</div></div>
        <div id="prForm"></div>
        ${ev || tab === "settings" ? `<div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button class="${k === tab ? "on" : ""}" data-tab="${k}">${v}${k === "review" && pending ? ` <span class="unread">${pending}</span>` : ""}</button>`).join("")}</div><div id="prBody"></div>`
          : `<div class="panel"><p>No events yet.</p><p class="muted small">${canEvents ? "Make one with New event: give it a name and dates, then add its running order here or import it from a spreadsheet." : "An admin sets events up."}</p></div>`}`;
      el.querySelector("#prEvent")?.addEventListener("change", (e) => { eventId = +e.target.value; try { localStorage.setItem("atsuit_pr_event", eventId); } catch (_) {} open.clear(); load(); });
      el.querySelector("#prNew")?.addEventListener("click", () => eventForm(null));
      el.querySelector("#prEdit")?.addEventListener("click", () => eventForm(ev));
      el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; history.replaceState(null, "", `#/presenter/${tab}`); render(); }));
      const body = el.querySelector("#prBody");
      if (!body) return;
      ({ order: renderOrder, review: renderReview, import: renderImport, settings: renderSettings }[tab] || renderOrder)(body);
    }

    // ------------------------------------------------------------ event --
    function eventForm(e) {
      const box = el.querySelector("#prForm");
      box.innerHTML = `<form class="panel" style="margin-bottom:1rem"><h2>${e ? "Edit event" : "New event"}</h2>
        <div class="row"><div class="grow"><label>Name</label><input name="name" required value="${esc(e?.name || "")}"></div>
          <div class="grow"><label>Client</label><input name="client" value="${esc(e?.client || "")}"></div>
          <div><label>Colour</label><input name="colour" type="color" value="${esc(e?.colour || "#8b5cf6")}" style="width:3.5rem;height:2.4rem;padding:0"></div></div>
        <div class="row"><div><label>First day</label><input name="starts_on" type="date" value="${esc(e?.starts_on || "")}"></div>
          <div><label>Last day</label><input name="ends_on" type="date" value="${esc(e?.ends_on || "")}"></div>
          <div><label>Status</label><select name="status">${["planning", "live", "finished"].map((s) => `<option ${e?.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
          ${boot.sites.length > 1 ? `<div><label>Site</label><select name="site_id">${boot.sites.map((s) => `<option value="${s.id}" ${e?.site_id === s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></div>` : ""}</div>
        ${e ? '<label><input type="checkbox" name="archived" style="width:auto"> Archive (hides it and switches off its presenter links)</label>' : ""}
        <div class="row" style="margin-top:.8rem"><button class="primary">${e ? "Save" : "Create event"}</button><button type="button" data-x>Cancel</button>
          ${e ? '<span class="grow"></span><button type="button" class="danger" data-del>Delete event and its files</button>' : ""}</div></form>`;
      const f = box.querySelector("form");
      f.querySelector("[data-x]").onclick = () => (box.innerHTML = "");
      f.querySelector("[data-del]")?.addEventListener("click", () => confirm(`Delete ${e.name}, its running order, presenters and every uploaded file?`) &&
        guard(() => del(`/api/presenter/events/${e.id}`)).then(() => { eventId = null; load(); }));
      f.onsubmit = async (x) => {
        x.preventDefault();
        const b = { name: f.name.value, client: f.client.value, colour: f.colour.value, starts_on: f.starts_on.value, ends_on: f.ends_on.value,
          status: f.status.value, site_id: f.site_id ? +f.site_id.value : null, archived: !!f.archived?.checked };
        const r = await guard(() => (e ? put(`/api/presenter/events/${e.id}`, b) : post("/api/presenter/events", b)));
        if (!e) { eventId = r.id; try { localStorage.setItem("atsuit_pr_event", eventId); } catch (_) {} }
        load();
      };
    }

    // ---------------------------------------------------- running order --
    function readiness(s) {
      const r = s.ready, bits = [];
      if (!r.presenters) bits.push('<span class="pill">no presenter</span>');
      else {
        bits.push(`<span class="pill ${r.checked_in === r.presenters ? "good" : ""}">${r.checked_in}/${r.presenters} here</span>`);
        if (r.approved === r.presenters) bits.push('<span class="pill good">files approved</span>');
        else if (r.rejected) bits.push(`<span class="pill bad">${r.rejected} rejected</span>`);
        else if (r.pending) bits.push(`<span class="pill warn">${r.pending} to review</span>`);
        else if (r.uploaded < r.presenters) bits.push(`<span class="pill">${r.presenters - r.uploaded} file${r.presenters - r.uploaded === 1 ? "" : "s"} missing</span>`);
      }
      if (r.show_files) bits.push(`<span class="pill">${r.show_files} show file${r.show_files === 1 ? "" : "s"}</span>`);
      return bits.join(" ");
    }

    function sessionForm(box, s) {
      box.innerHTML = `<form class="panel" style="margin:.6rem 0"><div class="row">
          <div class="grow"><label>Session</label><input name="title" required value="${esc(s?.title || "")}"></div>
          <div><label>Room</label><select name="room_id"><option value="">No room yet</option>${ev.rooms.map((r) => `<option value="${r.id}" ${s?.room_id === r.id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></div></div>
        <div class="row"><div><label>Starts</label><input name="starts_at" type="datetime-local" value="${esc(s?.starts_at || (ev.starts_on ? ev.starts_on + "T09:00" : ""))}"></div>
          <div><label>Ends</label><input name="ends_at" type="datetime-local" value="${esc(s?.ends_at || "")}"></div>
          <div class="grow"><label>Notes for the room</label><input name="notes" value="${esc(s?.notes || "")}"></div></div>
        <div class="row" style="margin-top:.7rem"><button class="primary">${s ? "Save" : "Add session"}</button><button type="button" data-x>Cancel</button></div></form>`;
      const f = box.querySelector("form");
      f.querySelector("[data-x]").onclick = () => (box.innerHTML = "");
      f.onsubmit = (x) => {
        x.preventDefault();
        const b = { event_id: ev.id, title: f.title.value, room_id: f.room_id.value ? +f.room_id.value : null, starts_at: f.starts_at.value, ends_at: f.ends_at.value, notes: f.notes.value };
        guard(() => (s ? put(`/api/presenter/sessions/${s.id}`, b) : post("/api/presenter/sessions", b))).then((r) => { open.add(r.id); load(); });
      };
      f.title.focus();
    }

    function presenterForm(box, sessionId, p) {
      box.innerHTML = `<form class="row" style="margin:.5rem 0"><input name="full_name" class="grow" required placeholder="Presenter's name" value="${esc(p?.full_name || "")}">
        <input name="email" type="email" placeholder="Email (optional)" style="width:14rem" value="${esc(p?.email || "")}"><input name="phone" placeholder="Phone (optional)" style="width:10rem" value="${esc(p?.phone || "")}">
        <button class="primary small">${p ? "Save" : "Add"}</button><button type="button" class="small" data-x>Cancel</button></form>`;
      const f = box.querySelector("form");
      f.querySelector("[data-x]").onclick = () => (box.innerHTML = "");
      f.onsubmit = (x) => {
        x.preventDefault();
        const b = { event_id: ev.id, session_id: p ? p.session_id : sessionId, full_name: f.full_name.value, email: f.email.value, phone: f.phone.value };
        guard(() => (p ? put(`/api/presenter/presenters/${p.id}`, b) : post("/api/presenter/presenters", b))).then(load);
      };
      f.full_name.focus();
    }

    function presenterHtml(p) {
      const f = p.files[0];
      return `<div class="pr" data-p="${p.id}"><div class="row">
          <b>${esc(p.full_name)}</b>${p.checked_in_at ? `<span class="pill good">here since ${when(p.checked_in_at)}</span>` : '<span class="pill">not checked in</span>'}
          ${f ? `<span class="pill ${STATUS[f.review_status][1]}">${STATUS[f.review_status][0]}</span>` : '<span class="pill">no file yet</span>'}
          <span class="muted small">${esc([p.email, p.phone].filter(Boolean).join(" · "))}</span></div>
        <div class="row small" style="margin-top:.3rem"><input readonly value="${esc(link(p.token))}" class="grow" style="font-size:.8rem;padding:.25rem .5rem" data-link>
          <button class="small" data-copy>Copy link</button><button class="small" data-upload>Upload for them</button>
          <button class="small" data-checkin>${p.checked_in_at ? "Undo check-in" : "Check in"}</button><button class="small" data-edit>Edit</button>
          <button class="small" data-newlink title="The old link stops working">New link</button><button class="small danger" data-del>Remove</button></div>
        ${p.files.length ? `<table class="small" style="margin-top:.3rem">${p.files.map((x) => fileRow(x)).join("")}</table>` : ""}<div data-pform></div></div>`;
    }

    const fileRow = (x) => `<tr data-f="${x.id}"><td>v${x.version}</td><td><a href="/api/presenter/files/${x.id}">${esc(x.original_name)}</a> <span class="muted">${fmtSize(x.size)} · ${when(x.uploaded_at)}${x.uploaded_by && !x.uploaded_by.startsWith("presenter:") ? " · by " + esc(x.uploaded_by) : ""}</span></td>
      <td><span class="pill ${STATUS[x.review_status][1]}">${STATUS[x.review_status][0]}</span>${x.review_note ? ` <span class="muted">${esc(x.review_note)}</span>` : ""}</td>
      <td style="white-space:nowrap"><button class="small" data-ok>Approve</button><button class="small" data-no>Reject</button></td></tr>`;

    function wireFiles(root) {
      root.querySelectorAll("[data-f]").forEach((row) => {
        const id = row.dataset.f;
        row.querySelector("[data-ok]").onclick = () => guard(() => put(`/api/presenter/files/${id}/review`, { status: "approved" })).then(load);
        row.querySelector("[data-no]").onclick = () => {
          const note = prompt("What should the presenter change? They see this on their page.", "");
          if (note !== null) guard(() => put(`/api/presenter/files/${id}/review`, { status: "rejected", note })).then(load);
        };
      });
    }

    function renderOrder(body) {
      const groups = {};
      for (const s of ev.sessions) (groups[day(s.starts_at)] ||= []).push(s);
      body.innerHTML = `<div class="row" style="margin:.4rem 0 .8rem"><button class="primary" id="addS">Add session</button>
          <span class="muted small">${ev.sessions.length} session${ev.sessions.length === 1 ? "" : "s"}. Each presenter gets their own link to upload slides and check in; no account needed.</span></div>
        <div id="sForm"></div>
        ${Object.keys(groups).sort((a, b) => (a || "9") < (b || "9") ? -1 : 1).map((d) => `<h2 style="margin-top:1rem">${esc(dayName(d))}</h2>${groups[d].map((s) => `
          <div class="panel sess" data-s="${s.id}" style="margin-bottom:.5rem;border-left:4px solid ${esc(ev.colour)}">
            <div class="row" style="cursor:pointer" data-toggle><b style="min-width:6.5rem">${esc(hhmm(s.starts_at))}${s.ends_at ? "–" + esc(hhmm(s.ends_at)) : ""}</b>
              <span class="pill">${esc(s.room_name || "no room")}</span><b class="grow">${esc(s.title)}</b>${readiness(s)}<span class="muted">${open.has(s.id) ? "▾" : "▸"}</span></div>
            ${open.has(s.id) ? `<div style="margin-top:.6rem">${s.notes ? `<p class="small muted">${esc(s.notes)}</p>` : ""}
              ${s.presenters.map(presenterHtml).join("") || '<p class="muted small">No presenter yet.</p>'}
              <div data-newp></div>
              <h3 style="margin-top:.8rem">Show files</h3><p class="small muted" style="margin:0 0 .4rem">The show's own files for this session, in running order (walk-in video, stings, the final deck). They sync to the room's laptop.</p>
              <table class="small">${s.show_files.map((f, i) => `<tr data-sf="${f.id}"><td>${i + 1}</td><td><span class="pill">${esc(f.kind)}</span></td><td><a href="/api/presenter/show-files/${f.id}">${esc(f.label)}</a> <span class="muted">${esc(f.label !== f.original_name ? f.original_name : "")} ${fmtSize(f.size)}</span></td>
                <td style="white-space:nowrap"><button class="small" data-up ${i ? "" : "disabled"}>↑</button><button class="small" data-down ${i < s.show_files.length - 1 ? "" : "disabled"}>↓</button><button class="small" data-ren>Rename</button><button class="small danger" data-sdel>✕</button></td></tr>`).join("")}</table>
              <div class="row" style="margin-top:.6rem"><button class="small primary" data-addp>Add presenter</button>
                <select data-kind style="width:auto" class="small">${KINDS.map((k) => `<option>${k}</option>`).join("")}</select><button class="small" data-addsf>Add show file</button>
                <span class="grow"></span><button class="small" data-sedit>Edit session</button><button class="small danger" data-sdelete>Delete session</button></div></div>` : ""}
          </div>`).join("")}`).join("")}
        ${ev.unassigned.length ? `<h2 style="margin-top:1rem">Presenters without a session</h2><div class="panel">${ev.unassigned.map(presenterHtml).join("")}</div>` : ""}`;
      body.querySelector("#addS").onclick = () => sessionForm(body.querySelector("#sForm"), null);
      body.querySelectorAll("[data-s]").forEach((card) => {
        const s = ev.sessions.find((x) => x.id === +card.dataset.s);
        card.querySelector("[data-toggle]").onclick = () => { open.has(s.id) ? open.delete(s.id) : open.add(s.id); render(); };
        if (!open.has(s.id)) return;
        card.querySelector("[data-addp]").onclick = () => presenterForm(card.querySelector("[data-newp]"), s.id, null);
        card.querySelector("[data-sedit]").onclick = () => sessionForm(card.querySelector("[data-newp]"), s);
        card.querySelector("[data-sdelete]").onclick = () => confirm(`Delete "${s.title}"? Its presenters stay, without a session.`) && guard(() => del(`/api/presenter/sessions/${s.id}`)).then(load);
        card.querySelector("[data-addsf]").onclick = async () => {
          const file = await pick(); if (!file) return;
          toast(`Uploading ${file.name}…`);
          guard(() => sendFile(`/api/presenter/sessions/${s.id}/show-files?kind=${card.querySelector("[data-kind]").value}`, file)).then(() => { toast("Added", "good"); load(); });
        };
        card.querySelectorAll("[data-sf]").forEach((row) => {
          const id = +row.dataset.sf, ids = s.show_files.map((f) => f.id), i = ids.indexOf(id), f = s.show_files[i];
          const move = (d) => { ids.splice(i + d, 0, ids.splice(i, 1)[0]); guard(() => post(`/api/presenter/sessions/${s.id}/show-files/order`, { ids })).then(load); };
          row.querySelector("[data-up]").onclick = () => move(-1);
          row.querySelector("[data-down]").onclick = () => move(1);
          row.querySelector("[data-ren]").onclick = () => { const label = prompt("Name", f.label); if (label) guard(() => put(`/api/presenter/show-files/${id}`, { label, kind: f.kind })).then(load); };
          row.querySelector("[data-sdel]").onclick = () => confirm(`Remove ${f.label}?`) && guard(() => del(`/api/presenter/show-files/${id}`)).then(load);
        });
      });
      body.querySelectorAll("[data-p]").forEach((card) => {
        const p = [...ev.sessions.flatMap((s) => s.presenters), ...ev.unassigned].find((x) => x.id === +card.dataset.p);
        card.querySelector("[data-copy]").onclick = async () => {
          const input = card.querySelector("[data-link]");
          try { await navigator.clipboard.writeText(input.value); toast("Link copied", "good"); } catch (_) { input.select(); document.execCommand("copy"); toast("Link copied", "good"); }
        };
        card.querySelector("[data-upload]").onclick = async () => {
          const file = await pick(); if (!file) return;
          toast(`Uploading ${file.name}…`);
          guard(() => sendFile(`/api/presenter/presenters/${p.id}/files`, file)).then(() => { toast("Uploaded", "good"); load(); });
        };
        card.querySelector("[data-checkin]").onclick = () => guard(() => post(`/api/presenter/presenters/${p.id}/checkin${p.checked_in_at ? "?undo=true" : ""}`)).then(load);
        card.querySelector("[data-edit]").onclick = () => presenterForm(card.querySelector("[data-pform]"), p.session_id, p);
        card.querySelector("[data-newlink]").onclick = () => confirm(`Make a new link for ${p.full_name}? The old one stops working.`) && guard(() => post(`/api/presenter/presenters/${p.id}/new-link`)).then(load);
        card.querySelector("[data-del]").onclick = () => confirm(`Remove ${p.full_name} and their files?`) && guard(() => del(`/api/presenter/presenters/${p.id}`)).then(load);
        wireFiles(card);
      });
    }

    // ------------------------------------------------------------ review --
    async function renderReview(body) {
      const rows = await api(`/api/presenter/events/${ev.id}/review`);
      const only = body.dataset.all !== "1";
      const shown = only ? rows.filter((r) => r.is_latest) : rows;
      body.innerHTML = `<div class="row" style="margin:.4rem 0 .8rem"><label style="margin:0"><input type="checkbox" id="allV" ${only ? "" : "checked"} style="width:auto"> Show older versions too</label>
          <span class="muted small">Approved files sync to the room's laptop. A rejected file's note shows on the presenter's page.</span></div>
        <div class="panel"><table><tr><th>Uploaded</th><th>Room</th><th>Session</th><th>Presenter</th><th>File</th><th>Status</th><th></th></tr>
        ${shown.map((x) => `<tr data-f="${x.id}" style="${x.is_latest ? "" : "opacity:.55"}"><td class="small">${when(x.uploaded_at)}</td><td>${esc(x.room_name || "")}</td>
          <td>${esc(x.session_title || "")} <span class="muted small">${esc(hhmm(x.starts_at || ""))}</span></td><td>${esc(x.presenter_name)}</td>
          <td><a href="/api/presenter/files/${x.id}">${esc(x.original_name)}</a> <span class="muted small">v${x.version} · ${fmtSize(x.size)}</span></td>
          <td><span class="pill ${STATUS[x.review_status][1]}">${STATUS[x.review_status][0]}</span>${x.review_note ? `<div class="muted small">${esc(x.review_note)}</div>` : ""}</td>
          <td style="white-space:nowrap"><button class="small" data-ok>Approve</button><button class="small" data-no>Reject</button></td></tr>`).join("") || '<tr><td colspan="7" class="muted">Nothing uploaded yet.</td></tr>'}</table></div>`;
      body.querySelector("#allV").onchange = (e) => { body.dataset.all = e.target.checked ? "1" : ""; renderReview(body); };
      wireFiles(body);
    }

    // ------------------------------------------------------------ import --
    function renderImport(body) {
      body.innerHTML = `<div class="panel"><h2>Bring in the running order</h2>
        <p class="small muted">A spreadsheet or CSV with column headings is read directly: Room, Date or Day, Start and End (or Time "09:00 - 09:45"), Session or Title, Speaker or Presenter, Email, Phone. PDFs and Word files need the schedule AI (Settings). Nothing is saved until you check the rows and press Add.</p>
        <div class="row"><input type="file" id="impFile" accept=".xlsx,.xlsm,.csv,.txt,.tsv,.pdf,.docx" class="grow"><button class="primary" id="impGo">Read it</button></div></div><div id="impRows"></div>`;
      body.querySelector("#impGo").onclick = async () => {
        const file = body.querySelector("#impFile").files[0];
        if (!file) return toast("Choose a file first", "bad");
        toast("Reading…");
        const r = await guard(() => sendFile(`/api/presenter/events/${ev.id}/import`, file));
        reviewRows(body.querySelector("#impRows"), r);
      };
    }

    function reviewRows(box, imp) {
      const cols = [["room_name", "Room", "7rem"], ["starts_at", "Starts", "10rem"], ["ends_at", "Ends", "10rem"], ["title", "Session", ""], ["presenter_name", "Presenter(s)", "12rem"], ["presenter_email", "Email", "11rem"]];
      box.innerHTML = `<div class="panel" style="margin-top:1rem"><h2>${imp.rows.length} sessions found</h2>
        <p class="small muted">Read ${imp.method === "ai" ? "by the schedule AI: check every row" : "from the table"}. Rooms in amber aren't set up at this site; those sessions are added without a room. Separate several presenters with ";".</p>
        <table class="small"><tr>${cols.map((c) => `<th>${c[1]}</th>`).join("")}<th></th></tr>
        ${imp.rows.map((r, i) => `<tr data-i="${i}">${cols.map(([k, , w]) => `<td><input data-k="${k}" value="${esc(r[k])}" style="${w ? `width:${w};` : ""}${k === "room_name" && !r.room_known ? "border-color:var(--warn)" : ""}"></td>`).join("")}<td><button class="small" data-drop>✕</button></td></tr>`).join("")}</table>
        <div class="row" style="margin-top:.8rem"><button class="primary" id="impAdd">Add these sessions</button><button id="impNo">Discard</button></div></div>`;
      box.querySelectorAll("[data-drop]").forEach((b) => (b.onclick = () => b.closest("tr").remove()));
      box.querySelector("#impNo").onclick = () => guard(() => post(`/api/presenter/imports/${imp.id}/discard`)).then(() => (box.innerHTML = ""));
      box.querySelector("#impAdd").onclick = async () => {
        const rows = [...box.querySelectorAll("[data-i]")].map((tr) => Object.fromEntries([...tr.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value])));
        const r = await guard(() => post(`/api/presenter/imports/${imp.id}/commit`, { rows }));
        toast(`${r.sessions} sessions added${r.unknown_rooms.length ? `; no room set up for ${r.unknown_rooms.join(", ")}` : ""}`, "good");
        tab = "order"; load();
      };
    }

    // ---------------------------------------------------------- settings --
    async function renderSettings(body) {
      const s = await api("/api/presenter/settings");
      body.innerHTML = `<div class="grid"><form class="panel" id="prefs"><h2>Uploads and the presenter page</h2>
          <label>Largest file a presenter can upload (MB)</label><input name="upload_limit_mb" type="number" min="1" value="${s.upload_limit_mb}">
          <label>Note on every presenter's page (optional)</label><textarea name="portal_note" placeholder="e.g. Please upload by 6pm the day before. 16:9 slides.">${esc(s.portal_note)}</textarea>
          <p class="small muted">Files are stored in <code>${esc(s.storage)}</code>. Set ATSUIT_PRESENTER_FILES to a NAS mount to keep them there.</p>
          <h2 style="margin-top:1rem">Schedule AI (optional)</h2>
          <p class="small muted">Reads PDF and Word running orders with a local model through <a href="https://ollama.com" target="_blank" rel="noopener">Ollama</a>; nothing leaves your network. Spreadsheets don't need it.</p>
          <label>Ollama address</label><input name="ai_url" placeholder="http://10.100.70.101:11434" value="${esc(s.ai_url)}">
          <label>Model</label><input name="ai_model" value="${esc(s.ai_model)}">
          <div class="row" style="margin-top:.8rem"><button class="primary">Save</button></div></form>
        <div class="panel"><h2>Room sync</h2>
          <p class="small">The presentation laptop in each room keeps a folder with every session's approved presenter file and show files. AT-SUIT Node laptops sync the room they're in. For any other laptop, run <a href="/api/presenter/room-sync/atsuit_room_sync.py">the room sync tool</a> (Python, nothing to install) with the room's code:</p>
          <code class="small">python atsuit_room_sync.py --server ${esc(location.origin)} --code ROOM-CODE</code>
          <table style="margin-top:.6rem">${s.rooms.map((r) => `<tr><td>${esc(r.name)} <span class="muted small">${esc(r.site)}</span></td><td><code>${esc(r.sync_code || "none")}</code></td><td><button class="small" data-code="${r.id}">${r.sync_code ? "New code" : "Make code"}</button></td></tr>`).join("")}</table></div></div>`;
      const f = body.querySelector("#prefs");
      f.onsubmit = (e) => { e.preventDefault(); guard(() => put("/api/presenter/settings", { upload_limit_mb: +f.upload_limit_mb.value, portal_note: f.portal_note.value, ai_url: f.ai_url.value.trim(), ai_model: f.ai_model.value.trim() })).then(() => toast("Saved", "good")); };
      body.querySelectorAll("[data-code]").forEach((b) => (b.onclick = () => guard(() => post(`/api/presenter/rooms/${b.dataset.code}/sync-code`)).then(() => renderSettings(body))));
    }

    let pendingLoad = null;
    load();
    return {
      onEvent(evt) {
        if (evt.type !== "presenter.changed" || evt.topic !== "presenter") return;
        if (el.querySelector("form:focus-within, #impRows tr")) return; // don't wipe what someone is typing
        clearTimeout(pendingLoad);
        pendingLoad = setTimeout(load, 300);
      },
    };
  }

  return { mount };
})();
