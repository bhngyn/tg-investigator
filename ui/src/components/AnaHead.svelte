<script>
  // Every analysis tab opens with the question it answers, then an honest caveat.
  let { question, children } = $props();
</script>
<div class="ana-head">
  <p class="q">{question}</p>
  {#if children}<div class="caveat small muted">{@render children()}</div>{/if}
</div>
<style>
  .ana-head { margin-bottom: 12px; }
  .q { font-size: 15px; font-weight: 600; margin: 0 0 2px; }
  .caveat { max-width: 90ch; }
</style>
