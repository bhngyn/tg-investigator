<script>
  // Drawer content for one comment: GET /api/comments/{id}.
  // Contract (render inside <Drawer>, or use TriageDetailDrawer):
  //   <CommentDetail id={commentId} seed={listCard?} focusMedia?={{sha256, t, kind, text}} onchange={(ch) => ...} onopen={({type, id}) => ...} />
  //   onchange ({type:'comment', id, review?, tags?, notes?}) after case triage changes.
  //   onopen   follow a link: sender -> {type:'account'}, parent/replies -> {type:'comment'}, post -> {type:'post'}.
  import { get } from '../lib/api.js';
  import { caseId, href } from '../lib/router.svelte.js';
  import { attempt, toast } from '../lib/store.svelte.js';
  import { addNote, listNotes, REVIEW_LABEL } from '../lib/triage.js';
  import { dt, bytes, tme, plural } from '../lib/fmt.js';
  import Media from './Media.svelte';
  import MediaText from './MediaText.svelte';
  import TagPicker from './TagPicker.svelte';
  import TriageBar from './TriageBar.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import Loading from './Loading.svelte';
  import TriagePhone from './TriagePhone.svelte';
  import AddToIncident from './AddToIncident.svelte';
  import { senderLabel } from './TriageCommentCard.svelte';

  let { id, seed = null, onchange, onopen, focusMedia = null } = $props();

  let c = $state(null);
  let error = $state(null);
  let review = $state('unreviewed');
  let tags = $state([]);
  let notes = $state([]);
  let noteText = $state('');
  let saving = $state(false);
  let incOpen = $state(false);
  let incidents = $state([]);
  const inCase = caseId() != null;

  async function load() {
    error = null;
    try {
      const [cm, inc] = await Promise.all([
        get(`/api/comments/${id}`, inCase ? { case: caseId() } : undefined),
        get(`/api/items/comment/${id}/incidents`).catch(() => []),
      ]);
      incidents = inc || [];
      if (inCase) {
        // The server annotates the comment with this case's own state (?case=).
        review = cm.case_review || 'unreviewed';
        tags = (cm.case_tags || []).map((t) => (typeof t === 'object' ? t.id ?? t.tag_id : t));
        notes = (await listNotes('comment', id)) || [];
      }
      c = cm;
    } catch (e) { error = e.message; }
  }
  $effect(() => { if (id != null) load(); });

  function changed(patch) { onchange?.({ type: 'comment', id: c.id, ...patch }); }

  async function saveNote(e) {
    e?.preventDefault();
    const text = noteText.trim();
    if (!text) return;
    saving = true;
    const r = await attempt(() => addNote('comment', c.id, text, c.channel), 'Note saved');
    saving = false;
    if (r === undefined) return;
    noteText = '';
    notes = (await listNotes('comment', c.id)) || notes;
    changed({ notes: notes.length });
  }
  function copy(text, what) {
    navigator.clipboard?.writeText(text).then(() => toast(`${what} copied`, 'ok'), () => toast('Could not copy', 'error'));
  }
  const open = (type, oid) => onopen?.({ type, id: oid });
  const versions = $derived(Array.isArray(c?.versions) ? c.versions.slice().reverse() : []);
</script>

