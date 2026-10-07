<script>
  // Confirmation dialog for anything that changes data or uses the logged-in Telegram account.
  // `lines` = [{title, detail, login?}] shows exactly what will run.
  import Modal from './Modal.svelte';

  /** @type {{ open: boolean, title: string, lines?: any[], intro?: string, confirmLabel?: string, danger?: boolean, busy?: boolean, onconfirm: () => void, onclose: () => void, children?: any }} */
  let { open, title, lines = [], intro = '', confirmLabel = 'Confirm', danger = false, busy = false, onconfirm, onclose, children } = $props();
  const login = $derived(lines.some((l) => l.login));
</script>

<Modal {open} {title} {onclose} width="560px">
  {#if intro}<p>{intro}</p>{/if}
  {#if lines.length}
    <ul class="lines">
      {#each lines as l}
        <li>
          <b>{l.title}</b>{#if l.login} <span class="pill warn">your Telegram account</span>{/if}
          {#if l.detail}<div class="small muted">{l.detail}</div>{/if}
        </li>
      {/each}
    </ul>
  {/if}
  {#if login}
    <div class="callout warn">Steps marked <b>your Telegram account</b> connect as you. Telegram can see these requests. Nothing is posted, joined or changed on Telegram.</div>
  {/if}
  {@render children?.()}
  {#snippet footer()}
    <button onclick={onclose} disabled={busy}>Cancel</button>
    <button class={danger ? 'danger' : 'primary'} onclick={onconfirm} disabled={busy}>{#if busy}<span class="spinner"></span>{/if}{confirmLabel}</button>
  {/snippet}
</Modal>

<style>
  .lines { list-style: none; margin: 0 0 12px; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .lines li { padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--radius-sm); }
</style>
