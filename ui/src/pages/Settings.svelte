<script>
  // Global settings (no case scope): preferences, codebook, integrity, import, Telegram login, audit log.
  import { onMount, untrack } from 'svelte';
  import { app, loadSettings, loadTags, toast } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { route, setQuery } from '../lib/router.svelte.js';
  import { prefs, setShortcuts } from '../lib/prefs.svelte.js';
  import { num, dt, pj } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';
  import Modal from '../components/Modal.svelte';
  import DictSettings from '../components/DictSettings.svelte';
  import MediaTextSettings from '../components/MediaTextSettings.svelte';

  const TABS = [['general', 'General'], ['codebook', 'Tags (codebook)'], ['data', 'Data and integrity'], ['dictionary', 'Dictionary'], ['mediatext', 'OCR & transcription'], ['telegram', 'Telegram'], ['audit', 'Audit log']];
  const tab = $derived(TABS.some(([k]) => k === route.query.tab) ? route.query.tab : 'general');
  const s = $derived(app.settings);

  // ---------- general ----------
  async function save(patch, msg = 'Saved') {
    try {
      const r = await post('/api/settings', patch);
      app.settings = r;
      toast(msg, 'ok', 1800);
    } catch (e) { toast(e.message, 'error', 7000); loadSettings(); }
  }
  const setBool = (key, on) => save({ [key]: on ? '1' : '0' });
  function setNumber(key, e, min, max) {
    let v = parseInt(e.currentTarget.value, 10);
    if (Number.isNaN(v)) { e.currentTarget.value = s[key]; return; }
    v = Math.min(max, Math.max(min, v));
    e.currentTarget.value = v;
    save({ [key]: String(v) });
  }
  const BLUR = [
    ['all', 'Blur all media', 'Every photo and video stays blurred until you reveal it. Safest default.'],
    ['sensitive', 'Blur only flagged media', 'Only media Telegram marked sensitive is blurred, plus any media that has no such flag to check (comments, files linked to incidents, similar-file previews), which stays blurred. Unflagged violent posts will show.'],
    ['none', 'Never blur', 'All media is shown immediately. Not recommended for long sessions.'],
  ];

  // ---------- codebook ----------
  const blankTag = () => ({ id: null, name: '', category: 'Evidence', color: '#475569', description: '', key: '' });
  let te = $state({ open: false, t: blankTag(), busy: false, error: '' });
  const categories = $derived([...new Set(app.tags.map((t) => t.category))].sort());
  const byCat = $derived(categories.map((c) => ({ c, tags: app.tags.filter((t) => t.category === c) })));
  const keyClash = $derived(te.t.key && app.tags.find((t) => t.key === te.t.key && t.id !== te.t.id));
  function newTag() { te = { open: true, t: blankTag(), busy: false, error: '' }; }
  function editTag(t) { te = { open: true, t: { id: t.id, name: t.name, category: t.category, color: t.color || '#475569', description: t.description || '', key: t.key || '' }, busy: false, error: '' }; }
  async function saveTag() {
    te.busy = true; te.error = '';
    try {
      const t = te.t;
      app.tags = await post('/api/tags', { ...(t.id ? { id: t.id } : {}), name: t.name.trim(), category: t.category.trim() || 'Other', color: t.color, description: t.description.trim(), key: t.key.trim().slice(0, 1) });
      te.open = false;
      toast(t.id ? 'Tag updated' : 'Tag created', 'ok');
    } catch (e) { te.error = e.message; }
    te.busy = false;
  }
  async function deleteTag(t) {
    const warn = t.uses ? `\n\nIt is currently applied to ${num(t.uses)} post${t.uses === 1 ? '' : 's'} (global triage); those tags will be removed. Case tags using it are affected too.` : '';
    if (!confirm(`Delete the tag "${t.name}"?${warn}\n\nThis cannot be undone (the deletion is recorded in the audit log). Consider renaming instead.`)) return;
    try { app.tags = await post('/api/tags', { id: t.id, delete: true }); toast('Tag deleted', 'ok'); }
    catch (e) { toast(e.message, 'error', 7000); loadTags(); }
  }

  // ---------- integrity ----------
  let integ = $state(null);
  let integBusy = $state(false);
  async function runIntegrity() {
    if (!confirm('Re-hash every stored media file, raw page and evidence package and compare with the recorded SHA-256?\n\nThis can take several minutes on a large archive and keeps the disk busy. Nothing is changed.')) return;
    integBusy = true; integ = null;
    try { integ = await post('/api/integrity'); }
    catch (e) { toast(e.message, 'error', 8000); }
    integBusy = false;
  }

  // ---------- import ----------
  let imp = $state({ path: '', networkOnly: false, busy: false, result: null, error: '' });
  async function runImport() {
    imp.busy = true; imp.error = ''; imp.result = null;
    try {
      imp.result = await post('/api/import', { path: imp.path.trim(), network_only: imp.networkOnly });
      toast('Import finished', 'ok');
    } catch (e) { imp.error = e.message; }
    imp.busy = false;
  }

  // ---------- telegram ----------
  let tgBusy = $state(false);
  async function recheckTg() {
    tgBusy = true;
    try { app.telegram = await get('/api/telegram', { force: 1 }); } catch (e) { toast(e.message, 'error'); }
    tgBusy = false;
  }

  // ---------- audit ----------
  let audit = $state([]);
  let auditLoaded = $state(false);
  let auditLimit = $state(200);
  let auditQ = $state('');
  let auditErr = $state('');
  async function loadAudit() {
    auditErr = '';
    try { audit = await get('/api/audit', { limit: auditLimit }); } catch (e) { auditErr = e.message; }
    auditLoaded = true;
  }
  $effect(() => { if (tab === 'audit') { auditLimit; untrack(loadAudit); } });
  const auditRows = $derived.by(() => {
    const q = auditQ.trim().toLowerCase();
    return q ? audit.filter((a) => `${a.actor} ${a.action} ${a.target || ''} ${a.detail || ''}`.toLowerCase().includes(q)) : audit;
  });
  function short(d) {
    const v = pj(d, null);
    const t = v && typeof v === 'object' ? JSON.stringify(v) : (d || '');
    return t.length > 160 ? t.slice(0, 160) + '…' : t;
  }

  onMount(() => { loadSettings(); loadTags(); if (!app.telegram) get('/api/telegram').then((t) => (app.telegram = t)).catch(() => {}); });
