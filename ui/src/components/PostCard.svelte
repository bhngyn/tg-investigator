<script>
  // One collected post. Used by Inbox, Search, Channel, Incident pages.
  // Triage/tags act in the current scope (case or global) and update the card in place.
  import Media from './Media.svelte';
  import TriageBar from './TriageBar.svelte';
  import TagPicker from './TagPicker.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import { reviewOf, tagsOf } from '../lib/triage.js';
  import { caseId } from '../lib/router.svelte.js';
  import { compact, dt } from '../lib/fmt.js';
  import { highlightParts, excerptParts, clock } from '../lib/highlight.js';

  /** @type {{ post: any, selected?: boolean, onopen?: (p:any, hint?: {sha256?: string, t?: number, kind?: string})=>void, onselect?: (p:any, on:boolean)=>void, checked?: boolean, showChannel?: boolean, showTags?: boolean, dense?: boolean, isNew?: boolean, terms?: string[], statusUnknown?: boolean }} */
  let { post, selected = false, onopen, onselect, checked = false, showChannel = true, showTags = true, dense = false, isNew = false, terms = [], statusUnknown = false } = $props();

  // Search-term highlighting (shared with comment cards).
  const parts = $derived(post.text ? highlightParts(post.text, terms) : null);

  // Where the search matched: earlier text versions, OCR of an image, speech in audio/video.
  const hits = $derived(Array.isArray(post.media_hits) ? post.media_hits : []);
  const kindOf = (k) => { k = String(k || '').toLowerCase(); return k.startsWith('version') || k === 'earlier' || k === 'edit' ? 'version' : k.startsWith('ocr') ? 'ocr' : k.startsWith('speech') || k === 'transcript' ? 'speech' : k; };
  const badges = $derived.by(() => {
    const out = [];
    const seen = new Set((post.matched_in || []).map((m) => kindOf(typeof m === 'object' ? m.kind || m.source : m)));
    if (post.version_hit) seen.add('version');
    if (seen.has('version')) out.push({ k: 'version', label: 'earlier version', title: 'The words were found in an earlier (edited-away) version of this post; the current text may not contain them.' });
    for (const h of hits) {
      const k = kindOf(h.kind);
      if (k !== 'ocr' && k !== 'speech') continue;
      out.push({ k, label: `${k === 'ocr' ? 'OCR' : 'speech'}${h.t_start != null ? ' ' + clock(h.t_start) : ''}`, title: k === 'ocr' ? 'Found in text read from an image or video frame (machine reading, may be wrong).' : 'Machine transcript, unverified. Whisper can invent text over music, chanting or silence; check against the audio before relying on this hit.' });
    }
    for (const k of ['ocr', 'speech']) if (seen.has(k) && !out.some((b) => b.k === k)) out.push({ k, label: k === 'ocr' ? 'OCR' : 'speech', title: k === 'speech' ? 'Machine transcript, unverified. Whisper can invent text over music or silence; check against the audio.' : 'Matched in machine-read media text (may be wrong).' });
    if (seen.has('names')) out.push({ k: 'names', label: 'channel / forward name', title: 'Matched the name or title of the channel, or of the channel this was forwarded from, not the words of the post.' });
    if (out.length && seen.has('text')) out.unshift({ k: 'text', label: 'post text', title: 'Also found in the current post text.' });
    return out;
  });

  const status = $derived(reviewOf(post));
  const tags = $derived(tagsOf(post));

  function setStatus(s) {
    if (caseId() != null) post.case_review = s; else post.review_status = s;
  }
  function setTags(ids) {
    if (caseId() != null) post.case_tags = ids; else post.tags = ids;
  }
  const clamp = $derived(dense ? 4 : 10);
</script>

