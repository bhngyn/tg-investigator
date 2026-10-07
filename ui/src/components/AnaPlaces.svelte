<script>
  // "What locations are claimed?" Coordinates written in posts/comments (and incidents with lat/lon). No map tiles.
  import { get } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { scoped, toast } from '../lib/store.svelte.js';
  import { dt, num } from '../lib/fmt.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import ChannelLink from './ChannelLink.svelte';
  import AnaHead from './AnaHead.svelte';
  import { download, toCsv, stamp } from './AnaUtil.js';

  let data = $state(null), error = $state(null), loading = $state(true);
  let seq = 0;
  $effect(() => {
    const params = scoped({});
    const my = ++seq;
    loading = true; error = null;
    get('/api/analysis/coordinates', params).then((d) => { if (my === seq) data = d; })
      .catch((e) => { if (my === seq) { error = e.message; data = null; } })
      .finally(() => { if (my === seq) loading = false; });
  });

  const kindF = $derived(route.query.kind || '');
  const chF = $derived(route.query.ch || '');
  const feats = $derived((data?.features || []).filter((f) => Array.isArray(f.geometry?.coordinates)
    && Math.abs(f.geometry.coordinates[1]) <= 90 && Math.abs(f.geometry.coordinates[0]) <= 180));
  const kinds = $derived([...new Set(feats.map((f) => f.properties.item_type))]);
  const chans = $derived([...new Set(feats.map((f) => f.properties.channel).filter(Boolean))].sort());
  const rows = $derived(feats.filter((f) => (!kindF || f.properties.item_type === kindF) && (!chF || f.properties.channel === chF))
    .sort((a, b) => (b.properties.date || '').localeCompare(a.properties.date || '')));
  const uncon = $derived(data?.unconverted || []);
  let sel = $state(null);

  // ---- offline scatter on plain SVG, equirectangular, with graticule ------------------------------
  const W = 720, H = 420, PAD = { l: 44, r: 12, t: 10, b: 26 };
  const box = $derived.by(() => {
    if (!rows.length) return null;
    const xs = rows.map((f) => f.geometry.coordinates[0]), ys = rows.map((f) => f.geometry.coordinates[1]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const m = Math.max(x1 - x0, y1 - y0, 0.02) * 0.08 + 0.005;
    x0 -= m; x1 += m; y0 -= m; y1 += m;
    // keep equal degrees per pixel in y and x scaled by cos(lat), so shapes are not distorted
    const cl = Math.cos((((y0 + y1) / 2) * Math.PI) / 180) || 1;
    const pw = W - PAD.l - PAD.r, ph = H - PAD.t - PAD.b;
    const s = Math.min(pw / ((x1 - x0) * cl), ph / (y1 - y0));
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const hx = pw / s / cl / 2, hy = ph / s / 2;
    return { x0: cx - hx, x1: cx + hx, y0: cy - hy, y1: cy + hy, cl, s };
  });
  const px = (lon) => PAD.l + (lon - box.x0) * box.cl * box.s;
  const py = (lat) => PAD.t + (box.y1 - lat) * box.s;
  function ticks(a, b) {
    const span = b - a, raw = span / 5;
    const steps = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30];
    const st = steps.find((x) => x >= raw) || 30;
    const out = [];
    for (let v = Math.ceil(a / st) * st; v <= b; v += st) out.push(+v.toFixed(6));
    return out;
  }
  const dec = (st) => (st >= 1 ? 0 : st >= 0.1 ? 1 : st >= 0.01 ? 2 : 3);
  const xt = $derived(box ? ticks(box.x0, box.x1) : []);
  const yt = $derived(box ? ticks(box.y0, box.y1) : []);
  const fmtT = (arr, v) => v.toFixed(arr.length > 1 ? dec(Math.abs(arr[1] - arr[0])) : 2);
  const TYPE_WORD = { post: 'Post text', comment: 'Comment text', incident: 'Incident you placed' };
  function shape(type, x, y, r) {
    if (type === 'comment') return `M${x - r} ${y - r}h${2 * r}v${2 * r}h${-2 * r}z`;
    if (type === 'incident') return `M${x} ${y - r * 1.3}L${x + r * 1.3} ${y}L${x} ${y + r * 1.3}L${x - r * 1.3} ${y}z`;
    return `M${x - r} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0z`;
  }
  const COLOR = { post: 'var(--accent)', comment: 'var(--info)', incident: 'var(--bad)' };

  const lat = (f) => f.geometry.coordinates[1], lon = (f) => f.geometry.coordinates[0];
  const coordText = (f) => `${lat(f).toFixed(6)}, ${lon(f).toFixed(6)}`;
  async function copy(f) {
    try { await navigator.clipboard.writeText(coordText(f)); toast('Copied lat, lon', 'ok', 1500); } catch { toast('Copy not available here', 'error'); }
  }

  function exportGeo() {
    const fc = { type: 'FeatureCollection', features: rows,
      note: 'Coordinates as written in collected text: claims to check, not verified locations. Exported ' + new Date().toISOString() };
    download(`claimed-locations-${stamp()}.geojson`, JSON.stringify(fc, null, 2), 'application/geo+json');
  }
  function exportCsv() {
    const out = rows.map((f) => ({ lat: lat(f), lon: lon(f), kind: f.properties.item_type, coordinate_format: f.properties.kind, written_as: f.properties.match,
      channel: f.properties.channel, date: f.properties.date, url: f.properties.url, id: f.properties.id, text: f.properties.excerpt || f.properties.title }));
    download(`claimed-locations-${stamp()}.csv`, toCsv(out, ['lat', 'lon', 'kind', 'coordinate_format', 'written_as', 'channel', 'date', 'url', 'id', 'text']), 'text/csv');
  }
  const FMT = { decimal: 'decimal degrees', dms: 'degrees-minutes-seconds', yandex: 'Yandex link', mgrs: 'MGRS grid', map_link: 'map link (Google/Apple/OSM)' };
