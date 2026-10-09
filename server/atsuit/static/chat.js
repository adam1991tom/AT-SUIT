// Chat component shared by the console and the node workspace.
// mount(el, {me, prefer: channelId|null, compact}) returns {onEvent(evt)}.
const Chat = (() => {
  const { esc, api, post, upload, guard, when, h } = AT;
  // A short, useful set: faces, hands, and the signs a crew uses on show day.
  const EMOJI = ["👍", "👎", "👌", "👏", "🙏", "🙌", "👋", "✌️", "🤞", "💪", "👀", "🫡",
    "😀", "😂", "🙂", "😉", "😊", "😍", "🤔", "😅", "😬", "😮", "😢", "😡", "🥳", "😴", "🤯", "🤦",
    "✅", "❌", "⚠️", "❗", "❓", "🆘", "⛔", "🔥", "🎉", "❤️", "⭐", "💯",
    "🎤", "🎧", "🔊", "🔇", "📢", "🎬", "📷", "💡", "🖥️", "💻", "📱", "🔌", "🔋", "🕒", "⏱️", "☕", "🍕"];
  const QUICK = ["👍", "✅", "👀", "❤️", "😂", "🙏"];
  const kb = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`;

  // One emoji picker for the page, opened next to whatever asked for it.
  let picker = null, pickDone = null;
  function pickEmoji(anchor, done) {
    if (!picker) {
      picker = document.createElement("div");
      picker.className = "emoji-pick hidden";
      picker.innerHTML = EMOJI.map((e) => `<button type="button" data-e="${e}">${e}</button>`).join("");
      picker.onclick = (e) => { const b = e.target.closest("[data-e]"); if (b) { picker.classList.add("hidden"); pickDone?.(b.dataset.e); } };
      document.addEventListener("pointerdown", (e) => { if (!picker.contains(e.target) && !e.target.closest("[data-emoji],[data-react]")) picker.classList.add("hidden"); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") picker.classList.add("hidden"); });
      document.body.appendChild(picker);
    }
    if (!picker.classList.contains("hidden") && pickDone === done) { picker.classList.add("hidden"); return; }
    pickDone = done;
    picker.classList.remove("hidden");
    const r = anchor.getBoundingClientRect(), pw = picker.offsetWidth, ph = picker.offsetHeight;
    picker.style.left = Math.max(8, Math.min(r.right - pw, innerWidth - pw - 8)) + "px";
    picker.style.top = (r.top - ph - 6 > 8 ? r.top - ph - 6 : r.bottom + 6) + "px";
  }

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
            <button type="button" data-emoji title="Emoji">😊</button>
            <label class="btn" style="margin:0" title="Attach files (or paste or drop them here)">📎<input type="file" name="file" class="hidden" multiple></label>
            <button class="primary">Send</button>
          </form>
          <div class="pending-files"></div>
        </div>
      </div>`;
    const chansEl = el.querySelector(".chans"), listEl = el.querySelector(".list"), titleEl = el.querySelector(".title"), form = el.querySelector("form");
    const pendEl = el.querySelector(".pending-files"), myKey = me.kind === "account" ? `a:${me.id}` : `n:${me.name}`;
    // Admins can delete a direct-message chat for both people (also in Settings → Chat).
    const delChat = document.createElement("button");
    delChat.type = "button"; delChat.className = "small danger hidden"; delChat.textContent = "Delete chat";
    delChat.title = "Delete this direct-message chat for both people";
    titleEl.after(delChat);
    delChat.onclick = () => current && confirm("Delete this whole chat, its files and reactions, for both people?") &&
      guard(() => AT.del(`/api/admin/comms/dms/${current.id}`)).then(() => load(null), () => {});

    // files waiting to go with the next message: picked, pasted or dropped
    let files = [];
    function drawFiles() {
      pendEl.innerHTML = files.map((f, i) => `<span class="pf">${f.type.startsWith("image/") ? "🖼️" : "📄"} ${esc(f.name)} <span class="muted">${kb(f.size)}</span><button type="button" class="small" data-rm="${i}" title="Remove">✕</button></span>`).join("");
      pendEl.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => { files.splice(+b.dataset.rm, 1); drawFiles(); });
    }
    const addFiles = (list) => { files.push(...[...list].filter((f) => f.size)); drawFiles(); form.body.focus(); };
    form.file.onchange = () => { addFiles(form.file.files); form.file.value = ""; };
    form.body.addEventListener("paste", (e) => { const f = [...(e.clipboardData?.files || [])]; if (f.length) { e.preventDefault(); addFiles(f); } });
    el.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); el.classList.add("chat-drop"); } });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("chat-drop"); });
    el.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { e.preventDefault(); el.classList.remove("chat-drop"); addFiles(e.dataTransfer.files); } });
    form.querySelector("[data-emoji]").onclick = (e) => pickEmoji(e.currentTarget, (emo) => {
      const i = form.body.selectionStart ?? form.body.value.length, v = form.body.value;
      form.body.value = v.slice(0, i) + emo + v.slice(form.body.selectionEnd ?? i);
      form.body.focus(); form.body.selectionStart = form.body.selectionEnd = i + emo.length;
    });

    function chanLabel(c) { return c.kind === "site" ? `# ${c.name}` : c.kind === "room" ? c.name : `@ ${c.name}`; }

    function renderChans() {
      const items = channels.map((c) => `<${opts.compact ? "button type=button" : "a href=#"} data-id="${c.id}" class="${c.id === current?.id ? (opts.compact ? "on" : "active") : ""}">${esc(chanLabel(c))}${unread[c.id] ? ` <span class="unread">${unread[c.id]}</span>` : ""}</${opts.compact ? "button" : "a"}>`).join("");
      chansEl.innerHTML = (opts.compact ? "" : "<h3>Channels</h3>") + items +
        (me.kind === "node" ? "" : `<${opts.compact ? "button type=button" : "a href=#"} data-dm="1" class="muted">+ Direct message</${opts.compact ? "button" : "a"}>`);
      chansEl.querySelectorAll("[data-id]").forEach((a) => a.onclick = (e) => { e.preventDefault(); open(+a.dataset.id); });
      chansEl.querySelector("[data-dm]")?.addEventListener("click", (e) => { e.preventDefault(); pickDm(); });
      const total = Object.values(unread).reduce((a, b) => a + b, 0);
      opts.onUnread && opts.onUnread(total);
    }

    function msgHtml(m) {
      const atts = (m.attachments || []).map((a) => {
        const url = `/api/comms/attachments/${a.id}`;
        return /^image\/(png|jpe?g|gif|webp|bmp)$/.test(a.mime)
          ? `<a class="att att-img" href="${url}" target="_blank" title="${esc(a.original_name)}"><img src="${url}" alt="${esc(a.original_name)}" loading="lazy"></a>`
          : `<a class="att" href="${url}" target="_blank">📄 ${esc(a.original_name)} <span class="muted">${kb(a.size)}</span></a>`;
      }).join(" ");
      const mine = me.kind === "account" && m.sender_id === me.id;
      const del = (mine || me.role === "admin") && !m.deleted ? `<button class="small" data-del="${m.id}" title="Delete this message">Delete</button>` : "";
      const react = !m.deleted && me.role !== "viewer" ? `<button class="small" data-react="${m.id}" title="React">☺︎+</button>` : "";
      const reactions = (m.reactions || []).map((r) => `<button type="button" class="rx ${r.who.includes(myKey) ? "mine" : ""}" data-rx="${m.id}" data-e="${esc(r.emoji)}" title="${esc(r.names.join(", "))}">${esc(r.emoji)} ${r.count}</button>`).join("");
      return `<div class="msg ${esc(m.priority)}" data-mid="${m.id}"><span class="msg-tools">${react}${del}</span><div class="who">${esc(m.sender_name)} <span class="muted small">${when(m.created_at)}</span></div>` +
        `${m.deleted ? '<div><i class="muted">Deleted</i></div>' : m.body ? `<div>${esc(m.body).replace(/\n/g, "<br>")}</div>` : ""}${m.deleted ? "" : atts}` +
        `${reactions ? `<div class="rxs">${reactions}</div>` : ""}</div>`;
    }

    const toggle = (id, emoji) => guard(() => post(`/api/comms/messages/${id}/reactions`, { emoji })).then((m) => replace(m), () => {});
    function replace(m) {
      const existing = listEl.querySelector(`[data-mid="${m.id}"]`);
      if (existing) { existing.outerHTML = msgHtml(m); wireDeletes(listEl); }
    }
    function wireDeletes(scope) {
      scope.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => confirm("Delete this message?") && guard(() => AT.del(`/api/comms/messages/${b.dataset.del}`)));
      scope.querySelectorAll("[data-rx]").forEach((b) => b.onclick = () => toggle(+b.dataset.rx, b.dataset.e));
      scope.querySelectorAll("[data-react]").forEach((b) => b.onclick = () => {
        // the six most used straight away, everything else from the picker
        const id = +b.dataset.react, row = b.closest(".msg");
        row.querySelector(".rx-quick")?.remove();
        const q = document.createElement("div");
        q.className = "rx-quick";
        q.innerHTML = QUICK.map((e) => `<button type="button" data-q="${e}">${e}</button>`).join("") + '<button type="button" data-more title="More emoji">…</button>';
        q.onclick = (e) => {
          const t = e.target.closest("button"); if (!t) return;
          if (t.dataset.q) { q.remove(); toggle(id, t.dataset.q); }
          else pickEmoji(t, (emo) => { q.remove(); toggle(id, emo); });
        };
        row.appendChild(q);
      });
    }

    async function open(id) {
      current = channels.find((c) => c.id === id) || channels[0];
      if (!current) return;
      unread[current.id] = 0;
      renderChans();
      titleEl.textContent = chanLabel(current);
      delChat.classList.toggle("hidden", !(current.kind === "dm" && me.role === "admin"));
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
      const body = form.body.value.trim(), send = files;
      if (!body && !send.length) return;
      const m = await guard(() => post(`/api/comms/channels/${current.id}/messages`, { body, priority: form.priority.value }));
      form.body.value = ""; form.priority.value = "normal"; files = []; drawFiles();
      for (const f of send) await guard(() => upload(`/api/comms/messages/${m.id}/attachments`, f)).catch(() => {});
    };

    async function load(preferId) {
      channels = await api("/api/comms/channels");
      channels.forEach((c) => { unread[c.id] = c.unread || 0; }); // kept on the server, so a reload doesn't lose them
      renderChans();
      await open(preferId ?? opts.prefer ?? (channels.some((c) => c.id === current?.id) ? current.id : channels[0]?.id));
    }

    function onEvent(evt) {
      if (evt.type === "channel.deleted") { load(current?.id === evt.data.channel_id ? null : current?.id); return; }
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
          // someone else's new message lights up for a few seconds so it catches the eye
          if (evt.type === "message.new" && !(me.kind === "account" ? m.sender_id === me.id : !m.sender_id && m.sender_name === me.name)) listEl.lastElementChild.classList.add("fresh");
          listEl.scrollTop = listEl.scrollHeight;
          post(`/api/comms/channels/${cid}/read`, { up_to: m.id }).catch(() => {});
        }
        wireDeletes(listEl);
      } else if (evt.type === "message.new") {
        unread[cid] = (unread[cid] || 0) + 1;
        renderChans();
      }
      if (!opts.quiet && evt.type === "message.new" && m.priority === "urgent" && !(me.kind === "account" && m.sender_id === me.id)) {
        AT.toast(`Urgent from ${m.sender_name}: ${m.body}`, "bad");
      }
    }

    load();
    return { onEvent, reload: () => load(current?.id), open };
  }

  return { mount };
})();
