// The tech workspace's desk: windows that open, close, move and resize over
// the timer, and the notification centre (bell list, in-window toasts and the
// app's silent pop-ups).
const Desk = (() => {
  const { esc, toast, when } = AT;
  const KEY = "atsuit_desk";
  let state = {};
  try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) {}
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (_) {} };
  let top = 20;
  // Windows open and stay below the top bar (it wraps to two rows on smaller screens).
  const below = () => Math.max(56, Math.round(document.querySelector(".top")?.getBoundingClientRect().bottom || 56) + 6);

  // Every <section class="win" data-win="id" data-title="..."> becomes a
  // window with a title bar; `bar` gets a toggle button for each.
  function windows(bar, defaults = {}) {
    const wins = {};
    document.querySelectorAll(".win[data-win]").forEach((w, i) => {
      const id = w.dataset.win;
      const head = document.createElement("div");
      head.className = "win-head";
      head.innerHTML = `<b>${esc(w.dataset.title)}</b><span class="grow"></span><button type="button" class="small" data-pin title="Pin to the board (Layout moves and resizes it)">📌</button><button type="button" class="small" data-x title="Close">✕</button>`;
      w.prepend(head);
      const btn = document.createElement("button");
      btn.type = "button"; btn.className = "small"; btn.dataset.open = id;
      btn.innerHTML = `${esc(w.dataset.title)}<span class="unread hidden"></span>`;
      bar.appendChild(btn);
      const st = (state[id] ||= { open: !!defaults[id], x: null, y: null, w: null, h: null });
      const place = () => {
        if (st.pin && window.Layout) { dock(); return; }
        if (window.Layout && Layout.has("win:" + id)) { Layout.release("win:" + id); document.body.appendChild(w); }
        w.classList.remove("pinned");
        const W = window.innerWidth, H = window.innerHeight, minY = below();
        const ww = Math.min(st.w || +w.dataset.w || 440, W - 16), hh = st.h || null;
        w.style.width = ww + "px";
        w.style.maxHeight = `calc(100vh - ${minY + 12}px)`;
        if (hh) w.style.height = Math.min(hh, H - minY - 12) + "px";
        const x = st.x ?? W - ww - 24 - (i % 4) * 60, y = st.y ?? minY + 20 + (i % 4) * 48;
        w.style.left = Math.max(0, Math.min(x, W - 120)) + "px";
        w.style.top = Math.max(minY, Math.min(y, H - 48)) + "px";
      };
      // A pinned window becomes a tile on the board (layout.js) instead of floating.
      const dock = () => {
        w.classList.add("pinned");
        w.style.width = w.style.height = w.style.left = w.style.top = "";
        if (window.Layout && !Layout.has("win:" + id)) Layout.adopt("win:" + id, w, w.dataset.title);
      };
      const raise = () => { w.style.zIndex = ++top; };
      const show = (open, keep) => {
        st.open = open; if (!keep) save();
        w.classList.toggle("hidden", !open || w.dataset.off === "1");
        btn.classList.toggle("on", open);
        if (open) { place(); raise(); }
      };
      btn.onclick = () => {
        show(!st.open);
        // On a phone the windows sit in the page, so take the tech to the one they opened.
        if (st.open && window.innerWidth < 900 && !st.pin) w.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      head.querySelector("[data-pin]").onclick = () => {
        st.pin = !st.pin; if (st.pin) st.open = true;
        head.querySelector("[data-pin]").classList.toggle("on", !!st.pin); show(st.open);
      };
      head.querySelector("[data-x]").onclick = () => show(false);
      w.addEventListener("pointerdown", raise);
      head.addEventListener("pointerdown", (e) => {
        if (e.target.closest("button") || window.innerWidth < 900 || st.pin) return;
        const r = w.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
        head.setPointerCapture(e.pointerId);
        const move = (m) => { st.x = m.clientX - dx; st.y = m.clientY - dy; place(); };
        head.addEventListener("pointermove", move);
        head.addEventListener("pointerup", () => { head.removeEventListener("pointermove", move); save(); }, { once: true });
      });
      // The corner handle resizes (CSS resize); remember the size it was left at.
      let before = null;
      w.addEventListener("pointerdown", () => { const r = w.getBoundingClientRect(); before = [r.width, r.height]; });
      w.addEventListener("pointerup", () => {
        const r = w.getBoundingClientRect();
        if (before && (Math.abs(r.width - before[0]) > 4 || Math.abs(r.height - before[1]) > 4)) { st.w = Math.round(r.width); st.h = Math.round(r.height); save(); }
      });
      wins[id] = {
        el: w, btn,
        open: () => show(true), close: () => show(false), isOpen: () => st.open,
        // Some windows only apply sometimes (no presenter module, no overlays here).
        available(yes) { w.dataset.off = yes ? "" : "1"; btn.classList.toggle("hidden", !yes); show(st.open, true); },
        badge(n) { const u = btn.querySelector(".unread"); u.textContent = n; u.classList.toggle("hidden", !n); },
      };
      document.addEventListener("atsuit:unpin", (e) => { if (e.detail === id && st.pin) head.querySelector("[data-pin]").click(); });
      head.querySelector("[data-pin]").classList.toggle("on", !!st.pin);
      show(st.open, true);
    });
    window.addEventListener("resize", () => Object.values(wins).forEach((x) => x.isOpen() && !x.el.classList.contains("hidden") && x.open()));
    return wins;
  }

  // Notifications: chat, help calls, the timer, and anything that has gone
  // wrong. `quiet` (the main PC) shows nothing at all.
  function notifier({ button, box, count, quiet, popup }) {
    const list = [];
    let unseen = 0;
    if (quiet) { button.classList.add("hidden"); return { add() {}, quiet: true }; }
    const render = () => {
      count.textContent = unseen; count.classList.toggle("hidden", !unseen);
      box.innerHTML = `<div class="row" style="justify-content:space-between"><b>Notifications</b>${list.length ? '<button class="small" data-clear>Clear</button>' : ""}</div>` +
        (list.map((n, i) => `<div class="note n-${n.kind}" data-i="${i}"><div class="row"><b class="grow">${esc(n.title)}</b><span class="muted small">${when(n.at)}</span></div>${n.body ? `<div class="small">${esc(n.body)}</div>` : ""}</div>`).join("")
          || '<p class="muted small">Nothing yet. Chat, help calls, the timer and problems show here.</p>');
      box.querySelector("[data-clear]")?.addEventListener("click", (e) => { e.stopPropagation(); list.length = 0; render(); });
      box.querySelectorAll("[data-i]").forEach((d) => (d.onclick = () => { const n = list[+d.dataset.i]; box.classList.add("hidden"); n.action && n.action(); }));
    };
    button.onclick = (e) => {
      e.stopPropagation();
      const opening = box.classList.contains("hidden");
      document.querySelectorAll(".dropdown").forEach((d) => d.classList.add("hidden"));
      box.classList.toggle("hidden", !opening);
      if (opening) { unseen = 0; render(); }
    };
    render();
    return {
      // kind: chat | urgent | help | timer | error | good
      add(kind, title, body = "", action = null) {
        list.unshift({ kind, title, body: String(body || "").slice(0, 300), action, at: new Date().toISOString() });
        list.length = Math.min(list.length, 60);
        if (box.classList.contains("hidden")) unseen++;
        render();
        toast(body ? `${title}: ${body}` : title, kind === "good" ? "good" : kind === "chat" ? "" : "bad");
        popup && popup(title, body, kind === "error" ? "urgent" : kind === "timer" ? "info" : kind);
      },
    };
  }

  // Close drop-downs when clicking elsewhere.
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".drop")) document.querySelectorAll(".dropdown").forEach((d) => d.classList.add("hidden"));
  });

  return { windows, notifier };
})();
