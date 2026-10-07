<script>
  // "What changed on Telegram?" Deleted / edited posts, deleted comments, channel changes.
  import { get } from '../lib/api.js';
  import { route, href, setQuery, caseId } from '../lib/router.svelte.js';
  import { scoped, sinceForCase } from '../lib/store.svelte.js';
  import { dt, day, ago, num } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp } from './AnaUtil.js';

  const LIMIT = 500;
  const SINCE = [
    { id: 'visit', label: 'Since last visit' },
    { id: '24h', label: 'Last 24 h' },
    { id: '7d', label: 'Last 7 days' },
    { id: '30d', label: 'Last 30 days' },
    { id: 'all', label: 'All time' },
  ];
  const HOURS = { '24h': 24, '7d': 168, '30d': 720 };

  const visit = $derived(sinceForCase());
  const sinceKey = $derived(route.query.since || (visit ? 'visit' : 'all'));
  const sinceIso = $derived.by(() => {
    if (sinceKey === 'visit') return visit ? visit.slice(0, 19) : '';
    if (HOURS[sinceKey]) return new Date(Date.now() - HOURS[sinceKey] * 3600e3).toISOString().slice(0, 19);
    if (/^\d{4}-\d\d-\d\dT/.test(sinceKey)) return sinceKey.slice(0, 19);   // explicit time from a link (Overview)
    return '';
  });
  const kind = $derived(route.query.kind || '');

  let data = $state(null), error = $state(null), loading = $state(true);
  let seq = 0;
  $effect(() => {
    const params = scoped({ since: sinceIso, limit: LIMIT });
    const my = ++seq;
    loading = true; error = null;
    get('/api/changes', params).then((d) => { if (my === seq) data = d; })
      .catch((e) => { if (my === seq) { error = e.message; data = null; } })
      .finally(() => { if (my === seq) loading = false; });
  });

  const items = $derived(data?.items || []);
  const group = (k) => (k.startsWith('channel_') ? 'channel' : k);
  const counts = $derived.by(() => {
    const c = { post_deleted: 0, post_edited: 0, comment_deleted: 0, channel: 0 };
    for (const i of items) c[group(i.kind)]++;
    return c;
  });
  const capped = $derived(data && Object.values(data.counts || {}).some((n) => n >= LIMIT));
  const shown = $derived(kind ? items.filter((i) => group(i.kind) === kind) : items);
  const days = $derived.by(() => {
    const out = [];
    for (const i of shown) {
      const d = day(i.at);
      if (!out.length || out[out.length - 1].d !== d) out.push({ d, rows: [] });
      out[out.length - 1].rows.push(i);
    }
    return out;
  });

  const KINDS = [
    { id: 'post_deleted', label: 'Posts deleted', pill: 'bad' },
    { id: 'post_edited', label: 'Posts edited', pill: 'warn' },
    { id: 'comment_deleted', label: 'Comments deleted', pill: 'bad' },
    { id: 'channel', label: 'Channel changes', pill: 'info' },
  ];
  const EVENT = {
    renamed: 'Channel renamed (handle changed)', title_changed: 'Title changed', description_changed: 'Description changed',
    preview_disabled: 'Web preview switched off', preview_restored: 'Web preview restored', unavailable: 'Channel unavailable',
    subscribers_jump: 'Subscriber count jumped',
  };
  const label = (i) => i.kind.startsWith('channel_') ? (EVENT[i.kind.slice(8)] || i.kind.slice(8).replace(/_/g, ' ')) : KINDS.find((k) => k.id === i.kind)?.label || i.kind;
  const pillOf = (i) => (i.kind.startsWith('channel_') ? 'info' : KINDS.find((k) => k.id === i.kind)?.pill || '');

  // Highlight the part that differs: trim the shared prefix and suffix.
  function diff(a, b) {
    a = a || ''; b = b || '';
    let p = 0;
    while (p < a.length && p < b.length && a[p] === b[p]) p++;
    let s = 0;
    while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    return {
      a: [a.slice(0, p), a.slice(p, a.length - s), a.slice(a.length - s)],
      b: [b.slice(0, p), b.slice(p, b.length - s), b.slice(b.length - s)],
    };
  }

  function exportCsv() {
    const rows = shown.map((i) => ({
      kind: i.kind, detected_at: i.at, channel: i.channel, msg_id: i.msg_id, post_date: i.date, last_seen: i.last_seen,
      url: i.url, text: i.text, previous_text: i.previous_text, old: i.old, new: i.new, sender_id: i.sender_id,
    }));
    download(`changes-${stamp()}.csv`, toCsv(rows, Object.keys(rows[0] || { kind: 1 })), 'text/csv');
  }
</script>

<AnaHead question="What changed on Telegram?">
  Deletions and edits are only known from the times we checked. “Detected” is when our collection noticed, not when it happened.
  A post missing from a page may have been removed by its author, an admin, or Telegram; we cannot tell which.
  Edit text shows only the most recent previous version and is cut to 200 characters.
</AnaHead>

