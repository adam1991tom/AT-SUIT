// Pane elements are made by the page (plain HTML and scripts); the desk only
// moves them between windows. A pane that isn't on screen waits in a hidden
// parking spot so its scripts keep working.
import { els } from "./store.svelte.js";

let park = null;
export function parking() {
  if (!park) {
    park = document.createElement("div");
    park.id = "dk-park";
    park.hidden = true;
    document.body.appendChild(park);
  }
  return park;
}

export function host(node, id) {
  let cur = null;
  const attach = (pid) => {
    const el = els[pid];
    if (el && el.parentNode !== node && el.ownerDocument === document) node.appendChild(el);
    cur = pid;
  };
  attach(id);
  return {
    update(pid) {
      if (pid === cur) return;
      const el = els[cur];
      if (el && el.parentNode === node) parking().appendChild(el);
      attach(pid);
    },
    destroy() {
      const el = els[cur];
      // Svelte may be about to hand it to another window: only park what is still here.
      if (el && el.parentNode === node) parking().appendChild(el);
    },
  };
}
