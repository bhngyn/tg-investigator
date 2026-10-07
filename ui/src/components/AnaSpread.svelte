<script>
  // "Where else did this appear?" Everywhere a post's content shows up, oldest first.
  import { get } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { dt, num } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp, span, parseTme } from './AnaUtil.js';

  const postId = $derived(/^\d+$/.test(route.query.post || '') ? Number(route.query.post) : null);

  let input = $state('');
  let resolving = $state(false), resolveErr = $state(null);
  let data = $state(null), error = $state(null), loading = $state(false);
  let seq = 0;

  $effect(() => {
    const id = postId;
    const my = ++seq;
    data = null; error = null;
    if (id == null) { loading = false; return; }
    loading = true;
    get(`/api/analysis/spread/${id}`).then((d) => { if (my === seq) data = d; })
      .catch((e) => { if (my === seq) error = e.status === 404 ? `No post with id ${id} in this archive.` : e.message; })
      .finally(() => { if (my === seq) loading = false; });
  });

  // The server has no "find post by t.me link" lookup, so page through the channel's posts (local, max 2000).
  async function findByLink(ch, msg) {
    for (let off = 0; off < 2000; off += 200) {
      const r = await get('/api/search', { channels: ch, limit: 200, offset: off, sort: 'oldest' });
      const hit = (r.results || []).find((p) => p.msg_id === msg);
      if (hit) return hit.id;
      if (off + 200 >= (r.total || 0)) break;
    }
    return null;
  }
  async function submit(e) {
    e?.preventDefault();
    resolveErr = null;
    const s = input.trim();
    if (!s) return;
    if (/^\d+$/.test(s)) { setQuery({ post: s }); return; }
    const t = parseTme(s);
    if (!t) { resolveErr = 'Enter a post id (a number) or a link like https://t.me/channel/1234.'; return; }
    resolving = true;
    try {
      const id = await findByLink(t.channel.toLowerCase(), t.msg_id);
      if (id) setQuery({ post: String(id) });
      else resolveErr = `@${t.channel} message ${t.msg_id} is not in the collected archive (or is beyond the first 2000 posts of that channel).`;
    } catch (err) { resolveErr = err.message; }
    finally { resolving = false; }
  }

  const items = $derived(data?.items || []);
  const t0 = $derived(items.length ? Date.parse(items[0].date) : 0);
  const HOW = { 'this post': 'accent', 'same file': 'ok', 'similar file': 'warn', 'same forwarded original': 'info', 'same text': '' };
  const HOWTIP = {
    'same file': 'byte-identical file (same SHA-256)',
    'similar file': 'looks the same by perceptual hash (heuristic; confirm by eye)',
    'same forwarded original': 'forwarded from, or is, the same original post',
    'same text': 'same text after normalising case, spacing and punctuation (heuristic, 40+ characters)',
  };

  function exportCsv() {
    const rows = items.map((i, k) => ({ order: k + 1, item_type: i.item_type, id: i.id, channel: i.channel, date: i.date,
      minutes_after_first: Math.round((Date.parse(i.date) - t0) / 60000), matched_by: i.how.join('; '), url: i.url, text: i.text }));
    download(`spread-post${data.post.id}-${stamp()}.csv`, toCsv(rows, Object.keys(rows[0])), 'text/csv');
  }
</script>

<AnaHead question="Where else did this appear?">
  Lists every collected post or comment that shares this post’s file, forwarded original or text. “Earliest” means earliest <em>in our collection</em>;
  the true origin may be a channel we do not follow, or a message that was later deleted. Matches on text or look-alike files are heuristics: check them by eye.
</AnaHead>

<form class="row controls" onsubmit={submit}>
  <label class="field grow">Post id or t.me link
    <input type="text" bind:value={input} aria-label="Post id or t.me link" placeholder={postId ? String(postId) : 'e.g. 298 or https://t.me/channel/1234'} data-page-search />
  </label>
  <button class="primary" type="submit" disabled={resolving || !input.trim()}>{resolving ? 'Looking up…' : 'Trace'}</button>
</form>
{#if resolveErr}<div class="callout warn small" style="margin-bottom:10px">{resolveErr}</div>{/if}

{#if postId == null}
  <Empty title="Choose a post to trace">
    Open a post’s details from <a href={href('search')}>Search</a>, the review queue or a channel page and choose “Where else?”, or paste a post id / t.me link above.
    Coordination groups and the Changes feed also link here.
  </Empty>
{:else if loading}
  <Loading label="Tracing post {postId}…" />
{:else if error}
  <div class="callout bad" role="alert">{error}</div>
{:else if data}
  <div class="stats" style="margin-bottom:12px">
    <div class="stat panel"><div class="v">{num(items.length)}</div><div class="l">places it appears</div></div>
    <div class="stat panel"><div class="v">{num(data.channels.length)}</div><div class="l">channels</div></div>
    <div class="stat panel"><div class="v">{span(items.length > 1 ? Math.round((Date.parse(items[items.length - 1].date) - t0) / 60000) : 0)}</div><div class="l">first to last</div></div>
  </div>
  <div class="row" style="margin-bottom:8px">
    <span class="small muted">Starting from post {data.post.id} in</span> <ChannelLink name={data.post.channel} showHandle={false} />
    <a class="small" href={data.post.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>
    <span class="spacer"></span>
    <a class="btn sm" href={href('analysis', 'media', { post: data.post.id })}>Check its media for look-alikes</a>
    <button class="sm" onclick={exportCsv} disabled={!items.length}>Export CSV</button>
  </div>

  {#if items.length <= 1}
    <div class="callout">Nothing else in the archive shares this post’s file, forwarded original or text. That only says it was not found in collected channels.</div>
  {/if}

  <ol class="chain">
    {#each items as it, k (it.item_type + it.id)}
      <li class="panel" class:this={it.is_this}>
        <div class="gap num small faint">{k === 0 ? 'earliest' : '+' + span(Math.round((Date.parse(it.date) - t0) / 60000))}</div>
        <div class="body">
          <div class="row">
            <ChannelLink name={it.channel} />
            <span class="faint small">{dt(it.date)}</span>
            {#if it.item_type === 'comment'}<span class="pill info" title="Found in a comment section">comment</span>{/if}
            {#each it.how as h}<span class="pill {HOW[h] ?? ''}" title={HOWTIP[h] || ''}>{h}</span>{/each}
            <span class="spacer"></span>
            {#if it.url}<a class="small" href={it.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}
            {#if it.item_type === 'post' && !it.is_this}<a class="small" href={href('analysis', 'spread', { post: it.id })}>trace this</a>{/if}
            {#if it.item_type === 'comment'}<a class="small" href={href('comments', { q: it.text ? it.text.slice(0, 40) : '' })}>find in comments</a>{/if}
          </div>
          {#if it.text}<div class="text-body txt" dir="auto">{it.text}</div>{/if}
        </div>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  .grow { flex: 1; max-width: 520px; }
  .chain { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .chain li { display: grid; grid-template-columns: 84px 1fr; gap: 10px; padding: 8px 12px; border-left: 3px solid var(--line-strong); }
  .chain li.this { border-left-color: var(--accent); background: var(--accent-soft); }
  .gap { padding-top: 2px; }
  .txt { font-size: 13px; margin-top: 4px; max-height: 5em; overflow: hidden; }
</style>
