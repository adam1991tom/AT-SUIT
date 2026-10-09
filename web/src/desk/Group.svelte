<script>
  // One window: a title bar with a tab per pane, the window buttons, and the panes.
  import * as T from "./tree.js";
  import { desk, can, set, focus, close } from "./store.svelte.js";
  import { host } from "./host.js";
  import { startDrag, startResize } from "./drag.js";
  import { popout } from "./popout.js";

  let { g, float = null, area } = $props();
  const tabs = $derived(g.tabs.filter(can));
  const active = $derived(tabs.includes(g.active) ? g.active : tabs[0]);
  const max = $derived(desk.layout.max === g.id);
  const EDGES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

  function headDown(e) {
    if (e.target.closest("button, input, select")) return;
    startDrag(e, { ids: [...g.tabs], gid: g.id, area: area() });
  }
  function tabDown(e, id) {
    if (e.button !== 0) return;
    set(T.activate(desk.layout, id));
    // One tab pulled out of several: just that pane moves.
    startDrag(e, { ids: g.tabs.length > 1 ? [id] : [...g.tabs], gid: g.id, area: area() });
  }
  function pop() {
    if (!popout(active)) window.AT?.toast?.("The pop-out was blocked. Allow pop-ups for this page and try again.", "bad");
  }
  const style = $derived(float && !max ? `left:${float.x}px;top:${float.y}px;width:${float.w}px;height:${float.h}px` : "");
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<section class="dk-win" aria-label={desk.panes[active]?.title} class:float={!!float} class:max class:focus={desk.focus === g.id} class:dk-flash={tabs.some((id) => desk.panes[id]?.flash)}
  data-gid={g.id} style={style} onpointerdown={() => focus(g.id)}>
  <!-- Dragging by the title bar is pointer-only, as on Windows; the taskbar and buttons work from the keyboard. -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <header class="dk-head" onpointerdown={headDown} ondblclick={(e) => !e.target.closest(".dk-btns") && set(T.setMax(desk.layout, g.id))}>
    <div class="dk-tabs" role="tablist">
      {#each tabs as id (id)}
        <button type="button" role="tab" class="dk-tab" class:on={id === active} class:dk-new={!!desk.panes[id].badge} aria-selected={id === active}
          onpointerdown={(e) => { e.stopPropagation(); tabDown(e, id); }}>
          {desk.panes[id].title}{#if desk.panes[id].badge}<span class="unread">{desk.panes[id].badge}</span>{/if}
        </button>
      {/each}
    </div>
    <span class="dk-btns">
      <button type="button" title="Pop out into its own window" aria-label="Pop out" onclick={pop}>⧉</button>
      <button type="button" title="Minimise" aria-label="Minimise" onclick={() => set(T.setMin(desk.layout, g.id, true))}>–</button>
      <button type="button" title={max ? "Restore" : "Maximise"} aria-label={max ? "Restore" : "Maximise"} onclick={() => set(T.setMax(desk.layout, g.id))}>{max ? "❐" : "□"}</button>
      <button type="button" title="Close" aria-label="Close" onclick={() => close(active)}>✕</button>
    </span>
  </header>
  <div class="dk-body">
    {#each tabs as id (id)}
      <div class="dk-pane" hidden={id !== active} data-pane={id} use:host={id}></div>
    {/each}
  </div>
  {#if float && !max}
    {#each EDGES as edge}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="dk-edge {edge}" onpointerdown={(e) => startResize(e, g.id, edge, area())}></div>
    {/each}
  {/if}
</section>
