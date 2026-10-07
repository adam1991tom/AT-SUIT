// Chat component shared by the console and the node workspace.
// mount(el, {me, prefer: channelId|null, compact}) returns {onEvent(evt)}.
const Chat = (() => {
  const { esc, api, post, upload, guard, when, h } = AT;

  function mount(el, opts) {
    const me = opts.me;
    let channels = [], current = null, unread = {}, people = [];
    el.innerHTML = `
      <div class="${opts.compact ? "" : "chat"}" style="${opts.compact ? "display:flex;flex-direction:column;height:100%;gap:.5rem" : ""}">
        <div class="chans ${opts.compact ? "tabs" : "panel"}"></div>
        <div class="msgs ${opts.compact ? "grow" : "panel"}" style="${opts.compact ? "min-height:300px" : ""}">
          <div class="row" style="justify-content:space-between;margin-bottom:.4rem"><h2 class="title" style="margin:0"></h2><span class="muted small typing"></span></div>
          <div class="list"></div>
          <form class="compose">
            <select name="priority" style="width:auto"><option value="normal">Normal</option><option value="important">Important</option><option value="urgent">Urgent</option></select>
            <input name="body" class="grow" placeholder="Message" autocomplete="off">
            <label class="btn" style="margin:0" title="Attach a file">📎<input type="file" name="file" class="hidden"></label>
            <button class="primary">Send</button>
          </form>
        </div>
      </div>`;
    const chansEl = el.querySelector(".chans"), listEl = el.querySelector(".list"), titleEl = el.querySelector(".title"), form = el.querySelector("form");

    function chanLabel(c) { return c.kind === "site" ? `# ${c.name}` : c.kind === "room" ? c.name : `@ ${c.name}`; }

    function renderChans() {
      const items = channels.map((c) => `<${opts.compact ? "button type=button" : "a href=#"} data-id="${c.id}" class="${c.id === current?.id ? (opts.compact ? "on" : "active") : ""}">${esc(chanLabel(c))}${unread[c.id] ? ` <span class="unread">${unread[c.id]}</span>` : ""}</${opts.compact ? "button" : "a"}>`).join("");
      chansEl.innerHTML = (opts.compact ? "" : "<h3>Channels</h3>") + items +
        `<${opts.compact ? "button type=button" : "a href=#"} data-dm="1" class="muted">+ Direct message</${opts.compact ? "button" : "a"}>`;
      chansEl.querySelectorAll("[data-id]").forEach((a) => a.onclick = (e) => { e.preventDefault(); open(+a.dataset.id); });
      chansEl.querySelector("[data-dm]").onclick = (e) => { e.preventDefault(); pickDm(); };
      const total = Object.values(unread).reduce((a, b) => a + b, 0);
      opts.onUnread && opts.onUnread(total);
    }

    function msgHtml(m) {
      const atts = (m.attachments || []).map((a) => `<a class="att" href="/api/comms/attachments/${a.id}" target="_blank">📄 ${esc(a.original_name)}</a>`).join(" ");
      const mine = me.kind === "account" && m.sender_id === me.id;
      const del = (mine || me.role === "admin") && !m.deleted ? `<button class="small" data-del="${m.id}" style="float:right">Delete</button>` : "";
      return `<div class="msg ${esc(m.priority)}" data-mid="${m.id}">${del}<div class="who">${esc(m.sender_name)} <span class="muted small">${when(m.created_at)}</span></div>` +
        `<div>${m.deleted ? '<i class="muted">Deleted</i>' : esc(m.body).replace(/\n/g, "<br>")}</div>${atts}</div>`;
    }

    function wireDeletes(scope) {
      scope.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => guard(() => AT.del(`/api/comms/messages/${b.dataset.del}`)));
    }

    async function open(id) {
      current = channels.find((c) => c.id === id) || channels[0];
      if (!current) return;
      unread[current.id] = 0;
      renderChans();
      titleEl.textContent = chanLabel(current);
      const msgs = await api(`/api/comms/channels/${current.id}/messages`);
      listEl.innerHTML = msgs.map(msgHtml).join("") || '<p class="muted">No messages yet.</p>';
      wireDeletes(listEl);
      listEl.scrollTop = listEl.scrollHeight;
      if (msgs.length) post(`/api/comms/channels/${current.id}/read`, { up_to: msgs[msgs.length - 1].id }).catch(() => {});
    }

    async function pickDm() {
      people = await api("/api/comms/people");
      if (!people.length) { AT.toast("There's nobody else to message yet."); return; }
      // An in-page picker, not prompt(): native dialogs can play a system sound.
      const pick = document.createElement("div");
      pick.className = "row dm-pick";
      pick.innerHTML = `<select class="grow"><option value="">Message who?</option>${people.map((x) => `<option value="${x.id}">${esc(x.display_name)}</option>`).join("")}</select><button type="button" class="primary">Open</button><button type="button">Cancel</button>`;
      el.querySelector(".dm-pick")?.remove();
      chansEl.after(pick);
      const choice = await new Promise((done) => {
        const [sel, ok, cancel] = pick.children;
        ok.onclick = () => done(sel.value);
        cancel.onclick = () => done("");
      });
      pick.remove();
      const p = choice && people.find((x) => String(x.id) === choice);
      if (!p) return;
      const { id } = await guard(() => post("/api/comms/dm", { account_id: p.id }));
      await load(id);
    }

    form.onsubmit = async (e) => {
      e.preventDefault();
      const body = form.body.value.trim(), file = form.file.files[0];
      if (!body && !file) return;
      const m = await guard(() => post(`/api/comms/channels/${current.id}/messages`, { body, priority: form.priority.value }));
      if (file) await guard(() => upload(`/api/comms/messages/${m.id}/attachments`, file));
      form.body.value = ""; form.file.value = ""; form.priority.value = "normal";
    };

    async function load(preferId) {
      channels = await api("/api/comms/channels");
      renderChans();
      await open(preferId ?? opts.prefer ?? current?.id ?? channels[0]?.id);
    }

    function onEvent(evt) {
      if (!evt.type || !evt.type.startsWith("message.")) return;
      const m = evt.data, cid = m.channel_id;
      if (!channels.find((c) => c.id === cid)) { load(current?.id); return; }
      if (current && cid === current.id) {
        const existing = listEl.querySelector(`[data-mid="${m.id}"]`);
        if (evt.type === "message.deleted") {
          if (existing) existing.outerHTML = msgHtml({ ...m, deleted: true, sender_name: existing.querySelector(".who").firstChild.textContent, attachments: [] });
        } else if (existing) existing.outerHTML = msgHtml(m);
        else {
          listEl.querySelector("p.muted")?.remove();
          listEl.insertAdjacentHTML("beforeend", msgHtml(m));
          listEl.scrollTop = listEl.scrollHeight;
          post(`/api/comms/channels/${cid}/read`, { up_to: m.id }).catch(() => {});
        }
        wireDeletes(listEl);
      } else if (evt.type === "message.new") {
        unread[cid] = (unread[cid] || 0) + 1;
        renderChans();
      }
      if (evt.type === "message.new" && m.priority === "urgent" && !(me.kind === "account" && m.sender_id === me.id)) {
        AT.toast(`Urgent from ${m.sender_name}: ${m.body}`, "bad");
      }
    }

    load();
    return { onEvent, reload: () => load(current?.id), open };
  }

  return { mount };
})();
