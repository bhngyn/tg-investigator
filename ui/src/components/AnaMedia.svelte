<script>
  // "Is this image/video reused or doctored?" Look-alike files, embedded metadata, and the local media scan.
  import { get, post } from '../lib/api.js';
  import { route, href, setQuery, caseId } from '../lib/router.svelte.js';
  import { app, scoped, attempt, currentCase, toast } from '../lib/store.svelte.js';
  import { dt, num, bytes } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import Media from './Media.svelte';
  import Modal from './Modal.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp } from './AnaUtil.js';

  const view = $derived(route.query.view || (route.query.sha || route.query.post ? 'reuse' : 'reuse'));
  const sha = $derived(/^[0-9a-f]{64}$/i.test(route.query.sha || '') ? route.query.sha.toLowerCase() : '');
  const postQ = $derived(/^\d+$/.test(route.query.post || '') ? Number(route.query.post) : null);
  const dist = $derived(Number(route.query.dist) === 3 ? 3 : 7);

  const VIEWS = [
    { id: 'reuse', label: 'Reused images & videos' },
    { id: 'embedded', label: 'Embedded metadata' },
    { id: 'scan', label: 'Scan media' },
  ];

  // ---- reuse -------------------------------------------------------------------
  let input = $state('');
  let inputErr = $state(null);
  function submit(e) {
    e.preventDefault();
    inputErr = null;
    const s = input.trim();
    if (/^[0-9a-f]{64}$/i.test(s)) setQuery({ sha: s.toLowerCase(), post: null });
    else if (/^\d+$/.test(s)) setQuery({ post: s, sha: null });
    else inputErr = 'Enter a 64-character SHA-256 (from a file’s details) or a post id.';
  }

  let sim = $state(null), simErr = $state(null), simLoading = $state(false);
  let seq = 0;
  $effect(() => {
    const s = sha, d = dist;
    const my = ++seq;
    sim = null; simErr = null; previews = {};
    if (!s) { simLoading = false; return; }
    simLoading = true;
    get(`/api/analysis/similar_media/${s}`, { max_distance: d })
      .then((r) => { if (my === seq) sim = r; })
      .catch((e) => { if (my === seq) simErr = e.message; })
      .finally(() => { if (my === seq) simLoading = false; });
  });

  let pmedia = $state(null), pmErr = $state(null);
  $effect(() => {
    const id = postQ;
    pmedia = null; pmErr = null;
    if (id == null) return;
    get(`/api/posts/${id}`).then((p) => { if (id === postQ) pmedia = p; }).catch((e) => { if (id === postQ) pmErr = e.status === 404 ? `No post with id ${id}.` : e.message; });
  });

  // Previews are fetched on demand, one post at a time, and always rendered through Media (blurred by default).
  let previews = $state({});
  async function preview(m) {
    if (m.item_type !== 'post' || previews[m.id]) return;
    const my = seq;   // a new similar-media search resets previews; a late answer must not land in it
    previews[m.id] = 'loading';
    try {
      const p = await get(`/api/posts/${m.id}`);
      if (my === seq) previews[m.id] = { media: (p.media || []).filter((x) => x.sha256 === m.sha256), sensitive: p.sensitive };
    } catch (e) { if (my === seq) previews[m.id] = { error: e.message || 'Could not load the file' }; }
  }
  const matches = $derived(sim?.matches || []);
  const refMatch = $derived(matches.find((m) => m.identical && m.item_type === 'post'));
  $effect(() => { if (refMatch) preview(refMatch); });
  const distLabel = (m) => m.identical ? 'identical file' : m.distance <= 3 ? 'almost certainly the same' : 'very likely the same';

  function exportMatches() {
    const rows = matches.map((m) => ({ item_type: m.item_type, id: m.id, channel: m.channel, date: m.date, url: m.url, sha256: m.sha256,
      identical: m.identical ? 1 : 0, phash_distance_bits: m.distance }));
    download(`similar-media-${sha.slice(0, 8)}-${stamp()}.csv`, toCsv(rows, Object.keys(rows[0])), 'text/csv');
  }

  // ---- embedded ------------------------------------------------------------------
  const gps = $derived(route.query.gps === '1');
  let emb = $state(null), embErr = $state(null), embLoading = $state(false);
  let seq2 = 0;
  $effect(() => {
    if (view !== 'embedded') return;
    const params = scoped({ gps: gps ? 1 : '' });
    const my = ++seq2;
    embLoading = true; embErr = null;
    get('/api/analysis/embedded', params).then((r) => { if (my === seq2) emb = r; })
      .catch((e) => { if (my === seq2) { embErr = e.message; emb = null; } })
      .finally(() => { if (my === seq2) embLoading = false; });
  });
  let openRaw = $state({});
  const kv = (o) => Object.entries(o || {}).filter(([, v]) => v !== null && v !== '' && v !== undefined);
  const val = (v) => (typeof v === 'object' ? JSON.stringify(v) : String(v));

  function exportEmbedded() {
    const rows = emb.map((r) => ({ sha256: r.sha256, has_gps: r.has_gps ? 1 : 0, channels: r.channels, post_id: r.post_id, embedded: JSON.stringify(r.embedded) }));
    download(`embedded-metadata-${stamp()}.csv`, toCsv(rows, ['sha256', 'has_gps', 'channels', 'post_id', 'embedded']), 'text/csv');
  }

  // ---- scan ----------------------------------------------------------------------
  let confirm = $state(false), busy = $state(false);
  const auto = $derived(app.settings.auto_mediascan !== '0');
  async function scan() {
    busy = true;
    const r = await attempt(() => post('/api/analysis/mediascan', caseId() != null ? { case: caseId() } : {}), null);
    busy = false; confirm = false;
    if (r) { toast(`Media scan queued (job ${r.id}). Progress shows in the jobs indicator.`, 'ok'); }
  }
