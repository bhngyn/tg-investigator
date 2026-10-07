<script>
  // Network: three questions, one canvas.
  //   Amplification  — who forwards / mentions / links whom, and how much
  //   Shared people  — accounts, channel identities and forward sources seen in several comment sections
  //   Discovery      — channels outside the case that the case's channels point at
  // Filters live in the URL query (setQuery) so a reload keeps the view.
  import { untrack } from 'svelte';
  import { get } from '../lib/api.js';
  import { route, setQuery, caseId, href, go } from '../lib/router.svelte.js';
  import { app, currentCase, toast } from '../lib/store.svelte.js';
  import { num, compact } from '../lib/fmt.js';
  import { EDGE_TYPES, EDGE_LABEL, fetchGraph, buildAmplification, buildDiscovery, buildShared, annotateDegrees } from '../lib/net-data.js';
  import { EDGE_COLOR_KEY, dominantType, mix } from '../lib/net-theme.js';
  import NetCanvas from '../components/NetCanvas.svelte';
  import NetPanel from '../components/NetPanel.svelte';
  import NetTable from '../components/NetTable.svelte';
  import Empty from '../components/Empty.svelte';
  import Loading from '../components/Loading.svelte';

  const MODES = [
    { id: 'amp', label: 'Amplification', q: 'Who forwards, mentions and links to whom — and how often?' },
    { id: 'shared', label: 'Shared people', q: 'Which accounts, channel identities and forward sources turn up in several comment sections?' },
    { id: 'discovery', label: 'Discovery', q: 'Which channels outside the case do the case’s channels keep pointing at?' },
  ];
  const ALL_TYPES = EDGE_TYPES.map((t) => t.id);

  // ---- URL-backed view state ------------------------------------------------------------
  const Q = route.query;
  let mode = $state(['amp', 'shared', 'discovery'].includes(Q.mode) ? Q.mode : 'amp');
  let types = $state(Q.types ? Q.types.split(',').filter((t) => ALL_TYPES.includes(t)) : ALL_TYPES.slice());
  let minWeight = $state(Math.max(1, Number(Q.minw) || 1));
  let showDismissed = $state(Q.dismissed === '1');
  let includeUsers = $state(Q.users === '1');
  let coreOnly = $state(Q.scope === 'core');
  let sizeBy = $state(Q.size === 'deg' ? 'deg' : 'subs');
  let viewAs = $state(Q.view === 'table' ? 'table' : 'graph');
  let candLimit = $state([30, 60, 120, 250].includes(Number(Q.limit)) ? Number(Q.limit) : 60);
  let sharedLayout = $state(Q.layout === 'proj' ? 'proj' : 'bip');
  let sharedLimit = $state(150);
  let selected = $state(Q.sel || null);
  let frozen = $state(false);

  $effect(() => {
    const patch = {
      mode: mode === 'amp' ? null : mode,
      types: types.length === ALL_TYPES.length ? null : types.join(',') || 'none',
      minw: minWeight > 1 ? minWeight : null, dismissed: showDismissed ? '1' : null, users: includeUsers ? '1' : null,
      scope: coreOnly ? 'core' : null, size: sizeBy === 'deg' ? 'deg' : null, view: viewAs === 'table' ? 'table' : null,
      limit: candLimit !== 60 ? candLimit : null, layout: sharedLayout === 'proj' ? 'proj' : null, sel: selected,
    };
    untrack(() => setQuery(patch));
  });

  // ---- data -----------------------------------------------------------------------------
  const cid = caseId();
  const cur = $derived(currentCase());
  let raw = $state.raw(null);           // merged /api/graph (all types, nothing hidden server-side)
  let candidates = $state.raw(null);    // /api/discovery/candidates
  let overlap = $state.raw(null);       // /api/overlap
  let loading = $state(false);
  let error = $state(null);
  let seq = 0;

  async function loadGraph() {
    const my = ++seq;
    loading = true; error = null;
    try {
      const r = await fetchGraph({ types: ALL_TYPES, caseId: cid, hideDismissed: false, channelsOnly: false });
      if (my === seq) raw = r;
    } catch (e) { if (my === seq) error = e.message; }
    if (my === seq) loading = false;
  }
  let cseq = 0;
  async function loadCandidates() {
    const my = ++cseq;
    try {
      const r = await get('/api/discovery/candidates', { case: cid ?? undefined, limit: candLimit, users: includeUsers ? 1 : undefined });
      if (my === cseq) candidates = r;
    } catch (e) { if (my === cseq) error = e.message; }
  }
  async function loadOverlap() {
    try { overlap = await get('/api/overlap', { case: cid ?? undefined }); } catch (e) { error = e.message; }
  }

  $effect(() => { void app.jobsTick; untrack(loadGraph); });
  $effect(() => { if (mode === 'discovery') { void candLimit; void includeUsers; void app.jobsTick; untrack(loadCandidates); } });
  $effect(() => { if (mode === 'shared') { void app.jobsTick; untrack(loadOverlap); } });

  // ---- client-side filtering -----------------------------------------------------------------
  const typeCounts = $derived.by(() => {
    const c = Object.fromEntries(ALL_TYPES.map((t) => [t, 0]));
    for (const l of raw?.links || []) for (const t in l.byType) c[t]++;
    return c;
  });
  const presentTypes = $derived(EDGE_TYPES.filter((t) => typeCounts[t.id] > 0));
  const absentTypes = $derived(EDGE_TYPES.filter((t) => !typeCounts[t.id]));

  const filtered = $derived.by(() => {
    if (!raw) return null;
    const hidden = new Set(raw.nodes.filter((n) => (!showDismissed && n.status === 'dismissed') || (!includeUsers && (n.type === 'user' || n.type === 'bot'))).map((n) => n.username));
    const sel = new Set(types);
    const links = [];
    for (const l of raw.links) {
      if (hidden.has(l.source) || hidden.has(l.target)) continue;
      let w = 0; const bt = {};
      for (const t in l.byType) if (sel.has(t)) { bt[t] = l.byType[t]; w += l.byType[t]; }
      if (w) links.push({ source: l.source, target: l.target, weight: w, byType: bt });
    }
    return { nodes: raw.nodes.filter((n) => !hidden.has(n.username)), links };
  });

  const maxWeight = $derived(Math.max(1, ...(filtered?.links || []).map((l) => l.weight)));

  const view = $derived.by(() => {
    let v = null;
    if (mode === 'amp' && filtered) v = buildAmplification(filtered, { caseId: cid, coreOnly, minWeight });
    else if (mode === 'discovery' && filtered && candidates) v = buildDiscovery(filtered, candidates, { caseId: cid, minWeight });
    else if (mode === 'shared' && overlap) v = buildShared(overlap, { layout: sharedLayout === 'proj' ? 'projected' : 'bipartite', limit: sharedLimit });
    if (!v) return null;
    for (const l of v.links) {
      const t = l.admin ? 'admin' : l.rel || dominantType(l);
      l.color = EDGE_COLOR_KEY[t] || 'muted';
      l.dashed = l.admin || (Object.keys(l.byType).length === 1 && l.byType.similar != null);
    }
    annotateDegrees(v.nodes, v.links);
    return v;
  });
  const byId = $derived(new Map((view?.nodes || []).map((n) => [n.id, n])));
  const selNode = $derived(selected != null ? byId.get(selected) || null : null);

  // ---- visual encoding ----------------------------------------------------------------------
  const rSubs = (n) => (n.kind === 'channel' || n.kind === 'candidate' || n.kind === 'section') && n.subscribers
    ? Math.min(17, 4 + 2.6 * Math.max(0, Math.log10(n.subscribers + 1) - 2)) : n.kind === 'channel' || n.kind === 'candidate' ? 4 : 4 + 1.5 * Math.sqrt(n.inDeg + n.outDeg);
  const rDeg = (n) => Math.min(20, 3.5 + 2.4 * Math.sqrt((n.inDeg || 0) + (n.outDeg || 0)));
  const radius = $derived(sizeBy === 'deg' ? rDeg : rSubs);

  const shapeOf = (n) => (n.type === 'group' || n.type === 'supergroup' ? 'square' : n.type === 'user' || n.type === 'bot' ? 'diamond' : 'circle');
  function styleAmp(n, p) {
    const shape = shapeOf(n);
    if (n.status === 'dismissed') return { fill: p.bad, hollow: true, shape };
    if (n.core) return { fill: p.accent, shape };
    if (n.status === 'seed' || n.status === 'watched') return { fill: p.info, shape };
    if (n.posts > 0 || n.crawled) return { fill: p.muted, shape };
    return { fill: p.faint, hollow: false, shape, stroke: null };
  }
  function styleDisc(n, p) {
    if (n.kind !== 'candidate') return { fill: p.accent, shape: shapeOf(n) };
    return { fill: mix(p.lineStrong, p.warn, 0.25 + 0.75 * (n.scoreN || 0)), shape: shapeOf(n), stroke: n.rank <= 10 ? p.warn : null };
  }
  function styleShared(n, p) {
    if (n.kind === 'section') return { fill: p.accent, shape: 'circle' };
    if (n.kind === 'account') return { fill: p.info, shape: 'circle', stroke: n.cross_admin ? p.bad : null };
    if (n.kind === 'identity') return { fill: p.ok, shape: 'square' };
    return { fill: p.warn, shape: 'diamond' };
  }
  const style = $derived(mode === 'amp' ? styleAmp : mode === 'discovery' ? styleDisc : styleShared);
  const edgeColor = (l, p) => p[l.color] || p.muted;

  // ---- interaction ---------------------------------------------------------------------------
  let canvasRef = $state();
  let search = $state('');
  let searchOpen = $state(false);
  let highlight = $state(null);
  const matches = $derived.by(() => {
    const s = search.trim().toLowerCase().replace(/^@/, '');
    if (!s || !view) return [];
    return view.nodes.filter((n) => String(n.label).toLowerCase().includes(s) || String(n.handle || '').toLowerCase().includes(s))
      .sort((a, b) => (b.inW + b.outW) - (a.inW + a.outW)).slice(0, 8);
  });
  function focusNode(id) {
    if (!byId.has(id)) {
      toast(`${String(id).startsWith('acct:') ? 'That account' : '@' + id} isn't in the current view. Loosen the filters or switch mode.`, 'info');
      return;
    }
    selected = id; highlight = id;
    if (viewAs === 'graph') canvasRef?.focus(id);
    setTimeout(() => { if (highlight === id) highlight = null; }, 2500);
  }
  function pickMatch(n) { search = ''; searchOpen = false; focusNode(n.id); }
  function searchKey(e) {
    if (e.key === 'Enter' && matches[0]) { e.preventDefault(); pickMatch(matches[0]); }
    if (e.key === 'Escape') { search = ''; searchOpen = false; e.currentTarget.blur(); }
  }
  function onKey(e) {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.target.closest?.('input, textarea, select, [contenteditable]')) return;
    if (e.key === 'Escape' && selected != null) selected = null;
  }

  function toggleType(t) { types = types.includes(t) ? types.filter((x) => x !== t) : [...types, t]; }
  function onchange(node, patch) {
    if (patch.in_case) { loadGraph(); if (mode === 'discovery') loadCandidates(); return; }
    if (raw) raw = { ...raw, nodes: raw.nodes.map((n) => (n.username === node.handle ? { ...n, ...patch } : n)) };
    if (candidates && patch.status === 'dismissed') candidates = candidates.filter((c) => c.username !== node.handle);
    if (patch.status === 'dismissed' && !showDismissed) selected = null;
  }

  const coreLabel = cid != null ? 'Case only' : 'Seed & watched';
  const modeInfo = $derived(MODES.find((m) => m.id === mode));

  // Empty-state reasons
  const overlapGroups = $derived(overlap?.groups?.length || 0);
  const overlapShared = $derived((overlap?.accounts?.length || 0) + (overlap?.channel_identities?.length || 0) + (overlap?.forward_sources?.length || 0) + (overlap?.cross_admins?.length || 0));

  // The key can cover part of the graph: let the user fold it away (remembered per browser).
  let legendOpen = $state((() => { try { return localStorage.getItem('ti.netLegend') !== '0'; } catch { return true; } })());
  function toggleLegend() {
    legendOpen = !legendOpen;
    try { localStorage.setItem('ti.netLegend', legendOpen ? '1' : '0'); } catch { /* storage blocked */ }
  }
