<script>
  // Evidence packages: build, download, verify.
  import { onMount } from 'svelte';
  import { app, toast } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { caseId, href } from '../lib/router.svelte.js';
  import { num, bytes, dt } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';

  const STATUS = [['relevant', 'Relevant'], ['verify', 'Needs verification'], ['unreviewed', 'Unreviewed (never triaged)'], ['irrelevant', 'Not relevant']];
  const cid = $derived(caseId());

  let past = $state([]);
  let loading = $state(true);
  let scope = $state('case');
  let caseSel = $state(caseId() ?? '');
  let statuses = $state({ relevant: true, verify: false, unreviewed: false, irrelevant: false });
  let media = $state(true);
  let includeIncidents = $state(true);
  let selPosts = $state('');
  let selComments = $state('');
  let building = $state(false);
  let built = $state(null);
  let err = $state('');

  async function load() {
    try { past = await get('/api/exports'); } catch (e) { toast(e.message, 'error'); }
    loading = false;
  }
  onMount(load);
  $effect(() => { if (cid != null) caseSel = cid; });

  const chosen = $derived(STATUS.map(([k]) => k).filter((k) => statuses[k]));
  const ids = (s) => s.split(/[\s,;]+/).filter(Boolean).map(Number).filter((n) => Number.isInteger(n) && n > 0);
  const canBuild = $derived(!building && (scope === 'case' ? caseSel !== '' && chosen.length > 0 : ids(selPosts).length + ids(selComments).length > 0));
  const caseName = (id) => app.cases.find((c) => c.id === id)?.name;

  async function build() {
    building = true; err = ''; built = null;
    try {
      const b = scope === 'case'
        ? { scope: 'case', id: Number(caseSel), statuses: chosen, media, include_incidents: includeIncidents }
        : { scope: 'selection', posts: ids(selPosts), comments: ids(selComments), media, case: cid ?? (caseSel || undefined) };
      built = await post('/api/exports', b);
      toast('Evidence package built', 'ok');
      load();
    } catch (e) { err = e.message; }
    building = false;
  }
  function what(e) {
    if (e.scope === 'case') return `Case: ${caseName(e.scope_id) || '#' + e.scope_id}`;
    if (e.scope === 'incident') return `Incident #${e.scope_id}`;
    return 'Hand-picked selection';
  }
  const fileName = (p) => p.split('/').pop();
</script>

