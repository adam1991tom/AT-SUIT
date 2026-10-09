// The tech workspace's notification centre: the bell list, in-window toasts
// and the app's silent pop-ups. (The windows themselves are static/ui/desk.js.)
const Desk = (() => {
  const { esc, toast, when } = AT;

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

  return { notifier };
})();
