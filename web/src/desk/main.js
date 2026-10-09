// window.ATDesk: the Windows-like workspace. The page registers its parts
// (plain elements) and the desk lays them out as windows that dock, tab,
// float, resize, minimise, maximise and pop out, with a taskbar and saved
// layouts.
//
//   ATDesk.add(id, element, title)        before start()
//   ATDesk.start(host, { key, fallback, shared, canShare, onShare })
//   ATDesk.open(id) / close(id) / available(id, yes) / badge(id, n) / isOpen(id)
//   ATDesk.find(elementId)                 an element here or in a pop-out
import { mount } from "svelte";
import "./desk.css";
import * as T from "./tree.js";
import { desk, els, configure, start as begin, open, close, isOpen } from "./store.svelte.js";
import { parking } from "./host.js";
import { find, popout } from "./popout.js";
import Desktop from "./Desktop.svelte";

let order = 0;

export function add(id, el, title) {
  els[id] = el;
  el.hidden = false; // parts wait hidden in the page until the desk places them
  desk.panes[id] = { title, available: true, badge: 0, popped: false, order: order++ };
  parking().appendChild(el);
}

export function available(id, yes) {
  if (desk.panes[id]) desk.panes[id].available = !!yes;
}

export function badge(id, n) {
  if (desk.panes[id]) desk.panes[id].badge = n || 0;
}

export function title(id, t) {
  if (desk.panes[id]) desk.panes[id].title = t;
}

// fallback: the starting layout, built with the helpers in ATDesk.tree.
export function start(hostEl, o = {}) {
  configure({ key: o.key || "atsuit_desk2", fallback: o.fallback || T.empty() });
  desk.canShare = !!o.canShare;
  begin(o.shared || null);
  return mount(Desktop, { target: hostEl, props: { onShare: o.onShare } });
}

export const layout = () => desk.layout;
export { open, close, isOpen, find, popout, T as tree };
