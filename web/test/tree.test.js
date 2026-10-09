import { test } from "node:test";
import assert from "node:assert/strict";
import * as T from "../src/desk/tree.js";

const base = () => {
  let L = T.empty();
  L = T.dock(L, "timer", "root", "center");
  L = T.dock(L, "cues", T.groupOf(L, "timer").id, "bottom");
  L = T.dock(L, "quick", "root", "right");
  return L;
};

test("docking builds splits and tabs", () => {
  const L = base();
  assert.equal(L.root.t, "split");
  assert.equal(L.root.dir, "row");
  assert.equal(L.root.kids[0].dir, "col");
  assert.deepEqual(T.panes(L), ["timer", "cues", "quick"]);
  const tabbed = T.dock(L, "messages", T.groupOf(L, "quick").id, "center");
  assert.deepEqual(T.groupOf(tabbed, "messages").tabs, ["quick", "messages"]);
  assert.equal(T.groupOf(tabbed, "messages").active, "messages");
  assert.deepEqual(T.panes(L), ["timer", "cues", "quick"], "the input is never changed");
});

test("moving a pane away tidies empty groups and one-child splits", () => {
  let L = base();
  L = T.dock(L, "cues", T.groupOf(L, "quick").id, "center");
  assert.equal(L.root.dir, "row");
  assert.equal(L.root.kids[0].t, "group", "the timer's column collapsed to just the timer");
  assert.deepEqual(L.root.kids[1].tabs, ["quick", "cues"]);
  L = T.close(L, "timer");
  assert.equal(L.root.t, "group");
  assert.deepEqual(L.closed, ["timer"]);
});

test("a row inside a row merges and sizes stay whole", () => {
  let L = base();
  L = T.dock(L, "chat", T.groupOf(L, "quick").id, "right");
  assert.equal(L.root.kids.length, 3);
  assert.ok(Math.abs(L.root.sizes.reduce((a, b) => a + b) - 1) < 1e-9);
  L = T.resize(L, [], [2, 1, 1]);
  assert.deepEqual(L.root.sizes, [0.5, 0.25, 0.25]);
});

test("floating, raising, minimising and maximising", () => {
  let L = T.float(base(), "links", { x: 10, y: 20, w: 300, h: 200 });
  L = T.float(L, "screens", { x: 40, y: 50, w: 300, h: 200 });
  const links = T.groupOf(L, "links").id;
  assert.ok(T.isFloating(L, links));
  L = T.raise(L, links);
  assert.equal(L.floats.at(-1).group.id, links);
  L = T.moveFloat(L, links, { x: 99.6 });
  assert.equal(L.floats.at(-1).x, 100);
  L = T.dock(L, "links", "root", "bottom");
  assert.ok(!T.isFloating(L, T.groupOf(L, "links").id));
  const g = T.groupOf(L, "timer").id;
  L = T.setMax(L, g); assert.equal(L.max, g);
  L = T.setMin(L, g, true); assert.equal(L.max, null);
  assert.equal(T.shown(T.findGroup(L, g), () => true), false);
  L = T.activate(L, "timer");
  assert.equal(T.shown(T.findGroup(L, g), () => true), true);
});

test("a saved layout meets a page with different panes", () => {
  const fallback = (() => {
    let L = base();
    L = T.dock(L, "second", T.groupOf(L, "quick").id, "center");
    L.closed.push("links");
    return L;
  })();
  let saved = T.close(base(), "cues");
  saved = T.dock(saved, "old-thing", "root", "left");
  const L = T.reconcile(saved, ["timer", "cues", "quick", "second", "links", "chat"], fallback);
  assert.ok(!T.panes(L).includes("old-thing"), "unknown panes go");
  assert.ok(L.closed.includes("cues"), "what the tech closed stays closed");
  assert.deepEqual(T.groupOf(L, "second").tabs, ["quick", "second"], "new panes join their default neighbours");
  assert.ok(L.closed.includes("links"));
  assert.ok(L.closed.includes("chat"), "a pane with no default place starts closed");
});
