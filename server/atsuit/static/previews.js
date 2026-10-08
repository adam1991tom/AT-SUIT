// Previews: live thumbnails of what the system is putting out for this room
// (timer views, caption screens, and what each Linux screen is showing). A
// tech picks the ones they want to keep an eye on and they stay in the window.
const Previews = (() => {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function urlFor(roomId, view) {
    if (!view) return "";
    if (view.startsWith("screentest:")) return `/screentest?p=${encodeURIComponent(view.slice(11))}`;
    if (view.startsWith("url:")) return view.slice(4);
    if (view.startsWith("view:")) return `/room/${roomId}/external/${encodeURIComponent(view.slice(5))}/`;
    if (view === "captions") return `/captions/${roomId}`;
    return `/timer/${roomId}?view=${encodeURIComponent(view)}`;
  }

  function mount(el, { roomId }) {
    const KEY = `atsuit_previews_${roomId}`;
    let pinned = [], views = [], screens = [];
    try { pinned = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (_) {}
    const save = () => { try { localStorage.setItem(KEY, JSON.stringify(pinned)); } catch (_) {} };

    const outputs = () => [
      ...views.filter((v) => !v.test).map((v) => ({ key: "view:" + v.id, label: v.name, url: urlFor(roomId, v.id), group: "Timer views" })),
      { key: "cap:screen", label: "Captions screen", url: `/captions/${roomId}`, group: "Captions" },
      { key: "cap:overlay", label: "Captions overlay", url: `/captions/${roomId}/overlay`, group: "Captions" },
      ...screens.map((n) => ({ key: "scr:" + n.id, label: n.name, sub: n.screen_view ? "showing " + (n.screen_view.startsWith("url:") ? n.screen_view.slice(4) : n.screen_view) : "nothing yet", url: urlFor(n.room_id || roomId, n.screen_view), group: "Linux screens" })),
    ];

    function fit() {
      el.querySelectorAll(".pv-frame").forEach((f) => {
        const k = f.clientWidth / 1280;
        f.firstElementChild.style.transform = `scale(${k})`;
        f.style.height = Math.round(720 * k) + "px";
      });
    }
    function draw() {
      const all = outputs(), byKey = Object.fromEntries(all.map((o) => [o.key, o]));
      const tiles = pinned.map((k) => byKey[k]).filter(Boolean);
      const unpinned = all.filter((o) => !pinned.includes(o.key));
      el.innerHTML = `<div class="row" style="margin-bottom:.5rem"><select id="pvPick" class="grow"><option value="">Pin a screen to watch…</option>` +
        [...new Set(unpinned.map((o) => o.group))].map((g) => `<optgroup label="${esc(g)}">${unpinned.filter((o) => o.group === g).map((o) => `<option value="${esc(o.key)}">${esc(o.label)}</option>`).join("")}</optgroup>`).join("") +
        `</select></div>` +
        (tiles.length ? `<div class="pv-grid">${tiles.map((o) => `<figure class="pv" data-k="${esc(o.key)}"><div class="pv-frame">${o.url ? `<iframe src="${esc(o.url)}" tabindex="-1" loading="lazy"></iframe>` : '<div class="pv-none">Nothing on this screen yet</div>'}</div>
          <figcaption><span class="grow"><b>${esc(o.label)}</b>${o.sub ? ` <span class="muted small">${esc(o.sub)}</span>` : ""}</span><button class="small" data-big title="Bigger">⤢</button><button class="small" data-un title="Unpin">✕</button></figcaption></figure>`).join("")}</div>`
          : '<p class="muted small">Pick a screen above and it stays here, live, so you can see what the room is seeing.</p>');
      el.querySelector("#pvPick").onchange = (e) => { if (e.target.value) { pinned.push(e.target.value); save(); draw(); } };
      el.querySelectorAll(".pv").forEach((f) => {
        const k = f.dataset.k;
        f.querySelector("[data-un]").onclick = () => { pinned = pinned.filter((x) => x !== k); save(); draw(); };
        f.querySelector("[data-big]").onclick = () => big(byKey[k]);
      });
      fit();
    }
    function big(o) {
      if (!o || !o.url) return;
      const d = document.createElement("div");
      d.className = "pv-big";
      d.innerHTML = `<div class="pv-bigbox"><div class="row" style="margin-bottom:.4rem"><b class="grow">${esc(o.label)}</b><a class="small" href="${esc(o.url)}" target="_blank" rel="noopener">Open in a tab</a><button class="small" data-c>Close</button></div><div class="pv-bigframe"><iframe src="${esc(o.url)}"></iframe></div></div>`;
      d.onclick = (e) => { if (e.target === d || e.target.closest("[data-c]")) d.remove(); };
      document.body.appendChild(d);
    }
    new ResizeObserver(fit).observe(el);
    draw();
    return {
      update(next) { if (next.views) views = next.views; if (next.screens) screens = next.screens; draw(); },
      count: () => pinned.length,
    };
  }
  return { mount, urlFor };
})();
