<script>
  // Live view of one job: status, progress, activity, log tail, stop buttons.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { attempt, app } from '../lib/store.svelte.js';
  import { href } from '../lib/router.svelte.js';
  import { num, ago, bytes, dt } from '../lib/fmt.js';
  import { ACTIVE, KIND_LABEL, LANE, LANE_LABEL, STATUS_CLASS, jobDesc, jobSummary, fmtDur } from './SrcFresh.svelte';

  /** @type {{ id: number, onchange?: () => void }} */
  let { id, onchange } = $props();

  let job = $state(null);
  let pr = $state(null);
  let stopReq = $state(false);
  let logText = $state('');
  let logOffset = 0;
  let logFor = null;
  let err = $state('');
  let showLog = $state(false);

  const live = $derived(job ? ACTIVE.includes(job.status) : false);
  const unit = $derived(!job ? 'item' : { crawl: 'channel', comments: 'batch', discover: 'lookup', mediascan: 'file' }[job.kind] || 'video');

  async function poll() {
    const jid = id;
    try {
      const r = await get(`/api/jobs/${jid}/progress`);
      if (jid !== id) return;
      job = r.job; pr = r.progress; stopReq = r.stop_requested; err = '';
      if (showLog || ACTIVE.includes(r.job.status)) await pollLog(jid);
    } catch (e) { err = e.message; }
  }
  async function pollLog(jid) {
    if (logFor !== jid) { logFor = jid; logOffset = 0; logText = ''; }
    try {
      const r = await get(`/api/jobs/${jid}/log`, { offset: logOffset });
      if (jid !== id) return;
      if (r.text) { logText = (logText + r.text).slice(-12000); }
      logOffset = r.offset;
    } catch { /* log missing */ }
  }

  $effect(() => {
    id; app.jobsTick;
    job = null; pr = null; logText = ''; logFor = null;
    untrack(poll);
    const t = setInterval(() => { if (!document.hidden && (!job || ACTIVE.includes(job.status))) poll(); }, 2000);
    return () => clearInterval(t);
  });
  $effect(() => { if (showLog) untrack(() => pollLog(id)); });

  async function stop() { await attempt(() => post(`/api/jobs/${id}/stop`, {}), 'Stopping after the current step. What is collected so far is imported.'); await poll(); onchange?.(); }
  async function cancel() {
    const queued = job?.status === 'queued';
    await attempt(() => post(`/api/jobs/${id}/cancel`, {}), queued ? 'Removed from the queue' : 'Stopped. What was collected so far is still imported.');
    await poll(); onchange?.();
  }

  const t = $derived(pr?.totals || {});
  const cur = $derived(pr?.current);
  const tiles = $derived.by(() => {
    if (!pr) return [];
    const LBL = { requests: 'requests to Telegram', pages: 'pages read', posts_seen: 'posts read', posts_saved: 'new posts saved', media_files: 'media files', media_bytes: 'media size', channels_crawled: 'channels crawled', signals: 'links to other channels', errors: 'errors', retries: 'retries / rate limits', messages: 'messages read', comments: 'comments read', users: 'people seen', files: 'files', posts_done: 'posts done', posts_total: 'posts total' };
    return Object.entries(t).filter(([k]) => !['media_failed', 'media_skipped', 'names_classified', 'discovered'].includes(k))
      .map(([k, v]) => ({ k: LBL[k] || k.replaceAll('_', ' '), v: k.endsWith('bytes') ? bytes(v) : num(v), bad: (k === 'errors' && v > 0) }));
  });
  const planned = $derived(pr && pr.tool === 'tgspider' ? (t.channels_crawled || 0) + (cur?.mode === 'crawl' ? 1 : 0) + (pr.queue?.planned_crawl || 0) : 0);
  const pct = (a, b) => (b ? Math.min(100, Math.round((100 * (a || 0)) / b)) : 0);
</script>

