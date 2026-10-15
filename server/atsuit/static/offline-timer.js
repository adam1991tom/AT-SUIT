// The room timer keeps working on this laptop when the server can't be reached.
// Every timer button posts through TimerLink.post(). When the network is down the press is
// worked out here, with the same rules as the server (server/atsuit/modules/timers.py), shown
// at once on this laptop's workspace and timer windows, and kept. When the server is back
// the presses go to it in order, each as of when it was made (POST /api/timers/<room>/sync),
// and the latest change wins: anything older than a change made at the server meanwhile is left out.
var TimerLink = (() => { // var: cuelist.js and timerpanel.js look for window.TimerLink
  const ACTIONS = ["set", "start", "pause", "toggle", "reset", "add", "message", "thresholds",
    "load", "go", "next", "previous", "stop", "blink", "clock", "blackout"];
  const SWITCHES = { blink: "message_blink", clock: "show_clock", blackout: "blackout" };

  // ---------------------------------------------------------------- the engine --
  function playback(x) {
    if (x.running) return "play";
    if (x.started_at) return "pause";
    if (x.cue || x.duration_ms) return "armed";
    return "stop";
  }
  function neighbour(cues, id, step = 1) {
    const ids = cues.map((q) => q.id);
    let i = ids.includes(id) ? ids.indexOf(id) + step : step > 0 ? 0 : cues.length - 1;
    for (; i >= 0 && i < cues.length; i += step) if (!cues[i].skip) return cues[i];
    return null;
  }
  class Refused extends Error {}

  // A new state from the last one: what the server would answer to that press, at `now` (ms).
  function apply(prev, path, body, cues, now) {
    const ago = now - (prev._rx || now);
    const x = JSON.parse(JSON.stringify(prev));
    const sec = { ...(x.secondary || {}) };
    let left = x.running ? x.remaining_ms - ago : x.remaining_ms;
    let sleft = sec.running ? sec.remaining_ms - ago : sec.remaining_ms;
    const tsec = now / 1000 + (prev._off || 0) / 1000;
    const set = (f) => Object.assign(x, f);
    const load = (q, start) => {
      set({ cue: q, title: q.title, duration_ms: q.duration_ms, timer_type: q.timer_type, end_action: q.end_action, added_ms: 0,
        warn_ms: q.warn_ms ?? 300000, danger_ms: q.danger_ms ?? 60000, running: start, started_at: start ? tsec : null });
      left = q.duration_ms;
    };
    const [head, tail] = path.split("/");
    const running = !!x.running;
    if (head === "preset") {
      const ms = Math.round(+body.minutes * 60000);
      if (!(ms > 0)) throw new Refused("Minutes must be between 0 and 1440");
      const start = body.start !== false;
      set({ cue: null, title: body.title || "", duration_ms: ms, added_ms: 0, timer_type: "count-down", end_action: "none",
        warn_ms: body.warn_ms ?? 300000, danger_ms: body.danger_ms ?? 60000, running: start, started_at: start ? tsec : null, show_clock: false });
      left = ms;
    } else if (head === "message" && tail === "show") {
      set({ message: body.text ?? x.message, message_visible: true });
      if (body.blink != null) x.message_blink = !!body.blink;
    } else if (head === "message" && tail === "hide") {
      set({ message_visible: false, message_blink: false });
    } else if (head === "secondary") {
      if (tail === "text") { if (!(body.text || "").trim()) throw new Refused("Give the text to show"); Object.assign(sec, { mode: "text", text: body.text.trim(), visible: true }); }
      else if (tail === "timer") {
        const ms = body.minutes ? Math.round(body.minutes * 60000) : body.duration_ms;
        if (!ms) throw new Refused("Give minutes or duration_ms");
        Object.assign(sec, { mode: "timer", duration_ms: ms, visible: true, running: body.start !== false }); sleft = ms;
      } else if ((tail === "start" || tail === "toggle") && !sec.running) Object.assign(sec, { mode: "timer", visible: true, running: true });
      else if ((tail === "pause" || tail === "toggle") && sec.running) sec.running = false;
      else if (tail === "reset") { sec.running = false; sleft = sec.duration_ms; }
      else if (tail === "add") sleft += body.delta_ms || 0;
      else if (tail === "show" || tail === "hide") sec.visible = tail === "show";
    } else if (ACTIONS.includes(path)) {
      if (["set", "load", "go", "next", "previous"].includes(path) || ((path === "start" || path === "toggle") && !running)) x.show_clock = false;
      if (path === "set") {
        const d = body.duration_ms ?? x.duration_ms;
        set({ cue: null, duration_ms: d, running: false, started_at: null, added_ms: 0, timer_type: "count-down", end_action: "none" });
        if (body.title != null) x.title = body.title;
        left = d;
      } else if ((path === "start" || path === "toggle") && !running) {
        const q = !x.cue && !x.duration_ms && cues.length ? neighbour(cues, null) : null;
        if (q) load(q, true);
        else set({ running: true, started_at: x.started_at || tsec });
      } else if ((path === "pause" || path === "toggle") && running) x.running = false;
      else if (path === "reset") { set({ running: false, started_at: null, added_ms: 0 }); left = x.duration_ms; }
      else if (path === "add") { left += body.delta_ms || 0; x.added_ms = (x.added_ms || 0) + (body.delta_ms || 0); }
      else if (path === "message") {
        if (body.message != null) x.message = body.message;
        for (const k of ["message_visible", "message_blink", "blackout"]) if (body[k] != null) x[k] = !!body[k];
      } else if (path === "thresholds") {
        for (const k of ["warn_ms", "danger_ms"]) if (body[k] != null) x[k] = body[k];
        if (body.flash_danger != null) x.flash_danger = !!body.flash_danger;
      } else if (path === "load") {
        const q = cues.find((c) => c.id === body.cue_id);
        if (!q) throw new Refused("Cue not found");
        load(q, false);
      } else if (path === "go" && playback(x) === "armed") set({ running: true, started_at: x.started_at || tsec });
      else if (path === "go" || path === "next") {
        const q = neighbour(cues, x.cue?.id);
        if (q) load(q, path === "go");
        else if (x.cue && cues.length) throw new Refused("That's the last cue");
      } else if (path === "previous") { const q = neighbour(cues, x.cue?.id, -1); if (q) load(q, false); }
      else if (path === "stop") { set({ cue: null, title: "", duration_ms: 0, running: false, started_at: null, added_ms: 0, timer_type: "count-down", end_action: "none" }); left = 0; }
      else if (SWITCHES[path]) x[SWITCHES[path]] = body.on != null ? !!body.on : !x[SWITCHES[path]];
    } else throw new Refused("This can't be done while the server is out of reach");
    x.remaining_ms = Math.round(left);
    sec.remaining_ms = Math.round(sleft);
    x.secondary = sec;
    x.playback = playback(x);
    x.elapsed_ms = x.started_at ? Math.max(0, x.duration_ms + (x.added_ms || 0) - left) : 0;
    const nxt = cues.length ? neighbour(cues, x.cue?.id) : null;
    x.next = nxt;
    x.cue_index = x.cue ? cues.findIndex((q) => q.id === x.cue.id) : null;
    x.cue_count = cues.length;
    x.clock_ms = (prev.clock_ms || 0) + ago;
    x.server_time = tsec;
    x._rx = now;
    x._off = prev._off || 0;
    x._local = true;
    return x;
  }

  // ------------------------------------------------------------- the link --
  let room = null, state = null, cues = [], queue = [], timer = null, busy = false, bar = null;
  const key = () => `atsuit.timer-offline.${room}`;
  const offline = (e) => e instanceof TypeError || [0, 502, 503, 504].includes(e?.status);

  function use(roomId) {
    if (room === roomId) return;
    room = roomId;
    try { queue = JSON.parse(localStorage.getItem(key()) || "[]"); } catch (_) { queue = []; }
    if (queue.length) { show(); schedule(); }
  }
  function save() { try { localStorage.setItem(key(), JSON.stringify(queue)); } catch (_) {} }

  function show() {
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "timerOffline";
      bar.setAttribute("role", "status");
      bar.style.cssText = "position:fixed;left:50%;bottom:.8rem;transform:translateX(-50%);z-index:60;max-width:min(92vw,40rem);padding:.55rem .9rem;border-radius:var(--radius,8px);background:var(--warn,#d97706);color:#111;font-weight:600;box-shadow:0 4px 16px #0006;text-align:center";
      document.body.append(bar);
    }
    const n = queue.length;
    bar.textContent = `Offline: the timer is running on this laptop. ${n} change${n === 1 ? "" : "s"} will go to every screen when the server is back.`;
    bar.hidden = !n;
  }
  function schedule() { if (!timer) timer = setInterval(flush, 3000); }

  function publish(x) {
    state = x;
    document.dispatchEvent(new CustomEvent("atsuit:timer", { detail: x }));
    try { window.atsuitApp?.timerLocal?.(x); } catch (_) {}
  }

  async function flush() {
    if (busy || !queue.length) return;
    busy = true;
    const ops = queue.slice();
    try {
      const r = await AT.post(`/api/timers/${room}/sync`, { ops });
      queue.splice(0, ops.length);
      save();
      if (!queue.length) { clearInterval(timer); timer = null; }
      show();
      publish(r.state);
      AT.toast(r.skipped
        ? `Back online. ${r.skipped} timer change${r.skipped === 1 ? " was" : "s were"} left out: someone changed this room's timer at the server after it.`
        : "Back online. Every screen has the timer changes made on this laptop.", r.skipped ? "" : "good");
    } catch (e) {
      if (!offline(e)) { // the server answered but won't take them (signed out, no such room): don't keep trying
        queue = []; save(); show();
        clearInterval(timer); timer = null;
        AT.toast(`The timer changes made offline weren't sent: ${e.message}`, "bad");
      }
    } finally { busy = false; }
  }

  // Post a timer press. Resolves to the new state, from the server or worked out here.
  async function post(roomId, path, body) {
    use(roomId);
    if (!queue.length) {
      try { return await AT.post(`/api/timers/${roomId}/${path}`, body); }
      catch (e) { if (!offline(e) || !state) throw e; }
    }
    const now = Date.now();
    const x = apply(state, path, body || {}, cues, now); // throws Refused, which the button shows
    queue.push({ path, body: body || {}, at: (now + (state._off || 0)) / 1000 });
    save(); show(); schedule();
    publish(x);
    return x;
  }

  return {
    post,
    apply, // for tests
    seen(x) { if (x && (x.room_id === room || room === null)) state = x; },
    cues(list) { cues = list || []; },
    pending: () => queue.length,
    use,
  };
})();
