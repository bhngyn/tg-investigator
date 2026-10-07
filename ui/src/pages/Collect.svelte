<script>
  // Collection control center: what is running (per lane), queue, history, watches, comment schedules, login status.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, setQuery } from '../lib/router.svelte.js';
  import { app, attempt, toast, currentCase } from '../lib/store.svelte.js';
  import { num, ago, dt, plural } from '../lib/fmt.js';
  import Empty from '../components/Empty.svelte';
  import Loading from '../components/Loading.svelte';
  import Modal from '../components/Modal.svelte';
  import SrcConfirm from '../components/SrcConfirm.svelte';
  import SrcJobMonitor from '../components/SrcJobMonitor.svelte';
  import SrcScheduleEditor from '../components/SrcScheduleEditor.svelte';
  import SrcFresh, { ACTIVE, KIND_LABEL, LANE, LANE_LABEL, STATUS_CLASS, jobChannels, jobDesc, jobSummary, intervalLabel, until, freshness } from '../components/SrcFresh.svelte';

  let jobs = $state([]);
  let watches = $state([]);
  let scheds = $state([]);
  let caseNames = $state(null);     // Set of channel names in the current case
  let loading = $state(true);
  let error = $state('');
  let selectedId = $state(null);
  function showJob(id) { selectedId = id; window.scrollTo?.(0, 0); document.querySelector('.main')?.scrollTo?.({ top: 0, behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }
  let tg = $state(null);
  let tgBusy = $state(false);

  const cid = $derived(caseId());
  const cur = $derived(currentCase());
  const onlyCase = $derived(cid != null && route.query.all !== '1');
  const statusFilter = $derived(route.query.status || '');

  async function load() {
    const id = caseId();
    try {
      const [j, w, s, c] = await Promise.all([
        get('/api/jobs'), get('/api/watches', id != null ? { case: id } : undefined), get('/api/schedules'),
        id != null ? get(`/api/cases/${id}`) : Promise.resolve(null),
      ]);
      if (caseId() !== id) return;
      jobs = j; watches = w; scheds = s;
      caseNames = c ? new Set(c.channels_detail.map((x) => x.channel)) : null;
      error = '';
    } catch (e) { error = e.message; } finally { loading = false; }
  }
  // A primitive: only a job starting or finishing (not every poll) reloads the page.
  const jobsBusy = $derived((app.jobs.running || 0) + (app.jobs.queued || 0));
  $effect(() => {
    caseId(); app.jobsTick; jobsBusy;
    untrack(load);
    untrack(() => { if (!app.telegram) get('/api/telegram').then((t) => (app.telegram = t)).catch(() => {}); });
  });
  // keep tables fresh while something is running
  $effect(() => {
    const t = setInterval(() => { if (!document.hidden && jobs.some((j) => ACTIVE.includes(j.status))) load(); }, 4000);
    return () => clearInterval(t);
  });

  const inScope = (j) => {
    if (!onlyCase || !caseNames) return true;
    const ch = jobChannels(j);
    return ch.length === 0 || ch.some((c) => caseNames.has(c));
  };
  const scopedJobs = $derived(jobs.filter(inScope));
  const activeJobs = $derived(scopedJobs.filter((j) => ACTIVE.includes(j.status)));
  const runningJobs = $derived(activeJobs.filter((j) => j.status !== 'queued'));
  const queued = $derived(activeJobs.filter((j) => j.status === 'queued'));
  const history = $derived(scopedJobs.filter((j) => !ACTIVE.includes(j.status) && (!statusFilter || j.status === statusFilter)));
  const scopedSchedules = $derived(scheds.filter((s) => !onlyCase || !caseNames || caseNames.has(s.channel)));
  const schedulerErr = $derived(scheds.find((s) => s.scheduler_error)?.scheduler_error);

  // the job shown in the monitor: clicked one, else the first running, else the first queued
  const shownId = $derived(selectedId ?? (runningJobs[0] || queued[0])?.id ?? null);

  // The session line comes from the server as text ("Logged in as B (@-, id …)"); "@-" means the account has no username.
  const tgMsg = (m) => String(m || '').replace(/\(@-(?=[,)])/, '(no username');
  const lanes = $derived(['web', 'api', 'local'].map((l) => ({
    key: l,
    running: runningJobs.filter((j) => LANE[j.kind] === l),
    queued: queued.filter((j) => LANE[j.kind] === l),
  })));
  const laneNote = {
    web: 'Crawls of public t.me pages, one at a time.',
    api: 'Comments, similar channels, full videos. One at a time, because the Telegram session can only be used by one process.',
    local: 'Media scans on files already collected. No network.',
  };

  async function checkTg() {
    tgBusy = true;
    const r = await attempt(() => get('/api/telegram', { force: 1 }));
    tgBusy = false;
    if (r) app.telegram = r;
  }
  const tgOk = $derived(app.telegram?.ok);

  // ---- watch / schedule actions ------------------------------------------------------------
  let editing = $state(null);    // {kind, row}
  let confirm = $state(null);    // {type, kind, row}
  let busy = $state(false);

  async function toggleWatch(w) {
    const r = await attempt(() => post('/api/watches', { id: w.id, enabled: !w.enabled }), w.enabled ? `Paused the watch on @${w.channel}` : `Resumed the watch on @${w.channel}`);
    if (r) load();
  }
  async function runWatch(w) {
    const r = await attempt(() => post(`/api/watches/${w.id}/run`, {}));
    if (!r) return;
    if (r.queued?.length) { toast(`Queued crawl #${r.queued[0]} for @${w.channel}.`, 'ok'); selectedId = r.queued[0]; }
    else toast(`A crawl of @${w.channel} is already queued or running.`, 'info');
    load();
  }
  async function doConfirm() {
    const { type, kind, row } = confirm;
    busy = true;
    let ok;
    if (kind === 'schedule' && type === 'enable') ok = await attempt(() => post('/api/schedules', { id: row.id, enabled: true }), `Resumed comment schedule for @${row.channel}`);
    else if (kind === 'schedule' && type === 'run') {
      const r = await attempt(() => post(`/api/schedules/${row.id}/run`, {}));
      if (r) { ok = true; if (r.queued?.length) { toast(`Queued comment job #${r.queued[0]}.`, 'ok'); selectedId = r.queued[0]; } else toast(`A comment job for @${row.channel} is already queued or running.`, 'info'); }
    } else if (type === 'delete') ok = await attempt(() => post(kind === 'watch' ? '/api/watches' : '/api/schedules', { id: row.id, delete: true }), `Deleted the ${kind === 'watch' ? 'watch' : 'comment schedule'} for @${row.channel}`);
    busy = false;
    if (ok !== undefined) { confirm = null; load(); }
  }
  async function pauseSchedule(s) {
    const r = await attempt(() => post('/api/schedules', { id: s.id, enabled: false }), `Paused comment schedule for @${s.channel}`);
    if (r) load();
  }
  const confirmLines = $derived.by(() => {
    if (!confirm) return [];
    const { type, kind, row } = confirm;
    if (kind === 'schedule' && type === 'enable') return [{ title: `Resume comment collection for @${row.channel}`, detail: `${intervalLabel(row.interval_min)}. The next run starts within a minute.`, login: true }];
    if (kind === 'schedule' && type === 'run') return [{ title: `Collect new comments of @${row.channel} now`, detail: 'One job, queued immediately.', login: true }];
    return [];
  });
  const confirmTitle = $derived(!confirm ? '' : confirm.type === 'delete' ? `Delete this ${confirm.kind === 'watch' ? 'watch' : 'comment schedule'}?` : confirm.type === 'run' ? 'Run comment collection now?' : 'Resume comment schedule?');
</script>

<div class="page wide">
  <div class="page-head">
    <div>
      <h1>Collection</h1>
      <p>What is being collected right now, what is waiting, what runs on a schedule, and what happened before.
        {#if onlyCase}Showing jobs for channels in "{cur?.name || 'this case'}".{/if}</p>
    </div>
    <div class="row">
      {#if cid != null}
        <label class="check small"><input type="checkbox" checked={!onlyCase} onchange={(e) => setQuery({ all: e.currentTarget.checked ? '1' : '' })} /> Show every job</label>
      {/if}
      <a class="btn" href={href('channels')}>Channels</a>
      <a class="btn primary" href={href('add')}>＋ Add channels</a>
    </div>
  </div>

  {#if loading}<Loading />
  {:else if error}<div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
  {:else}
    {#if schedulerErr}<div class="callout bad" style="margin-bottom:12px"><b>The background scheduler hit an error:</b> {schedulerErr}. Watches and schedules may not be running.</div>{/if}

    <!-- Telegram login -->
    <div class="callout" class:ok={tgOk} class:warn={app.telegram && !tgOk} style="margin-bottom:14px">
      <div class="row">
        <b>Telegram session on this Mac:</b>
        {#if !app.telegram}<span class="muted">not checked yet</span>
        {:else if tgOk}<span>{tgMsg(app.telegram.message)} <span class="tiny muted">(one login per Mac user, shared by every workspace)</span></span>
        {:else}<span>Not logged in. {tgMsg(app.telegram.message)}</span>{/if}
        <span class="spacer"></span>
        <button class="sm" onclick={checkTg} disabled={tgBusy} title="Asks Telegram whether the saved session still works">{#if tgBusy}<span class="spinner"></span>{/if}Re-check</button>
      </div>
      {#if app.telegram && !tgOk}
        <p class="small" style="margin:6px 0 0">Comments, similar-channel lookups and full-size video need a logged-in session. To log in, open a terminal in the project folder and run <code>.venv/bin/python tgmedia.py login</code> yourself. Never type your code or password into this app. Crawling public posts works without it.</p>
      {:else if tgOk}
        <p class="small muted" style="margin:6px 0 0">Steps marked "your Telegram account" (comments, similar channels, full video) use this session. Telegram can see those requests.</p>
      {/if}
    </div>

    <!-- lanes -->
    <div class="grid three lanes">
      {#each lanes as l (l.key)}
        <div class="panel lane" class:busy={l.running.length}>
          <div class="panel-head">
            <h3>{LANE_LABEL[l.key]}</h3>
            {#if l.queued.length}<span class="pill">{l.queued.length} queued</span>{/if}
          </div>
          <div class="panel-body">
            {#each l.running as j (j.id)}
              <button class="jobline" class:sel={shownId === j.id} onclick={() => (selectedId = j.id)}>
                <span class="spinner"></span>
                <span class="grow"><b>{KIND_LABEL[j.kind] || j.kind}</b> #{j.id}<span class="small muted ellipsis block">{jobDesc(j)}</span></span>
                <span class="pill info">{j.status}</span>
              </button>
            {:else}
              <div class="muted small">Idle</div>
            {/each}
            <div class="tiny faint" style="margin-top:6px">{laneNote[l.key]}</div>
          </div>
        </div>
      {/each}
    </div>

    <!-- monitor -->
    {#if shownId}
      <div style="margin-top:14px">
        <SrcJobMonitor id={shownId} onchange={load} />
      </div>
    {:else}
      <div class="panel" style="margin-top:14px"><Empty title="Nothing is running">
        Start a collection from a channel's page, or add new channels. Progress, the activity feed and a log will show here live.
        {#snippet action()}<a class="btn primary" href={href('add')}>＋ Add channels</a>{/snippet}
      </Empty></div>
    {/if}

    <!-- queue -->
    {#if queued.length}
      <div class="section-title">Queue ({queued.length})</div>
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">#</th><th scope="col">Job</th><th scope="col">Lane</th><th scope="col">Queued</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each queued as j (j.id)}
            <tr class="click" class:sel={shownId === j.id} onclick={(e) => { if (!e.target.closest('button')) selectedId = j.id; }}>
              <td>{j.id}</td><td><button class="rowbtn" onclick={() => showJob(j.id)} title="Show this job's progress and log"><b>{KIND_LABEL[j.kind] || j.kind}</b></button><div class="small muted">{jobDesc(j)}</div></td>
              <td class="small">{LANE_LABEL[LANE[j.kind]]}</td><td class="small">{ago(j.created)}</td>
              <td class="right"><button class="sm danger" onclick={() => attempt(() => post(`/api/jobs/${j.id}/cancel`, {}), 'Removed from the queue').then(load)}>Remove</button></td>
            </tr>
          {/each}
        </tbody></table></div>
    {/if}

    <!-- watches -->
    <div class="section-title">Watches: automatic re-crawls of public posts ({watches.length})</div>
    {#if !watches.length}
      <div class="panel"><Empty title="No watches">
        A watch re-crawls a channel on a schedule to catch new, edited and deleted posts. Set one up on a channel's Collection tab, or when adding channels.
      </Empty></div>
    {:else}
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">Channel</th><th scope="col">Every</th><th scope="col">Re-check</th><th scope="col">Last run</th><th scope="col">Next run</th><th scope="col">Last crawled</th><th scope="col" class="right">Deleted found</th><th scope="col">On</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each watches as w (w.id)}
            <tr>
              <td><a href={href('channel', w.channel, { tab: 'collection' })}><b>{w.title || '@' + w.channel}</b></a><div class="tiny faint"><bdi dir="ltr">@{w.channel}</bdi></div></td>
              <td class="nowrap">{intervalLabel(w.interval_min)}</td>
              <td class="small nowrap">{w.recheck_days} d · {w.media === 'none' ? 'no media' : w.media}</td>
              <td class="small">
                {#if w.last_run}{ago(w.last_run)} {#if w.last_status}<span class="pill {STATUS_CLASS[w.last_status] || ''}">{w.last_status}</span>{/if}
                  <div class="tiny muted">{jobSummary({ kind: 'crawl', summary: w.last_summary })}</div>
                {:else}<span class="faint">not run yet</span>{/if}
              </td>
              <td class="small nowrap">{w.enabled ? (w.next_run ? until(w.next_run) : 'within a minute') : '–'}</td>
              <td class="nowrap"><SrcFresh iso={w.last_crawled} watch={w} /></td>
              <td class="right num">{w.deleted ? num(w.deleted) : '–'}</td>
              <td><label class="check"><input type="checkbox" checked={!!w.enabled} onchange={() => toggleWatch(w)} aria-label="Watch on @{w.channel} enabled" /></label></td>
              <td class="nowrap right">
                <button class="sm" onclick={() => runWatch(w)}>Run now</button>
                <button class="sm" onclick={() => (editing = { kind: 'watch', row: w })}>Edit</button>
                <button class="ghost sm danger" onclick={() => (confirm = { type: 'delete', kind: 'watch', row: w })}>Delete</button>
              </td>
            </tr>
          {/each}
        </tbody></table></div>
    {/if}

    <!-- comment schedules -->
    <div class="section-title">Comment schedules: repeat collection with your Telegram account ({scopedSchedules.length})</div>
    {#if !scopedSchedules.length}
      <div class="panel"><Empty title="No comment schedules">
        Comment sections are only collected when you ask, or on a schedule you set on a channel's Collection tab.
      </Empty></div>
    {:else}
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">Channel</th><th scope="col">Every</th><th scope="col">Media</th><th scope="col">Last run</th><th scope="col">Next run</th><th scope="col">On</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each scopedSchedules as s (s.id)}
            <tr>
              <td><a href={href('channel', s.channel, { tab: 'collection' })}><b><bdi dir="ltr">@{s.channel}</bdi></b></a></td>
              <td class="nowrap">{intervalLabel(s.interval_min)}</td>
              <td class="small">{s.media === 'none' ? 'none' : s.media}</td>
              <td class="small">
                {#if s.last_run}{ago(s.last_run)} {#if s.last_status}<span class="pill {STATUS_CLASS[s.last_status] || ''}">{s.last_status}</span>{/if}
                  <div class="tiny muted">{jobSummary({ kind: 'comments', summary: s.last_summary })}</div>
                {:else}<span class="faint">not run yet</span>{/if}
              </td>
              <td class="small nowrap">{s.enabled ? (s.next_run ? until(s.next_run) : 'within a minute') : '–'}</td>
              <td><label class="check"><input type="checkbox" checked={!!s.enabled} onchange={(e) => { if (s.enabled) pauseSchedule(s); else { e.currentTarget.checked = false; confirm = { type: 'enable', kind: 'schedule', row: s }; } }} aria-label="Schedule for @{s.channel} enabled" /></label></td>
              <td class="nowrap right">
                <button class="sm" onclick={() => (confirm = { type: 'run', kind: 'schedule', row: s })}>Run now…</button>
                <button class="sm" onclick={() => (editing = { kind: 'schedule', row: s })}>Edit</button>
                <button class="ghost sm danger" onclick={() => (confirm = { type: 'delete', kind: 'schedule', row: s })}>Delete</button>
              </td>
            </tr>
          {/each}
        </tbody></table></div>
    {/if}

    <!-- history -->
    <div class="section-title row">
      <span>History (latest 100 jobs)</span><span class="spacer"></span>
      <select value={statusFilter} onchange={(e) => setQuery({ status: e.currentTarget.value })} aria-label="Filter by status" style="text-transform:none;letter-spacing:0">
        <option value="">All results</option><option value="done">Done</option><option value="stopped">Stopped</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option><option value="interrupted">Interrupted</option>
      </select>
    </div>
    {#if !history.length}
      <div class="panel"><Empty title="No finished jobs{statusFilter ? ' with this result' : ''}"></Empty></div>
    {:else}
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">#</th><th scope="col">Job</th><th scope="col">Result</th><th scope="col">Started</th><th scope="col">Took</th><th scope="col">Summary</th></tr></thead>
        <tbody>
          {#each history as j (j.id)}
            <tr class="click" class:sel={shownId === j.id} onclick={() => showJob(j.id)}>
              <td>{j.id}</td>
              <td><button class="rowbtn" onclick={(e) => { e.stopPropagation(); showJob(j.id); }} title="Show this job's progress and log"><b>{KIND_LABEL[j.kind] || j.kind}</b></button><div class="small muted">{jobDesc(j)}</div></td>
              <td><span class="pill {STATUS_CLASS[j.status] || ''}">{j.status}</span></td>
              <td class="small nowrap" title={dt(j.started || j.created)}>{ago(j.started || j.created)}</td>
              <td class="small nowrap">{j.started && j.finished ? Math.max(1, Math.round((Date.parse(j.finished) - Date.parse(j.started)) / 1000)) + ' s' : '–'}</td>
              <td class="small">{jobSummary(j)}</td>
            </tr>
          {/each}
        </tbody></table></div>
      <p class="help" style="margin-top:6px">Click a row to see its progress record and log above.</p>
    {/if}
  {/if}
</div>

<Modal open={!!editing} title={editing ? `${editing.kind === 'watch' ? 'Watch' : 'Comment schedule'}: @${editing.row.channel}` : ''} onclose={() => (editing = null)} width="640px">
  {#if editing}
    <SrcScheduleEditor kind={editing.kind} channel={editing.row.channel} row={editing.row} onchange={() => { editing = null; load(); }} />
  {/if}
</Modal>

<SrcConfirm open={!!confirm} title={confirmTitle} lines={confirmLines} {busy} danger={confirm?.type === 'delete'}
            confirmLabel={confirm?.type === 'delete' ? 'Delete' : confirm?.type === 'run' ? 'Run now' : 'Resume'}
            intro={confirm?.type === 'delete' ? `This removes the ${confirm.kind === 'watch' ? 'watch' : 'schedule'} for @${confirm.row.channel}. Data already collected is kept, and the deletion is recorded in the audit log.` : ''}
            onclose={() => (confirm = null)} onconfirm={doConfirm} />

<style>
  .lane.busy { border-color: var(--accent); }
  .jobline { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 6px 8px; margin-bottom: 4px; white-space: normal; }
  .jobline.sel { border-color: var(--accent); background: var(--accent-soft); }
  .grow { flex: 1; min-width: 0; }
  .block { display: block; }
</style>
