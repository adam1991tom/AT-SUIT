// Pop-outs: a pane moves into a window of its own (a real window in the
// AT-SUIT app, a browser window elsewhere) that can go on another monitor.
// The pane keeps running exactly as it was; closing the window, or "Back to
// the workspace", puts it back where it came from.
import { desk, els, open } from "./store.svelte.js";
import { parking } from "./host.js";

const pops = {}; // pane id -> window
const APP = typeof window !== "undefined" ? window.atsuitApp : null;

export const popped = (id) => !!pops[id] && !pops[id].closed;

function copyLooks(doc) {
  const html = document.documentElement;
  for (const a of html.attributes) doc.documentElement.setAttribute(a.name, a.value);
  doc.documentElement.style.cssText = html.style.cssText;
  document.querySelectorAll('link[rel="stylesheet"], style').forEach((n) => {
    const c = doc.importNode(n, true);
    if (n.tagName === "LINK") c.href = n.href; // absolute: about:blank has no base of its own
    doc.head.appendChild(c);
  });
}

export function popout(id) {
  const el = els[id], p = desk.panes[id];
  if (!el || !p) return false;
  if (popped(id)) { pops[id].focus(); return true; }
  const r = el.getBoundingClientRect();
  const w = Math.max(360, Math.round(r.width) + 24), h = Math.max(260, Math.round(r.height) + 70);
  const win = window.open("", `atsuit-pop-${id}`, `popup=yes,width=${w},height=${h}`);
  if (!win) return false;
  const doc = win.document;
  doc.open();
  doc.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body class="dk-popbody"></body></html>');
  doc.close();
  doc.title = `${p.title} · AT-SUIT`;
  copyLooks(doc);
  const bar = doc.createElement("div");
  bar.className = "dk-popbar";
  bar.innerHTML = `<b></b><span class="grow"></span>${APP && APP.popoutOnTop ? '<label class="small"><input type="checkbox" data-top> Keep on top</label>' : ""}<button type="button" class="small" data-back title="Put it back in the workspace">Back to the workspace</button>`;
  bar.querySelector("b").textContent = p.title;
  const body = doc.createElement("div");
  body.className = "dk-popmain";
  doc.body.append(bar, body);
  body.appendChild(el); // moves the live element, listeners and all
  pops[id] = win;
  p.popped = true;
  const top = bar.querySelector("[data-top]");
  if (top) top.onchange = () => APP.popoutOnTop(`atsuit-pop-${id}`, top.checked);
  bar.querySelector("[data-back]").onclick = () => win.close();
  let home = false;
  const back = () => {
    if (home) return;
    home = true;
    delete pops[id];
    parking().appendChild(el);
    p.popped = false;
    open(id);
  };
  win.addEventListener("pagehide", back);
  win.addEventListener("beforeunload", back);
  // A window closed by the system (or the app) doesn't always say so.
  const watch = setInterval(() => { if (win.closed) { clearInterval(watch); back(); } }, 1000);
  return true;
}

export function focusPop(id) { if (popped(id)) pops[id].focus(); }

// Find an element by id in the workspace or in any pop-out.
export function find(id) {
  const here = document.getElementById(id);
  if (here) return here;
  for (const w of Object.values(pops)) {
    if (w.closed) continue;
    const el = w.document.getElementById(id);
    if (el) return el;
  }
  return null;
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => Object.values(pops).forEach((w) => { try { w.close(); } catch (_) {} }));
}
