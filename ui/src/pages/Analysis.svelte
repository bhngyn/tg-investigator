<script>
  // "Patterns & changes": nine tabs, each one answers a single question. The tab is route.args[0].
  import { route, href, caseId } from '../lib/router.svelte.js';
  import { currentCase } from '../lib/store.svelte.js';
  import AnaChanges from '../components/AnaChanges.svelte';
  import AnaCoordination from '../components/AnaCoordination.svelte';
  import AnaSpread from '../components/AnaSpread.svelte';
  import AnaMedia from '../components/AnaMedia.svelte';
  import AnaPlaces from '../components/AnaPlaces.svelte';
  import AnaPeople from '../components/AnaPeople.svelte';
  import AnaCommunity from '../components/AnaCommunity.svelte';
  import AnaEntities from '../components/AnaEntities.svelte';
  import AnaMentions from '../components/AnaMentions.svelte';

  const TABS = [
    { id: 'changes', label: 'Changes', comp: AnaChanges },
    { id: 'coordination', label: 'Coordination', comp: AnaCoordination },
    { id: 'spread', label: 'Spread', comp: AnaSpread },
    { id: 'media', label: 'Media', comp: AnaMedia },
    { id: 'places', label: 'Claimed coordinates', comp: AnaPlaces },
    { id: 'mentions', label: 'Place names mentioned', comp: AnaMentions },
    { id: 'entities', label: 'Identifiers', comp: AnaEntities },
    { id: 'people', label: 'People', comp: AnaPeople },
    { id: 'community', label: 'Audience', comp: AnaCommunity },
  ];
  const tab = $derived(TABS.find((t) => t.id === route.args[0]) || TABS[0]);
  const Comp = $derived(tab.comp);
  const c = $derived(currentCase());
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>Patterns & changes</h1>
      <p>Questions the collected data can help answer. Everything here is computed locally from what has been collected;
        results are leads to verify, not findings.
        {#if caseId() != null}Scoped to <b>{c?.name || 'this case'}</b>.{:else}Covering the whole archive.{/if}</p>
    </div>
  </div>

  <nav class="tabs" aria-label="Analysis tabs">
    {#each TABS as t}
      <a href={href('analysis', t.id)} class:on={t.id === tab.id} aria-current={t.id === tab.id ? 'page' : undefined}>{t.label}</a>
    {/each}
  </nav>

  {#key tab.id + '|' + (route.scope ?? '')}
    <Comp />
  {/key}
</div>
