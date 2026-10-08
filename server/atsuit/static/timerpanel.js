// Quick controls under the room timer in the tech workspace: one-tap timers
// (3 to 60 minutes and the Clock), ready-made messages to the speaker, and
// the second line under the stage timer (a second countdown or a text).
// mount(el, roomId) returns {update(state)}; CueList calls it.
var TimerPanel = (() => { // var: cuelist.js looks for window.TimerPanel
  const { esc, api, post, put, del, guard, toast, fmtTime } = AT;
  const MINUTES = [3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60];
  const SEC_MINUTES = [1, 2, 3, 5, 10, 15, 30];
  const store = {
    get(k, d) { try { const v = localStorage.getItem("atsuit.tp." + k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("atsuit.tp." + k, JSON.stringify(v)); } catch (_) {} },
  };
  const parse = (s) => {
    const p = String(s).trim().split(":").map(Number);
    if (!String(s).trim() || p.some(isNaN)) return null;
    return (p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p.length === 2 ? p[0] * 60 + p[1] : p[0] * 60) * 1000;
  };
  let styled = false;
  function style() {
    if (styled) return;
    styled = true;
    const st = document.createElement("style");
    st.textContent = `
      .tp { margin-top: .7rem; display: grid; gap: .7rem; }
      .tp .tp-h { display: flex; align-items: center; justify-content: space-between; gap: .5rem; font-size: .78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; font-weight: 600; margin-bottom: .3rem; }
      .tp .tp-h label { display: inline-flex; align-items: center; gap: .3rem; margin: 0; text-transform: none; letter-spacing: 0; font-weight: 400; font-size: .8rem; }
      .tp .tp-h input[type=checkbox] { width: auto; }
      .tp .tp-q { display: grid; grid-template-columns: repeat(auto-fill, minmax(3.1rem, 1fr)); gap: .3rem; }
      .tp .tp-q button { justify-content: center; padding: .45rem 0; font-weight: 700; font-variant-numeric: tabular-nums; }
      .tp .tp-q button.cur { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
      .tp .tp-clock { grid-column: span 3; font-size: 1.05rem; letter-spacing: .03em; }
      .tp .tp-clock:not(.lit) { border-color: var(--accent); color: var(--accent); }
      .tp .tp-m { display: flex; flex-wrap: wrap; gap: .3rem; }
      .tp .tp-m button { padding: .35rem .6rem; font-size: .88rem; }
      .tp .tp-m button.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); font-weight: 700; }
      .tp .tp-row { display: flex; gap: .3rem; flex-wrap: wrap; align-items: center; }
      .tp .tp-row input { flex: 1; min-width: 8rem; width: auto; }
      .tp .tp-sec-now { font-variant-numeric: tabular-nums; font-weight: 700; }
      .tp textarea { min-height: 9rem; font-size: .9rem; }`;
    document.head.append(st);
  }

  function mount(el, roomId) {
    style();
    let s = null, msgs = [], canEdit = false, editing = false;
    let startOnTap = store.get("start", true), blinkMsg = store.get("blink", false);
    el.className = "tp";
    el.innerHTML = `
      <div><div class="tp-h"><span>Quick timer (minutes)</span>
          <label title="Off: the timer is loaded ready, and starts with Start / pause"><input type="checkbox" data-tstart ${startOnTap ? "checked" : ""}>Start on tap</label></div>
        <div class="tp-q">${MINUTES.map((m) => `<button data-min="${m}" title="${m} minute timer">${m}</button>`).join("")}
          <button class="tp-clock" data-sw="clock" title="Show the time of day on the stage screens (tap again for the timer)">🕒 Clock</button></div></div>
      <div><div class="tp-h"><span>Message to the speaker</span>
          <span><label title="Quick messages flash on the stage screens"><input type="checkbox" data-mblink ${blinkMsg ? "checked" : ""}>Blink</label>
          <button class="small hidden" data-medit title="Change the quick messages for every room">Edit</button></span></div>
        <div class="tp-m" data-msgs><span class="muted small">Loading…</span></div>
        <div class="hidden" data-meditor style="margin-top:.4rem"><textarea data-mtext placeholder="One message per line"></textarea>
          <div class="tp-row" style="justify-content:flex-end;margin-top:.3rem"><button class="small" data-mreset>Back to the defaults</button><button class="small" data-mcancel>Cancel</button><button class="small primary" data-msave>Save</button></div></div></div>
      <div><div class="tp-h"><span>Second line under the timer</span><span class="tp-sec-now small" data-snow style="text-transform:none;letter-spacing:0"></span></div>
        <div class="tp-row"><input data-stext maxlength="200" placeholder="Text, e.g. Q&amp;A next"><button class="small" data-sshow>Show text</button></div>
        <div class="tp-row" style="margin-top:.3rem"><span class="small muted">Countdown</span>${SEC_MINUTES.map((m) => `<button class="small" data-smin="${m}">${m}m</button>`).join("")}
          <input data-sdur placeholder="mm:ss" style="width:5.5rem;min-width:5.5rem;flex:0 0 auto"><button class="small" data-sset>Set</button></div>
        <div class="tp-row" style="margin-top:.3rem"><button class="small" data-sa="toggle">Start / pause</button><button class="small" data-sa="reset">Reset</button>
          <button class="small" data-sadd="-60000">−1m</button><button class="small" data-sadd="60000">+1m</button><button class="small" data-svis>Hide</button></div></div>`;
    const $ = (q) => el.querySelector(q);

    // quick timers
    $("[data-tstart]").onchange = (e) => { startOnTap = e.target.checked; store.set("start", startOnTap); };
    el.querySelectorAll("[data-min]").forEach((b) => b.onclick = () =>
      guard(() => post(`/api/timers/${roomId}/preset/${b.dataset.min}?start=${startOnTap}`)).then((x) => x && toast(`${b.dataset.min} minutes ${startOnTap ? "started" : "loaded"}`, "good")));

    // quick messages: tap to show, tap the one on stage to hide it
    $("[data-mblink]").onchange = (e) => { blinkMsg = e.target.checked; store.set("blink", blinkMsg); };
    const onStage = (t) => s && s.message_visible && s.message === t;
    function renderMsgs() {
      $("[data-msgs]").innerHTML = msgs.map((m, i) => `<button data-mi="${i}" class="${onStage(m) ? "on" : ""}">${esc(m)}</button>`).join("") || '<span class="muted small">No quick messages.</span>';
      $("[data-msgs]").querySelectorAll("[data-mi]").forEach((b) => b.onclick = () => {
        const t = msgs[+b.dataset.mi];
        guard(() => onStage(t) ? post(`/api/timers/${roomId}/message/hide`) : post(`/api/timers/${roomId}/message/show`, { text: t, blink: blinkMsg }));
      });
      $("[data-medit]").classList.toggle("hidden", !canEdit || editing);
    }
    const loadMsgs = () => api("/api/timers-quick-messages").then((r) => { msgs = r.messages; canEdit = r.can_edit; renderMsgs(); }).catch(() => { $("[data-msgs]").innerHTML = ""; });
    loadMsgs();
    const editor = (on) => { editing = on; $("[data-meditor]").classList.toggle("hidden", !on); $("[data-msgs]").classList.toggle("hidden", on); renderMsgs(); };
    $("[data-medit]").onclick = () => { $("[data-mtext]").value = msgs.join("\n"); editor(true); };
    $("[data-mcancel]").onclick = () => editor(false);
    $("[data-msave]").onclick = () => guard(() => put("/api/timers-quick-messages", { messages: $("[data-mtext]").value.split("\n") }))
      .then((r) => { if (r) { msgs = r.messages; editor(false); toast("Quick messages saved", "good"); } });
    $("[data-mreset]").onclick = () => confirm("Put the quick messages back to the defaults?") &&
      guard(() => del("/api/timers-quick-messages")).then((r) => { if (r) { msgs = r.messages; editor(false); } });

    // the second line
    const sec = (action, body) => guard(() => post(`/api/timers/${roomId}/secondary/${action}`, body || {}));
    $("[data-sshow]").onclick = () => { const t = $("[data-stext]").value.trim(); if (!t) return toast("Type the text first", "bad"); sec("text", { text: t }); };
    $("[data-stext]").onkeydown = (e) => { if (e.key === "Enter") $("[data-sshow]").click(); };
    el.querySelectorAll("[data-smin]").forEach((b) => b.onclick = () => sec("timer", { minutes: +b.dataset.smin }));
    $("[data-sset]").onclick = () => { const d = parse($("[data-sdur]").value); if (!d) return toast("Time as mm:ss, like 5:00", "bad"); sec("timer", { duration_ms: d, start: false }); };
    el.querySelectorAll("[data-sa]").forEach((b) => b.onclick = () => sec(b.dataset.sa));
    el.querySelectorAll("[data-sadd]").forEach((b) => b.onclick = () => sec("add", { delta_ms: +b.dataset.sadd }));
    $("[data-svis]").onclick = () => sec(s?.secondary?.visible ? "hide" : "show");

    function tick() {
      if (!s) return;
      const x = s.secondary || {};
      let now = "";
      if (x.mode === "timer" && x.duration_ms) {
        const left = x.running ? x.remaining_ms - (Date.now() - (s._rx || Date.now())) : x.remaining_ms;
        now = fmtTime(left).replace(/^\+/, "-") + (x.running ? "" : " (paused)");
      } else if (x.text) now = `“${x.text}”`;
      const txt = now ? (x.visible ? "On stage: " : "Hidden: ") + now : "Off";
      if ($("[data-snow]").textContent !== txt) $("[data-snow]").textContent = txt;
      $("[data-svis]").textContent = x.visible ? "Hide" : "Show";
      $("[data-svis]").disabled = !now;
      const cur = !s.cue && s.playback !== "stop" ? Math.round(s.duration_ms / 60000) : null;
      el.querySelectorAll("[data-min]").forEach((b) => b.classList.toggle("cur", cur !== null && +b.dataset.min === cur && s.duration_ms % 60000 === 0));
    }
    setInterval(tick, 250);

    return {
      update(state) {
        const was = s ? `${s.message_visible}|${s.message}` : "";
        if (!state._rx) state._rx = Date.now();
        s = state;
        if (`${s.message_visible}|${s.message}` !== was) renderMsgs();
        tick();
      },
    };
  }

  return { mount };
})();