</script>

<div class="page">
  <div class="page-head"><div><h1>Settings</h1><p>These apply to the whole workspace, not to one case.</p></div>
    <a class="btn" href="/classic" title="Opens the previous interface">Open the classic UI</a></div>

  <div class="tabs" role="group" aria-label="Settings sections">
    {#each TABS as [k, l]}<button class:on={tab === k} aria-pressed={tab === k} onclick={() => setQuery({ tab: k === 'general' ? null : k })}>{l}</button>{/each}
  </div>

  {#if tab === 'general'}
    <div class="grid two" style="align-items:start">
      <section class="panel">
        <div class="panel-head"><h2>Protecting yourself</h2></div>
        <div class="panel-body col" style="gap:12px">
          <div class="callout warn">
            Repeated exposure to graphic material harms investigators, even when it feels manageable. Keep media blurred by default, reveal only what you need, and take breaks.
            <b>Reveal clicks today: {num(s.exposure_today || 0)}</b> (counts each time you reveal a blurred item; media shown unblurred by your setting is not counted).
          </div>
          <fieldset><legend class="muted small">Blur media</legend>
            {#each BLUR as [v, l, h]}
              <label class="opt"><input type="radio" name="blur" value={v} checked={s.blur === v} onchange={() => save({ blur: v })} />
                <span><b>{l}</b><div class="help">{h}</div></span></label>
            {/each}
          </fieldset>
          <label class="check"><input type="checkbox" checked={s.grayscale === '1'} onchange={(e) => setBool('grayscale', e.currentTarget.checked)} /> Show revealed media in grayscale <span class="faint">(reduces emotional impact)</span></label>
          <label class="check"><input type="checkbox" checked={prefs.shortcuts} onchange={(e) => setShortcuts(e.currentTarget.checked)} /> Keyboard shortcuts <span class="faint">(single keys such as <kbd>/</kbd> <kbd>g</kbd> <kbd>j</kbd> <kbd>k</kbd> <kbd>r</kbd> <kbd>v</kbd> <kbd>i</kbd> <kbd>u</kbd> <kbd>n</kbd> <kbd>o</kbd> <kbd>t</kbd> <kbd>x</kbd>. Turn off if they fire by accident, e.g. with voice control. Kept in this browser only.)</span></label>
          <label class="check"><input type="checkbox" checked={s.autoplay_muted === '1'} onchange={(e) => setBool('autoplay_muted', e.currentTarget.checked)} /> Autoplay revealed videos, muted</label>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Workspace</h2></div>
        <div class="panel-body col" style="gap:12px">
          <label class="field">Investigator name <span class="faint">(recorded in the audit log with every action)</span>
            <input type="text" value={s.investigator || ''} onchange={(e) => e.currentTarget.value.trim() ? save({ investigator: e.currentTarget.value.trim() }) : (e.currentTarget.value = s.investigator)} /></label>
          <div class="form-grid">
            <label class="field">Results per page<input type="number" min="5" max="200" value={s.page_size} onchange={(e) => setNumber('page_size', e, 5, 200)} /></label>
            <label class="field">Re-check window (days)<input type="number" min="1" max="365" value={s.recheck_days} onchange={(e) => setNumber('recheck_days', e, 1, 365)} />
              <span class="help">Comment polls re-check messages this recent for edits and deletions.</span></label>
          </div>
          <label class="check"><input type="checkbox" checked={s.auto_mediascan === '1'} onchange={(e) => setBool('auto_mediascan', e.currentTarget.checked)} /> Scan new media automatically <span class="faint">(local perceptual hashes and metadata, no network)</span></label>
          <label class="check"><input type="checkbox" checked={s.collect_phones === '1'} onchange={(e) => setBool('collect_phones', e.currentTarget.checked)} /> Record commenters’ phone numbers where Telegram shows them</label>
          <div class="field"><span class="muted small" style="font-weight:550">Workspace folder</span>
            <code class="path">{s.workspace || '…'}</code>
            <span class="help">The database, downloaded media, logs and evidence packages all live here. Back it up as one folder.</span></div>
        </div>
      </section>
    </div>

  {:else if tab === 'codebook'}
    <div class="row" style="margin-bottom:10px">
      <p class="muted small" style="margin:0;max-width:70ch">Tags label evidence (violation type, evidence quality, sensitivity). A tag with a key can be applied with that key while reviewing. The same codebook is used in every case.</p>
      <span class="spacer"></span><button class="primary" onclick={newTag}>＋ New tag</button>
    </div>
    {#if !app.tags.length}<Loading label="Loading tags…" />{:else}
      {#each byCat as g (g.c)}
        <div class="section-title">{g.c}</div>
        <div class="table-wrap"><table class="t">
          <thead><tr><th scope="col" style="width:36%">Tag</th><th scope="col">Description</th><th scope="col">Key</th><th scope="col" class="right">Posts</th><th scope="col"></th></tr></thead>
          <tbody>{#each g.tags as t (t.id)}
            <tr><td><span class="sw" style:background={t.color}></span> <b>{t.name}</b></td>
              <td class="muted small">{t.description}</td>
              <td>{#if t.key}<kbd>{t.key}</kbd>{:else}<span class="faint">–</span>{/if}</td>
              <td class="right num">{num(t.uses)}</td>
              <td class="right nowrap"><button class="sm" onclick={() => editTag(t)}>Edit</button> <button class="sm danger" onclick={() => deleteTag(t)}>Delete</button></td></tr>
          {/each}</tbody></table></div>
      {/each}
    {/if}

  {:else if tab === 'data'}
    <div class="grid two" style="align-items:start">
      <section class="panel">
        <div class="panel-head"><h2>Verify all stored files</h2></div>
        <div class="panel-body col">
          <p class="muted small" style="margin:0">Re-computes the SHA-256 of every media file, raw page capture and evidence package and compares it with the hash recorded when it was collected. Run it before building evidence for a court, and after moving or restoring the workspace. Nothing is modified.</p>
          <div class="row"><button onclick={runIntegrity} disabled={integBusy}>{integBusy ? 'Verifying… this can take minutes' : 'Run integrity check'}</button>{#if integBusy}<span class="spinner"></span>{/if}</div>
          {#if integ}
            <div class="callout {integ.ok ? 'ok' : 'bad'}">
              <b>{integ.ok ? 'All files match their recorded hashes.' : 'Problems found.'}</b>
              <div class="small">Checked {num(integ.checked_media)} media files, {num(integ.checked_pages)} raw captures and {num(integ.checked_exports)} evidence packages · {dt(integ.at)}</div>
              {#if integ.mismatched.length}<div class="small"><b>{integ.mismatched.length} changed since collection</b> (do not rely on these as evidence):<ul class="paths">{#each integ.mismatched.slice(0, 50) as p}<li>{p}</li>{/each}</ul></div>{/if}
              {#if integ.missing.length}<div class="small"><b>{integ.missing.length} missing from disk:</b><ul class="paths">{#each integ.missing.slice(0, 50) as p}<li>{p}</li>{/each}</ul></div>{/if}
              {#if integ.mismatched.length > 50 || integ.missing.length > 50}<div class="tiny">First 50 of each shown. The full result is in the audit log.</div>{/if}
            </div>
          {/if}
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Import a run folder</h2></div>
        <form class="panel-body col" onsubmit={(e) => { e.preventDefault(); if (imp.path.trim()) runImport(); }}>
          <p class="muted small" style="margin:0">Bring in the output of the command-line collectors (a tgspider folder with <code>nodes.jsonl</code>, or a tgcomments folder with <code>group.json</code>). Type the folder’s path on this computer.</p>
          <label class="field">Folder path<input type="text" bind:value={imp.path} placeholder="/Users/you/tg/runs/2026-09-20" /></label>
          <label class="check"><input type="checkbox" bind:checked={imp.networkOnly} /> Import the channel network only (skip posts)</label>
          {#if imp.error}<div class="callout bad" role="alert">{imp.error}</div>{/if}
          <div class="row"><button class="primary" type="submit" disabled={imp.busy || !imp.path.trim()}>{imp.busy ? 'Importing…' : 'Import'}</button></div>
          {#if imp.result}
            <div class="callout ok"><b>Imported.</b>
              <dl class="kv">{#each Object.entries(imp.result) as [k, v]}<dt>{k.replaceAll('_', ' ')}</dt><dd>{typeof v === 'object' ? JSON.stringify(v) : num(v)}</dd>{/each}</dl></div>
          {/if}
        </form>
      </section>
    </div>

  {:else if tab === 'dictionary'}
    <DictSettings />

  {:else if tab === 'mediatext'}
    <MediaTextSettings />

  {:else if tab === 'telegram'}
    <section class="panel" style="max-width:720px">
      <div class="panel-head"><h2>Telegram login</h2><button class="sm" onclick={recheckTg} disabled={tgBusy}>{tgBusy ? 'Checking…' : 'Re-check'}</button></div>
      <div class="panel-body col">
        {#if !app.telegram}<Loading label="Checking…" />
        {:else}
          <div class="callout {app.telegram.ok ? 'ok' : 'warn'}"><b>{app.telegram.ok ? 'Telegram session on this Mac' : 'No Telegram session on this Mac'}</b><div class="small">{app.telegram.message}</div>
            <div class="tiny muted" style="margin-top:4px">The login is stored once per Mac user account and is shared by every workspace you open here. It does not belong to this workspace.</div></div>
        {/if}
        <p class="muted small" style="margin:0">Post collection needs no login. Reading <b>comments</b>, downloading some media and discovery use a Telegram account. Logging in is done by you, in a terminal, so the app never sees your phone number, code or password. From the project folder run:</p>
        <pre class="cmd">.venv/bin/python tgmedia.py login</pre>
        <p class="help">Then press “Re-check”. Consider using a dedicated account for investigations; collection from it is recorded in every capture’s log.</p>
      </div>
    </section>

  {:else if tab === 'audit'}
    <div class="row" style="margin-bottom:10px">
      <input type="search" bind:value={auditQ} aria-label="Filter the audit log" placeholder="Filter by action, target, person or detail…" style="width:min(100%,320px)" />
      <select bind:value={auditLimit} aria-label="How many entries"><option value={100}>Latest 100</option><option value={200}>Latest 200</option><option value={500}>Latest 500</option><option value={2000}>Latest 2000</option></select>
      <button class="sm" onclick={loadAudit}>Refresh</button>
      <span class="spacer"></span><span class="muted small">{num(auditRows.length)} of {num(audit.length)} entries · append-only record of every action</span>
    </div>
    {#if !auditLoaded}<Loading />
    {:else if auditErr}<div class="callout bad" role="alert">{auditErr}</div>
    {:else if !auditRows.length}<p class="muted">No entries match.</p>
    {:else}
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">When (UTC)</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Detail</th></tr></thead>
        <tbody>{#each auditRows as a (a.id)}
          <tr><td class="nowrap">{dt(a.ts)}</td><td>{a.actor}</td><td><code>{a.action}</code></td><td class="small">{a.target || ''}</td><td class="small muted dtl">{short(a.detail)}</td></tr>
        {/each}</tbody></table></div>
    {/if}
  {/if}
  <p class="help about">Open-source components bundled in this interface (Svelte, d3-force and their dependencies) are used under the MIT and ISC licences. <a href="/app/THIRD-PARTY-LICENSES.txt" target="_blank" rel="noreferrer noopener">Licence notices</a> · data credits for the dictionary are on the Dictionary tab.</p>
</div>

<Modal open={te.open} onclose={() => (te.open = false)} title={te.t.id ? 'Edit tag' : 'New tag'} width="480px">
  <form id="tagform" class="col" style="gap:12px" onsubmit={(e) => { e.preventDefault(); saveTag(); }}>
    <label class="field">Name<input type="text" bind:value={te.t.name} /></label>
    <div class="form-grid">
      <label class="field">Category<input type="text" list="ev-cats" bind:value={te.t.category} /><datalist id="ev-cats">{#each categories as c}<option value={c}></option>{/each}</datalist></label>
      <label class="field">Colour<input type="color" bind:value={te.t.color} style="height:32px;padding:2px" /></label>
      <label class="field">Shortcut key<input type="text" maxlength="1" bind:value={te.t.key} style="width:60px" />
        {#if keyClash}<span class="help" style="color:var(--warn)">Also used by “{keyClash.name}”.</span>{/if}</label>
    </div>
    <label class="field">Description <span class="faint">(when to apply it)</span><textarea bind:value={te.t.description} rows="2"></textarea></label>
    {#if te.error}<div class="callout bad" role="alert">{te.error}</div>{/if}
  </form>
  {#snippet footer()}
    <button onclick={() => (te.open = false)}>Cancel</button>
    <button class="primary" type="submit" form="tagform" disabled={te.busy || !te.t.name.trim()}>{te.busy ? 'Saving…' : 'Save tag'}</button>
  {/snippet}
</Modal>

<style>
  .about { margin-top: 24px; max-width: 80ch; }
  fieldset { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
  legend { padding: 0; margin-bottom: 4px; font-weight: 550; }
  .opt { display: flex; gap: 8px; align-items: flex-start; cursor: pointer; padding: 6px 8px; border: 1px solid var(--line); border-radius: var(--radius-sm); }
  .opt:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
  .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -1px; }
  .path, code { font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
  .path { background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 5px 8px; }
  .cmd { background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; font-family: var(--mono); font-size: 12.5px; margin: 0; overflow: auto; user-select: all; }
  kbd { font-family: var(--mono); font-size: 12px; background: var(--panel-2); border: 1px solid var(--line-strong); border-radius: 3px; padding: 0 5px; }
  .paths { margin: 4px 0; padding-left: 18px; font-family: var(--mono); font-size: 11.5px; max-height: 160px; overflow: auto; overflow-wrap: anywhere; }
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px; margin: 6px 0 0; font-size: 12.5px; }
  .kv dt { color: var(--muted); } .kv dd { margin: 0; }
  .dtl { max-width: 520px; overflow-wrap: anywhere; }
</style>
