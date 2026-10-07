<script>
  // Phone number behind a click (sensitive personal data). Stays hidden until asked for.
  //   <TriagePhone phone="249912345678" country="Sudan" />
  let { phone = null, country = null } = $props();
  let shown = $state(false);
  const fmt = (p) => (String(p).startsWith('+') ? String(p) : '+' + p);
</script>

{#if phone}
  {#if shown}
    <span class="mono ph">{fmt(phone)}</span>{#if country}<span class="tiny faint"> · {country}</span>{/if}
    <button class="sm ghost" onclick={(e) => { e.stopPropagation(); shown = false; }}>Hide</button>
  {:else}
    <button class="sm ghost" onclick={(e) => { e.stopPropagation(); shown = true; }} title="Phone numbers are personal data: shown only on request">
      Show phone{#if country} <span class="tiny faint">({country})</span>{/if}
    </button>
  {/if}
{:else}
  <span class="faint small">hidden or not collected</span>
{/if}

<style>
  .ph { user-select: all; }
</style>
