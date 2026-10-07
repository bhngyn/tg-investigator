<script>
  // All cases: one card per case with what needs attention, plus the whole archive.
  import { onMount } from 'svelte';
  import { app, loadCases, toast } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { hrefIn, go } from '../lib/router.svelte.js';
  import { num, ago, plural } from '../lib/fmt.js';
  import Modal from '../components/Modal.svelte';
  import Loading from '../components/Loading.svelte';
  import EvCaseFields from '../components/EvCaseFields.svelte';

  let overview = $state(null);
  let showArchived = $state(false);
  let archived = $state([]);
  let loadingArchived = $state(false);

  // editor modal: mode = 'create' | 'edit'
  let editor = $state({ open: false, mode: 'create', id: null, name: '', description: '', color: '#2458d6', error: '', busy: false });

  const active = $derived(app.cases.filter((c) => c.status !== 'archived'));

  async function loadOverview() {
    try { overview = await get('/api/overview'); } catch { overview = null; }
  }
  async function loadArchived() {
    loadingArchived = true;
    try { archived = (await get('/api/cases', { archived: 1 })).filter((c) => c.status === 'archived'); }
    catch (e) { toast(e.message, 'error'); }
    loadingArchived = false;
  }
  onMount(() => { loadCases(); loadOverview(); });
  $effect(() => { app.jobsTick; loadOverview(); });
  $effect(() => { if (showArchived) loadArchived(); });

  function attention(c) {
    const a = [];
    if (c.active_jobs?.length) a.push({ k: 'info', t: `Collecting now (${c.active_jobs.length})` });
    if (c.unread_alerts) a.push({ k: 'bad', t: plural(c.unread_alerts, 'unread alert') });
    if (c.new_posts) a.push({ k: 'accent', t: `${num(c.new_posts)} new post${c.new_posts === 1 ? '' : 's'}` });
    if (c.new_comments) a.push({ k: 'accent', t: `${num(c.new_comments)} new comment${c.new_comments === 1 ? '' : 's'}` });
    if (c.deleted_posts) a.push({ k: 'warn', t: `${num(c.deleted_posts)} deleted on Telegram` });
    return a;
  }

  function openCreate() { editor = { open: true, mode: 'create', id: null, name: '', description: '', color: '#2458d6', error: '', busy: false }; }
  function openEdit(c) { editor = { open: true, mode: 'edit', id: c.id, name: c.name, description: c.description || '', color: c.color || '#2458d6', error: '', busy: false }; }
  const closeEditor = () => (editor.open = false);

  async function save() {
    const name = editor.name.trim();
    if (!name) { editor.error = 'A case needs a name.'; return; }
    editor.busy = true; editor.error = '';
    try {
      if (editor.mode === 'create') {
        const c = await post('/api/cases', { name, description: editor.description.trim() || null, color: editor.color });
        await loadCases();
        editor.open = false;
        toast(`Case "${c.name}" created. Now add the channels it covers.`, 'ok');
        go(hrefIn(c.id, 'add'));
      } else {
        await post('/api/cases', { id: editor.id, name, description: editor.description.trim() || null, color: editor.color });
        await loadCases();
        if (showArchived) loadArchived();
        editor.open = false;
        toast('Case updated', 'ok');
      }
    } catch (e) {
      editor.error = e.status === 409 ? 'A case with that name already exists. Pick a different name.' : e.status === 500 && editor.mode === 'edit' ? 'Could not save. Another case probably already has that name.' : e.message;
    }
    editor.busy = false;
  }

  async function setStatus(c, status) {
    if (status === 'archived' && !confirm(`Archive "${c.name}"?\n\nThe case disappears from the switcher but nothing is deleted; you can restore it any time from "Show archived". Its channels and posts stay in the shared pool.`)) return;
    try {
      await post('/api/cases', { id: c.id, status });
      await loadCases();
      if (showArchived) loadArchived();
      toast(status === 'archived' ? `Archived "${c.name}"` : `Restored "${c.name}"`, 'ok');
    } catch (e) { toast(e.message, 'error', 7000); }
  }
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>All cases</h1>
      <p>Each case is a set of channels with its own triage, tags, notes, alerts and incidents. Pick one to work in it.</p>
    </div>
    <button class="primary" onclick={openCreate}>＋ New case</button>
  </div>

  {#if !app.casesLoaded}
    <Loading label="Loading cases…" />
  {:else if !active.length && !showArchived}
    <div class="panel welcome">
      <h2>Start your first case</h2>
      <ol class="steps">
        <li><b>Create a case</b>, a named investigation.</li>
        <li><b>Add channels</b>: paste public t.me channel names. Posts are read from Telegram's public web pages, no login needed.</li>
        <li><b>Collect</b>: you start each collection yourself and it asks first. Nothing runs automatically.</li>
      </ol>
      <p>All collected channels live in <b>one shared pool</b>. A <b>case</b> is a named set of channels from that pool, so a channel is collected once and can belong to several cases.</p>
      <p>Triage (relevant / not relevant), tags, notes, alerts and incidents are kept <b>per case</b>. The same post can be relevant in one case and irrelevant in another.</p>
      <div class="row"><button class="primary" onclick={openCreate}>＋ Create a case</button>
        <a class="btn" href={hrefIn('all', 'overview')}>Or browse the whole archive</a></div>
    </div>
  {:else}
    <div class="cards">
      {#each active as c (c.id)}
        {@const att = attention(c)}
        <article class="card panel" style:--cc={c.color || 'var(--accent)'}>
          <a class="main" href={hrefIn(c.id, 'overview')} aria-label="Open case {c.name}">
            <h2 class="ellipsis"><span class="dot"></span>{c.name}</h2>
            {#if c.description}<p class="desc muted small">{c.description}</p>{/if}
            <div class="nums">
              <span><b class="num">{num(c.channels)}</b> channels</span>
              <span><b class="num">{num(c.posts)}</b> posts</span>
              {#if c.comments}<span><b class="num">{num(c.comments)}</b> comments</span>{/if}
              {#if c.incidents}<span><b class="num">{num(c.incidents)}</b> incidents</span>{/if}
            </div>
            <div class="needs">
              {#if c.unreviewed}<span class="pill" title="Posts in this case not yet triaged">{num(c.unreviewed)} posts to review</span>{/if}
              {#if c.verify}<span class="pill warn">{num(c.verify)} posts to verify</span>{/if}
              {#if c.relevant}<span class="pill ok">{num(c.relevant)} posts relevant</span>{/if}
              {#if c.comment_unreviewed}<span class="pill" title="Discussion-group comments in this case not yet triaged">{num(c.comment_unreviewed)} comments to review</span>{/if}
              {#each att as a}<span class="pill {a.k}">{a.t}</span>{/each}
              {#if !c.unreviewed && !c.comment_unreviewed && !att.length}<span class="pill ok">Nothing waiting</span>{/if}
            </div>
            <div class="faint tiny">Last collected: {c.last_collected ? ago(c.last_collected) : 'never (add channels, then collect)'}</div>
          </a>
          <footer class="row">
            <button class="ghost sm" onclick={() => openEdit(c)}>Edit</button>
            <button class="ghost sm" onclick={() => setStatus(c, 'archived')}>Archive</button>
            <span class="spacer"></span>
            <a class="small" href={hrefIn(c.id, 'inbox')}>Review queue →</a>
          </footer>
        </article>
      {/each}

      <article class="card panel all">
        <a class="main" href={hrefIn('all', 'overview')} aria-label="Open the whole archive">
          <h2><span class="dot" style="background:var(--faint)"></span>Whole archive</h2>
          <p class="desc muted small">Everything collected, across all cases, with global triage. Useful for finding channels not in any case yet.</p>
          {#if overview}
            <div class="nums">
              <span><b class="num">{num(overview.counts.channels)}</b> channels</span>
              <span><b class="num">{num(overview.counts.posts)}</b> posts</span>
              <span><b class="num">{num(overview.counts.comments)}</b> comments</span>
            </div>
          {:else}<div class="nums faint small">…</div>{/if}
        </a>
      </article>
    </div>

    {#if !active.length}
      <div class="callout" style="margin-top:12px">No active cases. Create one, or restore an archived case below.</div>
    {/if}
  {/if}

  <div class="row" style="margin-top:18px">
    <label class="check"><input type="checkbox" bind:checked={showArchived} /> Show archived cases</label>
  </div>
  {#if showArchived}
    {#if loadingArchived}<Loading />
    {:else if !archived.length}<p class="muted small">No archived cases.</p>
    {:else}
      <div class="table-wrap" style="margin-top:8px"><table class="t">
        <thead><tr><th scope="col">Case</th><th scope="col" class="right">Channels</th><th scope="col" class="right">Posts</th><th scope="col">Last collected</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each archived as c (c.id)}
            <tr>
              <td><span class="dot inline" style:background={c.color || 'var(--faint)'}></span> {c.name}
                {#if c.description}<div class="muted tiny">{c.description}</div>{/if}</td>
              <td class="right num">{num(c.channels)}</td><td class="right num">{num(c.posts)}</td>
              <td class="muted">{ago(c.last_collected)}</td>
              <td class="right nowrap"><button class="sm" onclick={() => openEdit(c)}>Edit</button> <button class="sm" onclick={() => setStatus(c, 'active')}>Restore</button></td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
  {/if}
</div>

<Modal open={editor.open} onclose={closeEditor} title={editor.mode === 'create' ? 'New case' : 'Edit case'} width="480px">
  <form id="caseform" onsubmit={(e) => { e.preventDefault(); save(); }}>
    <EvCaseFields bind:name={editor.name} bind:description={editor.description} bind:color={editor.color} error={editor.error} autofocus />
    {#if editor.mode === 'create'}<p class="help" style="margin-top:10px">Next you will add the channels this case covers.</p>{/if}
  </form>
  {#snippet footer()}
    <button onclick={closeEditor}>Cancel</button>
    <button class="primary" type="submit" form="caseform" disabled={editor.busy || !editor.name.trim()}>{editor.busy ? 'Saving…' : editor.mode === 'create' ? 'Create case' : 'Save'}</button>
  {/snippet}
</Modal>

<style>
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
  .card { border-top: 4px solid var(--cc, var(--line-strong)); display: flex; flex-direction: column; }
  .card.all { --cc: var(--line-strong); background: var(--panel-2); }
  .main { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px 10px; color: inherit; text-decoration: none; flex: 1; }
  .main:hover h2 { color: var(--accent); }
  h2 { font-size: 16px; display: flex; align-items: center; gap: 8px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--cc); flex: none; display: inline-block; }
  .dot.inline { vertical-align: middle; }
  .desc { margin: 0; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .nums { display: flex; gap: 14px; flex-wrap: wrap; font-size: 13px; color: var(--muted); }
  .nums b { color: var(--text); }
  .needs { display: flex; gap: 5px; flex-wrap: wrap; }
  footer { padding: 6px 10px; border-top: 1px solid var(--line); }
  .welcome { padding: 24px 28px; max-width: 680px; }
  .welcome p { max-width: 62ch; margin: 8px 0; }
  .welcome .row { margin-top: 14px; }
  .steps { margin: 10px 0 10px 1.2em; padding: 0; max-width: 62ch; line-height: 1.6; }
</style>
