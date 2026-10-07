<script>
  // Name / description / colour fields shared by "New case" and "Edit case".
  import EvColorPicker from './EvColorPicker.svelte';
  let { name = $bindable(''), description = $bindable(''), color = $bindable('#2458d6'), error = '', autofocus = false } = $props();
  let nameEl = $state();
  $effect(() => { if (autofocus) nameEl?.focus(); });
</script>
<div class="col" style="gap:12px">
  <label class="field">Case name
    <input type="text" bind:this={nameEl} bind:value={name} placeholder="e.g. Unit X channels, 2025" maxlength="120" aria-invalid={!!error} />
    {#if error}<span class="err small">{error}</span>{/if}
  </label>
  <label class="field">Description <span class="faint">(what this case is about, optional)</span>
    <textarea bind:value={description} rows="3" aria-label="Description" placeholder="Scope, time period, who asked for it…"></textarea>
  </label>
  <div class="field"><span class="muted small" style="font-weight:550">Colour</span><EvColorPicker bind:value={color} /></div>
</div>
<style>
  .err { color: var(--bad); font-weight: 500; }
  .field { display: flex; flex-direction: column; gap: 4px; }
</style>
