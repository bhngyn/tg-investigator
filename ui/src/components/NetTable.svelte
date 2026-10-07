<script>
  // Text version of the Network graph: sortable edge list and node list, with CSV export.
  // Props: nodes, links (source/target ids), byId (Map), mode, selected, onselect(id)
  import { num } from '../lib/fmt.js';
  import { EDGE_LABEL, toCsv, saveText } from '../lib/net-data.js';
  import { EDGE_COLOR_KEY } from '../lib/net-theme.js';
  import { caseId } from '../lib/router.svelte.js';

  let { nodes = [], links = [], byId, mode = 'amp', selected = null, onselect } = $props();

  let tab = $state('edges');
  let sortE = $state({ key: 'weight', dir: -1 });
  const initialNodeSort = () => (mode === 'discovery' ? 'score' : 'inW');   // parent re-keys this table per mode
  let sortN = $state({ key: initialNodeSort(), dir: -1 });
  let filter = $state('');
  let onlySel = $state(false);

  const typeLabel = (t) => EDGE_LABEL[t] || t.replace(/_/g, ' ');
  const nm = (id) => byId.get(id)?.label || id;
  const handle = (id) => byId.get(id)?.handle || '';
  const f = $derived(filter.trim().toLowerCase());
  const match = (id) => !f || String(nm(id)).toLowerCase().includes(f) || String(handle(id)).toLowerCase().includes(f);

  const edgeKey = {
    source: (l) => String(nm(l.source)).toLowerCase(),
    target: (l) => String(nm(l.target)).toLowerCase(),
    weight: (l) => l.weight,
    types: (l) => Object.keys(l.byType).join(','),
    tstatus: (l) => byId.get(l.target)?.status || '',
  };
  const nodeKey = {
    label: (n) => String(n.label || '').toLowerCase(),
    status: (n) => n.status || n.kind || '',
    subscribers: (n) => n.subscribers ?? -1,
    inDeg: (n) => n.inDeg, inW: (n) => n.inW, outDeg: (n) => n.outDeg, outW: (n) => n.outW,
    score: (n) => n.score ?? -1,
  };
  const cmp = (fn, dir) => (a, b) => {
    const x = fn(a), y = fn(b);
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  };

  const edgeRows = $derived.by(() => {
    let r = links.filter((l) => match(l.source) || match(l.target));
    if (onlySel && selected != null) r = r.filter((l) => l.source === selected || l.target === selected);
    return [...r].sort(cmp(edgeKey[sortE.key], sortE.dir));
  });
  const nodeRows = $derived.by(() => {
    const r = nodes.filter((n) => match(n.id));
    return [...r].sort(cmp(nodeKey[sortN.key], sortN.dir));
  });

  function sortBy(which, key) {
    const s = which === 'e' ? sortE : sortN;
    const next = { key, dir: s.key === key ? -s.dir : key === 'source' || key === 'target' || key === 'label' ? 1 : -1 };
    if (which === 'e') sortE = next; else sortN = next;
  }
  const ariaSortOf = (s, key) => (s.key === key ? (s.dir < 0 ? 'descending' : 'ascending') : undefined);
  const arrow = (s, key) => (s.key === key ? (s.dir < 0 ? ' ▾' : ' ▴') : '');

  const directed = $derived(!(links[0]?.undirected));
  const fromLabel = $derived(mode === 'shared' ? (directed ? 'Person / source' : 'Section') : 'From (shares)');
  const toLabel = $derived(mode === 'shared' ? 'Comment section' : 'To (is shared)');

  function exportCsv() {
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
    if (tab === 'edges') {
      const types = [...new Set(links.flatMap((l) => Object.keys(l.byType)))];
      const text = toCsv(edgeRows, [
        ['source', (l) => handle(l.source) || l.source], ['source_title', (l) => byId.get(l.source)?.title || ''],
        ['target', (l) => handle(l.target) || l.target], ['target_title', (l) => byId.get(l.target)?.title || ''],
        ['total', (l) => l.weight], ...types.map((t) => [t, (l) => l.byType[t] ?? 0]),
      ]);
      saveText(`network-edges-${mode}-${stamp}.csv`, text);
    } else {
      const text = toCsv(nodeRows, [
        ['id', (n) => n.handle || n.id], ['title', (n) => n.title || n.label], ['kind', (n) => n.kind], ['status', (n) => n.status || ''],
        ['in_case', (n) => (n.in_case ? 1 : 0)], ['subscribers', (n) => n.subscribers ?? ''], ['in_links', (n) => n.inDeg],
        ['in_weight', (n) => n.inW], ['out_links', (n) => n.outDeg], ['out_weight', (n) => n.outW], ['score', (n) => n.score ?? ''],
      ]);
      saveText(`network-nodes-${mode}-${stamp}.csv`, text);
    }
  }