<div class="row controls">
  <label class="field">Show changes detected
    <select value={sinceKey} onchange={(e) => setQuery({ since: e.currentTarget.value })}>
      {#each SINCE as s}
        <option value={s.id} disabled={s.id === 'visit' && !visit}>{s.label}{s.id === 'visit' && visit ? ` (${ago(visit)})` : ''}</option>
      {/each}
      {#if /^\d{4}-/.test(sinceKey)}<option value={sinceKey}>Since {sinceKey.slice(0, 16).replace('T', ' ')} UTC</option>{/if}
    </select>
  </label>
  <div class="row tight chips" role="group" aria-label="Filter by kind">
    <button class="chip" class:on={!kind} aria-pressed={!kind} onclick={() => setQuery({ kind: null })}>All {num(items.length)}</button>
    {#each KINDS as k}
      <button class="chip" class:on={kind === k.id} aria-pressed={kind === k.id} disabled={!counts[k.id] && kind !== k.id} onclick={() => setQuery({ kind: kind === k.id ? null : k.id })}>
        {k.label} <b class="num">{num(counts[k.id])}</b>
      </button>
    {/each}
  </div>
  <span class="spacer"></span>
  <button class="sm" disabled={!shown.length} onclick={exportCsv} title="Download the rows shown as CSV (built in your browser)">Export CSV</button>
</div>

{#if loading && !data}
  <Loading />
{:else if error}
  <div class="callout bad">Could not load changes: {error}</div>
{:else if !items.length}
  <Empty title="No changes recorded {sinceKey === 'all' ? 'yet' : 'in this period'}">
    Changes appear after a channel has been collected more than once: a re-collection compares what is stored with what is now on the page.
    {#if sinceKey !== 'all'} Try “All time”.{/if}
    <span>Comment deletions need the logged-in comment collection (<a href={href('channels')}>Channels</a>).</span>
  </Empty>
{:else}
  {#if capped}<div class="callout warn small">Showing the newest {LIMIT} per kind. Narrow the period to see everything.</div>{/if}
  {#if !shown.length}
    <Empty title="Nothing of this kind in the period" />
  {/if}
  {#each days as g (g.d)}
    <h3 class="section-title">{g.d} <span class="faint">· {num(g.rows.length)}</span></h3>
    <div class="col">
      {#each g.rows as i (i.kind + i.id)}
        <article class="panel item">
          <header class="row">
            <span class="pill {pillOf(i)}">{label(i)}</span>
            <ChannelLink name={i.channel} />
            <span class="faint small" title="When our collection noticed">detected {dt(i.at)}</span>
            {#if i.date}<span class="faint small" title="Original post date">· posted {dt(i.date)}</span>{/if}
            <span class="spacer"></span>
            {#if i.url}<a class="small" href={i.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}
            {#if i.kind.startsWith('post_')}<a class="btn sm" href={href('analysis', 'spread', { post: i.id })}>Where else?</a>{/if}
            {#if i.kind === 'comment_deleted' && i.sender_id}<a class="btn sm" href={href('comments', { sender: i.sender_id })}>Account’s comments</a>{/if}
          </header>

          {#if i.kind === 'post_deleted'}
            {#if i.last_seen}<div class="faint small">Last seen present {dt(i.last_seen)}.</div>{/if}
            <div class="text text-body" dir="auto">{i.text || '(no text)'}</div>
          {:else if i.kind === 'post_edited'}
            {@const d = diff(i.previous_text, i.text)}
            <div class="grid two cmp">
              <div><div class="tiny faint">Before</div><div class="text-body old" dir="auto">{d.a[0]}<mark>{d.a[1]}</mark>{d.a[2]}{#if !i.previous_text}<i class="faint">(no text)</i>{/if}</div></div>
              <div><div class="tiny faint">Now</div><div class="text-body new" dir="auto">{d.b[0]}<mark>{d.b[1]}</mark>{d.b[2]}{#if !i.text}<i class="faint">(no text)</i>{/if}</div></div>
            </div>
            {#if i.previous_text === i.text}<div class="faint small">Text is unchanged in the part shown; the media or the edit flag changed.</div>{/if}
          {:else if i.kind === 'comment_deleted'}
            <div class="faint small">Comment #{i.msg_id}{#if i.last_seen} · last seen {dt(i.last_seen)}{/if}</div>
            <div class="text text-body" dir="auto">{i.text || '(no text)'}</div>
          {:else}
            <div class="row"><span class="faint small">Before</span><code class="ev">{i.old ?? '–'}</code><span aria-hidden="true">→</span><span class="faint small">After</span><code class="ev">{i.new ?? '–'}</code></div>
          {/if}
        </article>
      {/each}
    </div>
  {/each}
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  .chips { align-self: center; }
  .chip:disabled { opacity: .45; cursor: default; }
  .item { padding: 10px 12px; }
  .item header { margin-bottom: 6px; }
  .text { font-size: 13.5px; max-height: 9em; overflow: hidden; }
  .cmp { gap: 10px; }
  .cmp > div { padding: 6px 8px; border-radius: 6px; background: var(--panel-2); font-size: 13.5px; }
  .old mark { background: var(--bad-soft); text-decoration: line-through; }
  .new mark { background: var(--ok-soft); }
  .ev { background: var(--panel-2); padding: 1px 6px; border-radius: 4px; max-width: 60ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
