<script>
  // "Who posts the same thing at the same time?" Groups of matching content across channels, and channel pairs.
  import { get } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { scoped } from '../lib/store.svelte.js';
  import { dt, num } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp, span } from './AnaUtil.js';

  const days = $derived(Number(route.query.days) || 90);
  const windowMin = $derived(Number(route.query.window) || 60);
  const minCh = $derived(Number(route.query.min) || 2);
  const kindF = $derived(route.query.kind || '');
  const chF = $derived(route.query.ch || '');

  let data = $state(null), error = $state(null), loading = $state(true);
  let seq = 0;
  $effect(() => {
    const params = scoped({ days, window_min: windowMin, min_channels: minCh, limit: 500 });
    const my = ++seq;
    loading = true; error = null;
    get('/api/analysis/coordination', params).then((d) => { if (my === seq) data = d; })
      .catch((e) => { if (my === seq) { error = e.message; data = null; } })
      .finally(() => { if (my === seq) loading = false; });
  });

  const KIND = {
    same_text: { label: 'Same text', hint: 'identical text after normalisation (40+ characters)' },
    same_file: { label: 'Same file', hint: 'byte-identical file (same SHA-256)' },
    similar_file: { label: 'Look-alike file', hint: 'perceptual hash says the picture/video looks the same (needs a media scan)' },
    same_forward: { label: 'Same forward', hint: 'forwarded from the same original post' },
  };
  const allGroups = $derived(data?.groups || []);
  const chans = $derived([...new Set(allGroups.flatMap((g) => g.channels))].sort());
  const groups = $derived(allGroups.filter((g) => (!kindF || g.kind === kindF) && (!chF || g.channels.includes(chF))));
  const pairs = $derived((data?.pairs || []).filter((p) => !chF || p.channels.includes(chF)));

  let showN = $state(25);
  $effect(() => { kindF; chF; days; windowMin; minCh; showN = 25; });

  function bar(g, it) {
    const t0 = Date.parse(g.first.date), tN = Date.parse(g.items[g.items.length - 1].date);
    const w = tN - t0;
    return w > 0 ? ((Date.parse(it.date) - t0) / w) * 100 : 0;
  }
  const off = (g, it) => Math.round((Date.parse(it.date) - Date.parse(g.first.date)) / 60000);

  let openPair = $state(null);

  function exportPairs() {
    const rows = pairs.map((p) => ({
      channel_a: p.channels[0], channel_b: p.channels[1], shared_items: p.count,
      same_text: p.kinds.same_text || 0, same_file: p.kinds.same_file || 0, similar_file: p.kinds.similar_file || 0, same_forward: p.kinds.same_forward || 0,
      window_min: windowMin, days,
      example_urls: p.examples.map((e) => `${e.a.url} | ${e.b.url} (${e.gap_minutes} min apart)`).join(' ; '),
    }));
    download(`coordination-pairs-${stamp()}.csv`, toCsv(rows, ['channel_a', 'channel_b', 'shared_items', 'same_text', 'same_file', 'similar_file', 'same_forward', 'window_min', 'days', 'example_urls']), 'text/csv');
  }
  function exportGroups() {
    const rows = [];
    groups.forEach((g, n) => g.items.forEach((it, k) => rows.push({
      group: n + 1, kind: g.kind, sample: g.sample, channels_in_group: g.channels.length, spread_minutes: g.spread_minutes,
      order: k + 1, channel: it.channel, date: it.date, minutes_after_first: off(g, it), post_id: it.id, url: it.url,
    })));
    download(`coordination-groups-${stamp()}.csv`, toCsv(rows, ['group', 'kind', 'sample', 'channels_in_group', 'spread_minutes', 'order', 'channel', 'date', 'minutes_after_first', 'post_id', 'url']), 'text/csv');
  }
</script>

<AnaHead question="Who posts the same thing at the same time?">
  A shared file, text or forward is a lead, not proof of coordination: channels also repost the same viral item independently or copy each other openly.
  Look at who is consistently first, how tight the gaps are, and whether the pattern repeats. “Look-alike file” groups need a media scan (Media tab).
</AnaHead>

