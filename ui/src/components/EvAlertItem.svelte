<script>
  // One alert (or a preview match): title, short detail, and a link to the thing it is about.
  import { hrefIn, scopeSeg } from '../lib/router.svelte.js';
  import { ago, dt } from '../lib/fmt.js';

  /** @type {{ alert: any, onread?: (a:any)=>void, compact?: boolean }} */
  let { alert, onread, compact = false } = $props();

  const KIND = {
    keyword: 'keyword', post_deleted: 'post deleted', comment_deleted: 'comment deleted', post_edited: 'post edited',
    channel_change: 'channel change', admin_change: 'admin change', new_candidate: 'new candidate', media_reused: 'media reused',
  };
  const TONE = { post_deleted: 'bad', comment_deleted: 'bad', post_edited: 'warn', channel_change: 'warn', admin_change: 'info', new_candidate: 'accent', media_reused: 'info', keyword: 'accent' };

  const scope = $derived(alert.case_id || scopeSeg());
  const d = $derived(alert.detail && typeof alert.detail === 'object' ? alert.detail : {});
  const unread = $derived('read_at' in alert && !alert.read_at);

  // Where to look at the underlying item.
  const link = $derived.by(() => {
    const t = alert.item_type;
    if (t === 'channel') return { href: hrefIn(scope, 'channel', alert.channel), label: 'Open channel' };
    if (t === 'account') return { href: hrefIn(scope, 'comments', { sender: d.user_id }), label: 'Open account' };
    if (t === 'comment') return { href: hrefIn(scope, 'comments', { channels: alert.channel, sender: d.sender_id || undefined }), label: 'Open comments' };
    if (t === 'post') return { href: hrefIn(scope, 'channel', alert.channel, { post: d.id ?? parseInt(alert.item_id) }), label: 'Open in channel' };
    return null;
  });
  const snippet = $derived(d.text || d.previous_text || '');
</script>

<div class="al" class:unread class:compact>
  <div class="row head">
    <span class="pill {TONE[alert.kind] || ''}">{KIND[alert.kind] || alert.kind}</span>
    <b class="t">{alert.title}</b>
    <span class="spacer"></span>
    {#if alert.created}<span class="faint tiny nowrap" title={dt(alert.created)}>{ago(alert.created)}</span>{/if}
  </div>
  {#if alert.kind === 'post_edited' && d.previous_text}
    <div class="small edit"><span class="faint">Before:</span> <span dir="auto">{d.previous_text}</span></div>
    <div class="small edit"><span class="faint">Now:</span> <span dir="auto">{d.text}</span></div>
  {:else if snippet}
    <div class="small snip text-body" dir="auto">{snippet}</div>
  {/if}
  {#if alert.kind === 'channel_change' && (d.old || d.new)}
    <div class="small muted">{d.old ?? '–'} → {d.new ?? '–'}</div>
  {/if}
  {#if alert.kind === 'new_candidate' && d.title}<div class="small muted">{d.title}</div>{/if}
  {#if !compact}
    <div class="row acts">
      {#if link}<a class="small" href={link.href}>{link.label} →</a>{/if}
      {#if d.url}<a class="small" href={d.url} target="_blank" rel="noreferrer noopener" title="Opens Telegram (leaves the app)">t.me ↗</a>{/if}
      {#if 'read_at' in alert && onread}
        <span class="spacer"></span>
        <button class="sm ghost" onclick={() => onread(alert)}>{alert.read_at ? 'Mark unread' : 'Mark read'}</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .al { padding: 9px 12px; border-bottom: 1px solid var(--line); border-left: 3px solid transparent; display: flex; flex-direction: column; gap: 4px; }
  .al.unread { border-left-color: var(--accent); background: color-mix(in srgb, var(--accent-soft) 45%, var(--panel)); }
  .al:last-child { border-bottom: 0; }
  .head { flex-wrap: nowrap; }
  .t { font-weight: 600; font-size: 13px; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .snip { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; color: var(--muted); }
  .edit { overflow-wrap: anywhere; }
  .compact { padding: 6px 10px; }
  .acts { gap: 12px; }
</style>
