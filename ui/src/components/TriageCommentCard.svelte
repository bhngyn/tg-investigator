<script module>
  /** Display name for a comment's sender (user, channel identity or anonymous admin). */
  export function senderLabel(x) {
    if (x.sender_type === 'user') return x.sender_name || (x.sender_username ? '@' + x.sender_username : x.sender_deleted ? 'Deleted account' : `user ${x.sender_id}`);
    if (x.sender_type === 'channel') return (x.sender_name || x.sender_username || 'a channel') + ' (as channel)';
    if (x.sender_type === 'group') return 'Anonymous admin' + (x.post_author ? ` · ${x.post_author}` : '');
    return 'Unknown sender';
  }
</script>

<script>
  // One comment from a linked discussion group (card from /api/comments).
  //   <TriageCommentCard comment={c} onopen={(c) => ...} onsender={(c) => ...}
  //                      onselect?={(c, on) => ...} onthread?={(c) => ...} checked? selected? dense? isNew? inThread? terms? />
  // terms: words to highlight (the search query plus the dictionary's other spellings).
  // inThread drops what the surrounding thread already says (channel, link to the post).
  // Triage/tags only work inside a case (comments have no global triage).
  import Media from './Media.svelte';
  import TriageBar from './TriageBar.svelte';
  import TagPicker from './TagPicker.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import { reviewOf, tagsOf } from '../lib/triage.js';
  import { caseId } from '../lib/router.svelte.js';
  import { dt, compact, tme } from '../lib/fmt.js';
  import { highlightParts, excerptParts, clock } from '../lib/highlight.js';

  let { comment: c, onopen, onsender, onselect, onthread, checked = false, selected = false, dense = false, isNew = false, inThread = false, terms = [] } = $props();
  const parts = $derived(c.text ? highlightParts(c.text, terms) : null);

  const status = $derived(reviewOf(c));
  const tags = $derived(tagsOf(c));
  const inCase = $derived(caseId() != null);

  // Where the search matched beyond the comment's own words: OCR of an image, speech in audio/video (machine reading).
  const hits = $derived(Array.isArray(c.media_hits) ? c.media_hits : []);
  const badges = $derived.by(() => {
    const out = [];
    const seen = new Set(c.matched_in || []);
    for (const h of hits) {
      const k = h.kind === 'speech' ? 'speech' : 'ocr';
      out.push({ k, label: `${k === 'ocr' ? 'OCR' : 'speech'}${h.t_start != null ? ' ' + clock(h.t_start) : ''}`, title: k === 'ocr' ? 'Found in text read from an image or video frame attached to this comment (machine reading, may be wrong).' : 'Machine transcript, unverified. Whisper can invent text over music, chanting or silence; check against the audio before relying on this hit.' });
    }
    for (const k of ['ocr', 'speech']) if (seen.has(k) && !out.some((b) => b.k === k)) out.push({ k, label: k === 'ocr' ? 'OCR' : 'speech', title: 'Matched in machine-read media text (may be wrong).' });
    if (seen.has('names')) out.push({ k: 'names', label: 'sender / channel name', title: 'Matched a name, not the words of the comment.' });
    if (out.length && seen.has('text')) out.unshift({ k: 'text', label: 'comment text', title: 'Also found in the comment text.' });
    return out;
  });

  const postUrl = $derived(c.post_msg_id ? tme(c.channel, c.post_msg_id) : null);
</script>

