// The desk's live state: the layout (tree.js) plus what each pane is right now.
import * as T from "./tree.js";

export const desk = $state({
  layout: T.empty(),
  panes: {}, // id -> { title, available, badge, popped, order }
  focus: null, // group id
  narrow: false,
  drag: null, // { ids, from, x, y, target, zone, rect }
  layouts: [], // names of saved layouts on this laptop
  current: "", // name of the saved layout in use, if any
  menu: false,
  canShare: false,
});

export const els = {}; // pane id -> its element (kept out of $state: DOM nodes)
let opts = { key: "atsuit_desk2", fallback: T.empty(), shared: null };

const read = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (_) { return d; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };

export function configure(o) { opts = { ...opts, ...o }; }

export const can = (id) => {
  const p = desk.panes[id];
  return !!p && p.available && !p.popped;
};

export function known() {
  return Object.keys(desk.panes).sort((a, b) => desk.panes[a].order - desk.panes[b].order);
}

export function set(L, save = true) {
  desk.layout = L;
  if (save) write(opts.key, L);
}

export function start(sharedDefault) {
  if (sharedDefault && sharedDefault.v === 1) opts.fallback = T.reconcile(sharedDefault, known(), opts.fallback);
  desk.layouts = Object.keys(read(opts.key + ":named", {}));
  desk.current = read(opts.key + ":current", "");
  set(T.reconcile(read(opts.key, null), known(), opts.fallback), false);
}

export function fallback() { return opts.fallback; }

// ----------------------------------------------------------- actions --
export function focus(gid) {
  desk.focus = gid;
  if (T.isFloating(desk.layout, gid)) set(T.raise(desk.layout, gid));
}

export function open(id, rect) {
  const L = desk.layout, g = T.groupOf(L, id);
  if (g) { set(T.activate(L, id)); focus(g.id); return; }
  // Closed: back as a window of its own, near the middle of the desk.
  const r = rect || defaultRect(Object.keys(desk.panes).indexOf(id));
  set(T.float(L, id, r));
  focus(T.groupOf(desk.layout, id).id);
}

export function defaultRect(i = 0) {
  const W = (typeof innerWidth === "number" ? innerWidth : 1280), H = (typeof innerHeight === "number" ? innerHeight : 800);
  const w = Math.min(560, W - 40), h = Math.min(480, H - 160);
  return { x: Math.max(10, W - w - 40 - (i % 5) * 36), y: 20 + (i % 5) * 36, w, h };
}

export function close(id) { set(T.close(desk.layout, id)); }
export function isOpen(id) { const g = T.groupOf(desk.layout, id); return !!g && !g.min && g.active === id; }

// Taskbar click, Windows style: open it, bring it forward, or minimise it.
export function toggle(id) {
  const g = T.groupOf(desk.layout, id);
  if (!g) return open(id);
  if (g.min || g.active !== id || desk.focus !== g.id) { set(T.activate(desk.layout, id)); focus(g.id); return; }
  set(T.setMin(desk.layout, g.id, true));
}

// ---------------------------------------------------------- layouts --
export function saveAs(name) {
  const named = read(opts.key + ":named", {});
  named[name] = desk.layout;
  write(opts.key + ":named", named);
  desk.layouts = Object.keys(named);
  desk.current = name; write(opts.key + ":current", name);
}

export function load(name) {
  const named = read(opts.key + ":named", {});
  if (!named[name]) return;
  set(T.reconcile(named[name], known(), opts.fallback));
  desk.current = name; write(opts.key + ":current", name);
}

export function forget(name) {
  const named = read(opts.key + ":named", {});
  delete named[name];
  write(opts.key + ":named", named);
  desk.layouts = Object.keys(named);
  if (desk.current === name) { desk.current = ""; write(opts.key + ":current", ""); }
}

export function reset() {
  set(T.reconcile(null, known(), opts.fallback));
  desk.current = ""; write(opts.key + ":current", "");
}
