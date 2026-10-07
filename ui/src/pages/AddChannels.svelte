<script>
  // Add channels: paste handles -> live preview -> choose steps per channel -> confirm -> collect.
  import { onMount } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href, hrefIn } from '../lib/router.svelte.js';
  import { app, attempt, toast, currentCase, loadCases } from '../lib/store.svelte.js';
  import { num, compact, ago, plural } from '../lib/fmt.js';
  import SrcStepChooser, { defaultSteps, cloneSteps, stepsToBody, describeSteps, usesLogin } from '../components/SrcStepChooser.svelte';
  import SrcConfirm from '../components/SrcConfirm.svelte';
  import { ACTIVE, KIND_LABEL, STATUS_CLASS, fmtDur, jobSummary } from '../components/SrcFresh.svelte';

  const inCase = $derived(caseId() != null);
  const cur = $derived(currentCase());

  let view = $state('paste');           // paste | review | run
  let text = $state('');
  let resolving = $state(false);
  let progress = $state('');
  let entries = $state([]);
  let defaults = $state(defaultSteps());
  let defaultsOpen = $state(false);
  let defaultRole = $state('seed');
  let caseSel = $state('');             // "all" scope only: chosen case id or ''
  let mode = $state('collect');         // collect | add
  let confirmOpen = $state(false);
  let running = $state(false);
  let results = $state([]);
  let jobsById = $state({});

  onMount(() => {
    const h = route.query.handles;
    if (h) text = String(h).split(',').join('\n');
  });

  const targetCase = $derived(inCase ? caseId() : caseSel ? Number(caseSel) : null);
  const targetCaseName = $derived(targetCase == null ? '' : (app.cases.find((c) => c.id === targetCase)?.name || `case ${targetCase}`));

  /** Split pasted text into unique raw handles. */
  const parsed = $derived.by(() => {
    const seen = new Set();
    const out = [];
    for (const raw of text.split(/[\s,;]+/)) {
      const t = raw.trim();
      if (!t) continue;
      const key = t.toLowerCase().replace(/^https?:\/\//, '').replace(/^(www\.)?t\.me\/(s\/)?/, '').replace(/^@/, '').replace(/\/.*$/, '');
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(t);
    }
    return out;
  });

  // ---- 1. resolve -----------------------------------------------------------------------
  async function resolve(live) {
    if (!parsed.length) { toast('Paste at least one handle first.', 'error'); return; }
    resolving = true;
    const all = [];
    try {
      const batch = live ? 25 : 200;
      for (let i = 0; i < parsed.length; i += batch) {
        const part = parsed.slice(i, i + batch);
        progress = live ? `Checking ${Math.min(i + batch, parsed.length)} of ${parsed.length} on t.me (about a second each)…` : 'Checking your archive…';
        all.push(...await post('/api/resolve', { handles: part, live }));
      }
    } catch (e) {
      toast(e.message, 'error', 7000);
      resolving = false; progress = '';
      return;
    }
    resolving = false; progress = '';
    const seen = new Set();
    entries = all.filter((r) => {
      const k = r.username || r.input;
      if (seen.has(k)) return false;
      seen.add(k); return true;
    }).map((r) => makeEntry(r, live));
    view = 'review';
  }

  function makeEntry(r, live) {
    const type = r.error ? 'invalid' : r.live?.error ? 'unknown' : r.live?.type || r.known?.type || 'unchecked';
    const preview = r.live && !r.live.error ? !!r.live.preview : (r.known ? !!r.known.preview : null);
    const canPosts = !r.error && (preview === null || preview);
    const isChannelish = !r.error && (type === 'channel' || type === 'unchecked' || type === 'unknown' || type === 'group');
    const e = {
      ...r, live, liveInfo: r.live || null, type, preview, canPosts,
      include: !r.error && isChannelish && (canPosts || type === 'group'),
      role: defaultRole, open: false,
      steps: forChannel(defaults, canPosts),
    };
    return e;
  }
  function forChannel(base, canPosts) {
    const s = cloneSteps(base);
    if (!canPosts) { s.posts.on = false; s.watch.on = false; }
    return s;
  }
  function applyToAll() {
    for (const e of entries) if (!e.error) e.steps = forChannel(defaults, e.canPosts);
    toast('Applied these steps to every channel.', 'ok');
  }
  function setAllRole(role) { defaultRole = role; for (const e of entries) e.role = role; }
  const included = $derived(entries.filter((e) => e.include && !e.error));

  // ---- 2. confirm ------------------------------------------------------------------------
  const lines = $derived.by(() => {
    if (mode === 'add') {
      return [{ title: `Add ${plural(included.length, 'channel')} to "${targetCaseName}"`, detail: included.map((e) => `@${e.username} (${e.role})`).join(', ') + '. Nothing is collected yet.' }];
    }
    return included.map((e) => {
      const d = describeSteps(e.steps);
      return {
        title: `@${e.username}${targetCase != null ? ` · ${e.role} in "${targetCaseName}"` : ''}`,
        detail: d.length ? d.map((x) => `${x.title}: ${x.detail}${x.login ? ' (your Telegram account)' : ''}`).join('; ') : 'Save the channel only, collect nothing.',
        login: d.some((x) => x.login),
      };
    });
  });
  const estSeconds = $derived(mode === 'add' ? 0 : included.reduce((n, e) => n + (e.steps.posts.on ? Number(e.steps.posts.pages || 0) * 1.5 * (Number(e.steps.posts.depth) > 1 ? Math.min(e.steps.posts.max_channels || 30, 30) : 1) : 0), 0));
  const anyLogin = $derived(mode === 'collect' && included.some((e) => usesLogin(e.steps)));

  async function run() {
    running = true;
    const out = [];
    if (mode === 'add') {
      const byRole = {};
      for (const e of included) (byRole[e.role] ||= []).push(e.username);
      for (const [role, names] of Object.entries(byRole)) {
        try {
          const r = await post(`/api/cases/${targetCase}/channels`, { add: names, role });
          for (const n of names) out.push({ name: n, added: r.added.includes(n), already: r.already.includes(n), jobs: {} });
        } catch (e) { for (const n of names) out.push({ name: n, error: e.message, jobs: {} }); }
      }
    } else {
      for (const e of included) {
        const body = stepsToBody(e.steps);
        if (targetCase != null) { body.case = targetCase; body.role = e.role; }
        try {
          const r = await post(`/api/channels/${encodeURIComponent(e.username)}/collect`, body);
          out.push({ name: e.username, jobs: r.jobs || {}, watch: r.watch, sched: r.comment_schedule, case: r.case });
        } catch (err) { out.push({ name: e.username, error: err.message, jobs: {} }); }
      }
    }
    results = out;
    running = false; confirmOpen = false; view = 'run';
    loadCases();
    refreshJobs();
    const bad = out.filter((r) => r.error).length;
    if (bad) toast(`${plural(bad, 'channel')} could not be set up. See the list below.`, 'error', 7000);
    else toast(mode === 'add' ? 'Channels added.' : 'Collection set up.', 'ok');
  }

  // ---- 3. live job status ---------------------------------------------------------------------
  async function refreshJobs() {
    try {
      const list = await get('/api/jobs');
      jobsById = Object.fromEntries(list.map((j) => [j.id, j]));
    } catch { /* keep old */ }
  }
  const jobIds = $derived(results.flatMap((r) => Object.values(r.jobs || {})));
  const anyActive = $derived(jobIds.some((id) => ACTIVE.includes(jobsById[id]?.status) || !jobsById[id]));
  $effect(() => {
    if (view !== 'run') return;
    app.jobsTick;
    const t = setInterval(() => { if (!document.hidden) refreshJobs(); }, 2500);
    return () => clearInterval(t);
  });

  function reset() {
    view = 'paste'; text = ''; entries = []; results = []; mode = 'collect';
  }
  const typePill = (t) => ({ channel: 'ok', group: 'info', user: 'warn', bot: 'warn', invalid: 'bad' }[t] || '');
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>Add channels</h1>
      <p>Paste handles, preview them from Telegram's public pages, choose what to collect for each one, then confirm.
        {#if inCase}They will be added to "{cur?.name || 'this case'}".{/if}</p>
    </div>
    <ol class="steps-nav" aria-label="Progress">
      <li class:on={view === 'paste'} class:done={view !== 'paste'}>1 Paste</li>
      <li class:on={view === 'review'} class:done={view === 'run'}>2 Choose steps</li>
      <li class:on={view === 'run'}>3 Collecting</li>
    </ol>
  </div>

  {#if view === 'paste'}
    <div class="panel panel-pad">
      <label class="field">Handles or links
        <textarea bind:value={text} rows="7" placeholder={'@example_channel\nt.me/example_news\nsample_channel, another_example'} dir="auto" spellcheck="false"></textarea>
        <span class="help">One per line or separated by commas. Accepts @name, name, t.me/name and t.me/s/name. Private invite links (t.me/+…) cannot be read without joining, so they are rejected.</span>
      </label>
      <div class="row" style="margin-top:12px">
        <button class="primary" onclick={() => resolve(true)} disabled={resolving || !parsed.length}>
          {#if resolving}<span class="spinner"></span>{/if} Preview {parsed.length ? plural(parsed.length, 'handle') : 'handles'} from Telegram
        </button>
        <button onclick={() => resolve(false)} disabled={resolving || !parsed.length} title="Only looks at what is already in your archive. Nothing is fetched from Telegram.">Check archive only</button>
        {#if progress}<span class="muted small">{progress}</span>{/if}
      </div>
      <p class="help" style="margin-top:10px">
        "Preview from Telegram" fetches each public t.me page once, with no login, to show type, size, and whether posts can be read. Telegram can see these page visits like any other. Nothing is saved until you confirm.
        {#if parsed.length > 25}Handles are checked in batches of 25.{/if}
      </p>
    </div>

  {:else if view === 'review'}
    <div class="panel" style="margin-bottom:12px">
      <div class="panel-head">
        <h2>Defaults for every channel</h2>
        <button class="sm" onclick={() => (defaultsOpen = !defaultsOpen)}>{defaultsOpen ? 'Hide' : 'Edit'} steps</button>
      </div>
      <div class="panel-body">
        <div class="row" style="margin-bottom:8px">
          <div class="seg" role="group" aria-label="What to do">
            <button class:on={mode === 'collect'} aria-pressed={mode === 'collect'} onclick={() => (mode = 'collect')}>Add and collect</button>
            <button class:on={mode === 'add'} aria-pressed={mode === 'add'} onclick={() => (mode = 'add')} disabled={targetCase == null}
                    title={targetCase == null ? 'Pick a case to add to' : 'Add to the case without collecting anything'}>Just add to case</button>
          </div>
          {#if !inCase}
            <label class="row tight small">Case
              <select bind:value={caseSel} aria-label="Case to add to" onchange={() => { if (!caseSel) mode = 'collect'; }}>
                <option value="">None (keep in the archive only)</option>
                {#each app.cases as c (c.id)}<option value={String(c.id)}>{c.name}</option>{/each}
              </select>
            </label>
          {:else}
            <span class="small muted">Case: <b>{cur?.name || caseId()}</b></span>
          {/if}
          {#if targetCase != null}
            <label class="row tight small">Role
              <select value={defaultRole} onchange={(e) => setAllRole(e.currentTarget.value)} aria-label="Role for all">
                <option value="seed">Seed (central)</option><option value="watched">Watched</option><option value="context">Context (background)</option>
              </select>
            </label>
          {/if}
        </div>
        {#if mode === 'collect'}
          {#if defaultsOpen}
            <SrcStepChooser bind:steps={defaults} idp="def" />
            <div class="row" style="margin-top:10px"><button class="primary sm" onclick={applyToAll}>Apply to all {entries.length} channels</button>
              <span class="help">Overwrites the per-channel choices below. Steps that don't apply (no public preview) stay off.</span></div>
          {:else}
            <div class="chips">
              {#each describeSteps(defaults) as s}<span class="pill" class:warn={s.login}>{s.title}{s.login ? ' · your account' : ''}</span>{:else}<span class="muted small">No steps selected: channels are only saved.</span>{/each}
              <button class="ghost sm" onclick={applyToAll}>Apply to all</button>
            </div>
          {/if}
        {:else}
          <p class="help">Channels are added to the case and nothing is collected. You can collect later from the channel page.</p>
        {/if}
      </div>
    </div>

    <div class="section-title">{plural(entries.length, 'channel')} · {included.length} selected</div>
    <div class="col">
      {#each entries as e (e.username || e.input)}
        <article class="panel entry" class:dim={!e.include}>
          <div class="row top">
            <input type="checkbox" bind:checked={e.include} disabled={!!e.error} aria-label="Include {e.username || e.input}" />
            <div class="grow">
              <div class="row">
                <b dir="auto">{e.liveInfo?.title || e.known?.title || (e.username ? '@' + e.username : e.input)}</b>
                {#if e.username}<span class="faint small"><bdi dir="ltr">@{e.username}</bdi></span>{/if}
                <span class="pill {typePill(e.type)}">{e.type === 'unchecked' ? 'not checked' : e.type}</span>
                {#if e.preview === true}<span class="pill ok" title="Posts can be read without logging in">public preview</span>
                {:else if e.preview === false && !e.error}<span class="pill warn" title="No public web preview: posts cannot be crawled without logging in">no preview</span>{/if}
                {#if e.liveInfo?.subscribers ?? e.known?.subscribers}<span class="small muted">{compact(e.liveInfo?.subscribers ?? e.known?.subscribers)} subscribers</span>{/if}
                {#if e.liveInfo?.latest_post}<span class="small muted">latest post {ago(e.liveInfo.latest_post)}</span>{/if}
                <a class="small" href={`https://t.me/${e.username}`} target="_blank" rel="noreferrer noopener" style:display={e.username ? '' : 'none'}>t.me ↗</a>
              </div>
              {#if e.error}
                <div class="small" style="color:var(--bad)">{e.error}</div>
              {:else}
                {#if e.liveInfo?.error}<div class="small" style="color:var(--warn)">Could not check Telegram: {e.liveInfo.error}. You can still add it.</div>{/if}
                {#if e.type === 'user' || e.type === 'bot'}<div class="small" style="color:var(--warn)">This is a {e.type}, not a channel. It has no posts to collect; it is unticked by default.</div>{/if}
                {#if e.type === 'group' && !e.preview}<div class="small muted">Public group without a post preview. Only comment collection (your account) can read it.</div>{/if}
                <div class="row tight small">
                  {#if e.known}
                    <span class="pill info">already in archive</span>
                    <span class="muted">{plural(e.known.posts, 'post')}, last crawled {ago(e.known.last_crawled)}{#if e.known.has_comments}, comments collected{/if}</span>
                  {:else}<span class="pill accent">new</span>{/if}
                  {#each e.cases || [] as cn}<span class="pill" title="Already in this case">in {cn}</span>{/each}
                </div>
              {/if}
            </div>
            {#if !e.error}
              {#if targetCase != null}
                <select bind:value={e.role} aria-label="Role of {e.username}" class="inl">
                  <option value="seed">Seed</option><option value="watched">Watched</option><option value="context">Context</option>
                </select>
              {/if}
              {#if mode === 'collect'}<button class="sm" onclick={() => (e.open = !e.open)} aria-expanded={e.open}>{e.open ? 'Hide steps' : 'Steps…'}</button>{/if}
            {/if}
          </div>
          {#if !e.error && mode === 'collect'}
            {#if e.open}
              <div class="sub"><SrcStepChooser bind:steps={e.steps} canPosts={e.canPosts} idp={e.username} /></div>
            {:else if e.include}
              <div class="chips sub">
                {#each describeSteps(e.steps) as s}<span class="pill" class:warn={s.login}>{s.title}{s.login ? ' · your account' : ''}</span>{:else}<span class="muted small">Only save the channel, collect nothing</span>{/each}
              </div>
            {/if}
          {/if}
        </article>
      {/each}
    </div>

    <div class="footbar panel">
      <button onclick={() => (view = 'paste')}>← Back</button>
      <span class="spacer"></span>
      <span class="muted small">{plural(included.length, 'channel')}{targetCase != null ? ` → "${targetCaseName}"` : ''}</span>
      <button class="primary" disabled={!included.length} onclick={() => (confirmOpen = true)}>Review and confirm…</button>
    </div>

  {:else}
    <div class="panel">
      <div class="panel-head">
        <h2>{mode === 'add' ? 'Added' : 'Set up'}: {plural(results.length, 'channel')}</h2>
        {#if anyActive && mode === 'collect'}<span class="pill info"><span class="spinner"></span> collecting</span>{/if}
      </div>
      <div class="table-wrap" style="border:0">
        <table class="t">
          <thead><tr><th scope="col">Channel</th><th scope="col">Jobs</th><th scope="col">Repeating</th><th scope="col">Result</th></tr></thead>
          <tbody>
            {#each results as r (r.name)}
              <tr>
                <td><a href={href('channel', r.name)}><b><bdi dir="ltr">@{r.name}</bdi></b></a></td>
                <td>
                  {#each Object.entries(r.jobs || {}) as [k, id]}
                    {@const j = jobsById[id]}
                    <div class="row tight small">
                      <a href={href('collect')} title="Open Collection">#{id}</a> {k === 'similar' ? 'Similar channels' : KIND_LABEL[k === 'posts' ? 'crawl' : k] || k}
                      <span class="pill {j ? STATUS_CLASS[j.status] : ''}">{j ? j.status : 'queued'}</span>
                    </div>
                  {:else}<span class="faint small">{mode === 'add' ? '–' : 'none started'}</span>{/each}
                </td>
                <td class="small">
                  {#if r.watch}<div><span class="pill ok">watching</span> re-crawl every {r.watch.interval_min} min</div>{/if}
                  {#if r.sched}<div><span class="pill warn">comment schedule</span> every {r.sched.interval_min} min</div>{/if}
                  {#if !r.watch && !r.sched}<span class="faint">–</span>{/if}
                </td>
                <td class="small">
                  {#if r.error}<span style="color:var(--bad)">{r.error}</span>
                  {:else}
                    {#each Object.values(r.jobs || {}) as id}{#if jobsById[id] && jobsById[id].summary}<div>{jobSummary(jobsById[id])}</div>{/if}{/each}
                    {#if mode === 'add'}{r.added ? 'Added to the case' : r.already ? 'Already in the case' : ''}
                    {:else if !Object.keys(r.jobs || {}).length}Saved{/if}
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </div>
    {#if mode === 'collect' && jobIds.length}
      <p class="help" style="margin-top:8px">Jobs run in the background. Crawls of public pages run one at a time; jobs using your Telegram account run one at a time alongside them. This page updates on its own, or follow progress live on the Collection page.</p>
    {/if}
    <div class="row" style="margin-top:12px">
      <a class="btn primary" href={href('collect')}>Open Collection</a>
      <a class="btn" href={href('channels')}>Back to channels</a>
      <button onclick={reset}>Add more</button>
    </div>
  {/if}
</div>

<SrcConfirm open={confirmOpen} title={mode === 'add' ? 'Add channels to the case?' : 'Start collecting?'} {lines} busy={running}
            confirmLabel={mode === 'add' ? `Add ${plural(included.length, 'channel')}` : anyLogin ? 'Confirm and use my account' : 'Confirm and start'}
            onclose={() => (confirmOpen = false)} onconfirm={run}
            intro={mode === 'add' ? '' : `${plural(included.length, 'channel')} will be saved${targetCase != null ? ` and added to "${targetCaseName}"` : ''}. Exactly this will run:`}>
  {#if mode === 'collect' && estSeconds > 0}<p class="small muted">Rough time for the post crawls: about {fmtDur(estSeconds)} (about 1.5 s per page, one crawl at a time).</p>{/if}
  {#if mode === 'collect' && included.some((e) => e.steps.watch.on || e.steps.comment_schedule.on)}
    <p class="small muted">Watches and schedules keep running in the background while this app is open. You can pause or delete them on the Collection page.</p>
  {/if}
</SrcConfirm>

<style>
  .steps-nav { list-style: none; display: flex; gap: 6px; margin: 0; padding: 0; font-size: 12.5px; }
  .steps-nav li { padding: 3px 10px; border-radius: 999px; border: 1px solid var(--line); color: var(--faint); }
  .steps-nav li.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); font-weight: 600; }
  .steps-nav li.done { color: var(--ok); }
  .entry { padding: 10px 12px; }
  .entry.dim { opacity: .6; }
  .top { align-items: flex-start; flex-wrap: nowrap; }
  .grow { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .sub { margin-top: 10px; padding-left: 26px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .footbar { position: sticky; bottom: 10px; display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin-top: 14px; box-shadow: var(--shadow-lg); }
  select.inl { padding: 2px 4px; }
</style>
