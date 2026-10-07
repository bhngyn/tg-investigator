<script>
  // Drawer content for one Telegram account seen in comment sections: GET /api/tg_users/{uid}
  // plus their comments (GET /api/comments?sender=uid).
  // Contract (render inside <Drawer>, or use TriageDetailDrawer):
  //   <AccountDrawer uid={userId} onopen={({type, id}) => ...} />
  //   onopen is called with {type:'comment', id} when a comment is clicked.
  // Phone numbers are personal data: always behind a click (TriagePhone).
  import { get } from '../lib/api.js';
  import { href } from '../lib/router.svelte.js';
  import { dt, day, num, plural } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import TriagePhone from './TriagePhone.svelte';
  import AddToIncident from './AddToIncident.svelte';

  let { uid, onopen } = $props();

  let u = $state(null);
  let comments = $state(null);
  let error = $state(null);
  let incOpen = $state(false);

  async function load() {
    error = null;
    try {
      const [user, cm] = await Promise.all([
        get(`/api/tg_users/${uid}`),
        get('/api/comments', { sender: uid, limit: 40 }).catch(() => ({ total: 0, results: [] })),
      ]);
      u = user;
      comments = cm;
    } catch (e) {
      error = e.status === 404 ? 'This account is not in the archive (no comment by it has been collected).' : e.message;
    }
  }
  $effect(() => { if (uid != null) load(); });

  const name = $derived(u ? [u.first_name, u.last_name].filter(Boolean).join(' ') || (u.deleted ? 'Deleted account' : '(no name)') : '');
  const flags = $derived(u ? ['bot', 'premium', 'verified', 'scam', 'fake', 'deleted'].filter((f) => u[f]) : []);
  // Only show snapshot rows where something visible changed.
  const changes = $derived.by(() => {
    const out = [];
    let prev = null;
    for (const s of u?.snapshots || []) {
      const sig = [s.username, s.first_name, s.last_name, s.phone, s.bot, s.deleted].join('|');
      if (sig !== prev) out.push(s);
      prev = sig;
    }
    return out;
  });
</script>

{#if error}
  <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
{:else if !u}
  <Loading />
{:else}
  <div class="ad">
    <div>
      <h2 dir="auto">{name}</h2>
      <div class="row wrap small">
        {#if u.username}<a href={`https://t.me/${u.username}`} target="_blank" rel="noreferrer noopener"><bdi dir="ltr">@{u.username}</bdi> ↗</a>{:else}<span class="faint">no public username</span>{/if}
        <span class="mono faint">id {u.user_id}</span>
        {#each flags as f}<span class="pill" class:bad={f === 'scam' || f === 'fake'}>{f}</span>{/each}
      </div>
    </div>

    <dl class="kv small">
      <dt>Phone</dt><dd><TriagePhone phone={u.phone} /></dd>
      <dt>First seen</dt><dd>{dt(u.first_seen)}</dd>
      <dt>Last seen</dt><dd>{dt(u.last_seen)}</dd>
    </dl>

    <div class="row wrap">
      <button class="sm primary" onclick={() => (incOpen = true)}>⚑ Add to incident</button>
      <a class="btn sm" href={href('comments', { sender: u.user_id })}>All their comments</a>
      <a class="btn sm ghost" href={href('analysis', 'people')}>Cross-section overlap</a>
    </div>

    {#if u.roles?.length}
      <section class="panel box">
        <div class="section-title">Admin roles</div>
        {#each u.roles as r (r.group_id)}
          <div class="small">
            {#if r.channel}<ChannelLink name={r.channel} />{:else}<span dir="auto">{r.title || 'group ' + r.group_id}</span>{/if}
            <span class="pill accent">{r.role}{r.rank ? ': ' + r.rank : ''}</span>
            {#if r.removed_detected}<span class="pill bad">removed {day(r.removed_detected)}</span>{/if}
            <span class="tiny faint">{r.since ? 'since ' + day(r.since) : ''} · seen {day(r.first_seen)}–{day(r.last_seen)}</span>
          </div>
        {/each}
      </section>
    {/if}

    <section class="panel box">
      <div class="section-title">Comment sections <span class="tiny faint">{u.groups?.length || 0}</span></div>
      {#if u.groups?.length}
        <div class="table-wrap"><table class="t small">
          <thead><tr><th scope="col">Section</th><th scope="col" class="num">Comments</th><th scope="col" class="num">Deleted</th><th scope="col" class="num">Forwards</th><th scope="col">Active</th></tr></thead>
          <tbody>
            {#each u.groups as g (g.channel)}
              <tr>
                <td><a href={href('comments', { sender: u.user_id, channels: g.channel })}><bdi dir="ltr">@{g.channel}</bdi></a></td>
                <td class="num">{num(g.n)}</td><td class="num">{num(g.deleted)}</td><td class="num">{num(g.forwards)}</td>
                <td class="nowrap">{day(g.first)} – {day(g.last)}</td>
              </tr>
            {/each}
          </tbody>
        </table></div>
      {:else}
        <div class="tiny faint">No comments by this account are stored.</div>
      {/if}
    </section>

    {#if comments?.results?.length}
      <section class="panel box">
        <div class="section-title">Latest comments <span class="tiny faint">{comments.results.length} of {num(comments.total)}</span></div>
        {#each comments.results as c (c.id)}
          <button class="cm" onclick={() => onopen?.({ type: 'comment', id: c.id })} disabled={!onopen}>
            <span class="tiny faint"><bdi dir="ltr">@{c.channel}</bdi> · {dt(c.date)}{c.deleted_detected ? ' · deleted' : ''}</span>
            <span class="small" dir="auto">{(c.text || '(' + (c.media_types || []).join(', ') + ')').slice(0, 240)}</span>
          </button>
        {/each}
      </section>
    {/if}

    {#if changes.length > 1}
      <section class="panel box">
        <div class="section-title">Profile changes over time <span class="tiny faint">{plural(changes.length, 'distinct state')}</span></div>
        <div class="table-wrap"><table class="t small">
          <thead><tr><th scope="col">Captured</th><th scope="col">Name</th><th scope="col">Username</th><th scope="col">Phone</th></tr></thead>
          <tbody>
            {#each changes as s (s.id)}
              <tr><td class="nowrap">{dt(s.captured_at)}</td><td dir="auto">{[s.first_name, s.last_name].filter(Boolean).join(' ') || '–'}</td>
                <td>{s.username ? '@' + s.username : '–'}</td><td><TriagePhone phone={s.phone} /></td></tr>
            {/each}
          </tbody>
        </table></div>
      </section>
    {/if}
  </div>
  <AddToIncident open={incOpen} itemType="account" itemId={u.user_id} onclose={() => (incOpen = false)} />
{/if}

<style>
  .ad { display: flex; flex-direction: column; gap: 12px; }
  .wrap { flex-wrap: wrap; }
  .box { padding: 10px 12px; }
  .kv { display: grid; grid-template-columns: 110px 1fr; gap: 4px 10px; margin: 0; align-items: center; }
  .kv dt { color: var(--faint); }
  .kv dd { margin: 0; }
  .cm { display: flex; flex-direction: column; align-items: stretch; gap: 1px; width: 100%; text-align: start; border: 0; border-top: 1px solid var(--line); border-radius: 0; background: none; padding: 6px 2px; font-weight: 400; color: var(--text); }
  .cm:hover:not(:disabled) { background: var(--panel-2); }
</style>
