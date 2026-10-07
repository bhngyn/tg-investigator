<script module>
  /** Thread key of a comment card: the group message it hangs off (the post's mirror), or itself. */
  export function threadOf(c) {
    return { groupId: c.group_id, top: c.thread_top_id ?? c.msg_id };
  }

  const MAX_DEPTH = 3;

  /**
   * Nest a thread's comments by reply_to_msg_id, depth-first, oldest first at every level.
   * Returns [{c, depth, replyTo?, missingParent?}]. Past MAX_DEPTH the indent stops and the row
   * says who it answers; a reply whose parent was never captured hangs off the root and says so.
   */
  export function flattenThread(comments, top) {
    const byMsg = new Map(comments.map((c) => [c.msg_id, c]));
    const kids = new Map();
    const roots = [];
    for (const c of comments) {
      const p = c.reply_to_msg_id;
      if (p == null || p === top) roots.push({ c });
      else if (byMsg.has(p)) { if (!kids.has(p)) kids.set(p, []); kids.get(p).push(c); }
      else roots.push({ c, missingParent: p });
    }
    const byDate = (a, b) => (a.date || '').localeCompare(b.date || '') || a.msg_id - b.msg_id;
    roots.sort((a, b) => byDate(a.c, b.c));
    const out = [];
    const seen = new Set();
    function walk(c, depth, parent, missingParent) {
      if (seen.has(c.id)) return;
      seen.add(c.id);
      out.push({ c, depth: Math.min(depth, MAX_DEPTH), replyTo: depth > MAX_DEPTH ? parent : null, missingParent });
      for (const k of (kids.get(c.msg_id) || []).sort(byDate)) walk(k, depth + 1, c, null);
    }
    for (const r of roots) walk(r.c, 0, null, r.missingParent);
    return out;
  }
</script>

<script>
  // One comment thread, as it appeared under a channel post: the post, then every captured comment
  // nested by reply. GET /api/comment_threads/{group_id}/{top}.
  //   <CommentThread groupId top focus? match? preload? onopen? onsender? />
  //   focus   comment id to highlight and scroll to.
  //   match   Set of comment ids that matched the page's filters; the others are dimmed, not hidden.
  //   terms   words to highlight in the comments (search words + dictionary spellings).
  //   preload the thread head from the threads list ({root, post, comments}) so the header shows at once.
  //   onopen  ({type: 'comment'|'post', id, seed?}) for Details / the post.
  import { tick } from 'svelte';
  import { get } from '../lib/api.js';
  import { scoped } from '../lib/store.svelte.js';
  import { dt, tme, plural, num } from '../lib/fmt.js';
  import TriageCommentCard, { senderLabel } from './TriageCommentCard.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import Media from './Media.svelte';
  import Loading from './Loading.svelte';

  let { groupId, top, focus = null, match = null, preload = null, onopen, onsender, cap = 40, terms = [] } = $props();

  let t = $state(null);
  let error = $state(null);
  let showAll = $state(false);
  let box = $state();

  async function load() {
    error = null;
    try {
      t = await get(`/api/comment_threads/${groupId}/${top}`, scoped({}));
      if (focus != null) {
        showAll = true;
        await tick();
        const el = box?.querySelector(`[data-comment-id="${focus}"]`);
        el?.scrollIntoView({ block: 'center' });
      }
    } catch (e) { error = e.message; }
  }
  $effect(() => { if (groupId != null && top != null && !t) load(); });

  const head = $derived(t || preload || {});
  const root = $derived(head.root);
  const post = $derived(head.post);
  const rows = $derived(t ? flattenThread(t.comments, top) : []);
  // The channel's own copy of its post (the collector flags reposts too, from saved_from_peer).
  const rootIsPost = $derived(!!root?.is_mirror);
  // A mirror keeps the forward header of a post the channel reposted; a plain post's copy points at the channel itself.
  const isRepost = $derived(rootIsPost && !!root.fwd_type && root.fwd_id !== root.sender_id);
  // A plain group message with replies is the root itself: show it as the first comment.
  const rootIsComment = $derived(!!root && !rootIsPost);
  const shown = $derived(showAll ? rows : rows.slice(0, cap));
  const channel = $derived(head.channel || root?.channel);
  const channelPost = $derived(rootIsPost ? root.channel_post ?? root.post_msg_id : null);
  const postText = $derived(post?.text ?? (rootIsPost ? root.text : null));
  const deleted = $derived(t ? t.comments.filter((c) => c.deleted_detected).length : 0);
  // Only the post matched a text search: nothing to single out, so dim nothing.
  const dimSet = $derived(match && [...match].some((id) => id !== root?.id || rootIsComment) ? match : null);
  const byMsg = $derived(new Map((t?.comments || []).map((c) => [c.msg_id, c])));
</script>

