<script>
  // "Who is the audience of a comment section?" Commenter mix, languages, activity by UTC hour/weekday.
  import { get } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { scoped } from '../lib/store.svelte.js';
  import { num, day } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp } from './AnaUtil.js';

  const channel = $derived(route.query.channel || '');

  let groups = $state([]), gLoaded = $state(false);
  $effect(() => { get('/api/comment_groups').then((g) => { groups = g || []; }).catch(() => {}).finally(() => { gLoaded = true; }); });

  let data = $state(null), error = $state(null), loading = $state(true);
  let seq = 0;
  $effect(() => {
    const params = scoped({ channel });
    const my = ++seq;
    loading = true; error = null;
    get('/api/community', params).then((d) => { if (my === seq) data = d; })
      .catch((e) => { if (my === seq) { error = e.message; data = null; } })
      .finally(() => { if (my === seq) loading = false; });
  });

  const S = $derived(data?.summary);
  const none = $derived(data && S.comments === 0);
  const pct = (a, b) => (b ? Math.round((a / b) * 100) + '%' : '–');
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  // ---- hour bars + weekday heatmap (inline SVG) ----
  const hMax = $derived(Math.max(1, ...(data?.hours_utc || [0])));
  const peak = $derived(data ? data.hours_utc.indexOf(Math.max(...data.hours_utc)) : 0);
  const wPeak = $derived.by(() => { let best = { n: 0, d: 0, h: 0 }; (data?.weekday_hour_utc || []).forEach((row, d) => row.forEach((n, h) => { if (n > best.n) best = { n, d, h }; })); return best; });
  const wMax = $derived(Math.max(1, ...((data?.weekday_hour_utc || []).flat())));
  const perDay = $derived(data?.per_day || []);
  const dMax = $derived(Math.max(1, ...perDay.map((d) => d.n)));

  let openLang = $state(null), openCountry = $state(null);
  const cMax = $derived(Math.max(1, ...((data?.countries || []).map((c) => c.accounts.length))));
  const lMax = $derived(Math.max(1, ...((data?.languages || []).map((l) => l.comments))));

  function exportTop() {
    const rows = data.top.map((t) => ({ user_id: t.user_id, name: t.name, username: t.username, phone: t.phone, phone_country_guess: t.phone_country,
      comments: t.comments, admin: t.admin ? 1 : 0, bot: t.bot ? 1 : 0, sections: t.sections }));
    download(`top-commenters-${channel || 'all'}-${stamp()}.csv`, toCsv(rows, Object.keys(rows[0])), 'text/csv');
  }
</script>

<AnaHead question="Who is the audience of a comment section?">
  A profile of the accounts that comment: how concentrated it is, which phone-number countries and languages appear, and when they are active.
  Phone country (calling code) and language (a small local detector) are <b>heuristics</b>: numbers can be virtual or foreign, and short texts are often misdetected.
  Times are UTC; a daily peak can hint at time zones but is not evidence of where people are.
</AnaHead>

