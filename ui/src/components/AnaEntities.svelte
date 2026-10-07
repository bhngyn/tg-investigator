<script>
  // "What identifiers appear in the text?" Phone numbers, wallets, card numbers, unit numbers, call signs and vehicle plates
  // found by pattern in posts, comments and media text. GET /api/entities, GET /api/entities/{kind}/{value}, POST /api/entities/backfill.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { scoped, attempt, toast } from '../lib/store.svelte.js';
  import { dt, day, num, plural } from '../lib/fmt.js';
  import { highlightParts } from '../lib/highlight.js';
  import { ENTITY_KINDS, kindLabel, ENTITY_CAVEAT, listOf } from '../lib/searchparams.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import Drawer from './Drawer.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import TriageDetailDrawer from './TriageDetailDrawer.svelte';

  const Q = $derived(route.query);
  const kind = $derived(Q.kind || '');
  const LIMIT = 100;

  let qInput = $state(route.query.q || '');
  let data = $state(null), error = $state(''), loading = $state(true);
  let seq = 0;
  const key = $derived(JSON.stringify([kind, Q.q || '', scoped({})]));
  async function load() {
    const my = ++seq;
    loading = true; error = '';
    try {
      const r = await get('/api/entities', scoped({ kind: kind || undefined, q: Q.q || undefined, limit: LIMIT }));
      if (my === seq) data = r;
    } catch (e) { if (my === seq) { error = e.message; data = null; } }
    if (my === seq) loading = false;
  }
  $effect(() => { key; untrack(load); });

  const rows = $derived(listOf(data, 'entities', 'values'));
  const cnt = (r) => r.n ?? r.count ?? 0;
  const chans = (r) => (Array.isArray(r.channels) ? r.channels : []);
  const nChans = (r) => r.n_channels ?? (Array.isArray(r.channels) ? r.channels.length : 0);
  const groups = $derived.by(() => {
    const m = new Map();
    for (const r of rows) { if (!m.has(r.kind)) m.set(r.kind, []); m.get(r.kind).push(r); }
    const order = Object.keys(ENTITY_KINDS);
    return [...m.entries()].sort((a, b) => (order.indexOf(a[0]) + 1 || 99) - (order.indexOf(b[0]) + 1 || 99));
  });
  const total = $derived(data?.total ?? rows.length);
  const kindCounts = $derived(Array.isArray(data?.by_kind) ? Object.fromEntries(data.by_kind.map((x) => [x.kind, x.n])) : null);
  // scan status (what was last scanned)
  let stat = $state(null);
  const loadStat = () => get('/api/entities/status').then((r) => (stat = r)).catch(() => {});
  loadStat();
  const lastScan = $derived.by(() => { try { return stat?.backfill?.value ? JSON.parse(stat.backfill.value) : null; } catch { return null; } });
  const commit = (e) => { e?.preventDefault(); setQuery({ q: qInput.trim() || null }); };

  // ---- detail drawer -----------------------------------------------------------------------------
  let sel = $state(null), det = $state(null), detErr = $state(''), item = $state(null);
  let reveal = $state(false);
  async function openEntity(r, rev = false) {
    sel = r; det = null; detErr = ''; reveal = rev;
    try { const d = await get(`/api/entities/${encodeURIComponent(r.kind)}/${encodeURIComponent(r.value)}`, scoped(rev ? { reveal: 1 } : {})); if (sel === r) det = d; }
    catch (e) { if (sel === r) detErr = e.message; }
  }
  const items = $derived(listOf(det, 'items', 'occurrences'));
  const itemType = (i) => i.item_type || i.type || 'post';
  const itemId = (i) => i.item_id ?? i.id;
  // a hit in OCR/speech text belongs to a post, or to a comment, through its media file
  const openTarget = (i) => (itemType(i) === 'comment' ? { type: 'comment', id: itemId(i) }
    : i.post_id == null && itemType(i) === 'media_text' && i.comment_id != null ? { type: 'comment', id: i.comment_id }
    : { type: 'post', id: i.post_id ?? (itemType(i) === 'post' ? itemId(i) : null) });
  function revealCard() {
    if (confirm('Show the surrounding text for this card number?\n\nThe excerpts can contain the full card number. Card numbers are personal financial data: view it only if you need to, and do not copy it elsewhere. Showing it is recorded in the audit log.')) openEntity(sel, true);
  }

  // ---- backfill ------------------------------------------------------------------------------------
  let busy = $state(false);
  async function backfill() {
    if (!confirm('Scan all collected posts, comments and media text for identifiers?\n\nThis reads text already in the archive (nothing is fetched from Telegram and nothing original is changed) and adds derived pattern matches. It is safe to repeat; it can take a while on a large archive.')) return;
    busy = true;
    const r = await attempt(() => post('/api/entities/backfill', {}));
    busy = false;
    if (r === undefined) return;
    toast('Identifier scan finished', 'ok', 5000);
    load(); loadStat();
  }
</script>

<AnaHead question="Which identifiers appear in the collected text?">
  <b>{ENTITY_CAVEAT}</b> These are found by pattern (a number that looks like a phone, an address that looks like a wallet, a Luhn-valid card number…),
  so a match can be a coincidence and does not show who owns it or that it is in use. Card numbers are shown masked (first six and last four digits).
  Treat the list as leads: check each in its original context before relying on it.
</AnaHead>

