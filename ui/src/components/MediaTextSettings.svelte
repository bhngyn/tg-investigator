<script>
  // Settings > OCR & transcription: read text from images/video frames (Apple Vision) and speech (Whisper) with local models.
  // GET /api/mediatext/status, GET+POST /api/mediatext/settings, POST /api/mediatext/run.
  // Never automatic: a run starts only from the button below, after a confirmation that says what will run.
  import { onMount, onDestroy } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { app, attempt, toast } from '../lib/store.svelte.js';
  import { num, dt, plural } from '../lib/fmt.js';
  import Loading from './Loading.svelte';

  const LANGS = [['ar-SA', 'Arabic'], ['ars-SA', 'Arabic (Gulf / Saudi)'], ['ru-RU', 'Russian'], ['uk-UA', 'Ukrainian'], ['en-US', 'English']];
  const HINTS = [['', 'Detect'], ['ar', 'Arabic'], ['ru', 'Russian'], ['uk', 'Ukrainian'], ['en', 'English']];

  let st = $state(null), statErr = $state('');
  let cfg = $state(null);           // editable copy of the settings
  let saved = $state('');           // JSON of what the server has
  let saving = $state(false);
  let timer;

  async function loadStatus() {
    try { st = await get('/api/mediatext/status'); statErr = ''; } catch (e) { statErr = e.message; }
  }
  async function loadSettings() {
    try { const s = await get('/api/mediatext/settings'); cfg = clone(s); saved = JSON.stringify(clone(s)); } catch (e) { statErr = e.message; }
  }
  const clone = (s) => ({
    languages: [...(s.languages || ['auto'])], interval_s: s.interval_s ?? 5,
    channel_ocr_languages: { ...(s.channel_ocr_languages || {}) }, whisper_hints: { ...(s.whisper_hints || {}) },
    auto_after_media_download: !!s.auto_after_media_download,
  });
  onMount(() => { loadStatus(); loadSettings(); timer = setInterval(() => { if (!document.hidden && st?.running) loadStatus(); }, 4000); });
  onDestroy(() => clearInterval(timer));
  // a finished job refreshes the counts
  $effect(() => { app.jobsTick; loadStatus(); });

  const auto = $derived(cfg?.languages?.includes('auto'));
  const dirty = $derived(!!cfg && JSON.stringify(cfg) !== saved);
  function toggleLang(code, on) {
    const cur = cfg.languages.filter((x) => x !== 'auto');
    cfg.languages = on ? [...cur, code] : cur.filter((x) => x !== code);
    if (!cfg.languages.length) cfg.languages = ['auto'];
  }
  // per-channel rows
  let newCh = $state('');
  const chRows = $derived([...new Set([...Object.keys(cfg?.whisper_hints || {}), ...Object.keys(cfg?.channel_ocr_languages || {})])].sort());
  function addChannel() {
    const c = newCh.trim().toLowerCase().replace(/^@/, '').replace(/^https?:\/\/t\.me\//, '');
    if (!c) return;
    if (!(c in cfg.whisper_hints) && !(c in cfg.channel_ocr_languages)) cfg.whisper_hints[c] = '';
    newCh = '';
  }
  function hintOf(c) { return cfg.whisper_hints[c] || ''; }
  function setHint(c, v) { if (v) cfg.whisper_hints[c] = v; else delete cfg.whisper_hints[c]; if (!(c in cfg.whisper_hints) && !(c in cfg.channel_ocr_languages)) cfg.whisper_hints[c] = ''; }
  function removeChannel(c) { delete cfg.whisper_hints[c]; delete cfg.channel_ocr_languages[c]; cfg = { ...cfg }; }

  async function save() {
    saving = true;
    const body = { languages: cfg.languages, interval_s: Number(cfg.interval_s), channel_ocr_languages: cfg.channel_ocr_languages,
      whisper_hints: Object.fromEntries(Object.entries(cfg.whisper_hints).filter(([, v]) => v)) };
    const r = await attempt(() => post('/api/mediatext/settings', body), 'Settings saved');
    saving = false;
    if (r) { cfg = clone(r); saved = JSON.stringify(clone(r)); loadStatus(); }
  }
  async function autoOff() {
    const r = await attempt(() => post('/api/mediatext/settings', { auto_after_media_download: false }), 'Automatic reading turned off');
    if (r) { cfg = clone(r); saved = JSON.stringify(clone(r)); }
  }

  // ---- run ------------------------------------------------------------------------------------------
  let kind = $state('both');
  let scopeKind = $state('all_missing');
  let scopeVal = $state('');
  let redo = $state(false);
  let only = $state('');            // '' = posts and comment sections, else 'posts' | 'comments'
  let runBusy = $state(false);
  let runMsg = $state('');
  const need = $derived(st ? { ocr: st.ocr?.missing ?? 0, speech: st.speech?.missing ?? 0 } : { ocr: 0, speech: 0 });
  // Nothing to do: the whole-archive scope with 0 files unread (per selected kind) and no "read again".
  const nothingToDo = $derived(scopeKind === 'all_missing' && !redo && (kind === 'speech' ? 0 : need.ocr) + (kind === 'ocr' ? 0 : need.speech) === 0);
  const scopeOk = $derived(scopeKind === 'all_missing' || (scopeKind === 'case' ? !!scopeVal : !!scopeVal.trim()));
  const fromText = $derived(only === 'posts' ? ' in posts' : only === 'comments' ? ' in comment sections' : ' in posts and comment sections');
  const scopeText = $derived((scopeKind === 'all_missing' ? 'every collected image, video and audio file that has not been read yet' : scopeKind === 'case' ? `files in the case “${app.cases.find((c) => String(c.id) === String(scopeVal))?.name || scopeVal}”` : `files from @${scopeVal.trim().replace(/^@/, '')}`) + fromText);
  async function run() {
    const what = kind === 'both' ? 'OCR (text in images and video frames) and speech transcription' : kind === 'ocr' ? 'OCR (text in images and video frames)' : 'speech transcription';
    const counts = scopeKind === 'all_missing' ? `\n\nRoughly ${num(kind === 'speech' ? 0 : need.ocr)} files need OCR and ${num(kind === 'ocr' ? 0 : need.speech)} need transcription.` : '';
    if (!confirm(`Run ${what} on ${scopeText}?${redo ? '\n\nFiles that already have a result will be read again.' : ''}${counts}\n\nThis runs local models on this Mac (Apple Vision for OCR, Whisper ${st?.model ? '(' + st.model.split('/').pop() + ') ' : ''}for speech). It can use several GB of memory and keep the computer busy for a long time; nothing is sent over the network and the original files are not changed. The results are derived data, recorded in the audit log.\n\nStart it now?`)) return;
    runBusy = true; runMsg = '';
    const scope = { ...(scopeKind === 'all_missing' ? { all_missing: true } : scopeKind === 'case' ? { case: Number(scopeVal) } : { channel: scopeVal.trim().replace(/^@/, '') }), ...(only ? { only } : {}) };
    const r = await attempt(() => post('/api/mediatext/run', { kind, scope, ...(redo ? { redo: true } : {}) }));
    runBusy = false;
    if (r === undefined) return;
    runMsg = r.id ? `Queued as job ${r.id}: ${num(r.queued?.ocr || 0)} files for OCR, ${num(r.queued?.speech || 0)} for speech.` : r.note || 'Nothing to do.';
    toast(runMsg, r.id ? 'ok' : 'info', 6000);
    loadStatus();
  }
  const pct = (k) => { const x = st?.[k]; return x?.eligible ? Math.round((x.done / x.eligible) * 100) : 0; };
</script>

<div class="callout warn small" style="margin-bottom:12px">
  Text read from media is <b>machine output</b>: it can be wrong, especially for Arabic dialect, handwriting and noisy audio. Whisper (speech) can also <b>invent plausible sentences over music, chanting, silence or noise</b>. It is a lead for search, not a transcript of record: check it against the original audio or image before relying on or quoting it.
  Reading runs local models on this Mac and never starts by itself; press <b>Run</b> when you are ready.
</div>

{#if statErr && !st}<div class="callout bad" role="alert">{statErr} <button class="sm" onclick={() => { loadStatus(); loadSettings(); }}>Retry</button></div>
{:else if !st || !cfg}<Loading label="Loading…" />
{:else}
  <div class="grid two" style="align-items:start">
    <section class="panel">
      <div class="panel-head"><h2>Status</h2><button class="sm" onclick={loadStatus}>Refresh</button></div>
      <div class="panel-body col">
        <div class="row wrap">
          <span class="pill {st.worker_ready ? 'ok' : 'warn'}">{st.worker_ready ? 'OCR/speech worker installed' : 'worker not installed'}</span>
          <span class="pill {st.model_available ? 'ok' : 'warn'}" title={st.model}>{st.model_available ? 'Whisper model present' : 'Whisper model missing'}</span>
          {#if !st.job_supported}<span class="pill bad">job runner lacks the media-text job</span>{/if}
        </div>
        {#if !st.worker_ready}<div class="small muted">Create the worker environment first (see <code>tools/models/README.md</code>). The app never installs it or downloads models by itself.</div>{/if}
        {#if !st.model_available}<div class="small muted">Speech transcription needs the Whisper model in the local Hugging Face cache; it is never downloaded automatically.</div>{/if}
        <div class="stats">
          {#each [['ocr', 'Images & video frames (OCR)'], ['speech', 'Audio & video (speech)']] as [k, l]}
            <div class="stat"><div class="v">{num(st[k]?.done)} <span class="faint small">/ {num(st[k]?.eligible)}</span></div>
              <div class="l">{l}: {num(st[k]?.missing)} not read yet</div>
              {#if st[k]?.comments?.eligible}<div class="tiny faint">{num(st[k].comments.done)} / {num(st[k].comments.eligible)} of these are in comment sections</div>{/if}
              <div class="meter" aria-hidden="true"><i style:width="{pct(k)}%"></i></div></div>
          {/each}
          <div class="stat"><div class="v" style:color={st.errors ? 'var(--bad)' : ''}>{num(st.errors)}</div><div class="l">files that failed</div></div>
        </div>
        {#if st.running}
          <div class="callout">
            <b>Running: job {st.running.id}</b> ({st.running.status}){#if st.running.progress?.phase} · {st.running.progress.phase}{/if}
            {#if st.running.progress?.detail}<div class="small">{st.running.progress.detail}</div>{/if}
            {#if st.running.progress?.totals}<div class="tiny faint mono">{JSON.stringify(st.running.progress.totals)}</div>{/if}
            {#if st.running.progress?.peak_rss_gb}<div class="tiny faint">peak memory {st.running.progress.peak_rss_gb} GB</div>{/if}
            <div class="tiny faint">Started {dt(st.running.started || st.running.created)}. Progress is also on the Collection page.</div>
          </div>
        {/if}
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>Run</h2></div>
      <div class="panel-body col">
        <div class="form-grid">
          <label class="field">What to read
            <select bind:value={kind}><option value="both">OCR and speech</option><option value="ocr">OCR only (images, video frames)</option><option value="speech">Speech only (audio, video)</option></select></label>
          <label class="field">Which files
            <select bind:value={scopeKind}><option value="all_missing">Everything not read yet</option><option value="case">One case</option><option value="channel">One channel</option></select></label>
        </div>
        <label class="field">Where
          <select bind:value={only}><option value="">Posts and comment sections</option><option value="posts">Channel posts only</option><option value="comments">Comment sections only</option></select></label>
        {#if scopeKind === 'case'}
          <label class="field">Case<select bind:value={scopeVal}><option value="">Choose…</option>{#each app.cases as c (c.id)}<option value={String(c.id)}>{c.name}</option>{/each}</select></label>
        {:else if scopeKind === 'channel'}
          <label class="field">Channel<input type="text" bind:value={scopeVal} placeholder="@channel" /></label>
        {/if}
        <label class="check"><input type="checkbox" bind:checked={redo} /> Read again files whose earlier result used different settings (the old output is kept)</label>
        <div class="row">
          <button class="primary" onclick={run} disabled={runBusy || !scopeOk || !st.worker_ready || !!st.running || nothingToDo}>{runBusy ? 'Queuing…' : st.running ? 'A run is in progress' : nothingToDo ? 'Nothing to read' : 'Run…'}</button>
          <span class="tiny faint">{nothingToDo ? 'Every file has been read. Tick “read again” to redo them, or collect more media first.' : 'You will be asked to confirm.'}</span>
        </div>
        {#if runMsg}<div class="small">{runMsg}</div>{/if}
      </div>
    </section>
  </div>

  <section class="panel" style="margin-top:12px">
    <div class="panel-head"><h2>Languages and timing</h2>{#if dirty}<span class="pill warn">unsaved</span>{/if}<span class="spacer"></span>
      <button class="primary sm" onclick={save} disabled={saving || !dirty}>{saving ? 'Saving…' : 'Save'}</button></div>
    <div class="panel-body col" style="gap:14px">
      <div>
        <div class="muted small" style="font-weight:550">OCR languages</div>
        <label class="check"><input type="checkbox" checked={auto} onchange={(e) => (cfg.languages = e.currentTarget.checked ? ['auto'] : ['ar-SA'])} /> Detect automatically <span class="faint">(reads Arabic, Cyrillic and Latin in the same picture; recommended)</span></label>
        {#if !auto}
          <div class="row wrap" style="margin-top:4px">
            {#each LANGS as [code, l]}<label class="check"><input type="checkbox" checked={cfg.languages.includes(code)} onchange={(e) => toggleLang(code, e.currentTarget.checked)} /> {l} <span class="faint tiny">{code}</span></label>{/each}
          </div>
          <div class="help">With an explicit list, mostly the first language drives recognition. Prefer automatic detection for mixed Arabic/Russian material.</div>
        {/if}
      </div>
      <label class="field" style="max-width:280px">Video: one frame every (seconds)
        <input type="number" min="1" max="60" step="1" bind:value={cfg.interval_s} />
        <span class="help">Shorter finds more on-screen text but takes longer (1–60 s).</span></label>

      <div>
        <div class="muted small" style="font-weight:550">Per-channel language hints</div>
        <div class="help" style="margin-bottom:6px">Tell Whisper which language a channel’s speech is in, instead of guessing from the first seconds. Empty means detect.</div>
        {#if chRows.length}
          <div class="table-wrap"><table class="t">
            <thead><tr><th scope="col">Channel</th><th scope="col">Speech language</th><th scope="col">OCR languages</th><th scope="col"></th></tr></thead>
            <tbody>{#each chRows as c (c)}
              <tr><td><bdi dir="ltr">@{c}</bdi></td>
                <td><select value={hintOf(c)} onchange={(e) => setHint(c, e.currentTarget.value)} aria-label="Speech language for {c}">{#each HINTS as [k, l]}<option value={k}>{l}</option>{/each}</select></td>
                <td class="small">{(cfg.channel_ocr_languages[c] || []).join(', ') || 'default'}</td>
                <td class="right"><button class="sm ghost" onclick={() => removeChannel(c)} aria-label="Remove {c}">✕</button></td></tr>
            {/each}</tbody></table></div>
        {/if}
        <form class="row" onsubmit={(e) => { e.preventDefault(); addChannel(); }} style="margin-top:6px">
          <input type="text" bind:value={newCh} placeholder="@channel" aria-label="Channel to add" style="width:min(100%,200px)" /><button type="submit" class="sm" disabled={!newCh.trim()}>Add channel</button>
        </form>
      </div>

      {#if cfg.auto_after_media_download}
        <div class="callout bad small">Automatic reading after media downloads is <b>on</b> in this workspace. It starts local models without asking. <button class="sm" onclick={autoOff}>Turn it off</button></div>
      {:else}
        <div class="tiny faint">Automatic reading after media downloads is off. This screen cannot turn it on.</div>
      {/if}
    </div>
  </section>
{/if}

<style>
  .wrap { flex-wrap: wrap; }
  .meter { height: 5px; background: var(--panel-2); border-radius: 3px; margin-top: 4px; overflow: hidden; }
  .meter i { display: block; height: 100%; background: var(--accent); }
  .mono { font-family: var(--mono); overflow-wrap: anywhere; }
</style>