<article class="cmt panel rv-border-{status}" class:selected class:dense data-comment-id={c.id}>
  <header class="row">
    {#if onselect && inCase}
      <input type="checkbox" {checked} aria-label="Select comment" onclick={(e) => { e.stopPropagation(); onselect(c, e.currentTarget.checked); }} />
    {/if}
    {#if c.sender_type === 'user' && onsender}
      <button class="sender" onclick={() => onsender(c)} title="Open this account">
        <b dir="auto">{senderLabel(c)}</b>{#if c.sender_username && c.sender_name}<span class="faint small"> <bdi dir="ltr">@{c.sender_username}</bdi></span>{/if}
      </button>
    {:else}
      <b class="small" dir="auto">{senderLabel(c)}</b>
    {/if}
    {#if c.admin_role}<span class="pill accent" title="Admin of this comment group">{c.admin_role}{c.admin_rank ? ': ' + c.admin_rank : ''}</span>{/if}
    {#if c.sender_bot}<span class="pill">bot</span>{/if}
    {#if !inThread}<span class="faint small">in</span> <ChannelLink name={c.channel} showHandle={false} />{/if}
    <span class="faint small nowrap" title="Comment date (UTC)">{dt(c.date)}</span>
    {#if isNew}<span class="pill accent">new</span>{/if}
    {#if c.deleted_detected}<span class="pill bad" title="Found deleted {dt(c.deleted_detected)}">deleted</span>{/if}
    {#if c.versions}<span class="pill warn">edited</span>{/if}
    {#if c.fwd_type}<span class="pill info">↪ {c.fwd_username ? '@' + c.fwd_username : c.fwd_name || 'forwarded'}</span>{/if}
    <span class="spacer"></span>
    {#if c.reactions_total}<span class="faint tiny">{compact(c.reactions_total)} reactions</span>{/if}
    {#if inCase && c.case_notes}<span class="pill">✎ {c.case_notes}</span>{/if}
  </header>

  {#if c.text}
    <div class="text text-body" dir="auto" style:-webkit-line-clamp={dense ? 3 : 8}>{#if parts}{#each parts as p}{#if p.hit}<mark>{p.seg}</mark>{:else}{p.seg}{/if}{/each}{:else}{c.text}{/if}</div>
  {:else}
    <div class="faint small">({c.media_types?.length ? c.media_types.join(', ') : 'no text'})</div>
  {/if}
  {#if badges.length}
    <div class="row wrap srcs">{#each badges as b}<span class="pill {b.k === 'text' ? '' : 'info'}" title={b.title}>{b.label}</span>{/each}</div>
  {/if}
  {#if hits.length}
    <ul class="hits">
      {#each hits.slice(0, 3) as h}
        <li>
          <button class="hit" onclick={() => onopen?.(c, { sha256: h.sha256, t: h.t_start, kind: h.kind, text: h.excerpt })} disabled={!onopen} title="Open the comment at this file{h.t_start != null ? ' and time' : ''}">
            <span class="tiny faint">{h.kind === 'speech' ? 'speech' : 'OCR'}{h.t_start != null ? ' ' + clock(h.t_start) : ''}</span>
            <span class="ex" dir="auto">{#each excerptParts(h.excerpt, terms) as x}{#if x.hit}<mark>{x.seg}</mark>{:else}{x.seg}{/if}{/each}</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
  <Media media={c.media} sensitive={c.sensitive} channel={c.channel} date={c.date} size={dense ? 'sm' : 'md'} />

  <footer class="row">
    <TriageBar itemType="comment" ids={c.id} {status} onchange={(s) => (c.case_review = s)} />
    {#if inCase}<TagPicker itemType="comment" id={c.id} selected={tags} onchange={(ids) => (c.case_tags = ids)} compact />{/if}
    <span class="spacer"></span>
    {#if postUrl && !inThread}<a class="small" href={postUrl} target="_blank" rel="noreferrer noopener" title="The channel post this comment is under">post ↗</a>{/if}
    {#if onthread}<button class="sm" onclick={() => onthread(c)} title="See this comment in its conversation">Thread</button>{/if}
    {#if onopen}<button class="sm" onclick={() => onopen(c)}>Details</button>{/if}
  </footer>
</article>

<style>
  .cmt { padding: 9px 12px; border-left: 3px solid var(--line-strong); }
  .cmt.selected { outline: 2px solid var(--accent); outline-offset: 0; }
  .rv-border-relevant { border-left-color: var(--ok); }
  .rv-border-verify { border-left-color: var(--warn); }
  .rv-border-irrelevant { opacity: .8; }
  header { margin-bottom: 5px; flex-wrap: wrap; row-gap: 2px; }
  .sender { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font-weight: 400; }
  .sender:hover { text-decoration: underline; }
  .text { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; font-size: 13.5px; }
  footer { margin-top: 5px; }
  .srcs { gap: 4px; margin-top: 4px; }
  .wrap { flex-wrap: wrap; }
  .hits { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .hit { display: flex; gap: 8px; align-items: baseline; text-align: start; width: 100%; border: 0; border-left: 2px solid var(--info); background: var(--panel-2); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; padding: 3px 8px; font-weight: 400; color: var(--text); cursor: pointer; }
  .hit:hover:not(:disabled) { background: var(--accent-soft); }
  .hit .ex { font-size: 12.5px; overflow-wrap: anywhere; min-width: 0; }
</style>
