<script>
  // Details + actions for the node selected in the Network graph.
  // Props: node, links (current view, source/target are ids), byId (Map id -> node), mode,
  //        onfocus(id), onclose(), onchange(node, patch)  (patch is applied by the parent)
  import { app, attempt, loadCases, toast } from '../lib/store.svelte.js';
  import { post } from '../lib/api.js';
  import { href, caseId } from '../lib/router.svelte.js';
  import { num, compact, day, dt } from '../lib/fmt.js';
  import { EDGE_LABEL } from '../lib/net-data.js';
  import { EDGE_COLOR_KEY } from '../lib/net-theme.js';

  let { node, links = [], byId, mode = 'amp', onfocus, onclose, onchange } = $props();

  const cid = $derived(caseId());
  const isChannel = $derived(node && (node.kind === 'channel' || node.kind === 'candidate' || node.kind === 'section'));
  // Undirected views (shared commenters) list every neighbour once, as "in".
  const undirected = $derived(links.length > 0 && links[0].undirected);
  const inbound = $derived(undirected
    ? links.filter((l) => l.source === node?.id || l.target === node?.id).map((l) => (l.target === node.id ? l : { ...l, source: l.target, target: l.source })).sort((a, b) => b.weight - a.weight)
    : links.filter((l) => l.target === node?.id).sort((a, b) => b.weight - a.weight));
  const outbound = $derived(undirected ? [] : links.filter((l) => l.source === node?.id).sort((a, b) => b.weight - a.weight));
  const sum = (ls) => ls.reduce((s, l) => s + l.weight, 0);

  let role = $state('watched');
  let targetCase = $state(null);
  let busy = $state(false);
  const caseForAdd = $derived(cid ?? targetCase ?? app.cases[0]?.id ?? null);
  const caseName = (id) => app.cases.find((c) => c.id === id)?.name || `case ${id}`;

  const typeLabel = (t) => EDGE_LABEL[t] || t.replace(/_/g, ' ');
  const nameOf = (id) => byId.get(id)?.label || (String(id).includes(':') ? id : '@' + id);

  async function addToCase() {
    if (caseForAdd == null) return;
    busy = true;
    const r = await attempt(() => post(`/api/cases/${caseForAdd}/channels`, { add: [node.handle], role }));
    busy = false;
    if (!r) return;
    if (r.invalid?.length) { toast(`Not a valid handle: ${r.invalid.join(', ')}`, 'error'); return; }
    toast(r.added?.length ? `Added @${node.handle} to ${caseName(caseForAdd)} as ${role}` : `@${node.handle} is already in ${caseName(caseForAdd)}`, 'ok');
    const patch = { status: node.status === 'seed' ? 'seed' : 'watched' };
    if (caseForAdd === cid) Object.assign(patch, { in_case: true, core: true });
    onchange?.(node, patch);
    loadCases();
  }
  async function setStatus(status) {
    busy = true;
    const r = await attempt(() => post(`/api/channels/${encodeURIComponent(node.handle)}/status`, { status, reason: status === 'dismissed' ? 'dismissed from network view' : null }));
    busy = false;
    if (r === undefined) return;
    toast(status === 'dismissed' ? `Dismissed @${node.handle} (everywhere)` : `@${node.handle} is a candidate again`, 'ok');
    onchange?.(node, { status });
  }
</script>

