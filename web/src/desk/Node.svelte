<script>
  // A docked part of the desk: one window, or a row or column of them with bars between.
  import * as T from "./tree.js";
  import { can } from "./store.svelte.js";
  import { startSplit, nudgeSplit } from "./drag.js";
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
        <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
        <div class="dk-bar" role="separator" tabindex="0" aria-label="Resize the windows either side" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round((100 * (node.sizes[visible[n - 1]] ?? 0)) / shownTotal)} aria-orientation={node.dir === "row" ? "vertical" : "horizontal"}
          onpointerdown={(e) => startSplit(e, path, n, node.sizes, visible, box)}
          ondblclick={() => nudgeSplit(path, n, node.sizes, visible, "even")}
          onkeydown={(e) => {
            const back = node.dir === "row" ? "ArrowLeft" : "ArrowUp", on = node.dir === "row" ? "ArrowRight" : "ArrowDown";
            if (e.key === back || e.key === on) { e.preventDefault(); nudgeSplit(path, n, node.sizes, visible, e.key === on ? 0.05 : -0.05); }
          }}></div>
      {/if}
      <div class="dk-cell" style="flex:{(node.sizes[i] ?? 0) / shownTotal} 1 0">
        <Self node={node.kids[i]} path={[...path, i]} {area} />
      </div>
    {/each}
  </div>
{/if}
