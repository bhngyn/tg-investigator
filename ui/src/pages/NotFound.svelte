<script>
  import Empty from '../components/Empty.svelte';
  import { route } from '../lib/router.svelte.js';
</script>
<div class="page"><Empty title="Page not found">There is no page called “{route.page}”. <a href="#/">Back to all cases</a></Empty></div>
