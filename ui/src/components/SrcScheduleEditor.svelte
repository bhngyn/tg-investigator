<script>
  // Editor for one channel's watch (public posts re-crawl) or comment schedule (logged-in).
  // Calls onchange() after any change so the parent can reload.
  import { get, post } from '../lib/api.js';
  import { attempt, toast } from '../lib/store.svelte.js';
  import { ago, dt } from '../lib/fmt.js';
  import { INTERVALS, intervalLabel, until, jobSummary, STATUS_CLASS } from './SrcFresh.svelte';
  import SrcConfirm from './SrcConfirm.svelte';

  /** @type {{ kind: 'watch'|'schedule', channel: string, row: any, onchange?: () => void }} */
  let { kind, channel, row, onchange } = $props();

  const isWatch = $derived(kind === 'watch');
  const opts = $derived.by(() => {
    const base = isWatch ? INTERVALS : [[1, 'every minute'], [5, 'every 5 min'], [10, 'every 10 min'], [30, 'every 30 min'], [60, 'hourly'], [180, 'every 3 h'], [360, 'every 6 h'], [1440, 'daily']];
    const cur = Number(f.interval_min);
    return base.some(([m]) => m === cur) ? base : [...base, [cur, `every ${cur} min`]].sort((a, b) => a[0] - b[0]);
  });

  let f = $state({ interval_min: 360, pages: 30, recheck_days: 7, media: 'photos', enabled: true });
  let busy = $state(false);
  let confirm = $state(null);   // 'save' | 'run' | 'delete' | 'enable'
  let loadedId = null;

  // Reset the form whenever the row (or which row) changes.
  $effect(() => {
    const key = row ? `${row.id}:${row.interval_min}:${row.enabled}:${row.media}:${row.recheck_days}:${row.pages}` : 'none';
    if (key === loadedId) return;
    loadedId = key;
    f = row
      ? { interval_min: row.interval_min, pages: row.pages ?? 30, recheck_days: row.recheck_days ?? 7, media: row.media, enabled: !!row.enabled }
      : isWatch ? { interval_min: 360, pages: 30, recheck_days: 7, media: 'photos', enabled: true }
        : { interval_min: 60, pages: 30, recheck_days: 7, media: 'none', enabled: true };
  });

  const dirty = $derived(!row || row.interval_min !== Number(f.interval_min) || row.media !== f.media || !!row.enabled !== f.enabled
    || (isWatch && (row.pages !== Number(f.pages) || row.recheck_days !== Number(f.recheck_days))));
  const path = $derived(isWatch ? '/api/watches' : '/api/schedules');
  const what = $derived(isWatch ? 'watch' : 'comment schedule');

  function body() {
    const b = isWatch
      ? { interval_min: Number(f.interval_min), pages: Number(f.pages), recheck_days: Number(f.recheck_days), media: f.media, enabled: f.enabled }
      : { interval_min: Number(f.interval_min), media: f.media, enabled: f.enabled };
    return row ? { id: row.id, ...b } : { channel, ...b };
  }
  function askSave() {
    if (!isWatch && f.enabled) confirm = 'save'; else save();
  }
  async function save() {
    busy = true;
    const r = await attempt(() => post(path, body()), `Saved the ${what} for @${channel}`);
    busy = false; confirm = null;
    if (r) onchange?.();
  }
  async function runNow() {
    busy = true;
    const r = await attempt(() => post(`${path}/${row.id}/run`, {}));
    busy = false; confirm = null;
    if (!r) return;
    if (r.queued?.length) toast(`Queued job #${r.queued[0]}. Follow it on the Collection page.`, 'ok');
    else toast(`Nothing queued: ${isWatch ? 'a crawl' : 'a comment job'} for @${channel} is already queued or running.`, 'info');
    onchange?.();
  }
  async function del() {
    busy = true;
    const r = await attempt(() => post(path, { id: row.id, delete: true }), `Deleted the ${what} for @${channel}`);
    busy = false; confirm = null;
    if (r !== undefined) onchange?.();
  }