<div class="row controls">
  <div class="seg" role="group" aria-label="Kind">
    <button class:on={!kind} aria-pressed={!kind} onclick={() => setQuery({ kind: null })}>All</button>
    {#each Object.entries(ENTITY_KINDS) as [k, l]}
      <button class:on={kind === k} aria-pressed={kind === k} onclick={() => setQuery({ kind: k })} title={l}>{k}{#if kindCounts?.[k] != null} <span class="faint">{num(kindCounts[k])}</span>{/if}</button>
    {/each}
  </div>
  <form class="row" onsubmit={commit}>
    <input type="search" data-page-search bind:value={qInput} placeholder="Find a value" aria-label="Find a value" style="width:min(100%,200px)" />
    <button type="submit">Find</button>
  </form>
  <span class="spacer"></span>
  <button onclick={backfill} disabled={busy} title="Scan existing posts, comments and media text (explicit, idempotent)">{busy ? 'Scanning…' : 'Scan archive for identifiers'}</button>
</div>

{#if error}
  <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
{:else if loading && !data}
  <Loading />
{:else if !rows.length}
  <Empty title="No identifiers {Q.q || kind ? 'match' : 'found yet'}">
    {#if Q.q || kind}Try another kind or clear the search.{:else}New posts are scanned as they are collected. To scan what is already in the archive, use “Scan archive for identifiers”.{/if}
  </Empty>
{:else}
  {#if lastScan}<div class="tiny faint">Last full scan {dt(lastScan.at)}.</div>{/if}
  <div class="tiny faint" class:dim={loading} style="margin-bottom:6px">{num(total)} distinct values{total > rows.length ? `, showing the ${num(rows.length)} most frequent` : ''}. Click a value for the posts and comments.</div>
  {#each groups as [k, list] (k)}
    <div class="section-title">{kindLabel(k)} <span class="faint">{num(list.length)}</span></div>
    <div class="table-wrap panel" class:dim={loading}>
      <table class="t">
        <thead><tr><th scope="col">Value</th><th scope="col" class="num">Items</th><th scope="col" class="num">Channels</th><th scope="col">First seen</th><th scope="col">Last seen</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each list as r (r.kind + ':' + r.value)}
            <tr class="click" onclick={() => openEntity(r)}>
              <td class="mono nowrap" dir="ltr"><button class="rowbtn" onclick={(e) => { e.stopPropagation(); openEntity(r); }} title="Show where this appears"><b>{r.value}</b></button>{#if r.meta?.country || r.country} <span class="pill">{r.meta?.country || r.country}</span>{/if}</td>
              <td class="num">{num(cnt(r))}</td>
              <td class="num" title={chans(r).map((c) => '@' + c).join(', ')}>{num(nChans(r))}</td>
              <td class="nowrap small">{day(r.first_seen)}</td>
              <td class="nowrap small">{day(r.last_seen)}</td>
              <td class="right nowrap"><a class="btn sm ghost" href={href('search', { entity: r.kind + ':' + r.value })} onclick={(e) => e.stopPropagation()}>Search posts</a></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/each}
{/if}

<Drawer open={!!sel} onclose={() => (sel = null)} title={sel ? `${sel.kind}: ${sel.value}` : ''} width="680px">
  {#if sel}
    <div class="col" style="gap:10px">
      <div class="callout warn small">{ENTITY_CAVEAT} The excerpt shows where the pattern appeared.</div>
      <div class="row wrap">
        <a class="btn sm primary" href={href('search', { entity: sel.kind + ':' + sel.value })}>Search posts with this</a>
        <span class="small muted">{plural(cnt(sel), 'item')} · {plural(nChans(sel), 'channel')} · {day(sel.first_seen)} to {day(sel.last_seen)}</span>
      </div>
      {#if sel.kind === 'card' && !reveal}<div class="row"><span class="small muted">Excerpts are hidden for card numbers.</span><button class="sm" onclick={revealCard}>Show excerpts…</button></div>{/if}
      {#if detErr}<div class="callout bad" role="alert">{detErr}</div>
      {:else if !det}<Loading />
      {:else if !items.length}<div class="muted small">No items returned.</div>
      {:else}
        {#each items as i, n (n)}
          <div class="it panel">
            <div class="row wrap small">
              <span class="pill {itemType(i) === 'comment' ? 'info' : ''}">{itemType(i) === 'media_text' ? 'text read from media' : itemType(i)}</span>
              {#if i.channel}<ChannelLink name={i.channel} showHandle={false} />{/if}
              <span class="faint">{dt(i.date)}</span>
              <span class="spacer"></span>
              {#if i.url}<a class="tiny" href={i.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}
              {#if openTarget(i).id != null}<button class="sm" onclick={() => (item = openTarget(i))}>Details</button>{/if}
            </div>
            {#if i.excerpt || i.text}<div class="ex text-body" dir="auto">{#each (i.raw && i.excerpt ? highlightParts(i.excerpt, [i.raw]) : null) || [{ seg: i.excerpt || i.text, hit: false }] as x}{#if x.hit}<mark>{x.seg}</mark>{:else}{x.seg}{/if}{/each}</div>{/if}
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</Drawer>
<TriageDetailDrawer bind:item />

<style>
  .controls { margin-bottom: 12px; gap: 10px; flex-wrap: wrap; align-items: center; }
  .wrap { flex-wrap: wrap; }
  .controls .seg { flex-wrap: wrap; max-width: 100%; }
  .controls .seg button { flex: 0 1 auto; }
  .dim { opacity: .6; }
  .click { cursor: pointer; }
  .mono { font-family: var(--mono); font-size: 12.5px; }
  .it { padding: 8px 10px; }
  .ex { font-size: 13px; margin-top: 4px; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 9em; overflow: auto; }
</style>