<div class="row controls">
  <label class="field">Look back
    <select value={days} onchange={(e) => setQuery({ days: e.currentTarget.value })}>
      {#each [7, 30, 90, 180, 365, 1095] as d}<option value={d}>{d >= 365 ? d / 365 + (d === 365 ? ' year' : ' years') : d + ' days'}</option>{/each}
    </select>
  </label>
  <label class="field">“Same time” window (min)
    <input type="number" min="1" max="10080" value={windowMin} style="width:min(100%,110px)" onchange={(e) => setQuery({ window: Math.max(1, Number(e.currentTarget.value) || 60) })} />
  </label>
  <label class="field">Min. channels
    <input type="number" min="2" max="50" value={minCh} style="width:90px" onchange={(e) => setQuery({ min: Math.max(2, Number(e.currentTarget.value) || 2) })} />
  </label>
  <label class="field">Kind
    <select value={kindF} onchange={(e) => setQuery({ kind: e.currentTarget.value })}>
      <option value="">All kinds</option>
      {#each Object.entries(KIND) as [k, v]}<option value={k}>{v.label} ({num(data?.counts?.[k] ?? 0)})</option>{/each}
    </select>
  </label>
  <label class="field">Channel
    <select value={chF} onchange={(e) => setQuery({ ch: e.currentTarget.value })}>
      <option value="">Any</option>
      {#each chans as c}<option value={c}>@{c}</option>{/each}
    </select>
  </label>
  {#if loading && data}<span class="spinner"></span>{/if}
</div>

{#if loading && !data}
  <Loading />
{:else if error}
  <div class="callout bad">Could not load coordination: {error}</div>
{:else if !allGroups.length}
  <Empty title="No matching content found">
    Nothing was posted in {minCh}+ channels within the last {days} days that shares text, a file or a forward source. Try a longer period or fewer required channels.
  </Empty>
{:else}
  <div class="stats" style="margin-bottom:12px">
    <div class="stat panel"><div class="v">{num(groups.length)}</div><div class="l">groups{kindF || chF ? ' (filtered)' : ''}</div></div>
    {#each Object.entries(KIND) as [k, v]}
      <button class="stat panel statbtn" class:sel={kindF === k} title={v.hint} onclick={() => setQuery({ kind: kindF === k ? null : k })}>
        <div class="v">{num(data.counts?.[k] ?? 0)}</div><div class="l">{v.label}</div>
      </button>
    {/each}
    <div class="stat panel"><div class="v">{num(pairs.length)}</div><div class="l">channel pairs</div></div>
  </div>

  <div class="section-title row">Groups <span class="faint">first poster shown first · bar = time since the first post</span><span class="spacer"></span>
    <button class="sm" disabled={!groups.length} onclick={exportGroups}>Export groups CSV</button></div>
  {#if !groups.length}<Empty title="No groups match these filters" />{/if}
  <div class="col">
    {#each groups.slice(0, showN) as g (g.kind + g.key)}
      <article class="panel grp">
        <header class="row">
          <span class="pill {g.kind === 'same_forward' ? 'info' : g.kind === 'same_text' ? 'accent' : 'warn'}" title={KIND[g.kind]?.hint}>{KIND[g.kind]?.label || g.kind}</span>
          <b>{g.channels.length} channels</b><span class="faint small">· {g.items.length} posts</span>
          <span class="small" title="First post to last post">spread over <b>{span(g.spread_minutes)}</b></span>
          <span class="spacer"></span>
          <a class="btn sm" href={href('analysis', 'spread', { post: g.first.id })} title="Everywhere the first post’s content appears">Spread of first post</a>
        </header>
        {#if g.kind !== 'same_file' && g.sample}<div class="sample text-body" dir="auto">{g.sample}</div>{/if}
        {#if g.kind === 'same_file' || g.kind === 'similar_file'}
          <div class="faint tiny mono">{g.kind === 'same_file' ? 'sha256 ' + g.key.slice(0, 16) + '…' : g.sample}
            · <a href={href('analysis', 'media', { sha: g.key })}>check reuse</a></div>
        {/if}
        <ol class="tl">
          {#each g.items as it, k (it.id)}
            <li>
              <span class="who"><ChannelLink name={it.channel} />{#if k === 0}<span class="pill accent">first</span>{/if}</span>
              <span class="track" aria-hidden="true"><i style:left="{bar(g, it)}%" class:first={k === 0}></i></span>
              <span class="when small num" title={it.date}>{k === 0 ? dt(it.date) : '+' + span(off(g, it))}</span>
              <a class="small nowrap" href={it.url} target="_blank" rel="noreferrer noopener" title="Open on Telegram (leaves the app)">t.me ↗</a>
            </li>
          {/each}
        </ol>
      </article>
    {/each}
  </div>
  {#if groups.length > showN}<p><button onclick={() => (showN += 50)}>Show more ({num(groups.length - showN)} left)</button></p>{/if}

  <div class="section-title row">Channel pairs <span class="faint">posting the same thing within {windowMin} min of each other</span><span class="spacer"></span>
    <button class="sm" disabled={!pairs.length} onclick={exportPairs}>Export pairs CSV</button></div>
  {#if pairs.length}
    <div class="table-wrap">
      <table class="t">
        <thead><tr><th scope="col">Channel A</th><th scope="col">Channel B</th><th scope="col" class="right">Shared items</th><th scope="col">Kinds</th><th scope="col">Who was first (in examples)</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each pairs as p (p.channels.join('|'))}
            {@const firstA = p.examples.filter((e) => e.a.channel === p.channels[0]).length}
            <tr>
              <td><ChannelLink name={p.channels[0]} /></td>
              <td><ChannelLink name={p.channels[1]} /></td>
              <td class="right num"><b>{p.count}</b></td>
              <td class="small">{#each Object.entries(p.kinds) as [k, n]}<span class="pill">{KIND[k]?.label || k} {n}</span> {/each}</td>
              <td class="small">
                {#if p.examples.length}
                  {firstA} × @{p.channels[0]}, {p.examples.length - firstA} × @{p.channels[1]}
                  <span class="faint">of {p.examples.length}{p.count > p.examples.length ? ' shown' : ''}</span>
                {/if}
              </td>
              <td><button class="ghost sm" onclick={() => (openPair = openPair === p ? null : p)}>{openPair === p ? 'Hide' : 'Examples'}</button></td>
            </tr>
            {#if openPair === p}
              <tr><td colspan="6" class="ex">
                {#each p.examples as e}
                  <div class="row small">
                    <span class="pill">{KIND[e.kind]?.label || e.kind}</span>
                    <a href={e.a.url} target="_blank" rel="noreferrer noopener"><bdi dir="ltr">@{e.a.channel}</bdi> ↗</a> <span class="faint">{dt(e.a.date)}</span>
                    <span aria-hidden="true">→</span>
                    <a href={e.b.url} target="_blank" rel="noreferrer noopener"><bdi dir="ltr">@{e.b.channel}</bdi> ↗</a> <span class="faint">{dt(e.b.date)}</span>
                    <b>{e.gap_minutes === 0 ? 'same minute' : span(e.gap_minutes) + ' later'}</b>
                    <a href={href('analysis', 'spread', { post: e.a.id })}>spread</a>
                  </div>
                {/each}
                {#if p.count > p.examples.length}<div class="faint tiny">Up to 5 examples per pair; the CSV lists the same examples.</div>{/if}
              </td></tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <p class="muted small">No pair of channels posted the same thing within {windowMin} minutes of each other.</p>
  {/if}
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  .statbtn { text-align: left; display: block; cursor: pointer; font-weight: inherit; }
  .statbtn.sel { border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 0; }
  .grp { padding: 10px 12px; }
  .grp header { margin-bottom: 6px; }
  .sample { font-size: 13px; color: var(--muted); max-height: 4.5em; overflow: hidden; margin-bottom: 4px; }
  .tl { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .tl li { display: grid; grid-template-columns: minmax(150px, 260px) minmax(80px, 1fr) 130px 60px; gap: 10px; align-items: center; }
  .who { display: flex; gap: 6px; align-items: center; min-width: 0; }
  .track { position: relative; height: 10px; border-bottom: 1px solid var(--line-strong); }
  .track i { position: absolute; top: 1px; width: 8px; height: 8px; margin-left: -4px; border-radius: 50%; background: var(--muted); }
  .track i.first { background: var(--accent); }
  .when { text-align: right; }
  td.ex { background: var(--panel-2); }
  @media (max-width: 760px) { .tl li { grid-template-columns: 1fr auto; } .track { display: none; } }
</style>