</script>

<div class="ed">
  {#if row}
    <div class="row small" style="margin-bottom:8px">
      <span class="pill" class:ok={row.enabled}>{row.enabled ? 'active' : 'paused'}</span>
      {#if row.enabled}<span class="muted">next run {row.next_run ? until(row.next_run) : 'within a minute'}</span>{/if}
      {#if row.last_run}
        <span class="muted">last run {ago(row.last_run)}</span>
        {#if row.last_status}<span class="pill {STATUS_CLASS[row.last_status] || ''}">{row.last_status}</span>{/if}
        {#if row.last_summary}<span class="muted">{jobSummary({ kind: isWatch ? 'crawl' : 'comments', summary: row.last_summary })}</span>{/if}
      {:else}<span class="muted">has not run yet</span>{/if}
    </div>
  {/if}
  <div class="form-grid">
    <label class="field">{isWatch ? 'Re-crawl posts' : 'Check for new comments'}
      <select bind:value={f.interval_min}>{#each opts as [m, l] (m)}<option value={m}>{l}</option>{/each}</select>
    </label>
    {#if isWatch}
      <label class="field">Re-check posts from the last (days)
        <input type="number" min="1" max="365" bind:value={f.recheck_days} />
        <span class="help">Posts inside this window are compared each run to find edits and deletions.</span>
      </label>
      <label class="field">Page cap
        <input type="number" min="1" max="100" bind:value={f.pages} />
        <span class="help">Only a safety limit; each run stops once it is past the window.</span>
      </label>
    {/if}
    <label class="field">Media
      <select bind:value={f.media}><option value="none">None</option><option value="photos">Photos</option><option value="all">Photos and videos</option></select>
    </label>
  </div>
  <div class="row" style="margin-top:10px">
    <label class="check"><input type="checkbox" bind:checked={f.enabled} /> Enabled</label>
    {#if !isWatch}<span class="pill warn">uses your Telegram account</span>{/if}
    <span class="spacer"></span>
    {#if row}
      <button class="sm" onclick={() => (isWatch ? runNow() : (confirm = 'run'))} disabled={busy}>Run now</button>
      <button class="sm danger" onclick={() => (confirm = 'delete')} disabled={busy}>Delete</button>
    {/if}
    <button class="primary sm" onclick={askSave} disabled={busy || !dirty}>{row ? 'Save changes' : `Create ${what}`}</button>
  </div>
  {#if isWatch}<p class="help">Watches read the public page only (no login). Media already stored is never downloaded again.</p>
  {:else}<p class="help">Runs in the background as long as this app is open. Each run is a comment collection using your logged-in account.</p>{/if}
</div>

<SrcConfirm open={confirm === 'save'} title="Save comment schedule?" confirmLabel="Save and use my account" {busy} onclose={() => (confirm = null)} onconfirm={save}
            lines={[{ title: `Comments of @${channel}, ${intervalLabel(Number(f.interval_min)).replace(/^every /, 'every ')}`, detail: `Media: ${f.media}. The first run starts within a minute, then repeats until you pause or delete it.`, login: true }]} />
<SrcConfirm open={confirm === 'run'} title="Run comment collection now?" confirmLabel="Run now" {busy} onclose={() => (confirm = null)} onconfirm={runNow}
            lines={[{ title: `Collect new comments of @${channel}`, detail: 'One job, queued immediately.', login: true }]} />
<SrcConfirm open={confirm === 'delete'} title="Delete this {what}?" danger confirmLabel="Delete" {busy} onclose={() => (confirm = null)} onconfirm={del}
            intro={`The ${what} for @${channel} is removed and stops running. Data already collected is kept, and the deletion is recorded in the audit log.`} />

<style>
  .ed { display: block; }
</style>
