// The workspace layout as plain data, so it can be saved, shared and tested.
//
// Layout = { v: 1, root: Node | null, floats: [Float], closed: [paneId], max: groupId | null, seq }
// Node   = { t: "split", dir: "row" | "col", sizes: [fraction], kids: [Node] }
//        | { t: "group", id: "g1", tabs: [paneId], active: paneId, min?: true }
// Float  = { group: Group, x, y, w, h }   (pixels inside the desk area)
//
// A "group" is one window: a title bar with one tab per pane. Docked groups
// tile the desk; floating ones sit over it. Every function here returns a new
// layout and never changes the one it was given.

export const ZONES = ["left", "right", "top", "bottom", "center"];
const clone = (x) => JSON.parse(JSON.stringify(x));

export function empty() {
  return { v: 1, root: null, floats: [], closed: [], max: null, seq: 0 };
}

function newGroup(L, tabs) {
  L.seq = (L.seq || 0) + 1;
  return { t: "group", id: `g${L.seq}`, tabs: [...tabs], active: tabs[0] };
}

// Every group, docked first (in reading order), then floating.
export function groups(L) {
  const out = [];
  const walk = (n) => { if (!n) return; if (n.t === "group") out.push(n); else n.kids.forEach(walk); };
  walk(L.root);
  L.floats.forEach((f) => out.push(f.group));
  return out;
}

export function panes(L) {
  return groups(L).flatMap((g) => g.tabs);
}

export function groupOf(L, pane) {
  return groups(L).find((g) => g.tabs.includes(pane)) || null;
}

export function findGroup(L, id) {
  return groups(L).find((g) => g.id === id) || null;
}

export function isFloating(L, id) {
  return L.floats.some((f) => f.group.id === id);
}

// Tidy a tree: no empty groups, no splits with one child, no row inside a row.
export function normalize(n) {
  if (!n) return null;
  if (n.t === "group") {
    if (!n.tabs.length) return null;
    if (!n.tabs.includes(n.active)) n.active = n.tabs[0];
    return n;
  }
  const kids = [], sizes = [];
  n.kids.forEach((k, i) => {
    const s = n.sizes[i] ?? 1 / n.kids.length;
    const c = normalize(k);
    if (!c) return;
    if (c.t === "split" && c.dir === n.dir) c.kids.forEach((cc, j) => { kids.push(cc); sizes.push(s * c.sizes[j]); });
    else { kids.push(c); sizes.push(s); }
  });
  if (!kids.length) return null;
  if (kids.length === 1) return kids[0];
  const total = sizes.reduce((a, b) => a + b, 0) || 1;
  return { t: "split", dir: n.dir, kids, sizes: sizes.map((s) => s / total) };
}

function tidy(L) {
  L.root = normalize(L.root);
  L.floats = L.floats.filter((f) => normalize(f.group));
  if (L.max && !findGroup(L, L.max)) L.max = null;
  return L;
}

// Take panes out of wherever they are.
export function remove(L0, ids) {
  const L = clone(L0), drop = new Set([].concat(ids));
  groups(L).forEach((g) => { g.tabs = g.tabs.filter((p) => !drop.has(p)); });
  L.closed = L.closed.filter((p) => !drop.has(p));
  return tidy(L);
}

// Put panes next to a docked or floating group ("left", "right", "top",
// "bottom") or into it as tabs ("center"). target "root" docks along the edge
// of the whole desk.
export function dock(L0, ids, target, zone) {
  ids = [].concat(ids);
  let L = remove(L0, ids);
  if (!ids.length) return L;
  if (target === "root" || !L.root) {
    const g = newGroup(L, ids);
    if (!L.root || zone === "center") { L.root = L.root ? wrap(L.root, g, "right", 1 / 3) : g; return tidy(L); }
    L.root = wrap(L.root, g, zone, 1 / 3);
    return tidy(L);
  }
  const tg = findGroup(L, target);
  if (!tg) return dock(L, ids, "root", zone === "center" ? "right" : zone);
  if (zone === "center") {
    tg.tabs.push(...ids); tg.active = ids[0];
    return tidy(L);
  }
  const g = newGroup(L, ids);
  const fl = L.floats.find((f) => f.group.id === target);
  if (fl) { fl.group = wrapFloat(L, fl, g, zone); return tidy(L); }
  L.root = replace(L.root, target, (n) => wrap(n, g, zone));
  return tidy(L);
}

