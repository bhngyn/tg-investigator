<script>
  // Codebook tags as toggle chips. `selected` is an array of tag ids.
  import { app, attempt } from '../lib/store.svelte.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import { setTag, canTriage } from '../lib/triage.js';

  /** @type {{ itemType?: string, id: number, selected?: number[], onchange?: (ids: number[]) => void, compact?: boolean }} */
  let { itemType = 'post', id, selected = [], onchange, compact = false } = $props();

  const shown = $derived(compact ? app.tags.filter((t) => selected.includes(t.id)) : app.tags);

  async function toggle(t) {
    const on = !selected.includes(t.id);
    const r = await attempt(() => setTag(itemType, id, t.id, on));
    if (r === undefined) return;
    const next = r?.tags ? r.tags.map((x) => (typeof x === 'object' ? x.id ?? x.tag_id : x)) : on ? [...selected, t.id] : selected.filter((x) => x !== t.id);
    onchange?.(next);
  }
</script>

{#if canTriage(itemType)}
  <div class="row tight">
    {#each shown as t (t.id)}
      <button type="button" class="chip" class:on={selected.includes(t.id)} aria-pressed={selected.includes(t.id)} style:color={selected.includes(t.id) ? t.color : undefined}
        onclick={(e) => { e.stopPropagation(); toggle(t); }} title={t.description || t.category}>
        <span class="dot" style:background={t.color}></span>{t.name}{#if t.key && !compact && prefs.shortcuts}<kbd>{t.key}</kbd>{/if}
      </button>
    {/each}
  </div>
{/if}