</script>

<AnaHead question="Is this image or video reused or doctored?">
  Look-alike matching uses a perceptual hash (a 64-bit fingerprint of how the picture looks). It is a <b>heuristic</b>: it finds re-encodes, resizes and screenshots, and it can
  miss edits or match unrelated but similar images. Always confirm by eye. Embedded metadata is usually stripped by Telegram; when present it can be forged and is a clue, not proof.
</AnaHead>

<div class="row" style="margin-bottom:12px">
  <div class="seg" role="group" aria-label="Media analysis view">
    {#each VIEWS as v}<button aria-pressed={view === v.id} class:on={view === v.id} onclick={() => setQuery({ view: v.id })}>{v.label}</button>{/each}
  </div>
</div>

{#if view === 'reuse'}
  <form class="row controls" onsubmit={submit}>
    <label class="field grow">File SHA-256 or post id
      <input type="text" bind:value={input} aria-label="SHA-256 or post id" placeholder={sha ? sha.slice(0, 20) + '…' : postQ ? String(postQ) : 'paste a SHA-256, or a post id to pick one of its files'} />
    </label>
    <button class="primary" type="submit" disabled={!input.trim()}>Find look-alikes</button>
    <label class="field">Match strictness
      <select value={dist} onchange={(e) => setQuery({ dist: e.currentTarget.value === '3' ? '3' : null })}>
        <option value="7">Up to 7 bits (very likely + almost certain)</option>
        <option value="3">Up to 3 bits (almost certainly the same)</option>
      </select>
    </label>
  </form>
  {#if inputErr}<div class="callout warn small" style="margin-bottom:10px">{inputErr}</div>{/if}

  {#if !sha && postQ == null}
    <Empty title="Pick a file to check">
      Paste a file’s SHA-256, or a post id to choose one of its files. You can also start from a coordination group of look-alike or identical files,
      or from the embedded-metadata list.
      <span>The difference “distance” means: the number of bits, out of 64, in which the two fingerprints differ. 0–3: almost certainly the same picture or video. 4–7: very likely. Confirm by eye.</span>
    </Empty>
  {/if}

  {#if postQ != null && !sha}
    {#if pmErr}<div class="callout bad" role="alert">{pmErr}</div>
    {:else if !pmedia}<Loading />
    {:else}
      <div class="panel panel-pad" style="margin-bottom:12px">
        <div class="row"><b>Files in post {pmedia.id}</b> <ChannelLink name={pmedia.channel} showHandle={false} /><span class="faint small">{dt(pmedia.date)}</span></div>
        {#if !(pmedia.media || []).length}<p class="muted small">This post has no stored files.</p>{/if}
        <div class="row" style="align-items:flex-start;margin-top:6px">
          {#each (pmedia.media || []) as m (m.id)}
            <div class="col" style="width:min(100%,180px)">
              <Media media={[m]} sensitive={pmedia.sensitive} size="md" />
              <div class="tiny faint mono">{m.kind} · {bytes(m.bytes)}</div>
              <button class="sm" onclick={() => setQuery({ sha: m.sha256, post: null })}>Check this file</button>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  {/if}

  {#if sha}
    {#if simLoading}<Loading />
    {:else if simErr}<div class="callout bad" role="alert">{simErr}</div>
    {:else if sim && !sim.scanned}
      <div class="callout warn">
        This file has not been scanned yet, so it has no perceptual fingerprint. Run a media scan (it is local and quick), then come back.
        <button class="sm" style="margin-left:8px" onclick={() => setQuery({ view: 'scan' })}>Go to Scan media</button>
      </div>
    {:else if sim}
      <div class="grid two" style="margin-bottom:12px">
        <div class="panel panel-pad">
          <h3>The file you are checking</h3>
          <div class="mono tiny wrap faint" style="margin:4px 0">{sim.sha256}</div>
          {#if refMatch && previews[refMatch.id]?.media}<Media media={previews[refMatch.id].media} sensitive={previews[refMatch.id].sensitive} size="md" />{/if}
          <div class="section-title" style="margin-top:8px">File details (from ffprobe)</div>
          {#if kv(sim.meta).length}
            <dl class="kv">{#each kv(sim.meta) as [k, v]}<dt>{k}</dt><dd>{val(v)}</dd>{/each}</dl>
          {:else}<p class="faint small">None recorded.</p>{/if}
        </div>
        <div class="panel panel-pad">
          <h3>Embedded metadata</h3>
          {#if kv(sim.embedded).length}
            <dl class="kv">{#each kv(sim.embedded) as [k, v]}<dt>{k}</dt><dd>{val(v)}</dd>{/each}</dl>
            <p class="faint small">Camera, date, software or GPS tags still inside the file. Can be edited or fabricated; treat as a lead.</p>
          {:else}<p class="faint small">This file carries no embedded camera, GPS, date or software tags (normal for Telegram files).</p>{/if}
        </div>
      </div>

      <div class="section-title row">{num(matches.length)} place{matches.length === 1 ? '' : 's'} carrying this or a look-alike, oldest first<span class="spacer"></span>
        <button class="sm" disabled={!matches.length} onclick={exportMatches}>Export CSV</button></div>
      {#if !matches.length}
        <Empty title="No other place carries this file" />
      {:else}
        <div class="col">
          {#each matches as m (m.item_type + m.id + m.sha256)}
            <article class="panel item">
              <div class="row">
                <span class="pill {m.identical ? 'ok' : m.distance <= 3 ? 'warn' : ''}" title="Differing bits of 64 in the perceptual hash">
                  {m.identical ? 'identical file' : `distance ${m.distance}`}</span>
                {#if !m.identical}<span class="small muted">{distLabel(m)} <span class="faint">(confirm by eye)</span></span>{/if}
                <ChannelLink name={m.channel} />
                <span class="faint small">{dt(m.date)}</span>
                <span class="pill">{m.item_type === 'comment' ? 'comment' : m.kind}</span>
                <span class="spacer"></span>
                {#if m.url}<a class="small" href={m.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}
                {#if m.item_type === 'post'}
                  <a class="small" href={href('analysis', 'spread', { post: m.id })}>spread</a>
                  {#if !previews[m.id]}<button class="sm" onclick={() => preview(m)}>Show file</button>{/if}
                {/if}
              </div>
              {#if previews[m.id] === 'loading'}<span class="spinner"></span>{:else if previews[m.id]?.error}
                <div class="callout bad small">Could not load this file: {previews[m.id].error} <button class="sm" onclick={() => { delete previews[m.id]; preview(m); }}>Retry</button></div>
              {:else if previews[m.id]?.media}
                <Media media={previews[m.id].media} sensitive={previews[m.id].sensitive} size="md" />
                {#if !previews[m.id].media.length}<div class="faint small">File is not stored locally.</div>{/if}
              {/if}
            </article>
          {/each}
        </div>
      {/if}
    {/if}
  {/if}

{:else if view === 'embedded'}
  <div class="row controls">
    <label class="row small" style="gap:6px"><input type="checkbox" checked={gps} onchange={(e) => setQuery({ gps: e.currentTarget.checked ? '1' : null })} /> Only files with GPS coordinates</label>
    <span class="spacer"></span>
    <button class="sm" disabled={!emb?.length} onclick={exportEmbedded}>Export CSV</button>
  </div>
  {#if embLoading && !emb}<Loading />
  {:else if embErr}<div class="callout bad" role="alert">{embErr}</div>
  {:else if !emb?.length}
    <Empty title={gps ? 'No scanned file carries GPS coordinates' : 'No scanned file carries camera, GPS, date or software metadata'}>
      That is normal: Telegram strips most metadata. Files only appear here after a media scan; if none has run, use “Scan media”.
      {#if gps} Try turning off the GPS filter.{/if}
    </Empty>
  {:else}
    <div class="table-wrap">
      <table class="t">
        <thead><tr><th scope="col">File</th><th scope="col">Metadata still inside</th><th scope="col">Posted in</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each emb as r (r.sha256)}
            <tr>
              <td class="nowrap"><span class="mono tiny">{r.sha256.slice(0, 12)}…</span>{#if r.has_gps}<div><span class="pill warn" title="GPS coordinates inside the file: a claim to check, can be forged">GPS</span></div>{/if}</td>
              <td>
                <dl class="kv">{#each (openRaw[r.sha256] ? kv(r.embedded) : kv(r.embedded).slice(0, 6)) as [k, v]}<dt>{k}</dt><dd>{val(v)}</dd>{/each}</dl>
                {#if kv(r.embedded).length > 6}<button class="ghost sm" onclick={() => (openRaw[r.sha256] = !openRaw[r.sha256])}>{openRaw[r.sha256] ? 'Fewer' : `All ${kv(r.embedded).length} fields`}</button>{/if}
              </td>
              <td class="small">{#each (r.channels || '').split(',').filter(Boolean) as c}<div><ChannelLink name={c} /></div>{/each}</td>
              <td class="nowrap"><a class="btn sm" href={href('analysis', 'media', { sha: r.sha256 })}>Find copies</a>
                {#if r.post_id}<a class="btn sm" href={href('analysis', 'spread', { post: r.post_id })}>Spread</a>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="faint small">GPS or date tags are claims made by the file. They can be inserted, and a stripped file proves nothing either way.</p>
  {/if}

{:else}
  <div class="panel panel-pad" style="max-width:720px">
    <h3>Scan collected media</h3>
    <p class="muted">
      Computes a perceptual fingerprint for every stored image (and three frames of every video), reads ffprobe details and any
      embedded EXIF/container metadata. Only files not scanned yet are processed{#if caseId() != null}, limited to <b>{currentCase()?.name || 'this case'}</b>{:else}, across the whole archive{/if}.
    </p>
    <ul class="small muted">
      <li>Runs entirely on this computer, in the local lane. No network, no Telegram login.</li>
      <li>Read-only: stored files are never modified.</li>
      <li>Automatic scanning after downloads is currently <b>{auto ? 'on' : 'off'}</b> (setting <code>auto_mediascan</code>, in <a href="#/settings">Settings</a>).</li>
    </ul>
    <button class="primary" onclick={() => (confirm = true)}>Scan media…</button>
  </div>
  <Modal open={confirm} onclose={() => (confirm = false)} title="Start a media scan?" width="460px">
    <p>This will queue a <b>local</b> scan of stored files that have not been scanned yet{#if caseId() != null}, for <b>{currentCase()?.name}</b>{/if}.
      It reads files on disk and writes fingerprints to the database. It makes no network requests.</p>
    {#snippet footer()}
      <button onclick={() => (confirm = false)}>Cancel</button>
      <button class="primary" disabled={busy} onclick={scan}>{busy ? 'Queuing…' : 'Start scan'}</button>
    {/snippet}
  </Modal>
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  .grow { flex: 1; min-width: 260px; max-width: 520px; }
  .item { padding: 8px 12px; }
  .wrap { word-break: break-all; }
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 0; font-size: 12.5px; }
  .kv dt { color: var(--muted); }
  .kv dd { margin: 0; word-break: break-word; }
</style>
