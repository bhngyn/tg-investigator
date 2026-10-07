<script>
  // Link to a channel's page in the current scope.
  import { href } from '../lib/router.svelte.js';
  let { name, title = null, showHandle = true } = $props();
</script>
<a class="chlink" href={href('channel', name)} title={title ? `${title} (@${name})` : `@${name}`}>
  {#if title}<span class="t" dir="auto">{title}</span>{#if showHandle}<span class="h"><bdi dir="ltr">@{name}</bdi></span>{/if}{:else}<span class="t"><bdi dir="ltr">@{name}</bdi></span>{/if}
</a>
<style>
  .chlink { display: inline-flex; gap: 5px; align-items: baseline; min-width: 0; max-width: 100%; }
  .t { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .h { color: var(--faint); font-size: 12px; white-space: nowrap; }
</style>
