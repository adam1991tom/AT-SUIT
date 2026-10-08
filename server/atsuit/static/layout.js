// The tech workspace's board: every part (the timer, quick timers, messages,
// the second line, the cue list, captions, add a screen, sessions, and any
// window pinned from the top bar) is a tile on a 12-column grid. "Edit
// layout" lets the tech drag tiles into a new order, make them wider,
// narrower, taller or shorter (buttons or the corner handle), hide them and
// bring them back. The layout is kept per browser, so each laptop keeps its own.
var Layout = (() => { // var: desk.js looks for window.Layout
  const KEY = "atsuit_layout_v1";
  const COLS = 12, ROW = 40; // px per height step; height 0 = as tall as the content
  // Ready-made layouts. Anything not listed keeps its own default.
  const PRESETS = {
    standard: { label: "Standard", tiles: {
      timer: [5, 0], quick: [3, 0], messages: [4, 0], second: [4, 0], pair: [4, 0], captions: [4, 0], sessions: [4, 0], cues: [8, 0] },
      order: ["timer", "quick", "messages", "cues", "second", "pair", "captions", "sessions"] },
    compact: { label: "Compact timer", tiles: {
      timer: [4, 0], quick: [4, 0], messages: [4, 0], second: [4, 0], pair: [4, 0], captions: [4, 0], sessions: [4, 0], cues: [8, 0] },
      order: ["timer", "quick", "messages", "captions", "second", "pair", "cues", "sessions"] },
    big: { label: "Big timer", tiles: {
      timer: [12, 0], quick: [6, 0], messages: [6, 0], second: [4, 0], pair: [4, 0], captions: [4, 0], sessions: [4, 0], cues: [12, 0] },
      order: ["timer", "quick", "messages", "second", "pair", "captions", "cues", "sessions"] },
  };
  let state = { tiles: {}, order: [] };
  try { state = { ...state, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (_) {}
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (_) {} };
  let board = null, editing = false;
  const tiles = new Map(); // id -> {el, title, def:[w,h], optional}

  const st = (id) => (state.tiles[id] ||= {});
  const size = (id) => { const t = tiles.get(id), s = st(id); return [s.w ?? t.def[0], s.h ?? t.def[1]]; };

  function apply(id) {
    const t = tiles.get(id); if (!t) return;
    const [w, h] = size(id), s = st(id);
    t.el.style.gridColumn = `span ${Math.max(2, Math.min(COLS, w))}`;
    t.el.style.gridRow = h ? `span ${h}` : `span ${fitRows(t.el)}`;
    t.el.classList.toggle("tile-fixed", !!h);
    t.el.classList.toggle("tile-off", !!s.hidden);
    const i = state.order.indexOf(id);
    t.el.style.order = i < 0 ? 100 + [...tiles.keys()].indexOf(id) : i;
    t.el.dataset.w = w;
    const lab = t.el.querySelector(":scope > .tile-tools .tt-size");
    if (lab) lab.textContent = `${w} wide${h ? ` · ${h} high` : ""}`;
  }
  // A tile that fits its content takes as many grid rows as it needs; kept up to date as the content changes.
  const fitRows = (el) => Math.max(1, Math.ceil((el.scrollHeight + gap()) / (ROW + gap())));
  const ro = new ResizeObserver((list) => list.forEach((e) => {
    const id = e.target.dataset.tile; if (!id || !tiles.has(id) || size(id)[1]) return;
    const want = `span ${fitRows(e.target)}`;
    if (e.target.style.gridRow !== want) e.target.style.gridRow = want;
  }));
  const applyAll = () => { tiles.forEach((_, id) => apply(id)); drawAddMenu(); };

  function ensureOrder() {
    for (const id of tiles.keys()) if (!state.order.includes(id)) state.order.push(id);
  }

  // edit controls, shown on every tile while editing
  function tools(id) {
    const t = tiles.get(id);
    if (t.el.querySelector(":scope > .tile-tools")) return;
    const bar = document.createElement("div");
    bar.className = "tile-tools";
    bar.innerHTML = `<span class="tt-grip" title="Drag to move">⠿</span><b>${AT.esc(t.title)}</b><span class="tt-size muted"></span><span class="grow"></span>
      <button type="button" data-d="w-" title="Narrower">◂</button><button type="button" data-d="w+" title="Wider">▸</button>
      <button type="button" data-d="h-" title="Shorter">▴</button><button type="button" data-d="h+" title="Taller">▾</button>
      <button type="button" data-d="x" title="Hide this">✕</button>`;
    const corner = document.createElement("span");
    corner.className = "tile-corner"; corner.title = "Drag to resize";
    t.el.prepend(bar); t.el.append(corner);
    bar.querySelectorAll("[data-d]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      const s = st(id); let [w, h] = size(id);
      if (b.dataset.d === "w-") w = Math.max(2, w - 1);
      if (b.dataset.d === "w+") w = Math.min(COLS, w + 1);
      if (b.dataset.d === "h-") h = h <= 3 ? 0 : h - 1;
      if (b.dataset.d === "h+") h = h ? h + 1 : Math.max(3, Math.round(t.el.getBoundingClientRect().height / (ROW + gap())) + 1);
      if (b.dataset.d === "x") {
        if (id.startsWith("win:")) { document.dispatchEvent(new CustomEvent("atsuit:unpin", { detail: id.slice(4) })); return; }
        s.hidden = true;
      }
      s.w = w; s.h = h; save(); apply(id); drawAddMenu();
    }));
    // drag to reorder: the other tiles make room as the tile passes over them (mouse, pen or touch)
    const grip = bar.querySelector(".tt-grip");
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault(); grip.setPointerCapture(e.pointerId);
      t.el.classList.add("tile-moving"); ensureOrder();
      let last = "";
      const move = (m) => {
        t.el.style.pointerEvents = "none";
        const over = document.elementFromPoint(m.clientX, m.clientY)?.closest("[data-tile]");
        t.el.style.pointerEvents = "";
        if (!over || over === t.el || !board.contains(over)) return;
        const r = over.getBoundingClientRect(), after = m.clientX > r.left + r.width / 2;
        const key = over.dataset.tile + after;
        if (key === last) return;
        last = key;
        const o = state.order.filter((x) => x !== id);
        o.splice(o.indexOf(over.dataset.tile) + (after ? 1 : 0), 0, id);
        state.order = o; applyAll();
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", () => { grip.removeEventListener("pointermove", move); t.el.classList.remove("tile-moving"); save(); }, { once: true });
    });
    // corner: drag to resize in grid steps
    corner.addEventListener("pointerdown", (e) => {
      e.preventDefault(); corner.setPointerCapture(e.pointerId);
      const r0 = t.el.getBoundingClientRect(), colW = (board.clientWidth - gap() * (COLS - 1)) / COLS;
      const move = (m) => {
        const s = st(id);
        s.w = Math.max(2, Math.min(COLS, Math.round((r0.width + m.clientX - e.clientX + gap()) / (colW + gap()))));
        s.h = Math.max(3, Math.round((r0.height + m.clientY - e.clientY + gap()) / (ROW + gap())));
        apply(id);
      };
      corner.addEventListener("pointermove", move);
      corner.addEventListener("pointerup", () => { corner.removeEventListener("pointermove", move); save(); }, { once: true });
    });
  }
  const gap = () => parseFloat(getComputedStyle(board).columnGap) || 16;

  // the edit bar at the top of the board
  let bar = null;
  function drawAddMenu() {
    if (!bar) return;
    const hidden = [...tiles.entries()].filter(([id]) => st(id).hidden);
    bar.querySelector("[data-add]").innerHTML = `<option value="">Show a hidden part…</option>` + hidden.map(([id, t]) => `<option value="${AT.esc(id)}">${AT.esc(t.title)}</option>`).join("");
    bar.querySelector("[data-add]").disabled = !hidden.length;
  }
  function editBar() {
    bar = document.createElement("div");
    bar.className = "board-edit hidden";
    bar.innerHTML = `<b>Edit layout</b><span class="muted small">Drag ⠿ to move a part. ◂ ▸ for width, ▴ ▾ for height, or drag the corner. ✕ hides a part.</span><span class="grow"></span>
      <select data-add style="width:auto"></select>
      <select data-preset style="width:auto"><option value="">Ready-made layout…</option>${Object.entries(PRESETS).map(([k, p]) => `<option value="${k}">${p.label}</option>`).join("")}</select>
      <button type="button" class="primary" data-done>Done</button>`;
    board.before(bar);
    bar.querySelector("[data-add]").onchange = (e) => { const id = e.target.value; if (id) { st(id).hidden = false; save(); apply(id); drawAddMenu(); } };
    bar.querySelector("[data-preset]").onchange = (e) => { const p = PRESETS[e.target.value]; e.target.value = ""; if (p) usePreset(p); };
    bar.querySelector("[data-done]").onclick = () => edit(false);
  }
  function usePreset(p) {
    const keepWins = Object.fromEntries(Object.entries(state.tiles).filter(([id]) => id.startsWith("win:")));
    state = { tiles: { ...keepWins }, order: [...p.order] };
    for (const [id, [w, h]] of Object.entries(p.tiles)) state.tiles[id] = { ...(state.tiles[id] || {}), w, h };
    ensureOrder(); save(); applyAll();
  }

  function edit(on) {
    editing = on;
    board.classList.toggle("editing", on);
    bar.classList.toggle("hidden", !on);
    if (on) tiles.forEach((_, id) => tools(id));
    document.dispatchEvent(new CustomEvent("atsuit:layout-edit", { detail: on }));
  }

  return {
    // board: the grid element; editBtn: the button that turns editing on and off
    mount(el, editBtn) {
      board = el;
      editBar();
      if (editBtn) editBtn.onclick = () => edit(!editing);
    },
    // Make an element a tile. def = [width in columns, height in rows (0 = fit the content)].
    add(id, el, title, def = [4, 0]) {
      el.dataset.tile = id; el.classList.add("btile");
      tiles.set(id, { el, title, def });
      ro.observe(el);
      if (el.parentElement !== board && !el.closest(".board")) board.appendChild(el);
      if (editing) tools(id);
      apply(id); drawAddMenu();
    },
    // A floating window pinned to the board becomes a tile; unpinned it floats again.
    adopt(id, el, title) {
      if (el.parentElement !== board) board.appendChild(el);
      if (!state.order.includes(id)) { state.order.push(id); save(); }
      st(id).hidden = false;
      this.add(id, el, title, [4, 0]);
    },
    release(id) {
      const t = tiles.get(id); if (!t) return;
      t.el.querySelector(":scope > .tile-tools")?.remove(); t.el.querySelector(":scope > .tile-corner")?.remove();
      ro.unobserve(t.el);
      t.el.classList.remove("btile", "tile-off", "tile-fixed"); t.el.style.gridColumn = t.el.style.gridRow = t.el.style.order = "";
      delete t.el.dataset.tile; tiles.delete(id); drawAddMenu();
    },
    has: (id) => tiles.has(id),
    editing: () => editing,
  };
})();
