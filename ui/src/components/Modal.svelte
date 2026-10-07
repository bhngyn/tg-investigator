<script>
  // Centered dialog. Closes on Esc / backdrop / ✕. Focus moves in, is trapped, and returns to the opener (lib/dialog.js).
  let { open = false, onclose, title = '', width = '520px', children, footer } = $props();
  import { dialogLayer, okToDiscard } from '../lib/dialog.js';
  const uid = 'mdl' + Math.random().toString(36).slice(2, 8);
  let panel = $state();
  const requestClose = (checked) => { if (checked === true || okToDiscard(panel)) onclose?.(); };
</script>
{#if open}
  <div class="layer" use:dialogLayer={{ close: requestClose }}>
    <div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) requestClose(); }} role="presentation">
      <div class="modal" bind:this={panel} style:width role="dialog" aria-modal="true" aria-labelledby={uid} tabindex="-1">
        <header><h2 id={uid}>{title}</h2><button class="ghost icon" onclick={() => requestClose()} aria-label="Close">✕</button></header>
        <div class="body">{@render children?.()}</div>
        {#if footer}<footer>{@render footer()}</footer>{/if}
      </div>
    </div>
  </div>
{/if}
<style>
  .backdrop { position: fixed; inset: 0; background: rgba(10, 12, 16, .45); z-index: 50; display: flex; align-items: flex-start; justify-content: center; padding: 8vh 16px; overflow: auto; }
  .modal { background: var(--panel); border-radius: 10px; box-shadow: var(--shadow-lg); max-width: 100%; border: 1px solid var(--line); }
  .modal:focus { outline: none; }
  header { display: flex; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--line); }
  header h2 { flex: 1; }
  .body { padding: 16px; }
  footer { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--line); }
</style>
