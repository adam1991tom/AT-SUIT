<script>
  // A docked part of the desk: one window, or a row or column of them with bars between.
  import * as T from "./tree.js";
  import { can } from "./store.svelte.js";
  import { startSplit } from "./drag.js";
  import Group from "./Group.svelte";
  import Self from "./Node.svelte";

  let { node, path, area } = $props();
  let box = $state(null);
  const visible = $derived(node.t === "split" ? node.kids.map((k, i) => i).filter((i) => T.nodeShown(node.kids[i], can)) : []);
  // The shown windows share all the room between them: a minimised or hidden one leaves no gap.
  // (Flex grow factors that add up to less than 1 would leave the rest of the row empty.)
  const shownTotal = $derived(visible.reduce((s, i) => s + (node.sizes[i] ?? 0), 0) || 1);
</script>

{#if node.t === "group"}
  {#if T.shown(node, can)}<Group g={node} {area} />{/if}
{:else}
  <div class="dk-split dk-{node.dir}" data-dir={node.dir} bind:this={box}>
    {#each visible as i, n (i)}
      {#if n > 0}
        <div class="dk-bar" role="separator" aria-orientation={node.dir === "row" ? "vertical" : "horizontal"}
          onpointerdown={(e) => startSplit(e, path, n, node.sizes, visible, box)}></div>
      {/if}
      <div class="dk-cell" style="flex:{(node.sizes[i] ?? 0) / shownTotal} 1 0">
        <Self node={node.kids[i]} path={[...path, i]} {area} />
      </div>
    {/each}
  </div>
{/if}
