<script>
  let { title = 'Nothing here yet', children, action } = $props();
</script>
<div class="empty">
  <h3>{title}</h3>
  {#if children}<p>{@render children()}</p>{/if}
  {#if action}{@render action()}{/if}
</div>
