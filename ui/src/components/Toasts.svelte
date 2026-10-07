<script>
  import { app } from '../lib/store.svelte.js';
  const dismiss = (id) => { app.toasts = app.toasts.filter((t) => t.id !== id); };
</script>
<div class="toasts">
  {#each app.toasts as t (t.id)}
    {#if t.kind === 'error'}
      <div class="toast error" role="alert">{t.message} <button class="x" onclick={() => dismiss(t.id)} aria-label="Dismiss message">✕</button></div>
    {:else}
      <div class="toast {t.kind}" role="status">{t.message}</div>
    {/if}
  {/each}
</div>
<style>
  .toasts { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); z-index: 100; display: flex; flex-direction: column; gap: 6px; align-items: center; pointer-events: none; }
  .toast { background: var(--text); color: var(--bg); padding: 8px 14px; border-radius: 6px; box-shadow: var(--shadow-lg); font-size: 13px; max-width: 80vw; pointer-events: auto; }
  .toast.error { background: var(--bad); color: var(--on-fill); }
  .toast.ok { background: var(--ok); color: var(--on-fill); }
  .x { background: none; border: 0; color: inherit; padding: 0 0 0 8px; font-size: 12px; cursor: pointer; }
  .x:hover { background: none; }
  .x:focus-visible { outline-color: currentColor; }
</style>
