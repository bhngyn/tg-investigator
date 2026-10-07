<script>
  // "Terms in these results": words over-represented in the current result set versus the whole archive.
  // GET /api/search/terms?<search params>&limit=40 (add scope=comments for comment search). Loaded when opened, and again
  // when the search changes while it is open. Click a term to add it to the query; "Add to dictionary" opens the
  // dictionary's add-term dialog. Terms are a lead for new search words, not a finding.
  //   <SearchTerms params={apiParams} scope="posts"|"comments" q={Q.q} onadd={(term, op) => ...} />
  import { untrack } from 'svelte';
  import { get } from '../lib/api.js';
  import { num } from '../lib/fmt.js';
  import { scriptOf } from '../lib/expansion.js';
  import { listOf } from '../lib/searchparams.js';
  import Loading from './Loading.svelte';
  import AddDictEntry from './AddDictEntry.svelte';

  let { params, scope = 'posts', onadd } = $props();

  let open = $state(false);
  let data = $state(null);
  let error = $state('');
  let loading = $state(false);
  let seq = 0;
  let loadedFor = '';

  const key = $derived(JSON.stringify([params, scope]));
  async function load() {
    const my = ++seq;
    loading = true; error = '';
    loadedFor = key;
    try {
      const r = await get('/api/search/terms', { ...params, limit: 40, ...(scope === 'comments' ? { scope: 'comments' } : {}) });
      if (my === seq) data = r;
    } catch (e) { if (my === seq) { error = e.message; data = null; } }
    if (my === seq) loading = false;
  }
  // lazy: nothing is requested until the panel is opened; then follow the search
  $effect(() => { if (open) { key; untrack(() => { if (loadedFor !== key || !data) load(); }); } });

  const rows = $derived(listOf(data, 'terms'));
  const maxN = $derived(Math.max(1, ...rows.map((r) => r.in_results || 0)));
  // the API reports the script (ar / cy / la); fall back to a guess from the letters
  const SCRIPT = { ar: 'AR', cy: 'CYR', la: 'LAT' };
  const scriptBadge = (r) => SCRIPT[r.script] || (r.script ? String(r.script).toUpperCase() : scriptOf(r.term).toUpperCase());
  const dictId = (r) => r.dictionary?.entry_id ?? r.dictionary?.id ?? null;

  let adding = $state(null);
</script>

<details class="terms panel" bind:open>
  <summary><b>Terms in these results</b> <span class="faint small">words that stand out here compared with the whole archive</span></summary>
  <div class="body">
    {#if error}
      <div class="callout bad small">{error} <button class="sm" onclick={load}>Retry</button></div>
    {:else if !data && loading}
      <Loading label="Comparing with the archive…" />
    {:else if data}
      {#if !rows.length}
        <div class="muted small">Nothing stands out (too few results, or the words are already in your query).</div>
      {:else}
        <p class="tiny faint" style="margin:0 0 6px">Ranked by how much more often a word appears in these results than in the archive. Use it to find spellings and related words to search next; a frequent word is not evidence of anything by itself.</p>
        <div class="table-wrap" class:dim={loading}>
          <table class="t small">
            <thead><tr><th scope="col">Term</th><th scope="col" class="num" title="Items in these results containing the term">In results</th><th scope="col" class="num" title="Items in the whole archive containing the term">In archive</th><th scope="col" aria-label="Share"></th><th scope="col">Dictionary</th><th scope="col"></th></tr></thead>
            <tbody>
              {#each rows as r (r.term)}
                {@const id = dictId(r)}
                <tr>
                  <td><button class="link" onclick={() => onadd?.(r.term, 'and')} title="Add to the query (all words must appear)"><span dir="auto">{r.term}</span></button>
                    <span class="lang" title="Script of the word">{scriptBadge(r)}</span></td>
                  <td class="num">{num(r.in_results)}</td>
                  <td class="num faint">{num(r.in_corpus)}</td>
                  <td class="barcell"><span class="bar" style:width="{Math.max(3, Math.round(((r.in_results || 0) / maxN) * 100))}%"></span></td>
                  <td>
                    {#if id != null && (r.dictionary?.match_only || r.dictionary?.expand === false || r.dictionary?.expand === 0)}<span class="faint small" title="This word only helps match what you type; it is not a main name of that entry">alias of {r.dictionary.label || id}</span>
                    {:else if id != null}<a href={'#/settings?tab=dictionary&entry=' + encodeURIComponent(id)} title="Open this dictionary entry">{r.dictionary.label || id}</a>
                    {:else}<button class="sm ghost" onclick={() => (adding = r.term)}>Add to dictionary</button>{/if}
                  </td>
                  <td class="right nowrap"><button class="sm ghost" onclick={() => onadd?.(r.term, 'or')} title="Add as an alternative (OR)">or</button></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </div>
</details>

<AddDictEntry open={adding !== null} name={adding || ''} onclose={() => (adding = null)} onsaved={() => { if (open) load(); }} />

<style>
  .terms { margin: 8px 0 10px; }
  .terms summary { cursor: pointer; padding: 8px 12px; }
  .body { padding: 0 12px 10px; }
  .dim { opacity: .6; }
  .link { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 600; cursor: pointer; }
  .link:hover { text-decoration: underline; background: none; }
  .lang { font-size: 10px; font-weight: 700; text-transform: uppercase; color: var(--accent); background: var(--accent-soft); border-radius: 999px; padding: 0 5px; margin-inline-start: 4px; }
  .barcell { width: 22%; min-width: 60px; }
  .bar { display: block; height: 8px; border-radius: 3px; background: var(--accent); opacity: .7; }
</style>
