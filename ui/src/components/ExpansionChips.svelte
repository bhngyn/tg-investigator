<script>
  // "Also searching" row: what the dictionary added to the query, as removable chips.
  // `expansion` is the API's expansion object; `value` = { expand, drop, skip }; `onchange(next)` gets a new value.
  // With `readonly` (frozen alert rules, dictionary tester) no controls are shown.
  import { toast } from '../lib/store.svelte.js';
  import { isRtl } from '../lib/fmt.js';
  import { scriptOf } from '../lib/expansion.js';

  /** @type {{ expansion: any, value?: {expand:boolean, drop:string[], skip:string[]}, onchange?: (v:any)=>void, readonly?: boolean, hideToggle?: boolean, loading?: boolean, showExact?: boolean }} */
  let { expansion, value = { expand: true, drop: [], skip: [] }, onchange, readonly = false, hideToggle = false, loading = false, showExact = true } = $props();

  const groups = $derived(expansion?.groups || []);
  const noDict = $derived(expansion?.dictionary?.available === false);
  const on = $derived(readonly ? true : value.expand);
  const set = (patch) => onchange?.({ ...value, ...patch });
  const dropTerm = (t) => set({ drop: [...new Set([...value.drop, t])] });
  const restoreTerm = (t) => set({ drop: value.drop.filter((x) => x !== t) });
  const skipSpan = (s) => set({ skip: [...new Set([...value.skip, s])] });
  const restoreSpan = (s) => set({ skip: value.skip.filter((x) => x !== s) });

  // One header per group. An ambiguous place name says so once ("4 places named El Fasher") and never
  // asserts a single identity: if the candidates carry different names, the typed span is used instead.
  function groupTitle(g) {
    const n = (g.entries?.length || 0) + (g.more_entries || 0);
    if (g.kind === 'place' && n > 1) {
      const labels = new Set((g.entries || []).map((e) => e.label));
      return labels.size <= 1 ? `${n} places named ${g.label || g.span}` : `${n} possible places for “${g.span}”`;
    }
    return g.label || g.span;
  }
  const showDetail = (g) => g.detail && !(g.kind === 'place' && (g.entries?.length || 0) + (g.more_entries || 0) > 1) && g.detail.trim() !== groupTitle(g).trim() && !groupTitle(g).includes(g.detail.trim());
  // Exact query: split into quoted terms so each Arabic/Cyrillic term is bidi-isolated from the Latin operators.
  const ftsParts = $derived(expansion?.fts ? String(expansion.fts).split(/("[^"]*")/).filter(Boolean) : []);
  const hasTranslitKey = $derived(/"zz[a-z0-9]+"/i.test(expansion?.fts || ''));
  const isDropped = (a) => a.dropped || value.drop.includes(a.term);
  function altText(g, a) {
    if (g.kind === 'translit' && !String(a.term).includes('≈')) return `Cyrillic spelling ≈ ${a.term}`;
    return a.term;
  }
  // skipped spans that no longer appear as groups (the API omits them) stay restorable
  const skippedShown = $derived(readonly ? [] : value.skip.filter((s) => !groups.some((g) => g.span === s)));

  const copyText = $derived(expansion?.fts ? `FTS: ${expansion.fts}\nDictionary version: ${expansion.dict_version || 'none'}` : '');
  async function copy() {
    try { await navigator.clipboard.writeText(copyText); toast('Copied', 'ok', 1500); }
    catch {
      const ta = document.createElement('textarea'); ta.value = copyText; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); toast('Copied', 'ok', 1500); } catch { toast('Copy failed: select the text and copy it by hand', 'error'); }
      ta.remove();
    }
  }
</script>

