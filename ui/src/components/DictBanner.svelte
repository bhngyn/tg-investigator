<script>
  // Shown when the dictionary behind multilingual matching is missing or out of date.
  // `dictionary` is expansion.dictionary from any search/lookup response: {available, key_version_ok, message?}.
  let { dictionary = null } = $props();
</script>
{#if dictionary && dictionary.available === false}
  <div class="callout warn small" role="status"><b>No place dictionary is installed.</b> Only the words you typed (plus Cyrillic/Latin spelling keys) are searched; other languages and spellings of names are NOT matched, so results may be incomplete. See Settings, Dictionary.</div>
{:else if dictionary && dictionary.key_version_ok === false}
  <div class="callout bad small" role="status"><b>The dictionary needs rebuilding.</b> Spelling matches may miss names until it is rebuilt. {dictionary.message || ''} See Settings, Dictionary.</div>
{/if}
