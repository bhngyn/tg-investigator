<script>
  // Full post search. Every filter lives in the URL query so a search survives reload and can be shared
  // inside the app. Results: PostCards with bulk triage, channel facets, timeline, CSV export.
  import { untrack } from 'svelte';
  import { get, csvUrl } from '../lib/api.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import { keyOf, isInteractive, shortcutsOn } from '../lib/keys.js';
  import { scrollBehavior } from '../lib/motion.js';
  import { route, caseId, href, setQuery } from '../lib/router.svelte.js';
  import { app, scoped, sinceForCase, attempt, toast, loadCases } from '../lib/store.svelte.js';
  import { setReview, setTag, reviewOf, REVIEW_LABEL } from '../lib/triage.js';
  import { num, dt, ago, day, plural } from '../lib/fmt.js';
  import PostCard from '../components/PostCard.svelte';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import TriageDetailDrawer, { applyChange } from '../components/TriageDetailDrawer.svelte';
  import ExpansionChips from '../components/ExpansionChips.svelte';
  import DictBanner from '../components/DictBanner.svelte';
  import { expansionFromQuery, expansionToQuery } from '../lib/expansion.js';
  import SearchTerms from '../components/SearchTerms.svelte';
  import SearchFacets from '../components/SearchFacets.svelte';
  import NearFilter from '../components/NearFilter.svelte';
  import SaveSearch from '../components/SaveSearch.svelte';
  import { POST_FILTER_KEYS, IN_SOURCES, DEFAULT_IN, kindLabel, parseEntity, listOf } from '../lib/searchparams.js';

  const inCase = caseId() != null;
  const Q = $derived(route.query);
  const since = $derived(sinceForCase());
  const limit = $derived(Math.min(200, Number(app.settings.page_size) || 25));
  const offset = $derived(Math.max(0, Number(Q.offset) || 0));

  // Text inputs are local until committed (Enter / blur); selects and checkboxes commit at once.
  let qInput = $state(route.query.q || '');
  // Words to highlight in results: the query minus search operators, quotes and wildcards.
  const qTerms = $derived((route.query.q || '').replace(/["()*]/g, ' ').split(/\s+/)
    .filter((w) => w && !/^(OR|AND|NOT|NEAR(\/\d+)?)$/.test(w) && !w.startsWith('-') && !w.includes(':')));
  // plus the dictionary's other spellings (the API's `terms`), longest first so the longer match wins
  const hlTerms = $derived([...new Set([...qTerms, ...(res?.terms || [])])].sort((a, b) => b.length - a.length));
  const expState = $derived(expansionFromQuery(Q));
  let fwdInput = $state(route.query.fwd || '');
  let viewsInput = $state(route.query.min_views || '');
  // keep the boxes in step when the query changes without a remount (links, back/forward)
  $effect(() => { const v = Q.q || ''; untrack(() => { qInput = v; }); });
  $effect(() => { const v = Q.fwd || ''; untrack(() => { fwdInput = v; }); });
  $effect(() => { const v = Q.min_views || ''; untrack(() => { viewsInput = v; }); });

  const FILTER_KEYS = POST_FILTER_KEYS;

  /** API params from the URL query (same for search, facets and CSV). */
  const params = $derived.by(() => {
    const p = {};
    for (const k of FILTER_KEYS) if (Q[k]) p[k] = Q[k];
    delete p.new;
    if (Q.new === '1' && since) p.since = since;
    else if (/^\d{4}-\d\d-\d\dT/.test(Q.since || '')) p.since = Q.since;   // from a saved search or history entry
    return scoped(p);
  });
  const key = $derived(JSON.stringify([params, limit, offset]));

  let res = $state(null);
  let facets = $state([]);
  let saveOpen = $state(false);
  let loading = $state(true);
  let error = $state(null);
  let seq = 0;
  let selected = $state({});           // id -> true
  let cursor = $state(-1);             // keyboard focus in results
  let detail = $state(null);
  let drawerOpen = $state(false);
  let bulkBusy = $state(false);
  let filtersOpen = $state(false);      // narrow screens: the filter column is collapsed behind a button
  // Distinguish "nothing collected yet" from "no match": ask the archive size once, only when a search comes back empty.
  let archivePosts = $state(null);
  let mtStatus = $state(null);
  $effect(() => {
    if (res && res.total === 0 && Q.in && !inSet.includes('text') && !mtStatus) get('/api/mediatext/status').then((x) => { mtStatus = x; }).catch(() => {});
  });
  $effect(() => {
    if (res && res.total === 0 && archivePosts === null) get('/api/overview').then((o) => { archivePosts = o?.counts?.posts ?? -1; }).catch(() => { archivePosts = -1; });
  });

  async function load() {
    const my = ++seq;
    loading = true; error = null;
    try {
      const p = params;
      const [r, f] = await Promise.all([
        get('/api/search', { ...p, limit, offset }),
        // facets ignore the channel filter so other channels stay pickable
        p.channels ? get('/api/search', { ...p, channels: undefined, limit: 1, log: 0 }).then((x) => x.facets).catch(() => null) : null,
      ]);
      if (my !== seq) return;
      res = r;
      const fl = f || r.facets;
      facets = Array.isArray(fl) ? fl : listOf(fl, 'channels');
      selected = {};
      cursor = -1;
    } catch (e) {
      if (my === seq) { error = e.message; res = null; }
    }
    if (my === seq) loading = false;
  }
  $effect(() => { key; untrack(load); });

  // ---- filter helpers ---------------------------------------------------------------------------
  function set(patch) { setQuery({ ...patch, offset: null }); }
  function commitText(e) {
    e?.preventDefault();
    const mv = String(viewsInput ?? '').trim();
    set({ q: qInput.trim() || null, fwd: fwdInput.trim().replace(/^@/, '') || null, min_views: mv && Number(mv) > 0 ? mv : null });
  }
  function reset() {
    qInput = ''; fwdInput = ''; viewsInput = '';
    const patch = {};
    for (const k of FILTER_KEYS) patch[k] = null;
    patch.near_label = null;
    set(patch);
  }
  const chans = $derived((Q.channels || '').split(',').filter(Boolean));
  function toggleChannel(c) {
    const next = chans.includes(c) ? chans.filter((x) => x !== c) : [...chans, c];
    set({ channels: next.join(',') || null });
  }
  // ---- identifiers, places, near, matched sources ------------------------------------------------
  const ents = $derived((Q.entity || '').split(',').filter(Boolean));
  const plcs = $derived((Q.place || '').split(',').filter(Boolean));
  const toggleIn = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const entFacet = $derived(listOf(res?.entities ?? res?.facets?.entities, 'entities'));
  const plFacet = $derived(listOf(res?.places ?? res?.facets?.places, 'places'));
  const srcAvail = $derived(Array.isArray(res?.sources?.available) ? res.sources.available : null);
  const inSet = $derived(Q.in ? Q.in.split(',').filter(Boolean) : DEFAULT_IN);
  function toggleSource(k, on) {
    const next = IN_SOURCES.map(([x]) => x).filter((x) => (x === k ? on : inSet.includes(x)));
    if (!next.length) return;                         // at least one source must stay on
    const isDefault = next.length === DEFAULT_IN.length && DEFAULT_IN.every((x) => next.includes(x));
    set({ in: isDefault ? null : next.join(',') });
  }
  // filters as saved/logged: no paging, no case, and never the moving "since last visit" timestamp
  const searchParamsForOthers = $derived.by(() => { const o = {}; for (const [k, v] of Object.entries(params)) if (!['case', 'limit', 'offset', 'since', 'new'].includes(k)) o[k] = v; return o; });
  const mapQuery = $derived.by(() => { const o = {}; for (const k of POST_FILTER_KEYS) if (Q[k] && k !== 'sort') o[k] = Q[k]; return o; });
  function addTerm(term, op) {
    const cur = (Q.q || '').trim();
    const t = /[\s"]/.test(term) ? `"${term.replace(/"/g, '')}"` : term;
    const next = !cur ? t : op === 'or' ? `${cur} OR ${t}` : `${cur} ${t}`;
    qInput = next;
    set({ q: next });
  }

  const tagValue = $derived(Q.untagged === '1' ? 'untagged' : Q.tag || '');
  function setTagFilter(v) {
    if (v === 'untagged') set({ untagged: '1', tag: null });
    else set({ tag: v || null, untagged: null });
  }
  const tagName = (id) => app.tags.find((t) => String(t.id) === String(id))?.name || `tag ${id}`;

  // Active filter chips (each removable)
  const chips = $derived.by(() => {
    const out = [];
    if (Q.q) out.push(['q', `“${Q.q}”`]);
    for (const c of chans) out.push(['channel:' + c, '@' + c]);
    if (Q.date_from || Q.date_to) out.push(['dates', `${Q.date_from || '…'} → ${Q.date_to || '…'}`]);
    if (Q.fwd) out.push(['fwd', `forwarded from @${Q.fwd}`]);
    if (Q.forwarded) out.push(['forwarded', Q.forwarded === '1' ? 'only forwards' : 'only original posts']);
    if (Q.media) out.push(['media', { photo: 'has photo', video: 'has video', document: 'has file', audio: 'has audio', downloaded: 'media stored', refused: 'media not stored yet', none: 'text only' }[Q.media] || Q.media]);
    if (Q.review) out.push(['review', REVIEW_LABEL[Q.review] || Q.review]);
    if (Q.tag) out.push(['tag', 'tag: ' + Q.tag.split(',').map(tagName).join(' + ')]);
    if (Q.untagged === '1') out.push(['untagged', 'untagged']);
    if (Q.sensitive === '1') out.push(['sensitive', 'marked sensitive']);
    if (Q.min_views) out.push(['min_views', `≥ ${num(Number(Q.min_views))} views`]);
    if (Q.has_notes === '1') out.push(['has_notes', 'has global notes']);
    if (Q.edited === '1') out.push(['edited', 'edited']);
    if (Q.deleted === '1') out.push(['deleted', 'found deleted']);
    if (Q.new === '1' && since) out.push(['new', 'new since last visit']);
    for (const e of ents) { const { kind, value } = parseEntity(e); out.push(['entity:' + e, `${kindLabel(kind).replace(/s$/, '')}: ${value}`]); }
    for (const pl of plcs) out.push(['place:' + pl, 'place: ' + (plFacet.find((x) => String(x.entry_id) === pl)?.label || pl)]);
    if (Q.near) {
      const [la, lo, km] = Q.near.split(',');
      out.push(['near', `within ${km || 25} km of ${Q.near_label || `${la}, ${lo}`}`]);
    }
    if (Q.in) out.push(['in', 'matching in: ' + inSet.map((k) => IN_SOURCES.find(([x]) => x === k)?.[1] || k).join(', ')]);
    return out;
  });
  function removeChip(k) {
    if (k.startsWith('channel:')) return toggleChannel(k.slice(8));
    if (k === 'q') qInput = '';
    if (k === 'fwd') fwdInput = '';
    if (k === 'min_views') viewsInput = '';
    if (k === 'dates') return set({ date_from: null, date_to: null });
    if (k.startsWith('entity:')) return set({ entity: ents.filter((x) => x !== k.slice(7)).join(',') || null });
    if (k.startsWith('place:')) return set({ place: plcs.filter((x) => x !== k.slice(6)).join(',') || null });
    if (k === 'near') return set({ near: null, near_label: null });
    set({ [k]: null });
  }

  const timelinePeak = $derived.by(() => { const b = timeline?.bins?.find((x) => x.n === timeline.max); return b ? (b.from === b.to ? b.from : `${b.from} to ${b.to}`) : ''; });
  // ---- timeline sparkline (gap days filled with zero) --------------------------------------------
  const timeline = $derived.by(() => {
    const t = res?.timeline || [];
    if (!t.length) return null;
    const map = new Map(t.map((x) => [x.d, x.n]));
    const start = Date.parse(t[0].d + 'T00:00:00Z'), end = Date.parse(t[t.length - 1].d + 'T00:00:00Z');
    const days = Math.round((end - start) / 86400000) + 1;
    let bins;
    if (days <= 240) {
      bins = Array.from({ length: days }, (_, i) => { const d = new Date(start + i * 86400000).toISOString().slice(0, 10); return { from: d, to: d, n: map.get(d) || 0 }; });
    } else {  // weekly bins for long ranges
      const weeks = Math.ceil(days / 7);
      bins = Array.from({ length: weeks }, (_, i) => {
        const a = new Date(start + i * 7 * 86400000).toISOString().slice(0, 10);
        const b = new Date(Math.min(end, start + (i * 7 + 6) * 86400000)).toISOString().slice(0, 10);
        return { from: a, to: b, n: 0 };
      });
      for (const x of t) bins[Math.floor((Date.parse(x.d + 'T00:00:00Z') - start) / (7 * 86400000))].n += x.n;
    }
    const max = Math.max(...bins.map((b) => b.n), 1);
    return { bins, max, weekly: days > 240, first: t[0].d, last: t[t.length - 1].d };
  });

  // ---- selection + bulk -------------------------------------------------------------------------
  const results = $derived(res?.results || []);
  const selIds = $derived(Object.keys(selected).filter((k) => selected[k]).map(Number));
  const allOnPage = $derived(results.length > 0 && results.every((p) => selected[p.id]));
  function toggleAll() {
    if (allOnPage) selected = {};
    else selected = Object.fromEntries(results.map((p) => [p.id, true]));
  }
  async function bulkReview(status) {
    const ids = selIds;
    if (!ids.length) return;
    bulkBusy = true;
    const r = await attempt(() => setReview('post', ids, status), `${plural(ids.length, 'post')} → ${REVIEW_LABEL[status]}`);
    bulkBusy = false;
    if (r === undefined) return;
    for (const p of results) if (selected[p.id]) applyChange(p, { id: p.id, review: status });
    selected = {};
    loadCases();
  }
  let bulkTag = $state('');
  async function applyBulkTag(e) {
    const tid = Number(e?.currentTarget?.value || bulkTag);
    if (!tid || !selIds.length) return;
    bulkBusy = true;
    let n = 0;
    try {
      for (const p of results) {
        if (!selected[p.id]) continue;
        const r = await setTag('post', p.id, tid, true);
        applyChange(p, { id: p.id, tags: (r?.tags || []).map((x) => (typeof x === 'object' ? x.id ?? x.tag_id : x)) });
        n++;
      }
      toast(`Tagged ${plural(n, 'post')}: ${tagName(tid)}`, 'ok');
    } catch (e) {
      toast(`${e.message} (${n} tagged before the error)`, 'error', 7000);
    }
    bulkBusy = false;
    bulkTag = '';
  }

  const exportHref = $derived(csvUrl('/api/export.csv', params));
  const pageFrom = $derived(res ? Math.min(res.total, offset + 1) : 0);
  const pageTo = $derived(res ? Math.min(res.total, offset + results.length) : 0);
  function page(delta) {
    setQuery({ offset: Math.max(0, offset + delta * limit) || null });
    document.querySelector('.main')?.scrollTo({ top: 0 });
  }

  // ---- keyboard: j/k move, x select, o open, r/v/i decide --------------------------------------
  let lastKey = '', lastAt = 0;
  function onKey(e) {
    if (e.isComposing || e.keyCode === 229) return;
    const pk = lastKey, pa = lastAt;
    const k = keyOf(e);
    lastKey = k; lastAt = Date.now();
    if (e.metaKey || e.ctrlKey || e.altKey || !shortcutsOn() || drawerOpen || document.querySelector('[aria-modal="true"]')) return;
    if (e.target.closest?.('input, textarea, select, [contenteditable]')) return;
    if (pk === 'g' && Date.now() - pa < 900) return;
    if (!results.length) return;
    if (e.repeat && !'jk'.includes(k)) return;   // one key press = one decision
    const p = results[cursor];
    const move = (d) => {
      cursor = Math.max(0, Math.min(results.length - 1, cursor + d));
      queueMicrotask(() => document.querySelector(`[data-post-id="${results[cursor]?.id}"]`)?.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() }));
    };
    switch (k) {
      case 'j': move(1); break;
      case 'k': move(-1); break;
      case 'x': if (p) selected[p.id] = !selected[p.id]; break;
      case 'o': if (p) detail = { type: 'post', id: p.id, seed: p }; break;
      case 'r': case 'v': case 'i':
        if (p) {
          const st = { r: 'relevant', v: 'verify', i: 'irrelevant' }[k];
          attempt(() => setReview('post', p.id, st)).then((r) => { if (r !== undefined) { applyChange(p, { id: p.id, review: st }); move(1); } });
        }
        break;
      default: return;
    }
  }
  function onDetailChange(ch) { for (const p of results) applyChange(p, ch); }
</script>

<svelte:window onkeydown={onKey} />

<div class="page wide">
  <div class="page-head">
    <div>
      <h1>Search posts</h1>
      <p>{inCase ? 'Posts from this case’s channels. Review status and tags are this case’s.' : 'Every collected post, with archive-wide (global) triage.'} Other languages and spellings of names (Arabic, Cyrillic, Latin, historic) are matched too; you can see and remove them below the search box.</p>
    </div>
  </div>

  <form class="panel searchbar" onsubmit={commitText}>
    <input type="search" data-page-search dir="auto" bind:value={qInput} placeholder='Search text, e.g. الفاشر أسرى · "El Fasher" · drone OR مسيرة -ممول' aria-label="Search text" />
    <select value={Q.sort || ''} onchange={(e) => set({ sort: e.currentTarget.value || null })} aria-label="Sort">
      <option value="">{Q.q ? 'Best match' : 'Newest first'}</option>
      <option value="newest">Newest first</option><option value="oldest">Oldest first</option>
      <option value="views">Most viewed</option><option value="reactions">Most reactions</option>
    </select>
    <button class="primary" type="submit">Search</button>
  </form>
  <DictBanner dictionary={res?.expansion?.dictionary} />
  <ExpansionChips expansion={res?.expansion} value={expState} loading={loading} onchange={(v) => set(expansionToQuery(v))} />
  <details class="syntax small">
    <summary>How search works</summary>
    <ul>
      <li>All words must appear (AND). Each word also matches longer words starting with it: <code>قصف</code> finds <code>قصفت</code>.</li>
      <li><code>"exact phrase"</code> matches words in that order. <code>OR</code> between words matches either: <code>drone OR مسيرة</code>.</li>
      <li><code>-word</code> excludes posts containing it. Arabic articles (ال، وال، بال…) are stripped so <code>الفاشر</code> and <code>فاشر</code> both match.</li>
      <li><b>Nearby words:</b> <code>drone NEAR/10 Fasher</code> finds the two within 10 words of each other (either order). <code>NEAR</code> alone means 10. Phrases work too: <code>"El Fasher" NEAR/5 قصف</code>. Other spellings from the dictionary are tried in every combination (up to a limit; past it the words you typed are used as they are, and the search says so).</li>
      <li><b>Word forms:</b> common endings are ignored for Arabic, Russian and Ukrainian, so a search for one form also finds others (<code>مسيرة</code>~<code>مسيرات</code>, <code>беспилотник</code>~<code>беспилотники</code>). The stem is added to the search; it is a search aid and can occasionally match an unrelated word.</li>
      <li><b>Disguised spellings:</b> invisible characters, mixed Latin/Cyrillic look-alike letters (a Latin “a” inside a Cyrillic word), <code>@</code>/<code>0</code>/<code>$</code> for letters, stretched letters (<code>ваааагнер</code>) and spaced-out words (<code>в а г н е р</code>) are folded before matching, in the index and in your query. The stored text is never changed.</li>
      <li><b>Where it looks:</b> post text (channel names are not searched unless you ask), earlier versions of edited posts, text read from images (OCR) and transcripts of speech. Switch each on or off under <i>Search in</i>. Channel names and forwarded-from names are searched only if you tick <i>Channel &amp; forward names</i>; the words must then match a channel's name or title, not its posts. OCR and transcripts are machine-made and can be wrong; a match there is a lead. Results say which source matched.</li>
      {#if prefs.shortcuts}<li>Press <kbd>/</kbd> anywhere to jump to this box.</li>{/if}
    </ul>
  </details>
  <div class="row wrap logline tiny faint">
    <span>Searches are logged for your methodology record (who, when, exact query, result count).</span>
    <a href={href('searchlog')}>Search history →</a>
    <span class="spacer"></span>
    <button class="sm" onclick={() => (saveOpen = true)} disabled={!res}>Save this search</button>
  </div>

  <button class="filtertoggle" onclick={() => (filtersOpen = !filtersOpen)} aria-expanded={filtersOpen}>{filtersOpen ? 'Hide filters' : 'Filters'}{chips.length ? ` (${chips.length} active)` : ''}</button>
  <div class="layout">
    <aside class="side" class:open={filtersOpen}>
      <div class="panel filters">
        <div class="section-title">Filters</div>
        <div class="two">
          <label class="field">From <input type="date" value={Q.date_from || ''} onchange={(e) => set({ date_from: e.currentTarget.value || null })} /></label>
          <label class="field">To <input type="date" value={Q.date_to || ''} onchange={(e) => set({ date_to: e.currentTarget.value || null })} /></label>
        </div>
        <label class="field">Review status
          <select value={Q.review || ''} onchange={(e) => set({ review: e.currentTarget.value || null })}>
            <option value="">Any</option>
            {#each ['unreviewed', 'relevant', 'verify', 'irrelevant'] as s}<option value={s}>{REVIEW_LABEL[s]}</option>{/each}
          </select>
        </label>
        <label class="field">Tag
          <select value={tagValue} onchange={(e) => setTagFilter(e.currentTarget.value)}>
            <option value="">Any</option><option value="untagged">No tags</option>
            {#each app.tags as t (t.id)}<option value={String(t.id)}>{t.name}</option>{/each}
          </select>
        </label>
        <label class="field">Media
          <select value={Q.media || ''} onchange={(e) => set({ media: e.currentTarget.value || null })}>
            <option value="">Any</option><option value="photo">Has photo</option><option value="video">Has video</option>
            <option value="document">Has file</option><option value="audio">Has audio</option>
            <option value="downloaded">Media stored in archive</option><option value="refused">Media not stored yet</option><option value="none">Text only</option>
          </select>
        </label>
        <label class="field">Forwards
          <select value={Q.forwarded || ''} onchange={(e) => set({ forwarded: e.currentTarget.value || null })}>
            <option value="">All posts</option><option value="1">Only forwards</option><option value="0">Only original posts</option>
          </select>
        </label>
        <form onsubmit={commitText}>
          <label class="field">Forwarded from <input type="text" placeholder="@channel" bind:value={fwdInput} onblur={commitText} /></label>
          <label class="field">Minimum views <input type="number" min="0" step="100" bind:value={viewsInput} onblur={commitText} /></label>
        </form>
        <div class="checks">
          <label class="check"><input type="checkbox" checked={Q.deleted === '1'} onchange={(e) => set({ deleted: e.currentTarget.checked ? '1' : null })} /> Found deleted on Telegram</label>
          <label class="check"><input type="checkbox" checked={Q.edited === '1'} onchange={(e) => set({ edited: e.currentTarget.checked ? '1' : null })} /> Edited</label>
          <label class="check"><input type="checkbox" checked={Q.sensitive === '1'} onchange={(e) => set({ sensitive: e.currentTarget.checked ? '1' : null })} /> Marked sensitive by Telegram</label>
          <label class="check" title="Counts archive-wide notes only; notes written inside a case are not included yet"><input type="checkbox" checked={Q.has_notes === '1'} onchange={(e) => set({ has_notes: e.currentTarget.checked ? '1' : null })} /> Has global notes</label>
          {#if inCase}
            <label class="check" title={since ? `Collected since ${dt(since)}` : 'Available from your second visit to this case'}>
              <input type="checkbox" checked={Q.new === '1'} disabled={!since} onchange={(e) => set({ new: e.currentTarget.checked ? '1' : null })} />
              New since last visit{#if since}<span class="tiny faint">&nbsp;({ago(since)})</span>{/if}
            </label>
          {/if}
        </div>
        <div class="section-title">Search in</div>
        <div class="checks">
          {#each IN_SOURCES as [k, l]}
            {@const off = !!srcAvail && !srcAvail.includes(k)}
            <label class="check" class:faint={off} title={off ? 'Not available yet in this workspace (nothing indexed for it)' : k === 'text' ? '' : 'Only matters when there are words in the search box'}><input type="checkbox" checked={inSet.includes(k) && !off} disabled={off} onchange={(e) => toggleSource(k, e.currentTarget.checked)} /> {l}</label>
          {/each}
        </div>
        <div class="section-title">Near a place</div>
        <NearFilter value={Q.near || ''} label={Q.near_label || ''} onchange={(near, label) => set({ near, near_label: label })} />
        {#if chips.length}<button class="sm ghost" onclick={reset}>Reset all filters</button>{/if}
      </div>

      <div class="panel filters">
        <div class="section-title">Channels</div>
        {#each facets as f (f.channel)}
          <button class="facet" class:on={chans.includes(f.channel)} onclick={() => toggleChannel(f.channel)} aria-pressed={chans.includes(f.channel)}>
            <span class="ellipsis"><bdi dir="ltr">@{f.channel}</bdi></span><span class="faint num">{num(f.n)}</span>
          </button>
        {:else}
          <div class="tiny faint">{loading ? '…' : 'No channels match.'}</div>
        {/each}
        {#if facets.length >= 15}<div class="tiny faint">Top 15 channels for this search.</div>{/if}
      </div>

      <SearchFacets entities={entFacet} places={plFacet} activeEntities={ents} activePlaces={plcs} {mapQuery} loading={loading} unambiguous={Q.unambiguous === '1'} onunambiguous={(on) => set({ unambiguous: on ? '1' : null })}
        onentity={(kind, value) => set({ entity: toggleIn(ents, kind + ':' + value).join(',') || null })}
        onplace={(id) => set({ place: toggleIn(plcs, id).join(',') || null })} />
    </aside>

    <section class="results">
      {#if chips.length}
        <div class="row wrap chips">
          {#each chips as [k, label] (k)}
            <button class="chip on" onclick={() => removeChip(k)} title="Remove this filter">{label} ✕</button>
          {/each}
        </div>
      {/if}

      <!-- Result counts, loading and empty states are announced politely (the visible row below is not a live region). -->
      <p class="sr-only" role="status" aria-live="polite">{loading ? 'Searching…' : error ? '' : res ? (res.total ? `${num(res.total)} ${res.total === 1 ? 'post' : 'posts'}${res.total > results.length ? `, showing ${num(pageFrom)} to ${num(pageTo)}` : ''}` : 'No posts match') : ''}</p>
      {#if error}
        <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
      {:else if !res && loading}
        <Loading label="Searching…" />
      {:else if res}
        <div class="row wrap meta" class:dim={loading}>
          <span><b class="big">{num(res.total)}</b> {res.total === 1 ? 'post' : 'posts'}</span>
          {#if res.total > results.length}<span class="faint small">showing {num(pageFrom)}–{num(pageTo)}</span>{/if}
          {#if loading}<span class="spinner"></span>{/if}
          <span class="spacer"></span>
          {#if res.total}
            <a class="btn sm" href={exportHref} download title="All {num(res.total)} matching posts (up to 50,000) as CSV, including media hashes and notes. The export is recorded in the audit log.">Export CSV</a>
          {/if}
        </div>

        <SearchTerms params={params} onadd={addTerm} />

        {#if timeline && timeline.bins.length > 1}
          <div class="panel spark">
            <svg viewBox="0 0 {timeline.bins.length} 40" preserveAspectRatio="none" role="img" aria-label="Posts per {timeline.weekly ? 'week' : 'day'} from {timeline.first} to {timeline.last}, peak {timeline.max} on {timelinePeak}. The same numbers are in the table below.">
              {#each timeline.bins as b, i}
                {#if b.n}
                  <!-- Mouse shortcut only: the keyboard route is the "Show as table" list under the chart. -->
                  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
                  <rect x={i + 0.12} width="0.76" y={40 - (38 * b.n) / timeline.max} height={(38 * b.n) / timeline.max} class="barr"
                    onclick={() => set({ date_from: b.from, date_to: b.to })}>
                    <title>{b.from === b.to ? b.from : `${b.from} – ${b.to}`}: {plural(b.n, 'post')} (click to filter)</title>
                  </rect>
                {/if}
              {/each}
            </svg>
            <div class="row tiny faint"><span>{day(timeline.first)}</span><span class="spacer"></span><span>peak {num(timeline.max)} per {timeline.weekly ? 'week' : 'day'} · click a bar to filter</span><span class="spacer"></span><span>{day(timeline.last)}</span></div>
            <details class="tltable">
              <summary class="tiny">Show as table (filter by {timeline.weekly ? 'week' : 'day'})</summary>
              <div class="table-wrap" style="max-height:220px">
                <table class="t">
                  <caption class="sr-only">Posts per {timeline.weekly ? 'week' : 'day'} in these results</caption>
                  <thead><tr><th scope="col">{timeline.weekly ? 'Week' : 'Day'}</th><th scope="col" class="right">Posts</th><th scope="col"><span class="sr-only">Filter</span></th></tr></thead>
                  <tbody>
                    {#each timeline.bins.filter((b) => b.n) as b}
                      <tr><td>{b.from === b.to ? b.from : `${b.from} – ${b.to}`}</td><td class="num right">{num(b.n)}</td><td><button class="sm" onclick={() => set({ date_from: b.from, date_to: b.to })}>Filter to this {timeline.weekly ? 'week' : 'day'}</button></td></tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        {/if}

        {#if results.length}
          <div class="bulk row wrap" class:active={selIds.length}>
            <label class="check"><input type="checkbox" checked={allOnPage} onchange={toggleAll} /> {selIds.length ? `${selIds.length} selected` : 'Select page'}</label>
            {#if selIds.length}
              <span class="faint small">Set to</span>
              <button class="sm" disabled={bulkBusy} onclick={() => bulkReview('relevant')}>Relevant</button>
              <button class="sm" disabled={bulkBusy} onclick={() => bulkReview('verify')}>Needs verification</button>
              <button class="sm" disabled={bulkBusy} onclick={() => bulkReview('irrelevant')}>Not relevant</button>
              <button class="sm ghost" disabled={bulkBusy} onclick={() => bulkReview('unreviewed')}>Unreviewed</button>
              <select bind:value={bulkTag} onchange={applyBulkTag} disabled={bulkBusy} aria-label="Add a tag to the selected posts">
                <option value="">Add tag…</option>
                {#each app.tags as t (t.id)}<option value={String(t.id)}>{t.name}</option>{/each}
              </select>
              <button class="sm ghost" onclick={() => (selected = {})}>Clear selection</button>
            {:else}
              {#if prefs.shortcuts}<span class="tiny faint">Keys: <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>x</kbd> select · <kbd>o</kbd> details · <kbd>r</kbd>/<kbd>v</kbd>/<kbd>i</kbd> decide</span>{/if}
            {/if}
          </div>

          <div class="list">
            {#each results as p, i (p.id)}
              <PostCard post={p} selected={i === cursor} checked={!!selected[p.id]} terms={hlTerms}
                onselect={(x, on) => (selected[x.id] = on)}
                onopen={(x, hint) => { cursor = i; detail = { type: 'post', id: x.id, seed: x, media: hint?.sha256 || hint?.t != null ? hint : null }; }}
                isNew={!!(since && p.first_captured >= since)} />
            {/each}
          </div>

          {#if res.total > limit}
            <div class="pager row">
              <button onclick={() => page(-1)} disabled={offset === 0}>← Previous</button>
              <span class="muted small">{num(pageFrom)}–{num(pageTo)} of {num(res.total)}</span>
              <button onclick={() => page(1)} disabled={offset + limit >= res.total}>Next →</button>
            </div>
          {/if}
        {:else}
          {#if archivePosts === 0}
            <Empty title="Nothing has been collected yet">
              The archive has no posts, so there is nothing to search. Add the public channels you want to follow and collect them first.
              {#snippet action()}<a class="btn primary" href={href('add')}>Add channels</a>{/snippet}
            </Empty>
          {:else}
            <Empty title="No posts match">
              {#if chips.length}Try fewer words, a wider date range, or remove a filter.{:else}Nothing has been collected here yet.{/if}
              {#if Q.in && !inSet.includes('text') && res?.total === 0}
                <div class="small" style="margin-top:6px">This search only looked in {inSet.map((k) => IN_SOURCES.find(([x]) => x === k)?.[1] || k).join(' and ')}. Text is only available for files that were read{#if mtStatus}: {num(mtStatus.ocr?.done)} of {num(mtStatus.ocr?.eligible)} images/frames have OCR text ({num(mtStatus.ocr?.missing)} not read yet) and {num(mtStatus.speech?.done)} of {num(mtStatus.speech?.eligible)} audio/video files have a transcript ({num(mtStatus.speech?.missing)} not read yet){/if}. See <a href="#/settings?tab=mediatext">Settings, OCR &amp; transcription</a>.</div>
              {/if}
            </Empty>
          {/if}
          {#if chips.length}<div class="center"><button onclick={reset}>Reset all filters</button></div>{/if}
        {/if}
      {/if}
    </section>
  </div>
</div>

<SaveSearch truncated={!!res?.expansion?.truncated} open={saveOpen} onclose={() => (saveOpen = false)} q={Q.q || ''} params={searchParamsForOthers} />
<TriageDetailDrawer bind:item={detail} bind:open={drawerOpen} onchange={onDetailChange} />

<style>
  .searchbar { display: flex; gap: 8px; padding: 10px; align-items: center; }
  .searchbar input { flex: 1; font-size: 15px; min-width: 0; }
  .syntax { margin: 6px 2px 4px; color: var(--muted); }
  .logline { margin: 0 2px 12px; gap: 10px; align-items: center; }
  .syntax summary { cursor: pointer; }
  .syntax ul { margin: 6px 0 0; padding-left: 18px; }
  .syntax li { margin: 2px 0; }
  .layout { display: grid; grid-template-columns: 250px 1fr; gap: 14px; align-items: start; }
  .side { display: flex; flex-direction: column; gap: 10px; position: sticky; top: 12px; }
  .filters { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
  .filters .two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px; }
  .filters input, .filters select { width: 100%; min-width: 0; }
  .filters input[type=checkbox] { width: auto; }
  .filters .field { margin: 0; }
  .filters form { display: flex; flex-direction: column; gap: 8px; }
  .checks { display: flex; flex-direction: column; gap: 4px; }
  .facet { display: flex; justify-content: space-between; gap: 8px; width: 100%; border: 0; background: none; padding: 3px 6px; border-radius: 5px; font-weight: 400; color: var(--text); text-align: left; min-width: 0; }
  .facet:hover { background: var(--panel-2); }
  .facet.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .results { min-width: 0; }
  .chips { margin-bottom: 8px; gap: 6px; }
  .chips .chip { max-width: 100%; white-space: normal; text-align: start; }
  .meta { margin-bottom: 8px; gap: 10px; }
  .meta.dim { opacity: .6; }
  .big { font-size: 18px; }
  .spark { padding: 8px 10px 4px; margin-bottom: 10px; }
  .spark svg { width: 100%; height: 44px; display: block; }
  .barr { fill: var(--accent); opacity: .75; cursor: pointer; }
  .barr:hover { opacity: 1; }
  .bulk { padding: 6px 8px; border-radius: var(--radius); margin-bottom: 8px; gap: 6px; }
  .bulk.active { background: var(--accent-soft); position: sticky; top: 0; z-index: 5; }
  .list { display: flex; flex-direction: column; gap: 8px; }
  .pager { justify-content: center; margin: 14px 0; gap: 12px; }
  .center { display: flex; justify-content: center; }
  .wrap { flex-wrap: wrap; }
  .filtertoggle { display: none; margin: 0 0 10px; }
  @media (max-width: 900px) {
    .layout { grid-template-columns: 1fr; }
    .side { position: static; display: none; }
    .side.open { display: flex; }
    .filtertoggle { display: inline-flex; }
  }
</style>