<div class="exp" class:loading>
  {#if !readonly && !hideToggle}
    <label class="check tog" title={noDict ? 'No place dictionary is installed, so there are no other spellings to match.' : undefined}><input type="checkbox" checked={value.expand && !noDict} disabled={noDict} onchange={(e) => set({ expand: e.currentTarget.checked })} />
      Match other languages/spellings{#if noDict} <span class="faint small">(unavailable: no dictionary installed)</span>{/if}</label>
  {/if}
  {#if on && (groups.length || skippedShown.length)}
    <div class="lbl tiny faint">Also searching</div>
    <div class="groups">
      {#each groups as g (g.span + '|' + g.kind)}
        {#if g.skipped || value.skip.includes(g.span)}
          <div class="grp skipped"><span class="span" dir="auto">{g.span}</span> <span class="muted small">not expanded</span>
            {#if !readonly}<button class="x" onclick={() => restoreSpan(g.span)} title="Expand it again" aria-label="Expand {g.span} again">↺</button>{/if}</div>
        {:else}
        <div class="grp" class:tl={g.kind === 'translit'}>
          {#if g.kind !== 'translit'}
            <div class="gh">
              <span class="span" dir="auto">{g.span}</span><span class="arrow" aria-hidden="true">→</span>
              <b dir="auto">{groupTitle(g)}</b>
              {#if showDetail(g)}<span class="detail muted small" dir="auto">{g.detail}</span>{/if}
              {#if !readonly}<button class="x" onclick={() => skipSpan(g.span)} title="Don't expand “{g.span}” at all" aria-label="Don't expand {g.span}">✕</button>{/if}
            </div>
          {/if}
          <div class="alts">
            {#each g.alts || [] as a (a.term)}
              {@const dropped = isDropped(a)}
              <span class="alt" class:dropped class:hist={a.historic} title={dropped ? 'Left out of this search' : a.historic ? 'Historic name' : ''}>
                {#if g.kind !== 'translit'}<span class="lang">{a.lang || scriptOf(a.term)}</span>{/if}
                <span class="term" dir="auto" class:rtl={isRtl(a.term)}>{altText(g, a)}</span>
                {#if a.historic}<span class="hist-mark">historic</span>{/if}
                {#if !readonly}
                  {#if dropped}<button class="x" onclick={() => restoreTerm(a.term)} title="Put this back" aria-label="Restore {a.term}">↺</button>
                  {:else}<button class="x" onclick={() => dropTerm(a.term)} title="Leave this out of the search" aria-label="Remove {a.term}">✕</button>{/if}
                {/if}
              </span>
            {/each}
            {#each g.omitted || [] as t (t)}
              <span class="alt dropped omitted" title="Cut by the size limit: this spelling was NOT searched"><span class="term" dir="auto">{t}</span><span class="hist-mark">not searched</span></span>
            {/each}
          </div>
        </div>
        {/if}
      {/each}
      {#each skippedShown as s (s)}
        <div class="grp skipped"><span class="span" dir="auto">{s}</span> <span class="muted small">not expanded</span>
          <button class="x" onclick={() => restoreSpan(s)} title="Expand it again" aria-label="Expand {s} again">↺</button></div>
      {/each}
    </div>
    {#if expansion?.truncated}
      <div class="callout warn small"><b>Some spellings are not being searched.</b> The expansion was cut off to keep the search fast{expansion.truncated_count ? ` (${expansion.truncated_count} left out)` : ''}; the struck-through chips marked “not searched” were left out. Narrow the query or drop chips you don’t need, so the chips shown are what runs.</div>
    {/if}
  {:else if !on && !readonly}
    <div class="tiny faint">Expansion is off: only the exact words you typed are searched.</div>
  {/if}
  {#if expansion?.near_literal}
    <div class="callout warn small">Too many spelling combinations for a <code>NEAR</code> search, so the nearby-words part uses the words exactly as typed (other spellings are not tried there).</div>
  {:else if expansion?.near_reduced}
    <div class="callout warn small"><b>Not every spelling is tried in the <code>NEAR</code> part.</b> There were too many combinations, so each word keeps only its most useful spellings there:
      {#each expansion.near_used || [] as n}{#each n.used || [] as u, i}{i ? '; ' : ' '}<bdi>{u.operand}</bdi> → {#each u.alts || [] as a, j}{j ? ', ' : ''}<bdi dir="auto">{String(a).startsWith('zz') ? 'Cyrillic ≈ ' + String(a).slice(2) : a}</bdi>{/each}{/each}{/each}.
      Search a word on its own to see all of its spellings.</div>
  {/if}
  {#if showExact && expansion?.fts}
    <details class="exact tiny">
      <summary>Exact query</summary>
      <div class="ex-body">
        <code class="fts" dir="ltr">{#each ftsParts as part}{#if part.startsWith('"')}<bdi>{part}</bdi>{:else}{part}{/if}{/each}</code>
        {#if hasTranslitKey}<div class="faint">Terms starting with <code>zz</code> are internal transliteration keys (they let a Cyrillic spelling match its Latin one). They are not words you typed.</div>{/if}
        <div class="faint">Dictionary version <code>{expansion.dict_version || 'none'}</code></div>
        <div class="row"><button class="sm" onclick={copy}>Copy</button><span class="faint">Recorded so this search can be re-run exactly.</span></div>
      </div>
    </details>
  {/if}
</div>

<style>
  .exp { display: flex; flex-direction: column; gap: 6px; margin: 8px 2px; min-height: 22px; transition: opacity .12s; }
  .exp.loading { opacity: .65; }
  .tog { font-size: 12.5px; color: var(--muted); }
  .lbl { text-transform: uppercase; letter-spacing: .05em; font-weight: 650; }
  .groups { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: flex-start; }
  .grp { display: flex; flex-direction: column; gap: 4px; padding: 5px 8px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); max-width: 100%; }
  .grp.tl { padding: 3px 6px; }
  .grp.skipped { flex-direction: row; align-items: center; gap: 6px; opacity: .7; }
  .gh { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 13px; }
  .span { font-family: var(--mono); font-size: 12px; background: var(--panel-2); border-radius: 4px; padding: 0 5px; }
  .arrow { color: var(--faint); }
  .alts { display: flex; flex-wrap: wrap; gap: 4px; }
  .alt { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; padding: 1px 4px 1px 3px; border-radius: 999px; border: 1px solid var(--line-strong); background: var(--panel); max-width: 100%; }
  .alt.hist { border-style: dashed; }
  .alt.dropped { opacity: .55; }
  .alt.dropped .term { text-decoration: line-through; }
  .lang { font-size: 10px; font-weight: 700; text-transform: uppercase; color: var(--accent); background: var(--accent-soft); border-radius: 999px; padding: 0 5px; line-height: 15px; }
  .hist-mark { font-size: 10px; color: var(--warn); font-weight: 600; }
  .term { unicode-bidi: plaintext; }
  .term.rtl { font-size: 13.5px; }
  .x { border: 0; background: none; padding: 0 3px; font-size: 11px; color: var(--faint); border-radius: 50%; }
  .x:hover { color: var(--bad); background: var(--panel-2); }
  .exact summary { cursor: pointer; color: var(--muted); }
  .ex-body { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
  .fts { unicode-bidi: isolate; display: block; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--panel-2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 5px 8px; max-height: 140px; overflow: auto; }
</style>