{#if err && !job}
  <div class="callout bad" role="alert">{err}</div>
{:else if !job}
  <div class="loading"><span class="spinner"></span> Loading job #{id}…</div>
{:else}
  <div class="mon panel" class:live>
    <div class="panel-head">
      <h2>{KIND_LABEL[job.kind] || job.kind} #{job.id}</h2>
      <span class="pill {STATUS_CLASS[job.status] || ''}">{job.status}{#if live && job.status === 'running'} <span class="spinner tiny-sp"></span>{/if}</span>
      <span class="pill" class:warn={LANE[job.kind] === 'api'}>{LANE_LABEL[LANE[job.kind]] || ''}</span>
      <span class="spacer"></span>
      {#if job.status === 'queued'}
        <button class="sm danger" onclick={cancel}>Remove from queue</button>
      {:else if job.status === 'running'}
        {#if stopReq}<span class="pill warn">stopping after this {unit}…</span>
        {:else}<button class="sm" onclick={stop} title="Finish the {unit} in progress, then stop. Everything collected is imported.">Stop after this {unit}</button>{/if}
        <button class="sm danger" onclick={cancel} title="Stop right away. What was collected so far is still imported.">Stop now</button>
      {/if}
    </div>
    <div class="panel-body">
      <p class="small muted" style="margin:0 0 8px">{jobDesc(job)}</p>
      <div class="row small muted" style="margin-bottom:8px">
        <span>created {ago(job.created)}</span>
        {#if job.started}<span>· started {ago(job.started)}</span>{/if}
        {#if job.status === 'running' && pr}
          <span>· elapsed <b>{fmtDur(pr.elapsed_seconds)}</b></span>
          {#if pr.eta_seconds != null}<span>· about <b>{fmtDur(pr.eta_seconds)}</b> left{#if pr.settings?.depth > 1}<span class="faint" title="Following links adds channels as they are discovered, so this can grow."> (for channels found so far)</span>{/if}</span>{/if}
        {/if}
        {#if job.finished}<span>· finished {ago(job.finished)}{#if pr?.elapsed_seconds} · took {fmtDur(pr.elapsed_seconds)}{/if}</span>{/if}
      </div>

      {#if job.status === 'queued'}
        <div class="callout">Queued. It starts automatically when the job ahead of it in the {LANE[job.kind] === 'web' ? 'public web' : LANE[job.kind] === 'api' ? 'logged-in' : 'local'} lane finishes.</div>
      {:else if job.status === 'importing'}
        <div class="callout">Collection finished. Importing into the archive (hashing and copying files)…</div>
      {/if}
      {#if pr?.waiting && live}
        <div class="callout warn" style="margin-bottom:8px"><b>Paused:</b> {pr.waiting.reason}. Resumes in about {fmtDur(Math.max(0, pr.waiting.seconds - (Date.now() - Date.parse(pr.waiting.since)) / 1000))}.</div>
      {/if}

      {#if pr && job.status !== 'queued'}
        {#if pr.tool === 'tgspider' && planned}
          <div class="meter"><span class="lbl">Channels</span><div class="bar"><i style:width="{pct(t.channels_crawled, planned)}%"></i></div><span class="val">{t.channels_crawled || 0} of {planned}</span></div>
        {/if}
        {#if cur}
          <div class="cur">
            <div class="row small"><b>{cur.mode === 'classify' ? 'Checking' : pr.tool === 'tgspider' ? 'Crawling' : 'Working on'}
              {#if cur.channel}<a href={href('channel', cur.channel)}><bdi dir="ltr">@{cur.channel}</bdi></a>{/if}</b>
              {#if cur.title}<span dir="auto" class="muted">{cur.title}</span>{/if}
              <span class="spacer"></span><span class="muted">{cur.activity || ''}</span></div>
            {#if cur.pages_max}<div class="meter"><span class="lbl">Pages</span><div class="bar"><i style:width="{pct(cur.page, cur.pages_max)}%"></i></div><span class="val">{cur.page} / {cur.pages_max}</span></div>{/if}
            {#if cur.posts !== undefined}<div class="small muted">{num(cur.posts)} posts read · {num(cur.posts_saved)} new saved{#if cur.oldest} · reached back to {cur.oldest.slice(0, 10)}{/if}</div>{/if}
            {#if cur.media_total}<div class="meter"><span class="lbl">Media</span><div class="bar"><i style:width="{pct(cur.media_done, cur.media_total)}%"></i></div><span class="val">{cur.media_done} / {cur.media_total}</span></div>{/if}
          </div>
        {/if}
        <div class="tiles">
          {#each tiles as x}<div class="tile" class:bad={x.bad}><div class="v">{x.v}</div><div class="k">{x.k}</div></div>{/each}
        </div>
        {#if (pr.events || []).length}
          <div class="section-title" style="margin-top:12px">Activity</div>
          <div class="events">{#each [...pr.events].reverse().slice(0, 40) as e}<div class="ev {e.level}"><span class="mono faint">{(e.t || '').slice(11, 19)}</span> {e.msg}</div>{/each}</div>
        {/if}
      {:else if job.status !== 'queued' && !pr}
        <div class="callout">{live ? 'Starting…' : 'No live progress was recorded for this job.'}</div>
      {/if}
      {#if !live && job.summary}
        <p class="small" style="margin-top:8px"><b>Result:</b> {jobSummary(job) || '–'}</p>
      {/if}

      <details bind:open={showLog} style="margin-top:10px">
        <summary class="small muted">Raw log</summary>
        <pre class="log">{logText || (showLog ? '(empty)' : '')}</pre>
      </details>
    </div>
  </div>
{/if}

<style>
  .mon.live { border-color: var(--accent); }
  .tiny-sp { width: 10px; height: 10px; border-width: 2px; }
  .meter { display: grid; grid-template-columns: 70px 1fr auto; gap: 8px; align-items: center; margin: 4px 0; font-size: 12.5px; }
  .meter .lbl { color: var(--muted); }
  .meter .bar { height: 8px; }
  .cur { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; margin: 8px 0; background: var(--panel-2); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 6px; margin-top: 8px; }
  .tile { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 6px 9px; }
  .tile .v { font-size: 16px; font-weight: 650; font-variant-numeric: tabular-nums; }
  .tile .k { font-size: 11.5px; color: var(--muted); }
  .tile.bad .v { color: var(--bad); }
  .events { max-height: 180px; overflow: auto; font-size: 12px; border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 4px 8px; }
  .ev.warn { color: var(--warn); }
  .ev.error { color: var(--bad); }
  .log { max-height: 260px; overflow: auto; background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px; font: 11.5px/1.4 var(--mono); white-space: pre-wrap; word-break: break-all; margin: 6px 0 0; }
</style>
