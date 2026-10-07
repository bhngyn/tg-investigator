<script>
  // Review queue: the daily triage loop. One item at a time (focus) or as a list.
  // Filters live in the URL query (kind, review, channels, media, q, new, sort, mode).
  import { onDestroy, untrack } from 'svelte';
  import { get } from '../lib/api.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import { keyOf, isInteractive, shortcutsOn } from '../lib/keys.js';
  import { scrollBehavior } from '../lib/motion.js';
  import { route, caseId, href, setQuery } from '../lib/router.svelte.js';
  import { app, scoped, sinceForCase, attempt, toast, loadCases, currentCase } from '../lib/store.svelte.js';
  import { setReview, setTag, addNote, reviewOf, tagsOf, REVIEW_LABEL } from '../lib/triage.js';
  import { num, dt, compact, ago, plural } from '../lib/fmt.js';
  import PostCard from '../components/PostCard.svelte';
  import Media from '../components/Media.svelte';
  import TagPicker from '../components/TagPicker.svelte';
  import ChannelLink from '../components/ChannelLink.svelte';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import TriageCommentCard, { senderLabel } from '../components/TriageCommentCard.svelte';
  import TriageDetailDrawer, { applyChange } from '../components/TriageDetailDrawer.svelte';

  const inCase = caseId() != null;
  const cur = $derived(currentCase());

  // ---- filters (from the URL) -------------------------------------------------------------------
  const Q = $derived(route.query);
  const kind = $derived(inCase && Q.kind === 'comments' ? 'comment' : 'post');
  const mode = $derived(Q.mode === 'list' ? 'list' : 'focus');
  const queue = $derived(['unreviewed', 'verify', 'relevant', 'irrelevant'].includes(Q.review) ? Q.review : 'unreviewed');
  // "New since last visit": the case's previous visit, or an explicit time from a link (?since=ISO).
  const since = $derived(/^\d{4}-\d\d-\d\dT/.test(Q.since || '') ? Q.since : sinceForCase());
  const newOnly = $derived((Q.new === '1' || !!Q.since) && !!since);
  const sort = $derived(Q.sort === 'newest' ? 'newest' : 'oldest');
  let qInput = $state(route.query.q || '');
  $effect(() => { const v = Q.q || ''; untrack(() => { qInput = v; }); });

  const filterKey = $derived(JSON.stringify([kind, queue, Q.channels || '', Q.media || '', Q.q || '', newOnly ? since : '', sort]));

  // ---- queue state ------------------------------------------------------------------------------
  let items = $state([]);
  let idx = $state(0);
  let total = $state(0);           // queue size at load (exact, from the server, for posts and comments alike)
  let loading = $state(true);
  let loadingMore = $state(false);
  let error = $state(null);
  let reviewedThisSession = $state(0);
  let lastAction = $state(null);   // {id, prev, next}
  let seq = 0;

  let detail = $state(null);       // drawer item
  let drawerOpen = $state(false);
  let noteText = $state('');
  let noteEl = $state();
  let focusEl = $state();
  let channelOpts = $state([]);    // [{channel, n}]

  const inQueue = (it) => reviewOf(it) === queue;
  const doneOut = $derived(items.filter((it) => !inQueue(it)).length);
  const left = $derived(Math.max(0, total - doneOut));
  const hasMore = $derived(items.filter(inQueue).length < total - doneOut);
  const current = $derived(items[idx] || null);
  const pct = $derived(total ? Math.min(100, (100 * doneOut) / total) : 0);

  function baseParams() {
    return scoped({
      channels: Q.channels || undefined,
      q: Q.q || undefined,
      since: newOnly ? since : undefined,
      sort,
      review: queue,
      ...(kind === 'post' ? { media: Q.media || undefined } : {}),
    });
  }

  // Both queues use the server's review= filter, so the count is exact and an empty queue is really empty.
  async function fetchItems(offset) {
    return get(kind === 'post' ? '/api/search' : '/api/comments', { ...baseParams(), limit: 50, offset });
  }
  async function load() {
    const my = ++seq;
    loading = true; error = null; items = []; idx = 0; total = 0; lastAction = null;
    try {
      const r = await fetchItems(0);
      if (my !== seq) return;
      items = r.results; total = r.total;
      if (kind === 'post' && !Q.channels && !inCase) channelOpts = r.facets || [];
    } catch (e) {
      if (my === seq) error = e.message;
    }
    if (my === seq) loading = false;
  }

  async function loadMore() {
    if (loadingMore || !hasMore) return false;
    loadingMore = true;
    const my = seq;
    try {
      const have = new Set(items.map((i) => i.id));
      let fresh = [];
      const r = await fetchItems(items.filter(inQueue).length);
      if (my !== seq) return false;
      fresh = r.results.filter((x) => !have.has(x.id));
      total = r.total + doneOut;   // keep "left" consistent with what the server now says
      items.push(...fresh);
      return fresh.length > 0;
    } catch (e) {
      toast(e.message, 'error');
      return false;
    } finally {
      loadingMore = false;
    }
  }

  $effect(() => { filterKey; untrack(load); });

  // Channel list for the filter: in a case, the case's channels (with unreviewed counts).
  $effect(() => {
    if (!inCase) return;
    get(`/api/cases/${caseId()}`).then((c) => {
      channelOpts = (c.channels_detail || []).map((d) => ({ channel: d.username || d.channel, n: d.unreviewed, title: d.title }));
    }).catch(() => {});
  });

  // ---- navigation -------------------------------------------------------------------------------
  async function go(delta) {
    const next = idx + delta;
    if (next < 0) return;
    if (next >= items.length) {
      if (hasMore && (await loadMore())) { idx = next; afterMove(); return; }
      idx = items.length;   // "end of queue" screen
      return;
    }
    idx = next;
    afterMove();
  }
  async function advance() {
    // next item still waiting in this queue
    for (let i = idx + 1; i < items.length; i++) if (inQueue(items[i])) { idx = i; afterMove(); return; }
    if (hasMore && (await loadMore())) {
      for (let i = idx + 1; i < items.length; i++) if (inQueue(items[i])) { idx = i; afterMove(); return; }
    }
    idx = items.length;
  }
  function afterMove() {
    noteText = '';
    document.activeElement?.blur?.();
    if (mode === 'list') {
      queueMicrotask(() => document.querySelector(`[data-${kind}-id="${items[idx]?.id}"]`)?.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() }));
    } else {
      document.querySelector('.main')?.scrollTo({ top: 0 });
    }
  }

  // ---- actions ----------------------------------------------------------------------------------
  let badgeTimer;
  function refreshBadge() { clearTimeout(badgeTimer); badgeTimer = setTimeout(loadCases, 1500); }
  onDestroy(() => clearTimeout(badgeTimer));

  let deciding = false;   // one decision at a time: a second key press must not re-send for the old item
  async function verdict(status, it = current) {
    if (!it || deciding) return;
    deciding = true;
    try { await decide(status, it); } finally { deciding = false; }
  }
  async function decide(status, it) {
    const prev = reviewOf(it);
    if (prev !== status) {
      const r = await attempt(() => setReview(kind, it.id, status));
      if (r === undefined) return;
      applyChange(it, { id: it.id, review: status });
      reviewedThisSession++;
      lastAction = { id: it.id, prev, next: status };
      refreshBadge();
      if (reviewedThisSession % 50 === 0) toast(`${reviewedThisSession} reviewed this session. A short break helps: look away from the screen for a few minutes.`, 'info', 8000);
    }
    if (it === current) await advance();
  }

  async function undo() {
    if (!lastAction) return;
    const i = items.findIndex((x) => x.id === lastAction.id);
    const it = items[i];
    if (!it) return;
    const r = await attempt(() => setReview(kind, it.id, lastAction.prev), `Back to ${REVIEW_LABEL[lastAction.prev].toLowerCase()}`);
    if (r === undefined) return;
    applyChange(it, { id: it.id, review: lastAction.prev });
    reviewedThisSession = Math.max(0, reviewedThisSession - 1);
    lastAction = null;
    idx = i;
    afterMove();
    refreshBadge();
  }

  async function toggleTag(t, it = current) {
    if (!it) return;
    const tags = tagsOf(it);
    const on = !tags.includes(t.id);
    const r = await attempt(() => setTag(kind, it.id, t.id, on));
    if (r === undefined) return;
    const next = r?.tags ? r.tags.map((x) => (typeof x === 'object' ? x.id ?? x.tag_id : x)) : on ? [...tags, t.id] : tags.filter((x) => x !== t.id);
    applyChange(it, { id: it.id, tags: next });
    toast(`${on ? 'Tagged' : 'Removed tag'}: ${t.name}`, 'ok', 1800);
  }

  async function saveNote(e) {
    e?.preventDefault();
    const it = current;
    const text = noteText.trim();
    if (!it || !text) return;
    const r = await attempt(() => addNote(kind, it.id, text, it.channel), 'Note saved');
    if (r === undefined) return;
    noteText = '';
    const field = inCase ? 'case_notes' : 'notes';
    applyChange(it, { id: it.id, notes: (Number(it[field]) || 0) + 1 });
    noteEl?.blur();
  }

  function openDetail(it = current) {
    if (!it) return;
    detail = { type: kind, id: it.id, seed: it };
  }
  function onDetailChange(ch) {
    for (const it of items) if (ch.type === kind) applyChange(it, ch);
  }

  // ---- keyboard ---------------------------------------------------------------------------------
  let lastKey = '', lastKeyAt = 0, tagTimer;
  let tagPending = $state(false);
  function onKey(e) {
    if (e.isComposing || e.keyCode === 229) return;   // IME composition
    const prevKey = lastKey, prevAt = lastKeyAt;
    lastKey = keyOf(e); lastKeyAt = Date.now();
    if (e.metaKey || e.ctrlKey || e.altKey || !shortcutsOn()) return;
    if (drawerOpen || document.querySelector('[aria-modal="true"]')) return;
    const t = e.target;
    if (t.closest?.('input, textarea, select, [contenteditable]')) {
      if (e.key === 'Escape') t.blur();
      return;
    }
    if (prevKey === 'g' && Date.now() - prevAt < 900) return;   // App's g+letter navigation
    const k = keyOf(e);   // physical key on non-Latin layouts (Arabic, Cyrillic ...)
    if (tagPending) {
      tagPending = false; clearTimeout(tagTimer);
      const tag = app.tags.find((x) => x.key && (x.key.toLowerCase() === k.toLowerCase() || x.key.toLowerCase() === (e.key || '').toLowerCase()));
      if (tag) { e.preventDefault(); toggleTag(tag); }
      else if (k !== 'Escape') toast(`No tag uses the key "${k}"`, 'info', 2000);
      return;
    }
    if (loading) return;
    if (e.repeat && !'jk'.includes(k) && !k.startsWith('Arrow')) return;   // holding a key must not decide several items
    const can = kind === 'post' || inCase;
    switch (k) {
      case 'r': if (can) verdict('relevant'); break;
      case 'v': if (can) verdict('verify'); break;
      case 'i': if (can) verdict('irrelevant'); break;
      case 'u': undo(); break;
      case 'j': e.preventDefault(); go(1); break;
      case 'k': e.preventDefault(); go(-1); break;
      // Arrows only when nothing that uses them has focus (buttons, links, tabs, inputs, scrollers).
      case 'ArrowRight': if (isInteractive(e)) return; e.preventDefault(); go(1); break;
      case 'ArrowLeft': if (isInteractive(e)) return; e.preventDefault(); go(-1); break;
      case 'n': if (current) { e.preventDefault(); if (mode === 'focus') noteEl?.focus(); else openDetail(); } break;
      case 'o': if (current) { e.preventDefault(); openDetail(); } break;
      case 't': if (current && can) { tagPending = true; clearTimeout(tagTimer); tagTimer = setTimeout(() => (tagPending = false), 2500); } break;
      case ' ': {
        if (isInteractive(e)) return;   // Space on a focused button must still press it
        e.preventDefault();
        const scope = mode === 'focus' ? focusEl : document.querySelector(`[data-${kind}-id="${current?.id}"]`);
        const veil = scope?.querySelector('.veil');
        if (veil) veil.click(); else toast('No hidden media on this item', 'info', 1500);
        break;
      }
      default: return;
    }
  }

  // ---- filter helpers ---------------------------------------------------------------------------
  const set = (patch) => setQuery(patch);
  function commitQ(e) { e?.preventDefault(); set({ q: qInput.trim() || null }); }
  const queueLabels = { unreviewed: 'To review', verify: 'Needs verification', relevant: 'Relevant (re-check)', irrelevant: 'Not relevant (re-check)' };
  const blurMode = $derived(app.settings.blur || 'all');
  const activeFilters = $derived(!!(Q.channels || Q.media || Q.q || newOnly));
