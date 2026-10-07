// Shared helpers for every AT-SUIT page.
const AT = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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
      const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : data.detail.map((d) => d.msg).join(", ")) : r.statusText;
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

  async function guard(fn) {
    try { return await fn(); } catch (e) { toast(e.message, "bad"); throw e; }
  }

  function fmtTime(ms) {
    const neg = ms < 0; ms = Math.abs(ms);
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const body = h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return (neg ? "+" : "") + body;
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

  async function branding() {
    try {
      const b = await api("/api/public/branding");
      document.documentElement.style.setProperty("--accent", b.accent || "#4f7cff");
      document.querySelectorAll("[data-product]").forEach((el) => (el.textContent = b.product_name));
      return b;
    } catch (_) { return {}; }
  }

  function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

  return { esc, api, post, put, del, upload, socket, toast, guard, fmtTime, timerClock, timerDisplay, when, branding, h };
})();
