<script>
  // Drawer content for one post: GET /api/posts/{id}.
  // Contract (render inside <Drawer>, or use TriageDetailDrawer which does it for you):
  //   <PostDetail id={postId} seed={listCard?} onchange={(ch) => ...} onopen={({type, id}) => ...} />
  //   seed     optional list card (from /api/search): gives the case review/tags without a guess.
  //   onchange ({type:'post', id, review?, tags?, notes?}) after triage/tag/note changes, in the current scope.
  //   focusMedia {sha256, t, kind} from a search hit: opens the media text at that file and time.
  //   onopen   ({type:'post'|'comment'|'account', id}) to follow a link to another item. Without it,
  //            related posts open on the Search page instead.
  import { get } from '../lib/api.js';
  import { caseId, href } from '../lib/router.svelte.js';
  import { attempt, toast } from '../lib/store.svelte.js';
  import { setReview, addNote, listNotes, REVIEW_LABEL } from '../lib/triage.js';
  import { dt, ago, num, compact, bytes, pj, plural, safeUrl, isTelegramUrl, EXTERNAL_TITLE } from '../lib/fmt.js';
  import Media from './Media.svelte';
  import TagPicker from './TagPicker.svelte';
  import TriageBar from './TriageBar.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import Loading from './Loading.svelte';
  import AddToIncident from './AddToIncident.svelte';
  import MediaText from './MediaText.svelte';

  let { id, seed = null, onchange, onopen, focusMedia = null } = $props();

  let p = $state(null);
  let error = $state(null);
  let review = $state('unreviewed');
  let tags = $state([]);
  let notes = $state([]);          // rows: {id, text, author, created_at}
  let incidents = $state([]);
  let noteText = $state('');
  let saving = $state(false);
  let incOpen = $state(false);

  const inCase = caseId() != null;

  let loadSeq = 0;
  async function load() {
    const my = ++loadSeq;
    const stale = () => my !== loadSeq;   // a newer post was opened while this one was loading
    error = null;
    try {
      const [post, inc] = await Promise.all([
        get(`/api/posts/${id}`, inCase ? { case: caseId() } : undefined),
        get(`/api/items/post/${id}/incidents`).catch(() => []),
      ]);
      if (stale()) return;
      p = post;
      incidents = inc || [];
      if (inCase) {
        // The server annotates the post with this case's own state (?case=), so nothing is guessed.
        review = post.case_review || 'unreviewed';
        tags = (post.case_tags || []).map((t) => (typeof t === 'object' ? t.id ?? t.tag_id : t));
        const n = (await listNotes('post', id)) || [];
        if (stale()) return;
        notes = n;
      } else {
        review = post.review_status || 'unreviewed';
        tags = post.tags || [];
        notes = post.notes || [];
      }
    } catch (e) {
      if (!stale()) error = e.message;
    }
  }
  $effect(() => { if (id != null) load(); });

  async function reloadIncidents() {
    try { incidents = await get(`/api/items/post/${id}/incidents`); } catch { /* keep */ }
  }

  function changed(patch) { onchange?.({ type: 'post', id: p.id, ...patch }); }

  function onReview(s) { review = s; changed({ review: s }); }
  function onTags(ids) { tags = ids; changed({ tags: ids }); }

  async function saveNote(e) {
    e?.preventDefault();
    const text = noteText.trim();
    if (!text) return;
    saving = true;
    const r = await attempt(() => addNote('post', p.id, text, p.channel), 'Note saved');
    saving = false;
    if (r === undefined) return;
    noteText = '';
    if (inCase) notes = (await listNotes('post', p.id)) || notes;
    else notes = r?.notes || [...notes, { id: Date.now(), text, created_at: new Date().toISOString(), author: 'you' }];
    changed({ notes: notes.length });
  }
  function noteKey(e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) saveNote(e);
  }

  // ---- derived views --------------------------------------------------------------------------
  const shas = $derived.by(() => {
    const seen = new Map();
    for (const m of p?.media || []) if (m.sha256 && !seen.has(m.sha256)) seen.set(m.sha256, m);
    return [...seen.values()];
  });
  const reactions = $derived(Object.entries(p?.reactions || {}).sort((a, b) => b[1] - a[1]));
  const history = $derived((p?.history || []).slice().reverse());

  // Word-level diff between a previous version and the next one (small LCS; falls back to plain text).
  function diff(a, b) {
    const A = (a || '').split(/(\s+)/), B = (b || '').split(/(\s+)/);
    if (A.length * B.length > 400000) return null;
    const n = A.length, m = B.length;
    const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const out = [];
    let i = 0, j = 0;
    const push = (k, t) => { const last = out[out.length - 1]; if (last && last.k === k) last.t += t; else out.push({ k, t }); };
    while (i < n && j < m) {
      if (A[i] === B[j]) { push('=', A[i]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) { push('-', A[i]); i++; }
      else { push('+', B[j]); j++; }
    }
    while (i < n) push('-', A[i++]);
    while (j < m) push('+', B[j++]);
    return out;
  }
  // versions are earlier states, oldest first; each is compared with the state that followed it.
  const versions = $derived.by(() => {
    const v = p?.versions || [];
    return v.map((row, k) => {
      const next = k + 1 < v.length ? v[k + 1].text : p.text;
      return { ...row, next, parts: row.text === next ? [] : diff(row.text, next) };
    }).reverse();
  });

  function openPost(pid) {
    if (onopen) onopen({ type: 'post', id: pid });
  }
  function copy(text, what) {
    navigator.clipboard?.writeText(text).then(() => toast(`${what} copied`, 'ok'), () => toast('Could not copy', 'error'));
  }
  const auditLabel = (a) => a.replace(/_/g, ' ');
  function auditDetail(h) {
    const d = pj(h.detail, null);
    if (!d || typeof d !== 'object') return h.detail || '';
    const parts = [];
    if (d.from || d.to) parts.push(`${d.from || '?'} → ${d.to || '?'}`);
    if (d.tag) parts.push(d.tag);
    if (d.text) parts.push(`“${d.text}”`);
    return parts.join(' · ') || JSON.stringify(d);
  }
</script>

{#if error}
  <div class="callout bad">Could not load this post: {error} <button class="sm" onclick={load}>Retry</button></div>
{:else if !p}
  <Loading />
{:else}
  <div class="pd stack">
    <!-- Source line -->
    <div class="row wrap">
      <ChannelLink name={p.channel} title={p.channel_title} />
      <span class="faint small nowrap" title="Post date (UTC)">{dt(p.date)}</span>
      {#if p.deleted_detected}<span class="pill bad" title="Last seen {dt(p.last_seen)}">deleted on Telegram</span>{/if}
      {#if p.edited || p.versions?.length}<span class="pill warn">edited</span>{/if}
      {#if p.sensitive}<span class="pill warn" title="Telegram marked this as sensitive">sensitive</span>{/if}
      <span class="spacer"></span>
      <span class="faint small num">{num(p.views)} views</span>
    </div>

    {#if p.deleted_detected}
      <div class="callout bad small">
        <b>Found deleted</b> at {dt(p.deleted_detected)}. Last seen on Telegram {p.last_seen ? dt(p.last_seen) : 'at capture'}.
        The copy below is what was captured before deletion.
      </div>
    {/if}

    {#if p.fwd_username || p.fwd_name}
      <div class="small">
        <span class="pill info">↪ Forwarded</span>
        from {#if p.fwd_username}<ChannelLink name={p.fwd_username} />{:else}<b>{p.fwd_name}</b>{/if}
        {#if p.fwd_url}· {#if safeUrl(p.fwd_url)}<a href={safeUrl(p.fwd_url)} target="_blank" rel="noreferrer noopener" title={isTelegramUrl(p.fwd_url) ? 'Opens Telegram (leaves the app)' : EXTERNAL_TITLE}>original{isTelegramUrl(p.fwd_url) ? ' on t.me' : ' (external site)'} ↗</a>{:else}<span class="mono" title="Not a web link, shown as text">{p.fwd_url}</span>{/if}{/if}
        {#if p.original}· <button class="linkish" onclick={() => openPost(p.original.id)} disabled={!onopen}>open collected original ({p.original.channel}, {dt(p.original.date)})</button>{/if}
      </div>
    {/if}
    {#if p.author_signature}<div class="small muted">Signed: <b dir="auto">{p.author_signature}</b></div>{/if}
    {#if p.reply_to_url}<div class="small muted">Reply to {#if safeUrl(p.reply_to_url)}<a href={safeUrl(p.reply_to_url)} target="_blank" rel="noreferrer noopener" title={isTelegramUrl(p.reply_to_url) ? 'Opens Telegram (leaves the app)' : EXTERNAL_TITLE}>{p.reply_to_url}</a>{:else}<span class="mono">{p.reply_to_url}</span>{/if}</div>{/if}

    <!-- Content -->
    {#if p.text}
      <div class="text text-body" dir="auto">{p.text}</div>
    {:else}
      <div class="faint small">(no text)</div>
    {/if}
    <Media media={p.media} sensitive={p.sensitive} channel={p.channel} date={p.date} unavailable={p.media_unavailable} size="lg" max={12} />
    {#if p.media_unavailable?.length}
      <div class="tiny faint">Not stored: {p.media_unavailable.join(', ')}. Collect media from the channel page to download originals.</div>
    {/if}

    {#if p.media?.length}<MediaText postId={p.id} focus={focusMedia} />{/if}

    {#if reactions.length || p.hashtags?.length || p.links?.length}
      <div class="meta small">
        {#if reactions.length}
          <div><span class="faint">Reactions</span> {#each reactions as [e, n]}<span class="react">{e} {compact(n)}</span>{/each}</div>
        {/if}
        {#if p.hashtags?.length}<div><span class="faint">Hashtags</span> {#each p.hashtags as h}<a class="chip" href={href('search', { q: h.replace(/^#/, '') })}>#{h.replace(/^#/, '')}</a>{/each}</div>{/if}
        {#if p.links?.length}
          <div><span class="faint">Links</span>
            <ul class="links">{#each p.links as l}<li>{#if safeUrl(l)}<a href={safeUrl(l)} target="_blank" rel="noreferrer noopener" class="mono" title={isTelegramUrl(l) ? 'Opens Telegram (leaves the app)' : EXTERNAL_TITLE}>{l}</a>{#if !isTelegramUrl(l)} <span class="pill warn" title={EXTERNAL_TITLE}>external ↗</span>{/if}{:else}<span class="mono" title="Not a web link (http/https/tg only), shown as text">{l}</span>{/if}</li>{/each}</ul>
          </div>
        {/if}
      </div>
    {/if}

    <!-- Triage -->
    <section class="panel box">
      <div class="section-title">Triage {#if inCase}<span class="tiny faint">in this case</span>{:else}<span class="tiny faint">global (no case selected)</span>{/if}</div>
      <div class="row wrap">
        <TriageBar ids={p.id} status={review} onchange={onReview} />
        <span class="small muted">{REVIEW_LABEL[review]}</span>
      </div>
      <div class="mt"><TagPicker id={p.id} selected={tags} onchange={onTags} /></div>

      <div class="mt">
        <div class="small faint">Notes {#if inCase}(this case){/if}</div>
        {#each notes as n (n.id)}
          <div class="note"><div class="text-body" dir="auto">{n.text}</div><div class="tiny faint">{n.author || ''} · {dt(n.created_at)}</div></div>
        {:else}
          <div class="tiny faint">No notes yet.</div>
        {/each}
        <form class="notebox" onsubmit={saveNote}>
          <textarea data-draft rows="2" dir="auto" bind:value={noteText} onkeydown={noteKey} placeholder="Add a note (⌘/Ctrl+Enter to save)" aria-label="New note"></textarea>
          <button class="sm" type="submit" disabled={saving || !noteText.trim()}>{saving ? 'Saving…' : 'Add note'}</button>
        </form>
      </div>
    </section>

    <!-- Incidents + actions -->
    <section class="panel box">
      <div class="section-title">Incidents &amp; follow-up</div>
      {#if incidents.length}
        <div class="row wrap">
          {#each incidents as inc (inc.id)}
            <a class="chip on" href={href('incident', inc.id)} title={inc.relation || ''}>⚑ {inc.title} <span class="tiny faint">{inc.status}</span></a>
          {/each}
        </div>
      {:else}
        <div class="tiny faint">Not linked to any incident.</div>
      {/if}
      <div class="row wrap mt">
        <button class="sm primary" onclick={() => (incOpen = true)}>⚑ Add to incident</button>
        <a class="btn sm" href={href('analysis', 'spread', { post: p.id })}>Where else did this appear?</a>
        <a class="btn sm" href={p.url} target="_blank" rel="noreferrer noopener" title="Opens Telegram's public page (leaves the app)">Open on t.me ↗</a>
      </div>
      {#if shas.length}
        <div class="row wrap mt">
          {#each shas as m, k (m.sha256)}
            <a class="btn sm ghost" href={href('analysis', 'media', { sha: m.sha256 })} title="Posts and comments with a file that looks the same (perceptual hash)">
              Find visually similar{shas.length > 1 ? ` #${k + 1}` : ''}
            </a>
          {/each}
        </div>
      {/if}
    </section>

    <!-- Related -->
    {#if p.same_forward?.length || p.same_media?.length}
      <section class="panel box">
        <div class="section-title">Seen elsewhere in the archive</div>
        {#if p.same_forward?.length}
          <div class="small faint">{p.fwd_url ? 'Same original forwarded by' : 'Forwarded by'} ({p.same_forward.length})</div>
          <ul class="rel">
            {#each p.same_forward as r (r.id)}
              <li><button class="linkish" onclick={() => openPost(r.id)} disabled={!onopen}><bdi dir="ltr">@{r.channel}</bdi></button> <span class="faint tiny">{dt(r.date)}</span> <a class="tiny" href={r.url} target="_blank" rel="noreferrer noopener">t.me ↗</a></li>
            {/each}
          </ul>
        {/if}
        {#if p.same_media?.length}
          <div class="small faint">Identical file (same SHA-256) in ({p.same_media.length})</div>
          <ul class="rel">
            {#each p.same_media as r (r.id)}
              <li><button class="linkish" onclick={() => openPost(r.id)} disabled={!onopen}><bdi dir="ltr">@{r.channel}</bdi></button> <span class="faint tiny">{dt(r.date)}</span> <a class="tiny" href={r.url} target="_blank" rel="noreferrer noopener">t.me ↗</a></li>
            {/each}
          </ul>
        {/if}
      </section>
    {/if}

    <!-- Edits -->
    {#if versions.length}
      <section class="panel box">
        <div class="section-title">Edit history <span class="tiny faint">{plural(versions.length, 'earlier version')}</span></div>
        {#each versions as v (v.id)}
          <div class="ver">
            <div class="tiny faint">Captured {dt(v.captured_at)} · {num(v.views)} views{v.media_types ? ` · media ${v.media_types}` : ''}</div>
            {#if v.text === v.next}
              <div class="tiny faint">Text unchanged (media or views changed).</div>
            {:else if v.parts}
              <div class="text-body diff" dir="auto">{#each v.parts as part}{#if part.k === '='}{part.t}{:else if part.k === '-'}<del>{part.t}</del>{:else}<ins>{part.t}</ins>{/if}{/each}</div>
              <div class="tiny faint"><del>removed</del> · <ins>added</ins> in the next version</div>
            {:else}
              <div class="grid two">
                <div><div class="tiny faint">Before</div><div class="text-body" dir="auto">{v.text}</div></div>
                <div><div class="tiny faint">After</div><div class="text-body" dir="auto">{v.next}</div></div>
              </div>
            {/if}
          </div>
        {/each}
      </section>
    {/if}

    <!-- Evidence -->
    <section class="panel box">
      <div class="section-title">Evidence record</div>
      <dl class="kv small">
        <dt>Telegram link</dt><dd><a href={p.url} target="_blank" rel="noreferrer noopener" class="mono">{p.url}</a> <button class="sm ghost" onclick={() => copy(p.url, 'Link')}>Copy</button></dd>
        <dt>First captured</dt><dd>{dt(p.first_captured)}</dd>
        <dt>Last captured</dt><dd>{dt(p.last_captured)} <span class="faint">({plural(p.capture_count || 0, 'capture')})</span></dd>
        {#if p.last_seen}<dt>Last seen</dt><dd>{dt(p.last_seen)} ({ago(p.last_seen)})</dd>{/if}
        {#if p.deleted_detected}<dt>Found deleted</dt><dd>{dt(p.deleted_detected)}</dd>{/if}
        {#if p.page_capture}
          <dt>Page capture</dt>
          <dd><span class="mono hash" title={p.page_capture.sha256}>sha256 {p.page_capture.sha256}</span><br />
            <span class="faint">{dt(p.page_capture.captured_at)} · {p.page_capture.tool || ''} · {bytes(p.page_capture.bytes)}</span></dd>
        {/if}
      </dl>
      {#if p.media?.length}
        <div class="table-wrap mt">
          <table class="t small">
            <thead><tr><th scope="col">File</th><th scope="col">SHA-256</th><th scope="col">Captured</th><th scope="col">How</th></tr></thead>
            <tbody>
              {#each p.media as m (m.id)}
                {@const cap = (p.captures || []).find((c) => c.sha256 === m.sha256)}
                <tr>
                  <td class="nowrap">#{m.idx} {m.kind}<div class="tiny faint">{m.mime || ''} · {bytes(m.bytes)}</div></td>
                  <td><button class="hashbtn mono" onclick={() => copy(m.sha256, 'SHA-256')} title="Copy full hash">{m.sha256}</button></td>
                  <td class="nowrap">{dt(m.captured_at || cap?.captured_at)}</td>
                  <td class="nowrap">{m.method || cap?.method || '–'}{cap?.tool ? ` · ${cap.tool}` : ''}{cap?.account ? ` · ${cap.account}` : ''}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
      {#if p.captures?.length > (p.media?.length || 0)}
        <div class="tiny faint mt">The capture log has {plural(p.captures.length, 'entry', 'entries')} for these files (the same file can be captured more than once).</div>
      {/if}
    </section>

    {#if history.length}
      <details class="panel box">
        <summary class="section-title">Activity log <span class="tiny faint">{history.length}</span></summary>
        <ul class="audit small">
          {#each history as h, k (k)}
            <li><span class="faint nowrap">{dt(h.ts)}</span> <b>{auditLabel(h.action)}</b> <span class="muted">{auditDetail(h)}</span> <span class="tiny faint">{h.actor}</span></li>
          {/each}
        </ul>
      </details>
    {/if}
  </div>

  <AddToIncident open={incOpen} itemType="post" itemId={p.id} onclose={() => { incOpen = false; reloadIncidents(); }} />
{/if}

<style>
  .pd { display: flex; flex-direction: column; gap: 12px; }
  .wrap { flex-wrap: wrap; }
  .text { font-size: 14.5px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
  .box { padding: 10px 12px; }
  .mt { margin-top: 8px; }
  .meta > div { margin: 3px 0; }
  .react { display: inline-block; margin-right: 8px; }
  .links { margin: 2px 0 0; padding-left: 18px; }
  .links a { overflow-wrap: anywhere; font-size: 12px; }
  .note { border-left: 2px solid var(--line-strong); padding: 2px 8px; margin: 6px 0; }
  .notebox { display: flex; gap: 6px; align-items: flex-end; margin-top: 6px; }
  .notebox textarea { flex: 1; }
  .rel { margin: 4px 0 8px; padding-left: 18px; }
  .linkish { border: 0; background: none; padding: 0; color: var(--accent); font-weight: 500; cursor: pointer; }
  .linkish:disabled { color: var(--text); cursor: default; }
  .ver { border-top: 1px solid var(--line); padding-top: 6px; margin-top: 6px; }
  .diff { white-space: pre-wrap; }
  .diff del { background: var(--bad-soft); color: var(--bad); }
  .diff ins { background: var(--ok-soft); color: var(--ok); text-decoration: none; }
  .tiny del { background: var(--bad-soft); color: var(--bad); }
  .tiny ins { background: var(--ok-soft); color: var(--ok); text-decoration: none; }
  .kv { display: grid; grid-template-columns: 120px 1fr; gap: 4px 10px; margin: 0; }
  .kv dt { color: var(--faint); }
  .kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
  .hash { font-size: 11px; overflow-wrap: anywhere; }
  .hashbtn { border: 0; background: none; padding: 0; font-size: 11px; text-align: left; overflow-wrap: anywhere; word-break: break-all; color: var(--text); cursor: copy; font-weight: 400; }
  .audit { list-style: none; padding: 0; margin: 6px 0 0; }
  .audit li { padding: 3px 0; border-bottom: 1px solid var(--line); }
  details summary { cursor: pointer; }
</style>