<div class="page">
  <div class="page-head">
    <div><h1>Evidence packages</h1>
      <p>A package is a zip of what you collected, with the record of how and when. It is meant to be handed to lawyers, courts or investigative mechanisms and checked independently.</p></div>
  </div>

  <div class="grid two" style="align-items:start">
    <section class="panel">
      <div class="panel-head"><h2>Build a package</h2></div>
      <div class="panel-body col" style="gap:12px">
        <div class="seg" role="group" aria-label="Scope">
          <button class:on={scope === 'case'} aria-pressed={scope === 'case'} onclick={() => (scope = 'case')}>A case</button>
          <button class:on={scope === 'selection'} aria-pressed={scope === 'selection'} onclick={() => (scope = 'selection')}>Hand-picked items</button>
        </div>
        {#if scope === 'case'}
          <label class="field">Case
            <select bind:value={caseSel}><option value="" disabled>Choose a case…</option>{#each app.cases as c}<option value={c.id}>{c.name}</option>{/each}</select></label>
          <div class="field"><span class="muted small" style="font-weight:550">Include items reviewed as</span>
            <div class="row">{#each STATUS as [k, l]}<label class="check"><input type="checkbox" bind:checked={statuses[k]} /> {l}</label>{/each}</div>
            <span class="help">Only posts and comments with these case review statuses are included. Most packages should contain only “Relevant”.</span>
            {#if statuses.unreviewed}<span class="help" style="color:var(--warn)">“Unreviewed” adds every post and comment of the case's channels that nobody has triaged yet. That can be very large, and it includes material you have not looked at. Consider triaging first.</span>{/if}</div>
          <label class="check"><input type="checkbox" bind:checked={includeIncidents} /> Include the case’s incidents and everything linked to them</label>
        {:else}
          <label class="field">Post ids<input type="text" bind:value={selPosts} placeholder="e.g. 12, 40, 219" /></label>
          <label class="field">Comment ids<input type="text" bind:value={selComments} placeholder="optional" /></label>
          <span class="help">Ids are shown in a post’s or comment’s details. For a single event, build the package from its incident page instead.</span>
        {/if}
        <label class="check"><input type="checkbox" bind:checked={media} /> Include media files <span class="faint">(untick for a much smaller package without photos and videos)</span></label>
        {#if err}<div class="callout bad" role="alert">{err}</div>{/if}
        <div class="row"><button class="primary" onclick={build} disabled={!canBuild}>{building ? 'Building… (hashing every file)' : 'Build evidence package'}</button></div>
        <p class="help">If place or dictionary data (OpenStreetMap, GeoNames) was involved, the package README and manifest carry the data attribution and licence (ODbL / CC BY 4.0).</p>
        <p class="help">Building re-hashes every stored file it includes and refuses to continue if any no longer matches the hash recorded at collection.</p>
        <p class="help">A case package also carries <code>search_log.csv</code> and a “How items were found” section in its README: the searches run in the case, with the exact query, dictionary version, time, who ran them and a hash of the results. <a href={href('searchlog')}>See the search history</a>.</p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>What is in a package</h2></div>
      <div class="panel-body">
        <dl class="files">
          <dt>items.json / items.csv</dt><dd>Every post and comment with where, when and how it was collected, deletions, earlier versions, your review, tags and notes.</dd>
          <dt>media/</dt><dd>Collected photos and videos, byte-identical to the download, each named by its SHA-256.</dd>
          <dt>pages/ and api/</dt><dd>The raw captures posts and comments were read from (gzip), named by the SHA-256 of the content as received.</dd>
          <dt>capture_log.csv</dt><dd>URL, UTC time, method, tool and account for every file.</dd>
          <dt>audit.csv</dt><dd>Every action taken on these items.</dd>
          <dt>incidents.json, manifest.json, README.txt</dt><dd>Incident details, scope and counts, and instructions for the recipient.</dd>
          <dt>SHA256SUMS</dt><dd>A hash of every file. The recipient checks it with:</dd>
        </dl>
        <pre class="cmd">cd &lt;unzipped folder&gt;
shasum -a 256 -c SHA256SUMS</pre>
        <p class="help">Every line should end in “OK”. The zip’s own SHA-256 is shown below and recorded in the audit log, so you can prove the file was not altered after you built it: <code>shasum -a 256 file.zip</code>.</p>
      </div>
    </section>
  </div>

  {#if built}
    <div class="callout ok" style="margin-top:14px">
      <b>Package built.</b> {num(built.items)} items · {num(built.files)} files · {bytes(built.bytes)}
      <a class="btn sm" href={built.download}>Download zip</a>
      <div class="tiny" style="margin-top:4px;overflow-wrap:anywhere">SHA-256 <code>{built.sha256}</code></div>
      <div class="tiny" style="overflow-wrap:anywhere">Saved at <code>{built.path}</code></div>
      {#if built.missing_files?.length}<div class="tiny" style="color:var(--bad);margin-top:4px"><b>{built.missing_files.length} files were missing on disk and are not included:</b> {built.missing_files.slice(0, 10).join(', ')}{built.missing_files.length > 10 ? '…' : ''}. Run “Verify all files” in Settings.</div>{/if}
    </div>
  {/if}

  <div class="section-title">Past packages</div>
  {#if loading}<Loading />
  {:else if !past.length}<p class="muted">No packages built yet.</p>
  {:else}
    <div class="table-wrap"><table class="t">
      <thead><tr><th scope="col">Built (UTC)</th><th scope="col">Of</th><th scope="col" class="right">Items</th><th scope="col" class="right">Files</th><th scope="col" class="right">Size</th><th scope="col">SHA-256</th><th scope="col"></th></tr></thead>
      <tbody>
        {#each past as e (e.id)}
          <tr class:faint={!e.exists}>
            <td class="nowrap">{dt(e.created)}<div class="faint tiny">{e.created_by}</div></td>
            <td>{#if e.scope === 'incident'}<a href={href('incident', e.scope_id)}>{what(e)}</a>{:else}{what(e)}{/if}
              <div class="faint tiny" title={e.path}>{fileName(e.path)}</div></td>
            <td class="right num">{num(e.items)}</td><td class="right num">{num(e.files)}</td><td class="right nowrap">{bytes(e.bytes)}</td>
            <td><code class="tiny" title={e.sha256}>{e.sha256.slice(0, 16)}…</code></td>
            <td class="right nowrap">{#if e.exists}<a class="btn sm" href={`/api/exports/${e.id}/download`}>Download</a>{:else}<span class="pill bad" title={e.path}>file missing</span>{/if}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
  {/if}
</div>

<style>
  .files { display: grid; grid-template-columns: minmax(0, 10.5em) minmax(0, 1fr); gap: 6px 14px; margin: 0; font-size: 13px; }
  .files dt { overflow-wrap: anywhere; }
  @media (max-width: 560px) { .files { grid-template-columns: 1fr; gap: 2px; } .files dd { margin-bottom: 8px; } }
  .files dt { font-family: var(--mono); font-size: 12px; color: var(--text); }
  .files dd { margin: 0; color: var(--muted); }
  .cmd { background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; font-family: var(--mono); font-size: 12px; margin: 8px 0; overflow: auto; }
  code { font-family: var(--mono); font-size: 12px; }
</style>
