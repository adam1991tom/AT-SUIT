// Shared helpers for every AT-SUITE page.

// Passwords, tokens, keys and codes never sit in the address bar: a form is only
// ever sent by script (never by the browser itself, which would put what's typed
// in the address), and any such value that arrives in an address is wiped from it.
(() => {
  if (window.__atNoSecretsInUrl) return;
  window.__atNoSecretsInUrl = true;
  document.addEventListener("submit", (e) => e.preventDefault(), true);
  const SECRET = /^(password|new_password2?|pass|passwd|pw|pin|username|operator|token|node_token|access_token|ticket|secret|key|api_key|apikey|code|enrol_code|sync_code|setup_code|licence_key|admin_password)$/i;
  try {
    const q = new URLSearchParams(location.search);
    const hits = [...q.keys()].filter((k) => SECRET.test(k));
    if (hits.length) {
      hits.forEach((k) => q.delete(k));
      history.replaceState(history.state, "", location.pathname + (q.size ? "?" + q : "") + location.hash);
    }
  } catch (_) {}
})();

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
  // The accent as text that stays readable (WCAG 1.4.3, 4.5:1) whatever accent an admin picks:
  // lightened until it reads on the dark themes' panels, darkened until it reads on the light ones.
  function lum(hex) {
    const n = parseInt(hex.slice(1), 16);
    const c = [n >> 16, (n >> 8) & 255, n & 255].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  function readable(hex, bg, towards) {
    if (!/^#[0-9a-f]{6}$/i.test(hex || "")) return null;
    const n = parseInt(hex.slice(1), 16), from = [n >> 16, (n >> 8) & 255, n & 255], to = towards === "white" ? 255 : 0;
    for (let t = 0; t <= 1.0001; t += 0.05) {
      const c = "#" + from.map((v) => Math.round(v + (to - v) * t).toString(16).padStart(2, "0")).join("");
      if (contrast(c, bg) >= 4.6) return c;
    }
    return towards === "white" ? "#ffffff" : "#000000";
  }
  async function branding() {
    try {
      const b = await api("/api/public/branding");
      document.documentElement.style.setProperty("--accent", b.accent || "#FF7A1A");
      document.documentElement.style.setProperty("--accent-ink", ink(b.accent || "#FF7A1A"));
      document.documentElement.style.setProperty("--accent-on-dark", readable(b.accent || "#FF7A1A", "#2a313c", "white"));
      document.documentElement.style.setProperty("--accent-on-light", readable(b.accent || "#FF7A1A", "#eff2f5", "black"));
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

// Accessibility (WCAG 2.2 AA) for every page that loads this file, including parts drawn later:
// - each field is tied to the label written just before it, or named from its table column,
//   placeholder or tooltip, so a screen reader says what it is;
// - status dots say what they mean (they also differ in shape, see app.css), not only in colour;
// - pop-up notes are read out.
(() => {
  if (window.__atA11y) return;
  window.__atA11y = true;
  const FIELD = "input:not([type=hidden]), select, textarea";
  let seq = 0;
  const id = (el) => el.id || (el.id = `at-f${++seq}`);
  const named = (el) => el.labels?.length || el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby");
  const text = (el) => (el?.textContent || "").replace(/\s+/g, " ").trim();

  function nameField(el) {
    if (named(el)) return;
    // <label>Organisation</label><input>: the label written just before the field
    const prev = el.previousElementSibling;
    if (prev && prev.tagName === "LABEL" && !prev.htmlFor && !prev.querySelector(FIELD)) { prev.htmlFor = id(el); return; }
    // a field in a table: its column's heading
    const cell = el.closest("td");
    if (cell) {
      const table = cell.closest("table");
      const head = table?.querySelector("thead tr, tr:first-child");
      const th = head?.children[cell.cellIndex];
      const t = th && th.tagName === "TH" ? text(th) : "";
      if (t) { el.setAttribute("aria-label", t); return; }
    }
    const hint = el.getAttribute("placeholder") || el.getAttribute("title") || el.dataset.label;
    if (hint) { el.setAttribute("aria-label", hint); return; }
    if (el.type === "file") el.setAttribute("aria-label", "Choose a file");
  }

  const DOT = { on: "Online", off: "Offline" };
  const PRE = { good: "Ready", warn: "Wants a look", bad: "Not working", off: "Off" };
  function nameDot(d) {
    const pre = d.closest(".pre");
    const state = pre ? Object.keys(PRE).find((k) => pre.classList.contains(k)) : Object.keys(DOT).find((k) => d.classList.contains(k));
    const label = pre ? PRE[state] : DOT[state] || "Off";
    if (d.getAttribute("aria-label") !== label) { d.setAttribute("role", "img"); d.setAttribute("aria-label", label); }
  }

  function fix(root) {
    if (root.nodeType !== 1) return;
    if (root.matches(FIELD)) nameField(root);
    if (root.matches(".dot")) nameDot(root);
    if (root.matches(".pre")) root.querySelectorAll(".dot").forEach(nameDot);
    root.querySelectorAll(FIELD).forEach(nameField);
    root.querySelectorAll(".dot").forEach(nameDot);
    const toasts = root.id === "toasts" ? root : root.querySelector("#toasts");
    if (toasts && !toasts.hasAttribute("role")) { toasts.setAttribute("role", "status"); toasts.setAttribute("aria-live", "polite"); }
  }

  // Only what was added or changed is looked at, so a ticking timer costs nothing here.
  const start = () => {
    fix(document.body);
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "attributes") fix(m.target);
        else m.addedNodes.forEach(fix);
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
