<script>
  // Settings > Dictionary: what the multilingual search knows, licences, a query tester, and an editor.
  // Edits never touch the built dictionary; they are recorded in the workspace (and the audit log) and can be undone.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, setQuery } from '../lib/router.svelte.js';
  import { attempt } from '../lib/store.svelte.js';
  import { num, dt } from '../lib/fmt.js';
  import { normaliseExpansion } from '../lib/expansion.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import Drawer from './Drawer.svelte';
  import AddDictEntry from './AddDictEntry.svelte';
  import ExpansionChips from './ExpansionChips.svelte';

  const LANGS = [['', 'Detect from script'], ['en', 'English'], ['ar', 'Arabic'], ['ru', 'Russian'], ['uk', 'Ukrainian'], ['latn', 'Other Latin spelling']];
  const langLabel = (l) => LANGS.find(([k]) => k && k === l)?.[1] || l;

  // ---------- stats ----------
  let stats = $state(null);
  let statsErr = $state('');
  async function loadStats() {
    try { stats = await get('/api/dictionary/stats'); statsErr = ''; } catch (e) { statsErr = e.message; }
  }
  loadStats();
  // deep link from "Terms in these results": #/settings?tab=dictionary&entry=<id>
  let deepDone = '';
  $effect(() => {
    const id = route.query.entry;
    if (id && id !== deepDone) { deepDone = id; untrack(() => openEntry(id)); }
  });
  // counts arrive as nested objects (by country / kind / lang); show numbers as tiles and objects as small tables
  const countSrc = $derived({ ...(stats?.counts || {}), ...Object.fromEntries(['entries', 'names', 'by_country', 'by_kind', 'by_lang'].filter((k) => stats?.[k] != null).map((k) => [k, stats[k]])) });
  const countTiles = $derived(Object.entries(countSrc).filter(([, v]) => typeof v === 'number'));
  const countTables = $derived(Object.entries(countSrc).filter(([, v]) => v && typeof v === 'object'));
  const flat = (o) => Object.entries(o || {}).map(([k, v]) => [k, typeof v === 'object' && v ? Object.entries(v).map(([a, b]) => `${a} ${num(b)}`).join(' · ') : num(v)]);
  // stats.coverage (new): flat counts such as sd_entries_with_arabic_name plus nested breakdowns. Parsed defensively:
  // unknown shapes are shown as plain rows instead of failing.
  const cov = $derived(stats?.coverage && typeof stats.coverage === 'object' ? stats.coverage : null);
  const byCountry = $derived(stats?.by_country || cov?.entries_by_country || {});
  const ofTotal = (n, total) => (n != null && total ? `${num(n)} of ${num(total)} (${Math.round((n / total) * 1000) / 10}%)` : n != null ? num(n) : '–');
  const coverageRows = $derived.by(() => {
    if (stats?.coverage == null) return [];
    if (!cov) return [['Sudan places with an Arabic name', String(stats.coverage)]];
    const sd = byCountry.SD ?? byCountry.sd, ua = byCountry.UA ?? byCountry.ua;
    const rows = [];
    if (cov.sd_entries_with_arabic_name != null) rows.push(['Sudan entries with an Arabic name', ofTotal(cov.sd_entries_with_arabic_name, sd)]);
    if (cov.ua_entries_with_ru_name != null) rows.push(['Ukraine entries with a Russian name', ofTotal(cov.ua_entries_with_ru_name, ua)]);
    if (cov.ua_entries_with_historic_name != null) rows.push(['Ukraine entries with a historic (pre-renaming) name', ofTotal(cov.ua_entries_with_historic_name, ua)]);
    if (cov.ua_entries_with_ru_and_historic_name != null) rows.push(['… with both Russian and historic names', ofTotal(cov.ua_entries_with_ru_and_historic_name, ua)]);
    // any other flat number the builder adds
    const known = new Set(['sd_entries_with_arabic_name', 'ua_entries_with_ru_name', 'ua_entries_with_historic_name', 'ua_entries_with_ru_and_historic_name', 'entries', 'names']);
    for (const [k, v] of Object.entries(cov)) if (typeof v === 'number' && !known.has(k)) rows.push([k.replaceAll('_', ' '), num(v)]);
    return rows;
  });
  const covDetail = $derived.by(() => Object.entries(cov || {}).filter(([k, v]) => v && typeof v === 'object' && k !== 'entries_by_country_kind_category')
    .map(([k, o]) => [k.replaceAll('_', ' '), flat(o)]));
  const sdKinds = $derived(Object.entries(cov?.entries_by_country_kind_category || {}).filter(([k]) => k.startsWith('SD/place/')).map(([k, n]) => [k.slice(9), n]).sort((a, b) => b[1] - a[1]).slice(0, 8));

  // ---------- tester ----------
  let testQ = $state('');
  let test = $state(null);
  let testBusy = $state(false);
  let testErr = $state('');
  async function runTest(e) {
    e?.preventDefault();
    if (!testQ.trim()) return;
    testBusy = true; testErr = '';
    try { test = normaliseExpansion(await get('/api/dictionary/lookup', { q: testQ.trim() })); }
    catch (er) { testErr = er.message; }
    testBusy = false;
  }

  // ---------- entries browser ----------
  let f = $state({ q: '', kind: '', country: '' });
  let entries = $state([]);
  let total = $state(0);
  let loaded = $state(false);
  let listBusy = $state(false);
  let listErr = $state('');
  const PAGE = 50;
  let lseq = 0;
  async function loadEntries(more = false) {
    const my = ++lseq;
    listBusy = true; listErr = '';
    try {
      const r = await get('/api/dictionary/entries', { q: f.q.trim(), kind: f.kind, country: f.country, limit: PAGE, offset: more ? entries.length : 0 });
      if (my !== lseq) return;
      const rows = r.entries || r.results || (Array.isArray(r) ? r : []);
      entries = more ? [...entries, ...rows] : rows;
      total = r.total ?? entries.length;
    } catch (e) { if (my === lseq) listErr = e.message; }
    if (my === lseq) { listBusy = false; loaded = true; }
  }
  // debounce typing; selects apply at once
  $effect(() => {
    const k = [f.q, f.kind, f.country].join('\u0001');
    // `loaded` is read untracked: loadEntries() sets it, and tracking it would fire this effect a second time.
    const t = setTimeout(() => untrack(() => loadEntries()), untrack(() => loaded) ? 300 : 0);
    return () => clearTimeout(t);
  });
  const visibleNames = (e) => (e.names || []).filter((n) => !n.hidden).slice(0, 4);

  // ---------- entry drawer ----------
  let dr = $state({ open: false, id: null, entry: null, err: '', busy: false });
  let newName = $state({ name: '', lang: '' });
  async function openEntry(id) {
    dr = { open: true, id, entry: null, err: '', busy: false };
    newName = { name: '', lang: '' };
    await refreshEntry();
  }
  // Closing the drawer also drops entry=... from the URL, so reload and Back do not reopen it.
  function closeEntry() {
    dr.open = false; deepDone = '';
    if (route.query.entry) setQuery({ entry: null });
  }
  async function refreshEntry() {
    try { dr.entry = await get(`/api/dictionary/entry/${encodeURIComponent(dr.id)}`); dr.err = ''; } catch (e) { dr.err = e.message; }
  }
  /** Run an edit, refresh what is on screen, toast the outcome. */
  async function edit(body, okMsg) {
    dr.busy = true;
    const r = await attempt(() => post('/api/dictionary/edit', body), okMsg);
    dr.busy = false;
    if (r === undefined) return false;
    await Promise.all([dr.open ? refreshEntry() : null, loadEntries(), loadStats(), editsOpen ? loadAllEdits() : null]);
    return true;
  }
  async function addName() {
    const name = newName.name.trim();
    if (!name) return;
    if (await edit({ action: 'add_name', entry_id: dr.id, name, ...(newName.lang ? { lang: newName.lang } : {}) }, `Added “${name}”`)) newName.name = '';
  }
  const hideName = (n) => edit({ action: 'hide_name', entry_id: dr.id, name: n.name }, `Hid “${n.name}” (undo from the history below)`);
  function hideEntry() {
    if (!confirm(`Hide the entry "${dr.entry?.label}"?\n\nSearches will stop expanding to any of its names. Nothing is deleted: the entry stays in the dictionary and this can be undone from its history. The edit is recorded in the audit log.`)) return;
    edit({ action: 'hide_entry', entry_id: dr.id }, 'Entry hidden');
  }
  const undo = (h) => edit({ action: 'undo', edit_id: h.id }, 'Edit undone');
  const ACTION = { add_name: 'Added name', hide_name: 'Hid name', add_entry: 'Added entry', hide_entry: 'Hid entry' };

  // ---------- all my edits ----------
  let allEdits = $state(null);
  let editsOpen = $state(false);
  async function loadAllEdits() {
    try { allEdits = await get('/api/dictionary/edits', { limit: 100 }); } catch (e) { allEdits = { error: e.message }; }
  }
  const editRows = $derived(Array.isArray(allEdits) ? allEdits : allEdits?.edits || []);

  // ---------- add term / place ----------
  let adOpen = $state(false);
  async function addedEntry(r) {
    await Promise.all([loadEntries(), loadStats()]);
    if (r?.edit?.entry_id) openEntry(r.edit.entry_id);
  }
