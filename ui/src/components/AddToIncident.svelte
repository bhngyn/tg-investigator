<script>
  // Modal: link an item (post, comment, media, channel, account…) to an incident of the current case.
  // Contract: <AddToIncident open itemType="post|comment|media|comment_media|channel|account" itemId={..} onclose={() => ...} />
  import { untrack } from 'svelte';
  import Modal from './Modal.svelte';
  import { app, toast } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { caseId } from '../lib/router.svelte.js';
  import { day } from '../lib/fmt.js';

  let { open = false, itemType = 'post', itemId = null, onclose } = $props();

  const RELATIONS = ['primary footage', 'shows the aftermath', 'eyewitness account', 'reports the event', 'perpetrator claim or self-documentation', 'shows location', 'context', 'repost or copy of'];
  const LABEL = { post: 'post', comment: 'comment', media: 'media file', comment_media: 'comment media file', channel: 'channel', account: 'account' };

  let list = $state([]);
  let linked = $state([]);          // incidents this item is already in
  let loading = $state(false);
  let choice = $state('');          // incident id as string, or 'new'
  let title = $state('');
  let relation = $state('');
  let note = $state('');
  let busy = $state(false);
  let error = $state('');

  const cid = $derived(caseId());
  const casePart = $derived(cid != null ? app.cases.find((c) => c.id === cid)?.name : null);

  async function load() {
    loading = true; error = '';
    try {
      const [l, k] = await Promise.all([
        get('/api/incidents', { case: cid }),
        get(`/api/items/${itemType}/${encodeURIComponent(itemId)}/incidents`).catch(() => []),
      ]);
      list = l; linked = k;
      choice = l.length ? '' : 'new';
    } catch (e) { error = e.message; }
    loading = false;
  }
  $effect(() => {
    if (open) untrack(() => { title = ''; relation = ''; note = ''; load(); });
  });

  const isLinked = (id) => linked.some((k) => k.id === id);
  const canSave = $derived(!busy && itemId != null && ((choice === 'new' && title.trim()) || (choice && choice !== 'new')));

  async function save() {
    busy = true; error = '';
    try {
      let id = choice;
      let name;
      if (choice === 'new') {
        const inc = await post('/api/incidents', { title: title.trim(), case_id: cid, status: 'open' });
        id = inc.id; name = inc.title;
      } else name = list.find((i) => String(i.id) === choice)?.title;
      await post(`/api/incidents/${id}/items`, { add: [{ item_type: itemType, item_id: itemId, relation: relation.trim() || null, note: note.trim() || null }] });
      toast(`Added to incident “${name}”`, 'ok');
      onclose?.();
    } catch (e) { error = e.message; }
    busy = false;
  }
</script>

<Modal {open} {onclose} title="Add {LABEL[itemType] || 'item'} to an incident" width="520px">
  {#if loading}
    <p class="muted"><span class="spinner"></span> Loading incidents…</p>
  {:else}
    <div class="col" style="gap:12px">
      <fieldset>
        <legend class="muted small">Incident{casePart ? ` in ${casePart}` : ''}</legend>
        <div class="opts">
          {#each list as i (i.id)}
            <label class="opt"><input type="radio" name="inc" value={String(i.id)} bind:group={choice} />
              <span><b>{i.title}</b> <span class="pill">{i.status}</span>{#if i.date_start} <span class="faint small">{day(i.date_start)}</span>{/if}
                {#if isLinked(i.id)}<span class="pill ok" title="Adding again only updates relation and note">already linked</span>{/if}</span></label>
          {/each}
          <label class="opt"><input type="radio" name="inc" value="new" bind:group={choice} /><span><b>＋ New incident</b></span></label>
        </div>
        {#if choice === 'new'}
          <input type="text" bind:value={title} aria-label="New incident title" placeholder="Incident title, e.g. Shelling of the market, El Fasher" style="width:100%;margin-top:6px" />
          <p class="help">Created open{casePart ? ` in ${casePart}` : ''}. Add the date and place on the incident page.</p>
        {/if}
      </fieldset>
      <label class="field">How does it relate to the incident? <span class="faint">(optional)</span>
        <input type="text" list="ev-relations" bind:value={relation} aria-label="Relation to the incident" placeholder="e.g. primary footage" />
        <datalist id="ev-relations">{#each RELATIONS as r}<option value={r}></option>{/each}</datalist>
      </label>
      <label class="field">Note <span class="faint">(optional: why this item matters, what it shows)</span><textarea data-draft bind:value={note} rows="2"></textarea></label>
      {#if error}<div class="callout bad" role="alert">{error}</div>{/if}
    </div>
  {/if}
  {#snippet footer()}
    <button onclick={onclose}>Cancel</button>
    <button class="primary" onclick={save} disabled={!canSave}>{busy ? 'Adding…' : 'Add to incident'}</button>
  {/snippet}
</Modal>

<style>
  fieldset { border: 0; padding: 0; margin: 0; }
  legend { padding: 0; margin-bottom: 4px; font-weight: 550; }
  .opts { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: var(--radius-sm); max-height: 240px; overflow: auto; }
  .opt { display: flex; gap: 8px; align-items: flex-start; padding: 7px 10px; cursor: pointer; border-bottom: 1px solid var(--line); }
  .opt:last-child { border-bottom: 0; }
  .opt:hover { background: var(--panel-2); }
</style>
