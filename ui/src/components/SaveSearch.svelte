<script>
  // "Save this search": stores the query and filters (and optionally the exact expansion) as a named saved search.
  // POST /api/saved_searches {name, q, params, freeze, notes, case_id}
  import { post } from '../lib/api.js';
  import { caseId } from '../lib/router.svelte.js';
  import { toast, currentCase } from '../lib/store.svelte.js';
  import Modal from './Modal.svelte';
  import { describeParams } from '../lib/searchparams.js';
  const desc = $derived(describeParams(params));

  /** params: the API filter params of this search (no paging, no case). */
  let { open = false, onclose, q = '', params = {}, onsaved, truncated = false } = $props();

  let name = $state('');
  let freeze = $state(false);
  let notes = $state('');
  let busy = $state(false);
  let err = $state('');
  let wasOpen = false;
  let openedOn = '';          // the search this dialog was opened for
  $effect(() => {
    const key = JSON.stringify([q, params]);
    if (open && !wasOpen) { name = (q || '').slice(0, 60); freeze = false; notes = ''; err = ''; busy = false; openedOn = key; }
    else if (open && key !== openedOn) {
      // the search changed underneath (Back button, a pasted link): saving now would pair one search's name with
      // another's query, so close and say so
      onclose?.();
      toast('The search changed, so the save dialog was closed. Open it again to save the new search.');
    }
    wasOpen = open;
  });

  async function save() {
    if (freeze && truncated && !confirm('The list of other spellings was cut off to keep the search fast, so some spellings are NOT searched.\n\nFreezing stores this shortened list, and every future run will keep missing them. Freeze anyway?')) return;
    busy = true; err = '';
    try {
      const { q: _q, ...rest } = params;
      const cid = caseId();
      const r = await post('/api/saved_searches', { name: name.trim(), q: q || '', params: rest, freeze, notes: notes.trim() || null, ...(cid != null ? { case_id: cid, case: cid } : {}) });
      toast(`Saved “${name.trim()}”`, 'ok');
      onclose?.();
      onsaved?.(r);
    } catch (e) { err = e.message; }
    busy = false;
  }
</script>

<Modal {open} {onclose} title="Save this search" width="520px">
  <form id="savesearch" class="col" style="gap:12px" onsubmit={(e) => { e.preventDefault(); save(); }}>
    <p class="muted small" style="margin:0">Saved {currentCase() ? `in the case “${currentCase().name}”` : 'for the whole archive'}. Run it again later to see what is new; the runs are logged in Search history.</p>
    <label class="field">Name<input type="text" bind:value={name} maxlength="120" /></label>
    <label class="field">Notes <span class="faint">(why this search matters)</span><textarea data-draft rows="2" bind:value={notes}></textarea></label>
    <label class="opt"><input type="checkbox" bind:checked={freeze} />
      <span><b>Freeze the exact query</b>
        <div class="help">On: the words and spellings the dictionary added today are stored, so the search runs the same way in the future even if the dictionary changes (like an alert rule). Off: each run uses the dictionary as it is then, so new spellings you add later are picked up.</div></span></label>
    {#if truncated}<div class="callout warn small">The other spellings for these words were cut off, so this search does not cover all of them. {freeze ? 'Freezing keeps that gap.' : 'Leave “freeze” off to pick up the full list once you narrow the words.'}</div>{/if}
    <div class="tiny faint">Saving: <b dir="auto">{q || '(no words)'}</b>{#each desc as d}<span aria-hidden="true">{", "}</span><span dir="auto">{d.label}</span>{/each}</div>
    {#if err}<div class="callout bad" role="alert">{err}</div>{/if}
  </form>
  {#snippet footer()}
    <button onclick={onclose}>Cancel</button>
    <button class="primary" type="submit" form="savesearch" disabled={busy || !name.trim()}>{busy ? 'Saving…' : 'Save search'}</button>
  {/snippet}
</Modal>

<style>
  .opt { display: flex; gap: 8px; align-items: flex-start; cursor: pointer; padding: 6px 8px; border: 1px solid var(--line); border-radius: var(--radius-sm); }
  .opt:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
</style>
