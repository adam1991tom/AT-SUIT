// The AT-SUIT status bar for imported (uploaded) views, like the one on the
// built-in views: time now, timer state, cue, started, elapsed, expected end,
// and the room name. Served with every imported view next to ontime-shim.js;
// it only draws when switched on for the view in Admin → Timers, or with
// ?status=1 in the address. It sits in a shadow root, over the view's bottom edge.
(() => {
  const m = location.pathname.match(/^\/room\/(\d+)\/external\/([a-z0-9-]+)\//);
  if (!m) return;
  const [, room, slug] = m;
  const q = new URLSearchParams(location.search);
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (ms) => {
    const a = Math.abs(ms), h = Math.floor(a / 3600000), mi = Math.floor(a % 3600000 / 60000), s = Math.floor(a % 60000 / 1000);
    return (ms < 0 ? "-" : "") + (h ? `${h}:${pad(mi)}:${pad(s)}` : `${mi}:${pad(s)}`);
  };

  async function start() {
    let on = false;
    try { const look = await (await fetch(`/api/timers-views/look/view:${slug}`)).json(); on = !!(look.options && look.options.status_bar); } catch (_) {}
    if (q.has("status")) on = !["0", "false", "off", "no"].includes(q.get("status").toLowerCase());
    if (!on) return;
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:400;pointer-events:none";
    document.body.append(host);
    const root = host.attachShadow({ mode: "open" });
    const bg = getComputedStyle(document.body).backgroundColor || "#000";
    const dark = (() => { const c = bg.match(/\d+/g) || [0, 0, 0]; return (+c[0] * 299 + +c[1] * 587 + +c[2] * 114) / 1000 < 128; })();
    const fg = dark ? "#ffffff" : "#1a1a1a";
    root.innerHTML = `<style>
      .b { display: flex; align-items: center; gap: 2.6vw; height: clamp(38px, 5.4vh, 76px); padding: 0 1.4vw; box-sizing: border-box;
        font-family: Arial, "Helvetica Neue", Helvetica, sans-serif; font-variant-numeric: tabular-nums;
        background: color-mix(in srgb, ${fg} 4%, ${bg}); border-top: 1px solid color-mix(in srgb, ${fg} 12%, ${bg}); }
      .room { color: color-mix(in srgb, ${fg} 16%, ${bg}); font-size: clamp(10px, .85vw, 18px); font-weight: 600; letter-spacing: .04em; }
      .it { display: flex; flex-direction: column; min-width: 0; line-height: 1.15; }
      .it b { font-size: clamp(9px, .72vw, 15px); letter-spacing: .09em; text-transform: uppercase; color: color-mix(in srgb, ${fg} 42%, ${bg}); }
      .it span { font-size: clamp(14px, 1.45vw, 30px); font-weight: 700; color: color-mix(in srgb, ${fg} 78%, ${bg}); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .grow { flex: 1; } .over span { color: #ff0000; }
    </style><div class="b"><span class="room"></span>
      <div class="it" data-k="tod"><b>Time now</b><span></span></div><div class="it" data-k="state"><b>Timer</b><span></span></div>
      <div class="it grow" data-k="cue"><b>Now</b><span></span></div><div class="it" data-k="start"><b>Started</b><span></span></div>
      <div class="it" data-k="elapsed"><b>Elapsed</b><span></span></div><div class="it" data-k="end"><b>Expected end</b><span></span></div></div>`;
    const set = (k, v, cls) => { const it = root.querySelector(`[data-k="${k}"]`); it.lastChild.textContent = v; if (cls !== undefined) it.classList.toggle("over", cls === "over"); };
    let s = null;
    const load = async () => { try { const x = await (await fetch(`/api/timers/${room}`)).json(); x._rx = Date.now(); s = x; } catch (_) {} };
    const hm = (ms, secs) => {
      const t = Math.floor(((ms % 86400000) + 86400000) % 86400000 / 1000), h = Math.floor(t / 3600), mi = Math.floor(t % 3600 / 60);
      const tail = secs ? `:${pad(t % 60)}` : "";
      return s.time_format === "12" ? `${h % 12 || 12}:${pad(mi)}${tail} ${h >= 12 ? "PM" : "AM"}` : `${pad(h)}:${pad(mi)}${tail}`;
    };
    const draw = () => {
      if (!s) return;
      root.querySelector(".room").textContent = s.room;
      const now = s.clock_ms + Date.now() - s._rx;
      const left = s.running ? s.remaining_ms - (Date.now() - s._rx) : s.remaining_ms;
      const total = s.duration_ms + (s.added_ms || 0), pb = s.playback;
      set("tod", hm(now, true));
      const over = pb !== "stop" && s.timer_type === "count-down" && left < 0;
      set("state", s.show_clock ? "Clock" : over ? "Overtime" : { play: "Running", pause: "Paused", armed: "Ready" }[pb] || "Stopped", over ? "over" : "");
      set("cue", ((s.cue && s.cue.cue ? s.cue.cue + " · " : "") + (s.title || "")) || (s.cue_count ? "—" : "No cue loaded"));
      const ago = s.started_at ? (s.server_time - s.started_at) * 1000 + (Date.now() - s._rx) : null;
      set("start", ago != null ? hm(now - ago) : "—");
      set("elapsed", s.started_at && pb !== "stop" ? fmt(Math.max(0, total - left)) : "—");
      set("end", pb === "play" ? hm(now + left) : pb === "pause" || pb === "armed" ? `${fmt(Math.max(0, left))} left` : "—");
    };
    // Sit just above a progress bar fixed along the view's bottom edge (like BDNG's), if it has one.
    const lift = () => {
      let h = 0;
      for (const e of document.body.querySelectorAll("*")) {
        if (e === host) continue;
        const cs = getComputedStyle(e);
        if (cs.position !== "fixed" || cs.display === "none") continue;
        const r = e.getBoundingClientRect();
        if (r.height > 0 && r.height <= 80 && r.width >= innerWidth * 0.5 && Math.abs(innerHeight - r.bottom) < 2) h = Math.max(h, r.height);
      }
      host.style.bottom = h + "px";
    };
    lift(); addEventListener("resize", lift); setInterval(lift, 5000);
    await load();
    setInterval(load, 2000);
    setInterval(draw, 250);
    draw();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
