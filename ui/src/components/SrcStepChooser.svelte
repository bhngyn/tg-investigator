<script module>
  import { MEDIA_LABEL, intervalLabel } from './SrcFresh.svelte';
  import { num } from '../lib/fmt.js';

  /** Fresh set of collection steps. `over` may switch steps on or change values. */
  export function defaultSteps(over = {}) {
    const base = {
      posts: { on: true, pages: 10, media: 'photos', depth: 1, max_channels: 30 },
      comments: { on: false, limit: 200, media: 'none' },
      similar: { on: false },
      watch: { on: false, interval_min: 360, recheck_days: 7, media: 'photos', pages: 30 },
      comment_schedule: { on: false, interval_min: 60, media: 'none' },
    };
    for (const k of Object.keys(over)) base[k] = { ...base[k], ...over[k] };
    return base;
  }
  export const cloneSteps = (s) => JSON.parse(JSON.stringify(s));

  const clampInt = (v, lo, hi, dflt) => {
    const n = Math.round(Number(v));
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt;
  };

  /** Body for POST /api/channels/{name}/collect (only the switched-on steps). */
  export function stepsToBody(s) {
    const b = {};
    if (s.posts.on) {
      const depth = clampInt(s.posts.depth, 1, 2, 1);
      b.posts = { pages: clampInt(s.posts.pages, 1, 500, 10), media: s.posts.media, depth,
        max_channels: depth > 1 ? clampInt(s.posts.max_channels, 2, 200, 30) : 1 };
    }
    if (s.comments.on) b.comments = { limit: clampInt(s.comments.limit, 1, 100000, 200), media: s.comments.media };
    if (s.similar.on) b.similar = true;
    if (s.watch.on) b.watch = { interval_min: clampInt(s.watch.interval_min, 15, 525600, 360), recheck_days: clampInt(s.watch.recheck_days, 1, 365, 7),
      media: s.watch.media, pages: clampInt(s.watch.pages, 1, 100, 30) };
    if (s.comment_schedule.on) b.comment_schedule = { interval_min: clampInt(s.comment_schedule.interval_min, 1, 525600, 60), media: s.comment_schedule.media };
    return b;
  }

  /** Human-readable list of what will run: [{key, title, detail, login, when}]. */
  export function describeSteps(s) {
    const out = [];
    if (s.posts.on) {
      const d = clampInt(s.posts.depth, 1, 2, 1);
      out.push({ key: 'posts', title: 'Collect posts now', login: false, when: 'now',
        detail: `up to ${num(clampInt(s.posts.pages, 1, 500, 10))} pages (about 15 to 20 posts each), ${MEDIA_LABEL[s.posts.media]}${d > 1 ? `, also the channels it links to (up to ${clampInt(s.posts.max_channels, 2, 200, 30)})` : ''}` });
    }
    if (s.comments.on) {
      out.push({ key: 'comments', title: 'Collect comments now', login: true, when: 'now',
        detail: `latest ${num(clampInt(s.comments.limit, 1, 100000, 200))} comment messages, ${MEDIA_LABEL[s.comments.media]}` });
    }
    if (s.similar.on) out.push({ key: 'similar', title: 'Look up similar channels', login: true, when: 'now', detail: 'Telegram’s own list of similar channels (read only)' });
    if (s.watch.on) {
      out.push({ key: 'watch', title: 'Watch for changes', login: false, when: 'repeats',
        detail: `re-crawl ${intervalLabel(clampInt(s.watch.interval_min, 15, 525600, 360))}, re-check posts from the last ${clampInt(s.watch.recheck_days, 1, 365, 7)} days for edits and deletions, ${MEDIA_LABEL[s.watch.media]}` });
    }
    if (s.comment_schedule.on) {
      out.push({ key: 'comment_schedule', title: 'Collect comments on a schedule', login: true, when: 'repeats',
        detail: `${intervalLabel(clampInt(s.comment_schedule.interval_min, 1, 525600, 60))}, ${MEDIA_LABEL[s.comment_schedule.media]}. The first run starts within a minute.` });
    }
    return out;
  }
  export const usesLogin = (s) => describeSteps(s).some((x) => x.login);
</script>

<script>
  // Step chooser shared by "Add channels" and the channel page's "Collect now" panel.
  // Bind `steps` (see defaultSteps()). Steps marked "your Telegram account" use the logged-in session.
  import { INTERVALS } from './SrcFresh.svelte';

  /** @type {{ steps: any, canPosts?: boolean, reason?: string, only?: string[], idp?: string }} */
  let { steps = $bindable(), canPosts = true, reason = '', only = null, idp = 'st' } = $props();

  const show = (k) => !only || only.includes(k);
  const intervalOpts = (cur) => (INTERVALS.some(([m]) => m === cur) ? INTERVALS : [...INTERVALS, [cur, `every ${cur} min`]].sort((a, b) => a[0] - b[0]));
  const schedOpts = [[1, 'every minute'], [5, 'every 5 min'], [10, 'every 10 min'], [30, 'every 30 min'], [60, 'hourly'], [180, 'every 3 h'], [360, 'every 6 h'], [1440, 'daily']];
  const sched = (cur) => (schedOpts.some(([m]) => m === cur) ? schedOpts : [...schedOpts, [cur, `every ${cur} min`]].sort((a, b) => a[0] - b[0]));
</script>