</script>

<svelte:window onkeydown={onKey} />

<div class="page">
  <div class="page-head">
    <div>
      <h1>Review queue</h1>
      <p>
        {#if inCase}Posts{#if kind === 'comment'} and comments{/if} from the channels in <b>{cur?.name || 'this case'}</b>, triaged for this case only.
        {:else}Every collected post, using the archive-wide (global) triage. Pick a case to triage per case.{/if}
      </p>
    </div>
    <span class="spacer"></span>
    <div class="seg" role="group" aria-label="View">
      <button class:on={mode === 'focus'} aria-pressed={mode === 'focus'} onclick={() => set({ mode: null })} title="One item at a time">One at a time</button>
      <button class:on={mode === 'list'} aria-pressed={mode === 'list'} onclick={() => set({ mode: 'list' })} title="Scrollable list">List</button>
    </div>
  </div>

  <!-- Controls -->
  <div class="panel controls">
    <div class="row wrap">
      {#if inCase}
        <div class="seg" role="group" aria-label="Queue type">
          <button class:on={kind === 'post'} aria-pressed={kind === 'post'} onclick={() => set({ kind: null })}>Posts</button>
          <button class:on={kind === 'comment'} aria-pressed={kind === 'comment'} onclick={() => set({ kind: 'comments' })}>Comments</button>
        </div>
      {/if}
      <label class="field inline">Queue
        <select value={queue} onchange={(e) => set({ review: e.currentTarget.value === 'unreviewed' ? null : e.currentTarget.value })}>
          {#each Object.entries(queueLabels) as [v, l]}<option value={v}>{l}</option>{/each}
        </select>
      </label>
      <label class="field inline">Channel
        <select value={Q.channels || ''} onchange={(e) => set({ channels: e.currentTarget.value || null })}>
          <option value="">All channels</option>
          {#each channelOpts as c (c.channel)}<option value={c.channel}>@{c.channel}{c.n != null && kind === 'post' ? ` (${num(c.n)}${inCase ? ' to review' : ''})` : ''}</option>{/each}
          {#if Q.channels && !channelOpts.some((c) => c.channel === Q.channels)}<option value={Q.channels}>@{Q.channels}</option>{/if}
        </select>
      </label>
      {#if kind === 'post'}
        <label class="field inline">Media
          <select value={Q.media || ''} onchange={(e) => set({ media: e.currentTarget.value || null })}>
            <option value="">Any</option><option value="photo">Has photo</option><option value="video">Has video</option>
            <option value="downloaded">Media stored</option><option value="none">Text only</option>
          </select>
        </label>
      {/if}
      <form class="qform" onsubmit={commitQ}>
        <input type="search" data-page-search placeholder="Keywords (optional)" dir="auto" bind:value={qInput} onblur={commitQ} aria-label="Keywords" />
      </form>
      <label class="check" title={since ? `Collected since your previous visit (${dt(since)})` : 'Available from your second visit to this case'}>
        <input type="checkbox" checked={newOnly} disabled={!since} onchange={(e) => set({ new: e.currentTarget.checked ? '1' : null, since: null })} />
        New since last visit{#if since}<span class="tiny faint"> ({ago(since)})</span>{/if}
      </label>
      <label class="field inline">Order
        <select value={sort} onchange={(e) => set({ sort: e.currentTarget.value === 'oldest' ? null : 'newest' })}>
          <option value="oldest">Oldest first</option><option value="newest">Newest first</option>
        </select>
      </label>
      {#if activeFilters}<button class="sm ghost" onclick={() => { qInput = ''; set({ channels: null, media: null, q: null, new: null }); }}>Clear filters</button>{/if}
    </div>

    <!-- Progress -->
    <div class="progress-row">
      <div class="bar" aria-hidden="true"><i style:width="{pct}%"></i></div>
      <div class="row small" role="status" aria-live="polite">
        {#if loading}
          <span class="muted">Loading queue…</span>
        {:else}
          <b>{num(left)}</b><span class="muted">&nbsp;of {num(total)} left in “{queueLabels[queue]}”</span>
          {#if reviewedThisSession}<span class="muted">· {plural(reviewedThisSession, 'decision')} this session</span>{/if}
        {/if}
        <span class="spacer"></span>
        {#if lastAction}
          <span class="muted">Marked {REVIEW_LABEL[lastAction.next].toLowerCase()}</span>
          <button class="sm ghost" onclick={undo} title={prefs.shortcuts ? 'Undo the last decision (u)' : 'Undo the last decision'}>Undo {#if prefs.shortcuts}<kbd>u</kbd>{/if}</button>
        {/if}
      </div>
    </div>
  </div>

  <div class="care small">
    <span aria-hidden="true">◐</span>
    <span>
      Media stays {blurMode === 'none' ? 'unblurred (change this in Settings)' : blurMode === 'sensitive' ? 'blurred when marked sensitive' : 'blurred'} until you reveal it. You have revealed <b>{num(app.settings.exposure_today || 0)}</b> today.
      Pause between items when you need to, and step away for a few minutes every hour. Nothing here is timed.
    </span>
  </div>

  {#if error}
    <div class="callout bad">Could not load the queue: {error} <button class="sm" onclick={load}>Retry</button></div>
  {:else if loading}
    <Loading label="Loading the queue…" />
  {:else if !items.length}
    {#if kind === 'comment' && cur && cur.comments === 0 && !activeFilters}
      <Empty title="No comments collected for this case">
        Comments come from a channel's linked discussion group and are read with the logged-in Telegram account.
        Start a comment collection from a channel's page (Channels → a channel → Collect comments), then return here.
      </Empty>
      <div class="center"><a class="btn" href={href('channels')}>Go to channels</a></div>
    {:else if inCase && cur && cur.channels > 0 && cur.posts === 0 && kind === 'post' && !activeFilters}
      <Empty title="Nothing has been collected for this case yet">The channels are added, but no posts have been collected. Start a collection first; posts appear here afterwards.</Empty>
      <div class="center"><a class="btn primary" href={href('collect')}>Go to Collection</a></div>
    {:else if inCase && cur && cur.channels === 0}
      <Empty title="This case has no channels yet">Add channels to the case to start collecting and reviewing.</Empty>
      <div class="center"><a class="btn primary" href={href('add')}>Add channels</a></div>
    {:else}
      <Empty title={queue === 'unreviewed' ? 'Nothing waiting for review' : `Nothing in “${queueLabels[queue]}”`}>
        {#if activeFilters}No {kind === 'post' ? 'posts' : 'comments'} match these filters. Try clearing them.
        {:else if queue === 'unreviewed'}You are all caught up. New posts appear here after the next collection.{:else}Nothing has this status yet.{/if}
      </Empty>
      <div class="center row">
        {#if activeFilters}<button onclick={() => { qInput = ''; set({ channels: null, media: null, q: null, new: null }); }}>Clear filters</button>{/if}
        {#if queue === 'unreviewed'}<button onclick={() => set({ review: 'verify' })}>Open “Needs verification”</button>{/if}
        <a class="btn" href={href('search')}>Search posts</a>
      </div>
    {/if}
  {:else if mode === 'focus'}
    {#if !current}
      <Empty title={hasMore ? 'End of the loaded items' : 'End of the queue'}>
        {#if left > 0}{plural(left, 'item')} you skipped {left === 1 ? 'is' : 'are'} still waiting.{:else}Everything in this queue has a decision.{/if}
      </Empty>
      <div class="center row">
        <button onclick={() => { idx = Math.max(0, items.length - 1); }}>← Back to the last item</button>
        {#if left > 0}<button class="primary" onclick={load}>Start again from the first skipped item</button>{/if}
      </div>
    {:else}
      {@const it = current}
      {@const st = reviewOf(it)}
      <!-- keyed: page translators (Chrome Translate) replace Svelte's text nodes, so text would go stale between items -->
      {#key it.id}
      <div class="focus panel rv-border-{st}" bind:this={focusEl} data-item-id={it.id}>
        <header class="row wrap">
          {#if kind === 'post'}
            <ChannelLink name={it.channel} title={it.channel_title} />
          {:else}
            <button class="linkish" onclick={() => it.sender_type === 'user' && (detail = { type: 'account', id: it.sender_id })} disabled={it.sender_type !== 'user'}><b dir="auto">{senderLabel(it)}</b></button>
            <span class="faint small">in</span> <ChannelLink name={it.channel} showHandle={false} />
          {/if}
          <span class="faint small nowrap">{dt(it.date)}</span>
          {#if since && it.first_captured >= since}<span class="pill accent">new</span>{/if}
          {#if it.deleted_detected}<span class="pill bad">deleted</span>{/if}
          {#if it.edited || it.versions}<span class="pill warn">edited</span>{/if}
          {#if it.fwd_username || it.fwd_name}<span class="pill info">↪ <bdi dir="auto">{it.fwd_username ? '@' + it.fwd_username : it.fwd_name}</bdi></span>{/if}
          <span class="spacer"></span>
          {#if kind === 'post'}<span class="faint tiny">{compact(it.views)} views</span>{/if}
          <span class="faint tiny">item {num(idx + 1)} of {num(items.length)} loaded</span>
        </header>

        {#if it.text}
          <div class="ftext text-body" dir="auto">{it.text}</div>
        {:else}
          <div class="faint small">(no text)</div>
        {/if}
        <Media media={it.media} sensitive={it.sensitive} channel={it.channel} date={it.date} unavailable={it.media_unavailable} size="lg" max={8} />

        {#if st !== queue}
          <div class="callout ok small">Decided: <b>{REVIEW_LABEL[st]}</b>. {#if prefs.shortcuts}Press a key to change it, or <kbd>j</kbd> to move on.{:else}Choose another verdict to change it, or move on.{/if}</div>
        {/if}

        <div class="verdicts" role="group" aria-label="Decision">
          {#if kind === 'post' || inCase}
            <button class="v rv-relevant" class:on={st === 'relevant'} aria-pressed={st === 'relevant'} onclick={() => verdict('relevant')}>Relevant {#if prefs.shortcuts}<kbd>r</kbd>{/if}</button>
            <button class="v rv-verify" class:on={st === 'verify'} aria-pressed={st === 'verify'} onclick={() => verdict('verify')}>Needs verification {#if prefs.shortcuts}<kbd>v</kbd>{/if}</button>
            <button class="v rv-irrelevant" class:on={st === 'irrelevant'} aria-pressed={st === 'irrelevant'} onclick={() => verdict('irrelevant')}>Not relevant {#if prefs.shortcuts}<kbd>i</kbd>{/if}</button>
          {/if}
          <span class="spacer"></span>
          <button class="ghost" onclick={() => go(-1)} disabled={idx === 0} title={prefs.shortcuts ? 'Previous (k or ←)' : 'Previous'}>← Prev {#if prefs.shortcuts}<kbd>k</kbd>{/if}</button>
          <button class="ghost" onclick={() => go(1)} title={prefs.shortcuts ? 'Skip for now (j or →)' : 'Skip for now'}>Skip {#if prefs.shortcuts}<kbd>j</kbd>{/if} →</button>
        </div>

        <div class="tagrow">
          <TagPicker itemType={kind} id={it.id} selected={tagsOf(it)} onchange={(ids) => applyChange(it, { id: it.id, tags: ids })} />
        </div>

        <form class="notebox" onsubmit={saveNote}>
          <textarea rows="2" dir="auto" bind:this={noteEl} bind:value={noteText} placeholder="Note on this item (n to focus, ⌘/Ctrl+Enter to save, Esc to leave)"
            onkeydown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) saveNote(e); }} aria-label="Note"></textarea>
          <div class="col">
            <button class="sm" type="submit" disabled={!noteText.trim()}>Add note</button>
            {#if (inCase ? it.case_notes : it.notes)}<span class="tiny faint">✎ {inCase ? it.case_notes : it.notes} saved</span>{/if}
          </div>
        </form>

        <footer class="row small">
          <button class="sm" onclick={() => openDetail()}>Details, evidence &amp; incidents {#if prefs.shortcuts}<kbd>o</kbd>{/if}</button>
          {#if kind === 'post'}<a href={it.url} target="_blank" rel="noreferrer noopener">Open on t.me ↗</a>{/if}
        </footer>
      </div>
      {/key}
    {/if}
  {:else}
    <div class="list">
      {#each items as it, i (it.id)}
        {#if kind === 'post'}
          <PostCard post={it} selected={i === idx} onopen={(p) => { idx = i; openDetail(p); }} isNew={!!(since && it.first_captured >= since)} />
        {:else}
          <TriageCommentCard comment={it} selected={i === idx} onopen={(c) => { idx = i; openDetail(c); }} onsender={(c) => (detail = { type: 'account', id: c.sender_id })} onthread={(c) => (detail = { type: 'thread', id: `${c.group_id}:${c.thread_top_id ?? c.msg_id}`, focus: c.id })} isNew={!!(since && it.first_captured >= since)} />
        {/if}
      {/each}
      {#if hasMore}
        <div class="center"><button onclick={loadMore} disabled={loadingMore}>{loadingMore ? 'Loading…' : 'Load more'}</button></div>
      {/if}
    </div>
  {/if}

  {#if prefs.shortcuts}<details class="keys small">
    <summary>Keyboard shortcuts</summary>
    <div class="keygrid">
      <span><kbd>r</kbd> relevant</span><span><kbd>v</kbd> needs verification</span><span><kbd>i</kbd> not relevant</span><span><kbd>u</kbd> undo last decision</span>
      <span><kbd>j</kbd>/<kbd>→</kbd> next</span><span><kbd>k</kbd>/<kbd>←</kbd> previous</span><span><kbd>space</kbd> reveal next hidden media</span>
      <span><kbd>n</kbd> write a note</span><span><kbd>o</kbd> details drawer</span><span><kbd>t</kbd> then a tag key: toggle tag</span>
    </div>
    <div class="tiny faint">A decision moves you to the next item. Shortcuts pause while you type or while a drawer is open.</div>
  </details>{/if}
</div>

{#if tagPending}<div class="tagHint" role="status">Tag: press a tag key {#each app.tags.filter((t) => t.key) as t (t.id)}<span class="tk"><kbd>{t.key}</kbd> {t.name}</span>{/each}</div>{/if}

<TriageDetailDrawer bind:item={detail} bind:open={drawerOpen} onchange={onDetailChange} />

<style>
  .wrap { flex-wrap: wrap; }
  .controls { padding: 10px 12px; margin-bottom: 10px; }
  .inline { flex-direction: row; align-items: center; gap: 6px; margin: 0; }
  .qform input { width: min(100%, 190px); }
  .progress-row { margin-top: 10px; }
  .progress-row .bar { margin-bottom: 6px; }
  .care { display: flex; gap: 8px; align-items: flex-start; color: var(--muted); background: var(--info-soft); border-radius: var(--radius); padding: 8px 12px; margin-bottom: 12px; }
  .focus { padding: 14px 16px; border-left: 4px solid var(--line-strong); max-width: 900px; }
  .rv-border-relevant { border-left-color: var(--ok); }
  .rv-border-verify { border-left-color: var(--warn); }
  .focus header { margin-bottom: 10px; row-gap: 4px; }
  .ftext { font-size: 15.5px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 55vh; overflow: auto; }
  .verdicts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); }
  .v { padding: 8px 14px; font-weight: 600; }
  .v.rv-relevant.on { background: var(--ok); color: var(--on-fill); border-color: var(--ok); }
  .v.rv-verify.on { background: var(--warn); color: var(--on-fill); border-color: var(--warn); }
  .v.rv-irrelevant.on { background: var(--faint); color: var(--on-fill); }
  .tagrow { margin-top: 10px; }
  .notebox { display: flex; gap: 8px; align-items: flex-start; margin-top: 10px; }
  .notebox textarea { flex: 1; }
  .notebox .col { gap: 4px; align-items: flex-start; }
  .focus footer { margin-top: 10px; gap: 12px; }
  .list { display: flex; flex-direction: column; gap: 8px; max-width: 900px; }
  .center { display: flex; justify-content: center; gap: 8px; margin: 10px 0; }
  .keys { margin-top: 18px; color: var(--muted); max-width: 900px; }
  .keys summary { cursor: pointer; }
  .keygrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 4px 12px; margin: 8px 0 4px; }
  .tagHint { position: fixed; bottom: 60px; left: 50%; transform: translateX(-50%); z-index: 30; background: var(--panel); border: 1px solid var(--line-strong); box-shadow: var(--shadow-lg); border-radius: var(--radius); padding: 8px 12px; max-width: min(820px, 92vw); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 4px 12px; }
  .tk { white-space: nowrap; }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font-weight: 400; }
  .linkish:disabled { color: var(--text); cursor: default; }
</style>
