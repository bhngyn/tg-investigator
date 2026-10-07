<script>
  // Text read from a post's or comment's media by local models on this computer: OCR lines (with frame times for video) and speech transcripts.
  // GET /api/post/{id}/mediatext, GET /api/comments/{id}/mediatext, GET /api/mediatext/{sha256} (where the derived files live). Machine output, can be wrong.
  // The text can be as graphic as the media, so it stays collapsed behind a content note until you ask for it.
  //   <MediaText postId | commentId focus?={{sha256, t}} />       focus opens the file and marks the nearest segment (from a search hit).
  import { tick } from 'svelte';
  import { get } from '../lib/api.js';
  import { dt, num, plural } from '../lib/fmt.js';
  import { clock } from '../lib/highlight.js';
  import Loading from './Loading.svelte';

  let { postId = null, commentId = null, focus = null } = $props();

  let d = $state(null);
  let error = $state('');
  let shown = $state(false);        // content-note toggle
  let openSha = $state({});         // sha -> expanded
  let derived = $state({});         // sha -> {derived_dir, srt} | {error}
  let box = $state();

  async function load() {
    error = '';
    try { d = await get(commentId != null ? `/api/comments/${commentId}/mediatext` : `/api/post/${postId}/mediatext`); } catch (e) { error = e.message; }
  }
  $effect(() => { if (postId != null || commentId != null) load(); });

  const files = $derived((d?.media || []).filter((m) => m.runs?.length || m.segments?.ocr?.length || m.segments?.speech?.length));
  const anyText = $derived(files.some((m) => m.segments?.ocr?.length || m.segments?.speech?.length));
  const nSeg = $derived(files.reduce((a, m) => a + (m.segments?.ocr?.length || 0) + (m.segments?.speech?.length || 0), 0));

  // a search hit points at a file and a time: open it and mark the closest segment
  let focusKey = '';
  $effect(() => {
    const f = focus;
    if (!f || !files.length) return;
    const k = JSON.stringify(f) + files.length;
    if (k === focusKey) return;
    focusKey = k;
    const m = files.find((x) => x.sha256 === f.sha256) || files[0];
    shown = true; openSha[m.sha256] = true;
    tick().then(() => box?.querySelector('[data-hit="1"]')?.scrollIntoView({ block: 'center' }));
  });
  // Which segments to mark for a search hit: for video/audio the one nearest the hit time; for a still image
  // (no time) the line(s) whose text matches the hit excerpt. Returns a Set of segment ids (possibly empty).
  const clean = (x) => String(x || '').replace(/[…]|\.{3}/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase();
  function nearest(m) {
    const none = new Set();
    if (!focus || m.sha256 !== (focus.sha256 || m.sha256)) return none;
    const segs = [...(m.segments?.ocr || []), ...(m.segments?.speech || [])].filter((s) => (!focus.kind || String(focus.kind).slice(0, 3) === s.kind.slice(0, 3)));
    if (!segs.length) return none;
    if (focus.t != null) return new Set([segs.reduce((b, s) => (Math.abs((s.t_start ?? 0) - focus.t) < Math.abs((b.t_start ?? 0) - focus.t) ? s : b)).id]);
    const needle = clean(focus.text);
    const hits = needle ? segs.filter((s) => { const t = clean(s.text); return t.length > 1 && (t.includes(needle) || needle.includes(t)); }) : [];
    return new Set(hits.map((s) => s.id));   // no match: mark nothing rather than a wrong line
  }

  async function toggleFile(m) {
    openSha[m.sha256] = !openSha[m.sha256];
    if (openSha[m.sha256] && !derived[m.sha256]) {
      try { const r = await get(`/api/mediatext/${m.sha256}`); derived[m.sha256] = { dir: r.derived_dir, srt: r.srt }; }
      catch (e) { derived[m.sha256] = { error: e.message }; }
    }
  }
  const STATUS = { ok: 'read', no_text: 'no text found', no_audio: 'no audio', error: 'failed' };
  const pill = (s) => (s === 'ok' ? 'ok' : s === 'error' ? 'bad' : '');
  const langs = (r) => (Array.isArray(r.languages) ? r.languages.join(', ') : r.languages || '');
</script>

{#if error}
  <div class="tiny faint">Media text unavailable: {error}</div>
{:else if d && files.length}
  <section class="panel box" bind:this={box}>
    <div class="section-title">Text read from media <span class="tiny faint">machine-made, unverified · {plural(nSeg, 'segment')}</span></div>
    {#if !shown}
      <div class="note">
        <span class="small">Text from images, video frames and speech can describe the same graphic content as the media itself. It is hidden until you choose to read it.</span>
        <button class="sm" onclick={() => (shown = true)} disabled={!anyText} title={anyText ? '' : 'Files were processed but no text was found'}>Show transcript/OCR text</button>
      </div>
      {#if !anyText}<div class="tiny faint">{plural(files.length, 'file')} processed; no text was found.</div>{/if}
    {:else}
      <div class="row"><button class="sm ghost" onclick={() => (shown = false)}>Hide text</button>
        <span class="tiny faint">OCR (Apple Vision) and speech (Whisper) run locally on this Mac and make mistakes: Arabic dialect, handwriting, poor audio. Whisper can also invent plausible sentences over music, chanting, silence or noise. Machine-read text is a search aid, not a transcript: verify against the original before relying on or quoting it.</span></div>
      {#each files as m (m.sha256)}
        {@const hit = nearest(m)}
        <div class="file">
          <button class="fhead" onclick={() => toggleFile(m)} aria-expanded={!!openSha[m.sha256]}>
            <span>{openSha[m.sha256] ? '▾' : '▸'}</span>
            <b>File #{m.idx} · {m.kind}</b>
            <span class="tiny faint">{num(m.segments?.ocr?.length || 0)} OCR · {num(m.segments?.speech?.length || 0)} speech</span>
            {#each m.runs || [] as r}<span class="pill {pill(r.status)}" title="{r.tool || ''} {r.model || ''} {langs(r)} {r.created ? dt(r.created) : ''} {r.error || ''}">{r.kind} {STATUS[r.status] || r.status}</span>{/each}
          </button>
          {#if openSha[m.sha256]}
            <div class="fbody">
              {#if m.segments?.ocr?.length}
                <div class="tiny faint">Text in the picture{m.kind === 'video' || (m.mime || '').startsWith('video') ? ' (per video frame)' : ''}</div>
                {#each m.segments.ocr as s (s.id)}
                  <div class="seg-row" class:hit={hit.has(s.id)} data-hit={hit.has(s.id) ? '1' : null}>
                    <span class="ts">{s.t_start != null ? clock(s.t_start) : '·'}</span>
                    <span class="txt text-body" dir="auto">{s.text}</span>
                    {#if s.lang}<span class="lg">{s.lang}</span>{/if}
                  </div>
                {/each}
              {/if}
              {#if m.segments?.speech?.length}
                <div class="tiny faint"><span class="pill warn" title="Whisper can invent text over music, chanting, silence or noise. Check against the audio.">machine transcript, unverified</span> Speech transcript{m.segments.speech[0]?.lang ? ` · detected language ${[...new Set(m.segments.speech.map((s) => s.lang).filter(Boolean))].join(', ')}` : ''}</div>
                {#each m.segments.speech as s (s.id)}
                  <div class="seg-row" class:hit={hit.has(s.id)} data-hit={hit.has(s.id) ? '1' : null}>
                    <span class="ts">{clock(s.t_start)}{s.t_end != null ? '–' + clock(s.t_end) : ''}</span>
                    <span class="txt text-body" dir="auto">{s.text}</span>
                    {#if s.lang}<span class="lg">{s.lang}</span>{/if}
                  </div>
                {/each}
              {/if}
              {#if !m.segments?.ocr?.length && !m.segments?.speech?.length}<div class="tiny faint">No text was found in this file.</div>{/if}
              {#if derived[m.sha256]?.dir}
                <div class="tiny faint">Derived files (machine output, kept beside the original): <span class="mono">{derived[m.sha256].dir}</span>{#if derived[m.sha256].srt} · <span class="mono">{derived[m.sha256].srt}</span>{/if}</div>
              {/if}
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </section>
{:else if !d && !error}
  <Loading />
{/if}

<style>
  .box { padding: 10px 12px; }
  .note { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: var(--panel-2); border-radius: var(--radius-sm); padding: 8px 10px; }
  .file { border: 1px solid var(--line); border-radius: var(--radius-sm); margin-top: 8px; }
  .fhead { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; width: 100%; border: 0; background: var(--panel-2); text-align: start; padding: 6px 10px; font-weight: 400; color: var(--text); }
  .fbody { padding: 6px 10px 8px; display: flex; flex-direction: column; gap: 3px; }
  .seg-row { display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: baseline; padding: 2px 4px; border-radius: 4px; }
  .seg-row.hit { background: var(--warn-soft); box-shadow: inset 2px 0 0 var(--warn); }
  .ts { font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
  .txt { font-size: 13.5px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .lg { font-size: 10px; text-transform: uppercase; font-weight: 700; color: var(--accent); }
  .mono { font-family: var(--mono); overflow-wrap: anywhere; }
</style>
