// Room timer with an Ontime-style cue list, used in the tech workspace.
// mount(el, roomId) returns {onEvent(evt)}.
const CueList = (() => {
  const { esc, api, post, put, del, upload, guard, toast, fmtTime, timerDisplay } = AT;
  const END = { none: "Keep going (overtime)", stop: "Stop at zero", "load-next": "Load next", "play-next": "Play next" };
  const TYPES = { "count-down": "Count down", "count-up": "Count up", clock: "Clock", none: "No timer" };
  const dur = (ms) => fmtTime(ms).replace(/^\+/, "");
  const parse = (s) => {
    const p = String(s).trim().split(":").map(Number);
    if (!s.trim() || p.some(isNaN)) return null;
    return (p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p.length === 2 ? p[0] * 60 + p[1] : p[0] * 60) * 1000;
  };

  function mount(el, roomId) {
    let s = null, cues = [], editing = null;
    el.innerHTML = `
      <div class="row" style="justify-content:space-between"><h3>Room timer</h3>
        <span class="small"><a target="_blank" href="/timer/${roomId}">Standard ↗</a> · <a target="_blank" href="/timer/${roomId}?view=backstage">Backstage ↗</a>
          <select data-views style="width:auto;padding:.15rem .3rem;font-size:.85rem;margin-left:.3rem"><option value="">More views…</option></select></span></div>
      <div class="clock bigclock" data-clock>--:--</div>
      <div data-now style="font-weight:600"></div><div class="muted small" data-next></div>
      <div class="row" style="margin-top:.6rem;flex-wrap:wrap">
        <button class="primary" data-a="go" title="Load the next cue and start it">GO ▶</button>
        <button data-a="toggle">Start / pause</button><button data-a="stop">Stop</button>
        <button data-a="previous" title="Load the previous cue">◀ Prev</button><button data-a="next" title="Load the next cue without starting">Next ▶</button>
        <button data-add="-60000">−1m</button><button data-add="60000">+1m</button>
      </div>
      <div class="row" style="margin-top:.5rem;flex-wrap:wrap">
        <button data-sw="blink" title="Flash the timer (and the message) on the stage screens">Blink</button>
        ${window.TimerPanel ? "" : '<button data-sw="clock" title="Show the time of day on the stage screens instead of the timer">Clock</button>'}
        <button data-sw="blackout" title="Blank the stage screens">Blackout</button>
      </div>
      <div class="row" style="margin-top:.5rem"><input data-msg class="grow" placeholder="Message to the stage">
        <button data-show>Show</button><button data-hide>Hide</button></div>
      <label class="small muted" style="display:flex;gap:.3rem;align-items:center;margin:.4rem 0 0" title="Blinks the stage timer when a cue reaches its danger time, and stops when the next one starts"><input type="checkbox" data-flash style="width:auto">Flash the timer at danger</label>
      <div data-tp></div>
      <details style="margin-top:.6rem" open><summary><b>Cue list</b> <span class="muted small" data-count></span></summary>
        <div class="cues" data-list></div>
        <form class="cueform" data-form>
          <input name="cue" placeholder="Cue" style="width:4.5rem"><input name="title" placeholder="Title" class="grow" required>
          <input name="duration" placeholder="mm:ss" style="width:5.5rem" required><input name="time_start" placeholder="Start hh:mm" style="width:6.5rem">
          <select name="end_action" style="width:auto">${Object.entries(END).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
          <select name="timer_type" style="width:auto">${Object.entries(TYPES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
          <input name="note" placeholder="Note" class="grow">
          <input name="colour" type="color" value="#3b82f6" title="Colour" style="width:2.6rem;padding:0">
          <label class="small" style="display:flex;gap:.3rem;align-items:center;margin:0"><input type="checkbox" name="skip" style="width:auto">Skip</label>
          <button class="primary" data-save>Add cue</button><button type="button" data-cancel class="hidden">Cancel</button>
        </form>
        <div class="row small" style="margin-top:.4rem"><span class="muted">Bring in an Ontime running order:</span>
          <input type="file" data-import accept=".json" style="width:auto"></div>
      </details>`;
    const $ = (sel) => el.querySelector(sel);
    const form = $("[data-form]");
    // quick timers, quick messages and the second line (timerpanel.js), before the buttons are wired up
    const panel = window.TimerPanel ? TimerPanel.mount($("[data-tp]"), roomId) : null;

    function renderList() {
      $("[data-count]").textContent = cues.length ? `${cues.length} cue${cues.length === 1 ? "" : "s"}` : "";
      $("[data-list]").innerHTML = cues.map((q, i) => `
        <div class="cue ${q.id === s?.cue?.id ? "now" : ""} ${q.skip ? "skip" : ""}" data-q="${q.id}" style="border-left-color:${esc(q.colour || "transparent")}">
          <span class="c">${esc(q.cue)}</span><span class="t">${esc(q.title)}${q.note ? `<br><span class="muted small">${esc(q.note)}</span>` : ""}</span>
          <span class="d small">${q.time_start ? esc(q.time_start) + " · " : ""}${dur(q.duration_ms)}<br><span class="muted">${esc(END[q.end_action] || "")}</span></span>
          <span class="b"><button class="small" data-load title="Load this cue">Load</button><button class="small" data-edit>Edit</button>
            <button class="small" data-up ${i ? "" : "disabled"} title="Move up">↑</button><button class="small" data-down ${i < cues.length - 1 ? "" : "disabled"} title="Move down">↓</button>
            <button class="small danger" data-del title="Delete">✕</button></span></div>`).join("") || '<p class="muted small">No cues yet. Add the running order below, or bring in an Ontime file.</p>';
      $("[data-list]").querySelectorAll("[data-q]").forEach((row) => {
        const id = +row.dataset.q, i = cues.findIndex((q) => q.id === id);
        row.querySelector("[data-load]").onclick = () => guard(() => post(`/api/timers/${roomId}/load`, { cue_id: id }));
        row.querySelector("[data-edit]").onclick = () => startEdit(cues[i]);
        row.querySelector("[data-del]").onclick = () => guard(() => del(`/api/timers/${roomId}/cues/${id}`));
        const move = (d) => { const ids = cues.map((q) => q.id); ids.splice(i + d, 0, ids.splice(i, 1)[0]); guard(() => post(`/api/timers/${roomId}/cues/reorder`, { ids })); };
        row.querySelector("[data-up]").onclick = () => move(-1);
        row.querySelector("[data-down]").onclick = () => move(1);
      });
    }

    function startEdit(q) {
      editing = q.id;
      form.cue.value = q.cue; form.title.value = q.title; form.duration.value = dur(q.duration_ms); form.time_start.value = q.time_start;
      form.end_action.value = q.end_action; form.timer_type.value = q.timer_type; form.note.value = q.note;
      form.colour.value = q.colour || "#3b82f6"; form.skip.checked = q.skip;
      $("[data-save]").textContent = "Save cue"; $("[data-cancel]").classList.remove("hidden");
      form.title.focus();
    }
    function endEdit() {
      editing = null; form.reset();
      $("[data-save]").textContent = "Add cue"; $("[data-cancel]").classList.add("hidden");
    }
    $("[data-cancel]").onclick = endEdit;
    form.onsubmit = async (e) => {
      e.preventDefault();
      const d = parse(form.duration.value);
      if (d === null) return toast("Duration as mm:ss, like 20:00", "bad");
      const body = { cue: form.cue.value.trim(), title: form.title.value.trim(), duration_ms: d, time_start: form.time_start.value.trim(),
        end_action: form.end_action.value, timer_type: form.timer_type.value, note: form.note.value.trim(), colour: form.colour.value, skip: form.skip.checked };
      if (editing) await guard(() => put(`/api/timers/${roomId}/cues/${editing}`, body));
      else await guard(() => post(`/api/timers/${roomId}/cues`, body));
      endEdit();
    };
    $("[data-import]").onchange = (e) => {
      const f = e.target.files[0];
      if (f) guard(() => upload(`/api/timers/${roomId}/cues/import`, f)).then((r) => toast(`${r.imported} cues imported`, "good"));
      e.target.value = "";
    };
    // Every timer button shows its result straight away from the reply, without waiting for the live feed.
    const act = (path, body) => guard(() => post(`/api/timers/${roomId}/${path}`, body)).then(apply, () => {});
    el.querySelectorAll("[data-a]").forEach((b) => b.onclick = () => act(b.dataset.a));
    el.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => act("add", { delta_ms: +b.dataset.add }));
    $("[data-show]").onclick = () => act("message", { message: $("[data-msg]").value, message_visible: true });
    el.querySelectorAll("[data-sw]").forEach((b) => b.onclick = () => act(b.dataset.sw, {}));
    api("/api/timers-views").then((views) => {
      const sel = $("[data-views]");
      sel.insertAdjacentHTML("beforeend", views.filter((v) => !["hcc", "backstage"].includes(v.id)).map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join(""));
      sel.onchange = () => {
        const v = sel.value;
        if (v) open(v.startsWith("view:") ? `/room/${roomId}/external/${encodeURIComponent(v.slice(5))}/` : `/timer/${roomId}?view=${encodeURIComponent(v)}`, "_blank");
        sel.value = "";
      };
    }).catch(() => {});
    $("[data-flash]").onchange = (e) => act("thresholds", { flash_danger: e.target.checked });
    $("[data-hide]").onclick = () => act("message", { message_visible: false });

    // The newest state wins, whether it came back from a button or over the live feed.
    function apply(x) {
      if (!x || x.room_id !== roomId || !("remaining_ms" in x)) return x;
      if (s && x.server_time < s.server_time) return x;
      const was = s?.cue?.id;
      x._rx = Date.now(); s = x; panel?.update(s); draw();
      if (was !== s.cue?.id) renderList();
      return x;
    }
    document.addEventListener("atsuit:timer", (e) => apply(e.detail));

    setInterval(() => draw(), 100);
    function draw() {
      if (!s) return;
      const d = timerDisplay(s), c = $("[data-clock]");
      $("[data-flash]").checked = !!s.flash_danger;
      for (const [k, on] of [["blink", s.message_blink], ["clock", s.show_clock], ["blackout", s.blackout]]) $(`[data-sw="${k}"]`).classList.toggle("lit", !!on);
      c.textContent = d.text;
      c.className = "clock bigclock " + d.cls;
      $("[data-now]").textContent = (s.cue ? `${s.cue.cue ? s.cue.cue + " · " : ""}` : "") + (s.title || "") + (s.playback === "pause" ? "  (paused)" : "");
      $("[data-next]").textContent = (s.next ? `Next: ${s.next.title} (${dur(s.next.duration_ms)})` : "") + (s.message_visible ? `  ·  On stage: ${s.message}` : "");
    }

    (async () => {
      const [first, list] = await Promise.all([api(`/api/timers/${roomId}`), api(`/api/timers/${roomId}/cues`)]);
      cues = list; apply(first); renderList();
    })();

    return {
      onEvent(evt) {
        if (evt.topic !== `timer:${roomId}`) return;
        if (evt.type === "timer") apply(evt.data);
        if (evt.type === "cues") { cues = evt.data.cues; renderList(); }
      },
    };
  }

  return { mount };
})();
