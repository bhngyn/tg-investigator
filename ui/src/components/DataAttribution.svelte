<script>
  // Licence line shown wherever dictionary/place data is displayed (map, dictionary tab, place facets).
  // The dictionary is a derivative database of OpenStreetMap (ODbL 1.0, share-alike) plus GeoNames (CC BY 4.0).
  let { long = false } = $props();
</script>
<span class="attr tiny faint">Place data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener" title="Opens openstreetmap.org (external site)">OpenStreetMap contributors</a> (ODbL 1.0) and <a href="https://www.geonames.org" target="_blank" rel="noreferrer noopener" title="Opens geonames.org (external site)">GeoNames</a> (CC BY 4.0).{#if long} The dictionary file is a derivative database: share it, or a database built from it, only under the same ODbL terms and with this attribution.{/if}</span>