<section class="thread" bind:this={box}>
  <header class="head">
    <div class="row wrap">
      {#if channel}<ChannelLink name={channel} showHandle={false} />{/if}
      {#if rootIsPost}
        <span class="faint small">{isRepost ? 'repost' : 'post'}</span>
        {#if post}
          <button class="linkish small" onclick={() => onopen?.({ type: 'post', id: post.id })} disabled={!onopen} title="Open the collected post">{dt(post.date)}</button>
        {:else}
          <span class="small nowrap">{dt(root.date)}</span>
        {/if}
        {#if channelPost}<a class="small" href={tme(channel, channelPost)} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}
      {:else if rootIsComment}
        <span class="faint small">group discussion, not under a channel post</span>
      {:else if root === null || (t && !root)}
        <span class="faint small">channel post not captured <span class="tiny">(group message {top})</span></span>
      {/if}
      <span class="spacer"></span>
      {#if head.comments != null || t}
        <span class="faint small nowrap">{plural(t ? t.comments.length : head.comments, 'comment')}{#if deleted} · {num(deleted)} found deleted{/if}</span>
      {/if}
    </div>
    {#if rootIsPost}
      {#if isRepost}<div class="tiny faint">↪ reposted from {root.fwd_username ? '@' + root.fwd_username : root.fwd_name || 'another channel'}</div>{/if}
      {#if postText}<div class="post text-body" dir="auto">{postText}</div>{:else}<div class="faint small">(post without text{root.media_types?.length ? ': ' + root.media_types.join(', ') : ''})</div>{/if}
      <Media media={root.media} sensitive={root.sensitive} channel={root.channel} date={root.date} size="sm" max={4} />
    {/if}
  </header>

  {#if error}
      <div class="callout bad small">Could not load this thread: {error} <button class="sm" onclick={load}>Retry</button></div>
    {:else if !t}
      <Loading />
    {:else}
      <div class="body">
        {#if rootIsComment}
          <div class="node" class:dim={dimSet && !dimSet.has(root.id)}>
            <TriageCommentCard inThread {terms} comment={root} dense selected={root.id === focus} onopen={onopen && ((x) => onopen({ type: 'comment', id: x.id, seed: x }))} onsender={onsender} />
          </div>
        {/if}
        {#each shown as r (r.c.id)}
          {@const indent = r.depth + (rootIsComment ? 1 : 0)}
          <div class="node" style:--d={indent} class:nested={indent > 0} class:dim={dimSet && !dimSet.has(r.c.id)}>
            {#if r.replyTo}
              <div class="tiny faint re">↳ replying to {senderLabel(r.replyTo)}</div>
            {:else if r.missingParent}
              <div class="tiny faint re" title="Telegram no longer returns the message this answers, or it was never captured">↳ reply to a comment that wasn't captured (msg {r.missingParent})</div>
            {:else if r.depth === 0 && r.c.reply_to_msg_id && r.c.reply_to_msg_id !== top && byMsg.get(r.c.reply_to_msg_id)}
              <div class="tiny faint re">↳ replying to {senderLabel(byMsg.get(r.c.reply_to_msg_id))}</div>
            {/if}
            <TriageCommentCard inThread {terms} comment={r.c} dense selected={r.c.id === focus} onopen={onopen && ((x) => onopen({ type: 'comment', id: x.id, seed: x }))} onsender={onsender} />
          </div>
        {:else}
          {#if !rootIsComment}<div class="faint small pad">No captured comments under this post.</div>{/if}
        {/each}
        {#if rows.length > shown.length}
          <button class="sm more" onclick={() => (showAll = true)}>Show all {num(rows.length)} comments</button>
        {/if}
        {#if rows.length}
          <p class="tiny faint pad">Only what was captured is shown: Telegram drops many discussion-group messages, so gaps in a conversation are expected.</p>
        {/if}
      </div>
  {/if}
</section>

<style>
  .thread { border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); overflow: hidden; }
  .head { padding: 10px 12px; background: var(--panel-2); border-bottom: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; }
  .wrap { flex-wrap: wrap; row-gap: 2px; }
  .post { font-size: 13.5px; white-space: pre-wrap; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 6; overflow: hidden; }
  .body { padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
  .node { margin-left: calc(var(--d, 0) * 22px); position: relative; }
  .node.nested::before { content: ''; position: absolute; left: -12px; top: 0; bottom: 0; border-left: 2px solid var(--line); }
  .node.dim { opacity: .55; }
  .node.dim:hover, .node.dim:focus-within { opacity: 1; }
  .re { margin: 0 0 2px 2px; }
  .more { align-self: flex-start; }
  .pad { margin: 4px 2px 0; }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font-weight: 400; }
  .linkish:disabled { color: var(--text); cursor: default; }
  @media (max-width: 640px) { .node { margin-left: calc(var(--d, 0) * 12px); } }
</style>