// A floating window holds one group; docking beside it turns the float into
// a docked split is too surprising, so the new pane joins it as a tab instead.
function wrapFloat(L, fl, g, _zone) {
  fl.group.tabs.push(...g.tabs); fl.group.active = g.tabs[0];
  return fl.group;
}

// Beside one window the two share the space; along an edge of the whole desk
// the newcomer takes a third.
function wrap(node, g, zone, share = 0.5) {
  const dir = zone === "left" || zone === "right" ? "row" : "col";
  const first = zone === "left" || zone === "top";
  return { t: "split", dir, sizes: first ? [share, 1 - share] : [1 - share, share], kids: first ? [g, node] : [node, g] };
}

function replace(n, id, fn) {
  if (!n) return n;
  if (n.t === "group") return n.id === id ? fn(n) : n;
  return { ...n, kids: n.kids.map((k) => replace(k, id, fn)) };
}

export function float(L0, ids, rect) {
  ids = [].concat(ids);
  const L = remove(L0, ids);
  const g = newGroup(L, ids);
  L.floats.push({ group: g, x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.w), h: Math.round(rect.h) });
  return L;
}

export function moveFloat(L0, id, rect) {
  const L = clone(L0);
  const f = L.floats.find((x) => x.group.id === id);
  if (f) Object.assign(f, { x: Math.round(rect.x ?? f.x), y: Math.round(rect.y ?? f.y), w: Math.round(rect.w ?? f.w), h: Math.round(rect.h ?? f.h) });
  return L;
}

// Bring a floating window to the front (last drawn is on top).
export function raise(L0, id) {
  const i = L0.floats.findIndex((f) => f.group.id === id);
  if (i < 0 || i === L0.floats.length - 1) return L0;
  const L = clone(L0);
  L.floats.push(L.floats.splice(i, 1)[0]);
  return L;
}

export function close(L0, pane) {
  const L = remove(L0, pane);
  L.closed.push(pane);
  return L;
}

export function activate(L0, pane) {
  const L = clone(L0);
  const g = groupOf(L, pane);
  if (g) { g.active = pane; delete g.min; }
  return L;
}

export function setMin(L0, id, min) {
  const L = clone(L0);
  const g = findGroup(L, id);
  if (g) { if (min) g.min = true; else delete g.min; }
  if (min && L.max === id) L.max = null;
  return L;
}

export function setMax(L0, id) {
  const L = clone(L0);
  L.max = L.max === id ? null : id;
  const g = findGroup(L, id);
  if (g) delete g.min;
  return L;
}

// Splitter drags: sizes of the split at `path` (indexes from the root).
export function resize(L0, path, sizes) {
  const L = clone(L0);
  let n = L.root;
  for (const i of path) n = n.kids[i];
  if (n && n.t === "split" && sizes.length === n.kids.length) {
    const total = sizes.reduce((a, b) => a + b, 0) || 1;
    n.sizes = sizes.map((s) => s / total);
  }
  return L;
}

// Make a saved layout fit the panes this page has: drop panes it doesn't
// know, and put new ones where the default layout has them.
export function reconcile(L0, known, fallback) {
  let L = L0 && L0.v === 1 ? clone(L0) : clone(fallback);
  const have = new Set(known);
  const gone = panes(L).filter((p) => !have.has(p));
  if (gone.length) L = remove(L, gone);
  L.closed = L.closed.filter((p) => have.has(p));
  const placed = new Set([...panes(L), ...L.closed]);
  for (const p of known) {
    if (placed.has(p)) continue;
    const near = groupOf(fallback, p);
    const buddy = near && near.tabs.find((x) => x !== p && groupOf(L, x));
    if (buddy) L = dock(L, p, groupOf(L, buddy).id, "center");
    else if (fallback.closed.includes(p) || !near) L.closed.push(p);
    else L = dock(L, p, "root", "right");
    placed.add(p);
  }
  return tidy(L);
}

// Is this group drawn? Not when minimised or when none of its panes can show.
export function shown(g, can) {
  return !g.min && g.tabs.some(can);
}

export function nodeShown(n, can) {
  if (!n) return false;
  return n.t === "group" ? shown(n, can) : n.kids.some((k) => nodeShown(k, can));
}
