<script>
  // The Layout menu in the page's top bar: a line per part of the workspace to
  // open, bring forward or minimise it, and the saved layouts. It replaces the
  // old taskbar along the bottom, so the windows get that space back.
  // Questions are asked here in the menu, never with confirm()/prompt(): the
  // Windows app turns those off (they can play a sound).
  import * as T from "./tree.js";
  import { desk, known, toggle, saveAs, load, forget, reset } from "./store.svelte.js";
  import { popped, focusPop } from "./popout.js";

  let { onShare } = $props();
  let wrap = $state(null);
  let btn = $state(null);
  let nameEl = $state(null);
  let naming = $state(false);
  let name = $state("");
  let ask = $state(null); // { text, label, yes }: a question asked in the menu
  const items = $derived(known().filter((id) => desk.panes[id].available));
  // What's waiting in windows that aren't on screen shows on the menu button.
  const waiting = $derived(items.filter((id) => desk.panes[id].badge && where(id) !== "front" && where(id) !== "open")
    .reduce((n, id) => n + desk.panes[id].badge, 0));

  function where(id) {
    if (desk.panes[id].popped) return "popped";
    const g = T.groupOf(desk.layout, id);
    if (!g) return "closed";
    if (g.min) return "min";
    return g.active === id ? (desk.focus === g.id ? "front" : "open") : "tab";
  }
  const SAYS = { front: "Open", open: "Open", tab: "Behind another tab", min: "Minimised", closed: "Closed", popped: "In its own window" };
  function click(id) {
    if (popped(id)) return focusPop(id);
    toggle(id);
  }
  function shut(back = false) {
    desk.menu = false; naming = false; ask = null;
    if (back) btn?.focus();
  }
  function flip(e) {
    e.stopPropagation();
    if (desk.menu) return shut();
    // one drop-down at a time: the top bar's bell and Help close when this opens
    document.querySelectorAll(".dropdown").forEach((d) => d.classList.add("hidden"));
    desk.menu = true;
  }
  function startSave() {
    ask = null; naming = true; name = desk.current || "";
    queueMicrotask(() => nameEl?.focus());
  }
  function save(e) {
    e?.preventDefault();
    const n = name.trim().slice(0, 40);
    if (!n) return;
    saveAs(n);
    window.AT?.toast?.(`Saved "${n}"`, "good");
    shut();
  }
  function confirmThen(text, label, yes) { naming = false; ask = { text, label, yes }; }

  // A click anywhere else, or Escape, closes it. Capture, so parts that stop their own clicks still close it.
  $effect(() => {
    const away = (e) => { if (desk.menu && wrap && !wrap.contains(e.target)) shut(); };
    const esc = (e) => { if (desk.menu && e.key === "Escape") shut(wrap?.contains(document.activeElement)); };
    document.addEventListener("click", away, true);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("click", away, true); document.removeEventListener("keydown", esc); };
  });
</script>

<div class="dk-menuwrap" bind:this={wrap}>
  <button type="button" class="dk-start" class:on={desk.menu} class:dk-new={waiting > 0} bind:this={btn} onclick={flip}
    aria-expanded={desk.menu} aria-controls="dk-layout-menu" title="Windows and saved layouts">
    Layout{desk.current ? `: ${desk.current}` : ""}{#if waiting}<span class="unread">{waiting}</span>{/if} ▾</button>
  {#if desk.menu}
    <div class="dk-menu" id="dk-layout-menu" role="group" aria-label="Windows and saved layouts">
      <b class="small">Windows</b>
      {#each items as id (id)}
        <button type="button" class="dk-item {where(id)}" class:dk-new={!!desk.panes[id].badge} onclick={() => click(id)}
          aria-label="{desk.panes[id].title}: {SAYS[where(id)]}">
          <span class="dk-dot" aria-hidden="true"></span><span class="grow">{desk.panes[id].title}</span>
          {#if desk.panes[id].badge}<span class="unread">{desk.panes[id].badge}</span>{/if}
          <span class="dk-state muted small" aria-hidden="true">{SAYS[where(id)]}</span>
        </button>
      {/each}
      <hr>
      <b class="small">Saved layouts</b>
      {#each desk.layouts as n (n)}
        <div class="dk-mi"><button type="button" class:on={n === desk.current} aria-current={n === desk.current ? "true" : undefined}
            onclick={() => { load(n); shut(); }}>{n}</button>
          <button type="button" class="small" title="Delete this layout" aria-label="Delete the layout {n}"
            onclick={() => confirmThen(`Delete the layout "${n}"?`, "Delete", () => forget(n))}>✕</button></div>
      {:else}
        <p class="muted small">No saved layouts yet.</p>
      {/each}
      {#if naming}
        <form class="dk-ask" onsubmit={save}>
          <label class="small" for="dk-layout-name">Name this layout</label>
          <input id="dk-layout-name" bind:this={nameEl} bind:value={name} maxlength="40" placeholder="e.g. Plenary, Breakout or Captions only" autocomplete="off">
          <div class="dk-askbtns"><button type="submit" class="primary small" disabled={!name.trim()}>Save</button>
            <button type="button" class="small" onclick={() => (naming = false)}>Cancel</button></div>
        </form>
      {:else}
        <button type="button" onclick={startSave}>Save this layout…</button>
      {/if}
      <button type="button" onclick={() => confirmThen("Put every window back where it starts?", "Reset", reset)}>Reset to the starting layout</button>
      {#if desk.canShare}<button type="button" onclick={() => confirmThen("Use this layout as the starting layout on every laptop? Techs can still change their own.", "Use it", () => onShare?.(desk.layout))}>Use on every laptop…</button>{/if}
      {#if ask}
        <div class="dk-ask" role="group" aria-label={ask.text}>
          <p class="small">{ask.text}</p>
          <div class="dk-askbtns"><button type="button" class="primary small" onclick={() => { const y = ask.yes; shut(); y(); }}>{ask.label}</button>
            <button type="button" class="small" onclick={() => (ask = null)}>Cancel</button></div>
        </div>
      {/if}
      <p class="muted small">Drag a window by its title onto the guides to dock it, or into the middle guide to add it as a tab. Double-click a title to maximise.</p>
    </div>
  {/if}
</div>
