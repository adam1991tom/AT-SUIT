<script>
  // Along the bottom, like Windows: a button per part of the workspace, and
  // the Layouts menu for saved arrangements.
  import * as T from "./tree.js";
  import { desk, known, toggle, saveAs, load, forget, reset } from "./store.svelte.js";
  import { popped, focusPop } from "./popout.js";

  let { onShare } = $props();
  const items = $derived(known().filter((id) => desk.panes[id].available));
  function state(id) {
    if (desk.panes[id].popped) return "popped";
    const g = T.groupOf(desk.layout, id);
    if (!g) return "closed";
    if (g.min) return "min";
    return g.active === id ? (desk.focus === g.id ? "front" : "open") : "tab";
  }
  function click(id) {
    if (popped(id)) return focusPop(id);
    toggle(id);
  }
  function save() {
    const name = (prompt("Name this layout (for example Plenary, Breakout or Captions only)", desk.current || "") || "").trim();
    if (name) { saveAs(name.slice(0, 40)); window.AT?.toast?.(`Saved "${name}"`, "good"); }
    desk.menu = false;
  }
  function share() {
    desk.menu = false;
    if (confirm("Use this layout as the starting layout on every laptop? Techs can still change their own.")) onShare?.(desk.layout);
  }
</script>

<nav class="dk-task" aria-label="Workspace windows">
  <div class="dk-menuwrap">
    <button type="button" class="dk-start" class:on={desk.menu} onclick={(e) => { e.stopPropagation(); desk.menu = !desk.menu; }}
      title="Saved layouts">Layouts{desk.current ? `: ${desk.current}` : ""} ▴</button>
    {#if desk.menu}
      <div class="dk-menu" role="menu" tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.key === "Escape" && (desk.menu = false)}>
        {#each desk.layouts as name (name)}
          <div class="dk-mi"><button type="button" class:on={name === desk.current} onclick={() => { load(name); desk.menu = false; }}>{name}</button>
            <button type="button" class="small" title="Delete this layout" onclick={() => confirm(`Delete the layout "${name}"?`) && forget(name)}>✕</button></div>
        {:else}
          <p class="muted small">No saved layouts yet.</p>
        {/each}
        <hr>
        <button type="button" onclick={save}>Save this layout…</button>
        <button type="button" onclick={() => { if (confirm("Put every window back where it starts?")) { reset(); desk.menu = false; } }}>Reset to the starting layout</button>
        {#if desk.canShare}<button type="button" onclick={share}>Use on every laptop…</button>{/if}
        <p class="muted small">Drag a window by its title onto the guides to dock it, or into the middle guide to add it as a tab. Double-click a title to maximise.</p>
      </div>
    {/if}
  </div>
  <div class="dk-items">
    {#each items as id (id)}
      <button type="button" class="dk-item {state(id)}" onclick={() => click(id)} title={state(id) === "closed" ? "Open" : state(id) === "popped" ? "In its own window" : ""}>
        {desk.panes[id].title}{#if desk.panes[id].badge}<span class="unread">{desk.panes[id].badge}</span>{/if}
      </button>
    {/each}
  </div>
</nav>
