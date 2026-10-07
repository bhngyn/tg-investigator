<script>
  import TriageDetailDrawer from '../components/TriageDetailDrawer.svelte';
  // One incident: editable details, linked evidence, history and evidence packages.
  import { untrack } from 'svelte';
  import { app, toast } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { route, caseId, href } from '../lib/router.svelte.js';
  import { num, dt, bytes, pj } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import PostCard from '../components/PostCard.svelte';
  import EvIncidentItem from '../components/EvIncidentItem.svelte';

  const STATUS_TONE = { open: 'accent', verified: 'ok', disputed: 'warn', closed: '' };
  const STATUS_HELP = { open: 'Still being worked on.', verified: 'Date, place and events are corroborated by independent sources.', disputed: 'Sources conflict about what happened.', closed: 'Work is finished. Incidents are closed, never deleted.' };
  const FIELDS = ['title', 'description', 'status', 'case_id', 'date_start', 'date_end', 'date_note', 'location', 'lat', 'lon', 'location_note'];
  const TYPES = [['post', 'Post (id)'], ['comment', 'Comment (id)'], ['media', 'Media file (id)'], ['comment_media', 'Comment media (id)'], ['channel', 'Channel (username)'], ['account', 'Account (Telegram user id)']];

  const id = $derived(Number(route.args[0]));
  let inc = $state(null);
  let form = $state(null);
  let posts = $state({});        // post id -> card
  let loading = $state(true);
  let error = $state('');
  let saving = $state(false);
  let building = $state(false);
  let built = $state(null);
  let formErr = $state('');
  let statusErr = $state('');    // could not load this case's review status for the linked posts

  const toForm = (i) => Object.fromEntries(FIELDS.map((k) => [k, i[k] ?? '']));
  const dirty = $derived(inc && form && FIELDS.some((k) => String(form[k] ?? '') !== String(inc[k] ?? '')));

  async function load(keepForm = false) {
    error = '';
    try {
      const i = await get(`/api/incidents/${id}`);
      inc = i;
      if (!keepForm || !form) form = toForm(i);
      await loadPosts(i);
    } catch (e) { error = e.message; }
    loading = false;
  }
  $effect(() => { id; untrack(() => { loading = true; inc = null; form = null; built = null; load(); }); });

  async function loadPosts(i) {
    const ids = i.items.filter((x) => x.item_type === 'post' && x.item).map((x) => Number(x.item_id));
    const cards = await Promise.all(ids.map((p) => get(`/api/posts/${p}`).catch(() => null)));
    const map = {};
    for (const c of cards) if (c) map[c.id] = c;
    // /api/posts/{id} returns the global review status; inside a case show that case's own status,
    // asked for exactly these posts (never guessed, never silently "unreviewed").
    const cid = caseId();
    statusErr = '';
    if (cid != null && Object.keys(map).length) {
      try {
        // The server caps ids per request; ask in chunks of 500 and merge.
        const all = Object.keys(map);
        const r = { reviews: {}, tags: {}, notes: {} };
        for (let k = 0; k < all.length; k += 500) {
          const part = await get(`/api/cases/${cid}/reviews`, { type: 'post', ids: all.slice(k, k + 500).join(',') });
          Object.assign(r.reviews, part.reviews); Object.assign(r.tags, part.tags); Object.assign(r.notes, part.notes);
        }
        let missing = 0;
        for (const c of Object.values(map)) {
          // An id the server did not return is unknown, never assumed to be unreviewed.
          if (r.reviews[c.id] === undefined) missing++;
          c.case_review = r.reviews[c.id];
          c.case_tags = r.tags[c.id] || [];
          c.case_notes = r.notes[c.id] || 0;
        }
        if (missing) statusErr = `Review status could not be loaded for ${missing} post${missing === 1 ? '' : 's'}.`;
      } catch (e) {
        statusErr = e.message;
        for (const c of Object.values(map)) { c.case_review = undefined; c.case_tags = []; c.case_notes = 0; }
      }
    }
    posts = map;
  }

  async function save() {
    formErr = '';
    if (!String(form.title).trim()) { formErr = 'An incident needs a title.'; return; }
    saving = true;
    try {
      const b = { id };
      for (const k of FIELDS) {
        let v = form[k];
        if (typeof v === 'string') v = v.trim();
        if (k === 'case_id') v = v === '' ? null : Number(v);
        else if (k === 'lat' || k === 'lon') v = v === '' ? null : v;
        else if (v === '' && !['title', 'status'].includes(k)) v = null;
        b[k] = v;
      }
      inc = { ...inc, ...(await post('/api/incidents', b)) };
      form = toForm(inc);
      toast('Incident saved', 'ok');
      load(true);
    } catch (e) { formErr = e.message; }
    saving = false;
  }

  // ---- items ----
  let add = $state({ type: 'post', id: '', relation: '', note: '' });
  let addErr = $state('');
  async function addItem() {
    addErr = '';
    const itemId = add.id.trim().replace(/^@/, '');
    if (!itemId) return;
    try {
      const r = await post(`/api/incidents/${id}/items`, { add: [{ item_type: add.type, item_id: add.type === 'channel' ? itemId.toLowerCase() : itemId, relation: add.relation.trim() || null, note: add.note.trim() || null }] });
      toast(r.added ? 'Item linked' : 'Already linked (relation and note updated)', 'ok');
      add = { ...add, id: '', note: '' };
      await load(true);
    } catch (e) { addErr = e.message; }
  }
  async function removeItem(it) {
    if (!confirm(`Remove this ${it.item_type.replace('_', ' ')} from the incident?\n\nThe item itself stays in the archive; only the link is removed (recorded in the audit log).`)) return;
    try { await post(`/api/incidents/${id}/items`, { remove: [{ item_type: it.item_type, item_id: it.item_id }] }); toast('Removed from incident', 'ok'); await load(true); }
    catch (e) { toast(e.message, 'error'); }
  }
  async function editItem(it, relation, note) {
    try { await post(`/api/incidents/${id}/items`, { add: [{ item_type: it.item_type, item_id: it.item_id, relation: relation || null, note: note || null }] }); await load(true); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function build() {
    if (dirty && !confirm('You have unsaved changes to this incident. The package uses the saved version. Build anyway?')) return;
    building = true; built = null;
    try { built = await post('/api/exports', { scope: 'incident', id }); toast('Evidence package built', 'ok'); await load(true); }
    catch (e) { toast(e.message, 'error', 8000); }
    building = false;
  }

  function histText(h) {
    const d = pj(h.detail, null);
    if (!d) return '';
    if (h.action === 'incident_edit') return Object.entries(d).map(([k, v]) => `${k}: ${v?.from ?? '–'} → ${v?.to ?? '–'}`).join('; ').slice(0, 300);
    if (d.item) return `${d.item}${d.relation ? ' (' + d.relation + ')' : ''}`;
    return '';
  }
  const HL = { incident_create: 'Created', incident_edit: 'Edited', incident_item_add: 'Item linked', incident_item_remove: 'Item removed', evidence_export: 'Evidence package built' };
  const postItems = $derived((inc?.items || []).filter((x) => x.item_type === 'post'));
  const otherItems = $derived((inc?.items || []).filter((x) => x.item_type !== 'post'));
  let postDetail = $state(null);   // {type:'post', id, seed} while the details drawer is open
</script>

<div class="page">
  <p class="small" style="margin:0 0 8px"><a href={href('incidents')}>← All incidents</a></p>
  {#if loading}<Loading />
  {:else if error}<div class="callout bad" role="alert">{error} <a href={href('incidents')}>Back to incidents</a></div>
  {:else if inc && form}
    <div class="page-head">
      <div>
        <h1>{inc.title} <span class="pill {STATUS_TONE[inc.status] || ''}">{inc.status}</span></h1>
        <div class="faint small">Incident #{inc.id} · created {dt(inc.created)} by {inc.created_by}{#if inc.case_name} · case: <a href={`#/${inc.case_id}/overview`}>{inc.case_name}</a>{/if}</div>
      </div>
      <button class="primary" onclick={build} disabled={building}>{building ? 'Building…' : 'Build evidence package'}</button>
    </div>

    {#if built}
      <div class="callout ok" style="margin-bottom:12px">
        <b>Package built:</b> {num(built.items)} items, {num(built.files)} files, {bytes(built.bytes)}.
        <a class="btn sm" href={built.download}>Download zip</a>
        <div class="tiny" style="margin-top:4px;overflow-wrap:anywhere">SHA-256 <code>{built.sha256}</code><br />{built.path}</div>
        {#if built.missing_files?.length}<div class="tiny" style="color:var(--bad)">Missing from disk and not included: {built.missing_files.join(', ')}</div>{/if}
      </div>
    {/if}

    <form class="panel" onsubmit={(e) => { e.preventDefault(); save(); }}>
      <div class="panel-head"><h2>Details</h2>
        {#if dirty}<span class="pill warn">unsaved changes</span>{/if}
        <button type="button" class="sm" onclick={() => (form = toForm(inc))} disabled={!dirty}>Reset</button>
        <button class="primary sm" type="submit" disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save'}</button></div>
      <div class="panel-body col" style="gap:14px">
        <div class="form-grid">
          <label class="field wide">Title<input type="text" bind:value={form.title} /></label>
          <label class="field">Status<select bind:value={form.status}>{#each Object.keys(STATUS_HELP) as s}<option value={s}>{s}</option>{/each}</select>
            <span class="help">{STATUS_HELP[form.status]}</span></label>
          <label class="field">Case<select bind:value={form.case_id}><option value="">No case</option>{#each app.cases as c}<option value={c.id}>{c.name}</option>{/each}</select></label>
        </div>
        <label class="field">Description<textarea bind:value={form.description} rows="3" placeholder="What happened, according to the evidence gathered so far"></textarea></label>
        <div class="section-title" style="margin:0">When</div>
        <div class="form-grid">
          <label class="field">Start (UTC)<input type="text" bind:value={form.date_start} placeholder="2026-09-20 or 2026-09-20T14:00" /></label>
          <label class="field">End (UTC, if a range)<input type="text" bind:value={form.date_end} placeholder="optional" /></label>
        </div>
        <label class="field">How was the date established?<textarea bind:value={form.date_note} rows="2" placeholder="e.g. Post timestamp is the earliest capture; video shows daylight and a dated newspaper; matches a report from another source…"></textarea></label>
        <div class="section-title" style="margin:0">Where</div>
        <div class="form-grid">
          <label class="field">Location<input type="text" bind:value={form.location} placeholder="Place name" /></label>
          <label class="field">Latitude<input type="text" inputmode="decimal" bind:value={form.lat} placeholder="e.g. 13.6289" /></label>
          <label class="field">Longitude<input type="text" inputmode="decimal" bind:value={form.lon} placeholder="e.g. 25.3492" /></label>
        </div>
        <label class="field">How was the location established?<textarea bind:value={form.location_note} rows="2" placeholder="e.g. Geolocated from the mosque and the road junction visible at 0:12; coordinates written in the post are unverified claims…"></textarea>
          <span class="help">Coordinates you enter here are your own determination. Say how you reached them: coordinates found in a post are claims to check, not verified locations.</span></label>
        {#if formErr}<div class="callout bad" role="alert">{formErr}</div>{/if}
      </div>
    </form>

    <div class="section-title">Linked evidence ({num(inc.items.length)})</div>
    {#if statusErr}
      <div class="callout warn" style="margin-bottom:8px"><b>Review status unknown.</b> {statusErr} Affected cards show “review status unknown” instead of a status. <button class="sm" onclick={() => load(true)}>Retry</button></div>
    {/if}
    <form class="panel panel-body addbox" onsubmit={(e) => { e.preventDefault(); addItem(); }}>
      <div class="row">
        <select bind:value={add.type} aria-label="Item type">{#each TYPES as [v, l]}<option value={v}>{l}</option>{/each}</select>
        <input type="text" bind:value={add.id} placeholder={add.type === 'channel' ? 'channel username' : 'id'} style="width:min(100%,150px)" aria-label="Item id" />
        <input type="text" bind:value={add.relation} aria-label="Relation (optional)" placeholder="relation (optional)" style="width:min(100%,180px)" />
        <input type="text" bind:value={add.note} aria-label="Note (optional)" placeholder="note (optional)" style="flex:1;min-width:140px" />
        <button class="primary sm" type="submit" disabled={!add.id.trim()}>Add</button>
      </div>
      <div class="help">Tip: you can also link items from anywhere with “Add to incident” on a post, comment or file. Ids are shown in the item’s details.</div>
      {#if addErr}<div class="callout bad" style="margin-top:6px">{addErr}</div>{/if}
    </form>

    {#if !inc.items.length}
      <Empty title="Nothing linked yet">Link the posts, comments, files, channels and accounts that document this incident. Only linked items go into its evidence package.</Empty>
    {:else}
      <div class="stack" style="margin-top:10px">
        {#each postItems as it (it.id)}
          <div class="pwrap">
            <div class="row small" style="margin-bottom:4px">
              <span class="pill info">Post</span>
              {#if it.relation}<span class="pill accent">{it.relation}</span>{/if}
              {#if it.note}<span class="muted">📝 {it.note}</span>{/if}
              <span class="spacer"></span>
              <button class="sm ghost" onclick={() => { const r = prompt('Relation to the incident (e.g. primary footage):', it.relation || ''); if (r !== null) editItem(it, r.trim(), it.note || ''); }}>Edit relation</button>
              <button class="sm danger" onclick={() => removeItem(it)}>Remove</button>
            </div>
            {#if posts[Number(it.item_id)]}
              <PostCard post={posts[Number(it.item_id)]} showTags={false} statusUnknown={caseId() != null && posts[Number(it.item_id)]?.case_review === undefined} onopen={(x) => (postDetail = { type: 'post', id: x.id, seed: x })} />
            {:else}
              <div class="panel panel-body muted small">Post {it.item_id} could not be loaded{it.item ? ` (@${it.item.channel})` : ' (no longer in the archive)'}.</div>
            {/if}
          </div>
        {/each}
        {#each otherItems as it (it.id)}<EvIncidentItem {it} onremove={removeItem} onedit={editItem} />{/each}
      </div>
    {/if}

    <div class="grid two" style="margin-top:18px">
      <section class="panel">
        <div class="panel-head"><h2>Evidence packages</h2></div>
        {#if inc.exports?.length}
          <table class="t"><thead><tr><th scope="col">Built</th><th scope="col" class="right">Items</th><th scope="col">Size</th><th scope="col"></th></tr></thead><tbody>
            {#each inc.exports as e (e.id)}
              <tr><td>{dt(e.created)}<div class="faint tiny" title={e.sha256}>SHA-256 {e.sha256.slice(0, 16)}…</div></td>
                <td class="right num">{num(e.items)}</td><td>{bytes(e.bytes)}</td>
                <td class="right"><a class="btn sm" href={`/api/exports/${e.id}/download`}>Download</a></td></tr>
            {/each}</tbody></table>
        {:else}<div class="panel-body muted small">No package built for this incident yet.</div>{/if}
        <div class="panel-body small"><a href={href('exports')}>All evidence packages →</a></div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>History</h2><span class="faint small">audit log</span></div>
        {#if inc.history?.length}
          <ul class="hist">{#each [...inc.history].reverse() as h}
            <li><b class="small">{HL[h.action] || h.action.replaceAll('_', ' ')}</b> <span class="faint small">{histText(h)}</span>
              <div class="faint tiny">{h.actor} · {dt(h.ts)}</div></li>{/each}</ul>
        {:else}<div class="panel-body muted small">No history.</div>{/if}
      </section>
    </div>
  {/if}
</div>


<TriageDetailDrawer bind:item={postDetail} />

<style>
  h1 { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .hist { list-style: none; margin: 0; padding: 0; max-height: 360px; overflow: auto; }
  .hist li { padding: 7px 14px; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
  .hist li:last-child { border-bottom: 0; }
  .addbox { background: var(--panel-2); box-shadow: none; }
  .wide { grid-column: 1 / -1; }
</style>
