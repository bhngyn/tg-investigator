<script>
  import { onMount, tick, untrack } from 'svelte';
  import { route, href, hrefIn, caseId, go } from './lib/router.svelte.js';
  import { app, loadCases, loadSettings, loadTags, startPolling, enterCase, currentCase, announce } from './lib/store.svelte.js';
  import { num } from './lib/fmt.js';
  import Toasts from './components/Toasts.svelte';
  import CaseSwitcher from './components/CaseSwitcher.svelte';
  import JobsIndicator from './components/JobsIndicator.svelte';
  import { PAGES, NAV } from './pages/registry.js';
  import { anyLayerOpen } from './lib/layers.js';
  import { keyOf, shortcutsOn } from './lib/keys.js';
  import { get } from './lib/api.js';
  import { prefs } from './lib/prefs.svelte.js';

  onMount(() => {
    loadCases(); loadSettings(); loadTags(); startPolling();
  });

  // Entering a case stamps last_opened once per browser session (for "new since last visit").
  $effect(() => { const id = caseId(); if (id != null && app.casesLoaded && app.cases.some((c) => c.id === id)) enterCase(id); });

  // A case id that is not in the active list: archived, deleted or mistyped. Say so instead of "Loading…" forever.
  const caseMissing = $derived(typeof route.scope === 'number' && app.casesLoaded && !app.casesError && !cur);
  let archivedRow = $state(null);
  $effect(() => {
    if (!caseMissing) { archivedRow = null; return; }
    const id = route.scope;
    get('/api/cases', { archived: 1 }).then((rows) => { if (route.scope === id) archivedRow = (rows || []).find((c) => c.id === id && c.status === 'archived') || null; }).catch(() => {});
  });

  // A new page starts at the top (the scroll container is .main, so the browser does not do this itself).
  let mainEl = $state();
  $effect(() => { route.scope; route.page; route.args.join('/'); if (mainEl) mainEl.scrollTop = 0; });

  // Route changes for keyboard and screen-reader users: a distinct document title, focus moved to the page heading,
  // and a polite announcement ("Search posts, Sudan case").
  const LABELS = Object.fromEntries(NAV.flatMap((g) => g.items.map((i) => [i.page, i.label])));
  const pageLabel = $derived.by(() => {
    const pg = route.page;
    if (pg === 'home' || pg === 'cases') return 'All cases';
    if (pg === 'settings') return 'Settings';
    if (pg === 'channel' && route.args[0]) return '@' + route.args[0];
    if (pg === 'incident' && route.args[0]) return 'Incident ' + route.args[0];
    if (pg === 'analysis' && route.args[0]) return 'Patterns & changes: ' + route.args[0];
    if (pg === 'notfound') return 'Page not found';
    return LABELS[pg] || pg;
  });
  const scopeLabel = $derived(route.scope === 'all' ? 'Whole archive' : route.scope === null ? '' : (cur?.name || ''));
  const docTitle = $derived([pageLabel, scopeLabel, 'Telegram Investigator'].filter(Boolean).join(' · '));
  $effect(() => { document.title = docTitle; });
  let firstRoute = true;
  $effect(() => {
    route.scope; route.page; route.args.join('/');
    if (firstRoute) { firstRoute = false; return; }
    const label = untrack(() => [pageLabel, scopeLabel].filter(Boolean).join(', '));
    tick().then(() => setTimeout(() => {
      const h = mainEl?.querySelector('h1');
      const target = h || mainEl;
      if (target && !document.querySelector('[aria-modal="true"]')) {
        if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
        target.focus({ preventScroll: true });
      }
      announce(label);
    }, 50));
  });

  const Page = $derived(PAGES[route.page] || PAGES.notfound);
  const cur = $derived(currentCase());
  const inScope = $derived(route.scope !== null);
  const badge = (key) => {
    if (key === 'alerts') return app.unreadAlerts || 0;
    if (key === 'collect') return (app.jobs.running || 0) + (app.jobs.queued || 0) + (app.jobs.job ? 1 : 0);
    if (key === 'inbox' && cur) return cur.unreviewed || 0;
    return 0;
  };

  // Global shortcuts: g then i/s/c/n/a — quick page switching. "/" focuses the page search if present.
  let gPending = false;
  function onKey(e) {
    const t = e.target;
    if (e.isComposing || e.keyCode === 229) return;
    if (!shortcutsOn() || t.closest?.('input, textarea, select, [contenteditable]') || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = keyOf(e);
    if (k === '/') {
      const s = document.querySelector('[data-page-search]');
      if (s) { e.preventDefault(); s.focus(); s.select?.(); }
      return;
    }
    if (!inScope || anyLayerOpen()) return;
    if (k === 'g') { gPending = true; setTimeout(() => (gPending = false), 900); return; }
    if (gPending) {
      const map = { o: 'overview', i: 'inbox', s: 'search', c: 'comments', h: 'channels', n: 'network', a: 'analysis', l: 'alerts', j: 'collect' };
      if (map[k]) { e.preventDefault(); e.stopImmediatePropagation(); gPending = false; go(href(map[k])); }
      gPending = false;
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="shell" class:noscope={!inScope}>
  <a class="skip" href="#main" onclick={(e) => { e.preventDefault(); mainEl?.focus(); }}>Skip to main content</a>
  <header class="topbar">
    <a class="brand" href="#/" title="All cases" aria-label="Telegram Investigator: all cases">
      <span class="mark">TI</span><span class="name">Telegram Investigator</span>
    </a>
    <CaseSwitcher />
    <span class="spacer"></span>
    <JobsIndicator />
    {#if inScope}
      <a class="topicon" href={href('alerts')} title="Alerts" aria-label={app.unreadAlerts ? `Alerts, ${app.unreadAlerts} unread` : 'Alerts'} aria-current={route.page === 'alerts' ? 'page' : undefined}>
        <span aria-hidden="true">🔔</span>{#if app.unreadAlerts}<span class="badge bad">{app.unreadAlerts}</span>{/if}
      </a>
    {/if}
    <span class="exposure" title="How many times you clicked to reveal a blurred item today (media shown unblurred by your setting is not counted). Take breaks.">
      <span class="l-long">Reveal clicks today: </span><span class="l-short" aria-hidden="true">Reveals </span><b>{num(app.settings.exposure_today || 0)}</b>
    </span>
    <a class="topicon" href="#/settings" title="Settings" aria-label="Settings" class:on={route.page === 'settings'} aria-current={route.page === 'settings' ? 'page' : undefined}><span aria-hidden="true">⚙</span></a>
  </header>

  {#if inScope}
    <nav class="rail" aria-label="Case navigation">
      <a class="only-narrow" href="#/">All cases</a>
      <a class="only-narrow" href={hrefIn('all', route.page === 'channel' ? 'channels' : route.page === 'incident' ? 'incidents' : route.page)}>Whole archive</a>
      <div class="scope">
        {#if cur}
          <span class="dot" style:background={cur.color || 'var(--accent)'}></span>
          <div class="ellipsis"><b>{cur.name}</b><div class="tiny faint">{num(cur.channels)} channels · {num(cur.posts)} posts</div></div>
        {:else if route.scope === 'all'}
          <span class="dot all"></span>
          <div><b>Whole archive</b><div class="tiny faint">All channels, global triage</div></div>
        {:else if caseMissing}
          <div class="small"><b>Case not found</b><div class="tiny faint">Archived, deleted or mistyped</div></div>
        {:else}
          <div class="faint small">Loading case…</div>
        {/if}
      </div>
      {#each NAV as group}
        <div class="group">{group.label}</div>
        {#each group.items as it}
          {@const n = badge(it.page)}
          <a href={href(it.page)} class:on={route.page === it.page || it.also?.includes(route.page)} aria-current={route.page === it.page || it.also?.includes(route.page) ? 'page' : undefined}>
            <span class="ico" aria-hidden="true">{it.icon}</span><span class="lbl">{it.label}</span>
            {#if n}<span class="badge" class:bad={it.page === 'alerts'} class:muted={it.page === 'inbox'}>{n > 999 ? '999+' : n}</span>{/if}
          </a>
        {/each}
      {/each}
      <div class="railfoot tiny faint">
        <a href="#/">← All cases</a>
        {#if cur}<span> · </span><a href={hrefIn('all', route.page === 'channel' ? 'channels' : route.page)}>Whole archive</a>{/if}
        {#if prefs.shortcuts}<div class="keys">Press <kbd>g</kbd> then <kbd>i</kbd> inbox · <kbd>s</kbd> search · <kbd>h</kbd> channels</div>{/if}
      </div>
    </nav>
  {/if}

  <main class="main" id="main" tabindex="-1" bind:this={mainEl}>
    {#if app.stale}
      <div class="callout bad banner" role="alert"><b>The server restarted.</b> Reload the page to continue; changes made now would not be saved. <button class="sm" onclick={() => location.reload()}>Reload</button></div>
    {:else if app.offline}
      <div class="callout warn banner" role="alert"><b>Cannot reach the local server.</b> Numbers on screen may be out of date. Retrying automatically; reload the page if it stays like this.</div>
    {/if}
    {#if caseMissing}
      <div class="page">
        <div class="empty">
          <h3>{archivedRow ? 'This case is archived' : 'Case not found'}</h3>
          <p class="muted">{archivedRow ? `“${archivedRow.name}” is archived, so it is hidden from the case list. Restore it from All cases (Show archived cases).` : `There is no active case number ${route.scope}. The link may be mistyped, or the case was deleted.`}</p>
          <p><a href="#/">Back to all cases</a></p>
        </div>
      </div>
    {:else}
      {#key route.scope + '|' + route.page + '|' + route.args.join('/')}
        <Page />
      {/key}
    {/if}
  </main>
</div>

<Toasts />
<!-- Outside .shell so it keeps working while a dialog makes the shell inert. -->
<div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{app.announcement}</div>

<style>
  .shell { display: grid; grid-template-columns: var(--rail-w) 1fr; grid-template-rows: var(--topbar-h) 1fr; height: 100vh; height: 100dvh; }
  .shell.noscope { grid-template-columns: 1fr; }
  .topbar { grid-column: 1 / -1; display: flex; align-items: center; gap: 12px; padding: 0 14px; background: var(--panel); border-bottom: 1px solid var(--line); z-index: 20; min-width: 0; }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--text); font-weight: 700; text-decoration: none; white-space: nowrap; }
  .mark { width: 28px; height: 28px; border-radius: 7px; background: var(--accent); color: var(--on-fill); display: grid; place-items: center; font-size: 12px; letter-spacing: .02em; }
  .spacer { flex: 1; }
  .topicon { position: relative; font-size: 16px; padding: 4px 6px; border-radius: 6px; color: var(--muted); text-decoration: none; display: inline-flex; gap: 3px; align-items: center; }
  .topicon:hover, .topicon.on { background: var(--panel-2); text-decoration: none; }
  .topicon .badge { font-size: 10px; }
  .exposure { font-size: 12px; color: var(--muted); white-space: nowrap; }
  .l-short { display: none; }
  .only-narrow { display: none !important; }
  .main:focus { outline: none; }
  .main :global(h1:focus) { outline: none; }
  .rail { background: var(--panel); border-right: 1px solid var(--line); overflow-y: auto; padding: 10px 8px 16px; display: flex; flex-direction: column; gap: 1px; }
  .scope { display: flex; gap: 8px; align-items: flex-start; padding: 6px 8px 10px; border-bottom: 1px solid var(--line); margin-bottom: 4px; min-width: 0; }
  .scope .dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 4px; flex: none; }
  .scope .dot.all { background: conic-gradient(var(--accent), var(--ok), var(--warn), var(--accent)); }
  .group { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--faint); font-weight: 700; padding: 12px 8px 4px; }
  .rail a { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 6px; color: var(--text); text-decoration: none; font-size: 13.5px; }
  .rail a:hover { background: var(--panel-2); }
  .rail a.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .ico { width: 18px; text-align: center; opacity: .85; }
  .lbl { flex: 1; }
  .railfoot { margin-top: auto; padding: 14px 8px 0; }
  .railfoot a { display: inline; padding: 0; }
  .keys { margin-top: 8px; line-height: 1.8; }
  .main { overflow-y: auto; min-width: 0; position: relative; }
  .banner { margin: 10px 16px 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

  @media (max-width: 980px) {
    .name { display: none; }
    /* The exposure meter is a wellbeing feature: it stays visible at every width, in a compact form. */
    .l-long { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
    .l-short { display: inline; }
  }
  @media (max-width: 760px) {
    .shell { grid-template-columns: 1fr; grid-template-rows: auto auto 1fr; }
    .topbar { flex-wrap: wrap; gap: 4px 8px; padding: 6px 8px; min-height: var(--topbar-h); }
    .topbar .spacer { display: none; }
    .rail { -webkit-mask-image: linear-gradient(90deg, #000 94%, transparent); mask-image: linear-gradient(90deg, #000 94%, transparent); }
    .rail a.only-narrow { display: flex !important; }
    .rail { flex-direction: row; overflow-x: auto; overflow-y: hidden; border-right: 0; border-bottom: 1px solid var(--line); padding: 4px 6px; gap: 2px; }
    .rail .scope, .rail .group, .rail .railfoot { display: none; }
    .rail a { white-space: nowrap; flex: none; }
  }
</style>
