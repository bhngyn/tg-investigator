<script>
  // Top-bar collection status: what's running now, in which lane. Links to Collection.
  import { app } from '../lib/store.svelte.js';
  import { href, route } from '../lib/router.svelte.js';
  import { pj } from '../lib/fmt.js';

  const j = $derived(app.jobs);
  const running = $derived([j.job, ...(j.others || [])].filter(Boolean));
  const label = $derived.by(() => {
    if (!running.length) return j.queued ? `${j.queued} queued` : null;
    const first = running[0];
    const p = pj(first.params, {});
    const what = p.channel || (p.seeds && p.seeds.join(', ')) || p.channels?.join?.(', ') || '';
    return `${first.kind}${what ? ' · ' + what : ''}${running.length > 1 ? ` +${running.length - 1}` : ''}${j.queued ? ` · ${j.queued} queued` : ''}`;
  });
</script>

{#if label}
  <a class="jobs" href={route.scope === null ? '#/all/collect' : href('collect')} title="Collection jobs">
    <span class="spinner" class:idle={!running.length}></span>
    <span class="ellipsis">{label}</span>
  </a>
{/if}

<style>
  .jobs { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--muted); max-width: 300px; padding: 3px 8px; border-radius: 6px; border: 1px solid var(--line); text-decoration: none; }
  .jobs:hover { background: var(--panel-2); text-decoration: none; }
  .spinner { width: 12px; height: 12px; flex: none; }
  .spinner.idle { animation: none; }
</style>