</script>

<div class="nettable">
  <div class="bar row">
    <div class="seg" role="group" aria-label="Table shows">
      <button class:on={tab === 'edges'} onclick={() => (tab = 'edges')} aria-pressed={tab === 'edges'}>Edges ({num(links.length)})</button>
      <button class:on={tab === 'nodes'} onclick={() => (tab = 'nodes')} aria-pressed={tab === 'nodes'}>{mode === 'shared' ? 'Nodes' : 'Channels'} ({num(nodes.length)})</button>
    </div>
    <input type="search" placeholder="Filter by name…" bind:value={filter} aria-label="Filter table" />
    {#if tab === 'edges' && selected != null}
      <label class="check small"><input type="checkbox" bind:checked={onlySel} /> only {nm(selected)}</label>
    {/if}
    <span class="spacer"></span>
    <button class="sm" onclick={exportCsv} title="Save the rows shown as a CSV file">Export CSV</button>
  </div>
  <div class="table-wrap">
    {#if tab === 'edges'}
      <table class="t">
        <thead><tr>
          <th scope="col" aria-sort={ariaSortOf(sortE, 'source')}><button class="th" onclick={() => sortBy('e', 'source')}>{fromLabel}{arrow(sortE, 'source')}</button></th>
          <th scope="col"><span class="sr-only">Direction</span></th>
          <th scope="col" aria-sort={ariaSortOf(sortE, 'target')}><button class="th" onclick={() => sortBy('e', 'target')}>{toLabel}{arrow(sortE, 'target')}</button></th>
          {#if mode !== 'shared'}<th scope="col" aria-sort={ariaSortOf(sortE, 'tstatus')}><button class="th" onclick={() => sortBy('e', 'tstatus')}>Target status{arrow(sortE, 'tstatus')}</button></th>{/if}
          <th scope="col" aria-sort={ariaSortOf(sortE, 'types')}><button class="th" onclick={() => sortBy('e', 'types')}>Types{arrow(sortE, 'types')}</button></th>
          <th scope="col" class="right" aria-sort={ariaSortOf(sortE, 'weight')}><button class="th" onclick={() => sortBy('e', 'weight')}>Weight{arrow(sortE, 'weight')}</button></th>
        </tr></thead>
        <tbody>
          {#each edgeRows.slice(0, 2000) as l (l.source + '>' + l.target)}
            <tr class:sel={selected === l.source || selected === l.target}>
              <td><button class="cell" dir="auto" onclick={() => onselect?.(l.source)}>{nm(l.source)}</button>{#if handle(l.source) && byId.get(l.source)?.title}<div class="tiny faint"><bdi dir="ltr">@{handle(l.source)}</bdi></div>{/if}</td>
              <td class="faint">{directed ? '→' : '↔'}</td>
              <td><button class="cell" dir="auto" onclick={() => onselect?.(l.target)}>{nm(l.target)}</button>{#if handle(l.target) && byId.get(l.target)?.title}<div class="tiny faint"><bdi dir="ltr">@{handle(l.target)}</bdi></div>{/if}</td>
              {#if mode !== 'shared'}
                {@const t = byId.get(l.target)}
                <td><span class="pill" class:accent={t?.in_case || (caseId() == null && (t?.status === 'seed' || t?.status === 'watched'))}>{caseId() != null && t?.in_case ? 'in case' : t?.status || '–'}</span></td>
              {/if}
              <td><span class="types">{#each Object.entries(l.byType) as [t, w]}<span class="ty" style:--c="var(--{EDGE_COLOR_KEY[t] || 'muted'})">{typeLabel(t)}{#if !l.approx && t !== 'admin'} {num(w)}{/if}</span>{/each}</span></td>
              <td class="right num">{l.approx ? '–' : num(l.weight)}</td>
            </tr>
          {:else}
            <tr><td colspan="6" class="faint">No edges match.</td></tr>
          {/each}
        </tbody>
      </table>
      {#if edgeRows.length > 2000}<div class="tiny faint pad">Showing 2,000 of {num(edgeRows.length)}. Export CSV for all.</div>{/if}
    {:else}
      <table class="t">
        <thead><tr>
          <th scope="col" aria-sort={ariaSortOf(sortN, 'label')}><button class="th" onclick={() => sortBy('n', 'label')}>Name{arrow(sortN, 'label')}</button></th>
          <th scope="col" aria-sort={ariaSortOf(sortN, 'status')}><button class="th" onclick={() => sortBy('n', 'status')}>{mode === 'shared' ? 'Kind' : 'Status'}{arrow(sortN, 'status')}</button></th>
          {#if mode !== 'shared'}<th scope="col" class="right" aria-sort={ariaSortOf(sortN, 'subscribers')}><button class="th" onclick={() => sortBy('n', 'subscribers')}>Subscribers{arrow(sortN, 'subscribers')}</button></th>{/if}
          {#if mode === 'discovery'}<th scope="col" class="right" aria-sort={ariaSortOf(sortN, 'score')}><button class="th" onclick={() => sortBy('n', 'score')}>Score{arrow(sortN, 'score')}</button></th>{/if}
          <th scope="col" class="right" aria-sort={ariaSortOf(sortN, 'inDeg')}><button class="th" onclick={() => sortBy('n', 'inDeg')} title="Distinct nodes pointing here">In{arrow(sortN, 'inDeg')}</button></th>
          <th scope="col" class="right" aria-sort={ariaSortOf(sortN, 'inW')}><button class="th" onclick={() => sortBy('n', 'inW')} title="Total signals pointing here">In weight{arrow(sortN, 'inW')}</button></th>
          <th scope="col" class="right" aria-sort={ariaSortOf(sortN, 'outDeg')}><button class="th" onclick={() => sortBy('n', 'outDeg')}>Out{arrow(sortN, 'outDeg')}</button></th>
          <th scope="col" class="right" aria-sort={ariaSortOf(sortN, 'outW')}><button class="th" onclick={() => sortBy('n', 'outW')}>Out weight{arrow(sortN, 'outW')}</button></th>
        </tr></thead>
        <tbody>
          {#each nodeRows as n (n.id)}
            <tr class="click" class:sel={selected === n.id} onclick={() => onselect?.(n.id)}>
              <td><button class="cell" dir="auto" onclick={(e) => { e.stopPropagation(); onselect?.(n.id); }}><span class="strong">{n.label}</span></button>{#if n.handle && n.title}<div class="tiny faint"><bdi dir="ltr">@{n.handle}</bdi></div>{/if}</td>
              <td>{#if mode === 'shared'}{n.kind.replace('_', ' ')}{:else}<span class="pill" class:accent={n.core}>{caseId() != null && n.in_case ? 'in case' : n.status || '–'}</span>{/if}</td>
              {#if mode !== 'shared'}<td class="right num">{num(n.subscribers)}</td>{/if}
              {#if mode === 'discovery'}<td class="right num">{n.score ?? '–'}</td>{/if}
              <td class="right num">{num(n.inDeg)}</td><td class="right num">{num(n.inW)}</td>
              <td class="right num">{num(n.outDeg)}</td><td class="right num">{num(n.outW)}</td>
            </tr>
          {:else}
            <tr><td colspan="8" class="faint">No nodes match.</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </div>
</div>

<style>
  .nettable { display: flex; flex-direction: column; gap: 8px; height: 100%; min-height: 0; padding: 10px; }
  .table-wrap { flex: 1; min-height: 0; }
  .th { border: 0; background: none; padding: 0; font: inherit; color: inherit; font-weight: 600; }
  .th:hover { background: none; color: var(--text); }
  .cell { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 550; text-align: start; white-space: normal; }
  .cell:hover { background: none; text-decoration: underline; }
  .strong { font-weight: 550; }
  .types { display: flex; flex-wrap: wrap; gap: 4px; }
  .ty { font-size: 11px; padding: 0 6px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); color: var(--c); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .pad { padding: 6px 10px; }
</style>
