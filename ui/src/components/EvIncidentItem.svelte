<script>
  // A non-post item linked to an incident (comment, media file, channel, account), with its relation and note.
  import Media from './Media.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import { href } from '../lib/router.svelte.js';
  import { dt, bytes } from '../lib/fmt.js';

  /** @type {{ it: any, onremove: (it:any)=>void, onedit: (it:any, relation:string, note:string)=>Promise<void> }} */
  let { it, onremove, onedit } = $props();
  const x = $derived(it.item);
  let editing = $state(false);
  let relation = $state('');
  let note = $state('');
  let busy = $state(false);
  function start() { relation = it.relation || ''; note = it.note || ''; editing = true; }
  async function save() { busy = true; await onedit(it, relation.trim(), note.trim()); busy = false; editing = false; }
  const name = (u) => [u.first_name, u.last_name].filter(Boolean).join(' ') || (u.username ? '@' + u.username : `id ${u.user_id}`);
  const TYPE = { post: 'Post', comment: 'Comment', media: 'Media file', comment_media: 'Comment media', channel: 'Channel', account: 'Account' };
</script>

<div class="it panel">
  <div class="row head">
    <span class="pill info">{TYPE[it.item_type] || it.item_type}</span>
    {#if it.relation}<span class="pill accent">{it.relation}</span>{/if}
    <span class="spacer"></span>
    <button class="sm ghost" onclick={start}>Edit relation / note</button>
    <button class="sm danger" onclick={() => onremove(it)}>Remove</button>
  </div>

  {#if !x}
    <div class="muted small">This item no longer exists in the archive (type {it.item_type}, id {it.item_id}).</div>
  {:else if it.item_type === 'comment'}
    <div class="row small"><ChannelLink name={x.channel} /> <span class="faint">comment · {dt(x.date)}</span>
      {#if x.deleted_detected}<span class="pill bad">deleted</span>{/if}
      {#if x.sender_type === 'user' && x.sender_id}<a href={href('comments', { sender: x.sender_id })}>sender {x.sender_id}</a>{/if}</div>
    <div class="text-body body" dir="auto">{x.text || '(no text)'}</div>
  {:else if it.item_type === 'media' || it.item_type === 'comment_media'}
    <div class="row small"><ChannelLink name={x.channel} /> <span class="faint">{x.kind || 'file'} · {bytes(x.bytes)}</span>
      {#if x.url}<a href={x.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}</div>
    <Media media={[x]} sensitive={x.sensitive} channel={x.channel} date={x.date} size="sm" />
    <div class="faint tiny mono">SHA-256 {x.sha256}</div>
  {:else if it.item_type === 'channel'}
    <ChannelLink name={x.username} title={x.title} />
    <span class="faint small"> {x.type}{#if x.subscribers} · {x.subscribers.toLocaleString('en-US')} subscribers{/if}</span>
  {:else if it.item_type === 'account'}
    <a href={href('comments', { sender: x.user_id })}><b>{name(x)}</b></a>
    <span class="faint small"> Telegram account {x.user_id}</span>
  {/if}

  {#if editing}
    <div class="col" style="margin-top:8px">
      <input type="text" bind:value={relation} aria-label="Relation to the incident" placeholder="Relation, e.g. primary footage" />
      <textarea bind:value={note} rows="2" aria-label="Note" placeholder="Note"></textarea>
      <div class="row"><button class="sm primary" onclick={save} disabled={busy}>Save</button><button class="sm" onclick={() => (editing = false)}>Cancel</button></div>
    </div>
  {:else if it.note}
    <div class="note small">📝 {it.note}</div>
  {/if}
  <div class="faint tiny">Added by {it.added_by || '?'} · {dt(it.added_at)}</div>
</div>

<style>
  .it { padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
  .body { font-size: 13.5px; }
  .note { background: var(--panel-2); padding: 4px 8px; border-radius: var(--radius-sm); }
  .mono { font-family: var(--mono); overflow-wrap: anywhere; }
</style>