{#if error}
  <div class="callout bad">Could not load this comment: {error} <button class="sm" onclick={load}>Retry</button></div>
{:else if !c}
  <Loading />
{:else}
  <div class="cd">
    <div class="row wrap">
      {#if c.sender_type === 'user' && c.sender_id}
        <button class="linkish" onclick={() => open('account', c.sender_id)} disabled={!onopen} title="Open this account"><b dir="auto">{senderLabel(c)}</b></button>
        {#if c.sender_username}<span class="faint small"><bdi dir="ltr">@{c.sender_username}</bdi></span>{/if}
      {:else}
        <b dir="auto">{senderLabel(c)}</b>
      {/if}
      {#if c.admin_role}<span class="pill accent">{c.admin_role}{c.admin_rank ? ': ' + c.admin_rank : ''}</span>{/if}
      {#if c.sender_bot}<span class="pill">bot</span>{/if}
      {#if c.sender_deleted}<span class="pill">deleted account</span>{/if}
      <span class="spacer"></span>
      <span class="faint small nowrap">{dt(c.date)}</span>
    </div>
    <div class="small muted">In the comment section of <ChannelLink name={c.channel} />
      {#if c.post}· under <button class="linkish" onclick={() => open('post', c.post.id)} disabled={!onopen}>post of {dt(c.post.date)}</button>{:else if c.post_msg_id} · under <a href={tme(c.channel, c.post_msg_id)} target="_blank" rel="noreferrer noopener">post {c.post_msg_id} ↗</a> <span class="tiny faint">(not collected)</span>{/if}
    </div>

    {#if c.deleted_detected}
      <div class="callout bad small"><b>Found deleted</b> at {dt(c.deleted_detected)}. Last seen {dt(c.last_seen)}. The text below is the captured copy.</div>
    {/if}
    {#if c.fwd_type}
      <div class="small"><span class="pill info">↪ Forwarded</span> from {#if c.fwd_username}<ChannelLink name={c.fwd_username} />{:else}<b dir="auto">{c.fwd_name || (c.fwd_type === 'hidden' ? 'a hidden source' : c.fwd_type + ' ' + (c.fwd_id || ''))}</b>{/if}
        {#if c.fwd_date}<span class="faint"> · originally {dt(c.fwd_date)}</span>{/if}
        {#if c.fwd_username && c.fwd_post}· <a href={tme(c.fwd_username, c.fwd_post)} target="_blank" rel="noreferrer noopener">original ↗</a>{/if}
      </div>
    {/if}

    {#if c.parent}
      <div class="quote small">
        <span class="faint">Reply to</span>
        <button class="linkish" onclick={() => open('comment', c.parent.id)} disabled={!onopen}>{senderLabel(c.parent)}</button>:
        <span dir="auto">{(c.parent.text || '').slice(0, 220)}{(c.parent.text || '').length > 220 ? '…' : ''}</span>
      </div>
    {/if}

    {#if c.text}<div class="text text-body" dir="auto">{c.text}</div>{:else}<div class="faint small">(no text{c.media_types?.length ? ': ' + c.media_types.join(', ') : ''})</div>{/if}
    <Media media={c.media} sensitive={c.sensitive} channel={c.channel} date={c.date} size="lg" max={12} />
    {#if c.media?.length}<MediaText commentId={c.id} focus={focusMedia} />{/if}

    {#if c.post?.text}
      <details class="small"><summary class="faint">The channel post this is under</summary><div class="text-body quote" dir="auto">{c.post.text}</div></details>
    {/if}

    <section class="panel box">
      <div class="section-title">Triage {#if inCase}<span class="tiny faint">in this case</span>{/if}</div>
      {#if inCase}
        <div class="row wrap"><TriageBar itemType="comment" ids={c.id} status={review} onchange={(s) => { review = s; changed({ review: s }); }} /><span class="small muted">{REVIEW_LABEL[review]}</span></div>
        <div class="mt"><TagPicker itemType="comment" id={c.id} selected={tags} onchange={(ids) => { tags = ids; changed({ tags: ids }); }} /></div>
        <div class="mt">
          <div class="small faint">Notes (this case)</div>
          {#each notes as n (n.id)}
            <div class="note"><div class="text-body" dir="auto">{n.text}</div><div class="tiny faint">{n.author || ''} · {dt(n.created_at)}</div></div>
          {:else}<div class="tiny faint">No notes yet.</div>{/each}
          <form class="notebox" onsubmit={saveNote}>
            <textarea data-draft rows="2" dir="auto" bind:value={noteText} onkeydown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) saveNote(e); }} placeholder="Add a note (⌘/Ctrl+Enter)" aria-label="New note"></textarea>
            <button class="sm" type="submit" disabled={saving || !noteText.trim()}>{saving ? 'Saving…' : 'Add note'}</button>
          </form>
        </div>
      {:else}
        <p class="small muted">Comments are triaged, tagged and annotated per case. Pick a case in the top bar to triage this comment.</p>
      {/if}
      <div class="row wrap mt">
        {#if incidents.length}{#each incidents as inc (inc.id)}<a class="chip on" href={href('incident', inc.id)}>⚑ {inc.title}</a>{/each}{/if}
        <button class="sm primary" onclick={() => (incOpen = true)}>⚑ Add to incident</button>
        {#if c.sender_type === 'user' && c.sender_id}<button class="sm" onclick={() => open('account', c.sender_id)} disabled={!onopen}>Account profile</button>{/if}
        <button class="sm" onclick={() => onopen?.({ type: 'thread', id: `${c.group_id}:${c.thread_top_id ?? c.msg_id}`, focus: c.id })} disabled={!onopen}>View in thread</button>
        <a class="btn sm" href={href('comments', { sender: c.sender_type === 'user' ? c.sender_id : undefined, channels: c.channel })}>More from this sender here</a>
      </div>
    </section>

    {#if c.replies?.length}
      <section class="panel box">
        <div class="section-title">Replies <span class="tiny faint">{c.replies.length}{c.replies.length >= 50 ? '+' : ''}</span></div>
        {#each c.replies as r (r.id)}
          <div class="reply small">
            <button class="linkish" onclick={() => open('comment', r.id)} disabled={!onopen}>{senderLabel(r)}</button>
            <span class="tiny faint">{dt(r.date)}</span>
            {#if r.deleted_detected}<span class="pill bad">deleted</span>{/if}
            <div dir="auto" class="text-body">{(r.text || '').slice(0, 300)}</div>
          </div>
        {/each}
      </section>
    {/if}

    {#if versions.length}
      <section class="panel box">
        <div class="section-title">Edit history <span class="tiny faint">{plural(versions.length, 'earlier version')}</span></div>
        {#each versions as v, k (v.id ?? k)}
          <div class="ver"><div class="tiny faint">Captured {dt(v.captured_at)}{v.edit_date ? ` · edited ${dt(v.edit_date)}` : ''}</div><div class="text-body" dir="auto">{v.text}</div></div>
        {/each}
        <div class="tiny faint">Current text is shown above; these are the earlier captured states, newest first.</div>
      </section>
    {/if}

    <section class="panel box">
      <div class="section-title">Evidence record</div>
      <dl class="kv small">
        <dt>Sender</dt><dd>{c.sender_type}{c.sender_id ? ` · id ${c.sender_id}` : ''}</dd>
        {#if c.sender_type === 'user'}<dt>Phone</dt><dd><TriagePhone phone={c.sender_phone} /></dd>{/if}
        <dt>Message id</dt><dd class="mono">group {c.group_id} · msg {c.msg_id}</dd>
        <dt>First captured</dt><dd>{dt(c.first_captured)}</dd>
        <dt>Last captured</dt><dd>{dt(c.last_captured)} ({plural(c.capture_count || 0, 'capture')})</dd>
        {#if c.edit_date}<dt>Edited on Telegram</dt><dd>{dt(c.edit_date)}</dd>{/if}
        {#if c.raw_capture}
          <dt>Raw API capture</dt><dd><button class="hashbtn mono" onclick={() => copy(c.raw_capture.sha256, 'SHA-256')}>sha256 {c.raw_capture.sha256}</button><br /><span class="faint">{dt(c.raw_capture.captured_at)} · {c.raw_capture.tool || ''}{c.raw_capture.account ? ' · account ' + c.raw_capture.account : ''} · {bytes(c.raw_capture.bytes)}</span></dd>
        {/if}
      </dl>
      {#if c.media?.length}
        <div class="table-wrap mt"><table class="t small">
          <thead><tr><th scope="col">File</th><th scope="col">SHA-256</th><th scope="col">Captured</th></tr></thead>
          <tbody>{#each c.media as m (m.id)}<tr>
            <td class="nowrap">#{m.idx} {m.kind}<div class="tiny faint">{m.mime || ''} · {bytes(m.bytes)}</div></td>
            <td><button class="hashbtn mono" onclick={() => copy(m.sha256, 'SHA-256')}>{m.sha256}</button>
              <div><a class="tiny" href={href('analysis', 'media', { sha: m.sha256 })}>Find visually similar</a></div></td>
            <td class="nowrap">{dt(m.captured_at)}</td>
          </tr>{/each}</tbody>
        </table></div>
      {/if}
    </section>
  </div>
  <AddToIncident open={incOpen} itemType="comment" itemId={c.id} onclose={() => { incOpen = false; get(`/api/items/comment/${c.id}/incidents`).then((r) => (incidents = r)).catch(() => {}); }} />
{/if}

<style>
  .cd { display: flex; flex-direction: column; gap: 10px; }
  .wrap { flex-wrap: wrap; }
  .text { font-size: 14.5px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
  .box { padding: 10px 12px; }
  .mt { margin-top: 8px; }
  .quote { border-left: 3px solid var(--line-strong); padding: 4px 10px; background: var(--panel-2); border-radius: 4px; }
  .note { border-left: 2px solid var(--line-strong); padding: 2px 8px; margin: 6px 0; }
  .notebox { display: flex; gap: 6px; align-items: flex-end; margin-top: 6px; }
  .notebox textarea { flex: 1; }
  .reply { border-top: 1px solid var(--line); padding: 5px 0; }
  .ver { border-top: 1px solid var(--line); padding: 5px 0; }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 500; cursor: pointer; }
  .linkish:disabled { color: var(--text); cursor: default; }
  .kv { display: grid; grid-template-columns: 130px 1fr; gap: 4px 10px; margin: 0; }
  .kv dt { color: var(--faint); }
  .kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  .hashbtn { border: 0; background: none; padding: 0; font-size: 11px; text-align: left; word-break: break-all; color: var(--text); cursor: copy; font-weight: 400; }
</style>