</script>

<div class="callout small" style="margin-bottom:12px">
  <b>Content note.</b> The term list (lexicon) deliberately includes racist slurs, dehumanising terms and violent slang, so that you can find and document hate speech. It is a search vocabulary, not evidence and not a judgement about who uses a word. Entries you open here may be upsetting.
</div>
<div class="callout warn small" style="margin-bottom:12px">
  Places come from OpenStreetMap and GeoNames plus a curated list; coverage of small Sudanese villages in Arabic is incomplete — add spellings you meet.
  Automatic matches are a search aid, not a verified identification: confirm a place before relying on it.
</div>

{#if stats?.key_version_ok === false}
  <div class="callout bad" style="margin-bottom:12px">
    <b>Dictionary needs rebuilding.</b> Run <code>python -m investigator.gazetteer_build</code> in the project folder, then reload this page. Until then, spelling matches from the dictionary can miss names.
    {#if stats.key_version_message || stats.message}<div class="small">{stats.key_version_message || stats.message}</div>{/if}
  </div>
{/if}

{#if !stats && !statsErr}<Loading label="Loading dictionary…" />
{:else if statsErr}<div class="callout bad" role="alert">{statsErr} <button class="sm" onclick={loadStats}>Retry</button></div>
{:else}
  {#if !stats.available}
    <div class="callout warn">The place dictionary has not been built on this computer, so searches only get the Cyrillic-spelling matches. It is built once from files in <code>investigator/data/gazetteer/raw</code> with <code>python -m investigator.gazetteer_build</code>. Nothing is downloaded while the app runs.</div>
  {:else}
    <div class="grid two" style="align-items:start">
      <section class="panel">
        <div class="panel-head"><h2>Dictionary</h2></div>
        <div class="panel-body col">
          <div class="stats">
            {#each countTiles as [k, v]}<div class="stat"><div class="v">{num(v)}</div><div class="l">{k.replaceAll('_', ' ')}</div></div>{/each}
            {#if stats.edits != null}<div class="stat"><div class="v">{num(typeof stats.edits === 'object' ? stats.edits.active ?? stats.edits.total : stats.edits)}</div><div class="l">your edits</div></div>{/if}
          </div>
          {#if coverageRows.length}
            <div><div class="section-title" style="margin:6px 0 4px">Coverage of names <span class="faint">(how many entries have a name in each language)</span></div>
              <dl class="kv">{#each coverageRows as [a, b]}<dt>{a}</dt><dd>{b}</dd>{/each}</dl>
              <div class="tiny faint">Sudanese places with no Arabic name here match nothing extra in Arabic searches: add the spelling yourself.</div>
              {#if covDetail.length || sdKinds.length}
                <details class="tiny"><summary>More detail</summary>
                  {#if sdKinds.length}<div class="section-title" style="margin:6px 0 2px">Sudan places by kind</div><dl class="kv">{#each sdKinds as [a, n]}<dt>{a}</dt><dd>{num(n)}</dd>{/each}</dl>{/if}
                  {#each covDetail as [k, rows]}<div class="section-title" style="margin:6px 0 2px">{k}</div><dl class="kv">{#each rows as [a, b]}<dt>{a}</dt><dd>{b}</dd>{/each}</dl>{/each}
                </details>
              {/if}</div>
          {/if}
          <div class="small muted">Built {stats.built_at ? dt(stats.built_at) : 'unknown'} · version <code>{stats.version || '—'}</code></div>
          {#each countTables as [k, o]}
            <div><div class="section-title" style="margin:6px 0 4px">By {k.replace(/^by_/, '').replaceAll('_', ' ')}</div>
              <dl class="kv">{#each flat(o) as [a, b]}<dt>{a}</dt><dd>{b}</dd>{/each}</dl></div>
          {/each}
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>Sources and licences</h2></div>
        <div class="panel-body col">
          {#each stats.sources || [] as src}
            <div class="src"><b>{src.name}</b> <span class="faint small">{src.date ? dt(src.date) : ''}</span>
              <div class="small">{src.license}</div>
              {#if src.attribution}<div class="small muted">{src.attribution}</div>{/if}
              {#if src.file}<div class="tiny faint mono">{src.file}{src.sha256 ? ` · sha256 ${String(src.sha256).slice(0, 16)}…` : ''}</div>{/if}</div>
          {:else}<div class="muted small">No source information recorded.</div>{/each}
          <p class="tiny faint" style="margin:0">Place data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener">OpenStreetMap contributors</a>, licensed under the ODbL 1.0, and <a href="https://www.geonames.org" target="_blank" rel="noreferrer noopener">GeoNames</a> (CC BY 4.0). The dictionary file is a derivative database of OpenStreetMap: if you share it or a database built from it, keep this attribution and share it under the same ODbL terms. Both sources are kept as local files; the app never fetches them at runtime.</p>
        </div>
      </section>
    </div>
  {/if}

  <section class="panel" style="margin-top:12px">
    <div class="panel-head"><h2>Test a query</h2></div>
    <div class="panel-body col">
      <form class="row" onsubmit={runTest}>
        <input type="search" dir="auto" bind:value={testQ} placeholder="e.g. Kiev, الفاشر, Bakhmut" style="flex:1;min-width:min(100%,200px)" aria-label="Query to test" />
        <button type="submit" disabled={testBusy || !testQ.trim()}>{testBusy ? 'Looking…' : 'Show expansion'}</button>
      </form>
      {#if testErr}<div class="callout bad" role="alert">{testErr}</div>
      {:else if test}
        {#if test.groups.length}<ExpansionChips expansion={test} readonly />
        {:else}<div class="muted small">Nothing in the dictionary matches these words. They are searched exactly as typed.</div>{/if}
      {/if}
    </div>
  </section>

  <div class="row" style="margin:16px 0 8px">
    <h2 style="font-size:15px">Browse and edit entries</h2><span class="spacer"></span>
    <button class="primary" onclick={() => (adOpen = true)} disabled={stats && stats.available === false} title={stats && stats.available === false ? 'The dictionary is not installed. Build it first: python -m investigator.gazetteer_build' : undefined}>＋ Add term or place</button>
  </div>
  <div class="row filters">
    <input type="search" dir="auto" bind:value={f.q} placeholder="Name in any language…" style="width:min(100%,260px)" aria-label="Search entries" />
    <select bind:value={f.kind} aria-label="Kind"><option value="">Places and terms</option><option value="place">Places</option><option value="term">Terms</option></select>
    <select bind:value={f.country} aria-label="Country"><option value="">Any country</option><option value="UA">Ukraine</option><option value="SD">Sudan</option><option value="RU">Russia</option></select>
    <span class="muted small">{loaded ? `${num(total)} entries` : ''}</span>{#if listBusy}<span class="spinner"></span>{/if}
  </div>
  {#if listErr}<div class="callout bad" role="alert">{listErr}</div>
  {:else if !loaded}<Loading />
  {:else if !entries.length}<Empty title="No entries match">Try another spelling, or add the term yourself.</Empty>
  {:else}
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Entry</th><th scope="col">Where</th><th scope="col">Names</th></tr></thead>
      <tbody>
        {#each entries as e (e.id)}
          <tr class="clickable" class:faint={e.hidden}>
            <td><button class="link" onclick={() => openEntry(e.id)}>{e.label}</button>
              <div class="tiny muted">{e.kind}{e.category ? ` · ${e.category}` : ''}{#if e.hidden} · <b>hidden</b>{/if}{#if String(e.id).startsWith('user:')} · yours{/if}</div></td>
            <td class="small">{[e.admin1, e.country].filter(Boolean).join(' · ')}</td>
            <td class="small names">{#each visibleNames(e) as n}<span class="nm" dir="auto">{n.name}</span>{/each}{#if (e.names || []).length > 4}<span class="faint tiny">+{(e.names || []).length - 4}</span>{/if}</td>
          </tr>
        {/each}
      </tbody></table></div>
    {#if entries.length < total}<div class="row" style="justify-content:center;margin:10px 0"><button onclick={() => loadEntries(true)} disabled={listBusy}>Show more ({num(total - entries.length)} left)</button></div>{/if}
  {/if}
{/if}

{#if stats}
  <details class="alledits" bind:open={editsOpen} ontoggle={(e) => e.currentTarget.open && loadAllEdits()}>
    <summary>All my dictionary edits ({num(typeof stats.edits_total === 'number' ? stats.edits_total : 0)})</summary>
    {#if allEdits?.error}<div class="callout bad" role="alert">{allEdits.error}</div>
    {:else if !allEdits}<Loading />
    {:else if !editRows.length}<div class="tiny faint">No edits yet.</div>
    {:else}
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Edit</th><th scope="col">Entry</th><th scope="col"></th></tr></thead>
        <tbody>{#each editRows as h (h.id)}
          <tr class:faint={h.undone}><td class="nowrap small">{h.created ? dt(h.created) : ''}</td><td class="small">{h.created_by || ''}</td>
            <td class="small" class:strike={h.undone}><b>{ACTION[h.action] || h.action}</b>{h.name ? ` “${h.name}”` : ''}{h.label ? ` “${h.label}”` : ''}</td>
            <td class="small"><button class="link" onclick={() => openEntry(h.entry_id)}>{h.entry_id}</button></td>
            <td class="right">{#if h.undone}<span class="tiny faint">undone</span>{:else}<button class="sm" onclick={() => undo(h)} disabled={dr.busy}>Undo</button>{/if}</td></tr>
        {/each}</tbody></table></div>
    {/if}
  </details>
{/if}

<Drawer open={dr.open} onclose={closeEntry} title={dr.entry?.label || 'Entry'} width="560px">
  {#if dr.err}<div class="callout bad" role="alert">{dr.err}</div>
  {:else if !dr.entry}<Loading />
  {:else}
    {@const e = dr.entry}
    <div class="col" style="gap:12px;padding:2px">
      <div class="row">
        <span class="pill">{e.kind}</span>{#if e.category}<span class="pill">{e.category}</span>{/if}{#if e.country}<span class="pill info">{e.country}</span>{/if}
        {#if e.hidden}<span class="pill bad">hidden</span>{/if}
        {#if e.admin1}<span class="small muted">{e.admin1}</span>{/if}
        {#if e.population}<span class="small faint">pop. {num(e.population)}</span>{/if}
        {#if e.lat != null && e.lon != null}<span class="small faint mono" title="Coordinates from the dictionary source, not verified">{Number(e.lat).toFixed(3)}, {Number(e.lon).toFixed(3)}</span>{/if}
      </div>
      {#if e.notes}<div class="small muted">{e.notes}</div>{/if}
      {#if e.sources?.length}<div class="tiny faint">Sources: {Array.isArray(e.sources) ? e.sources.join(', ') : e.sources}</div>{/if}

      <div>
        <div class="section-title" style="margin-top:0">Names ({(e.names || []).length})</div>
        <div class="table-wrap"><table class="t">
          <thead><tr><th scope="col">Name</th><th scope="col">Language</th><th scope="col">Source</th><th scope="col"></th></tr></thead>
          <tbody>
            {#each e.names || [] as n}
              <tr class:faint={n.hidden}>
                <td dir="auto" class:strike={n.hidden}>{n.name}{#if n.historic} <span class="pill warn">historic</span>{/if}{#if n.expand === 0 || n.expand === false} <span class="pill" title="Helps match what you type, but is not added to searches">match only</span>{/if}</td>
                <td class="small">{langLabel(n.lang)}</td>
                <td class="small muted">{n.user ? 'you' : n.source || ''}{#if n.hidden} · hidden{/if}</td>
                <td class="right">{#if !n.hidden}<button class="sm ghost" onclick={() => hideName(n)} disabled={dr.busy} title="Stop using this spelling in searches">Hide</button>{/if}</td>
              </tr>
            {/each}
          </tbody></table></div>
      </div>

      <form class="row addname" onsubmit={(ev) => { ev.preventDefault(); addName(); }}>
        <input type="text" dir="auto" bind:value={newName.name} placeholder="Add a spelling…" style="flex:1;min-width:150px" aria-label="New name" />
        <select bind:value={newName.lang} aria-label="Language">{#each LANGS as [k, l]}<option value={k}>{l}</option>{/each}</select>
        <button class="primary" type="submit" disabled={dr.busy || !newName.name.trim()}>Add</button>
      </form>

      <div class="row">
        {#if !e.hidden}<button class="danger" onclick={hideEntry} disabled={dr.busy}>Hide this entry</button>{/if}
        <span class="help">Edits are stored in your workspace and the audit log; the built dictionary is never changed.</span>
      </div>

      <div>
        <div class="section-title">Edit history</div>
        {#each e.edits || e.history || [] as h (h.id)}
          <div class="hist row" class:faint={h.undone}>
            <span class="small" class:strike={h.undone}><b>{ACTION[h.action] || h.action}</b>{h.name ? ` “${h.name}”` : ''}{h.lang ? ` (${h.lang})` : ''}</span>
            <span class="tiny faint">{h.created ? dt(h.created) : ''}{h.created_by ? ` · ${h.created_by}` : ''}</span>
            <span class="spacer"></span>
            {#if h.undone}<span class="tiny faint">undone</span>{:else}<button class="sm" onclick={() => undo(h)} disabled={dr.busy}>Undo</button>{/if}
          </div>
        {:else}<div class="tiny faint">No edits to this entry.</div>{/each}
      </div>
    </div>
  {/if}
</Drawer>

<AddDictEntry open={adOpen} onclose={() => (adOpen = false)} onsaved={addedEntry} />

<style>
  .kv { display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px; margin: 0; font-size: 12.5px; }
  .kv dt { color: var(--muted); } .kv dd { margin: 0; overflow-wrap: anywhere; min-width: 0; }
  .src { padding-bottom: 6px; border-bottom: 1px solid var(--line); }
  .src:last-of-type { border-bottom: 0; }
  .filters { margin-bottom: 10px; }
  .link { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 600; }
  .link:hover { text-decoration: underline; background: none; }
  .names { display: flex; flex-wrap: wrap; gap: 4px; }
  .nm { background: var(--panel-2); border-radius: 4px; padding: 0 5px; }
  .alledits { margin-top: 16px; }
  .alledits summary { cursor: pointer; font-weight: 600; }
  .strike { text-decoration: line-through; }
  .hist { gap: 8px; padding: 3px 0; border-bottom: 1px solid var(--line); }
  .mono { font-family: var(--mono); }
</style>