</script>

<svelte:window onkeydown={onKey} />

{#snippet tip(n)}
  <div class="tip-t" dir="auto">{n.label}</div>
  {#if n.handle && n.title}<div class="tiny faint"><bdi dir="ltr">@{n.handle}</bdi></div>{/if}
  <div class="tiny muted">
    {#if n.kind === 'account'}Account · {n.sections?.length} sections · {num(n.total)} comments{#if n.cross_admin} · <b class="bad">admin elsewhere</b>{/if}
    {:else if n.kind === 'identity'}Channel identity · {n.sections?.length} sections
    {:else if n.kind === 'forward_source'}Forward source · {n.sections?.length} sections · {num(n.total)} forwards
    {:else if n.kind === 'section'}Comment section · {num(n.commenters)} commenters
    {:else}
      {cid != null && n.in_case ? 'In this case' : n.status || '–'}{#if n.type && n.type !== 'channel'} · {n.type}{/if} · {compact(n.subscribers)} subs
      <br />in {n.inDeg} ({num(n.inW)}) · out {n.outDeg} ({num(n.outW)}){#if n.kind === 'candidate'} · score {n.score}{/if}
    {/if}
  </div>
{/snippet}

<div class="netpage">
  <div class="head">
    <div class="titles">
      <h1>Network</h1>
      <p class="muted small">{modeInfo.q}</p>
    </div>
    <div class="seg modes" role="group" aria-label="Question">
      {#each MODES as m}
        <button aria-pressed={mode === m.id} class:on={mode === m.id} onclick={() => { mode = m.id; selected = null; }}>{m.label}</button>
      {/each}
    </div>
    <span class="spacer"></span>
    <div class="search">
      <input type="search" data-page-search placeholder="Find a node…  ( / )" bind:value={search}
        onfocus={() => (searchOpen = true)} onblur={() => setTimeout(() => (searchOpen = false), 150)} onkeydown={searchKey} aria-label="Find a node" />
      {#if searchOpen && matches.length}
        <div class="sugg panel">
          {#each matches as n (n.id)}
            <button class="sitem" onmousedown={(e) => e.preventDefault()} onclick={() => pickMatch(n)}>
              <span dir="auto" class="ellipsis">{n.label}</span>{#if n.handle && n.title}<span class="tiny faint"><bdi dir="ltr">@{n.handle}</bdi></span>{/if}
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <div class="seg" role="group" aria-label="View as">
      <button class:on={viewAs === 'graph'} aria-pressed={viewAs === 'graph'} onclick={() => (viewAs = 'graph')}>Graph</button>
      <button class:on={viewAs === 'table'} aria-pressed={viewAs === 'table'} onclick={() => (viewAs = 'table')} title="Sortable list of every edge — easier to cite">Table</button>
    </div>
  </div>

  <div class="filters">
    {#if mode !== 'shared'}
      <div class="fgroup types" role="group" aria-label="Edge types">
        {#each presentTypes as t (t.id)}
          <button class="chip" class:on={types.includes(t.id)} aria-pressed={types.includes(t.id)} style:--c="var(--{EDGE_COLOR_KEY[t.id]})" onclick={() => toggleType(t.id)} title={t.help}>
            <span class="sw" class:dash={t.id === 'similar'}></span>{t.label} <span class="faint num">{num(typeCounts[t.id])}</span>
          </button>
        {/each}
        {#if raw && absentTypes.length}
          <span class="tiny faint" title={absentTypes.map((t) => `${t.label}: ${t.help}`).join('\n')}>none yet: {absentTypes.map((t) => t.label.toLowerCase()).join(', ')}</span>
        {/if}
      </div>
      <div class="fgroup">
        <label class="check small" title="Hide edges with fewer signals than this (summed over the selected types)">Min weight
          <input type="number" min="1" max={maxWeight} bind:value={minWeight} style="width:62px" /></label>
        {#if mode === 'amp'}
          <div class="seg sm" role="group" aria-label="Which channels">
            <button class="sm" class:on={coreOnly} aria-pressed={coreOnly} onclick={() => (coreOnly = true)}>{coreLabel}</button>
            <button class="sm" class:on={!coreOnly} aria-pressed={!coreOnly} onclick={() => (coreOnly = false)}>+ direct neighbours</button>
          </div>
        {:else}
          <label class="check small">Top
            <select bind:value={candLimit}>{#each [30, 60, 120, 250] as n}<option value={n}>{n}</option>{/each}</select> candidates</label>
          <a class="small" href={href('discovery')}>Ranked list & actions →</a>
        {/if}
        {#if mode === 'amp'}<label class="check small" title="Channels you dismissed (hidden from discovery in every case)"><input type="checkbox" bind:checked={showDismissed} /> show dismissed</label>{/if}
        <label class="check small" title="Telegram users and bots mentioned in posts"><input type="checkbox" bind:checked={includeUsers} /> include users & bots</label>
      </div>
    {:else}
      <div class="fgroup">
        <div class="seg sm" role="group" aria-label="Layout">
          <button class="sm" class:on={sharedLayout === 'bip'} aria-pressed={sharedLayout === 'bip'} onclick={() => (sharedLayout = 'bip')} title="People and identities linked to the comment sections they appear in">People ↔ sections</button>
          <button class="sm" class:on={sharedLayout === 'proj'} aria-pressed={sharedLayout === 'proj'} onclick={() => (sharedLayout = 'proj')} title="Sections joined by how many commenters they share">Sections only (shared commenters)</button>
        </div>
        {#if sharedLayout === 'bip'}
          <label class="check small">Show top <select bind:value={sharedLimit}>{#each [50, 150, 400, 1000] as n}<option value={n}>{n}</option>{/each}</select></label>
        {/if}
      </div>
    {/if}
    <div class="fgroup">
      <span class="small muted">Size by</span>
      <div class="seg sm" role="group" aria-label="Node size">
        <button class="sm" class:on={sizeBy === 'subs'} aria-pressed={sizeBy === 'subs'} onclick={() => (sizeBy = 'subs')}>Subscribers</button>
        <button class="sm" class:on={sizeBy === 'deg'} aria-pressed={sizeBy === 'deg'} onclick={() => (sizeBy = 'deg')}>Connections</button>
      </div>
    </div>
  </div>

  <div class="stage" class:withpanel={!!selNode}>
    <div class="canvasbox panel">
      {#if error}
        <div class="center"><div class="callout bad" role="alert">{error} <button class="sm" onclick={() => (mode === 'shared' ? loadOverlap() : loadGraph())}>Retry</button></div></div>
      {:else if !view}
        <div class="center"><Loading /></div>
      {:else if mode === 'shared' && overlapGroups < 2}
        <Empty title="Needs comment sections from at least two channels">
          This view links comment sections through the people in them: accounts commenting in several sections, channels posting
          in comments under their own identity, the same forward source, and admins of one section commenting in another.
          {overlapGroups === 0 ? 'No comment sections have been collected' : 'Only one comment section has been collected'}{cid != null ? ' in this case' : ''} yet.
          Comment collection uses your logged-in Telegram account; start it from a channel's page or Collection.
          {#snippet action()}<a class="btn" href={href('collect')}>Go to Collection</a>{/snippet}
        </Empty>
      {:else if mode === 'shared' && !overlapShared && sharedLayout === 'bip'}
        <Empty title="No shared people found yet">
          {overlapGroups} comment sections are collected, but no account, channel identity or forward source appears in more than one of them.
          Try the “Sections only” layout, or collect more comments.
        </Empty>
      {:else if mode !== 'shared' && !types.length}
        <Empty title="No edge types selected">Pick at least one edge type above.</Empty>
      {:else if mode === 'discovery' && candidates && !candidates.length}
        <Empty title="No candidates yet">
          Candidates are channels outside {cid != null ? 'this case' : 'your seed & watched channels'} that they forward, mention or link to.
          Collect posts from {cid != null ? 'the case’s channels' : 'your channels'} first, or run a logged-in discovery search.
          {#snippet action()}<a class="btn" href={href('discovery')}>Open Discover</a>{/snippet}
        </Empty>
      {:else if mode === 'amp' && !view.links.length && !raw?.links.length}
        <Empty title="No links collected yet">
          Links appear once channels are crawled: every forward, @mention and t.me link in a post becomes an edge.
          {#if cid != null && cur && !cur.channels}This case has no channels yet.{/if}
          {#snippet action()}<a class="btn" href={href(cid != null && cur && !cur.channels ? 'add' : 'collect')}>{cid != null && cur && !cur.channels ? 'Add channels' : 'Go to Collection'}</a>{/snippet}
        </Empty>
      {:else if viewAs === 'table'}
        {#key mode}
          <NetTable nodes={view.nodes} links={view.links} {byId} {mode} {selected} onselect={(id) => (selected = id)} />
        {/key}
      {:else}
        <NetCanvas bind:this={canvasRef} nodes={view.nodes} links={view.links} {style} {edgeColor} {radius} {selected} {highlight} {frozen}
          directed={!(mode === 'shared' && sharedLayout === 'proj')} tooltip={tip}
          onselect={(n) => (selected = n ? n.id : null)} onopen={(n) => { if (n.handle && n.kind !== 'account' && n.kind !== 'identity' && n.kind !== 'forward_source') go(href('channel', n.handle)); }} />

        <div class="tools">
          <button class="sm icon" onclick={() => canvasRef?.zoomBy(1.3)} title="Zoom in" aria-label="Zoom in">＋</button>
          <button class="sm icon" onclick={() => canvasRef?.zoomBy(1 / 1.3)} title="Zoom out" aria-label="Zoom out">−</button>
          <button class="sm" onclick={() => canvasRef?.fit()} title="Fit everything in view">Fit</button>
          <button class="sm" class:on={frozen} aria-pressed={frozen} onclick={() => (frozen = !frozen)} title="Stop the layout moving (drag still works)">{frozen ? 'Frozen' : 'Freeze'}</button>
          {#if !frozen}<button class="sm" onclick={() => canvasRef?.reheat()} title="Shake the layout">Re-layout</button>{/if}
        </div>

        <div class="legend">
          <div class="stats-line tiny muted row tight">
            <span>{num(view.nodes.length)} nodes · {num(view.links.length)} edges{#if view.truncated} · {num(view.truncated)} weaker entities hidden{/if}{#if loading} · refreshing…{/if}</span>
            <button class="sm ghost lg-toggle" onclick={toggleLegend} aria-expanded={legendOpen} title={legendOpen ? 'Hide legend' : 'Show legend'}>{legendOpen ? 'Hide key ▾' : 'Key ▸'}</button>
          </div>
          {#if legendOpen}
          {#if mode === 'amp'}
            <div class="lg"><i class="dot" style:background="var(--accent)"></i>{cid != null ? 'In this case' : 'Seed / watched'}</div>
            {#if cid != null}<div class="lg"><i class="dot" style:background="var(--info)"></i>Seed / watched, other case</div>{/if}
            <div class="lg"><i class="dot" style:background="var(--muted)"></i>Candidate, posts collected</div>
            <div class="lg"><i class="dot" style:background="var(--faint)"></i>Candidate, not collected</div>
            {#if showDismissed}<div class="lg"><i class="dot hollow" style:border-color="var(--bad)"></i>Dismissed</div>{/if}
          {:else if mode === 'discovery'}
            <div class="lg"><i class="dot" style:background="var(--accent)"></i>{cid != null ? 'In this case' : 'Seed / watched'}</div>
            <div class="lg"><i class="ramp"></i>Candidate score, low → high (ringed: top 10)</div>
          {:else}
            <div class="lg"><i class="dot" style:background="var(--accent)"></i>Comment section (channel)</div>
            {#if sharedLayout === 'bip'}
              <div class="lg"><i class="dot" style:background="var(--info)"></i>Account (<span class="bad">red ring</span>: admin in another section)</div>
              <div class="lg"><i class="sq" style:background="var(--ok)"></i>Channel posting as itself in comments</div>
              <div class="lg"><i class="dia" style:background="var(--warn)"></i>Forward source</div>
            {/if}
          {/if}
          {#if mode !== 'shared'}
            <div class="lg tiny faint"><i class="sq" style:background="var(--faint)"></i>group <i class="dia" style:background="var(--faint)"></i>user / bot</div>
            <div class="tiny faint expl">Arrow A → B: A shared B's content or pointed at B. Line width = number of signals; colour = main edge type. Dashed = Telegram's “similar” suggestion.</div>
          {:else if sharedLayout === 'bip'}
            <div class="tiny faint expl">Line: the person appears in that section (width = comments where known). Dashed red: admin of that section.</div>
          {:else}
            <div class="tiny faint expl">Line width = number of accounts commenting in both sections.</div>
          {/if}
          <div class="tiny faint">Scroll to zoom · drag to pan · double-click to open</div>
          {/if}
        </div>
      {/if}
    </div>
    {#if selNode}
      <NetPanel node={selNode} links={view?.links || []} {byId} {mode} onfocus={focusNode} onclose={() => (selected = null)} {onchange} />
    {/if}
  </div>
</div>

<style>
  .netpage { display: flex; flex-direction: column; height: 100%; min-height: 520px; padding: 14px 18px 14px; gap: 10px; }
  /* Narrow or zoomed-in windows: let the page scroll as one instead of a scroll area inside a scroll area. */
  @media (max-width: 900px) { .netpage { height: auto; min-height: 0; } .canvasbox { min-height: 60vh; } }
  .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .titles p { margin: 2px 0 0; }
  .modes button { font-weight: 600; }
  .search { position: relative; }
  .search input { width: min(230px, 100%); }
  .sugg { position: absolute; top: calc(100% + 4px); right: 0; width: min(300px, 90vw); z-index: 30; box-shadow: var(--shadow-lg); padding: 4px; }
  .sitem { display: flex; flex-direction: column; align-items: flex-start; gap: 0; width: 100%; border: 0; background: none; padding: 5px 8px; text-align: start; }
  .sitem:hover { background: var(--panel-2); }
  .filters { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; }
  .fgroup { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .types .chip { color: var(--muted); }
  .types .chip.on { color: var(--text); border-color: var(--c); background: color-mix(in srgb, var(--c) 10%, var(--panel)); }
  .sw { width: 14px; height: 3px; border-radius: 2px; background: var(--c); display: inline-block; }
  .sw.dash { background: repeating-linear-gradient(90deg, var(--c) 0 4px, transparent 4px 7px); }
  .chip:not(.on) .sw { opacity: .35; }
  .stage { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr; gap: 10px; }
  .stage.withpanel { grid-template-columns: 1fr 330px; }
  .canvasbox { position: relative; min-height: 360px; overflow: hidden; }
  .center { position: absolute; inset: 0; display: grid; place-items: center; padding: 20px; }
  .tools { position: absolute; top: 8px; right: 8px; display: flex; gap: 4px; z-index: 4; }
  .tools button { background: var(--panel); }
  .legend { position: absolute; left: 8px; bottom: 8px; z-index: 4; background: color-mix(in srgb, var(--panel) 92%, transparent); border: 1px solid var(--line);
    border-radius: var(--radius-sm); padding: 7px 9px; font-size: 12px; max-width: 330px; display: flex; flex-direction: column; gap: 3px; }
  .lg { display: flex; align-items: center; gap: 6px; }
  .lg-toggle { margin-left: auto; padding: 0 6px; }
  .lg i { display: inline-block; flex: none; }
  .dot { width: 10px; height: 10px; border-radius: 50%; }
  .dot.hollow { border: 2px solid; background: var(--panel); }
  .sq { width: 9px; height: 9px; }
  .dia { width: 8px; height: 8px; transform: rotate(45deg); margin: 0 1px; }
  .ramp { width: 60px; height: 9px; border-radius: 5px; background: linear-gradient(90deg, color-mix(in srgb, var(--warn) 25%, var(--line-strong)), var(--warn)); }
  .expl { max-width: 310px; line-height: 1.35; }
  .tip-t { font-weight: 600; font-size: 12.5px; }
  .bad { color: var(--bad); }
  @media (max-width: 900px) {
    .stage.withpanel { grid-template-columns: 1fr; grid-template-rows: 1fr auto; }
    .legend { display: none; }
  }
</style>
