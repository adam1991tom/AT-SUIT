// Shared helpers for every AT-SUIT page.
const AT = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // A validation error from the server, in words: "Start: use h:mm, like 9:30" rather than a regex.
  function fieldMsg(d) {
    const f = String((d.loc || []).slice(-1)[0] || "").replace(/_/g, " ");
    const name = f && f !== "body" ? f[0].toUpperCase() + f.slice(1) : "";
    let m = d.msg || "Not valid";
    if (d.type === "string_pattern_mismatch") m = /\\d\{1,2\}:/.test(String(d.ctx && d.ctx.pattern)) ? "use a time like 9:30" : "not in the right format";
    else if (d.type === "string_too_short") m = d.ctx && d.ctx.min_length > 1 ? `needs at least ${d.ctx.min_length} characters` : "can't be empty";
    else if (d.type === "string_too_long") m = `can be at most ${d.ctx && d.ctx.max_length} characters`;
    else if (d.type === "missing") m = "is needed";
    m = m.replace(/^Value error, /, "");
    return name ? `${name}: ${m}` : m;
  }

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: { ...(opts.headers || {}) }, credentials: "same-origin" };
    if (opts.form) init.body = opts.form;
    else if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const r = await fetch(path, init);
    const type = r.headers.get("content-type") || "";
    const data = type.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : data.detail.map(fieldMsg).join(". ")) : r.statusText;
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return data;
  }
  const post = (p, body) => api(p, { method: "POST", body: body ?? {} });
  const put = (p, body) => api(p, { method: "PUT", body: body ?? {} });
  const del = (p) => api(p, { method: "DELETE" });
  const upload = (p, file, field = "file") => { const f = new FormData(); f.append(field, file); return api(p, { method: "POST", form: f }); };

  // One reconnecting WebSocket with topic subscriptions.
  function socket(topics, onEvent, onState) {
    let ws, closed = false, delay = 1000, current = new Set(topics);
    function open() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws?topics=${encodeURIComponent([...current].join(","))}`);
      ws.onopen = () => { delay = 1000; onState && onState(true); };
      ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)); } catch (_) {} };
      ws.onclose = () => { onState && onState(false); if (!closed) setTimeout(open, delay = Math.min(delay * 2, 15000)); };
    }
    open();
    const ping = setInterval(() => ws && ws.readyState === 1 && ws.send('{"type":"ping"}'), 25000);
    return {
      subscribe(more) { more.forEach((t) => current.add(t)); if (ws.readyState === 1) ws.send(JSON.stringify({ type: "subscribe", topics: more })); },
      close() { closed = true; clearInterval(ping); ws.close(); },
    };
  }

  function toast(msg, kind = "") {
    let box = document.getElementById("toasts");
    if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
    const t = document.createElement("div");
    t.className = `toast ${kind}`;
    t.textContent = msg;
    box.appendChild(t);
    setTimeout(() => t.remove(), 4500);
  }

  // Runs an action and shows its error. It still rejects (so a caller's .then() is skipped),
  // but an error it has shown isn't reported again as an unhandled rejection.
  async function guard(fn) {
    try { return await fn(); } catch (e) { toast(e.message, "bad"); if (e && typeof e === "object") e.shown = true; throw e; }
  }
  addEventListener("unhandledrejection", (e) => { if (e.reason && e.reason.shown) e.preventDefault(); });

  function fmtTime(ms) {
    const neg = ms < 0; ms = Math.abs(ms);
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const body = h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return (neg ? "+" : "") + body;
  }

  // Marks when a timer state arrived (x._rx), which every page counts the time from.
  // Each copy takes a slightly different time to arrive, so taking every arrival as
  // "now" makes the seconds jump about. While the gap between the server's clock and
  // ours stays within half a second of the last copy's, keep the last copy's gap, so
  // the count carries on smoothly; anything bigger is a real jump and is taken as it is.
  function stamp(x, prev) {
    const now = Date.now();
    x._rx = now;
    if (x.server_time) {
      x._off = x.server_time * 1000 - now;
      if (prev && prev._off !== undefined && Math.abs(x._off - prev._off) < 500) {
        x._rx = now + (prev._off - x._off);
        x._off = prev._off;
      }
    }
    return x;
  }

  // Redraws a timer page the moment a shown second changes (the timer, the second
  // line or the time of day) or a new state arrives, and every quarter second anyway.
  // A fixed 100ms timer flips each second up to a tenth late, a different amount
  // each time, which reads as stutter on a big screen.
  function ticker(get, fn) {
    let key = "", at = 0, last = null;
    const sec = (ms) => Math.floor(ms / 1000);
    const frame = (t) => {
      requestAnimationFrame(frame);
      const x = get();
      if (!x || !x._rx) return;
      const ago = Date.now() - x._rx, y = x.secondary || {};
      const k = [sec(x.running ? x.remaining_ms - ago : x.remaining_ms), sec((x.clock_ms || 0) + ago), sec(y.running ? y.remaining_ms - ago : 0)].join("|");
      if (k === key && x === last && t - at < 250) return;
      key = k; at = t; last = x;
      fn();
    };
    requestAnimationFrame(frame);
  }

  // Keeps a timer state ticking locally, corrected for server clock offset.
  // Remaining ms of a timer state, counted locally from when this copy arrived.
  function timerClock(state) {
    if (!state._rx) state._rx = Date.now();
    return () => (state.running ? state.remaining_ms - (Date.now() - state._rx) : state.remaining_ms);
  }

  // What a timer shows: count-down, count-up (elapsed), the time of day, or nothing.
  function timerDisplay(state) {
    const left = timerClock(state)();
    const type = state.timer_type || "count-down";
    let text = fmtTime(left);
    if (type === "count-up") text = fmtTime(Math.max(0, state.duration_ms + (state.added_ms || 0) - left)).replace(/^\+/, "");
    else if (type === "clock") text = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    else if (type === "none") text = "";
    const loaded = state.playback !== "stop";
    const cls = !loaded || type === "clock" || type === "none" ? "" : left < 0 ? "over" : left <= state.danger_ms ? "danger" : left <= state.warn_ms ? "warn" : "";
    return { left, text: loaded || type === "clock" ? text : "--:--", cls };
  }

  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
  }

  // Text on the accent colour: navy on light accents (the brand orange), white on dark ones.
  function ink(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || ""); if (!m) return "#0B1020";
    const n = parseInt(m[1], 16), [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255];
    return 0.299 * r + 0.587 * g + 0.114 * b > 140 ? "#0B1020" : "#ffffff";
  }
  async function branding() {
    try {
      const b = await api("/api/public/branding");
      document.documentElement.style.setProperty("--accent", b.accent || "#FF7A1A");
      document.documentElement.style.setProperty("--accent-ink", ink(b.accent || "#FF7A1A"));
      document.querySelectorAll("[data-product]").forEach((el) => (el.textContent = b.product_name));
      if (window.ATTheme && b.appearance) { ATTheme.apply(b.appearance); ATTheme.remember(b.appearance); }
      return b;
    } catch (_) { return {}; }
  }

  function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

  // A part of a panel can be moved into a window of its own (the workspace desk).
  // detach() notes where it came from, so q() on the panel still finds what is in it.
  const detached = [];
  function detach(part) { detached.push({ from: part.parentNode, el: part }); return part; }
  function q(root, sel) {
    const here = root.querySelector(sel);
    if (here) return here;
    for (const d of detached) {
      if (d.from !== root && !root.contains(d.from)) continue;
      const hit = d.el.matches(sel) ? d.el : d.el.querySelector(sel);
      if (hit) return hit;
    }
    return null;
  }
  // Sign-in forms (with username and password inputs): "Forgot password?", and the
  // step where someone signing in with a password an admin set chooses their own.
  // Send {...body, ...pw.extra()}, and pass any error to pw.handle() first.
  function passwordForm(f, errEl) {
    f.password.insertAdjacentHTML("afterend", `
      <div class="pw-new hidden"><p class="small" style="margin:.5rem 0 0">Choose your own password now: at least 8 characters, not ChangeMe.</p>
        <label>New password</label><input name="new_password" type="password" autocomplete="new-password">
        <label>New password again</label><input name="new_password2" type="password" autocomplete="new-password"></div>
      <p class="small" style="margin:.35rem 0 0"><a href="#" data-forgot>Forgot password?</a></p>`);
    const box = f.querySelector(".pw-new");
    f.querySelector("[data-forgot]").onclick = async (e) => {
      e.preventDefault();
      const username = f.username.value.trim();
      if (!username) { errEl.textContent = "Type your username first, then press Forgot password."; f.username.focus(); return; }
      try {
        const r = await post("/api/auth/forgot", { username });
        errEl.textContent = r.at_once
          ? "Your password is now ChangeMe. Sign in with it and choose a new one. Your admins and managers have been told."
          : "Your admins and managers have been told. Once one of them resets it, sign in with ChangeMe and choose a new one.";
      } catch (ex) { errEl.textContent = ex.message; }
    };
    return {
      extra() {
        if (box.classList.contains("hidden")) return {};
        if (f.new_password.value !== f.new_password2.value) throw new Error("The two new passwords are different");
        return { new_password: f.new_password.value };
      },
      handle(ex) {
        if (ex.status !== 428) return false;
        box.classList.remove("hidden");
        errEl.textContent = "This password was set for you, so choose your own to carry on.";
        f.new_password.focus();
        return true;
      },
    };
  }

  // Every match, here and in the parts moved out into windows of their own.
  function qa(root, sel) {
    const all = [...root.querySelectorAll(sel)];
    for (const d of detached) {
      if (d.from !== root && !root.contains(d.from)) continue;
      if (d.el.matches(sel)) all.push(d.el);
      all.push(...d.el.querySelectorAll(sel));
    }
    return [...new Set(all)];
  }

  return { esc, api, post, put, del, upload, socket, toast, guard, fmtTime, stamp, ticker, timerClock, timerDisplay, when, branding, h, detach, q, qa, passwordForm };
})();
