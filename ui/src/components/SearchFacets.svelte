<script>
  // Facets computed from the current results: identifiers found in the text (phones, wallets, ...) and places mentioned.
  // Clicking a value filters the search (entity=kind:value, place=<entry id>). Both are patterns/names found in text:
  // claims to check, not verified identities or locations.
  import { num } from '../lib/fmt.js';
  import { kindLabel, ENTITY_CAVEAT, PLACES_CAVEAT, UNAMBIGUOUS_HELP } from '../lib/searchparams.js';
  import { href } from '../lib/router.svelte.js';
  import DataAttribution from './DataAttribution.svelte';

  /** @type {{ entities: any[], places: any[], activeEntities: string[], activePlaces: string[], onentity: (kind:string, value:string)=>void, onplace: (id:string)=>void, loading?: boolean, mapQuery?: object, unambiguous?: boolean, onunambiguous?: (on:boolean)=>void, showMapLink?: boolean }} */
  let { entities = [], places = [], activeEntities = [], activePlaces = [], onentity, onplace, loading = false, mapQuery = {}, unambiguous = false, onunambiguous, showMapLink = true } = $props();

  let showAllEnt = $state(false);
  let showAllPl = $state(false);
  const ent = $derived(showAllEnt ? entities : entities.slice(0, 8));
  const pl = $derived(showAllPl ? places : places.slice(0, 8));
</script>

{#if entities.length || activeEntities.length}
  <div class="panel filters">
    <div class="section-title">Identifiers in results</div>
    {#each ent as e (e.kind + ':' + e.value)}
      {@const on = activeEntities.includes(e.kind + ':' + e.value)}
      <button class="facet" class:on onclick={() => onentity(e.kind, e.value)} aria-pressed={on} title="{kindLabel(e.kind)}: {e.value}">
        <span class="ellipsis"><span class="k">{e.kind}</span> <span class="mono" dir="ltr">{e.value}</span></span><span class="faint num">{num(e.n ?? e.count)}</span>
      </button>
    {/each}
    {#if entities.length > 8}<button class="sm ghost" onclick={() => (showAllEnt = !showAllEnt)}>{showAllEnt ? 'Fewer' : `All ${entities.length}`}</button>{/if}
    <div class="tiny faint">{ENTITY_CAVEAT}</div>
  </div>
{/if}

{#if places.length || activePlaces.length || unambiguous}
  <div class="panel filters">
    <div class="section-title">Place names mentioned</div>
    {#if onunambiguous}
      <label class="check small" title={UNAMBIGUOUS_HELP}><input type="checkbox" checked={unambiguous} onchange={(e) => onunambiguous(e.currentTarget.checked)} /> Only unambiguous place names</label>
      <div class="tiny faint">{UNAMBIGUOUS_HELP}</div>
    {/if}
    {#each pl as p (p.entry_id)}
      {@const on = activePlaces.includes(String(p.entry_id))}
      <button class="facet" class:on onclick={() => onplace(String(p.entry_id))} aria-pressed={on} title="{[p.label, p.admin1, p.country, p.category].filter(Boolean).join(' · ')}">
        <span class="ellipsis"><span dir="auto">{p.label}</span>{#if p.country}<span class="faint tiny">&nbsp;·&nbsp;{p.country}</span>{/if}{#if p.category}<span class="faint tiny">&nbsp;·&nbsp;{p.category}</span>{/if}</span>
        <span class="faint num">{num(p.n)}{#if p.ambiguous_n}<span class="amb" title="{num(p.ambiguous_n)} of these mentions could also be other places with the same name"> ?{num(p.ambiguous_n)}</span>{/if}</span>
      </button>
    {/each}
    {#if places.length > 8}<button class="sm ghost" onclick={() => (showAllPl = !showAllPl)}>{showAllPl ? 'Fewer' : `All ${places.length}`}</button>{/if}
    <div class="tiny faint">{PLACES_CAVEAT} <b>?n</b> = mentions that could be another place of the same name.</div>
    {#if showMapLink}<a class="small" href={href('analysis', 'mentions', mapQuery)}>Show these on a map →</a>{/if}
    <DataAttribution />
  </div>
{/if}

<style>
  .filters { padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
  .facet { display: flex; justify-content: space-between; gap: 8px; width: 100%; border: 0; background: none; padding: 3px 6px; border-radius: 5px; font-weight: 400; color: var(--text); text-align: left; min-width: 0; }
  .facet:hover { background: var(--panel-2); }
  .facet.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .k { font-size: 10px; text-transform: uppercase; font-weight: 700; color: var(--accent); }
  .mono { font-family: var(--mono); font-size: 12px; }
  .amb { color: var(--warn); font-weight: 600; }
</style>