<article class="post panel rv-border-{statusUnknown ? 'unknown' : status}" class:selected class:dense data-post-id={post.id}>
  <header class="row">
    {#if onselect}
      <input type="checkbox" checked={checked} aria-label="Select post" onclick={(e) => { e.stopPropagation(); onselect(post, e.currentTarget.checked); }} />
    {/if}
    {#if showChannel}<ChannelLink name={post.channel} title={post.channel_title} />{/if}
    <span class="faint small nowrap" title="Post date (UTC)">{dt(post.date)}</span>
    {#if isNew}<span class="pill accent">new</span>{/if}
    {#if post.deleted_detected}<span class="pill bad" title="Found deleted at {dt(post.deleted_detected)}; last seen {dt(post.last_seen)}">deleted</span>{/if}
    {#if post.edited}<span class="pill warn" title="Edited on Telegram">edited</span>{/if}
    {#if post.fwd_username || post.fwd_name}
      <span class="pill info" title="Forwarded">↪ <bdi dir="auto">{post.fwd_username ? '@' + post.fwd_username : post.fwd_name}</bdi></span>
    {/if}
    <span class="spacer"></span>
    <span class="faint tiny num" title="views · reactions">{compact(post.views)} views{#if post.reactions_total} · {compact(post.reactions_total)} reactions{/if}</span>
    {#if (caseId() != null ? post.case_notes : post.notes)}<span class="pill" title="Notes">✎ {caseId() != null ? post.case_notes : post.notes}</span>{/if}
  </header>

  {#if post.text}
    <div class="text text-body" dir="auto" style:-webkit-line-clamp={clamp}>{#if parts}{#each parts as p}{#if p.hit}<mark>{p.seg}</mark>{:else}{p.seg}{/if}{/each}{:else}{post.text}{/if}</div>
  {:else}
    <div class="faint small">(no text)</div>
  {/if}

  {#if badges.length}
    <div class="row wrap srcs">
      {#each badges as b}<span class="pill {b.k === 'version' ? 'warn' : b.k === 'text' ? '' : 'info'}" title={b.title}>{b.label}</span>{/each}
    </div>
  {/if}
  {#if hits.length}
    <ul class="hits">
      {#each hits.slice(0, 3) as h}
        <li>
          <button class="hit" onclick={() => onopen?.(post, { sha256: h.sha256, t: h.t_start, kind: h.kind, text: h.excerpt })} disabled={!onopen} title="Open the post at this file{h.t_start != null ? ' and time' : ''}">
            <span class="tiny faint">{kindOf(h.kind) === 'ocr' ? 'OCR' : 'speech'}{h.t_start != null ? ' ' + clock(h.t_start) : ''}</span>
            <span class="ex" dir="auto">{#each excerptParts(h.excerpt, terms) as x}{#if x.hit}<mark>{x.seg}</mark>{:else}{x.seg}{/if}{/each}</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  <Media media={post.media} sensitive={post.sensitive} channel={post.channel} date={post.date} unavailable={post.media_unavailable} size={dense ? 'sm' : 'md'} />

  <footer class="row">
    {#if statusUnknown}<span class="pill warn" title="This case's review status for the post could not be loaded, so none is shown or changed here. Open the details to review it.">review status unknown</span>
    {:else}<TriageBar ids={post.id} {status} onchange={setStatus} />{/if}
    {#if showTags}<TagPicker id={post.id} selected={tags} onchange={setTags} compact />{/if}
    <span class="spacer"></span>
    <a class="small" href={post.url} target="_blank" rel="noreferrer noopener" title="Open on Telegram (leaves the app)">t.me ↗</a>
    {#if onopen}<button class="sm" onclick={() => onopen(post)}>Details</button>{/if}
  </footer>
</article>

<style>
  .post { padding: 10px 12px; border-left: 3px solid var(--line-strong); }
  .post.selected { outline: 2px solid var(--accent); outline-offset: 0; }
  .rv-border-relevant { border-left-color: var(--ok); }
  .rv-border-verify { border-left-color: var(--warn); }
  .rv-border-unknown { border-left-color: var(--warn); border-left-style: dashed; }
  .rv-border-irrelevant { border-left-color: var(--line); opacity: .8; }
  header { margin-bottom: 6px; min-width: 0; }
  .text { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; font-size: 13.5px; padding-block: .1em; }
  footer { margin-top: 6px; }
  .dense .text { font-size: 13px; }
  .srcs { gap: 4px; margin-top: 4px; }
  .wrap { flex-wrap: wrap; }
  .hits { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .hit { display: flex; gap: 8px; align-items: baseline; text-align: start; width: 100%; border: 0; border-left: 2px solid var(--info); background: var(--panel-2); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; padding: 3px 8px; font-weight: 400; color: var(--text); cursor: pointer; }
  .hit:hover:not(:disabled) { background: var(--accent-soft); }
  .hit .ex { font-size: 12.5px; overflow-wrap: anywhere; min-width: 0; }
</style>
