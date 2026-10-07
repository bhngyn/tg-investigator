<script>
  // Top-bar case switcher: pick a case (scopes every page) or the whole archive.
  import { app, currentCase } from '../lib/store.svelte.js';
  import { route, hrefIn, go } from '../lib/router.svelte.js';
  import { num, ago } from '../lib/fmt.js';

  let open = $state(false);
  let filter = $state('');
  let input = $state();
  const cur = $derived(currentCase());
  const list = $derived(app.cases.filter((c) => !filter || c.name.toLowerCase().includes(filter.toLowerCase())));

  // Keep the same page when switching scope where it makes sense.
  const SCOPELESS = ['channel', 'incident'];
  function target(scope) {
    const page = route.scope === null || SCOPELESS.includes(route.page) ? 'overview' : route.page;
    return hrefIn(scope, page);
  }
  function pick(scope) { open = false; filter = ''; go(target(scope)); }
  function toggle() { open = !open; if (open) setTimeout(() => input?.focus(), 0); }
  function key(e) {
    if (e.key === 'Escape') open = false;
    if (e.key === 'Enter' && list[0]) pick(list[0].id);
  }
</script>

<svelte:window onclick={(e) => { if (open && !e.target.closest?.('.switcher')) open = false; }} />

<div class="switcher">
  <button class="current" onclick={toggle} aria-haspopup="listbox" aria-expanded={open}>
    {#if cur}
      <span class="dot" style:background={cur.color || 'var(--accent)'}></span><span class="ellipsis">{cur.name}</span>
    {:else if route.scope === 'all'}
      <span class="dot all"></span><span>Whole archive</span>
    {:else}
      <span class="muted">Choose a case…</span>
    {/if}
    <span class="caret">▾</span>
  </button>
  {#if open}
    <div class="menu panel" role="listbox">
      <input type="search" placeholder="Find a case…" aria-label="Find a case" bind:value={filter} bind:this={input} onkeydown={key} />
      <div class="items">
        {#each list as c (c.id)}
          <button class="item" class:on={cur?.id === c.id} onclick={() => pick(c.id)} role="option" aria-selected={cur?.id === c.id}>
            <span class="dot" style:background={c.color || 'var(--accent)'}></span>
            <span class="nm ellipsis">{c.name}</span>
            {#if c.active_jobs?.length}<span class="pill accent">collecting</span>{/if}
            {#if c.unread_alerts}<span class="badge bad" title="Unread alerts">{c.unread_alerts}</span>{/if}
            <span class="meta tiny faint">{num(c.unreviewed)} to review · {ago(c.last_collected)}</span>
          </button>
        {:else}
          <div class="faint small pad">{app.casesLoaded ? 'No cases yet.' : 'Loading…'}</div>
        {/each}
      </div>
      <div class="foot">
        <button class="item" onclick={() => pick('all')}><span class="dot all"></span><span class="nm">Whole archive</span><span class="meta tiny faint">every channel, global triage</span></button>
        <a class="item" href="#/" onclick={() => (open = false)}>All cases &amp; new case…</a>
      </div>
    </div>
  {/if}
</div>

<style>
  .switcher { position: relative; min-width: 0; }
  .current { max-width: 320px; min-width: 180px; justify-content: flex-start; }
  .caret { margin-left: auto; color: var(--faint); }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; display: inline-block; }
  .dot.all { background: conic-gradient(var(--accent), var(--ok), var(--warn), var(--accent)); }
  .menu { position: absolute; top: calc(100% + 6px); left: 0; width: 400px; max-width: 92vw; z-index: 30; box-shadow: var(--shadow-lg); padding: 8px; }
  .menu input { width: 100%; margin-bottom: 6px; }
  .items { max-height: 50vh; overflow: auto; }
  .item { display: grid; grid-template-columns: 12px 1fr auto auto; align-items: center; gap: 6px; width: 100%; border: 0; background: none; padding: 6px 8px; border-radius: 6px; text-align: left; font-weight: 500; color: var(--text); }
  .item .meta { grid-column: 2 / -1; }
  .item:hover, .item.on { background: var(--panel-2); text-decoration: none; }
  .foot { border-top: 1px solid var(--line); margin-top: 6px; padding-top: 6px; }
  .foot a.item { display: block; color: var(--accent); }
  .pad { padding: 8px; }
</style>
