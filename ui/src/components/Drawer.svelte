<script>
  // Right-side panel for item details. Closes on Esc, backdrop click or ✕. Focus moves in, is trapped, and returns to the opener.
  let { open = false, onclose, title = '', width = '640px', children, actions } = $props();
  import { dialogLayer, okToDiscard } from '../lib/dialog.js';
  const uid = 'drw' + Math.random().toString(36).slice(2, 8);
  let panel = $state();
  const requestClose = (checked) => { if (checked === true || okToDiscard(panel)) onclose?.(); };
</script>
{#if open}
  <div class="layer" use:dialogLayer={{ close: requestClose }}>
    <div class="backdrop" onclick={() => requestClose()} role="presentation"></div>
    <div class="drawer" bind:this={panel} style:width role="dialog" aria-modal="true" aria-labelledby={uid} tabindex="-1">
      <header>
        <h2 class="ellipsis" id={uid}>{title}</h2>
        {#if actions}{@render actions()}{/if}
        <button class="ghost icon" onclick={() => requestClose()} aria-label="Close" title="Close (Esc)">✕</button>
      </header>
      <div class="body">{@render children?.()}</div>
    </div>
  </div>
{/if}
<style>
  .backdrop { position: fixed; inset: 0; background: rgba(10, 12, 16, .35); z-index: 40; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; max-width: 100vw; background: var(--panel); z-index: 41; box-shadow: var(--shadow-lg); display: flex; flex-direction: column; border-left: 1px solid var(--line); }
  .drawer:focus { outline: none; }
  header { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--line); }
  header h2 { flex: 1; }
  .body { flex: 1; overflow: auto; padding: 16px; }
</style>
