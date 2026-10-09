<script>
  // The tech workspace as a desktop: docked windows filling the screen,
  // floating windows over them, and the taskbar.
  import * as T from "./tree.js";
  import { desk, can } from "./store.svelte.js";
  import Node from "./Node.svelte";
  import Group from "./Group.svelte";
  import Taskbar from "./Taskbar.svelte";

  let { onShare } = $props();
  let areaEl = $state(null);
  const area = () => areaEl;
  const L = $derived(desk.layout);
  const floats = $derived(L.floats.filter((f) => T.shown(f.group, can)));
  const SIDES = ["top", "left", "center", "right", "bottom"];

  $effect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const on = () => (desk.narrow = mq.matches);
    on(); mq.addEventListener("change", on);
    const shut = () => (desk.menu = false);
    window.addEventListener("click", shut);
    return () => { mq.removeEventListener("change", on); window.removeEventListener("click", shut); };
  });
  const px = (r) => `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px`;
</script>

<div class="dk" class:narrow={desk.narrow} class:dragging={!!desk.drag}>
  <div class="dk-area" bind:this={areaEl}>
    {#if L.root && T.nodeShown(L.root, can)}
      <div class="dk-root"><Node node={L.root} path={[]} {area} /></div>
    {:else}
      <div class="dk-empty muted">Everything is closed. Open a window from the taskbar below.</div>
    {/if}
    {#each floats as f (f.group.id)}
      <Group g={f.group} float={f} {area} />
    {/each}

    {#if desk.drag}
      {#if desk.drag.ghost}<div class="dk-ghost" style={px(desk.drag.ghost)}></div>{/if}
      {#if desk.drag.preview}<div class="dk-preview" style={px(desk.drag.preview)}></div>{/if}
      {#each ["top", "left", "right", "bottom"] as s}
        <div class="dk-guide root {s}" class:hot={desk.drag.zone === `root:${s}`} data-guide={`root:${s}`} title="Dock along this edge"></div>
      {/each}
      {#if desk.drag.over && desk.drag.overRect}
        {@const r = desk.drag.overRect}
        <div class="dk-compass" style="left:{r.x + r.w / 2}px;top:{r.y + r.h / 2}px">
          {#each SIDES as s}
            <div class="dk-guide {s}" class:hot={desk.drag.zone === `${desk.drag.over}:${s}`} data-guide={`${desk.drag.over}:${s}`}
              title={s === "center" ? "Add as a tab" : `Dock ${s}`}></div>
          {/each}
        </div>
      {/if}
    {/if}
  </div>
  <Taskbar {onShare} />
</div>
