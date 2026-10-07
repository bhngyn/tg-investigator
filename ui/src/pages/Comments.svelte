<script>
  // Comments & people: comments from channels' linked discussion groups (read with the logged-in
  // account), the comment sections collected so far, and a light "who comments" view.
  // Tabs and filters live in the URL query (tab, q, channels, sender, post, …).
  import { untrack } from 'svelte';
  import { get, csvUrl } from '../lib/api.js';
  import { route, caseId, href, setQuery } from '../lib/router.svelte.js';
  import { app, scoped, sinceForCase, attempt, loadCases } from '../lib/store.svelte.js';
  import { setReview, REVIEW_LABEL } from '../lib/triage.js';
  import { num, dt, day, ago, plural } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import ChannelLink from '../components/ChannelLink.svelte';
  import TriagePhone from '../components/TriagePhone.svelte';
  import TriageCommentCard from '../components/TriageCommentCard.svelte';
  import CommentThread, { threadOf } from '../components/CommentThread.svelte';
  import TriageDetailDrawer, { applyChange } from '../components/TriageDetailDrawer.svelte';
  import ExpansionChips from '../components/ExpansionChips.svelte';
  import DictBanner from '../components/DictBanner.svelte';
  import { expansionFromQuery, expansionToQuery } from '../lib/expansion.js';
  import SearchTerms from '../components/SearchTerms.svelte';
  import SearchFacets from '../components/SearchFacets.svelte';
  import { listOf, COMMENT_IN_SOURCES, DEFAULT_COMMENT_IN } from '../lib/searchparams.js';

  const inCase = caseId() != null;
  const Q = $derived(route.query);
  const tab = $derived(['sections', 'people'].includes(Q.tab) ? Q.tab : 'comments');
  const since = $derived(/^\d{4}-\d\d-\d\dT/.test(Q.since || '') ? Q.since : sinceForCase());
  const newOnly = $derived((Q.new === '1' || !!Q.since) && !!since);
  const threaded = $derived(Q.view === 'threads');
  const limit = $derived(threaded ? 10 : 50);
  const offset = $derived(Math.max(0, Number(Q.offset) || 0));

  let detail = $state(null);
  let drawerOpen = $state(false);

  // ---- comment sections (shared by all tabs: decides the empty state) ----------------------------
  let groups = $state(null);
  let caseChannels = $state(null);   // Set of the case's channels, or null in "all"
  let groupsError = $state(null);
  async function loadGroups() {
    try {
      const [g, c] = await Promise.all([get('/api/comment_groups'), inCase ? get(`/api/cases/${caseId()}`) : null]);
      caseChannels = c ? new Set((c.channels_detail || []).map((d) => d.channel || d.username)) : null;
      groups = caseChannels ? g.filter((x) => caseChannels.has(x.channel)) : g;
    } catch (e) { groupsError = e.message; groups = []; }
  }
  loadGroups();
  const noSections = $derived(groups !== null && groups.length === 0);

  // ---- comments tab ------------------------------------------------------------------------------
  const FILTERS = ['q', 'channels', 'sender', 'post', 'date_from', 'date_to', 'deleted', 'forwarded', 'edited', 'media', 'sender_type', 'admins', 'mirrors', 'sort', 'expand', 'drop', 'skip', 'in', 'place', 'unambiguous'];
  const params = $derived.by(() => {
    const p = {};
    for (const k of FILTERS) if (Q[k]) p[k] = Q[k];
    if (newOnly) p.since = since;
    return scoped(p);
  });
  const plFacet = $derived(listOf(res?.places, 'places'));
  // "Search in": the comment's own words and the OCR / speech text read from its media (defaults on; names opt-in)
  const srcAvail = $derived(Array.isArray(res?.sources?.available) ? res.sources.available : null);
  const inSet = $derived(Q.in ? Q.in.split(',').filter(Boolean) : DEFAULT_COMMENT_IN);
  function toggleSource(k, on) {
    const next = COMMENT_IN_SOURCES.map(([x]) => x).filter((x) => (x === k ? on : inSet.includes(x)));
    if (!next.length) return;                         // at least one source must stay on
    const isDefault = next.length === DEFAULT_COMMENT_IN.length && DEFAULT_COMMENT_IN.every((x) => next.includes(x));
    set({ in: isDefault ? null : next.join(',') });
  }
  const plcs = $derived(Q.place ? Q.place.split(',').filter(Boolean) : []);
  let qInput = $state(route.query.q || '');
  $effect(() => { const v = Q.q || ''; untrack(() => { qInput = v; }); });
  let res = $state(null);
  let loading = $state(false);
  let error = $state(null);
  let selected = $state({});
  let seq = 0;
  async function loadComments() {
    const my = ++seq;
    loading = true; error = null;
    try {
      const r = await get(threaded ? '/api/comment_threads' : '/api/comments', { ...params, limit, offset });
      if (my !== seq) return;
      res = r; selected = {};
    } catch (e) { if (my === seq) error = e.message; }
    if (my === seq) loading = false;
  }
  const key = $derived(tab === 'comments' ? JSON.stringify([params, offset, threaded]) : null);
  $effect(() => { if (key) untrack(loadComments); });

  // Who is `sender=`? (for the filter chip)
  let senderInfo = $state(null);
  $effect(() => {
    const s = Q.sender;
    senderInfo = null;
    if (s && /^\d+$/.test(s)) get(`/api/tg_users/${s}`).then((u) => { if (Q.sender === s) senderInfo = u; }).catch(() => {});
  });
  const senderName = $derived(senderInfo ? [senderInfo.first_name, senderInfo.last_name].filter(Boolean).join(' ') || (senderInfo.username ? '@' + senderInfo.username : `user ${senderInfo.user_id}`) : /^\d+$/.test(Q.sender || '') ? `account ${Q.sender}` : '');

  function set(patch) { setQuery({ ...patch, offset: null }); }
  function commitQ(e) { e?.preventDefault(); set({ q: qInput.trim() || null }); }
  const active = $derived(FILTERS.some((k) => !['sort', 'expand', 'drop', 'skip', 'in'].includes(k) && Q[k]) || Q.new === '1');
  function reset() {
    qInput = '';
    const p = { new: null };
    for (const k of FILTERS) p[k] = null;
    set(p);
  }
  const expState = $derived(expansionFromQuery(Q));
  // words to highlight in comments: the query minus operators, plus the dictionary's other spellings (the response's `terms`)
  const hlTerms = $derived.by(() => {
    const own = (Q.q || '').replace(/["()*]/g, ' ').split(/\s+/).filter((w) => w && !/^(OR|AND|NOT|NEAR(\/\d+)?)$/.test(w) && !w.startsWith('-') && !w.includes(':'));
    return [...new Set([...own, ...(res?.terms || [])])].sort((a, b) => b.length - a.length);
  });
  function addTerm(term, op) {
    const cur = (Q.q || '').trim();
    const t = /[\s"]/.test(term) ? `"${term.replace(/"/g, '')}"` : term;
    const next = !cur ? t : op === 'or' ? `${cur} OR ${t}` : `${cur} ${t}`;
    qInput = next;
    set({ q: next });
  }
  const results = $derived(res?.results || []);
  const threads = $derived(res?.threads || []);
  // In threads view, comments that matched the filters stand out; the rest of the thread is context.
  const filtering = $derived(FILTERS.some((k) => !['sort', 'channels', 'mirrors', 'expand', 'drop', 'skip', 'in'].includes(k) && Q[k]) || newOnly);
  const openThread = (c) => (detail = { type: 'thread', id: `${threadOf(c).groupId}:${threadOf(c).top}`, focus: c.id });
  const selIds = $derived(Object.keys(selected).filter((k) => selected[k]).map(Number));
  let busy = $state(false);
  async function bulk(status) {
    const ids = selIds;
    busy = true;
    const r = await attempt(() => setReview('comment', ids, status), `${plural(ids.length, 'comment')} → ${REVIEW_LABEL[status]}`);
    busy = false;
    if (r === undefined) return;
    for (const c of results) if (selected[c.id]) applyChange(c, { id: c.id, review: status });
    selected = {};
    loadCases();
  }
  function page(d) { setQuery({ offset: Math.max(0, offset + d * limit) || null }); document.querySelector('.main')?.scrollTo({ top: 0 }); }
  const exportHref = $derived(csvUrl('/api/comments/export.csv', params));

  // ---- people tab --------------------------------------------------------------------------------
  let community = $state(null);
  let communityError = $state(null);
  $effect(() => {
    if (tab !== 'people' || community) return;
    get('/api/community', scoped({})).then((r) => (community = r)).catch((e) => (communityError = e.message));
  });

  const openAccount = (uid) => (detail = { type: 'account', id: uid });
  function onDetailChange(ch) { for (const c of results) applyChange(c, ch); }
</script>

<div class="page wide">
  <div class="page-head">
    <div>
      <h1>Comments &amp; people</h1>
      <p>Comments left in channels' linked discussion groups, and the accounts that wrote them. {inCase ? 'Limited to this case’s channels; triage is per case.' : 'Whole archive. Pick a case to triage comments.'}</p>
    </div>
  </div>

  <div class="tabs">
    <a href={href('comments', { ...Q, tab: null, offset: null })} class:on={tab === 'comments'} aria-current={tab === 'comments' ? 'page' : undefined}>Comments{#if res && tab === 'comments' && !threaded} <span class="badge muted">{num(res.total)}</span>{/if}</a>
    <a href={href('comments', { tab: 'sections' })} class:on={tab === 'sections'} aria-current={tab === 'sections' ? 'page' : undefined}>Comment sections{#if groups} <span class="badge muted">{groups.length}</span>{/if}</a>
    <a href={href('comments', { tab: 'people' })} class:on={tab === 'people'} aria-current={tab === 'people' ? 'page' : undefined}>People</a>
  </div>

  {#if noSections && tab !== 'sections'}
    <div class="callout">
      <b>No comment sections collected {inCase ? 'for this case' : 'yet'}.</b>
      Comments are read from a channel's linked discussion group with the logged-in Telegram account, so nothing appears here until a comment collection has run.
      Open a channel and choose <b>Collect comments</b> (you'll be asked to confirm before anything runs).
      <div class="row mt"><a class="btn sm primary" href={href('channels')}>Choose a channel</a><a class="btn sm ghost" href={href('collect')}>Collection status</a></div>
    </div>
  {/if}

  {#if tab === 'comments'}
    <form class="panel searchbar" onsubmit={commitQ}>
      <input type="search" data-page-search dir="auto" bind:value={qInput} placeholder='Search comment text ("phrase", OR, -exclude)' aria-label="Search comments" />
      <select value={Q.sort || ''} onchange={(e) => set({ sort: e.currentTarget.value || null })} aria-label="Sort">
        <option value="">Newest first</option><option value="oldest">Oldest first</option>
      </select>
      <button class="primary" type="submit">Search</button>
    </form>
    <DictBanner dictionary={res?.expansion?.dictionary} />
    <ExpansionChips expansion={res?.expansion} value={expState} {loading} onchange={(v) => set(expansionToQuery(v))} />
    <SearchTerms params={params} scope="comments" onadd={addTerm} />

    <div class="row viewbar">
      <div class="seg" role="group" aria-label="View">
        <button class:on={!threaded} aria-pressed={!threaded} onclick={() => set({ view: null })}>List</button>
        <button class:on={threaded} aria-pressed={threaded} onclick={() => set({ view: 'threads' })} title="Group comments under the post they were left on, nested by reply">Threads</button>
      </div>
      {#if threaded}<span class="small faint">Each post's conversation, nested by reply.{#if filtering} Comments matching your filters are highlighted; the rest are shown dimmed for context.{/if}</span>{/if}
    </div>

    <div class="panel filterbar row wrap">
      <label class="field inline">Section
        <select value={Q.channels || ''} onchange={(e) => set({ channels: e.currentTarget.value || null })}>
          <option value="">All sections</option>
          {#each groups || [] as g (g.group_id)}<option value={g.channel}>@{g.channel} ({num(g.comments)})</option>{/each}
          {#if Q.channels && !(groups || []).some((g) => g.channel === Q.channels)}<option value={Q.channels}>@{Q.channels}</option>{/if}
        </select>
      </label>
      <label class="field inline">Sender
        <select value={Q.sender_type || ''} onchange={(e) => set({ sender_type: e.currentTarget.value || null })}>
          <option value="">Anyone</option><option value="user">Personal accounts</option><option value="channel">Posting as a channel</option><option value="group">Anonymous admins</option>
        </select>
      </label>
      <label class="field inline">From <input type="date" value={Q.date_from || ''} onchange={(e) => set({ date_from: e.currentTarget.value || null })} /></label>
      <label class="field inline">To <input type="date" value={Q.date_to || ''} onchange={(e) => set({ date_to: e.currentTarget.value || null })} /></label>
      <label class="check"><input type="checkbox" checked={Q.admins === '1'} onchange={(e) => set({ admins: e.currentTarget.checked ? '1' : null })} /> By group admins</label>
      <label class="check"><input type="checkbox" checked={Q.deleted === '1'} onchange={(e) => set({ deleted: e.currentTarget.checked ? '1' : null })} /> Deleted</label>
      <label class="check"><input type="checkbox" checked={Q.edited === '1'} onchange={(e) => set({ edited: e.currentTarget.checked ? '1' : null })} /> Edited</label>
      <label class="check"><input type="checkbox" checked={Q.forwarded === '1'} onchange={(e) => set({ forwarded: e.currentTarget.checked ? '1' : null })} /> Forwards</label>
      <label class="check"><input type="checkbox" checked={Q.media === '1'} onchange={(e) => set({ media: e.currentTarget.checked ? '1' : null })} /> With media</label>
      {#each COMMENT_IN_SOURCES as [k, l]}
        {@const off = !!srcAvail && !srcAvail.includes(k)}
        <label class="check" class:faint={off} title={off ? 'Nothing read from comment media yet: run OCR & transcription (Settings) on comment sections' : k === 'text' ? 'Search in the comment words' : k === 'names' ? 'Match a sender or channel name instead of the words' : 'Machine-read text of images and video attached to comments; only matters when there are words in the search box'}><input type="checkbox" checked={inSet.includes(k) && !off} disabled={off} onchange={(e) => toggleSource(k, e.currentTarget.checked)} /> {l}</label>
      {/each}
      {#if !threaded}<label class="check" title="The automatic copies of channel posts that comment threads hang off"><input type="checkbox" checked={Q.mirrors === '1'} onchange={(e) => set({ mirrors: e.currentTarget.checked ? '1' : null })} /> Include post copies</label>{/if}
      {#if inCase}
        <label class="check" title={since ? `Collected since ${dt(since)}` : 'Available from your second visit to this case'}>
          <input type="checkbox" checked={newOnly} disabled={!since} onchange={(e) => set({ new: e.currentTarget.checked ? '1' : null, since: null })} /> New since last visit
        </label>
      {/if}
      {#if active}<button class="sm ghost" onclick={reset}>Reset</button>{/if}
    </div>

    {#if plFacet.length || Q.place || Q.unambiguous}
      <SearchFacets places={plFacet} activePlaces={plcs} showMapLink={false} {loading} unambiguous={Q.unambiguous === '1'} onunambiguous={(on) => set({ unambiguous: on ? '1' : null })}
        onplace={(id) => set({ place: (plcs.includes(id) ? plcs.filter((x) => x !== id) : [...plcs, id]).join(',') || null })} />
    {/if}

    {#if Q.sender || Q.post}
      <div class="row wrap chips">
        {#if Q.sender}
          <span class="chip on">Account: <button class="linkish" onclick={() => openAccount(Number(Q.sender))}>{senderName}</button>
            <button class="x" onclick={() => set({ sender: null })} aria-label="Remove account filter">✕</button></span>
        {/if}
        {#if Q.post}<span class="chip on">Under post id {Q.post} <button class="x" onclick={() => set({ post: null })} aria-label="Remove post filter">✕</button></span>{/if}
      </div>
    {/if}

    <p class="sr-only" role="status" aria-live="polite">{loading ? 'Loading…' : error ? '' : res ? (res.total ? `${num(res.total)} ${threaded ? (res.total === 1 ? 'thread' : 'threads') : res.total === 1 ? 'comment' : 'comments'}` : 'Nothing matches') : ''}</p>
    {#if error}
      <div class="callout bad" role="alert">{error} <button class="sm" onclick={loadComments}>Retry</button></div>
    {:else if !res && loading}
      <Loading />
    {:else if res}
      <div class="row wrap meta" class:dim={loading}>
        <span><b class="big">{num(res.total)}</b> {threaded ? (res.total === 1 ? 'thread' : 'threads') : res.total === 1 ? 'comment' : 'comments'}</span>
        {#if res.total > limit}<span class="faint small">showing {num(offset + 1)}–{num(Math.min(res.total, offset + (threaded ? threads.length : results.length)))}</span>{/if}
        <span class="spacer"></span>
        {#if res.total}<a class="btn sm" href={exportHref} download title="Includes sender IDs, usernames and phone numbers where collected. Recorded in the audit log.">Export CSV</a>{/if}
      </div>

      {#if threaded}
        {#if threads.length}
          <div class="list">
            {#each threads as th (th.group_id + ':' + th.top)}
              <CommentThread groupId={th.group_id} top={th.top} preload={th} match={filtering ? new Set(th.ids) : null} cap={25} terms={hlTerms}
                onopen={(x) => (detail = x)} onsender={(x) => openAccount(x.sender_id)} />
            {/each}
          </div>
        {:else if !noSections}
          <Empty title="No threads match">{#if active}Try removing a filter.{:else}The collected sections have no comments yet.{/if}</Empty>
          {#if active}<div class="center"><button onclick={reset}>Reset filters</button></div>{/if}
        {/if}
        {#if res.total > limit}
          <div class="pager row">
            <button onclick={() => page(-1)} disabled={offset === 0}>← Previous</button>
            <span class="muted small">{num(offset + 1)}–{num(Math.min(res.total, offset + threads.length))} of {num(res.total)} threads</span>
            <button onclick={() => page(1)} disabled={offset + limit >= res.total}>Next →</button>
          </div>
        {/if}
      {:else if results.length}
        {#if inCase}
          <div class="bulk row wrap" class:active={selIds.length}>
            <label class="check"><input type="checkbox" checked={results.length > 0 && results.every((c) => selected[c.id])}
              onchange={(e) => (selected = e.currentTarget.checked ? Object.fromEntries(results.map((c) => [c.id, true])) : {})} /> {selIds.length ? `${selIds.length} selected` : 'Select page'}</label>
            {#if selIds.length}
              <button class="sm" disabled={busy} onclick={() => bulk('relevant')}>Relevant</button>
              <button class="sm" disabled={busy} onclick={() => bulk('verify')}>Needs verification</button>
              <button class="sm" disabled={busy} onclick={() => bulk('irrelevant')}>Not relevant</button>
              <button class="sm ghost" onclick={() => (selected = {})}>Clear</button>
            {/if}
            <span class="spacer"></span>
            <a class="small" href={href('inbox', { kind: 'comments' })}>Review comments one at a time →</a>
          </div>
        {/if}
        <div class="list">
          {#each results as c (c.id)}
            <TriageCommentCard comment={c} terms={hlTerms} checked={!!selected[c.id]} onselect={(x, on) => (selected[x.id] = on)}
              onopen={(x, hint) => (detail = { type: 'comment', id: x.id, seed: x, media: hint })} onsender={(x) => openAccount(x.sender_id)} onthread={openThread}
              isNew={!!(since && c.first_captured >= since)} />
          {/each}
        </div>
        {#if res.total > limit}
          <div class="pager row">
            <button onclick={() => page(-1)} disabled={offset === 0}>← Previous</button>
            <span class="muted small">{num(offset + 1)}–{num(Math.min(res.total, offset + results.length))} of {num(res.total)}</span>
            <button onclick={() => page(1)} disabled={offset + limit >= res.total}>Next →</button>
          </div>
        {/if}
      {:else if !noSections}
        <Empty title="No comments match">{#if active}Try removing a filter.{:else}The collected sections have no comments yet.{/if}</Empty>
        {#if active}<div class="center"><button onclick={reset}>Reset filters</button></div>{/if}
      {/if}
    {/if}

  {:else if tab === 'sections'}
    {#if groupsError}
      <div class="callout bad" role="alert">{groupsError}</div>
    {:else if groups === null}
      <Loading />
    {:else if groups.length}
      <div class="table-wrap panel">
        <table class="t">
          <thead><tr><th scope="col">Channel</th><th scope="col">Discussion group</th><th scope="col" class="num">Comments</th><th scope="col" class="num">Members</th><th scope="col">First collected</th><th scope="col">Last checked</th><th scope="col"></th></tr></thead>
          <tbody>
            {#each groups as g (g.group_id)}
              <tr>
                <td><ChannelLink name={g.channel} /></td>
                <td><span dir="auto">{g.title || '–'}</span>{#if g.username}<div class="tiny faint"><bdi dir="ltr">@{g.username}</bdi></div>{/if}</td>
                <td class="num">{num(g.comments)}</td>
                <td class="num">{num(g.participants)}{#if g.hidden_members}<div class="tiny faint">list hidden</div>{/if}</td>
                <td class="nowrap">{day(g.first_seen)}</td>
                <td class="nowrap" title={dt(g.last_checked)}>{ago(g.last_checked)}</td>
                <td class="nowrap">
                  <a class="btn sm" href={href('comments', { channels: g.channel })}>Comments</a>
                  <a class="btn sm ghost" href={href('channel', g.channel)}>Channel</a>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="small faint">New comments are picked up by comment collections and schedules, started from each channel's page. Nothing on this page contacts Telegram.</p>
    {:else}
      <Empty title="No comment sections yet">Collect comments from a channel's page to see its discussion group here.</Empty>
      <div class="center"><a class="btn primary" href={href('channels')}>Go to channels</a></div>
    {/if}

  {:else}
    {#if communityError}
      <div class="callout bad" role="alert">{communityError}</div>
    {:else if !community}
      <Loading />
    {:else if !community.summary?.comments}
      {#if !noSections}<Empty title="No commenters yet">Once comments are collected, the most active accounts appear here.</Empty>{/if}
    {:else}
      {@const s = community.summary}
      <div class="stats">
        <div class="stat"><div class="v">{num(s.accounts)}</div><div class="l">accounts commenting</div></div>
        <div class="stat"><div class="v">{num(s.comments)}</div><div class="l">comments</div></div>
        <div class="stat"><div class="v">{Math.round((s.top10_share || 0) * 100)}%</div><div class="l">written by the top 10</div></div>
        <div class="stat"><div class="v">{num(s.in_other_sections)}</div><div class="l">also comment in other sections</div></div>
        <div class="stat"><div class="v">{num(s.as_channel)}</div><div class="l">posted as a channel</div></div>
        <div class="stat"><div class="v">{num(s.deleted)}</div><div class="l">found deleted</div></div>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Most active accounts</h3><span class="spacer"></span>
          <a class="small" href={href('analysis', 'people')}>Full overlap analysis (shared commenters, admins, forward sources) →</a></div>
        <div class="table-wrap">
          <table class="t">
            <thead><tr><th scope="col">Account</th><th scope="col" class="num">Comments</th><th scope="col" class="num">Sections</th><th scope="col">Phone</th><th scope="col"></th></tr></thead>
            <tbody>
              {#each community.top as a (a.user_id)}
                <tr>
                  <td>
                    <button class="linkish" onclick={() => openAccount(a.user_id)}><b dir="auto">{a.name || (a.username ? '@' + a.username : 'user ' + a.user_id)}</b></button>
                    {#if a.username && a.name}<span class="tiny faint"><bdi dir="ltr">@{a.username}</bdi></span>{/if}
                    {#if a.admin}<span class="pill accent">admin</span>{/if}{#if a.bot}<span class="pill">bot</span>{/if}
                  </td>
                  <td class="num">{num(a.comments)}</td>
                  <td class="num">{num(a.sections)}</td>
                  <td><TriagePhone phone={a.phone} country={a.phone_country} /></td>
                  <td class="nowrap"><a class="btn sm ghost" href={href('comments', { sender: a.user_id })}>Comments</a></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
      <p class="small faint">Account data is what Telegram showed the logged-in account at collection time. Phone numbers appear only where the user's privacy settings allowed it; treat them as personal data.</p>
    {/if}
  {/if}
</div>

<TriageDetailDrawer bind:item={detail} bind:open={drawerOpen} onchange={onDetailChange} />

<style>
  .mt { margin-top: 8px; }
  .wrap { flex-wrap: wrap; }
  .tabs { margin-bottom: 12px; }
  .searchbar { display: flex; gap: 8px; padding: 10px; align-items: center; margin-bottom: 8px; }
  .searchbar input { flex: 1; font-size: 15px; min-width: 0; }
  .filterbar { padding: 8px 12px; margin-bottom: 10px; gap: 8px 14px; }
  .inline { flex-direction: row; align-items: center; gap: 6px; margin: 0; }
  .chips { margin-bottom: 8px; gap: 6px; }
  .chip .x { border: 0; background: none; padding: 0 0 0 4px; color: inherit; cursor: pointer; }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font-weight: 400; text-align: start; }
  .meta { margin-bottom: 8px; gap: 10px; }
  .viewbar { gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
  .seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: var(--radius-sm); overflow: hidden; }
  .seg button { border: 0; border-radius: 0; background: none; padding: 4px 12px; font-weight: 500; }
  .seg button + button { border-left: 1px solid var(--line-strong); }
  .seg button.on { background: var(--accent-soft); color: var(--accent); }
  .meta.dim { opacity: .6; }
  .big { font-size: 18px; }
  .bulk { padding: 6px 8px; border-radius: var(--radius); margin-bottom: 8px; gap: 6px; }
  .bulk.active { background: var(--accent-soft); position: sticky; top: 0; z-index: 5; }
  .list { display: flex; flex-direction: column; gap: 8px; max-width: 980px; }
  .pager { justify-content: center; margin: 14px 0; gap: 12px; }
  .center { display: flex; justify-content: center; margin: 8px 0; }
  .stats { margin-bottom: 12px; }
</style>
