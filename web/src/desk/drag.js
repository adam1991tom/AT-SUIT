// Dragging windows and tabs: move a floating window, or pull a docked one out
// and drop it on a dock guide (beside another window, as a tab, or along an
// edge of the desk). Dropped anywhere else, it floats where it was let go.
import * as T from "./tree.js";
import { desk, set, focus } from "./store.svelte.js";

const THRESHOLD = 6;

export function startDrag(e, { ids, gid, area }) {
  if (e.button !== 0 || desk.narrow) return;
  const L0 = desk.layout;
  const fl = L0.floats.find((f) => f.group.id === gid);
  const g = T.findGroup(L0, gid);
  const wholeGroup = !!g && ids.length === g.tabs.length;
  const box = area.getBoundingClientRect();
  const winEl = e.currentTarget.closest("[data-gid]");
  const wr = winEl ? winEl.getBoundingClientRect() : { left: e.clientX - 40, top: e.clientY - 10, width: 480, height: 360 };
  const grab = { dx: e.clientX - wr.left, dy: e.clientY - wr.top };
  const size = fl && wholeGroup ? { w: fl.w, h: fl.h } : { w: Math.min(Math.max(wr.width, 320), 640), h: Math.min(Math.max(wr.height, 220), 520) };
  if (!(fl && wholeGroup)) grab.dx = Math.min(grab.dx, size.w - 40);
  const x0 = e.clientX, y0 = e.clientY;
  let moved = false;
  focus(gid);

  const move = (m) => {
    if (!moved && Math.hypot(m.clientX - x0, m.clientY - y0) < THRESHOLD) return;
    moved = true;
    const x = m.clientX - box.left - grab.dx, y = Math.max(0, m.clientY - box.top - grab.dy);
    if (fl && wholeGroup) set(T.moveFloat(desk.layout, gid, { x, y }), false);
    // Which window is under the pointer (not the one being dragged)?
    let over = null, overRect = null;
    for (const el of document.elementsFromPoint(m.clientX, m.clientY)) {
      const guide = el.closest && el.closest("[data-guide]");
      if (guide) { over = desk.drag?.over; overRect = desk.drag?.overRect; break; }
      const w = el.closest && el.closest("[data-gid]");
      if (w && w.dataset.gid !== gid) {
        over = w.dataset.gid;
        const r = w.getBoundingClientRect();
        overRect = { x: r.left - box.left, y: r.top - box.top, w: r.width, h: r.height };
        break;
      }
    }
    const hit = document.elementsFromPoint(m.clientX, m.clientY).find((el) => el.dataset && el.dataset.guide);
    const zone = hit ? hit.dataset.guide : null;
    desk.drag = { ids, gid, over, overRect, zone, preview: preview(zone, overRect, box), ghost: fl && wholeGroup ? null : { x, y, w: size.w, h: size.h } };
  };

  const up = (u) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    const d = desk.drag;
    desk.drag = null;
    if (!moved) return;
    let L = desk.layout;
    if (d && d.zone) {
      const [where, zone] = d.zone.split(":");
      L = T.dock(L, ids, where === "root" ? "root" : d.over, zone);
    } else if (!(fl && wholeGroup)) {
      const x = u.clientX - box.left - grab.dx, y = Math.max(0, u.clientY - box.top - grab.dy);
      L = T.float(L, ids, { x, y, w: size.w, h: size.h });
    }
    set(L);
    const ng = T.groupOf(desk.layout, ids[0]);
    if (ng) focus(ng.id);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

// Where the window would land, drawn as a see-through box.
function preview(zone, r, box) {
  if (!zone) return null;
  const [where, side] = zone.split(":");
  const R = where === "root" ? { x: 0, y: 0, w: box.width, h: box.height } : r;
  if (!R) return null;
  const f = where === "root" ? 0.3 : 0.5;
  if (side === "center") return R;
  if (side === "left") return { ...R, w: R.w * f };
  if (side === "right") return { ...R, x: R.x + R.w * (1 - f), w: R.w * f };
  if (side === "top") return { ...R, h: R.h * f };
  return { ...R, y: R.y + R.h * (1 - f), h: R.h * f };
}

// Resize a floating window from any edge or corner.
export function startResize(e, gid, edge, area) {
  if (e.button !== 0) return;
  e.preventDefault(); e.stopPropagation();
  const f0 = desk.layout.floats.find((f) => f.group.id === gid);
  if (!f0) return;
  const start = { ...f0 }, x0 = e.clientX, y0 = e.clientY;
  const max = area.getBoundingClientRect();
  focus(gid);
  const move = (m) => {
    const dx = m.clientX - x0, dy = m.clientY - y0, r = { x: start.x, y: start.y, w: start.w, h: start.h };
    if (edge.includes("e")) r.w = start.w + dx;
    if (edge.includes("s")) r.h = start.h + dy;
    if (edge.includes("w")) { r.w = start.w - dx; r.x = start.x + dx; }
    if (edge.includes("n")) { r.h = start.h - dy; r.y = start.y + dy; }
    if (r.w < 240) { if (edge.includes("w")) r.x -= 240 - r.w; r.w = 240; }
    if (r.h < 140) { if (edge.includes("n")) r.y -= 140 - r.h; r.h = 140; }
    r.y = Math.max(0, r.y); r.w = Math.min(r.w, max.width); r.h = Math.min(r.h, max.height);
    set(T.moveFloat(desk.layout, gid, r), false);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    set(desk.layout);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

// The bar between two docked windows.
export function startSplit(e, path, i, sizes, visible, container) {
  if (e.button !== 0) return;
  e.preventDefault();
  const dir = container.dataset.dir;
  const r = container.getBoundingClientRect();
  const total = dir === "row" ? r.width : r.height;
  const a = visible[i - 1], b = visible[i];
  const start = [...sizes], p0 = dir === "row" ? e.clientX : e.clientY;
  const sumVisible = visible.reduce((s, k) => s + start[k], 0);
  const move = (m) => {
    const d = ((dir === "row" ? m.clientX : m.clientY) - p0) / total * sumVisible;
    const min = 0.06 * sumVisible, pair = start[a] + start[b];
    const na = Math.max(min, Math.min(pair - min, start[a] + d));
    const next = [...start]; next[a] = na; next[b] = pair - na;
    set(T.resize(desk.layout, path, next), false);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    set(desk.layout);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}
