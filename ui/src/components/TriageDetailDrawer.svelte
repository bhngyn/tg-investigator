<script module>
  import { caseId as _caseId } from '../lib/router.svelte.js';

  /**
   * Apply a change reported by PostDetail / CommentDetail (`onchange`) to a list card, so the
   * list updates in place. `change` = { type, id, review?, tags?, notes? } in the current scope.
   */
  export function applyChange(item, change) {
    if (!item || !change || item.id !== change.id) return;
    const inCase = _caseId() != null;
    if (change.review !== undefined) { if (inCase) item.case_review = change.review; else item.review_status = change.review; }
    if (change.tags !== undefined) { if (inCase) item.case_tags = change.tags; else item.tags = change.tags; }
    if (change.notes !== undefined) { if (inCase) item.case_notes = change.notes; else item.notes = change.notes; }
  }
</script>

<script>
  // One right-side drawer for post / comment / account details, with a Back stack so that
  // following a link inside a detail (sender -> account, reply -> comment, same media -> post)
  // never stacks a second drawer (Esc would close both).
  //
  // Contract:
  //   <TriageDetailDrawer bind:item onchange={(ch) => ...} />
  //   item   = { type: 'post'|'comment'|'account'|'thread', id, seed?, focus?, media?: {sha256, t} } | null   (null = closed)
  //            A thread's id is "<group_id>:<top msg id>"; `focus` is the comment to highlight.
  //            `seed` is the list card, used to show case triage without a second fetch.
  //   onchange(change) is forwarded from the detail components (see applyChange above).
  //   bind:open (optional) mirrors whether the drawer is showing (pages use it to pause shortcuts).
  import Drawer from './Drawer.svelte';
  import PostDetail from './PostDetail.svelte';
  import CommentDetail from './CommentDetail.svelte';
  import AccountDrawer from './AccountDrawer.svelte';
  import CommentThread from './CommentThread.svelte';

  let { item = $bindable(null), onchange, open = $bindable(false) } = $props();

  let stack = $state([]);           // earlier items, for Back
  let lastRoot = null;
  let internal = false;             // true while we change `item` ourselves (push/back)

  // A new item from the page (not via onopen) resets the stack.
  $effect(() => {
    const it = item;
    if (it !== lastRoot && !internal) stack = [];
    lastRoot = it;
    internal = false;
    open = !!it;
  });

  function push(next) {
    if (!next || (item && next.type === item.type && String(next.id) === String(item.id))) return;
    stack = [...stack, item];
    internal = true;
    item = next;
  }
  function back() {
    const prev = stack[stack.length - 1];
    stack = stack.slice(0, -1);
    internal = true;
    item = prev;
  }
  function close() { stack = []; item = null; }

  const title = $derived(item ? ({ post: 'Post', comment: 'Comment', account: 'Account', thread: 'Thread' }[item.type] || 'Details') : '');
</script>

<Drawer open={!!item} onclose={close} {title} width="720px">
  {#snippet actions()}
    {#if stack.length}
      <button class="sm ghost" onclick={back} title="Back to the previous item">← Back</button>
    {/if}
  {/snippet}
  {#if item}
    {#key item.type + ':' + item.id}
      {#if item.type === 'post'}
        <PostDetail id={item.id} seed={item.seed} focusMedia={item.media} {onchange} onopen={push} />
      {:else if item.type === 'comment'}
        <CommentDetail id={item.id} seed={item.seed} focusMedia={item.media} {onchange} onopen={push} />
      {:else if item.type === 'account'}
        <AccountDrawer uid={item.id} onopen={push} />
      {:else if item.type === 'thread'}
        {@const [g, t] = String(item.id).split(':').map(Number)}
        <CommentThread groupId={g} top={t} focus={item.focus} onopen={push} onsender={(x) => push({ type: 'account', id: x.sender_id })} />
      {/if}
    {/key}
  {/if}
</Drawer>
