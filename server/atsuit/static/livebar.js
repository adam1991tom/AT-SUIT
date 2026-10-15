// The live strip in the middle of the tech workspace's top bar: the time at the venue, this room's
// timer, its screens, captions, the session on now (or next) and the handover notes. Each one is a
// button that opens the window it comes from. It only shows things: nothing here pops up or makes a sound.
const ATLiveBar = (() => {
  const { esc, timerDisplay } = AT;
  const DAY = 86400000;
  const hm = (iso) => (iso && iso.includes("T") ? iso.slice(11, 16) : iso || "");

  function mount(el, { open, modules = {} }) {
    const chip = (key, title, base = "") => `<button type="button" class="lb ${base} hidden" data-lb="${key}" data-base="${base}" title="${esc(title)}"></button>`;
    el.innerHTML = chip("clock", "The time at the venue", "lb-clock") + chip("timer", "This room's timer. Click to open it.") +
      chip("session", "This room's sessions. Click to open them.", "lb-sess") + chip("screens", "The screens in this room. Click to open them.") +
      chip("captions", "Captions for this room. Click to open the controls.", "lb-cap") + chip("notes", "Handover notes for this room. Click to read them.");
    const $ = (k) => el.querySelector(`[data-lb="${k}"]`);
    const show = (k, html, cls, title) => {
      const b = $(k);
      b.innerHTML = html;
      b.className = `lb ${b.dataset.base} ${cls || ""}`.trim();
      if (title) b.title = title;
    };
    $("timer").onclick = () => open("timer");
    $("session").onclick = () => open("sessions");
    $("captions").onclick = () => open("captions");
    $("notes").onclick = () => open("notes");
    let scrCount = 0;
    $("screens").onclick = () => open(scrCount ? "screens" : "pair");

    // The venue's clock: the server sends the time of day at the site with every timer update, so a
    // laptop with the wrong time or time zone still shows the right time. Until then, this laptop's clock.
    let venue = null, tstate = null;
    function clockText() {
      const twelve = venue && venue.fmt === "12";
      let d;
      if (venue) { const ms = ((venue.ms + Date.now() - venue.at) % DAY + DAY) % DAY; d = new Date(2000, 0, 1, 0, 0, 0, ms); }
      else d = new Date();
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: twelve });
    }
    function tick() {
      show("clock", `<b class="clock">${esc(clockText())}</b>`);
      if (!tstate || !modules.timers) return;
      const t = timerDisplay(tstate);
      const pb = tstate.playback;
      const state = pb === "pause" ? "paused" : pb === "armed" ? "ready" : pb === "stop" ? "stopped" : "";
      const flags = [tstate.message_visible && tstate.message ? "message on stage" : "", tstate.blackout ? "blackout" : ""].filter(Boolean);
      const title = tstate.title || tstate.cue?.title || "";
      show("timer", `<span class="lb-k">Timer</span><b class="clock ${t.cls}">${esc(t.text)}</b>` +
        (state ? `<span class="lb-s">${state}</span>` : "") + (title ? `<span class="lb-sub">${esc(title)}</span>` : "") +
        flags.map((f) => `<span class="pill warn lb-flag">${f}</span>`).join(""),
        t.cls === "over" || t.cls === "danger" ? "bad" : t.cls === "warn" ? "warn" : "",
        `This room's timer${title ? ": " + title : ""}${state ? " (" + state + ")" : ""}${flags.length ? ". On stage: " + flags.join(", ") : ""}. Click to open it.`);
    }
    tick();
    setInterval(tick, 250);

    return {
      timer(s) {
        if (!s) return;
        tstate = s;
        if (typeof s.clock_ms === "number") venue = { ms: s.clock_ms, at: Date.now(), fmt: s.time_format };
        tick();
      },
      // The room's Linux screens: how many are connected, and which aren't.
      screens(list) {
        if (!modules.fleet) return;
        scrCount = list.length;
        const on = list.filter((n) => n.online).length, off = list.length - on;
        const names = list.map((n) => `${n.name}: ${n.online ? "connected" : "not connected"}${n.screen_view ? ", showing " + (n.screen_view.startsWith("url:") ? n.screen_view.slice(4) : n.screen_view) : ""}`);
        if (!list.length) show("screens", '<span class="lb-k">Screens</span><span class="lb-s">none yet</span>', "", "No screens in this room yet. Click to add one.");
        else show("screens", `<span class="dot ${off ? "off" : "on"}"></span><span class="lb-k">Screens</span><b>${on}/${list.length}</b>${off ? `<span class="lb-s">${off} offline</span>` : ""}`,
          off ? "bad" : "", `${names.join("\n")}\nClick to open Screens.`);
      },
      captions(live, source) {
        if (!modules.captions) return;
        show("captions", `<span class="dot ${live ? "on" : ""}"></span><span class="lb-k">Captions</span><span>${live ? "live" : "off"}</span>`,
          live ? "good" : "", live ? `Captions are live${source ? " from " + source : ""}. Click to open the controls.` : "Captions are off. Click to open the controls.");
      },
      // The session on now in this room, or else the next one today, and how many of its presenters are here.
      session(now, next) {
        if (!modules.presenter) return;
        const s = now || next;
        if (!s) { $("session").classList.add("hidden"); return; }
        const here = s.presenters.filter((p) => p.checked_in_at).length, all = s.presenters.length;
        const when = now ? (s.ends_at ? `to ${hm(s.ends_at)}` : "now") : `at ${hm(s.starts_at)}`;
        show("session", `<span class="lb-k">${now ? "Now" : "Next"}</span><span class="lb-sub lb-sess">${esc(s.title)}</span><span class="lb-s">${esc(when)}</span>` +
          (all ? `<span class="pill ${here === all ? "good" : ""}">${here}/${all} here</span>` : ""), "",
          `${now ? "On now" : "Next"}: ${s.title}, ${now && s.ends_at ? "until " + hm(s.ends_at) : when}${all ? `. Presenters here: ${here} of ${all}` : ""}. Click to open Sessions.`);
      },
      notes(unseen, total) {
        show("notes", `<span class="lb-k">Notes</span><b>${total}</b>${unseen ? `<span class="pill lb-new">${unseen} new</span>` : ""}`, unseen ? "lit" : "",
          total ? `${total} handover note${total === 1 ? "" : "s"} for this room${unseen ? `, ${unseen} you haven't read` : ""}. Click to read them.` : "No handover notes for this room. Click to leave one.");
      },
    };
  }

  return { mount };
})();