{#if node}
  <aside class="netpanel panel">
    <header>
      <div class="ttl">
        <h2 dir="auto" class="nm">{node.title || node.label}</h2>
        {#if node.handle}<div class="small faint"><bdi dir="ltr">@{node.handle}</bdi></div>{/if}
      </div>
      <button class="ghost icon" onclick={onclose} aria-label="Close details" title="Close (Esc)">✕</button>
    </header>

    <div class="body">
      <div class="row tight">
        {#if node.kind === 'account'}<span class="pill info">account</span>{#if node.bot}<span class="pill">bot</span>{/if}{#if node.cross_admin}<span class="pill bad">admin elsewhere</span>{/if}
        {:else if node.kind === 'identity'}<span class="pill ok">channel identity</span>
        {:else if node.kind === 'forward_source'}<span class="pill warn">forward source</span>
        {:else}
          {#if node.status}<span class="pill" class:accent={node.status === 'seed' || node.status === 'watched'} class:bad={node.status === 'dismissed'}>{node.status}</span>{/if}
          {#if cid != null}{#if node.in_case}<span class="pill accent">in this case</span>{:else}<span class="pill">not in this case</span>{/if}{/if}
          {#if node.type && node.type !== 'channel'}<span class="pill warn">{node.type}</span>{/if}
          {#if node.kind === 'candidate'}<span class="pill warn">candidate #{node.rank}</span>{/if}
        {/if}
      </div>

      {#if isChannel}
        <dl class="kv">
          <dt>Subscribers</dt><dd class="num">{num(node.subscribers)}</dd>
          <dt>Posts collected</dt><dd class="num">{num(node.posts)}{#if !node.crawled && !node.posts} <span class="faint">(not collected)</span>{/if}</dd>
          {#if node.kind === 'section'}<dt>Commenters</dt><dd class="num">{num(node.commenters)}</dd>{/if}
          {#if mode !== 'shared'}
            <dt>Pointed at by</dt><dd class="num">{inbound.length} channel{inbound.length === 1 ? '' : 's'} · {num(sum(inbound))} signals</dd>
            <dt>Points at</dt><dd class="num">{outbound.length} channel{outbound.length === 1 ? '' : 's'} · {num(sum(outbound))} signals</dd>
          {/if}
        </dl>
      {:else}
        <dl class="kv">
          {#if node.phone_country}<dt>Phone country</dt><dd>{node.phone_country}</dd>{/if}
          {#if node.total != null}<dt>{node.kind === 'forward_source' ? 'Forwards' : 'Comments'}</dt><dd class="num">{num(node.total)}{#if node.forwarders} <span class="faint">by {num(node.forwarders)} accounts</span>{/if}</dd>{/if}
          <dt>Seen in</dt><dd>{node.sections?.length || 0} comment sections</dd>
          {#if node.first}<dt>First / last</dt><dd class="small">{day(node.first)} → {day(node.last)}</dd>{/if}
        </dl>
        {#if node.admin_of?.length}
          <div class="section-title">Admin of</div>
          <ul class="plain">
            {#each node.admin_of as a}
              <li><button class="linkish" onclick={() => onfocus?.(a.channel)}><bdi dir="ltr">@{a.channel}</bdi></button> <span class="small muted">{a.role}{a.rank ? ` · “${a.rank}”` : ''}</span>
                {#if a.removed_detected}<span class="pill bad" title="Removed as admin, detected {dt(a.removed_detected)}">removed</span>{/if}</li>
            {/each}
          </ul>
        {/if}
      {/if}

      {#if node.kind === 'candidate'}
        <div class="section-title">Why it's suggested · score {node.score}</div>
        <ul class="plain small">{#each node.reasons || [] as r}<li>{r}</li>{/each}</ul>
      {/if}

      {#if inbound.length}
        <div class="section-title">{undirected ? 'Shares commenters with' : mode === 'shared' ? 'Linked people' : 'Pointed at by (in)'} · {inbound.length}</div>
        <ul class="edges">
          {#each inbound.slice(0, 60) as l (l.source)}
            <li>
              <button class="linkish ellipsis" dir="auto" onclick={() => onfocus?.(l.source)}>{nameOf(l.source)}</button>
              <span class="types">{#each Object.entries(l.byType) as [t, w]}<span class="ty" style:--c="var(--{EDGE_COLOR_KEY[t] || 'muted'})" title={typeLabel(t)}>{typeLabel(t)}{#if !l.approx && t !== 'admin'} {num(w)}{/if}</span>{/each}</span>
            </li>
          {/each}
          {#if inbound.length > 60}<li class="faint small">+{inbound.length - 60} more (see table view)</li>{/if}
        </ul>
      {/if}
      {#if outbound.length}
        <div class="section-title">{mode === 'shared' ? 'Comment sections' : 'Points at (out)'} · {outbound.length}</div>
        <ul class="edges">
          {#each outbound.slice(0, 60) as l (l.target)}
            <li>
              <button class="linkish ellipsis" dir="auto" onclick={() => onfocus?.(l.target)}>{nameOf(l.target)}</button>
              <span class="types">{#each Object.entries(l.byType) as [t, w]}<span class="ty" style:--c="var(--{EDGE_COLOR_KEY[t] || 'muted'})" title={typeLabel(t)}>{typeLabel(t)}{#if !l.approx && t !== 'admin'} {num(w)}{/if}</span>{/each}</span>
            </li>
          {/each}
          {#if outbound.length > 60}<li class="faint small">+{outbound.length - 60} more (see table view)</li>{/if}
        </ul>
      {/if}
      {#if !inbound.length && !outbound.length}<p class="small faint">No edges in the current view. Loosen the filters to see more.</p>{/if}
    </div>

    <footer>
      {#if isChannel && node.handle}
        <div class="row tight">
          <a class="btn sm primary" href={href('channel', node.handle)}>Open channel page</a>
          <a class="btn sm" href={href('add', { handles: node.handle })} title="Choose what to collect for this channel">Collect…</a>
          <a class="btn sm" href="https://t.me/s/{node.handle}" target="_blank" rel="noreferrer noopener" title="Opens t.me in your browser (visible to Telegram)">t.me ↗</a>
        </div>
        {#if !(cid != null && node.in_case)}
          <div class="row tight add">
            {#if cid == null}
              {#if app.cases.length}
                <select bind:value={targetCase} aria-label="Case to add to">
                  {#each app.cases as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
                </select>
              {/if}
            {/if}
            <select bind:value={role} aria-label="Role in the case" title="seed: a core channel · watched: follow it · context: background only">
              <option value="seed">seed</option><option value="watched">watched</option><option value="context">context</option>
            </select>
            <button class="sm" disabled={busy || caseForAdd == null} onclick={addToCase}>{cid != null ? 'Add to this case' : 'Add to case'}</button>
          </div>
        {/if}
        {#if node.status === 'dismissed'}
          <button class="sm ghost" disabled={busy} onclick={() => setStatus('candidate')}>Undo dismiss</button>
        {:else if node.status !== 'seed' && node.status !== 'watched' && !node.in_case}
          <button class="sm danger" disabled={busy} onclick={() => setStatus('dismissed')} title="Marks it not relevant for every case and hides it from discovery">Dismiss (all cases)</button>
        {/if}
      {:else if node.kind === 'account' && cid != null}
        <a class="btn sm" href={href('comments', { sender: node.user_id })}>Their comments</a>
      {:else if node.handle}
        <a class="btn sm" href="https://t.me/{node.handle}" target="_blank" rel="noreferrer noopener">t.me ↗</a>
      {/if}
    </footer>
  </aside>
{/if}

<style>
  .netpanel { display: flex; flex-direction: column; min-height: 0; height: 100%; overflow: hidden; }
  header { display: flex; align-items: flex-start; gap: 6px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
  .ttl { flex: 1; min-width: 0; }
  .nm { font-size: 15px; overflow-wrap: anywhere; }
  .body { flex: 1; overflow: auto; padding: 10px 12px; }
  footer { border-top: 1px solid var(--line); padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .add select { max-width: 150px; }
  .kv { display: grid; grid-template-columns: 110px 1fr; gap: 3px 10px; margin: 10px 0 0; font-size: 13px; }
  .kv dt { color: var(--muted); }
  .kv dd { margin: 0; }
  ul.plain { margin: 0; padding-left: 16px; }
  ul.edges { list-style: none; margin: 0; padding: 0; }
  ul.edges li { display: flex; flex-direction: column; gap: 2px; padding: 4px 0; border-bottom: 1px solid var(--line); }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 550; text-align: start; max-width: 100%; display: block; }
  .linkish:hover { text-decoration: underline; background: none; }
  .types { display: flex; flex-wrap: wrap; gap: 4px; }
  .ty { font-size: 11px; padding: 0 6px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); color: var(--c); white-space: nowrap; font-variant-numeric: tabular-nums; }
</style>
