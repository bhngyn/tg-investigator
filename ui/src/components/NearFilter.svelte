<script>
  // "Near a place" filter: pick a place from the dictionary and a radius -> near=lat,lon,km.
  // The centre is the dictionary's coordinate for that place (a search aid, not a verified location).
  //   <NearFilter value={Q.near} label={Q.near_label} onchange={(near, label) => ...} />
  import { get } from '../lib/api.js';
  import { listOf } from '../lib/searchparams.js';

  let { value = '', label = '', onchange } = $props();

  let q = $state('');
  let opts = $state([]);
  let busy = $state(false);
  let err = $state('');
  let km = $state(25);
  let seq = 0;

  const parsed = $derived.by(() => { const [la, lo, k] = String(value || '').split(',').map(Number); return Number.isFinite(la) && Number.isFinite(lo) ? { la, lo, k: k || 25 } : null; });
  $effect(() => { if (parsed) km = parsed.k; });

  $effect(() => {
    const term = q.trim();
    const my = ++seq;   // also invalidates any request already in flight
    if (term.length < 2) { opts = []; busy = false; return; }
    const t = setTimeout(async () => {
      busy = true; err = '';
      try {
        const r = await get('/api/dictionary/entries', { q: term, kind: 'place', limit: 8 });
        if (my === seq) opts = listOf(r, 'entries');
      } catch (e) { if (my === seq) err = e.message; }
      if (my === seq) busy = false;
    }, 250);
    return () => clearTimeout(t);
  });

  async function pick(e) {
    let { lat, lon } = e;
    if (lat == null || lon == null) {
      try { const full = await get(`/api/dictionary/entry/${encodeURIComponent(e.id)}`); lat = full.lat; lon = full.lon; } catch (er) { err = er.message; return; }
    }
    if (lat == null || lon == null) { err = 'This dictionary entry has no coordinates.'; return; }
    onchange?.(`${(+lat).toFixed(4)},${(+lon).toFixed(4)},${km}`, [e.label, e.country].filter(Boolean).join(', '));
    q = ''; opts = [];
  }
  function setKm(v) {
    km = v;
    if (parsed) onchange?.(`${parsed.la},${parsed.lo},${v}`, label);
  }
</script>

<div class="near col">
  {#if parsed}
    <div class="row wrap small">
      <span>Within</span>
      <select value={km} onchange={(e) => setKm(Number(e.currentTarget.value))} aria-label="Radius in kilometres">
        {#each [5, 10, 25, 50, 100, 250, 500] as k}<option value={k}>{k} km</option>{/each}
      </select>
      <span>of <b dir="auto">{label || `${parsed.la}, ${parsed.lo}`}</b></span>
      <button class="sm ghost" onclick={() => onchange?.(null, null)}>Clear</button>
    </div>
  {:else}
    <div class="row wrap">
      <input type="search" dir="auto" bind:value={q} placeholder="Place name (any language)" aria-label="Find a place" style="flex:1;min-width:0" />
      <select bind:value={km} aria-label="Radius in kilometres">{#each [5, 10, 25, 50, 100, 250, 500] as k}<option value={k}>{k} km</option>{/each}</select>
    </div>
    {#if busy}<div class="tiny faint">Looking…</div>{/if}
    {#if err}<div class="tiny" style="color:var(--bad)">{err}</div>{/if}
    {#if opts.length}
      <ul class="opts">
        {#each opts as e (e.id)}
          <li><button class="opt" onclick={() => pick(e)}><b dir="auto">{e.label}</b> <span class="faint tiny">{[e.admin1, e.country, e.category].filter(Boolean).join(' · ')}</span></button></li>
        {/each}
      </ul>
    {/if}
  {/if}
  <div class="tiny faint">Posts that mention a place inside the circle (places found in text, not verified locations).</div>
</div>

<style>
  .near { gap: 6px; }
  .wrap { flex-wrap: wrap; }
  .opts { list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel); }
  .opt { display: block; width: 100%; text-align: start; border: 0; background: none; padding: 4px 8px; font-weight: 400; color: var(--text); }
  .opt:hover { background: var(--accent-soft); }
</style>
