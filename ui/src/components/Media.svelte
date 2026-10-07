<script>
  // Media tiles for a post or comment. Blurred by default (setting `blur`: all | sensitive | none).
  // Revealing a tile counts toward the daily exposure meter. Videos never autoplay unrevealed.
  import { tick } from 'svelte';
  import { app, recordExposure, announce } from '../lib/store.svelte.js';
  import { mediaUrl } from '../lib/api.js';
  import { bytes, dt } from '../lib/fmt.js';

  /** @type {{ media?: any[], sensitive?: boolean (undefined = unknown, treated as sensitive in "blur only flagged" mode), channel?: string, date?: string (context for the accessible name), unavailable?: string[], size?: 'sm'|'md'|'lg', max?: number }} */
  let { media = [], sensitive = undefined, unavailable = [], size = 'md', max = 6, channel = '', date = '' } = $props();

  const blurMode = $derived(app.settings.blur || 'all');
  const gray = $derived(app.settings.grayscale === '1');
  const shouldBlur = $derived(blurMode === 'all' || (blurMode === 'sensitive' && (sensitive ?? true)));

  // Prefer full files over thumbs of the same idx.
  const tiles = $derived.by(() => {
    const byIdx = new Map();
    for (const m of media || []) {
      const cur = byIdx.get(m.idx);
      if (!cur || (cur.kind === 'thumb' && m.kind !== 'thumb')) byIdx.set(m.idx, m);
    }
    return [...byIdx.values()].sort((a, b) => a.idx - b.idx);
  });

  let revealed = $state({});
  async function reveal(m, e) {
    e?.stopPropagation();
    if (revealed[m.id]) return;
    const tile = e?.currentTarget?.closest?.('.tile');
    revealed[m.id] = true;
    if (shouldBlur) recordExposure();
    // The veil button disappears: keep keyboard/screen-reader focus on the media that appeared instead of losing it to <body>.
    await tick();
    tile?.focus({ preventScroll: true });
    announce(`${kindWord(m)} revealed`);
  }
  const kindWord = (m) => (isVideo(m) ? 'Video' : isImage(m) ? 'Photo' : 'File');
  const where = () => [channel ? `from @${channel}` : '', date ? `posted ${dt(date)}` : ''].filter(Boolean).join(', ');
  // Name for a tile. Before reveal it says only what and where (never anything about the content); after reveal, position too.
  const label = (m, i, n) => `${kindWord(m)}${n > 1 ? ` ${i + 1} of ${n}` : ''}${where() ? ' ' + where() : ''}`;
  const isVideo = (m) => (m.mime || '').startsWith('video') || m.kind === 'video' || m.kind === 'round';
  const isImage = (m) => (m.mime || '').startsWith('image') || m.kind === 'photo' || m.kind === 'thumb';
</script>

{#if tiles.length || unavailable?.length}
  <div class="media {size}">
    {#each tiles.slice(0, max) as m, i (m.id)}
      {@const hidden = shouldBlur && !revealed[m.id]}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <div class="tile" class:gray class:hidden tabindex={hidden ? undefined : -1} role={hidden ? undefined : 'group'} aria-label={hidden ? undefined : label(m, i, tiles.length)}>
        {#if isVideo(m)}
          {#if hidden}
            <div class="ph" aria-hidden="true"></div>
          {:else}
            <!-- svelte-ignore a11y_media_has_caption -->
            <video src={mediaUrl(m.path)} controls muted={app.settings.autoplay_muted !== '0'} preload="metadata"></video>
          {/if}
        {:else if isImage(m)}
          <img src={mediaUrl(m.path)} alt={hidden ? '' : (m.alt || label(m, i, tiles.length))} aria-hidden={hidden ? 'true' : undefined} loading="lazy" draggable="false" />
        {:else}
          <div class="doc"><b>{m.kind}</b><span>{m.mime || ''}</span><a href={mediaUrl(m.path)} target="_blank" rel="noreferrer noopener">Open file</a></div>
        {/if}
        {#if hidden}
          <button class="veil" onclick={(e) => reveal(m, e)} title="Reveal (counts toward today's exposure)"
            aria-label={`Hidden ${kindWord(m).toLowerCase()}${where() ? ' ' + where() : ''} (${bytes(m.bytes)}). Press to reveal; counts toward today's reveal clicks.`}>
            <span aria-hidden="true">{isVideo(m) ? 'Video' : m.kind === 'thumb' ? 'Preview' : 'Image'} hidden</span>
            <small aria-hidden="true">Click to reveal · {bytes(m.bytes)}</small>
          </button>
        {/if}
        {#if m.kind === 'thumb'}<span class="kind">thumb</span>{/if}
      </div>
    {/each}
    {#if tiles.length > max}<div class="more">+{tiles.length - max}</div>{/if}
    {#each unavailable || [] as u}
      <div class="tile na" title="Not downloaded: {u}"><span>{u}</span><small>not stored</small></div>
    {/each}
  </div>
{/if}

<style>
  .media { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
  .tile { position: relative; border-radius: 6px; overflow: hidden; background: var(--panel-2); border: 1px solid var(--line); }
  .sm .tile { width: 84px; height: 64px; }
  .md .tile { width: 168px; height: 120px; }
  .lg .tile { width: 100%; max-width: 640px; height: auto; min-height: 200px; }
  .tile img, .tile video, .tile .ph { width: 100%; height: 100%; object-fit: cover; display: block; }
  .lg .tile img, .lg .tile video { object-fit: contain; max-height: 70vh; background: #000; }
  .tile.hidden img { filter: blur(24px) saturate(.4); transform: scale(1.15); }
  .tile.gray img, .tile.gray video { filter: grayscale(1); }
  .tile.gray.hidden img { filter: blur(24px) grayscale(1); }
  .ph { background: repeating-linear-gradient(45deg, var(--panel-2), var(--panel-2) 8px, var(--line) 8px, var(--line) 16px); min-height: 60px; }
  .veil { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
    background: rgba(15, 18, 24, .55); color: #fff; border: 0; border-radius: 0; font-size: 12px; cursor: pointer; }
  .tile:focus { outline: none; }
  .tile:focus-visible { outline: var(--focus-ring); outline-offset: -2px; }
  .veil:hover { background: rgba(15, 18, 24, .45); }
  .veil small { opacity: .8; font-weight: 400; font-size: 11px; }
  .sm .veil small { display: none; }
  .kind { position: absolute; left: 4px; bottom: 4px; font-size: 10px; background: rgba(0,0,0,.6); color: #fff; padding: 0 4px; border-radius: 3px; }
  .na { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--faint); font-size: 11px; text-align: center; padding: 4px; }
  .doc { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 2px; font-size: 11px; }
  .more { align-self: center; color: var(--muted); font-size: 12px; }
</style>