</script>

<AnaHead question="What locations are claimed?">
  Every coordinate found <b>written in collected text</b> (and incidents you placed yourself). These are claims to check, not verified locations:
  the author may be wrong, may have made a typo, or may be misleading. Plain decimal pairs are matched by pattern, so a pair of numbers that is not a location (a price, a measurement) can appear; lat/lon order can also be swapped.
  No map is loaded (nothing leaves this computer), so points are plotted on a plain graticule.
</AnaHead>

{#if loading}
  <Loading />
{:else if error}
  <div class="callout bad">Could not load coordinates: {error}</div>
{:else if !feats.length && !uncon.length}
  <Empty title="No coordinates found in the collected text">
    Looked for decimal degrees, degrees-minutes-seconds, and Google/Apple/OSM/Yandex map links in posts and comments, plus incidents with a latitude and longitude.
    Comments need the logged-in comment collection (<a href={href('channels')}>Channels</a>).
  </Empty>
{:else}
  <div class="row controls">
    <div class="stats grow">
      <div class="stat panel"><div class="v">{num(feats.length)}</div><div class="l">claimed points</div></div>
      <div class="stat panel"><div class="v">{num(uncon.length)}</div><div class="l">grid refs to convert</div></div>
    </div>
    <label class="field">Source
      <select value={kindF} onchange={(e) => setQuery({ kind: e.currentTarget.value })}>
        <option value="">All</option>{#each kinds as k}<option value={k}>{k}</option>{/each}
      </select>
    </label>
    <label class="field">Channel
      <select value={chF} onchange={(e) => setQuery({ ch: e.currentTarget.value })}>
        <option value="">Any</option>{#each chans as c}<option value={c}>@{c}</option>{/each}
      </select>
    </label>
    <button class="primary" disabled={!rows.length} onclick={exportGeo} title="GeoJSON of the rows shown, built in your browser">Download GeoJSON</button>
    <button disabled={!rows.length} onclick={exportCsv}>Download CSV</button>
  </div>

  {#if box}
    <figure class="panel plot">
      <svg viewBox="0 0 {W} {H}" role="img" aria-label="Scatter plot of claimed coordinates on a blank graticule, no basemap">
        <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} class="frame" />
        {#each xt as v}<line x1={px(v)} x2={px(v)} y1={PAD.t} y2={H - PAD.b} class="grat" /><text x={px(v)} y={H - PAD.b + 14} text-anchor="middle" class="tick">{fmtT(xt, v)}°</text>{/each}
        {#each yt as v}<line y1={py(v)} y2={py(v)} x1={PAD.l} x2={W - PAD.r} class="grat" /><text x={PAD.l - 5} y={py(v) + 3} text-anchor="end" class="tick">{fmtT(yt, v)}°</text>{/each}
        {#each rows as f, i (i)}
          <!-- Shape as well as colour: circle = post, square = comment, diamond = incident you placed. -->
          <path d={shape(f.properties.item_type, px(lon(f)), py(lat(f)), sel === f ? 7 : 4.5)} style="fill:{COLOR[f.properties.item_type] || 'var(--muted)'}" class="pt" class:sel={sel === f}
            role="button" tabindex="0" aria-pressed={sel === f} aria-label="{TYPE_WORD[f.properties.item_type] || 'point'} at {coordText(f)}" onclick={() => (sel = sel === f ? null : f)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sel = sel === f ? null : f; } }}>
            <title>{coordText(f)} · {f.properties.channel || f.properties.title || ''} · {f.properties.date?.slice(0, 10) || ''}</title>
          </path>
        {/each}
      </svg>
      <figcaption class="small muted row">
        <span><svg class="lg" viewBox="-6 -6 12 12" aria-hidden="true"><circle r="4.5" fill="var(--accent)" /></svg> circle: post text</span>
        <span><svg class="lg" viewBox="-6 -6 12 12" aria-hidden="true"><rect x="-4.5" y="-4.5" width="9" height="9" fill="var(--info)" /></svg> square: comment text</span>
        <span><svg class="lg" viewBox="-6 -6 12 12" aria-hidden="true"><path d="M0 -6L6 0L0 6L-6 0z" fill="var(--bad)" /></svg> diamond: incident you placed</span>
        <span class="faint">Blank grid, longitude east/latitude north; x scaled by cos(latitude). Overlapping points are hidden; use the table.</span>
      </figcaption>
    </figure>
  {/if}

  <div class="section-title">Claimed points <span class="faint">newest first · click a row to highlight it on the plot</span></div>
  {#if !rows.length}
    <Empty title="Nothing matches these filters" />
  {:else}
    <div class="table-wrap">
      <table class="t">
        <thead><tr><th scope="col">Coordinates (lat, lon)</th><th scope="col">As written</th><th scope="col">Channel</th><th scope="col">Date</th><th scope="col">Text</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each rows.slice(0, 500) as f, i (i)}
            <tr class="click" class:sel={sel === f} onclick={() => (sel = sel === f ? null : f)}>
              <td class="nowrap mono"><button class="rowbtn" onclick={(e) => { e.stopPropagation(); sel = sel === f ? null : f; }} aria-pressed={sel === f} title="Highlight on the map">{coordText(f)}</button><div class="tiny faint">{FMT[f.properties.kind] || f.properties.kind || f.properties.status || ''}</div></td>
              <td class="mono small">{f.properties.match || f.properties.location || ''}</td>
              <td>{#if f.properties.channel}<ChannelLink name={f.properties.channel} showHandle={false} />{:else}<span class="pill accent">incident</span>{/if}
                {#if f.properties.item_type === 'comment'}<span class="pill info">comment</span>{/if}</td>
              <td class="nowrap small">{dt(f.properties.date)}</td>
              <td class="small"><div class="ex text-body" dir="auto">{f.properties.excerpt || f.properties.title || ''}</div></td>
              <td class="nowrap">
                <button class="ghost sm" onclick={(e) => { e.stopPropagation(); copy(f); }}>Copy</button>
                {#if f.properties.url}<a class="small" href={f.properties.url} target="_blank" rel="noreferrer noopener" onclick={(e) => e.stopPropagation()}>t.me ↗</a>{/if}
                {#if f.properties.item_type === 'incident'}<a class="small" href={href('incident', f.properties.id)} onclick={(e) => e.stopPropagation()}>incident</a>{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if rows.length > 500}<p class="faint small">Showing 500 of {num(rows.length)}. The downloads contain all of them.</p>{/if}
  {/if}

  {#if uncon.length}
    <div class="section-title">Grid references not converted ({num(uncon.length)})</div>
    <p class="muted small" style="max-width:80ch">MGRS grid references (often used in military posts) were found but are not converted or plotted here. Convert them yourself with an offline tool and treat the result as a claim.</p>
    <div class="table-wrap">
      <table class="t">
        <thead><tr><th scope="col">Written as</th><th scope="col">Channel</th><th scope="col">Date</th><th scope="col">Text</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each uncon.slice(0, 200) as u, i (i)}
            <tr>
              <td class="mono">{u.match}</td>
              <td><ChannelLink name={u.channel} showHandle={false} />{#if u.item_type === 'comment'} <span class="pill info">comment</span>{/if}</td>
              <td class="nowrap small">{dt(u.date)}</td>
              <td class="small"><div class="ex text-body" dir="auto">{u.excerpt}</div></td>
              <td>{#if u.url}<a class="small" href={u.url} target="_blank" rel="noreferrer noopener">t.me ↗</a>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}

<style>
  .controls { margin-bottom: 12px; align-items: flex-end; }
  .grow { flex: 1; min-width: 260px; max-width: 420px; }
  .plot { margin: 0 0 12px; padding: 8px; }
  svg { width: 100%; height: auto; max-height: 460px; display: block; }
  .frame { fill: var(--panel-2); stroke: var(--line-strong); }
  .grat { stroke: var(--line); stroke-width: 1; }
  .tick { fill: var(--faint); font-size: 10px; font-family: var(--mono); }
  .pt { fill-opacity: .75; stroke: var(--panel); stroke-width: 1; cursor: pointer; }
  .pt.sel { fill-opacity: 1; stroke: var(--text); stroke-width: 2; }
  .lg { width: 12px; height: 12px; vertical-align: -2px; margin-right: 3px; }
  .ex { max-height: 4.2em; overflow: hidden; max-width: 60ch; }
</style>