<div class="row controls">
  <label class="field">Comment section
    <select value={channel} onchange={(e) => setQuery({ channel: e.currentTarget.value })}>
      <option value="">All collected sections{groups.length ? ` (${groups.length})` : ''}</option>
      {#each groups as g (g.group_id)}<option value={g.channel}>@{g.channel}{g.title ? ' · ' + g.title : ''} ({num(g.comments)})</option>{/each}
    </select>
  </label>
</div>

{#if loading && !data}
  <Loading />
{:else if error}
  <div class="callout bad">Could not load community profile: {error}</div>
{:else if none}
  <Empty title="No comments collected{channel ? ' for @' + channel : ''}">
    {#if gLoaded && !groups.length}
      No comment section has been collected yet. Comments are read through the logged-in Telegram account, only when you start it, one channel at a time. Start from <a href={href('channels')}>Channels</a>.
    {:else}
      This section has no collected comments. Collect its comments from <a href={href('channels')}>Channels</a>.
    {/if}
  </Empty>
{:else if data}
  <div class="stats" style="margin-bottom:12px">
    <div class="stat panel"><div class="v">{num(S.comments)}</div><div class="l">comments</div></div>
    <div class="stat panel"><div class="v">{num(S.accounts)}</div><div class="l">commenting accounts</div></div>
    <div class="stat panel" title="Share of user comments written by the ten most active accounts"><div class="v">{Math.round(S.top10_share * 100)}%</div><div class="l">from the top 10 accounts</div></div>
    <div class="stat panel"><div class="v">{pct(S.one_comment, S.accounts)}</div><div class="l">commented only once</div></div>
    <div class="stat panel"><div class="v">{num(S.in_other_sections)}</div><div class="l">also in other sections</div></div>
    <div class="stat panel"><div class="v">{pct(S.with_phone, S.accounts)}</div><div class="l">show a phone number</div></div>
    <div class="stat panel"><div class="v">{num(S.deleted)}</div><div class="l">comments found deleted</div></div>
  </div>
  <p class="faint small">{num(S.as_channel)} comments were posted as a channel, {num(S.anonymous_admin)} as anonymous admins, {num(S.forwards)} are forwards;
    {num(S.bots)} bot and {num(S.deleted_accounts)} deleted account{S.deleted_accounts === 1 ? '' : 's'}; {num(S.premium)} Premium; {pct(S.with_username, S.accounts)} have a username.</p>

  <div class="grid two">
    <section class="panel">
      <div class="panel-head"><h3>Activity by hour (UTC)</h3><span class="faint small">peak {String(peak).padStart(2, '0')}:00, {num(data.hours_utc[peak])} comments</span></div>
      <div class="panel-body">
        <svg viewBox="0 0 480 150" role="img" aria-label="Comments per UTC hour. Busiest hour {String(peak).padStart(2, '0')}:00 UTC with {data.hours_utc[peak]} comments.">
          {#each data.hours_utc as n, h}
            <rect x={10 + h * 19} y={120 - (n / hMax) * 100} width="15" height={(n / hMax) * 100} class="b" class:pk={h === peak}><title>{String(h).padStart(2, '0')}:00 UTC · {n} comments</title></rect>
            {#if h % 3 === 0}<text x={17.5 + h * 19} y="136" text-anchor="middle" class="tk">{String(h).padStart(2, '0')}</text>{/if}
          {/each}
          <text x={17.5 + peak * 19} y={116 - (data.hours_utc[peak] / hMax) * 100} text-anchor="middle" class="lab">{data.hours_utc[peak]}</text>
          <line x1="10" x2="470" y1="120" y2="120" class="ax" />
        </svg>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h3>Weekday × hour (UTC)</h3><span class="faint small">darker = more comments</span></div>
      <div class="panel-body">
        <svg viewBox="0 0 480 170" role="img" aria-label="Heatmap of comments by weekday and UTC hour. {wPeak.n ? `Busiest slot ${DAYS[wPeak.d]} ${String(wPeak.h).padStart(2, '0')}:00 UTC with ${wPeak.n} comments.` : 'No comments.'}">
          {#each data.weekday_hour_utc as row, d}
            <text x="30" y={20 + d * 17 + 11} text-anchor="end" class="tk">{DAYS[d]}</text>
            {#each row as n, h}
              <rect x={36 + h * 18} y={20 + d * 17} width="16" height="15" rx="2" class="hm" style:fill-opacity={n ? 0.1 + 0.9 * (n / wMax) : 0.04}><title>{DAYS[d]} {String(h).padStart(2, '0')}:00 UTC · {n}</title></rect>
            {/each}
          {/each}
          {#each [0, 3, 6, 9, 12, 15, 18, 21] as h}<text x={44 + h * 18} y="14" text-anchor="middle" class="tk">{String(h).padStart(2, '0')}</text>{/each}
        </svg>
      </div>
    </section>
  </div>

  {#if perDay.length > 1}
    <section class="panel" style="margin-top:12px">
      <div class="panel-head"><h3>Comments per day</h3><span class="faint small">{day(perDay[0].d)} → {day(perDay[perDay.length - 1].d)} · busiest {num(dMax)}</span></div>
      <div class="panel-body">
        <svg viewBox="0 0 900 90" preserveAspectRatio="none" role="img" aria-label="Comments per day, {perDay.length} days, busiest {dMax}" class="days">
          {#each perDay as d, i}
            <rect x={(i / perDay.length) * 900} y={80 - (d.n / dMax) * 76} width={Math.max(1, 900 / perDay.length - 1)} height={(d.n / dMax) * 76} class="b"><title>{d.d} · {d.n}</title></rect>
          {/each}
        </svg>
      </div>
    </section>
  {/if}

  <div class="grid two" style="margin-top:12px">
    <section class="panel">
      <div class="panel-head"><h3>Phone-number countries</h3><span class="pill">heuristic</span></div>
      <div class="panel-body">
        {#if !data.countries.length}<p class="muted small">No commenter shows a phone number (most hide it). Nothing to infer.</p>{/if}
        {#each data.countries as c (c.iso)}
          <div class="brow">
            <button class="ghost sm lbl" onclick={() => (openCountry = openCountry === c.iso ? null : c.iso)}>{c.country} (+{c.code})</button>
            <div class="bar"><i style:width="{(c.accounts.length / cMax) * 100}%"></i></div>
            <b class="num">{c.accounts.length}</b>
          </div>
          {#if openCountry === c.iso}
            <ul class="small acc">{#each c.accounts.slice(0, 30) as a}<li><a href={href('comments', { sender: a.user_id })}>{a.name || (a.username ? '@' + a.username : a.user_id)}</a> <span class="faint">{a.comments} comments</span></li>{/each}</ul>
          {/if}
        {/each}
        {#if data.countries.length}<p class="faint small">Accounts per calling-code country. A +249 number does not prove the person is in Sudan; numbers can be bought or roamed.</p>{/if}
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h3>Languages</h3><span class="pill">heuristic</span></div>
      <div class="panel-body">
        {#each data.languages as l (l.lang)}
          <div class="brow">
            <button class="ghost sm lbl" onclick={() => (openLang = openLang === l.lang ? null : l.lang)}>{l.lang === 'none' ? 'No text / too short' : l.name}</button>
            <div class="bar"><i style:width="{(l.comments / lMax) * 100}%"></i></div>
            <b class="num">{num(l.comments)}</b>
          </div>
          {#if openLang === l.lang && l.samples.length}
            <ul class="small acc">{#each l.samples as s}<li dir="auto" class="text-body">{s.text}</li>{/each}</ul>
          {/if}
        {/each}
        <p class="faint small">Comments per detected language, from a small on-device detector. Dialect, transliteration and short messages are often mislabelled. Click a language for samples.</p>
      </div>
    </section>
  </div>

  <div class="section-title row">Most active accounts <span class="spacer"></span>
    <button class="sm" disabled={!data.top.length} onclick={exportTop}>Export CSV</button></div>
  <div class="table-wrap"><table class="t">
    <thead><tr><th scope="col">Account</th><th scope="col" class="right">Comments</th><th scope="col">Phone country <span class="faint">(guess)</span></th><th scope="col" class="right">Sections</th><th scope="col"></th></tr></thead>
    <tbody>{#each data.top as t (t.user_id)}
      <tr>
        <td><a href={href('comments', { sender: t.user_id })}><b>{t.name || (t.username ? '@' + t.username : 'account ' + t.user_id)}</b></a>{#if t.username && t.name}<span class="faint small"> @{t.username}</span>{/if}
          {#if t.admin}<span class="pill warn">admin</span>{/if}{#if t.bot}<span class="pill">bot</span>{/if}{#if t.premium}<span class="pill info">premium</span>{/if}</td>
        <td class="right num"><b>{t.comments}</b></td><td class="small">{t.phone_country || '–'}</td><td class="right num">{t.sections}</td>
        <td><a class="btn sm" href={href('comments', { sender: t.user_id })}>Comments</a></td>
      </tr>{/each}</tbody>
  </table></div>
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  svg { width: 100%; height: auto; display: block; }
  svg.days { height: 90px; }
  .b { fill: var(--accent); fill-opacity: .55; }
  .b.pk { fill-opacity: 1; }
  .hm { fill: var(--accent); stroke: var(--line-strong); stroke-width: .6; paint-order: stroke; }
  .ax { stroke: var(--line-strong); }
  .tk { fill: var(--faint); font-size: 10px; font-family: var(--mono); }
  .lab { fill: var(--text); font-size: 11px; font-weight: 600; }
  .brow { display: grid; grid-template-columns: minmax(120px, 190px) 1fr 44px; gap: 8px; align-items: center; margin-bottom: 3px; }
  .lbl { justify-content: flex-start; padding-left: 0; overflow: hidden; text-overflow: ellipsis; }
  .acc { margin: 2px 0 8px 12px; padding-left: 12px; color: var(--muted); }
</style>