<div class="steps">
  {#if show('posts')}
  <section class="step" class:on={steps.posts.on} class:off={!canPosts}>
    <label class="head">
      <input type="checkbox" bind:checked={steps.posts.on} disabled={!canPosts} />
      <b>Collect posts now</b>
      <span class="pill">public web, no login</span>
    </label>
    {#if !canPosts}
      <p class="help">{reason || 'This handle has no public post preview, so posts cannot be collected without logging in.'}</p>
    {:else if steps.posts.on}
      <div class="form-grid">
        <label class="field">Pages to read
          <input type="number" min="1" max="500" bind:value={steps.posts.pages} />
          <span class="help">About 15 to 20 posts per page, newest first.</span>
        </label>
        <label class="field">Media
          <select bind:value={steps.posts.media}>
            <option value="none">None (text only)</option>
            <option value="photos">Photos</option>
            <option value="all">Photos and videos in the preview</option>
          </select>
          <span class="help">Full-size video uses your account and is a separate job.</span>
        </label>
        <label class="field">Follow links
          <select bind:value={steps.posts.depth}>
            <option value={1}>This channel only</option>
            <option value={2}>Also crawl channels it links to</option>
          </select>
          {#if Number(steps.posts.depth) > 1}
            <span class="help">Crawls every linked channel too (max
              <input class="mini" type="number" min="2" max="200" bind:value={steps.posts.max_channels} aria-label="Maximum channels" />),
              which can be many requests.</span>
          {/if}
        </label>
      </div>
    {/if}
  </section>
  {/if}

  {#if show('comments')}
  <section class="step login" class:on={steps.comments.on}>
    <label class="head">
      <input type="checkbox" bind:checked={steps.comments.on} />
      <b>Collect comments now</b>
      <span class="pill warn">uses your Telegram account</span>
    </label>
    {#if steps.comments.on}
      <div class="form-grid">
        <label class="field">Messages to fetch
          <input type="number" min="1" max="100000" bind:value={steps.comments.limit} />
          <span class="help">Latest comment messages of the linked discussion group.</span>
        </label>
        <label class="field">Media
          <select bind:value={steps.comments.media}>
            <option value="none">None</option>
            <option value="photos">Photos</option>
            <option value="all">Photos and videos</option>
          </select>
        </label>
      </div>
      <p class="help">Only works if the channel has a discussion group. Reads as your logged-in account, so Telegram can see the requests.</p>
    {/if}
  </section>
  {/if}

  {#if show('similar')}
  <section class="step login" class:on={steps.similar.on}>
    <label class="head">
      <input type="checkbox" bind:checked={steps.similar.on} />
      <b>Find similar channels</b>
      <span class="pill warn">uses your Telegram account</span>
    </label>
    {#if steps.similar.on}
      <p class="help">Asks Telegram for its "similar channels" list (read only). Results appear in Discover and the network graph.</p>
    {/if}
  </section>
  {/if}

  {#if show('watch')}
  <section class="step" class:on={steps.watch.on} class:off={!canPosts}>
    <label class="head">
      <input type="checkbox" bind:checked={steps.watch.on} disabled={!canPosts} />
      <b>Watch for new, edited and deleted posts</b>
      <span class="pill">repeats, public web</span>
    </label>
    {#if steps.watch.on}
      <div class="form-grid">
        <label class="field">Re-crawl
          <select bind:value={steps.watch.interval_min}>
            {#each intervalOpts(Number(steps.watch.interval_min)) as [m, l] (m)}<option value={m}>{l}</option>{/each}
          </select>
        </label>
        <label class="field">Re-check posts from the last (days)
          <input type="number" min="1" max="365" bind:value={steps.watch.recheck_days} />
          <span class="help">Posts in this window are compared each run to catch edits and deletions.</span>
        </label>
        <label class="field">Media
          <select bind:value={steps.watch.media}>
            <option value="none">None</option><option value="photos">Photos</option><option value="all">Photos and videos</option>
          </select>
        </label>
      </div>
      <p class="help">The first watch run is queued within about a minute. If a crawl of this channel is already queued, they share it.</p>
    {/if}
  </section>
  {/if}

  {#if show('comment_schedule')}
  <section class="step login" class:on={steps.comment_schedule.on}>
    <label class="head">
      <input type="checkbox" bind:checked={steps.comment_schedule.on} />
      <b>Collect comments on a schedule</b>
      <span class="pill warn">repeats, uses your Telegram account</span>
    </label>
    {#if steps.comment_schedule.on}
      <div class="form-grid">
        <label class="field">Check for new comments
          <select bind:value={steps.comment_schedule.interval_min}>
            {#each sched(Number(steps.comment_schedule.interval_min)) as [m, l] (m)}<option value={m}>{l}</option>{/each}
          </select>
        </label>
        <label class="field">Media
          <select bind:value={steps.comment_schedule.media}>
            <option value="none">None</option><option value="photos">Photos</option><option value="all">Photos and videos</option>
          </select>
        </label>
      </div>
      <p class="help">Runs by itself in the background as long as this app is open. Short intervals mean many requests from your account.</p>
    {/if}
  </section>
  {/if}
</div>

<style>
  .steps { display: flex; flex-direction: column; gap: 8px; }
  .step { border: 1px solid var(--line); border-radius: var(--radius); padding: 9px 12px; background: var(--panel); }
  .step.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent-soft) 35%, var(--panel)); }
  .step.login.on { border-color: var(--warn); background: color-mix(in srgb, var(--warn-soft) 40%, var(--panel)); }
  .step.off { opacity: .65; }
  .head { display: flex; align-items: center; gap: 8px; cursor: pointer; flex-wrap: wrap; }
  .head:has(input:disabled) { cursor: not-allowed; }
  .form-grid { margin-top: 10px; }
  .help { margin: 6px 0 0; }
  .mini { width: 64px; padding: 1px 4px; }
</style>
