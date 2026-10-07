<script>
  // Review-status buttons for one item (or a bulk selection). Uses case triage inside a case.
  import { setReview, canTriage, REVIEW_LABEL } from '../lib/triage.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import { attempt } from '../lib/store.svelte.js';

  /** @type {{ itemType?: 'post'|'comment', ids: number|number[], status?: string, onchange?: (s: string) => void, size?: string, keys?: boolean }} */
  let { itemType = 'post', ids, status = 'unreviewed', onchange, size = 'sm', keys = false } = $props();

  const opts = [
    ['relevant', 'Relevant', 'r'],
    ['verify', 'Verify', 'v'],
    ['irrelevant', 'Not relevant', 'i'],
  ];
  async function set(s) {
    const next = status === s ? 'unreviewed' : s;   // clicking the active one clears it
    const r = await attempt(() => setReview(itemType, ids, next));
    if (r !== undefined) onchange?.(next);
  }
</script>

{#if canTriage(itemType)}
  <div class="seg triage {size}" role="group" aria-label="Review status">
    {#each opts as [s, label, k]}
      <button type="button" class="{size} rv-{s}" class:on={status === s} aria-pressed={status === s} onclick={(e) => { e.stopPropagation(); set(s); }}
        title="{REVIEW_LABEL[s]}{keys && prefs.shortcuts ? ` (${k})` : ''}">{label}{#if keys && prefs.shortcuts}&nbsp;<kbd>{k}</kbd>{/if}</button>
    {/each}
  </div>
{:else}
  <span class="tiny faint" title="Comments are triaged per case">Open a case to triage</span>
{/if}

<style>
  .triage button.on.rv-relevant { background: var(--ok); color: var(--on-fill); }
  .triage button.on.rv-verify { background: var(--warn); color: var(--on-fill); }
  .triage button.on.rv-irrelevant { background: var(--faint); color: var(--on-fill); }
  .triage button { color: var(--muted); }
</style>
