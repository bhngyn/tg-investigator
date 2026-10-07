<script>
  // "Add term or place" dialog for the dictionary (used by Settings > Dictionary and by "Terms in these results").
  // POST /api/dictionary/edit {action:'add_entry'}; nothing in the built dictionary changes, the edit is audited and undoable.
  //   <AddDictEntry open onclose onsaved?={(r, label) => ...} name? kind? />
  import { post } from '../lib/api.js';
  import { toast } from '../lib/store.svelte.js';
  import Modal from './Modal.svelte';

  const LANGS = [['', 'Detect from script'], ['en', 'English'], ['ar', 'Arabic'], ['ru', 'Russian'], ['uk', 'Ukrainian'], ['latn', 'Other Latin spelling']];

  let { open = false, onclose, onsaved, name = '', kind = 'term' } = $props();

  const blank = () => ({ kind, label: /^[\x00-\x7F]+$/.test(name) ? name : '', country: '', category: '', names: [{ name, lang: '' }] });
  let v = $state(blank());
  let busy = $state(false);
  let err = $state('');
  // start fresh (and prefilled) every time the dialog opens
  let wasOpen = false;
  $effect(() => {
    if (open && !wasOpen) { v = blank(); err = ''; busy = false; }
    wasOpen = open;
  });

  async function save() {
    busy = true; err = '';
    try {
      const r = await post('/api/dictionary/edit', { action: 'add_entry', kind: v.kind, label: v.label.trim(), country: v.country || null, category: v.category.trim() || null,
        names: v.names.map((n) => ({ name: n.name.trim(), ...(n.lang ? { lang: n.lang } : {}) })).filter((n) => n.name) });
      toast(`Added “${v.label.trim()}”`, 'ok');
      onclose?.();
      onsaved?.(r, v.label.trim());
    } catch (e) { err = e.message; }
    busy = false;
  }
</script>

<Modal {open} {onclose} title="Add term or place" width="560px">
  <form id="addform" class="col" style="gap:12px" onsubmit={(e) => { e.preventDefault(); save(); }}>
    <div class="form-grid">
      <label class="field">Kind<select bind:value={v.kind}><option value="term">Term (a word or phrase)</option><option value="place">Place</option></select></label>
      <label class="field">English label<input type="text" bind:value={v.label} placeholder={v.kind === 'place' ? 'e.g. Um Baru' : 'e.g. drone'} /></label>
      <label class="field">Country <span class="faint">(optional)</span>
        <select bind:value={v.country}><option value="">—</option><option value="UA">Ukraine</option><option value="SD">Sudan</option><option value="RU">Russia</option></select></label>
      <label class="field">Category <span class="faint">(optional)</span><input type="text" bind:value={v.category} placeholder={v.kind === 'place' ? 'village, city, camp…' : 'weapon, unit…'} /></label>
    </div>
    <div>
      <div class="muted small" style="font-weight:550;margin-bottom:4px">Names and spellings</div>
      {#each v.names as n, i}
        <div class="row" style="margin-bottom:4px">
          <input type="text" dir="auto" bind:value={n.name} placeholder="Spelling" style="flex:1;min-width:140px" aria-label="Name {i + 1}" />
          <select bind:value={n.lang} aria-label="Language of name {i + 1}">{#each LANGS as [k, l]}<option value={k}>{l}</option>{/each}</select>
          {#if v.names.length > 1}<button type="button" class="sm ghost" onclick={() => (v.names = v.names.filter((_, j) => j !== i))} aria-label="Remove this name">✕</button>{/if}
        </div>
      {/each}
      <button type="button" class="sm" onclick={() => (v.names = [...v.names, { name: '', lang: '' }])}>＋ Another spelling</button>
    </div>
    {#if err}<div class="callout bad" role="alert">{err}</div>{/if}
  </form>
  {#snippet footer()}
    <button onclick={onclose}>Cancel</button>
    <button class="primary" type="submit" form="addform" disabled={busy || !v.label.trim() || !v.names.some((n) => n.name.trim())}>{busy ? 'Saving…' : 'Add'}</button>
  {/snippet}
</Modal>
