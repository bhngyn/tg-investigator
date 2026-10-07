<script>
  // Preset colour palette for cases. value is a hex string (bindable).
  export const PALETTE = ['#2458d6', '#0f766e', '#17803d', '#a16207', '#c2410c', '#b3261e', '#be123c', '#86198f', '#5b4bc4', '#475569'];
  let { value = $bindable(PALETTE[0]) } = $props();
</script>
<div class="row tight" role="radiogroup" aria-label="Colour">
  {#each PALETTE as c}
    <button type="button" class="sw" class:on={value === c} style:background={c} role="radio" aria-checked={value === c}
      aria-label={c} title={c} onclick={() => (value = c)}></button>
  {/each}
</div>
<style>
  .sw { width: 24px; height: 24px; padding: 0; border-radius: 50%; border: 2px solid var(--panel); box-shadow: 0 0 0 1px var(--line-strong); }
  .sw:hover { filter: brightness(1.1); background-color: inherit; }
  .sw.on { box-shadow: 0 0 0 2px var(--text); }
</style>
